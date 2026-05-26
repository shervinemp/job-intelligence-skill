#!/usr/bin/env python3
"""01_navigate.py — Find external apply URL on LinkedIn and set __applyPage.
Extracts from anchor tag or LinkedIn safety redirect URL.
"""
import json, os, sys, time, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect
from lib.db import get_conn

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
try:
    with open(STATE_PATH) as f:
        state = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    state = {}

jid = sys.argv[1]
c = get_conn()
r = c.execute("SELECT url, title, company FROM jobs WHERE id=?", (jid,)).fetchone()
url, title, company = r["url"], r["title"], r["company"]

b, ctx = connect()
p = ctx.new_page()
p.goto(url, wait_until='domcontentloaded', timeout=30000)
time.sleep(5)
p.evaluate("() => window.__applyPage = true")

# Extract external URL
external_url = p.evaluate("""() => {
    // 1. Anchor wrapping the "on company website" button
    const anchors = document.querySelectorAll('a[href]');
    for (const a of anchors) {
        const btn = a.querySelector('button');
        if (!btn) continue;
        const aria = (btn.getAttribute('aria-label') || '');
        if (aria.includes('on company website') && btn.offsetParent !== null)
            return a.href;
    }
    // 2. Check current page (some jobs redirect)
    if (!window.location.hostname.includes('linkedin.com'))
        return window.location.href;
    // 3. Data-tracking anchor
    for (const a of anchors) {
        const aria = (a.getAttribute('aria-label') || '');
        if (aria.includes('on company website')) return a.href;
    }
    // 4. Any anchor pointing off LinkedIn with job-related href
    for (const a of anchors) {
        const h = a.href || '';
        if (h.includes('linkedin.com/safety/go/')) return h;
    }
    return null;
}""")

# Decode LinkedIn safety redirect
if external_url and 'linkedin.com/safety/go/' in external_url:
    qs = urllib.parse.urlparse(external_url).query
    params = urllib.parse.parse_qs(qs)
    decoded = params.get('url', [None])[0]
    if decoded:
        external_url = urllib.parse.unquote(decoded)
        print(f"Decoded safety redirect: {external_url}", file=sys.stderr)

# Fallback: click the button if we got a LinkedIn link
if external_url and 'linkedin.com' in external_url.lower():
    clicked = p.evaluate("""() => {
        const all = document.querySelectorAll('button');
        for (const b of all) {
            const aria = (b.getAttribute('aria-label') || '');
            if (aria.includes('on company website') && b.offsetParent !== null) {
                b.click(); return true;
            }
        }
        return false;
    }""")
    print(f"Clicked external button: {clicked}", file=sys.stderr)
    time.sleep(5)
    current = p.evaluate("window.location.href")
    if 'linkedin.com' not in current:
        external_url = current
    else:
        for p2 in ctx.pages:
            u = p2.url
            if 'linkedin.com' not in u and u != 'about:blank' and not u.startswith('chrome'):
                external_url = u; break

if external_url and 'linkedin.com' not in external_url:
    state["external_url"] = external_url
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    print(f"External URL: {external_url}", file=sys.stderr)
    print("NEXT: apply/detect_ats.py", file=sys.stderr)
else:
    print(f"ERROR: no external URL found (got: {external_url})", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
