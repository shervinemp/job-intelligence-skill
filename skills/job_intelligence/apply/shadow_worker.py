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
from apply.common import terms as _T


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
        _write_outcome("_nojid", {"outcome": _T.OUTCOME_ERROR, "detail": "no_jid"})
        return 2

    from lib.db import get_job
    job = get_job(jid)
    if not job:
        _write_outcome(jid, {"outcome": _T.OUTCOME_ERROR, "detail": "job_not_found"})
        return 2

    from apply.auto import _process_one
    results = {_T.K_RES_SUBMITTED: [], _T.K_RES_STOPPED: [], _T.K_RES_SKIPPED: [],
               _T.K_RES_ALREADY_APPLIED: []}
    t0 = time.time()
    try:
        _process_one(jid, job, quick=quick, max_pages=4, results=results)
        if results[_T.K_RES_ALREADY_APPLIED]:
            outcome, detail = _T.OUTCOME_ALREADY_APPLIED, ""
        elif results[_T.K_RES_SUBMITTED]:
            outcome, detail = _T.OUTCOME_SUBMITTED, str(results[_T.K_RES_SUBMITTED][0][1])[:120]
        elif results[_T.K_RES_STOPPED]:
            detail = str(results[_T.K_RES_STOPPED][0][1])[:120]
            if detail.startswith("submit returned 0"):
                # rc==0 in shadow means: the pre-submit check PASSED and
                # the policy then suppressed the click. It must never be
                # asserted without the check actually having run — see the
                # gate ordering in act/submit.py. The remaining unknown is
                # unanswered OPTIONAL fields, which check does not fail on,
                # so the wording stays narrow.
                outcome = _T.OUTCOME_HELD_SHADOW
                detail = "check passed, submit held (shadow)"
            else:
                outcome, detail = _T.OUTCOME_STOPPED, detail
        else:
            outcome, detail = _T.OUTCOME_SKIPPED, (
                str(results[_T.K_RES_SKIPPED][0][1])[:120] if results[_T.K_RES_SKIPPED] else "?")
    except Exception as e:
        outcome, detail = _T.OUTCOME_EXCEPTION, str(e)[:150]

    # Capture pre-submit check errors (stored by cmd_check) so
    # check-failed jobs are reviewable without re-running.
    check_errors = []
    if outcome in (_T.OUTCOME_STOPPED, _T.OUTCOME_EXCEPTION):
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
