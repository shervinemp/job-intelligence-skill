#!/usr/bin/env python3
"""scripts/backfill_ledger.py — seed the fill ledger from existing dossiers.

The ledger (lib/db/fills.py) only starts collecting from the next fill
run, but ~/.ji/results/*/handoffs/ already holds every field decision the
pipeline has ever made. Replaying them gives wrong-fill rate a real
denominator today instead of in a fortnight.

OPT-IN and idempotent-ish by design:
  * it is a separate script, never called by the pipeline;
  * --dry-run (default) reports what it would insert and writes nothing;
  * --commit performs the insert;
  * it refuses to run twice unless --force, because the ledger is an
    append-only log and a double replay would silently double every
    denominator — a corrupted metric is worse than a missing one.

Usage:
    python3 scripts/backfill_ledger.py              # report only
    python3 scripts/backfill_ledger.py --commit
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dossiers(results_dir):
    """Newest dossier per job: handoffs/<ts>.json, else handoff.json."""
    out = []
    for jdir in sorted(glob.glob(os.path.join(results_dir, "*"))):
        if not os.path.isdir(jdir):
            continue
        jid = os.path.basename(jdir)
        hist = sorted(glob.glob(os.path.join(jdir, "handoffs", "*.json")))
        path = hist[-1] if hist else os.path.join(jdir, "handoff.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                out.append((jid, path, json.load(f)))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  SKIP {jid}: unreadable dossier ({e})", file=sys.stderr)
    return out


def main():
    commit = "--commit" in sys.argv
    force = "--force" in sys.argv

    from lib.config import RESULTS_DIR
    from lib.db import get_conn
    from lib.db.fills import record_fills

    conn = get_conn()
    existing = conn.execute("SELECT COUNT(*) c FROM field_fills").fetchone()["c"]
    if existing and not force:
        print(f"REFUSED: field_fills already holds {existing} row(s). "
              f"Re-running would double every denominator. Use --force only "
              f"if you know the table is empty of backfilled rows.",
              file=sys.stderr)
        return 1

    known = {r["id"] for r in conn.execute("SELECT id FROM jobs")}
    docs = _dossiers(RESULTS_DIR)
    total = orphan = 0
    per_job = []
    for jid, path, d in docs:
        fields = d.get("fields") or []
        if not fields:
            continue
        if jid not in known:
            # field_fills.job_id is a FK to jobs — a dossier for a deleted
            # job cannot be recorded. Report it rather than fail the run.
            orphan += 1
            continue
        per_job.append((jid, len(fields), d.get("url", ""), d.get("run_id", "")))
        total += len(fields)

    print(f"dossiers scanned : {len(docs)}", file=sys.stderr)
    print(f"jobs with fields : {len(per_job)}", file=sys.stderr)
    print(f"field decisions  : {total}", file=sys.stderr)
    if orphan:
        print(f"orphan dossiers  : {orphan} (job row deleted — skipped)",
              file=sys.stderr)

    if not commit:
        print("\nDRY RUN — nothing written. Re-run with --commit.",
              file=sys.stderr)
        return 0

    written = 0
    for jid, _n, url, run_id in per_job:
        for _j, _p, d in docs:
            if _j == jid:
                written += record_fills(jid, d.get("fields") or [],
                                        run_id=run_id or "backfill", url=url)
                break
    print(f"\nBACKFILLED: {written} field decision(s) into the ledger.",
          file=sys.stderr)
    print("  NEXT: report.py adjudicate    (then report.py wrongfill)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
