"""tailor.py — Generate tailored CV + cover letter PDFs via Gemini Web."""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime

from lib.db import load, save, advance, get_failed
from lib.db import (
    desc_get, desc_exists, desc_save, app_save, app_get, app_list, get_job,
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
        "scripts": saved_scripts,
        "strategy_path": strategy_path,
        "notes": "; ".join(notes),
    }


def cmd_run(count=1):
    state = load()
    described = [
        (jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "described"
    ]
    if not described:
        print("No described jobs. Run fetch.py first.", file=sys.stderr)
        sys.exit(1)

    processed = failed_count = 0
    for jid, entry in described[:count]:
        print(
            f"\nProcessing: {entry.get('title')} @ {entry.get('company')}",
            file=sys.stderr,
        )
        success, result = generate_tailored_docs(entry)
        if success:
            advance(
                entry,
                "tailored",
                response_path=result.get("response_path"),
                scripts=result.get("scripts", []),
            )
            save(state)
            scripts_str = (
                ", ".join(result.get("scripts", []))
                if result.get("scripts")
                else "no scripts"
            )
            print(f"  Complete -> {scripts_str}", file=sys.stderr)
            processed += 1
        else:
            advance(entry, "failed", error=str(result))
            print(f"  Failed: {result}", file=sys.stderr)
            failed_count += 1
        save(state)
    print(f"\nDone. Processed: {processed}, Failed: {failed_count}", file=sys.stderr)


def cmd_status():
    from lib.db import STAGES
    state = load()
    if not state.get("jobs"):
        print("No jobs in state. Run extract first.", file=sys.stderr)
        return
    print(f"Jobs: {len(state['jobs'])} total", file=sys.stderr)
    for s in STAGES:
        c = state['stages'].get(s, 0)
        if c:
            print(f"  {s}: {c}")


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
        save(state)
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
        save(state)
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
    save(state)
    print(f"SKIP:{count}", file=sys.stderr)


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
        url = entry.get("url", "")
        if url:
            webbrowser.open(url)
            print(f"Opening: {url}", file=sys.stderr)

        RESULTS_DIR = os.path.join(os.path.expanduser("~"), ".openclaw", "results")
        tmp_dir = os.path.join(RESULTS_DIR, jid)
        os.makedirs(tmp_dir, exist_ok=True)
        files = app_list(jid)
        for f in files:
            content = app_get(jid, f["filename"])
            if content:
                fpath = os.path.join(tmp_dir, f["filename"])
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(content)

        if os.path.exists(tmp_dir):
            subprocess.run(["explorer", tmp_dir], shell=True)
            print(f"Folder: {tmp_dir}", file=sys.stderr)

        print(
            f"\nReady: {entry.get('title')} @ {entry.get('company')}", file=sys.stderr
        )


def cmd_run_all(no_open=False):
    state = load()
    if not state.get("jobs"):
        print("STATE_EMPTY", file=sys.stderr)
        return

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

    jid, entry = described[0]
    title = entry.get("title", "?")
    company = entry.get("company", "?")
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
            save(state)
            if no_open:
                print(
                    f"  COMPLETE {jid} (--no-open, use 'ready {jid}' later)",
                    file=sys.stderr,
                )
            else:
                print(f"  COMPLETE {jid}", file=sys.stderr)
                cmd_ready(jid)
        else:
            advance(entry, "failed", error=str(result)[:200])
            save(state)
            err_str = str(result)[:120]
            if "RATE_LIMIT" in err_str:
                reset_time = err_str.split(":", 1)[1] if ":" in err_str else "later"
                print(f"  RATE_LIMIT {jid} — resets {reset_time}", file=sys.stderr)
            else:
                print(f"  FAILED {jid} {err_str}", file=sys.stderr)
    except Exception as e:
        advance(entry, "failed", error=str(e)[:200])
        save(state)
        print(f"  ERROR {jid} {str(e)[:120]}", file=sys.stderr)


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

        # Create .url shortcut to the job posting
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
    save(state)
    print(f"DONE:{count}", file=sys.stderr)


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
    save(state)
    print(
        f"Redo: {entry.get('title')} @ {entry.get('company')} ({old_stage} -> described)",
        file=sys.stderr,
    )


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
        print(
            "Usage: python3 tailor.py reset <jid> [--hard] | --all [--hard]",
            file=sys.stderr,
        )
        return

    to_stage = "extracted" if hard else "described"
    for jid, entry in targets:
        old = entry.get("stage", "?")
        advance(entry, to_stage, error=None, response_path=None, scripts=[])
        print(f"  {jid}: {old} -> {to_stage}", file=sys.stderr)

    save(state)
    mode = "hard" if hard else "soft"
    print(f"Reset {len(targets)} jobs ({mode}).", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tailor.py <command> [args]", file=sys.stderr)
        print("Commands:", file=sys.stderr)
        print(
            "  run [--count N]        Process N described jobs (default: 1)",
            file=sys.stderr,
        )
        print(
            "  run-all                Process next described job, hand off",
            file=sys.stderr,
        )
        print("  status                 Show pipeline state", file=sys.stderr)
        print("  resume <job_id>        Show application files", file=sys.stderr)
        print(
            "  ready [job_id]         Open URL + folder for tailored job",
            file=sys.stderr,
        )
        print("  done <job_id>          Mark job as applied", file=sys.stderr)
        print("  redo <job_id>          Re-tailor a job (described)", file=sys.stderr)
        print(
            "  reset <jid> [--hard]   Re-tailor (soft) or re-fetch+re-tailor (hard)",
            file=sys.stderr,
        )
        print("  reset --all [--hard]   Mass reset all jobs", file=sys.stderr)
        print("  retry                  Retry all failed", file=sys.stderr)
        print("  skip <job_id>          Skip a job", file=sys.stderr)
        print("  list-gems              List gems", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "run":
        count = 1
        if "--count" in sys.argv:
            idx = sys.argv.index("--count")
            if idx + 1 < len(sys.argv):
                count = int(sys.argv[idx + 1])
        cmd_run(count)
    elif command == "status":
        cmd_status()
    elif command == "resume":
        job_id = sys.argv[2] if len(sys.argv) > 2 else None
        if not job_id:
            print("Usage: python3 tailor.py resume <job_id>", file=sys.stderr)
        else:
            cmd_resume(job_id)
    elif command == "retry":
        cmd_retry()
    elif command == "skip":
        cmd_skip(*sys.argv[2:])
    elif command == "ready":
        job_id = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_ready(job_id)
    elif command == "run-all":
        no_open = "--no-open" in sys.argv
        cmd_run_all(no_open=no_open)
    elif command == "done":
        cmd_done(*sys.argv[2:])
    elif command == "redo":
        job_id = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_redo(job_id)
    elif command == "reset":
        hard = "--hard" in sys.argv
        job_id = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "--hard" else None
        cmd_reset(job_id=job_id, hard=hard)
    elif command == "list-gems":
        list_gems()
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
