"""skyvern_bridge.py — Sync Skyvern client via HTTP REST API.

Makes HTTP calls to a containerized Skyvern server (http://localhost:8000).
No async, no SDK, no threads — just urllib like ask_api.py.

Replaces the entire fill/verify/read strategy layer.

Usage:
    from apply.common.skyvern_bridge import fill_form, submit_form
    result = fill_form(url, answers)
    result = submit_form(url)
"""

import json
import os
import time
import urllib.request
import urllib.error


SKYVERN_URL = os.environ.get("SKYVERN_URL", "http://localhost:8000")

# Read API key once from the credentials file inside the container.
# Persisted to a local cache so we don't re-read every call.
_API_KEY_CACHE = os.path.join(
    os.environ.get("JI_HOME", os.path.expanduser("~/.ji")),
    ".skyvern_api_key",
)


def _api_key() -> str:
    """Get Skyvern API key from cache or container credentials file."""
    if os.path.exists(_API_KEY_CACHE):
        with open(_API_KEY_CACHE) as f:
            return f.read().strip()
    try:
        import subprocess, re
        result = subprocess.run(
            ["docker", "exec", "skyvern-skyvern-1", "cat", "/app/.skyvern/credentials.toml"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r'cred="([^"]+)"', result.stdout)
        if m:
            key = m.group(1)
            os.makedirs(os.path.dirname(_API_KEY_CACHE), exist_ok=True)
            with open(_API_KEY_CACHE, "w") as f:
                f.write(key)
            return key
    except Exception:
        pass
    return ""


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "x-api-key": _api_key(),
    }


def _fmt_answers(answers: dict) -> str:
    lines = []
    for k, v in answers.items():
        k = k.replace("*", "").strip()
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


def _build_prompt(url: str, answers: dict, submit: bool = False) -> str:
    if submit:
        return (
            "Click the Submit Application or Submit button on this job application form. "
            "If there is a Review step before Submit, click Review first. "
            "Complete the submission process. Do NOT fill any new fields."
        )
    return (
        f"You are filling out a job application form at {url}.\n\n"
        f"Fields to fill (use ONLY these values, do not make up answers):\n"
        f"{_fmt_answers(answers)}\n\n"
        f"Instructions:\n"
        f"1. Fill EVERY field listed above. For dropdown/combobox, click to open and select the matching option.\n"
        f"2. If the exact label isn't found, match by meaning (e.g. 'Country*' = country dropdown).\n"
        f"3. If no matching option exists in a dropdown, type the value directly.\n"
        f"4. For file uploads, find the Resume/CV file input.\n"
        f"5. Check required consent/checkbox fields.\n"
        f"6. If there is a Next/Continue button, click it and fill the next page too.\n"
        f"7. STOP before clicking Submit Application or Submit. Do NOT submit.\n"
        f"8. After filling all visible fields, stop and report what you filled.\n"
    )


def _call_api(method: str, path: str, body: dict = None) -> dict:
    url = f"{SKYVERN_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, headers=_headers(), method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def _start_task(prompt: str, url: str, cdp_url: str, max_steps: int = 50) -> dict:
    body = {
        "prompt": prompt,
        "url": url,
        "max_steps": max_steps,
        "proxy_location": "NONE",
    }
    if cdp_url:
        body["browser_address"] = cdp_url
    return _call_api("POST", "/v1/run/tasks", body)


def _poll_task(run_id: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _call_api("GET", f"/v1/runs/{run_id}")
        if "error" in result:
            return {"status": "error", "details": result["error"]}
        status = result.get("status", "unknown")
        finished = status in ("completed", "failed", "timed_out", "terminated", "canceled")
        if finished:
            details = result.get("failure_reason", "") or ""
            output = result.get("output", None)
            return {
                "status": status,
                "details": details[:500] if details else str(result)[:300],
                "extracted_data": output,
            }
        time.sleep(2)
    return {"status": "timed_out", "details": f"Task did not complete within {timeout}s"}


def fill_form(url: str, answers: dict, cdp_url: str, timeout: int = 300) -> dict:
    """Fill a job application form using Skyvern vision AI."""
    prompt = _build_prompt(url, answers)
    task = _start_task(prompt, url, cdp_url, max_steps=50)
    if "error" in task:
        return {"status": "error", "details": task["error"]}
    run_id = task.get("run_id", "")
    if not run_id:
        return {"status": "error", "details": f"No run_id in response: {task}"}
    return _poll_task(run_id, timeout)


def submit_form(url: str, cdp_url: str, timeout: int = 120) -> dict:
    """Click Submit on a job application form."""
    prompt = _build_prompt(url, {}, submit=True)
    task = _start_task(prompt, url, cdp_url, max_steps=20)
    if "error" in task:
        return {"status": "error", "details": task["error"]}
    run_id = task.get("run_id", "")
    if not run_id:
        return {"status": "error", "details": f"No run_id in response: {task}"}
    return _poll_task(run_id, timeout)


def verify_submission(url: str, cdp_url: str, timeout: int = 60) -> dict:
    """Check if an application was successfully submitted."""
    prompt = (
        f"Check if this job application at {url} was successfully submitted. "
        f"Look for success messages like 'Your application has been submitted', "
        f"'Thank you for your application', 'Application received', etc. "
        f"Answer only: SUBMITTED or NOT_SUBMITTED."
    )
    task = _start_task(prompt, url, cdp_url, max_steps=10)
    if "error" in task:
        return {"status": "error", "details": task["error"]}
    run_id = task.get("run_id", "")
    if not run_id:
        return {"status": "error", "details": f"No run_id in response: {task}"}
    return _poll_task(run_id, timeout)
