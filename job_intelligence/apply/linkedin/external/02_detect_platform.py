#!/usr/bin/env python3
"""02_detect_platform.py — Detect ATS platform and read form fields.
Navigates to the external URL if needed, identifies the ATS,
and reports all form fields for review.
"""
import json, os, sys, time, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

external_url = state.get("external_url", "")
if not external_url:
    print("ERROR: no external URL in state", file=sys.stderr)
    sys.exit(1)

# Detect platform from URL
def detect_platform(url):
    host = url.split("/")[2] if "//" in url else ""
    for kw, plat in [
        ("greenhouse", "greenhouse"), ("lever.co", "lever"),
        ("myworkdayjobs", "workday"), ("workday.com", "workday"),
        ("ashbyhq", "ashby"), ("icims", "icims"), ("taleo", "taleo"),
        ("smartrecruiters", "smartrecruiters"), ("bamboohr", "bamboohr"),
    ]:
        if kw in host or kw in url:
            return plat
    return "unknown"

plat = detect_platform(external_url)
print(f"Platform: {plat}", file=sys.stderr)

b, ctx = connect()
page = None
for p in ctx.pages:
    if external_url in p.url or (plat != "unknown" and plat in p.url):
        page = p
        break
if not page:
    page = ctx.new_page()
    page.goto(external_url, wait_until='domcontentloaded', timeout=30000)
    time.sleep(5)

# Read form fields
info = page.evaluate("""() => {
    const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    const fileInputs = document.querySelectorAll('input[type="file"]');
    const fields = Array.from(inputs).map(el => {
        const lbl = document.querySelector('label[for="'+el.id+'"]');
        const parent = el.closest('div, fieldset, section, li');
        const plbl = parent ? parent.querySelector('label, legend, [role="heading"], strong, span:not([class*="hidden"])') : null;
        let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
        if (!label && plbl) label = plbl.textContent.trim();
        const opts = el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [];
        return {
            tag: el.tagName, type: el.getAttribute('type')||'',
            id: el.id, name: el.getAttribute('name')||'',
            label: label.replace(/\\s+/g, ' ').trim().slice(0, 80),
            placeholder: el.getAttribute('placeholder')||'',
            required: el.required || el.getAttribute('aria-required') === 'true',
            value: el.value||'',
            options: opts.slice(0, 12),
        };
    });
    return {
        fieldCount: fields.length,
        hasFileUpload: fileInputs.length > 0,
        fields: fields,
        buttons: Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => ({
            text: (b.textContent||'').trim().slice(0, 30), disabled: b.disabled,
        })),
        first500: (document.body.innerText||'').slice(0, 500),
    };
}""")

state["platform"] = plat
state["external_form"] = info
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"Fields ({info['fieldCount']}):", file=sys.stderr)
for f in info['fields']:
    opts = f" opts={f['options'][:3]}" if f.get('options') else ''
    print(f"  [{f['tag']}:{f['type']}] '{f['label']}' req={f['required']} val='{f['value']}'{opts}", file=sys.stderr)
if info['hasFileUpload']:
    print(f"  [FILE] Resume upload available", file=sys.stderr)
print(f"Buttons: {[b['text'] for b in info['buttons']]}", file=sys.stderr)

# Next step
has_empty_req = any(f['required'] and not f['value'] for f in info['fields'])
if has_empty_req:
    print("NEXT: apply/common/01_fill_fields.py", file=sys.stderr)
else:
    print("NEXT: apply/external/03_submit.py", file=sys.stderr)
