#!/usr/bin/env python3
"""detect.py — Navigate to LinkedIn search results, find job card, classify apply type.
Usage: python3 detect.py <jid>
Output:
  TYPE: easy_apply | external | applied | unavailable
  NEXT: ...
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

# Extract job ID from URL
job_id = url.split('/jobs/view/')[1].split('/')[0]

b, ctx = connect()
page = ctx.new_page()

# Go to search results
page.goto("https://www.linkedin.com/jobs/search/?keywords=software&location=Canada", wait_until='domcontentloaded', timeout=30000)
time.sleep(5)

# Click our specific job card
found = page.evaluate(f"""() => {{
    const card = document.querySelector('.job-card-container[data-job-id="{job_id}"]');
    if (card) {{ card.click(); return true; }}
    // Fallback: first card
    const first = document.querySelector('.job-card-container');
    if (first) {{ first.click(); return false; }}
    return false;
}}""")

if not found:
    # Job card not on page — try clicking first card anyway
    print(f"Job card {job_id} not found in results, using first card", file=sys.stderr)
time.sleep(3)

# Look for pane
pane = page.query_selector('.jobs-search__job-details--container')
if not pane:
    print("TYPE: unavailable (no pane)", file=sys.stderr)
    print("NEXT: none", file=sys.stderr)
    sys.exit(1)

# Classify
result = page.evaluate("""() => {
    const pane = document.querySelector('.jobs-search__job-details--container');
    if (!pane) return { type: 'no_pane' };
    const found = [];
    // Check buttons in pane
    const btns = pane.querySelectorAll('button');
    for (const b of btns) {
        const t = (b.textContent || '').trim().toLowerCase();
        if (t === 'easy apply' && !b.disabled) found.push({ type: 'easy_apply' });
        if (t === 'applied') found.push({ type: 'applied' });
    }
    // Check links
    const links = pane.querySelectorAll('a');
    for (const a of links) {
        const aria = (a.getAttribute('aria-label') || '').toLowerCase();
        if (aria.includes('apply on company website')) {
            found.push({ type: 'external', href: a.href });
        }
    }
    return found.length > 0 ? found : [{ type: 'unknown' }];
}""")

apply_type = 'unavailable'
for r2 in result if isinstance(result, list) else []:
    if r2['type'] == 'applied': apply_type = 'applied'; break
    if r2['type'] == 'easy_apply': apply_type = 'easy_apply'; break
    if r2['type'] == 'external': apply_type = 'external'; break

state = {"jid": jid, "url": url, "type": apply_type, "job_id": job_id, 
         "search_url": page.url, "page_url": page.url}
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

print(f"TYPE: {apply_type}", file=sys.stderr)
if apply_type == 'easy_apply':
    print("NEXT: apply/linkedin/easy_apply/01_click.py", file=sys.stderr)
elif apply_type == 'external':
    print("NEXT: apply/linkedin/external/01_navigate.py", file=sys.stderr)
elif apply_type == 'applied':
    print("NEXT: none (already applied)", file=sys.stderr)
else:
    print(f"NEXT: none ({apply_type})", file=sys.stderr)
