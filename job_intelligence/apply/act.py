#!/usr/bin/env python3
"""act.py — One action per call: fill, next, back, submit, auto.
Always reads fresh state, verifies before/after, prints structured output.
"""
import json, os, sys, re, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.page_helpers import load_state, save_state, read_page, find_page, resolve_label

profile_path = os.path.join(os.path.dirname(__file__), "..", "profile.json")
_EXCLUDED_BUTTONS = {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse"}

def _fill_radios(page, fields, answers, ca, jid):
    """Fill radio groups. Returns filled count + unfilled list."""
    filled = 0
    unfilled = []
    handled = set()
    for f in fields:
        if f["type"] != "radio" or f["name"] in handled: continue
        handled.add(f["name"])
        radios = [rf for rf in fields if rf.get("name") == f["name"]]
        opts = [rf["label"] for rf in radios]
        q_label = opts[0].split(" - ")[0] if " - " in opts[0] else opts[0]
        q_norm = re.sub(r'[^a-z0-9]+', ' ', q_label.lower()).strip()

        ans = None
        for k, v in answers.items():
            k_norm = re.sub(r'[^a-z0-9]+', ' ', k.lower()).strip()
            if k_norm == q_norm or q_norm.startswith(k_norm):
                ans = v; break
        if not ans:
            for ck, cv in ca.items():
                if cv and ck.lower().replace('_', ' ') in q_norm:
                    ans = cv; break
        if ans:
            for opt in opts:
                if ans.lower() in opt.lower():
                    for rf in radios:
                        if rf["label"] == opt and rf["id"]:
                            try:
                                el = page.query_selector(f'[id="{rf["id"]}"]')
                                if el and not el.is_checked():
                                    el.check(); filled += 1
                            except: pass
                            break
                        elif rf["label"] == opt and rf["name"]:
                            try:
                                el = page.query_selector(f'[name="{rf["name"]}"][value="on"]')
                                if el and not el.is_checked():
                                    el.check(); filled += 1
                            except: pass
                            break
            continue
        unfilled.append({"label": q_label[:60], "options": opts, "tag": "radio_group"})
    return filled, unfilled

def _fill_text(page, fields, answers, ca, profile, jid, state):
    """Fill text/select/textarea fields. Returns filled count + unfilled list."""
    filled = 0
    unfilled = []
    file_uploaded = False

    for f in fields:
        if f["type"] == "radio": continue
        if f["tag"] == "INPUT" and f["type"] == "file":
            if file_uploaded or not f.get("required", False): continue
            results_dir = os.path.expanduser(f"~/.openclaw/results/{jid}")
            if os.path.isdir(results_dir):
                # Find resume: prefer one matching job title or company
                candidates = []
                for fn in os.listdir(results_dir):
                    if "Resume" in fn and fn.endswith(".pdf"):
                        score = 0
                        if state.get("title","").split(" ")[0].lower() in fn.lower(): score += 2
                        if state.get("company","").lower() in fn.lower(): score += 1
                        candidates.append((score, fn))
                candidates.sort(key=lambda x: -x[0])
                if candidates:
                    try:
                        fi = page.query_selector('input[type="file"][required]') or page.query_selector('input[type="file"]')
                        if fi: fi.set_input_files(os.path.join(results_dir, candidates[0][1])); file_uploaded = True; filled += 1
                    except: pass
            continue

        lbl = f["label"]
        lbl_norm = re.sub(r'[^a-z0-9]+', ' ', lbl.lower()).strip()

        ans = None
        for k, v in answers.items():
            k_norm = re.sub(r'[^a-z0-9]+', ' ', k.lower()).strip()
            if k_norm == lbl_norm or lbl_norm.startswith(k_norm):
                ans = v; break
        if not ans:
            for ck, cv in ca.items():
                if cv and ck.lower().replace('_', ' ') in lbl_norm:
                    ans = cv; break
        if not ans:
            ans = resolve_label(lbl, profile)

        if ans:
            sel = f'[id="{f["id"]}"]' if f["id"] else f'[name="{f["name"]}"]'
            if sel and sel != "#":
                try:
                    el = page.query_selector(sel)
                    if el:
                        if f["tag"] == "SELECT":
                            for opt in f["options"]:
                                if ans.lower() in opt.lower(): el.select_option(opt); break
                            else: el.select_option(ans)
                        elif f["tag"] in ("INPUT", "TEXTAREA"): el.fill(ans)
                        filled += 1
                except: pass
        elif f.get("required"):
            unfilled.append({"label": lbl[:60], "options": f.get("options", []), "tag": f["tag"]})
    return filled, unfilled

def cmd_fill(jid, answers_json=None):
    answers = {}
    if answers_json:
        try: answers = json.loads(answers_json)
        except: print("ERROR: --answers must be valid JSON", file=sys.stderr)

    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    page = find_page(ctx, state)
    if not page:
        ext = state.get("external_url", "")
        if ext:
            page = ctx.new_page()
            page.goto(ext, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
        else:
            print("ERROR: no page found and no external URL", file=sys.stderr); sys.exit(1)
    ps = read_page(page)
    # Guard: if this page was already filled, warn but proceed
    last_fingerprint = state.get("page_fingerprint", "")
    current_fingerprint = str(len(ps["fields"])) + ":" + str(len(ps.get("buttons", [])))
    if current_fingerprint == last_fingerprint and state.get("filled", 0) > 0:
        print("WARN: page looks unchanged from last fill — verify the form advanced", file=sys.stderr)
    state["page_fingerprint"] = current_fingerprint

    # Check for login wall — try guest apply first, then abort
    text = page.evaluate("() => document.body.innerText") or ""
    from apply.common.platforms import check_page, LOGIN_WALL, GUEST_APPLY
    plat = state.get("platform", "")
    if check_page(text, plat, LOGIN_WALL):
        # Try guest apply buttons
        guest_patterns = GUEST_APPLY.get(plat, []) + GUEST_APPLY["default"]
        guest_clicked = False
        for gp in guest_patterns:
            btn = page.evaluate(f"""(gp) => {{
                const all = document.querySelectorAll('button, a, span, div');
                for (const el of all) {{
                    if (el.offsetParent === null) continue;
                    const t = (el.textContent || '').trim().toLowerCase();
                    if (t === gp || t.startsWith(gp)) {{
                        if (el.tagName === 'A') return {{tag: 'A', href: el.href}};
                        return {{tag: el.tagName}};
                    }}
                }}
                return null;
            }}""", gp)
            if btn:
                if btn.get("tag") == "A" and btn.get("href"):
                    page.goto(btn["href"], wait_until="domcontentloaded", timeout=15000)
                else:
                    page.evaluate(f"""(gp) => {{
                        const all = document.querySelectorAll('button');
                        for (const el of all) {{
                            if (el.offsetParent === null) continue;
                            if ((el.textContent || '').trim().toLowerCase() === gp) {{
                                el.click(); return;
                            }}
                        }}
                    }}""", gp)
                time.sleep(5)
                ps = read_page(page)
                guest_clicked = True
                print(f"GUEST_APPLY: clicked '{gp}'", file=sys.stderr)
                break
        if not guest_clicked:
            print("LOGIN_WALL: sign-in required, no guest apply option found", file=sys.stderr)
            return

    # If no fields detected but page has form hints, try to find and click apply link
    if ps["fieldCount"] == 0:
        if ps.get("pageType") == "maybe_form" or ps.get("pageType") == "unknown":
            # Search for apply link/button and follow it
            apply_link = page.evaluate("""() => {
                const all = document.querySelectorAll('a, button');
                for (const el of all) {
                    if (el.offsetParent === null) continue;
                    const t = (el.textContent || '').trim().toLowerCase();
                    const h = (el.href || '').toLowerCase();
                    if (t.includes('apply') || h.includes('/apply')) {
                        if (el.tagName === 'A') return {tag: 'A', href: el.href, text: t.slice(0,30)};
                        return {tag: el.tagName, text: t.slice(0,30)};
                    }
                }
                return null;
            }""")
            if apply_link:
                if apply_link["tag"] == "A":
                    page.goto(apply_link["href"], wait_until="domcontentloaded", timeout=15000)
                else:
                    page.evaluate("""() => {
                        const btns = document.querySelectorAll('button');
                        for (const b of btns) {
                            if (b.offsetParent === null) continue;
                            if ((b.textContent || '').trim().toLowerCase().includes('apply')) {
                                b.click(); return;
                            }
                        }
                    }""")
                time.sleep(5)
                ps = read_page(page)
                print(f"APPLY_LINK: followed '{apply_link['text']}' → {ps['fieldCount']} fields", file=sys.stderr)
            elif ps.get("pageType") == "maybe_form":
                print("WARN: no form fields found (likely custom UI or shadow DOM)", file=sys.stderr)
                print("PAGE: form-like content detected but no standard inputs nor apply link", file=sys.stderr)
                state["page"] = ps
                state["filled"] = 0
                save_state(state)
                return
            else:
                print("WARN: no form fields found — page may not be an application form", file=sys.stderr)
                return

    profile = {}
    if os.path.exists(profile_path):
        with open(profile_path) as f: profile = json.load(f)
    ca = profile.get("common_answers", {})

    radio_filled, radio_unfilled = _fill_radios(page, ps["fields"], answers, ca, jid)
    text_filled, text_unfilled = _fill_text(page, ps["fields"], answers, ca, profile, jid, state)
    filled = radio_filled + text_filled
    unfilled = radio_unfilled + text_unfilled

    # Unfollow company checkbox (always)
    page.evaluate("""() => {
        const c = document.querySelector('[role="dialog"]') || document;
        const cbs = c.querySelectorAll('input[type="checkbox"]');
        for (const cb of cbs) {
            const lbl = c.querySelector('label[for="' + cb.id + '"]');
            if (lbl) {
                const t = (lbl.textContent||'').toLowerCase();
                if (t.includes('follow') && t.includes('up to date')) {
                    cb.checked = false; cb.dispatchEvent(new Event('change', {bubbles:true}));
                }
            }
        }
    }""")

    from apply.common.page_helpers import read_and_save as rs
    state["filled"] = filled
    rs(page, state)

    print(f"FILLED: {filled}  UNFILLED: {len(unfilled)}", file=sys.stderr)
    for f in unfilled:
        opts = f" options={f['options'][:3]}" if f.get('options') else ''
        print(f"  {f['label']}{opts}", file=sys.stderr)

    btns = ps.get("buttons", [])
    has_submit = any(b["text"].lower() in ("submit", "submit application", "apply", "send application") and not b["disabled"] for b in btns)
    has_next = any(b["text"].lower() in ("next", "review", "continue", "done") and not b["disabled"] for b in btns)
    if unfilled:
        print("NEXT: act --fill --answers '{\"<question>\": \"<answer>\"}'", file=sys.stderr)
    elif has_submit:
        print("NEXT: act --submit", file=sys.stderr)
    else:
        print("NEXT: act --next", file=sys.stderr)

def cmd_next(jid):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    page = find_page(ctx, state)
    if not page:
        ext = state.get("external_url", "")
        if ext:
            page = ctx.new_page()
            page.goto(ext, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
        else:
            print("ERROR: no page found and no external URL", file=sys.stderr); return

    ps = read_page(page)
    btns = ps.get("buttons", [])

    # Find forward button: rightmost non-excluded inside dialog
    candidates = [b for b in btns if not b["disabled"] and b["text"].lower().strip() not in _EXCLUDED_BUTTONS]
    target = None
    for kw in ["review", "next", "continue", "done"]:  # No "submit" — use --submit for that
        for b in candidates:
            if b["text"].lower().strip() == kw:
                target = b; break
        if target: break
    if not target and candidates:
        target = candidates[-1]  # rightmost

        if not target:
            for b in btns:
                bt = b["text"].lower().strip()
                if bt in ("submit", "submit application") and b["disabled"]:
                    print(f"BUTTON_DISABLED: {b['text']} — fill required fields first", file=sys.stderr)
                    print("NEXT: act --fill", file=sys.stderr); return
                if bt in ("next", "review", "continue", "done") and b["disabled"]:
                    print(f"BUTTON_DISABLED: {b['text']} — fill required fields first", file=sys.stderr)
                    print("NEXT: act --fill", file=sys.stderr); return
            # Check if a dry-run submit was done (submit button exists but no forward button found)
            submit_exists = any(b["text"].lower().strip() in ("submit", "submit application") for b in btns)
            if submit_exists:
                print("NO_BUTTON — use act --submit (or --submit --confirm) for the submit button", file=sys.stderr)
            else:
                print("NO_BUTTON\nNEXT: none", file=sys.stderr); return

    print(f"ACTION: {target['text']}", file=sys.stderr)
    try:
        btn = page.locator(f'button:has-text("{target["text"]}")')
        if btn.count(): btn.first.click(timeout=5000)
    except: pass
    time.sleep(4)

    ps2 = read_page(page)
    if not ps2 or ps2["fieldCount"] == 0:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        for w in ["thank you", "submitted", "your application", "has been sent"]:
            if w in text:
                print("STATUS: submitted\nNEXT: verify", file=sys.stderr)
                state["result"] = "submitted"
                save_state(state)
                return
        print("STATUS: modal_closed\nNEXT: verify", file=sys.stderr)
        state["result"] = "modal_closed"
        save_state(state)
        return

    print(f"PAGE: {json.dumps(ps2)}", file=sys.stderr)
    has_submit = any(b["text"].lower() in ("submit", "submit application", "apply", "send application") and not b["disabled"] for b in ps2.get("buttons",[]))
    print(f"NEXT: {'act --submit' if has_submit else 'act --fill'}", file=sys.stderr)

def cmd_back(jid):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    page = find_page(ctx, state)
    if not page: print("ERROR: no page found", file=sys.stderr); sys.exit(1)
    page.evaluate("""() => { const c = document.querySelector('[role="dialog"]') || document; c.querySelectorAll('button').forEach(b => { if ((b.textContent||'').trim().toLowerCase() === 'back' && !b.disabled) b.click(); }); }""")
    time.sleep(3)
    ps = read_page(page)
    state["page"] = ps
    save_state(state)
    print(f"ACTION: Back\nPAGE: {json.dumps(ps)}\nNEXT: act --fill", file=sys.stderr)

def cmd_submit(jid, confirm=False):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    page = find_page(ctx, state)
    if not page: print("ERROR: no page found", file=sys.stderr); sys.exit(1)

    ps = read_page(page)
    submit_kw_order = ("submit application", "submit", "send application", "apply", "send")
    btns = ps.get("buttons", [])
    target = None
    for kw in submit_kw_order:
        for b in btns:
            if b["text"].lower().strip() == kw and not b["disabled"]:
                target = b; break
        if target: break
    if not target:
        for kw in submit_kw_order:
            for b in btns:
                if kw in b["text"].lower().strip() and not b["disabled"]:
                    target = b; break
            if target: break
    if not target:
        print("NO_SUBMIT_BUTTON\nNEXT: none", file=sys.stderr); return
    print(f"SUBMIT: {target['text']}\nDISABLED: {target['disabled']}", file=sys.stderr)
    if not confirm:
        print("DRY_RUN: pass --confirm to submit\nNEXT: act --submit --confirm", file=sys.stderr); return

    try:
        b_loc = page.locator(f'button:has-text("{target["text"]}")')
        b_loc.first.click(timeout=5000)
    except: pass
    time.sleep(5)

    if not page.evaluate("() => document.querySelector('[role=\"dialog\"]')"):
        print("STATUS: submitted", file=sys.stderr)
        get_conn().execute("UPDATE jobs SET stage=? WHERE id=?", ("applied", jid)).connection.commit()
    else:
        print("STATUS: unknown (modal still open)", file=sys.stderr)
    print("NEXT: verify", file=sys.stderr)

def cmd_auto(jid, answers_json=None):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    answers = {}
    if answers_json:
        try: answers = json.loads(answers_json)
        except: pass

    for pn in range(1, 11):
        cmd_fill(jid, json.dumps(answers) if answers else None)
        state = load_state()
        ps = state.get("page", {})
        unfilled = [f for f in ps.get("fields", []) if f.get("required") and not f.get("value")]
        if unfilled:
            print(f"AUTO: page {pn} — {len(unfilled)} unfilled, stop", file=sys.stderr)
            return
        print(f"AUTO: page {pn} — filled, advancing", file=sys.stderr)
        cmd_next(jid)
        state = load_state()
        if state.get("result") in ("submitted", "modal_closed"):
            print(f"AUTO: {state['result']}", file=sys.stderr)
            return
    print(f"AUTO: max pages reached without submit", file=sys.stderr)

def run(args):
    if args.fill: cmd_fill(args.jid, args.answers)
    elif args.next: cmd_next(args.jid)
    elif args.back: cmd_back(args.jid)
    elif args.submit: cmd_submit(args.jid, args.confirm)
    elif args.auto: cmd_auto(args.jid, args.answers)
    else: print("ERROR: specify --fill, --next, --back, --submit, or --auto", file=sys.stderr)
