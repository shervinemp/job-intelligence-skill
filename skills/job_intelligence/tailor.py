"""tailor.py — Tailor CVs via Gemini Web.

Usage:
  tailor.py [--auto]                  Craft all described jobs
  tailor.py admit <jid> [jid...]      Mark job as tailored (grounding-gated)
  tailor.py reject <jid> [jid...]      Reject job(s)
  tailor.py retry                     Retry all failed (batch)
  tailor.py retry <jid>               Re-tailor a specific job
  tailor.py retry <jid> --feedback "x" Re-tailor with feedback
  tailor.py undo <jid>                Move job back one stage
  tailor.py reset <jid>               Reset job to extracted (first stage)
  tailor.py reset --all               Mass reset
  tailor.py reset --state <state>     Reset by state (failed, skipped)
  tailor.py ground <jid>              Factual-grounding manifest: every
                                      tailored claim must trace to
                                      profile.json (admit is blocked until
                                      clean; --force after human review)
  tailor.py check <jid>               Re-run the PDF quality gate (one-page
                                      + no overlapping/clipped text)
  tailor.py review [--jobs N]         Review tailored jobs (approve, or
                                      retry --feedback)
"""

import hashlib, json, os, re, sys

from lib.db import load, advance, get_failed, pipeline_status
from lib.db import desc_get, app_save
from lib.call_gemini import call_gemini_node
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
        if not os.path.exists(prompt_path):
            return False, f"tailor_prompt.md not found at {prompt_path}"
        with open(prompt_path) as f:
            instructions = f.read()
        prompt = instructions + "\n\n---\n\n" + prompt
        prompt += "\n\nWrite the resume.json file with the tailored JSON Resume data."
        print(f"PROMPT: {os.path.join(RESULTS_DIR, job_id, 'prompt.txt')}", file=sys.stderr)
        print(f"  Write resume.json, then: python -m lib.build_resume {os.path.join(RESULTS_DIR, job_id, 'resume.json')} {os.path.join(RESULTS_DIR, job_id)} && tailor.py admit {job_id}", file=sys.stderr)
        return True, {"text": prompt, "response_path": None, "scripts": []}

    # Gem route — output JSON block (gem has instructions built-in)
    prompt += "\n\nOutput the full JSON Resume (including coverLetter) in a ```json code block."

    app_dir = os.path.join(RESULTS_DIR, job_id)
    os.makedirs(app_dir, exist_ok=True)

    # PDF-quality retry loop: the one-page/overlap/clip gate runs after every
    # build; a failing resume re-invokes the gem with the exact findings as
    # feedback (the orchestrator's "retry with feedback" contract). Bounded so
    # a stuck model can't loop forever.
    _MAX_PDF_RETRIES = int(os.environ.get("JI_PDF_RETRY", "2"))
    _pdf_feedback = ""
    success = False
    output = ""
    _gate_exhausted = False
    _no_json = False
    for attempt in range(_MAX_PDF_RETRIES + 1):
        stale = os.path.join(app_dir, "gemini_response.txt")
        if os.path.exists(stale):
            os.remove(stale)
        _prompt = prompt
        if _pdf_feedback:
            _prompt += (
                "\n\n--- PDF QUALITY CHECK FAILED (fix these, keep the JSON "
                "Resume format) ---\n" + _pdf_feedback)
        success, output = call_gemini_node(
            [_prompt, "--app-dir", app_dir], timeout_seconds=600, gem=gem)
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

        report = _extract_and_build_resume(output, app_dir, job)
        if report is None:
            # No resume JSON block in the output (model replied with prose or
            # an error). This is NOT a successful tailor — the resume does not
            # exist, so nothing passed the gate. Fail so the job is retryable
            # rather than advancing with no resume.
            _gate_exhausted = True
            print(f"  PDF_FAILED: no resume JSON in gem output — review the "
                  f"response before admit", file=sys.stderr)
            _no_json = True
            break
        if report["check"].get("ok", True):
            print(f"  PDF_CHECK: resume OK — {report['check']['pages']} page(s)",
                  file=sys.stderr)
            break
        if attempt < _MAX_PDF_RETRIES:
            from lib.pdf_check import feedback_for as _pdf_feedback_for
            _pdf_feedback = _pdf_feedback_for(report["check"])
            print(f"  PDF_RETRY: resume failed check (attempt {attempt + 1}) — "
                  f"re-invoking gem with feedback", file=sys.stderr)
        else:
            _gate_exhausted = True
            print(f"  PDF_FAILED: resume still fails check after "
                  f"{_MAX_PDF_RETRIES + 1} attempt(s) — review the PDF before "
                  f"admit", file=sys.stderr)
            break

    if _gate_exhausted:
        # A resume that cannot pass the one-page/overlap gate (or was never
        # produced) must NOT be marked tailored. Return failure so the job
        # goes to `failed` state and `tailor.py retry` (with feedback) can fix.
        if _no_json:
            return False, ("no resume JSON in gem output — the model did not "
                           "produce a resume; review the response and retry")
        return False, ("resume fails the PDF quality gate (one page / no "
                       "overlap / no clipping) after retries — fix via "
                       "tailor.py retry <jid> --feedback or review manually")

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


def _extract_and_build_resume(output, app_dir, job):
    """Extract the JSON Resume block from a gem response, write resume.json,
    and build PDFs. Returns {'check': report} on success, or None when no
    resume JSON is present in the output."""
    json_match = re.search(r"```json\s*(.*?)```", output, re.DOTALL)
    if not json_match:
        json_match = re.search(r"\b[Jj][Ss][Oo][Nn]\s*\n\s*(\{[\s\S]*?\})\s*$", output)
    if not json_match:
        return None
    try:
        raw = json_match.group(1)
        resume_data = json.loads(raw)
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
        out = build_pdfs(resume_path, app_dir, company=job.get("company", ""))
        if out:
            print(f"  RESUME: {out['resume']}", file=sys.stderr)
            if out.get('cover'):
                print(f"  COVER: {out['cover']}", file=sys.stderr)
            return {"check": out.get("check", {})}
        return None
    except Exception as e:
        print(f"  JSON extraction failed: {e}", file=sys.stderr)
        return None


def cmd_craft(auto=False):
    if auto:
        return cmd_relentless()
    state = load()
    described = [(jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "described" and e.get("state") == "active"]
    if not described:
        failed_count = state["states"].get("failed", 0)
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
            print(f"  PROMPT_READY {jid} — review prompt.txt, create resume.json, then run 'python -m lib.build_resume {os.path.join(RESULTS_DIR, jid, 'resume.json')} {os.path.join(RESULTS_DIR, jid)}' && 'tailor.py admit {jid}'", file=sys.stderr)
        elif success:
            print(f"  COMPLETE {jid} — run 'tailor.py review --jid {jid}'", file=sys.stderr)
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
        print(f"  PROMPT_READY {jid} — review prompt.txt, create resume.json, then run 'python -m lib.build_resume {os.path.join(RESULTS_DIR, jid, 'resume.json')} {os.path.join(RESULTS_DIR, jid)}' && 'tailor.py admit {jid}'" if mode == "agent" else f"  COMPLETE {jid} — run 'admit {jid}' to confirm, or 'review' to check", file=sys.stderr)
    else:
        err_str = str(result)[:120]
        if any(x in err_str for x in ["RATE_LIMIT", "Chrome not responding", "[gemini]"]):
            print(f"  TRANSIENT {jid} — {err_str}", file=sys.stderr)
        else:
            advance(entry, entry.get("stage"), state="failed", error=str(result)[:200])
            print(f"  FAILED {jid} {err_str}", file=sys.stderr)


def cmd_pdf_check(job_id):
    """Re-run the PDF quality gate on an existing tailored resume.

    For the agent route (the LLM writes resume.json + builds manually), this
    is the orchestrator's post-build verification: read the built resume PDF
    and emit PDF_CHECK lines. Returns 0 if the gate passes, 1 if it fails or
    no resume PDF exists."""
    if not job_id:
        print("Usage: python3 tailor.py check <jid>", file=sys.stderr)
        return 1
    rd = os.path.join(RESULTS_DIR, job_id)
    if not os.path.isdir(rd):
        print(f"PDF_CHECK: {job_id} — no results dir", file=sys.stderr)
        return 1
    pdfs = [f for f in os.listdir(rd) if "Resume" in f and f.endswith(".pdf")]
    if not pdfs:
        print(f"PDF_CHECK: {job_id} — no resume PDF; build first "
              f"(python -m lib.build_resume {os.path.join(rd, 'resume.json')} {rd})",
              file=sys.stderr)
        return 1
    from lib.pdf_check import check_file as _pdf_check_file
    path = os.path.join(rd, pdfs[0])
    report = _pdf_check_file(path, max_pages=1, label=f"resume[{job_id}]")
    return 0 if report["ok"] else 1


def cmd_review(jid=None, count=1):
    state = load()
    if jid:
        candidates = [(jid, state["jobs"][jid])] if jid in state["jobs"] else []
    else:
        candidates = [(jid, e) for jid, e in state["jobs"].items() if e.get("stage") == "tailored"]
    if not candidates:
        print("No tailored jobs to review.", file=sys.stderr)
        return
    batch = candidates if (count == -1 or jid) else candidates[:count]
    for jid, entry in batch:
        title = entry.get("title", "?")
        company = entry.get("company", "?")
        rd = os.path.join(RESULTS_DIR, jid)
        print(f"JOB {jid} {title} @ {company}", file=sys.stderr)
        print(f"  URL: {entry.get('url', '')}", file=sys.stderr)
        print(f"  RESUME: {os.path.join(rd, 'resume.json')}", file=sys.stderr)
        resume_pdfs = [f for f in os.listdir(rd) if "Resume" in f and f.endswith(".pdf")] if os.path.isdir(rd) else []
        if resume_pdfs:
            print(f"  PDF: {os.path.join(rd, resume_pdfs[0])}", file=sys.stderr)
        print(f"NEXT: tailor.py admit {jid}", file=sys.stderr)


def cmd_admit(*job_ids, pdf_path=None, force=False):
    if not job_ids:
        print("Usage: python3 tailor.py admit <jid1> [jid2 ...]", file=sys.stderr)
        return
    # Factual-grounding gate: a tailored resume with NOVEL claims (facts
    # not traceable to profile.json) must not ship to an employer. The
    # manifest names the claims; --force is the explicit review override.
    from lib.grounding import ground
    from lib.config import PROFILE_PATH
    import json as _json
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            _profile = _json.load(f)
    except Exception:
        _profile = {}
    state = load()
    count = 0
    for job_id in job_ids:
        if job_id not in state.get("jobs", {}):
            print(f"Job not found: {job_id}", file=sys.stderr)
            continue
        if pdf_path and not os.path.exists(pdf_path):
            print(f"PDF_NOT_FOUND: {job_id} — {pdf_path}", file=sys.stderr)
            continue
        # PDF quality gate (one-page + no overlap/clip). A resume that breaks
        # the one-page rule must not ship to an employer — admit is blocked
        # unless --force (the orchestrator's explicit review verdict).
        if not force:
            try:
                _rd = os.path.join(RESULTS_DIR, job_id)
                _pdfs = [f for f in os.listdir(_rd)
                         if "Resume" in f and f.endswith(".pdf")] \
                    if os.path.isdir(_rd) else []
                if _pdfs:
                    from lib.pdf_check import check as _pdf_check
                    _pc = _pdf_check(os.path.join(_rd, _pdfs[0]), max_pages=1)
                    if not _pc["ok"]:
                        print(f"PDF_CHECK_BLOCKED: {job_id} — "
                              f"{_pc['pages']} page(s), "
                              f"{len(_pc['overlaps'])} overlap(s), "
                              f"{len(_pc['clipped'])} clip(s). "
                              f"Fix the resume (tailor.py retry {job_id} "
                              f"--feedback ... or tailor.py check {job_id}) "
                              f"or --force after review.", file=sys.stderr)
                        continue
            except Exception as _pe:
                print(f"PDF_CHECK_ERR: {job_id} — {_pe}", file=sys.stderr)
        if not force:
            _rp = os.path.join(RESULTS_DIR, job_id, "resume.json")
            if os.path.exists(_rp):
                try:
                    with open(_rp, encoding="utf-8") as f:
                        _r = _json.load(f)
                    _m = ground(_r, profile=_profile)
                    if _m["blocked"]:
                        print(f"GROUNDING_BLOCKED: {job_id} — "
                              f"material={len(_m['material'])} "
                              f"figure={len(_m['figure'])} "
                              f"mismatch={len(_m['mismatches'])}. "
                              f"Run 'tailor.py ground {job_id}' and review. "
                              f"(--force after orchestrator verdict)",
                              file=sys.stderr)
                        continue
                    elif _m["framing"]:
                        print(f"FRAMING_NOTE: {job_id} — {len(_m['framing'])} "
                              f"framing claim(s) ride (advisory):", file=sys.stderr)
                        for _fc in _m["framing"][:5]:
                            print(f"    - {_fc}", file=sys.stderr)
                except Exception as _ge:
                    print(f"GROUNDING_ERR: {job_id} — {_ge}", file=sys.stderr)
                    continue
            else:
                print(f"GROUNDING_ERR: {job_id} — no resume.json; tailor first",
                      file=sys.stderr)
                continue
        else:
            # --force = the orchestrator's conscious verdict after review.
            # Record it so the override is auditable, not a silent escape hatch.
            try:
                _rp = os.path.join(RESULTS_DIR, job_id, "resume.json")
                if os.path.exists(_rp):
                    with open(_rp, encoding="utf-8") as f:
                        _r = _json.load(f)
                    _m = ground(_r, profile=_profile)
                    import datetime as _dt
                    with open(os.path.join(RESULTS_DIR, job_id,
                                           "grounding_manifest.json"),
                              "w", encoding="utf-8") as _mf:
                        _json.dump({
                            "verdict": "force",
                            "at": _dt.datetime.now().isoformat(),
                            "material": _m.get("material", []),
                            "figure": _m.get("figure", []),
                            "framing": _m.get("framing", []),
                            "mismatches": _m.get("mismatches", []),
                        }, _mf, indent=2)
                    print(f"OVERRIDE_RECORDED: {job_id} — "
                          f"grounding_manifest.json written", file=sys.stderr)
            except Exception as _ge:
                print(f"OVERRIDE_WARN: {job_id} — could not record manifest: {_ge}",
                      file=sys.stderr)
        entry = state["jobs"][job_id]
        if entry.get("state") != "active":
            print(f"  {job_id}: admitted with state '{entry.get('state')}' -> active", file=sys.stderr)
        advance(entry, "tailored", state="active")
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
    if os.environ.get("JI_TAILOR", "agent") == "agent":
        print("ERROR: --auto requires gem route (JI_TAILOR=gem). Agent mode is one-job-at-a-time.", file=sys.stderr)
        return
    consecutive_fail = 0
    while True:
        state = load()
        described = [(jid, e) for jid, e in state["jobs"].items()
                     if e.get("stage") == "described" and e.get("state") == "active"]
        if not described:
            s = pipeline_status()
            failed = state.get("states", {}).get("failed", 0)
            if failed:
                print(f"DONE: {s['stages'].get('tailored', 0)} tailored, {failed} failed (use 'retry')", file=sys.stderr)
            else:
                print(f"DONE: {s['stages'].get('tailored', 0)} tailored, all clear", file=sys.stderr)
            break
        if consecutive_fail >= 3:
            print(f"PAUSED: {consecutive_fail} consecutive failures — Chrome/gemini likely down", file=sys.stderr)
            break

        jid, entry = described[0]
        title = entry.get("title", "?")
        company = entry.get("company", "?")
        print(f"\n{jid} {title} @ {company}", file=sys.stderr)

        try:
            success, result = generate_tailored_docs(entry)
        except Exception as e:
            success, result = False, str(e)

        if success:
            consecutive_fail = 0
            advance(entry, "tailored", state="active")
            print(f"  COMPLETE {jid}", file=sys.stderr)
        else:
            err_str = str(result)[:160]
            if any(x in err_str for x in ["RATE_LIMIT", "rate_limit"]):
                resets = _re_search_resets(err_str)
                if resets:
                    _wait_until(resets)
                else:
                    print(f"  RATE_LIMIT {jid} — sleeping 120s", file=sys.stderr)
                    import time as _t
                    _t.sleep(120)
                continue
            consecutive_fail += 1
            if any(x in err_str for x in ["Chrome not responding", "[gemini]"]):
                print(f"  TRANSIENT {jid} — {err_str}", file=sys.stderr)
                continue
            advance(entry, entry.get("stage"), state="failed", error=err_str[:200])
            print(f"  FAILED {jid} — {err_str}", file=sys.stderr)


def _re_search_resets(s):
    import re as _re
    m = _re.search(r'"resetsAt"\s*:\s*"([^"]+)"', s)
    return m.group(1) if m else None


def _wait_until(target_str):
    from datetime import datetime
    year = datetime.now().year
    for fmt in [f"%b %d, %I:%M %p, %Y", f"%B %d, %I:%M %p, %Y"]:
        try:
            target = datetime.strptime(target_str + f", {year}", fmt)
            wait = (target - datetime.now()).total_seconds()
            if 0 < wait < 14400:
                print(f"Rate limit — sleeping {wait:.0f}s", file=sys.stderr)
                import time as _t
                _t.sleep(wait)
                return
        except ValueError:
            continue
    print(f"Rate limit — unknown reset '{target_str}', sleeping 120s", file=sys.stderr)
    import time as _t
    _t.sleep(120)


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
  [--auto]                                  Craft all described jobs (gem route only)
  admit <jid> [jid...]                      Mark tailored (grounding-gated)
  reject <jid> [jid...]                     Reject
  undo <jid>                                Move back one stage
  retry                                     Retry all failed (batch)
  retry <jid>                               Re-tailor a job
  retry <jid> --feedback "text"             Re-tailor with feedback
  check <jid>                               Re-run the PDF quality gate
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
    admit_p = sub.add_parser("admit", help="Mark job as tailored (grounding-gated)")
    admit_p.add_argument("jids", nargs="+")
    admit_p.add_argument("--pdf", help="Path to generated PDF (verifies file exists)")
    admit_p.add_argument("--force", action="store_true",
                         help="Skip the factual-grounding gate (after human review)")
    ground_p = sub.add_parser("ground", help="Factual-grounding manifest for a tailored resume")
    ground_p.add_argument("jids", nargs="+")
    check_p = sub.add_parser("check", help="Re-run the PDF quality gate (one-page + no overlap/clip)")
    check_p.add_argument("jid", help="Job ID whose resume PDF to check")
    sub.add_parser("reject", help="Reject job").add_argument("jids", nargs="+")
    review_p = sub.add_parser("review", help="Review tailored jobs (strategy + cover letter)")
    review_p.add_argument("--jid", help="Specific job ID to review")
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
        cmd_review(jid=args.jid, count=args.jobs)
    elif args.command == "ground":
        from lib.grounding import cmd_ground
        rc = 0
        for jid in args.jids:
            rc |= cmd_ground(jid, RESULTS_DIR)
        return rc
    elif args.command == "check":
        return cmd_pdf_check(args.jid)
    elif args.command == "admit":
        cmd_admit(*args.jids, pdf_path=args.pdf, force=getattr(args, "force", False))
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
        if args.auto:
            cmd_relentless()
        else:
            cmd_craft(auto=False)


if __name__ == "__main__":
    main()
