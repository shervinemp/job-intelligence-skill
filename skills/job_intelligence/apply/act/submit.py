"""act/submit.py — Submit command with policy gate and outcome detection cascade."""
import json, sys, time

from lib.db import get_conn
from apply.common import terms as _T
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


def _determine_outcome(page, ctx, pages_before, url_before, submit_text_before,
                       target_url=""):
    """Multi-step confidence cascade. Tries everything before giving up."""
    from apply.common.signals import has_success_text, has_already_applied_text

    # 1. Success text signals (page + all iframes)
    for source, label in _all_text_sources(page):
        if has_success_text(source):
            return "success", f"success signal in {label}"
        # UNRECOVERABLE guard: 'already-applied' text must be on the TARGET
        # posting to count. A redirected/stale page showing it for a DIFFERENT
        # job must not mark THIS job applied — that false positive can never
        # be corrected (the job is already applied with no applied_at).
        if has_already_applied_text(source):
            _cur = (page.url or "").lower()
            _tgt = (target_url or "").lower()
            _on_target = bool(_cur and _tgt and
                              (_cur.split("?")[0].rstrip("/")
                               == _tgt.split("?")[0].rstrip("/")))
            if _on_target:
                return "success", f"already-applied text in {label} (on target)"
            print(f"  WARN: 'already-applied' text on a non-target page "
                  f"({_cur[:70]}) — NOT counting as success", file=sys.stderr)

    # 2. _check_submit_success (new pages, signals, iframes)
    success, _ = _check_submit_success(ctx, page, pages_before)
    if success:
        return "success", "check_submit_success"

    # 2.5 Validation errors — the form was rejected. Checked BEFORE the
    # URL/form-gone signals below: a submit click can navigate to a
    # review step (URL changes, form briefly absent) that carries
    # validation errors — claiming success there would be a false
    # positive that can never be corrected (job already marked applied).
    errors = _get_validation_errors(page)
    if errors:
        return "rejected", f"{len(errors)} validation error(s)"

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

    # 6.5 Session timeout — submit returned to login wall or error
    # text saying the session expired. This is NOT a submission
    # failure — the user needs to re-auth and retry. Emit a
    # distinct signal so the orchestrator knows to retry rather
    # than treat as rejected.
    try:
        session_expired = page.evaluate("""() => {
            const txt = (document.body.innerText || '').toLowerCase();
            // Signal patterns from common ATSs:
            //   - "Your session has expired"
            //   - "Please log in to continue"
            //   - "Session timed out"
            //   - Workday: returns to sign-in form with no greeting
            if (/session (has )?expired|session timed out|your session has expired/i.test(txt))
                return true;
            const visiblePws = Array.from(document.querySelectorAll('input[type="password"]'))
                .filter(p => p.offsetParent !== null);
            // If a password field reappeared with no greeting AND no
            // error text, likely session was invalidated mid-submit.
            if (visiblePws.length > 0) {
                if (/sign in|log in|please login/i.test(txt)
                    && !/invalid|incorrect|failed/i.test(txt)) return true;
            }
            return false;
        }""")
        if session_expired:
            return "session_expired", "session expired mid-submit — re-auth required"
    except Exception:
        pass

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
    stage = db_row["stage"]

    if stage == "applied":
        emit_status("already applied")
        emit_next("verify")
        return 0

    from apply.common.submit_policy import load_policy, resolve_mode
    from apply.common.gate import submit_decision
    pol = load_policy()
    mode = resolve_mode()
    _db_job = db_row
    _host = ""
    try:
        from urllib.parse import urlparse as _up
        _host = (_up(_db_job.get("url") or _db_job.get("external_url") or "")
                 .netloc or "").lower()
    except Exception:
        pass
    action, reason = submit_decision(mode, pol, host=_host)
    if action == _T.STATUS_BLOCKED:
        emit_status(_T.STATUS_BLOCKED, reason)
        emit_next("none", "kill-switch active — resume via apply_policy.json")
        return 1

    # F5 per-jid cross-process lock: a live submit and a shadow worker can
    # race on the same jid (the one-shot guard is per-jid state, not
    # cross-process locked). Serialize the guard-check + gate + click + outcome
    # for this jid. Released in the finally below (and by stale-owner reaping).
    from apply.common.jid_lock import JidLock
    _jidlock = JidLock(jid)
    _jidlock.acquire()
    # GATE: run check BEFORE the hold branch.
    #
    # The check used to sit below the hold return, so in shadow/hold mode
    # it never ran at all — yet the batch labelled those jobs
    # "fill+check OK, submit held (shadow)". That string was synthesised
    # purely from "submit returned 0", which in hold mode means only "the
    # policy said hold". 23 of 53 jobs reported ready had a REQUIRED field
    # with no answer. Shadow exists to PRODUCE evidence (ETHOS §7 step 2);
    # skipping the one validation step and then claiming it passed is the
    # opposite of that.
    check_rc = 0
    if not force:
        from apply.act.check import cmd_check
        print("  Pre-submit check...", file=sys.stderr)
        check_rc = cmd_check(jid)

    if action == _T.STATUS_HOLD:
        if check_rc != 0:
            emit_status(_T.STATUS_CHECK_FAILED,
                        f"{mode} mode — submit suppressed, AND the pre-submit "
                        f"check failed")
            emit_next("check", "fix the check errors before this job is ready")
            return 1
        emit_status(_T.STATUS_HOLD, reason)
        emit_next("none", "review form in browser, then run submit with policy=live")
        return 0

    if check_rc != 0:
        print("  CHECK FAILED — submit blocked. Use --force to override.", file=sys.stderr)
        emit_status(_T.STATUS_CHECK_FAILED, "run 'apply act --check' and fix errors first")
        emit_next("check", "fix errors then resubmit (or --force to override)")
        return 1

    # B2: same posting, two jids — the same posting can enter the pipeline via
    # email AND LinkedIn, producing two jids that each reach submit. The one-shot
    # guard is per-jid, so a duplicate submission would be possible. Refuse the
    # submit if another ACTIVE job carries the same posting URL (or the same
    # external apply URL) and is not already applied. --force overrides after
    # human verification.
    if not force:
        try:
            from lib.db import get_conn as _dedup_conn
            _c = _dedup_conn()
            _row = _c.execute(
                "SELECT url, external_url FROM jobs WHERE id=?", (jid,)).fetchone()
            _url = _row["url"] if _row else ""
            _ext = _row["external_url"] if _row else ""
            _probe = None
            if _url:
                _probe = _c.execute(
                    "SELECT id, stage FROM jobs WHERE url=? AND id != ? "
                    "AND state='active' AND stage != 'applied' LIMIT 1",
                    (_url, jid)).fetchone()
            if not _probe and _ext:
                _probe = _c.execute(
                    "SELECT id, stage FROM jobs WHERE external_url=? AND id != ? "
                    "AND state='active' AND stage != 'applied' LIMIT 1",
                    (_ext, jid)).fetchone()
            if _probe:
                print(f"  SAME-POSTING GATE: another active job {_probe['id']} "
                      f"(stage={_probe['stage']}) carries this posting URL — "
                      f"refusing duplicate submit (--force to override)",
                      file=sys.stderr)
                emit_status(_T.STATUS_BLOCKED,
                            "same posting already active under another jid")
                emit_next("none", "--force only after confirming they differ")
                return 1
        except Exception:
            pass

    # GATE: regression canary — if the latest dossier REGRESSED fields
    # that a previous run filled, the current state is suspect and the
    # submit is refused (--force is the explicit override). Enforcement,
    # not a warning: a regression means something broke between runs.
    if not force:
        try:
            from apply.preflight import preflight
            _pf = preflight()
            if _pf["hard_missing"]:
                print(f"  PREFLIGHT: profile still missing "
                      f"{', '.join(_pf['hard_missing'])} — submit proceeds, "
                      f"but related fields will be empty", file=sys.stderr)
        except Exception:
            pass
        try:
            from lib.automation.diff import load_handoffs, compare_handoffs
            from lib.config import RESULTS_DIR as _RD
            hs = load_handoffs(jid, _RD)
            if len(hs) >= 2:
                d = compare_handoffs(hs[0], hs[1])
                if d["regressed"]:
                    labels = ", ".join(lbl[:40] for lbl, _now in d["regressed"][:5])
                    print(f"  REGRESSION GATE: {len(d['regressed'])} field(s) "
                          f"regressed vs previous run: {labels}",
                          file=sys.stderr)
                    emit_status(_T.STATUS_REGRESSION_GATE,
                                "filled-before fields now fail — investigate before submit")
                    emit_next("diff", f"python3 report.py diff {jid} (or --force to override)")
                    return 1
        except Exception:
            pass

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
        print("  GUARD: submit was already clicked — investigating page (no re-click)", file=sys.stderr)

    url = state.get("external_url") or state.get("url", "")
    if not url:
        emit_error("no external_url in state")
        return 1

    browser_session_id = state.get("browser_session_id", "")

    # F3 session isolation: strip cookies of OTHER employers before the
    # one-shot action that transmits PII, so company A's session cannot
    # leak into company B's page (and vice versa).
    from apply.act.helpers import _host as _url_host
    _isolate = _url_host(url)

    try:
        with chrome_session(state, isolate_host=_isolate) as (page, ctx):
            cur = page.url or ""
            if not cur or "about:blank" in cur or "chrome-error" in cur:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

            if handle_captcha(page, state):
                emit_status("captcha", "CAPTCHA still present after timeout")
                state["status"] = _T.STATUS_CAPTCHA_REQUIRED
                save_state(state)
                return 1

            # Pre-flight: check for already-applied signals on any ATS.
            # A6: only believe "already applied" when we're on the TARGET
            # posting's URL — a redirect to a different page (home, search,
            # another listing) could show "already applied" for a DIFFERENT
            # job. Cross-check the current URL against the target before
            # marking applied.
            from apply.common.signals import has_already_applied_text
            from apply.common.page_helpers import page_text as _pt
            try:
                _ptxt = _pt(page) or ""
                _cur = (page.url or "").lower()
                _tgt = (url or "").lower()
                _on_target = bool(_cur and _tgt and
                                  (_cur.split("?")[0].rstrip("/")
                                   == _tgt.split("?")[0].rstrip("/")))
                if has_already_applied_text(_ptxt) and _on_target:
                    mark_applied(jid)
                    emit_status("already applied")
                    emit_next("verify")
                    return 0
                elif has_already_applied_text(_ptxt) and not _on_target:
                    print(f"  WARN: 'already applied' text on a page that is "
                          f"NOT the target posting ({_cur[:80]}) — NOT "
                          f"marking applied", file=sys.stderr)
            except Exception:
                pass

            if "linkedin.com/jobs" in (page.url or "").lower():
                dialog_open = page.evaluate("""() => {
                    const d = document.querySelector('dialog[data-testid="dialog"]');
                    return d && d.open && d.offsetParent !== null;
                }""")
                if dialog_open:
                    print("  Easy Apply: modal already open", file=sys.stderr)
                else:
                    ea_btn = page.locator('button:has-text("Easy Apply")').first
                    if ea_btn.count() > 0:
                        try:
                            ea_btn.click(timeout=5000)
                        except Exception:
                            ea_btn.click(force=True, timeout=5000)
                        print("  Easy Apply: modal opened", file=sys.stderr)
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
                outcome, reason = _determine_outcome(page, ctx, pages_before, url_before, submit_text_before, target_url=url)
                print(f"  INVESTIGATE: {outcome} — {reason}", file=sys.stderr)

                if outcome == "success":
                    mark_applied(jid)
                    emit_status(_T.OUTCOME_SUBMITTED, f"investigation: {reason}")
                    emit_next("verify")
                    return 0
                if outcome == "rejected":
                    state["submit_clicked"] = False
                    save_state(state)
                    emit_status("validation_error", f"investigation: {reason}")
                    emit_next("act --fill", "fix validation errors then resubmit")
                    return 1
                if outcome == "session_expired":
                    # Session died between fill and submit — form was
                    # silently invalidated. Don't mark as applied
                    # (submit didn't go through), clear the guard so
                    # user can re-auth and retry.
                    state["submit_clicked"] = False
                    save_state(state)
                    emit_status("session_expired", f"investigation: {reason}")
                    emit_next("login",
                              "re-auth then 'act --fill' to regenerate the form "
                              "and resubmit")
                    return 1
                # uncertain - mark as applied (conservative, prevents duplicate)
                # but flag for human review
                print("  UNCERTAIN - marking as applied (conservative), flagging for review", file=sys.stderr)
                mark_applied(jid)
                emit_status(_T.OUTCOME_SUBMITTED, f"uncertain outcome - {reason}. Review recommended.")
                emit_next("verify", "please verify this submission was received")
                return 0

            # LinkedIn Easy Apply: navigate to review/submit page
            if "linkedin.com" in (page.url or ""):
                from apply.common.page_state import has_dialog as _has_dialog
                prev_next_text = ""
                stuck_count = 0
                for _nav in range(10):
                    if not _has_dialog(page):
                        break
                    submit_btn = _detect_submit_button(page)
                    if submit_btn:
                        print("  Found submit button on review page", file=sys.stderr)
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
                                    print("  Clicked submit inside iframe", file=sys.stderr)
                                    break
                            except Exception:
                                continue
                        if iframe_clicked:
                            clicked = True
                        else:
                            try:
                                page.evaluate(f"""() => {{
                                    const target = {json.dumps(submit_text.lower())};
                                    const inDlg = (el) => !!el.closest('dialog, [role="dialog"]');
                                    const isDisabled = (el) => el.disabled || el.getAttribute('aria-disabled') === 'true';
                                    const all = Array.from(document.querySelectorAll('button'));
                                    // Prefer dialog buttons (Easy Apply), don't filter by offsetParent
                                    const sorted = all.sort((a, b) => (inDlg(b) ? 1 : 0) - (inDlg(a) ? 1 : 0));
                                    for (const btn of sorted) {{
                                        if (isDisabled(btn)) continue;
                                        if ((btn.textContent || '').trim().toLowerCase() === target) {{ btn.click(); return true; }}
                                    }}
                                    for (const btn of sorted) {{
                                        if (isDisabled(btn)) continue;
                                        if ((btn.textContent || '').trim().toLowerCase().includes(target)) {{ btn.click(); return true; }}
                                    }}
                                    return false;
                                }}""")
                                clicked = True
                            except Exception:
                                print("  Could not click submit button via Playwright", file=sys.stderr)
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
                        page, ctx, pages_before, url_before_click, submit_text_before, target_url=url)
                    if outcome != "uncertain":
                        break
                    if attempt < 3:
                        time.sleep(2)

                print(f"  OUTCOME: {outcome} — {reason}", file=sys.stderr)

                if outcome == "success":
                    was_new = mark_applied(jid)
                    if was_new:
                        emit_status(_T.OUTCOME_SUBMITTED, reason)
                    else:
                        emit_status("already applied")
                    emit_next("verify")
                    return 0

                if outcome == "rejected":
                    errors = _get_validation_errors(page)
                    for e in errors[:5]:
                        print(f"    ! {e[:80]}", file=sys.stderr)
                    state["submit_errors"] = errors
                    state["submit_clicked"] = False  # form rejected - safe to retry
                    save_state(state)
                    emit_status("validation_error", f"{len(errors)} field(s) need fixing")
                    # A1: the validation errors are EVIDENCE, surfaced outward
                    # to the orchestrator (S2 dossier + S5 outcome evidence),
                    # not consumed locally. The orchestrator reads them in
                    # tandem with the fill dossier and answers the fix via
                    # --answers; ask_api is not invoked for this text.
                    emit_next("act --fill",
                              "validation errors are in the dossier — answer "
                              "them with --answers, then re-fill")
                    return 1

                if outcome == "session_expired":
                    # Form silently invalidated mid-fill. Do NOT mark as
                    # applied (submit didn't go through). Clear guard so
                    # user can re-auth and retry without --force.
                    state["submit_clicked"] = False
                    state["submit_errors"] = [reason]
                    save_state(state)
                    emit_status("session_expired", reason)
                    emit_next("login",
                              "re-authenticate in Chrome then 'act --fill' to "
                              "regenerate form and resubmit (no --force needed)")
                    return 1

                # uncertain — click succeeded, no validation errors, no success signal
                # Mark as applied (conservative — prevents duplicate) but flag for review
                print("  UNCERTAIN — marking as applied (conservative), flagging for review", file=sys.stderr)
                mark_applied(jid)
                emit_status(_T.OUTCOME_SUBMITTED, f"uncertain outcome — {reason}. Review recommended.")
                emit_next("verify", "please verify this submission was received")
                return 0

            if not clicked:
                empt = _empty_required(page)
                if empt:
                    print(f"  {empt} required field(s) empty — cannot submit", file=sys.stderr)
                    emit_status("incomplete", f"{empt} required field(s) need answers")
                    emit_next("act --fill", "supply answers for empty fields, then resubmit")
                    return 1
                # No form/dialog/iframe form on the page — nothing to submit against.
                # This catches expired jobs on any platform (LinkedIn, Workday,
                # Ashby, etc.) where the page loaded but no form rendered.
                from apply.common.page_state import has_any_form
                from apply.common.registry import resolve as _resolve_reg
                _reg = _resolve_reg(page.url)
                _cwd = _reg.widgets if _reg and hasattr(_reg, 'widgets') else None
                if not has_any_form(page, custom_widget_selectors=_cwd):
                    print("  No form, dialog, or iframe form found", file=sys.stderr)
                    state["status"] = _T.STATUS_NO_APPLY_PATH
                    save_state(state)
                    emit_status(_T.STATUS_NO_APPLY_PATH, "no form or submit button — job may be expired")
                    emit_next("none", "check if job is still active or apply via external URL")
                    return 1

                # Keyboard-based submit — works even across cross-origin
                # iframes since keyboard events propagate natively.
                # Tries several keyboard strategies in order.
                print("  Submit not found — trying keyboard methods", file=sys.stderr)
                keyboard_clicked = False
                url_before_kb = page.url or ""
                submit_text_before_kb = ""

                # 1. Ctrl+Enter — universal web form shortcut
                try:
                    page.keyboard.press('Control+Enter')
                    time.sleep(2)
                    pages_after = {id(p) for p in ctx.pages}
                    outcome, reason = _determine_outcome(
                        page, ctx, pages_after, url_before_kb, submit_text_before_kb)
                    if outcome == "success":
                        keyboard_clicked = True
                        print(f"  KEYBOARD: Ctrl+Enter succeeded ({reason})",
                              file=sys.stderr)
                except Exception:
                    pass

                # 2. Tab+Enter — move focus past last field and click
                if not keyboard_clicked:
                    for tab_count in range(1, 4):
                        try:
                            for _ in range(tab_count):
                                page.keyboard.press('Tab')
                                time.sleep(0.3)
                            page.keyboard.press('Enter')
                            time.sleep(2)
                            pages_after = {id(p) for p in ctx.pages}
                            outcome, reason = _determine_outcome(
                                page, ctx, pages_after, url_before_kb, submit_text_before_kb)
                            if outcome == "success":
                                keyboard_clicked = True
                                print(f"  KEYBOARD: {tab_count}×Tab+Enter ({reason})",
                                      file=sys.stderr)
                                break
                        except Exception:
                            continue

                if keyboard_clicked:
                    was_new = mark_applied(jid)
                    if was_new:
                        emit_status(_T.OUTCOME_SUBMITTED, "keyboard submit")
                    else:
                        emit_status("already applied")
                    emit_next("verify")
                    return 0

            # Only reach here if we could not click submit at all
            emit_status("submit_failed", "could not click submit button")
            emit_next("act --inspect", "inspect page to find submit button")
            return 1
    except Exception as e:
        emit_error(f"submit failed: {e}")
        return 1
    finally:
        # Release the per-jid lock on every exit path (success, guard, error).
        try:
            _jidlock.release()
        except Exception:
            pass
