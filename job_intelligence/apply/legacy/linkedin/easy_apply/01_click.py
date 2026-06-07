#!/usr/bin/env python3
"""01_click.py — Verify the Easy Apply modal is open and content is loaded.
The /apply/ URL should have already opened the modal.
This script waits for content, or falls back to clicking the Easy Apply link.
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

# Wait for modal content to load
modal = None
for attempt in range(5):
    time.sleep(2)
    modal = page.evaluate("""() => {
        const d = document.querySelector('[role="dialog"]');
        if (!d) return null;
        const inputs = d.querySelectorAll('input, select, textarea');
        return { fieldCount: inputs.length, textLen: (d.innerText||'').length };
    }""")
    if modal and modal['fieldCount'] > 0 and modal['textLen'] > 200:
        print(f"Modal loaded: {modal['fieldCount']} fields, {modal['textLen']} chars", file=sys.stderr)
        break
    # Fallback: try clicking the Easy Apply link
    if attempt == 2:
        print(f"Retry: clicking Easy Apply link...", file=sys.stderr)
        page.evaluate("""() => {
            const a = document.querySelector('a[aria-label*="Easy Apply"]');
            if (a && a.offsetParent !== null) { a.click(); }
        }""")

if not modal or modal['fieldCount'] == 0:
    print("ERROR: modal did not load", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(1)

# Write updated state
state["modal"] = modal
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print("NEXT: apply/linkedin/easy_apply/02_read_state.py", file=sys.stderr)
