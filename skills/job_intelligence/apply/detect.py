#!/usr/bin/env python3
"""detect.py — Pre-flight classify. Reads DB, detects type from URL.
No Playwright. No Chrome. Just DB + URL patterns."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from apply.common.output import emit_next, emit_type, emit_status, emit_error
from apply.common.registry import resolve as resolve_registry

STATE_PATH = os.path.join(
    os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji")),
    "state", "apply_state.json",
)


def _classify(url: str, ext_url: str = "") -> tuple[str, str]:
    """(type, external_url) from a job URL.
    LinkedIn with an external ATS URL → external (Skyvern navigates there).
    LinkedIn without external URL → easy_apply (needs legacy handler)."""
    if not url:
        return "unknown", ""
    ul = url.lower()
    eul = (ext_url or "").lower()
    # LinkedIn job that has an external ATS URL = external redirect
    if "linkedin.com/jobs" in ul and eul:
        return "external", eul
    # LinkedIn with no external URL = Easy Apply modal
    if "linkedin.com/jobs" in ul and not eul:
        return "easy_apply", ""
    # Direct ATS URL
    if any(d in ul for d in ["greenhouse.io", "lever.co", "myworkdayjobs.com",
                              "ashbyhq.com", "icims.com", "jobvite.com"]):
        return "ats_direct", ul
    return "external", ul  # assume external if we have a URL


def run(jid):
    conn = get_conn()
    row = conn.execute(
        "SELECT url, title, company, stage, state, external_url FROM jobs WHERE id=?",
        (jid,),
    ).fetchone()
    if not row:
        emit_error(f"job {jid} not found")
        sys.exit(1)

    url, title, company, stage, job_state = row["url"], row["title"], row["company"], row["stage"], row["state"]
    if job_state != "active":
        emit_error(f"job is in state '{job_state}', not active")
        sys.exit(1)

    if stage == "applied":
        emit_type("already_applied")
        emit_next("none")
        return

    print(f"JOB: {title or '?'} @ {company or '?'}", file=sys.stderr)

    job_type, resolved_url = _classify(url, ext_url)
    reg = resolve_registry(resolved_url or ext_url or url)
    plat_name = reg.name if reg else ""

    emit_line = f"EXTERNAL_URL: {resolved_url}" if resolved_url else ""

    if job_type == "ats_direct":
        emit_type("ats_direct", emit_line)
        if plat_name:
            print(f"PLATFORM: {plat_name}", file=sys.stderr)
        emit_next("act --fill")
    elif job_type == "external":
        emit_type("external", emit_line)
        if plat_name:
            print(f"PLATFORM: {plat_name}", file=sys.stderr)
        emit_next("navigate")
    elif job_type == "easy_apply":
        emit_type("easy_apply")
        print("NOTE: LinkedIn Easy Apply needs CDP browser — run with --cdp or skip", file=sys.stderr)
        emit_next("act --fill")
    else:
        emit_type("unknown")
        emit_next("none")

    state = {"jid": jid, "external_url": resolved_url or "", "url": url,
             "title": title or "", "company": company or ""}
    if plat_name:
        state["platform"] = plat_name
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH + ".tmp", "w") as f:
        json.dump(state, f, indent=2)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)
