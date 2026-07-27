#!/usr/bin/env python3
"""auto.py — Autonomous apply pipeline orchestrator.

Walks every tailored job through: detect -> navigate -> fill -> check -> submit.
Stops a job when check fails (needs orchestrator input for unresolved fields).
Continues to the next job regardless.

Usage:
  python apply.py auto                    Process all tailored jobs
  python apply.py auto --jid <jid>        Process a single job
  python apply.py auto --dry-run          List what would be processed
  python apply.py auto --limit N          Cap at N jobs
  python apply.py auto --no-submit        Stop after check passes
  python apply.py auto --quick            Deterministic-only (no vision/Skyvern)
  python apply.py auto --max-pages N      Max form pages (default 4)
"""
import os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run(jid=None, limit=None, dry_run=False, quick=False, max_pages=4, no_submit=False):
    from lib.db import get_jobs_by_stage, get_job, get_conn

    if jid:
        job = get_job(jid)
        if not job:
            print(f"ERROR: job {jid} not found", file=sys.stderr)
            return 1
        jobs = [(jid, job)]
    else:
        jobs = get_jobs_by_stage("tailored")
        if not jobs:
            print("NO_TAILORED: no jobs ready to apply", file=sys.stderr)
            return 0

    if limit:
        jobs = jobs[:limit]

    N = len(jobs)
    print(f"\nAUTO: {N} job(s) to process (stage=tailored)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if dry_run:
        from apply.detect import _classify
        from apply.common.registry import resolve as resolve_registry
        for i, (jid, job) in enumerate(jobs):
            title = job.get("title", "?")
            company = job.get("company", "?")
            url = job.get("url", "")
            ext_url = job.get("external_url") or ""
            jtype, resolved_url = _classify(url, ext_url)
            reg = resolve_registry(resolved_url or ext_url or url)
            platform = reg.name if reg else ""
            tag = f" -> {platform}" if platform else ""
            print(f"  [{i+1}/{N}] {jid[:12]} {jtype}{tag} -- {title} @ {company}", file=sys.stderr)
        print(f"\nDRY_RUN: {N} job(s) listed. Remove --dry-run to process.", file=sys.stderr)
        return 0

    results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}

    for i, (jid, job) in enumerate(jobs):
        title = job.get("title", "?")
        company = job.get("company", "?")
        print(f"\n[{i+1}/{N}] {jid[:12]} -- {title} @ {company}", file=sys.stderr)
        print(f"{'-'*60}", file=sys.stderr)

        try:
            _process_one(jid, quick, max_pages, no_submit, results)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            results["skipped"].append((jid, f"exception: {e}"))

        if i < N - 1:
            time.sleep(2)

    _print_summary(results)
    return 0 if not results["skipped"] else 1


def _process_one(jid, quick, max_pages, no_submit, results):
    from apply.detect import run as detect_run
    from apply.navigate import run as navigate_run
    from apply.act.fill import cmd_fill
    from apply.act.check import cmd_check
    from apply.act.submit import cmd_submit
    from apply.common.page_helpers import load_state
    from lib.db import get_conn

    def _stage():
        row = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
        return row["stage"] if row else ""

    # Step 1: detect
    print(f"  detect...", file=sys.stderr, end=" ")
    rc = detect_run(jid)
    if rc != 0:
        results["skipped"].append((jid, "detect failed"))
        return

    state = load_state()
    jtype = state.get("type", "")
    print(f"type={jtype}", file=sys.stderr)

    if jtype == "already_applied":
        results["already_applied"].append((jid, "already applied"))
        return

    if jtype in ("unknown", ""):
        results["skipped"].append((jid, "unknown type -- can't classify URL"))
        print(f"  SKIP -- unknown type", file=sys.stderr)
        return

    # Step 2: navigate (only for external type)
    if jtype == "external":
        print(f"  navigate...", file=sys.stderr)
        rc = navigate_run(jid)
        if rc != 0:
            results["skipped"].append((jid, "navigate failed"))
            return

    # Step 3: fill
    print(f"  fill...", file=sys.stderr)
    rc = cmd_fill(jid, answers=None, verify=not quick,
                  max_pages=max_pages, quick=quick)
    if rc != 0:
        if _stage() == "applied":
            results["already_applied"].append((jid, "already applied"))
            return
        results["skipped"].append((jid, "fill failed"))
        return

    if _stage() == "applied":
        results["already_applied"].append((jid, "already applied"))
        return

    # Step 4: check
    print(f"  check...", file=sys.stderr)
    rc = cmd_check(jid)
    if rc != 0:
        results["stopped"].append((jid, "check failed -- supply answers and retry"))
        print(f"  STOPPED -- check failed, orchestrator review needed", file=sys.stderr)
        return

    print(f"  check passed", file=sys.stderr)

    # Step 5: submit
    if no_submit:
        results["stopped"].append((jid, "check passed -- ready to submit"))
        print(f"  READY -- --no-submit, run: python apply.py act --submit {jid}", file=sys.stderr)
        return

    print(f"  submit...", file=sys.stderr)
    rc = cmd_submit(jid, confirm=True)
    if rc != 0:
        if _stage() == "applied":
            results["submitted"].append((jid, "submitted"))
            return
        results["skipped"].append((jid, "submit failed"))
        return

    if _stage() == "applied":
        results["submitted"].append((jid, "submitted"))
    else:
        results["stopped"].append((jid, "submit returned 0 but stage not applied"))


def _print_summary(results):
    print(f"\n{'='*60}", file=sys.stderr)
    n_sub = len(results["submitted"])
    n_stop = len(results["stopped"])
    n_skip = len(results["skipped"])
    n_already = len(results["already_applied"])
    print(f"SUMMARY: {n_sub} submitted, {n_stop} stopped (review), "
          f"{n_skip} skipped, {n_already} already applied", file=sys.stderr)

    if results["submitted"]:
        print(f"\n  SUBMITTED:", file=sys.stderr)
        for jid, detail in results["submitted"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)

    if results["stopped"]:
        print(f"\n  STOPPED (orchestrator review needed):", file=sys.stderr)
        for jid, detail in results["stopped"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)
        print(f"\n  To resolve: review fill output above, supply --answers, then:", file=sys.stderr)
        print(f"    python apply.py act --fill <jid> --answers '{{...}}'", file=sys.stderr)
        print(f"    python apply.py act --submit <jid>", file=sys.stderr)

    if results["skipped"]:
        print(f"\n  SKIPPED:", file=sys.stderr)
        for jid, detail in results["skipped"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)

    if results["already_applied"]:
        print(f"\n  ALREADY APPLIED:", file=sys.stderr)
        for jid, detail in results["already_applied"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)
