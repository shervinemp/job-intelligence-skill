"""fingerprint.py — Stable semantic keys for form fields (ADR-001 Phase 3).

A mapping is keyed on the *meaning* of a question, not its raw label, so two
questions with the same words but different option sets / types are different
keys. The fingerprint folds in: normalized label + sorted normalized options +
tag + input type. Platform is deliberately NOT included — the meaning of a
question ("Authorized to work in Canada? [Yes/No]") is platform-independent, so a
mapping learned on one ATS should apply on another. `options_hash` is stored
separately so a drifted option set invalidates a mapping even if the label is
unchanged.

Pure functions — unit-testable, no DOM access.
"""
import hashlib
import re


def _norm(s):
    return re.sub(r"[^a-z0-9+#]+", " ", (s or "").lower()).strip()


def _opts_norm(options):
    return sorted(o for o in (_norm(o) for o in (options or [])) if o)


def field_fingerprint(field):
    """Stable 16-hex key for a field's meaning (label + options + tag + type)."""
    label = _norm(field.get("label"))
    opts = ",".join(_opts_norm(field.get("options")))
    tag = (field.get("tag") or "").upper()
    ftype = (field.get("type") or "").lower()
    basis = "|".join([label, opts, tag, ftype])
    return hashlib.sha1(basis.encode()).hexdigest()[:16]
