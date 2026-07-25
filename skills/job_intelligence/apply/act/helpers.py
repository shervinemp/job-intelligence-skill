"""act/helpers.py — Shared helpers for all act commands.

Chrome lifecycle, DOM interaction (JS evaluation), field probing,
fill dispatch, file upload, validation scanning, submit detection,
profile loading, answer dict construction, vision verification.
"""
import json, os, sys, time
from contextlib import contextmanager

from lib.config import PROFILE_PATH, JI_HOME
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.page_helpers import (
    load_state, save_state, read_page, page_text, find_page,
    tag_page, check_applied_signal, check_captcha, handle_captcha,
    scan_actions, mark_applied, handle_session_timeout,
)
from apply.common.resolve import resolve, learn_mapping
from apply.common.signals import has_success_text

RESULTS_DIR = os.path.join(JI_HOME, "results")


def _load_profile():
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _chrome():
    from lib.chrome_manager import connect, start
    try:
        if not start():
            emit_error("could not start Chrome")
            sys.exit(1)
        b, ctx = connect()
    except RuntimeError as e:
        emit_error(str(e))
        sys.exit(1)
    if not ctx:
        emit_error("could not connect to Chrome")
        sys.exit(1)
    try:
        ctx.on("page", lambda pg: _wire_dialogs(pg))
    except Exception:
        pass
    for pg in ctx.pages:
        _wire_dialogs(pg)
    return b, ctx


@contextmanager
def chrome_session(state=None):
    """Context manager for Chrome lifecycle: starts Chrome, yields (page, ctx),
    closes browser on exit. Replaces 5 duplicated b,ctx=_chrome(); try;finally;close() patterns."""
    b, ctx = _chrome()
    try:
        page = _page_for(ctx, state)
        yield page, ctx
    finally:
        try:
            b.close()
        except Exception:
            pass


def _wire_dialogs(page):
    try:
        page.on("dialog", lambda d: d.accept() if not d.type else
                d.accept() if d.type == "confirm" else d.dismiss())
    except Exception:
        pass


def _page_for(ctx, state=None):
    if state:
        try:
            p = find_page(ctx, state)
            if p is not None:
                return p
        except Exception:
            pass
    pages = [p for p in ctx.pages if "about:blank" not in p.url and "chrome-error" not in p.url]
    if pages:
        return pages[-1]
    return ctx.new_page()


def _host(u):
    from urllib.parse import urlparse
    try:
        return urlparse(u or "").netloc.lower().split(":")[0]
    except Exception:
        return ""


_NEXT_KEYWORDS_JS = ["next", "continue", "continue to review", "review",
                     "review application", "next step", "save and continue"]

_ERROR_MARKERS = (
    "upstream connect error", "bad gateway", "service unavailable",
    "404 not found", "page not found", "access denied", "403 forbidden",
    "this site can't", "err_connection", "err_ssl", "application error",
)


def _is_error_page(page):
    try:
        text = (page_text(page) or "").strip().lower()
    except Exception:
        return False
    if len(text) > 400:
        return False
    return any(m in text for m in _ERROR_MARKERS)


def _url_fallbacks(url, state_url=""):
    from apply.common.registry import resolve as resolve_registry
    out = []
    for u in (url, state_url):
        if not u:
            continue
        reg = resolve_registry(u)
        if reg:
            for alt in reg.rewrite_urls(u):
                if alt not in out:
                    out.append(alt)
    return out


def _wait_for_fields(page, timeout=8):
    for _ in range(timeout):
        try:
            n = page.evaluate("""() => document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]), select, textarea'
            ).length""")
            if n:
                return True
        except Exception:
            return False
        time.sleep(1)
    return False


def _find_next_button(page):
    import json as _json
    try:
        cands = page.evaluate("""(kws) => {
            const out = [];
            const all = document.querySelectorAll('button, a, [role="button"], input[type=submit]');
            for (const el of all) {
                if (el.offsetParent === null || el.disabled) continue;
                if (!el.closest('dialog') && el.closest('nav, header, footer, [role=navigation], [role=banner], [role=contentinfo]')) continue;
                const t = ((el.textContent || el.value || '')).trim().toLowerCase().replace(/\\s+/g, ' ');
                if (!t || t.length > 30) continue;
                let score = 0;
                for (const kw of kws) {
                    if (t === kw) score = Math.max(score, 4);
                    else if (t.startsWith(kw)) score = Math.max(score, 3);
                }
                if (score >= 3) out.push({text: t.slice(0, 30), score});
            }
            out.sort((a, b) => b.score - a.score);
            return out;
        }""", _NEXT_KEYWORDS_JS)
        return cands[0] if cands else None
    except Exception:
        return None


_JUNK_TYPES = {"range", "search", "hidden", "submit", "button", "reset"}
_JUNK_LABEL_KW = ("progress", "scrubber", "search", "subscribe", "newsletter",
                  "volume", "playback", "password", "captcha")


def _is_junk_field(f):
    t = (f.get("type") or "").lower()
    if t in _JUNK_TYPES:
        return True
    lbl = (f.get("label") or "").lower()
    return any(k in lbl for k in _JUNK_LABEL_KW)


def _click_apply_button(page):
    try:
        return bool(page.evaluate("""() => {
            const kws = ['apply for this job', 'apply now', 'start application',
                         'apply to this', 'apply online', 'apply'];
            const all = Array.from(document.querySelectorAll('button, a, [role="button"]'))
                .filter(el => el.offsetParent !== null);
            for (const kw of kws) {
                for (const el of all) {
                    const t = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                    if (t === kw || t.startsWith(kw)) { el.click(); return true; }
                }
            }
            return false;
        }"""))
    except Exception:
        return False


def _probe_form(page, reg, jid, allow_vision=True):
    from apply.common import inspector as _insp
    orig = _insp._PROBE_STRATEGIES
    if not allow_vision:
        _insp._PROBE_STRATEGIES = [s for s in orig if s[0] != "vision"]
    try:
        orig_url = page.url
        pr = _insp.probe(page, registry_config=reg, jid=jid)
        fields = [f for f in (pr.fields or []) if not _is_junk_field(f)]
        if fields:
            pr.fields = fields
            if pr.strategy == "iframe":
                print(f"  Form in iframe ({len(fields)} fields) — using frame API", file=sys.stderr)
            return pr
        if pr.strategy != "iframe":
            iframe_pr = _insp._probe_iframes(page)
            iframe_fields = [f for f in (iframe_pr.fields or []) if not _is_junk_field(f)]
            if iframe_fields:
                iframe_pr.fields = iframe_fields
                print(f"  Form in iframe ({len(iframe_fields)} fields) — using frame API", file=sys.stderr)
                return iframe_pr
            srcs = getattr(iframe_pr, "iframe_srcs", []) or []
            src = next((s for s in srcs if s and "http" in s), "")
            if src:
                print(f"  Form lives in cross-origin iframe — navigating directly: {src[:90]}", file=sys.stderr)
                try:
                    page.goto(src, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
                    pr2 = _insp.probe(page, registry_config=reg, jid=jid)
                    pr2.fields = [f for f in (pr2.fields or []) if not _is_junk_field(f)]
                    if pr2.fields:
                        return pr2
                    page.goto(orig_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
        if _click_apply_button(page):
            time.sleep(2)
            pr2 = _insp.probe(page, registry_config=reg, jid=jid)
            pr2.fields = [f for f in (pr2.fields or []) if not _is_junk_field(f)]
            return pr2
        pr.fields = []
        return pr
    finally:
        _insp._PROBE_STRATEGIES = orig


def _set_files_any_frame(page, sel, path):
    try:
        page.set_input_files(sel, path)
        return True
    except Exception:
        pass
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            fr.set_input_files(sel, path)
            return True
        except Exception:
            continue
    return False


def _dismiss_confirm_modal(page):
    try:
        page.evaluate("""() => {
            const kws = ['yes', 'confirm', 'submit', 'ok', 'sure', 'continue'];
            const modals = document.querySelectorAll('[role="dialog"], .modal, [class*="confirm"], [class*="popup"]');
            for (const m of modals) {
                if (m.offsetParent === null) continue;
                for (const btn of m.querySelectorAll('button, a, [role="button"]')) {
                    const t = (btn.textContent || '').trim().toLowerCase();
                    if (kws.includes(t) || kws.some(k => t.startsWith(k))) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        }""")
    except Exception:
        pass


def _get_validation_errors(page):
    _JS = """() => {
        const sels = '[role="alert"], .field-error, .error-message, [class*="error"]:not([class*="error-icon"]), .form-error, .invalid-feedback, [class*="correction"], [class*="missing"], [class*="validation"]';
        const seen = new Set();
        const out = [];
        for (const el of document.querySelectorAll(sels)) {
            if (el.offsetParent === null) continue;
            const t = (el.textContent || '').trim();
            if (!t || t.length > 200 || seen.has(t)) continue;
            seen.add(t);
            out.push(t);
        }
        const body = document.body.innerText || '';
        const ashbyMatch = body.match(/Missing entry for required field[:\\s]*([^\\n]+)/i);
        if (ashbyMatch && !seen.has(ashbyMatch[0])) out.push('Missing: ' + ashbyMatch[1].trim());
        const correctionsMatch = body.match(/form needs corrections/i);
        if (correctionsMatch && !out.length) out.push('Form has validation errors');
        const reqMatches = body.matchAll(/([A-Za-z][A-Za-z\\s]+?)\\s+is required\\.?/g);
        for (const m of reqMatches) {
            const t = m[0].trim();
            if (!seen.has(t)) { seen.add(t); out.push(t); }
        }
        return out.slice(0, 15);
    }"""
    errors = []
    try:
        errors = page.evaluate(_JS) or []
    except Exception:
        pass
    if not errors:
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                errors = fr.evaluate(_JS) or []
                if errors:
                    break
            except Exception:
                continue
    return errors


def _empty_required(page):
    try:
        return int(page.evaluate("""() => {
            let n = 0;
            const seenRadios = new Set();
            for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea')) {
                if (el.offsetParent === null) continue;
                if (!(el.required || el.getAttribute('aria-required') === 'true')) continue;
                if (el.type === 'checkbox') { if (!el.checked) n++; continue; }
                if (el.type === 'radio') {
                    const name = el.name || el.id;
                    if (!name || seenRadios.has(name)) continue;
                    seenRadios.add(name);
                    const group = el.closest('form, dialog, [role=radiogroup], div') || document;
                    const checked = group.querySelector(`input[type=radio][name="${CSS.escape(name)}"]:checked`);
                    if (!checked) n++;
                    continue;
                }
                if (el.getAttribute('role') === 'combobox') {
                    const scope = el.closest('.select__control') || el.parentElement;
                    if (scope && scope.querySelector('.select__single-value')) continue;
                    const owns = el.getAttribute('aria-owns') || el.getAttribute('aria-controls');
                    if (owns) {
                        const lb = document.getElementById(owns);
                        if (lb && lb.querySelector('[aria-selected="true"]')) continue;
                    }
                }
                if (!el.value) n++;
            }
            return n;
        }""") or 0)
    except Exception:
        return 0


def _check_submit_success(ctx, page, pages_before_ids):
    if check_applied_signal(page) or has_success_text(page_text(page) or ""):
        return True, page
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            body = fr.evaluate("() => document.body.innerText") or ""
            if has_success_text(body):
                return True, page
        except Exception:
            continue
    new_pages = [p for p in ctx.pages if id(p) not in pages_before_ids
                 and "about:blank" not in p.url]
    for p in new_pages:
        try:
            if check_applied_signal(p) or has_success_text(page_text(p) or ""):
                return True, p
        except Exception:
            continue
    return False, None


def _click_action(page, text):
    try:
        return bool(page.evaluate("""(t) => {
            const all = document.querySelectorAll('button, a, [role="button"]');
            const vis = Array.from(all).filter(el => el.offsetParent !== null && !el.disabled);
            for (const el of vis) {
                if ((el.textContent || '').trim().toLowerCase() === t) { el.click(); return true; }
            }
            for (const el of vis) {
                if ((el.textContent || '').trim().toLowerCase().includes(t)) { el.click(); return true; }
            }
            return false;
        }""", (text or "").strip().lower()))
    except Exception:
        return False


def _field_key(f):
    sel = f.get("_sel") or f.get("selector") or ""
    if sel:
        return sel
    return (f.get("label", ""), f.get("id", ""), f.get("name", ""))


def _resolve_linkedin_apply(page):
    """On a LinkedIn job page, find the 'Apply' link and extract the real ATS URL."""
    import urllib.parse as _up
    try:
        href = page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = (a.textContent || '').trim().toLowerCase();
                if (text === 'apply') return a.href || null;
            }
            return null;
        }""")
        if not href:
            return None
        if 'linkedin.com/safety/go' in href:
            qs = _up.parse_qs(_up.urlparse(href).query)
            real = qs.get('url', [None])[0]
            return real or href
        return href
    except Exception:
        return None


def _fill_with_playwright(page, fields, profile, answers_override) -> tuple[list[dict], list[dict]]:
    from apply.strategies.dispatch import field_deterministic

    filled = []
    failed = []

    state = load_state()
    jid = state.get("jid", "")

    resume_path = None
    cover_path = None
    if jid:
        rd = os.path.join(RESULTS_DIR, jid)
        import glob
        resumes = glob.glob(os.path.join(rd, "*Resume*.pdf"))
        covers = glob.glob(os.path.join(rd, "*Cover*.pdf"))
        if resumes:
            resume_path = resumes[0]
        if covers:
            cover_path = covers[0]

    # Derive user's location parts for radio disambiguation
    # (e.g. Ashby 4-option sponsorship: "Yes...United States" vs "Yes...Canada"
    #  and 3-option office: "Yes, in the San Francisco office" vs "Yes, in the Toronto office")
    loc = (profile.get("location") or "")
    user_country = ""
    user_city = ""
    user_region = ""
    if loc and "," in loc:
        parts = [p.strip() for p in loc.split(",")]
        if parts:
            user_city = parts[0]
        if len(parts) >= 2:
            user_region = parts[1]
        if len(parts) >= 3:
            user_country = parts[-1]
    if not user_country:
        user_country = profile.get("country", "")
    # Comma-separated location keywords for matching (e.g. "ottawa,ontario,canada,toronto")
    user_loc_words = ",".join(w.strip().lower() for w in [user_city, user_region, user_country] if w)

    for f in fields:
        f["_country"] = user_loc_words

    for f in fields:
        label = f.get("label", "").strip()
        if not label:
            continue

        tag = (f.get("tag") or "").lower()
        ftype = (f.get("type") or "").lower()
        lc = label.lower()
        if (tag == "input" and (f.get("accept") or ftype == "file")) or "resume" in lc or "cv" in lc or "cover" in lc:
            ident = f"{lc} {(f.get('id') or '').lower()} {(f.get('name') or '').lower()}"
            path = cover_path if "cover" in ident else resume_path
            if path and os.path.exists(path):
                try:
                    sel = f.get("_sel") or f.get("selector") or ""
                    if not sel:
                        from apply.steps.probe import resolve_selector
                        sel = resolve_selector(page, f) or ""
                    if sel and _set_files_any_frame(page, sel, path):
                        filled.append({"label": label, "key": _field_key(f)})
                        continue
                    fallbacks = [
                        'input[type=file][id*="resume"]',
                        'input[type=file][accept*="pdf"]',
                        'input[type=file]',
                    ]
                    for fb in fallbacks:
                        if _set_files_any_frame(page, fb, path):
                            filled.append({"label": label, "key": _field_key(f)})
                            break
                    else:
                        print(f"  UPLOAD_FAIL: {label}", file=sys.stderr)
                except Exception as ue:
                    print(f"  UPLOAD_FAIL: {label} — {str(ue)[:120]}", file=sys.stderr)

        res = resolve(label, profile, answers_override,
                      autocomplete=f.get("autocomplete", ""),
                      field_name=f.get("name", ""),
                      field_id=f.get("id", ""))
        ans = res.value
        if ans is None:
            if tag == "input" and ftype == "checkbox" and os.environ.get("JI_AUTO_CONSENT") == "1":
                ans = "true"
            else:
                failed.append({**f, "_why": "no_answer"})
                continue

        try:
            if field_deterministic(page, f, ans):
                filled.append({"label": label, "key": _field_key(f)})
                if res.provenance == "answers_override":
                    learn_mapping(label, ans)
                if jid:
                    from apply.common.audit import log_field
                    log_field(jid, label, str(ans), res.provenance, filled=True)
            else:
                failed.append({**f, "_why": "fill_failed", "attempted": str(ans)[:50]})
                if jid:
                    from apply.common.audit import log_field
                    log_field(jid, label, str(ans), res.provenance, filled=False, reason="fill_failed")
        except Exception:
            failed.append({**f, "_why": "fill_failed", "attempted": str(ans)[:50]})

    return filled, failed


def _verify_with_ask_api(page, answers: dict) -> dict:
    try:
        from lib.ask_api import available, ask_bytes
        from apply.common.inspect_lib import form_jpeg
        if not available():
            return {"ok": False, "reason": "ask_api not available"}

        img_bytes = form_jpeg(page)
        prompt_lines = ["List every visible form field and its current value. Return as 'label: value' lines."]
        for k in answers:
            prompt_lines.append(f"  {k}: <expected: {answers[k]}>")
        prompt = "\n".join(prompt_lines)

        reply, err = ask_bytes(img_bytes, prompt)
        if err:
            return {"ok": False, "reason": str(err)}
        text = str(reply or "")
        mismatches = []
        for k, expected in answers.items():
            if k.lower() in text.lower():
                pass
            else:
                mismatches.append({"field": k, "expected": expected})
        return {
            "ok": len(mismatches) == 0,
            "mismatches": mismatches,
            "vision_text": text[:200],
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _detect_submit_button(page) -> str | None:
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            buttons = fr.evaluate("""() => {
                const all = document.querySelectorAll('button, input[type=submit]');
                return Array.from(all).filter(b => b.offsetParent !== null).map(b => ({
                    text: (b.textContent || b.value || '').trim().toLowerCase(),
                    type: b.type || '',
                }));
            }""")
            for b in buttons:
                if b.get("text") in ("submit", "submit application", "send", "send application") or b.get("type") == "submit":
                    if b.get("text"):
                        return b["text"]
                    return "submit"
        except Exception:
            continue
    candidates = scan_actions(page, ["submit", "submit application", "send application"])
    if candidates:
        for c in candidates:
            if not c.get("disabled") and c.get("tag") != "A":
                return c.get("text", "")
    try:
        buttons = page.evaluate("""() => {
            const all = document.querySelectorAll('button');
            return Array.from(all).filter(b => b.offsetParent !== null).map(b => b.textContent.trim().toLowerCase());
        }""")
        for b in buttons:
            if b in ("submit", "submit application", "send", "send application"):
                return b
    except Exception:
        pass
    return None


def _build_ans_dict(profile: dict, answers_override: dict = None) -> dict:
    result = {}
    if isinstance(profile, dict):
        result.update(profile.get("answers", {}))
    if answers_override:
        result.update(answers_override)
    for key in ("first_name", "last_name", "email", "phone", "full_name",
                "city", "state", "country", "linkedin_url", "github_url",
                "website", "headline"):
        if key not in result:
            val = profile.get(key) or profile.get(key.upper()) or profile.get(key.title())
            if val:
                result[key.replace("_", " ").title()] = val
    return result
