"""fetch.py — Fetch job descriptions. SLM reviews DESC lines, admits or rejects.

Usage:
  fetch.py [--count N] [--curl] [--force] [--refresh]   (default --count 3)
  fetch.py admit <jid> [jid...]
  fetch.py reject <jid> [jid...]
  fetch.py flag <jid> [jid...]
  fetch.py open [<jid>]
  fetch.py retry               Retry failed fetches
  fetch.py retry-skipped       Reset all skipped jobs back to extracted
  fetch.py status
"""
import os, subprocess, sys, time, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from lib.db import load, advance, pipeline_status, get_conn
from lib.db import desc_save, desc_exists
from lib.chrome_manager import CHROME_PROFILE as BROWSER_PROFILE, connect
from lib import auth_walls
from lib.platforms import fetch_description

MAX_DESC_LEN = 8000

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
            text = fetch_description(url, page)
            if text and len(text.strip()) > 80:
                return True, text.strip()
            if _detect_auth_wall(text):
                return False, "auth_wall"
            return False, f"Short text ({len(text or '')} chars)"
        else:
            with sync_playwright() as spw:
                ctx = spw.chromium.launch_persistent_context(BROWSER_PROFILE, headless=True, no_viewport=True)
                page = ctx.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                page.wait_for_timeout(2000)
                text = fetch_description(url, page)
                if text and len(text.strip()) > 80:
                    return True, text.strip()
                if _detect_auth_wall(text):
                    return False, "auth_wall"
                return False, f"Short text ({len(text or '')} chars)"
    except Exception as e:
        return False, str(e)[:120]
    finally:
        try:
            if page:
                page.close(run_before_unload=False)
                time.sleep(0.3)
        except Exception as e:
            print(f"WARN: page.close failed ({e})", file=sys.stderr)


def _fetch_from_url(url, use_playwright=False):
    if use_playwright:
        ok, text = _pw_fetch(url)
        if ok:
            return True, text
        if text == "auth_wall":
            return False, "auth_wall"
    try:
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "30",
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", url],
            capture_output=True, timeout=35
        )
        out = r.stdout
        if r.returncode == 0 and out and len(out) > 100:
            text = out.decode('utf-8', errors='replace')
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', '\n', text)
            text = re.sub(r'\n\s*\n', '\n\n', text)
            text = re.sub(r'\s{3,}', '  ', text).strip()
            if len(text) > 100:
                return True, text[:MAX_DESC_LEN]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return False, "Fetch failed"


def save_description(jid, text):
    text = text[:MAX_DESC_LEN]
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
    # Skip known-broken trackers
    pending = [(jid, e) for jid, e in pending
               if "cts.indeed.com" not in e.get("url", "")
               and "ca.indeed.com/pagead" not in e.get("url", "")]
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
        ok, result = _fetch_from_url(url, use_playwright=use_playwright)
        if ok:
            save_description(jid, result)
            limit = 2000 if verbose else 500
            snippet = re.sub(r'\s+', ' ', result[:limit].replace('\r', '')).strip()
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
        print("Usage: python3 fetch.py flag <jid> [jid...]", file=sys.stderr)
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


def cmd_admit(*jids):
    state = load()
    count = 0
    for jid in jids:
        if jid in state.get("jobs", {}) and desc_exists(jid):
            advance(state["jobs"][jid], "described")
            count += 1
    print(f"ADMITTED:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_reject(*jids):
    state = load()
    count = 0
    for jid in jids:
        if jid in state.get("jobs", {}):
            advance(state["jobs"][jid], "skipped", error="garbage")
            count += 1
    print(f"REJECTED:{count}", file=sys.stderr)
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


def cmd_help():
    print("Usage:", file=sys.stderr)
    print("  [--count N] [--curl] [--force] [--refresh] [--verbose]   Fetch descriptions (default 3)", file=sys.stderr)
    print("  admit <jid> [jid...]                                      Mark job as described", file=sys.stderr)
    print("  reject <jid> [jid...]                                     Skip (garbage/closed)", file=sys.stderr)
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
        p.close()
        b.close()
        print("Opened. Close tab when done.", file=sys.stderr)
    else:
        print("Could not open Chrome.", file=sys.stderr)


def _parse_count():
    if "--count" in sys.argv:
        i = sys.argv.index("--count")
        if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
            print("Warning: --count requires a number, defaulting to 3", file=sys.stderr)
            return 3
        return int(sys.argv[i + 1])
    return 3


def main():
    subcommands = {"admit", "reject", "flag", "open", "retry", "retry-skipped", "status", "help"}
    if len(sys.argv) > 1 and sys.argv[1] in subcommands:
        cmd = sys.argv[1]
        if cmd == "admit":
            cmd_admit(*sys.argv[2:])
        elif cmd == "reject":
            cmd_reject(*sys.argv[2:])
        elif cmd == "flag":
            cmd_flag(*sys.argv[2:])
        elif cmd == "open":
            cmd_open(*sys.argv[2:])
        elif cmd == "retry":
            cmd_retry(use_playwright='--curl' not in sys.argv)
        elif cmd == "retry-skipped":
            cmd_retry_skipped()
        elif cmd == "status":
            cmd_status()
        elif cmd == "help":
            cmd_help()
    elif len(sys.argv) == 1 or sys.argv[1].startswith("--"):
        cmd_fetch(
            count=_parse_count(),
            use_playwright='--curl' not in sys.argv,
            force='--force' in sys.argv,
            refresh='--refresh' in sys.argv,
            verbose='--verbose' in sys.argv,
        )
    else:
        print(f"Unknown subcommand: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
