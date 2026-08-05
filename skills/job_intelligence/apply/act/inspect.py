"""act/inspect.py — Inspect and Next commands."""
import sys, time

from apply.common.output import emit_next, emit_status, emit_error
from apply.common.page_helpers import load_state
from apply.act.helpers import chrome_session, _find_next_button, _click_action


def cmd_inspect(jid):
    from lib.ask_api import available as _vision_available
    from lib.chrome_manager import CDP_URL

    state = load_state()

    with chrome_session(state) as (page, ctx):
        url = state.get("external_url") or state.get("url", "")
        if url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
            except Exception as e:
                print(f"  GOTO_ERR: {e}", file=sys.stderr)

        from apply.common.inspect_lib import capture
        from apply.common.page_helpers import read_page, scan_actions

        jid = state.get("jid", jid)
        img_path = capture(page, jid, prefix="inspect")
        print(f"  IMG: {img_path}", file=sys.stderr)

        info = read_page(page)
        print(f"  FIELDS: {info.get('fieldCount', 0)} detected", file=sys.stderr)
        for f in info.get("fields", []):
            opts = f.get("options", [])
            opt_str = f" ({len(opts)} options)" if opts else ""
            print(f"    [{f.get('type','?')}] {f.get('label','?')}{opt_str}", file=sys.stderr)

        submit_candidates = scan_actions(page, ["submit", "send", "apply", "next", "continue"])
        print("  BUTTONS:", file=sys.stderr)
        for c in submit_candidates[:10]:
            print(f"    [{c.get('score',0)}] '{c.get('text','')}' ({c.get('tag','')})", file=sys.stderr)

        print(f"  URL: {page.url[:120]}", file=sys.stderr)
        print(f"  CDP: {CDP_URL}", file=sys.stderr)

        if _vision_available():
            print(f"  ask: lib/ask_api.py --img {img_path} --prompt '?'", file=sys.stderr)

    return 0


def cmd_next(jid):
    state = load_state()
    # CURVEBALL C2: if submit was already clicked (uncertain outcome), --next
    # must not click more buttons on a possibly-submitted page — route to the
    # investigation path instead.
    if state.get("submit_clicked"):
        print(f"  GUARD: submit was already clicked for {jid} — --next refused; "
              f"investigate the outcome instead", file=sys.stderr)
        emit_next("act --submit", "the submit path investigates (never re-clicks)")
        return 1
    with chrome_session(state) as (page, ctx):
        url = state.get("external_url") or state.get("url", "")
        cur = page.url or ""
        if url and (not cur or "about:blank" in cur or "chrome-error" in cur):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1)

        nxt = _find_next_button(page)
        if not nxt:
            emit_error("no Next/Continue button found")
            return 1
        _t = (nxt.get("text") or "").lower()
        # CURVEBALL C1: a "Continue"/"Review" button on the final page can be
        # the SUBMIT. Clicking it here bypasses the one-shot submit gate (the
        # guard, policy check, and domain gate live in cmd_submit). Refuse to
        # click submit-like buttons and route to the gated submit path instead.
        _SUBMIT_LIKE = ("submit", "send", "apply now", "continue to review",
                        "continue to submit", "review and submit")
        if any(w in _t for w in _SUBMIT_LIKE):
            print(f"  BUTTON_GATE: '{nxt['text']}' looks like a SUBMIT — "
                  f"routing to the gated submit path (one-shot safety)",
                  file=sys.stderr)
            emit_next("submit", f"python3 apply.py act --submit {jid} — "
                      "the gated submit path (not --next)")
            return 1
        if _click_action(page, nxt["text"]):
            time.sleep(2)
            emit_status("navigated", f"clicked '{nxt['text']}'")
            emit_next("fill")
            return 0

        emit_error(f"could not click '{nxt['text']}'")
        return 1
