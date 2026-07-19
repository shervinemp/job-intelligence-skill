"""verify.py — Check DB stage. Browser-free, Playwright-free."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from apply.common.output import emit_next, emit_status
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
    except ImportError:
        return False
    try:
        if not available():

def run(jid):
    row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not row:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        return
    if row["stage"] == "applied":
        emit_status("submitted")
        emit_next("none")
    else:
        print(f"STATUS: {row['stage']} (state={row['state']})", file=sys.stderr)
        emit_next("retry")
