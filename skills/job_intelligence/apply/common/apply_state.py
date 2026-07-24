"""Per-job apply state cleanup.

State is stored at ~/.ji/state/apply/{jid}.json.
Created on first act --fill, deleted on successful verify via clear().
"""
import os
from lib.config import STATE_DIR

APPLY_DIR = os.path.join(STATE_DIR, "apply")


def _path(jid):
    return os.path.join(APPLY_DIR, f"{jid}.json")


def clear(jid):
    """Delete state file for a job."""
    p = _path(jid)
    try:
        os.remove(p)
    except Exception:
        pass
