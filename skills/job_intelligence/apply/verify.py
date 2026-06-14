#! /usr/bin/env python3
"""verify.py — Check if a job was submitted. No state mutation.
4 strategies: modal closed, success text, Applied button, DB stage.
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state
from apply.common.output import emit_next, emit_status, emit_error


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
        "thank you",
        "submitted",
        "your application",
        "has been sent",
        "application received",
    ]
    if page:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        if any(s in text for s in success_signals):
            get_conn().execute(
                "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
                ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid),
            ).connection.commit()
            emit_status("submitted (text match on page)")
            emit_next("none")
            return
    else:
        # No matching page — scan ALL pages for success text
        for p in ctx.pages:
            try:
                t = (p.evaluate("() => document.body.innerText") or "").lower()
                if any(s in t for s in success_signals):
                    get_conn().execute(
                        "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
                        ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid),
                    ).connection.commit()
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
                emit_status("submitted (modal closed, no inputs)")
                get_conn().execute(
                    "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
                    ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid),
                ).connection.commit()
                emit_next("none")
                return

        # Strategy 2: Success text in body
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        for signal in [
            "thank you",
            "submitted",
            "your application",
            "has been sent",
            "application received",
        ]:
            if signal in text:
                emit_status(f"submitted (text: '{signal}')")
                get_conn().execute(
                    "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
                    ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid),
                ).connection.commit()
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
        if "Applied" in buttons:
            emit_status("submitted (Applied button)")
            get_conn().execute(
                "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
                ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid),
            ).connection.commit()
            emit_next("none")
            return
    except Exception as e:
        emit_status("verify_error", f"page evaluate failed: {str(e)[:100]}")
        emit_next("act --inspect")
        return

    emit_status("unknown", f"DB stage: {stage}")
    emit_next("act --fill or check manually")
