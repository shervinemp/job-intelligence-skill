#!/usr/bin/env python3
"""01_navigate.py — Click the "Apply on company website" button on LinkedIn.
Captures the new tab URL and stores it in state.
The new page is left open for subsequent scripts.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

b, ctx = connect()
page = None
for p in ctx.pages:
    if '/jobs/view/' in p.url:
        page = p
        break
if not page:
    print("ERROR: no LinkedIn page found", file=sys.stderr)
    sys.exit(1)

# Track pages before click for comparison
pages_before = set(p.url for p in ctx.pages)

# Click the external apply button
clicked = page.evaluate("""() => {
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

# Find external URL — check for new pages first, then existing pages
external_url = None
for p in ctx.pages:
    url = p.url
    if url not in pages_before and 'linkedin.com' not in url and url != 'about:blank' and not url.startswith('chrome'):
        external_url = url
        break

if not external_url:
    for p in ctx.pages:
        url = p.url
        if 'linkedin.com' not in url and url != 'about:blank' and not url.startswith('chrome'):
            external_url = url
            print(f"External page found (pre-existing): {url}", file=sys.stderr)
            break

if external_url:
    state["external_url"] = external_url
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    print(f"External URL: {external_url}", file=sys.stderr)
    print("NEXT: apply/detect_ats.py", file=sys.stderr)
else:
    print("ERROR: no external page opened", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
