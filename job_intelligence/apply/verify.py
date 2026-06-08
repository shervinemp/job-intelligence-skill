#! /usr/bin/env python3
"""verify.py — Check if a job was submitted. No state mutation.
4 strategies: modal closed, success text, Applied button, DB stage.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state, is_aggregator, set_platform_trusted

def _maybe_trust(state):
    """Set platform trust on verified submission success."""
    platform = state.get("platform", "")
    domain = state.get("external_url", "") or state.get("url", "")
    if platform and not is_aggregator(domain):
        set_platform_trusted(platform)

def run(jid):
    db_stage = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if not db_stage: print(f"ERROR: job {jid} not found", file=sys.stderr); sys.exit(1)
    stage = db_stage["stage"]

    if stage == "applied":
        print("STATUS: submitted (DB)\nNEXT: none")
        return

    b, ctx = connect()
    state = load_state()
    page = None
    for p in ctx.pages:
        if not p.url or "about:blank" in p.url or "chrome-error" in p.url:
            continue
        if jid in p.url or (state.get("external_url", "") and state["external_url"] in p.url):
            page = p
            break

    if not page:
        page = ctx.pages[0] if ctx.pages else None

    if not page:
        print(f"STATUS: unknown (no active pages)\nNEXT: act --fill or check manually")
        return

    # Strategy 1: Modal closed (Easy Apply)
    has_modal = page.evaluate("() => !!document.querySelector('[role=\"dialog\"]')")
    if not has_modal:
        # Check if form inputs are also gone
        has_inputs = page.evaluate("""() => {
            const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
            return inputs.length > 0 && Array.from(inputs).some(i => i.offsetParent !== null);
        }""") or False
        if not has_inputs:
            print("STATUS: submitted (modal closed, no inputs)")
            get_conn().execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid)).connection.commit()
            _maybe_trust(state)
            print("NEXT: none")
            return

    # Strategy 2: Success text in body
    text = (page.evaluate("() => document.body.innerText") or "").lower()
    for signal in ["thank you", "submitted", "your application", "has been sent", "application received"]:
        if signal in text:
            print(f"STATUS: submitted (text: '{signal}')")
            get_conn().execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid)).connection.commit()
            _maybe_trust(state)
            print("NEXT: none")
            return

    # Strategy 3: "Applied" button visible
    buttons = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('button'))
            .filter(b => b.offsetParent)
            .map(b => b.textContent.trim());
    }""")
    if "Applied" in buttons:
        print("STATUS: submitted (Applied button)")
        get_conn().execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("applied", time.strftime("%Y-%m-%dT%H:%M:%S"), jid)).connection.commit()
        _maybe_trust(state)
        print("NEXT: none")
        return

    print(f"STATUS: unknown (DB stage: {stage})\nNEXT: act --fill or check manually")
