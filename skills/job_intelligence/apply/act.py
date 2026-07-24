#/usr/bin/env python3
"""act.py — Hybrid fill/submit: Playwright-first for deterministic fields,
Skyvern-fallback for complex fields, ask_api vision verification.

Flow:
  1. Start Chrome with CDP
  2. Playwright connects, reads DOM fields
  3. FieldFiller fills text/select/checkbox/radio/file fields deterministically
  4. Track filled vs failed fields
  5. If any failed → Skyvern fill_remaining() (vision-guided)
  6. ask_api.py vision verifies before submit
  7. Playwright clicks submit (or Skyvern fallback)
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.config import PROFILE_PATH, JI_HOME
from lib.db import get_conn
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
    if not start():
        emit_error("could not start Chrome")
        sys.exit(1)
    b, ctx = connect()
    if not ctx:
        emit_error("could not connect to Chrome")
        sys.exit(1)
    # ATSes gate submit behind window.confirm()/alert(). Auto-accept so the
    # pipeline never hangs silently on a modal it can't see (CDP can't focus
    # native dialogs). Mirrors Jobright's main-world alert suppressor.
    try:
        ctx.on("page", lambda pg: _wire_dialogs(pg))
    except Exception:
        pass
    for pg in ctx.pages:
        _wire_dialogs(pg)
    return b, ctx


def _wire_dialogs(page):
    try:
        page.on("dialog", lambda d: d.accept() if not d.type else
                d.accept() if d.type == "confirm" else d.dismiss())
    except Exception:
        pass


def _page_for(ctx, state=None):
    """Find the page showing this job's form. Prefers an exact match via
    find_page (jid tag / external_url) over the last non-blank tab."""
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
    """True when the landed page is a proxy/server error rather than a form."""
    try:
        text = (page_text(page) or "").strip().lower()
    except Exception:
        return False
    if len(text) > 400:
        return False
    return any(m in text for m in _ERROR_MARKERS)


def _url_fallbacks(url, state_url=""):
    """Alternate canonical URLs when the primary landing page fails.
    Platform knowledge lives in registry YAMLs (url_rewrites rules); the
    engine only evaluates them. Rules are tried against both the current
    and the original URL (redirects can move off the ATS domain)."""
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
    """Give SPA pages a moment to render inputs before probing."""
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
    """Best visible form-pagination candidate, or None. Strict: short text,
    exact/starts-with keyword match, and never nav/header/footer links."""
    import json as _json
    try:
        cands = page.evaluate("""(kws) => {
            const out = [];
            const all = document.querySelectorAll('button, a, [role="button"], input[type=submit]');
            for (const el of all) {
                if (el.offsetParent === null || el.disabled) continue;
                if (el.closest('nav, header, footer, [role=navigation], [role=banner], [role=contentinfo]')) continue;
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
    """Non-application inputs: media controls, site search, footer forms."""
    t = (f.get("type") or "").lower()
    if t in _JUNK_TYPES:
        return True
    lbl = (f.get("label") or "").lower()
    return any(k in lbl for k in _JUNK_LABEL_KW)


def _click_apply_button(page):
    """Click an Apply-ish button to open the application form (broad match)."""
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
    """Probe for application-form fields with junk filtering. If nothing usable
    is found, tries clicking an Apply-ish button once and re-probes.
    When the form lives inside an iframe, navigates directly to the iframe URL
    so all fills run in the main frame (keyboard/focus/upload all work there).
    Vision probing (LLM screenshot) only runs when allow_vision — it costs
    30-60s per call, so it's restricted to the first form page."""
    from apply.common import inspector as _insp
    orig = _insp._PROBE_STRATEGIES
    if not allow_vision:
        _insp._PROBE_STRATEGIES = [s for s in orig if s[0] != "vision"]
    try:
        pr = _insp.probe(page, registry_config=reg, jid=jid)
        fields = [f for f in (pr.fields or []) if not _is_junk_field(f)]
        if fields:
            pr.fields = fields
            if pr.strategy == "iframe":
                srcs = getattr(pr, "iframe_srcs", []) or []
                src = next((s for s in srcs if s and "http" in s), "")
                if src:
                    print(f"  Form lives in an iframe — navigating directly: {src[:90]}", file=sys.stderr)
                    try:
                        page.goto(src, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(2)
                        pr = _insp.probe(page, registry_config=reg, jid=jid)
                        pr.fields = [f for f in (pr.fields or []) if not _is_junk_field(f)]
                    except Exception:
                        pass
            return pr
        # Nothing usable — maybe the form hides behind an Apply button
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
    """set_input_files on the main frame, then any iframe (cross-origin frames
    are reachable via Playwright's frame objects even when page-level fails)."""
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
    """Dismiss custom (DOM-based) confirmation modals some ATSes show after
    clicking Submit. page.on('dialog') only catches native confirm()/alert()."""
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
    """Scan for validation error messages after a failed submit attempt."""
    try:
        return page.evaluate("""() => {
            const sels = '[role="alert"], .field-error, .error-message, [class*="error"]:not([class*="error-icon"]), .form-error, .invalid-feedback';
            const seen = new Set();
            const out = [];
            for (const el of document.querySelectorAll(sels)) {
                if (el.offsetParent === null) continue;
                const t = (el.textContent || '').trim();
                if (!t || t.length > 200 || seen.has(t)) continue;
                seen.add(t);
                out.push(t);
            }
            return out.slice(0, 15);
        }""") or []
    except Exception:
        return []


def _empty_required(page):
    try:
        return int(page.evaluate("""() => {
            let n = 0;
            for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea')) {
                if (el.offsetParent === null) continue;
                if (!(el.required || el.getAttribute('aria-required') === 'true')) continue;
                if (el.type === 'checkbox') { if (!el.checked) n++; continue; }
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
    """Check for submit success across all pages — the current page AND any
    new tabs that opened (target='_blank' confirmations). Returns
    (success: bool, page_with_signal or None)."""
    if check_applied_signal(page) or has_success_text(page_text(page) or ""):
        return True, page
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
    """Click a button/link by visible text (exact, then contains)."""
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
    """Unique key for dedup — selector preferred, then (label, id, name) composite.
    Labels like 'Attach' can appear on multiple fields (resume + cover); selectors
    are unique per element."""
    sel = f.get("_sel") or f.get("selector") or ""
    if sel:
        return sel
    return (f.get("label", ""), f.get("id", ""), f.get("name", ""))


def _fill_with_playwright(page, fields, profile, answers_override) -> tuple[list[dict], list[dict]]:
    """Fill all detectable fields using resolve() for label→answer matching and
    FieldFiller dispatch for the actual fill.
    Returns (filled_records, failed_records) where each filled record is
    {"label": ..., "key": ...} and each failed record is the field dict
    plus _why: 'no_answer' (nothing in profile maps to it) or 'fill_failed'
    (we have a value but the widget rejected it)."""
    from apply.strategies.dispatch import field_deterministic

    filled = []
    failed = []

    state = load_state()
    jid = state.get("jid", "")

    # Find resume/cover files for file uploads
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

    for f in fields:
        label = f.get("label", "").strip()
        if not label:
            continue

        # File upload — handle with Playwright directly, no answer needed
        tag = (f.get("tag") or "").lower()
        ftype = (f.get("type") or "").lower()
        lc = label.lower()
        if (tag == "input" and (f.get("accept") or ftype == "file")) or "resume" in lc or "cv" in lc or "cover" in lc:
            # Route by id/name too: Greenhouse labels both inputs "Attach"
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
                    print(f"  UPLOAD_FAIL: {label}", file=sys.stderr)
                except Exception as ue:
                    print(f"  UPLOAD_FAIL: {label} — {str(ue)[:120]}", file=sys.stderr)
            # No file on disk and no answer either → fails below if unresolvable

        # Resolve label → answer value (deterministic, profile-driven)
        res = resolve(label, profile, answers_override,
                      autocomplete=f.get("autocomplete", ""),
                      field_name=f.get("name", ""),
                      field_id=f.get("id", ""))
        ans = res.value
        if ans is None:
            # Consent/agreement checkboxes: only auto-check when explicitly
            # enabled via JI_AUTO_CONSENT=1 (off by default to avoid
            # circumventing ToS — user must opt in).
            if tag == "input" and ftype == "checkbox" and os.environ.get("JI_AUTO_CONSENT") == "1":
                ans = "true"
            else:
                failed.append({**f, "_why": "no_answer"})
                continue

        # Use the standard field_deterministic dispatch from strategies
        try:
            if field_deterministic(page, f, ans):
                filled.append({"label": label, "key": _field_key(f)})
                if res.provenance == "answers_override":
                    # User/orchestrator-supplied answer that worked → remember it
                    learn_mapping(label, ans)
            else:
                failed.append({**f, "_why": "fill_failed", "attempted": str(ans)[:50]})
        except Exception:
            failed.append({**f, "_why": "fill_failed", "attempted": str(ans)[:50]})

    return filled, failed


def _verify_with_ask_api(page, answers: dict) -> dict:
    """Use ask_api.py vision to verify field values on the page.
    Returns {ok: bool, mismatches: list}."""
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
    """Find the submit button on the page using scan_actions + fallbacks."""
    candidates = scan_actions(page, ["submit", "submit application", "send application", "apply"])
    if candidates:
        for c in candidates:
            if not c.get("disabled"):
                return c.get("text", "")
    # Direct keyword match in buttons
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
    """Build the full answer dict from profile + --answers override.
    Includes both static answers dict and ephemeral derivations."""
    result = {}
    if isinstance(profile, dict):
        result.update(profile.get("answers", {}))
    if answers_override:
        result.update(answers_override)
    # Ephemeral derivations (name, location, contact info)
    for key in ("first_name", "last_name", "email", "phone", "full_name",
                "city", "state", "country", "linkedin_url", "github_url",
                "website", "headline"):
        if key not in result:
            val = profile.get(key) or profile.get(key.upper()) or profile.get(key.title())
            if val:
                result[key.replace("_", " ").title()] = val
    return result


def cmd_fill(jid, answers: dict = None, verify: bool = True, max_pages: int = 4,
             quick: bool = False):
    """Hybrid fill: Playwright-first with multi-page loop, Skyvern-fallback.
    quick=True: deterministic-only pass, no vision verify, no Skyvern — fast
    feedback on what's fillable and what's missing."""
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1
    stage, job_state = db_row["stage"], db_row["state"]

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    state["jid"] = jid

    url = state.get("external_url") or state.get("url", "")
    if not url:
        emit_error("no external_url in state — run 'apply navigate <jid>' first")
        return 1
    orig_url = url  # pre-redirect ATS URL — fallback rules may match it

    # Build the full answer dict
    profile = _load_profile()
    ans_dict = _build_ans_dict(profile, answers)
    if not ans_dict:
        emit_error("no answers resolved — check profile or --answers")
        return 1

    from apply.common.registry import resolve as resolve_registry

    # Phase 1: Playwright deterministic fill, looping through form pages
    b, ctx = _chrome()
    filled_all, failed_all = [], []
    filled_keys = set()
    field_total = 0
    submit_visible = False

    try:
        page = _page_for(ctx, state)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        if handle_captcha(page, state):
            emit_status("captcha", "CAPTCHA still present after timeout")
            return 1

        # Redirect detection — ATS links often bounce to branded career sites
        if _host(page.url) and _host(url) and _host(page.url) != _host(url):
            print(f"  REDIRECT: {_host(url)} -> {_host(page.url)}", file=sys.stderr)
            state["external_url"] = page.url
            url = page.url

        # Broken landing page recovery — try canonical fallback URLs
        fallbacks = _url_fallbacks(url, orig_url)
        if _is_error_page(page):
            for alt in fallbacks:
                print(f"  Landing page broken — trying fallback: {alt[:90]}", file=sys.stderr)
                try:
                    page.goto(alt, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(2)
                except Exception:
                    continue
                if not _is_error_page(page):
                    state["external_url"] = page.url
                    url = page.url
                    break
            fallbacks = []

        tag_page(page, jid)
        reg = resolve_registry(page.url) or resolve_registry(orig_url)
        if reg and reg.page_range:
            try:
                max_pages = min(max_pages, int(reg.page_range[-1]))
            except Exception:
                pass
        _wait_for_fields(page, timeout=8)

        seen = set()
        for page_num in range(1, max_pages + 1):
            pr = _probe_form(page, reg, jid, allow_vision=(page_num == 1))
            # Probing may navigate (iframe-direct, apply-button) — keep the
            # handoff URL pointed at the page that actually shows the form
            if page.url and page.url != url and "about:blank" not in page.url:
                state["external_url"] = page.url
                url = page.url
            fields = pr.fields or []
            field_total += len(fields)
            if not fields:
                print(f"  No fields detected (page {page_num}, strategy={pr.strategy})", file=sys.stderr)
                if page_num == 1 and fallbacks:
                    alt = fallbacks.pop(0)
                    print(f"  Trying fallback URL: {alt[:90]}", file=sys.stderr)
                    try:
                        page.goto(alt, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(2)
                        _wait_for_fields(page, timeout=8)
                        state["external_url"] = page.url
                        url = page.url
                        reg = resolve_registry(page.url)
                    except Exception:
                        pass
                    continue

            filled, failed = _fill_with_playwright(page, fields, profile, answers)
            for rec in filled:
                if rec["key"] not in filled_keys:
                    filled_keys.add(rec["key"])
                    filled_all.append(rec["label"])
            for rec in failed:
                k = _field_key(rec)
                if k not in filled_keys and k not in {_field_key(r) for r in failed_all}:
                    failed_all.append(rec)

            if fields:
                print(f"  Page {page_num}: filled {len(filled)}/{len(fields)}"
                      + (f" — failed: {', '.join(r['label'] for r in failed[:5])}" if failed else ""), file=sys.stderr)

            # Multi-page progression: click Next/Continue/Review, stop when gone
            fp = (page.url, tuple(sorted(f.get("label", "") for f in fields)))
            if fp in seen:
                break
            seen.add(fp)
            nxt = _find_next_button(page)
            if not nxt:
                submit_visible = bool(_detect_submit_button(page))
                break
            # Don't advance with empty required fields — the ATS will bounce us
            # back with validation errors (loop risk). Skyvern gets the rest.
            empt = _empty_required(page)
            if empt:
                print(f"  {empt} required field(s) still empty — not advancing", file=sys.stderr)
                break
            print(f"  Multi-page: clicking '{nxt['text']}'", file=sys.stderr)
            if not _click_action(page, nxt["text"]):
                break
            time.sleep(2)
            if handle_captcha(page, state):
                emit_status("captcha", "CAPTCHA during multi-page navigation")
                return 1
            handle_session_timeout(page)

        # Phase 1.5: vision verify — only when Playwright covered everything
        # (if Skyvern is taking over anyway, the extra LLM call buys nothing)
        remaining_now = [r for r in failed_all if _field_key(r) not in filled_keys]
        if verify and filled_all and not remaining_now and field_total > 0:
            try:
                verify_result = _verify_with_ask_api(page, ans_dict)
                if not verify_result.get("ok"):
                    mm = verify_result.get("mismatches", [])
                    if mm:
                        print(f"  Vision flag: {len(mm)} field(s) may need review", file=sys.stderr)
            except Exception as ve:
                print(f"  Vision verify skipped: {ve}", file=sys.stderr)

    except Exception as e:
        emit_error(f"Playwright fill failed: {e}")
        return 1
    finally:
        try:
            b.close()
        except Exception:
            pass

    # ─── Failure triage — Skyvern is the LAST resort ───
    # fill_failed (we have the value, widget rejected it) → Skyvern can act.
    # no_answer + required → Skyvern infers from the full answer dict.
    # no_answer + optional → SKIPPED entirely: no LLM can invent profile data
    # the user never provided, and optional fields don't block submission.
    remaining = [r for r in failed_all if _field_key(r) not in filled_keys]
    skyvern_fields = [r for r in remaining if r["_why"] == "fill_failed" or r.get("required")]
    skipped = [r for r in remaining if r["_why"] == "no_answer" and not r.get("required")]

    if remaining:
        from apply.common.output import emit_fill_report
        emit_fill_report(len(filled_all), remaining, 1, profile)
    if skipped:
        skip_labels = [r["label"] for r in skipped]
        print(f"  SKIPPED (optional, no answer): {', '.join(skip_labels)}", file=sys.stderr)

    # Phase 2: Skyvern fills remaining (non-blocking) — skipped in --quick mode
    skyvern_result = None
    needs_skyvern = (bool(skyvern_fields) or field_total == 0) and not quick
    if needs_skyvern:
        # Cap the LLM budget: ~3 steps per field + navigation slack
        n = len(skyvern_fields) if skyvern_fields else 8
        budget = min(30, 6 + 3 * n)
        print(f"  Handing off {n} field(s) to Skyvern (non-blocking, max_steps={budget})...", file=sys.stderr)
        from apply.common.skyvern_bridge import fill_remaining as _fill_remaining
        try:
            skyvern_result = _fill_remaining(
                url=url,
                answers=ans_dict,
                # Skipped optional fields are listed as "already filled" so
                # Skyvern leaves them alone
                filled_fields=filled_all + [r["label"] for r in skipped],
                wait=False,  # don't block — poll via run_id
                timeout=30,  # just need the initial response
                max_steps=budget,
            )
            status = skyvern_result.get("status", "unknown")
            print(f"  Skyvern: {status}", file=sys.stderr)
            if skyvern_result.get("browser_session_id"):
                state["browser_session_id"] = skyvern_result["browser_session_id"]
            if skyvern_result.get("run_id"):
                state["fill_run_id"] = skyvern_result["run_id"]
                state["fill_run_started"] = time.time()
                print(f"  Skyvern run_id: {state['fill_run_id']}", file=sys.stderr)
                print(f"  Check status later via 'apply verify {jid}'", file=sys.stderr)
        except Exception as se:
            print(f"  Skyvern fill failed: {se}", file=sys.stderr)

    # Save state
    state["filled_count"] = len(filled_all)
    state["failed_fields"] = [r["label"] for r in skyvern_fields]
    state["skipped_fields"] = [r["label"] for r in skipped]
    if not (skyvern_result and skyvern_result.get("run_id")):
        state.pop("fill_run_id", None)
        state.pop("fill_run_started", None)
    save_state(state)

    if field_total == 0 and not skyvern_result:
        emit_status("unknown", "no fields found by Playwright or Skyvern")
        return 1

    msg = f"Playwright: {len(filled_all)} fields"
    if skyvern_fields:
        msg += f", to Skyvern: {len(skyvern_fields)}"
    if skipped:
        msg += f", skipped optional: {len(skipped)}"
    if skyvern_result:
        msg += f" + Skyvern: {skyvern_result.get('status', 'unknown')}"
    emit_status("filled", msg)

    if skyvern_result and skyvern_result.get("run_id"):
        emit_next("verify", "poll Skyvern fill progress")
    elif submit_visible or filled_all:
        emit_next("submit")
    else:
        emit_next("act --inspect", "no fillable fields and no Skyvern run")
    return 0


def cmd_next(jid):
    """Click Next/Continue on a multi-page form using Playwright."""
    state = load_state()
    b, ctx = _chrome()
    page = _page_for(ctx, state)
    try:
        url = state.get("external_url") or state.get("url", "")
        cur = page.url or ""
        if url and (not cur or "about:blank" in cur or "chrome-error" in cur):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1)

        # Try Playwright first
        nxt = _find_next_button(page)
        if nxt and _click_action(page, nxt["text"]):
            time.sleep(2)
            emit_status("navigated", f"clicked '{nxt['text']}'")
            emit_next("fill")
            return 0

        # Fallback: Skyvern click_next
        print(f"  No Next button found via DOM — using Skyvern", file=sys.stderr)
        from apply.common.skyvern_bridge import click_next
        result = click_next(url=page.url, timeout=120)
        if result.get("status") == "completed":
            emit_status("navigated", "skyvern clicked Next")
            emit_next("fill")
            return 0

        emit_error("no Next/Continue button found")
        return 1
    finally:
        try:
            b.close()
        except Exception:
            pass


def cmd_investigate(jid):
    """Deep analysis of an unknown-platform form. Free probe cascade first;
    if 0 fields, Skyvern's investigator (one blocking LLM task) describes the
    form so a registry entry/handler can be written. Saves report to results."""
    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    url = state.get("external_url") or state.get("url", "")
    if not url:
        emit_error("no url in state — run 'apply navigate <jid>' first")
        return 1

    from apply.common.inspector import probe as probe_page
    from apply.common.registry import resolve as resolve_registry

    b, ctx = _chrome()
    try:
        page = _page_for(ctx, state)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        if handle_captcha(page, state):
            emit_status("captcha", "CAPTCHA still present after timeout")
            return 1
        pr = probe_page(page, registry_config=resolve_registry(page.url), jid=jid)
        if pr.field_count > 0:
            print(f"  Probe found {pr.field_count} fields (strategy={pr.strategy}):", file=sys.stderr)
            for f in pr.fields:
                print(f"    [{f.get('type','?')}] {f.get('label','?')}", file=sys.stderr)
            emit_status("investigated", f"{pr.field_count} fields via {pr.strategy} — no Skyvern needed")
            emit_next("act --fill")
            return 0
    finally:
        try:
            b.close()
        except Exception:
            pass

    # 0 fields from DOM — try ask_api vision first (1 LLM call vs Skyvern's
    # 10-step agent loop). Screenshot + structured extraction prompt.
    from lib.ask_api import available as _vision_available
    if _vision_available():
        from lib.ask_api import ask_bytes
        from apply.common.inspect_lib import page_jpeg, form_jpeg
        print(f"  DOM probe found nothing — analyzing with vision (1 LLM call)...", file=sys.stderr)
        try:
            img = form_jpeg(page)
            reply, err = ask_bytes(
                img,
                "Analyze this job application form. List every visible form field "
                "as 'LABEL | TYPE | REQUIRED | OPTIONS' lines. "
                "Also state: is this multi-page? What buttons exist (Next, Submit, etc.)?",
                max_tokens=2048,
            )
            if not err and reply:
                rd = os.path.join(RESULTS_DIR, jid)
                os.makedirs(rd, exist_ok=True)
                rpt_path = os.path.join(rd, "investigate_report.json")
                import json as _json
                with open(rpt_path, "w", encoding="utf-8") as fh:
                    _json.dump({"url": url, "method": "ask_api", "analysis": reply}, fh, indent=2)
                state["investigate_report"] = rpt_path
                save_state(state)
                print(f"  Report saved: {rpt_path}", file=sys.stderr)
                print(f"  Vision analysis:\n{reply[:500]}", file=sys.stderr)
                emit_status("investigated", f"report at {rpt_path}")
                emit_next("act --fill")
                return 0
            if err:
                print(f"  VISION_FAIL: {err} — falling back to Skyvern", file=sys.stderr)
        except Exception as ve:
            print(f"  VISION_FAIL: {ve} — falling back to Skyvern", file=sys.stderr)

    # Vision unavailable or failed — Skyvern investigator as last resort
    print(f"  Running Skyvern investigator (slow, 10-step agent)...", file=sys.stderr)
    from apply.common.skyvern_bridge import SkyvernExtraction
    report = SkyvernExtraction().investigate_form(url, timeout=300)
    if not report:
        emit_error("Skyvern investigation returned nothing")
        return 1

    rd = os.path.join(RESULTS_DIR, jid)
    os.makedirs(rd, exist_ok=True)
    rpt_path = os.path.join(rd, "investigate_report.json")
    with open(rpt_path, "w", encoding="utf-8") as fh:
        json.dump({"url": url, **report}, fh, indent=2)
    state["investigate_report"] = rpt_path
    save_state(state)

    fields = (report.get("fields") or {})
    n = len(fields.get("fields", [])) if isinstance(fields, dict) else 0
    print(f"  Report saved: {rpt_path}", file=sys.stderr)
    print(f"  Skyvern saw {n} fields, multi_page={fields.get('multi_page') if isinstance(fields, dict) else '?'}", file=sys.stderr)
    emit_status("investigated", f"report at {rpt_path}")
    emit_next("none", "write a registry YAML for this platform from the report")
    return 0


def cmd_submit(jid, confirm=False):
    """Submit the form: Playwright finds and clicks submit, Skyvern fallback."""
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1
    stage, job_state = db_row["stage"], db_row["state"]

    if stage == "applied":
        emit_status("already applied")
        emit_next("verify")
        return 0

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    state["jid"] = jid

    url = state.get("external_url") or state.get("url", "")
    if not url:
        emit_error("no external_url in state")
        return 1

    browser_session_id = state.get("browser_session_id", "")

    # Phase 1: Playwright tries to click submit
    b, ctx = _chrome()
    page = _page_for(ctx, state)

    try:
        # Only navigate if the current tab isn't already on the form —
        # reloading could wipe values filled by Playwright/Skyvern.
        cur = page.url or ""
        if not cur or "about:blank" in cur or "chrome-error" in cur:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

        if handle_captcha(page, state):
            emit_status("captcha", "CAPTCHA still present after timeout")
            return 1

        # Deterministic re-fill: Skyvern runs navigate fresh, which wipes
        # Playwright-filled values on ATSes without server-side persistence.
        # A fast local pass restores them — idempotent and LLM-free.
        try:
            from apply.common.registry import resolve as resolve_registry
            profile = _load_profile()
            pr = _probe_form(page, resolve_registry(page.url), jid, allow_vision=False)
            fields = pr.fields or []
            if fields:
                refilled, _ = _fill_with_playwright(page, fields, profile, None)
                if refilled:
                    print(f"  Re-fill: {len(refilled)} fields restored/confirmed", file=sys.stderr)
            empt = _empty_required(page)
            if empt:
                print(f"  WARN: {empt} required field(s) still empty before submit", file=sys.stderr)
        except Exception as re_:
            print(f"  Re-fill skipped: {re_}", file=sys.stderr)

        submit_text = _detect_submit_button(page)
        clicked = False
        if submit_text:
            print(f"  Found submit button: '{submit_text}'", file=sys.stderr)
            try:
                page.click(f'button:text("{submit_text}")')
                clicked = True
            except Exception:
                try:
                    page.click(f'text="{submit_text}"')
                    clicked = True
                except Exception:
                    print(f"  Could not click submit button via Playwright", file=sys.stderr)

        if clicked:
            # Wait for page response — not a fixed sleep. domcontentloaded
            # catches same-tab navigation; networkidle catches async updates.
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass

            # Dismiss custom (non-native) confirmation modals some ATSes show
            _dismiss_confirm_modal(page)
            time.sleep(1)

            # Check if submit succeeded — current page AND new tabs
            # (target=_blank confirmations open in a new tab we'd otherwise miss)
            pages_before = {id(p) for p in ctx.pages}
            success, success_page = _check_submit_success(ctx, page, pages_before)
            if success:
                mark_applied(jid)
                emit_status("submitted", "Playwright clicked submit")
                emit_next("verify")
                return 0

            # Check for validation errors — submit was rejected
            errors = _get_validation_errors(page)
            if errors:
                print(f"  VALIDATION_ERRORS: {len(errors)} field(s) blocked submit", file=sys.stderr)
                for e in errors[:5]:
                    print(f"    ! {e[:80]}", file=sys.stderr)
                state["submit_errors"] = errors
                save_state(state)
                emit_status("validation_error", f"{len(errors)} field(s) need fixing")
                emit_next("act --fill", "fix validation errors then resubmit")
                return 1

            # Check for multi-page (Review step)
            next_btn = _detect_submit_button(page)
            if next_btn:
                print(f"  Review step detected — clicking '{next_btn}'", file=sys.stderr)
                try:
                    page.click(f'button:text("{next_btn}")')
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                success, _ = _check_submit_success(ctx, page, pages_before)
                if success:
                    mark_applied(jid)
                    emit_status("submitted", "Playwright review->submit")
                    emit_next("verify")
                    return 0

            # DOM signals inconclusive — try ask_api vision (1 LLM call)
            # before falling back to Skyvern (15-step agent loop)
            try:
                from lib.ask_api import available, ask_bytes
                if available():
                    from apply.common.inspect_lib import page_jpeg
                    img = page_jpeg(page, full=False)
                    reply, err = ask_bytes(
                        img,
                        "Did this job application submit successfully? "
                        "Look for: confirmation message, thank you text, "
                        "application ID, success indicator. "
                        "Answer only YES or NO.",
                    )
                    if not err and (reply or "").strip().lower().startswith("yes"):
                        mark_applied(jid)
                        emit_status("submitted", "vision confirmed via ask_api")
                        emit_next("verify")
                        return 0
                    if err:
                        print(f"  VISION_SKIP: {err}", file=sys.stderr)
            except Exception as ve:
                print(f"  VISION_SKIP: {ve}", file=sys.stderr)

        # Phase 2: Skyvern click submit — only when Playwright couldn't click
        # (button not found or click failed), NOT when it clicked but we
        # couldn't confirm. That's a verification gap, not a click failure.
        if not clicked:
            print(f"  Playwright could not click submit — using Skyvern", file=sys.stderr)
            from apply.common.skyvern_bridge import click_submit
            result = click_submit(url=page.url, browser_session_id=browser_session_id, timeout=180)
            if result.get("status") == "completed":
                mark_applied(jid)
                emit_status("submitted", "Skyvern clicked submit")
                emit_next("verify")
                return 0

        emit_status("unknown", "submit attempts inconclusive — check manually")
        emit_next("verify")
        return 1
    except Exception as e:
        emit_error(f"submit failed: {e}")
        return 1
    finally:
        try:
            b.close()
        except Exception:
            pass


def cmd_inspect(jid):
    """Full page analysis: screenshot, HTML, fields, buttons."""
    from lib.ask_api import available as _vision_available
    from lib.chrome_manager import CDP_URL

    b, ctx = _chrome()
    state = load_state()
    page = _page_for(ctx, state)

    url = state.get("external_url") or state.get("url", "")
    if url:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        except Exception as e:
            print(f"  GOTO_ERR: {e}", file=sys.stderr)

    from apply.common.inspect_lib import capture, page_jpeg
    from apply.common.page_helpers import read_page, scan_actions

    jid = state.get("jid", jid)
    img_path = capture(page, jid, prefix="inspect")
    print(f"  IMG: {img_path}", file=sys.stderr)

    info = read_page(page)
    print(f"  FIELDS: {info.get('fieldCount', 0)} detected", file=sys.stderr)
    for f in info.get("fields", []):
        opts = f.get("options", [])
        opt_str = f" ({len(opts)} options)" if opts else ""
        print(f"    [{f.get('type','?')}] {f.get('label','?')}{opt_str}", file=sys.stderr)

    submit_candidates = scan_actions(page, ["submit", "send", "apply", "next", "continue"])
    print(f"  BUTTONS:", file=sys.stderr)
    for c in submit_candidates[:10]:
        print(f"    [{c.get('score',0)}] '{c.get('text','')}' ({c.get('tag','')})", file=sys.stderr)

    print(f"  URL: {page.url[:120]}", file=sys.stderr)
    print(f"  CDP: {CDP_URL}", file=sys.stderr)

    if _vision_available():
        print(f"  ask: lib/ask_api.py --img {img_path} --prompt '?'", file=sys.stderr)

    return 0


def run(args):
    cmd = args.get("command", "")
    jid = args.get("jid", "")

    if cmd == "fill":
        answers = None
        raw = args.get("--answers")
        if raw:
            try:
                answers = json.loads(raw)
            except json.JSONDecodeError:
                emit_error(f"invalid --answers JSON: {raw}")
                return 1
        verify = not args.get("--no-verify", False)
        return cmd_fill(jid, answers, verify=verify,
                        max_pages=args.get("--max-pages", 4),
                        quick=args.get("--quick", False))

    elif cmd == "next":
        return cmd_next(jid)

    elif cmd == "back":
        print("  Back: not implemented in hybrid mode — use browser back", file=sys.stderr)
        return 1

    elif cmd == "submit":
        return cmd_submit(jid, confirm=args.get("--confirm", False))

    elif cmd == "inspect":
        return cmd_inspect(jid)

    elif cmd == "investigate":
        return cmd_investigate(jid)

    else:
        emit_error(f"unknown act command: {cmd}")
        return 1
