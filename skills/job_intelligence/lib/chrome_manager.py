"""chrome_manager.py — Shared Chrome lifecycle management for the pipeline.

All components (fetch, tailor, apply, gemini.js, skyvern_bridge) use this
module instead of managing their own profile paths and CDP connections.

Import-time side effects are deliberately avoided: call init() explicitly
before first use, or rely on start()/connect() which call it automatically.
"""

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

from .config import JI_HOME, CHROME_PROFILE, CHROME_CONFIG

_LOCK_PATH = Path(JI_HOME) / "pipeline.lock"

def _find_chrome():
    """Find Chrome executable. Checks env, PATH, and common Windows install locations."""
    env_path = os.environ.get("CHROME_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    for name in ("google-chrome", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    win_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in win_candidates:
        if p and os.path.isfile(p):
            return p
    return win_candidates[0]  # fall back to default path (will fail with clear error)


CHROME_PATH = _find_chrome()
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
_initialized = False


def _acquire_lock():
    """Prevent concurrent pipeline processes from corrupting shared state.
    Raises RuntimeError if another pipeline process holds the lock.
    JI_NO_LOCK=1 skips acquisition — the batch supervisor holds the lock
    for its whole run; shadow worker children must not re-acquire it."""
    if os.environ.get("JI_NO_LOCK"):
        return
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _LOCK_PATH.exists():
        try:
            pid = int(_LOCK_PATH.read_text().strip())
            if os.name == "nt":
                import csv, io, subprocess as sp
                r = sp.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                          capture_output=True, timeout=5)
                rows = csv.reader(io.StringIO(r.stdout.decode(errors="replace")))
                alive = any(len(row) > 1 and row[1] == str(pid) for row in rows)
            else:
                os.kill(pid, 0)
                alive = True
            if alive:
                raise RuntimeError(
                    f"pipeline already running (PID {pid}). "
                    f"Lockfile: {_LOCK_PATH}. If stuck, delete the lockfile and retry."
                )
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
    _LOCK_PATH.write_text(str(os.getpid()))


def _release_lock():
    try:
        if _LOCK_PATH.exists() and _LOCK_PATH.read_text().strip() == str(os.getpid()):
            _LOCK_PATH.unlink()
    except Exception:
        pass


def init():
    """Explicit initialization: acquire lock, register cleanup handlers,
    write initial config for Node tools. Safe to call multiple times.
    Raises RuntimeError if another pipeline process is already running."""
    global _initialized
    if _initialized:
        return
    _acquire_lock()
    import atexit
    atexit.register(_release_lock)
    atexit.register(_cleanup)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _cleanup)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _cleanup)
    if not os.path.exists(CHROME_CONFIG):
        try:
            with open(CHROME_CONFIG, "w") as f:
                json.dump({
                    "CHROME_PATH": CHROME_PATH,
                    "CHROME_PROFILE": CHROME_PROFILE,
                    "CDP_PORT": CDP_PORT,
                    "CDP_URL": CDP_URL,
                }, f, indent=2)
        except Exception:
            pass
    _initialized = True


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
    _release_lock()
    if signum is not None:
        sys.exit(1)


def is_running():
    """Lightweight CDP liveness check via socket."""
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


def _read_port():
    try:
        with open(CHROME_CONFIG) as f:
            return json.load(f).get("CDP_PORT", CDP_PORT)
    except Exception:
        return CDP_PORT


def _write_port():
    try:
        with open(CHROME_CONFIG, "w") as f:
            json.dump({
                "CHROME_PATH": CHROME_PATH,
                "CHROME_PROFILE": CHROME_PROFILE,
                "CDP_PORT": CDP_PORT,
                "CDP_URL": CDP_URL,
            }, f, indent=2)
    except Exception:
        pass


def start():
    """Start a DEDICATED pipeline Chrome instance. Reuses a previously-started
    pipeline Chrome (from config) if still alive. Never connects to user's Chrome.
    Calls init() on first use."""
    global CDP_PORT, CDP_URL
    init()
    if os.path.exists(CHROME_CONFIG):
        cfg_port = _read_port()
        if is_running():
            CDP_PORT = cfg_port
            CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
            return True
        if cfg_port != CDP_PORT:
            old_url = f"http://127.0.0.1:{cfg_port}"
            try:
                pw = _pw()
                b = pw.chromium.connect_over_cdp(old_url)
                _ = b.contexts[0].pages
                CDP_PORT = cfg_port
                CDP_URL = old_url
                b.close()
                return True
            except Exception:
                pass
    port = _find_free_port()
    CDP_PORT = port
    CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
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
            _write_port()
            return True
        time.sleep(1)
    return False


def _kill_port_owner(port):
    """Kill the process listening on `port` (the stale pipeline Chrome).
    The port is pipeline-dedicated (never the user's Chrome), so killing
    its owner is safe. Best-effort; no-op when nothing is listening."""
    try:
        if os.name == "nt":
            r = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                               capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                parts = line.split()
                if (len(parts) >= 5 and parts[0] == "TCP"
                        and f"127.0.0.1:{port}" in parts[1]
                        and parts[3] == "LISTENING"):
                    subprocess.run(["taskkill", "/F", "/PID", parts[4]],
                                   capture_output=True, timeout=10)
                    return
        else:
            r = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                               capture_output=True, text=True, timeout=10)
            for pid in r.stdout.split():
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except (OSError, ValueError):
                    continue
    except Exception:
        pass


def connect(timeout=15):
    """Get a (browser, context) pair connected to a healthy dedicated Chrome.
    Calls init() on first use. Returns (None, None) on failure.
    Recovers from a stale/unresponsive Chrome: kills the process bound to
    the CDP port and relaunches a fresh instance."""
    global CDP_PORT, CDP_URL
    init()
    for attempt in range(3):
        pw = _pw()
        port = _read_port()
        CDP_PORT = port
        CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
        running = is_running()
        if not running:
            if not start():
                print("ERROR: could not start Chrome", file=sys.stderr)
                return None, None
        for _ in range(timeout):
            try:
                b = pw.chromium.connect_over_cdp(CDP_URL)
                ctx = b.contexts[0]
                _ = ctx.pages
                return b, ctx
            except Exception as e:
                err = str(e)
                if "Target closed" in err or "Connection" in err or "Not connected" in err:
                    break
                time.sleep(1)
        print(f"Chrome unresponsive (attempt {attempt+1}/3), killing stale instance and relaunching...",
              file=sys.stderr)
        close()
        _kill_port_owner(CDP_PORT)
        time.sleep(2)
    print("ERROR: could not connect to Chrome after 3 attempts", file=sys.stderr)
    return None, None
