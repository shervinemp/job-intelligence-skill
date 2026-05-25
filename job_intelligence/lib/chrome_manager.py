"""chrome_manager.py — Shared Chrome lifecycle management for the pipeline.

All components (fetch, tailor, apply, gemini.js) use this module instead of
managing their own profile paths and CDP connections.

On import, writes ~/.openclaw/chrome-config.json so Node.js tools (gemini.js)
can read the same paths.
"""

import json
import os
import subprocess
import sys
import time

CHROME_PATH = os.environ.get(
    "CHROME_PATH",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
)
CHROME_PROFILE = os.path.join(
    os.path.expanduser("~"), ".openclaw", "chrome-profile"
)
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
_PW = None  # cached playwright instance


def _pw():
    global _PW
    if _PW is None:
        from playwright.sync_api import sync_playwright
        _PW = sync_playwright().start()
    return _PW


def close():
    global _PW
    if _PW:
        try:
            _PW.stop()
        except Exception:
            pass
        _PW = None


def is_running():
    """Check if Chrome is running with remote debugging on CDP_PORT."""
    try:
        pw = _pw()
        b = pw.chromium.connect_over_cdp(CDP_URL)
        b.close()
        return True
    except Exception:
        return False


def start():
    """Start Chrome with remote debugging on CDP_PORT."""
    if is_running():
        return True
    os.makedirs(CHROME_PROFILE, exist_ok=True)
    subprocess.Popen(
        [CHROME_PATH,
         f"--user-data-dir={CHROME_PROFILE}",
         f"--remote-debugging-port={CDP_PORT}",
         "--no-first-run", "--disable-session-crashed-bubble",
         "--disable-restore-session-state"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for _ in range(30):
        if is_running():
            return True
        time.sleep(1)
    return False


def connect(timeout=15):
    """Get a (browser, context) pair connected to the running Chrome.
    Starts Chrome if not running."""
    if not is_running():
        if not start():
            return None, None
    pw = _pw()
    for _ in range(timeout):
        try:
            b = pw.chromium.connect_over_cdp(CDP_URL)
            ctx = b.contexts[0]
            return b, ctx
        except Exception:
            time.sleep(1)
    return None, None


def new_page(timeout=15):
    """Convenience: connect, return a new page."""
    b, ctx = connect(timeout)
    if not ctx:
        return None, None
    p = ctx.new_page()
    return b, p


def session_ok(url, check_text="Sign in", timeout=15):
    """Check if a logged-in session is valid for the given URL.
    Navigates to URL, checks that check_text is NOT present."""
    b, p = new_page(timeout)
    if not p:
        return False
    try:
        p.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        time.sleep(2)
        text = p.evaluate("document.body.innerText")
        return check_text not in text and check_text not in text.lower()
    except Exception:
        return False
    finally:
        try:
            b.close()
        except Exception:
            pass


# On import, write a JSON config that Node tools (gemini.js) can read
_CONFIG_PATH = os.path.join(os.path.dirname(CHROME_PROFILE), "chrome-config.json")
try:
    with open(_CONFIG_PATH, "w") as f:
        json.dump({
            "CHROME_PATH": CHROME_PATH,
            "CHROME_PROFILE": CHROME_PROFILE,
            "CDP_PORT": CDP_PORT,
            "CDP_URL": CDP_URL,
        }, f, indent=2)
except Exception:
    pass
