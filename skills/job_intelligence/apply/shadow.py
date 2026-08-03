"""apply/shadow.py — Observability-only batch runner for tailored jobs.

Runs the full auto pipeline (detect → navigate → fill → check → submit)
with JI_APPLY_MODE=shadow forced, so NOTHING is ever submitted and no
job state is mutated (expired-job rejection is live-only). Every outcome
is recorded in a resumable JSONL log at ~/.ji/state/shadow_run.jsonl.

Robustness model (best-of-all-worlds):
- Each job runs in a SUBPROCESS (apply/shadow_worker.py) — a hard crash
  (Playwright/CDP native fault) can never take down the batch.
- Per-job wall-clock budget kills hung children (a stuck page can't
  stall the fleet).
- A crashed job is RE-PROBED ONCE — a second, evidence-bearing attempt
  before it's recorded as a crash.
- PYTHONFAULTHANDLER + PYTHONUNBUFFERED make silent deaths impossible:
  native crashes write tracebacks, and no evidence is lost to buffering.
- Per-job transcripts isolate evidence: ~/.ji/state/shadow_jobs/{jid}.log

Usage:
    python apply.py shadow                          All tailored jobs
    python apply.py shadow --jid <jid> [--jid ...]  Specific jobs
    python apply.py shadow --limit N                Cap this run (resumable)
    python apply.py shadow --quick                  Deterministic-only fill
"""
import json
import os
import subprocess
import sys
import time

from lib.config import JI_HOME

os.environ["JI_APPLY_MODE"] = "shadow"
os.environ.setdefault("JI_CAPTCHA_TIMEOUT", "60")

LOG_PATH = os.path.join(JI_HOME, "state", "shadow_run.jsonl")
JOBS_DIR = os.path.join(JI_HOME, "state", "shadow_jobs")
JOB_TIMEOUT = int(os.environ.get("JI_SHADOW_JOB_TIMEOUT", "600"))


class _Tee:
    """Duplicate stderr to a transcript file (flushed per write — a kill
    never loses evidence)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
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
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABORT_AFTER_CONSECUTIVE_FAILS = 3


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
    """Record a job's outcome, REPLACING any older entries for the same
    job — the log is a per-job LATEST-state map."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    lines = []
    if os.path.exists(LOG_PATH):
        for line in open(LOG_PATH, encoding="utf-8"):
            try:
                if json.loads(line).get("jid") == rec["jid"]:
                    continue
            except Exception:
                continue
            lines.append(line)
    lines.append(json.dumps(rec) + "\n")
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("".join(lines))


def _run_worker(jid, quick=False):
    """Run one job in a subprocess, streaming output to a per-job
    transcript. Returns (rc, transcript_path, out_text, timed_out).

    The per-job timeout must bound SILENT hangs too: the stdout reader
    lives in a thread, so a worker that prints nothing is still killed
    when the wall-clock budget expires."""
    import threading
    os.makedirs(JOBS_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    tlog = os.path.join(JOBS_DIR, f"{jid[:12]}_{ts}.log")
    env = dict(os.environ,
               JI_APPLY_MODE="shadow",
               JI_CAPTCHA_TIMEOUT=os.environ.get("JI_CAPTCHA_TIMEOUT", "60"),
               PYTHONFAULTHANDLER="1",
               PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-m", "apply.shadow_worker", jid]
    if quick:
        cmd.append("--quick")
    env = dict(env, JI_NO_LOCK="1")
    # Clear any STALE outcome file from a previous run of this job —
    # otherwise the supervisor would read yesterday's verdict for
    # today's (possibly crashed) run.
    try:
        stale = os.path.join(JOBS_DIR, f"{jid}.outcome.json")
        if os.path.exists(stale):
            os.remove(stale)
    except Exception:
        pass
    t0 = time.time()
    chunks = []
    state = {"rc": None}

    def _reader(p, f):
        try:
            for line in iter(p.stdout.readline, b""):
                f.write(line)
                f.flush()
                chunks.append(line)
        except Exception:
            pass
        try:
            state["rc"] = p.wait()
        except Exception:
            state["rc"] = -1

    timed_out = False
    try:
        with open(tlog, "wb") as f:
            p = subprocess.Popen(cmd, cwd=_SKILL_DIR, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            thr = threading.Thread(target=_reader, args=(p, f), daemon=True)
            thr.start()
            while state["rc"] is None and time.time() - t0 < JOB_TIMEOUT:
                time.sleep(0.5)
            if state["rc"] is None:
                timed_out = True
                try:
                    p.kill()
                except Exception:
                    pass
                f.write(f"SHADOW: per-job timeout ({JOB_TIMEOUT}s) — killed\n"
                        .encode())
                f.flush()
                thr.join(timeout=10)
            rc = state["rc"] if state["rc"] is not None else -9
    except Exception as e:
        chunks.append(f"SHADOW: supervisor error {e}\n".encode())
        rc = -1
        timed_out = False
    out = b"".join(chunks).decode(errors="replace")
    return rc, tlog, out, timed_out


def _load_worker_outcome(jid):
    """The AUTHORITATIVE outcome: the worker's atomic outcome file.
    Returns the record dict, or None when the worker never finished
    (crash) — the supervisor then falls back to stdout evidence."""
    try:
        path = os.path.join(JOBS_DIR, f"{jid}.outcome.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) and rec.get("outcome") else None
    except Exception:
        return None


def _parse_worker(out):
    rec = {}
    for line in out.splitlines():
        if line.startswith("OUTCOME="):
            rec["outcome"] = line[len("OUTCOME="):].strip()
        elif line.startswith("DETAIL="):
            rec["detail"] = line[len("DETAIL="):].strip()
        elif line.startswith("SECS="):
            rec["secs"] = line[len("SECS="):].strip()
        elif line.startswith("CHECK_ERRORS="):
            try:
                rec["check_errors"] = json.loads(line[len("CHECK_ERRORS="):])
            except Exception:
                pass
    return rec


def _regression_canary(jid, rec):
    """Compare with the previous fill run's dossier. A field that WAS
    filled and now fails means a recent change broke something."""
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


def _probe_llm_availability():
    """One cheap, bounded availability ping recorded in the batch header
    so every outcome in the run is interpretable (escape hatches open or
    closed) without per-field archaeology."""
    try:
        from lib.automation.llm import mode as _llm_mode, _KIND_AUTO
        from lib.ask_api import available as _avail
        import socket
        ok = False
        try:
            ok = bool(_avail())
        except Exception:
            ok = False
        return {"mode": _llm_mode(), "api_available": ok,
                "auto_defaults": {k: v for k, v in _KIND_AUTO.items()}}
    except Exception:
        return {"mode": "?", "api_available": False}


def run(jids=None, limit=None, quick=False, recheck=False):
    from lib.db import get_jobs_by_stage

    # Recheck mode: re-run only the unconfirmed-skip queue (see the
    # cause-tagging below) — the jobs most likely to be session variance
    # rather than genuinely closed postings. Their log entries are
    # removed first so the resumable logic actually re-runs them.
    if recheck:
        _done = _load_done()
        _unconf = [jid for jid, rec in _done.items()
                   if rec.get("unconfirmed") or rec.get("recheck")]
        if not _unconf:
            print("RECHECK: no unconfirmed skips in the log — nothing to re-examine",
                  file=sys.stderr)
            return 0
        print(f"RECHECK: {len(_unconf)} unconfirmed skip(s) queued", file=sys.stderr)
        lines = []
        if os.path.exists(LOG_PATH):
            for line in open(LOG_PATH, encoding="utf-8"):
                try:
                    if json.loads(line).get("jid") in _unconf:
                        continue
                except Exception:
                    continue
                lines.append(line)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("".join(lines))
        jids = list(dict.fromkeys(_unconf + list(jids or [])))

    # The batch holds the pipeline lock for its whole run (children run
    # with JI_NO_LOCK=1) — a concurrent manual run can't interleave the
    # shared apply_state.json mid-batch. Stale locks self-heal.
    try:
        from lib.chrome_manager import init as _init
        _init()
    except RuntimeError as _lk:
        print(f"ERROR: {_lk}", file=sys.stderr)
        return 1
    except Exception:
        pass

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
    _llmhdr = _probe_llm_availability()
    _api = "up" if _llmhdr.get("api_available") else "DOWN"
    print(f"LLM: mode={_llmhdr.get('mode')} api={_api} — "
          f"escape hatches: vision={_llmhdr['auto_defaults'].get('vision')} "
          f"option_pick={_llmhdr['auto_defaults'].get('option_pick')} "
          f"gap_fill={_llmhdr['auto_defaults'].get('gap_fill')} "
          f"(batch_verify/verify_reads/auto_retry off by default)",
          file=sys.stderr)
    if not _llmhdr.get("api_available"):
        print("  NOTE: ask_api unavailable — escape-hatch fields will be "
              "recorded as unassisted; orchestrator review required.",
              file=sys.stderr)
    # Profile readiness gate: the fleet fails identically on missing
    # work_history/contact — surface it BEFORE burning browser time.
    try:
        from apply.preflight import preflight, fmt_manifest
        _pf = preflight()
        if _pf["hard_missing"]:
            print("PREFLIGHT WARNING — profile incomplete:", file=sys.stderr)
            print(fmt_manifest(_pf), file=sys.stderr)
            print("  The fleet will fail identically on these gaps. "
                  "Fix profile.json or accept the predicted failures.",
                  file=sys.stderr)
        elif _pf["answer_gaps"] or _pf["soft_missing"]:
            print(f"PREFLIGHT: coverage={_pf['coverage_pct']}% "
                  f"answer_gaps={len(_pf['answer_gaps'])} "
                  f"soft_missing={len(_pf['soft_missing'])}", file=sys.stderr)
    except Exception:
        pass
    if not todo:
        print("ALL_DONE — nothing left to shadow-run", file=sys.stderr)
        return 0

    processed = 0
    consec_fails = 0
    for jid, job in todo:
        if limit is not None and processed >= limit:
            break
        rec = {"jid": jid, "title": (job.get("title") or "?")[:50],
               "company": (job.get("company") or "?")[:25],
               "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        t0 = time.time()
        print(f"\n=== [{processed + 1}] {jid[:12]} {rec['title']} @ {rec['company']} ===",
              file=sys.stderr)

        rc, tlog, out, timed_out = _run_worker(jid, quick=quick)
        fout = _load_worker_outcome(jid)
        if fout:
            # Authoritative record from the worker's atomic file — the
            # stdout parse is only a fallback for evidence details.
            parsed = fout
            crashed = False
        else:
            parsed = _parse_worker(out)
            crashed = rc != 0 or not parsed.get("outcome") or parsed.get("outcome") == "error"

        if crashed and rc == 0:
            # Worker reported an error outcome (job not found etc.) — not
            # a crash, but nothing to classify either.
            rec["outcome"] = "error"
            rec["detail"] = parsed.get("detail", "?")
            rec["secs"] = round(time.time() - t0)
            rec["shadow"] = True
            rec["transcript"] = tlog
            rec["exit_code"] = rc
            _append(rec)
            print(f"    -> {rec['outcome']} {rec.get('detail', '')} ({rec['secs']}s)",
                  file=sys.stderr)
            processed += 1
            time.sleep(1)
            continue

        if timed_out:
            # Slow job, not a crash — record the timeout as-is; a
            # re-probe would just burn another budget on the same job.
            rec["outcome"] = "timeout"
            rec["detail"] = f"killed after {JOB_TIMEOUT}s"
            rec["transcript"] = tlog
            rec["exit_code"] = rc
            _regression_canary(jid, rec)
            rec["secs"] = int(rec.get("secs") or round(time.time() - t0))
            rec["shadow"] = True
            _append(rec)
            print(f"    -> {rec['outcome']} {rec.get('detail', '')} ({rec['secs']}s)",
                  file=sys.stderr)
            processed += 1
            consec_fails += 1
            if _abort_if_unhealthy(consec_fails):
                break
            time.sleep(1)
            continue

        if crashed:
            # Crash — re-probe ONCE with full evidence before recording.
            print("  CRASH — re-probing once...", file=sys.stderr)
            rc2, tlog2, out2, timed_out2 = _run_worker(jid, quick=quick)
            parsed2 = _parse_worker(out2)
            if rc2 == 0 and parsed2.get("outcome") and parsed2.get("outcome") != "error":
                rec.update(parsed2)
                rec["after_crash"] = True
                rec["first_attempt"] = {
                    "exit_code": rc, "transcript": tlog,
                    "tail": "\n".join(out.splitlines()[-6:])[:400],
                }
            else:
                rec["outcome"] = "timeout" if timed_out2 else "crash"
                rec["detail"] = parsed.get("detail", "no OUTCOME emitted")
                rec["exit_code"] = rc
                rec["transcript"] = tlog2
                rec["tail"] = "\n".join(out2.splitlines()[-8:])[:600]
        else:
            rec.update(parsed)

        if not rec.get("outcome"):
            rec["outcome"] = "crash"
            rec["detail"] = "no OUTCOME emitted"
            rec["exit_code"] = rc
            rec["transcript"] = tlog

        # Cause-tagging: an unconfirmed no-apply-path skip is a LIVE
        # question (cookie/session variance), not a dead posting — it
        # must be re-examined, never silently forgotten.
        if rec.get("outcome") == "skipped" and "unconfirmed" in (rec.get("detail") or ""):
            rec["unconfirmed"] = True
            rec["recheck"] = True

        _regression_canary(jid, rec)
        rec["secs"] = int(rec.get("secs") or round(time.time() - t0))
        rec["shadow"] = True
        rec["transcript"] = rec.get("transcript") or tlog
        if rc == 0 and not rec.get("exit_code"):
            rec["exit_code"] = 0

        _append(rec)
        print(f"    -> {rec['outcome']} {rec.get('detail', '')} ({rec['secs']}s)",
              file=sys.stderr)
        processed += 1
        if rec["outcome"] in ("crash", "timeout"):
            consec_fails += 1
        else:
            consec_fails = 0
        if _abort_if_unhealthy(consec_fails):
            break
        time.sleep(1)

    print(f"\nDONE batch: processed {processed}, total recorded {len(_load_done())}/{len(jobs)}",
          file=sys.stderr)
    return 0


def _abort_if_unhealthy(consec_fails):
    """Abort the batch when the environment is unhealthy (Chrome/CDP
    down, worker death spiral) instead of burning the whole fleet.
    Returns True when the run should stop."""
    if consec_fails >= ABORT_AFTER_CONSECUTIVE_FAILS:
        print(f"\nABORT: {ABORT_AFTER_CONSECUTIVE_FAILS} consecutive "
              f"crash/timeout — environment unhealthy (Chrome/CDP down?). "
              f"Fix and re-run; the log is resumable.", file=sys.stderr)
        return True
    return False
