#!/usr/bin/env python3
"""act.py — One action per call: fill, next, back, submit, auto.
Always reads fresh state, verifies before/after, prints structured output.
"""
import json, os, sys, re, time
from urllib.parse import urlparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.page_helpers import load_state, save_state, read_page, find_page, resolve_label, handle_captcha, check_captcha
from apply.common.registry import resolve as resolve_registry
from apply.common.inspector import probe as probe_page
from apply.common.field_reader import read_fields
from apply.common.learner import LearnSession, SiteProfile, ButtonIntentClassifier, LabelRegistry

profile_path = os.path.join(os.path.dirname(__file__), "..", "profile.json")
_EXCLUDED_BUTTONS = {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse"}

# Pipeline mode
_TRUSTED = False  # set via --trust or auto-promotion


class CircuitBreaker:
    """Halt pipeline on 3+ consecutive identical failures."""
    _state = {"pattern": "", "count": 0}

    _USER_TYPES = {"login_wall", "captcha"}

    @classmethod
    def record(cls, error_type):
        if error_type in cls._USER_TYPES:
            return False  # user-action-required — never trip breaker
        if error_type == cls._state["pattern"]:
            cls._state["count"] += 1
        else:
            cls._state["pattern"] = error_type
            cls._state["count"] = 1
        if cls._state["count"] >= 3:
            print(f"\n*** CIRCUIT BREAKER: {cls._state['count']} consecutive '{error_type}' failures ***", file=sys.stderr)
            print(f"  Pipeline halted. Inspect the ATS form or job, then reset state.", file=sys.stderr)
            return True
        return False

    @classmethod
    def reset(cls):
        cls._state = {"pattern": "", "count": 0}


CircuitBreaker.reset()

def _domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _load_learn_session(domain, jid):
    """Create or continue a learn session for this domain."""
    if not domain:
        return None
    # Aggregator domains are always trusted (no learning needed)
    from apply.common.learner import _SKIP_DOMAINS as _aggs
    global _TRUSTED
    if any(d in domain for d in _aggs):
        _TRUSTED = True
        return None
    session = LearnSession(domain, jid)
    profile = SiteProfile.load(domain)
    if profile.trusted:
        _TRUSTED = True
    return session


_KNOWN_PROFILE_KEYS = {
    "first_name", "last_name", "email", "phone", "linkedin_url",
    "github_url", "portfolio_url", "website", "address", "city",
    "state", "zip", "country", "authorized_to_work", "visa_status",
    "requires_sponsorship", "expected_salary", "salary_currency",
    "work_preference", "remote_preference", "start_date",
    "pronouns", "common_answers",
}


def _validate_profile(profile):
    """Warn about unrecognized keys in profile.json to catch typos early."""
    unknown = set(profile.keys()) - _KNOWN_PROFILE_KEYS
    if unknown:
        print(f"WARN: profile.json has unrecognized keys: {', '.join(sorted(unknown))}", file=sys.stderr)
        print(f"  Known keys: {', '.join(sorted(_KNOWN_PROFILE_KEYS))}", file=sys.stderr)


def _match_word(needle, haystack):
    """Word-boundary match: 'phone' matches 'phone number' but NOT 'phone extension'."""
    return f" {needle} " in f" {haystack} " or haystack.startswith(f"{needle} ") or haystack.endswith(f" {needle}")

def _save_answer(label, value, profile_path):
    """Normalize a field label to a common_answers key and save to profile.json."""
    try:
        with open(profile_path) as f:
            profile = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    norm = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:80]
    words = [w for w in norm.split('_') if len(w) > 2]
    key = '_'.join(words[:4]) if len(words) >= 3 else norm
    ca = profile.setdefault("common_answers", {})
    if key not in ca or ca[key] != value:
        ca[key] = value
        try:
            with open(profile_path, "w") as f:
                json.dump(profile, f, indent=2)
        except OSError:
            pass


def _find_answer(label, label_norm, answers, ca, profile, required=False):
    """Find answer from --answers, common_answers, or profile.
    For common_answers: optional fields only use exact matches; required fields can use prefix matches."""
    for k, v in answers.items():
        k_norm = re.sub(r'[^a-z0-9+#]+', ' ', k.lower()).strip()
        if k_norm == label_norm or label_norm.startswith(k_norm):
            return v
    # common_answers: optional fields only use exact matches
    best_key, best_val, best_words = "", None, 0
    for ck, cv in ca.items():
        if not cv: continue
        kn = ck.lower().replace('_', ' ').strip()
        if kn == label_norm:
            return cv  # exact match always works
        if required and label_norm.startswith(kn):
            # Prefix match: prefer more specific (more words) key
            kw_count = len(kn.split())
            if kw_count > best_words:
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
        if CircuitBreaker.record("validation_error"):
            return True
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
        q_norm = re.sub(r'[^a-z0-9+#]+', ' ', q_label.lower()).strip()
 
        ans = None
        for k, v in answers.items():
            k_norm = re.sub(r'[^a-z0-9+#]+', ' ', k.lower()).strip()
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
            if not os.path.isdir(results_dir) or not any("Resume" in fn and fn.endswith(".pdf") for fn in os.listdir(results_dir)):
                print(f"WARN: no resume PDF found in {results_dir} — upload will be skipped", file=sys.stderr)
                continue
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
                    pdf_path = os.path.join(results_dir, candidates[0][1])
                    try:
                        if os.path.getsize(pdf_path) < 512:
                            print(f"WARN: {candidates[0][1]} is {os.path.getsize(pdf_path)} bytes — skipping empty PDF", file=sys.stderr)
                            continue
                    except OSError:
                        continue
                    try:
                        fi = page.query_selector('input[type="file"][required]') or page.query_selector('input[type="file"]')
                        if fi: fi.set_input_files(pdf_path); file_uploaded = True; filled += 1
                    except: pass
            continue

        # Custom dropdown (e.g. Workday province selectors)
        if f["tag"] == "DROPDOWN":
            current = f.get("value", "")
            if current and current != "Select One" and current != "Select...":
                continue  # already filled
            lbl = f["label"]
            lbl_norm = re.sub(r'[^a-z0-9+#]+', ' ', lbl.lower()).strip()
            ans = _find_answer(lbl, lbl_norm, answers, ca, profile, required=f.get("required", False))
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
        lbl_norm = re.sub(r'[^a-z0-9+#]+', ' ', lbl.lower()).strip()

        # Skip pre-filled fields with valid data
        current_val = f.get("value", "")
        if current_val and len(current_val.strip()) > 1 and not f.get("required", False):
            continue

        ans = _find_answer(lbl, lbl_norm, answers, ca, profile, required=f.get("required", False))

        if ans is not None:
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
                            # Check if this is an autocomplete field (multiselect widget)
                            is_ac = False
                            try:
                                if f.get("placeholder") == "Search" or f.get("data_automation_id", ""):
                                    is_ac = True
                            except:
                                pass
                            if is_ac:
                                page.evaluate("""(args) => {
                                    var ans = args[0], sel = args[1];
                                    var el = document.querySelector(sel);
                                    if (!el) return;
                                    el.focus();
                                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                                    nativeInputValueSetter.call(el, ans);
                                    el.dispatchEvent(new Event("input", { bubbles: true }));
                                    el.dispatchEvent(new Event("change", { bubbles: true }));
                                }""", [ans, sel])
                                time.sleep(0.5)
                            else:
                                el.fill(ans)
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
    if handle_captcha(page, state):
        print("NEXT: retry after solving CAPTCHA", file=sys.stderr); return

    # Registry + probe cascade
    domain = _domain(page.url)
    registry = resolve_registry(page.url)
    learn = _load_learn_session(domain, jid)

    # Probe: try standard first, cascade on failure
    ps = read_page(page)
    if ps.get("fieldCount", 0) == 0 and domain:
        reg_config = {"probe": {"widgets": registry.widgets}} if registry and registry.widgets else None
        probe_result = probe_page(page, domain=domain, registry_config=reg_config)
        if probe_result.field_count > 0:
            ps = probe_result.to_dict()
        elif probe_result.snapshot_path:
            print(f"PROBE_FAILED: snapshot saved to {probe_result.snapshot_path}", file=sys.stderr)

    # Learn session tracking
    if learn:
        learn.record_page(ps.get("fields", []), ps.get("buttons", []))
        state["_learn_page"] = learn.page_count
        state["_learn_domain"] = learn.domain

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
            CircuitBreaker.record("login_wall")
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
            CircuitBreaker.record("no_buttons")
            print("NEXT: skip", file=sys.stderr)
            state["page"] = ps; state["filled"] = 0; save_state(state); return

    profile = {}
    if os.path.exists(profile_path):
        try:
            with open(profile_path) as f: profile = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"WARN: profile.json corrupt or unreadable — using empty profile", file=sys.stderr)
            profile = {}
        else:
            _validate_profile(profile)
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
                if (/\bfollow\b/.test(t) && /\b(updates?|company|page)\b/.test(t)) {
                    cb.checked = false; cb.dispatchEvent(new Event('change', {bubbles:true}));
                }
            }
        }
    }""")

    from apply.common.page_helpers import read_and_save as rs
    state["filled"] = filled
    rs(page, state)

    # Save new answers to profile common_answers for future use
    if filled > 0 and answers:
        for label, val in answers.items():
            _save_answer(label, val, profile_path)

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
    if handle_captcha(page, state):
        print("NEXT: retry after solving CAPTCHA", file=sys.stderr); return

    from apply.common.page_helpers import scan_actions
    advance_kws = ["next", "continue", "review", "done", "submit", "submit application"]

    # Classify buttons via ButtonIntentClassifier
    all_buttons = ps.get("buttons", [])
    learn = state.get("_learn_session")
    if not learn:
        domain = _domain(page.url)
        learn = _load_learn_session(domain, state.get("jid", ""))

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
    if candidates:
        best = ButtonIntentClassifier.pick(candidates, "submit")
        if not best:
            best = ButtonIntentClassifier.pick(candidates, "advance")
        if best:
            target = candidates[best["index"]]
            if learn:
                learn.record_transition(target["text"])
    if not target and candidates and candidates[0]["score"] >= 4:
        target = candidates[0]
        if learn:
            learn.record_transition(target["text"])
    elif not target and candidates:
        print("CHOOSE: act --next <jid> --candidate N", file=sys.stderr)
        print("NEXT: model_choice", file=sys.stderr)
        ButtonIntentClassifier.record_ambiguity(candidates[0]["text"], "advance")
        save_state(state); return
    elif not target:
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

    # Trust gate: learning mode never submits
    domain = _domain(state.get("external_url", "") or state.get("url", ""))
    if domain and not _TRUSTED:
        profile = SiteProfile.load(domain)
        learn = LearnSession(domain, jid)
        learn.submit_reached = True
        learn.complete(submit_reached=True)
        print("LEARNING: submit gate reached — no submit until --trust is set", file=sys.stderr)
        print("NEXT: verify or run with --trust", file=sys.stderr)
        return

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
            print("ERROR: no page found and no external URL\nNEXT: detect or navigate first", file=sys.stderr)
            return

    if handle_captcha(page, state):
        print("NEXT: retry after solving CAPTCHA", file=sys.stderr); return

    # ButtonIntentClassifier for submit buttons
    all_buttons = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('button'))
            .filter(b => b.offsetParent)
            .map(b => ({text: (b.textContent||'').trim().slice(0,30), disabled: b.disabled}));
    }""") or []
    best = ButtonIntentClassifier.pick(all_buttons, "submit")

    cands = []
    if best:
        cands = [{"text": all_buttons[best["index"]]["text"],
                   "disabled": False,
                   "score": best["confidence"] * 5}]
    if not cands:
        from apply.common.page_helpers import scan_actions
        submit_kws = ["submit application", "submit", "send application", "apply", "send"]
        cands = [c for c in scan_actions(page, submit_kws, _EXCLUDED_BUTTONS) if not c.get("disabled")]

    if candidate is not None:
        if candidate < len(cands):
            target = cands[candidate]
        else:
            print(f"ERROR: candidate {candidate} out of range (0-{len(cands)-1})", file=sys.stderr); return
    elif cands and (cands[0].get("score", 0) >= 4 or cands[0].get("confidence", 0) >= 0.8):
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

    text = (page.evaluate("() => document.body.innerText") or "").lower()
    has_form = page.evaluate("""() => {
        const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        return inputs.length > 0 && Array.from(inputs).some(i => i.offsetParent !== null);
    }""") or False
    has_error = any(w in text for w in ["error", "required", "invalid", "correct the"])
    if has_error and has_form:
        print("STATUS: validation_errors — form still present\nNEXT: act --fill", file=sys.stderr)
    elif not page.evaluate("() => document.querySelector('[role=\"dialog\"]')") and not has_form:
        get_conn().execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid)).connection.commit()
        print("STATUS: submitted\nNEXT: verify", file=sys.stderr)
    else:
        print("STATUS: unknown (page unchanged or not submitted)\nNEXT: verify", file=sys.stderr)

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
        btns = ps.get("buttons", [])
        has_submit = any(b["text"].lower() in ("submit", "submit application", "apply", "send application") and not b.get("disabled") for b in btns)
        has_next = any(b["text"].lower() in ("next", "review", "continue", "done") and not b.get("disabled") for b in btns)
        unfilled = [f for f in ps.get("fields", []) if f.get("required") and not f.get("value")]

        if has_submit and not unfilled:
            print(f"AUTO: page {pn} — all filled, submitting", file=sys.stderr)
            cmd_submit(jid, confirm=True)
            state = load_state()
            if state.get("result") in ("submitted", "modal_closed"):
                print(f"AUTO: {state['result']}", file=sys.stderr)
                return
            print(f"AUTO: submit returned unknown state, continuing", file=sys.stderr)
        elif unfilled:
            print(f"AUTO: page {pn} — {len(unfilled)} unfilled, stop", file=sys.stderr)
            return
        elif has_next:
            print(f"AUTO: page {pn} — filled, advancing", file=sys.stderr)
            cmd_next(jid)
            state = load_state()
            if state.get("result") in ("submitted", "modal_closed"):
                print(f"AUTO: {state['result']}", file=sys.stderr)
                return
        else:
            print(f"AUTO: page {pn} — no buttons detected, stop", file=sys.stderr)
            return
    print(f"AUTO: max pages reached without submit", file=sys.stderr)

def run(args):
    global _TRUSTED
    if getattr(args, 'trust', False):
        _TRUSTED = True
    if args.fill: cmd_fill(args.jid, args.answers, args.candidate)
    elif args.next: cmd_next(args.jid, args.candidate)
    elif args.back: cmd_back(args.jid)
    elif args.submit: cmd_submit(args.jid, args.confirm, args.candidate)
    elif args.auto: cmd_auto(args.jid, args.answers)
    else: print("ERROR: specify --fill, --next, --back, --submit, or --auto", file=sys.stderr)
