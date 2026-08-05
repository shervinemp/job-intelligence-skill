"""observations.py — Learned probe-routing observations.

The observation store is the *only* learned state in the probe layer.
It sits between the static YAML registry (hand-tuned, authoritative)
and the 9-level cascade (the safety net). Observations are hints,
never commitments:

  - YAML best_strategy wins
  - Confirmed observation (N≥3 consistent runs on the same capability
    profile hash) is tried next
  - Cheap capability-scan suggestion is tried next
  - Full cascade fires regardless — observations only reorder the
    starting point

Keyed by `profile_hash`, *not* domain — so two different ATS that
happen to share the same form shape share the learned starting point.
This is intentional: domain-keyed learning can't recognise a new
tenant on a known multi-tenant platform (Workday/Ashby/Greenhouse),
but capability-keyed learning does so automatically.

Drift handling: if a confirmed observation's winning strategy returns
0 fields on the next run, the observation is demoted (confirmed=False,
fail_count++) and the cascade fires. After N=3 subsequent successes,
it re-confirms. This catches platform redesigns without losing the
prior learning permanently.

File layout: ~/.ji/registry-obs/<profile_hash>.json
  {
    "profile_hash": "...",
    "confirmed": true|false,
    "success_count": int,         # consecutive successes
    "fail_count": int,            # consecutive failures
    "winning_strategy": str,      # last strategy that returned fields
    "winning_widgets": {...},     # widget selectors used (may be {})
    "candidate_strategies": [str],# pending observations before confirm
    "first_seen": "...",
    "last_seen": "...",
    "last_run_url": "...",
    "domain_examples": [...],     # last 5 distinct domains seen
  }
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

from lib.config import atomic_write_json
from apply.common.capabilities import profile_hash

# N consecutive identical winning strategies before an observation is
# promoted to "confirmed" (tried before the capability suggestion).
CONFIRM_THRESHOLD = 3

# N consecutive failures of a confirmed observation before it is
# fully demoted (cleared back to candidate) rather than just retried.
DRIFT_FULL_DEMOTE_THRESHOLD = 2

# Cap on candidate_strategies retained (avoids unbounded growth).
_MAX_CANDIDATES = 5

# Cap on domain_examples retained (privacy + size).
_MAX_DOMAIN_EXAMPLES = 5


def _obs_dir() -> str:
    """Read JI_HOME at call time (not import time) so tests with
    tempdirs get a clean OBS_DIR without module-reload gymnastics."""
    from lib.config import JI_HOME
    return os.path.join(JI_HOME, "registry-obs")


def _obs_path(hash_key: str) -> str:
    return os.path.join(_obs_dir(), f"{hash_key}.json")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _empty_record(hash_key: str) -> dict:
    return {
        "profile_hash": hash_key,
        "confirmed": False,
        "success_count": 0,
        "fail_count": 0,
        "winning_strategy": "",
        "winning_widgets": {},
        "candidate_strategies": [],
        "first_seen": _now(),
        "last_seen": _now(),
        "last_run_url": "",
        "domain_examples": [],
    }


def _load(hash_key: str) -> dict:
    path = _obs_path(hash_key)
    if not os.path.exists(path):
        return _empty_record(hash_key)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("profile_hash") != hash_key:
            return _empty_record(hash_key)
        # Backfill any missing fields
        rec = _empty_record(hash_key)
        rec.update(data)
        return rec
    except Exception:
        return _empty_record(hash_key)


def _save(record: dict) -> None:
    try:
        os.makedirs(_obs_dir(), exist_ok=True)
        atomic_write_json(_obs_path(record["profile_hash"]), record)
    except Exception as e:
        print(f"OBS_WRITE_FAIL: {e}", file=sys.stderr)


def _add_domain_example(record: dict, url: str) -> None:
    if not url:
        return
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        if not host:
            return
        examples = record.get("domain_examples", [])
        if host in examples:
            return
        examples.append(host)
        record["domain_examples"] = examples[-_MAX_DOMAIN_EXAMPLES:]
    except Exception:
        pass


def _add_candidate(record: dict, strategy: str) -> None:
    """Track a strategy that succeeded while the observation is still
    unconfirmed. Promotion to confirmed happens when the same strategy
    appears CONFIRM_THRESHOLD consecutive times."""
    if not strategy:
        return
    cands = record.get("candidate_strategies", [])
    # Place at the head, dedupe, cap.
    cands = [strategy] + [c for c in cands if c != strategy]
    record["candidate_strategies"] = cands[:_MAX_CANDIDATES]


def record_success(profile: Optional[dict], url: str,
                    winning_strategy: str,
                    widgets_used: Optional[dict] = None) -> dict:
    """Record a successful probe for this capability profile.

    A "success" is `field_count > 0` returned by ANY strategy in the
    cascade. The cascade runs to its first non-empty result, so
    `winning_strategy` is the *first* strategy that returned fields.

    Threshold logic:
      - If the recorded winning_strategy matches the new observation,
        success_count++. On reaching CONFIRM_THRESHOLD, confirmed=True.
      - If different, the new winner replaces it but success_count
        resets to 1 (we don't trust a one-shot change).
      - Recording always clears fail_count (it's a success).

    Returns the updated record (mostly for tests / debugging).
    """
    h = profile_hash(profile)
    record = _load(h)
    record["last_seen"] = _now()
    record["last_run_url"] = (url or "")[:200]
    record["fail_count"] = 0
    _add_domain_example(record, url)
    if not winning_strategy:
        _save(record)
        return record
    widgets_used = widgets_used or {}
    if record.get("winning_strategy") == winning_strategy:
        record["success_count"] = int(record.get("success_count", 0)) + 1
        # Merge widgets (newest wins for any key)
        merged = dict(record.get("winning_widgets") or {})
        merged.update(widgets_used)
        record["winning_widgets"] = merged
    else:
        # Strategy changed — start tracking the new one from 1.
        record["winning_strategy"] = winning_strategy
        record["success_count"] = 1
        record["winning_widgets"] = dict(widgets_used)
        _add_candidate(record, winning_strategy)
    if record["success_count"] >= CONFIRM_THRESHOLD and not record["confirmed"]:
        record["confirmed"] = True
        print(f"OBS_CONFIRMED: profile={h[:8]} strategy={winning_strategy} "
              f"(after {record['success_count']} consistent wins)", file=sys.stderr)
    _save(record)
    return record


def record_failure(profile: Optional[dict], url: str) -> dict:
    """Record a cascade-complete failure (all 9 depths returned 0 fields).

    If we had a confirmed observation for this profile, demote it:
      - fail_count++
      - On reaching DRIFT_FULL_DEMOTE_THRESHOLD, drop confirmed=False
        and reset success_count=0 so the next 3 successes re-confirm.
        This is the drift case: the platform changed and the prior
        winning strategy no longer applies.

    Returns the updated record.
    """
    h = profile_hash(profile)
    record = _load(h)
    record["last_seen"] = _now()
    record["last_run_url"] = (url or "")[:200]
    _add_domain_example(record, url)
    if record.get("confirmed"):
        record["fail_count"] = int(record.get("fail_count", 0)) + 1
        if record["fail_count"] >= DRIFT_FULL_DEMOTE_THRESHOLD:
            record["confirmed"] = False
            record["success_count"] = 0
            record["winning_strategy"] = ""
            record["winning_widgets"] = {}
            record["candidate_strategies"] = []  # prior winners are suspect after drift
            record["fail_count"] = 0  # fresh slate — next cycle starts clean
            # B3: a full demotion means the platform may have been redesigned —
            # per-field fill-method preferences for this host are suspect too.
            try:
                from apply.common import field_methods
                field_methods.clear_host(url)
            except Exception:
                pass
            print(f"OBS_DRIFT: profile={h[:8]} demoted after "
                  f"{DRIFT_FULL_DEMOTE_THRESHOLD} cascade failures — "
                  "platform may have been redesigned", file=sys.stderr)
    _save(record)
    return record


def lookup(profile: Optional[dict]) -> Optional[dict]:
    """Look up the observation record for a capability profile.

    Returns None if no record exists yet (first run on this shape).
    The router uses this to decide:
      - If `confirmed` and `winning_strategy` → try that strategy first
      - Else if `candidate_strategies` → prefer the most-frequent candidate
        as a soft hint (still tries the full cascade if it returns 0)
    """
    h = profile_hash(profile)
    if h == "unknown":
        return None
    path = _obs_path(h)
    if not os.path.exists(path):
        return None
    rec = _load(h)
    # Empty record (no winning_strategy) shouldn't influence routing
    if not rec.get("winning_strategy") and not rec.get("candidate_strategies"):
        return None
    return rec


def list_all() -> list[dict]:
    """List all observed profiles — used by `apply.py registry candidates`."""
    if not os.path.isdir(_obs_dir()):
        return []
    out = []
    for name in sorted(os.listdir(_obs_dir())):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_obs_dir(), name), encoding="utf-8") as f:
                rec = json.load(f)
            if isinstance(rec, dict):
                out.append(rec)
        except Exception:
            continue
    return out


def confirm_hash(hash_key: str) -> bool:
    """Manually confirm an observation (writes confirmed=True, success_count
    set to CONFIRM_THRESHOLD). Used by `apply.py registry confirm <hash>`
    — an escape hatch when the user trusts the pending candidate."""
    path = _obs_path(hash_key)
    if not os.path.exists(path):
        return False
    rec = _load(hash_key)
    if not rec.get("winning_strategy") and rec.get("candidate_strategies"):
        rec["winning_strategy"] = rec["candidate_strategies"][0]
    if not rec.get("winning_strategy"):
        return False
    rec["confirmed"] = True
    rec["success_count"] = CONFIRM_THRESHOLD
    rec["fail_count"] = 0
    _save(rec)
    return True


def clear_hash(hash_key: str) -> bool:
    """Delete an observation record. Use when it has gone stale and
    we want to re-accumulate from scratch (manual reset)."""
    path = _obs_path(hash_key)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception:
            return False
    return False


def recommend_start_strategy(profile: Optional[dict] = None,
                             record: Optional[dict] = None,
                             registry_strategy: Optional[str] = None) -> Optional[str]:
    """Decide which strategy to try *first*.

    Priority (highest wins):
      1. `registry_strategy` (from YAML) — authoritative, always wins.
         We don't second-guess the YAML here; the cascade will fire if
         it returns 0 (CONFIG_STALE is logged elsewhere).
      2. `record.confirmed and record.winning_strategy` — learned from
         N≥3 consistent runs.
      3. The most-frequent `candidate_strategy` (if at least 2
         observations agree) — soft hint below the confirmation bar.
      4. Suggested strategy from the capability profile itself
         (dialog/iframe/widgets signals).
      5. None — let the cascade run in declaration order.

    The router never returns a strategy it isn't willing to also lose
    on. The cascade always runs.
    """
    # 1. YAML authoritative
    if registry_strategy:
        return registry_strategy
    if record:
        # 2. Confirmed learned
        if record.get("confirmed") and record.get("winning_strategy"):
            return record["winning_strategy"]
        # 3. Soft candidate hint (≥2 wins on the same candidate)
        cands = record.get("candidate_strategies") or []
        if cands:
            return cands[0]
    # 4. Capability scan suggestion (computed by the router caller, not
    #    here, to keep the store pure-data). Caller adds this as the
    #    fallback when no record exists.
    return None