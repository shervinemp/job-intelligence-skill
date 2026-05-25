#!/usr/bin/env python3
"""02_read_state.py — Read current modal state (fields, buttons, progress).
Reconnects to the existing page with the open modal.
Output: current fields + buttons + progress for user review.
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
    if 'jobs/search' in p.url or 'jobs/view' in p.url:
        page = p
        break
if not page:
    print("ERROR: no LinkedIn page found", file=sys.stderr)
    sys.exit(1)

modal = page.evaluate("""() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) return { status: 'no_modal' };
    const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    const fileInputs = d.querySelectorAll('input[type="file"]');
    return {
        status: 'open',
        url: location.href,
        progress: (d.querySelector('[data-test-text-progress-percent]') || {}).textContent || '',
        first500: (d.innerText || '').slice(0, 500),
        fields: Array.from(inputs).map(el => {
            const lbl = d.querySelector('label[for="'+el.id+'"]');
            const opts = el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [];
            return {
                tag: el.tagName, type: el.getAttribute('type')||'',
                id_tail: (el.id||'').slice(-30),
                label: (lbl?lbl.textContent.trim():'')||el.placeholder||el.getAttribute('aria-label')||'',
                required: el.required, hasValue: !!el.value, value: el.value||'',
                options: opts.slice(0, 8),
            };
        }),
        resumeUpload: fileInputs.length > 0,
        buttons: Array.from(d.querySelectorAll('button')).map(b => ({
            text: (b.textContent||'').trim().slice(0, 30), disabled: b.disabled,
        })),
    };
}""")

state["modal"] = modal
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

if modal['status'] != 'open':
    print("Modal: closed", file=sys.stderr)
    # Check for success
    text = page.evaluate("() => document.body.innerText").lower()
    for w in ["thank you", "application submitted", "your application"]:
        if w in text:
            print("RESULT: submitted", file=sys.stderr)
            break
    else:
        for w in ["already applied", "applied"]:
            if w in text:
                print("RESULT: already_applied", file=sys.stderr)
                break
    print("NEXT: none", file=sys.stderr)
    sys.exit(0)

print(f"Progress: {modal['progress']}", file=sys.stderr)
print(f"Fields ({len(modal['fields'])}):", file=sys.stderr)
for f_info in modal['fields']:
    opts = f" opts={f_info['options']}" if f_info['options'] else ""
    print(f"  [{f_info['tag']}:{f_info['type']}] '{f_info['label']}' req={f_info['required']} val='{f_info['value']}'{opts}", file=sys.stderr)
if modal['resumeUpload']:
    print(f"  [FILE] Resume upload field detected", file=sys.stderr)
print(f"Buttons: {[b['text'] for b in modal['buttons']]}", file=sys.stderr)

# Determine next step
has_empty_required = any(f['required'] and not f['value'] for f in modal['fields'])
has_next = any(b['text'].lower() in ('next', 'review') and not b['disabled'] for b in modal['buttons'])
has_submit = any(b['text'].lower() in ('submit', 'submit application', 'send', 'done') and not b['disabled'] for b in modal['buttons'])

if has_empty_required:
    print("NEXT: apply/linkedin/easy_apply/03_fill_fields.py", file=sys.stderr)
elif has_submit:
    print("NEXT: apply/linkedin/easy_apply/06_submit.py", file=sys.stderr)
elif has_next:
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
else:
    print("NEXT: apply/linkedin/easy_apply/03_fill_fields.py", file=sys.stderr)
