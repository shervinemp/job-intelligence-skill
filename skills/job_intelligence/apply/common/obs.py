"""obs — the always-on observation layer.

Every pipeline action emits a structured event: {run_id, seq, ts, jid,
actor, action, target, pre, post, outcome, detail}. Events stream to
~/.ji/state/session/<run_id>.jsonl — the machine-readable source of
truth for debugging and LLM observation. stderr prints remain for
humans; this log is the machine view.

Lessons baked in:
  - ALWAYS on (no debug env flags — observability is the default).
  - Read-before-write: emit pre/post state around every mutation so a
    corrupted action is reconstructable.
  - Stage-tagged failures: outcome + detail carry the exact stage that
    failed (menu_closed vs no_option_match, etc.).

Usage:
    begin_run() at CLI start → obs(...) throughout → end_run() at exit.
"""
import json
import os
import time

from lib.config import JI_HOME

_state = {"run_id": "", "seq": 0, "fh": None, "path": ""}


def session_dir():
    d = os.path.join(JI_HOME, "state", "session")
    os.makedirs(d, exist_ok=True)
    return d


def begin_run(name="", jid=""):
    """Start a run's event log. Returns (run_id, path)."""
    run_id = time.strftime("%Y%m%d_%H%M%S")
    if name:
        run_id += f"_{name}"
    if jid:
        run_id += f"_{jid[:12]}"
    path = os.path.join(session_dir(), run_id + ".jsonl")
    _state["run_id"] = run_id
    _state["seq"] = 0
    _state["path"] = path
    _state["fh"] = open(path, "w", encoding="utf-8")
    obs("run", "begin", jid=jid, detail=name or "")
    return run_id, path


def end_run(outcome="ok", detail=""):
    obs("run", "end", outcome=outcome, detail=detail)
    try:
        _state["fh"].close()
    except Exception:
        pass
    _state["fh"] = None


def obs(actor, action, jid="", target="", pre=None, post=None,
        outcome="", detail=""):
    """Emit one event. `pre`/`post` are the read-before-write states."""
    _state["seq"] += 1
    ev = {
        "run_id": _state["run_id"],
        "seq": _state["seq"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "jid": jid or "",
        "actor": actor,
        "action": action,
        "target": _clip(pre if False else target, 200),
        "pre": _clip(pre, 200),
        "post": _clip(post, 200),
        "outcome": _clip(outcome, 120),
        "detail": _clip(detail, 400),
    }
    try:
        _state["fh"].write(json.dumps(ev) + "\n")
        _state["fh"].flush()
    except Exception:
        pass
    return ev


def _clip(v, n):
    if v is None:
        return ""
    if isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    return s[:n]


def load(run_id=None):
    """Load events: latest run when run_id is None. Returns list of dicts."""
    d = session_dir()
    if not os.path.isdir(d):
        return []
    files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))
    if not files:
        return []
    path = os.path.join(d, run_id + ".jsonl") if run_id else os.path.join(d, files[-1])
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
