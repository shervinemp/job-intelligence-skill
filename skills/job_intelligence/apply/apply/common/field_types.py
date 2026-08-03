"""Shared field-type predicates.

Single source of truth for combobox detection — used by both
filler.py (strategy dispatch) and dispatch.py (value verification),
which import from different layers and cannot safely import from each other.
"""


def is_combobox(f) -> bool:
    """True if field is a combobox/dropdown widget (not a native <select>)."""
    return f.get("role") == "combobox" or f.get("tag") == "DROPDOWN"
