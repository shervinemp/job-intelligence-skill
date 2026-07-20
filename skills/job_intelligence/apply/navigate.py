#!/usr/bin/env python3
"""navigate.py — Store external_url in state, detect platform from URL.
No Playwright. Skyvern navigates itself."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from apply.common.output import emit_next, emit_error
from apply.common.registry import resolve as resolve_registry

STATE_PATH = os.path.join(
    os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji")),
    "state", "apply_state.json",
)


def run(jid):
    c = get_conn()
    r = c.execute("SELECT url, title, company, stage, state, external_url FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        sys.exit(1)
    url, title, company, stage, job_state, ext_url = (
        r["url"], r["title"], r["company"], r["stage"], r["state"], r["external_url"] or ""
    )
    if job_state != "active":
        print(f"ERROR: job {jid} is in state '{job_state}', not active", file=sys.stderr)
        sys.exit(1)

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    target_url = ext_url or url
    print(f"EXTERNAL_URL: {target_url}", file=sys.stderr)

    reg = resolve_registry(target_url)
    plat_name = reg.name if reg else ""
    if plat_name:
        print(f"PLATFORM: {plat_name}", file=sys.stderr)
    else:
        print(f"PLATFORM: unknown", file=sys.stderr)

    # Save state
    state = {"jid": jid, "external_url": target_url, "url": url,
             "title": title or "", "company": company or "", "platform": plat_name}
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH + ".tmp", "w") as f:
        json.dump(state, f, indent=2)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)

    emit_next("act --fill")
