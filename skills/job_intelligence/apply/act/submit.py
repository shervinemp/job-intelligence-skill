"""act/submit.py — Submit command with policy gate, Playwright + Skyvern fallback."""
import json, os, sys, time

from lib.db import get_conn
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.page_helpers import load_state, save_state, handle_captcha, mark_applied
from apply.act.helpers import (
    _load_profile, chrome_session, _probe_form, _fill_with_playwright,
    _empty_required, _detect_submit_button, _dismiss_confirm_modal,
    _check_submit_success, _get_validation_errors,
    _find_next_button, _click_action,
)


def cmd_submit(jid, confirm=False, force=False):
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1
    stage, job_state = db_row["stage"], db_row["state"]

    if stage == "applied":
        emit_status("already applied")
        emit_next("verify")
        return 0

    from apply.common.policy import load_policy, resolve_mode
    from apply.common.gate import submit_decision
    pol = load_policy()
    mode = resolve_mode()
    action, reason = submit_decision(mode, pol)
    if action == "blocked":
        emit_status("blocked", reason)
        emit_next("none", "kill-switch active — resume via apply_policy.json")
        return 1
    if action == "hold":
        emit_status("hold", reason)
        emit_next("none", "review form in browser, then run submit with policy=live")
        return 0

    # GATE: run check before submit unless --force
    if not force:
        from apply.act.check import cmd_check
        print(f"  Pre-submit check...", file=sys.stderr)
        check_rc = cmd_check(jid)
        if check_rc != 0:
            print(f"  CHECK FAILED — submit blocked. Use --force to override.", file=sys.stderr)
            emit_status("check_failed", "run 'apply act --check' and fix errors first")
            emit_next("check", "fix errors then resubmit (or --force to override)")
            return 1

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    state["jid"] = jid

    url = state.get("external_url") or state.get("url", "")
    if not url:
        emit_error("no external_url in state")
        return 1

    browser_session_id = state.get("browser_session_id", "")

    try:
        with chrome_session(state) as (page, ctx):
            cur = page.url or ""
            if not cur or "about:blank" in cur or "chrome-error" in cur:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

            if handle_captcha(page, state):
                emit_status("captcha", "CAPTCHA still present after timeout")
                return 1

            # Pre-flight: check for already-applied signals on any ATS
            from apply.common.signals import has_already_applied_text
            from apply.common.page_helpers import page_text as _pt
            try:
                _ptxt = _pt(page) or ""
                if has_already_applied_text(_ptxt):
                    mark_applied(jid)
                    emit_status("already applied")
                    emit_next("verify")
                    return 0
            except Exception:
                pass

            if "linkedin.com/jobs" in (page.url or "").lower():
                # LinkedIn-specific pre-flight: Easy Apply button may be gone
                # (LinkedIn shows "Applied" after submission)
                from apply.common.signals import has_already_applied_text
                from apply.common.page_helpers import page_text as _lpt
                try:
                    _lptxt = _lpt(page) or ""
                    if has_already_applied_text(_lptxt):
                        mark_applied(jid)
                        emit_status("already applied")
                        emit_next("verify")
                        return 0
                except Exception:
                    pass

                dialog_open = page.evaluate("""() => {
                    const d = document.querySelector('dialog[data-testid="dialog"]');
                    return d && d.open && d.offsetParent !== null;
                }""")
                if dialog_open:
                    print(f"  Easy Apply: modal already open", file=sys.stderr)
                else:
                    ea_btn = page.locator('button:has-text("Easy Apply")').first
                    if ea_btn.count() > 0:
                        try:
                            ea_btn.click(timeout=5000)
                        except Exception:
                            ea_btn.click(force=True, timeout=5000)
                        print(f"  Easy Apply: modal opened", file=sys.stderr)
                        time.sleep(3)

            try:
                empt = _empty_required(page)
                if empt:
                    print(f"  WARN: {empt} required field(s) still empty before submit", file=sys.stderr)
            except Exception as re_:
                print(f"  Re-fill skipped: {re_}", file=sys.stderr)

            # LinkedIn Easy Apply: navigate to review/submit page
            if "linkedin.com" in (page.url or ""):
                prev_next_text = ""
                stuck_count = 0
                for _nav in range(10):
                    has_dialog = page.evaluate("""() => !!document.querySelector('dialog, [role="dialog"]')""")
                    if not has_dialog:
                        break
                    submit_btn = page.evaluate("""() => {
                        const d = document.querySelector('dialog, [role="dialog"]');
                        if (!d) return null;
                        for (const b of d.querySelectorAll('button')) {
                            const t = b.textContent.trim().toLowerCase();
                            if (t === 'submit' || t === 'submit application' || t === 'send application') return t;
                        }
                        // Fallback: any button with 'submit' in text
                        for (const b of d.querySelectorAll('button')) {
                            const t = b.textContent.trim().toLowerCase();
                            if (t.includes('submit') && t.length < 30) return t;
                        }
                        return null;
                    }""")
                    if submit_btn:
                        print(f"  Found submit button on review page", file=sys.stderr)
                        break
                    nxt = _find_next_button(page)
                    if not nxt:
                        break
                    # Detect stuck loop (same button text repeatedly)
                    if nxt["text"] == prev_next_text:
                        stuck_count += 1
                        if stuck_count >= 2:
                            # Check which required fields are still empty
                            empt_labels = page.evaluate("""() => {
                                const d = document.querySelector('dialog, [role="dialog"]');
                                if (!d) return [];
                                // Only check visible fields in the dialog
                                const visible = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
                                const empt = [];
                                for (const el of visible) {
                                    // Skip if parent is display:none
                                    let p = el;
                                    let hidden = false;
                                    for (let i = 0; i < 5 && p; i++) {
                                        const s = getComputedStyle(p);
                                        if (s.display === 'none') { hidden = true; break; }
                                        p = p.parentElement;
                                    }
                                    if (hidden) continue;
                                    if (el.required || el.getAttribute('aria-required') === 'true') {
                                        if (el.type === 'radio') continue; // radio groups handled separately
                                        if (!el.value || el.value === '') {
                                            const lbl = el.getAttribute('aria-label') || el.placeholder || el.name || '';
                                            empt.push(lbl.slice(0, 40));
                                        }
                                    }
                                }
                                return empt;
                            }""")
                            if empt_labels:
                                print(f"  Cannot advance — required fields empty: {', '.join(empt_labels[:5])}", file=sys.stderr)
                            else:
                                print(f"  Stuck on '{nxt['text']}' — looking for submit directly", file=sys.stderr)
                            break
                    else:
                        stuck_count = 0
                        prev_next_text = nxt["text"]
                    print(f"  Review step — clicking '{nxt['text']}'", file=sys.stderr)
                    if not _click_action(page, nxt["text"]):
                        break
                    time.sleep(2)

            submit_text = _detect_submit_button(page)
            clicked = False
            if submit_text:
                print(f"  Found submit button: '{submit_text}'", file=sys.stderr)
                try:
                    page.click(f'button:text("{submit_text}")', timeout=5000)
                    clicked = True
                except Exception:
                    try:
                        page.click(f'button:text("{submit_text}")', force=True, timeout=5000)
                        clicked = True
                    except Exception:
                        iframe_clicked = False
                        for fr in page.frames:
                            if fr == page.main_frame:
                                continue
                            try:
                                btn = fr.locator(f'button:text("{submit_text}")').first
                                if btn.count() > 0:
                                    btn.click(timeout=5000)
                                    iframe_clicked = True
                                    print(f"  Clicked submit inside iframe", file=sys.stderr)
                                    break
                            except Exception:
                                continue
                        if iframe_clicked:
                            clicked = True
                        else:
                            try:
                                page.evaluate(f"""() => {{
                                    const btn = [...document.querySelectorAll('button')].find(
                                        b => b.textContent.trim().toLowerCase() === {json.dumps(submit_text.lower())}
                                    );
                                    if (btn) btn.click();
                                }}""")
                                clicked = True
                            except Exception:
                                print(f"  Could not click submit button via Playwright", file=sys.stderr)

            if clicked:
                time.sleep(3)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass

                _dismiss_confirm_modal(page)
                time.sleep(2)

                pages_before = {id(p) for p in ctx.pages}
                success, success_page = _check_submit_success(ctx, page, pages_before)
                if success:
                    was_new = mark_applied(jid)
                    if was_new:
                        emit_status("submitted", "Playwright clicked submit")
                    else:
                        emit_status("already applied")
                    emit_next("verify")
                    return 0

                errors = _get_validation_errors(page)
                if errors:
                    print(f"  VALIDATION_ERRORS: {len(errors)} field(s) blocked submit", file=sys.stderr)
                    for e in errors[:5]:
                        print(f"    ! {e[:80]}", file=sys.stderr)
                    state["submit_errors"] = errors
                    save_state(state)
                    emit_status("validation_error", f"{len(errors)} field(s) need fixing")
                    emit_next("act --fill", "fix validation errors then resubmit")
                    return 1

                # Multi-step: look for Next/Review/Continue (NOT submit — we already clicked that)
                next_btn = None
                try:
                    nav_cands = page.evaluate("""() => {
                        const kws = ['next', 'review', 'continue'];
                        const all = document.querySelectorAll('button, [role="button"]');
                        for (const el of all) {
                            if (el.offsetParent === null || el.disabled) continue;
                            if (!el.closest('dialog') && el.closest('nav, header, footer')) continue;
                            const t = (el.textContent || '').trim().toLowerCase();
                            if (kws.includes(t)) return t;
                        }
                        return null;
                    }""")
                    next_btn = nav_cands
                except Exception:
                    pass
                last_btn = None
                for _ in range(5):
                    if not next_btn or next_btn == last_btn:
                        break
                    last_btn = next_btn
                    print(f"  Review step — clicking '{next_btn}'", file=sys.stderr)
                    try:
                        page.click(f'button:text("{next_btn}")', timeout=5000)
                    except Exception:
                        try:
                            page.evaluate(f"""() => {{
                                const btn = [...document.querySelectorAll('button')].find(
                                    b => b.textContent.trim().toLowerCase() === {json.dumps(next_btn.lower())}
                                );
                                if (btn) btn.click();
                            }}""")
                        except Exception:
                            break
                    time.sleep(2)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    success, _ = _check_submit_success(ctx, page, pages_before)
                    if success:
                        mark_applied(jid)
                        emit_status("submitted", "Playwright multi-step submit")
                        emit_next("verify")
                        return 0
                    # Check for submit button on the new page
                    submit_now = _detect_submit_button(page)
                    if submit_now:
                        try:
                            page.click(f'button:text("{submit_now}")', timeout=5000)
                        except Exception:
                            page.evaluate(f"""() => {{
                                const btn = [...document.querySelectorAll('button')].find(
                                    b => b.textContent.trim().toLowerCase() === {json.dumps(submit_now.lower())}
                                );
                                if (btn) btn.click();
                            }}""")
                        time.sleep(3)
                        success, _ = _check_submit_success(ctx, page, pages_before)
                        if success:
                            mark_applied(jid)
                            emit_status("submitted", "Playwright review->submit")
                            emit_next("verify")
                            return 0
                    # Look for next nav button
                    try:
                        next_btn = page.evaluate("""() => {
                            const kws = ['next', 'review', 'continue'];
                            const all = document.querySelectorAll('button, [role="button"]');
                            for (const el of all) {
                                if (el.offsetParent === null || el.disabled) continue;
                                if (!el.closest('dialog') && el.closest('nav, header, footer')) continue;
                                const t = (el.textContent || '').trim().toLowerCase();
                                if (kws.includes(t)) return t;
                            }
                            return null;
                        }""")
                    except Exception:
                        next_btn = None

                try:
                    from lib.ask_api import available, ask_bytes
                    if available():
                        from apply.common.inspect_lib import page_jpeg
                        img = page_jpeg(page, full=False)
                        reply, err = ask_bytes(
                            img,
                            "Did this job application submit successfully? "
                            "Look for: confirmation message, thank you text, "
                            "application ID, success indicator. "
                            "Answer only YES or NO.",
                        )
                        if not err and (reply or "").strip().lower().startswith("yes"):
                            mark_applied(jid)
                            emit_status("submitted", "vision confirmed via ask_api")
                            emit_next("verify")
                            return 0
                        if err:
                            print(f"  VISION_SKIP: {err}", file=sys.stderr)
                except Exception as ve:
                    print(f"  VISION_SKIP: {ve}", file=sys.stderr)

            if not clicked:
                empt = _empty_required(page)
                if empt:
                    print(f"  {empt} required field(s) empty — cannot submit", file=sys.stderr)
                    emit_status("incomplete", f"{empt} required field(s) need answers")
                    emit_next("act --fill", "supply answers for empty fields, then resubmit")
                    return 1
                print(f"  Playwright could not click submit — using Skyvern", file=sys.stderr)
                from apply.common.skyvern_bridge import click_submit
                result = click_submit(url=page.url, browser_session_id=browser_session_id, timeout=60)
                if result.get("status") == "completed":
                    mark_applied(jid)
                    emit_status("submitted", "Skyvern clicked submit")
                    emit_next("verify")
                    return 0

            emit_status("unknown", "submit attempts inconclusive — check manually")
            emit_next("verify")
            return 1
    except Exception as e:
        emit_error(f"submit failed: {e}")
        return 1
