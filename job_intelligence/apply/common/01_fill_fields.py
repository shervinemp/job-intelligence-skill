#!/usr/bin/env python3
"""01_fill_fields.py — Shared field filler for any form (LinkedIn modal or external ATS).
Reads state, fills fields from profile, reports unfilled required fields.

Usage:
  fill_fields.py <jid> [--answer "question"=value ...]

--answer flags provide answers to unfilled fields. Each answer is saved
to profile.json common_answers for future reuse.
"""
import json, os, sys, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.chrome_manager import connect
from apply.common.platforms import check_page, ALREADY_APPLIED, LOGIN_WALL, GUEST_APPLY
from apply.common import find_apply_page

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

profile_path = os.path.join(os.path.dirname(__file__), "..", "..", "profile.json")
with open(profile_path) as f:
    profile = json.load(f)

# Parse --answer flags
answers_override = {}
argv = sys.argv[1:]  # first is jid
found_jid = False
for a in argv:
    if not found_jid:
        found_jid = True
        continue
    if a.startswith("--answer"):
        eq = a.find("=")
        if eq > 0:
            q = a[len("--answer="):eq]
            v = a[eq+1:]
            if q.endswith('"') and q.startswith('"'):
                q = q[1:-1]
            answers_override[q] = v
            # Also handle "question"=value with equals in value
        elif '=' not in a:
            continue
# Handle the case where --answer is followed by space-separated key=value
i = 0
while i < len(argv):
    if argv[i] == '--answer' and i + 1 < len(argv) and '=' in argv[i+1]:
        parts = argv[i+1].split('=', 1)
        answers_override[parts[0]] = parts[1]
        i += 2
    else:
        i += 1

def save_answer(question_text, answer):
    """Save a question→answer mapping to common_answers for future reuse."""
    norm = re.sub(r'[^a-z0-9]+', '_', question_text.lower()).strip('_')[:60]
    # Try to find a concise key — use first distinctive 3-4 words
    words = [w for w in norm.split('_') if len(w) > 2]
    key = '_'.join(words[:4]) if len(words) >= 3 else norm
    profile.setdefault("common_answers", {})
    existing = profile["common_answers"]
    # Only save if no existing answer for this key
    if key not in existing or existing[key] != answer:
        existing[key] = answer
        with open(profile_path, "w") as f:
            json.dump(profile, f, indent=2)
        print(f"  SAVED: common_answers['{key}'] = '{answer}'", file=sys.stderr)

# Heuristic label → profile value resolution
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

def resolve(label):
    """Resolve a field label to a profile value. Returns None if uncertain."""
    norm = re.sub(r'[^a-z0-9]+', ' ', label.lower()).strip()
    key = FIELD_MAP.get(norm)
    if key:
        section, field = key
        if section == "name" and field == "":
            fn = profile.get("first_name", "")
            ln = profile.get("last_name", "")
            if fn and ln: return f"{fn} {ln}"
            return fn or ln or None
        lookup = field or section
        return profile.get(lookup)
    return None

b, ctx = connect()
ext_url = state.get("external_url", "")
page, navigated_fresh = find_apply_page(ctx, ext_url or None)

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

# Get standard form fields
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

# Detect custom question buttons (Ashby-style yes/no button groups not rendered as radios)
questions = page.evaluate("""() => {
    const qs = [];
    const sections = document.querySelectorAll('div[class*="question"], fieldset, div[class*="field"], li[class*="field"]');
    for (const sec of sections) {
        const label = sec.querySelector('label, legend, strong, p, [class*="label"], [class*="heading"], [class*="title"]');
        if (!label) continue;
        const btns = sec.querySelectorAll('button');
        if (btns.length < 2) continue;
        const qText = (label.textContent || '').trim();
        if (qText.length < 5) continue;
        const opts = Array.from(btns).map(b => (b.textContent || '').trim()).filter(Boolean);
        const answered = btns.length === 0 || Array.from(btns).some(b => {
            const cls = b.getAttribute('class') || '';
            return cls.includes('selected') || cls.includes('active') || b.getAttribute('aria-pressed') === 'true';
        });
        if (!answered) {
            qs.push({ question: qText.slice(0, 120), options: opts, required: true });
        }
    }
    return qs;
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
file_uploaded = False
handled_radios = set()

# Common answers from profile for auto-resolve
common_answers = profile.get("common_answers", {})

def check_answers(label, opts=None):
    """Check answers_override and common_answers for a matching answer."""
    # CLI override takes priority
    if label in answers_override:
        return answers_override[label]
    # Check common_answers for any matching key
    norm = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')
    for key, val in common_answers.items():
        if val and key.replace('_', ' ') in norm or norm[:len(key)] == key:
            return val
    # Check common_answers keys against radio options
    if opts:
        norm_opts = set(re.sub(r'[^a-z0-9]+', '', o.lower()) for o in opts)
        for key, val in common_answers.items():
            val_norm = re.sub(r'[^a-z0-9]+', '', val.lower())
            if val_norm in norm_opts:
                for opt in opts:
                    if val_norm in re.sub(r'[^a-z0-9]+', '', opt.lower()):
                        return opt
    return None

def apply_and_save(label, value, tag_type, options=None):
    """Apply a value to a field, file, radio, or button. Save for future."""
    global filled
    
    if tag_type in ('radio_group', 'button_group') and options:
        for opt in options:
            if value.lower() in opt.lower() or opt.lower() in value.lower():
                if tag_type == 'radio_group':
                    page.evaluate(f"(opt) => {{ const rs = document.querySelectorAll('input[type=\"radio\"]'); for (const r of rs) {{ const lbl = document.querySelector('label[for=\"' + r.id + '\"]'); if (lbl && (lbl.textContent||'').trim() === opt) {{ r.click(); return true; }} }} return false; }}", opt)
                else:
                    page.evaluate("(opt) => { const secs = document.querySelectorAll('div[class*=\"question\"], fieldset, div[class*=\"field\"], li[class*=\"field\"]'); for (const sec of secs) { const btns = sec.querySelectorAll('button'); for (const b of btns) { if ((b.textContent||'').trim() === opt) { b.click(); return true; } } } return false; }", opt)
                filled += 1
                print(f"  ANSWERED: '{label[:40]}' -> '{value}'", file=sys.stderr)
                save_answer(label, value)
                return True
    
    # Text fields: find by label
    sel = None
    for f in fields:
        if f['label'] == label or label in f['label'] or f['label'] in label:
            sel = f"#{f['id']}" if f['id'] else f"[name=\"{f['name']}\"]" if f['name'] else None
            if sel:
                try:
                    el = page.query_selector(sel)
                    if el:
                        if f['tag'] == 'SELECT':
                            el.select_option(value)
                        else:
                            el.fill(value)
                        filled += 1
                        print(f"  FILLED: '{label[:40]}' -> '{value}'", file=sys.stderr)
                        save_answer(label, value)
                        return True
                except:
                    pass
    return False

for f in fields:
    if f['value'] and f['value'] != 'Select an option':
        continue
    
    if f['tag'] == 'INPUT' and f['type'] == 'file':
        if file_uploaded:
            continue
        if resume_path:
            try:
                el = page.query_selector("input[type=\"file\"]")
                if el:
                    el.set_input_files(resume_path)
                    file_uploaded = True
                    filled += 1
                    print(f"  UPLOADED: resume -> {os.path.basename(resume_path)}", file=sys.stderr)
            except Exception as e:
                print(f"  UPLOAD FAILED: {e}", file=sys.stderr)
        continue
    
    if f['type'] == 'radio':
        if f['name'] in handled_radios:
            continue
        handled_radios.add(f['name'])
        radios = [rf for rf in fields if rf['name'] == f['name']]
        group_label = radios[0]['label'].split(' - ')[0] if ' - ' in radios[0]['label'] else radios[0]['label']
        opts = [rf['label'] for rf in radios]
        
        # Check answers before deferring
        ans = check_answers(group_label, opts)
        if ans and apply_and_save(group_label, ans, 'radio_group', opts):
            continue
        
        unfilled.append({"tag": "radio_group", "label": group_label[:60], "options": opts, "required": f['required']})
        continue
    
    if f['type'] == 'checkbox':
        if f['required']:
            unfilled.append(f)
        continue
    
    # Check override/common_answers first
    ans = check_answers(f['label'])
    if ans:
        apply_and_save(f['label'], ans, 'text')
        continue
    
    val = resolve(f['label'])
    if val:
        sel = f"#{f['id']}" if f['id'] and not f['id'][0].isdigit() else f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]"
        if not sel or sel == '#':
            if f['required']:
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

# Custom button questions (Ashby-style)
for q in questions:
    if set(o.lower() for o in q['options']) <= {"replace", "upload file", "upload"}:
        continue
    ans = check_answers(q['question'], q['options'])
    if ans and apply_and_save(q['question'], ans, 'button_group', q['options']):
        continue
    unfilled.append({"label": q['question'], "tag": "button_group", "type": "choice", "options": q['options'], "required": True})

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
