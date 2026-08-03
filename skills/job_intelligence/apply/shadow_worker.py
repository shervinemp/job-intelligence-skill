"""apply/shadow_worker.py — runs the full auto pipeline for ONE job.

Spawned by apply.py shadow as a SUBPROCESS per job, so a hard crash
(Playwright/CDP native fault) can never take down the batch supervisor.

The AUTHORITATIVE outcome is the structured file it writes as its very
last action: ~/.ji/state/shadow_jobs/{jid}.outcome.json. stdout lines
(OUTCOME=/DETAIL=/SECS=) are a human-readable mirror — the supervisor
reads the FILE, so partial/corrupt stdout can never misclassify a run.
A missing outcome file + non-zero exit = crash, deterministically.

Env: JI_APPLY_MODE=shadow is forced here (never mutates job state).
JI_NO_LOCK=1 is set by the supervisor — the batch holds the pipeline
lock, so children must not re-acquire it.
"""
import json
import os
import sys
import time

os.environ["JI_APPLY_MODE"] = "shadow"
os.environ.setdefault("JI_CAPTCHA_TIMEOUT", "60")


def _write_outcome(jid, rec):
    """Atomically write the authoritative outcome file (tmp + replace).
    A crash can never leave a partial file — the supervisor treats a
    missing file with a non-zero exit as a crash, deterministically."""
    try:
        from lib.config import JI_HOME
        outdir = os.path.join(JI_HOME, "state", "shadow_jobs")
        os.makedirs(outdir, exist_ok=True)
        target = os.path.join(outdir, f"{jid}.outcome.json")
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(tmp, target)
    except Exception:
        # Never let a failed outcome write flip the exit code — the
        # supervisor still sees rc and falls back to stdout evidence.
        pass


def main():
    jid = sys.argv[1] if len(sys.argv) > 1 else ""
    quick = "--quick" in sys.argv
    if not jid:
        _write_outcome("_nojid", {"outcome": "error", "detail": "no_jid"})
        return 2

    from lib.db import get_job
    job = get_job(jid)
    if not job:
        _write_outcome(jid, {"outcome": "error", "detail": "job_not_found"})
        return 2

    from apply.auto import _process_one
    results = {"submitted": [], "stopped": [], "skipped": [],
               "already_applied": []}
    t0 = time.time()
    try:
        _process_one(jid, job, quick=quick, max_pages=4, results=results)
        if results["already_applied"]:
            outcome, detail = "already_applied", ""
        elif results["submitted"]:
            outcome, detail = "submitted", str(results["submitted"][0][1])[:120]
        elif results["stopped"]:
            detail = str(results["stopped"][0][1])[:120]
            if detail.startswith("submit returned 0"):
                outcome, detail = "held_shadow", "fill+check OK, submit held (shadow)"
            else:
                outcome, detail = "stopped", detail
        else:
            outcome, detail = "skipped", (
                str(results["skipped"][0][1])[:120] if results["skipped"] else "?")
    except Exception as e:
        outcome, detail = "exception", str(e)[:150]

    # Capture pre-submit check errors (stored by cmd_check) so
    # check-failed jobs are reviewable without re-running.
    check_errors = []
    if outcome in ("stopped", "exception"):
        try:
            from apply.common.page_helpers import load_state
            st = load_state()
            errs = st.get("check_errors") or []
            check_errors = [
                {"label": e.get("label", "")[:60],
                 "reason": (e.get("reason") or "")[:80]}
                for e in errs[:8]]
        except Exception:
            pass

    rec = {
        "outcome": outcome,
        "detail": detail,
        "secs": round(time.time() - t0),
        "jid": jid,
    }
    if check_errors:
        rec["check_errors"] = check_errors
    _write_outcome(jid, rec)

    # Human-readable mirror on stdout (never the source of truth).
    print(f"OUTCOME={outcome}")
    print(f"DETAIL={detail}")
    print(f"SECS={round(time.time() - t0)}")
    if check_errors:
        print(f"CHECK_ERRORS={json.dumps(check_errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
