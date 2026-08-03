#!/usr/bin/env python3
"""navigate.py — Store external_url in state, detect platform from URL.
No Playwright."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from apply.common.output import emit_next
from apply.common.registry import resolve as resolve_registry


def run(jid):
    c = get_conn()
    r = c.execute("SELECT url, title, company, stage, state, external_url FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        return 1
    url, title, company, job_state, ext_url = (
        r["url"], r["title"], r["company"], r["state"], r["external_url"] or ""
    )
    if job_state != "active":
        print(f"ERROR: job {jid} is in state '{job_state}', not active", file=sys.stderr)
        return 1

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    # Prefer DB external_url; fall back to a prior state's external_url
    # (e.g. manually supplied), then the job URL itself.
    prior_ext = ""
    from apply.common.page_helpers import load_state
    prior = load_state()
    if prior.get("jid") == jid:
        prior_ext = prior.get("external_url", "") or ""
    target_url = ext_url or prior_ext or url
    print(f"EXTERNAL_URL: {target_url}", file=sys.stderr)

    reg = resolve_registry(target_url)
    plat_name = reg.name if reg else ""
    if plat_name:
        print(f"PLATFORM: {plat_name}", file=sys.stderr)
        reg.emit_notes()
    else:
        print("PLATFORM: unknown", file=sys.stderr)

    # Save state via the single stamped writer (runtime cache role).
    state = {"jid": jid, "external_url": target_url, "url": url,
             "title": title or "", "company": company or "", "platform": plat_name}
    from apply.common.page_helpers import save_state
    save_state(state)

    emit_next("act --fill")
    return 0
