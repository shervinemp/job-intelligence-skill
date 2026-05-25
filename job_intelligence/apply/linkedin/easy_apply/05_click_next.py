#!/usr/bin/env python3
"""05_click_next.py — Click the "Next" button in the Easy Apply modal.
Then re-read state to display new fields.
Leaves the modal/page open for the next script.
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

# Click "Next" or "Review" button using Playwright (force=true bypasses interop overlay)
target_action = page.evaluate("""() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) return null;
    const btns = d.querySelectorAll('button:not([disabled])');
    for (const b of btns) {
        const t = (b.textContent || '').trim().toLowerCase();
        if (t === 'next' || t === 'review' || t === 'submit application' || t === 'submit' || t === 'done') {
            return { action: t, id: b.id, text: (b.textContent || '').trim().slice(0, 20) };
        }
    }
    return null;
}""")

if not target_action:
    print("No Next/Review/Submit button found", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(1)

action = target_action["action"]
text = target_action["text"]
print(f"Action: {action}", file=sys.stderr)

# Remove interop overlay that intercepts clicks, then click the button
clicked = page.evaluate(f"""() => {{
    const overlay = document.getElementById('interop-outlet');
    if (overlay) overlay.style.pointerEvents = 'none';
    const d = document.querySelector('[role="dialog"]');
    if (!d) return false;
    const btns = d.querySelectorAll('button');
    for (const b of btns) {{
        if ((b.textContent || '').trim() === '{text}') {{
            b.click(); return true;
        }}
    }}
    return false;
}}""")
print(f"Clicked: {clicked}", file=sys.stderr)
time.sleep(3)

# Check if modal closed (success)
modal_still_open = page.evaluate("() => !!document.querySelector('[role=\"dialog\"]')")
if not modal_still_open:
    print("Modal closed — checking for success...", file=sys.stderr)
    text = page.evaluate("() => document.body.innerText").lower()
    if any(w in text for w in ["thank you", "submitted", "your application"]):
        print("RESULT: submitted", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(0)

time.sleep(3)

# Check what changed
new_modal = page.evaluate("""() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) return { status: 'no_modal' };
    const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    const fileInputs = d.querySelectorAll('input[type="file"]');
    return {
        status: 'open',
        progress: (d.querySelector('[data-test-text-progress-percent]')||{}).textContent||'',
        fieldCount: inputs.length,
        fields: Array.from(inputs).map(el => {
            const lbl = d.querySelector('label[for="'+el.id+'"]');
            return {
                tag: el.tagName, type: el.getAttribute('type')||'',
                label: (lbl?lbl.textContent.trim():'')||el.placeholder||el.getAttribute('aria-label')||'',
                required: el.required, value: el.value||'',
            };
        }),
        resumeUpload: fileInputs.length > 0,
        buttons: Array.from(d.querySelectorAll('button')).map(b => ({
            text: (b.textContent||'').trim().slice(0,25), disabled: b.disabled,
        })),
    };
}""")

state["modal"] = new_modal
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"Progress: {new_modal.get('progress', '')}", file=sys.stderr)
print(f"Fields ({new_modal.get('fieldCount', 0)}):", file=sys.stderr)
for f in new_modal.get('fields', []):
    print(f"  [{f['tag']}] '{f['label']}' req={f['required']} val='{f['value']}'", file=sys.stderr)
print(f"Buttons: {[b['text'] for b in new_modal.get('buttons', [])]}", file=sys.stderr)

# Route
has_empty_req = any(f['required'] and not f['value'] for f in new_modal.get('fields', []))
has_submit = any(b['text'].lower() in ('submit', 'submit application', 'send', 'done') for b in new_modal.get('buttons', []))
has_next = any(b['text'].lower() in ('next', 'review') for b in new_modal.get('buttons', []))

if has_empty_req:
    print("NEXT: apply/linkedin/easy_apply/03_fill_fields.py", file=sys.stderr)
elif has_submit:
    print("NEXT: apply/linkedin/easy_apply/06_submit.py", file=sys.stderr)
elif has_next:
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
else:
    print("NEXT: none (unknown state)", file=sys.stderr)
