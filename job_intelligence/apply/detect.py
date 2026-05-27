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
        print("TYPE: already_applied\nNEXT: none"); save_state({"jid": jid}); sys.exit(0)
    if stage == "failed":
        print("STATUS: failed — run tailor.py retry first\nNEXT: tailor.py retry"); save_state({"jid": jid}); sys.exit(0)
    if stage in ("extracted", "described"):
        if not _has_pdf(jid):
            if desc_exists(jid):
                print(f"STATUS: needs advance + tailor (stage={stage}, has desc, no PDF)\nNEXT: tailor.py --jid {jid}")
            else:
                print(f"STATUS: needs description (stage={stage}, no desc, no PDF)\nNEXT: fetch.py  then  tailor.py --jid {jid}")
            save_state({"jid": jid}); sys.exit(0)

    # Classify type
    b, ctx = connect()
    p = ctx.new_page()

    if "linkedin.com/jobs/view" in url:
        job_id = url.split("/jobs/view/")[1].split("/")[0]

        # First check the regular job page for external apply button
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        from apply.common.page_manager import PageManager
        PageManager(ctx, jid).register(p)
        buttons = p.evaluate("""() => {
            const all = document.querySelectorAll('button, a');
            return Array.from(all).filter(el => el.offsetParent !== null).map(el => ({
                text: (el.textContent || '').trim().slice(0, 25),
                aria: (el.getAttribute('aria-label') || '').slice(0, 40),
                tag: el.tagName
            }));
        }""")

        if any(b["text"] == "Applied" for b in buttons):
            print("TYPE: already_applied\nNEXT: none"); save_state({"jid": jid}); sys.exit(0)
        if any("applied" in (b.get("aria") or "").lower() for b in buttons):
            print("TYPE: already_applied\nNEXT: none"); save_state({"jid": jid}); sys.exit(0)
        if "you have applied" in (p.evaluate("() => (document.body.innerText || '').toLowerCase()") or ""):
            print("TYPE: already_applied\nNEXT: none"); save_state({"jid": jid}); sys.exit(0)
        if any("on company website" in (b.get("aria") or "").lower() for b in buttons):
            print(f"TYPE: external\nBUTTONS: {json.dumps([b for b in buttons if 'company website' in b['aria']])}\nNEXT: navigate")
            save_state({"jid": jid, "url": url, "title": title, "company": company})
            sys.exit(0)

        # Not external — try opening Easy Apply modal
        p.goto(f"https://www.linkedin.com/jobs/view/{job_id}/apply/?openSDUIApplyFlow=true", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        page_state = read_page(p)
        buttons = p.evaluate("""() => {
            const all = document.querySelectorAll('button, a');
            return Array.from(all).filter(el => el.offsetParent !== null).map(el => ({
                text: (el.textContent || '').trim().slice(0, 25),
                aria: (el.getAttribute('aria-label') || '').slice(0, 40),
                tag: el.tagName
            }));
        }""")

        if page_state and page_state["fieldCount"] > 0:
            print(f"TYPE: easy_apply\nPAGE: {json.dumps(page_state)}\nNEXT: act --fill")
        elif any("easy apply" in (b.get("aria") or b["text"]).lower() for b in buttons):
            print("TYPE: easy_apply\nPAGE: {{}}\nNOTE: dialog not auto-opened\nNEXT: act --fill")
        else:
            print("TYPE: unknown\nNEXT: none")
    else:
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        page_state = read_page(p)
        if page_state and page_state["fieldCount"] > 0:
            from apply.common.platforms import detect_platform
            plat = detect_platform(url)
            print(f"TYPE: ats_direct\nEXTERNAL_URL: {url}\nPLATFORM: {plat}\nPAGE: {json.dumps(page_state)}\nNEXT: act --fill")
            save_state({"jid": jid, "url": url, "title": title, "company": company,
                        "external_url": url, "platform": plat, "page": page_state})
        else:
            from apply.common.platforms import detect_platform, check_page, LOGIN_WALL
            plat = detect_platform(url)
            text = (p.evaluate("() => document.body.innerText") or "").lower()
            if plat and check_page(text, plat, LOGIN_WALL):
                print(f"TYPE: login_wall\nPLATFORM: {plat}\nNEXT: login then retry")
                save_state({"jid": jid})
            else:
                print(f"TYPE: unknown\nNEXT: none")
                save_state({"jid": jid})
