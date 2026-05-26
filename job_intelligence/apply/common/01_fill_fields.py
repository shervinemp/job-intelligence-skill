#!/usr/bin/env python3
"""01_fill_fields.py — Read and fill form fields. Model decides via --answer.
Usage: fill_fields.py <jid> [--answer "question"=value ...]
"""
import json, os, sys, re, time
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

argv = sys.argv[1:]
answers_override = {}
i = 0
while i < len(argv):
    if argv[i] == '--answers' and i + 1 < len(argv):
        try:
            answers_override.update(json.loads(argv[i+1]))
        except Exception as e:
            print(f"  --answers JSON parse error: {e}", file=sys.stderr)
        i += 2
    elif argv[i] == '--answer' and i + 2 < len(argv) and argv[i+2].startswith('='):
        # PowerShell splat: --answer "key" "=value"
        answers_override[argv[i+1]] = argv[i+2].lstrip('=')
        i += 3
    elif argv[i] == '--answer' and i + 1 < len(argv):
        # Unity: --answer="key=value" or --answer "key=value"
        a = argv[i+1]
        if '=' in a:
            k, v = a.split('=', 1)
            answers_override[k] = v
            i += 2
        else:
            i += 2
    else:
        i += 1

def save_answer(text, value):
    norm = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')[:80]
    words = [w for w in norm.split('_') if len(w) > 2]
    key = '_'.join(words[:4]) if len(words) >= 3 else norm
    ca = profile.setdefault("common_answers", {})
    if key not in ca or ca[key] != value:
        ca[key] = value
        with open(profile_path, "w") as f:
            json.dump(profile, f, indent=2)
        print(f"  SAVED: common_answers['{key}'] = '{value}'", file=sys.stderr)

def fuzzy_match(question_text, ca):
    """Find a matching answer in common_answers by word overlap or exact key match."""
    q_words = set(re.sub(r'[^a-z0-9]+', ' ', question_text.lower()).split())
    q_words -= {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'and', 'or', 'is', 'are', 'do', 'does', 'will', 'would', 'have', 'has', 'you', 'your'}
    if len(q_words) < 2:
        # Single significant word: try exact key containment
        q_lower = question_text.lower()
        for key in ca:
            if ca[key] and key.lower() in q_lower:
                return ca[key]
        return None
    best_key, best_overlap = None, 0
    for key in ca:
        if not ca[key]:
            continue
        k_words = set(re.sub(r'[^a-z0-9]+', ' ', key.lower()).split())
        overlap = len(q_words & k_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_key = key
    if best_overlap >= 2:
        return ca[best_key]
    return None
    best_key, best_overlap = None, 0
    for key in ca:
        if not ca[key]:
            continue
        k_words = set(re.sub(r'[^a-z0-9]+', ' ', key.lower()).split())
        overlap = len(q_words & k_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_key = key
    # Require at least 2 overlapping significant words
    if best_overlap >= 2:
        val = ca[best_key]
        return val
    return None

def click_option(page, opt, tag_type):
    if tag_type == 'radio_group':
        return page.evaluate("(opt) => { const rs = document.querySelectorAll('input[type=\"radio\"]'); for (const r of rs) { const lbl = document.querySelector('label[for=\"' + r.id + '\"]'); if (lbl && lbl.textContent.trim() === opt) { r.click(); return true; } } return false; }", opt)
    return page.evaluate("(opt) => { const secs = document.querySelectorAll('div[class*=\"question\"], fieldset, div[class*=\"field\"], li[class*=\"field\"]'); for (const sec of secs) { const btns = sec.querySelectorAll('button'); for (const b of btns) { if (b.textContent.trim() === opt) { b.click(); return true; } } } return false; }", opt)

b, ctx = connect()
ext_url = state.get("external_url", "")

# Clean up stale non-relevant pages before starting
for pg in ctx.pages:
    u = pg.url
    if u == 'about:blank' or u.startswith('chrome'):
        continue
    if '/jobs/view/' in u or (ext_url and (u in ext_url or ext_url in u)):
        continue
    try: pg.close()
    except: pass

page, navigated_fresh = find_apply_page(ctx, ext_url or None)

# Check for already-applied / login wall
plat = state.get("platform") or ""
text = page.evaluate("() => document.body.innerText")
if check_page(text, plat, ALREADY_APPLIED):
    print("STATUS: already_applied", file=sys.stderr); sys.exit(0)
if check_page(text, plat, LOGIN_WALL):
    guest = page.evaluate("""() => {""" + f"""
        const patterns = {json.dumps(list(set(GUEST_APPLY.get(plat, []) + GUEST_APPLY["default"])))};
    """ + """
        const all = document.querySelectorAll('button, a');
        for (const el of all) {
            const t = (el.textContent || '').toLowerCase();
            for (const p of patterns)
                if (t.includes(p) && el.offsetParent !== null) { el.click(); return true; }
        }
        return false;
    }""")
    if guest:
        print("  Guest apply clicked", file=sys.stderr); time.sleep(3)
    else:
        print("  Cannot bypass login wall", file=sys.stderr); sys.exit(0)

# Read all form fields in one pass: inputs + custom buttons
form = page.evaluate("""() => {
    const container = document.querySelector('[role="dialog"]') || document;
    const inputs = container.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
    const fields = Array.from(inputs).map(el => {
        const lbl = container.querySelector('label[for="'+el.id+'"]');
        const parent = el.closest('div, fieldset, section, li');
        const plbl = parent ? parent.querySelector('label, legend, strong, span:not([class*="hidden"])') : null;
        let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
        if (!label && plbl) label = plbl.textContent.trim();
        return {
            tag: el.tagName, type: el.getAttribute('type')||'',
            id: el.id, name: el.getAttribute('name')||'',
            label: label.replace(/\\s+/g, ' ').trim().slice(0, 80),
            required: el.required, value: el.value||'',
            options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0, 12) : [],
        };
    });
    // Custom button questions (Ashby-style)
    const qs = [];
    const sections = container.querySelectorAll('div[class*="question"], fieldset, div[class*="field"], li[class*="field"]');
    for (const sec of sections) {
        const label = sec.querySelector('label, legend, strong, p, [class*="label"], [class*="heading"], [class*="title"]');
        if (!label) continue;
        const btns = sec.querySelectorAll('button');
        if (btns.length < 2) continue;
        const qText = (label.textContent || '').trim();
        if (qText.length < 5) continue;
        const opts = Array.from(btns).map(b => (b.textContent || '').trim()).filter(Boolean);
        if (new Set(opts.map(o => o.toLowerCase())).isSubsetOf(new Set(['replace', 'upload file', 'upload']))) continue;
        const answered = Array.from(btns).some(b => {
            const cls = b.getAttribute('class') || '';
            return cls.includes('selected') || cls.includes('active') || b.getAttribute('aria-pressed') === 'true';
        });
        if (!answered) {
            qs.push({ tag: 'button_group', type: 'choice', id: '', name: '',
                      label: qText.slice(0, 120), required: true, value: '', options: opts });
        }
    }
    return fields.concat(qs);
}""")

# Find resume
resume_path = None
results_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "results", state["jid"])
if os.path.isdir(results_dir):
    for fn in os.listdir(results_dir):
        if "Resume" in fn and fn.endswith(".pdf"):
            resume_path = os.path.join(results_dir, fn); break

ca = profile.get("common_answers", {})
filled = 0
unfilled = []
file_uploaded = False
handled_radios = set()
first_name = profile.get("first_name", "")
last_name = profile.get("last_name", "")

for f in form:
    if f['value'] and f['value'] != 'Select an option':
        continue

    # File: upload once
    if f['tag'] == 'INPUT' and f['type'] == 'file':
        if not file_uploaded and resume_path:
            try:
                el = page.query_selector("input[type=\"file\"]")
                if el:
                    el.set_input_files(resume_path)
                    file_uploaded = True
                    filled += 1
                    print(f"  RESUME: uploaded {os.path.basename(resume_path)}", file=sys.stderr)
            except Exception as e:
                print(f"  RESUME FAILED: {e}", file=sys.stderr)
        continue

    # Radio groups: dedup by name
    if f['type'] == 'radio':
        if f['name'] in handled_radios: continue
        handled_radios.add(f['name'])
        radios = [rf for rf in form if rf.get('name') == f['name']]
        opts = [rf['label'] for rf in radios]
        q_label = opts[0].split(' - ')[0] if ' - ' in opts[0] else opts[0]
        # Check answers
        ans = None
        for k, v in answers_override.items():
            if k in q_label or q_label in k:
                ans = v; break
        if ans is None:
            ans = fuzzy_match(q_label, ca)
        if ans:
            for opt in opts:
                if ans.lower() in opt.lower() or opt.lower() in ans.lower():
                    if click_option(page, opt, 'radio_group'):
                        filled += 1; print(f"  ANSWERED: '{q_label[:40]}' -> '{ans}'", file=sys.stderr)
                        save_answer(q_label, ans)
                    break
            continue
        unfilled.append({"label": q_label[:60], "options": opts, "required": True})
        continue

    # Check answers_override for any label
    ans = None
    lbl = f.get('label', '')
    # Substring match in answers_override
    for k, v in answers_override.items():
        if k in lbl or lbl in k:
            ans = v
            break
    if ans is None:
        ans = fuzzy_match(lbl, ca)

    if ans:
        # Apply to text/select/button
        if f['tag'] in ('INPUT', 'TEXTAREA', 'SELECT'):
            sel = f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]" if f['name'] else None
            if sel:
                try:
                    el = page.query_selector(sel)
                    if el:
                        if f['tag'] == 'SELECT':
                            for opt in f['options']:
                                if ans.lower() in opt.lower(): el.select_option(opt); break
                            else: el.select_option(ans)
                        else: el.fill(ans)
                        filled += 1; print(f"  FILLED: '{lbl[:40]}' -> '{ans}'", file=sys.stderr)
                        save_answer(lbl, ans)
                except Exception as e:
                    print(f"  FAILED: '{lbl[:40]}' ({e})", file=sys.stderr)
                    unfilled.append(f)
        elif f['tag'] == 'button_group' and f.get('options'):
            for opt in f['options']:
                if ans.lower() in opt.lower() or opt.lower() in ans.lower():
                    if click_option(page, opt, 'button_group'):
                        filled += 1; print(f"  ANSWERED: '{lbl[:40]}' -> '{ans}'", file=sys.stderr)
                        save_answer(lbl, ans)
                    break
        continue

    # Name: deterministic from profile
    if f['tag'] in ('INPUT', 'TEXTAREA') and f['type'] in ('text', '', 'email', 'tel'):
        nlbl = re.sub(r'[^a-z0-9]+', ' ', lbl.lower()).strip()
        if nlbl == 'full name' and first_name and last_name:
            fn = f"{first_name} {last_name}"
            sel = f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]"
            if sel:
                try:
                    el = page.query_selector(sel)
                    if el: el.fill(fn); filled += 1; print(f"  FILLED: '{lbl}' -> '{fn}'", file=sys.stderr)
                except: pass
            continue

    if f.get('required', True):
        unfilled.append({"label": lbl[:60], "options": f.get('options', []), "required": True})

# Detect buttons for navigation decision
buttons = page.evaluate("""() => {
    const container = document.querySelector('[role="dialog"]') || document;
    return Array.from(container.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => ({
        text: (b.textContent||'').trim().slice(0, 30), disabled: b.disabled
    }));
}""")
has_next = any(b['text'].lower() in ('next', 'review', 'continue') and not b['disabled'] for b in buttons)
has_submit = any('submit' in b['text'].lower() or 'send' in b['text'].lower() for b in buttons)

state["filled"] = filled
state["unfilled"] = [f['label'] for f in unfilled]
state["buttons"] = buttons[:8]
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"\nFilled: {filled}  Unfilled: {len(unfilled)}", file=sys.stderr)
if unfilled:
    for f in unfilled:
        opts = f" options={f['options'][:3]}" if f.get('options') else ''
        print(f"  '{f['label']}'{opts}", file=sys.stderr)
if has_submit and not unfilled:
    print("NEXT: submit (dry-run)", file=sys.stderr)
elif has_next:
    print("NEXT: next (form has more pages)", file=sys.stderr)
elif unfilled:
    print("NEXT: --answer for unfilled fields", file=sys.stderr)
else:
    print("NEXT: submit (dry-run)", file=sys.stderr)
