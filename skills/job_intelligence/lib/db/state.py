"""lib/db/state.py — per-job state load/save."""

from .schema import STAGES, _JOBS_COLS, get_conn


def load_state():
    from .jobs import _row_to_job
    conn = get_conn()
    rows = conn.execute(f"SELECT {_JOBS_COLS} FROM jobs ORDER BY created_at").fetchall()
    jobs = {}
    for r in rows:
        d = _row_to_job(r)
        jobs[d["id"]] = d
    stage_rows = conn.execute(
        "SELECT stage, state, COUNT(*) as cnt FROM jobs GROUP BY stage, state"
    ).fetchall()
    stage_counts = {s: 0 for s in STAGES}
    state_counts = {"active": 0, "rejected": 0, "failed": 0}
    for sr in stage_rows:
        st = sr["stage"]
        if sr["state"] == "active" and st in stage_counts:
            stage_counts[st] += sr["cnt"]
        if sr["state"] in state_counts:
            state_counts[sr["state"]] += sr["cnt"]
    return {"jobs": jobs, "stages": stage_counts, "states": state_counts}


def save_state(state):
    from .jobs import _insert_job
    for jid, entry in state["jobs"].items():
        fields = {k: v for k, v in entry.items() if v is not None or k in ("stage", "state", "scripts", "notes")}
        _insert_job(jid, replace=True, commit=False, **fields)
    get_conn().commit()