#! /usr/bin/env python3
"""verify.py — Check if a job was submitted. No state mutation.
4 strategies: modal closed, success text, Applied button, DB stage.
"""
import os, sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state, page_text, mark_applied
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.signals import SUCCESS_STRICT, has_success_text


# Tokens that, in a post-submit URL, strongly indicate a confirmation page.
# Conservative set — avoids generic words like "complete" that appear pre-submit.
_CONFIRM_URL_TOKENS = (
    "thank", "thankyou", "success", "confirmation", "confirmed",
    "submitted", "application-received", "received", "applied",
)


def _is_confirmation_url(url):
    """True if the URL path/query looks like a post-submit confirmation page."""
    try:
        u = urlparse(url or "")
        hay = (u.path + "?" + (u.query or "")).lower()
    except Exception:
        return False
    return any(t in hay for t in _CONFIRM_URL_TOKENS)


def _registrable_domain(url):
    """Last two host labels ('careers.foo.com' -> 'foo.com'). Coarse but adequate
    for scoping the redirect scan to the ATS's site."""
    try:
        host = urlparse(url or "").netloc.lower().split(":")[0]
    except Exception:
        return ""
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _vision_confirms(page, jid):
    """Last-resort: ask the vision model if the page shows a successful submission.
    Only call when deterministic signals were inconclusive AND the endpoint is up.
    Returns True only on a clear YES."""
    try:
        from lib.ask_api import available, ask
        if not available():
            return False
        from apply.common.inspect_lib import page_jpeg
        import tempfile
        fd, img = tempfile.mkstemp(suffix=".jpg")
        with os.fdopen(fd, "wb") as _f:
            _f.write(page_jpeg(page, full=False))
        try:
            reply, err = ask(
                img,
                "Did this job application submit successfully? Answer only YES or NO.",
            )
        finally:
            try:
                os.unlink(img)
            except Exception:
                pass
        if err:
            return False
        ans = (reply or "").strip().lower()
        if ans.startswith("yes"):
            return True
        if ans:
            print(f"  VISION: {ans[:60]}", file=sys.stderr)
    except Exception as e:
        print(f"  VISION_SKIP: {e}", file=sys.stderr)
    return False


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
    if state.get("jid") != jid:
        # Stale state from another job — never use its URL/flags for this one.
        state = {}
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

    if page:
        text = page_text(page) or ""
        if has_success_text(text):
            mark_applied(jid)
            emit_status("submitted (text match on page)")
            emit_next("none")
            return
        if _is_confirmation_url(page.url):
            mark_applied(jid)
            emit_status(f"submitted (confirmation URL: {page.url[:60]})")
            emit_next("none")
            return
    else:
        # No matching page — scan same-site pages only (handles post-submit
        # redirects within the ATS domain). Never scan unrelated tabs: this runs
        # against the user's real Chrome, and generic phrases like "has been sent"
        # appear in webmail etc.
        site = _registrable_domain(state.get("external_url", ""))
        if site:
            for p in ctx.pages:
                try:
                    if _registrable_domain(p.url) != site:
                        continue
                    if has_success_text(page_text(p)) or _is_confirmation_url(p.url):
                        mark_applied(jid)
                        emit_status("submitted (same-site redirect)")
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
                mark_applied(jid)
                emit_status("submitted (modal closed, no inputs)")
                emit_next("none")
                return

        # Strategy 2: Success text in body (including shadow DOM)
        text = (page_text(page) or "").lower()
        for signal in SUCCESS_STRICT:
            if signal in text:
                mark_applied(jid)
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
            mark_applied(jid)
            emit_status("submitted (Applied button)")
            emit_next("none")
            return
    except Exception as e:
        emit_status("verify_error", f"page evaluate failed: {str(e)[:100]}")
        emit_next("act --inspect")
        return

    # Last-resort: auto vision check (only if the endpoint is reachable).
    if page and _vision_confirms(page, jid):
        mark_applied(jid)
        emit_status("submitted (vision last-resort)")
        emit_next("none")
        return

    # Capture the screenshot as an audit artifact (and a manual-check fallback when
    # no vision endpoint is configured).
    if page:
        try:
            from apply.common.inspect_lib import capture
            img_path = capture(page, str(jid), prefix="verify")
            print(f"  Verify screenshot: {img_path}", file=sys.stderr)
        except Exception:
            pass

    emit_status("unknown", f"DB stage: {stage}" + (f", last: {last_submit}" if last_submit else ""))
    emit_next("act --fill or check manually")
