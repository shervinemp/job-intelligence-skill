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
import sys

from lib.db import stage_list_all, stage_count, setting_get, setting_set
from lib.db import add_job, load

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_IDS_KEY = "extracted_ids"


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
    """Print staged email content for LLM to process. Agent reads via stderr, calls submit."""
    cmd_run(count=count)
    print("\n---\nRead the FILE content above. Identify job URLs. Then call:", file=sys.stderr)
    print("  python3 extract.py submit <tid> '<json>'", file=sys.stderr)


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
        print("  step [--count N]     Print staged emails for LLM to identify job URLs", file=sys.stderr)
        print("  run [--count N]      Print staged emails for manual review", file=sys.stderr)
        print("  submit <tid> <json>  Submit job URLs. JSON: [{"url":"...","title":"...","company":"..."}]", file=sys.stderr)
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
