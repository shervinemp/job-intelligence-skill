#!/usr/bin/env python3
"""04_screening.py — Present screening questions to the model for decisions.
DO NOT auto-fill any defaults. Present each question with all options.
The model reads the output and decides what to answer.

Output format per question:
  [TYPE] "Question text"
    Options: [opt1, opt2, ...]
    Current: "current_value"

Model should rerun with --answers '{"label":"value"}' or fill manually.
"""
import json, os, sys, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

# Parse answers if provided
answers = {}
if "--answers" in sys.argv:
    idx = sys.argv.index("--answers")
    if idx + 1 < len(sys.argv):
        try: 
            answers = json.loads(sys.argv[idx + 1])
            print(f"Parsed answers: {answers}", file=sys.stderr)
        except json.JSONDecodeError as e:
            print(f"Answers parse failed: {e}", file=sys.stderr)

b, ctx = connect()
page = None
for p in ctx.pages:
    if 'jobs/' in p.url:
        page = p; break
if not page:
    print("ERROR: no LinkedIn page", file=sys.stderr); sys.exit(1)

fields = page.evaluate("""() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) return [];
    const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    return Array.from(inputs).map(el => {
        const lbl = d.querySelector('label[for="'+el.id+'"]');
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
            options: opts.slice(0, 20),
        };
    });
}""")

# Identify screening questions: text inputs with long labels, radio groups, selects
screening_fields = []
handled_radios = set()
for f in fields:
    if f['type'] == 'radio':
        if f['name'] and f['name'] not in handled_radios:
            handled_radios.add(f['name'])
            group = [rf for rf in fields if rf['name'] == f['name']]
            # Find the question from the parent element's text
            first = group[0]
            parent_label = ""
            sel = f"#{first['id']}" if first['id'] and not first['id'][0].isdigit() else f"[id=\"{first['id']}\"]" if first['id'] else None
            if sel:
                try:
                    el = page.query_selector(sel)
                    if el:
                        parent_label = el.evaluate("""el => {
                            let e = el;
                            for (let i = 0; i < 4; i++) {
                                e = e.parentElement;
                                if (!e) break;
                                const heading = e.querySelector('label, legend, span, strong, [role="heading"]');
                                if (heading) {
                                    const t = heading.textContent.trim();
                                    if (t.length > 3 && !t.includes('Yes') && !t.includes('No')) return t;
                                }
                            }
                            return '';
                        }""")
                except: pass
            label = parent_label or "Selection"
            screening_fields.append({
                "tag": "radio_group",
                "label": label,
                "required": any(rf['required'] for rf in group),
                "value": "",
                "options": [rf['label'] for rf in group if rf['label']],
                "radio_name": group[0]['name'] if group else "",
            })
        continue
    if f['type'] == 'checkbox':
        continue
    if f['tag'] == 'TEXTAREA':
        continue
    if f['tag'] == 'INPUT' and f['type'] == 'file':
        continue
    # Text inputs: include even if filled (wrong defaults)
    if f['tag'] == 'INPUT' and f['type'] in ('text', 'number'):
        screening_fields.append(f)
        continue
    # Selects: include if required or has meaningful options
    if f['tag'] == 'SELECT' and f['options']:
        screening_fields.append(f)
        continue

if not screening_fields:
    print("No screening questions detected", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
    sys.exit(0)

# Fill from answers if provided
filled = 0
for f in screening_fields:
    label = f['label']
    if label in answers:
        val = answers[label]
        print(f"MATCH: '{label}' -> '{val}'", file=sys.stderr)
        if f.get('tag') == 'radio_group':
            name = f.get('radio_name', '')
            if name:
                page.evaluate(f"""(val) => {{
                    const d = document.querySelector('[role="dialog"]');
                    const radios = d.querySelectorAll('input[type="radio"][name="{name}"]');
                    for (const r of radios) {{
                        const lbl = d.querySelector('label[for="'+r.id+'"]');
                        const t = (lbl?lbl.textContent.trim():'').toLowerCase();
                        if (t === val.toLowerCase()) {{
                            r.click(); return;
                        }}
                    }}
                }}""", val)
            filled += 1
            print(f"  FILLED: '{label}' -> '{val}'", file=sys.stderr)
            continue
        sel = f"#{f['id']}" if f['id'] and not f['id'][0].isdigit() else f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]"
        if not sel or sel == '#': continue
        try:
            el = page.query_selector(sel)
            if el:
                if f['tag'] == 'SELECT':
                    el.select_option(val)
                elif f['type'] == 'radio':
                    page.evaluate(f"""() => {{
                        const d = document.querySelector('[role="dialog"]');
                        const radios = d.querySelectorAll('input[type="radio"]');
                        for (const r of radios) {{
                            const lbl = d.querySelector('label[for="'+r.id+'"]');
                            const t = (lbl?lbl.textContent.trim():'').toLowerCase();
                            if (r.name === '{f['name']}' && t === '{val.lower()}') {{
                                r.click(); return;
                            }}
                        }}
                    }}""")
                else:
                    el.fill(val)
                filled += 1
                print(f"  FILLED: '{label}' -> '{val[:30]}'", file=sys.stderr)
        except Exception as e:
            print(f"  FAILED: '{label}' ({e})", file=sys.stderr)

# Present remaining unanswered questions
remaining = [f for f in screening_fields if f['label'] not in answers]
if remaining:
    print(f"\nScreening questions ({len(remaining)}):", file=sys.stderr)
    for f in remaining:
        tag = f.get('tag', f['tag'])
        opts = f"  Options: {f['options']}" if f.get('options') else ""
        print(f"  [{tag}] '{f['label']}'", file=sys.stderr)
        if opts: print(opts, file=sys.stderr)
    print(f"\nNEXT: apply.py screen <jid> --answers '{{\"label\":\"value\"}}'", file=sys.stderr)
else:
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
