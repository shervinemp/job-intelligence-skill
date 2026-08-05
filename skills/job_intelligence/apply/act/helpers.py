"""act/helpers.py — Shared helpers for all act commands.

Chrome lifecycle, DOM interaction (JS evaluation), field probing,
validation scanning, submit detection, profile loading, answer dict
construction, vision verification. The fill loop (including file
upload + consent mechanics) lives in apply/common/fill_runner.py.
"""
import json, os, re, sys, time
from contextlib import contextmanager

from lib.config import PROFILE_PATH, JI_HOME
from apply.common.output import emit_error
from apply.common.page_helpers import (
    load_state, page_text, find_page,
    check_applied_signal, scan_actions,
)
from apply.common.resolve import resolve, learn_mapping, _build_ephemeral
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
def chrome_session(state=None, isolate_host=""):
    """Context manager for Chrome lifecycle: starts Chrome, yields (page, ctx),
    closes browser on exit. Replaces 5 duplicated b,ctx=_chrome(); try;finally;close() patterns.

    F3 session isolation: when `isolate_host` is given (the employer's ATS
    domain), cookies belonging to OTHER hosts are stripped from the context
    before any navigation. One shared profile persists cookies across
    companies, so company A's session can leak into company B's apply flow
    (and vice versa) — stripping cross-host cookies keeps each employer's
    session scoped to its own domain.
    """
    b, ctx = _chrome()
    try:
        if isolate_host:
            _isolate_session(ctx, isolate_host)
        page = _page_for(ctx, state)
        yield page, ctx
    finally:
        try:
            b.close()
        except Exception:
            pass


def _isolate_session(ctx, host):
    """Strip cookies for hosts OTHER than `host` (and its subdomains), so the
    current employer's session is not contaminated by another employer's
    cookies. Cookies for the target host itself are kept (login walls persist
    per-employer as designed)."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return
    try:
        for cookie in ctx.cookies():
            domain = (cookie.get("domain") or "").lower().lstrip(".")
            if not domain:
                continue
            keep = (domain == host) or domain.endswith("." + host) \
                or host.endswith("." + domain)
            if not keep:
                try:
                    ctx.clear_cookies(**{k: cookie[k] for k in (
                        "name", "domain", "path") if k in cookie})
                except Exception:
                    pass
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


def _find_next_button(page):
    """Find the best 'Next'/'Continue' button. Delegates to page_state.find_buttons
    with the next-keyword list. Returns {text, score} dict or None."""
    from apply.common.page_state import find_buttons
    cands = find_buttons(page, _NEXT_KEYWORDS_JS, scope="any")
    return cands[0] if cands else None


def _click_action(page, text):
    """Click a button by text. Delegates to page_state.click_button_by_text
    which handles dialog offsetParent and modal preference."""
    from apply.common.page_state import click_button_by_text
    return click_button_by_text(page, text, prefer_dialog=True)

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
    """Delegate to page_state.wait_for_form."""
    from apply.common.page_state import wait_for_form
    return wait_for_form(page, timeout)


_JUNK_TYPES = {"range", "search", "hidden", "submit", "button", "reset"}
_JUNK_LABEL_KW = ("progress", "scrubber", "search", "subscribe", "newsletter",
                  "volume", "playback", "password", "captcha",
                  "robot", "honeypot", "leave this blank", "leave empty",
                  "for bots", "spam trap", "do not fill", "do not enter")


def _is_junk_field(f):
    if f.get("is_honeypot"):
        return True
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
        # Closed-shadow-DOM escape hatch: DOM queries cannot pierce closed
        # roots, so a form inside them looks empty — which would be
        # misclassified (login wall / expired). The browser's OWN
        # accessibility tree exposes them; use it before giving up.
        if not fields:
            try:
                from apply.common.a11y_reader import read_fields_from_a11y
                a11y_fields = read_fields_from_a11y(page)
                a11y_fields = [f for f in a11y_fields if not _is_junk_field(f)]
                if a11y_fields:
                    print(f"  {len(a11y_fields)} field(s) via accessibility tree "
                          f"(closed shadow DOM)", file=sys.stderr)
                    pr.fields = a11y_fields
                    return pr
            except Exception:
                pass
        if _click_apply_button(page):
            # The click may trigger a navigation (SSO redirect, lazy
            # apply link) — settle before probing so the JS context
            # isn't destroyed mid-evaluate.
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            time.sleep(2)
            pr2 = _insp.probe(page, registry_config=reg, jid=jid)
            pr2.fields = [f for f in (pr2.fields or []) if not _is_junk_field(f)]
            return pr2
        pr.fields = []
        return pr
    finally:
        _insp._PROBE_STRATEGIES = orig


def _dismiss_confirm_modal(page):
    try:
        page.evaluate("""() => {
            const kws = ['yes', 'confirm', 'submit', 'ok', 'sure', 'continue',
                         'accept', 'accept all', 'agree', 'allow', 'allow all',
                         'accept and continue'];
            const modals = document.querySelectorAll('[role="dialog"], .modal, [class*="confirm"], [class*="popup"], [class*="cookie"], [class*="consent"]');
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
                    const _escN = name.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
                    const checked = group.querySelector('input[type=radio][name="' + _escN + '"]:checked');
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
    # Check iframes first (Greenhouse etc.)
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
                bt = b.get("text", "")
                if bt in ("submit", "submit application", "send", "send application"):
                    return bt
        except Exception:
            continue
    candidates = scan_actions(page, ["submit", "submit application", "send application"])
    if candidates:
        # Prefer buttons inside dialog/modal (Easy Apply, etc.)
        dialog_btns = [c for c in candidates if c.get("_inDialog")]
        page_btns = [c for c in candidates if not c.get("_inDialog")]
        for pool in [dialog_btns, page_btns]:
            for c in pool:
                if not c.get("disabled") and c.get("tag") != "A":
                    return c.get("text", "")
    try:
        buttons = page.evaluate("""() => {
            // LinkedIn Easy Apply uses native <dialog open> where children
            // may have offsetParent === null. Just check all buttons inside
            // dialog regardless of offsetParent.
            const dlg = document.querySelector('dialog, [role="dialog"]');
            if (dlg) {
                const dlgBtns = dlg.querySelectorAll('button');
                for (const b of dlgBtns) {
                    const t = b.textContent.trim().toLowerCase();
                    if (t === "submit" || t === "submit application" || t === "send" || t === "send application") return t;
                }
            }
            // Fallback: check page-level buttons
            const all = document.querySelectorAll('button');
            for (const b of all) {
                if (b.offsetParent === null) continue;
                if (b.closest('dialog, [role="dialog"]')) continue;  // already checked
                const t = b.textContent.trim().toLowerCase();
                if (t === "submit" || t === "submit application" || t === "send" || t === "send application") return t;
            }
            return null;
        }""")
        if buttons:
            return buttons
    except Exception:
        pass
    return None


def _resolve_standalone_form_url(page) -> str | None:
    """If the page has a cross-origin iframe containing a job form
    (e.g. Ashby embed on customer domain), return the standalone
    form URL.  Strips embed parameters so Playwright can access the
    form directly instead of through a cross-origin iframe."""
    try:
        return page.evaluate("""() => {
            const iframes = document.querySelectorAll('iframe');
            for (const ifr of iframes) {
                const src = ifr.src || '';
                if (!src || src === 'about:blank') continue;
                let visible = false;
                try {
                    const rect = ifr.getBoundingClientRect();
                    visible = rect.width >= 100 && rect.height >= 100
                        && ifr.offsetParent !== null;
                } catch { continue; }
                if (!visible) continue;
                const title = (ifr.title || '').toLowerCase();
                const srcL = src.toLowerCase();
                const isFormIframe = (
                    /ashbyhq/.test(srcL)
                    || /greenhouse/.test(srcL)
                    || title.includes('job') || title.includes('apply')
                    || title.includes('application') || title.includes('career')
                    || title.includes('form')
                );
                if (!isFormIframe) continue;
                try {
                    const u = new URL(src);
                    u.searchParams.delete('embed');
                    u.searchParams.delete('embedded');
                    for (const k of [...u.searchParams.keys()]) {
                        if (k.startsWith('utm_')) u.searchParams.delete(k);
                    }
                    return u.href;
                } catch { return src; }
            }
            return null;
        }""")
    except Exception:
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
