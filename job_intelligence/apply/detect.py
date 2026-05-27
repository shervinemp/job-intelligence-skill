#!/usr/bin/env python3
"""detect.py — Classify job entry point.
Outputs structured JSON-like info for the model to read.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

def read_page_state(p):
    """Read dialog fields + buttons. Returns dict or None if no dialog."""
    return p.evaluate("""() => {
        const d = document.querySelector('[role="dialog"]');
        if (!d) return null;
        const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        const btns = d.querySelectorAll('button');
        const fields = Array.from(inputs).map(el => {
            const lbl = d.querySelector('label[for="' + el.id + '"]');
            const parent = el.closest('div,fieldset,section,li');
            const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
            let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
            if (!label && plbl) label = plbl.textContent.trim();
            return {
                tag: el.tagName, type: el.getAttribute('type') || '',
                label: label.replace(/\\s+/g,' ').trim().slice(0, 80),
                required: el.required, value: el.value || '',
                checked: el.type === 'radio' ? el.checked : null,
                options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0,15) : [],
            };
        });
        return {
            fieldCount: fields.length,
            fields: fields.slice(0,30),
            buttons: Array.from(btns).filter(b => b.offsetParent !== null).map(b => ({
                text: (b.textContent || '').trim().slice(0,30), disabled: b.disabled
            })),
            hasFileInput: d.querySelectorAll('input[type="file"]').length > 0,
        };
    }""")

def run(jid):
    c = get_conn()
    r = c.execute("SELECT url, title, company, stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        sys.exit(1)
    url, title, company, stage = r["url"], r["title"], r["company"], r["stage"]

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    if stage == "applied":
        print("TYPE: already_applied")
        print("NEXT: none")
        sys.exit(0)

    b, ctx = connect()
    p = ctx.new_page()

    if "linkedin.com/jobs/view" in url:
        job_id = url.split("/jobs/view/")[1].split("/")[0]
        apply_url = f"https://www.linkedin.com/jobs/view/{job_id}/apply/?openSDUIApplyFlow=true"
        p.goto(apply_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        p.evaluate("() => window.__applyPage = true")

        # Check page
        buttons = p.evaluate("""() => Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => ({
            text: (b.textContent||'').trim().slice(0,25), aria: (b.getAttribute('aria-label')||'').slice(0,40)
        }))""")

        page_state = read_page_state(p)

        if page_state and page_state["fieldCount"] > 0:
            print("TYPE: easy_apply")
            print(f"PAGE: {json.dumps(page_state)}")
            print("NEXT: act --fill")
        elif any(b["text"] == "Applied" for b in buttons):
            print("TYPE: already_applied")
            print("NEXT: none")
        elif any("applied" in (b.get("aria") or b["text"]).lower() for b in buttons):
            print("TYPE: already_applied")
            print("NEXT: none")
        elif "you have applied" in (p.evaluate("() => (document.body.innerText || '').toLowerCase()") or ""):
            print("TYPE: already_applied")
            print("NEXT: none")
        # Check for Easy Apply button (fallback if dialog didn't auto-open)
        easy_btn = any("easy apply" in (b.get("aria") or b["text"]).lower() for b in buttons)
        if easy_btn:
            print("TYPE: easy_apply")
            print("PAGE: {}")
            print("NOTE: dialog not auto-opened, try clicking Easy Apply button")
            print("NEXT: act --fill")
        elif any("on company website" in (b.get("aria") or "").lower() for b in buttons):
            print("TYPE: external")
            print(f"BUTTONS: {json.dumps([b for b in buttons if 'company website' in b['aria']])}")
            print("NEXT: navigate")
        else:
            print("TYPE: unknown")
            print("NEXT: none")
    else:
        # Direct URL (non-LinkedIn)
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        page_state = read_page_state(p)
        if page_state and page_state["fieldCount"] > 0:
            print("TYPE: ats_direct")
            print(f"PAGE: {json.dumps(page_state)}")
            print("NEXT: act --fill")
        else:
            text = (p.evaluate("() => document.body.innerText") or "").lower()
            if any(w in text for w in ["sign in", "log in", "sign in to view"]):
                print("TYPE: auth_wall")
            else:
                print("TYPE: unknown")
            print("NEXT: none")

    # Save state
    state = {"jid": jid, "url": url, "title": title, "company": company}
    if page_state:
        state["page"] = page_state
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
