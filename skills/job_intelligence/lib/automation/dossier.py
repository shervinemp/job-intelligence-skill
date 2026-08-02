"""dossier — the standard handover format.

One dossier shape across the whole agent: {jid, url, mode, ts, error,
summary, fields, blockers, decisions, artifacts}. Written atomically to
<results_dir>/<jid>/handoff.json with a timestamped history (last 5) for
run-diffing. Consumers: the orchestrator (LLM) and report views.

Domain-free: callers supply their own field/blocker/decision payloads.
"""
import json
import os
import time


def write_dossier(jid, results_dir, *, summary, fields, blockers=None,
                  decisions=None, artifacts=None, mode="unknown",
                  error="", url="", run_id="", keep_history=5):
    """Write the dossier + history. Returns the handoff.json path."""
    d = os.path.join(results_dir, str(jid))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "handoff.json")

    handoff = {
        "jid": jid, "url": url, "mode": mode,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_id": run_id or "",  # link back to the event timeline
        "error": error,
        "summary": summary,
        "fields": fields,
        "blockers": blockers or [],
        "decisions": decisions or [],
        "artifacts": artifacts or {},
    }
    try:
        from lib.config import atomic_write_json
        atomic_write_json(path, handoff)
    except Exception:
        pass
    # Timestamped history so consecutive runs can be diffed.
    try:
        hist_dir = os.path.join(d, "handoffs")
        os.makedirs(hist_dir, exist_ok=True)
        hist_path = os.path.join(hist_dir, time.strftime("%Y%m%d_%H%M%S") + ".json")
        with open(hist_path, "w", encoding="utf-8") as hf:
            json.dump(handoff, hf, indent=2)
        for old in sorted(os.listdir(hist_dir))[:-keep_history]:
            os.remove(os.path.join(hist_dir, old))
    except Exception:
        pass
    return path


def merge_check(jid, results_dir, *, passed, errors, warnings, infos):
    """Merge pre-submit check results into the latest dossier so the
    handoff stays current after check runs. Best-effort; no history
    entry (check is a revision of the same run)."""
    try:
        path = os.path.join(results_dir, str(jid), "handoff.json")
        with open(path, encoding="utf-8") as f:
            h = json.load(f)
        h["check"] = {
            "passed": bool(passed),
            "errors": errors or [],
            "warnings": warnings or [],
            "infos": infos or [],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        from lib.config import atomic_write_json
        atomic_write_json(path, h)
    except Exception:
        pass
