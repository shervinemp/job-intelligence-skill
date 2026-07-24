"""Field fill dispatch ΓÇö routes to correct strategy by field type.
Tries each method in METHOD_CHAIN, then cross-type fallbacks.
Pre/post delta check verifies mutations actually took effect."""
import json, sys
from apply.strategies import combobox, text, select
from apply.common.value_reader import read_value as _read_value
from apply.steps.probe import resolve_selector


def _is_combobox(f):
    """Single source of truth for combobox detection."""
    return f.get("role") == "combobox" or f.get("tag") == "DROPDOWN"


def _frame_for_sel(page, sel):
    """Find Playwright frame containing element matching sel. Returns frame or None."""
    for f in page.frames:
        try:
            if f.evaluate(f"() => !!document.querySelector({json.dumps(sel)})"):
                return f
        except Exception:
            continue
    return None


def _element_value(page, sel, ans=None, field=None):
    """Read field value using FieldValueReader cascade (see value_reader.py).
    For combobox-role fields, skip StandardReader — raw el.value is just typed
    text on a typeahead, not a committed selection (phantom-success source)."""
    try:
        fr = _frame_for_sel(page, sel) or page
        if field is not None and _is_combobox(field):
            from apply.common.value_reader import AriaComboboxReader, ReactSelectReader, FuzzyComboboxReader
            for reader in (AriaComboboxReader(), ReactSelectReader(), FuzzyComboboxReader()):
                v = reader.read(fr, sel, ans=ans)
                if v:
                    return v
            return ""
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
    if _is_combobox(f):
        # native_setter on a combobox is a phantom factory: it writes text the
        # widget never commits as a selection. Fail honestly instead.
        return False
    if f.get("contenteditable") or f["tag"] == "DIV":
        return bool(_ce.fill(page, sel, ans))
    el = page.query_selector(sel)
    if el and f["tag"] in ("INPUT", "TEXTAREA"):
        for method in getattr(_tx, "METHOD_CHAIN", ["fill"]):
            if _tx.fill_text_field(page, f, ans, sel, el, method=method):
                return True
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

    before = _element_value(page, sel, field=f)
    label = f.get("label", "")
    aft = before

    # Use FieldFiller registry for type dispatch
    from apply.common.filler import fill_field as _fill_field
    ok, filler = _fill_field(fr, f, ans)
    if ok:
        # Comboboxes: _select_option already verified the click (polled for
        # visible options and clicked one). Trust it — the read-back via
        # ReactSelectReader can race the DOM and report empty, which would
        # trigger a phantom-clear that wipes a real selection.
        if _is_combobox(f):
            return True
        aft = _element_value(page, sel, ans=ans, field=f)
        if _check_delta(page, sel, before, aft, ans, label):
            return True
        # Filler reported success but value didn't stick ΓÇö try text fallback
    if _try_text_fallback(fr, f, ans, sel):
        # Fallback claims success — verify the value actually changed, so the
        # Skyvern skip-list never contains fields that only look filled.
        aft2 = _element_value(page, sel, ans=ans, field=f)
        if isinstance(ans, list):
            ans_s = ", ".join(str(v) for v in ans)
        else:
            ans_s = str(ans) if ans is not None else ""
        if aft2 and (aft2 != before) and (not ans_s or aft2 == ans_s or ans_s in aft2 or aft2 in ans_s):
            return True
        if ans_s and aft2 and (aft2 == ans_s or ans_s in aft2 or aft2 in ans_s):
            return True
        return False
    return False
