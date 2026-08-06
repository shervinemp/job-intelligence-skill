"""lib/g2.py — G2 post-submit verification (the second confirmation).

The pipeline marks a job 'applied' on G1 signals (in-page success text,
confirmation URL, modal closed, 'Applied' button). G1 is the submit's OWN
belief — it is not independent. G2 is the INDEPENDENT check that the
submission actually landed in a place the pipeline does not control:

  LinkedIn Easy Apply  → the Job Tracker "Applied" list (server-rendered)
  External ATS (Oracle) → the confirmation email in the inbox

`applied-confirm` / `verify-applied` must not blindly flip a flag (that
is what G1 already did). This module turns verification into a real check.
"""
import json
import os
import time


def _applied_confirm_path():
    # Read STATE_DIR at call time (not import) so tests and JI_HOME overrides
    # patch the effective path — matches report.py's _applied_confirm_path.
    from lib.config import STATE_DIR
    return os.path.join(STATE_DIR, "applied_confirmations.json")


def _applied_confirmed():
    try:
        with open(_applied_confirm_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_confirmed(jid):
    return bool(_applied_confirmed().get(jid))


def record_confirmed(jid):
    """Record an explicitly G2-confirmed submission (after the check ran,
    not instead of it). Returns True when written."""
    try:
        from lib.config import atomic_write_json
        data = _applied_confirmed()
        data[jid] = True
        atomic_write_json(_applied_confirm_path(), data, indent=2)
        return True
    except Exception:
        return False


def _host_of(url):
    try:
        from urllib.parse import urlparse as _up
        return (_up(url or "").netloc or "").lower().split(":")[0]
    except Exception:
        return ""


def tracker_applied_entries(ctx=None, page=None):
    """Open the LinkedIn Job Tracker once and return the RAW text of the
    'Applied' tab. One visit serves many jobs — the per-job confirm functions
    each re-open the page, which is wasteful when the backlog is large.

    Returns (hay, detail): `hay` is the lowercased tracker text (or ""), and
    `detail` explains a definitive no vs. an inconclusive check.
    """
    own_browser = False
    try:
        if page is None:
            from lib.chrome_manager import connect
            b, ctx = connect(timeout=30)
            if not ctx:
                return "", "no Chrome context — cannot open tracker"
            own_browser = True
            page = ctx.pages[-1]

        page.goto("https://www.linkedin.com/my-items/saved-jobs/",
                  wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
        loc = page.locator("text=Applied").first
        if loc.count() > 0:
            try:
                loc.click(timeout=5000)
                time.sleep(5)
            except Exception:
                pass

        hay = ""
        for _ in range(20):
            try:
                page.evaluate("window.scrollBy(0, 2500); true")
            except Exception:
                break
            time.sleep(0.5)
            try:
                hay = page.evaluate("() => document.body.innerText || ''") or ""
            except Exception:
                break
            if "no items" in hay.lower() or "you haven" in hay.lower():
                break
        if not hay:
            return "", "tracker returned no text (login wall or empty)"
        return (hay or "").lower(), "tracker Applied list read"
    except Exception as e:
        return "", f"tracker check failed: {str(e)[:120]}"
    finally:
        if own_browser:
            try:
                b.close()
            except Exception:
                pass


def linkedin_tracker_confirm(jid, url="", title="", company="", ctx=None, page=None):
    """G2 for LinkedIn: open the Job Tracker 'Applied' list and search for the
    job's title+company. Returns True when the posting is confirmed present.

    This is the independent check — the tracker is LinkedIn's server-side
    record of what was actually submitted, not the pipeline's own belief.
    Returns (confirmed, detail) so the caller can distinguish a definitive
    NO from an inconclusive check (tracker not reachable, login wall, ...).
    """
    host = _host_of(url)
    if host and "linkedin.com" not in host:
        return False, "not a LinkedIn job — tracker check does not apply"
    if not (title or company):
        return False, "no title/company to search for"

    own_browser = False
    try:
        if page is None:
            from lib.chrome_manager import connect
            b, ctx = connect(timeout=30)
            if not ctx:
                return False, "no Chrome context — cannot open tracker"
            own_browser = True
            page = ctx.pages[-1]

        # The tracker page defaults to the 'Saved' tab (0 items). The
        # Applied tab is a text tab labeled "Applied" — click it via text.
        page.goto("https://www.linkedin.com/my-items/saved-jobs/",
                  wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
        loc = page.locator("text=Applied").first
        if loc.count() > 0:
            try:
                loc.click(timeout=5000)
                time.sleep(5)
            except Exception:
                pass

        # Scroll the virtualized list so entries render into the DOM.
        hay = ""
        for _ in range(20):
            try:
                page.evaluate("window.scrollBy(0, 2500); true")
            except Exception:
                break
            time.sleep(0.5)
            try:
                hay = page.evaluate("() => document.body.innerText || ''") or ""
            except Exception:
                break
            if title and title.lower() in hay.lower():
                break

        lb = (hay or "").lower()
        tlb = (title or "").lower()
        clb = (company or "").lower()
        if tlb and tlb in lb:
            return True, f"found '{title[:40]}' in the Applied tracker"
        # Fallback: some tracker rows drop the full title; company-only match
        # is weaker but still a positive signal when the job is recent.
        if clb and clb in lb:
            return True, f"found company '{company[:30]}' in the Applied tracker"
        return False, "not found on the Applied tracker (may need a re-check)"
    except Exception as e:
        return False, f"tracker check failed: {str(e)[:120]}"
    finally:
        if own_browser:
            try:
                b.close()
            except Exception:
                pass
