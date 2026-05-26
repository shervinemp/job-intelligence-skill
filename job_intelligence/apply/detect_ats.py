#!/usr/bin/env python3
"""detect_ats.py — Navigate to external ATS URL, detect platform, read form fields.
Usage: python3 detect_ats.py <jid>
Reads external_url from apply_state.json (saved by navigate step) or DB notes,
then reads the form and hands off to common filler.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.platforms import detect_platform, check_page, ALREADY_APPLIED, LOGIN_WALL

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

jid = sys.argv[1]

c = get_conn()
r = c.execute("SELECT url, title, company, notes FROM jobs WHERE id=?", (jid,)).fetchone()
title, company = r["title"], r["company"]

# Priority: apply_state.json external_url > DB notes > DB url
url = r["url"]
existing_state = {}
if os.path.exists(STATE_PATH):
    with open(STATE_PATH) as f:
        existing_state = json.load(f)
    external = existing_state.get("external_url")
    if external:
        url = external
    else:
        # Try DB notes
        notes = r["notes"]
        if notes:
            try:
                n = json.loads(notes)
                if n.get("external_url"):
                    url = n["external_url"]
            except (json.JSONDecodeError, TypeError):
                pass

print(f"JOB: {title} @ {company}", file=sys.stderr)

plat = detect_platform(url)

# Aggregator check (not a real ATS)
if plat is None and "jobright.ai" in url:
    print("Platform: jobright (aggregator)", file=sys.stderr)
    print("TYPE: aggregator (no apply form)", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(0)

print(f"Platform: {plat}", file=sys.stderr)

b, ctx = connect()
p = ctx.new_page()
p.goto(url, wait_until='domcontentloaded', timeout=30000)
time.sleep(5)

# Read form fields
info = p.evaluate("""() => {
    const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    const fileInputs = document.querySelectorAll('input[type="file"]');
    const fields = Array.from(inputs).map(el => {
        const lbl = document.querySelector('label[for="'+el.id+'"]');
        const parent = el.closest('div, fieldset, section, li');
        const plbl = parent ? parent.querySelector('label, legend, [role="heading"], strong, span') : null;
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
    return {
        fieldCount: fields.length,
        hasFileUpload: fileInputs.length > 0,
        url: location.href,
        fields: fields,
        buttons: Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => ({
            text: (b.textContent||'').trim().slice(0, 25), disabled: b.disabled,
        })),
    };
}""")

# Enrich existing state — don't overwrite external_url
existing_state.setdefault("jid", jid)
existing_state.update({"type": "ats_direct", "platform": plat, "external_form": info})
existing_state.setdefault("external_url", url)
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
with open(STATE_PATH, "w") as f:
    json.dump(existing_state, f, indent=2)

# Persist external_url to DB notes for future runs
if existing_state.get("external_url") and existing_state["external_url"] != r["url"]:
    notes = r["notes"]
    try:
        notes_dict = json.loads(notes) if notes else {}
    except (json.JSONDecodeError, TypeError):
        notes_dict = {}
    if not isinstance(notes_dict, dict):
        notes_dict = {}
    notes_dict["external_url"] = existing_state["external_url"]
    c.execute("UPDATE jobs SET notes=? WHERE id=?", (json.dumps(notes_dict), jid))
    c.commit()

print(f"Fields ({info['fieldCount']}):", file=sys.stderr)
for f in info['fields'][:15]:
    opts = f" opts={f['options'][:3]}" if f.get('options') else ''
    val = f" val='{f['value']}'" if f['value'] else ''
    print(f"  [{f['tag']}:{f['type']}] '{f['label']}' req={f['required']}{val}{opts}", file=sys.stderr)
if info['hasFileUpload']:
    print(f"  [FILE] Resume upload available", file=sys.stderr)
print(f"Buttons: {[b['text'] for b in info['buttons'][:5]]}", file=sys.stderr)

print("NEXT: apply/common/01_fill_fields.py", file=sys.stderr)
