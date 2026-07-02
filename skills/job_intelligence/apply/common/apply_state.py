"""Multi-page form state tracker — enables crash recovery mid-apply.

State is stored at ~/.ji/state/apply/{jid}.json.
Created on first act --fill, deleted on successful verify.

Schema:
{
  "jid": "abc123",
  "started_at": "iso",
  "updated_at": "iso",
  "page_current": 3,
  "page_total": 7,
  "page_history": [
    {"page": 1, "fields": {"name": "value"}, "status": "done"}
  ],
  "submitted": false,
  "url": "https://...",
  "ats_type": "workday"
}
"""
import json, os, time
from lib.config import STATE_DIR

APPLY_DIR = os.path.join(STATE_DIR, "apply")


def _path(jid):
    return os.path.join(APPLY_DIR, f"{jid}.json")


def load(jid):
    """Load state for a job. Returns None if missing or corrupt."""
    p = _path(jid)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def save(jid, data):
    """Save state atomically."""
    os.makedirs(APPLY_DIR, exist_ok=True)
    data["updated_at"] = time.time()
    p = _path(jid)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def clear(jid):
    """Delete state file for a job."""
    p = _path(jid)
    try:
        os.remove(p)
    except Exception:
        pass


def init(jid, url, ats_type, page_total=0):
    """Create initial state for a job."""
    now = time.time()
    data = {
        "jid": jid,
        "started_at": now,
        "updated_at": now,
        "page_current": 1,
        "page_total": page_total,
        "page_history": [],
        "submitted": False,
        "url": url,
        "ats_type": ats_type,
    }
    save(jid, data)
    return data


def record_fill(jid, page, fields_filled):
    """Record that a page was filled."""
    state = load(jid)
    if not state:
        return
    existing = [p for p in state["page_history"] if p["page"] == page]
    if existing:
        existing[0]["fields"] = fields_filled
        existing[0]["status"] = "done"
    else:
        state["page_history"].append({"page": page, "fields": fields_filled, "status": "done"})
    save(jid, state)


def advance_page(jid):
    """Increment page_current and return new page number."""
    state = load(jid)
    if not state:
        return None
    state["page_current"] += 1
    save(jid, state)
    return state["page_current"]


def mark_submitted(jid):
    """Mark the job as submitted in state."""
    state = load(jid)
    if not state:
        return
    state["submitted"] = True
    save(jid, state)
