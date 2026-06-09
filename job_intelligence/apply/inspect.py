"""inspect.py — Apply-pipeline page diagnostics.
Wraps inspect_lib with job context: state, platform, filled count, next-step hint.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state
from apply.common.output import emit_next, emit_error, emit_warn
from apply.common.page_manager import PageManager
from apply.common.inspect_lib import capture, probe_state


def run(jid, candidate=None):
    state = load_state()
    if state.get("jid") != jid:
        emit_error(f"state is for job {state.get('jid','?')}, not {jid}")
        print("  Run detect first.", file=sys.stderr)
        return

    b, ctx = connect()
    pm = PageManager(ctx, jid)
    ext = state.get("external_url", "")
    page, score, candidates = pm.find(fallback_url=ext)

    print(f"Open pages ({len(ctx.pages)}):", file=sys.stderr)
    for i, p in enumerate(ctx.pages):
        url = p.url[:100]
        match = " [MATCH]" if p == page else ""
        print(f"  [{i}] {url}{match}", file=sys.stderr)

    if not page:
        if candidate is not None and candidate < len(ctx.pages):
            page = ctx.pages[candidate]
            print(f"Picked page [{candidate}]: {page.url[:100]}", file=sys.stderr)
        else:
            emit_warn(f"no page matches job {jid}")
            print(f"  Wanted: {ext[:100] if ext else '?'}", file=sys.stderr)
            if ctx.pages:
                emit_next("model_choice")
            else:
                emit_next("none")
            return

    # Job-specific context
    print(f"URL: {page.url}", file=sys.stderr)
    print(f"Title: {page.title() or '?'}", file=sys.stderr)
    print(f"Platform: {state.get('platform', '?')}", file=sys.stderr)
    print(f"Filled: {state.get('filled', 0)} fields", file=sys.stderr)

    # Universal page capture + apply-specific probes
    capture(page, jid)
    fc, fields, buttons, page_type = probe_state(page)

    if fc > 0:
        emit_next("act --fill")
    else:
        emit_next("none")
