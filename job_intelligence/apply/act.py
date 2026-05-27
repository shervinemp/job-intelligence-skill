#!/usr/bin/env python3
"""act.py — One action per call: fill, next, back, submit, auto.
Always reads fresh state, verifies before/after, prints structured output.
"""
import json, os, sys, re, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
profile_path = os.path.join(os.path.dirname(__file__), "..", "profile.json")

_FORWARD_BUTTONS = {"submit", "review", "next", "continue", "done", "submit application", "send application"}
_EXCLUDED_BUTTONS = {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse"}

def _load_state():
    with open(STATE_PATH) as f:
        return json.load(f)

def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

def _read_page(p):
    """Read dialog (if LinkedIn modal) or document (if external ATS)."""
    return p.evaluate("""() => {
        const container = document.querySelector('[role="dialog"]') || document;
        const inputs = container.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        const btns = container.querySelectorAll('button');
        const fields = Array.from(inputs).map(el => {
            const lbl = container.querySelector('label[for="' + el.id + '"]');
            const parent = el.closest('div,fieldset,section,li');
            const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
            let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
            if (!label && plbl) label = plbl.textContent.trim();
            return {
                tag: el.tagName, type: el.getAttribute('type') || '',
                id: el.id, name: el.getAttribute('name') || '',
                label: label.replace(/\\s+/g,' ').trim().slice(0, 80),
                required: el.required, value: el.value || '',
                checked: el.type === 'radio' ? el.checked : null,
                options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0,15) : [],
            };
        });
        return {
            fieldCount: fields.length, fields: fields.slice(0,35),
            hasFileInput: container.querySelectorAll('input[type="file"]').length > 0,
            hasRequiredFile: container.querySelectorAll('input[type="file"][required]').length > 0,
            buttons: Array.from(btns).filter(b => b.offsetParent !== null).map(b => ({
                text: (b.textContent || '').trim().slice(0,30), disabled: b.disabled
            })),
        };
    }""")

def _find_page(ctx, state):
    """Find the page by external_url or LinkedIn jobs URL."""
    ext = state.get("external_url", "")
    for p in ctx.pages:
        url = p.url
        if ext and (url in ext or ext in url):
            return p
        if "linkedin.com/jobs/view" in url:
            return p
    return None

def _read_state_after(p):
    """Read + save page state. Returns page dict."""
    ps = _read_page(p)
    state = _load_state()
    state["page"] = ps
    _save_state(state)
    return ps

def _resolve(label, profile, ca):
    """Deterministic profile field resolution. Returns value or None."""
    norm = re.sub(r'[^a-z0-9]+', ' ', label.lower()).strip()
    ca_lookup = {
        "phone": "phone", "phone number": "phone", "mobile": "phone",
        "linkedin": "linkedin_url", "linkedin profile": "linkedin_url", "linkedin profile link": "linkedin_url",
        "github": "github_url",
        "portfolio": "portfolio_url", "website": "portfolio_url",
    }
    if norm == "full name":
        fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
        return f"{fn} {ln}" if fn and ln else fn or ln or None
    if norm in ("first name", "firstname"): return profile.get("first_name")
    if norm in ("last name", "lastname"): return profile.get("last_name")
    if norm in ("email", "email address"): return profile.get("email")
    key = ca_lookup.get(norm)
    if key: return ca.get(key)
    return None

def cmd_fill(jid, answers_json=None):
    """Fill ALL fields on current page. Upload resume. Report result."""
    answers = {}
    if answers_json:
        try: answers = json.loads(answers_json)
        except: print("ERROR: --answers must be valid JSON", file=sys.stderr)

    state = _load_state()
    b, ctx = connect()
    page = _find_page(ctx, state)
    if not page:
        # Navigate fresh
        ext = state.get("external_url", "")
        if ext:
            page = ctx.new_page()
            page.goto(ext, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            page.evaluate("() => window.__applyPage = true")
        else:
            print("ERROR: no page found and no external URL to navigate to", file=sys.stderr)
            sys.exit(1)

    ps = _read_page(page)
    profile = {}
    if os.path.exists(profile_path):
        with open(profile_path) as f:
            profile = json.load(f)
    ca = profile.get("common_answers", {})

    filled = 0
    unfilled = []
    file_uploaded = False
    handled_radios = set()

    for f in ps["fields"]:
        if f["type"] == "radio":
            if f["name"] in handled_radios: continue
            handled_radios.add(f["name"])
            radios = [rf for rf in ps["fields"] if rf.get("name") == f["name"]]
            opts = [rf["label"] for rf in radios]
            q_label = opts[0].split(" - ")[0] if " - " in opts[0] else opts[0]
            # Check --answers
            ans = None
            for k, v in answers.items():
                if k in q_label or q_label in k:
                    ans = v
                    break
            if not ans:
                # Check common_answers by word overlap
                q_words = set(re.sub(r'[^a-z0-9]+', ' ', q_label.lower()).split())
                stop = {"the","a","an","in","on","at","to","for","of","with","and","or","is","are","do","does","will","would","have","has","you","your"}
                q_words -= stop
                best = None
                for ck, cv in ca.items():
                    if not cv: continue
                    kw = set(re.sub(r'[^a-z0-9]+', ' ', ck.lower()).split()) - stop
                    if len(q_words & kw) >= 2:
                        best = cv; break
                ans = best
            if ans:
                for opt in opts:
                    if ans.lower() in opt.lower() or opt.lower() in ans.lower():
                        # Find the radio in DOM and click
                        for rf in radios:
                            if rf["label"] == opt:
                                sel = f'[id="{rf["id"]}"]' if rf["id"] else f'[name="{rf["name"]}"]'
                                if sel:
                                    try:
                                        el = page.query_selector(sel)
                                        if el and not el.is_checked():
                                            el.check()
                                            filled += 1
                                    except: pass
                                break
                continue
            unfilled.append({"label": q_label[:60], "options": opts, "tag": "radio_group"})
            continue

        if f["type"] == "checkbox":
            # Unfollow company checkbox
            if f["required"]:
                unfilled.append({"label": f["label"][:60], "tag": "checkbox"})
            continue

        if f["tag"] == "INPUT" and f["type"] == "file":
            if file_uploaded: continue
            if f.get("required", False):
                # Upload resume to required file inputs
                results_dir = os.path.expanduser(f"~/.openclaw/results/{jid}")
                resume_path = None
                if os.path.isdir(results_dir):
                    for fn in os.listdir(results_dir):
                        if "Resume" in fn and fn.endswith(".pdf"):
                            resume_path = os.path.join(results_dir, fn)
                            break
                if resume_path:
                    try:
                        fi = page.query_selector('input[type="file"][required]') or page.query_selector('input[type="file"]')
                        if fi:
                            fi.set_input_files(resume_path)
                            file_uploaded = True
                            filled += 1
                    except Exception as e:
                        print(f"  RESUME FAILED: {e}", file=sys.stderr)
            continue

        # Check --answers
        ans = None
        lbl = f["label"]
        for k, v in answers.items():
            if k in lbl or lbl in k:
                ans = v
                break
        if not ans:
            # Check common_answers by containment
            q_lower = lbl.lower()
            for ck, cv in ca.items():
                if cv and ck.lower() in q_lower:
                    ans = cv
                    break
        if not ans:
            # Check profile fields
            ans = _resolve(lbl, profile, ca)

        if ans:
            sel = f'[id="{f["id"]}"]' if f["id"] else f'[name="{f["name"]}"]'
            if sel and sel != "#":
                try:
                    el = page.query_selector(sel)
                    if el:
                        if f["tag"] == "SELECT":
                            for opt in f["options"]:
                                if ans.lower() in opt.lower():
                                    el.select_option(opt); break
                            else:
                                el.select_option(ans)
                        elif f["tag"] in ("INPUT", "TEXTAREA"):
                            el.fill(ans)
                        filled += 1
                except: pass
        elif f.get("required") or (f["type"] == "radio" and not any(rf.get("checked") for rf in ps["fields"] if rf.get("name") == f["name"])):
            unfilled.append({"label": lbl[:60], "options": f.get("options",[]), "tag": f["tag"]})

    # Unfollow company checkbox (always uncheck if present)
    page.evaluate("""() => {
        const container = document.querySelector('[role="dialog"]') || document;
        const cbs = container.querySelectorAll('input[type="checkbox"]');
        for (const cb of cbs) {
            const lbl = container.querySelector('label[for="' + cb.id + '"]');
            if (lbl && (lbl.textContent || '').includes('Follow') && cb.checked) {
                cb.checked = false;
                cb.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }
    }""")

    # Re-read page state
    ps2 = _read_page(page)
    state["page"] = ps2
    _save_state(state)

    print(f"FILLED: {filled}  UNFILLED: {len(unfilled)}", file=sys.stderr)
    for f in unfilled:
        opts = f" options={f['options'][:3]}" if f.get('options') else ''
        print(f"  {f['label']}{opts}", file=sys.stderr)

    # Determine next action
    btns = ps2.get("buttons", [])
    has_submit = any(b["text"].lower() in ("submit", "submit application", "send application") and not b["disabled"] for b in btns)
    has_next = any(b["text"].lower() in ("next", "review", "continue", "done") and not b["disabled"] for b in btns)

    if unfilled:
        print("NEXT: act --fill --answers '{\"<question>\": \"<answer>\"}'", file=sys.stderr)
    elif has_submit:
        print("NEXT: act --submit", file=sys.stderr)
    elif has_next:
        print("NEXT: act --next", file=sys.stderr)
    else:
        print("NEXT: act --next", file=sys.stderr)


def cmd_next(jid):
    """Click forward button (Submit > Review > Next > Continue > Done). Verify result."""
    state = _load_state()
    b, ctx = connect()
    page = _find_page(ctx, state)
    if not page:
        print("ERROR: no relevant page found", file=sys.stderr); sys.exit(1)

    ps = _read_page(page)
    btns = ps.get("buttons", [])

    # Find forward button
    target = None
    for b in btns:
        t = b["text"].lower()
        if t in _FORWARD_BUTTONS and not b["disabled"] and t not in _EXCLUDED_BUTTONS:
            target = b; break
    if not target:
        # Check if button is disabled
        for b in btns:
            t = b["text"].lower()
            if t in _FORWARD_BUTTONS and t not in _EXCLUDED_BUTTONS:
                print(f"BUTTON_DISABLED: {b['text']} is disabled — fill required fields first")
                print("NEXT: act --fill")
                sys.exit(0)
        print("NO_BUTTON: no forward button found")
        print("NEXT: none")
        sys.exit(0)

    print(f"ACTION: {target['text']}", file=sys.stderr)

    # Click via Playwright native click (reliable)
    overlay = page.evaluate("() => { const o = document.getElementById('interop-outlet'); if(o) o.style.pointerEvents='none'; return !!o; }")
    try:
        btn = page.locator(f'button:has-text("{target["text"]}")')
        if btn.count():
            btn.first.click(timeout=5000)
        else:
            page.evaluate(f"""() => {{
                const container = document.querySelector('[role="dialog"]') || document;
                const btns = container.querySelectorAll('button');
                for (const b of btns) {{
                    const t = (b.textContent||'').trim().toLowerCase();
                    if ('{target["text"].lower()}' === t || t.includes('{target["text"].lower()}')) {{ b.click(); return; }}
                }}
            }}""")
    except: pass
    time.sleep(4)

    # Check result
    ps2 = _read_page(page)
    if not ps2 or ps2["fieldCount"] == 0:
        # Modal closed — possibly submitted
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        for w in ["thank you", "submitted", "your application", "has been sent"]:
            if w in text:
                print("STATUS: submitted", file=sys.stderr)
                print("NEXT: verify", file=sys.stderr)
                sys.exit(0)
        print("STATUS: modal_closed", file=sys.stderr)
        print("NEXT: verify", file=sys.stderr)
        sys.exit(0)

    print(f"PAGE: {json.dumps(ps2)}", file=sys.stderr)

    # Determine next action
    has_submit = any(b["text"].lower() in ("submit", "submit application") and not b["disabled"] for b in ps2.get("buttons",[]))
    has_next = any(b["text"].lower() in ("next", "review", "continue") and not b["disabled"] for b in ps2.get("buttons",[]))

    if has_submit:
        print("NEXT: act --fill (review page)", file=sys.stderr)
    elif has_next:
        print("NEXT: act --fill (next page)", file=sys.stderr)
    else:
        print("NEXT: act --fill", file=sys.stderr)


def cmd_back(jid):
    """Click Back button."""
    state = _load_state()
    b, ctx = connect()
    page = _find_page(ctx, state)
    if not page:
        print("ERROR: no relevant page found", file=sys.stderr); sys.exit(1)

    page.evaluate("""() => {
        const container = document.querySelector('[role="dialog"]') || document;
        const btns = container.querySelectorAll('button');
        for (const b of btns) {
            const t = (b.textContent||'').trim().toLowerCase();
            if (t === 'back' && !b.disabled) { b.click(); return; }
        }
    }""")
    time.sleep(3)
    ps = _read_page(page)
    state["page"] = ps
    _save_state(state)
    print("ACTION: Back", file=sys.stderr)
    print(f"PAGE: {json.dumps(ps)}", file=sys.stderr)
    print("NEXT: act --fill", file=sys.stderr)


def cmd_submit(jid, confirm=False):
    """Click Submit on review page. Dry-run without --confirm."""
    state = _load_state()
    b, ctx = connect()
    page = _find_page(ctx, state)
    if not page:
        print("ERROR: no relevant page found", file=sys.stderr); sys.exit(1)

    ps = _read_page(page)
    btns = ps.get("buttons", [])
    submit_btn = [b for b in btns if b["text"].lower() in ("submit", "submit application", "send application")]

    if not submit_btn:
        print("NO_SUBMIT_BUTTON", file=sys.stderr)
        print("NEXT: none", file=sys.stderr)
        sys.exit(0)

    btn = submit_btn[0]
    print(f"SUBMIT: {btn['text']}", file=sys.stderr)
    print(f"DISABLED: {btn['disabled']}", file=sys.stderr)

    if not confirm:
        print("DRY_RUN: pass --confirm to submit", file=sys.stderr)
        print("NEXT: act --submit --confirm", file=sys.stderr)
        sys.exit(0)

    # Click Submit
    try:
        b_loc = page.locator(f'button:has-text("{btn["text"]}")')
        b_loc.first.click(timeout=5000)
    except:
        page.evaluate("""() => {
            const container = document.querySelector('[role="dialog"]') || document;
            const btns = container.querySelectorAll('button');
            for (const b of btns) {
                const t = (b.textContent||'').trim().toLowerCase();
                if (t.includes('submit')) { b.click(); return; }
            }
        }""")
    time.sleep(5)

    # Verify
    dlg = page.evaluate("() => document.querySelector('[role=\"dialog\"]')")
    if not dlg:
        print("STATUS: submitted", file=sys.stderr)
        # Update DB
        c = get_conn()
        c.execute("UPDATE jobs SET stage=? WHERE id=?", ("applied", jid))
        c.commit()
    else:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        for w in ["thank you", "submitted", "your application", "has been sent"]:
            if w in text:
                print("STATUS: submitted", file=sys.stderr)
                break
        else:
            print("STATUS: unknown (modal still open)", file=sys.stderr)
    print("NEXT: verify", file=sys.stderr)


def cmd_auto(jid, answers_json=None):
    """Full auto loop: fill → next → fill → ... → submit. Stops on unfilled fields."""
    answers = {}
    if answers_json:
        try: answers = json.loads(answers_json)
        except: pass

    max_pages = 10
    for page_num in range(1, max_pages + 1):
        cmd_fill(jid, json.dumps(answers) if answers else None)
        state = _load_state()
        ps = state.get("page", {})
        unfilled = [f for f in ps.get("fields", []) if f.get("required") and not f.get("value")]
        if unfilled:
            print(f"AUTO: page {page_num} — {len(unfilled)} unfilled", file=sys.stderr)
            print(f"AUTO: STOP — provide --answers for remaining fields", file=sys.stderr)
            return
        print(f"AUTO: page {page_num} — filled, advancing", file=sys.stderr)
        cmd_next(jid)
        # Check if submitted
        import re as re2
        if re2.match(r"STATUS: submitted|STATUS: modal_closed", open(STATE_PATH).read()):
            print(f"AUTO: submitted", file=sys.stderr)
            return
    print(f"AUTO: reached max pages ({max_pages}) without submit", file=sys.stderr)


def run(args):
    if args.fill:
        cmd_fill(args.jid, args.answers)
    elif args.next:
        cmd_next(args.jid)
    elif args.back:
        cmd_back(args.jid)
    elif args.submit:
        cmd_submit(args.jid, args.confirm)
    elif args.auto:
        cmd_auto(args.jid, args.answers)
    else:
        print("ERROR: specify --fill, --next, --back, --submit, or --auto", file=sys.stderr)
