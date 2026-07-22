"""skyvern_bridge.py — Sync Skyvern client via Python SDK.

Auto-starts a local Skyvern server (SQLite, no Docker) with LLM config
pointing to the local proxy on port 9000. The pipeline never launches
a browser — Skyvern handles everything in its own Playwright instance.

Usage:
    from apply.common.skyvern_bridge import fill_form, submit_form, close_session
    result = fill_form(url, answers)  # fills form, returns browser_session_id
    result = submit_form(url, browser_session_id)
    close_session(browser_session_id)
"""

import asyncio
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

RESULTS_DIR = os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji"))
RESULTS_DIR = os.path.join(RESULTS_DIR, "results")


def _fmt_answers(answers: dict) -> str:
    lines = []
    for k, v in answers.items():
        k = k.replace("*", "").strip()
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


def _build_prompt(url: str, answers: dict, jid: str = "", submit: bool = False) -> str:
    if submit:
        return (
            "Click the Submit Application or Submit button on this job application form. "
            "If there is a Review step before Submit, click Review first, then Submit. "
            "Complete the submission process. Do NOT fill any new fields."
        )
    parts = [
        f"You are filling out a job application form at {url}.\n",
        "Fields to fill (use ONLY these values, do not make up answers):",
        _fmt_answers(answers),
        "",
        "Instructions:",
        "1. Fill EVERY field listed above. For dropdown/combobox, click to open and select the matching option.",
        "2. If the exact label isn't found, match by meaning (e.g. 'Country*' = country dropdown).",
        "3. If no matching option exists in a dropdown, type the value directly.",
        "4. Check required consent/checkbox fields.",
        "5. If there is a Next/Continue button, click it and fill the next page too.",
        "6. STOP before clicking Submit Application or Submit. Do NOT submit.",
    ]
    if jid:
        rd = os.path.join(RESULTS_DIR, jid)
        resumes = glob.glob(os.path.join(rd, "*Resume*.pdf"))
        covers = glob.glob(os.path.join(rd, "*Cover*.pdf"))
        if resumes:
            parts.append(f"\nUpload resume from {resumes[0]} to the Resume/CV file input.")
        if covers:
            parts.append(f"Upload cover letter from {covers[0]} to the cover letter file input.")
    return "\n".join(parts)


def _run_async(coro, timeout=300):
    """Run async SDK call synchronously. Handles both main-thread and
    already-running-event-loop scenarios (Playwright sync API case)."""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, asyncio.wait_for(coro, timeout=timeout))
                return future.result(timeout=timeout + 30)
    except RuntimeError:
        pass
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
    except asyncio.TimeoutError:
        return None


_SERVER_PROC = None  # type: ignore


def _server_alive() -> bool:
    """True if the Skyvern server is responding on port 8000."""
    try:
        req = urllib.request.Request("http://localhost:8000/openapi.json", method="GET")
        urllib.request.urlopen(req, timeout=2)
        return True  # 200 OK
    except urllib.error.HTTPError:
        return True  # any HTTP response means the server is up
    except Exception:
        return False  # connection refused / timeout = not up


def _ensure_server():
    """Start the local Skyvern server if not already running, with LLM config."""
    global _SERVER_PROC
    if _server_alive():
        return
    # Set LLM env vars so litellm routes OpenAI models to our local proxy
    env = os.environ.copy()
    env.setdefault("OPENAI_API_BASE", "http://localhost:9000/v1")
    env.setdefault("OPENAI_API_KEY", "sk-dummy")
    env.setdefault("ENABLE_OPENAI", "true")
    env.setdefault("LLM_CONFIG", '{"model":"gpt-4","api_key":"sk-dummy"}')
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "..", "tmp")
    log_dir = os.path.normpath(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    log = os.path.join(log_dir, "skyvern_server.log")
    _SERVER_PROC = subprocess.Popen(
        [sys.executable, "-m", "skyvern", "run", "server"],
        env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT,
    )
    # Register cleanup on normal interpreter exit
    import atexit
    atexit.register(lambda: _kill_server())
    # Wait for startup
    for _ in range(30):
        if _server_alive():
            return
        time.sleep(1)
    print("WARN: Skyvern server may not have started (port 8000 not responding after 30s)",
          file=sys.stderr)


def _kill_server():
    global _SERVER_PROC
    if _SERVER_PROC is not None and _SERVER_PROC.poll() is None:
        _SERVER_PROC.terminate()
        try:
            _SERVER_PROC.wait(timeout=5)
        except Exception:
            _SERVER_PROC.kill()
        _SERVER_PROC = None


def _api_key() -> str:
    """Get Skyvern API key from env or .env file."""
    key = os.environ.get("SKYVERN_API_TOKEN", "")
    if key:
        return key
    # Walk up from __file__ to find .env files
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../apply
    import re
    for env_path in [
        os.path.join(_root, "..", "..", ".env"),           # skill root (parent of job_intelligence)
        os.path.join(_root, "..", "job_intelligence", ".env"),  # nested package
        os.path.join(_root, "..", ".env"),                  # skill root from apply dir
    ]:
        env_path = os.path.normpath(env_path)
        if not os.path.exists(env_path):
            continue
        with open(env_path) as f:
            for line in f:
                if "SKYVERN_API_KEY" in line:
                    m = re.search(r"SKYVERN_API_KEY='([^']+)'", line)
                    if m:
                        return m.group(1)
    return ""


def _client():
    """Lazy import + create Skyvern client. Auto-starts server if needed."""
    _ensure_server()
    from skyvern import Skyvern
    return Skyvern(base_url="http://localhost:8000", api_key=_api_key())


_CHROME_PROC = None
_CHROME_PROFILE = os.path.join(
    os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji")),
    "chrome-profile",
)


def _find_chrome() -> str:
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return "chrome"


def _start_chrome() -> str:
    """Start Chrome with CDP using the configured profile. Returns CDP URL."""
    global _CHROME_PROC
    # Kill any existing process on port 9222 so we start fresh with the right profile
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9222/json/version"), timeout=2)
        import subprocess as _sp
        import socket as _sk
        r = _sp.run(["netstat", "-ano", "|", "findstr", ":9222"], capture_output=True, text=True, shell=True, timeout=5)
        for line in r.stdout.splitlines():
            if "LISTENING" in line:
                pid = line.strip().rsplit(" ", 1)[-1]
                if pid.isdigit():
                    _sp.run(["taskkill", "/f", "/pid", pid], capture_output=True, timeout=5)
        time.sleep(2)
    except Exception:
        pass
    chrome_path = _find_chrome()
    os.makedirs(_CHROME_PROFILE, exist_ok=True)
    stealth = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars", "--no-default-browser-check",
        "--disable-component-update", "--disable-background-timer-throttling",
    ]
    _CHROME_PROC = subprocess.Popen(
        [chrome_path,
         f"--user-data-dir={_CHROME_PROFILE}",
         "--remote-debugging-port=9222",
         "--no-first-run", "--disable-session-crashed-bubble",
         "--disable-restore-session-state"] + stealth,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    import atexit
    atexit.register(_kill_chrome)
    for _ in range(30):
        try:
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9222/json/version"), timeout=2)
            return "http://127.0.0.1:9222"
        except Exception:
            time.sleep(1)
    print("WARN: Chrome didn't start on port 9222", file=sys.stderr)
    return ""


def _kill_chrome():
    global _CHROME_PROC
    if _CHROME_PROC is not None and _CHROME_PROC.poll() is None:
        _CHROME_PROC.terminate()
        try:
            _CHROME_PROC.wait(timeout=5)
        except Exception:
            _CHROME_PROC.kill()
        _CHROME_PROC = None


def _chrome_cdp_url() -> str:
    """Auto-start Chrome with CDP, return the CDP address."""
    return _start_chrome()


def fill_form(url: str, answers: dict, jid: str = "", timeout: int = 300) -> dict:
    """Fill a job application form. Returns task result with browser_session_id."""
    prompt = _build_prompt(url, answers, jid=jid)
    sk = _client()
    cdp = _chrome_cdp_url()

    async def run():
        kwargs = dict(prompt=prompt, url=url, max_steps=50,
                      wait_for_completion=True, timeout=timeout * 1000,
                      model={"max_tokens": 4096},
                      proxy_location="NONE")
        if cdp:
            kwargs["browser_address"] = cdp
        return await sk.run_task(**kwargs)

    task = _run_async(run(), timeout=timeout + 30)
    if task is None:
        return {"status": "timed_out", "details": f"Skyvern did not complete within {timeout}s"}
    return {
        "status": getattr(task, "status", "unknown"),
        "details": getattr(task, "failure_reason", "") or str(task)[:300],
        "browser_session_id": getattr(task, "browser_session_id", None),
        "run_id": getattr(task, "run_id", None),
        "screenshot_urls": getattr(task, "screenshot_urls", []),
        "errors": getattr(task, "errors", []),
    }


def submit_form(url: str, browser_session_id: str = "", timeout: int = 120) -> dict:
    """Click Submit on a job application form. Reuses browser_session_id."""
    prompt = _build_prompt(url, {}, submit=True)
    sk = _client()
    cdp = _chrome_cdp_url()

    async def run():
        kwargs = dict(prompt=prompt, url=url, max_steps=20,
                      wait_for_completion=True, timeout=timeout * 1000,
                      model={"max_tokens": 4096},
                      proxy_location="NONE")
        if browser_session_id:
            kwargs["browser_session_id"] = browser_session_id
        if cdp:
            kwargs["browser_address"] = cdp
        return await sk.run_task(**kwargs)

    task = _run_async(run(), timeout=timeout + 30)
    if task is None:
        return {"status": "timed_out", "details": f"Submit did not complete within {timeout}s"}
    return {
        "status": getattr(task, "status", "unknown"),
        "details": getattr(task, "failure_reason", "") or str(task)[:300],
        "run_id": getattr(task, "run_id", None),
    }


def get_task(run_id: str) -> dict:
    """Get task result by run_id (for state recovery)."""
    sk = _client()
    async def run():
        return await sk.get_run(run_id)
    task = _run_async(run(), timeout=15)
    if task is None:
        return {}
    return {
        "status": getattr(task, "status", "unknown"),
        "browser_session_id": getattr(task, "browser_session_id", None),
    }


def close_session(browser_session_id: str) -> bool:
    """Close a Skyvern browser session, releasing resources."""
    if not browser_session_id:
        return False
    sk = _client()
    try:
        async def run():
            return await sk.close_browser_session(browser_session_id)
        _run_async(run(), timeout=10)
        return True
    except Exception:
        return False


def fill_remaining(url: str, answers: dict, filled_fields: list[str] = None,
                   browser_session_id: str = "", timeout: int = 300,
                   wait: bool = True) -> dict:
    """Fill only fields that weren't already filled by Playwright.
    If wait=False, returns immediately with the run_id for polling."""
    skip = filled_fields or []
    skip_hint = ""
    if skip:
        skip_hint = f"\n\nThe following fields are ALREADY filled — do NOT modify them: {', '.join(skip)}"

    prompt = (
        f"You are filling a job application form at {url}."
        f"{skip_hint}"
        f"\n\nValues to use for remaining fields:"
        f"{_fmt_answers(answers)}"
        f"\n\nInstructions:"
        f"\n1. Fill ONLY fields that are currently empty or not listed as already-filled."
        f"\n2. For dropdowns/comboboxes, click to open and select the matching option."
        f"\n3. If no matching option exists, type the value directly."
        f"\n4. Check required consent/checkbox fields."
        f"\n5. If there is a Next/Continue button, click it and fill the next page too."
        f"\n6. STOP before clicking Submit Application or Submit."
    )

    sk = _client()
    cdp = _chrome_cdp_url()

    async def run():
        kwargs = dict(prompt=prompt, url=url, max_steps=30,
                      wait_for_completion=wait,
                      model={"max_tokens": 4096},
                      proxy_location="NONE")
        if browser_session_id:
            kwargs["browser_session_id"] = browser_session_id
        if cdp:
            kwargs["browser_address"] = cdp
        return await sk.run_task(**kwargs)

    if wait:
        task = _run_async(run(), timeout=timeout + 30)
        if task is None:
            return {"status": "timed_out", "details": f"Skyvern fill_remaining did not complete within {timeout}s"}
        return {
            "status": getattr(task, "status", "unknown"),
            "details": getattr(task, "failure_reason", "") or str(task)[:300],
            "browser_session_id": getattr(task, "browser_session_id", None),
            "run_id": getattr(task, "run_id", None),
        }
    else:
        # Fire-and-forget: return run_id immediately for polling
        task = _run_async(run(), timeout=timeout + 30)
        run_id = getattr(task, "run_id", None) if task else None
        return {
            "status": "started",
            "run_id": run_id,
            "browser_session_id": getattr(task, "browser_session_id", None) if task else None,
        }


def verify_fields(url: str, answers: dict, browser_session_id: str = "",
                  timeout: int = 120) -> dict:
    """Use Skyvern's data extraction to read back field values and compare
    against expected answers. Returns field-level match results."""
    schema = {
        "type": "object",
        "properties": {k: {"type": "string"} for k in answers},
    }
    prompt = (
        f"Read every visible form field on this page and return its current value."
        f"\n\nExpected fields (return empty string if a field is not visible or empty):"
        f"\n{_fmt_answers(answers)}"
    )

    sk = _client()
    cdp = _chrome_cdp_url()

    async def run():
        kwargs = dict(prompt=prompt, url=url, max_steps=10,
                      wait_for_completion=True, timeout=timeout * 1000,
                      data_extraction_schema=schema,
                      model={"max_tokens": 4096},
                      proxy_location="NONE")
        if browser_session_id:
            kwargs["browser_session_id"] = browser_session_id
        if cdp:
            kwargs["browser_address"] = cdp
        return await sk.run_task(**kwargs)

    task = _run_async(run(), timeout=timeout + 30)
    if task is None:
        return {"status": "timed_out"}
    extracted = getattr(task, "extracted_information", {}) or {}
    if isinstance(extracted, dict):
        mismatches = {}
        for field, expected in answers.items():
            actual = extracted.get(field, "")
            if str(actual).strip().lower() != str(expected).strip().lower():
                mismatches[field] = {"expected": expected, "actual": actual}
        return {
            "status": getattr(task, "status", "unknown"),
            "extracted": extracted,
            "mismatches": mismatches,
            "all_match": len(mismatches) == 0,
            "run_id": getattr(task, "run_id", None),
        }
    return {
        "status": getattr(task, "status", "unknown"),
        "extracted": extracted,
        "all_match": False,
        "run_id": getattr(task, "run_id", None),
    }


def click_submit(url: str, browser_session_id: str = "", timeout: int = 120) -> dict:
    """Use Skyvern to click the submit button on a form.
    Reuses an existing browser session if provided."""
    prompt = (
        f"Click the Submit Application or Submit button on this job application form. "
        f"If there is a Review step before Submit, click Review first, then Submit. "
        f"Complete the submission process. Do NOT fill any new fields."
    )
    return _run_submit_action(url, prompt, browser_session_id, timeout)


def click_next(url: str, browser_session_id: str = "", timeout: int = 120) -> dict:
    """Use Skyvern to click Next/Continue on a multi-page form."""
    prompt = (
        f"Click the Next or Continue button on this job application form "
        f"to proceed to the next page. Do NOT fill any fields."
    )
    return _run_submit_action(url, prompt, browser_session_id, timeout)


def _run_submit_action(url: str, prompt: str, browser_session_id: str = "",
                        timeout: int = 120) -> dict:
    sk = _client()
    cdp = _chrome_cdp_url()
    async def run():
        kwargs = dict(prompt=prompt, url=url, max_steps=15,
                      wait_for_completion=True, timeout=timeout * 1000,
                      model={"max_tokens": 4096},
                      proxy_location="NONE")
        if browser_session_id:
            kwargs["browser_session_id"] = browser_session_id
        if cdp:
            kwargs["browser_address"] = cdp
        return await sk.run_task(**kwargs)
    task = _run_async(run(), timeout=timeout + 30)
    if task is None:
        return {"status": "timed_out"}
    return {
        "status": getattr(task, "status", "unknown"),
        "details": getattr(task, "failure_reason", "") or str(task)[:300],
        "run_id": getattr(task, "run_id", None),
    }


class SkyvernExtraction:
    """Simple text extraction from the current page using Skyvern.
    Used for verification and investigation — no form filling."""

    def extract_text(self, url: str, prompt: str, timeout: int = 120) -> dict | None:
        sk = _client()
        cdp = _chrome_cdp_url()
        async def run():
            kwargs = dict(prompt=prompt, url=url, max_steps=5,
                          wait_for_completion=True, timeout=timeout * 1000,
                          model={"max_tokens": 4096},
                          proxy_location="NONE")
            if cdp:
                kwargs["browser_address"] = cdp
            return await sk.run_task(**kwargs)
        task = _run_async(run(), timeout=timeout + 30)
        if task is None:
            return None
        return {
            "status": getattr(task, "status", "unknown"),
            "extracted_text": str(getattr(task, "extracted_information", "") or ""),
            "details": getattr(task, "failure_reason", "") or str(task)[:300],
        }

    def investigate_form(self, url: str, timeout: int = 180) -> dict | None:
        """Analyze a job application form and return structured field info.
        Used for the 'investigator mode' — understanding unknown platforms."""
        prompt = (
            f"Analyze this job application form. List every visible form field "
            f"with its label, type (text/select/combobox/checkbox/file/datepicker), "
            f"whether it's required, and any dropdown options. "
            f"Also identify: is this a multi-page form? What buttons exist (Next, Submit, etc.)?"
        )
        schema = {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "type": {"type": "string"},
                            "required": {"type": "boolean"},
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "multi_page": {"type": "boolean"},
                "buttons": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        sk = _client()
        cdp = _chrome_cdp_url()
        async def run():
            kwargs = dict(
                prompt=prompt, url=url, max_steps=10,
                wait_for_completion=True, timeout=timeout * 1000,
                data_extraction_schema=schema,
                model={"max_tokens": 4096},
                proxy_location="NONE",
            )
            if cdp:
                kwargs["browser_address"] = cdp
            return await sk.run_task(**kwargs)
        task = _run_async(run(), timeout=timeout + 30)
        if task is None:
            return None
        return {
            "status": getattr(task, "status", "unknown"),
            "fields": getattr(task, "extracted_information", {}),
            "run_id": getattr(task, "run_id", None),
        }
