#!/usr/bin/env python3
"""act.py — One action per call: fill, next, back, submit.
Always reads fresh state, verifies before/after, prints structured output.
"""
import json, os, sys, re, time, random
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.page_helpers import (
    load_state,
    save_state,
    read_page,
    handle_captcha,
    handle_session_timeout,
    page_text,
    scan_actions,
    read_and_save,
    mark_applied,
    DEFAULT_EXCLUDED_BUTTONS as _EXCLUDED_BUTTONS,
)
from apply.common.signals import SUCCESS_BROAD, has_success_text
from apply.common.registry import resolve as resolve_registry
from apply.common.apply_state import init as _as_init, record_fill as _as_record, advance_page as _as_advance
from apply.common.inspector import probe as probe_page
from apply.common.learner import ButtonIntentClassifier
from apply.common.field_reader import scan_errors
from apply.common.output import (
    emit_next,
    emit_status,
    emit_warn,
    emit_fill_report,
    emit_candidates,
)
from apply.common.page_manager import PageManager
from apply.common.platforms import check_page, LOGIN_WALL, GUEST_APPLY
from apply.common.resolve import resolution_for_fill
from apply.common.policy import resolve_mode, submits_for_real
from apply.common import audit
from apply.steps.probe import run as probe_fields

from lib.config import PROFILE_PATH as profile_path


def _domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


# Typo-detection set for profile.json (warns on unrecognized keys). NOT the same
# as what gets auto-resolved: deterministic resolution uses the string-valued
# subset in resolve._PROFILE_KEYS (+ the profile["answers"] map + name/location
# derivations). Booleans like authorized_to_work are valid keys but are resolved
# via the mapping layer (ADR-001 Phase 3), not by exact label match.
_KNOWN_PROFILE_KEYS = {
    "first_name", "last_name", "email", "phone",
    "linkedin_url", "github_url", "portfolio_url", "website",
    "address", "city", "state", "zip", "country",
    "authorized_to_work", "visa_status", "requires_sponsorship",
    "expected_salary", "salary_currency",
    "work_preference", "remote_preference", "start_date", "pronouns",
    "common_answers", "answers",
    "resume_path", "location",
}


def _derive_provenance(label, answers, profile):
    """Determine the provenance of a filled value for display in submit preview."""
    from apply.common.resolve import normalize as _norm, resolution_for_fill
    res = resolution_for_fill(label, profile, answers_override=answers)
    if res.value is not None and res.provenance != "no_match":
        return res.provenance
    # Check if this was auto-declined as EEO (common_answers is normalized to dict by _validate_profile)
    for k, v in profile.get("common_answers", {}).get("eeo", {}).items():
        if _norm(k) == _norm(label) and v:
            return "profile_eeo"
    return "answers"


def _reconcile_fields(state, jid):
    """Compare field values across pages and return mismatches list."""
    history = state.get("_field_values_history", [])
    if len(history) < 2:
        return []
    from apply.common.resolve import normalize as _norm
    by_label = {}
    for entry in history:
        nl = _norm(entry.get("label", ""))
        by_label.setdefault(nl, []).append(entry)
    mismatches = []
    for nl, entries in by_label.items():
        vals = set(e["value"] for e in entries)
        if len(vals) > 1:
            pages = [(e["page"], e["value"]) for e in entries]
            mismatches.append({"label": entries[0]["label"], "pages": pages})
    return mismatches


def _validate_profile(profile):
    """Normalize profile types and warn about unrecognized keys."""
    # Normalize dict-valued fields at the boundary so downstream code never
    # has to guard against unexpected types.
    for _key in ("answers", "common_answers"):
        if _key in profile and not isinstance(profile[_key], dict):
            print(f"WARN: profile.{_key} should be a dict, got {type(profile[_key]).__name__} — resetting to empty dict", file=sys.stderr)
            profile[_key] = {}
    unknown = set(profile.keys()) - _KNOWN_PROFILE_KEYS
    if unknown:
        print(
            f"WARN: profile.json has unrecognized keys: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )
        print(
            f"  Known keys: {', '.join(sorted(_KNOWN_PROFILE_KEYS))}", file=sys.stderr
        )


def _find_answer(label, answers, profile, field=None):
    """Find answer via resolve chain (--answers override → profile facts →
    confirmed mapping). The mapping step is a no-op unless policy.use_mappings."""
    res = resolution_for_fill(label, profile, answers_override=answers)
    if res.value is not None:
        return res.value
    if field is not None:
        from apply.common import mappings
        m = mappings.resolve_field(field, profile)
        if m:
            return m[0]
    return None


def _page_hash(page):
    """Stable hash of form-relevant content — field count + first 5 button texts."""
    try:
        return page.evaluate(
            """() => {
            const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
            const btns = document.querySelectorAll('button, [role="button"]');
            const btnTexts = Array.from(btns).filter(b => b.offsetParent).map(b => b.textContent.trim()).slice(0, 5).join('|');
            return inputs.length + ':' + btnTexts;
        }"""
        )
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
        print(
            f"SKIP: '{c['text']}' is disabled — fill required fields first",
            file=sys.stderr,
        )
        return
    if c["tag"] == "A" and c.get("href"):
        page.goto(c["href"], wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
        # If goto resulted in 0 fields and no forms, try click instead (SPA)
        _ps = read_page(page)
        if _ps["fieldCount"] == 0 and not page.evaluate(
            "() => document.querySelectorAll('form').length"
        ):
            before_spa = _page_hash(page)
            page.evaluate(
                f"""(txt) => {{
                const all = document.querySelectorAll('a');
                for (const el of all) {{
                    if (el.offsetParent === null) continue;
                    if ((el.textContent || '').trim().toLowerCase() === txt) {{ el.click()
    return true; }}
                }}
                return false;
            }}""",
                c["text"],
            )
            _wait_for_change(page, before_spa)
    else:
        clicked = page.evaluate(
            f"""(txt) => {{
            const all = document.querySelectorAll('button');
            for (const el of all) {{
                if (el.offsetParent === null) continue;
                if ((el.textContent || '').trim().toLowerCase() === txt) {{ el.click()
    return true; }}
            }}
            return false;
        }}""",
            c["text"],
        )
        if not clicked:
            # Fallback: Playwright locator with more flexible text matching
            try:
                loc = page.locator(f'button:has-text("{c["text"]}"), [role="button"]:has-text("{c["text"]}")')
                if loc.count() > 0:
                    loc.first.click(force=True, timeout=5000)
                else:
                    print(
                        f"  Click warning: button '{c['text']}' not found via JS or locator",
                        file=sys.stderr,
                    )
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
        has_submit_btn = any(
            b["text"].lower()
            in (
                "submit",
                "submit application",
                "apply",
                "send",
                "review",
                "review application",
            )
            and not b.get("disabled")
            for b in (ps.get("buttons") or [])
        )
        if has_submit_btn:
            return False
        text = page_text(page) or ""
        if has_success_text(text):
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
    error_btns = [
        b for b in (ps.get("buttons") or []) if "error" in b.get("text", "").lower()
    ]
    if error_btns:
        print(f"ERRORS: {json.dumps([b['text'] for b in error_btns])}", file=sys.stderr)
        cands = scan_actions(
            page, ["save and continue", "next", "continue", "review", "submit"]
        )
        emit_candidates(cands)
        emit_next("model_choice", "fix errors or skip")
        state["result"] = "validation_error"
        save_state(state)
        return True
    return False


def _redirect_submit_intent(cand, state):
    """Structural invariant: only cmd_submit clicks submit-intent buttons — that is
    where the gate, --confirm, and audit live. Returns True if redirected."""
    intent, _ = ButtonIntentClassifier.classify(cand.get("text", ""))
    if intent != "submit":
        return False
    print(
        f"SUBMIT_REDIRECT: '{cand.get('text', '?')}' is a submit action — routed through act --submit",
        file=sys.stderr,
    )
    save_state(state)
    emit_next("act --submit")
    return True


def _fill_radios(page, fields, answers, profile, jid):
    """Fill radio groups. Returns filled count + unfilled list."""
    filled = 0
    unfilled = []

    def _radio_group_key(rf, idx):
        k = rf.get("name")
        if k:
            return k
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

    radios = [f for f in fields if f.get("type") == "radio"]
    groups = {}
    for idx, rf in enumerate(radios):
        gk = _radio_group_key(rf, idx)
        groups.setdefault(gk, []).append(rf)

    def _check_radio(rf):
        """Check a radio element by id or name+value. Returns True if the radio
        ends up checked — an already-checked target counts (idempotent success)."""
        try:
            if rf.get("id"):
                el = page.locator(f'id={rf["id"]}')
                if el.count() > 0:
                    if not el.first.is_checked():
                        el.first.check()
                    return True
            if rf.get("name"):
                selector = f'input[type="radio"][name="{rf["name"]}"]'
                rv = rf.get("value") or ""
                if rv:
                    selector += f'[value="{rv}"]'
                el = page.query_selector(selector)
                if el:
                    if not el.is_checked():
                        el.check()
                    return True
        except Exception:
            pass
        return False

    for gk, group in groups.items():
        opts = [rf["label"] for rf in group]
        q_label = opts[0].split(" - ")[0] if " - " in opts[0] else opts[0]

        ans = _find_answer(q_label, answers, profile,
                           field={"label": q_label, "options": opts, "tag": "radio"})
        if ans:
            ans_lower = ans.lower()
            matched = False
            # 1. Match by option_label (exact match only — defers to LLM for fuzzy cases)
            for rf in group:
                ol = (rf.get("option_label", "") or "").lower()
                if ol and ans_lower == ol:
                    if _check_radio(rf):
                        filled += 1
                        matched = True
                        break
            if not matched:
                # 2. Match by value attribute (exact match only)
                for rf in group:
                    rv = (rf.get("value", "") or "").lower()
                    if rv and ans_lower == rv:
                        if _check_radio(rf):
                            filled += 1
                            matched = True
                            break
            if not matched:
                # Answer exists but matched no option — surface it, never drop it
                # silently (the submit path trusts `unfilled` to be complete).
                print(
                    f"  RADIO_MISMATCH: '{ans[:40]}' matched no option of {q_label[:50]}",
                    file=sys.stderr,
                )
                unfilled.append(
                    {"label": q_label[:60], "options": opts, "tag": "radio_group", "field": group}
                )
        else:
            unfilled.append(
                {"label": q_label[:60], "options": opts, "tag": "radio_group", "field": group}
            )
    return filled, unfilled


def _fill_file_upload(page, f, results_dir, jid, state):
    """Upload resume PDF to a file input. Returns 'skip', 'filled', 'unfilled', or None."""
    if not os.path.isdir(results_dir) or not any("Resume" in fn and fn.endswith(".pdf") for fn in os.listdir(results_dir)):
        return "unfilled"
    candidates = []
    for fn in os.listdir(results_dir):
        if "Resume" in fn and fn.lower().endswith(".pdf"):
            score = 0
            if (state.get("title") or "").split(" ")[0].lower() in fn.lower():
                score += 2
            if state.get("company", "").lower() in fn.lower():
                score += 1
            candidates.append((score, fn))
    candidates.sort(key=lambda x: -x[0])
    if not candidates:
        return None
    pdf_path = os.path.join(results_dir, candidates[0][1])
    try:
        if os.path.getsize(pdf_path) < 512:
            print(f"WARN: {candidates[0][1]} is {os.path.getsize(pdf_path)} bytes — skipping empty PDF", file=sys.stderr)
            return "unfilled"
    except OSError:
        return None
    try:
        resume_inputs = [fi for fi in page.query_selector_all('input[type="file"]') if "resume" in (page.evaluate(f'(el) => el.closest("div,fieldset,section")?.textContent || ""', fi) or "").lower()]
        fi = resume_inputs[0] if resume_inputs else page.query_selector('input[type="file"]')
        if fi:
            fi.set_input_files(pdf_path)
            return "filled"
    except Exception:
        pass
    return None


def _try_drag_drop(page, results_dir):
    """Fallback: drag-and-drop zone with no visible file input."""
    candidates = [fn for fn in os.listdir(results_dir) if "Resume" in fn and fn.lower().endswith(".pdf")]
    if not candidates:
        return False
    pdf_path = os.path.join(results_dir, candidates[0])
    import base64
    with open(pdf_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    data_url = f"data:application/pdf;base64,{b64}"
    try:
        return page.evaluate(f"""(dataUrl) => {{
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
    except Exception:
        return False


def _fill_field_deterministic(page, f, ans):
    """Dispatch to canonical strategy module."""
    from apply.strategies import field_deterministic as _fd
    return _fd(page, f, ans)


def _fill_text(page, fields, answers, profile, jid, state):
    """Fill text/select/textarea fields. Returns filled count + unfilled list."""
    from apply.common.policy import load_policy
    from apply.common.validate import validate_value
    _enforce_validation = bool(load_policy().get("enforce_validation", False))
    filled = 0
    unfilled = []
    file_uploaded = False
    _RESUME_DIR = None
    _seen_labels = set()
    _conditional_depth = 0

    for f in fields:
        prev_filled = filled
        if f.get("type") == "radio":
            continue

        # Mid-fill guard: check session timeout (long fills may expire)
        try:
            from apply.common.page_helpers import handle_session_timeout
            handle_session_timeout(page)
        except Exception:
            pass

        # File upload (resume PDF)
        if f.get("tag") == "INPUT" and f.get("type") == "file":
            lbl_lower = (f.get("label", "") or "").lower()
            if file_uploaded and not f.get("required", False):
                if "cover" not in lbl_lower and "letter" not in lbl_lower and "discovery" not in lbl_lower:
                    continue
            if _RESUME_DIR is None:
                from lib.config import RESULTS_DIR as _RD
                _RESUME_DIR = os.path.join(_RD, jid)
            res = _fill_file_upload(page, f, _RESUME_DIR, jid, state)
            if res == "skip":
                continue
            elif res == "filled":
                file_uploaded = True
                filled += 1
                continue
            elif res == "unfilled":
                if f.get("required"):
                    unfilled.append({"label": "Resume Upload", "options": [], "tag": "FILE"})
                continue
            if not file_uploaded:
                if _try_drag_drop(page, _RESUME_DIR):
                    file_uploaded = True
                    filled += 1
            continue

        # Standard field
        lbl = f["label"]
        _seen_labels.add(lbl)

        # Skip pre-filled fields with valid data (any non-empty value is filled,
        # even if display text differs from answer — widget may translate codes)
        current_val = f.get("value", "")
        current_stripped = current_val.strip()
        # Use isEmpty flag from field_reader, fallback to manual check
        is_empty = f.get("isEmpty", current_stripped.lower() in ("no selection", "select one", "select...", "select an option", ""))
        if is_empty:
            pass
        elif current_stripped and len(current_stripped) > 1:
            # For required fields, still check if answer contradicts the current value
            if f.get("required"):
                ans_check = _find_answer(lbl, answers, profile, field=f)
                if ans_check:
                    cw = current_stripped.lower().split()
                    aw = ans_check.lower().split()
                    if not any(w in cw for w in aw):
                        pass  # will overwrite below
                    else:
                        continue  # already matches closely enough
                else:
                    continue  # no better answer — keep existing value
            else:
                continue

        ans = _find_answer(lbl, answers, profile, field=f)
        if ans is None:
            if f.get("required"):
                unfilled.append({"label": lbl[:60], "options": f.get("options", []), "tag": f.get("tag", "?"), "field": f})
            continue

        # Phase 4: escalate values that fail validation instead of filling them.
        if _enforce_validation:
            ok_v, _vr = validate_value(f, ans)
            if not ok_v:
                print(f"  VALIDATION_SKIP: {lbl[:50]} -> {str(ans)[:30]} ({_vr})", file=sys.stderr)
                if f.get("required"):
                    unfilled.append({"label": lbl[:60], "options": f.get("options", []), "tag": f.get("tag", "?"), "field": f, "attempted": ans})
                continue

        if _fill_field_deterministic(page, f, ans):
            filled += 1
            # Track field values across pages — both current (for fill-time detection)
            # and history (for submit-time reconciliation)
            from apply.common.resolve import normalize as _norm
            _norm_label = _norm(lbl)
            _pv = state.setdefault("_field_values", {})
            if _norm_label in _pv and _norm(_pv[_norm_label]) != _norm(str(ans)):
                print(f"  PAGE_INCONSISTENCY: '{lbl[:40]}' was '{_pv[_norm_label][:30]}' on a previous page, now '{str(ans)[:30]}'", file=sys.stderr)
            _pv[_norm_label] = str(ans)
            _history = state.setdefault("_field_values_history", [])
            _page_now = state.get("_page", "?")
            _existing = next((e for e in _history if e["page"] == _page_now and e["label"] == lbl), None)
            if _existing:
                _existing["value"] = str(ans)
            else:
                _history.append({"page": _page_now, "label": lbl, "value": str(ans)})
            # After filling a field, check for new conditional fields (max 3 levels)
            if _conditional_depth < 3:
                try:
                    _ps = read_page(page)
                    _new = [nf for nf in _ps.get("fields", []) if nf.get("required") and nf.get("label", "") not in _seen_labels]
                    if _new:
                        _conditional_depth += 1
                        fields.extend(f for f in _new if f not in fields)
                        _seen_labels.update(nf.get("label", "") for nf in _new)
                except Exception:
                    pass
        elif f.get("required"):
            unfilled.append({"label": lbl[:60], "options": f.get("options", []), "tag": f.get("tag", "?"), "field": f, "attempted": ans})

        if filled > prev_filled:
            time.sleep(random.uniform(0.15, 0.4))

    return filled, unfilled


def _check_already_submitted(state, jid):
    """Check DB stage and return True if already applied. Prevents re-filling."""
    from lib.db import get_conn

    r = get_conn().execute("SELECT stage, state FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        return False
    if r["state"] != "active":
        print(f"ERROR: job {jid} is in state '{r['state']}', not active", file=sys.stderr)
        emit_next("none")
        return True
    if r["stage"] == "applied":
        print(f"ALREADY: job {jid} is already applied (stage=applied)", file=sys.stderr)
        emit_next("none")
        return True
    return False


def _audit_fill(jid, fields, answers, profile, page_num, platform="", corrected_labels=()):
    """Read-only audit pass: for each detected field, record the resolved tier
    (value + provenance + category) and whether it ended up filled. Decoupled
    from the fill mutation so it cannot affect form state. Also feeds the mapping
    learner (no-op unless policy.use_mappings)."""
    from apply.common.validate import validate_value
    from apply.common import mappings
    corrected = set(corrected_labels or ())
    for f in fields:
        lbl = f.get("label", "")
        if not lbl:
            continue
        if f.get("type") == "file":
            cur = (f.get("value", "") or "").strip()
            audit.log_field(jid, lbl, "<resume PDF>" if cur else "", provenance="file",
                            category="generic", filled=bool(cur), page=page_num)
            continue
        res = resolution_for_fill(lbl, profile, answers_override=answers)
        value, provenance = res.value, res.provenance
        if value is None:
            # Reflect a mapping-store fill too (read-only — don't bump hit_count here).
            m = mappings.resolve_field(f, profile, bump=False)
            if m:
                value, provenance = m
        cur = (f.get("value", "") or "").strip()
        validated = None
        if value:
            validated, _reason = validate_value(f, value)
        audit.log_field(jid, lbl, value or cur, provenance=provenance,
                        category=audit.categorize(lbl, f.get("options"), f.get("tag")),
                        filled=bool(cur), validated=validated, page=page_num)
        # Learn: record a pending mapping for explicitly-answered fields.
        if value:
            mappings.learn(jid, f, value, provenance, profile,
                           platform=platform, corrected=(lbl in corrected))


def cmd_fill(jid, answers_json=None, candidate=None, dry_run=None, shadow=False):
    dry_run = True if dry_run is None else dry_run  # default: preview only
    answers = {}
    if answers_json:
        if answers_json.startswith("@"):
            try:
                with open(answers_json[1:], encoding="utf-8") as f:
                    answers = json.load(f)
            except Exception as e:
                print(f"ERROR: could not read answers file: {e}", file=sys.stderr)
        else:
            try:
                answers = json.loads(answers_json)
            except Exception:
                print("ERROR: --answers must be valid JSON or @file.json", file=sys.stderr)

    # Normalize --answers values to strings at the boundary so downstream
    # fill strategies never receive unexpected types (ints, lists, etc.)
    if answers:
        _normed = {}
        for _k, _v in answers.items():
            if _v is None:
                continue
            if isinstance(_v, list):
                _normed[_k] = [str(_x) for _x in _v]
            else:
                _normed[_k] = str(_v)
        answers = _normed

    state = load_state()
    if state.get("jid") != jid:
        print(
            f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first",
            file=sys.stderr,
        )
        return
    if _check_already_submitted(state, jid):
        return
    if not dry_run and not state.get("_dry_run_done"):
        print("ERROR: must dry-run first — run act --fill <jid> (without --apply) to preview field mappings", file=sys.stderr)
        print("  Dry-run is read-only — shows what values would be filled without touching the DOM.", file=sys.stderr)
        emit_next("act --fill")
        return
    b, ctx = connect()
    pm = PageManager(ctx, jid)
    pm.close_stale(target_url=state.get("external_url", ""))
    ext = state.get("external_url", "")
    page, _, _ = pm.find(fallback_url=ext)
    # Wire network interception for submit detection (Playwright route,
    # not JS monkeypatch — no fingerprinting risk)
    if page:
        try:
            from apply.common.agent_bridge import setup_network_interception
            setup_network_interception(page)
        except Exception:
            pass
    if not page:
        # Reuse existing page matching external URL
        if ext:
            for pg in ctx.pages:
                if ext.rstrip("/") in pg.url.rstrip("/") or pg.url.rstrip("/") in ext.rstrip("/"):
                    page = pg
                    pm.register(page)
                    break
        if not page:
            if ctx.pages:
                print("NO_MATCH: no page matches. Open pages:", file=sys.stderr)
                for i, p in enumerate(ctx.pages):
                    print(f"  [{i}] {p.url[:100]}", file=sys.stderr)
                if ext:
                    print(f"WANTED: {ext[:100]}", file=sys.stderr)
                print(
                    "TIP: open the job URL in Chrome to check, then retry", file=sys.stderr
                )
                emit_next("act --inspect")
                return
            elif ext:
                page = ctx.new_page()
                page.goto(ext, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                pm.register(page)
            else:
                print("ERROR: no page found and no external URL", file=sys.stderr)
                return
    handle_session_timeout(page)
    if handle_captcha(page, state):
        emit_next("retry after solving CAPTCHA")
        return

    # LinkedIn Easy Apply: re-open modal if it closed (ephemeral overlay)
    has_detect = state.get("_detect_fields", {}).get("fieldCount", 0) > 0
    is_linkedin = "linkedin.com" in page.url.lower()
    if is_linkedin and has_detect:
        dialog_open = page.evaluate("() => !!document.querySelector('[role=dialog], dialog')")
        if not dialog_open:
            print("EASY_APPLY: modal closed, re-opening...", file=sys.stderr)
            easy_apply = page.evaluate("""() => {
                const all = document.querySelectorAll('button, a');
                for (const el of all) {
                    if (el.offsetParent === null) continue;
                    const t = (el.textContent || '').trim().toLowerCase();
                    if (t === 'easy apply' || t.startsWith('easy apply')) {
                        el.click()
    return true;
                    }
                }
                return false;
            }""")
            if easy_apply:
                time.sleep(2)
                try:
                    page.wait_for_selector('[role="dialog"], dialog', timeout=8000)
                except Exception:
                    pass
            else:
                print("EASY_APPLY: re-open failed, trying URL navigate", file=sys.stderr)
                job_id_match = re.search(r"/jobs/view/(\d+)", page.url)
                if job_id_match:
                    page.goto(
                        f"https://www.linkedin.com/jobs/view/{job_id_match.group(1)}/apply/?openSDUIApplyFlow=true",
                        wait_until="domcontentloaded", timeout=30000,
                    )
                    time.sleep(3)

    profile = {}
    if os.path.exists(profile_path):
        try:
            with open(profile_path) as f:
                profile = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(
                f"WARN: profile.json corrupt or unreadable — using empty profile",
                file=sys.stderr,
            )
            profile = {}
        else:
            _validate_profile(profile)

    # Registry + probe cascade
    domain = _domain(page.url)
    registry = resolve_registry(page.url)
    if registry:
        registry.emit_notes()

    # Initialize/reconcile apply state for crash recovery
    try:
        _as = _as_init(jid, state.get("external_url", ""), state.get("platform", ""))
        if _as:
            _as_advance(jid)
    except Exception:
        pass

    # Probe: try standard first, cascade on failure
    ps = read_page(page)
    # Use agent's MutationObserver-based field detection (no polling).
    # Agent watches DOM for input/select/textarea additions and sets dirty flag.
    if ps.get("fieldCount", 0) == 0:
        try:
            page.wait_for_function(
                "window.__opencode?.getFields().length > 0",
                timeout=8000
            )
            ps = read_page(page)
            print(f"AGENT_WAIT: fields detected via MutationObserver", file=sys.stderr)
        except Exception:
            pass  # timeout — fall through to detect-phase or probe cascade
    # Use detect-phase fields (e.g., GraphQL) if DOM returned nothing
    if (
        ps.get("fieldCount", 0) == 0
        and state.get("_detect_fields", {}).get("fieldCount", 0) > 0
    ):
        ps = state["_detect_fields"]
    if ps.get("fieldCount", 0) == 0 and domain:
        reg_config = registry
        probe_result = probe_page(page, domain=domain, registry_config=reg_config)
        if probe_result.field_count > 0:
            ps = probe_result.to_dict()
        elif probe_result.snapshot_path:
            print(
                f"PROBE_FAILED: snapshot saved to {probe_result.snapshot_path}",
                file=sys.stderr,
            )
    # Also probe if iframes exist — parent DOM may have chrome fields while the real form is in an iframe
    elif ps.get("fieldCount", 0) > 0 and domain and page.evaluate("() => !!document.querySelector('iframe')"):
        from apply.common.inspector import probe_iframes, probe_iframe_navigate
        # Same-origin iframes (fast, no navigation)
        ifr = probe_iframes(page)
        if ifr.field_count > ps.get("fieldCount", 0):
            ps = ifr.to_dict()
        else:
            # Cross-origin iframes: navigate to the iframe URL, read fields, navigate back
            ifr2 = probe_iframe_navigate(page)
            if ifr2.field_count > ps.get("fieldCount", 0):
                ps = ifr2.to_dict()

    # Platform pre-fill hooks (e.g. expand collapsed sections before filling)
    if registry and registry.has_hook("pre_fill"):
        registry.call_hook("pre_fill", page)
        time.sleep(1)
        ps = read_page(page)

    # Upload tailored documents if platform has custom upload widget
    if registry and registry.has_hook("upload_documents") and not ps.get("hasFileInput"):
        registry.call_hook("upload_documents", page, jid)
        time.sleep(1)
        ps = read_page(page)

    # Platform handler (PlatformHandler interface): replaces the entire
    # fill/navigate/submit chain for platforms with ephemeral state.
    handler = registry.get_handler() if registry else None
    if handler:
        from apply.common import gate
        from apply.common.policy import load_policy
        from apply.common.handler_base import run_modal_flow
        mode = resolve_mode("shadow" if shadow else None)
        action, reason = gate.submit_decision(mode, load_policy(), audit.summarize(jid))
        allow_submit = (action == "submit")
        if not allow_submit:
            emit_status(f"submit_{action}", f"handler submit suppressed — {reason}")
        _init_fields = ps.get("fields", [])
        result = run_modal_flow(
            handler, page, jid, profile,
            answers=answers, allow_submit=allow_submit, max_steps=10,
            dry_run=dry_run,
            initial_fields=_init_fields if _init_fields else None,
        )
        if result == "done":
            emit_status("submitted")
            emit_next("verify")
            state["result"] = "submitted"
            save_state(state)
        elif result == "paused":
            if dry_run:
                state["_dry_run_done"] = True
            save_state(state)
        elif result == "failed":
            emit_status("flow_failed", "handler could not proceed")
            emit_next("act --inspect")
        return

    # Legacy flow hook fallback
    if registry and registry.flow_hook and registry.has_hook(registry.flow_hook):
        from apply.common import gate
        from apply.common.policy import load_policy
        mode = resolve_mode("shadow" if shadow else None)
        action, reason = gate.submit_decision(mode, load_policy(), audit.summarize(jid))
        if action != "submit":
            emit_status(f"submit_{action}", f"flow hook submit suppressed — {reason}")
        result = registry.call_hook(
            registry.flow_hook, page, jid,
            profile=profile, answers=answers, allow_submit=(action == "submit"),
        )
        if result == "done":
            emit_status("submitted")
            emit_next("verify")
            state["result"] = "submitted"
            save_state(state)
        elif result == "paused":
            save_state(state)
        elif result == "failed":
            emit_status("flow_failed", "flow hook could not proceed")
            emit_next("act --inspect")
        return

    # Page counter: only bump when the page actually changed. Re-fills of the same
    # page (the normal --answers correction loop) must keep the same page number, or
    # the audit's latest-record-per-(page,label) dedup breaks and a once-invalid
    # field wedges the submit gate forever.
    last_fingerprint = state.get("page_fingerprint", "")
    label_fp = "_".join(f.get("label", "")[:20] for f in ps["fields"][:3])
    current_fingerprint = f"{len(ps['fields'])}:{len(ps.get('buttons',[]))}:{label_fp}"
    if current_fingerprint == last_fingerprint:
        if state.get("filled", 0) > 0:
            print(
                "WARN: page looks unchanged from last fill — verify the form advanced",
                file=sys.stderr,
            )
    else:
        state["_page"] = state.get("_page", 0) + 1
    state["page_fingerprint"] = current_fingerprint

    # If candidate was specified, find and click it
    # (entry-point buttons only — no "submit": clicking submit is cmd_submit's job)
    if candidate is not None and ps["fieldCount"] == 0:
        kws = ["apply", "apply for this job", "apply manually", "apply now"]
        cands = scan_actions(page, kws, _EXCLUDED_BUTTONS)
        if candidate < len(cands):
            c = cands[candidate]
            _click_candidate(page, c, state)
            ps = read_page(page)
            print(
                f"CANDIDATE_CLICK: #{candidate} '{c['text']}' → {ps['fieldCount']} fields",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: candidate {candidate} out of range (0-{len(cands)-1})",
                file=sys.stderr,
            )
            return

    # Check for login wall — only when no form is visible (a fillable form is
    # direct counter-evidence of a wall). Try guest apply first, then abort.
    text = page_text(page) or ""
    plat = state.get("platform", "")
    guest_clicked = False
    if ps.get("fieldCount", 0) == 0 and check_page(text, plat, LOGIN_WALL):
        # Try guest apply buttons
        guest_patterns = GUEST_APPLY.get(plat, []) + GUEST_APPLY["default"]
        for gp in guest_patterns:
            btn = page.evaluate(
                f"""(gp) => {{
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
            }}""",
                gp,
            )
            if btn:
                if btn.get("tag") == "A" and btn.get("href"):
                    page.goto(btn["href"], wait_until="domcontentloaded", timeout=15000)
                else:
                    page.evaluate(
                        f"""(gp) => {{
                        const all = document.querySelectorAll('button');
                        for (const el of all) {{
                            if (el.offsetParent === null) continue;
                            if ((el.textContent || '').trim().toLowerCase() === gp) {{
                                el.click()
    return;
                            }}
                        }}
                    }}""",
                        gp,
                    )
                time.sleep(5)
                ps = read_page(page)
                guest_clicked = True
                print(f"GUEST_APPLY: clicked '{gp}'", file=sys.stderr)
                break
        if not guest_clicked:
            state["page"] = ps
            state["filled"] = 0
            save_state(state)
            emit_status("login_wall", "sign in required — retry after login")
            emit_next("retry after login")
            return

    # If no fields detected, use model-assisted action finding
    if ps["fieldCount"] == 0:
        apply_kws = [
            "apply",
            "apply for this job",
            "apply manually",
            "apply now",
        ]
        candidates = scan_actions(page, apply_kws, _EXCLUDED_BUTTONS)
        emit_candidates(candidates)

        if candidates and candidates[0]["score"] >= 4:
            c = candidates[0]
            _click_candidate(page, c, state)
            ps = read_page(page)
            print(
                f"AUTO_FOLLOW: '{c['text']}' → {ps['fieldCount']} fields",
                file=sys.stderr,
            )
        elif candidates:
            print("CHOOSE: act --fill <jid> --candidate N", file=sys.stderr)
            emit_next("model_choice")
            state["page"] = ps
            state["filled"] = 0
            save_state(state)
            return
        else:
            emit_warn("no fields or buttons found")
            print("  Open Chrome to inspect the page, then retry.", file=sys.stderr)
            emit_next("act --inspect")
            state["page"] = ps
            state["filled"] = 0
            save_state(state)
            return

    # Pass 1: Probe — enrich fields with selectors + combobox options (read-only)
    ps["fields"] = probe_fields(page, ps["fields"])

    from apply.common.output import field_format_hint as _fmt_hint

    # Dry-run: resolve answers without touching DOM, print what would happen
    if dry_run:
        filled = 0
        total = len(ps.get("fields", []))
        auto_fields = []
        need_fields = []
        skip_fields = []
        for f in ps.get("fields", []):
            lbl = f.get("label", "")
            if not lbl:
                continue
            if f["type"] == "file":
                auto_fields.append((f, "<resume PDF>"))
                filled += 1
                continue
            opts = f.get("options", [])
            ans = _find_answer(lbl, answers, profile, field=f)
            if ans:
                if opts and ans not in opts:
                    match = next((o for o in opts if ans.lower() in o.lower()), None)
                    if not match:
                        fmt = _fmt_hint(f)
                        w = f"  fmt={fmt}" if fmt else ""
                        print(f"  WARN: [{f['tag']}] {lbl[:50]} -> {ans[:50]} not in options {opts[:5]}{w}", file=sys.stderr)
                        need_fields.append((f, None, "no match in options"))
                        continue
                auto_fields.append((f, ans))
                filled += 1
            elif f.get("required"):
                need_fields.append((f, None, "required"))
            else:
                skip_fields.append(f)

        print(f"DRY_RUN — {filled}/{total} resolvable:", file=sys.stderr)
        if auto_fields:
            print(f"  ✅ Auto-fill ({len(auto_fields)}):", file=sys.stderr)
            for f, val in auto_fields:
                fmt = _fmt_hint(f)
                w = f"  fmt={fmt}" if fmt else ""
                print(f"    [{f['tag']}] {f.get('label','?')[:50]} -> {str(val)[:50]}{w}", file=sys.stderr)
        if need_fields:
            print(f"  ❓ Needs your input ({len(need_fields)}):", file=sys.stderr)
            for f, _, reason in need_fields:
                opts = f.get("options", [])
                opt_str = json.dumps(opts[:5]) if opts else ""
                fmt = _fmt_hint(f)
                hint = f"  fmt={fmt}" if fmt else ""
                extra = f" -> {opt_str}{hint}" if opt_str else hint
                print(f"    [{f['tag']}] {f.get('label','?')[:50]}{extra}", file=sys.stderr)
        if skip_fields:
            print(f"  ⏭ Skipped ({len(skip_fields)}):", file=sys.stderr)
            for f in skip_fields:
                print(f"    [{f['tag']}] {f.get('label','?')[:50]} (optional, no profile match)", file=sys.stderr)

        state["_dry_run_done"] = True
        save_state(state)
        if need_fields:
            emit_next("act --fill --answers '{\"<label>\": \"<value>\"}'")
        else:
            emit_next("proceed")
        return

    radio_filled, radio_unfilled = _fill_radios(
        page, ps["fields"], answers, profile, jid
    )
    text_filled, text_unfilled = _fill_text(
        page, ps["fields"], answers, profile, jid, state
    )
    filled = radio_filled + text_filled
    unfilled = radio_unfilled + text_unfilled

    # EEO/demographic fields — detect by decline options (language-agnostic).
    # Auto-decline with "Prefer not to say" unless the user provided an explicit
    # answer via profile.common_answers.eeo. The LLM never sees EEO fields as
    # unfilled — eliminates any possibility of stereotyping.
    _DECLINE_SIGNALS = ["prefer not", "decline", "not say", "rather not"]
    eeo_unfilled = [f for f in unfilled if any(
        any(sig in (o or "").lower() for sig in _DECLINE_SIGNALS)
        for o in f.get("options", [])
    )]
    if eeo_unfilled:
        _eeo_answers = profile.get("common_answers", {}).get("eeo", {})
        for ef in eeo_unfilled:
            # Try explicit EEO answer from profile first (user opt-in)
            res = resolution_for_fill(ef["label"], {"answers": _eeo_answers}, answers_override=answers)
            if not res.value:
                # Auto-decline: find "Prefer not to say" style option
                decline_opt = next((o for o in ef.get("options", [])
                                   if any(sig in (o or "").lower() for sig in _DECLINE_SIGNALS)), None)
                if decline_opt:
                    res = Resolution(decline_opt, "auto_decline", ef["label"], "auto_decline")
            if res.value:
                orig = ef.get("field")
                eeo_answers = {ef["label"]: res.value}
                if ef.get("tag") == "radio_group" and orig:
                    nf, _ = _fill_radios(page, orig, eeo_answers, profile, jid)
                elif orig:
                    nf, _ = _fill_text(page, [orig], eeo_answers, profile, jid, state)
                else:
                    nf = 0
                if nf:
                    filled += nf
                unfilled = [u for u in unfilled if u is not ef]
                provenance = res.provenance if hasattr(res, 'provenance') else "auto_decline"
                print(f"  EEO: {ef['label'][:50]} -> {res.value[:40]} ({provenance})", file=sys.stderr)

    # Platform post-fill hook (e.g. notify widget frameworks of DOM changes)
    if registry and registry.has_hook("post_fill"):
        registry.call_hook("post_fill", page)

    # Unfollow company/social update checkboxes (always)
    page.evaluate(
        """() => {
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
    }"""
    )

    state["filled"] = filled

    # Re-scan for conditional fields that may have appeared after fill (e.g., "Do you have a portfolio?" → URL field)
    if filled > 0:
        time.sleep(0.5)
        ps2 = read_page(page)  # read_page already falls back to dialog scope
        seen_labels = {f.get("label", "") for f in ps.get("fields", [])}
        new_fields = [
            f
            for f in ps2.get("fields", [])
            if f.get("required") and f.get("label", "") not in seen_labels
        ]
        if new_fields:
            text_filled2, text_unfilled2 = _fill_text(
                page, new_fields, answers, profile, jid, state
            )
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
            match = next(
                (
                    f3
                    for f3 in ps3.get("fields", [])
                    if f3.get("tag") == "SELECT" and f3.get("label") == cf.get("label")
                ),
                None,
            )
            if match and len(match.get("options", [])) > len(
                cf.get("options", []) or []
            ):
                nf, nu = _fill_text(page, [match], answers, profile, jid, state)
                if nf:
                    filled_any = True
                    filled += nf
                    unfilled = [
                        u for u in unfilled if u.get("label") != cf.get("label")
                    ]
        if filled_any:
            ps = ps3

    read_and_save(page, state)

    # Verify filled values persisted (ATS may reject or clear values silently)
    ps_v = read_page(page)
    missing = [fv.get("label", "?")[:50] for fv in ps_v.get("fields", [])
               if fv.get("required") and not fv.get("value", "").strip() and fv.get("type") != "file"]
    if missing:
        print(f"RE_FILL: {len(missing)} required fields empty after fill — retrying", file=sys.stderr)
        for ml in missing:
            match = next((ff for ff in ps_v["fields"] if ff.get("label", "")[:50] == ml), None)
            if match:
                nf, _ = _fill_text(page, [match], answers, profile, jid, state)
                filled += nf
        if registry and registry.has_hook("post_fill"):
            registry.call_hook("post_fill", page)
        state.pop("_fields_with_errors", None)
        read_and_save(page, state)

    # --answers values apply only to this run; they are not persisted. To reuse an
    # answer across jobs, add it to profile.json ("answers" map or a known key).

    # Keep the persisted fill count current after all rescans/refills —
    # cmd_submit's confirmation preview reads it.
    state["filled"] = filled
    # Re-fill invalidates previous submit preview — must re-preview before confirming
    state.pop("_submit_previewed", None)
    state["_last_answers"] = answers
    save_state(state)

    page_num = state.get("_page", "?")

    # Audit pass (read-only) + shadow-mode evidence.
    mode = resolve_mode("shadow" if shadow else None)
    try:
        ps_audit = read_page(page)
        _platform = state.get("platform", "") or _domain(page.url)
        _corrected = state.get("_fields_with_errors", [])
        _audit_fill(jid, ps_audit.get("fields", []), answers, profile, page_num,
                    platform=_platform, corrected_labels=_corrected)
        asum = audit.summarize(jid)
        emit_status("audit", f"fields={asum['fields']} filled={asum['filled']} "
                             f"invalid={asum['invalid']} prov={asum['by_provenance']} cat={asum['by_category']}")
    except Exception as _e:
        print(f"AUDIT_WARN: {_e}", file=sys.stderr)
    if not submits_for_real(mode):
        try:
            from apply.common.inspect_lib import capture
            capture(page, jid, prefix="shadow_fill")
        except Exception:
            pass
        emit_status(f"{mode}_mode", "fill recorded; submit will be blocked (set JI_APPLY_MODE=live to submit)")

    emit_fill_report(filled, unfilled, page_num, profile if unfilled else None)

    btns = ps.get("buttons", [])
    has_submit = any(
        b["text"].lower()
        in ("submit", "submit application", "apply", "send application")
        and not b["disabled"]
        for b in btns
    )
    has_next = any(
        b["text"].lower() in ("next", "review", "continue", "done")
        and not b["disabled"]
        for b in btns
    )
    # Track fill progress in apply state for crash recovery
    if not unfilled:
        filled_fields = {f.get("label", f.get("_sel", f"field_{i}")): "" for i, f in enumerate(ps.get("fields", [])) if f.get("filled")}
        try:
            _as_record(jid, state.get("_page", 1), filled_fields)
        except Exception:
            pass
    if unfilled:
        emit_next('act --fill --answers \'{"<question>": "<answer>"}\'')
    elif has_submit:
        emit_next("act --submit")
    elif has_next:
        emit_next("act --next")
    elif not unfilled and not has_submit and not has_next:
        # All fields filled, no buttons — possible auto-submit
        body_text = page_text(page) or ""
        if has_success_text(body_text):
            emit_status("submitted", "auto-submit without clicking")
            emit_next("verify")
        else:
            emit_status("unknown", "all fields filled but no buttons")
            emit_next("verify")


def cmd_next(jid, candidate=None):
    state = load_state()
    if state.get("jid") != jid:
        print(
            f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first",
            file=sys.stderr,
        )
        return
    if _check_already_submitted(state, jid):
        return
    b, ctx = connect()
    pm = PageManager(ctx, jid)
    pm.close_stale(target_url=state.get("external_url", ""))
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
            return

    ps = read_page(page)
    handle_session_timeout(page)
    if handle_captcha(page, state):
        emit_next("retry after solving CAPTCHA")
        return

    advance_kws = ["next", "continue", "review", "done", "submit", "submit application"]
    all_candidates = scan_actions(page, advance_kws, _EXCLUDED_BUTTONS)

    # Detect page/step number from the DOM — multi-strategy cascade
    step_info = page.evaluate(r"""() => {
        const doc = document;
        const text = (doc.body.innerText || '').toLowerCase();

        // Strategy 1: ARIA progressbar (most reliable)
        const pb = doc.querySelector('[role="progressbar"]');
        if (pb) {
            const now = pb.getAttribute('aria-valuenow');
            const max = pb.getAttribute('aria-valuemax');
            if (now && max) return {current: parseInt(now), total: parseInt(max)};
        }

        // Strategy 2: aria-setsize + aria-posinset on step items
        const steps = doc.querySelectorAll('[aria-setsize]');
        for (const s of steps) {
            const pos = s.getAttribute('aria-posinset');
            const total = s.getAttribute('aria-setsize');
            if (pos && total) return {current: parseInt(pos), total: parseInt(total)};
        }

        // Strategy 3: aria-current="step" — count matching elements
        const current = doc.querySelector('[aria-current="step"]');
        if (current) {
            const allSteps = doc.querySelectorAll('[aria-current="step"], [aria-current="false"], [class*="step"], [data-step]');
            if (allSteps.length > 1) {
                const idx = Array.from(allSteps).indexOf(current) + 1;
                return {current: idx, total: allSteps.length};
            }
        }

        // Strategy 4: data attributes (generic)
        const dataStep = doc.querySelector('[data-current-step], [data-step]');
        if (dataStep) {
            const v = dataStep.getAttribute('data-current-step') || dataStep.getAttribute('data-step');
            const allSteps = doc.querySelectorAll('[data-step], [class*="step"]');
            const total = allSteps.length;
            if (v && total > 0) return {current: parseInt(v), total: total};
        }

        // Strategy 5: structured step indicators in the DOM
        const indicators = doc.querySelectorAll('[class*="step-indicator"], [class*="stepper"], [class*="wizard"], [class*="progress-track"]');
        if (indicators.length > 0) {
            const active = doc.querySelector('[class*="active"], [class*="current"]');
            if (active && active.closest('[class*="step-indicator"], [class*="stepper"]')) {
                const idx = Array.from(indicators[0].querySelectorAll('[class*="step"], li, [data-index]')).indexOf(active) + 1;
                if (idx > 0 && indicators.length > 0) return {current: idx, total: indicators.length};
            }
        }

        // Strategy 6: text-based (broader patterns)
        const patterns = [
            /(?:step|page|question)\s*(\d+)\s*(?:of|\/|—|-|–)\s*(\d+)/i,
            /(\d+)\s*\/\s*(\d+)\s*(?:steps?|pages?|questions?)/i,
            /(\d+)\s+of\s+(\d+)\s*(?:steps?|pages?|questions?)/i,
        ];
        for (const pat of patterns) {
            const m = text.match(pat);
            if (m) return {current: parseInt(m[1]), total: parseInt(m[2])};
        }

        return null;
    }""")
    # Always compute has_unfilled_required (needed for button selection regardless of step_info)
    has_unfilled_required = any(
        f.get("required") and not f.get("value")
        for f in ps.get("fields", [])
    )
    if step_info:
        state["_page"] = step_info["current"]
        state["_page_total"] = step_info["total"]
        save_state(state)
    else:
        # Infer page from unfilled fields if no step indicator found
        state.pop("_page_total", None)
        if has_unfilled_required and state.get("_page", 1) == 1:
            pass  # already on page 1, no update needed
        elif has_unfilled_required:
            # Fields are still unfilled even after advancing — likely still on current page
            pass
        else:
            state["_page"] = state.get("_page", 0) + 1  # assume advanced

    # If candidate was specified, click it directly
    if candidate is not None:
        if candidate < len(all_candidates):
            c = all_candidates[candidate]
            if _redirect_submit_intent(c, state):
                return
            _click_candidate(page, c, state)
            ps2 = read_page(page)
            _handle_post_click(state, ps2, page)
        else:
            print(
                f"ERROR: candidate {candidate} out of range (0-{len(all_candidates)-1})",
                file=sys.stderr,
            )
        return

    candidates = [c for c in all_candidates if not c.get("disabled")]
    target = None

    # Categorize by intent
    advance_cands = []
    submit_cands = []
    for c in candidates:
        intent, _ = ButtonIntentClassifier.classify(c["text"])
        if intent == "advance":
            advance_cands.append(c)
        elif intent == "submit":
            submit_cands.append(c)

    # Phase 1: if on an early page, only advance (never submit)
    on_early_page = step_info and step_info["current"] < step_info["total"]

    if advance_cands and (on_early_page or has_unfilled_required):
        # Prefer "Next" / "Continue" over "Review" when there are unfilled fields
        score_key = lambda c: 3 if c["text"].lower() in ("next", "continue") else (
                             2 if c["text"].lower() == "review" else 1)
        target = max(advance_cands, key=score_key)
    elif advance_cands and not has_unfilled_required and not on_early_page:
        # Last page, all filled — advance to review/submit
        target = advance_cands[0]
    elif submit_cands and not has_unfilled_required:
        # No advance buttons, all filled — safe to submit
        target = submit_cands[0]
    elif candidates and not has_unfilled_required and candidates[0].get("score", 0) >= 4:
        target = candidates[0]

    if not target and candidates:
        print("CANDIDATES:", file=sys.stderr)
        for i, c in enumerate(candidates[:8]):
            intent, conf = ButtonIntentClassifier.classify(c["text"])
            print(f"  [{i}] '{c['text'][:40]}' ({intent}) score={c.get('score','?')}", file=sys.stderr)
        print("CHOOSE: act --next <jid> --candidate N", file=sys.stderr)
        emit_next("model_choice")
        save_state(state)
        return
    elif not target:
        # Check if there are disabled advance buttons
        for c in scan_actions(page, advance_kws):
            if c.get("disabled"):
                print(
                    f"BUTTON_DISABLED: {c['text']} — fill required fields first",
                    file=sys.stderr,
                )
                emit_next("act --fill")
                return
        print("NO_BUTTON", file=sys.stderr)
        emit_next("act --inspect")
        return

    if _redirect_submit_intent(target, state):
        return
    print(f"ACTION: {target['text']}", file=sys.stderr)
    _click_candidate(page, target, state)
    ps2 = read_page(page)
    if not _handle_post_click(state, ps2, page):
        has_submit = any(
            b["text"].lower()
            in ("submit", "submit application", "apply", "send application")
            and not b["disabled"]
            for b in ps2.get("buttons", [])
        )
        emit_next("act --submit" if has_submit else "act --fill")


def cmd_back(jid):
    state = load_state()
    if state.get("jid") != jid:
        print(
            f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first",
            file=sys.stderr,
        )
        return
    if _check_already_submitted(state, jid):
        return
    b, ctx = connect()
    pm = PageManager(ctx, jid)
    pm.close_stale(target_url=state.get("external_url", ""))
    page = pm.find(fallback_url=state.get("external_url", ""))[0]
    if not page:
        print("ERROR: no page found", file=sys.stderr)
        return
    before = _page_hash(page)
    page.evaluate(
        """() => { const c = document.querySelector('[role="dialog"]') || document; c.querySelectorAll('button').forEach(b => { if ((b.textContent||'').trim().toLowerCase() === 'back' && !b.disabled) b.click(); }); }"""
    )
    _wait_for_change(page, before)
    ps = read_page(page)
    state["page"] = ps
    save_state(state)
    emit_next("act --fill")


def cmd_submit(jid, confirm=False, candidate=None, shadow=False):
    state = load_state()
    if state.get("jid") != jid:
        print(
            f"ERROR: state is for job {state.get('jid','?')}, not {jid} — run detect {jid} first",
            file=sys.stderr,
        )
        return
    if _check_already_submitted(state, jid):
        return

    b, ctx = connect()
    pm = PageManager(ctx, jid)
    pm.close_stale(target_url=state.get("external_url", ""))
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

    handle_session_timeout(page)

    if handle_captcha(page, state):
        emit_next("retry after solving CAPTCHA")
        return

    # Capture alert dialogs (validation errors, success alerts)
    _alerts = []
    page.on("dialog", lambda d: (_alerts.append(d.message), d.accept()))

    # ButtonIntentClassifier for submit buttons
    all_buttons = (
        page.evaluate(
            """() => {
        return Array.from(document.querySelectorAll('button, [role="button"]'))
            .filter(b => b.offsetParent)
            .map(b => ({text: (b.textContent||'').trim().slice(0,30), disabled: b.disabled}));
    }"""
        )
        or []
    )
    best = ButtonIntentClassifier.pick(all_buttons, "submit")

    cands = []
    if best:
        cands = [
            {
                "text": all_buttons[best["index"]]["text"],
                "disabled": False,
                "score": best["confidence"] * 5,
            }
        ]
    if not cands:
        submit_kws = [
            "submit application",
            "submit",
            "send application",
            "apply",
            "send",
        ]
        cands = [
            c
            for c in scan_actions(page, submit_kws, _EXCLUDED_BUTTONS)
            if not c.get("disabled")
        ]

    if candidate is not None:
        if candidate < len(cands):
            target = cands[candidate]
        else:
            print(
                f"ERROR: candidate {candidate} out of range (0-{len(cands)-1})",
                file=sys.stderr,
            )
            return
    elif cands and (
        cands[0].get("score", 0) >= 4 or cands[0].get("confidence", 0) >= 0.8
    ):
        target = cands[0]
    elif cands:
        print("CANDIDATES:", file=sys.stderr)
        for i, c in enumerate(cands[:8]):
            print(
                f"  [{i}] '{c['text'][:40]}' score={c.get('score','?')}",
                file=sys.stderr,
            )
        print("CHOOSE: act --submit <jid> --candidate N", file=sys.stderr)
        emit_next("model_choice")
        return
    else:
        print("NO_SUBMIT_BUTTON", file=sys.stderr)
        emit_next("act --inspect")
        return
    if not confirm:
        url = state.get("external_url", "") or state.get("url", "")
        url_short = url.split("?")[0][:80] if url else "?"
        plat = state.get("platform", "") or _domain(url) or "unknown"
        ps = state.get("page", {})
        fields = ps.get("fields", [])
        unfilled_fields = [f for f in fields if f.get("required") and not f.get("value")]
        last = state.get("_last_submit", "")
        warn = ""
        if last == "validation_error":
            warn = " (last submit had validation errors)"
        elif last == "captcha":
            warn = " (CAPTCHA was triggered last time)"
        elif last == "unknown":
            warn = " (last submit was not confirmed)"

        # Load profile + last answers for provenance derivation
        _preview_profile = {}
        if os.path.exists(profile_path):
            try:
                with open(profile_path, encoding="utf-8") as _pf:
                    _preview_profile = json.load(_pf)
                _validate_profile(_preview_profile)
            except Exception:
                pass
        _preview_answers = state.get("_last_answers", {})

        print(
            f"SUBMIT: {plat} — {len(fields)} fields, {len(unfilled_fields)} unfilled{warn}",
            file=sys.stderr,
        )

        # Group filled fields by provenance source
        _provenance_groups = {"profile": [], "answers": [], "auto_decline": [], "file": [], "unknown": []}
        _prov_normalize = {"profile": "profile", "ephemeral": "profile", "derived": "profile",
                           "answers_override": "answers", "user_typed": "answers",
                           "auto_decline": "auto_decline", "static": "profile",
                           "file": "file", "mapping": "profile", "profile_eeo": "auto_decline",
                           "no_match": "unknown"}
        _profile_eeo_answers = _preview_profile.get("common_answers", {}).get("eeo", {})
        for f in fields:
            label = f.get("label", f.get("key", "?"))
            val = f.get("value", "")
            if not val:
                continue
            if f.get("type") == "file":
                _provenance_groups["file"].append(f)
                continue
            raw_prov = _derive_provenance(label, _preview_answers, _preview_profile)
            if _profile_eeo_answers.get(label) or raw_prov == "auto_decline":
                prov_group = "auto_decline"
            else:
                prov_group = _prov_normalize.get(raw_prov, "unknown")
            _provenance_groups[prov_group].append(f)

        source_labels = {
            "profile": "From profile",
            "answers": "From --answers (you provided)",
            "auto_decline": "Auto-declined (EEO)",
            "file": "Resume upload",
            "unknown": "Source unknown",
        }
        for group_key, group_fields in _provenance_groups.items():
            if not group_fields:
                continue
            group_label = source_labels.get(group_key, group_key)
            print(f"  ── {group_label} ({len(group_fields)}):", file=sys.stderr)
            for _gf in group_fields:
                _glbl = _gf.get("label", _gf.get("key", "?"))[:45]
                _gval = _gf.get("value", "")[:40]
                _gtype = _gf.get("type", "?")
                print(f"    [{_gtype}] {_glbl:45s} = {_gval}", file=sys.stderr)

        # Unfilled fields
        if unfilled_fields:
            print(f"  ── Unfilled ({len(unfilled_fields)}):", file=sys.stderr)
            for uf in unfilled_fields[:5]:
                ulbl = uf.get("label", uf.get("key", "?"))[:45]
                print(f"    [{uf.get('type','?')}] {ulbl}", file=sys.stderr)
            if len(unfilled_fields) > 5:
                print(f"    ... and {len(unfilled_fields)-5} more", file=sys.stderr)

        print(f"  URL: {url_short}", file=sys.stderr)
        print("  Review the values above. Pass --confirm to submit.", file=sys.stderr)

        # Reconciliation: cross-page value comparison
        _mm = _reconcile_fields(state, jid)
        if _mm:
            print(f"  ── Inconsistencies found ({len(_mm)}):", file=sys.stderr)
            for _m in _mm:
                _pages_str = "; ".join(f"pg{p[0]}={p[1][:30]}" for p in _m["pages"])
                print(f"    '{_m['label'][:40]}': {_pages_str}", file=sys.stderr)
            print("  Verify the values above and re-fill if needed.", file=sys.stderr)

        state["_submit_previewed"] = True
        save_state(state)
        emit_next("act --submit --confirm")
        return

    # Safety gate: must preview before confirming
    if not state.get("_submit_previewed"):
        print("ERROR: must preview submission first — run act --submit <jid> (without --confirm) to review", file=sys.stderr)
        emit_next("act --submit")
        return
    print(
        f"SUBMIT: {target['text']}\nDISABLED: {target.get('disabled', False)}",
        file=sys.stderr,
    )

    # Gate the submit: shadow/hold mode, kill-switch, or validation gate (Phase 4).
    from apply.common.policy import load_policy
    from apply.common import gate
    mode = resolve_mode("shadow" if shadow else None)
    action, reason = gate.submit_decision(mode, load_policy(), audit.summarize(jid))
    if action != "submit":
        try:
            from apply.common.inspect_lib import capture
            capture(page, jid, prefix="shadow_submit")
        except Exception:
            pass
        audit.log_event(jid, "submit_blocked", mode=mode, detail=f"{target['text']} | {reason}")
        emit_status(f"submit_{action}", f"would submit '{target['text']}' — {reason}")
        emit_next("verify, or resolve the reason above and set JI_APPLY_MODE=live")
        return

    # Platform pre-submit hook (e.g., scroll to reveal button)
    reg = resolve_registry(page.url)
    if reg and reg.has_hook("pre_submit"):
        reg.call_hook("pre_submit", page)
        time.sleep(1)

    before_hash = _page_hash(page)
    b_loc = page.locator(f'button:has-text("{target["text"]}"), a:has-text("{target["text"]}"), [role="button"]:has-text("{target["text"]}")')
    if b_loc.count() == 0:
        print(
            f"  Submit warning: button '{target['text']}' not found on page",
            file=sys.stderr,
        )
    else:
        try:
            b_loc.first.click(timeout=5000, force=True)
        except Exception as e:
            print(f"  Submit warning: click failed — {e}", file=sys.stderr)
    # Wait for either a page transition OR visible error text
    for _ in range(30):
        time.sleep(0.5)
        current = _page_hash(page)
        current_text = (page_text(page) or "").lower()
        if current != before_hash or any(
            w in current_text for w in ["error", "required", "invalid"]
        ):
            break

    # If error text was detected, don't trust it yet — poll for success signals
    # which may arrive after validation errors (SAP SF async pattern).
    # Broad tier is fine here: this only ends the poll, it never marks applied.
    for _ in range(10):
        current_text = page_text(page) or ""
        if has_success_text(current_text, SUCCESS_BROAD):
            break
        time.sleep(0.5)

    # Check for CAPTCHA triggered by submission
    handle_session_timeout(page)
    if handle_captcha(page, state):
        print("*** Solve the CAPTCHA above, then retry submit ***", file=sys.stderr)
        state["_last_submit"] = "captcha"
        save_state(state)
        emit_next("act --submit --confirm")
        return

    text = (page_text(page) or "").lower()
    # Include alert dialog messages in error/success detection
    for msg in _alerts:
        text += " " + msg.lower()
    # Check for success signals first (handles AJAX submit where form stays visible)
    if has_success_text(text):
        mark_applied(jid)
        state["_last_submit"] = ""
        save_state(state)
        emit_status("submitted (via AJAX)")
        emit_next("verify")
        return

    has_form = (
        page.evaluate(
            """() => {
        const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        return inputs.length > 0 && Array.from(inputs).some(i => i.offsetParent !== null);
    }"""
        )
        or False
    )
    has_error = any(w in text for w in ["error", "required", "invalid", "correct the"])
    if has_error and has_form:
        state["_last_submit"] = "validation_error"
        save_state(state)
        # Field-level diagnostics — wait briefly for animated errors to render
        try:
            page.wait_for_selector('[aria-invalid="true"]', timeout=3000)
        except Exception:
            pass
        errs = scan_errors(page)
        if errs:
            for e in errs:
                lbl = e.get("label", "?")
                txt = e.get("error_text", "")
                if txt:
                    print(f"  FIELD_ERROR: {lbl} — {txt}", file=sys.stderr)
                else:
                    print(f"  FIELD_ERROR: {lbl} (invalid)", file=sys.stderr)
            state["_fields_with_errors"] = [e["label"] for e in errs if e.get("label")]
        else:
            print("  FIELD_ERROR: (validation errors detected, no field-level detail)", file=sys.stderr)
            state.pop("_fields_with_errors", None)
        save_state(state)
        emit_status("validation_errors", f"{len(errs)} field(s) with errors")
        emit_next("act --fill")
    elif (
        not page.evaluate("() => document.querySelector('[role=\"dialog\"]')")
        and not has_form
    ):
        mark_applied(jid)
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
    """Full page analysis: screenshot, HTML, probes, fields, buttons.
    Uses inspect_lib for core logic; adds job context from state."""
    from apply.common.output import emit_warn, emit_error, emit_next
    state = load_state()
    if state.get("jid") != jid:
        emit_error(f"state is for job {state.get('jid','?')}, not {jid}")
        print("  Run detect first.", file=sys.stderr)
        return

    from apply.common.inspect_lib import capture, probe_state
    from lib.ask_api import available as _vision_available
    b, ctx = connect()
    pm = PageManager(ctx, jid)
    pm.close_stale(target_url=state.get("external_url", ""))
    ext = state.get("external_url", "")
    page, score, candidates = pm.find(fallback_url=ext)

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
                emit_next("model_choice")
            else:
                emit_next("none")
            return

    print(f"URL: {page.url}", file=sys.stderr)
    print(f"Title: {page.title() or '?'}", file=sys.stderr)
    print(f"Platform: {state.get('platform', '?')}", file=sys.stderr)
    print(f"Filled: {state.get('filled', 0)} fields", file=sys.stderr)

    capture(page, jid)
    if _vision_available():
        print(f"  ask: lib/ask_api.py --img <path> --prompt '?'", file=sys.stderr)
    fc, _, _, _ = probe_state(page)

    emit_next("act --fill" if fc > 0 else "none")


def run(args):
    shadow = getattr(args, "shadow", False)
    if args.inspect:
        cmd_inspect(args.jid, args.candidate)
    elif args.fill:
        cmd_fill(args.jid, args.answers, args.candidate, not args.apply, shadow)
    elif args.next:
        cmd_next(args.jid, args.candidate)
    elif args.back:
        cmd_back(args.jid)
    elif args.submit:
        cmd_submit(args.jid, args.confirm, args.candidate, shadow)
    else:
        print(
            "ERROR: specify --fill, --next, --back, --submit, or --inspect",
            file=sys.stderr,
        )
