#!/usr/bin/env python3
"""01_fill_fields.py — Shared field filler for any form (LinkedIn modal or external ATS).
Reads state, fills fields from profile, reports unfilled required fields.
"""
import json, os, sys, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.chrome_manager import connect
from apply.common.platforms import check_page, ALREADY_APPLIED, LOGIN_WALL, GUEST_APPLY

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

profile_path = os.path.join(os.path.dirname(__file__), "..", "..", "profile.json")
with open(profile_path) as f:
    profile = json.load(f)
ca = profile.get("common_answers", {})

# Heuristic label → value resolution
FIELD_MAP = {
    "full name": ("name", ""),
    "first name": ("first_name", ""), "firstname": ("first_name", ""),
    "last name": ("last_name", ""), "lastname": ("last_name", ""),
    "email": ("email", ""), "email address": ("email", ""),
    "phone": ("phone", ""), "phone number": ("phone", ""), "mobile": ("phone"),
    "linkedin": ("linkedin", ""), "linkedin profile": ("linkedin", ""), "linkedin profile link": ("linkedin", ""),
    "github": ("github", ""), "portfolio": ("portfolio", ""), "website": ("website", ""),
    "resume": ("resume", ""), "upload resume": ("resume", ""), "cv": ("resume", ""),
}

CA_MAP = {
    "work authorization": "work_authorization", "authorized": "authorized_to_work",
    "visa sponsorship": "require_visa", "sponsorship": "visa_sponsorship", "require visa": "require_visa",
    "gender": "gender", "veteran": "veteran_status", "disability": "disability_status",
    "how did you hear": "how_did_you_hear", "referral": "how_did_you_hear",
    "relocate": "willing_to_relocate", "relocation": "willing_to_relocate",
    "salary": "expected_ctc",
}

# Yes/no defaults for common questions
def resolve(label, profile, ca):
    norm = re.sub(r'[^a-z0-9]+', ' ', label.lower()).strip()
    
    # Direct field map
    key = FIELD_MAP.get(norm)
    if key:
        section, field = key
        if section == "name" and field == "":
            fn = profile.get("first_name", "")
            ln = profile.get("last_name", "")
            if fn and ln: return f"{fn} {ln}"
            return fn or ln or None
        lookup = field or section
        val = profile.get(lookup) or ca.get(lookup)
        if val: return val
    
    # Common answers match
    for phrase, ca_key in CA_MAP.items():
        if phrase in norm:
            val = ca.get(ca_key)
            if val: return val
    
    return None

b, ctx = connect()

# Find the right page: LinkedIn modal or external ATS
page = None
for p in ctx.pages:
    url = p.url
    if state.get("external_url") and state["external_url"] in url:
        page = p
        break
    if '/jobs/view/' in url:
        page = p
        break
if not page:
    print("ERROR: no relevant page found", file=sys.stderr)
    sys.exit(1)

# Check for already-applied or login wall
plat = state.get("platform") or ""
text = page.evaluate("() => document.body.innerText")
if check_page(text, plat, ALREADY_APPLIED):
    print("STATUS: already_applied", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(0)
if check_page(text, plat, LOGIN_WALL):
    print("STATUS: login_wall", file=sys.stderr)
    # Try guest apply
    guest = page.evaluate("""() => {""" + f"""
        const patterns = {json.dumps(list(set(GUEST_APPLY.get(plat, []) + GUEST_APPLY["default"])))};
""" + """
        const all = document.querySelectorAll('button, a');
        for (const el of all) {
            const t = (el.textContent || '').toLowerCase();
            for (const p of patterns) {
                if (t.includes(p) && el.offsetParent !== null) {
                    el.click(); return true;
                }
            }
        }
        return false;
    }""")
    if guest:
        print("  Guest apply clicked", file=sys.stderr)
        import time
        time.sleep(3)
    else:
        print("  Cannot bypass login wall", file=sys.stderr)
        print("NEXT: none", file=sys.stderr)
        sys.exit(0)

# Get fields
fields = page.evaluate("""() => {
    const container = document.querySelector('[role="dialog"]') || document;
    const inputs = container.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    return Array.from(inputs).map(el => {
        const lbl = container.querySelector('label[for="'+el.id+'"]');
        const parent = el.closest('div, fieldset, section, li');
        const plbl = parent ? parent.querySelector('label, legend, strong, span:not([class*="hidden"])') : null;
        let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
        if (!label && plbl) label = plbl.textContent.trim();
        const opts = el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [];
        return {
            tag: el.tagName, type: el.getAttribute('type')||'',
            id: el.id, name: el.getAttribute('name')||'',
            label: label.replace(/\\s+/g, ' ').trim().slice(0, 80),
            required: el.required, value: el.value||'',
            options: opts.slice(0, 12),
        };
    });
}""")

# Find resume PDF
resume_path = None
results_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "results", state["jid"])
if os.path.isdir(results_dir):
    for f_name in os.listdir(results_dir):
        if "Resume" in f_name and f_name.endswith(".pdf"):
            resume_path = os.path.join(results_dir, f_name)
            break

filled = 0
unfilled = []
handled_radios = set()  # track radio groups by name

for f in fields:
    # Skip already-filled fields (except "Select an option")
    if f['value'] and f['value'] != 'Select an option':
        continue
    
    val = resolve(f['label'], profile, ca)
    
    # Radio buttons: NEVER auto-fill — present to model
    if f['type'] == 'radio':
        if f['name'] in handled_radios:
            continue
        radios = [rf for rf in fields if rf['name'] == f['name']]
        handled_radios.add(f['name'])
        unfilled.append({"tag": "radio_group", "label": f['label'].split(' - ')[0] if ' - ' in f['label'] else f['label'],
                         "options": [rf['label'] for rf in radios], "required": f['required']})
        continue
    
    if f['type'] == 'checkbox':
        # Checkbox: NEVER auto-fill
        if f['required']:
            unfilled.append(f)
        continue
    
    if f['tag'] == 'INPUT' and f['type'] == 'file':
        if resume_path:
            try:
                el = page.query_selector(f"input[type=\"file\"]")
                if el:
                    el.set_input_files(resume_path)
                    filled += 1
                    print(f"  UPLOADED: resume -> {os.path.basename(resume_path)}", file=sys.stderr)
            except Exception as e:
                print(f"  UPLOAD FAILED: {e}", file=sys.stderr)
        continue
    
    if val:
        sel = f"#{f['id']}" if f['id'] and not f['id'][0].isdigit() else f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]"
        if not sel or sel == '#':
            unfilled.append(f)
            continue
        try:
            el = page.query_selector(sel)
            if el:
                if f['tag'] == 'SELECT':
                    for opt in f['options']:
                        if val.lower() in opt.lower():
                            el.select_option(opt); break
                    else:
                        el.select_option(val)
                elif f['tag'] == 'TEXTAREA' or f['type'] in ('text', 'email', 'tel'):
                    el.fill(val)
                filled += 1
                print(f"  FILLED: '{f['label']}' -> '{val[:30]}'", file=sys.stderr)
        except Exception as e:
            print(f"  FAILED: '{f['label']}' ({e})", file=sys.stderr)
            if f['required']:
                unfilled.append(f)
    elif f['required']:
        unfilled.append(f)

state["filled"] = filled
state["unfilled"] = [f"{f['label']}" for f in unfilled]
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"\nFilled: {filled}", file=sys.stderr)
if unfilled:
    print(f"Unfilled required ({len(unfilled)}):", file=sys.stderr)
    for f in unfilled:
        opts = f" options={f['options'][:3]}" if f.get('options') else ''
        print(f"  '{f['label']}' type={f['tag']}:{f['type']}{opts}", file=sys.stderr)
    print("NEXT: apply/common/01_fill_fields.py (rerun or model provides answers)", file=sys.stderr)
else:
    print("NEXT: apply/external/03_submit.py", file=sys.stderr)
