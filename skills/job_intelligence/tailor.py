"""tailor.py — Tailor CVs via Gemini Web.

Usage:
  tailor.py [--auto]                  Craft all described jobs
   tailor.py admit <jid> [jid...]      Mark job as tailored (auto-finds resume in results dir)
  tailor.py reject <jid> [jid...]      Reject job(s)
  tailor.py retry                     Retry all failed (batch)
  tailor.py retry <jid>               Re-tailor a specific job
  tailor.py retry <jid> --feedback "x" Re-tailor with feedback
  tailor.py undo <jid>                Move job back one stage
  tailor.py reset <jid>               Reset job to extracted (first stage)
  tailor.py reset --all               Mass reset
  tailor.py reset --state <state>     Reset by state (failed, skipped)
"""

import hashlib, json, os, re, sys
from datetime import datetime

from lib.db import load, advance, get_failed, pipeline_status
from lib.db import desc_get, app_save
from lib.call_gemini import call_gemini_node
from lib.config import RESULTS_DIR
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
        title_clean = title_clean.replace(bad, good)
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
        prompt += f"\n\n--- PREVIOUS ATTEMPT ---\n{prev_response}\n\n--- FEEDBACK ---\n{feedback}"

    # Always attach tailor_prompt.md rules
    prompt_path_md = os.path.join(os.path.dirname(__file__), "tailor_prompt.md")
    try:
        with open(prompt_path_md) as f:
            instructions = f.read()
        prompt = instructions + "\n\n---\n\n" + prompt
    except FileNotFoundError:
        pass

    tailor_mode = os.environ.get("JI_TAILOR", "agent")
    app_dir = os.path.join(RESULTS_DIR, job_id)
    os.makedirs(app_dir, exist_ok=True)

    # Save prompt for reference
    prompt_file = os.path.join(app_dir, "prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)

    if tailor_mode == "agent":
        print(f"PROMPT: {prompt_file}", file=sys.stderr)
        print(f"  Write resume.json, then: tailor.py build {job_id} && tailor.py admit {job_id}", file=sys.stderr)
        return True, {"text": prompt, "response_path": prompt_file, "scripts": []}

    # Gemini route
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

    # Extract JSON Resume from response
    json_match = re.search(r"```json\s*(.*?)```", output, re.DOTALL)
    if not json_match:
        json_match = re.search(r"({[\s\S]*\"basics\"[\s\S]*})", output)
    if json_match:
        try:
            resume_data = json.loads(json_match.group(1))
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

    # Extract strategy section (optional)
    strategy_path = None
    strategy_match = re.search(r"(?:1\.\s*)?(Strategy.*?)(?=\n\s*(?:2\s*[&.]|3\.|Optimized|$))", output, re.DOTALL)
    if strategy_match:
        strategy_text = strategy_match.group(1).strip()
        app_save(job_id, "strategy.md", strategy_text)
        strategy_path = f"db://{job_id}/strategy.md"

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
            print("ALL_DONE", file=sys.stderr)
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
            print(f"  PROMPT_READY {jid} — write resume.json, then 'tailor.py build {jid}'", file=sys.stderr)
        elif success:
            print(f"  COMPLETE {jid} — run 'admit {jid}' to confirm, or 'review' to check quality", file=sys.stderr)
            text = result.get("text", "")
            if text:
                print(f"\n---RESPONSE---\n{text}\n---", file=sys.stderr)
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
        print(f"  PROMPT_READY {jid} — write resume.json, then 'tailor.py build {jid}'" if mode == "agent" else f"  COMPLETE {jid} — run 'admit {jid}' to confirm, or 'review' to check", file=sys.stderr)
    else:
        err_str = str(result)[:120]
        if any(x in err_str for x in ["RATE_LIMIT", "Chrome not responding", "[gemini]"]):
            print(f"  TRANSIENT {jid} — {err_str}", file=sys.stderr)
        else:
            advance(entry, entry.get("stage"), state="failed", error=str(result)[:200])
            print(f"  FAILED {jid} {err_str}", file=sys.stderr)


def cmd_review(count=1):
    state = load()
    # Find jobs to review: any with resume.json in results dir, or stage=tailored
    candidates = []
    for jid, entry in state["jobs"].items():
        if entry.get("stage") == "tailored":
            candidates.append((jid, entry))
        elif os.path.exists(os.path.join(RESULTS_DIR, jid, "resume.json")):
            candidates.append((jid, entry))
    if not candidates:
        print("No jobs to review.", file=sys.stderr)
        return
    batch = candidates if count == -1 else candidates[:count]
    rules_path = os.path.join(os.path.dirname(__file__), "tailor_prompt.md")
    for jid, entry in batch:
        title = entry.get("title", "?")
        company = entry.get("company", "?")
        print(f"JOB {jid} {title} @ {company}", file=sys.stderr)
        print(f"  URL: {entry.get('url', '')}", file=sys.stderr)
        rd = os.path.join(RESULTS_DIR, jid)
        # Show resume summary + first work entry from resume.json
        json_path = os.path.join(rd, "resume.json")
        if os.path.exists(json_path):
            try:
                import json as _j
                with open(json_path) as f:
                    rd_data = _j.load(f)
                basics = rd_data.get("basics", {})
                work = rd_data.get("work", [])
                summary = (basics.get("summary") or "")[:400]
                if summary:
                    print(f"  SUMMARY: {summary}", file=sys.stderr)
                if work:
                    w = work[0]
                    print(f"  EXP: {w.get('position','?')} @ {w.get('company','?')}", file=sys.stderr)
                    for h in w.get("highlights", [])[:2]:
                        print(f"    - {h[:120]}", file=sys.stderr)
            except Exception:
                pass
        # Show cover letter from PDF
        if os.path.isdir(rd):
            covers = [f for f in os.listdir(rd) if "Cover" in f and f.endswith(".pdf")]
            if covers:
                try:
                    import PyPDF2
                    with open(os.path.join(rd, covers[0]), "rb") as f:
                        ct = " ".join(p.extract_text() for p in PyPDF2.PdfReader(f).pages)
                    print(f"  COVER: {ct[:500]}", file=sys.stderr)
                except Exception:
                    pass
        print(f"  RULES: check against tailor_prompt.md — no pandering, hallucination, title creep, tool soup. Company name must appear in summary or bullets.", file=sys.stderr)
        if os.path.exists(rules_path):
            print(f"  Full rules: {rules_path}", file=sys.stderr)
        print(f"  apprentice: admit/reject/retry --feedback '...'", file=sys.stderr)


def cmd_admit(*job_ids):
    if not job_ids:
        print("Usage: python tailor.py admit <jid1> [jid2 ...]", file=sys.stderr)
        return
    state = load()
    count = 0
    for job_id in job_ids:
        if job_id not in state.get("jobs", {}):
            print(f"Job not found: {job_id}", file=sys.stderr)
            continue
        rd = os.path.join(RESULTS_DIR, job_id)
        if os.path.isdir(rd):
            pdfs = [f for f in os.listdir(rd) if f.lower().endswith('.pdf') and 'resume' in f.lower()]
            if pdfs:
                pdf_path = os.path.join(rd, pdfs[0])
            else:
                print(f"NO_PDF: {job_id} — no Resume PDF in {rd}", file=sys.stderr)
                print(f"  Run tailor.py build {job_id} first, then re-run admit", file=sys.stderr)
                continue
        else:
            print(f"NO_RESULTS: {job_id} — no results directory", file=sys.stderr)
            continue
        entry = state["jobs"][job_id]
        if entry.get("state") != "active":
            print(f"  {job_id}: admitted with state '{entry.get('state')}' -> active", file=sys.stderr)
        # Quality summary before committing
        from lib.build_resume import validate_file as _vf
        _jp = os.path.join(RESULTS_DIR, job_id, "resume.json")
        if os.path.exists(_jp):
            if not _vf(_jp):
                print(f"  BLOCKED: validation errors — fix resume.json and re-run admit", file=sys.stderr)
                continue
        else:
            print(f"  NOTE: no resume.json — admit from file only", file=sys.stderr)
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


def cmd_build(job_id):
    """Validate resume.json and build PDFs for a job."""
    if not job_id:
        print("Usage: python tailor.py build <jid>", file=sys.stderr)
        return
    from lib.build_resume import validate_file, build as build_pdfs
    json_path = os.path.join(RESULTS_DIR, job_id, "resume.json")
    if not os.path.exists(json_path):
        print(f"NOT_FOUND: {json_path}", file=sys.stderr)
        print("  Write resume.json first, then run build.", file=sys.stderr)
        return
    print(f"BUILD: {job_id}", file=sys.stderr)
    if not validate_file(json_path):
        print("  Fix validation errors above, then re-run build.", file=sys.stderr)
        return
    out = build_pdfs(json_path, os.path.join(RESULTS_DIR, job_id))
    if out:
        print(f"  RESUME: {out['resume']}", file=sys.stderr)
        if out.get("cover"):
            print(f"  COVER: {out['cover']}", file=sys.stderr)
        print(f"  NEXT: tailor.py admit {job_id}", file=sys.stderr)


def cmd_reject(*job_ids):
    if not job_ids:
        print("Usage: python tailor.py reject <jid1> [jid2 ...]", file=sys.stderr)
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
        print("Usage: python tailor.py undo <job_id>", file=sys.stderr)
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
        prev = ""
        for candidate in ["gemini_response.txt", "resume.json"]:
            p = os.path.join(RESULTS_DIR, job_id, candidate)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    prev = f.read()
                break
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
        print("Usage: python tailor.py reset <jid> [--all]", file=sys.stderr)
        return
    for jid, entry in targets:
        old = entry.get("stage", "?")
        advance(entry, "extracted", state="active", error=None, response_path=None, scripts=[])
        print(f"  {jid}: {old} -> extracted", file=sys.stderr)
    print(f"Reset {len(targets)} jobs.", file=sys.stderr)


def cmd_help():
    print("""Usage:
  [--auto]                                  Craft all described jobs
  admit <jid> [jid...]                      Mark tailored (auto-finds resume)
  reject <jid> [jid...]                     Reject
  build <jid>                               Validate resume.json + build PDFs
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
    admit_p = sub.add_parser("admit", help="Mark job as tailored (auto-finds resume in results dir)")
    admit_p.add_argument("jids", nargs="+")
    sub.add_parser("reject", help="Reject job").add_argument("jids", nargs="+")
    review_p = sub.add_parser("review", help="Review tailored jobs (strategy + cover letter)")
    review_p.add_argument("--jobs", type=int, default=1, help="Jobs to review (default 1, -1 = all)")
    retry_p = sub.add_parser("retry", help="Retry failed, or re-process a specific job")
    retry_p.add_argument("jid", nargs="?")
    retry_p.add_argument("--feedback", help="What to improve (replaces any previous feedback — not cumulative)")
    sub.add_parser("undo", help="Move job back one stage").add_argument("jid", nargs="?")
    reset_p = sub.add_parser("reset", help="Reset job to extracted (first stage)")
    reset_p.add_argument("target", nargs="?", help="jid or --all")
    reset_p.add_argument("--state", dest="states", help="Filter by state: failed, skipped")
    reset_p.add_argument("--stage", dest="stages", help="Filter by stage: tailored, described, extracted")
    build_p = sub.add_parser("build", help="Validate resume.json + build PDFs")
    build_p.add_argument("jid", help="Job ID")
    sub.add_parser("help", help="This message")

    args = parser.parse_args()

    if args.command == "review":
        cmd_review(count=args.jobs)
    elif args.command == "build":
        cmd_build(args.jid)
    elif args.command == "admit":
        cmd_admit(*args.jids)
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
