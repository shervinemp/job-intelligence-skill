"""apply/common/page_helpers.py — Shared page reading, state persistence, page finding."""
import json, os, random, sys, time
import webbrowser

from lib.config import STATE_PATH

# Aggregator domains — always trusted, no learning needed
_SKIP_DOMAINS = {"linkedin.com", "linkedin.com/jobs", "indeed.com",
                 "ca.indeed.com", "indeed.ca", "glassdoor.com",
                 "monster.com", "ziprecruiter.com", "simplyhired.com"}


def is_aggregator(domain):
    """Check if a domain is a job aggregator (not an ATS to learn from)."""
    for skip in _SKIP_DOMAINS:
        if skip in domain:
            return True
    return False


_PAGE_JID_MAP = {}  # page object id -> jid mapping (in-memory cache, not persistent)
_DOM_ATTR = "data-opencode-jid"


def tag_page(page, jid):
    """Persistently tag a page with a JID via DOM attribute (survives process restarts)."""
    try:
        page.evaluate(f"document.documentElement.setAttribute('{_DOM_ATTR}', {json.dumps(jid)})")
    except Exception:
        pass
    _PAGE_JID_MAP[id(page)] = jid  # cache for current process


def mark_applied(jid):
    """Update DB stage to applied."""
    from lib.db import get_conn
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    get_conn().execute(
        "UPDATE jobs SET stage='applied', updated_at=?, applied_at=? WHERE id=?",
        (ts, ts, jid),
    ).connection.commit()


def check_applied_signal(page):
    """Check page for successful application signals. Returns True if applied."""
    try:
        body = (page.evaluate("() => document.body.innerText") or "").lower()
    except Exception:
        return False
    signals = ["your application has been", "your application was",
               "has been sent", "application received", "you have applied",
               "thank you for applying", "application submitted"]
    for s in signals:
        if s in body:
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
    """Read persistent JID tag from DOM."""
    try:
        return page.evaluate(f"document.documentElement.getAttribute('{_DOM_ATTR}') or ''")
    except Exception:
        return ""

_CAPTCHA_SIGNALS = [
    "challenge-platform", "cf-browser-verification",
    "challenge-running", "challenge-stage",
]


def page_text(page):
    """Return page text including content from shadow roots."""
    return page.evaluate("""() => {
        let t = document.body.innerText || '';
        document.querySelectorAll(':defined').forEach(el => {
            if (el.shadowRoot) t += '\\n' + (el.shadowRoot.textContent || '');
        });
        return t;
    }""") or ""


def page_html(page):
    """Return page HTML including declarative shadow DOM."""
    return page.evaluate("""() => {
        // Recursive serialization that captures shadow DOM
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
    """Check if the current page has a CAPTCHA challenge. Returns True if detected.
    Only flags CAPTCHA if the challenge is visible (has a visible iframe or widget),
    not just because a keyword appears in a script tag."""
    try:
        # Check for visible challenge widget first — most reliable
        has_widget = page.evaluate("""() => {
            const sel = 'iframe[src*="challenge"], [class*="cf-browser"], [id*="challenge"], [class*="challenge"], [class*="turnstile"]';
            return !!document.querySelector(sel);
        }""")
        if has_widget:
            return True
        # Fallback: check body text for CAPTCHA keywords
        text = page_text(page).lower()
        for kw in ["verify you are human", "security check", "i'm not a robot", "complete the security check"]:
            if kw in text:
                return True
        return False
    except Exception:
        return False


def handle_captcha(page, state):
    """If CAPTCHA detected, notify user and pause."""
    if not check_captcha(page):
        return False
    url = page.url[:120]
    print(f"\n*** CAPTCHA DETECTED ***", file=sys.stderr)
    print(f"  URL: {url}", file=sys.stderr)
    print(f"  Solve it in your Chrome browser, then press Enter to continue", file=sys.stderr)
    print(f"  (Pipeline will wait up to 300s, then abort)", file=sys.stderr)
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print(f"\n  CAPTCHA wait aborted.", file=sys.stderr)
    print(f"  Resuming...", file=sys.stderr)
    return True


def handle_session_timeout(page):
    """Dismiss session timeout dialogs.
    Checks page title for timeout signals, then looks for the dismissal
    button. Only returns True if the button was actually clicked."""
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
    """Read page content including fields, buttons, page type hints.
    Tries document scope first, falls back to dialog scope if no fields found.
    If still no fields found, probes same-origin iframes for embedded forms.

    If custom_widgets is not provided, auto-resolves from the page URL via
    platform registry — works for Ashby, Workday, and other ATS without
    callers needing to know about widget configs."""
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
    # If a dialog/modal is open, prefer dialog scope over document — document
    # includes site chrome (nav, search, sidebar) that drowns out real fields.
    has_dialog = p.evaluate("() => !!document.querySelector('[role=dialog], dialog, [data-test-form-builder]')")
    if has_dialog:
        result = _rf(p, scope="dialog", custom_widgets=custom_widgets)
        result["_scoped_to"] = "dialog"
    else:
        result = _rf(p, scope="document", custom_widgets=custom_widgets)
    if result["fieldCount"] == 0:
        result = _rf(p, scope="dialog", custom_widgets=custom_widgets)
    # Iframe fallback: some ATS embed forms in same-origin iframes.
    # Only merge if the iframe has MORE fields than the parent DOM — this prevents
    # sidebar widgets or analytics iframes from polluting the field list.
    # probe_iframes only accesses same-origin iframes; cross-origin ones are skipped.
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
    """Find the best matching page by JID mapping (DOM tag + cache), then URL score."""
    jid = state.get("jid", "")
    ext = state.get("external_url", "").rstrip("/")

    # First pass: find by JID tag (DOM attribute survives process restarts)
    for p in ctx.pages:
        if read_page_tag(p) == jid:
            return p
    for p in ctx.pages:
        if _PAGE_JID_MAP.get(id(p)) == jid:
            return p

    # Second pass: score by URL match quality
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

    # Third pass: LinkedIn job ID match
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
    """Read page state, save to state file, return page dict."""
    ps = read_page(p)
    state["page"] = ps
    save_state(state)
    return ps

def retry_with_backoff(fn, max_retries=2, base_delay=2, is_rate_limit=None):
    """Retry fn on rate-limit/transient failure with exponential backoff + jitter."""
    for attempt in range(max_retries + 1):
        try:
            result = fn()
            if is_rate_limit and is_rate_limit(result):
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt) + random.random()
                    print(f"  Rate limited, retrying in {delay:.1f}s...", file=sys.stderr)
                    time.sleep(delay)
                    continue
            return result
        except Exception:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.random()
                time.sleep(delay)
                continue
            raise


DEFAULT_EXCLUDED_BUTTONS = {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse", "clear", "reset", "start over"}

def scan_actions(page, keywords, exclude=None):
    """Score all clickable elements (buttons + links) against keyword list.
    Returns sorted list of candidates with scores."""
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
            const href = (el.href || '').toLowerCase().replace(/\\/$/, '');
            // Skip self-referencing links
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
                    href: href,
                    disabled: el.disabled || false
                });
            }
        }
        candidates.sort((a, b) => b.score - a.score);
        return candidates;
    })""", [keywords, list(exclude)])
    return result
