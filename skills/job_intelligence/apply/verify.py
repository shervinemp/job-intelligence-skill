#! /usr/bin/env python3
"""verify.py — Check if a job was submitted. No state mutation.
4 strategies: modal closed, success text, Applied button, DB stage.
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state, page_text
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.apply_state import clear as _as_clear
from apply.common.resolve import promote_session_cache


def run(jid):
    db_row = (
        get_conn().execute("SELECT stage, state FROM jobs WHERE id=?", (jid,)).fetchone()
    )
    if not db_row:
        emit_error(f"job {jid} not found")
        sys.exit(1)
    stage, job_state = db_row["stage"], db_row["state"]

    if job_state != "active":
        emit_error(f"job is in state '{job_state}', not active")
        sys.exit(1)

    if stage == "applied":
        emit_status("submitted (DB)")
        emit_next("none")
        return

    b, ctx = connect()
    state = load_state()
    last_submit = state.get("_last_submit", "")

    # Context from previous stage: guide what to look for
    if last_submit in ("submitted", "submitted (via AJAX)"):
        # Previous stage thinks it succeeded — confirm or flag false positive
        pass  # proceed to DOM checks below
    elif last_submit == "validation_error":
        emit_status("validation_error", "previous submit had field errors")
        emit_next("act --fill")
        return
    elif last_submit == "captcha":
        emit_status("captcha", "CAPTCHA blocked submission")
        emit_next("solve captcha then retry")
        return

    page = None
    for p in ctx.pages:
        if not p.url or "about:blank" in p.url or "chrome-error" in p.url:
            continue
        if jid in p.url or (
            state.get("external_url", "") and state["external_url"] in p.url
        ):
            page = p
            break

    # Fallback: scan all pages for success text (handles cross-domain redirects)
    success_signals = [
        "your application has been",
        "your application was",
        "has been sent",
        "application received",
        "you have applied",
    ]
    if page:
        text = (page_text(page) or "").lower()
        if any(s in text for s in success_signals):
            _mark_applied(jid)
            emit_status("submitted (text match on page)")
            emit_next("none")
            return
    else:
        # No matching page — scan ALL pages for success text
        for p in ctx.pages:
            try:
                t = (page_text(p) or "").lower()
                if any(s in t for s in success_signals):
                    _mark_applied(jid)
                    emit_status("submitted (cross-domain redirect)")
                    emit_next("none")
                    return
            except Exception:
                pass
        emit_status("unknown", "no active pages")
        emit_next("act --fill or check manually")
        return

    # Strategy 1: Modal closed (Easy Apply)
    try:
        has_modal = page.evaluate("() => !!document.querySelector('[role=\"dialog\"]')")
        if not has_modal:
            has_inputs = (
                page.evaluate(
                    """() => {
                const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
                return inputs.length > 0 && Array.from(inputs).some(i => i.offsetParent !== null);
            }"""
                )
                or False
            )
            if not has_inputs:
                _mark_applied(jid)
                emit_status("submitted (modal closed, no inputs)")
                emit_next("none")
                return

        # Strategy 2: Success text in body (including shadow DOM)
        text = (page_text(page) or "").lower()
        for signal in success_signals:
            if signal in text:
                _mark_applied(jid)
                emit_status(f"submitted (text: '{signal}')")
                emit_next("none")
                return

        # Strategy 3: "Applied" button visible
        buttons = page.evaluate(
            """() => {
            return Array.from(document.querySelectorAll('button'))
                .filter(b => b.offsetParent)
                .map(b => b.textContent.trim());
        }"""
        )
        if any(b.lower() == "applied" for b in buttons):
            print(f"  SIGNAL: Applied button found (high confidence)", file=sys.stderr)
            _mark_applied(jid)
            emit_status("submitted (Applied button)")
            emit_next("none")
            return
    except Exception as e:
        emit_status("verify_error", f"page evaluate failed: {str(e)[:100]}")
        emit_next("act --inspect")
        return

    # Screenshot for optional LLM verification (I run ask_api manually if needed)
    if page:
        try:
            from apply.common.inspect_lib import capture
            img_path = capture(page, str(jid), prefix="verify")
            print(f"  Verify screenshot: {img_path}", file=sys.stderr)
            print(f"  Run 'lib/ask_api.py --img {img_path} --prompt \"check for success message\"' for vision verification", file=sys.stderr)
        except Exception:
            pass

    emit_status("unknown", f"DB stage: {stage}" + (f", last: {last_submit}" if last_submit else ""))
    emit_next("act --fill or check manually")


def _mark_applied(jid):
    get_conn().execute(
        "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
        ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid),
    ).connection.commit()
    _as_clear(jid)
    # Promote session-cached LLM selections that passed the two-encounter rule
    try:
        promote_session_cache()
    except Exception:
        pass
