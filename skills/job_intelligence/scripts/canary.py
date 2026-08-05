#!/usr/bin/env python3
"""canary.py — soft-gate live shadow canary (AGENTS.md protocol step 3).

After any hot-path change, run ONE live shadow fill on a chosen job, compare
its dossier against the previous run (the regression canary), and check the
upload state. This is the verification step that unit tests cannot provide —
it exercises the REAL browser/ATS, catching hydration races, DOM changes, and
silent missing uploads.

SOFT gate by design: it REPORTS a verdict and exit code, but a live browser +
network + ATS-side changes can fail for reasons unrelated to the change. The
exit code is advisory — the human/orchestrator decides whether to block.

Usage:
  python3 scripts/canary.py [--jid JID] [--quick] [--limit N]
    --jid     a specific tailored job (default: pick the first READY with a
              prior dossier so a regression comparison is possible)
    --quick   deterministic-only shadow (no vision)
    --limit   cap jobs (default 1)

Checks:
  1. SHADOW_RAN — the fill ran without a hard crash.
  2. REGRESSION — fields that were filled last run and now fail (via
     lib/report.compare_handoffs against the prior dossier).
  3. UPLOAD — a file input was observed but no upload placed → upload_pending.
  4. HYDRATION — a hydration-stale selector was detected and re-resolved
     (reported, not an error — recovery is the fix working).
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _pick_canary_job():
    """A tailored-active job with a prior dossier, so regression compare works."""
    sys.path.insert(0, os.getcwd())
    try:
        from lib.config import RESULTS_DIR
        from lib.db import get_conn
        from apply.common.hydration import is_hydration_stale_selector
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, title, company FROM jobs "
            "WHERE stage='tailored' AND state='active' ORDER BY updated_at DESC"
        ).fetchall()
        for r in rows:
            jid = r["id"]
            h = os.path.join(RESULTS_DIR, jid, "handoff.json")
            hist = os.path.join(RESULTS_DIR, jid, "handoffs")
            if os.path.exists(h) and os.path.isdir(hist) and len(os.listdir(hist)) >= 1:
                return jid, r["title"], r["company"]
    except Exception as e:
        print(f"CANARY_PICK_FAIL: {e}", file=sys.stderr)
    return None, None, None


def _prior_dossier(jid):
    from lib.config import RESULTS_DIR
    from lib.automation.diff import load_handoffs
    hs = load_handoffs(jid, RESULTS_DIR)
    return hs[1] if len(hs) >= 2 else (hs[0] if hs else None)


def main():
    ap = argparse.ArgumentParser(prog="canary.py")
    ap.add_argument("--jid", help="specific job (default: pick READY w/ prior dossier)")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--limit", type=int, default=1)
    args = ap.parse_args()

    jid = args.jid
    if not jid:
        jid, _t, _c = _pick_canary_job()
    if not jid:
        print("CANARY_IDLE: no tailored job with a prior dossier — "
              "nothing to compare against")
        return 0

    sys.path.insert(0, os.getcwd())
    from lib.config import RESULTS_DIR
    from lib.report import compare_handoffs
    from lib.automation.diff import load_handoffs

    prior = _prior_dossier(jid)
    print(f"CANARY: {jid}  (prior dossier: {'yes' if prior else 'no'})",
          file=sys.stderr)

    # Run the live shadow. The shadow log skips already-recorded jobs, so
    # remove this jid first (AGENTS protocol step 3: re-run after removing
    # from the log) so the canary actually re-fills against current code.
    shadow_log = os.path.expanduser("~/.ji/state/shadow_run.jsonl")
    if os.path.exists(shadow_log):
        try:
            lines = open(shadow_log, encoding="utf-8").read().strip().splitlines()
            kept = [l for l in lines if jid not in l]
            open(shadow_log, "w", encoding="utf-8").write(
                "\n".join(kept) + ("\n" if kept else ""))
            if len(kept) != len(lines):
                print(f"  (removed {jid} from shadow log for canary re-run)",
                      file=sys.stderr)
        except Exception as e:
            print(f"  CANARY_LOG_WARN: could not clear shadow log: {e}",
                  file=sys.stderr)

    cmd = [sys.executable, "apply.py", "shadow", "--jid", jid, "--limit",
           str(args.limit)]
    if args.quick:
        cmd.append("--quick")
    r = subprocess.run(cmd, cwd=os.path.join(os.path.dirname(__file__), ".."))
    ran_ok = (r.returncode == 0)
    print(f"SHADOW_RAN: {'yes' if ran_ok else 'NO (exit %d)' % r.returncode}",
          file=sys.stderr)

    # Read the fresh dossier.
    h = os.path.join(RESULTS_DIR, jid, "handoff.json")
    fresh = None
    if os.path.exists(h):
        fresh = json.load(open(h, encoding="utf-8"))
    else:
        print("CANARY_WARN: no dossier after shadow run — fill may have "
              "failed before writing", file=sys.stderr)

    issues = []
    if fresh:
        # 2. regression
        if prior:
            d = compare_handoffs(fresh, prior)
            if d["regressed"]:
                for lbl, now in d["regressed"]:
                    issues.append(f"REGRESSION: {lbl} was filled, now {now}")
                print("  REGRESSED:", file=sys.stderr)
                for lbl, now in d["regressed"]:
                    print(f"    - {lbl} -> {now}", file=sys.stderr)
            else:
                print("  no regressions vs prior dossier", file=sys.stderr)
        # 3. upload
        for b in (fresh.get("blockers") or []):
            if b.get("type") == "upload_pending":
                issues.append("UPLOAD_PENDING: file input observed but no "
                              "upload placed")
                print("  UPLOAD_PENDING (blocker present)", file=sys.stderr)
        # 4. hydration
        recovered = 0
        for f in (fresh.get("fields") or []):
            if f.get("diag", {}).get("hydration_recovered"):
                recovered += 1
        if recovered:
            print(f"  HYDRATION_RECOVERED: {recovered} field(s) re-resolved",
                  file=sys.stderr)
        # also flag stale selectors that did NOT recover (still a gap)
        stale_unrecovered = 0
        for f in (fresh.get("fields") or []):
            if f.get("diag", {}).get("reason") == "hydration_stale":
                stale_unrecovered += 1
        if stale_unrecovered:
            issues.append(f"HYDRATION_STALE: {stale_unrecovered} field(s) "
                          "could not re-resolve")
            print(f"  HYDRATION_STALE: {stale_unrecovered} unrecovered",
                  file=sys.stderr)

    verdict = "FAIL" if issues else "PASS"
    print(f"\nCANARY_VERDICT: {verdict}  ({len(issues)} issue(s))",
          file=sys.stderr)
    for i in issues:
        print(f"  - {i}", file=sys.stderr)
    # LOOK FIRST: when there are issues, point the orchestrator at the live
    # page evidence so it diagnoses by looking, not by pattern-matching the
    # reason strings. The status says WHAT failed; the page says WHY.
    if issues:
        print("\n  LOOK FIRST — read the page, not the strings:",
              file=sys.stderr)
        # fresh inspect evidence for this jid (best-effort)
        ss = os.path.join(RESULTS_DIR, jid, "..", "screenshots",
                          f"inspect_{jid}.jpg")
        if os.path.exists(ss):
            print(f"    IMG:  {ss}  (vision — is the modal open? login wall? "
                  f"a different form?)", file=sys.stderr)
        print(f"    HTML: {os.path.join(RESULTS_DIR, jid, 'handoff.json')} "
              f"(dossier — what failed)",
              file=sys.stderr)
        print(f"    RUN:  python3 apply.py act --inspect {jid}  "
              f"(fresh screenshot + DOM dump)", file=sys.stderr)
    # SOFT gate: exit 0 even on issues — the orchestrator/human decides. The
    # issue list is the report.
    return 0 if ran_ok else 1


if __name__ == "__main__":
    sys.exit(main())
