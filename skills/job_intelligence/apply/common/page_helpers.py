"""apply/common/page_helpers.py — Shared page reading, state persistence, page finding,
Playwright-first field reading, and success signal detection.

Merged from master (DOM reading, verification) + skyvern-migration (state, CDP Chrome)."""
import json, os, random, sys, time
import webbrowser

from lib.config import STATE_PATH

_SKIP_DOMAINS = {"linkedin.com", "linkedin.com/jobs", "indeed.com",
                 "ca.indeed.com", "indeed.ca", "glassdoor.com",
                 "monster.com", "ziprecruiter.com", "simplyhired.com"}


def is_aggregator(domain):
    for skip in _SKIP_DOMAINS:
        if skip in domain:
            return True
    return False


_PAGE_JID_MAP = {}
_DOM_ATTR = "data-opencode-jid"


def tag_page(page, jid):
    try:
        page.evaluate(f"document.documentElement.setAttribute('{_DOM_ATTR}', {json.dumps(jid)})")
    except Exception:
        pass
    _PAGE_JID_MAP[id(page)] = jid


def mark_applied(jid):
    from lib.db import get_conn
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    get_conn().execute(
        "UPDATE jobs SET stage='applied', updated_at=?, applied_at=? WHERE id=?",
        (ts, ts, jid),
    ).connection.commit()
    try:
        from apply.common.apply_state import clear as _as_clear
        _as_clear(jid)
    except Exception:
        pass
    try:
        from apply.common import mappings
        n = mappings.promote(jid)
        if n:
            print(f"  MAPPINGS: promoted {n} confirmed mapping(s)", file=sys.stderr)
    except Exception:
        pass


def check_applied_signal(page):
    from apply.common.signals import has_success_text
    try:
        body = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return False
    if has_success_text(body):
        return True
    try:
        found = page.evaluate("""() => {
            const all = document.querySelectorAll('button, a, span, div');
            for (const el of all) {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t === 'applied' || t === 'application submitted') return true;
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


def page_html(page):
    return page.evaluate("""() => {
        function serialize(node) {
            if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/[\\x00-\\x08\\x0B\\x0E-\\x1F]/g, '');
            if (node.nodeType !== Node.ELEMENT_NODE) return '';
            let s = '<' + node.tagName.toLowerCase();
            for (const a of node.attributes) s += ' ' + a.name + '="' + a.value.replace(/"/g, '&quot;') + '"';
            s += '>';
            if (node.shadowRoot) {
                s += '<template shadowrootmode="' + node.shadowRoot.mode + '">';
                for (const c of node.shadowRoot.childNodes) s += serialize(c);
                s += '</template>';
            }
            for (const c of node.childNodes) {
                if (c.nodeType === Node.ELEMENT_NODE && c.tagName === 'SLOT') continue;
                s += serialize(c);
            }
            s += '</' + node.tagName.toLowerCase() + '>';
            return s;
        }
        return serialize(document.documentElement);
    }""") or ""


def check_captcha(page):
    try:
        has_widget = page.evaluate("""() => {
            const sel = 'iframe[src*="challenge"], [class*="cf-browser"], [id*="challenge"], [class*="challenge"], [class*="turnstile"]';
            return !!document.querySelector(sel);
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


def handle_captcha(page, state, wait_s=300, poll_s=3):
    if not check_captcha(page):
        return False
    url = page.url[:120]
    print(f"\n*** CAPTCHA DETECTED ***", file=sys.stderr)
    print(f"  URL: {url}", file=sys.stderr)
    print(f"  Solve it in your Chrome browser — resuming automatically once solved", file=sys.stderr)
    print(f"  (waiting up to {wait_s}s, then aborting this step)", file=sys.stderr)
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        webbrowser.open(url)
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


def handle_session_timeout(page):
    title = page.evaluate("document.title") or ""
    has_signal = "session" in title.lower() and ("time out" in title.lower() or "timeout" in title.lower())
    if not has_signal:
        return False
    clicked = page.evaluate("""() => {
        for (const el of document.querySelectorAll('button')) {
            const t = (el.textContent || '').trim();
            if (t === 'Keep Working' || t === 'Continue Session') {
                el.click(); return true;
            }
        }
        return false;
    }""")
    if clicked:
        time.sleep(2)
        return True
    return False


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


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


def read_and_save(p, state):
    ps = read_page(p)
    state["page"] = ps
    save_state(state)
    return ps


DEFAULT_EXCLUDED_BUTTONS = {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse", "clear", "reset", "start over"}


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
                    disabled: el.disabled || false
                });
            }
        }
        candidates.sort((a, b) => b.score - a.score);
        return candidates;
    })""", [keywords, list(exclude)])
    return result
