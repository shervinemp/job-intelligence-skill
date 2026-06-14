"""enrich.py — Fetch job descriptions + enrich fields (title, company, location, salary, category). SLM reviews DESC lines, admits or skips.

Usage:
  enrich.py [--count N] [--curl] [--force] [--refresh]   (default --count 3)
  enrich.py admit <jid> [jid...]
  enrich.py skip <jid> [jid...]
  enrich.py flag <jid> [jid...]
  enrich.py open [<jid>]
  enrich.py retry               Retry failed fetches
  enrich.py retry-skipped       Reset all skipped jobs back to extracted
  enrich.py status
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


def _detect_auth_wall(text):
    t = (text or "").lower()
    for signal in _AUTH_SIGNALS:
        if signal in t:
            return True
    return False


def _pw_fetch(url, timeout=30):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright not installed"
    page = b = None
    try:
        b, ctx = connect()
        if ctx:
            page = ctx.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            page.wait_for_timeout(2000)
            dl = time.time() + 5
            while time.time() < dl:
                t = (page.evaluate("() => document.body.innerText") or "").strip()
                if len(t) > 80:
                    break
                time.sleep(0.5)
            text = fetch_description(url, page)
            if text and len(text.strip()) > 80:
                page_title = (page.title() or "").strip()
                raw_html = page.evaluate("() => document.documentElement.outerHTML")
                return True, text.strip(), page_title, raw_html
            if _detect_auth_wall(text):
                return False, "auth_wall", None, None
            return False, f"Short text ({len(text or '')} chars)", None, None
        else:
            with sync_playwright() as spw:
                ctx = spw.chromium.launch_persistent_context(BROWSER_PROFILE, headless=True, no_viewport=True)
                page = ctx.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                page.wait_for_timeout(2000)
                dl = time.time() + 5
                while time.time() < dl:
                    t = (page.evaluate("() => document.body.innerText") or "").strip()
                    if len(t) > 80:
                        break
                    time.sleep(0.5)
                text = fetch_description(url, page)
                if text and len(text.strip()) > 80:
                    page_title = (page.title() or "").strip()
                    raw_html = page.evaluate("() => document.documentElement.outerHTML")
                    return True, text.strip(), page_title, raw_html
                if _detect_auth_wall(text):
                    return False, "auth_wall", None, None
                return False, f"Short text ({len(text or '')} chars)", None, None
    except Exception as e:
        return False, str(e)[:120], None, None
    finally:
        try:
            if page:
                page.close(run_before_unload=False)
                time.sleep(0.3)
        except Exception as e:
            print(f"WARN: page.close failed ({e})", file=sys.stderr)


def _retry_fetch(url, use_playwright):
    import random, time
    for attempt in range(2):
        if use_playwright:
            ok, text, page_title, raw_html = _pw_fetch(url)
        else:
            ok, text, page_title, raw_html = _curl_fetch(url)
        if ok:
            return True, text, page_title, raw_html
        if text in ("auth_wall", "Playwright not installed"):
            return False, text, None, None
        if attempt < 1:
            delay = 2 + random.random()
            print(f"  Fetch failed ({text[:40]}), retry in {delay:.1f}s...", file=sys.stderr)
            time.sleep(delay)
    return False, text, page_title, None


def _enrich_from_ld(raw_html, entry):
    """Extract JSON-LD JobPosting data and backfill empty fields in entry."""
    from lib.extract_structured import extract_job_postings
    jobs = extract_job_postings(raw_html)
    if not jobs:
        return
    job = jobs[0]
    if not entry.get("title") and job.get("title"):
        entry["title"] = job["title"]
    if not entry.get("company") and job.get("company"):
        entry["company"] = job["company"]
    if not entry.get("location") and job.get("location"):
        entry["location"] = job["location"]
    if not entry.get("salary") and job.get("salary"):
        entry["salary"] = job["salary"]


def _curl_fetch(url):
    try:
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "30",
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", url],
            capture_output=True, timeout=35
        )
        out = r.stdout
        if r.returncode == 0 and out and len(out) > 100:
            raw_html = out.decode('utf-8', errors='replace')
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
        ok, text, page_title, raw_html = _retry_fetch(url, use_playwright=True)
        if ok:
            return True, text, page_title, raw_html
        if text == "auth_wall":
            return False, "auth_wall", None, None
    ok, text, page_title, raw_html = _retry_fetch(url, use_playwright=False)
    if ok:
        return True, text, page_title, raw_html
    return False, text or "Fetch failed", None, None


def save_description(jid, text):
    cutoff = int(len(text) * 0.3)
    idx = text.lower().find('copyright', cutoff)
    if idx != -1:
        text = text[:idx].strip()
    desc_save(jid, text)


def cmd_fetch(count=None, use_playwright=True, force=False, refresh=False, verbose=False):
    state = load()
    stage = "described" if refresh else "extracted"
    pending = [(jid, e) for jid, e in state["jobs"].items()
               if e.get("stage") == stage and (force or not desc_exists(jid))]

    if count:
        pending = pending[:count]
    if not pending:
        print("NO_PENDING_FETCH", file=sys.stderr)
        return

    fetched = failed = 0
    for jid, entry in pending:
        title = entry.get("title", "")
        company = entry.get("company", "")
        url = entry.get("url", "")
        ok, result, page_title, raw_html = _fetch_from_url(url, use_playwright=use_playwright)
        if ok:
            save_description(jid, result)
            conn = get_conn()
            need_title = not entry.get("title")
            need_company = not entry.get("company")
            need_location = not entry.get("location")
            need_salary = not entry.get("salary")
            # Enrich from JSON-LD structured data (only backfill empty fields)
            if raw_html:
                _enrich_from_ld(raw_html, entry)
            # Build updates for any newly-filled fields
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
            limit = 2000 if verbose else 500
            snippet = re.sub(r'\s+', ' ', result[:limit].replace('\r', '')).strip()
            print(f"IS THIS A JOB POSTING? (admit/reject)", file=sys.stderr)
            print(f"DESC:{jid}:{snippet}")
            auth_walls.remove(jid)
            fetched += 1
        else:
            if result == "auth_wall":
                auth_walls.add(jid, url, title, company)
            advance(entry, "failed", error=str(result))
            failed += 1
    print(f"FETCHED:{fetched} FAILED:{failed}", file=sys.stderr)


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
        advance(entry, "described", **updates)
        count += 1
    print(f"ADMITTED:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_skip(*jids):
    state = load()
    count = 0
    for jid in jids:
        if jid in state.get("jobs", {}):
            advance(state["jobs"][jid], "skipped", error="garbage")
            count += 1
    print(f"SKIP:{count}", file=sys.stderr)
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
    cur = conn.execute("UPDATE jobs SET stage='extracted', error=NULL WHERE stage='skipped'")
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
    print("  [--count N] [--curl] [--force] [--refresh] [--verbose]   Fetch descriptions (default 3)", file=sys.stderr)
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
    failed = [(jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "failed"]
    if not failed:
        print("No failed.", file=sys.stderr)
        return
    fetched = 0
    for jid, entry in failed:
        ok, result = _fetch_from_url(entry.get("url", ""), use_playwright=use_playwright)
        if ok:
            save_description(jid, result)
            snippet = re.sub(r'\s+', ' ', result[:200].replace('\r', '')).strip()
            print(f"DESC:{jid}:{entry.get('title','')[:40]}:{snippet}")
            auth_walls.remove(jid)
            fetched += 1
        else:
            if result == "auth_wall":
                auth_walls.add(jid, entry.get("url", ""), entry.get("title", ""), entry.get("company", ""))
            advance(entry, "failed", error=str(result))
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
        if jids:
            from apply.common.page_manager import PageManager
            PageManager(ctx, jids[0]).register(p)
        print("Opened. Close tab when done.", file=sys.stderr)
    else:
        print("Could not open Chrome.", file=sys.stderr)


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="enrich.py", description="Fetch descriptions + enrich job fields (title, company, location, salary, category)")
    parser.add_argument("--count", type=int, default=3, help="Jobs to fetch (default 3)")
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
    sub.add_parser("reject", help="Skip (garbage/closed)").add_argument("jids", nargs="+")
    sub.add_parser("flag", help="Mark as auth wall").add_argument("jids", nargs="*")
    sub.add_parser("open", help="Open job in Chrome").add_argument("jid", nargs="?")
    sub.add_parser("retry", help="Retry failed fetches")
    sub.add_parser("retry-skipped", help="Reset skipped back to extracted")
    sub.add_parser("undo", help="Move described job back to extracted").add_argument("jid")
    sub.add_parser("status", help="Pipeline state")
    sub.add_parser("help", help="This message")

    args = parser.parse_args()
    
    if args.command == "admit":
        cmd_admit(*args.jids, title=args.title, company=args.company, location=args.location, salary=args.salary, category=args.category, notes=args.notes, url=args.url)
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
    elif args.command == "status":
        cmd_status()
    elif args.command == "help":
        cmd_help()
    else:
        cmd_fetch(
            count=args.count,
            use_playwright=not args.curl,
            force=args.force,
            refresh=args.refresh,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    main()
