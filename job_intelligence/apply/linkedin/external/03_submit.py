#!/usr/bin/env python3
"""03_submit.py — Click Submit on an external ATS form (dry-run safe).
Reports submit button status without clicking unless --confirm flag is passed.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect
from apply.common import find_apply_page

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

b, ctx = connect()
ext_url = state.get("external_url", "")
page, navigated_fresh = find_apply_page(ctx, ext_url or None)
if not page:
    print("ERROR: no relevant page found", file=sys.stderr)
    sys.exit(1)

# Find submit button
btn_info = page.evaluate("""() => {
    const container = document.querySelector('[role="dialog"]') || document;
    const btns = container.querySelectorAll('button:not([disabled])');
    const submitKeywords = ['submit application', 'submit', 'send application', 'send'];
    for (const b of btns) {
        const t = (b.textContent || '').trim().toLowerCase();
        for (const kw of submitKeywords) {
            if (t === kw || t.includes(kw)) {
                const rect = b.getBoundingClientRect();
                return {
                    found: true,
                    text: (b.textContent||'').trim().slice(0, 30),
                    disabled: b.disabled,
                    visible: rect.width > 0 && rect.height > 0,
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y) },
                };
            }
        }
    }
    return { found: false };
}""")

if not btn_info.get('found'):
    print("ERROR: no submit button found", file=sys.stderr)
    sys.exit(1)

print(f"Submit button: '{btn_info['text']}' disabled={btn_info['disabled']} visible={btn_info['visible']}", file=sys.stderr)

# Check for unfilled required fields
unfilled = page.evaluate("""() => {
    const container = document.querySelector('[role="dialog"]') || document;
    const inputs = container.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    const empty = [];
    for (const el of inputs) {
        if (el.required && (!el.value || el.value === 'Select an option')) {
            const lbl = container.querySelector('label[for="'+el.id+'"]');
            const label = lbl ? lbl.textContent.trim() : el.placeholder || el.getAttribute('aria-label') || el.name;
            empty.push(label.slice(0, 50));
        }
    }
    return empty;
}""")

if unfilled:
    print(f"WARNING: {len(unfilled)} required fields still empty", file=sys.stderr)
    for u in unfilled[:5]:
        print(f"  '{u}'", file=sys.stderr)

print(f"Would click '{btn_info['text']}'", file=sys.stderr)
print(f"Required fields empty: {len(unfilled)}", file=sys.stderr)
print(f"\nForm state:", file=sys.stderr)
for u in unfilled[:5]:
    print(f"  MISSING: {u}", file=sys.stderr)
print("\nNEXT: ask before submitting", file=sys.stderr)
