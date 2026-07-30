"""lib/report.py — CLI for DB inspection, export, and pipeline management.

Usage:
  python3 report.py shell                     Open SQLite shell
  python3 report.py stats                     Pipeline statistics
  python3 report.py candidates [--limit N]    Tailored jobs ready to apply (with guard flags)
  python3 report.py inspect <jid>             Full job details
  python3 report.py search <query>            Search jobs
  python3 report.py export json [--stage S]   Export jobs as JSON
  python3 report.py export csv [--stage S]    Export jobs as CSV
  python3 report.py summary [--days N]        Recent activity digest
  python3 report.py companies [query]         List/search companies
  python3 report.py events [--upcoming]       List events
  python3 report.py contacts <jid>            Contacts for a job
  python3 report.py archive                   Archive state/registry entries for reset jobs
"""

import csv
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta

from .db import (
    DB_PATH, get_conn,
    load_state, get_job, search_jobs, job_count_by_stage,
    company_search, event_list, contact_list,
    desc_get, app_list, app_get,
)
from .config import STATE_PATH, REGISTRY_PATH, atomic_write_json


def cmd_shell():
    subprocess.run(["sqlite3", DB_PATH])


def cmd_stats():
    from lib.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT stage, state, COUNT(*) as cnt FROM jobs GROUP BY stage, state ORDER BY stage"
    ).fetchall()
    stages = ["extracted", "described", "tailored", "applied"]
    states = ["active", "rejected", "failed"]
    matrix = {st: {st2: 0 for st2 in states} for st in stages}
    for r in rows:
        if r["stage"] in matrix and r["state"] in states:
            matrix[r["stage"]][r["state"]] = r["cnt"]

    print(f"Total jobs: {conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]}")
    print()
    print(f"{'Stage/State':16s}", end="")
    for st in states:
        print(f"{st:>10s}", end="")
    print(f"{'Total':>8s}")
    print("-" * 50)
    total_by_stage = 0
    for stage in stages:
        row = matrix[stage]
        row_total = sum(row.values())
        print(f"{stage:16s}", end="")
        for st in states:
            print(f"{row[st]:>10d}", end="")
        print(f"{row_total:>8d}")
        total_by_stage += row_total
    print("-" * 50)
    print(f"{'Total':16s}", end="")
    grand = 0
    for st in states:
        c = sum(matrix[s][st] for s in stages)
        print(f"{c:>10d}", end="")
        grand += c
    print(f"{grand:>8d}")
    print()
    by_stage = job_count_by_stage()
    described = by_stage.get("described", 0)
    tailored = by_stage.get("tailored", 0)
    print(f"Need tailoring: {described}")
    print(f"Ready to apply: {tailored}")

    # Contact/outreach summary
    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    total_outreach = conn.execute("SELECT COUNT(*) FROM contacts WHERE reached_out=1").fetchone()[0]
    pending_email = conn.execute("SELECT COUNT(*) FROM contacts WHERE email_sent=0 AND email != '' AND email IS NOT NULL").fetchone()[0]
    pending_dm = conn.execute("SELECT COUNT(*) FROM contacts WHERE message_sent=0 AND linkedin_url != '' AND linkedin_url IS NOT NULL").fetchone()[0]
    if total_contacts:
        print()
        print(f"Contacts: {total_contacts}")
        print(f"  Reached out: {total_outreach}")
        print(f"  Pending email: {pending_email}")
        print(f"  Pending DM:    {pending_dm}")
        print(f"  NEXT: reach.py discover <jid>  OR  reach.py email <jid> --contact N")


def cmd_candidates(limit=None):
    """List tailored jobs ready to apply, surfacing guard-state flags
    (submit_clicked, no_apply_path, login_required) so the operator can
    decide what to run safely. Also shows the already-applied set for
    dedup reference."""
    from apply.detect import _classify
    from apply.common.registry import resolve as resolve_registry

    conn = get_conn()

    # Stage counts header
    print("STAGES:", end="")
    for r in conn.execute("SELECT stage, COUNT(*) n FROM jobs GROUP BY stage ORDER BY n DESC").fetchall():
        print(f"  {r['stage']}={r['n']}", end="")
    print()

    # Classify every tailored job for the summary breakdown
    all_tailored = conn.execute(
        "SELECT id, url, external_url FROM jobs WHERE stage='tailored' AND state='active'"
    ).fetchall()
    types = {}  # "easy_apply" -> count, "ats_direct(Workday)" -> count, etc.
    for r in all_tailored:
        url = r["url"] or ""
        ext_url = r["external_url"] or ""
        jtype, resolved_url = _classify(url, ext_url)
        if jtype == "ats_direct":
            reg = resolve_registry(resolved_url or url)
            label = f"ats_direct({reg.name})" if reg else "ats_direct(?)"
        elif jtype == "external":
            reg = resolve_registry(ext_url or resolved_url or url)
            label = f"external({reg.name})" if reg else "external(?)"
        else:
            label = jtype
        types[label] = types.get(label, 0) + 1
    type_summary = ", ".join(f"{k}={v}" for k, v in sorted(types.items()))
    print(f"\nTAILORED: {type_summary}")

    # Tailored with external URLs — candidates to apply
    q = ("SELECT id, title, company, url, external_url, state FROM jobs "
         "WHERE stage='tailored' AND external_url IS NOT NULL AND external_url != '' "
         "ORDER BY company")
    rows = conn.execute(q).fetchall()
    if limit:
        rows = rows[:limit]
    print(f"\nEXTERNAL ({len(rows)} — require Playwright pipeline):")
    for r in rows:
        state = r["state"] or ""
        flags = []
        if "submit_clicked" in state:
            flags.append("SUBMIT_CLICKED")
        if "no_apply_path" in state:
            flags.append("NO_APPLY_PATH")
        if "login_required" in state:
            flags.append("LOGIN_REQ")
        flag_str = "  [" + ", ".join(flags) + "]" if flags else ""
        ext_url = r["external_url"] or ""
        reg = resolve_registry(ext_url)
        tag = f" ({reg.name})" if reg else ""
        url_display = ext_url[:48]
        print(f"  {r['id'][:14]} {_clean(r['company'] or '')[:20]:20} "
              f"{_clean(r['title'] or '')[:34]:34} {url_display}{tag}{flag_str}")

    # Tailored without external URL — LinkedIn Easy Apply
    easy = conn.execute(
        "SELECT id, title, company, state FROM jobs "
        "WHERE stage='tailored' AND (external_url IS NULL OR external_url = '') AND state='active' "
        "ORDER BY company"
    ).fetchall()
    if easy:
        print(f"\nEASY_APPLY ({len(easy)} — LinkedIn modal):")
        for r in easy[:20]:
            state = r["state"] or ""
            flags = []
            if "submit_clicked" in state:
                flags.append("SUBMIT_CLICKED")
            if "no_apply_path" in state:
                flags.append("NO_APPLY_PATH")
            flag_str = "  [" + ", ".join(flags) + "]" if flags else ""
            print(f"  {r['id'][:14]} {_clean(r['company'] or '')[:22]:22} "
                  f"{_clean(r['title'] or '')[:40]}{flag_str}")
        if len(easy) > 20:
            print(f"  ... +{len(easy) - 20} more")

    # Already-applied set (dedup reference)
    applied = conn.execute(
        "SELECT title, company FROM jobs WHERE stage='applied' ORDER BY company"
    ).fetchall()
    print(f"\nALREADY APPLIED ({len(applied)}) — dedup reference:")
    for r in applied[:12]:
        print(f"  {_clean(r['company'] or '')[:22]:22} {_clean(r['title'] or '')[:40]}")
    if len(applied) > 12:
        print(f"  ... +{len(applied) - 12} more")



def cmd_inspect(jid):
    job = get_job(jid)
    if not job:
        print(f"Job not found: {jid}", file=sys.stderr)
        return
    print(f"{'-'*60}")
    print(f"  JOB: {_clean(job.get('title', ''))}")
    print(f"  AT:  {_clean(job.get('company', ''))}")
    print(f"{'-'*60}")
    for k in ["id", "email_id", "location", "url", "source_url", "salary",
              "salary_min", "salary_max", "salary_currency", "remote_status",
               "job_type", "department", "source", "stage", "state", "fit_score",
              "error", "created_at", "updated_at", "applied_at"]:
        v = job.get(k)
        if v:
            print(f"  {k:20s} {v}")
    print()
    desc = desc_get(jid)
    if desc:
        print(f"  Description: {len(desc)} chars")
        print(f"  {desc[:300]}...")
        print()
    apps = app_list(jid)
    if apps:
        print(f"  Application files ({len(apps)}):")
        for a in apps:
            content = app_get(jid, a["filename"])
            sz = len(content) if content else 0
            print(f"    {a['filename']:30s} {sz} chars")
    contacts = contact_list(job_id=jid)
    if contacts:
        print(f"\n  Contacts ({len(contacts)}):")
        for c in contacts:
            print(f"    {c['name']:20s} {c['role'] or ''}")
    rname = job.get("recruiter_name", "")
    rurl = job.get("recruiter_url", "")
    if rname:
        print(f"\n  Recruiter: {rname}" + (f"  {rurl}" if rurl else ""))


def cmd_search(query):
    results = search_jobs(query)
    if not results:
        print(f"No jobs matching '{query}'")
        return
    print(f"Found {len(results)} jobs:")
    for j in results:
        stage = j.get("stage", "?")
        state = j.get("state", "?")
        title = _clean(j.get("title", ""))[:50]
        company = _clean(j.get("company", ""))[:30]
        print(f"  [{stage:12s}/{state:10s}] {j['id']} {title} @ {company}")


def cmd_export(fmt, stage=None):
    s = load_state()
    jobs = list(s["jobs"].values())
    if stage:
        jobs = [j for j in jobs if j.get("stage") == stage]
    if fmt == "json":
        print(json.dumps(jobs, indent=2, ensure_ascii=False, default=str))
    elif fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        keys = ["id", "title", "company", "location", "url", "salary",
                "salary_min", "salary_max", "remote_status", "source",
                "stage", "state", "fit_score", "created_at", "applied_at"]
        w.writerow(keys)
        for j in jobs:
            w.writerow([j.get(k, "") for k in keys])
        print(out.getvalue().strip())


def cmd_summary(days=7):
    since = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_conn()
    new_jobs = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE created_at >= ?", (since,)
    ).fetchone()["c"]
    updated = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE updated_at >= ?", (since,)
    ).fetchone()["c"]
    applied = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE applied_at >= ?", (since,)
    ).fetchone()["c"]
    events = conn.execute(
        "SELECT COUNT(*) as c FROM events WHERE created_at >= ? OR event_at >= ?",
        (since, since),
    ).fetchone()["c"]
    print(f"Summary (last {days} days):")
    print(f"  New jobs:     {new_jobs}")
    print(f"  Updated:      {updated}")
    print(f"  Applied:      {applied}")
    print(f"  Events:       {events}")
    if events:
        print()
        for e in event_list(upcoming=True):
            if e.get("event_at", "") >= since:
                print(f"  [{e.get('event_type')}] {e.get('job_title','')[:40]} @ {e.get('job_company','')[:20]} - {e.get('event_at','')}")


def _clean(s):
    return re.sub(r'[\u200b\u200c\u200d\ufffe\ufeff]', '', s).strip()


def cmd_companies(query=None):
    conn = get_conn()
    if query:
        results = company_search(query)
    else:
        results = [dict(r) for r in conn.execute(
            "SELECT * FROM companies ORDER BY name LIMIT 50"
        ).fetchall()]
    if not results:
        print("No companies found")
        return
    print(f"Companies ({len(results)}):")
    for c in results:
        jc = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE company=?", (c["name"],)).fetchone()["c"]
        name = _clean(c["name"])[:30]
        ind = _clean(c.get("industry", "") or "")[:20]
        print(f"  {name:30s} {ind} ({jc} jobs)")


def cmd_events(upcoming=False):
    events = event_list(upcoming=upcoming)
    if not events:
        print("No events" if not upcoming else "No upcoming events")
        return
    for e in events:
        status = "[x]" if e.get("completed") else "[ ]"
        job_info = f" [{e.get('job_title','')} @ {e.get('job_company','')}]" if "job_title" in e else ""
        print(f"  {status} [{e.get('event_type')}] {e.get('title','')}{job_info}")
        if e.get("event_at"):
            print(f"     at {e['event_at']}")
        if e.get("description"):
            print(f"     {e['description'][:100]}")


def cmd_contacts(jid=None):
    contacts = contact_list(job_id=jid)
    if not contacts:
        print("No contacts" if not jid else f"No contacts for {jid}")
        return
    for c in contacts:
        reached = "mail" if c.get("reached_out") else "[ ]"
        print(f"  {reached} {c['name']:20s} {c.get('role','') or '':25s} {c.get('email','') or ''}")


def cmd_archive():
    """Move state/registry entries for reset jobs to archive files (preserves history)."""
    conn = get_conn()
    existing = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
    total = 0
    for path, label in [(STATE_PATH, "apply_state.json"), (REGISTRY_PATH, "page_registry.json")]:
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        stale = {jid: data[jid] for jid in data if jid not in existing}
        if not stale:
            continue
        archive_path = path.replace(".json", "_archive.json")
        archive = {}
        try:
            with open(archive_path) as f:
                archive = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        archive.update(stale)
        atomic_write_json(archive_path, archive)
        for jid in stale:
            del data[jid]
        atomic_write_json(path, data)
        print(f"  {label}: archived {len(stale)} entries ({len(data)} remain)", file=sys.stderr)
        total += len(stale)
    if total:
        print(f"Archived {total} stale entries.", file=sys.stderr)
    else:
        print("No stale entries.", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "shell":
        cmd_shell()
    elif cmd == "stats":
        cmd_stats()
    elif cmd == "candidates":
        lim = None
        if "--limit" in args:
            i = args.index("--limit")
            if i + 1 < len(args):
                lim = int(args[i + 1])
        cmd_candidates(limit=lim)
    elif cmd == "inspect":
        if not args:
            print("Usage: python3 report.py inspect <jid>", file=sys.stderr)
            sys.exit(1)
        cmd_inspect(args[0])
    elif cmd == "search":
        if not args:
            print("Usage: python3 report.py search <query>", file=sys.stderr)
            sys.exit(1)
        cmd_search(" ".join(args))
    elif cmd == "export":
        fmt = args[0] if args else "json"
        stage = None
        if "--stage" in args:
            i = args.index("--stage")
            if i + 1 < len(args):
                stage = args[i + 1]
        cmd_export(fmt, stage)
    elif cmd == "summary":
        days = 7
        if "--days" in args:
            i = args.index("--days")
            if i + 1 < len(args):
                days = int(args[i + 1])
        cmd_summary(days)
    elif cmd == "companies":
        cmd_companies(" ".join(args) if args else None)
    elif cmd == "events":
        cmd_events(upcoming="--upcoming" in args)
    elif cmd == "contacts":
        cmd_contacts(args[0] if args else None)
    elif cmd == "archive":
        cmd_archive()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
