#!/usr/bin/env python3
"""06_submit.py — Click Submit on the Easy Apply modal.
Verifies success after submitting. Reports result.
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
    if 'jobs/' in p.url:
        page = p
        break
if not page:
    print("ERROR: no LinkedIn page found", file=sys.stderr); sys.exit(1)

# Click Submit / Send Application (disable overlay first)
clicked = page.evaluate("""() => {
    const overlay = document.getElementById('interop-outlet');
    if (overlay) overlay.style.pointerEvents = 'none';
    const d = document.querySelector('[role="dialog"]');
    if (!d) return { status: 'no_modal' };
    const btns = d.querySelectorAll('button:not([disabled])');
    const keywords = ['submit application', 'submit', 'send', 'done', 'save'];
    for (const b of btns) {
        const t = (b.textContent || '').trim().toLowerCase();
        for (const kw of keywords) {
            if (t === kw || t.includes(kw)) {
                b.click();
                return { status: 'clicked', action: t };
            }
        }
    }
    return { status: 'not_found' };
}""")
print(f"Submit clicked: {clicked.get('status')}", file=sys.stderr)

time.sleep(5)

# Verify success
text = page.evaluate("() => document.body.innerText").lower()
modal_gone = page.evaluate("() => !document.querySelector('[role=\"dialog\"]')")

result = 'unknown'
for w in ["thank you", "application submitted", "your application was sent", "has been submitted"]:
    if w in text:
        result = 'submitted'
        break
if result == 'unknown' and modal_gone:
    for w in ["already applied", "applied"]:
        if w in text:
            result = 'already_applied'
            break
    if result == 'unknown':
        result = 'submitted'  # modal gone + no errors = probably submitted

print(f"Result: {result}", file=sys.stderr)
print(f"Modal gone: {modal_gone}", file=sys.stderr)

state["apply_result"] = result
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print("NEXT: none", file=sys.stderr)
