"""extract.py — LLM-driven extraction. Uses gemini.js to identify job URLs from
staged emails and extract structured job data from fetched pages.

Usage:
  extract.py step [--count N]    Process N staged emails (LLM identifies URLs →
                                    fetches → LLM extracts jobs → saves to DB)
  extract.py run [--count N]     Print staged emails for manual LLM review
  extract.py submit <tid> <json> Save LLM results manually
  extract.py status              Extraction status
  extract.py reset               Reset extraction state (clear stale jobs)
"""

import json
import os
import re
import subprocess
import sys
import tempfile

from lib.db import stage_list_all, stage_count, setting_get, setting_set
from lib.db import add_job, load, save

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
GEMINI_JS = os.path.join(SKILL_DIR, "..", "gemini-browser", "gemini.js")
EXTRACTED_IDS_KEY = "extracted_ids"

EXTRACT_PROMPT = """You are reviewing a job-related email. Your task:

1. Read the email content below.
2. Identify any actual JOB POSTING URLs — links to specific job listings.
3. For each job URL found, return a JSON array with objects: url, title, company, confidence (1-10).
4. Skip: unsubscribe links, profile pages, social media, settings, "view in browser", tracking pixels.

Return ONLY a valid JSON array — no explanation, no markdown. If no jobs found, return [].

Email:
"""

PARSE_PROMPT = """You are analyzing a job listing page. Extract structured data:

Return JSON:
{{
  "title": "Job title",
  "company": "Company name",
  "location": "Location or null",
  "url": "{url}",
  "salary": "Salary or null",
  "job_type": "Full-Time or Part-Time or Contract or Internship",
  "department": "Department or null"
}}

Return ONLY valid JSON — no explanation, no markdown. If not a job posting, return {{"title": null}}.

Page content:
"""


def _call_gemini(prompt, timeout=120):
    """Call gemini.js with a prompt via --prompt-file. Returns stdout text."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(prompt)
    tmp.close()
    try:
        r = subprocess.run(
            ["node", GEMINI_JS, "--prompt-file", tmp.name],
            capture_output=True, timeout=timeout,
        )
        return r.stdout.strip()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _extract_json(text):
    """Extract JSON array or object from LLM response (handles markdown wrapping)."""
    if not text:
        return None
    text = text.strip()
    # Remove markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try finding JSON within the text
    m = re.search(r'(\[{.*}\]|{.*})', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _extract_urls(text):
    return list(set(re.findall(r'https?://[^\s<>"\'\]\)]+', text)))


def cmd_run(count=None):
    pending_ids = set(setting_get(EXTRACTED_IDS_KEY, []))
    all_staged = stage_list_all()
    pending = [(tid, content) for tid, content in all_staged if tid not in pending_ids]
    if not pending:
        print("ALL_EXTRACTED", file=sys.stderr)
        return
    if count:
        pending = pending[:count]
    for tid, content in pending:
        urls = _extract_urls(content)
        print(f"FILE {tid}", file=sys.stderr)
        print(f"URLS {json.dumps(urls)}", file=sys.stderr)
        print(f"---BEGIN EMAIL---", file=sys.stderr)
        print(content, file=sys.stderr)
        print(f"---END EMAIL---", file=sys.stderr)


def cmd_submit(tid, jobs_json):
    if isinstance(jobs_json, str):
        jobs = json.loads(jobs_json)
    if not isinstance(jobs, list):
        jobs = [jobs]
    count = 0
    for job in jobs:
        if not job.get("url"):
            continue
        job["email_id"] = tid
        job["source"] = "Email"
        job["source_url"] = job.get("url", "")
        jid = add_job(job)
        if jid:
            count += 1
    extracted_ids = setting_get(EXTRACTED_IDS_KEY, [])
    if tid not in extracted_ids:
        extracted_ids.append(tid)
        setting_set(EXTRACTED_IDS_KEY, extracted_ids)
    print(f"SUBMIT:{tid}:{count}", file=sys.stderr)


def cmd_step(count=1):
    """Automated end-to-end: LLM identifies URLs → fetches → LLM extracts → saves."""
    pending_ids = set(setting_get(EXTRACTED_IDS_KEY, []))
    all_staged = stage_list_all()
    pending = [(tid, content) for tid, content in all_staged if tid not in pending_ids]
    if not pending:
        print("ALL_EXTRACTED", file=sys.stderr)
        return
    if count:
        pending = pending[:count]

    for tid, content in pending:
        print(f"--- Processing {tid} ---", file=sys.stderr)

        # Step 1: Ask LLM to identify job URLs from email
        prompt = EXTRACT_PROMPT + content[:6000]
        print(f"  Identify URLs...", file=sys.stderr)
        resp = _call_gemini(prompt, timeout=120)
        urls_data = _extract_json(resp)
        if not urls_data or not isinstance(urls_data, list):
            print(f"  No URLs identified (skipping)", file=sys.stderr)
            _mark_extracted(tid, 0)
            continue
        print(f"  Got {len(urls_data)} potential job URLs", file=sys.stderr)

        # Step 2: Fetch and parse each URL
        job_count = 0
        for url_item in urls_data:
            url = url_item.get("url", "")
            if not url:
                continue
            if url_item.get("confidence", 5) < 3:
                print(f"  Skip (low confidence): {url[:60]}", file=sys.stderr)
                continue

            print(f"  Fetching: {url[:60]}", file=sys.stderr)
            try:
                from fetch import fetch_description
                ok, text = fetch_description(url, use_playwright=True)
            except Exception as e:
                print(f"  Fetch error: {e}", file=sys.stderr)
                continue

            if not ok or len(text) < 80:
                print(f"  Short content, skipping", file=sys.stderr)
                continue

            # Step 3: LLM extracts job details from fetched content
            parse_prompt = PARSE_PROMPT.format(url=url) + text[:4000]
            print(f"  Parsing...", file=sys.stderr)
            parse_resp = _call_gemini(parse_prompt, timeout=120)
            job_data = _extract_json(parse_resp)
            if not job_data or not job_data.get("title"):
                print(f"  Not a job posting", file=sys.stderr)
                continue

            job_data["email_id"] = tid
            job_data["source"] = "Email"
            job_data["source_url"] = url
            jid = add_job(job_data)
            if jid:
                # Save the fetched description
                from lib.db import desc_save
                desc_save(jid, text[:8000])
                job_count += 1
                print(f"  Saved: {job_data.get('title','?')[:40]}", file=sys.stderr)

        _mark_extracted(tid, job_count)
        print(f"--- Done {tid}: {job_count} jobs ---", file=sys.stderr)


def _mark_extracted(tid, count):
    extracted_ids = setting_get(EXTRACTED_IDS_KEY, [])
    if tid not in extracted_ids:
        extracted_ids.append(tid)
        setting_set(EXTRACTED_IDS_KEY, extracted_ids)
    print(f"EXTRACTED:{tid}:{count}", file=sys.stderr)


def cmd_reset():
    """Reset extraction state and clear stale jobs from old regex extraction."""
    from lib.db import get_conn
    c = get_conn()
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("DELETE FROM events")
    c.execute("DELETE FROM job_documents")
    c.execute("DELETE FROM jobs")
    c.execute("DELETE FROM companies")
    c.execute("PRAGMA foreign_keys=ON")
    c.commit()
    setting_set(EXTRACTED_IDS_KEY, [])
    import shutil
    res_dir = os.path.join(SKILL_DIR, "results")
    if os.path.exists(res_dir):
        shutil.rmtree(res_dir)
    print("Reset complete. Staged emails ready for fresh extraction.", file=sys.stderr)


def cmd_status():
    total = stage_count()
    extracted_ids = setting_get(EXTRACTED_IDS_KEY, [])
    pending = total - len(extracted_ids)
    print(f"Staged: {total} | Extracted: {len(extracted_ids)} | Pending: {pending}", file=sys.stderr)
    state = load()
    for s in ["extracted", "described", "tailored", "applied", "skipped", "failed"]:
        c = state.get("stages", {}).get(s, 0)
        if c:
            print(f"  {s}: {c}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 extract.py <cmd> [args]", file=sys.stderr)
        print("Commands:", file=sys.stderr)
        print("  step [--count N]     Automated: LLM → fetch → LLM → save (end-to-end)", file=sys.stderr)
        print("  run [--count N]      Print staged emails for manual review", file=sys.stderr)
        print("  submit <tid> <json>  Submit LLM results manually", file=sys.stderr)
        print("  reset                Clear state and start fresh", file=sys.stderr)
        print("  status               Extraction status", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "step":
        count = 1
        if "--count" in sys.argv:
            i = sys.argv.index("--count")
            if i + 1 < len(sys.argv):
                count = int(sys.argv[i + 1])
        cmd_step(count=count)

    elif cmd == "run":
        count = None
        if "--count" in sys.argv:
            i = sys.argv.index("--count")
            if i + 1 < len(sys.argv):
                count = int(sys.argv[i + 1])
        cmd_run(count=count)

    elif cmd == "submit":
        if len(sys.argv) < 4:
            print("Usage: python3 extract.py submit <tid> '<json>'", file=sys.stderr)
            sys.exit(1)
        cmd_submit(sys.argv[2], sys.argv[3])

    elif cmd == "reset":
        cmd_reset()

    elif cmd == "status":
        cmd_status()

    else:
        print(f"Unknown: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
