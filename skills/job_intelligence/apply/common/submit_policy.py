"""policy.py — Apply-pipeline submission policy (live / shadow / hold).

Source of truth: apply_policy.json in JI_HOME. `JI_APPLY_MODE` env overrides the
file; a per-run override (e.g. `act --shadow`) overrides both.

mode:
  live   — submit for real. Must be chosen EXPLICITLY (file or env).
  shadow — fill + screenshot + audit, but NEVER click submit (observability).
  hold   — fill completely, then stop before submit for human review. DEFAULT.

Fail-closed contract (ETHOS §11, "hold-mode by default"): every way this
module can fail to learn the operator's intent resolves to `hold`. A
missing policy file, an unreadable one, malformed JSON, or a typo'd mode
all mean "we do not know that a live submit was authorised" — and the
only safe reading of that is: don't submit.

Phase 1 (ADR-001) ships shadow/hold as "do not submit"; the confidence/category
fields below are recorded for later phases and are not yet enforced.
"""
import json
import os
import sys

_VALID_MODES = ("live", "shadow", "hold")

_warned_invalid_mode = False

_DEFAULTS = {
    "mode": "hold",
    "auto_submit_min_confidence": 0.9,
    "never_auto": ["freetext"],
    "ttl_days": 90,
    "paused": False,
    "use_mappings": False,
    "enforce_validation": False,
    "gate_submit": False,
    # Batch-bounding controls: skip-and-flag CAPTCHAs instead of blocking
    # the run on a human solve, and abort per-job after N seconds
    # (recorded as captcha_required / timed_out — both resumable).
    "captcha_skip": False,
    "job_timeout_sec": 0,
}


def _policy_path():
    from lib.config import JI_HOME
    return os.path.join(os.environ.get("JI_HOME") or JI_HOME, "apply_policy.json")


def load_policy():
    """Return the effective policy dict (defaults ← file ← JI_APPLY_MODE env).

    Every failure path lands on 'hold' and SAYS SO. Previously a missing or
    corrupt policy file was swallowed silently and inherited the 'live'
    default — i.e. the most likely failure of the safety control opened the
    gate instead of closing it.
    """
    pol = dict(_DEFAULTS)
    try:
        with open(_policy_path(), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            pol.update(loaded)
        else:
            _warn_once(f"apply_policy.json is not an object — holding "
                       f"({_policy_path()})")
            pol["mode"] = "hold"
    except FileNotFoundError:
        # Not an error: no file means no live authorisation was ever given.
        pol.setdefault("mode", "hold")
    except (json.JSONDecodeError, OSError) as e:
        _warn_once(f"apply_policy.json unreadable ({e}) — failing closed to "
                   f"'hold'")
        pol["mode"] = "hold"
    env_mode = os.environ.get("JI_APPLY_MODE")
    if env_mode:
        pol["mode"] = env_mode
    if pol.get("mode") not in _VALID_MODES:
        # Fail closed: a typo in a safety control must never cause real submits.
        _warn_once(f"invalid apply mode '{pol.get('mode')}' — failing closed "
                   f"to 'hold' (valid: {', '.join(_VALID_MODES)})")
        pol["mode"] = "hold"
    return pol


def _warn_once(msg):
    global _warned_invalid_mode
    if not _warned_invalid_mode:
        _warned_invalid_mode = True
        print(f"POLICY: {msg}", file=sys.stderr)


def resolve_mode(cli_override=None):
    """Effective mode for this run. cli_override (e.g. 'shadow') wins if valid."""
    if cli_override in _VALID_MODES:
        return cli_override
    return load_policy()["mode"]


def paused_platforms():
    """Platforms paused by the wrong-fill SPC trip (B2) — autonomous submits
    on these are suppressed regardless of mode."""
    try:
        pol = load_policy()
        return set(pol.get("paused_platforms") or [])
    except Exception:
        return set()


def submits_for_real(mode, platform=""):
    """True only in live mode. shadow/hold never click submit.
    B2: a platform paused by the wrong-fill SPC trip is treated as hold even
    in live mode — the SPC bound is a tripwire, not a suggestion."""
    if mode != "live":
        return False
    if platform:
        plat = (platform or "").lower()
        if plat in paused_platforms():
            print(f"POLICY: platform '{plat}' paused by wrong-fill SPC — "
                  f"submit suppressed (run report.py fleet to review)",
                  file=sys.stderr)
            return False
    return True
