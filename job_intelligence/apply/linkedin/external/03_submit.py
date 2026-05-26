#!/usr/bin/env python3
"""03_submit.py — Click Submit on an external ATS form (dry-run safe).
Reports submit button status without clicking unless --confirm flag is passed.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

DRY_RUN = "--confirm" not in sys.argv
if DRY_RUN:
    print("DRY RUN: use --confirm to actually submit", file=sys.stderr)

b, ctx = connect()
page = None
ext_url = state.get("external_url", "")
is_external = bool(ext_url)
for p in ctx.pages:
    url = p.url
    if is_external:
        if url in ext_url or ext_url in url:
            page = p
            break
    else:
        if '/jobs/view/' in url:
            page = p
            break
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

if unfilled and btn_info.get('disabled') == False:
    print(f"WARNING: {len(unfilled)} required fields still empty but button is enabled", file=sys.stderr)
    for u in unfilled[:5]:
        print(f"  '{u}'", file=sys.stderr)

if not unfilled or DRY_RUN:
    if DRY_RUN:
        print(f"Would click '{btn_info['text']}'", file=sys.stderr)
        print(f"Required fields empty: {len(unfilled)}", file=sys.stderr)
        print("NEXT: apply/external/03_submit.py --confirm", file=sys.stderr)
    else:
        page.evaluate(f"""() => {{
            const container = document.querySelector('[role="dialog"]') || document;
            const btns = container.querySelectorAll('button:not([disabled])');
            for (const b of btns) {{
                const t = (b.textContent || '').trim().toLowerCase();
                if (t === '{btn_info['text'].lower()}' || t.includes('{btn_info['text'].lower()}')) {{
                    b.click(); return;
                }}
            }}
        }}""")
        time.sleep(4)
        # Verify
        text = page.evaluate("() => document.body.innerText").toLowerCase()
        for w in ["thank you", "application submitted", "your application", "has been submitted"]:
            if w in text:
                print("RESULT: submitted", file=sys.stderr)
                break
        else:
            print("RESULT: unknown (page may have errors)", file=sys.stderr)
        print("NEXT: none", file=sys.stderr)
