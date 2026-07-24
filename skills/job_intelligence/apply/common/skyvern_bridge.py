"""skyvern_bridge.py — Sync Skyvern client via Python SDK.

Auto-starts a local Skyvern server (SQLite, no Docker) with LLM config
pointing to the local proxy on port 9000. The pipeline never launches
a browser — Skyvern handles everything in its own Playwright instance.

Usage:
    from apply.common.skyvern_bridge import fill_remaining, click_submit
    result = fill_remaining(url, answers, filled_fields=[...])
    result = click_submit(url, browser_session_id)
"""

import asyncio
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

from lib.config import RESULTS_DIR


def _fmt_answers(answers: dict) -> str:
    lines = []
    for k, v in answers.items():
        k = k.replace("*", "").strip()
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


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
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def _ensure_server():
    """Start the local Skyvern server if not already running, with LLM config.

    The server is deliberately NOT killed at exit: non-blocking fill tasks
    (wait=False) keep running after the CLI process returns. A reused server
    is idempotent — the next run detects it via _server_alive()."""
    global _SERVER_PROC
    if _server_alive():
        return
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
    for _ in range(30):
        if _server_alive():
            return
        time.sleep(1)
    print("WARN: Skyvern server may not have started (port 8000 not responding after 30s)",
          file=sys.stderr)


def _api_key() -> str:
    """Get Skyvern API key from env or .env file."""
    key = os.environ.get("SKYVERN_API_TOKEN", "")
    if key:
        return key
    import re
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for env_path in [
        os.path.join(_root, "..", "..", ".env"),
        os.path.join(_root, "..", "job_intelligence", ".env"),
        os.path.join(_root, "..", ".env"),
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


def _chrome_cdp_url() -> str:
    """Reuse the pipeline's shared Chrome (via chrome_manager). Never kills or
    restarts Chrome — Playwright-filled form state must survive the handoff."""
    try:
        from lib import chrome_manager
        if chrome_manager.start():
            return chrome_manager.CDP_URL
    except Exception as e:
        print(f"WARN: chrome_manager start failed: {e}", file=sys.stderr)
    return ""


def _task_to_dict(task) -> dict:
    """Extract common fields from a Skyvern task object."""
    if task is None:
        return {}
    return {
        "status": getattr(task, "status", "unknown"),
        "details": getattr(task, "failure_reason", "") or str(task)[:300],
        "run_id": getattr(task, "run_id", None),
        "browser_session_id": getattr(task, "browser_session_id", None),
    }


async def _run_task(sk, *, prompt, url, max_steps=15, wait_for_completion=True,
                    timeout=120000, data_extraction_schema=None,
                    browser_session_id="", browser_address=""):
    """Single entry point for all Skyvern task execution.
    Replaces 5 duplicated async kwargs-building blocks."""
    kwargs = dict(
        prompt=prompt, url=url, max_steps=max_steps,
        wait_for_completion=wait_for_completion, timeout=timeout,
        model={"max_tokens": 4096}, proxy_location="NONE",
    )
    if data_extraction_schema:
        kwargs["data_extraction_schema"] = data_extraction_schema
    if browser_session_id:
        kwargs["browser_session_id"] = browser_session_id
    if browser_address:
        kwargs["browser_address"] = browser_address
    return await sk.run_task(**kwargs)


def get_task(run_id: str) -> dict:
    """Get task result by run_id (for state recovery / polling)."""
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


def fill_remaining(url: str, answers: dict, filled_fields: list[str] = None,
                   browser_session_id: str = "", timeout: int = 300,
                   wait: bool = True, max_steps: int = 30) -> dict:
    """Fill only fields that weren't already filled by Playwright.
    If wait=False, returns immediately with the run_id for polling.
    max_steps caps the LLM-call budget (each step = 1+ slow LLM calls)."""
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

    task = _run_async(
        _run_task(sk, prompt=prompt, url=url, max_steps=max_steps,
                  wait_for_completion=wait, timeout=timeout * 1000,
                  browser_session_id=browser_session_id, browser_address=cdp),
        timeout=timeout + 30,
    )
    if task is None:
        return {"status": "timed_out", "details": f"Skyvern fill_remaining did not complete within {timeout}s"}
    d = _task_to_dict(task)
    if not wait:
        d["status"] = "started"
    return d


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
    task = _run_async(
        _run_task(sk, prompt=prompt, url=url, max_steps=15,
                  wait_for_completion=True, timeout=timeout * 1000,
                  browser_session_id=browser_session_id, browser_address=cdp),
        timeout=timeout + 30,
    )
    if task is None:
        return {"status": "timed_out"}
    return _task_to_dict(task)


class SkyvernExtraction:
    """Simple text extraction from the current page using Skyvern.
    Used for verification and investigation — no form filling."""

    def extract_text(self, url: str, prompt: str, timeout: int = 120) -> dict | None:
        sk = _client()
        cdp = _chrome_cdp_url()
        task = _run_async(
            _run_task(sk, prompt=prompt, url=url, max_steps=5,
                      wait_for_completion=True, timeout=timeout * 1000,
                      browser_address=cdp),
            timeout=timeout + 30,
        )
        if task is None:
            return None
        d = _task_to_dict(task)
        d["extracted_text"] = str(getattr(task, "extracted_information", "") or "")
        return d

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
        task = _run_async(
            _run_task(sk, prompt=prompt, url=url, max_steps=10,
                      wait_for_completion=True, timeout=timeout * 1000,
                      data_extraction_schema=schema, browser_address=cdp),
            timeout=timeout + 30,
        )
        if task is None:
            return None
        d = _task_to_dict(task)
        d["fields"] = getattr(task, "extracted_information", {})
        return d
