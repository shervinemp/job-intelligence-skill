"""fetch.py — Fetch job descriptions. SLM reviews DESC lines, admits or rejects."""
import os, subprocess, sys, time, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from lib.db import load, save, advance, pipeline_status
from lib.db import desc_save, desc_exists
from lib.chrome_manager import CHROME_PROFILE as BROWSER_PROFILE, connect
from lib import auth_walls

MAX_DESC_LEN = 8000


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


def fetch_description(url, use_playwright=False):
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
            auth_walls.remove(jid)
            fetched += 1
        else:
            if result == "auth_wall":
                auth_walls.add(jid, url, title, company)
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
        auth_walls.add(jid, url, entry.get("title",""), entry.get("company",""))
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
            auth_walls.remove(jid)
            fetched += 1
        else:
            if result == "auth_wall":
                auth_walls.add(jid, entry.get("url", ""), entry.get("title", ""), entry.get("company", ""))
            advance(entry, "failed", error=str(result))
        save(state)
    print(f"RETRY:{fetched}", file=sys.stderr)


def cmd_open():
    """Open single visible browser tab for the first flagged job. Browser stays open 5min."""
    from playwright.sync_api import sync_playwright
    entries = auth_walls.list_all()
    if not entries:
        print("NO_AUTH_WALLS", file=sys.stderr)
        return

    entry = entries[0]
    url = entry.get("url", "https://linkedin.com")
    print(f"OPENING: {entry.get('title','')[:40]} @ {entry.get('company','')[:20]}", file=sys.stderr)
    print("Log in, close browser. Pipeline will retry flagged jobs after.", file=sys.stderr)

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
        b.close()
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
    print("LOGIN_DONE — retrying flagged jobs", file=sys.stderr)
    cmd_run(count=30, use_playwright=True, force=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fetch.py <cmd> [args]", file=sys.stderr)
        print("Commands: run, admit, reject, flag, open, status, retry", file=sys.stderr)
        sys.exit(1)
    use_pw = '--curl' not in sys.argv
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
