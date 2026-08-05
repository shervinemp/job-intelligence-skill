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
    with chrome_session(state) as (page, ctx):
        url = state.get("external_url") or state.get("url", "")
        cur = page.url or ""
        if url and (not cur or "about:blank" in cur or "chrome-error" in cur):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1)

        nxt = _find_next_button(page)
        if nxt and _click_action(page, nxt["text"]):
            time.sleep(2)
            emit_status("navigated", f"clicked '{nxt['text']}'")
            # A3: a "Next"/"Continue" button on a review page may actually
            # SUBMIT. Surface the evidence so the orchestrator can veto the
            # next step if the clicked text is submit-like.
            _t = (nxt.get("text") or "").lower()
            if any(w in _t for w in ("submit", "send", "apply", "review")):
                print(f"  BUTTON_WARN: '{nxt['text']}' may submit — verify "
                      f"before proceeding (one-shot boundary)", file=sys.stderr)
            emit_next("fill")
            return 0

        emit_error("no Next/Continue button found")
        return 1
