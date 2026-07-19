"""skyvern_bridge.py — Sync Skyvern client via Python SDK.

Connects to a containerized Skyvern server at http://localhost:8000.
Uses the Skyvern SDK with asyncio.run() — no event loop conflict
because all Playwright has been removed from the pipeline.

Usage:
    from apply.common.skyvern_bridge import fill_form, submit_form, close_session
    result = fill_form(url, answers)  # returns {status, browser_session_id, run_id, ...}
    result = submit_form(url, browser_session_id)
    close_session(browser_session_id)
"""

import asyncio
import glob
import os
import sys
import time

SKYVERN_URL = os.environ.get("SKYVERN_URL", "http://localhost:8000")
_SKYVERN_API_KEY = os.environ.get("SKYVERN_API_TOKEN", "")
RESULTS_DIR = os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji"))

def _api_key() -> str:
    global _SKYVERN_API_KEY
    if _SKYVERN_API_KEY:
        return _SKYVERN_API_KEY
    import subprocess, re
    try:
        r = subprocess.run(
            ["docker", "exec", "skyvern-skyvern-1", "cat", "/app/.skyvern/credentials.toml"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r'cred="([^"]+)"', r.stdout)
        if m:
            _SKYVERN_API_KEY = m.group(1)
    except Exception:
        pass
    return _SKYVERN_API_KEY


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
        rd = os.path.join(RESULTS_DIR, "results", jid)
        resumes = glob.glob(os.path.join(rd, "*Resume*.pdf"))
        covers = glob.glob(os.path.join(rd, "*Cover*.pdf"))
        if resumes:
            parts.append(f"\nUpload resume from /ji-results/{jid}/{os.path.basename(resumes[0])} to the Resume/CV file input.")
        if covers:
            parts.append(f"Upload cover letter from /ji-results/{jid}/{os.path.basename(covers[0])} to the cover letter file input.")
    return "\n".join(parts)


def _run_async(coro, timeout=300):
    """Run async SDK call synchronously. Safe because Pipeline has no event loop."""
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
    except asyncio.TimeoutError:
        return None


def _client():
    """Lazy import + create Skyvern client. Import is slow (~2s)."""
    from skyvern import Skyvern
    return Skyvern(base_url=SKYVERN_URL, api_key=_api_key())


def fill_form(url: str, answers: dict, jid: str = "", timeout: int = 300) -> dict:
    """Fill a job application form. Returns task result with browser_session_id."""
    prompt = _build_prompt(url, answers, jid=jid)
    sk = _client()

    async def run():
        return await sk.run_task(
            prompt=prompt, url=url, max_steps=50,
            wait_for_completion=True, timeout=timeout * 1000,
        )

    task = _run_async(run(), timeout=timeout + 30)
    if task is None:
        return {"status": "timed_out", "details": f"Sskyvern did not complete within {timeout}s"}
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
    return _call_api("GET", f"/v1/runs/{run_id}")


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


def _call_api(method: str, path: str) -> dict:
    """Minimal REST helper for state recovery (no SDK needed for a GET)."""
    import urllib.request, json
    url = f"{SKYVERN_URL}{path}"
    req = urllib.request.Request(url, headers={"x-api-key": _api_key()}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}
