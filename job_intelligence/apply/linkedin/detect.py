#!/usr/bin/env python3
"""detect.py — Classify a LinkedIn job's apply type via /apply/ URL.
Usage: python3 detect.py <jid>

Outputs to stderr:
  TYPE: easy_apply | external | applied | unavailable
  (data about what was found)
  NEXT: <next script path>

State: ~/.openclaw/apply_state.json
Leaves the page open for subsequent scripts.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.chrome_manager import connect
from lib.db import get_conn

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

jid = sys.argv[1]
c = get_conn()
r = c.execute("SELECT url, title, company FROM jobs WHERE id=?", (jid,)).fetchone()
url, title, company = r["url"], r["title"], r["company"]
print(f"JOB: {title} @ {company}", file=sys.stderr)

job_id = url.split("/jobs/view/")[1].split("/")[0]
apply_url = f"https://www.linkedin.com/jobs/view/{job_id}/apply/?openSDUIApplyFlow=true"

b, ctx = connect()
p = ctx.new_page()
p.goto(apply_url, wait_until='domcontentloaded', timeout=30000)
time.sleep(5)

# Classify
result = p.evaluate("""() => {
    const r = { type: 'unknown', details: {} };
    
    // 1. Easy Apply: dialog with real content (opened by /apply/ URL)
    const d = document.querySelector('[role="dialog"]');
    if (d && (d.innerText || '').trim().length > 80) {
        r.type = 'easy_apply';
        return r;
    }
    
    // 2. Already applied: <button> with text "Applied"
    const all = document.querySelectorAll('button');
    for (const el of all) {
        if ((el.textContent || '').trim() === 'Applied' && el.offsetParent !== null) {
            r.type = 'applied'; return r;
        }
    }
    
    // 3. External apply: <button> with aria-label containing "on company website"
    for (const el of all) {
        const aria = (el.getAttribute('aria-label') || '');
        if (aria.includes('on company website') && el.offsetParent !== null) {
            r.type = 'external';
            r.details.aria = aria.slice(0, 80);
            return r;
        }
    }
    
    return r;
}""")

apply_type = result.get('type', 'unknown')
state = {
    "jid": jid, "url": url, "type": apply_type,
    "details": result.get('details', {}),
    "page_url": p.url,
}
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"TYPE: {apply_type}", file=sys.stderr)
if apply_type == 'easy_apply':
    print(f"Fields: {result['details'].get('fieldCount')}", file=sys.stderr)
    print(f"Buttons: {[b['text'] for b in result['details'].get('buttons', [])]}", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/01_click.py", file=sys.stderr)
elif apply_type == 'external':
    print(f"External apply detected", file=sys.stderr)
    print("NEXT: apply/linkedin/external/01_navigate.py", file=sys.stderr)
elif apply_type == 'applied':
    print("NEXT: none", file=sys.stderr)
else:
    print("NEXT: none", file=sys.stderr)
