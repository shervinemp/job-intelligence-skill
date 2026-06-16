#!/usr/bin/env python3
"""detect.py — Classify job entry point. Also pre-flight: checks stage, PDF, type.
One command tells you if a job is ready for the apply pipeline.
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from lib.db import get_conn, desc_exists
from apply.common.page_helpers import read_page, check_captcha, tag_page, STATE_PATH
from apply.common.output import emit_next, emit_status, emit_type, emit_error
from apply.common.registry import resolve as resolve_registry
from apply.common.platforms import (
    check_page,
    detect_platform,
    ALREADY_APPLIED,
    LOGIN_WALL,
    GUEST_APPLY,
)


def _merge_state(new):
    """Merge new state into existing. Clears stale job fields when jid changes."""
    existing = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    # Clear stale job-specific fields when switching to a new job
    if "jid" in new and new["jid"] != existing.get("jid"):
        existing = {"jid": existing["jid"]} if "jid" in existing else {}
    existing.update(new)
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(existing, f, indent=2)
    os.replace(tmp, STATE_PATH)


def _has_pdf(jid):
    from lib.config import RESULTS_DIR as _RD

    rd = os.path.join(_RD, jid)
    if not os.path.isdir(rd):
        return False
    return any("Resume" in f and f.endswith(".pdf") for f in os.listdir(rd))


def run(jid):
    c = get_conn()
    r = c.execute(
        "SELECT url, title, company, stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not r:
        emit_error(f"job {jid} not found")
        sys.exit(1)
    url, title, company, stage, state = r["url"], r["title"], r["company"], r["stage"], r["state"]
    if state != "active":
        emit_error(f"job is in state '{state}', not active")
        sys.exit(1)

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    # Stage check
    if stage == "applied":
        emit_type("already_applied")
        emit_next("none")
        _merge_state({"jid": jid})
        sys.exit(0)
    if stage == "failed":
        emit_status("failed", "run tailor.py retry first")
        emit_next("tailor.py retry")
        _merge_state({"jid": jid})
        sys.exit(0)
    # Guard: tailored stage must have a resume PDF (may be missing after manual DB edit
    # or admit without --pdf). If missing, treat same as described -> needs re-tailor.
    if stage == "tailored" and not _has_pdf(jid):
        emit_status(f"stage=tailored but no Resume PDF — re-tailoring")
        emit_next(f"tailor.py undo {jid} && tailor.py --jid {jid}")
        _merge_state({"jid": jid})
        sys.exit(0)

    if stage in ("extracted", "described"):
        if not _has_pdf(jid):
            if desc_exists(jid):
                emit_status(f"needs advance + tailor (stage={stage}, has desc, no PDF)")
                emit_next(f"tailor.py --jid {jid}")
            else:
                emit_status(f"needs description (stage={stage}, no desc, no PDF)")
                emit_next(f"enrich.py  then  tailor.py --jid {jid}")
            _merge_state({"jid": jid})
            sys.exit(0)

    # Classify type
    b, ctx = connect()
    p = ctx.new_page()
    page_owner = True  # track whether we should close p at exit

    def _close_p():
        nonlocal page_owner
        if page_owner:
            try:
                p.close()
            except Exception:
                pass
            page_owner = False

    if "linkedin.com/jobs/view" in url:
        job_id = url.split("/jobs/view/")[1].split("/")[0]

        # Intercept LinkedIn GraphQL response for Easy Apply field detection
        apply_fields = []

        def _handle_response(response):
            nonlocal apply_fields
            if "jobPostingApplyFlowByJobId" in response.url and response.ok:
                try:
                    body = response.json()
                    fields = (
                        body.get("data", {})
                        .get("jobPostingApplyFlowByJobId", {})
                        .get("questions", [])
                    )
                    for q in fields:
                        if isinstance(q, dict):
                            label = q.get("title", {}).get(
                                "text", q.get("body", {}).get("text", "")
                            )
                            apply_fields.append(
                                {
                                    "label": label[:80],
                                    "type": q.get("type", "unknown"),
                                    "required": q.get("required", False),
                                }
                            )
                except Exception:
                    pass

        p.on("response", _handle_response)

        # First check the regular job page for external apply button
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            p.wait_for_selector('[role="main"], article, .jobs-details', timeout=8000)
        except Exception:
            pass
        time.sleep(2)
        from apply.common.page_manager import PageManager

        pm = PageManager(ctx, jid)
        pm.close_stale(target_url=url)
        pm.register(p)
        buttons = p.evaluate(
            """() => {
            const all = document.querySelectorAll('button, a');
            return Array.from(all).filter(el => el.offsetParent !== null).map(el => ({
                text: (el.textContent || '').trim().slice(0, 25),
                aria: (el.getAttribute('aria-label') || '').slice(0, 40),
                tag: el.tagName
            }));
        }"""
        )

        if any(b["text"] == "Applied" for b in buttons):
            emit_type("already_applied")
            emit_next("none")
            _merge_state({"jid": jid})
            _close_p()
            sys.exit(0)
        if any("on company website" in (b.get("aria") or "").lower() for b in buttons):
            ext_url = p.evaluate("""() => {
                const anchors = document.querySelectorAll('a[href]');
                const intent = (s) => (s||'').toLowerCase().includes('on company website');
                for (const a of anchors) {
                    const btn = a.querySelector('button');
                    if (!btn) continue;
                    if (intent(btn.getAttribute('aria-label')) && btn.offsetParent) return a.href;
                }
                for (const a of anchors) {
                    if (intent(a.getAttribute('aria-label'))) return a.href;
                    if ((a.href||'').includes('linkedin.com/safety/go/')) return a.href;
                }
                return null;
            }""")
            if ext_url and "linkedin.com/safety/go/" in ext_url:
                import urllib.parse as _up
                qs = _up.urlparse(ext_url).query
                decoded = _up.parse_qs(qs).get("url", [None])[0]
                if decoded:
                    ext_url = _up.unquote(decoded)
                else:
                    ext_url = None
            try:
                p.close()
            except Exception:
                pass
            emit_type("external", f"EXTERNAL_URL: {ext_url}")
            emit_next("navigate")
            _merge_state({"jid": jid, "url": url, "title": title, "company": company, "external_url": ext_url or ""})
            sys.exit(0)

        # Not external — try opening Easy Apply modal
        p.goto(
            f"https://www.linkedin.com/jobs/view/{job_id}/apply/?openSDUIApplyFlow=true",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        try:
            p.wait_for_selector(
                '[role="dialog"], [data-test-form-builder]', timeout=8000
            )
        except Exception:
            pass
        time.sleep(2)
        tag_page(p, jid)  # re-tag after navigation (first tag was wiped)

        if check_captcha(p):
            emit_status("captcha", "detected on Easy Apply modal")
            emit_next("retry after solving")
            _close_p()
            return

        page_state = read_page(p)
        buttons = p.evaluate(
            """() => {
            const all = document.querySelectorAll('button, a');
            return Array.from(all).filter(el => el.offsetParent !== null).map(el => ({
                text: (el.textContent || '').trim().slice(0, 25),
                aria: (el.getAttribute('aria-label') || '').slice(0, 40),
                tag: el.tagName
            }));
        }"""
        )

        reg = resolve_registry(url)
        if page_state and page_state["fieldCount"] > 0:
            page_owner = False  # keep page open for act --fill
            tag_page(p, jid)
            _merge_state({"jid": jid, "_detect_fields": page_state, "external_url": p.url})
            # Click "Easy Apply" if available — modal fields will be picked up by --fill
            p.evaluate("""() => {
                for (const el of document.querySelectorAll('button, a, [role="button"]')) {
                    if ((el.textContent || '').trim().toLowerCase() === 'easy apply') { el.click(); return; }
                }
            }""")
            time.sleep(3)
            emit_type("easy_apply")
            if reg:
                reg.emit_notes()
            emit_next("act --fill")
        elif apply_fields:
            fb = {"fieldCount": len(apply_fields), "fields": apply_fields}
            page_owner = False
            tag_page(p, jid)
            _merge_state({"jid": jid, "_detect_fields": fb, "external_url": p.url})
            emit_type("easy_apply")
            if reg:
                reg.emit_notes()
            emit_next("act --fill")
        elif any("easy apply" in (b.get("aria") or b["text"]).lower() for b in buttons):
            page_owner = False
            tag_page(p, jid)
            _merge_state({"jid": jid, "external_url": p.url})
            emit_type("easy_apply", "dialog not auto-opened")
            if reg:
                reg.emit_notes()
            emit_next("act --fill")
        else:
            emit_type("unknown")
            emit_next("act --inspect")
    else:
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            p.wait_for_selector(
                'form, input, select, textarea, [role="dialog"]', timeout=8000
            )
        except Exception:
            pass
        time.sleep(2)
        tag_page(p, jid)  # tag so act can find this page by JID
        if check_captcha(p):
            emit_status("captcha", "detected on job page")
            emit_next("retry after solving")
            return
        # Check for already-applied text patterns before proceeding
        plat_text = (p.evaluate("() => document.body.innerText") or "").lower()
        reg = resolve_registry(url)
        plat_name = reg.name if reg else None
        if check_page(plat_text, plat_name, ALREADY_APPLIED):
            emit_type("already_applied")
            emit_next("none")
            _merge_state({"jid": jid})
            sys.exit(0)
        page_state = read_page(p)
        if page_state and page_state["fieldCount"] > 0:
            plat = detect_platform(url)
            reg = resolve_registry(url)
            plat_name = reg.name if reg else plat
            emit_type("ats_direct", f"EXTERNAL_URL: {url}\nPLATFORM: {plat_name}")
            if reg:
                reg.emit_notes()
            emit_next("act --fill")
            _merge_state(
                {
                    "jid": jid,
                    "url": url,
                    "title": title,
                    "company": company,
                    "external_url": url,
                    "platform": plat_name,
                    "page": page_state,
                }
            )
        else:
            plat = detect_platform(url)
            text = (p.evaluate("() => document.body.innerText") or "").lower()
            if plat and check_page(text, plat, LOGIN_WALL):
                # Check for guest apply buttons before declaring full login wall
                guest_patterns = GUEST_APPLY.get(plat, []) + GUEST_APPLY["default"]
                guest_btn = p.evaluate(
                    f"""() => {{
                    const patterns = {json.dumps(guest_patterns)};
                    const all = document.querySelectorAll('button, a, span, div');
                    for (const el of all) {{
                        if (el.offsetParent === null) continue;
                        const t = (el.textContent || '').trim().toLowerCase();
                        for (const p of patterns) {{
                            if (t === p || t.startsWith(p)) return p;
                        }}
                    }}
                    return null;
                }}"""
                )
                if guest_btn:
                    emit_status(
                        "guest_available", f"PLATFORM: {plat}, button: '{guest_btn}'"
                    )
                    emit_next("act --fill")
                else:
                    emit_type("login_wall", f"PLATFORM: {plat}")
                    emit_next("login then retry")
                _merge_state({"jid": jid})
            else:
                emit_type("unknown")
                emit_next("act --inspect")
                _merge_state({"jid": jid})
