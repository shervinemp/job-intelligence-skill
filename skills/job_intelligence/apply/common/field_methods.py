"""apply/common/field_methods.py — per-field fill-method learning (META_FLOW
Loop 4 gap #3).

The observation system learns WHICH PROBE STRATEGY wins per capability
profile, but not WHICH FILL METHOD wins per (domain, field). That gap is the
Antigua class: the system never learns "this platform's country picker is a
flag-class picker." This store learns label → proven filler method, scoped by
host, so a field that succeeded via `combobox` on a platform is tried with the
combobox filler FIRST next time.

S2-hygiened: only a method confirmed ≥2 times on the same host is promoted to
a first-try preference; a conflicting method resets the count. Cross-host
conflicts keep the field methodless (no global preference).
"""
import os
import time

from lib.config import STATE_DIR

_PATH = os.path.join(STATE_DIR, "field_methods.json")
_cache = None
_MIN_CONFIRMS = 2


def _load():
    global _cache
    if _cache is None:
        try:
            with open(_PATH, encoding="utf-8") as f:
                import json
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def _save():
    global _cache
    try:
        from lib.config import atomic_write_json
        import json
        atomic_write_json(_PATH, _cache, indent=2)
    except Exception:
        pass


def _norm_label(label):
    import re
    return re.sub(r"[^a-z0-9 ]", " ", (label or "").lower()).strip()


def _host_of(url):
    from urllib.parse import urlparse
    try:
        return (urlparse(url or "").netloc or "").lower().split(":")[0]
    except Exception:
        return ""


def record_method(label, method, host=""):
    """Record a successful fill method for (host, label). Returns True when
    the method is promoted to a preference (≥2 confirms on the same host)."""
    if not label or not method:
        return False
    nl = _norm_label(label)
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    m = _load()
    key = f"{host}\u0001{nl}"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    e = m.get(key)
    if not e or e.get("method") != method:
        m[key] = {"method": method, "count": 1, "ts": now}
        promoted = False
    else:
        e["count"] = int(e.get("count", 0)) + 1
        e["ts"] = now
        promoted = e["count"] >= _MIN_CONFIRMS
    _save()
    return promoted


def prefer_method(label, host=""):
    """The preferred filler method for (host, label) at/above the confirm
    threshold, else ''."""
    if not label:
        return ""
    nl = _norm_label(label)
    host = (host or "").lower().rstrip(".")
    if not host:
        return ""
    e = _load().get(f"{host}\u0001{nl}")
    if e and int(e.get("count", 0)) >= _MIN_CONFIRMS:
        return e.get("method", "")
    return ""


def reject_method(label, host=""):
    """#4: an adjudicated WRONG fill must weaken the per-field method
    preference for (host, label) — a method that "succeeded" but filled
    wrong must not keep accumulating wins. Resets the count so re-confirmation
    is required before it is preferred again."""
    nl = _norm_label(label)
    host = (host or "").lower().rstrip(".")
    if not host:
        return
    m = _load()
    key = f"{host}\u0001{nl}"
    if key in m:
        del m[key]
        _save()


def record_verify_strategy(label, strategy, host=""):
    """Record the verification STRATEGY that confirmed a (host, label) read-back
    (META_FLOW Loop-4 gap #3): e.g. 'flag_class' (intl-tel picker verified via
    the flag class) vs 'text' (read via option text). Promoted to a preference
    after ≥2 confirms, so the reader cascade prefers the proven strategy."""
    if not label or not strategy:
        return False
    nl = _norm_label(label)
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    m = _load()
    key = f"{host}\u0001{nl}"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    e = m.get(key)
    if not e or e.get("verify_strategy") != strategy:
        e = e or {}
        e["verify_strategy"] = strategy
        e["verify_count"] = 1
        e["ts"] = now
        m[key] = e
        promoted = False
    else:
        e["verify_count"] = int(e.get("verify_count", 0)) + 1
        e["ts"] = now
        promoted = e["verify_count"] >= _MIN_CONFIRMS
    _save()
    return promoted


def prefer_verify_strategy(label, host=""):
    """The proven verification strategy for (host, label), else ''."""
    if not label:
        return ""
    nl = _norm_label(label)
    host = (host or "").lower().rstrip(".")
    if not host:
        return ""
    e = _load().get(f"{host}\u0001{nl}")
    if e and int(e.get("verify_count", 0)) >= _MIN_CONFIRMS:
        return e.get("verify_strategy", "")
    return ""


def clear_host(url):
    """Drop all per-field method preferences for one host — called on
    observation demotion (B3): a platform redesign invalidates what was
    learned about its fields."""
    host = _host_of(url)
    if not host:
        return
    m = _load()
    prefix = f"{host}\u0001"
    removed = [k for k in m if k.startswith(prefix)]
    for k in removed:
        del m[k]
    if removed:
        _save()


def clear_for_test():
    global _cache
    _cache = {}
    try:
        from lib.config import atomic_write_json
        import json
        atomic_write_json(_PATH, {}, indent=2)
    except Exception:
        pass
