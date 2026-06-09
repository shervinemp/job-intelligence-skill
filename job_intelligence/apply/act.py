#!/usr/bin/env python3
"""act.py — One action per call: fill, next, back, submit.
Always reads fresh state, verifies before/after, prints structured output.
"""
import json, os, sys, re, time, random
from urllib.parse import urlparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.page_helpers import load_state, save_state, read_page, handle_captcha, scan_actions, read_and_save, DEFAULT_EXCLUDED_BUTTONS as _EXCLUDED_BUTTONS
from apply.common.registry import resolve as resolve_registry
from apply.common.inspector import probe as probe_page, probe_all
from apply.common.learner import ButtonIntentClassifier
from apply.common.output import emit_next, emit_status, emit_warn, emit_fill_report, emit_candidates
from apply.common.page_manager import PageManager
from apply.common.platforms import check_page, LOGIN_WALL, GUEST_APPLY

profile_path = os.path.join(os.path.dirname(__file__), "..", "profile.json")
# Pipeline mode
_DEBUG = False    # set via --debug; controls verbose output like PAGE full JSON

def _domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


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
            tmp_path = profile_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(profile, f, indent=2)
            os.replace(tmp_path, profile_path)
        except OSError:
            pass


def _find_answer(label, label_norm, answers, ca, profile, required=False):
    """Find answer from --answers, common_answers, or profile. Delegates to answer_matcher."""
    return match_answer(label, answers=answers, common_answers=ca, profile=profile, required=required)

def _page_hash(page):
    """Stable hash of form-relevant content — field count + first 5 button texts."""
    try:
        return page.evaluate("""() => {
            const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
            const btns = document.querySelectorAll('button');
            const btnTexts = Array.from(btns).filter(b => b.offsetParent).map(b => b.textContent.trim()).slice(0, 5).join('|');
            return inputs.length + ':' + btnTexts;
        }""")
    except Exception:
        return ""


def _wait_for_change(page, before, timeout=12):
    """Poll for page content to differ from `before` hash.
    Requires 1s stability to avoid false-positives from loading spinners.
    Returns True if page changed, False on timeout."""
    stable = 0
    last = before
    for _ in range(timeout * 2):
        time.sleep(0.5)
        current = _page_hash(page)
        if current == last:
            stable += 1
            if stable >= 2 and current != before:
                return True
        else:
            stable = 0
            last = current
    return False


def _click_candidate(page, c, state=None):
    if c.get("disabled"):
        print(f"SKIP: '{c['text']}' is disabled — fill required fields first", file=sys.stderr)
        return
    if c["tag"] == "A" and c.get("href"):
        page.goto(c["href"], wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
        # If goto resulted in 0 fields and no forms, try click instead (SPA)
        _ps = read_page(page)
        if _ps["fieldCount"] == 0 and not page.evaluate("() => document.querySelectorAll('form').length"):
            before_spa = _page_hash(page)
            page.evaluate(f"""(txt) => {{
                const all = document.querySelectorAll('a');
                for (const el of all) {{
                    if (el.offsetParent === null) continue;
                    if ((el.textContent || '').trim().toLowerCase() === txt) {{ el.click(); return true; }}
                }}
                return false;
            }}""", c["text"])
            _wait_for_change(page, before_spa)
    else:
        clicked = page.evaluate(f"""(txt) => {{
            const all = document.querySelectorAll('button');
            for (const el of all) {{
                if (el.offsetParent === null) continue;
                if ((el.textContent || '').trim().toLowerCase() === txt) {{ el.click(); return true; }}
            }}
            return false;
        }}""", c["text"])
        if not clicked:
            # Fallback: Playwright locator with more flexible text matching
            try:
                loc = page.locator(f'button:has-text("{c["text"]}")')
                if loc.count() > 0:
                    loc.first.click(force=True, timeout=5000)
                else:
                    print(f"  Click warning: button '{c['text']}' not found via JS or locator", file=sys.stderr)
            except Exception as e:
                print(f"  Click warning: {e}", file=sys.stderr)
    before = _page_hash(page)
    if state:
        pm = PageManager(page.context, state.get("jid", ""))
        snap = pm.snapshot(page)
        state["external_url"] = page.url
        _wait_for_change(page, before)
        pm.register(page)
        snap2 = pm.snapshot(page)
        diff = pm.diff(snap, snap2)
        if diff.get("changes"):
            print(f"CHANGE: {';'.join(diff['changes'])}", file=sys.stderr)
    else:
        _wait_for_change(page, before)
def _handle_post_click(state, ps, page):
    if not ps or ps["fieldCount"] == 0:
        # Don't treat review pages (0 inputs + submit/review button) as submitted
        has_submit_btn = any(b["text"].lower() in ("submit", "submit application", "apply", "send", "review", "review application") and not b.get("disabled")
                            for b in (ps.get("buttons") or []))
        if has_submit_btn:
            return False
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        for w in ["thank you", "submitted", "your application", "has been sent"]:
            if w in text:
                emit_status("submitted")
                emit_next("verify")
                state["result"] = "submitted"
                save_state(state)
                return True
        emit_status("modal_closed")
        emit_next("verify")
        state["result"] = "modal_closed"
        save_state(state)
        return True
    # Check for validation errors
    error_btns = [b for b in (ps.get("buttons") or []) if "error" in b.get("text","").lower()]
    if error_btns:
        print(f"ERRORS: {json.dumps([b['text'] for b in error_btns])}", file=sys.stderr)
        cands = scan_actions(page, ["save and continue", "next", "continue", "review", "submit"])
        emit_candidates(cands)
        emit_next("model_choice", "fix errors or skip")
        state["result"] = "validation_error"
        save_state(state)
        return True
    if _DEBUG:
        print(f"PAGE: {json.dumps(ps)}", file=sys.stderr)
    return False

def _fill_radios(page, fields, answers, ca, profile, jid):
    """Fill radio groups. Returns filled count + unfilled list."""
    filled = 0
    unfilled = []
    def _radio_group_key(rf, idx):
        k = rf.get("name")
        if k: return k
        rid = rf.get("id", "")
        if "_" in rid:
            return rid.rsplit("_", 1)[0]
        dai = rf.get("data_automation_id", "")
        if "_" in dai:
            return dai.rsplit("_", 1)[0]
        lbl = rf.get("label", "")
        if " - " in lbl:
            return lbl.split(" - ")[0]
        # Last resort: unique key per radio — each becomes its own group
        return f"_ungrouped_{idx}"

    radios = [f for f in fields if f["type"] == "radio"]
    groups = {}
    for idx, rf in enumerate(radios):
        gk = _radio_group_key(rf, idx)
        groups.setdefault(gk, []).append(rf)

    def _check_radio(rf):
        """Check a radio element by id or name+value. Returns True if checked."""
        try:
            if rf.get("id"):
                el = page.locator(f'id={rf["id"]}')
                if el.count() > 0 and not el.first.is_checked():
                    el.first.check(); return True
            if rf.get("name"):
                selector = f'input[type="radio"][name="{rf["name"]}"]'
                rv = (rf.get("value") or "")
                if rv: selector += f'[value="{rv}"]'
                el = page.query_selector(selector)
                if el and not el.is_checked():
                    el.check(); return True
        except: pass
        return False

    for gk, group in groups.items():
        opts = [rf["label"] for rf in group]
        q_label = opts[0].split(" - ")[0] if " - " in opts[0] else opts[0]
        q_norm = re.sub(r'[^a-z0-9+#]+', ' ', q_label.lower()).strip()

        ans = _find_answer(q_label, q_norm, answers, ca, profile, required=True)
        if ans:
            ans_lower = ans.lower()
            matched = False
            # 1. Match by option_label (for grid/matrix: column header or choice text)
            for rf in group:
                ol = (rf.get("option_label", "") or "").lower()
                if ol and (ans_lower in ol or ol in ans_lower):
                    if _check_radio(rf):
                        filled += 1; matched = True; break
            if not matched:
                # 2. Match by value attribute
                for rf in group:
                    rv = (rf.get("value", "") or "").lower()
                    if rv and (ans_lower == rv or rv in ans_lower or ans_lower in rv):
                        if _check_radio(rf):
                            filled += 1; matched = True; break
            if not matched:
                # 3. Fallback: match by primary label (original behavior)
                for opt in opts:
                    if ans_lower in opt.lower():
                        for rf in group:
                            if rf["label"] == opt and _check_radio(rf):
                                filled += 1; matched = True; break
                        if matched: break
        else:
            unfilled.append({"label": q_label[:60], "options": opts, "tag": "radio_group"})
    return filled, unfilled

def _fill_text(page, fields, answers, ca, profile, jid, state):
    """Fill text/select/textarea fields. Returns filled count + unfilled list."""
    filled = 0
    unfilled = []
    file_uploaded = False

    for f in fields:
        prev_filled = filled
        if f["type"] == "radio": continue
        if f["tag"] == "INPUT" and f["type"] == "file":
            results_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "results", jid)
            lbl_lower = (f.get("label", "") or "").lower()
            # Skip optional uploads after the first file is placed (unless it's a distinct field like Cover Letter)
            if file_uploaded and not f.get("required", False):
                # Only skip if not a distinct secondary upload (cover letter vs resume)
                if "cover" not in lbl_lower and "letter" not in lbl_lower and "discovery" not in lbl_lower:
                    continue
            if not os.path.isdir(results_dir) or not any("Resume" in fn and fn.endswith(".pdf") for fn in os.listdir(results_dir)):
                if f.get("required", False):
                    unfilled.append({"label": "Resume Upload", "options": [], "tag": "FILE"})
                continue
            candidates = []
            for fn in os.listdir(results_dir):
                if re.search(r'\bResume\b', fn, re.I) and fn.lower().endswith(".pdf"):
                    score = 0
                    if (state.get("title") or "").split(" ")[0].lower() in fn.lower(): score += 2
                    if state.get("company","").lower() in fn.lower(): score += 1
                    candidates.append((score, fn))
            candidates.sort(key=lambda x: -x[0])
            if not candidates: continue
            pdf_path = os.path.join(results_dir, candidates[0][1])
            try:
                if os.path.getsize(pdf_path) < 512:
                    print(f"WARN: {candidates[0][1]} is {os.path.getsize(pdf_path)} bytes — skipping empty PDF", file=sys.stderr)
                    if f.get("required", False):
                        unfilled.append({"label": "Resume Upload", "options": [], "tag": "FILE"})
                    continue
            except OSError:
                continue
            try:
                resume_inputs = [fi for fi in page.query_selector_all('input[type="file"]')
                                 if "resume" in ((page.evaluate(f'(el) => el.closest("div,fieldset,section")?.textContent || ""', fi) or "").lower())]
                fi = resume_inputs[0] if resume_inputs else page.query_selector('input[type="file"]')
                if fi:
                    fi.set_input_files(pdf_path); file_uploaded = True; filled += 1
                    continue
            except: pass
            # Fallback: drag-and-drop zone with no visible file input
            if not file_uploaded:
                try:
                    import base64
                    with open(pdf_path, 'rb') as fh:
                        b64 = base64.b64encode(fh.read()).decode()
                    data_url = f"data:application/pdf;base64,{b64}"
                    dropped = page.evaluate(f"""(dataUrl) => {{
                        const dz = document.querySelector('.dropzone, [ondrop], [class*="file-upload"], [class*="drag-drop"], [class*="upload-resume"]');
                        if (!dz) return false;
                        return fetch(dataUrl).then(r => r.blob()).then(blob => {{
                            const file = new File([blob], 'Resume.pdf', {{type: 'application/pdf'}});
                            const dt = new DataTransfer();
                            dt.items.add(file);
                            ['dragenter', 'dragover'].forEach(t => {{
                                dz.dispatchEvent(new DragEvent(t, {{dataTransfer: dt, bubbles: true, cancelable: true}}));
                            }});
                            return dz.dispatchEvent(new DragEvent('drop', {{dataTransfer: dt, bubbles: true, cancelable: true}}));
                        }}).catch(() => false);
                    }}""", data_url)
                    if dropped:
                        file_uploaded = True; filled += 1
                except Exception:
                    pass
            continue

        # Custom dropdown (e.g. Workday province selectors)
        if f["tag"] == "DROPDOWN":
            current = f.get("value", "")
            if current and current != "Select One" and current != "Select...":
                # Check if current value matches the answer — overwrite if different
                lbl = f["label"]
                lbl_norm = re.sub(r'[^a-z0-9+#]+', ' ', lbl.lower()).strip()
                ans_check = _find_answer(lbl, lbl_norm, answers, ca, profile, required=f.get("required", False))
                if ans_check and ans_check.lower() != current.lower():
                    pass  # will overwrite below
                else:
                    continue  # already filled correctly
            lbl = f["label"]
            lbl_norm = re.sub(r'[^a-z0-9+#]+', ' ', lbl.lower()).strip()
            ans = _find_answer(lbl, lbl_norm, answers, ca, profile, required=f.get("required", False))
            if ans:
                sel = None
                if f.get("id"):
                    sel = f'[id="{f["id"]}"]'
                elif f.get("data_automation_id"):
                    sel = f'[data-automation-id="{f["data_automation_id"]}"]'
                elif f.get("name"):
                    sel = f'[name="{f["name"]}"]'
                if sel:
                    try:
                        btn = page.locator(sel)
                        if btn.count() > 0:
                            btn.first.click(force=True, timeout=5000)
                            time.sleep(1)
                            opt = page.locator(f'[role="option"]:has-text("{ans}")')
                            if opt.count() > 0:
                                opt.first.click(force=True, timeout=3000)
                                time.sleep(0.5)
                                filled += 1
                            else:
                                # Try listbox instead of option (Workday variant)
                                lb = page.locator(f'[role="listbox"]:has-text("{ans}")')
                                if lb.count() > 0:
                                    lb.first.click(force=True, timeout=3000)
                                    time.sleep(0.5)
                                    filled += 1
                                else:
                                    # Close dropdown by clicking trigger again (safe toggle, won't close modal)
                                    btn.first.click(force=True, timeout=5000)
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
            if not sel and f.get("label"):
                try:
                    sel = page.evaluate("""(lbl) => {
                        const labels = document.querySelectorAll('label');
                        for (const l of labels) {
                            if (l.textContent.trim().toLowerCase() === lbl.toLowerCase()) {
                                const forId = l.getAttribute('for');
                                if (forId && document.getElementById(forId)) return '#' + CSS.escape(forId);
                                const inp = l.querySelector('input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable]');
                                if (inp && inp.id) return '#' + CSS.escape(inp.id);
                            }
                        }
                        const all = document.querySelectorAll('[aria-labelledby]');
                        for (const el of all) {
                            const ref = document.getElementById(el.getAttribute('aria-labelledby'));
                            if (ref && ref.textContent.trim().toLowerCase() === lbl.toLowerCase() && el.id) return '#' + CSS.escape(el.id);
                        }
                        return '';
                    }""", f["label"]) or ""
                except: pass
            if sel:
                try:
                    el = page.query_selector(sel)
                    if el:
                        if f["tag"] == "SELECT":
                            try:
                                values = ans if isinstance(ans, list) else [ans]
                                selected = [next((o for o in f['options'] if v.lower() in o.lower()), v) for v in values]
                                el.select_option(selected if len(selected) > 1 else selected[0])
                                filled += 1
                            except Exception:
                                if f.get("required"):
                                    unfilled.append({"label": lbl[:60], "options": f.get("options", []), "tag": "SELECT"})
                                continue
                        elif f.get("datepicker") == "flatpickr":
                            page.evaluate("""(args) => {
                                var sel = args[0], val = args[1];
                                var el = document.querySelector(sel);
                                if (!el) return;
                                if (el._flatpickr) { el._flatpickr.setDate(val, true); return; }
                                var fp = el.closest('.flatpickr');
                                if (fp && fp._flatpickr) { fp._flatpickr.setDate(val, true); }
                            }""", [sel, ans])
                            filled += 1
                        elif f["tag"] == "DIV" or f.get("contenteditable"):
                            page.evaluate(f"""(sel, val) => {{
                                const el = document.querySelector(sel);
                                if (el) {{ el.textContent = val; el.dispatchEvent(new Event('input', {{bubbles:true}})); }}
                            }}""", [sel, ans])
                            filled += 1
                        elif f["tag"] in ("INPUT", "TEXTAREA"):
                            # Check maxlength and truncate if needed
                            try:
                                ml = el.get_attribute('maxlength')
                                if ml and ans and len(ans) > int(ml):
                                    ans = ans[:int(ml)]
                            except: pass
                            # Check if this is an autocomplete field (multiselect widget)
                            is_ac = False
                            try:
                                if f.get("placeholder") == "Search" or f.get("data_automation_id", ""):
                                    is_ac = True
                            except:
                                pass
                            if is_ac:
                                # Use press_sequentially for autocomplete (triggers dropdown, suggests options)
                                try:
                                    el.click()
                                    time.sleep(0.3)
                                    el.press_sequentially(ans, delay=random.randint(40, 90))
                                except Exception:
                                    # Fallback: native value setter
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
        # Brief random delay if a field was filled (masks automation speed)
        if filled > prev_filled:
            time.sleep(random.uniform(0.15, 0.4))
    return filled, unfilled

def _check_already_submitted(state, jid):
    """Check DB stage and return True if already applied. Prevents re-filling."""
    from lib.db import get_conn
    r = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if r and r["stage"] == "applied":
        print(f"ALREADY: job {jid} is already applied (stage=applied)", file=sys.stderr)
        emit_next("none")
        return True
    return False

def cmd_fill(jid, answers_json=None, candidate=None):
    answers = {}
    if answers_json:
        try: answers = json.loads(answers_json)
        except: print("ERROR: --answers must be valid JSON", file=sys.stderr)

    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    if _check_already_submitted(state, jid):
        return
    b, ctx = connect()
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
            print("TIP: open the job URL in Chrome to check, then retry", file=sys.stderr)
            emit_next("act --inspect"); return
        elif ext:
            page = ctx.new_page()
            page.goto(ext, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            pm.register(page)
        else:
            print("ERROR: no page found and no external URL", file=sys.stderr); return
    if handle_captcha(page, state):
        emit_next("retry after solving CAPTCHA"); return

    # Registry + probe cascade
    domain = _domain(page.url)
    registry = resolve_registry(page.url)
    if registry: registry.emit_notes()

    # Track page number for progress indicator
    state["_page"] = state.get("_page", 0) + 1

    # Probe: try standard first, cascade on failure
    ps = read_page(page)
    # Poll for React SPA fields that render 3-8s after DOMContentLoaded
    if ps.get("fieldCount", 0) == 0:
        for _ in range(16):
            time.sleep(0.5)
            ps = read_page(page)
            if ps.get("fieldCount", 0) > 0:
                print(f"SPA_WAIT: {(_+1)*0.5:.1f}s", file=sys.stderr); break
    # Use detect-phase fields (e.g., GraphQL) if DOM returned nothing
    if ps.get("fieldCount", 0) == 0 and state.get("_detect_fields", {}).get("fieldCount", 0) > 0:
        ps = state["_detect_fields"]
    if ps.get("fieldCount", 0) == 0 and domain:
        reg_config = registry  # Pass RegistryConfig object for best_strategy + widgets
        probe_result = probe_page(page, domain=domain, registry_config=reg_config)
        if probe_result.field_count > 0:
            ps = probe_result.to_dict()
        elif probe_result.snapshot_path:
            print(f"PROBE_FAILED: snapshot saved to {probe_result.snapshot_path}", file=sys.stderr)

    # Guard: if this page was already filled, warn but proceed
    last_fingerprint = state.get("page_fingerprint", "")
    label_fp = "_".join(f.get("label", "")[:20] for f in ps["fields"][:3])
    current_fingerprint = f"{len(ps['fields'])}:{len(ps.get('buttons',[]))}:{label_fp}"
    if current_fingerprint == last_fingerprint and state.get("filled", 0) > 0:
        print("WARN: page looks unchanged from last fill — verify the form advanced", file=sys.stderr)
    state["page_fingerprint"] = current_fingerprint

    # If candidate was specified, find and click it
    if candidate is not None and ps["fieldCount"] == 0:
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
            emit_status("login_wall", "sign in required — retry after login")
            emit_next("retry after login")
            return

    # If no fields detected, use model-assisted action finding
    if ps["fieldCount"] == 0:
        apply_kws = ["apply", "apply for this job", "apply manually", "submit", "apply now"]
        candidates = scan_actions(page, apply_kws, _EXCLUDED_BUTTONS)
        emit_candidates(candidates)

        if candidates and candidates[0]["score"] >= 4:
            c = candidates[0]
            _click_candidate(page, c, state)
            ps = read_page(page)
            print(f"AUTO_FOLLOW: '{c['text']}' → {ps['fieldCount']} fields", file=sys.stderr)
        elif candidates:
            print("CHOOSE: act --fill <jid> --candidate N", file=sys.stderr)
            emit_next("model_choice")
            state["page"] = ps; state["filled"] = 0; save_state(state); return
        else:
            emit_warn("no fields or buttons found")
            print("  Open Chrome to inspect the page, then retry.", file=sys.stderr)
            emit_next("act --inspect")
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

    radio_filled, radio_unfilled = _fill_radios(page, ps["fields"], answers, ca, profile, jid)
    text_filled, text_unfilled = _fill_text(page, ps["fields"], answers, ca, profile, jid, state)
    filled = radio_filled + text_filled
    unfilled = radio_unfilled + text_unfilled

    # Unfollow company/social update checkboxes (always)
    page.evaluate("""() => {
        const c = document.querySelector('[role="dialog"]') || document;
        const labels = c.querySelectorAll('label');
        for (const lbl of labels) {
            const t = (lbl.textContent||'').toLowerCase().trim();
            const m = t.match(/^(follow|subscribe|sign up|receive)\\s+(updates?|newsletter|company|page)/);
            if (!m) continue;
            const cbId = lbl.getAttribute('for');
            if (cbId) {
                const cb = c.querySelector('input[type="checkbox"][id="' + cbId + '"]');
                if (cb && cb.checked) { cb.checked = false; cb.dispatchEvent(new Event('change', {bubbles:true})); }
            } else {
                const cb = lbl.querySelector('input[type="checkbox"]');
                if (cb && cb.checked) { cb.checked = false; cb.dispatchEvent(new Event('change', {bubbles:true})); }
            }
        }
    }""")

    state["filled"] = filled

    # Re-scan for conditional fields that may have appeared after fill (e.g., "Do you have a portfolio?" → URL field)
    if filled > 0:
        time.sleep(0.5)
        ps2 = read_page(page)  # read_page already falls back to dialog scope
        seen_labels = {f.get("label", "") for f in ps.get("fields", [])}
        new_fields = [f for f in ps2.get("fields", []) if f.get("required") and f.get("label", "") not in seen_labels]
        if new_fields:
            text_filled2, text_unfilled2 = _fill_text(page, new_fields, answers, ca, profile, jid, state)
            filled += text_filled2
            unfilled.extend(text_unfilled2)
            ps = ps2

    # Re-check unfilled selects — options may have populated after cascading fill (Country → State)
    select_unfilled = [f for f in unfilled if f.get("tag") == "SELECT"]
    if select_unfilled:
        time.sleep(0.3)
        ps3 = read_page(page)
        filled_any = False
        for cf in select_unfilled:
            match = next((f3 for f3 in ps3.get("fields", []) if f3.get("tag") == "SELECT" and f3.get("label") == cf.get("label")), None)
            if match and len(match.get("options", [])) > len(cf.get("options", []) or []):
                nf, nu = _fill_text(page, [match], answers, ca, profile, jid, state)
                if nf:
                    filled_any = True
                    filled += nf
                    unfilled = [u for u in unfilled if u.get("label") != cf.get("label")]
        if filled_any:
            ps = ps3

    read_and_save(page, state)

    # Save new answers to profile common_answers for future use
    if filled > 0 and answers:
        for label, val in answers.items():
            _save_answer(label, val, profile_path)

    page_num = state.get('_page', '?')
    emit_fill_report(filled, unfilled, page_num, profile if unfilled else None)

    btns = ps.get("buttons", [])
    has_submit = any(b["text"].lower() in ("submit", "submit application", "apply", "send application") and not b["disabled"] for b in btns)
    has_next = any(b["text"].lower() in ("next", "review", "continue", "done") and not b["disabled"] for b in btns)
    if unfilled:
        emit_next('act --fill --answers \'{"<question>": "<answer>"}\'')
    elif has_submit:
        emit_next("act --submit")
    elif has_next:
        emit_next("act --next")
    elif not unfilled and not has_submit and not has_next:
        # All fields filled, no buttons — possible auto-submit
        page_text = (page.evaluate("() => document.body.innerText") or "").lower()
        if any(w in page_text for w in ["thank you", "submitted", "your application", "has been sent"]):
            emit_status("submitted", "auto-submit without clicking")
            emit_next("verify")
        else:
            emit_status("unknown", "all fields filled but no buttons")
            emit_next("verify")

def cmd_next(jid, candidate=None):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    if _check_already_submitted(state, jid):
        return
    b, ctx = connect()
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
        emit_next("retry after solving CAPTCHA"); return

    advance_kws = ["next", "continue", "review", "done", "submit", "submit application"]
    all_candidates = scan_actions(page, advance_kws, _EXCLUDED_BUTTONS)

    # If candidate was specified, click it directly
    if candidate is not None:
        if candidate < len(all_candidates):
            c = all_candidates[candidate]
            _click_candidate(page, c, state)
            ps2 = read_page(page)
            _handle_post_click(state, ps2, page)
        else:
            print(f"ERROR: candidate {candidate} out of range (0-{len(all_candidates)-1})", file=sys.stderr)
        return

    candidates = [c for c in all_candidates if not c.get("disabled")]
    print("CANDIDATES:", file=sys.stderr)
    for i, c in enumerate(candidates[:8]):
        print(f"  [{i}] '{c['text'][:40]}' score={c.get('score','?')}", file=sys.stderr)

    target = None
    if candidates:
        best = ButtonIntentClassifier.pick(candidates, "submit")
        if not best:
            best = ButtonIntentClassifier.pick(candidates, "advance")
        if best:
            target = candidates[best["index"]]
    if not target and candidates and candidates[0]["score"] >= 4:
        target = candidates[0]

    if not target and candidates:
        print("CHOOSE: act --next <jid> --candidate N", file=sys.stderr)
        emit_next("model_choice")
        save_state(state); return
    elif not target:
        # Check if there are disabled advance buttons
        for c in scan_actions(page, advance_kws):
            if c.get("disabled"):
                print(f"BUTTON_DISABLED: {c['text']} — fill required fields first", file=sys.stderr)
                emit_next("act --fill"); return
        print("NO_BUTTON", file=sys.stderr)
        emit_next("none"); return

    print(f"ACTION: {target['text']}", file=sys.stderr)
    _click_candidate(page, target, state)
    ps2 = read_page(page)
    if not _handle_post_click(state, ps2, page):
        has_submit = any(b["text"].lower() in ("submit", "submit application", "apply", "send application") and not b["disabled"] for b in ps2.get("buttons",[]))
        emit_next("act --submit" if has_submit else "act --fill")

def cmd_back(jid):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    if _check_already_submitted(state, jid):
        return
    b, ctx = connect()
    page = PageManager(ctx, jid).find(fallback_url=state.get("external_url", ""))[0]
    if not page: print("ERROR: no page found", file=sys.stderr); return
    before = _page_hash(page)
    page.evaluate("""() => { const c = document.querySelector('[role="dialog"]') || document; c.querySelectorAll('button').forEach(b => { if ((b.textContent||'').trim().toLowerCase() === 'back' && !b.disabled) b.click(); }); }""")
    _wait_for_change(page, before)
    ps = read_page(page)
    state["page"] = ps
    save_state(state)
    if _DEBUG:
        print(f"PAGE: {json.dumps(ps)}", file=sys.stderr)
    emit_next("act --fill")

def cmd_submit(jid, confirm=False, candidate=None):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first", file=sys.stderr); return
    if _check_already_submitted(state, jid):
        return

    b, ctx = connect()
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
            print("ERROR: no page found and no external URL", file=sys.stderr)
            emit_next("detect or navigate first")
            return

    if handle_captcha(page, state):
        emit_next("retry after solving CAPTCHA"); return

    # Capture alert dialogs (validation errors, success alerts)
    _alerts = []
    page.on("dialog", lambda d: (_alerts.append(d.message), d.accept()))

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
        print("CANDIDATES:", file=sys.stderr)
        for i, c in enumerate(cands[:8]):
            print(f"  [{i}] '{c['text'][:40]}' score={c.get('score','?')}", file=sys.stderr)
        print("CHOOSE: act --submit <jid> --candidate N", file=sys.stderr)
        emit_next("model_choice"); return
    else:
        print("NO_SUBMIT_BUTTON", file=sys.stderr)
        emit_next("none"); return
    if not confirm:
        url = state.get("external_url", "") or state.get("url", "")
        url_short = url.split("?")[0][:80] if url else "?"
        plat = state.get("platform", "") or _domain(url) or "unknown"
        filled = state.get("filled", 0)
        ps = state.get("page", {})
        unfilled_fields = [f for f in ps.get("fields", []) if f.get("required") and not f.get("value")]
        last = state.get("_last_submit", "")
        warn = ""
        if last == "validation_error":
            warn = " (last submit had validation errors)"
        elif last == "captcha":
            warn = " (CAPTCHA was triggered last time)"
        elif last == "unknown":
            warn = " (last submit was not confirmed)"
        print(f"SUBMIT: {plat} — {filled} filled, {len(unfilled_fields)} unfilled{warn}", file=sys.stderr)
        for f in unfilled_fields[:3]:
            print(f"  Unfilled: {f.get('label', '?')}", file=sys.stderr)
        if len(unfilled_fields) > 3:
            print(f"  ... and {len(unfilled_fields)-3} more", file=sys.stderr)
        print(f"  URL: {url_short}", file=sys.stderr)
        print("  Pass --confirm to submit, or investigate first.", file=sys.stderr)
        emit_next("act --submit --confirm"); return
    print(f"SUBMIT: {target['text']}\nDISABLED: {target.get('disabled', False)}", file=sys.stderr)

    before_hash = _page_hash(page)
    b_loc = page.locator(f'button:has-text("{target["text"]}")')
    if b_loc.count() == 0:
        print(f"  Submit warning: button '{target['text']}' not found on page", file=sys.stderr)
    else:
        try:
            b_loc.first.click(timeout=5000)
        except Exception as e:
            print(f"  Submit warning: click failed — {e}", file=sys.stderr)
    # Wait for either a page transition OR visible error text
    for _ in range(30):
        time.sleep(0.5)
        current = _page_hash(page)
        current_text = (page.evaluate("() => document.body.innerText") or "").lower()
        if current != before_hash or \
           any(w in current_text for w in ["error", "required", "invalid"]):
            break

    # Check for CAPTCHA triggered by submission
    if handle_captcha(page, state):
        print("*** Solve the CAPTCHA above, then retry submit ***", file=sys.stderr)
        state["_last_submit"] = "captcha"
        save_state(state)
        emit_next("act --submit --confirm")
        return

    text = (page.evaluate("() => document.body.innerText") or "").lower()
    # Include alert dialog messages in error/success detection
    for msg in _alerts:
        text += " " + msg.lower()
    # Check for success signals first (handles AJAX submit where form stays visible)
    for signal in ["thank you", "submitted", "your application", "has been sent", "application received"]:
        if signal in text:
            get_conn().execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid)).connection.commit()
            state["_last_submit"] = ""
            save_state(state)
            emit_status("submitted (via AJAX)")
            emit_next("verify")
            return

    has_form = page.evaluate("""() => {
        const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        return inputs.length > 0 && Array.from(inputs).some(i => i.offsetParent !== null);
    }""") or False
    has_error = any(w in text for w in ["error", "required", "invalid", "correct the"])
    if has_error and has_form:
        state["_last_submit"] = "validation_error"
        save_state(state)
        emit_status("validation_errors", "form still present")
        emit_next("act --fill")
    elif not page.evaluate("() => document.querySelector('[role=\"dialog\"]')") and not has_form:
        get_conn().execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid)).connection.commit()
        state["_last_submit"] = ""
        save_state(state)
        emit_status("submitted")
        emit_next("verify")
    else:
        state["_last_submit"] = "unknown"
        save_state(state)
        emit_status("unknown", "page unchanged or not submitted")
        emit_next("verify")

def cmd_inspect(jid, candidate=None):
    """Analyze the job page: dump fields, buttons, probe result, screenshot.
    No fill, no submit — pure analysis. Use when stuck with NEXT: act --inspect."""
    state = load_state()
    if state.get("jid") != jid:
        emit_error(f"state is for job {state.get('jid','?')}, not {jid}")
        print("  Run detect first.", file=sys.stderr); return

    b, ctx = connect()
    pm = PageManager(ctx, jid)
    ext = state.get("external_url", "")
    page, score, candidates = pm.find(fallback_url=ext)

    # Show all open pages for context
    print(f"Open pages ({len(ctx.pages)}):", file=sys.stderr)
    for i, p in enumerate(ctx.pages):
        url = p.url[:100]
        match = " [MATCH]" if p == page else ""
        print(f"  [{i}] {url}{match}", file=sys.stderr)

    if not page:
        if candidate is not None and candidate < len(ctx.pages):
            page = ctx.pages[candidate]
            print(f"Picked page [{candidate}]: {page.url[:100]}", file=sys.stderr)
        else:
            emit_warn(f"no page matches job {jid}")
            print(f"  Wanted: {ext[:100] if ext else '?'}", file=sys.stderr)
            if ctx.pages:
                print(f"  Use --inspect --candidate N to pick one.", file=sys.stderr)
                emit_next("model_choice")
            else:
                emit_next("none")
            return

    if _DEBUG:
        import pathlib
        ss_dir = pathlib.Path.home() / ".openclaw"
        ss_dir.mkdir(exist_ok=True)
        ss = page.screenshot(path=str(ss_dir / f"inspect_{jid}_{int(time.time())}.png"))
        print(f"Screenshot: {ss}", file=sys.stderr)

    # Page info
    print(f"URL: {page.url}", file=sys.stderr)
    print(f"Title: {page.title() or '?'}", file=sys.stderr)
    print(f"Platform: {state.get('platform', '?')}", file=sys.stderr)
    print(f"Filled: {state.get('filled', 0)} fields", file=sys.stderr)

    # Run probe (short-circuits via YAML best_strategy if configured)
    ps = read_page(page)
    domain = _domain(page.url)
    registry = resolve_registry(page.url)
    best = probe_page(page, domain=domain, registry_config=registry)
    if best and best.field_count > 0:
        ps = best.to_dict()
        print(f"Probe: {best.strategy} ({best.field_count} fields)", file=sys.stderr)
    else:
        # Full diagnostic probe_all on failure
        best, all_results = probe_all(page, domain=domain, registry_config=registry)
        if best and best.field_count > 0:
            ps = best.to_dict()
            print(f"Probe results ({len(all_results)} strategies):", file=sys.stderr)
            for r in all_results:
                if r.field_count > 0 or r is best:
                    marker = " [BEST]" if r is best else ""
                    print(f"  {r.strategy}: {r.field_count} fields{marker}", file=sys.stderr)
        else:
            print("Probe: all strategies failed", file=sys.stderr)

    fc = ps.get("fieldCount", 0)
    print(f"Fields: {fc}", file=sys.stderr)
    for f in ps.get("fields", []):
        opts = f.get("options", [])
        extra = f" -> {opts[:5]}" if opts else ""
        print(f"  [{f.get('tag','?')}] {f.get('label','?')} req={f.get('required')}{extra}", file=sys.stderr)

    btns = ps.get("buttons", [])
    print(f"Buttons: {len(btns)}", file=sys.stderr)
    for b in btns:
        d = " [DISABLED]" if b.get("disabled") else ""
        print(f"  '{b.get('text','?')}'{d}", file=sys.stderr)

    print(f"Page type: {ps.get('pageType', '?')}", file=sys.stderr)
    print(f"Dialog: {'yes' if page.evaluate('() => !!document.querySelector(\"[role=dialog], dialog\")') else 'no'}", file=sys.stderr)

    if fc > 0:
        emit_next("act --fill")
    else:
        # Show page text so SLM can identify login walls, blank pages, errors, etc.
        raw = (page.evaluate("() => document.body.innerText") or "")[:500]
        if raw.strip():
            print(f"Page text (first 500 chars):", file=sys.stderr)
            for line in raw.split("\n")[:10]:
                if line.strip():
                    print(f"  {line.strip()[:120]}", file=sys.stderr)
        else:
            print("Page text: empty — page may be blank or not loaded.", file=sys.stderr)
        emit_next("none")

def run(args):
    global _DEBUG
    _DEBUG = getattr(args, 'debug', False)
    if args.fill: cmd_fill(args.jid, args.answers, args.candidate)
    elif args.next: cmd_next(args.jid, args.candidate)
    elif args.back: cmd_back(args.jid)
    elif args.submit: cmd_submit(args.jid, args.confirm, args.candidate)
    elif args.inspect: cmd_inspect(args.jid, args.candidate)
    else: print("ERROR: specify --fill, --next, --back, --submit, or --inspect", file=sys.stderr)
