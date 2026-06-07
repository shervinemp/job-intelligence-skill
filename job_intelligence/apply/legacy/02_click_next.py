#!/usr/bin/env python3
"""02_click_next.py — Click the primary action button on an external ATS page.
Handles "Next", "Continue", "Review", "Submit Application", etc.
For multi-page forms (Workday, Greenhouse), loops back to fill → next → fill → next.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

b, ctx = connect()
page = None
for p in ctx.pages:
    url = p.url
    if state.get("external_url") and state["external_url"] in url:
        page = p; break
    if '/jobs/' in url:
        page = p; break
if not page:
    print("ERROR: no relevant page found", file=sys.stderr); sys.exit(1)

# Find primary action button
actions = page.evaluate("""() => {
    const btns = document.querySelectorAll('button:not([disabled])');
    const keywords = [
        ['submit application', 'submit'], ['submit', 'submit'],
        ['send application', 'submit'], ['done', 'submit'],
        ['review', 'review'], ['next', 'next'], ['continue', 'next'],
        ['save', 'next'],
    ];
    for (const b of btns) {
        const t = (b.textContent || '').trim().toLowerCase();
        for (const [kw, action] of keywords) {
            if (t === kw || t.includes(kw)) {
                return { text: kw, action: action, found: true };
            }
        }
    }
    return { found: false };
}""")

if not actions.get('found'):
    print("No primary action button found", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(1)

print(f"Button: '{actions['text']}' -> {actions['action']}", file=sys.stderr)

# Click using button text
try:
    btn = page.locator(f'button:has-text("{actions["text"]}")').first
    btn.click(timeout=5000)
    print(f"Clicked: {actions['action']}", file=sys.stderr)
except Exception as e:
    print(f"Click failed: {e}", file=sys.stderr)
    sys.exit(1)

time.sleep(4)

# Check result
current_url = page.url
text = page.evaluate("() => document.body.innerText").lower()
for w in ["thank you", "application submitted", "your application", "has been submitted"]:
    if w in text:
        print("RESULT: submitted", file=sys.stderr)
        print("NEXT: none", file=sys.stderr)
        sys.exit(0)

# Still on a form page — re-read fields
new_fields = page.evaluate("""() => {
    const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    return Array.from(inputs).map(el => {
        const lbl = document.querySelector('label[for="'+el.id+'"]');
        const parent = el.closest('div, fieldset, section, li');
        const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
        let label = (lbl?lbl.textContent.trim():'')||el.placeholder||el.getAttribute('aria-label')||'';
        if (!label && plbl) label = plbl.textContent.trim();
        const opts = el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [];
        return {
            tag: el.tagName, type: el.getAttribute('type')||'',
            id: el.id, name: el.getAttribute('name')||'',
            label: label.replace(/\\s+/g,' ').trim().slice(0, 100),
            required: el.required, value: el.value||'',
            options: opts.slice(0, 15),
        };
    });
}""")

print(f"Next page: {len(new_fields)} fields", file=sys.stderr)
for f in new_fields[:10]:
    opts = f" opts={f['options'][:3]}" if f.get('options') else ''
    val = f" val='{f['value']}'" if f['value'] else ''
    print(f"  [{f['tag']}:{f['type']}] '{f['label']}' req={f['required']}{val}{opts}", file=sys.stderr)

print("NEXT: apply/common/01_fill_fields.py", file=sys.stderr)
