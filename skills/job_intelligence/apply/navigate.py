#! /usr/bin/env python3
"""navigate.py — Go to external ATS URL (stored by detect), classify the form."""
import json, os, re, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.page_helpers import read_page, save_state, load_state
from apply.common.platforms import detect_platform
from apply.common.output import emit_next, emit_error


def run(jid):
    c = get_conn()
    r = c.execute("SELECT url, title, company, stage, state FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        sys.exit(1)
    url, title, company, stage, state = r["url"], r["title"], r["company"], r["stage"], r["state"]
    if state != "active":
        print(f"ERROR: job {jid} is in state '{state}', not active", file=sys.stderr)
        sys.exit(1)

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    # External URL was stored by detect — use it directly, skip LinkedIn re-navigation
    state = load_state()
    external_url = state.get("external_url", "")
    if not external_url or "linkedin.com" in external_url:
        # Fallback: detect may not have found it — try opening LinkedIn job page
        from apply.common.page_helpers import tag_page as _tp
        b, ctx = connect()
        p = ctx.new_page()
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        external_url = p.evaluate("""() => {
            for (const a of document.querySelectorAll('a[href]')) {
                const btn = a.querySelector('button');
                const aria = (btn?.getAttribute('aria-label') || a.getAttribute('aria-label') || '').toLowerCase();
                if (aria.includes('on company website') && (btn?.offsetParent || a.offsetParent)) return a.href;
                if ((a.href||'').includes('linkedin.com/safety/go/')) return a.href;
            }
            return null;
        }""")
        if external_url and "linkedin.com/safety/go/" in external_url:
            import urllib.parse as _up
            qs = _up.urlparse(external_url).query
            decoded = _up.parse_qs(qs).get("url", [None])[0]
            if decoded:
                external_url = _up.unquote(decoded)
            else:
                external_url = None
        try:
            p.close()
        except Exception:
            pass

    if not external_url or "linkedin.com" in (external_url or ""):
        emit_error("no external URL — job may be closed or premium-walled")
        emit_next("act --inspect")
        sys.exit(1)

    plat = detect_platform(external_url)
    print(f"EXTERNAL_URL: {external_url}\nPLATFORM: {plat}", file=sys.stderr)

    b, ctx = connect()
    for pg in ctx.pages:
        if pg.url.rstrip("/") == external_url.rstrip("/"):
            try:
                pg.close()
            except Exception:
                pass
            break

    ep = ctx.new_page()
    ep.goto(external_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    # Some ATS (Taleo, Workday job listings) land on a job details page
    # with an "Apply now" button instead of the application form directly.
    apply_btn = ep.locator("a.btn, a[role=button], button").filter(has_text=re.compile(r"Apply", re.I)).first
    if apply_btn.count():
        page_state = read_page(ep)
        real_fields = [f for f in page_state.get("fields", [])
                       if f.get("required") and f.get("tag") != "CHECKBOX"]
        if not real_fields:
            apply_btn.click()
            time.sleep(4)
            # Guard: if the click surfaced a login/sign-up page, abort
            has_password = ep.locator('input[type="password"]').count()
            body_text = (ep.inner_text("body") or "").lower()
            if has_password or ("sign in" in body_text and "apply" not in body_text):
                print("LOGIN_WALL: Apply button leads to sign-in — aborting", file=sys.stderr)
                save_state({"jid": jid, "external_url": ep.url, "page": read_page(ep)})
                emit_next("act --inspect")
                sys.exit(0)

    from apply.common.page_manager import PageManager

    from apply.common.page_manager import PageManager

    pm = PageManager(ctx, jid)
    pm.cleanup_all()
    pm.register(ep)
    pm.close_others(ep)
    page_state = read_page(ep)
    print(f"PAGE: {json.dumps(page_state)}", file=sys.stderr)
    emit_next("act --fill")

    # Use actual page URL (Greenhouse rewrites boards -> job-boards on redirect)
    actual_url = ep.url
    save_state(
        {"jid": jid, "external_url": actual_url, "platform": plat, "page": page_state}
    )
