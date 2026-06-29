"""mappings.py — Field→meaning mapping store (ADR-001 Phase 3).

Caches the *mapping* from a field's fingerprint to a meaning (a profile key, or a
stable literal answer), never a job-specific value. Values are recomputed from the
profile on every lookup, so editing the profile propagates everywhere and there is
no stale-value risk.

Lifecycle:
  - learn()    records a *pending* mapping when the agent answers a previously
               unmapped field via --answers (provenance "user_typed").
  - promote()  on a verified submission, auto-promotes pending mappings that were
               corrected-then-passed (ADR #8 confidence ladder) and are in a safe
               category. One-shot guesses are NOT auto-promoted.
  - confirm()  human review path: promote all pending for a job (used after a
               shadow run review).
  - resolve_field() consults the persistent store during fill, validating the
               value against the live field before trusting it, and invalidating
               on option-set drift / profile-version change / TTL.

OFF BY DEFAULT (policy.use_mappings). Enable only after reviewing shadow-run data.

Persistence is atomic (tmp + os.replace). Concurrent multi-process runs are not
locked yet — single-session use is assumed (see AGENTS.md).
"""
import json
import os
import time

from apply.common.fingerprint import field_fingerprint
from apply.common.validate import validate_value
from apply.common.audit import categorize
from apply.common.policy import load_policy

_NEVER_LEARN = ("freetext", "eeo")  # never cache these regardless of policy


def _home():
    return os.environ.get("JI_HOME", os.path.expanduser("~/.ji"))


def _persist_path():
    return os.path.join(_home(), "mappings.json")


def _pending_path():
    return os.path.join(_home(), "mappings_pending.json")


def _today():
    return time.strftime("%Y-%m-%d")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def enabled():
    return bool(load_policy().get("use_mappings", False))


def _profile_version(profile):
    return str(profile.get("_version", 0))


def _transform(value, transform):
    if transform == "yesno":
        if isinstance(value, bool):
            return "Yes" if value else "No"
        sv = str(value).strip().lower()
        if sv in ("true", "yes", "1"):
            return "Yes"
        if sv in ("false", "no", "0"):
            return "No"
    return str(value)


def _norm(s):
    import re
    return re.sub(r"[^a-z0-9+#]+", " ", (s or "").lower()).strip()


def _target_for(value, profile):
    """Prefer a profile-key mapping (robust to profile edits) over a literal.
    Returns (target_kind, target, transform)."""
    nv = _norm(value)
    for k, v in profile.items():
        if k in ("answers", "common_answers") or not isinstance(v, str):
            continue
        if v and _norm(v) == nv:
            return "profile_key", k, "passthrough"
    return "literal", None, "passthrough"


def _expired(entry, ttl_days):
    lc = entry.get("last_confirmed")
    if not lc:
        return False
    try:
        t = time.mktime(time.strptime(lc, "%Y-%m-%d"))
    except ValueError:
        return False
    return (time.time() - t) > ttl_days * 86400


# ─── Read path (fill) ────────────────────────────────────────────────

def resolve_field(field, profile, bump=True):
    """Return (value, 'mapping') from a confirmed mapping, or None.
    Validates against the live field and invalidates on drift/version/TTL.
    bump=False does a side-effect-free lookup (used by the audit pass)."""
    if not enabled():
        return None
    fp = field_fingerprint(field)
    store = _load(_persist_path())
    m = store.get(fp)
    if not m:
        return None
    if m.get("profile_version") != _profile_version(profile):
        return None
    if _expired(m, int(load_policy().get("ttl_days", 90))):
        return None

    if m.get("target_kind") == "profile_key":
        v = profile.get(m.get("target"))
        if not v:
            return None
        value = _transform(v, m.get("transform", "passthrough"))
    else:
        value = m.get("value", "")
    if not value:
        return None

    ok, _reason = validate_value(field, value)
    if not ok:
        return None

    if bump:
        m["hit_count"] = m.get("hit_count", 0) + 1
        m["last_used"] = _today()
        _save(_persist_path(), store)
    return value, "mapping"


# ─── Write path (learn / promote) ────────────────────────────────────

def learn(jid, field, value, provenance, profile, platform="", corrected=False):
    """Record a pending mapping for an explicitly-answered (--answers) field."""
    if not enabled() or provenance != "user_typed" or not value:
        return
    category = categorize(field.get("label"), field.get("options"), field.get("tag"))
    if category in _NEVER_LEARN:
        return
    fp = field_fingerprint(field)
    kind, target, transform = _target_for(value, profile)
    pending = _load(_pending_path())
    jp = pending.setdefault(str(jid), {})
    prev = jp.get(fp, {})
    jp[fp] = {
        "target_kind": kind,
        "target": target,
        "value": value if kind == "literal" else "",
        "transform": transform,
        "category": category,
        "profile_version": _profile_version(profile),
        "platform": (platform or "").lower(),
        "source": provenance,
        "label": (field.get("label") or "")[:80],
        "corrected": bool(prev.get("corrected")) or bool(corrected),
        "seen": _today(),
    }
    _save(_pending_path(), pending)


def _promote_entries(jid, predicate, confidence):
    pending = _load(_pending_path())
    jp = pending.get(str(jid), {})
    if not jp:
        return 0
    never_auto = set(load_policy().get("never_auto", []))
    store = _load(_persist_path())
    n = 0
    for fp, e in list(jp.items()):
        if e.get("category") in never_auto or e.get("category") in _NEVER_LEARN:
            continue
        if not predicate(e):
            continue
        existing = store.get(fp, {})
        store[fp] = {
            **e,
            "confidence": confidence,
            "created": existing.get("created", _today()),
            "last_confirmed": _today(),
            "hit_count": existing.get("hit_count", 0) + 1,
        }
        del jp[fp]
        n += 1
    if n:
        _save(_persist_path(), store)
        pending[str(jid)] = jp
        _save(_pending_path(), pending)
    return n


def promote(jid):
    """Auto-promote on verified submission: only corrected-then-passed mappings
    (one-shot guesses are not trusted — ADR #8)."""
    if not enabled():
        return 0
    return _promote_entries(jid, lambda e: e.get("corrected"), confidence=0.9)


def confirm(jid):
    """Human-review path: promote all safe-category pending mappings for a job."""
    return _promote_entries(jid, lambda e: True, confidence=0.8)


def list_pending(jid):
    return _load(_pending_path()).get(str(jid), {})


def clear(jid):
    pending = _load(_pending_path())
    if str(jid) in pending:
        del pending[str(jid)]
        _save(_pending_path(), pending)
