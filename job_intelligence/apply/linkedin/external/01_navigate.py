#!/usr/bin/env python3
"""01_navigate.py — Find and click external apply link on LinkedIn.
Navigates to /jobs/view/ URL directly (not dependent on /apply/ modal state),
extracts the external apply URL from the page, then navigates to it.
"""
import json, os, sys, time
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

# Extract external URL from the page
external_url = p.evaluate("""() => {
    // 1. Look for anchor wrapping the "on company website" button
    const anchors = document.querySelectorAll('a[href]');
    for (const a of anchors) {
        const btn = a.querySelector('button');
        if (!btn) continue;
        const aria = (btn.getAttribute('aria-label') || '');
        if (aria.includes('on company website') && btn.offsetParent !== null) {
            return a.href;
        }
    }
    // 2. Check if the job page itself navigated to an external URL
    // (some LinkedIn jobs redirect to the company's ATS)
    if (window.location.hostname !== 'www.linkedin.com') {
        return window.location.href;
    }
    // 3. Try data-tracking attribute on anchor
    for (const a of anchors) {
        const aria = (a.getAttribute('aria-label') || '');
        if (aria.includes('on company website')) {
            return a.href;
        }
    }
    return null;
}""")

if external_url and 'linkedin.com' in external_url.lower():
    # The link might still point to a LinkedIn redirect; need to click it
    clicked = p.evaluate("""() => {
        const all = document.querySelectorAll('button');
        for (const b of all) {
            const aria = (b.getAttribute('aria-label') || '');
            if (aria.includes('on company website') && b.offsetParent !== null) {
                b.click();
                return true;
            }
        }
        return false;
    }""")
    print(f"Clicked external button: {clicked}", file=sys.stderr)
    time.sleep(5)
    # Check current page URL
    current_url = p.evaluate("window.location.href")
    if 'linkedin.com' not in current_url:
        external_url = current_url
    else:
        # Check for new pages
        for p2 in ctx.pages:
            url2 = p2.url
            if 'linkedin.com' not in url2 and url2 != 'about:blank' and not url2.startswith('chrome'):
                external_url = url2
                break

if external_url and 'linkedin.com' not in external_url:
    state["external_url"] = external_url
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    print(f"External URL: {external_url}", file=sys.stderr)
    print("NEXT: apply/detect_ats.py", file=sys.stderr)
else:
    print(f"ERROR: no external URL found (got: {external_url})", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
