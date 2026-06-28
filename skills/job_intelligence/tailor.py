"""tailor.py — Tailor CVs via Gemini Web.

Usage:
  tailor.py [--auto]                  Craft all described jobs
  tailor.py admit <jid> [jid...]      Mark job as tailored (also: done)
  tailor.py reject <jid> [jid...]      Reject job(s)
  tailor.py retry                     Retry all failed (batch)
  tailor.py retry <jid>               Re-tailor a specific job
  tailor.py retry <jid> --feedback "x" Re-tailor with feedback
  tailor.py undo <jid>                Move job back one stage
  tailor.py reset <jid>               Reset job to extracted (first stage)
  tailor.py reset --all               Mass reset
  tailor.py reset --state <state>     Reset by state (failed, skipped)
"""

import hashlib, json, os, re, subprocess, sys
from datetime import datetime

from lib.db import load, advance, get_failed, pipeline_status
from lib.db import desc_get, app_save, app_get, app_list
from lib.call_gemini import call_gemini_node, list_gems
from lib.config import RESULTS_DIR
# JSON extraction is done inline in the gem route
from lib.platforms import clean as clean_desc


JOB_PROMPT_TEMPLATE = """Job Title: {title}
Company: {company}
Location: {location}

Job Description:

{job_description}"""


def generate_tailored_docs(job_entry, feedback=None, prev_response=None):
    job = job_entry
    url = job.get("url", "")
    job_id = hashlib.md5(url.encode()).hexdigest()[:16]
    description = desc_get(job_id)
    description = clean_desc(url, description)

    if not description:
        return False, "No job description found — run enrich.py first"

    cat = job.get("category")
    if not cat:
        return False, f"No category for job {job_id} — enrich.py admit --category <name> first"
    cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")
    try:
        with open(cat_path) as f:
            cat_info = json.load(f).get(cat)
    except Exception as e:
        return False, f"Can't read categories.json: {e}"
    if not cat_info:
        return False, f"Category '{cat}' not in categories.json"
    gem = cat_info.get("gem")
    title_clean = job.get("title", "Unknown").split("\u00b7")[0].strip()
    desc_clean = description
    for bad, good in [
        ("\u200b", ""), ("\xa0", " "), ("\u2013", "-"), ("\u2014", "--"),
        ("\u2018", "'"), ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
        ("\u2026", "..."), ("\u2022", "-"), ("\u25e6", "-"), ("\u00b7", "-"),
    ]:
        desc_clean = desc_clean.replace(bad, good)
    desc_clean = re.sub(r"https?://\S+", "", desc_clean)
    desc_clean = re.sub(r"\n{2,}", "\n", desc_clean).strip()

    prompt = JOB_PROMPT_TEMPLATE.format(
        title=title_clean, company=job.get("company", "Unknown"),
        location=job.get("location", "Unknown"), job_description=desc_clean,
    )
    notes = job.get("notes", "")
    if notes:
        prompt += f"\n\nContext: {notes}"
    if feedback and prev_response:
        prompt += f"\n\n--- YOUR PREVIOUS OUTPUT (address feedback below) ---\n{prev_response[:3000]}"
        prompt += f"\n\n--- FEEDBACK FROM REVIEW ---\n{feedback}"

    tailor_mode = os.environ.get("JI_TAILOR", "agent")
    if tailor_mode == "agent":
        prompt_path = os.path.join(os.path.dirname(__file__), "tailor_prompt.md")
        try:
            with open(prompt_path) as f:
                agent_instructions = f.read()
        except FileNotFoundError:
            agent_instructions = "Write a Python script that generates a tailored CV PDF for this job."
        prompt = agent_instructions + "\n\n---\n\n" + prompt
        prompt += "\n\nPut the PDF generation script in a single ```python\n...\n``` fenced code block."
        print(f"PROMPT: {os.path.join(RESULTS_DIR, job_id, 'prompt.txt')}", file=sys.stderr)
        print(f"  Write resume.json, then: tailor.py build {job_id} && tailor.py admit {job_id}", file=sys.stderr)
        return True, {"text": prompt, "response_path": None, "scripts": []}

    # Gem route — explicitly tell the gem to output JSON Resume only
    prompt = "Output ONLY a ```json code block containing a valid JSON Resume. No Python code, no explanations.\n\n" + prompt

    app_dir = os.path.join(RESULTS_DIR, job_id)
    os.makedirs(app_dir, exist_ok=True)
    stale = os.path.join(app_dir, "gemini_response.txt")
    if os.path.exists(stale):
        os.remove(stale)
    success, output = call_gemini_node([prompt, "--app-dir", app_dir], timeout_seconds=600, gem=gem)
    if not success:
        response_path = os.path.join(app_dir, "gemini_response.txt")
        if os.path.exists(response_path):
            with open(response_path, encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) > 50:
                success, output = True, content
    if not success:
        return False, output
    app_save(job_id, "gemini_response.txt", output)

    strategy_path = None
    strategy_match = re.search(r"(?:1\.\s*)?(Strategy.*?)(?=\n\s*(?:2\s*[&.]|3\.|Optimized|$))", output, re.DOTALL)
    if strategy_match:
        strategy_text = strategy_match.group(1).strip()
        app_save(job_id, "strategy.md", strategy_text)
        strategy_path = f"db://{job_id}/strategy.md"

    # Extract JSON Resume from response
    json_match = re.search(r"```json\s*(.*?)```", output, re.DOTALL)
    if not json_match:
        json_match = re.search(r"\b[Jj][Ss][Oo][Nn]\s*\n\s*(\{[\s\S]*?\})\s*$", output)
    if json_match:
        try:
            raw = json_match.group(1)
            resume_data = json.loads(raw)
            # Strip empty or invalid date fields
            for section in ('work', 'education', 'volunteer'):
                for item in resume_data.get(section, []):
                    for f in ('startDate', 'endDate', 'date'):
                        if f in item:
                            v = item[f]
                            if not v or not re.match(r'^\d{4}(-\d{2}(-\d{2})?)?$', str(v)):
                                del item[f]
            resume_path = os.path.join(app_dir, "resume.json")
            with open(resume_path, "w", encoding="utf-8") as f:
                json.dump(resume_data, f, indent=2)
            from lib.build_resume import build as build_pdfs
            out = build_pdfs(resume_path, app_dir)
            if out:
                print(f"  RESUME: {out['resume']}", file=sys.stderr)
                if out.get('cover'):
                    print(f"  COVER: {out['cover']}", file=sys.stderr)
        except Exception as e:
            print(f"  JSON extraction failed: {e}", file=sys.stderr)

    return True, {
        "response_path": f"db://{job_id}/gemini_response.txt",
        "text": output[:2000], "scripts": [],
        "strategy_path": strategy_path,
    }


def cmd_craft(auto=False):
    if auto:
        return cmd_relentless()
    state = load()
    described = [(jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "described" and e.get("state") == "active"]
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
    print(f"URL: {entry.get('url', '')}")
    print(f"DIR: {os.path.join(RESULTS_DIR, jid)}")
    try:
        success, result = generate_tailored_docs(entry)
        if success and os.environ.get("JI_TAILOR", "agent") == "agent":
            print(f"  PROMPT_READY {jid} — write script.py then run 'admit {jid} --pdf <path>'", file=sys.stderr)
        elif success:
            print(f"  COMPLETE {jid} — run 'admit {jid}' to confirm, or 'review' to check quality", file=sys.stderr)
        else:
            err_str = str(result)[:120]
            if any(x in err_str for x in ["RATE_LIMIT", "Chrome not responding", "[gemini]"]):
                print(f"  TRANSIENT {jid} — {err_str}", file=sys.stderr)
                return
            advance(entry, entry.get("stage"), state="failed", error=str(result)[:200])
            print(f"  FAILED {jid} {err_str}", file=sys.stderr)
    except Exception as e:
        advance(entry, entry.get("stage"), state="failed", error=str(e)[:200])
        print(f"  ERROR {jid} {str(e)[:120]}", file=sys.stderr)


def craft_jid(jid):
    state = load()
    if jid not in state["jobs"]:
        print(f"ERROR: job {jid} not found", file=sys.stderr)
        return
    entry = state["jobs"][jid]
    stage = entry.get("stage")
    from lib.db import desc_exists
    if stage in ("extracted",):
        if desc_exists(jid):
            advance(entry, "described")
        else:
            print(f"ERROR: job {jid} has no description — run enrich.py first", file=sys.stderr)
            return
    if entry.get("stage") not in ("described",) or entry.get("state") != "active":
        print(f"ERROR: job {jid} is in stage '{entry.get('stage')}', state '{entry.get('state')}', can't tailor", file=sys.stderr)
        return
    if not entry.get("category"):
        print(f"ERROR: job {jid} has no category — enrich.py admit --category <name> first", file=sys.stderr)
        return
    success, result = generate_tailored_docs(entry)
    if success:
        mode = os.environ.get("JI_TAILOR", "agent")
        print(f"  PROMPT_READY {jid} — write script.py then run 'admit {jid} --pdf <path>'" if mode == "agent" else f"  COMPLETE {jid} — run 'admit {jid}' to confirm, or 'review' to check", file=sys.stderr)
    else:
        err_str = str(result)[:120]
        if any(x in err_str for x in ["RATE_LIMIT", "Chrome not responding", "[gemini]"]):
            print(f"  TRANSIENT {jid} — {err_str}", file=sys.stderr)
        else:
            advance(entry, entry.get("stage"), state="failed", error=str(result)[:200])
            print(f"  FAILED {jid} {err_str}", file=sys.stderr)


def cmd_review(count=1):
    state = load()
    tailored = [(jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "tailored"]
    if not tailored:
        print("No tailored jobs to review.", file=sys.stderr)
        return
    batch = tailored if count == -1 else tailored[:count]
    for jid, entry in batch:
        title = entry.get("title", "?")
        company = entry.get("company", "?")
        script_path = os.path.join(RESULTS_DIR, jid, "script.py")
        cl_text = ""
        if os.path.exists(script_path):
            with open(script_path, encoding="utf-8") as f:
                script_src = f.read()
            cl_match = re.search(r'COVER_LETTER_TEXT\s*=\s*"""(.+?)"""', script_src, re.DOTALL)
            if cl_match:
                cl_text = cl_match.group(1).strip()[:400]
        print(f"JOB {jid} {title} @ {company}", file=sys.stderr)
        print(f"  URL: {entry.get('url', '')}", file=sys.stderr)
        if cl_text:
            print(f"  COVER: {cl_text}", file=sys.stderr)
        print(f"  APPROVED or REJECT --feedback \"reason\"?", file=sys.stderr)


def cmd_admit(*job_ids, pdf_path=None):
    if not job_ids:
        print("Usage: python3 tailor.py admit <jid1> [jid2 ...]", file=sys.stderr)
        return
    state = load()
    count = 0
    for job_id in job_ids:
        if job_id not in state.get("jobs", {}):
            print(f"Job not found: {job_id}", file=sys.stderr)
            continue
        if pdf_path and not os.path.exists(pdf_path):
            print(f"PDF_NOT_FOUND: {job_id} — {pdf_path}", file=sys.stderr)
            continue
        entry = state["jobs"][job_id]
        if entry.get("state") != "active":
            print(f"  {job_id}: admitted with state '{entry.get('state')}' -> active", file=sys.stderr)
        advance(entry, "tailored", state="active", applied_at=datetime.now().isoformat())
        job_url = state["jobs"][job_id].get("url", "")
        if job_url and sys.platform == "win32":
            url_path = os.path.join(RESULTS_DIR, job_id, f"{job_id}.url")
            try:
                os.makedirs(os.path.dirname(url_path), exist_ok=True)
                with open(url_path, "w") as f:
                    f.write(f"[InternetShortcut]\nURL={job_url}\n")
            except Exception:
                pass
        count += 1
    print(f"ADMITTED:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_reject(*job_ids):
    if not job_ids:
        print("Usage: python3 tailor.py reject <jid1> [jid2 ...]", file=sys.stderr)
        return
    s = load()
    count = 0
    for job_id in job_ids:
        if job_id in s.get("jobs", {}):
            entry = s["jobs"][job_id]
            advance(entry, entry.get("stage"), state="rejected")
            count += 1
        else:
            print(f"Job not found: {job_id}", file=sys.stderr)
    print(f"REJECT:{count}", file=sys.stderr)
    if count:
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_undo(job_id):
    if not job_id:
        print("Usage: python3 tailor.py undo <job_id>", file=sys.stderr)
        return
    state = load()
    if job_id not in state.get("jobs", {}):
        print(f"Job not found: {job_id}", file=sys.stderr)
        return
    entry = state["jobs"][job_id]
    old_stage = entry.get("stage")
    prev = {"applied": "tailored", "tailored": "described", "described": "extracted"}.get(old_stage)
    if not prev:
        print(f"Can't undo: {old_stage} is the first stage", file=sys.stderr)
        return
    advance(entry, prev, error=None)
    print(f"Undone: {entry.get('title')} @ {entry.get('company')} ({old_stage} -> {prev})", file=sys.stderr)
    print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_retry(job_id=None, feedback=None):
    if job_id:
        state = load()
        entry = state["jobs"].get(job_id)
        if not entry:
            print(f"Job not found: {job_id}", file=sys.stderr)
            return
        advance(entry, "described", state="active", error=None)
        resp_path = os.path.join(RESULTS_DIR, job_id, "gemini_response.txt")
        prev = ""
        if os.path.exists(resp_path):
            with open(resp_path, encoding="utf-8") as f:
                prev = f.read()
        success, result = generate_tailored_docs(entry, feedback=feedback, prev_response=prev)
        if success:
            advance(entry, "tailored", response_path=result.get("response_path"), scripts=result.get("scripts", []))
            msg = "re-tailored with feedback" if feedback else "re-tailored"
            print(f"  {job_id}: {msg}", file=sys.stderr)
        else:
            advance(entry, entry.get("stage"), state="failed", error=str(result))
            print(f"  {job_id}: re-tailor failed - {result}", file=sys.stderr)
        return
    state = load()
    failed_jobs = get_failed(state)
    if not failed_jobs:
        print("No failed jobs.", file=sys.stderr)
        return
    # Only retry jobs that have descriptions (tailor failures, not enrich failures)
    from lib.db import desc_exists
    failed = [(jid, e) for jid, e in failed_jobs if desc_exists(jid)]
    skipped = len(failed_jobs) - len(failed)
    if skipped:
        print(f"Skipped {skipped} jobs with no description (not tailor failures)", file=sys.stderr)
    if not failed:
        print("No tailor failures to retry.", file=sys.stderr)
        return
    print(f"Retrying {len(failed)} failed jobs...", file=sys.stderr)
    processed = 0
    for job_id, entry in failed:
        advance(entry, "described", state="active")
        success, result = generate_tailored_docs(entry)
        if success:
            advance(entry, "tailored", response_path=result.get("response_path"), scripts=result.get("scripts", []))
            processed += 1
            print(f"  {job_id}: retry success", file=sys.stderr)
        else:
            advance(entry, entry.get("stage"), state="failed", error=str(result))
            print(f"  {job_id}: retry failed - {result}", file=sys.stderr)
    print(f"\nRetry complete. Succeeded: {processed}/{len(failed)}", file=sys.stderr)


def cmd_relentless():
    import re as _re, time as _time, subprocess as _sp, sys as _sys, os as _os
    from datetime import datetime
    _YEAR = datetime.now().year
    def wait_until(target_str):
        for fmt in [f"%b %d, %I:%M %p, %Y", f"%B %d, %I:%M %p, %Y"]:
            try:
                target = datetime.strptime(target_str + f", {_YEAR}", fmt)
                wait = (target - datetime.now()).total_seconds()
                if 0 < wait < 14400:
                    print(f"Rate limit — sleeping {wait:.0f}s", file=sys.stderr)
                    _time.sleep(wait)
                    return
            except ValueError:
                continue
        print(f"Rate limit — unknown reset '{target_str}', sleeping 120s", file=sys.stderr)
        _time.sleep(120)
    tailor_script = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "tailor.py")
    while True:
        r = _sp.run([_sys.executable, tailor_script], capture_output=True, text=True, timeout=300)
        output = (r.stdout or "") + (r.stderr or "")
        if "ALL_DONE" in output or "NO_PENDING" in output:
            s = pipeline_status()
            print(f"DONE: {s['stages'].get('tailored',0)} tailored")
            break
        m = _re.search(r'"resetsAt"\s*:\s*"([^"]+)"', output)
        if m:
            wait_until(m.group(1))
            continue
        s = pipeline_status()
        if s.get("stages", {}).get("described", 0) == 0:
            print(f"DONE: {s['stages'].get('tailored',0)} tailored")
            break


def cmd_reset(job_id=None, states=None, stages=None):
    s = load()
    if not s.get("jobs"):
        print("No jobs.", file=sys.stderr)
        return
    state_filter = []
    if states:
        state_filter.extend(st.strip() for st in states.split(","))
    stage_filter = []
    if stages:
        stage_filter.extend(st.strip() for st in stages.split(","))
    if state_filter or stage_filter:
        targets = [(jid, e) for jid, e in s["jobs"].items()
                   if (not state_filter or e.get("state") in state_filter)
                   and (not stage_filter or e.get("stage") in stage_filter)]
        if not targets:
            print(f"No matching jobs.", file=sys.stderr)
            return
    elif job_id == "--all":
        targets = list(s["jobs"].items())
    elif job_id:
        if job_id not in s["jobs"]:
            print(f"Job not found: {job_id}", file=sys.stderr)
            return
        targets = [(job_id, s["jobs"][job_id])]
    else:
        print("Usage: python3 tailor.py reset <jid> [--all]", file=sys.stderr)
        return
    for jid, entry in targets:
        old = entry.get("stage", "?")
        advance(entry, "extracted", state="active", error=None, response_path=None, scripts=[])
        print(f"  {jid}: {old} -> extracted", file=sys.stderr)
    print(f"Reset {len(targets)} jobs.", file=sys.stderr)


def cmd_help():
    print("""Usage:
  [--auto]                                  Craft all described jobs
  admit <jid> [jid...]                      Mark tailored (also: done)
  reject <jid> [jid...]                     Reject
  undo <jid>                                Move back one stage
  retry                                     Retry all failed (batch)
  retry <jid>                               Re-tailor a job
  retry <jid> --feedback "text"             Re-tailor with feedback
  reset <jid>                               Reset to extracted (first stage)
  reset --all                               Mass reset
  reset --state failed,skipped              Reset by stage
  help                                      This message""", file=sys.stderr)


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="tailor.py", description="Tailor CVs via Gemini Web")
    parser.add_argument("--auto", action="store_true", help="Craft all described jobs, retry on rate limit")
    parser.add_argument("--jid", help="Tailor a specific job by JID")

    sub = parser.add_subparsers(dest="command")
    admit_p = sub.add_parser("admit", help="Mark job as tailored")
    admit_p.add_argument("jids", nargs="+")
    admit_p.add_argument("--pdf", help="Path to generated PDF (verifies file exists)")
    done_p = sub.add_parser("done", help="Alias for admit (backward compat)")
    done_p.add_argument("jids", nargs="+")
    done_p.add_argument("--pdf", help="Path to generated PDF (verifies file exists)")
    sub.add_parser("reject", help="Reject job").add_argument("jids", nargs="+")
    review_p = sub.add_parser("review", help="Review tailored jobs (strategy + cover letter)")
    review_p.add_argument("--jobs", type=int, default=1, help="Jobs to review (default 1, -1 = all)")
    retry_p = sub.add_parser("retry", help="Retry failed, or re-process a specific job")
    retry_p.add_argument("jid", nargs="?")
    retry_p.add_argument("--feedback", help="What to fix (triggers one-shot re-tailor)")
    sub.add_parser("undo", help="Move job back one stage").add_argument("jid", nargs="?")
    reset_p = sub.add_parser("reset", help="Reset job to extracted (first stage)")
    reset_p.add_argument("target", nargs="?", help="jid or --all")
    reset_p.add_argument("--state", dest="states", help="Filter by state: failed, skipped")
    reset_p.add_argument("--stage", dest="stages", help="Filter by stage: tailored, described, extracted")
    sub.add_parser("help", help="This message")

    args = parser.parse_args()

    if args.command == "review":
        cmd_review(count=args.jobs)
    elif args.command in ("admit", "done"):
        cmd_admit(*args.jids, pdf_path=args.pdf)
    elif args.command == "reject":
        cmd_reject(*args.jids)
    elif args.command == "undo":
        cmd_undo(args.jid)
    elif args.command == "retry":
        cmd_retry(job_id=args.jid, feedback=args.feedback)
    elif args.command == "reset":
        if getattr(args, "states", None) or getattr(args, "stages", None):
            cmd_reset(states=args.states, stages=args.stages)
        elif args.target == "--all":
            cmd_reset(job_id="--all")
        elif args.target:
            cmd_reset(job_id=args.target)
        else:
            parser.print_help()
    elif args.command == "help":
        cmd_help()
    elif args.jid:
        craft_jid(args.jid)
    elif args.command is None:
        cmd_craft(auto=args.auto)


if __name__ == "__main__":
    main()
