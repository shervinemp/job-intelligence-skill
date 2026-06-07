#!/usr/bin/env python3
"""Greenhouse handler. Routes to common fill → next → submit.
Greenhouse: multi-page forms, screening questions, resume upload.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from common.platforms import detect_platform, check_page, ALREADY_APPLIED, LOGIN_WALL

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

jid = sys.argv[1]
c = get_conn()
r = c.execute("SELECT url, title, company FROM jobs WHERE id=?", (jid,)).fetchone()
url, title, company = r["url"], r["title"], r["company"]
print(f"JOB: {title} @ {company}", file=sys.stderr)
print(f"PLATFORM: greenhouse", file=sys.stderr)

b, ctx = connect()
p = ctx.new_page()
p.goto(url, wait_until='domcontentloaded', timeout=30000)
time.sleep(5)

# Check already-applied
text = p.evaluate("() => document.body.innerText")
if check_page(text, "greenhouse", ALREADY_APPLIED):
    print("STATUS: already_applied", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(0)

# Read form
info = p.evaluate("""() => {
    const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    const fileInputs = document.querySelectorAll('input[type="file"]');
    return {
        fieldCount: inputs.length,
        hasFileUpload: fileInputs.length > 0,
        url: location.href,
        fields: Array.from(inputs).map(el => {
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
        }),
        buttons: Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => ({
            text: (b.textContent||'').trim().slice(0, 25), disabled: b.disabled,
        })),
    };
}""")

state = {"jid": jid, "url": url, "type": "greenhouse", "platform": "greenhouse",
         "external_url": url, "external_form": info}
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

# Check for login wall
if check_page(text, "greenhouse", LOGIN_WALL):
    state["login_wall"] = True
    print("STATUS: login_wall", file=sys.stderr)

with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"Fields ({info['fieldCount']}):", file=sys.stderr)
for f in info['fields'][:15]:
    opts = f" opts={f['options'][:3]}" if f.get('options') else ''
    val = f" val='{f['value']}'" if f['value'] else ''
    print(f"  [{f['tag']}:{f['type']}] '{f['label']}' req={f['required']}{val}{opts}", file=sys.stderr)
print(f"Buttons: {[b['text'] for b in info['buttons'][:5]]}", file=sys.stderr)

print("NEXT: apply/common/01_fill_fields.py", file=sys.stderr)
