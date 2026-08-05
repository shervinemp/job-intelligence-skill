"""enrich.py — Fetch job descriptions + enrich fields (title, company, location, salary, category). SLM reviews DESC lines, admits or skips.

Usage:
  enrich.py [--curl] [--force] [--refresh]
  enrich.py admit <jid> [jid...]
  enrich.py reject <jid> [jid...]   Skip (was documented as 'skip', which
                                    no longer dispatches)
  enrich.py flag <jid> [jid...]     Toggle auth wall
  enrich.py undo <jid> [jid...]     Move back one stage
  enrich.py open [<jid>]
  enrich.py retry                   Retry failed fetches
  enrich.py retry-skipped           Reset all skipped jobs back to extracted
"""
import html, json, os, subprocess, sys, time, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from lib.db import load, advance, pipeline_status, get_conn
from lib.db import desc_save, desc_exists, desc_get
from lib.chrome_manager import CHROME_PROFILE as BROWSER_PROFILE, connect
from lib import auth_walls
from lib.platforms import fetch_description

_AUTH_SIGNALS = [
    "sign in", "sign in to view", "sign in to see", "sign in to continue",
    "log in", "log in to view", "log in to continue",
    "create account to view", "join now to see", "please sign in",
    "authwall", "auth_wall", "this page requires you to sign in",
]

# A4 — wall-type triage. A boolean "auth wall?" doesn't tell the operator
# WHAT kind of wall it is, so the recovery path is guessed. Classify the
# signal: session-expired (re-auth, fast), create-account (new account,
# manual), 2FA (manual), or plain login (reuse session). Deterministic.
_WALL_CLASSES = [
    ("session_expired", ["session expired", "session has expired", "signed out",
                         "you've been signed out", "log back in", "sign back in"]),
    ("2fa", ["two-step", "two factor", "2fa", "verification code", "authenticator",
             "text me a code", "enter the code"]),
    ("create_account", ["create account to view", "create an account", "join now to see",
                        "sign up to view", "register to view"]),
    ("login", ["sign in", "log in", "please sign in", "authwall", "auth_wall"]),
]


def _classify_auth_wall(text):
    """Classify an auth-wall text into a type for the recovery path.
    Returns (type, matched_signal) or None when no wall is present."""
    t = (text or "").lower()
    for wtype, signals in _WALL_CLASSES:
        for sig in signals:
            if sig in t:
                return wtype, sig
    return None


def _detect_auth_wall(text):
    t = (text or "").lower()
    for signal in _AUTH_SIGNALS:
        if signal in t:
            return True
    return False


def _pw_fetch(url, timeout=30):
    from urllib.parse import urlparse
    from lib.url_safety import is_safe_url
    parsed = urlparse(url)
    # Skip root URLs (no meaningful path) — they load feed pages, not job descriptions
    path = parsed.path.strip("/")
    if not path or len(path.split("/")) < 2:
        return False, "root_url", None, None, ""

    # This URL came from an email body. Vet it BEFORE handing it to a
    # browser that carries the user's LinkedIn/ATS session cookies.
    ok, why = is_safe_url(url)
    if not ok:
        print(f"  URL_REFUSED: {why}", file=sys.stderr)
        return False, f"unsafe_url: {why}", None, None, ""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # 4-tuple like every other exit — _retry_fetch unpacks four.
        return False, "Playwright not installed", None, None, ""
    page = b = None
    _opened_tab = False
    try:
        # Use the HUMAN'S logged-in profile browser (connect() → port 9222) —
        # the fetch needs the real LinkedIn session, not a separate logged-out
        # Chromium. Root cause of past hangs: killed fetches leaked tabs, the
        # renderer saturated, and CDP blocked. Hygiene below: clean stale job
        # tabs first, reuse one if present, and close it before returning.
        b, ctx = connect()
        if not ctx:
            return False, "Could not connect to Chrome", None, None, ""
        # Clean up any stale job tabs leaked by earlier killed processes, so
        # the renderer isn't saturated when we start.
        try:
            for p in list(ctx.pages):
                u = p.url or ""
                if "jobs/view/" in u and u != url.rstrip("/"):
                    p.close()
        except Exception:
            pass
        # Reuse an existing tab on this exact URL if present (no new leak).
        page = None
        for p in ctx.pages:
            if (p.url or "").rstrip("/") == url.rstrip("/"):
                page = p
                break
        if page is None:
            page = ctx.new_page()
            _opened_tab = True
        page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
        page.wait_for_timeout(2000)
        # Bound the readiness wait: innerText on a huge SPA (LinkedIn's
        # ~3MB job page) forces full layout reflow on EVERY call and can
        # stall. textContent avoids reflow; read once, not in a tight loop.
        dl = time.time() + 5
        t = ""
        while time.time() < dl:
            t = (page.evaluate("() => document.body.textContent") or "").strip()
            if len(t) > 80:
                break
            time.sleep(0.5)
        text = fetch_description(url, page)
        if text and len(text.strip()) > 80:
            page_title = (page.title() or "").strip()
            raw_html = page.evaluate("() => document.documentElement.outerHTML")
            # LinkedIn has no JSON-LD hiringOrganization — read the company
            # from the live page's /company/ anchor links.
            company = ""
            try:
                from lib.platforms.linkedin import extract_company
                company = extract_company(page) or ""
            except Exception:
                pass
            return True, text.strip(), page_title, raw_html, company
        if _detect_auth_wall(text):
            _wtype = _classify_auth_wall(text)
            return False, f"auth_wall:{_wtype[0] if _wtype else 'unknown'}", None, None, ""
        return False, f"Short text ({len(text or '')} chars)", None, None, ""
    except Exception as e:
        return False, str(e)[:120], None, None, ""
    finally:
        # Close the tab we opened so it can't leak and saturate the renderer.
        # Reused tabs (already existed) are left open — closing someone's tab
        # is worse than leaving it.
        try:
            if _opened_tab and page:
                page.close(run_before_unload=False)
        except Exception:
            pass
        try:
            if b:
                b.close()
        except Exception:
            pass


def _retry_fetch(url, use_playwright):
    import random, time
    # LOOK FIRST: log which URL is being fetched so a hang is visible, not
    # silent (the "infinite scroll" that looked like a loop was a fetch with
    # zero progress output — nothing said which URL or attempt).
    print(f"  FETCH[{1 if not use_playwright else 'pw'}]: "
          f"{(url or '')[:90]}", file=sys.stderr)
    for attempt in range(2):
        if attempt:
            print(f"  FETCH attempt {attempt + 1} (retrying)", file=sys.stderr)
        company = ""
        if use_playwright:
            ok, text, page_title, raw_html, company = _pw_fetch(url)
        else:
            ok, text, page_title, raw_html = _curl_fetch(url)
        if ok:
            return True, text, page_title, raw_html, company
        if text.startswith("auth_wall") or text == "Playwright not installed":
            return False, text, None, None, ""
        if attempt < 1:
            delay = 2 + random.random()
            print(f"  Fetch failed ({text[:40]}), retry in {delay:.1f}s...", file=sys.stderr)
            time.sleep(delay)
    return False, text, page_title, None, company


def _enrich_from_ld(raw_html, entry):
    """Extract JSON-LD JobPosting data and backfill empty fields in entry.

    A5: staffing agencies post SEVERAL roles on one page. When >1 posting is
    detected, we must not silently take the first — surface the ambiguity as
    a flag on the entry so the orchestrator decides which role, instead of
    enriching the wrong one."""
    from lib.extract_structured import extract_job_postings
    jobs = extract_job_postings(raw_html)
    if not jobs:
        # LinkedIn does NOT emit JSON-LD hiringOrganization (verified live:
        # 0 JSON-LD blocks). Company is set from the live page by _pw_fetch
        # (extract_company → a[href*="/company/"]), not from raw HTML regex.
        return
    if len(jobs) > 1:
        entry["multi_role"] = True
        entry["multi_role_titles"] = [j.get("title", "")[:80]
                                      for j in jobs[:8]]
    job = jobs[0]
    if not entry.get("title") and job.get("title"):
        entry["title"] = job["title"]
    if not entry.get("company") and job.get("company"):
        entry["company"] = job["company"]
    if not entry.get("location") and job.get("location"):
        entry["location"] = job["location"]
    if not entry.get("salary") and job.get("salary"):
        entry["salary"] = job["salary"]


# B2 — free-text salary/location extraction. Structured JSON-LD is the ideal
# source, but many postings carry salary/location only in prose ("Salary:
# $120k – $150k CAD", "Remote — Toronto, Ontario"). This deterministic parser
# backfills them when structured data is absent. No LLM: it is regex-based and
# testable.
_SALARY_RE = re.compile(
    r"(?:\$|CA\$|C\$|CAD\s?|USD\s?)?\s?"
    r"(\d{2,3}(?:[.,]\d{1,3})?\s?[kK])"
    r"\s*(?:-|–|—|to)\s*"
    r"(?:\$|CA\$|C\$|CAD\s?|USD\s?)?\s?"
    r"(\d{2,3}(?:[.,]\d{1,3})?\s?[kK])"
    r"\s*(CAD|USD|C\$|CA\$)?"
)
_SALARY_CUR_RE = re.compile(r"\b(CAD|USD|CA\$|C\$)\b")
_LOC_RE = re.compile(
    r"(?:location|based in|office in|city)\s*[:\-]?\s*"
    r"([A-Z][A-Za-z.\-]{2,40}?)\s*[,|\-]\s*"
    r"([A-Z][A-Za-z.\-]{2,40})", re.I)


def _extract_salary_prose(text):
    """Salary range from prose: returns (salary_text, currency) or None."""
    if not text:
        return None
    m = _SALARY_RE.search(text)
    if not m:
        return None
    lo, hi = m.group(1), m.group(2)
    cur = m.group(3) or (_SALARY_CUR_RE.search(text) or [None])[0]
    return f"{lo} - {hi} {cur or ''}".strip()


def _extract_location_prose(text):
    """Location from prose: returns the location string or None."""
    if not text:
        return None
    m = _LOC_RE.search(text)
    if not m:
        return None
    city = m.group(1)
    region = m.group(2)
    return f"{city}, {region}" if region else city


def _curl_fetch(url):
    from lib.url_safety import is_safe_url
    ok, why = is_safe_url(url)
    if not ok:
        print(f"  URL_REFUSED: {why}", file=sys.stderr)
        return False, f"unsafe_url: {why}", None, None, ""
    try:
        # -w %{url_effective} reports the FINAL url after redirects so we can
        # re-vet it (A4: a host that looks public on hop 1 can redirect to a
        # private/loopback host on hop 2 — DNS rebinding). --proto/--proto-redir
        # pin schemes across redirects; --max-redirs bounds the loop.
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "30",
             "--proto", "=http,https", "--proto-redir", "=http,https",
             "--max-redirs", "5",
             "-w", "\n%{url_effective}",
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", url],
            capture_output=True, timeout=35
        )
        out = r.stdout
        # Final URL is the last line after the body (curl -w appends it).
        final_url = ""
        try:
            _parts = out.split(b"\n")
            final_url = _parts[-1].decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        if final_url:
            ok2, why2 = is_safe_url(final_url)
            if not ok2:
                print(f"  URL_REFUSED (final redirect target): {why2} — "
                      f"{final_url[:80]}", file=sys.stderr)
                return False, f"unsafe_redirect: {why2}", None, None
        if r.returncode == 0 and out and len(out) > 100:
            raw_html = out.decode('utf-8', errors='replace')
            # Strip the -w %{url_effective} trailer we appended.
            if final_url:
                raw_html = raw_html.rsplit(final_url, 1)[0]
            title_match = re.search(r'<title[^>]*>(.*?)</title>', raw_html, re.DOTALL)
            page_title = html.unescape(title_match.group(1).strip()[:200]) if title_match else ""
            text = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', '\n', text)
            text = re.sub(r'\n\s*\n', '\n\n', text)
            text = re.sub(r'\s{3,}', '  ', text).strip()
            if len(text) > 100:
                return True, text, page_title, raw_html
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return False, "Fetch failed", None, None


def _fetch_from_url(url, use_playwright=False):
    if use_playwright:
        ok, text, page_title, raw_html, company = _retry_fetch(
            url, use_playwright=True)
        if ok:
            return True, text, page_title, raw_html, company
        if text == "auth_wall":
            return False, "auth_wall", None, None, ""
    ok, text, page_title, raw_html, _company = _retry_fetch(
        url, use_playwright=False)
    if ok:
        return True, text, page_title, raw_html, _company
    return False, text or "Fetch failed", None, None, ""


def save_description(jid, text):
    cutoff = int(len(text) * 0.3)
    idx = text.lower().find('copyright', cutoff)
    if idx != -1:
        text = text[:idx].strip()
    desc_save(jid, text)


def cmd_fetch(use_playwright=True, force=False, refresh=False, verbose=False):
    # One-at-a-time: fetch one job description, show DESC, wait for LLM admit/reject.
    state = load()
    stage = "described" if refresh else "extracted"
    pending = [(jid, e) for jid, e in state["jobs"].items()
               if e.get("stage") == stage and e.get("state") == "active" and (force or not desc_exists(jid))]

    # First, auto-admit all pending LinkedIn jobs (pre-scraped descriptions, no review needed)
    linkedin = [(jid, e) for jid, e in pending if e.get("source") == "LinkedIn"]
    if linkedin:
        from lib.db import find_duplicate
        skipped_dups = 0
        for jid, entry in linkedin:
            title = entry.get("title", "")
            company = entry.get("company", "")
            if title and company:
                dup = find_duplicate(jid, title, company)
                if dup and not dup.get("ambiguous"):
                    advance(entry, "described", state="rejected",
                           error=f"duplicate of {dup['id']}")
                    skipped_dups += 1
                    continue
                if dup and dup.get("ambiguous"):
                    # B1: gray-band — admit for review, do not silently reject.
                    print(f"DEDUP_AMBIGUOUS: {jid} close to {dup['id']} "
                          f"(overlap={dup.get('overlap')}) — admitted for "
                          f"review", file=sys.stderr)
            advance(entry, "described")
        get_conn().commit()
        if skipped_dups:
            print(f"DEDUP: skipped {skipped_dups} LinkedIn duplicate(s)", file=sys.stderr)
        print(f"AUTO_ADMIT: {len(linkedin) - skipped_dups} LinkedIn jobs", file=sys.stderr)
        state = load()  # reload after advances
        pending = [(jid, e) for jid, e in state["jobs"].items()
                   if e.get("stage") == stage and e.get("state") == "active" and (force or not desc_exists(jid))]

    if not pending:
        print("NO_PENDING_FETCH", file=sys.stderr)
        return

    # One job at a time — LLM-in-the-middle handoff
    jid, entry = pending[0]
    title = entry.get("title", "")
    company = entry.get("company", "")
    url = entry.get("url", "")
    ok, result, page_title, raw_html, fetched_company = _fetch_from_url(
        url, use_playwright=use_playwright)
    if ok:
        # LinkedIn has no JSON-LD; company comes from the live page's
        # /company/ link (fetched_company). Backfill when the entry lacks it.
        if not entry.get("company") and fetched_company:
            entry["company"] = fetched_company
        save_description(jid, result)
        conn = get_conn()
        need_title = not entry.get("title")
        need_company = not entry.get("company")
        need_location = not entry.get("location")
        need_salary = not entry.get("salary")
        if raw_html:
            _enrich_from_ld(raw_html, entry)
        # B2: when structured data left salary/location empty, backfill from
        # prose ("$120k - $150k CAD", "Remote — Toronto, Ontario").
        if need_salary and not entry.get("salary"):
            _sal = _extract_salary_prose(result)
            if _sal:
                entry["salary"] = _sal
        if need_location and not entry.get("location"):
            _loc = _extract_location_prose(result)
            if _loc:
                entry["location"] = _loc
        sets, vals = [], []
        if page_title and need_title:
            sets.append("title=?")
            vals.append(page_title[:200])
        elif entry.get("title") and need_title:
            sets.append("title=?")
            vals.append(entry["title"])
        if entry.get("company") and need_company:
            sets.append("company=?")
            vals.append(entry["company"])
        if entry.get("location") and need_location:
            sets.append("location=?")
            vals.append(entry["location"])
        if entry.get("salary") and need_salary:
            sets.append("salary=?")
            vals.append(entry["salary"])
        if sets:
            vals.append(jid)
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit()

        # #6: persist the multi-role flag into the job's notes so the
        # orchestrator sees it via report.py inspect / ji job — the stderr
        # MULTI_ROLE line alone is easy to miss. Only set when not already
        # noted (don't overwrite user notes).
        if entry.get("multi_role"):
            try:
                row = conn.execute(
                    "SELECT notes FROM jobs WHERE id=?", (jid,)).fetchone()
                if row:
                    notes = row["notes"] or ""
                    if "MULTI_ROLE" not in notes:
                        conn.execute(
                            "UPDATE jobs SET notes=? WHERE id=?",
                            (notes + f" MULTI_ROLE: "
                                     f"{'; '.join(entry.get('multi_role_titles', []))[:200]}",
                             jid))
                        conn.commit()
            except Exception:
                pass

        final_title = entry.get("title", "")
        final_company = entry.get("company", "")
        if final_title and final_company:
            from lib.db import find_duplicate
            dup = find_duplicate(jid, final_title, final_company, conn)
            if dup:
                if dup.get("ambiguous"):
                    # B1: gray-band duplicate — do not silently reject. Flag
                    # the decision so the orchestrator resolves it.
                    print(f"DEDUP_AMBIGUOUS: {jid} close to {dup['id']} "
                          f"(overlap={dup.get('overlap')}) — "
                          f"{final_title[:40]} @ {final_company[:20]}; "
                          f"reject if same role", file=sys.stderr)
                else:
                    advance(entry, entry.get("stage"), state="rejected",
                            error=f"duplicate of {dup['id']}")
                    conn.commit()
                    print(f"DEDUP: {jid} is a duplicate of {dup['id']} "
                          f"({final_title[:40]} @ {final_company[:20]})", file=sys.stderr)
                    return

        limit = 2000 if verbose else 500
        snippet = re.sub(r'\s+', ' ', result[:limit].replace('\r', '')).strip()
        print(f"DESC:{jid}:{snippet}")
        if entry.get("multi_role"):
            print(f"MULTI_ROLE:{jid}: {' | '.join(entry.get('multi_role_titles', []))}", file=sys.stderr)
            print(f"NEXT: enrich.py admit {jid} --title '<which role>'  OR  enrich.py reject {jid}", file=sys.stderr)
        else:
            print(f"NEXT: enrich.py admit {jid} --category tech|general  OR  enrich.py reject {jid}", file=sys.stderr)
        auth_walls.remove(jid)
    else:
        if result.startswith("auth_wall"):
            auth_walls.add(jid, url, title, company)
            print(f"WALL: {result}  — recovery: session_expired=re-auth via "
                  f"enrich.py open <jid>; create_account/2fa=manual",
                  file=sys.stderr)
        advance(entry, entry.get("stage"), state="failed", error=str(result))
        print(f"NEXT: enrich.py reject {jid}")


def cmd_flag(*jids):
    if not jids:
        print("Usage: python3 enrich.py flag <jid> [jid...]", file=sys.stderr)
        return
    state = load()
    count = 0
    for jid in jids:
        entry = state.get("jobs", {}).get(jid)
        if not entry:
            continue
        url = entry.get("url", "")
        auth_walls.add(jid, url, entry.get("title",""), entry.get("company",""))
        count += 1
    print(f"FLAGGED:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_admit(*jids, **fields):
    state = load()
    cats_path = os.path.join(os.path.dirname(__file__), "categories.json")
    try:
        with open(cats_path) as f:
            cats = json.load(f)
    except Exception as e:
        print(f"ERROR: can't read categories.json: {e}", file=sys.stderr)
        return

    cat = fields.get("category")
    if cat and cat not in cats:
        print(f"ERROR: unknown category '{cat}'. Options: {', '.join(cats)}", file=sys.stderr)
        return

    count = 0
    for jid in jids:
        entry = state.get("jobs", {}).get(jid)
        if not entry or not desc_exists(jid):
            continue
        if entry.get("state") != "active":
            print(f"ERROR: job {jid} is in state '{entry.get('state')}', not active", file=sys.stderr)
            continue
        current_cat = entry.get("category")
        if not cat and not current_cat:
            desc = desc_get(jid)
            if desc:
                limit = 500
                snippet = re.sub(r'\s+', ' ', desc[:limit].replace('\r', '')).strip()
                print(f"DESC:{jid}:{snippet}")
            print(f"ERROR: --category required (no category set). Options: {', '.join(cats)}", file=sys.stderr)
            print(f"  Usage: enrich.py admit {jid} --category <name>", file=sys.stderr)
            continue
        updates = {k: v for k, v in fields.items() if v is not None}
        # Map --team to team_name DB column
        if "team" in updates:
            updates["team_name"] = updates.pop("team")
        advance(entry, "described", **updates)
        print(f"  NEXT: reach.py discover {jid}  (contact discovery — optional)", file=sys.stderr)
        count += 1
    print(f"ADMITTED:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_reject(*jids):
    state = load()
    count = 0
    for jid in jids:
        if jid in state.get("jobs", {}):
            entry = state["jobs"][jid]
            advance(entry, entry.get("stage"), state="rejected", error="garbage")
            count += 1
        else:
            print(f"WARN: unknown jid '{jid}' — full 16-hex jid required",
                  file=sys.stderr)
    print(f"REJECT:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_status():
    s = pipeline_status()
    if not s["jobs"]:
        print("No jobs in state. Run extract first.", file=sys.stderr)
        return
    print(f"Jobs: {s['jobs']} total", file=sys.stderr)
    for stage in ["extracted", "described", "tailored", "applied", "skipped", "failed"]:
        c = s["stages"].get(stage, 0)
        if c:
            print(f"  {stage}: {c}", file=sys.stderr)
    if s["staged"]["pending"]:
        print(f"  staged (pending extraction): {s['staged']['pending']}", file=sys.stderr)
    if s["auth_walls"]["count"]:
        domains = " ".join(s["auth_walls"]["domains"])
        print(f"  auth walls: {s['auth_walls']['count']} ({domains})", file=sys.stderr)
    print(f"  next: {s['next_step']}", file=sys.stderr)


def cmd_retry_skipped():
    conn = get_conn()
    cur = conn.execute("UPDATE jobs SET stage='extracted', state='active', error=NULL WHERE state='rejected'")
    conn.commit()
    count = cur.rowcount
    print(f"UNSKIPPED:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_undo(jid):
    state = load()
    entry = state.get("jobs", {}).get(jid)
    if not entry:
        print(f"Job not found: {jid}", file=sys.stderr)
        return
    if entry.get("stage") not in ("described",):
        print(f"Job is {entry.get('stage')} - can't undo from here", file=sys.stderr)
        return
    advance(entry, "extracted", error=None)
    conn = get_conn()
    conn.execute("DELETE FROM job_documents WHERE doc_type='description' AND job_id=?", (jid,))
    conn.commit()
    print(f"  {jid}: described -> extracted (description cleared)", file=sys.stderr)


def cmd_help():
    print("Usage:", file=sys.stderr)
    print("  [--curl] [--force] [--refresh] [--verbose]   Fetch descriptions", file=sys.stderr)
    print("  admit <jid> [jid...] [--category <name>] [--title ...] [--company ...] [--location ...] [--salary ...] [--url ...] [--notes ...]   Mark described", file=sys.stderr)
    print("  skip <jid> [jid...]                                        Skip (garbage/closed)", file=sys.stderr)
    print("  flag <jid> [jid...]                                       Mark as auth wall", file=sys.stderr)
    print("  open [<jid>]                                              Open in Chrome", file=sys.stderr)
    print("  retry                                                     Retry failed fetches", file=sys.stderr)
    print("  retry-skipped                                             Reset all skipped back to extracted", file=sys.stderr)
    print("  status                                                    Pipeline state", file=sys.stderr)
    print("  help                                                      This message", file=sys.stderr)


def cmd_retry(use_playwright=True):
    state = load()
    failed = [(jid, e) for jid, e in state["jobs"].items() if e.get("state") == "failed"]
    if not failed:
        print("No failed.", file=sys.stderr)
        return
    fetched = 0
    for jid, entry in failed:
        ok, result, _pt, _rh, _co = _fetch_from_url(
            entry.get("url", ""), use_playwright=use_playwright)
        if ok:
            save_description(jid, result)
            snippet = re.sub(r'\s+', ' ', result[:200].replace('\r', '')).strip()
            print(f"DESC:{jid}:{entry.get('title','')[:40]}:{snippet}")
            auth_walls.remove(jid)
            fetched += 1
        else:
            if result.startswith("auth_wall"):
                auth_walls.add(jid, entry.get("url", ""), entry.get("title", ""), entry.get("company", ""))
                print(f"WALL: {result} — recovery per wall type", file=sys.stderr)
            advance(entry, entry.get("stage"), state="failed", error=str(result))
    print(f"RETRY:{fetched}", file=sys.stderr)


def cmd_open(*jids):
    if jids:
        jid = jids[0]
        state = load()
        entry = state.get("jobs", {}).get(jid)
        if not entry:
            print(f"Job not found: {jid}", file=sys.stderr)
            return
        url = entry.get("url", "")
        print(f"Opening {entry.get('title','')[:40]} @ {entry.get('company','')[:20]}", file=sys.stderr)
    else:
        entries = auth_walls.list_all()
        if entries:
            url = entries[0].get("url", "https://linkedin.com")
            print(f"Opening: {entries[0].get('title','')[:40]} @ {entries[0].get('company','')[:20]}", file=sys.stderr)
        else:
            state = load()
            for jid, e in state.get("jobs", {}).items():
                if e.get("stage") in ("extracted", "described"):
                    url = e.get("url", "")
                    print(f"Opening {e.get('title','')[:40]} @ {e.get('company','')[:20]}", file=sys.stderr)
                    break
            else:
                print("No jobs to open.", file=sys.stderr)
                return
    b, ctx = connect()
    if ctx:
        p = ctx.new_page()
        try:
            p.bring_to_front()
        except Exception:
            pass
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        print("Opened. Close tab when done.", file=sys.stderr)
    else:
        print("Could not open Chrome.", file=sys.stderr)


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="enrich.py", description="Fetch descriptions + enrich job fields (title, company, location, salary, category)")
    parser.add_argument("--curl", action="store_true", help="Use curl instead of Playwright")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if description exists")
    parser.add_argument("--refresh", action="store_true", help="Fetch from described stage")
    parser.add_argument("--verbose", action="store_true", help="Show more description text")

    sub = parser.add_subparsers(dest="command")
    sub.required = False
    admit_p = sub.add_parser("admit", help="Mark jobs as described")
    admit_p.add_argument("jids", nargs="+")
    admit_p.add_argument("--title", help="Job title")
    admit_p.add_argument("--company", help="Company name")
    admit_p.add_argument("--location", help="Job location")
    admit_p.add_argument("--salary", help="Salary range")
    admit_p.add_argument("--category", help="Job category (tech/general)")
    admit_p.add_argument("--notes", help="Job notes/context")
    admit_p.add_argument("--url", help="External apply URL")
    admit_p.add_argument("--team", help="Team/department name (e.g. 'AI/ML', 'Product')")
    sub.add_parser("reject", help="Skip (garbage/closed)").add_argument("jids", nargs="+")
    sub.add_parser("flag", help="Mark as auth wall").add_argument("jids", nargs="*")
    sub.add_parser("open", help="Open job in Chrome").add_argument("jid", nargs="?")
    retry_p = sub.add_parser("retry", help="Retry failed fetches")
    retry_p.add_argument("--curl", action="store_true",
                         help="Use curl instead of Playwright")
    sub.add_parser("retry-skipped", help="Reset skipped back to extracted")
    sub.add_parser("undo", help="Move described job back to extracted").add_argument("jid")
    sub.add_parser("help", help="This message")

    args = parser.parse_args()
    
    if args.command == "admit":
        cmd_admit(*args.jids, title=args.title, company=args.company, location=args.location, salary=args.salary, category=args.category, notes=args.notes, url=args.url, team=args.team)
    elif args.command == "reject":
        cmd_reject(*args.jids)
    elif args.command == "flag":
        cmd_flag(*args.jids)
    elif args.command == "open":
        cmd_open(args.jid)
    elif args.command == "retry":
        cmd_retry(use_playwright=not args.curl)
    elif args.command == "retry-skipped":
        cmd_retry_skipped()
    elif args.command == "undo":
        cmd_undo(args.jid)
    elif args.command == "help":
        cmd_help()
    else:
        cmd_fetch(
            use_playwright=not args.curl,
            force=args.force,
            refresh=args.refresh,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    main()
