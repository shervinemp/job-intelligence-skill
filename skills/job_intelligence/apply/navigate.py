#! /usr/bin/env python3
"""navigate.py — Go to external ATS URL (stored by detect), classify the form."""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn
from apply.common.page_helpers import read_page, save_state, load_state
from apply.common.platforms import detect_platform
from apply.common.output import emit_next, emit_error
from lib.auth_walls import add as mark_auth_wall


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

    from apply.common.page_manager import PageManager

    page_state = read_page(ep)
    # Only count fields that look like application form fields
    # (exclude job listing page fields: search, cookie consent, job alert)
    skip_labels = {"search", "cookie", "alert", "keyword", "locationsearch"}
    real_fields = [f for f in page_state.get("fields", [])
                   if f.get("required") and f.get("tag") != "CHECKBOX"
                   and not any(x in (f.get("label") or "").replace(" ", "").lower() for x in skip_labels)]
    if not real_fields:
        # Detect "Apply now" button (not OneTrust cookie "Apply" button)
        apply_matches = [b for b in page_state.get("buttons", [])
                         if any(x in b.get("text", "").lower() for x in ["apply now", "apply for", "submit application"])]
        if apply_matches:
            txt = apply_matches[0]["text"]
            clicked = ep.evaluate(f"""(target) => {{
                for (const el of document.querySelectorAll('button, a.btn, a[role="button"], a')) {{
                    if (el.offsetParent === null) continue;
                    if ((el.textContent || '').trim() === target) {{
                        el.click();
                        return true;
                    }}
                }}
                return false;
            }}""", txt)
            if clicked:
                time.sleep(4)
                page_state = read_page(ep)
                # Guard: login/sign-up instead of form
                has_password = len(ep.locator('input[type="password"]').all()) > 0
                body_text = (ep.evaluate("document.body.innerText") or "").lower()
                if has_password or ("sign in" in body_text and "apply" not in body_text):
                    mark_auth_wall(jid, ep.url, title or "", company or "")
                    print(f"AUTH_WALL: {jid} — {title} @ {company}", file=sys.stderr)
                    save_state({"jid": jid, "external_url": ep.url, "page": page_state})
                    emit_next("enrich.py open")
                    sys.exit(0)

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
