"""extract.py — Auto-extract URLs from staged emails, SLM admits/rejects."""

import hashlib
import json
import os
import re
import sys
import shutil

from lib.config import RESULTS_DIR, SNAPSHOTS_DIR, STATE_PATH, REGISTRY_PATH
from lib.db import stage_list_all, stage_count, setting_get, setting_set
from lib.db import add_job, pipeline_status, get_conn

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_IDS_KEY = "extracted_ids"


def _clean_state_entry(path, jids):
    """Remove state/registry entries for given JIDs from a JSON file."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    changed = False
    for jid in jids:
        if jid in data:
            del data[jid]
            changed = True
    if changed:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)

_SKIP_DOMAINS = {
    "linkedin.com/comm", "linkedin.com/feed", "linkedin.com/notifications",
    "linkedin.com/mynetwork", "linkedin.com/messaging", "linkedin.com/company",
    "t1.em.linkedin.com",
    "accounts.google.com", "github.com", "google.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "unsubscribe", "user-subscription",
    "ca.indeed.com/notifications", "ca.indeed.com/?", "messages.indeed.com", "engage.indeed.com",
    "jobright.ai", "searchalert.action.azurecomm.net",
    "workinottawa.investottawa.ca/privacy-policy",
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


def _load_categories():
    path = os.path.join(SKILL_DIR, "categories.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"Can't read categories.json: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_auto():
    pending_ids = set(setting_get(EXTRACTED_IDS_KEY, []))
    all_staged = stage_list_all()
    pending = [(tid, content) for tid, content in all_staged if tid not in pending_ids]
    if not pending:
        print("NO_PENDING_STAGED", file=sys.stderr)
        return

    total = 0
    extracted_ids = list(pending_ids)
    conn = get_conn()
    for tid, content in pending:
        urls = _extract_urls(content)
        if not urls:
            extracted_ids.append(tid)
            continue
        for url in urls:
            expected_jid = hashlib.md5(url.encode()).hexdigest()[:16]
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (expected_jid,)).fetchone():
                continue
            jid = add_job({"url": url, "email_id": tid, "source": "Email", "source_url": url})
            if jid:
                ctx = _snippet(content, url)
                print(f"REAL JOB? (admit/reject)", file=sys.stderr)
                print(f"JOB:{jid}:{url}  [{ctx}]")
                total += 1
        extracted_ids.append(tid)
    setting_set(EXTRACTED_IDS_KEY, extracted_ids)
    print(f"EXTRACTED:{total}", file=sys.stderr)


def cmd_admit(*jids, category=None, notes=None):
    cats = _load_categories()
    conn = get_conn()
    for item in jids:
        if len(item) != 16 or not all(c in '0123456789abcdef' for c in item):
            print(f"Invalid JID: '{item}'. JIDs are 16 hex characters.", file=sys.stderr)
            print("Usage: extract.py admit --category <name> <jid> [jid...]", file=sys.stderr)
            sys.exit(1)
    for jid in jids:
        row = conn.execute("SELECT stage, category FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            continue
        current_cat = row["category"]
        if current_cat is None and not category:
            print(f"ERROR: --category required for first admit. Options: {', '.join(cats)}", file=sys.stderr)
            print("  See 'extract.py help' for usage", file=sys.stderr)
            sys.exit(1)
        if category:
            if category not in cats:
                print(f"Unknown category '{category}'. Options: {', '.join(cats)}", file=sys.stderr)
                sys.exit(1)
            conn.execute("UPDATE jobs SET category=? WHERE id=?", (category, jid))
        if notes is not None:
            conn.execute("UPDATE jobs SET notes=? WHERE id=?", (notes, jid))
    conn.commit()
    print(f"ADMITTED:{len(jids)}", file=sys.stderr)


def cmd_skip(*jids):
    conn = get_conn()
    count = 0
    for jid in jids:
        c = conn.execute("UPDATE jobs SET stage='skipped' WHERE id=?", (jid,))
        if c.rowcount:
            count += 1
    conn.commit()
    if count:
        print(f"SKIP:{count}", file=sys.stderr)
        print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


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
    print("  python3 extract.py submit [<tid>] '<json>'", file=sys.stderr)


def cmd_submit(tid, jobs_json=None):
    if jobs_json is None:
        jobs_json = tid
        tid = "manual"
    if isinstance(jobs_json, str):
        jobs = json.loads(jobs_json)
    if not isinstance(jobs, list):
        jobs = [jobs]
    cats = _load_categories()
    for job in jobs:
        if not job.get("url"):
            continue
        if not job.get("category"):
            print(f"JSON must include 'category'. Options: {', '.join(cats)}", file=sys.stderr)
            sys.exit(1)
        if job["category"] not in cats:
            print(f"Unknown category '{job['category']}'. Options: {', '.join(cats)}", file=sys.stderr)
            sys.exit(1)
    created = 0
    updated = 0
    for job in jobs:
        if not job.get("url"):
            continue
        job["email_id"] = tid
        job["source"] = "Email" if tid != "manual" else "Manual"
        job["source_url"] = job.get("url", "")
        jid_candidate = hashlib.md5(job["url"].encode()).hexdigest()[:16]
        existing = get_conn().execute("SELECT 1 FROM jobs WHERE id=?", (jid_candidate,)).fetchone()
        jid = add_job(job)
        if jid and not existing:
            created += 1
        if jid and existing and "notes" in job:
            updated += 1
    if tid != "manual":
        extracted_ids = setting_get(EXTRACTED_IDS_KEY, [])
        if tid not in extracted_ids:
            extracted_ids.append(tid)
            setting_set(EXTRACTED_IDS_KEY, extracted_ids)
    if created:
        print(f"CREATED:{created}", file=sys.stderr)
    if updated:
        print(f"UPDATED:{updated}", file=sys.stderr)
    print(f"  NEXT: {pipeline_status()['next_step']}", file=sys.stderr)


def cmd_reset(*jids, confirm=False):
    conn = get_conn()
    if not jids:
        if not confirm:
            print("ERROR: mass reset requires --confirm. This deletes ALL jobs, results, and pipeline state.", file=sys.stderr)
            sys.exit(1)
        c = conn
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
        if os.path.exists(RESULTS_DIR):
            shutil.rmtree(RESULTS_DIR)
        # Clean state and registry files entirely on mass reset
        for p in (STATE_PATH, REGISTRY_PATH):
            if os.path.exists(p):
                os.remove(p)
        print("Reset complete. Staged emails ready for fresh extraction.", file=sys.stderr)
    else:
        email_ids_to_remove = set()
        for jid in jids:
            if len(jid) != 16 or not all(c in '0123456789abcdef' for c in jid):
                print(f"Invalid JID: '{jid}'. JIDs are 16 hex characters.", file=sys.stderr)
                continue
            row = conn.execute("SELECT email_id FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                print(f"Job not found: {jid}", file=sys.stderr)
                continue
            email_id = row["email_id"]
            conn.execute("DELETE FROM job_documents WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM events WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM contacts WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM jobs WHERE id=?", (jid,))
            if email_id:
                email_ids_to_remove.add(email_id)
            # Clean up files: results dir, screenshots, state/registry entries
            jid_dir = os.path.join(RESULTS_DIR, jid)
            if os.path.exists(jid_dir):
                shutil.rmtree(jid_dir)
            for ext in (".png", ".html"):
                screenshot = str(SNAPSHOTS_DIR.parent / "screenshots" / f"inspect_{jid}{ext}")
                if os.path.exists(screenshot):
                    os.remove(screenshot)
        conn.commit()
        if email_ids_to_remove:
            extracted_ids = set(setting_get(EXTRACTED_IDS_KEY, []))
            extracted_ids -= email_ids_to_remove
            setting_set(EXTRACTED_IDS_KEY, list(extracted_ids))
        # Remove from state and registry
        _clean_state_entry(STATE_PATH, set(jids))
        _clean_state_entry(REGISTRY_PATH, set(jids))
        print(f"RESET:{len(jids)}", file=sys.stderr)
        s = pipeline_status()
        if s["next_step"]:
            print(f"  NEXT: {s['next_step']}", file=sys.stderr)


def cmd_status():
    s = pipeline_status()
    p = s['staged']['total'] - s['staged']['pending']
    print(f"Staged: {s['staged']['total']} | Extracted: {p} | Pending: {s['staged']['pending']}", file=sys.stderr)
    for stage in ["extracted", "described", "tailored", "applied", "skipped", "failed"]:
        c = s["stages"].get(stage, 0)
        if c:
            print(f"  {stage}: {c}", file=sys.stderr)
    conn = get_conn()
    cat_rows = conn.execute("SELECT category, COUNT(*) FROM jobs WHERE category IS NOT NULL GROUP BY category").fetchall()
    uncat = conn.execute("SELECT COUNT(*) FROM jobs WHERE category IS NULL").fetchone()[0]
    if cat_rows or uncat:
        parts = [f"{r['category']}: {r['COUNT(*)']}" for r in cat_rows]
        if uncat:
            parts.append(f"uncategorized: {uncat}")
        print(f"  categories: {', '.join(parts)}", file=sys.stderr)
    if s["auth_walls"]["count"]:
        domains = " ".join(s["auth_walls"]["domains"])
        print(f"  auth walls: {s['auth_walls']['count']} ({domains})", file=sys.stderr)
    print(f"  next: {s['next_step']}", file=sys.stderr)


def cmd_help():
    cats = _load_categories()
    print("Usage:", file=sys.stderr)
    print("  admit --category <name> <jid> [jid...]     Acknowledge + set category. First admit requires --category.", file=sys.stderr)
    print("                                              --notes optional for context.", file=sys.stderr)
    print("  reject <jid> [jid...]                      Skip", file=sys.stderr)
    print("  submit [<tid>] '<json>'                    JSON must include 'category'", file=sys.stderr)
    print("  review [--count N]                         Show emails for manual picking", file=sys.stderr)
    print("  status                                     Pipeline state", file=sys.stderr)
    print("  reset <jid> [jid...]                       Delete specific job, re-extract on next run", file=sys.stderr)
    print("  reset (no args, requires --confirm)         DANGER: deletes ALL jobs, results, and pipeline state", file=sys.stderr)
    print("  help                                       This message", file=sys.stderr)
    print("", file=sys.stderr)
    print("Categories:", file=sys.stderr)
    for name, info in cats.items():
        gem = info.get("gem", "none")
        desc = info.get("desc", "")
        print(f"  {name} → {gem}" + (f" ({desc})" if desc else ""), file=sys.stderr)
    print("", file=sys.stderr)
    print("Note: --category required on first admit. Use enrich.py admit --category to override after seeing JD.", file=sys.stderr)


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="extract.py", description="Extract job URLs from staged emails")
    parser.add_argument("--count", type=int, default=3, help="Jobs to review (default 3)")
    
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("auto", help="Auto-extract URLs from staged emails")
    submit_p = sub.add_parser("submit", help="Submit a job entry")
    submit_p.add_argument("tid", nargs="?")
    submit_p.add_argument("json_data", nargs="?")
    reset_p = sub.add_parser("reset", help="Reset extraction state")
    reset_p.add_argument("args", nargs="*")
    reset_p.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("status", help="Pipeline state")
    admit_p = sub.add_parser("admit", help="Admit an extracted job")
    admit_p.add_argument("jids", nargs="+")
    admit_p.add_argument("--category", help="Job category (required on first admit)")
    admit_p.add_argument("--notes", help="Job notes/context")
    sub.add_parser("reject", help="Reject an extracted job").add_argument("jids", nargs="+")
    sub.add_parser("review", help="Review extracted jobs for admit/reject")
    sub.add_parser("help", help="This message")

    args = parser.parse_args()
    
    if args.command == "submit":
        if args.tid and args.json_data:
            cmd_submit(args.tid, args.json_data)
        elif args.tid:
            cmd_submit(args.tid)
        else:
            parser.print_help()
    elif args.command == "reset":
        cmd_reset(*args.args, confirm=getattr(args, "confirm", False))
    elif args.command == "status":
        cmd_status()
    elif args.command == "admit":
        cmd_admit(*args.jids, category=args.category, notes=args.notes)
    elif args.command == "reject":
        cmd_reject(*args.jids)
    elif args.command == "review":
        cmd_review(args.count)
    elif args.command == "auto" or args.command is None:
        cmd_auto()
    elif args.command == "help":
        cmd_help()
    else:
        cmd_auto()

if __name__ == "__main__":
    main()
