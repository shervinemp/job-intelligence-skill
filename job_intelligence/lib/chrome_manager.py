"""chrome_manager.py — Shared Chrome lifecycle management for the pipeline.

All components (fetch, tailor, apply, gemini.js) use this module instead of
managing their own profile paths and CDP connections.

On import, writes ~/.openclaw/chrome-config.json so Node.js tools (gemini.js)
can read the same paths.
"""

import atexit
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

_LOCK_PATH = Path(os.path.expanduser("~")) / ".openclaw" / "pipeline.lock"


def _acquire_lock():
    """Prevent concurrent pipeline processes from corrupting shared state."""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _LOCK_PATH.exists():
        try:
            pid = int(_LOCK_PATH.read_text().strip())
            if os.name == "nt":
                # Windows: check if process exists via tasklist
                import subprocess as sp
                r = sp.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                          capture_output=True, timeout=5)
                alive = str(pid) in r.stdout.decode()
            else:
                os.kill(pid, 0)
                alive = True
            if alive:
                print(f"ERROR: pipeline already running (PID {pid})", file=sys.stderr)
                print(f"  Lockfile: {_LOCK_PATH}", file=sys.stderr)
                print(f"  If stuck, delete the lockfile and retry.", file=sys.stderr)
                sys.exit(1)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass  # stale lockfile — overwrite
    _LOCK_PATH.write_text(str(os.getpid()))
    atexit.register(_release_lock)


def _release_lock():
    try:
        if _LOCK_PATH.exists() and _LOCK_PATH.read_text().strip() == str(os.getpid()):
            _LOCK_PATH.unlink()
    except Exception:
        pass


_acquire_lock()

CHROME_PATH = os.environ.get("CHROME_PATH") or (
    shutil.which("google-chrome") or shutil.which("chromium-browser")
    or shutil.which("chrome") or "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
)
CHROME_PROFILE = os.path.join(
    os.path.expanduser("~"), ".openclaw", "chrome-profile"
)
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-default-browser-check",
    "--disable-component-update",
    "--disable-background-timer-throttling",
]
_PW = None
_PW_PID = None


def _pw():
    global _PW, _PW_PID
    if _PW is None:
        from playwright.sync_api import sync_playwright
        _PW = sync_playwright().start()
        _PW_PID = os.getpid()
    return _PW


def close():
    global _PW
    if _PW:
        try:
            _PW.stop()
        except Exception:
            pass
        _PW = None


def _cleanup(signum=None, frame=None):
    close()
    if signum is not None:
        sys.exit(1)


atexit.register(_cleanup)
if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)


def is_running():
    """Lightweight CDP liveness check via socket — avoids full Playwright connect."""
    try:
        s = socket.create_connection(("127.0.0.1", CDP_PORT), timeout=2)
        s.close()
        return True
    except (OSError, socket.timeout):
        return False


def _find_free_port():
    port = CDP_PORT
    for _ in range(10):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            port += 1
        except (OSError, socket.timeout):
            return port
    return CDP_PORT


def start():
    """Start Chrome with remote debugging on CDP_PORT."""
    port = _find_free_port()
    global CDP_PORT, CDP_URL
    CDP_PORT = port
    CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
    if is_running():
        return True
    os.makedirs(CHROME_PROFILE, exist_ok=True)
    subprocess.Popen(
        [CHROME_PATH,
         f"--user-data-dir={CHROME_PROFILE}",
         f"--remote-debugging-port={CDP_PORT}",
         "--no-first-run", "--disable-session-crashed-bubble",
         "--disable-restore-session-state"] + _STEALTH_ARGS,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for _ in range(30):
        if is_running():
            return True
        time.sleep(1)
    return False


def connect(timeout=15):
    """Get a (browser, context) pair connected to a healthy Chrome.
    Auto-restarts Chrome if the process crashed or is unresponsive."""
    pw = _pw()
    for attempt in range(3):
        # Check if Chrome is alive
        running = is_running()
        if not running:
            if not start():
                print("ERROR: could not start Chrome", file=__import__('sys').stderr)
                return None, None
        # Try connecting
        for _ in range(timeout):
            try:
                b = pw.chromium.connect_over_cdp(CDP_URL)
                ctx = b.contexts[0]
                # Verify connection is alive (not a zombie)
                _ = ctx.pages
                return b, ctx
            except Exception as e:
                err = str(e)
                if "Target closed" in err or "Connection" in err or "Not connected" in err:
                    break  # Chrome died — restart
                time.sleep(1)
        # Chrome was running but connection failed — restart
        print(f"Chrome unresponsive (attempt {attempt+1}/3), restarting...", file=__import__('sys').stderr)
        close()
        _PW = None
        time.sleep(2)
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
