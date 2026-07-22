"""Field fill dispatch ΓÇö routes to correct strategy by field type.
Tries each method in METHOD_CHAIN, then cross-type fallbacks.
Pre/post delta check verifies mutations actually took effect."""
import json, sys
from apply.strategies import combobox, text, select
from apply.common.value_reader import read_value as _read_value
from apply.steps.probe import resolve_selector


def _frame_for_sel(page, sel):
    """Find Playwright frame containing element matching sel. Returns frame or None."""
    for f in page.frames:
        try:
            if f.evaluate(f"() => !!document.querySelector({json.dumps(sel)})"):
                return f
        except Exception:
            continue
    return None


def _element_value(page, sel, ans=None):
    """Read field value using FieldValueReader cascade (see value_reader.py)."""
    try:
        fr = _frame_for_sel(page, sel) or page
        return _read_value(fr, sel, ans=ans)
    except Exception:
        return ""


def _check_delta(page, sel, before, after, ans, label):
    from apply.common.output import emit_diag
    if isinstance(ans, list):
        ans = ", ".join(str(v) for v in ans)
    elif ans is not None:
        ans = str(ans)
    if after and after != before and after != ans:
        return True
    if after and ans and (after == ans or ans in after or after in ans):
        return True
    if after == before and label:
        if before:
            emit_diag(label, ans, before, "unchanged", "ATS may have rejected the value")
        else:
            emit_diag(label, ans, "(empty)", "still_empty", "ATS silently rejected value")
        return False
    if not after and before:
        emit_diag(label, ans, "(empty)", "cleared", "ATS silently reset the value")
        return False
    return True


def _try_text_fallback(page, f, ans, sel):
    """Last-resort cross-type fallback: text fill via contenteditable or dispatch_events."""
    from apply.strategies import contenteditable as _ce, text as _tx
    if f.get("contenteditable") or f["tag"] == "DIV":
        return bool(_ce.fill(page, sel, ans))
    el = page.query_selector(sel)
    if el and f["tag"] in ("INPUT", "TEXTAREA"):
        for method in getattr(_tx, "METHOD_CHAIN", ["fill"]):
            if _tx.fill_text_field(page, f, ans, sel, el, method=method):
                return True
    if f.get("role") == "combobox" or f["tag"] == "DROPDOWN":
        return bool(_tx.native_setter(page, sel, ans))
    return False


def field_deterministic(page, f, ans):
    sel = f.get("_sel", "")
    if not sel:
        sel = resolve_selector(page, f)
        if not sel:
            return False
        f["_sel"] = sel

    # Route fills to the correct frame (iframe fields need frame-level access)
    fr = _frame_for_sel(page, sel) or page

    before = _element_value(page, sel)
    label = f.get("label", "")
    aft = before

    # Use FieldFiller registry for type dispatch
    from apply.common.filler import fill_field as _fill_field
    ok, filler = _fill_field(fr, f, ans)
    if ok:
        aft = _element_value(page, sel, ans=ans)
        if _check_delta(page, sel, before, aft, ans, label):
            return True
        # Filler reported success but value didn't stick ΓÇö try text fallback
    return _try_text_fallback(fr, f, ans, sel)
