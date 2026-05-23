"""extract.py — Auto-extract URLs from staged emails, SLM admits/rejects."""

import json
import os
import re
import sys

from lib.db import stage_list_all, stage_count, setting_get, setting_set
from lib.db import add_job, pipeline_status, get_conn, advance

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_IDS_KEY = "extracted_ids"

_SKIP_DOMAINS = {
    "linkedin.com/comm", "linkedin.com/feed", "linkedin.com/notifications",
    "linkedin.com/mynetwork", "linkedin.com/messaging", "t1.em.linkedin.com",
    "accounts.google.com", "github.com", "google.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "unsubscribe", "user-subscription",
}


def _extract_urls(content):
    urls = set()
    for m in re.finditer(r'https?://[^\s<>"\')\]]+', content):
        url = m.group(0).rstrip('.,;:!?)>\'"]')
        skip = any(d in url.lower() for d in _SKIP_DOMAINS)
        if not skip and len(url) > 20:
            urls.add(url)
    return list(urls)


def _snippet(content, url):
    idx = content.lower().find(url.lower())
    if idx == -1:
        return ""
    start = max(0, idx - 80)
    end = min(len(content), idx + len(url) + 80)
    s = content[start:end]
    s = re.sub(r'\s+', ' ', s).strip()
    return f"...{s}..."


def cmd_auto():
    pending_ids = set(setting_get(EXTRACTED_IDS_KEY, []))
    all_staged = stage_list_all()
    pending = [(tid, content) for tid, content in all_staged if tid not in pending_ids]
    if not pending:
        print("NO_PENDING_STAGED", file=sys.stderr)
        return

    total = 0
    extracted_ids = list(pending_ids)
    for tid, content in pending:
        urls = _extract_urls(content)
        if not urls:
            extracted_ids.append(tid)
            continue
        for url in urls:
            jid = add_job({"url": url, "email_id": tid, "source": "Email", "source_url": url})
            if jid:
                ctx = _snippet(content, url)
                print(f"JOB:{jid}:{url}  [{ctx}]")
                total += 1
        extracted_ids.append(tid)
    setting_set(EXTRACTED_IDS_KEY, extracted_ids)
    print(f"EXTRACTED:{total}", file=sys.stderr)


def cmd_admit(*jids):
    conn = get_conn()
    for jid in jids:
        conn.execute("UPDATE jobs SET stage='extracted' WHERE id=? AND stage='extracted'", (jid,))
    conn.commit()
    print(f"ADMITTED:{len(jids)}", file=sys.stderr)


def cmd_reject(*jids):
    conn = get_conn()
    for jid in jids:
        conn.execute("UPDATE jobs SET stage='skipped' WHERE id=?", (jid,))
    conn.commit()
    print(f"REJECTED:{len(jids)}", file=sys.stderr)


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
    c = get_conn()
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("DELETE FROM events")
    c.execute("DELETE FROM job_documents")
    c.execute("DELETE FROM jobs")
    c.execute("DELETE FROM companies")
    c.execute("DELETE FROM stages")
    c.execute("DELETE FROM search_threads")
    c.execute("PRAGMA foreign_keys=ON")
    c.commit()
    setting_set(EXTRACTED_IDS_KEY, [])
    setting_set("staged_ids", [])
    setting_set("skipped_ids", [])
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


def main():
    subcommands = {"submit", "reset", "status", "admit", "reject", "review"}
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
        elif cmd == "admit":
            cmd_admit(*sys.argv[2:])
        elif cmd == "reject":
            cmd_reject(*sys.argv[2:])
        elif cmd == "review":
            count = 3
            if "--count" in sys.argv:
                i = sys.argv.index("--count")
                if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                    count = int(sys.argv[i + 1])
            cmd_review(count)
    elif len(sys.argv) == 1 or sys.argv[1].startswith("--"):
        cmd_auto()
    else:
        print(f"Unknown subcommand: {sys.argv[1]}", file=sys.stderr)
        print("Usage: python3 extract.py [--count N] | submit | reset | status | admit | reject", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
