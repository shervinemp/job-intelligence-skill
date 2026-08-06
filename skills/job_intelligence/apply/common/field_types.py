"""Shared field-type predicates.

Single source of truth for combobox detection — used by both
filler.py (strategy dispatch) and dispatch.py (value verification),
which import from different layers and cannot safely import from each other.
"""


def is_combobox(f) -> bool:
    """True if field is a combobox/dropdown widget (not a native <select>).

    Recognizes:
      - explicit ARIA role: `role="combobox"`
      - tag DROPDOWN
      - LinkedIn-style typeahead: `data-testid="typeahead-input"` with
        `aria-autocomplete="list"` (Easy Apply location autocomplete has
        NO role=combobox — without this the check reads the raw input
        .value, which Ember keeps empty, and flags a filled location as
        unreadable)."""
    if f.get("role") == "combobox" or f.get("tag") == "DROPDOWN":
        return True
    tid = (f.get("data_testid") or f.get("data-testid") or "").lower()
    if tid == "typeahead-input" and (f.get("aria_autocomplete") or "").lower() == "list":
        return True
    return False
