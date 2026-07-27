"""act/submit.py — Submit command with policy gate and outcome detection cascade."""
import json, sys, time

from lib.db import get_conn
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.page_helpers import load_state, save_state, handle_captcha, mark_applied
from apply.act.helpers import (
    chrome_session,
    _empty_required, _detect_submit_button, _dismiss_confirm_modal,
    _check_submit_success, _get_validation_errors,
    _find_next_button, _click_action,
)


# ─── Submit outcome detection cascade ─────────────────────────────────
# Tries every available method to confidently determine whether the
# submission succeeded or failed. Returns one of:
#   "success"    — confidently submitted (success signal, URL change,
#                  form disappeared, already-applied text)
#   "rejected"   — form rejected (validation errors present)
#   "uncertain"  — tried everything, could not determine confidently


def _determine_outcome(page, ctx, pages_before, url_before, submit_text_before):
    """Multi-step confidence cascade. Tries everything before giving up."""
    from apply.common.signals import has_success_text, has_already_applied_text
    from apply.common.page_helpers import page_text as _pt

    # 1. Success text signals (page + all iframes)
    for source, label in _all_text_sources(page):
        if has_success_text(source):
            return "success", f"success signal in {label}"
        if has_already_applied_text(source):
            return "success", f"already-applied text in {label}"

    # 2. _check_submit_success (new pages, signals, iframes)
    success, _ = _check_submit_success(ctx, page, pages_before)
    if success:
        return "success", "check_submit_success"

    # 3. URL change — if we navigated away from the form URL, likely success
    url_after = page.url or ""
    if url_before and url_after and url_after != url_before:
        if "about:blank" not in url_after and "chrome-error" not in url_after:
            # Check if the new URL looks like a confirmation/thank-you page
            url_lower = url_after.lower()
            if any(w in url_lower for w in ("thank", "confirm", "success", "complete", "done")):
                return "success", f"URL changed to confirmation: {url_after[:80]}"
            # URL changed but no confirmation keyword — moderate confidence
            # Check if the form is gone from the new page
            if not _form_still_present(page):
                return "success", f"URL changed and form gone: {url_after[:80]}"

    # 4. Form disappeared — submit button gone, no form fields visible
    if submit_text_before:
        submit_now = _detect_submit_button(page)
        if not submit_now:
            # Submit button is gone — did the form get replaced?
            if not _form_still_present(page):
                return "success", "submit button and form disappeared"

    # 5. Validation errors — form was rejected
    errors = _get_validation_errors(page)
    if errors:
        return "rejected", f"{len(errors)} validation error(s)"

    # 6. Vision API — screenshot and ask the LLM
    try:
        from lib.ask_api import available, ask_bytes
        if available():
            from apply.common.inspect_lib import page_jpeg
            img = page_jpeg(page, full=False)
            reply, err = ask_bytes(
                img,
                "Did this job application submit successfully? "
                "Look for: confirmation message, thank you text, "
                "application ID, success indicator, OR error messages "
                "and validation errors. "
                "Answer YES_SUBMITTED, YES_ALREADY_APPLIED, NO_REJECTED, or UNKNOWN.",
            )
            if not err and reply:
                r = reply.strip().upper()
                if r.startswith("YES_SUBMITTED") or r.startswith("YES_ALREADY"):
                    return "success", f"vision API: {r[:30]}"
                if r.startswith("NO_REJECTED"):
                    return "rejected", f"vision API: {r[:30]}"
                if r.startswith("UNKNOWN"):
                    pass  # fall through to uncertain
            if err:
                print(f"  VISION_SKIP: {err}", file=sys.stderr)
    except Exception as ve:
        print(f"  VISION_SKIP: {ve}", file=sys.stderr)

    # 7. Uncertain — tried everything
    return "uncertain", "all detection methods exhausted"


def _all_text_sources(page):
    """Yield (text, source_label) for page + all iframes."""
    from apply.common.page_helpers import page_text as _pt
    try:
        yield (_pt(page) or "", "page")
    except Exception:
        pass
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            yield (fr.evaluate("() => document.body.innerText") or "", "iframe")
        except Exception:
            continue


def _form_still_present(page):
    """Check if form fields are still visible on the page (or in iframes)."""
    try:
        has_fields = page.evaluate("""() => {
            const els = document.querySelectorAll('input:not([type=hidden]), select, textarea');
            return Array.from(els).filter(el => el.offsetParent !== null).length;
        }""")
        if has_fields and has_fields > 0:
            return True
    except Exception:
        pass
    # Check iframes
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            has_fields = fr.evaluate("""() => {
                const els = document.querySelectorAll('input:not([type=hidden]), select, textarea');
                return Array.from(els).filter(el => el.offsetParent !== null).length;
            }""")
            if has_fields and has_fields > 0:
                return True
        except Exception:
            continue
    return False


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

    # GUARD: if we already clicked submit for this job, do NOT click again.
    # Instead, investigate the page to determine the outcome.
    # Only --force clears the flag (for manual retry after confirming
    # the previous attempt truly failed).
    submit_clicked = state.get("submit_clicked", False)
    if submit_clicked and not force:
        print(f"  GUARD: submit was already clicked — investigating page (no re-click)", file=sys.stderr)

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

            # GUARD investigation: if we already clicked submit, check the page
            # state without clicking again. Use the full confidence cascade.
            if submit_clicked and not force:
                url_before = state.get("external_url") or state.get("url") or ""
                submit_text_before = state.get("submit_text", "")
                pages_before = {id(p) for p in ctx.pages}
                outcome, reason = _determine_outcome(page, ctx, pages_before, url_before, submit_text_before)
                print(f"  INVESTIGATE: {outcome} — {reason}", file=sys.stderr)

                if outcome == "success":
                    mark_applied(jid)
                    emit_status("submitted", f"investigation: {reason}")
                    emit_next("verify")
                    return 0
                if outcome == "rejected":
                    state["submit_clicked"] = False
                    save_state(state)
                    emit_status("validation_error", f"investigation: {reason}")
                    emit_next("act --fill", "fix validation errors then resubmit")
                    return 1
                # uncertain — mark as applied (conservative, prevents duplicate)
                # but flag for human review
                print(f"  UNCERTAIN — marking as applied (conservative), flagging for review", file=sys.stderr)
                mark_applied(jid)
                emit_status("submitted", f"uncertain outcome — {reason}. Review recommended.")
                emit_next("verify", "please verify this submission was received")
                return 0

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
            url_before_click = page.url or ""
            submit_text_before = submit_text or ""
            if submit_text:
                print(f"  Found submit button: '{submit_text}'", file=sys.stderr)
                # Record that we're about to click submit. If the click succeeds
                # but success detection fails, the guard above ensures we
                # investigate instead of clicking again on the next run.
                state["submit_clicked"] = True
                state["submit_text"] = submit_text
                save_state(state)
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
                                # Click failed — don't leave submit_clicked set
                                state["submit_clicked"] = False
                                save_state(state)

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

                # Retry detection for slow-loading confirmations
                outcome, reason = "uncertain", ""
                for attempt in range(4):
                    outcome, reason = _determine_outcome(
                        page, ctx, pages_before, url_before_click, submit_text_before)
                    if outcome != "uncertain":
                        break
                    if attempt < 3:
                        time.sleep(2)

                print(f"  OUTCOME: {outcome} — {reason}", file=sys.stderr)

                if outcome == "success":
                    was_new = mark_applied(jid)
                    if was_new:
                        emit_status("submitted", reason)
                    else:
                        emit_status("already applied")
                    emit_next("verify")
                    return 0

                if outcome == "rejected":
                    errors = _get_validation_errors(page)
                    for e in errors[:5]:
                        print(f"    ! {e[:80]}", file=sys.stderr)
                    state["submit_errors"] = errors
                    state["submit_clicked"] = False  # form rejected — safe to retry
                    save_state(state)
                    emit_status("validation_error", f"{len(errors)} field(s) need fixing")
                    emit_next("act --fill", "fix validation errors then resubmit")
                    return 1

                # uncertain — click succeeded, no validation errors, no success signal
                # Mark as applied (conservative — prevents duplicate) but flag for review
                print(f"  UNCERTAIN — marking as applied (conservative), flagging for review", file=sys.stderr)
                mark_applied(jid)
                emit_status("submitted", f"uncertain outcome — {reason}. Review recommended.")
                emit_next("verify", "please verify this submission was received")
                return 0

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

            # Only reach here if we could not click submit at all
            emit_status("submit_failed", "could not click submit button")
            emit_next("act --inspect", "inspect page to find submit button")
            return 1
    except Exception as e:
        emit_error(f"submit failed: {e}")
        return 1
