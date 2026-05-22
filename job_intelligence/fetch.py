"""fetch.py — Fetch job descriptions. SLM reviews DESC lines, admits or rejects."""
import hashlib, json, os, subprocess, sys, time, re, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from lib.db import load, save, advance
from lib.db import desc_save, desc_exists, desc_get, get_jobs_by_stage, get_job
from lib.chrome_manager import CHROME_PROFILE as BROWSER_PROFILE, connect

MAX_DESC_LEN = 8000

NEEDS_AUTH_PATH = os.path.join(os.path.expanduser('~'), '.openclaw', 'needs_auth.json')


def _record_auth_wall(jid, url, title, company):
    entries = []
    existing_jids = set()
    if os.path.exists(NEEDS_AUTH_PATH):
        try:
            with open(NEEDS_AUTH_PATH, "r", encoding="utf-8") as f:
                entries = json.load(f)
                existing_jids = {e.get("jid") for e in entries}
        except (json.JSONDecodeError, IOError):
            entries = []
    if jid in existing_jids:
        return
    domain = urlparse(url).netloc
    entries.append({"jid": jid, "url": url, "domain": domain, "title": title, "company": company})
    with open(NEEDS_AUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)




def _pw_fetch(url, timeout=30):
    """Fetch a URL via Playwright. Uses chrome_manager for CDP or fallback."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright not installed"
    try:
        b, ctx = connect()
        if ctx:
            p = ctx.pages[0] if ctx.pages else ctx.new_page()
            p.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            p.wait_for_timeout(2000)
            text = p.evaluate('document.body.innerText')
            b.close()
            if text and len(text.strip()) > 80:
                return True, text.strip()
            return False, f"Short text ({len(text or '')} chars)"
        else:
            with sync_playwright() as spw:
                ctx = spw.chromium.launch_persistent_context(BROWSER_PROFILE, headless=True, no_viewport=True)
                p = ctx.pages[0] if ctx.pages else ctx.new_page()
                p.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                p.wait_for_timeout(2000)
                text = p.evaluate('document.body.innerText')
                ctx.close()
                if text and len(text.strip()) > 80:
                    return True, text.strip()
                return False, f"Short text ({len(text or '')} chars)"
    except Exception as e:
        return False, str(e)[:120]


# _pw_chase is an alias for _pw_fetch (both use chrome_manager)
def _pw_chase(url, timeout=30):
    return _pw_fetch(url, timeout)


def fetch_description(url, use_playwright=False):
    if use_playwright:
        ok, text = _pw_chase(url)
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
    desc_save(jid, text[:MAX_DESC_LEN])


def cmd_run(count=None, use_playwright=True, force=False):
    state = load()
    pending = [(jid, e) for jid, e in state["jobs"].items()
               if e.get("stage") == "extracted" and (force or not desc_exists(jid))]
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
        ok, result = fetch_description(url, use_playwright=use_playwright)
        if ok:
            save_description(jid, result)
            snippet = re.sub(r'\s+', ' ', result[:200].replace('\r', '')).strip()
            print(f"DESC:{jid}:{snippet}")
            fetched += 1
        else:
            if result == "auth_wall":
                _record_auth_wall(jid, url, title, company)
            advance(entry, "failed", error=str(result))
            failed += 1
        save(state)
    print(f"FETCHED:{fetched} FAILED:{failed}", file=sys.stderr)


def cmd_flag(*jids):
    """Mark jobs as needing human attention (auth wall, cookie wall, etc.) without rejecting. Stays at extracted."""
    state = load()
    count = 0
    for jid in jids:
        entry = state.get("jobs", {}).get(jid)
        if not entry:
            continue
        url = entry.get("url", "")
        _record_auth_wall(jid, url, entry.get("title",""), entry.get("company",""))
        count += 1
    save(state)
    print(f"FLAGGED:{count}", file=sys.stderr)


def cmd_admit(*jids):
    state = load()
    count = 0
    for jid in jids:
        if jid in state.get("jobs", {}) and desc_exists(jid):
            advance(state["jobs"][jid], "described")
            count += 1
    save(state)
    print(f"ADMITTED:{count}", file=sys.stderr)


def cmd_reject(*jids):
    state = load()
    count = 0
    for jid in jids:
        if jid in state.get("jobs", {}):
            advance(state["jobs"][jid], "skipped", error="garbage")
            count += 1
    save(state)
    print(f"REJECTED:{count}", file=sys.stderr)


def cmd_status():
    from lib.db import STAGES
    state = load()
    if not state.get("jobs"):
        print("No jobs in state.", file=sys.stderr)
        return
    for s in STAGES:
        c = state['stages'].get(s, 0)
        if c:
            print(f"  {s}: {c}")
    if os.path.exists(NEEDS_AUTH_PATH):
        try:
            import json
            entries = json.load(open(NEEDS_AUTH_PATH))
            domains = sorted(set(e.get("domain", "?") for e in entries))
            if domains:
                print(f"  auth: {' '.join(domains)}")
        except Exception:
            pass


def cmd_retry(use_playwright=True):
    state = load()
    failed = [(jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "failed"]
    if not failed:
        print("No failed.", file=sys.stderr)
        return
    fetched = 0
    for jid, entry in failed:
        ok, result = fetch_description(entry.get("url", ""), use_playwright=use_playwright)
        if ok:
            save_description(jid, result)
            snippet = re.sub(r'\s+', ' ', result[:200].replace('\r', '')).strip()
            print(f"DESC:{jid}:{entry.get('title','')[:40]}:{snippet}")
            fetched += 1
        else:
            if result == "auth_wall":
                _record_auth_wall(jid, entry.get("url", ""), entry.get("title", ""), entry.get("company", ""))
            advance(entry, "failed", error=str(result))
        save(state)
    print(f"RETRY:{fetched}", file=sys.stderr)


def cmd_open():
    """Open single visible browser tab for the first flagged job. Browser stays open 5min."""
    from playwright.sync_api import sync_playwright
    entries = []
    if os.path.exists(NEEDS_AUTH_PATH):
        try:
            entries = json.load(open(NEEDS_AUTH_PATH))
        except Exception:
            pass
    if not entries:
        print("NO_AUTH_WALLS", file=sys.stderr)
        return

    entry = entries[0]
    url = entry.get("url", "https://linkedin.com")
    print(f"OPENING: {entry.get('title','')[:40]} @ {entry.get('company','')[:20]}", file=sys.stderr)
    print("Log in, close browser. Pipeline auto-retries after.", file=sys.stderr)

    # Try connecting to an existing Chrome first (e.g. Gemini browser already running)
    b, ctx = connect()
    if ctx is not None:
        p = ctx.pages[0] if ctx.pages else ctx.new_page()
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        p.wait_for_timeout(2000)
        for i in range(300):
            try:
                pages = ctx.pages
                if not pages:
                    break
                p = pages[0]
                if p.url() == "about:blank" or p.is_closed():
                    break
            except Exception:
                break
            time.sleep(1)
        pw.stop()
    else:
        with sync_playwright() as spw:
            b = spw.chromium.launch_persistent_context(
                BROWSER_PROFILE, headless=False, no_viewport=True,
            )
            p = b.pages[0] if b.pages else b.new_page()
            p.goto(url, wait_until="domcontentloaded", timeout=30000)
            p.wait_for_timeout(2000)
            for i in range(300):
                try:
                    if not b.pages:
                        break
                    p = b.pages[0]
                    if p.url() == "about:blank" or p.is_closed():
                        break
                except Exception:
                    break
                time.sleep(1)
            b.close()
    os.remove(NEEDS_AUTH_PATH)
    print("LOGIN_DONE — retrying flagged jobs", file=sys.stderr)
    cmd_run(count=30, use_playwright=True, force=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fetch.py <cmd> [args]", file=sys.stderr)
        print("Commands: run, admit, reject, flag, open, status, retry", file=sys.stderr)
        sys.exit(1)
    use_pw = '--curl' not in sys.argv  # playwright is default
    force = '--force' in sys.argv
    cmd = sys.argv[1]
    if cmd == "run":
        count = None
        if "--count" in sys.argv:
            i = sys.argv.index("--count")
            if i + 1 < len(sys.argv):
                count = int(sys.argv[i + 1])
        cmd_run(count=count, use_playwright=use_pw, force=force)
    elif cmd == "admit":
        cmd_admit(*sys.argv[2:])
    elif cmd == "flag":
        cmd_flag(*sys.argv[2:])
    elif cmd == "open":
        cmd_open()
    elif cmd == "reject":
        cmd_reject(*sys.argv[2:])
    elif cmd == "status":
        cmd_status()
    elif cmd == "retry":
        cmd_retry(use_playwright=use_pw)
    else:
        print(f"Unknown: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
