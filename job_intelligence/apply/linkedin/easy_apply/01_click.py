#!/usr/bin/env python3
"""01_click.py — Click the Easy Apply button in the search results pane.
State: reads apply_state.json, clicks Easy Apply, updates state with modal info.
Leaves modal open. Next script reconnects and finds the page by URL.
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
    if 'jobs/search' in p.url:
        page = p
        break
if not page:
    page = ctx.new_page()
    page.goto(state["search_url"], wait_until='domcontentloaded', timeout=30000)
    time.sleep(5)

# Click Easy Apply button in pane
clicked = page.evaluate("""() => {
    const pane = document.querySelector('.jobs-search__job-details--container');
    if (!pane) return false;
    const btns = pane.querySelectorAll('button');
    for (const b of btns) {
        if ((b.textContent || '').trim().toLowerCase() === 'easy apply' && !b.disabled) {
            b.click(); return true;
        }
    }
    return false;
}""")
print(f"Clicked Easy Apply: {clicked}", file=sys.stderr)
time.sleep(4)

# Check modal
modal = page.evaluate("""() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) return { status: 'no_modal' };
    const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    return {
        status: 'open',
        fieldCount: inputs.length,
        first200: (d.innerText || '').slice(0, 200),
        progress: (d.querySelector('[data-test-text-progress-percent]') || {}).textContent || '',
        fields: Array.from(inputs).map(el => {
            const lbl = d.querySelector('label[for="' + el.id + '"]');
            return {
                tag: el.tagName, type: el.getAttribute('type') || '',
                label: (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '',
                required: el.required, hasValue: !!el.value,
            };
        }),
        buttons: Array.from(d.querySelectorAll('button')).map(b => ({
            text: (b.textContent || '').trim().slice(0, 25),
            disabled: b.disabled,
        })),
    };
}""")

state["modal"] = modal
state["modal_url"] = page.url
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"Modal: {modal['status']}", file=sys.stderr)
if modal['status'] == 'open':
    print(f"Fields ({modal['fieldCount']}):", file=sys.stderr)
    for f_info in modal['fields']:
        print(f"  [{f_info['tag']}:{f_info['type']}] '{f_info['label']}' req={f_info['required']} filled={f_info['hasValue']}", file=sys.stderr)
    print(f"Buttons: {[b['text'] for b in modal['buttons']]}", file=sys.stderr)
    print(f"Progress: {modal['progress']}", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/02_read_state.py", file=sys.stderr)
else:
    print("NEXT: none", file=sys.stderr)
