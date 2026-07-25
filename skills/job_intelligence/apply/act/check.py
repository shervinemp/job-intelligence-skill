"""act/check.py — Pre-submit validation: read filled values, flag contradictions.

The orchestrator (LLM-in-the-loop) runs this after --fill and before --submit.
It reads the current form state from the DOM and cross-references against:
  1. Profile answers — did the right value get filled?
  2. Logical consistency — are answers self-contradictory?
  3. Known traps — wrong country, wrong city, sponsorship mismatch, etc.

Output is a structured report the orchestrator reviews before deciding to submit.
"""
import json, os, sys, time

from lib.db import get_conn
from apply.common.output import emit_error, emit_status
from apply.common.page_helpers import load_state, save_state, handle_captcha, tag_page
from apply.act.helpers import (
    _load_profile, _build_ans_dict, chrome_session, _host,
    _probe_form, _fill_with_playwright, _field_key,
)
from apply.common.registry import resolve as resolve_registry
from apply.common.filler import _read_element_value


def cmd_check(jid):
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    state["jid"] = jid

    url = state.get("external_url") or state.get("url", "")
    if not url:
        row = get_conn().execute("SELECT url, external_url FROM jobs WHERE id=?", (jid,)).fetchone()
        if row:
            url = row["external_url"] or row["url"]
    if not url:
        emit_error("no URL found — run 'apply act --fill' first")
        return 1

    profile = _load_profile()
    reg = resolve_registry(url)

    issues = []
    checked = 0

    try:
        with chrome_session(state) as (page, ctx):
            cur = page.url or ""
            if not cur or "about:blank" in cur or "chrome-error" in cur:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

            tag_page(page, jid)

            pr = _probe_form(page, reg, jid, allow_vision=False)
            fields = pr.fields or []
            if not fields:
                emit_error("no fields detected — run 'apply act --fill' first")
                return 1

            ans_dict = _build_ans_dict(profile)

            for f in fields:
                label = (f.get("label") or "").strip()
                if not label:
                    continue

                tag = (f.get("tag") or "").lower()
                ftype = (f.get("type") or "").lower()

                # Skip file upload fields and optional fields with no answer
                lc = label.lower()
                if (tag == "input" and (f.get("accept") or ftype == "file")) or "resume" in lc or "cv" in lc or "cover" in lc:
                    continue

                sel = f.get("_sel") or f.get("selector") or ""
                if not sel:
                    from apply.steps.probe import resolve_selector
                    sel = resolve_selector(page, f) or ""
                if not sel:
                    continue

                # Read what's actually in the DOM
                actual = _read_element_value(page, sel, ans=ans_dict.get(label, ""), field=f)
                # Resolve what we expected to fill
                from apply.common.resolve import resolve, normalize
                res = resolve(label, profile, None,
                              autocomplete=f.get("autocomplete", ""),
                              field_name=f.get("name", ""),
                              field_id=f.get("id", ""))
                expected = res.value

                checked += 1

                # Skip if no expected value (optional field)
                if expected is None:
                    continue

                # Radio groups: check which option was selected
                if f.get("tag") == "RADIO_GROUP" or ftype == "radio":
                    selected = page.evaluate("""(args) => {
                        const [sel] = args;
                        const radios = [...document.querySelectorAll(sel)];
                        for (const r of radios) {
                            if (r.checked) {
                                const lbl = r.closest('label');
                                if (lbl) return lbl.textContent.replace(r.value || '', '').trim();
                                return r.value || '';
                            }
                        }
                        return '';
                    }""", [sel])
                    if selected:
                        actual_lower = selected.lower()
                        expected_lower = str(expected).lower()
                        # Check for country/city mismatch in Yes/No options
                        if actual_lower != expected_lower:
                            # Is it a disambiguation issue?
                            user_loc = (profile.get("location") or "").lower()
                            user_city = user_loc.split(",")[0].strip() if "," in user_loc else ""
                            if user_city and user_city not in actual_lower and actual_lower.startswith("yes"):
                                issues.append({
                                    "label": label,
                                    "expected": str(expected),
                                    "actual": selected,
                                    "severity": "ERROR",
                                    "reason": f"Wrong location selected — user is in '{user_city}' but form shows '{selected}'",
                                })
                            elif actual_lower.startswith(expected_lower[:3]):
                                pass  # Close enough (prefix match)
                            else:
                                issues.append({
                                    "label": label,
                                    "expected": str(expected),
                                    "actual": selected,
                                    "severity": "WARN",
                                    "reason": "Radio selection doesn't match expected value",
                                })
                    continue

                # Checkbox: check if consent matches
                if tag == "input" and ftype == "checkbox":
                    # Ashby yesno: check button state
                    is_yesno = page.evaluate("""(args) => {
                        const [sel] = args;
                        const cb = document.querySelector(sel);
                        if (!cb) return null;
                        const container = cb.closest('[class*="yesno"]');
                        if (!container) return cb.checked ? 'checked' : 'unchecked';
                        const buttons = container.querySelectorAll('button');
                        for (const btn of buttons) {
                            if (btn.className.includes('active') || btn.getAttribute('aria-pressed') === 'true') {
                                return btn.textContent.trim().toLowerCase();
                            }
                        }
                        return 'unknown';
                    }""", [sel])
                    if is_yesno and is_yesno not in ("unknown", "checked", "unchecked"):
                        expected_lower = str(expected).lower().strip()
                        if is_yesno != expected_lower and is_yesno != expected_lower[:3]:
                            issues.append({
                                "label": label,
                                "expected": str(expected),
                                "actual": is_yesno,
                                "severity": "ERROR",
                                "reason": "Yes/No button doesn't match expected answer",
                            })
                    continue

                # Text fields: compare actual vs expected
                if actual and expected:
                    actual_lower = actual.lower().strip()
                    expected_lower = str(expected).lower().strip()
                    # Skip if they match (or prefix match)
                    if actual_lower == expected_lower or actual_lower.startswith(expected_lower) or expected_lower.startswith(actual_lower):
                        continue
                    # Flag mismatch
                    issues.append({
                        "label": label,
                        "expected": str(expected),
                        "actual": actual,
                        "severity": "WARN",
                        "reason": "Filled value doesn't match expected",
                    })
                elif expected and not actual:
                    issues.append({
                        "label": label,
                        "expected": str(expected),
                        "actual": "(empty)",
                        "severity": "ERROR",
                        "reason": "Required field appears empty (may be React state — verify visually)",
                    })

    except Exception as e:
        emit_error(f"check failed: {e}")
        return 1

    # Cross-field consistency checks
    loc = (profile.get("location") or "").lower()
    user_city = loc.split(",")[0].strip() if "," in loc else ""
    filled_values = {}
    for f in fields:
        sel = f.get("_sel") or ""
        if sel:
            val = _read_element_value(page if 'page' in dir() else None, sel, field=f)
            if val:
                filled_values[(f.get("label") or "").lower()] = val

    # Check: if "country" field says one thing and "location" says another
    country_val = filled_values.get("country") or filled_values.get("if hired, in which country will you be based?")
    loc_val = filled_values.get("location") or filled_values.get("location (city)")
    if country_val and loc_val and user_city:
        if user_city not in loc_val.lower() and country_val.lower() not in loc_val.lower():
            issues.append({
                "label": "cross-field: country vs location",
                "expected": f"both should reference '{user_city}'",
                "actual": f"country={country_val}, location={loc_val}",
                "severity": "WARN",
                "reason": "Country and location fields may be inconsistent",
            })

    # Output report
    print(f"CHECK: {checked} fields verified, {len(issues)} issues found", file=sys.stderr)
    errors = [i for i in issues if i["severity"] == "ERROR"]
    warnings = [i for i in issues if i["severity"] == "WARN"]

    if errors:
        print(f"\nERRORS ({len(errors)}) — DO NOT SUBMIT:", file=sys.stderr)
        for i in errors:
            print(f"  ! {i['label'][:50]}", file=sys.stderr)
            print(f"    expected: {i['expected'][:50]}", file=sys.stderr)
            print(f"    actual:   {i['actual'][:50]}", file=sys.stderr)
            print(f"    reason:   {i['reason'][:60]}", file=sys.stderr)

    if warnings:
        print(f"\nWARNINGS ({len(warnings)}) — review before submit:", file=sys.stderr)
        for i in warnings:
            print(f"  ? {i['label'][:50]}", file=sys.stderr)
            print(f"    expected: {i['expected'][:50]}", file=sys.stderr)
            print(f"    actual:   {i['actual'][:50]}", file=sys.stderr)
            print(f"    reason:   {i['reason'][:60]}", file=sys.stderr)

    if not issues:
        emit_status("check_passed", f"{checked} fields verified, no contradictions")
        emit_next_value("submit", "all fields verified — safe to submit")
    elif errors:
        emit_status("check_failed", f"{len(errors)} error(s) — fix before submit")
        emit_next_value("act --fill", "fix errors with --answers then re-check")
    else:
        emit_status("check_warn", f"{len(warnings)} warning(s) — review before submit")
        emit_next_value("submit", "review warnings, then submit if acceptable")

    return 0 if not errors else 1


def emit_next_value(status, next_action):
    print(f"NEXT: {next_action}", file=sys.stderr)
