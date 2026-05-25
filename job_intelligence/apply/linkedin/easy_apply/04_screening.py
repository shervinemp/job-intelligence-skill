#!/usr/bin/env python3
"""04_screening.py — Resolve unfilled required fields via LLM.
Displays each question for user review before filling.
"""
import json, os, sys, time, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect
from lib.call_gemini import call_gemini_node

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

# Get unfilled required fields
fields = page.evaluate("""() => {
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

unfilled = [f for f in fields if f['required'] and (not f['value'] or f['value'] == 'Select an option')]
if not unfilled:
    print("No unfilled required fields", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
    sys.exit(0)

# Load profile
profile_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "profile.json")
with open(profile_path) as f:
    profile = json.load(f)
ca_text = "\n".join(f"{k}: {v}" for k, v in profile.get("common_answers", {}).items() if v)

prompt = "Answer these job application questions using my profile data. Return JSON.\n\n"
prompt += f"Name: {profile.get('first_name','')} {profile.get('last_name','')}\n"
prompt += f"Email: {profile.get('email','')}\nPhone: {profile.get('phone','')}\n"
prompt += ca_text + "\n\nQuestions:\n"
for f in unfilled:
    opts = f" Options: {f['options'][:6]}" if f.get('options') else ''
    prompt += f"  label='{f['label']}' type={f['tag']}:{f['type']}{opts}\n"
prompt += "\nReturn: [{\"label\":\"...\", \"value\":\"...\"}]"

print(f"Asking LLM for {len(unfilled)} fields...", file=sys.stderr)
ok, out = call_gemini_node(prompt, timeout_seconds=30)

if not ok:
    print(f"LLM failed: {out}", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/03_fill_fields.py", file=sys.stderr)
    sys.exit(1)

try:
    answers = json.loads(out) if isinstance(out, str) else out
    if not isinstance(answers, list): answers = [answers]
except:
    print(f"LLM returned invalid JSON: {out[:200]}", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/03_fill_fields.py", file=sys.stderr)
    sys.exit(1)

filled = 0
for item in answers:
    label = item.get("label", "")
    val = item.get("value", "")
    if not label or not val: continue
    for f in unfilled:
        if f['label'].lower().strip() == label.lower().strip():
            sel = f"#{f['id']}" if f['id'] else f"[name=\"{f['name']}\"]"
            if not sel or sel == '#': continue
            try:
                el = page.query_selector(sel)
                if el:
                    if f['tag'] == 'SELECT':
                        el.select_option(val)
                    elif f['type'] == 'checkbox':
                        if val.lower() in ('yes','true','1','on') and not el.is_checked(): el.click()
                    else:
                        el.fill(val)
                    filled += 1
                    print(f"  FILLED: '{label}' -> '{val}'", file=sys.stderr)
            except Exception as e:
                print(f"  FAILED: '{label}' ({e})", file=sys.stderr)
            break

print(f"LLM filled: {filled}/{len(unfilled)}", file=sys.stderr)
print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
