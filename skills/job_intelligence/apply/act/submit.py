"""act/submit.py — Submit command with policy gate, Playwright + Skyvern fallback."""
import json, os, sys, time

from lib.db import get_conn
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.page_helpers import load_state, save_state, handle_captcha, mark_applied
from apply.act.helpers import (
    _load_profile, chrome_session, _probe_form, _fill_with_playwright,
    _empty_required, _detect_submit_button, _dismiss_confirm_modal,
    _check_submit_success, _get_validation_errors,
)


def cmd_submit(jid, confirm=False):
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
                from apply.common.registry import resolve as resolve_registry
                profile = _load_profile()
                pr = _probe_form(page, resolve_registry(page.url), jid, allow_vision=False)
                fields = pr.fields or []
                if fields:
                    refilled, _ = _fill_with_playwright(page, fields, profile, None)
                    if refilled:
                        print(f"  Re-fill: {len(refilled)} fields restored/confirmed", file=sys.stderr)
                empt = _empty_required(page)
                if empt:
                    print(f"  WARN: {empt} required field(s) still empty before submit", file=sys.stderr)
            except Exception as re_:
                print(f"  Re-fill skipped: {re_}", file=sys.stderr)

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
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass

                _dismiss_confirm_modal(page)
                time.sleep(1)

                pages_before = {id(p) for p in ctx.pages}
                success, success_page = _check_submit_success(ctx, page, pages_before)
                if success:
                    mark_applied(jid)
                    emit_status("submitted", "Playwright clicked submit")
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
