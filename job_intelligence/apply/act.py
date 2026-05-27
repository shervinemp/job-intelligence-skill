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

def _match_word(needle, haystack):
    """Word-boundary match: 'phone' matches 'phone number' but NOT 'phone extension'."""
    return f" {needle} " in f" {haystack} " or haystack.startswith(f"{needle} ") or haystack.endswith(f" {needle}")

def _find_answer(label, label_norm, answers, ca, profile):
    """Find answer from --answers, common_answers, or profile. Returns None if uncertain.
    For common_answers, prefers the most specific (most words) match."""
    for k, v in answers.items():
        k_norm = re.sub(r'[^a-z0-9]+', ' ', k.lower()).strip()
        if k_norm == label_norm or label_norm.startswith(k_norm):
            return v
    # common_answers: find all matches, pick the one with most words (most specific)
    best_key, best_val = "", None
    best_words = 0
    for ck, cv in ca.items():
        if not cv: continue
        kn = ck.lower().replace('_', ' ').strip()
        kw_count = len(kn.split())
        if kn == label_norm:
            return cv  # exact match wins immediately regardless of specificity
        if label_norm.startswith(kn) and kw_count > best_words:
            best_key, best_val = kn, cv
            best_words = kw_count
    if best_val:
        return best_val
    from apply.common.page_helpers import resolve_label
    return resolve_label(label, profile)

def _click_candidate(page, c, state=None):
    if c["tag"] == "A" and c.get("href"):
        page.goto(c["href"], wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
        # If goto resulted in 0 fields and no forms, try click instead (SPA)
        from apply.common.page_helpers import read_page
        _ps = read_page(page)
        if _ps["fieldCount"] == 0 and not page.evaluate("() => document.querySelectorAll('form').length"):
            try:
                loc = page.locator(f'a:has-text("{c["text"]}")')
                if loc.count() > 0:
                    loc.first.click(force=True, timeout=5000)
                    time.sleep(3)
            except:
                pass
    else:
        try:
            loc = page.locator(f'button:has-text("{c["text"]}")')
            if loc.count() > 0:
                loc.first.click(force=True, timeout=5000)
            else:
                page.evaluate(f"""(txt) => {{
                    const all = document.querySelectorAll('button');
                    for (const el of all) {{
                        if (el.offsetParent === null) continue;
                        if ((el.textContent || '').trim().toLowerCase() === txt) {{ el.click(); return; }}
                    }}
                }}""", c["text"])
        except:
                page.evaluate(f"""(txt) => {{
                    const all = document.querySelectorAll('button');
                    for (const el of all) {{
                        if (el.offsetParent === null) continue;
                        if ((el.textContent || '').trim().toLowerCase() === txt) {{ el.dispatchEvent(new MouseEvent('click', {{bubbles:true}})); return; }}
                    }}
                }}""", c["text"])
    if state:
        from apply.common.page_manager import PageManager
        pm = PageManager(page.context, state.get("jid", ""))
        snap = pm.snapshot(page)
        state["external_url"] = page.url
        time.sleep(5)
        pm.register(page)
        snap2 = pm.snapshot(page)
        diff = pm.diff(snap, snap2)
        if diff.get("changes"):
            print(f"CHANGE: {';'.join(diff['changes'])}", file=sys.stderr)
    else:
        time.sleep(5)
def _handle_post_click(state, ps, page):
    if not ps or ps["fieldCount"] == 0:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        for w in ["thank you", "submitted", "your application", "has been sent"]:
            if w in text:
                print("STATUS: submitted\nNEXT: verify", file=sys.stderr)
                state["result"] = "submitted"
                save_state(state)
                return True
        print("STATUS: modal_closed\nNEXT: verify", file=sys.stderr)
        state["result"] = "modal_closed"
        save_state(state)
        return True
    # Check for validation errors
    error_btns = [b for b in (ps.get("buttons") or []) if "error" in b.get("text","").lower()]
    if error_btns:
        print(f"ERRORS: {json.dumps([b['text'] for b in error_btns])}", file=sys.stderr)
        from apply.common.page_helpers import scan_actions
        cands = scan_actions(page, ["save and continue", "next", "continue", "review", "submit"])
        print(f"CANDIDATES: {json.dumps(cands[:5])}", file=sys.stderr)
        print("NEXT: model_choice — fix errors or skip", file=sys.stderr)
        state["result"] = "validation_error"
        save_state(state)
        return True
    print(f"PAGE: {json.dumps(ps)}", file=sys.stderr)
    return False

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

        # Custom dropdown (e.g. Workday province selectors)
        if f["tag"] == "DROPDOWN":
            current = f.get("value", "")
            if current and current != "Select One" and current != "Select...":
                continue  # already filled
            lbl = f["label"]
            lbl_norm = re.sub(r'[^a-z0-9]+', ' ', lbl.lower()).strip()
            ans = _find_answer(lbl, lbl_norm, answers, ca, profile)
            if ans and f.get("id"):
                try:
                    btn = page.locator(f'[id="{f["id"]}"]')
                    if btn.count() > 0:
                        btn.first.click(force=True, timeout=5000)
                        time.sleep(1)
                        opt = page.locator(f'[role="option"]:has-text("{ans}")')
                        if opt.count() > 0:
                            opt.first.click(force=True, timeout=3000)
                            time.sleep(0.5)
                            filled += 1
                        else:
                            page.keyboard.press("Escape")
                except: pass
            elif f.get("required"):
                unfilled.append({"label": lbl[:60], "options": [], "tag": "DROPDOWN"})
            continue

        lbl = f["label"]
        lbl_norm = re.sub(r'[^a-z0-9]+', ' ', lbl.lower()).strip()

        ans = _find_answer(lbl, lbl_norm, answers, ca, profile)

        if ans:
            sel = ""
            if f["id"]: sel = f'[id="{f["id"]}"]'
            elif f["name"]: sel = f'[name="{f["name"]}"]'
            elif f.get("placeholder"): sel = f'[placeholder="{f["placeholder"]}"]'
            if sel:
                try:
                    el = page.query_selector(sel)
                    if el:
                        if f["tag"] == "SELECT":
                            for opt in f["options"]:
                                if ans.lower() in opt.lower(): el.select_option(opt); break
                            else: el.select_option(ans)
                        elif f["tag"] in ("INPUT", "TEXTAREA"):
                            el.fill(ans)
                            # Autocomplete: if inside multiselect widget, confirm selection
                            try:
                                aid = f.get("data-automation-id", "")
                                is_ac = "multiSelect" in aid or bool(page.locator(f'[data-automation-id="multiSelectContainer"]').count())
                                if is_ac:
                                    time.sleep(0.5)
                                    # Try clicking matching option in dropdown
                                    clicked = page.evaluate(f"""(a) => {{
                                        var opts = document.querySelectorAll('[role="option"]');
                                        for (var o of opts) {{
                                            if (o.offsetParent === null) continue;
                                            if ((o.textContent || "").trim().toLowerCase() === a.toLowerCase()) {{
                                                o.click(); return true;
                                            }}
                                        }}
                                        return false;
                                    }}""", ans)
                                    if not clicked:
                                        page.keyboard.press("Enter")
                                        time.sleep(0.3)
                            except: pass
                        filled += 1
                except: pass
        elif f.get("required"):
            unfilled.append({"label": lbl[:60], "options": f.get("options", []), "tag": f["tag"]})
    return filled, unfilled

def cmd_fill(jid, answers_json=None, candidate=None):
    answers = {}
    if answers_json:
        try: answers = json.loads(answers_json)
        except: print("ERROR: --answers must be valid JSON", file=sys.stderr)

    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    from apply.common.page_manager import PageManager
    pm = PageManager(ctx, jid)
    ext = state.get("external_url", "")
    page, _, _ = pm.find(fallback_url=ext)
    if not page:
        if ctx.pages:
            print("NO_MATCH: no page matches. Open pages:", file=sys.stderr)
            for i, p in enumerate(ctx.pages):
                print(f"  [{i}] {p.url[:100]}", file=sys.stderr)
            if ext:
                print(f"WANTED: {ext[:100]}", file=sys.stderr)
            print("TIP: navigate to the job URL, then retry", file=sys.stderr)
            print("NEXT: retry", file=sys.stderr); return
        elif ext:
            page = ctx.new_page()
            page.goto(ext, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            pm.register(page)
        else:
            print("ERROR: no page found and no external URL", file=sys.stderr); sys.exit(1)
    ps = read_page(page)
    # Guard: if this page was already filled, warn but proceed
    last_fingerprint = state.get("page_fingerprint", "")
    current_fingerprint = str(len(ps["fields"])) + ":" + str(len(ps.get("buttons", [])))
    if current_fingerprint == last_fingerprint and state.get("filled", 0) > 0:
        print("WARN: page looks unchanged from last fill — verify the form advanced", file=sys.stderr)
    state["page_fingerprint"] = current_fingerprint

    # If candidate was specified, find and click it
    if candidate is not None and ps["fieldCount"] == 0:
        from apply.common.page_helpers import scan_actions
        kws = ["apply", "apply for this job", "apply manually", "submit", "apply now"]
        cands = scan_actions(page, kws, _EXCLUDED_BUTTONS)
        if candidate < len(cands):
            c = cands[candidate]
            _click_candidate(page, c, state)
            ps = read_page(page)
            print(f"CANDIDATE_CLICK: #{candidate} '{c['text']}' → {ps['fieldCount']} fields", file=sys.stderr)
        else:
            print(f"ERROR: candidate {candidate} out of range (0-{len(cands)-1})", file=sys.stderr); return

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
            print("LOGIN_WALL: sign in required — login in your Chrome browser, then retry this command", file=sys.stderr)
            print("NEXT: retry after login", file=sys.stderr)
            return

    # If no fields detected, use model-assisted action finding
    if ps["fieldCount"] == 0:
        from apply.common.page_helpers import scan_actions
        apply_kws = ["apply", "apply for this job", "apply manually", "submit", "apply now"]
        candidates = scan_actions(page, apply_kws, _EXCLUDED_BUTTONS)
        print(f"CANDIDATES: {json.dumps(candidates[:8])}", file=sys.stderr)

        if candidates and candidates[0]["score"] >= 4:
            # Certain match — auto-follow
            c = candidates[0]
            _click_candidate(page, c, state)
            ps = read_page(page)
            print(f"AUTO_FOLLOW: '{c['text']}' → {ps['fieldCount']} fields", file=sys.stderr)
        elif candidates:
            print("CHOOSE: act --fill <jid> --candidate N", file=sys.stderr)
            print("NEXT: model_choice", file=sys.stderr)
            state["page"] = ps; state["filled"] = 0; save_state(state); return
        else:
            print("WARN: no actionable buttons found — page may not be an application form", file=sys.stderr)
            print("NEXT: skip", file=sys.stderr)
            state["page"] = ps; state["filled"] = 0; save_state(state); return

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

def cmd_next(jid, candidate=None):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    from apply.common.page_manager import PageManager
    pm = PageManager(ctx, jid)
    ext = state.get("external_url", "")
    page, _, _ = pm.find(fallback_url=ext)
    if not page:
        if ext:
            page = ctx.new_page()
            page.goto(ext, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            pm.register(page)
        else:
            print("ERROR: no page found and no external URL", file=sys.stderr); return

    ps = read_page(page)

    from apply.common.page_helpers import scan_actions
    advance_kws = ["next", "continue", "review", "done", "submit", "submit application"]

    # If candidate was specified, click it directly
    if candidate is not None:
        cands = scan_actions(page, advance_kws, _EXCLUDED_BUTTONS)
        if candidate < len(cands):
            c = cands[candidate]
            _click_candidate(page, c, state)
            ps2 = read_page(page)
            _handle_post_click(state, ps2, page)
        else:
            print(f"ERROR: candidate {candidate} out of range (0-{len(cands)-1})", file=sys.stderr)
        return

    candidates = [c for c in scan_actions(page, advance_kws, _EXCLUDED_BUTTONS) if not c.get("disabled")]
    print(f"CANDIDATES: {json.dumps(candidates[:8])}", file=sys.stderr)

    target = None
    if candidates and candidates[0]["score"] >= 4:
        target = candidates[0]
    elif candidates:
        print("CHOOSE: act --next <jid> --candidate N", file=sys.stderr)
        print("NEXT: model_choice", file=sys.stderr)
        save_state(state); return
    else:
        # Check if there are disabled advance buttons
        for c in scan_actions(page, advance_kws):
            if c.get("disabled"):
                print(f"BUTTON_DISABLED: {c['text']} — fill required fields first", file=sys.stderr)
                print("NEXT: act --fill", file=sys.stderr); return
        print("NO_BUTTON\nNEXT: none", file=sys.stderr); return

    print(f"ACTION: {target['text']}", file=sys.stderr)
    _click_candidate(page, target, state)
    ps2 = read_page(page)
    if not _handle_post_click(state, ps2, page):
        has_submit = any(b["text"].lower() in ("submit", "submit application", "apply", "send application") and not b["disabled"] for b in ps2.get("buttons",[]))
        print(f"NEXT: {'act --submit' if has_submit else 'act --fill'}", file=sys.stderr)

def cmd_back(jid):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    from apply.common.page_manager import PageManager
    page = PageManager(ctx, jid).find(fallback_url=state.get("external_url", ""))[0]
    if not page: print("ERROR: no page found", file=sys.stderr); sys.exit(1)
    page.evaluate("""() => { const c = document.querySelector('[role="dialog"]') || document; c.querySelectorAll('button').forEach(b => { if ((b.textContent||'').trim().toLowerCase() === 'back' && !b.disabled) b.click(); }); }""")
    time.sleep(3)
    ps = read_page(page)
    state["page"] = ps
    save_state(state)
    print(f"ACTION: Back\nPAGE: {json.dumps(ps)}\nNEXT: act --fill", file=sys.stderr)

def cmd_submit(jid, confirm=False, candidate=None):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    b, ctx = connect()
    from apply.common.page_manager import PageManager
    page = PageManager(ctx, jid).find(fallback_url=state.get("external_url", ""))[0]
    if not page: print("ERROR: no page found", file=sys.stderr); sys.exit(1)

    from apply.common.page_helpers import scan_actions
    submit_kws = ["submit application", "submit", "send application", "apply", "send"]
    cands = [c for c in scan_actions(page, submit_kws, _EXCLUDED_BUTTONS) if not c.get("disabled")]

    if candidate is not None:
        if candidate < len(cands):
            target = cands[candidate]
        else:
            print(f"ERROR: candidate {candidate} out of range (0-{len(cands)-1})", file=sys.stderr); return
    elif cands and cands[0]["score"] >= 4:
        target = cands[0]
    elif cands:
        print(f"CANDIDATES: {json.dumps(cands[:8])}", file=sys.stderr)
        print("CHOOSE: act --submit <jid> --candidate N", file=sys.stderr)
        print("NEXT: model_choice", file=sys.stderr); return
    else:
        print("NO_SUBMIT_BUTTON\nNEXT: none", file=sys.stderr); return
    print(f"SUBMIT: {target['text']}\nDISABLED: {target.get('disabled', False)}", file=sys.stderr)
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
    if args.fill: cmd_fill(args.jid, args.answers, args.candidate)
    elif args.next: cmd_next(args.jid, args.candidate)
    elif args.back: cmd_back(args.jid)
    elif args.submit: cmd_submit(args.jid, args.confirm, args.candidate)
    elif args.auto: cmd_auto(args.jid, args.answers)
    else: print("ERROR: specify --fill, --next, --back, --submit, or --auto", file=sys.stderr)
