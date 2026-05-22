"""tailor.py — Tailor CVs via Gemini Web.

Usage:
  tailor.py [--count N] [--no-open]   Craft N described jobs (default: 1 with handoff)
  tailor.py done <jid> [jid...]       Mark job(s) as applied
  tailor.py skip <jid> [jid...]       Skip job(s)
  tailor.py redo <jid>                Re-tailor a job
  tailor.py retry                     Retry all failed
  tailor.py reset <jid> [--hard]      Reset a job to described or extracted
  tailor.py reset --all [--hard]      Mass reset
  tailor.py ready [<jid>]             Open URL + files for a tailored job
  tailor.py resume <jid>              Show application files
  tailor.py status                    Pipeline status
  tailor.py list-gems                 List Gemini gems
"""

import hashlib
import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime

from lib.db import load, advance, get_failed, pipeline_status
from lib.db import (
    desc_get, app_save, app_get, app_list,
)
from lib.call_gemini import (
    call_gemini_node,
    list_gems,
    GEM_ID,
)
from lib.extract_pdf import extract_and_run

JOB_PROMPT_TEMPLATE = """Job Title: {title}
Company: {company}
Location: {location}

Job Description:
{job_description}"""


def generate_tailored_docs(job_entry):
    job = job_entry
    url = job.get("url", "")
    job_id = hashlib.md5(url.encode()).hexdigest()[:16]
    description = desc_get(job_id)

    if not description:
        return False, "No job description found — run fetch.py first"

    title_clean = job.get("title", "Unknown").split("·")[0].split("\u00b7")[0].strip()
    desc_clean = description[:5000]
    for bad, good in [
        ("\u200b", ""),
        ("\xa0", " "),
        ("\u2013", "-"),
        ("\u2014", "--"),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2026", "..."),
        ("\u2022", "-"),
        ("\u25e6", "-"),
        ("\u00b7", "-"),
    ]:
        desc_clean = desc_clean.replace(bad, good)

    prompt = JOB_PROMPT_TEMPLATE.format(
        title=title_clean,
        company=job.get("company", "Unknown"),
        location=job.get("location", "Unknown"),
        job_description=desc_clean,
    )

    RESULTS_DIR = os.path.join(os.path.expanduser("~"), ".openclaw", "results")
    app_dir = os.path.join(RESULTS_DIR, job_id)
    os.makedirs(app_dir, exist_ok=True)
    success, output = call_gemini_node(
        [prompt, "--app-dir", app_dir], timeout_seconds=600
    )

    if not success:
        response_path = os.path.join(app_dir, "gemini_response.txt")
        if os.path.exists(response_path):
            with open(response_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) > 50:
                success, output = True, content
    if not success:
        return False, output

    app_save(job_id, "gemini_response.txt", output)

    strategy_path = None
    strategy_match = re.search(
        r"(?:1\.\s*)?(Strategy.*?)(?=\n\s*(?:2\s*[&.]|3\.|Optimized|$))",
        output,
        re.DOTALL,
    )
    if strategy_match:
        strategy_text = strategy_match.group(1).strip()
        app_save(job_id, "strategy.md", strategy_text)
        strategy_path = f"db://{job_id}/strategy.md"

    saved_scripts, notes = extract_and_run(output, app_dir)
    notes.append(f"Full response: db://{job_id}/gemini_response.txt")

    return True, {
        "response_path": f"db://{job_id}/gemini_response.txt",
        "text": output[:2000],
        "scripts": saved_scripts,
        "strategy_path": strategy_path,
        "notes": "; ".join(notes),
    }


def cmd_craft(count=1, no_open=False):
    state = load()
    described = [
        (jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "described"
    ]
    if not described:
        failed_count = state["stages"].get("failed", 0)
        if failed_count:
            print(f"NO_PENDING ({failed_count} failed, use 'retry')", file=sys.stderr)
        else:
            print(f"ALL_DONE", file=sys.stderr)
        return

    processed = failed_count = 0
    for jid, entry in described[:count]:
        title = entry.get("title", "?")
        company = entry.get("company", "?")
        if count > 1:
            print(f"\nProcessing: {title} @ {company}", file=sys.stderr)
        else:
            print(f"\nJOB {jid} {title} @ {company}", file=sys.stderr)

        try:
            success, result = generate_tailored_docs(entry)
            if success:
                advance(
                    entry,
                    "tailored",
                    response_path=result.get("response_path"),
                    scripts=result.get("scripts", []),
                )
                if count == 1:
                    print(f"  COMPLETE {jid}", file=sys.stderr)
                    text = result.get("text", "")
                    if text:
                        print(f"\n---RESPONSE---\n{text}\n---", file=sys.stderr)
                    if not no_open:
                        _cmd_ready(jid)
                else:
                    scripts_str = ", ".join(result.get("scripts", [])) if result.get("scripts") else "no scripts"
                    print(f"  Complete -> {scripts_str}", file=sys.stderr)
                processed += 1
            else:
                advance(entry, "failed", error=str(result)[:200])
                err_str = str(result)[:120]
                if "RATE_LIMIT" in err_str:
                    reset_time = err_str.split(":", 1)[1] if ":" in err_str else "later"
                    print(f"  RATE_LIMIT {jid} — resets {reset_time}", file=sys.stderr)
                else:
                    print(f"  FAILED {jid} {err_str}", file=sys.stderr)
                failed_count += 1
        except Exception as e:
            advance(entry, "failed", error=str(e)[:200])
            print(f"  ERROR {jid} {str(e)[:120]}", file=sys.stderr)
            failed_count += 1

    if count > 1:
        print(f"\nDone. Crafted: {processed}, Failed: {failed_count}", file=sys.stderr)


def cmd_status():
    s = pipeline_status()
    if not s["jobs"]:
        print("No jobs in state. Run extract first.", file=sys.stderr)
        return
    print(f"Jobs: {s['jobs']} total", file=sys.stderr)
    for stage in ["extracted", "described", "tailored", "applied", "skipped", "failed"]:
        c = s["stages"].get(stage, 0)
        if c:
            print(f"  {stage}: {c}", file=sys.stderr)
    if s["staged"]["pending"]:
        print(f"  staged (pending extraction): {s['staged']['pending']}", file=sys.stderr)
    if s["auth_walls"]["count"]:
        domains = " ".join(s["auth_walls"]["domains"])
        print(f"  auth walls: {s['auth_walls']['count']} ({domains})", file=sys.stderr)
    print(f"  next: {s['next_step']}", file=sys.stderr)


def _cmd_ready(job_id):
    state = load()
    if not state.get("jobs"):
        return
    entry = state["jobs"].get(job_id)
    if not entry:
        return
    url = entry.get("url", "")
    if url:
        webbrowser.open(url)
        print(f"Opening: {url}", file=sys.stderr)
    RESULTS_DIR = os.path.join(os.path.expanduser("~"), ".openclaw", "results")
    tmp_dir = os.path.join(RESULTS_DIR, job_id)
    os.makedirs(tmp_dir, exist_ok=True)
    files = app_list(job_id)
    for f in files:
        content = app_get(job_id, f["filename"])
        if content:
            fpath = os.path.join(tmp_dir, f["filename"])
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(content)
    if os.path.exists(tmp_dir):
        subprocess.run(["explorer", tmp_dir], shell=True)
        print(f"Folder: {tmp_dir}", file=sys.stderr)
    print(f"\nReady: {entry.get('title')} @ {entry.get('company')}", file=sys.stderr)


def cmd_ready(job_id=None):
    state = load()
    if not state.get("jobs"):
        return
    targets = []
    for jid, entry in state["jobs"].items():
        if job_id and jid == job_id:
            targets = [(jid, entry)]
            break
        elif not job_id and entry.get("stage") == "tailored":
            targets.append((jid, entry))
    if not targets:
        print("Job not found" if job_id else "No tailored jobs", file=sys.stderr)
        return
    for jid, entry in targets:
        _cmd_ready(jid)


def cmd_resume(job_id):
    files = app_list(job_id)
    if files:
        for f in files:
            print(f"  {job_id}/{f['filename']} ({f['created_at']})", file=sys.stderr)
    else:
        print(f"No application files for {job_id}", file=sys.stderr)


def cmd_retry():
    state = load()
    failed = get_failed(state)
    if not failed:
        print("No failed jobs.", file=sys.stderr)
        return
    print(f"Retrying {len(failed)} failed jobs...", file=sys.stderr)
    processed = 0
    for job_id, entry in failed:
        advance(entry, "described")
        success, result = generate_tailored_docs(entry)
        if success:
            advance(
                entry,
                "tailored",
                response_path=result.get("response_path"),
                scripts=result.get("scripts", []),
            )
            processed += 1
            print(f"  {job_id}: retry success", file=sys.stderr)
        else:
            advance(entry, "failed", error=str(result))
            print(f"  {job_id}: retry failed - {result}", file=sys.stderr)
    print(f"\nRetry complete. Succeeded: {processed}/{len(failed)}", file=sys.stderr)


def cmd_skip(*job_ids):
    if not job_ids:
        print("Usage: python3 tailor.py skip <jid1> [jid2 ...]", file=sys.stderr)
        return
    state = load()
    count = 0
    for job_id in job_ids:
        if job_id in state.get("jobs", {}):
            advance(state["jobs"][job_id], "skipped")
            count += 1
        else:
            print(f"Job not found: {job_id}", file=sys.stderr)
    print(f"SKIP:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_done(*job_ids):
    if not job_ids:
        print("Usage: python3 tailor.py done <jid1> [jid2 ...]", file=sys.stderr)
        return
    state = load()
    count = 0
    RESULTS_DIR = os.path.join(os.path.expanduser("~"), ".openclaw", "results")
    for job_id in job_ids:
        if job_id not in state.get("jobs", {}):
            print(f"Job not found: {job_id}", file=sys.stderr)
            continue
        advance(state["jobs"][job_id], "applied", applied_at=datetime.now().isoformat())

        job_url = state["jobs"][job_id].get("url", "")
        if job_url:
            url_path = os.path.join(RESULTS_DIR, job_id, f"{job_id}.url")
            try:
                os.makedirs(os.path.dirname(url_path), exist_ok=True)
                with open(url_path, "w") as f:
                    f.write(f"[InternetShortcut]\nURL={job_url}\n")
            except Exception:
                pass
        count += 1
    print(f"DONE:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_redo(job_id):
    if not job_id:
        print("Usage: python3 tailor.py redo <job_id>", file=sys.stderr)
        return
    state = load()
    if job_id not in state.get("jobs", {}):
        print(f"Job not found: {job_id}", file=sys.stderr)
        return
    entry = state["jobs"][job_id]
    old_stage = entry.get("stage")
    if old_stage not in ("tailored", "applied", "failed", "skipped"):
        print(f"Job is {old_stage} - cannot redo", file=sys.stderr)
        return
    advance(entry, "described", error=None)
    print(
        f"Redo: {entry.get('title')} @ {entry.get('company')} ({old_stage} -> described)",
        file=sys.stderr,
    )
    print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_reset(job_id=None, hard=False):
    state = load()
    if not state.get("jobs"):
        print("No jobs.", file=sys.stderr)
        return
    if job_id == "--all":
        targets = list(state["jobs"].items())
    elif job_id:
        if job_id not in state["jobs"]:
            print(f"Job not found: {job_id}", file=sys.stderr)
            return
        targets = [(job_id, state["jobs"][job_id])]
    else:
        print("Usage: python3 tailor.py reset <jid> [--hard] | --all [--hard]", file=sys.stderr)
        return
    to_stage = "extracted" if hard else "described"
    for jid, entry in targets:
        old = entry.get("stage", "?")
        advance(entry, to_stage, error=None, response_path=None, scripts=[])
        print(f"  {jid}: {old} -> {to_stage}", file=sys.stderr)
    mode = "hard" if hard else "soft"
    print(f"Reset {len(targets)} jobs ({mode}).", file=sys.stderr)


def _parse_count():
    if "--count" in sys.argv:
        i = sys.argv.index("--count")
        if i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
    return None


def main():
    subcommands = {"done", "skip", "redo", "retry", "reset", "status", "resume", "ready", "list-gems"}
    if len(sys.argv) > 1 and sys.argv[1] in subcommands:
        cmd = sys.argv[1]
        if cmd == "done":
            cmd_done(*sys.argv[2:])
        elif cmd == "skip":
            cmd_skip(*sys.argv[2:])
        elif cmd == "redo":
            cmd_redo(sys.argv[2] if len(sys.argv) > 2 else None)
        elif cmd == "retry":
            cmd_retry()
        elif cmd == "reset":
            hard = "--hard" in sys.argv
            job_id = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "--hard" else None
            cmd_reset(job_id=job_id, hard=hard)
        elif cmd == "status":
            cmd_status()
        elif cmd == "resume":
            cmd_resume(sys.argv[2] if len(sys.argv) > 2 else None)
        elif cmd == "ready":
            cmd_ready(sys.argv[2] if len(sys.argv) > 2 else None)
        elif cmd == "list-gems":
            list_gems()
    else:
        cmd_craft(
            count=_parse_count() or 1,
            no_open="--no-open" in sys.argv,
        )


if __name__ == "__main__":
    main()
