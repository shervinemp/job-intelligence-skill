"""skyvern_bridge.py — Sync Skyvern client via Python SDK.

Connects to containerized Skyvern server at http://localhost:8000 via SDK.
The server runs in Docker with PostgreSQL and its own Playwright browser.
The pipeline never launches a browser — Skyvern handles everything.

Usage:
    from apply.common.skyvern_bridge import fill_form, submit_form, close_session
    result = fill_form(url, answers)  # fills form, returns browser_session_id
    result = submit_form(url, browser_session_id)
    close_session(browser_session_id)
"""

import asyncio
import glob
import os
import sys
import time

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
    """Run async SDK call synchronously. Safe because pipeline has no event loop."""
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
    except asyncio.TimeoutError:
        return None


def _ensure_server():
    """Start the local Skyvern server if not already running, with LLM config."""
    import subprocess, time, urllib.request, json
    # Check if server is already up
    try:
        req = urllib.request.Request("http://localhost:8000/v1/run/tasks", method="GET")
        urllib.request.urlopen(req, timeout=2)
        return  # already running
    except Exception:
        pass
    # Set LLM env vars so litellm routes OpenAI models to our local proxy
    env = os.environ.copy()
    env.setdefault("OPENAI_API_BASE", "http://localhost:9000/v1")
    env.setdefault("OPENAI_API_KEY", "sk-dummy")
    env.setdefault("LLM_CONFIG", '{"model":"gpt-4","api_key":"sk-dummy"}')
    log = os.path.join(os.path.dirname(__file__), "..", "..", "..", "tmp", "skyvern_server.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "skyvern", "run", "server"],
        env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT,
    )
    # Wait for startup
    for _ in range(30):
        try:
            req = urllib.request.Request("http://localhost:8000/v1/run/tasks", method="GET")
            urllib.request.urlopen(req, timeout=2)
            return
        except Exception:
            time.sleep(1)


def _api_key() -> str:
    """Get Skyvern API key from env or .env file."""
    key = os.environ.get("SKYVERN_API_TOKEN", "")
    if key:
        return key
    import re
    for env_path in [
        r"C:\Users\sherv\.openclaw\workspace\skills\.env",
        r"C:\Users\sherv\.openclaw\workspace\skills\job_intelligence\.env",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env"),
    ]:
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


def fill_form(url: str, answers: dict, jid: str = "", timeout: int = 300) -> dict:
    """Fill a job application form. Returns task result with browser_session_id."""
    prompt = _build_prompt(url, answers, jid=jid)
    sk = _client()

    async def run():
        return await sk.run_task(
            prompt=prompt, url=url, max_steps=50,
            wait_for_completion=True, timeout=timeout * 1000,
            model={"max_tokens": 4096},
        )

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

    async def run():
        kwargs = dict(prompt=prompt, url=url, max_steps=20,
                      wait_for_completion=True, timeout=timeout * 1000)
        if browser_session_id:
            kwargs["browser_session_id"] = browser_session_id
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
