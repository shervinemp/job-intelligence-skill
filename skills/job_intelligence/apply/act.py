#!/usr/bin/env python3
"""act.py — Apply pipeline: fill and submit via Skyvern.

No Playwright. No DOM strategies. No per-ATS handlers.
Skyvern handles everything inside a Docker container via vision.

Usage:
  act --fill <jid> [--answers JSON]
  act --submit <jid>
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.db import get_conn
from apply.common.output import emit_next, emit_status
from apply.common.page_helpers import load_state, save_state

_PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "profile.json")


def _load_profile() -> tuple[dict, dict]:
    if not os.path.exists(_PROFILE_PATH):
        return {}, {}
    try:
        with open(_PROFILE_PATH) as f:
            p = json.load(f)
        return p, p.get("common_answers", {})
    except (json.JSONDecodeError, OSError):
        return {}, {}


def _merge_answers(answers: dict, profile: dict, ca: dict) -> dict:
    """Merge --answers > profile.answers > common_answers.
    Uses resolve.resolution_for_fill for profile key → field label mapping."""
    merged = {}
    # Start with common_answers
    for k, v in ca.items():
        merged[k] = v
    # Profile answers override
    for k, v in profile.get("answers", {}).items():
        merged[k] = v
    # --answers are highest priority
    merged.update(answers)
    return merged


def cmd_fill(jid, answers_json=None):
    answers = {}
    if answers_json:
        if answers_json.startswith("@"):
            try:
                with open(answers_json[1:]) as f:
                    answers = json.load(f)
            except Exception as e:
                print(f"ERROR: reading answers file: {e}", file=sys.stderr)
        else:
            try:
                answers = json.loads(answers_json)
            except Exception:
                print("ERROR: --answers must be valid JSON or @file.json", file=sys.stderr)
                return

    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for {state.get('jid','?')}, not {jid}", file=sys.stderr)
        return
    url = state.get("external_url", "")
    if not url:
        print("ERROR: no external_url — run navigate first", file=sys.stderr)
        return

    # Check DB for already applied
    r = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if r and r["stage"] == "applied":
        print(f"ALREADY: {jid} is already applied", file=sys.stderr)
        return

    # Merge answers
    profile, ca = _load_profile()
    merged = _merge_answers(answers, profile, ca)

    print(f"SKYVERN: filling {jid}", file=sys.stderr)
    print(f"  url: {url}", file=sys.stderr)
    print(f"  fields: {len(merged)}", file=sys.stderr)

    from apply.common.skyvern_bridge import fill_form
    result = fill_form(url, merged, jid=jid, timeout=300)

    status = result.get("status", "error")
    details = result.get("details", "")
    session_id = result.get("browser_session_id", "")
    run_id = result.get("run_id", "")

    if status == "completed":
        print(f"STATUS: filled", file=sys.stderr)
        state["browser_session_id"] = session_id
        state["fill_run_id"] = run_id
        state["external_url"] = url
        state["jid"] = jid
        save_state(state)
        emit_next("act --submit")
    else:
        print(f"STATUS: {status}", file=sys.stderr)
        if details:
            print(f"  {details[:300]}", file=sys.stderr)
        emit_next("retry")


def cmd_submit(jid):
    state = load_state()
    if state.get("jid") != jid:
        print(f"ERROR: state is for {state.get('jid','?')}, not {jid}", file=sys.stderr)
        return
    url = state.get("external_url", "")
    session_id = state.get("browser_session_id", "")

    if not session_id:
        fill_run_id = state.get("fill_run_id", "")
        if fill_run_id:
            from apply.common.skyvern_bridge import get_task
            task = get_task(fill_run_id)
            session_id = (task or {}).get("browser_session_id", "")
            if session_id:
                state["browser_session_id"] = session_id
                save_state(state)

    print(f"SKYVERN: submitting {jid}", file=sys.stderr)

    from apply.common.skyvern_bridge import submit_form
    result = submit_form(url, browser_session_id=session_id, timeout=120)

    status = result.get("status", "error")
    details = result.get("details", "")

    if status == "completed":
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        get_conn().execute(
            "UPDATE jobs SET stage='applied', updated_at=?, applied_at=? WHERE id=?",
            (ts, ts, jid),
        ).connection.commit()
        print(f"STATUS: submitted", file=sys.stderr)
        emit_status("submitted")
        emit_next("verify")

        # Clean up browser session
        if session_id:
            try:
                from apply.common.skyvern_bridge import close_session
                close_session(session_id)
            except Exception:
                pass
    else:
        print(f"STATUS: {status}", file=sys.stderr)
        if details:
            print(f"  {details[:300]}", file=sys.stderr)
        emit_next("retry")


def run(args):
    if args.fill:
        cmd_fill(args.jid, args.answers)
    elif args.submit:
        cmd_submit(args.jid)
    else:
        print("ERROR: specify --fill or --submit", file=sys.stderr)
