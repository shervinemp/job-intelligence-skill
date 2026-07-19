"""skyvern_bridge.py — Sync wrapper around Skyvern async SDK.

Connects Skyvern to our existing Chrome instance (via CDP port) and
provides synchronous helpers for the pipeline. Replaces the entire
fill/verify/read strategy layer (value_reader.py, filler.py, strategies/*.py).

Usage:
    from apply.common.skyvern_bridge import fill_form, submit_form
    fill_form(url, answers)  # fills form, stops before submit
    submit_form(url)         # clicks Submit
"""

import asyncio
import sys
import time
from typing import Optional


def _fmt_answers(answers: dict) -> str:
    lines = []
    for k, v in answers.items():
        k = k.replace("*", "").strip()
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


def _run_async(coro, timeout=300):
    """Run an async coroutine synchronously."""
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def fill_form(
    url: str,
    answers: dict,
    cdp_url: str,
    timeout: int = 300,
) -> dict:
    """Fill a job application form using Skyvern.

    Skyvern navigates to url in a new tab, fills all fields using vision AI,
    handles dropdowns/comboboxes/datepickers, uploads files if needed.
    Stops before clicking the final Submit button.

    Returns:
        dict with status ("completed"|"failed"|"timed_out"|"error") and details.
    """
    prompt = (
        f"You are filling out a job application form at {url}.\n\n"
        f"Fields to fill (use ONLY these values, do not make up answers):\n"
        f"{_fmt_answers(answers)}\n\n"
        f"Instructions:\n"
        f"1. Fill EVERY field listed above. For dropdowns, click to open and select the matching option.\n"
        f"2. If a field label doesn't match exactly, match by meaning (e.g. 'Country*' = country dropdown).\n"
        f"3. If you cannot find the exact matching option, type the value directly.\n"
        f"4. For file uploads (Resume), look for the file input.\n"
        f"5. Check required consent/checkbox fields.\n"
        f"6. If there is a Next/Continue button, click it and fill the next page too.\n"
        f"7. STOP before clicking Submit Application or Submit. Do NOT submit.\n"
        f"8. After filling all visible fields, stop and report what you filled.\n"
    )
    return _run_skyvern_task(prompt, url, cdp_url, timeout)


def submit_form(
    url: str,
    cdp_url: str,
    timeout: int = 120,
) -> dict:
    """Click the Submit button on a job application form."""
    prompt = (
        f"Click the Submit Application or Submit button on this job application form. "
        f"If there is a Review step before Submit, click Review first. "
        f"Complete the submission process. "
        f"Do NOT fill any new fields."
    )
    return _run_skyvern_task(prompt, url, cdp_url, timeout)


def verify_submission(
    url: str,
    cdp_url: str,
    timeout: int = 60,
) -> dict:
    """Check if an application was successfully submitted."""
    prompt = (
        f"Check if this job application was successfully submitted. "
        f"Look for success messages like 'Your application has been submitted', "
        f"'Thank you for your application', 'Application received', etc. "
        f"Answer only: SUBMITTED or NOT_SUBMITTED."
    )
    return _run_skyvern_task(prompt, url, cdp_url, timeout)


def _run_skyvern_task(
    prompt: str, url: str, cdp_url: str, timeout: int
) -> dict:
    try:
        result = _run_async(_skyvern_task(prompt, url, cdp_url, timeout), timeout=timeout + 30)
        return result
    except asyncio.TimeoutError:
        return {"status": "timed_out", "details": f"Skyvern did not complete within {timeout}s"}
    except Exception as e:
        return {"status": "error", "details": str(e)}


async def _skyvern_task(
    prompt: str, url: str, cdp_url: str, timeout: int
) -> dict:
    from skyvern import Skyvern

    skyvern = Skyvern()
    task = await skyvern.run_task(
        prompt=prompt,
        url=url,
        browser_address=cdp_url,
        wait_for_completion=True,
        max_steps=50,
        timeout=timeout * 1000,
    )
    status = getattr(task, "status", "unknown")
    details = ""
    extracted = None
    if hasattr(task, "failure_reason") and task.failure_reason:
        details = task.failure_reason
    if hasattr(task, "extracted_information") and task.extracted_information:
        extracted = task.extracted_information
    if hasattr(task, "outputs") and task.outputs:
        extracted = task.outputs
    if not details:
        details = str(task)[:300]
    return {"status": status, "details": details, "extracted_data": extracted}
