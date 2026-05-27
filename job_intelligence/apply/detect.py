#!/usr/bin/env python3
"""detect.py — Classify job entry point. Also pre-flight: checks stage, PDF, type.
One command tells you if a job is ready for the apply pipeline.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn, desc_exists
from apply.common.page_helpers import read_page, save_state

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

def _has_pdf(jid):
    rd = os.path.expanduser(f"~/.openclaw/results/{jid}")
    if not os.path.isdir(rd): return False
    return any("Resume" in f and f.endswith(".pdf") for f in os.listdir(rd))

def run(jid):
    c = get_conn()
    r = c.execute("SELECT url, title, company, stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        print(f"ERROR: job {jid} not found", file=sys.stderr); sys.exit(1)
    url, title, company, stage = r["url"], r["title"], r["company"], r["stage"]

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    # Stage check
    if stage == "applied":
        print("TYPE: already_applied\nNEXT: none"); sys.exit(0)
    if stage == "failed":
        print("STATUS: failed — run tailor.py retry first\nNEXT: tailor.py retry"); sys.exit(0)
    if stage in ("extracted", "described"):
        if not _has_pdf(jid):
            if desc_exists(jid):
                print(f"STATUS: needs advance + tailor (stage={stage}, has desc, no PDF)\nNEXT: tailor.py --jid {jid}")
            else:
                print(f"STATUS: needs description (stage={stage}, no desc, no PDF)\nNEXT: fetch.py  then  tailor.py --jid {jid}")
            sys.exit(0)

    # Classify type
    b, ctx = connect()
    p = ctx.new_page()

    if "linkedin.com/jobs/view" in url:
        job_id = url.split("/jobs/view/")[1].split("/")[0]
        p.goto(f"https://www.linkedin.com/jobs/view/{job_id}/apply/?openSDUIApplyFlow=true", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        buttons = p.evaluate("""() => Array.from(document.querySelectorAll('button')).filter(b=>b.offsetParent!==null).map(b=>({text:(b.textContent||'').trim().slice(0,25),aria:(b.getAttribute('aria-label')||'').slice(0,40)}))""")
        page_state = read_page(p)

        if page_state and page_state["fieldCount"] > 0:
            print(f"TYPE: easy_apply\nPAGE: {json.dumps(page_state)}\nNEXT: act --fill")
        elif any(b["text"] == "Applied" for b in buttons):
            print("TYPE: already_applied\nNEXT: none")
        elif any("applied" in (b.get("aria") or b["text"]).lower() for b in buttons):
            print("TYPE: already_applied\nNEXT: none")
        elif "you have applied" in (p.evaluate("() => (document.body.innerText || '').toLowerCase()") or ""):
            print("TYPE: already_applied\nNEXT: none")
        elif any("easy apply" in (b.get("aria") or b["text"]).lower() for b in buttons):
            print("TYPE: easy_apply\nPAGE: {}\nNOTE: dialog not auto-opened\nNEXT: act --fill")
        elif any("on company website" in (b.get("aria") or "").lower() for b in buttons):
            print(f"TYPE: external\nBUTTONS: {json.dumps([b for b in buttons if 'company website' in b['aria']])}\nNEXT: navigate")
        else:
            print("TYPE: unknown\nNEXT: none")
    else:
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        page_state = read_page(p)
        if page_state and page_state["fieldCount"] > 0:
            print(f"TYPE: ats_direct\nPAGE: {json.dumps(page_state)}\nNEXT: act --fill")
        else:
            text = (p.evaluate("() => document.body.innerText") or "").lower()
            print(f"TYPE: {'auth_wall' if any(w in text for w in ['sign in','log in']) else 'unknown'}\nNEXT: none")

    save_state({"jid": jid, "url": url, "title": title, "company": company, "page": page_state or {}})
