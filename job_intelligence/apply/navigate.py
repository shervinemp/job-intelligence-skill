#!/usr/bin/env python3
"""navigate.py — LinkedIn job page -> click external apply -> detect ATS."""
import json, os, sys, time, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.page_helpers import read_page, save_state
from apply.common.platforms import detect_platform
from apply.common.output import emit_next, emit_error

def run(jid):
    c = get_conn()
    r = c.execute("SELECT url, title, company FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r: print(f"ERROR: job {jid} not found", file=sys.stderr); sys.exit(1)
    url, title, company = r["url"], r["title"], r["company"]

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    b, ctx = connect()
    p = ctx.new_page()
    p.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    external_url = p.evaluate("""() => {
        const anchors = document.querySelectorAll('a[href]');
        const hasApplyIntent = (s) => {
            const t = (s || '').toLowerCase();
            return t.includes('on company website') || t.includes('company site')
                || /apply\\s*(on|at|through|via|external(ly)?)/.test(t)
                || /external\\s+apply/.test(t);
        };
        // 1. Anchor wrapping a button with external-apply intent
        for (const a of anchors) {
            const btn = a.querySelector('button');
            if (!btn) continue;
            const aria = btn.getAttribute('aria-label') || '';
            const text = (btn.textContent || '').trim();
            if ((hasApplyIntent(aria) || hasApplyIntent(text)) && btn.offsetParent !== null)
                return a.href;
        }
        // 2. Anchor with external-apply aria-label
        for (const a of anchors) {
            const aria = a.getAttribute('aria-label') || '';
            const text = (a.textContent || '').trim();
            const href = a.href || '';
            if (hasApplyIntent(aria) || hasApplyIntent(text)) return href;
            if (href.includes('linkedin.com/safety/go/')) return href;
        }
        // 3. Bare button with external-apply intent (no wrapping anchor)
        const buttons = document.querySelectorAll('button');
        for (const b of buttons) {
            const aria = b.getAttribute('aria-label') || '';
            const text = (b.textContent || '').trim();
            if (hasApplyIntent(aria) || hasApplyIntent(text)) return b.getAttribute('formaction') || '';
        }
        return null;
    }""")

    if external_url and 'linkedin.com/safety/go/' in external_url:
        qs = urllib.parse.urlparse(external_url).query
        decoded = urllib.parse.parse_qs(qs).get('url', [None])[0]
        if decoded: external_url = urllib.parse.unquote(decoded)
        else: external_url = None

    if not external_url or 'linkedin.com' in external_url:
        p.evaluate("""() => { document.querySelectorAll('button').forEach(b => { if ((b.getAttribute('aria-label')||'').includes('on company website') && b.offsetParent !== null) b.click(); }); }""")
        time.sleep(5)
        for p2 in ctx.pages:
            u = p2.url
            if 'linkedin.com' not in u and u != 'about:blank' and not u.startswith('chrome'):
                external_url = u; break
        if not external_url or 'linkedin.com' in external_url:
            current = p.evaluate("window.location.href")
            if 'linkedin.com' not in current: external_url = current

    if not external_url or 'linkedin.com' in (external_url or ''):
        emit_error("no external URL — job may be closed or premium-walled")
        emit_next("none"); sys.exit(1)

    plat = detect_platform(external_url)
    print(f"EXTERNAL_URL: {external_url}\nPLATFORM: {plat}", file=sys.stderr)

    # Close the origin LinkedIn page and any safety-redirect pages
    for pg in ctx.pages:
        u = pg.url
        if 'linkedin.com' in u and u != p.url:
            try: pg.close()
            except: pass

    # Navigate to external URL and read form
    ep = ctx.new_page()
    ep.goto(external_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)
    from apply.common.page_manager import PageManager
    pm = PageManager(ctx, jid)
    pm.cleanup_all()
    pm.register(ep)
    pm.close_others(ep)
    page_state = read_page(ep)
    print(f"PAGE: {json.dumps(page_state)}", file=sys.stderr)
    emit_next("act --fill")

    save_state({"jid": jid, "external_url": external_url, "platform": plat, "page": page_state})
