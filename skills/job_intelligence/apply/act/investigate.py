"""act/investigate.py — Deep analysis of unknown-platform forms."""
import json, os, sys, time

from apply.common.output import emit_next, emit_status, emit_error
from apply.common.page_helpers import load_state, save_state, handle_captcha
from apply.act.helpers import chrome_session, RESULTS_DIR


def cmd_investigate(jid):
    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    url = state.get("external_url") or state.get("url", "")
    if not url:
        emit_error("no url in state — run 'apply navigate <jid>' first")
        return 1

    from apply.common.inspector import probe as probe_page
    from apply.common.registry import resolve as resolve_registry

    with chrome_session(state) as (page, ctx):
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        if handle_captcha(page, state):
            emit_status("captcha", "CAPTCHA still present after timeout")
            return 1
        pr = probe_page(page, registry_config=resolve_registry(page.url), jid=jid)
        if pr.field_count > 0:
            print(f"  Probe found {pr.field_count} fields (strategy={pr.strategy}):", file=sys.stderr)
            for f in pr.fields:
                print(f"    [{f.get('type','?')}] {f.get('label','?')}", file=sys.stderr)
            emit_status("investigated", f"{pr.field_count} fields via {pr.strategy} — no Skyvern needed")
            emit_next("act --fill")
            return 0

    from lib.ask_api import available as _vision_available
    if _vision_available():
        from lib.ask_api import ask_bytes
        from apply.common.inspect_lib import form_jpeg
        print(f"  DOM probe found nothing — analyzing with vision (1 LLM call)...", file=sys.stderr)
        try:
            img = form_jpeg(page)
            reply, err = ask_bytes(
                img,
                "Analyze this job application form. List every visible form field "
                "as 'LABEL | TYPE | REQUIRED | OPTIONS' lines. "
                "Also state: is this multi-page? What buttons exist (Next, Submit, etc.)?",
                max_tokens=2048,
            )
            if not err and reply:
                rd = os.path.join(RESULTS_DIR, jid)
                os.makedirs(rd, exist_ok=True)
                rpt_path = os.path.join(rd, "investigate_report.json")
                with open(rpt_path, "w", encoding="utf-8") as fh:
                    json.dump({"url": url, "method": "ask_api", "analysis": reply}, fh, indent=2)
                state["investigate_report"] = rpt_path
                save_state(state)
                print(f"  Report saved: {rpt_path}", file=sys.stderr)
                print(f"  Vision analysis:\n{reply[:500]}", file=sys.stderr)
                emit_status("investigated", f"report at {rpt_path}")
                emit_next("act --fill")
                return 0
            if err:
                print(f"  VISION_FAIL: {err} — falling back to Skyvern", file=sys.stderr)
        except Exception as ve:
            print(f"  VISION_FAIL: {ve} — falling back to Skyvern", file=sys.stderr)

    print(f"  Running Skyvern investigator (slow, 10-step agent)...", file=sys.stderr)
    from apply.common.skyvern_bridge import SkyvernExtraction
    report = SkyvernExtraction().investigate_form(url, timeout=300)
    if not report:
        emit_error("Skyvern investigation returned nothing")
        return 1

    rd = os.path.join(RESULTS_DIR, jid)
    os.makedirs(rd, exist_ok=True)
    rpt_path = os.path.join(rd, "investigate_report.json")
    with open(rpt_path, "w", encoding="utf-8") as fh:
        json.dump({"url": url, **report}, fh, indent=2)
    state["investigate_report"] = rpt_path
    save_state(state)

    fields = (report.get("fields") or {})
    n = len(fields.get("fields", [])) if isinstance(fields, dict) else 0
    print(f"  Report saved: {rpt_path}", file=sys.stderr)
    print(f"  Skyvern saw {n} fields, multi_page={fields.get('multi_page') if isinstance(fields, dict) else '?'}", file=sys.stderr)
    emit_status("investigated", f"report at {rpt_path}")
    emit_next("none", "write a registry YAML for this platform from the report")
    return 0
