"""apply/shadow.py — Observability-only batch runner for tailored jobs.

Runs the full auto pipeline (detect → navigate → fill → check → submit)
with JI_APPLY_MODE=shadow forced, so NOTHING is ever submitted and no
job state is mutated (expired-job rejection is live-only). Every outcome
is recorded in a resumable JSONL log at ~/.ji/state/shadow_run.jsonl.

Usage:
    python apply.py shadow                          All tailored jobs
    python apply.py shadow --jid <jid> [--jid ...]  Specific jobs
    python apply.py shadow --limit N                Cap this run (resumable)
    python apply.py shadow --quick                  Deterministic-only fill
"""
import json
import os
import sys
import time

from lib.config import JI_HOME

os.environ["JI_APPLY_MODE"] = "shadow"
os.environ.setdefault("JI_CAPTCHA_TIMEOUT", "60")

LOG_PATH = os.path.join(JI_HOME, "state", "shadow_run.jsonl")


class _Tee:
    """Duplicate stderr to a transcript file so DIAG lines (fill failures,
    truncations, rejected values) are retrievable after the run."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


_TRANSCRIPT = os.path.join(JI_HOME, "state",
                           f"shadow_transcript_{time.strftime('%Y%m%d_%H%M%S')}.log")


def _load_done():
    done = {}
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done[rec["jid"]] = rec
                except Exception:
                    continue
    return done


def _append(rec):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def run(jids=None, limit=None, quick=False):
    from lib.db import get_jobs_by_stage
    from apply.auto import _process_one

    os.makedirs(os.path.dirname(_TRANSCRIPT), exist_ok=True)
    _tf = open(_TRANSCRIPT, "w", encoding="utf-8")
    sys.stderr = _Tee(sys.stderr, _tf)
    print(f"TRANSCRIPT: {_TRANSCRIPT}", file=sys.stderr)

    if jids:
        from lib.db import get_job
        jobs = []
        for jid in jids:
            job = get_job(jid)
            if not job:
                print(f"ERROR: job {jid} not found", file=sys.stderr)
                continue
            jobs.append((jid, job))
    else:
        jobs = get_jobs_by_stage("tailored")

    done = _load_done()
    todo = [(jid, job) for jid, job in jobs if jid not in done]
    print(f"TOTAL: {len(jobs)}, already recorded: {len(done)}, to do: {len(todo)}",
          file=sys.stderr)
    if not todo:
        print("ALL_DONE — nothing left to shadow-run", file=sys.stderr)
        return 0

    processed = 0
    for jid, job in todo:
        if limit is not None and processed >= limit:
            break
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        rec = {"jid": jid, "title": (job.get("title") or "?")[:50],
               "company": (job.get("company") or "?")[:25],
               "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        t0 = time.time()
        print(f"\n=== [{processed + 1}] {jid[:12]} {rec['title']} @ {rec['company']} ===",
              file=sys.stderr)
        try:
            _process_one(jid, job, quick=quick, max_pages=4, results=results)
            if results["already_applied"]:
                rec["outcome"] = "already_applied"
            elif results["submitted"]:
                rec["outcome"] = "submitted"
            elif results["stopped"]:
                rec["outcome"] = "stopped"
                rec["detail"] = results["stopped"][0][1][:120]
                if rec["detail"].startswith("submit returned 0"):
                    rec["outcome"] = "held_shadow"
                    rec["detail"] = "fill+check OK, submit held (shadow)"
            else:
                rec["outcome"] = "skipped"
                rec["detail"] = (results["skipped"][0][1][:120] if results["skipped"] else "?")
        except Exception as e:
            rec["outcome"] = "exception"
            rec["detail"] = str(e)[:150]

        # Capture pre-submit check errors (stored by cmd_check) so the
        # check-failed jobs are reviewable from the log without re-running.
        if rec["outcome"] in ("stopped", "exception"):
            try:
                from apply.common.page_helpers import load_state
                st = load_state()
                errs = st.get("check_errors") or []
                if errs:
                    rec["check_errors"] = [
                        {"label": e.get("label", "")[:60],
                         "reason": (e.get("reason") or "")[:80]}
                        for e in errs[:8]
                    ]
            except Exception:
                pass

        rec["secs"] = round(time.time() - t0)
        rec["shadow"] = True

        # Regression canary: compare with the previous fill run's dossier.
        # A field that WAS filled and now fails means a recent change
        # broke something — surfaced loudly instead of silently.
        try:
            from lib.report import _load_handoffs, compare_handoffs
            hs = _load_handoffs(jid)
            if len(hs) >= 2:
                d = compare_handoffs(hs[0], hs[1])
                if d["regressed"]:
                    rec["regressed"] = [lbl for lbl, _now in d["regressed"]]
                    print("  *** REGRESSION *** fields that were filled and now fail:",
                          file=sys.stderr)
                    for lbl, now in d["regressed"]:
                        print(f"      - {lbl[:50]} -> {now}", file=sys.stderr)
                    print("      (investigate before relying on this run)",
                          file=sys.stderr)
                elif d["improved"]:
                    rec["improved"] = len(d["improved"])
        except Exception:
            pass

        _append(rec)
        print(f"    -> {rec['outcome']} {rec.get('detail', '')} ({rec['secs']}s)",
              file=sys.stderr)
        processed += 1
        time.sleep(1)

    print(f"\nDONE batch: processed {processed}, total recorded {len(_load_done())}/{len(jobs)}",
          file=sys.stderr)
    return 0
