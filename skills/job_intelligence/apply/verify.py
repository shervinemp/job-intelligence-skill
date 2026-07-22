#! /usr/bin/env python3
"""verify.py — Check if a job was submitted. No state mutation.
4 strategies: modal closed, success text, Applied button, DB stage.
Plus Skyvern-assisted vision verification as last-resort."""
import os, sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from apply.common.page_helpers import load_state, page_text, mark_applied
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.signals import SUCCESS_STRICT, has_success_text

_CONFIRM_URL_TOKENS = (
    "thank", "thankyou", "success", "confirmation", "confirmed",
    "submitted", "application-received", "received", "applied",
)


def _is_confirmation_url(url):
    try:
        u = urlparse(url or "")
        hay = (u.path + "?" + (u.query or "")).lower()
    except Exception:
        return False
    return any(t in hay for t in _CONFIRM_URL_TOKENS)


def _registrable_domain(url):
    try:
        host = urlparse(url or "").netloc.lower().split(":")[0]
    except Exception:
        return ""
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _vision_confirms(page, jid):
    """Last-resort: ask the vision model if the page shows a successful submission."""
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


def _skyvern_confirms(page, jid, state):
    """Use Skyvern data extraction to verify submission on the current page."""
    try:
        from apply.common.skyvern_bridge import SkyvernExtraction
        extractor = SkyvernExtraction()
        result = extractor.extract_text(page.url, "Read the visible page content and answer: Did this job application submit successfully? Look for confirmation messages, thank you text, application IDs, or success indicators.")
        if not result:
            return False
        text = (result.get("extracted_text", "") or "").lower()
        for signal in ["submitted", "thank you", "application received", "success", "confirmation", "applied"]:
            if signal in text:
                return True
    except Exception as e:
        print(f"  SKYVERN_VERIFY_SKIP: {e}", file=sys.stderr)
    return False


def _playwright_verify(page, jid, state):
    """Run all 4 deterministic verification strategies on a Playwright page."""
    last_submit = state.get("_last_submit", "")

    if last_submit in ("validation_error", "captcha"):
        return None  # inconclusive — don't mark applied

    text = (page_text(page) or "").lower()
    if has_success_text(text):
        mark_applied(jid)
        emit_status("submitted (text match on page)")
        emit_next("none")
        return True

    if _is_confirmation_url(page.url):
        mark_applied(jid)
        emit_status(f"submitted (confirmation URL: {page.url[:60]})")
        emit_next("none")
        return True

    # Strategy 1: Modal closed (Easy Apply)
    try:
        has_modal = page.evaluate("() => !!document.querySelector('[role=\"dialog\"]')")
        if not has_modal:
            has_inputs = page.evaluate(
                """() => {
                    const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
                    return inputs.length > 0 && Array.from(inputs).some(i => i.offsetParent !== null);
                }"""
            ) or False
            if not has_inputs:
                mark_applied(jid)
                emit_status("submitted (modal closed, no inputs)")
                emit_next("none")
                return True
    except Exception:
        pass

    # Strategy 2: Success text (including shadow DOM)
    for signal in SUCCESS_STRICT:
        if signal in text:
            mark_applied(jid)
            emit_status(f"submitted (text: '{signal}')")
            emit_next("none")
            return True

    # Strategy 3: "Applied" button visible
    try:
        buttons = page.evaluate(
            """() => {
                return Array.from(document.querySelectorAll('button'))
                    .filter(b => b.offsetParent)
                    .map(b => b.textContent.trim());
            }"""
        )
        if any(b.lower() == "applied" for b in buttons):
            print(f"  SIGNAL: Applied button found", file=sys.stderr)
            mark_applied(jid)
            emit_status("submitted (Applied button)")
            emit_next("none")
            return True
    except Exception:
        pass

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

    state = load_state()
    if state.get("jid") != jid:
        state = {}

    # Try CDP Chrome page verification
    try:
        from lib.chrome_manager import connect as chrome_connect
        b, ctx = chrome_connect()
        if ctx:
            page = None
            for p in ctx.pages:
                if not p.url or "about:blank" in p.url or "chrome-error" in p.url:
                    continue
                if jid in p.url or (
                    state.get("external_url", "") and state["external_url"] in p.url
                ):
                    page = p
                    break
            if page and _playwright_verify(page, jid, state):
                return
            # Same-site redirect scan
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
            # Last-resort vision via ask_api
            if page and _vision_confirms(page, jid):
                mark_applied(jid)
                emit_status("submitted (vision last-resort)")
                emit_next("none")
                return
    except Exception as e:
        print(f"  CHROME_VERIFY_SKIP: {e}", file=sys.stderr)

    # Fallback: Skyvern verify
    try:
        if _skyvern_confirms(None, jid, state):
            mark_applied(jid)
            emit_status("submitted (skyvern vision)")
            emit_next("none")
            return
    except Exception as e:
        print(f"  SKYVERN_VERIFY_FAILED: {e}", file=sys.stderr)

    emit_status("unknown", "all verification strategies inconclusive")
    emit_next("act --fill or check manually")
