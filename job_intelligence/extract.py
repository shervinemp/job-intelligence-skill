"""extract.py — Review staged emails, pick job URLs, save to DB.

Usage:
  extract.py [--count N]        Show N staged emails to pick URLs from (default: 1)
  extract.py submit <tid> <json>  Save extracted jobs from an email
  extract.py clean               Filter non-job emails + auto-extract URLs
  extract.py reset               Clear all jobs and start fresh
  extract.py status              Pipeline status
"""

import json
import os
import re
import sys

from lib.db import stage_list_all, stage_count, setting_get, setting_set
from lib.db import add_job, pipeline_status

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_IDS_KEY = "extracted_ids"


def cmd_review(count):
    pending_ids = set(setting_get(EXTRACTED_IDS_KEY, []))
    all_staged = stage_list_all()
    pending = [(tid, content) for tid, content in all_staged if tid not in pending_ids]
    if not pending:
        print("ALL_EXTRACTED", file=sys.stderr)
        return
    pending = pending[:count]
    for tid, content in pending:
        print(f"FILE {tid}", file=sys.stderr)
        print(f"---BEGIN EMAIL---", file=sys.stderr)
        print(content, file=sys.stderr)
        print(f"---END EMAIL---", file=sys.stderr)
    print("\n---\nRead the FILE content above. Identify job URLs, then call:", file=sys.stderr)
    print("  python3 extract.py submit <tid> '<json>'", file=sys.stderr)


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
    print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_reset():
    from lib.db import get_conn
    c = get_conn()
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("DELETE FROM events")
    c.execute("DELETE FROM job_documents")
    c.execute("DELETE FROM jobs")
    c.execute("DELETE FROM companies")
    c.execute("DELETE FROM stages")
    c.execute("PRAGMA foreign_keys=ON")
    c.commit()
    setting_set(EXTRACTED_IDS_KEY, [])
    setting_set("staged_ids", [])
    import shutil
    res_dir = os.path.join(SKILL_DIR, "results")
    if os.path.exists(res_dir):
        shutil.rmtree(res_dir)
    print("Reset complete. Staged emails ready for fresh extraction.", file=sys.stderr)


def cmd_status():
    s = pipeline_status()
    p = s['staged']['total'] - s['staged']['pending']
    print(f"Staged: {s['staged']['total']} | Extracted: {p} | Pending: {s['staged']['pending']}", file=sys.stderr)
    for stage in ["extracted", "described", "tailored", "applied", "skipped", "failed"]:
        c = s["stages"].get(stage, 0)
        if c:
            print(f"  {stage}: {c}", file=sys.stderr)
    if s["auth_walls"]["count"]:
        domains = " ".join(s["auth_walls"]["domains"])
        print(f"  auth walls: {s['auth_walls']['count']} ({domains})", file=sys.stderr)
    print(f"  next: {s['next_step']}", file=sys.stderr)


_URL_PATTERNS = [
    r'https?://(?:www\.)?jobright\.ai/jobs/info/[a-zA-Z0-9]+',
    r'https?://(?:www\.)?linkedin\.com/jobs/view/\d+',
    r'https?://(?:www\.)?linkedin\.com/comm/jobs/view/\d+',
    r'https?://(?:www\.)?indeed\.com/viewjob\?[^"\s>]+',
    r'https?://(?:www\.)?careerbeacon\.com/job/\d+',
    r'https?://[^"\s>]*teamtailor[^"\s>]+',
    r'https?://[^"\s>]*jobs2web[^"\s>]+',
]


def cmd_clean():
    all_staged = stage_list_all()
    pending_ids = set(setting_get(EXTRACTED_IDS_KEY, []))
    pending = [(tid, content) for tid, content in all_staged if tid not in pending_ids]
    if not pending:
        print("ALL_EXTRACTED", file=sys.stderr)
        return
    filtered = 0
    total = 0
    extracted_ids = list(pending_ids)
    for tid, content in pending:
        if not re.search(r'\b(job|jobs)\b', content.lower()):
            filtered += 1
            extracted_ids.append(tid)
            continue
        urls = set()
        for pat in _URL_PATTERNS:
            for m in re.finditer(pat, content):
                urls.add(m.group(0).rstrip(')'))
        if not urls:
            extracted_ids.append(tid)
            continue
        count = 0
        for url in urls:
            if add_job({"url": url, "email_id": tid, "source": "Email", "source_url": url}):
                count += 1
        total += count
        extracted_ids.append(tid)
        print(f"  {tid}: {count} jobs", file=sys.stderr)
    setting_set(EXTRACTED_IDS_KEY, extracted_ids)
    print(f"\nFiltered: {filtered} non-job | Extracted: {total} jobs", file=sys.stderr)


def main():
    subcommands = {"submit", "reset", "status", "clean"}
    if len(sys.argv) > 1 and sys.argv[1] in subcommands:
        cmd = sys.argv[1]
        if cmd == "submit":
            if len(sys.argv) < 4:
                print("Usage: python3 extract.py submit <tid> '<json>'", file=sys.stderr)
                sys.exit(1)
            cmd_submit(sys.argv[2], sys.argv[3])
        elif cmd == "reset":
            cmd_reset()
        elif cmd == "status":
            cmd_status()
        elif cmd == "clean":
            cmd_clean()
    elif len(sys.argv) == 1 or sys.argv[1].startswith("--"):
        count = 3
        if "--count" in sys.argv:
            i = sys.argv.index("--count")
            if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
                print("Warning: --count requires a number, using default 3", file=sys.stderr)
            else:
                count = int(sys.argv[i + 1])
        cmd_review(count)
    else:
        print(f"Unknown subcommand: {sys.argv[1]}", file=sys.stderr)
        print("Usage: python3 extract.py [--count N] | submit <tid> '<json>' | reset | status", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
