"""lib/db/pipeline.py — state-wrapper API (load/save/add/advance/...)."""

import hashlib

from . import schema


def load():
    from .state import load_state
    return load_state()


def save(state):
    from .state import save_state
    save_state(state)


def job_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:16] if url else None


def add(state, job_data):
    from .companies import company_upsert
    from .jobs import add_job, get_job
    jid = add_job(job_data)
    if jid:
        state["jobs"][jid] = get_job(jid)
        company = job_data.get("company", "")
        if company and company != "Unknown":
            try:
                company_upsert(company)
            except Exception:
                pass
    return jid


def advance(entry, new_stage, **updates):
    from .jobs import advance_job
    jid = entry.get("id")
    entry["stage"] = new_stage
    for k, v in updates.items():
        entry[k] = v
    advance_job(jid, new_stage, **updates)


def get_by_stage(state, stage):
    from .jobs import get_jobs_by_stage
    return get_jobs_by_stage(stage)


def next_pending(state):
    from .jobs import next_pending_job
    return next_pending_job()


def get_failed(state):
    from .jobs import get_failed_jobs
    return get_failed_jobs()


def pipeline_status():
    from ..auth_walls import count as auth_count, domains as auth_domains
    from .settings import setting_get
    from .state import load_state
    from .stages import stage_count

    state = load_state()
    staged_total = stage_count()
    extracted_ids = setting_get("extracted_ids", [])
    pending_staged = max(0, staged_total - len(extracted_ids))
    auth_n = auth_count()
    auth_d = auth_domains()

    next_step = ""
    if pending_staged > 0:
        next_step = "extract.py"
    elif state["stages"].get("extracted", 0) > 0:
        next_step = "enrich.py"
    elif state["stages"].get("described", 0) > 0:
        next_step = "tailor.py"
    elif state["stages"].get("tailored", 0) > 0:
        next_step = "apply.py detect <jid>"
    elif state["states"].get("failed", 0) > 0:
        next_step = "enrich.py retry or tailor.py retry"
    elif auth_n > 0:
        next_step = "enrich.py open"
    else:
        next_step = "all done — run gmail search"

    return {
        "jobs": len(state["jobs"]),
        "stages": state["stages"],
        "states": state["states"],
        "staged": {"total": staged_total, "pending": pending_staged},
        "auth_walls": {"count": auth_n, "domains": auth_d},
        "next_step": next_step,
    }


def close():
    if schema._conn:
        schema._conn.close()
        schema._conn = None