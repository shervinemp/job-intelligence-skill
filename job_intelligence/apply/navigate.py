#!/usr/bin/env python3
"""navigate.py — LinkedIn job page -> click external apply -> detect ATS.
Outputs external URL + platform name.
"""
import json, os, sys, time, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.platforms import detect_platform

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

def run(jid):
    c = get_conn()
    r = c.execute("SELECT url, title, company FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        sys.exit(1)
    url, title, company = r["url"], r["title"], r["company"]

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    b, ctx = connect()
    p = ctx.new_page()
    p.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)
    p.evaluate("() => window.__applyPage = true")

    # Extract external URL from LinkedIn page
    external_url = p.evaluate("""() => {
        // Try anchor wrapping the button first
        const anchors = document.querySelectorAll('a[href]');
        for (const a of anchors) {
            const btn = a.querySelector('button');
            if (!btn) continue;
            const aria = (btn.getAttribute('aria-label') || '');
            if (aria.includes('on company website') && btn.offsetParent !== null) return a.href;
        }
        // Data-tracking anchor
        for (const a of anchors) {
            if ((a.getAttribute('aria-label') || '').includes('on company website')) return a.href;
        }
        // Safety redirect
        for (const a of anchors) {
            if ((a.href || '').includes('linkedin.com/safety/go/')) return a.href;
        }
        return null;
    }""")

    # Decode LinkedIn safety redirect
    if external_url and 'linkedin.com/safety/go/' in external_url:
        qs = urllib.parse.urlparse(external_url).query
        params = urllib.parse.parse_qs(qs)
        decoded = params.get('url', [None])[0]
        if decoded:
            external_url = urllib.parse.unquote(decoded)
        else:
            external_url = None

    # Fallback: click the button
    if not external_url or 'linkedin.com' in external_url:
        p.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                const aria = (b.getAttribute('aria-label') || '');
                if (aria.includes('on company website') && b.offsetParent !== null) { b.click(); return; }
            }
        }""")
        time.sleep(5)
        for p2 in ctx.pages:
            u = p2.url
            if 'linkedin.com' not in u and u != 'about:blank' and not u.startswith('chrome'):
                external_url = u; break
        if not external_url or 'linkedin.com' in external_url:
            current = p.evaluate("window.location.href")
            if 'linkedin.com' not in current:
                external_url = current

    if not external_url or 'linkedin.com' in (external_url or ''):
        print("ERROR: no external URL found — job may be closed or premium-walled")
        print("NEXT: none")
        sys.exit(1)

    plat = detect_platform(external_url)
    print(f"EXTERNAL_URL: {external_url}")
    print(f"PLATFORM: {plat}")

    # Read form on the external page
    ext_page = None
    for pg in ctx.pages:
        if external_url in pg.url or pg.url in external_url:
            ext_page = pg; break
    if not ext_page:
        ext_page = p  # current page may have navigated to external
        time.sleep(3)

    page_state = p.evaluate("""() => {
        const container = document.querySelector('[role="dialog"]') || document;
        const inputs = container.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        const btns = container.querySelectorAll('button');
        const fields = Array.from(inputs).map(el => {
            const lbl = container.querySelector('label[for="' + el.id + '"]');
            const parent = el.closest('div,fieldset,section,li');
            const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
            let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
            if (!label && plbl) label = plbl.textContent.trim();
            return {
                tag: el.tagName, type: el.getAttribute('type') || '',
                label: label.replace(/\\s+/g,' ').trim().slice(0, 80),
                required: el.required, value: el.value || '',
                checked: el.type === 'radio' ? el.checked : null,
            };
        });
        return {
            fieldCount: fields.length, fields: fields.slice(0,30),
            hasFileInput: container.querySelectorAll('input[type="file"]').length > 0,
            buttons: Array.from(btns).filter(b => b.offsetParent !== null).map(b => ({
                text: (b.textContent || '').trim().slice(0,30), disabled: b.disabled
            })),
        };
    }""")

    print(f"PAGE: {json.dumps(page_state)}")
    print("NEXT: act --fill")

    state = {"jid": jid, "external_url": external_url, "platform": plat, "page": page_state}
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
