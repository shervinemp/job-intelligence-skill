"""Field fill dispatch — thin wrapper over filler.fill_field.

Pre-fill validation (email/phone/url format) gates bad values before
they reach the widget. The actual fill + verify logic lives in
filler.fill_field, which consolidates the former two-layer dispatch.
"""
from apply.common.field_types import is_combobox as _is_combobox
from apply.steps.probe import resolve_selector


def field_deterministic(page, f, ans):
    """Fill a field deterministically. Returns True on success.

    1. Pre-fill validation (format checks for typed inputs)
    2. Delegate to filler.fill_field (includes post-fill verification)
    """
    sel = f.get("_sel", "")
    if not sel:
        sel = f.get("selector", "")
    if not sel:
        sel = resolve_selector(page, f)
        if not sel:
            return False
    f["_sel"] = sel

    # Pre-fill validation: catch bad values before they reach the widget.
    # Skip option-constraint check for RADIO_GROUP — the RadioFiller has its
    # own matching cascade (prefix match, label walk, EEOC normalize, negation
    # detection) that's more nuanced than a simple substring check.
    if not _is_combobox(f) and f.get("tag") != "RADIO_GROUP":
        from apply.common.validate import validate_value
        ok, reason = validate_value(f, ans)
        if not ok and reason != "empty":
            from apply.common.output import emit_diag
            emit_diag(f.get("label", ""), str(ans), "", "validation_skip", reason)
            return False

    from apply.common.filler import fill_field
    ok, _filler_name = fill_field(page, f, ans)
    return ok
