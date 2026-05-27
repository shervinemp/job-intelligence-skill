#!/usr/bin/env python3
"""verify.py — Check if a job was submitted. No state mutation."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn

def run(jid):
    c = get_conn()
    r = c.execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        sys.exit(1)

    db_stage = r["stage"]

    if db_stage == "applied":
        print("STATUS: submitted (DB)")
        print("NEXT: none")
        return

    # Check LinkedIn page
    b, ctx = connect()
    for p in ctx.pages:
        url = p.url
        if "linkedin.com/jobs/view" in url or "linkedin.com/jobs/collections" in url:
            text = (p.evaluate("() => document.body.innerText") or "").lower()
            if "applied" in text:
                print("STATUS: submitted (LinkedIn shows Applied)")
                c.execute("UPDATE jobs SET stage=? WHERE id=?", ("applied", jid))
                c.commit()
                print("NEXT: none")
                return
            break

    print("STATUS: unknown")
    print("DB stage:", db_stage)
    print("NEXT: act --fill or check manually")
