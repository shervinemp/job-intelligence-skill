"""validate.py — Fill-time value validation (ADR-001 requirement #2).

Checks a resolved value against a field's constraints before it is trusted: option
match for select/radio/combobox, and format checks for email/phone/number/url. A
value that fails should be escalated (treated as unfilled), never submitted.

Pure functions — no page/DOM access, so they are cheap and unit-testable. Phase 2
wires this into the read-only audit pass (records `validated`); enforcing it at
fill time (escalate on invalid) is a deliberate later step, gated on shadow-run
data showing it doesn't break working fills.
"""
import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL = re.compile(r"^https?://", re.I)
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _norm(s):
    return re.sub(r"[^a-z0-9+#]+", " ", (s or "").lower()).strip()


def value_matches_option(value, options):
    """True if value matches one of the field's options (normalized exact/substring).
    Empty options means no option constraint → True."""
    if not options:
        return True
    nv = _norm(value)
    if not nv:
        return False
    for o in options:
        no = _norm(o)
        if not no:
            continue
        if nv == no or nv in no or no in nv:
            return True
    return False


def validate_value(field, value):
    """Return (ok, reason). ok=False means the value should not be trusted for this field."""
    if value is None or str(value).strip() == "":
        return False, "empty"
    val = str(value).strip()
    tag = (field.get("tag") or "").upper()
    ftype = (field.get("type") or "").lower()
    label = _norm(field.get("label"))
    options = field.get("options") or []

    # Option-constrained fields: the value must be one of the offered choices.
    if options or tag in ("SELECT", "DROPDOWN") or field.get("role") == "combobox":
        if not value_matches_option(val, options):
            return False, "not in options"
        return True, "option"

    # Format checks for typed inputs (type attr primary, label as hint).
    if ftype == "email" or "email" in label:
        return (True, "email") if _EMAIL.match(val) else (False, "bad email")
    if ftype in ("tel", "phone") or "phone" in label:
        return (True, "phone") if len(re.sub(r"\D", "", val)) >= 7 else (False, "too few digits")
    if ftype == "number":
        return (True, "number") if _NUM.match(val) else (False, "not numeric")
    if ftype == "url" or label.endswith(" url") or label in ("url", "website"):
        return (True, "url") if _URL.match(val) else (False, "not a url")

    return True, "text"
