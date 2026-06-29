"""policy.py — Apply-pipeline submission policy (live / shadow / hold).

Source of truth: apply_policy.json in JI_HOME. `JI_APPLY_MODE` env overrides the
file; a per-run override (e.g. `act --shadow`) overrides both.

mode:
  live   — submit for real (DEFAULT; preserves prior behavior).
  shadow — fill + screenshot + audit, but NEVER click submit (observability).
  hold   — fill completely, then stop before submit for human review.

Phase 1 (ADR-001) ships shadow/hold as "do not submit"; the confidence/category
fields below are recorded for later phases and are not yet enforced.
"""
import json
import os

_VALID_MODES = ("live", "shadow", "hold")

_DEFAULTS = {
    "mode": "live",
    "auto_submit_min_confidence": 0.9,
    "never_auto": ["freetext"],
    "ttl_days": 90,
    "paused": False,
    "use_mappings": False,  # ADR-001 Phase 3: enable the field→meaning mapping store
}


def _policy_path():
    base = os.environ.get("JI_HOME", os.path.expanduser("~/.ji"))
    return os.path.join(base, "apply_policy.json")


def load_policy():
    """Return the effective policy dict (defaults ← file ← JI_APPLY_MODE env)."""
    pol = dict(_DEFAULTS)
    try:
        with open(_policy_path(), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            pol.update(loaded)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    env_mode = os.environ.get("JI_APPLY_MODE")
    if env_mode:
        pol["mode"] = env_mode
    if pol.get("mode") not in _VALID_MODES:
        pol["mode"] = "live"
    return pol


def resolve_mode(cli_override=None):
    """Effective mode for this run. cli_override (e.g. 'shadow') wins if valid."""
    if cli_override in _VALID_MODES:
        return cli_override
    return load_policy()["mode"]


def submits_for_real(mode):
    """True only in live mode. shadow/hold never click submit."""
    return mode == "live"
