"""audit.py — Append-only per-application audit log.

One JSONL file per job at results/<jid>/apply_audit.jsonl. Records every field
resolution (value + provenance + tier category + whether it ended up filled) and
every page-level apply event. Two consumers:
  - the human / shadow-mode review (what would we submit, and why)
  - the in-loop LLM as cross-step memory of what is already answered/held

Paths read JI_HOME from the environment at call time (testable; mirrors resolve.py).
"""
import json
import os
import sys
import time

_DECLINE = ("prefer not", "decline", "not say", "rather not")
_SALARY = ("salary", "compensation", "ctc", "expected pay", "pay rate", "desired pay")
_LEGAL = ("authorize", "sponsor", "eligible to work", "right to work", "legally", "certify", "visa")


def _results_dir():
    base = os.environ.get("JI_HOME", os.path.expanduser("~/.ji"))
    return os.path.join(base, "results")


def _path(jid):
    d = os.path.join(_results_dir(), str(jid))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "apply_audit.jsonl")


def categorize(label, options=None, tag=None):
    """Classify a field into a tier category: eeo | salary | legal | freetext | generic.

    EEO is detected by decline-option content (language-agnostic), matching the
    existing apply behavior. Used for observability now; gating later (ADR-001)."""
    lbl = (label or "").lower()
    opts = [(o or "").lower() for o in (options or [])]
    if any(any(d in o for d in _DECLINE) for o in opts):
        return "eeo"
    if any(w in lbl for w in _SALARY):
        return "salary"
    if any(w in lbl for w in _LEGAL):
        return "legal"
    if (tag or "").upper() == "TEXTAREA":
        return "freetext"
    return "generic"


def _write(jid, rec):
    rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    try:
        with open(_path(jid), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"AUDIT_FAIL: {e}", file=sys.stderr)


def log_field(jid, label, value, provenance, category="generic", filled=True,
              validated=None, page=None):
    _write(jid, {
        "kind": "field",
        "label": (label or "")[:80],
        "value": (value or "")[:200],
        "provenance": provenance,
        "category": category,
        "filled": bool(filled),
        "validated": validated,  # True / False / None (not checked)
        "page": page,
    })


def log_event(jid, event, mode=None, detail=None, page=None):
    _write(jid, {"kind": "event", "event": event, "mode": mode, "detail": detail, "page": page})


def summarize(jid):
    """Aggregate the audit log into counts (by provenance, category, filled). Empty if none."""
    summary = {"fields": 0, "filled": 0, "invalid": 0, "by_provenance": {}, "by_category": {}, "events": []}
    try:
        with open(_path(jid), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return summary
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "field":
            summary["fields"] += 1
            if rec.get("filled"):
                summary["filled"] += 1
            if rec.get("validated") is False:
                summary["invalid"] += 1
            p = rec.get("provenance", "?")
            c = rec.get("category", "?")
            summary["by_provenance"][p] = summary["by_provenance"].get(p, 0) + 1
            summary["by_category"][c] = summary["by_category"].get(c, 0) + 1
        elif rec.get("kind") == "event":
            summary["events"].append(rec.get("event"))
    return summary
