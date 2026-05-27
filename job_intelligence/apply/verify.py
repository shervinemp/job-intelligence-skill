#!/usr/bin/env python3
"""verify.py — Check if a job was submitted. No state mutation."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from lib.chrome_manager import connect

def run(jid):
    db_stage = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if not db_stage: print(f"ERROR: job {jid} not found", file=sys.stderr); sys.exit(1)
    stage = db_stage["stage"]

    if stage == "applied":
        print("STATUS: submitted (DB)\nNEXT: none")
        return

    b, ctx = connect()
    for p in ctx.pages:
        if "linkedin.com/jobs" in p.url:
            text = (p.evaluate("() => document.body.innerText") or "").lower()
            if "you have applied" in text:
                print("STATUS: submitted (LinkedIn)")
                get_conn().execute("UPDATE jobs SET stage=? WHERE id=?", ("applied", jid)).connection.commit()
                print("NEXT: none")
                return
            break
    print(f"STATUS: unknown (DB stage: {stage})\nNEXT: act --fill or check manually")
