#!/usr/bin/env python3
"""03_fill_fields.py — Fill modal fields from profile using heuristic mapping.
Fills all mapped fields, reports unmapped required fields for LLM fallback.
"""
import json, os, sys, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

# Load profile
profile_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "profile.json")
with open(profile_path) as f:
    profile = json.load(f)

ca = profile.get("common_answers", {})

# Field label → profile key mapping
FIELD_MAP = {
    "first name": ("first_name", ""), "firstname": ("first_name", ""),
    "last name": ("last_name", ""), "lastname": ("last_name", ""),
    "email": ("email", ""), "email address": ("email", ""),
    "phone": ("phone", ""), "phone number": ("phone", ""), "mobile phone number": ("phone", ""), "mobile": ("phone", ""),
    "city": ("location", ""), "location": ("location", ""),
    "linkedin": ("linkedin", ""), "linkedin profile": ("linkedin", ""),
    "github": ("github", ""), "github url": ("github", ""),
    "portfolio": ("portfolio", ""), "website": ("website", ""),
    "phone country code": ("common_answers", "phone_country"),
}

COMMON_FIELD_MAP = {
    "work authorization": "work_authorization", "authorized to work": "authorized_to_work",
    "visa": "require_visa", "visa sponsorship": "visa_sponsorship",
    "require visa": "require_visa", "sponsorship": "visa_sponsorship",
    "gender": "gender", "veteran": "veteran_status", "veteran status": "veteran_status",
    "disability": "disability_status", "disability status": "disability_status",
    "how did you hear": "how_did_you_hear", "referral": "how_did_you_hear",
    "relocate": "willing_to_relocate", "relocation": "willing_to_relocate",
    "current salary": "current_ctc", "expected salary": "expected_ctc",
    "notice period": "notice_period",
}

def resolve(label, profile, ca):
    norm = re.sub(r'[^a-z0-9]+', ' ', label.lower()).strip()
    # Direct map lookup
    if norm in FIELD_MAP:
        section, key = FIELD_MAP[norm]
        if section == "common_answers":
            return ca.get(key)
        lookup = key or section
        val = profile.get(lookup) or ca.get(lookup)
        if val: return val
    # Substring match for noisy labels (e.g. "Location (city)Location (city)")
    for map_norm, (section, key) in FIELD_MAP.items():
        if map_norm in norm:
            lookup = key or section
            val = profile.get(lookup) or ca.get(lookup)
            if val: return val
    # Common answers multi-word match
    for phrase, key in COMMON_FIELD_MAP.items():
        if phrase in norm:
            return ca.get(key)
    return None

b, ctx = connect()
page = None
for p in ctx.pages:
    if 'jobs/' in p.url:
        page = p
        break
if not page:
    print("ERROR: no LinkedIn page found", file=sys.stderr); sys.exit(1)

# Get current fields from modal
fields_result = page.evaluate("""() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) return [];
    const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    return Array.from(inputs).map(el => {
        const lbl = d.querySelector('label[for="'+el.id+'"]');
        return {
            tag: el.tagName, type: el.getAttribute('type')||'',
            id: el.id, name: el.getAttribute('name')||'',
            label: (lbl?lbl.textContent.trim():'')||el.placeholder||el.getAttribute('aria-label')||'',
            required: el.required, value: el.value||'',
            options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [],
        };
    });
}""")

filled = 0
unfilled_required = []
resume_path = None

# Find resume PDF
results_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "results", state["jid"])
if os.path.isdir(results_dir):
    for f_name in os.listdir(results_dir):
        if "Resume" in f_name and f_name.endswith(".pdf"):
            resume_path = os.path.join(results_dir, f_name)
            break

for f_info in fields_result:
    # Skip already-filled fields (non-default values)
    if f_info["value"] and f_info["value"] != "Select an option":
        continue
    val = resolve(f_info["label"], profile, ca)
    if val is None:
        if f_info["required"] and (not f_info["value"] or f_info["value"] == "Select an option"):
            unfilled_required.append(f_info)
        continue
    
    # Fill the field
    sel = f"#{f_info['id']}" if f_info['id'] else f"[name=\"{f_info['name']}\"]"
    if not sel or sel == '#':
        continue
    try:
        el = page.query_selector(sel)
        if not el:
            continue
        if f_info["tag"] == "SELECT":
            # Try to find matching option
            found = False
            for opt in f_info["options"]:
                if val.lower() in opt.lower() or opt.lower() in val.lower():
                    el.select_option(opt)
                    found = True; break
            if not found:
                el.select_option(val)
        elif f_info["type"] in ("checkbox", "radio"):
            is_checked = val.lower() in ("yes", "true", "1", "on")
            if el.is_checked() != is_checked:
                el.click()
        elif not f_info["value"]:  # only fill if empty
            el.fill(val)
        filled += 1
        print(f"  FILLED: '{f_info['label']}' -> '{val[:30]}'", file=sys.stderr)
    except Exception as e:
        print(f"  FAILED: '{f_info['label']}' ({e})", file=sys.stderr)
        if f_info["required"] and (not f_info["value"] or f_info["value"] == "Select an option"):
            unfilled_required.append(f_info)

# Upload resume if available and file input exists
if resume_path:
    file_input = page.evaluate("() => document.querySelector('[role=\"dialog\"] input[type=\"file\"]')")
    if file_input:
        try:
            file_input.set_input_files(resume_path)
            print(f"  UPLOADED: resume -> {resume_path}", file=sys.stderr)
        except Exception as e:
            print(f"  UPLOAD FAILED: {e}", file=sys.stderr)

state["filled_count"] = filled
state["unfilled_required"] = [f["label"] for f in unfilled_required]
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"\nFilled: {filled}", file=sys.stderr)
if unfilled_required:
    print(f"Unfilled required ({len(unfilled_required)}):", file=sys.stderr)
    for f_info in unfilled_required:
        opts = f" options={f_info['options']}" if f_info.get('options') else ''
        print(f"  '{f_info['label']}' type={f_info['tag']}:{f_info['type']}{opts}", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/04_screening.py", file=sys.stderr)
else:
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
