#!/usr/bin/env python3
"""04_screening.py — Handle custom screening questions.
Strategy:
1. Try to match from profile common_answers
2. Yes/No questions → answer "No" by default
3. Text questions → use safe defaults
4. Print all remaining for model intercept

No Gemini call.
"""
import json, os, sys, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

profile_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "profile.json")
with open(profile_path) as f:
    profile = json.load(f)
ca = profile.get("common_answers", {})

b, ctx = connect()
page = None
for p in ctx.pages:
    if 'jobs/' in p.url:
        page = p
        break
if not page:
    print("ERROR: no LinkedIn page found", file=sys.stderr); sys.exit(1)

# Get current fields
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

# Process ALL text-input fields (screening questions may have wrong defaults)
all_text_fields = [f for f in fields if f['type'] in ('text',) and f['tag'] == 'INPUT']
filled = 0
remaining = []

for f in all_text_fields:
    label_lower = f['label'].lower()
    val = None
    
    # 1. Try common_answers
    for ca_key, ca_val in ca.items():
        if ca_val and ca_key.replace('_', ' ') in label_lower:
            val = ca_val
            break
    
    if val is None:
        # 2. Years of experience questions
        years = ca.get('years_of_experience', '') or ca.get('total_years', '')
        if years and ('year' in label_lower or 'experience' in label_lower):
            val = years

    if val is None:
        # 3. Common label patterns
        for phrase, ca_key in {
            "years of experience": "years_of_experience",
            "years of work": "years_of_experience", 
            "how many years": "years_of_experience",
        }.items():
            if phrase in label_lower:
                v = ca.get(ca_key)
                if v: val = v; break
    
    if val and val != f['value']:
        # Override the wrong default
        sel = f"#{f['id']}" if f['id'] and not f['id'][0].isdigit() else f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]"
        if sel and sel != '#':
            try:
                el = page.query_selector(sel)
                if el:
                    el.fill(val)
                    filled += 1
                    print(f"  FILLED: '{f['label']}' -> '{val}'", file=sys.stderr)
                    continue
            except Exception as e:
                print(f"  FAILED: '{f['label']}' ({e})", file=sys.stderr)
    
    if not val:
        remaining.append(f)

# Also handle remaining unfilled required fields (selects with bad defaults)
unfilled = [f for f in fields if f['required'] and (not f['value'] or f['value'] == 'Select an option')]

state["screening_filled"] = filled
state["screening_remaining"] = [f"{f['label']} (options: {f['options'][:4]})" for f in remaining]
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"\nAuto-filled: {filled}/{len(unfilled)}", file=sys.stderr)
if remaining:
    print(f"Remaining ({len(remaining)}):", file=sys.stderr)
    for f in remaining:
        opts = f" options={f['options'][:3]}" if f.get('options') else ''
        print(f"  '{f['label']}' type={f['tag']}:{f['type']}{opts}", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/04_screening.py (rerun after model provides answers)", file=sys.stderr)
else:
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
