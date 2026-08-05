"""apply/common/page_helpers.py — Shared page reading, state persistence, page finding,
Playwright-first field reading, and success signal detection."""
import json, os, sys, time

from lib.config import STATE_PATH


_PAGE_JID_MAP = {}
_DOM_ATTR = "data-opencode-jid"


def handle_session_timeout(page):
    """Dismiss Workday-style 'Your session is about to time out' popups."""
    try:
        title = page.evaluate("document.title") or ""
    except Exception:
        return False
    if "session" not in title.lower() or ("time out" not in title.lower() and "timeout" not in title.lower()):
        return False
    clicked = page.evaluate("""() => {
        for (const el of document.querySelectorAll('button')) {
            const t = (el.textContent || '').trim();
            if (t === 'Keep Working' || t === 'Continue Session') { el.click(); return true; }
        }
        return false;
    }""")
    if clicked:
        time.sleep(2)
        return True
    return False


def tag_page(page, jid):
    try:
        page.evaluate(f"document.documentElement.setAttribute('{_DOM_ATTR}', {json.dumps(jid)})")
    except Exception:
        pass
    _PAGE_JID_MAP[id(page)] = jid


def mark_applied(jid):
    """Atomically mark a job as applied. Returns True if this call actually
    transitioned the stage (was not already 'applied'). This prevents
    duplicate submissions even if two processes race past the stage check."""
    from lib.db import get_conn
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    cur = get_conn().execute(
        "UPDATE jobs SET stage='applied', updated_at=?, applied_at=? "
        "WHERE id=? AND stage != 'applied'",
        (ts, ts, jid),
    )
    cur.connection.commit()
    was_new = cur.rowcount > 0
    try:
        from apply.common.apply_state import clear as _as_clear
        _as_clear(jid)
    except Exception:
        pass
    # Clear the one-shot submit guard from the shared apply state so a
    # later run for the same job can't dead-end on submit_clicked.
    try:
        st = load_state()
        if st.get("jid") == jid and st.get("submit_clicked"):
            st["submit_clicked"] = False
            save_state(st)
    except Exception:
        pass
    return was_new


def check_applied_signal(page):
    from apply.common.signals import has_success_text
    try:
        body = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return False
    if has_success_text(body):
        return True
    # URL-based: Ashby/Workday redirect to confirmation pages
    url = (page.url or "").lower()
    if "/application" in url and ("submitted" in url or "complete" in url or "confirmed" in url or "success" in url):
        return True
    try:
        found = page.evaluate("""() => {
            const all = document.querySelectorAll('button, a, span, div, h1, h2, h3');
            for (const el of all) {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t === 'applied' || t === 'application submitted' || t === 'application complete') return true;
            }
            return false;
        }""")
        return bool(found)
    except Exception:
        return False


def read_page_tag(page):
    try:
        return page.evaluate(f"document.documentElement.getAttribute('{_DOM_ATTR}') or ''")
    except Exception:
        return ""


def page_text(page):
    return page.evaluate("""() => {
        let t = document.body.innerText || '';
        document.querySelectorAll(':defined').forEach(el => {
            if (el.shadowRoot) t += '\\n' + (el.shadowRoot.textContent || '');
        });
        return t;
    }""") or ""


def check_captcha(page):
    try:
        # Visible CAPTCHA widgets. Cloudflare/Turnstile use challenge-stage /
        # cf-* classes; reCAPTCHA uses iframe[src*="recaptcha"] + .g-recaptcha;
        # hCaptcha uses iframe[src*="hcaptcha"] + .h-captcha. The probe-time
        # detector (capabilities.py) already matches these — the runtime check
        # must too, or the fill/submit loops never pause for a human solve and
        # never record captcha_required.
        has_widget = page.evaluate("""() => {
            const sel = 'iframe[src*="challenge"], iframe[src*="recaptcha"], '
                + 'iframe[src*="hcaptcha"], iframe[src*="captcha"], '
                + '.g-recaptcha, .h-captcha, '
                + '[class*="cf-browser"], [id*="challenge"], '
                + '[class*="challenge"], [class*="turnstile"]';
            for (const el of document.querySelectorAll(sel)) {
                if (el.offsetParent !== null) return true;
            }
            return false;
        }""")
        if has_widget:
            return True
        text = page_text(page).lower()
        for kw in ["verify you are human", "security check", "i'm not a robot", "complete the security check"]:
            if kw in text:
                return True
        return False
    except Exception:
        return False


def is_cloudflare_challenge(page):
    """Cloudflare managed challenge — auto-resolves in 10-30s, no user action.
    Distinct from a real CAPTCHA (which needs human interaction)."""
    try:
        return bool(page.evaluate("""() => {
            const sel = '#challenge-stage, #cf-challenge-running, .cf-browser-verification, '
                + '#cf-please-wait, .cf-turnstile, iframe[src*="challenges.cloudflare.com"]';
            if (document.querySelector(sel)) return true;
            const t = (document.title || '').toLowerCase();
            if (t.includes('just a moment') || t.includes('attention required')) return true;
            return false;
        }"""))
    except Exception:
        return False


def wait_cloudflare(page, timeout=30):
    """Wait for a Cloudflare managed challenge to auto-resolve."""
    import time as _t
    waited = 0
    while waited < timeout:
        if not is_cloudflare_challenge(page):
            return True
        _t.sleep(2)
        waited += 2
    return not is_cloudflare_challenge(page)


def handle_captcha(page, state, wait_s=None, poll_s=3):
    if not check_captcha(page):
        return False
    # Policy skip: don't block a batch on a human solve — abort so the
    # caller records captcha_required (resumable when a human is present).
    try:
        from apply.common.submit_policy import load_policy
        if load_policy().get("captcha_skip"):
            print("  CAPTCHA_SKIP: policy captcha_skip=true — aborting this step",
                  file=sys.stderr)
            return True
    except Exception:
        pass
    # Cloudflare managed challenge auto-resolves — wait silently, don't alert
    if is_cloudflare_challenge(page):
        print("  Cloudflare challenge detected — waiting for auto-resolution...", file=sys.stderr)
        if wait_cloudflare(page, timeout=30):
            print("  Cloudflare resolved.", file=sys.stderr)
            return False
        print("  Cloudflare didn't resolve in 30s — escalating to user.", file=sys.stderr)
    if wait_s is None:
        wait_s = int(os.environ.get("JI_CAPTCHA_TIMEOUT", "300"))
    url = page.url[:120]
    print("\n*** CAPTCHA DETECTED ***", file=sys.stderr)
    print(f"  URL: {url}", file=sys.stderr)
    print("  Solve it in this Chrome window — resuming automatically once solved", file=sys.stderr)
    print(f"  (waiting up to {wait_s}s, then aborting this step)", file=sys.stderr)
    try:
        page.bring_to_front()
    except Exception:
        pass
    waited = 0
    while waited < wait_s:
        time.sleep(poll_s)
        waited += poll_s
        if not check_captcha(page):
            print(f"  CAPTCHA solved after {waited}s — resuming.", file=sys.stderr)
            return False
    print(f"  CAPTCHA still present after {wait_s}s — aborting this step.", file=sys.stderr)
    return True


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def save_state(state):
    from lib.config import atomic_write_json
    # RUNTIME CACHE ONLY - the dossier (results/{jid}/handoff.json) is the
    # truth; this marker keeps every reader from mistaking scratch for
    # authority (see terms glossary: "dossier" vs "profile").
    state["_role"] = "runtime_cache"
    # #7: the apply_state.json file is SHARED across jobs. A concurrent
    # different-jid write must not clobber it — serialize the write with a
    # short file lock (the batch's pipeline lock already excludes the batch
    # vs manual case; this closes the manual-vs-manual race).
    try:
        from apply.common.jid_lock import JidLock
        with JidLock("__apply_state__", timeout=10.0):
            atomic_write_json(STATE_PATH, state)
    except Exception:
        atomic_write_json(STATE_PATH, state)


def clear_runtime_state(jid):
    """Reset the runtime cache for this job (reject/undo): drop the
    action flags (submit_clicked / submit_text / status) but keep
    identity + answers (external_url, url, title, company, platform,
    fill_answers) so a re-run re-fills and re-checks without re-
    navigating. The dossier remains the truth throughout."""
    st = load_state()
    if st.get("jid") != jid:
        return
    for k in ("submit_clicked", "submit_text", "status"):
        st.pop(k, None)
    save_state(st)


def read_page(p, custom_widgets=None):
    from apply.common.field_reader import read_fields as _rf
    from apply.common.inspector import probe_iframes
    if custom_widgets is None:
        try:
            from apply.common.registry import resolve as resolve_registry
            registry = resolve_registry(p.url)
            if registry and registry.widgets:
                cw = dict(registry.widgets)
                wp = getattr(registry, 'widget_parent', None)
                if wp:
                    cw["parent"] = wp
                custom_widgets = cw
        except Exception:
            pass
    has_dialog = p.evaluate("() => !!document.querySelector('[role=dialog], dialog, [data-test-form-builder]')")
    if has_dialog:
        result = _rf(p, scope="dialog", custom_widgets=custom_widgets)
        result["_scoped_to"] = "dialog"
    else:
        result = _rf(p, scope="document", custom_widgets=custom_widgets)
    if result["fieldCount"] == 0:
        result = _rf(p, scope="dialog", custom_widgets=custom_widgets)
    has_iframes = p.evaluate("() => !!document.querySelector('iframe')")
    if has_iframes:
        ifr = probe_iframes(p)
        if ifr.field_count > result.get("fieldCount", 0):
            existing = {f.get("label", "") for f in result.get("fields", [])}
            for f in ifr.fields:
                if f.get("label", "") not in existing:
                    result["fields"].append(f)
                    existing.add(f.get("label", ""))
            result["fieldCount"] = len(result["fields"])
    return result


def find_page(ctx, state):
    jid = state.get("jid", "")
    ext = state.get("external_url", "").rstrip("/")
    for p in ctx.pages:
        if read_page_tag(p) == jid:
            return p
    for p in ctx.pages:
        if _PAGE_JID_MAP.get(id(p)) == jid:
            return p
    if ext:
        best_score = -1
        best_page = None
        for p in ctx.pages:
            url = p.url.rstrip("/")
            score = -1
            if url == ext:
                score = 3
            elif url.startswith(ext + "/"):
                score = 2
            elif ext.startswith(url + "/"):
                score = 1
            if score > best_score:
                best_score = score
                best_page = p
        if best_page:
            return best_page
    li_job_id = None
    if "linkedin.com/jobs/view" in ext:
        try:
            li_job_id = ext.split("/jobs/view/")[1].split("/")[0]
        except Exception:
            pass
    if li_job_id:
        for p in ctx.pages:
            if li_job_id in p.url:
                return p
    return None


DEFAULT_EXCLUDED_BUTTONS = {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse", "clear", "reset", "start over", "skip to search", "skip to main content", "skip to primary content"}


def scan_actions(page, keywords, exclude=None):
    exclude = exclude or DEFAULT_EXCLUDED_BUTTONS
    result = page.evaluate("""((args) => {
        const kws = args[0], excl = new Set(args[1].map(e => e.toLowerCase()));
        const currentUrl = location.href.replace(/\\/$/, '').toLowerCase();
        const all = document.querySelectorAll('button, a, [role="button"]');
        const candidates = [];
        for (const el of all) {
            if (el.offsetParent === null) continue;
            const text = (el.textContent || '').trim().toLowerCase();
            if (excl.has(text)) continue;
            const rawHref = (el.href || '').replace(/\\/$/, '');
            const href = rawHref.toLowerCase();
            if (el.tagName === 'A' && href === currentUrl) continue;
            let score = 0;
            for (const kw of kws) {
                if (text === kw) score = Math.max(score, 4);
                else if (text.startsWith(kw)) score = Math.max(score, 3);
                else if (text.includes(kw)) score = Math.max(score, 2);
                else if (href.includes(kw)) score = Math.max(score, 1);
            }
            if (score > 0) {
                candidates.push({
                    text: text.slice(0, 30), score: score, tag: el.tagName,
                    href: rawHref,
                    disabled: el.disabled || false,
                    _inDialog: !!el.closest('dialog, [role="dialog"]'),
                });
            }
        }
        candidates.sort((a, b) => b.score - a.score);
        return candidates;
    })""", [keywords, list(exclude)])
    return result
