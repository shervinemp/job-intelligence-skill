"""apply/common/page_helpers.py — Shared page reading, state persistence, page finding."""
import json, os, random, re, time
import webbrowser

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
PLATFORMS_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "platforms.json")

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


def is_platform_trusted(platform):
    """Check if a platform name has been trusted via platforms.json."""
    if not platform:
        return False
    try:
        data = json.load(open(PLATFORMS_PATH, encoding="utf-8"))
        return data.get("platforms", {}).get(platform, {}).get("trusted", False)
    except (json.JSONDecodeError, OSError):
        return False


def set_platform_trusted(platform):
    """Mark a platform as trusted in platforms.json."""
    if not platform:
        return
    try:
        data = json.load(open(PLATFORMS_PATH, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {"platforms": {}}
    data.setdefault("platforms", {})[platform] = {"trusted": True}
    os.makedirs(os.path.dirname(PLATFORMS_PATH), exist_ok=True)
    with open(PLATFORMS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

_PAGE_JID_MAP = {}  # page object id -> jid mapping (no DOM mutation)

_CAPTCHA_SIGNALS = [
    "recaptcha", "hcaptcha", "cf-turnstile", "turnstile",
    "cloudflare", "challenge-platform", "g-recaptcha",
    "data-sitekey", "data-callback",
    "cf-browser-verification", "challenge-running", "challenge-stage",
]


def check_captcha(page):
    """Check if the current page has a CAPTCHA challenge. Returns True if detected.
    Single evaluate call for efficiency."""
    try:
        result = page.evaluate(f"""((args) => {{
            const signals = args[0], keywords = args[1];
            const text = (document.body.innerText || '').toLowerCase();
            for (const kw of keywords) {{ if (text.includes(kw)) return true; }}
            const html = (document.documentElement.innerHTML || '').toLowerCase();
            for (const sig of signals) {{ if (html.includes(sig)) return true; }}
            const iframes = document.querySelectorAll('iframe');
            for (const f of iframes) {{
                const src = (f.src || '').toLowerCase();
                if (src.includes('recaptcha') || src.includes('hcaptcha') ||
                    src.includes('turnstile') || src.includes('challenge')) return true;
            }}
            return false;
        }})""", [_CAPTCHA_SIGNALS,
               ["verify you are human", "security check", "captcha",
                "i'm not a robot", "complete the security check"]])
        return result
    except Exception:
        pass
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

def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

def read_page(p):
    """Read page content including fields, buttons, page type hints.
    Delegates to the canonical field_reader for consistency."""
    from apply.common.field_reader import read_fields as _rf
    result = _rf(p, scope="document")
    # Auto-detect dialog scope: if activeElement is inside a dialog, re-read with dialog scope
    try:
        in_dialog = p.evaluate("""() => {
            const d = document.querySelector('[role="dialog"]');
            return d && d.contains(document.activeElement) ? true : false;
        }""")
        if in_dialog:
            result = _rf(p, scope="dialog")
    except Exception:
        pass
    return result

def find_page(ctx, state):
    """Find the best matching page by JID mapping, then URL score."""
    jid = state.get("jid", "")
    ext = state.get("external_url", "").rstrip("/")

    # First pass: find by JID mapping (no DOM mutation)
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
        except:
            pass
    if li_job_id:
        for p in ctx.pages:
            if li_job_id in p.url:
                return p

    return None

def tag_page(page, jid):
    """Tag a page with a job ID for reliable find_page lookups. No DOM mutation."""
    _PAGE_JID_MAP[id(page)] = jid

def read_and_save(p, state):
    """Read page state, save to state file, return page dict."""
    ps = read_page(p)
    state["page"] = ps
    save_state(state)
    return ps

def resolve_label(label, profile):
    """Resolve a label to a profile value by exact key match. Falls back to answer_matcher."""
    norm = re.sub(r'[^a-z0-9+#]+', ' ', label.lower()).strip()
    fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
    if norm in ("full name", "name", "your name"):
        return f"{fn} {ln}" if fn and ln else fn or ln or None
    for pk, pv in profile.items():
        if not pv or not isinstance(pv, str) or len(pv) < 2:
            continue
        pn = re.sub(r'[^a-z0-9]+', ' ', pk.lower()).strip()
        if pn == norm:
            return pv
    return None

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
        except Exception as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.random()
                time.sleep(delay)
                continue
            raise


def scan_actions(page, keywords, exclude=None):
    """Score all clickable elements (buttons + links) against keyword list.
    Returns sorted list of candidates with scores."""
    exclude = exclude or {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse"}
    result = page.evaluate("""((args) => {
        const kws = args[0], excl = new Set(args[1].map(e => e.toLowerCase()));
        const currentUrl = location.href.replace(/\\/$/, '').toLowerCase();
        const all = document.querySelectorAll('button, a');
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
