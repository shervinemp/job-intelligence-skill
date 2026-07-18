"""Field fill dispatch — routes to correct strategy by field type.
Tries each method in METHOD_CHAIN, then cross-type fallbacks.
Pre/post delta check verifies mutations actually took effect."""
import json, sys
from apply.strategies import combobox, text, select
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


def _element_value(page, sel):
    """Read field value from DOM. Waterfall:
    1. el.value (standard inputs, 85% of sites)
    2. aria-owns listbox option text (WAI-ARIA combobox pattern, 10-15%)
    3. textContent (DIV-based fields)"""
    try:
        fr = _frame_for_sel(page, sel) or page
        return (fr.evaluate(f"""() => {{
            const el = document.querySelector({json.dumps(sel)});
            if (!el) return '';
            if (el.tagName === 'SELECT') return el.options[el.selectedIndex]?.text || el.value || '';
            if (el.type === 'checkbox') return el.checked ? '__checked__' : '';
            if (el.tagName === 'DIV' || el.isContentEditable) return el.textContent?.trim() || '';
            const v = el.value || '';
            if (v) return v;
            // Standard WAI-ARIA combobox: read selected option from listbox
            const owns = el.getAttribute('aria-owns');
            if (owns) {{
                const lb = document.getElementById(owns);
                if (lb) {{
                    for (const o of lb.querySelectorAll('[role="option"]')) {{
                        if (o.getAttribute('aria-selected') === 'true') return o.textContent?.trim() || '';
                    }}
                }}
            }}
            return '';
        }}""") or "").strip()
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

    if f["tag"] == "INPUT" and f.get("type") == "checkbox":
        lbl = (label or "").lower()
        if any(kw in lbl for kw in ["agree", "consent", "accept", "terms", "confirm", "understand"]):
            try:
                cb = fr.locator(sel)
                if cb.count() and not cb.is_checked():
                    cb.check(force=True)
                    aft = _element_value(page, sel)
                    if aft != before:
                        return True
                return True
            except Exception:
                pass
        return False

    if f["tag"] == "SELECT":
        el = fr.query_selector(sel)
        if not el:
            return False
        methods = getattr(select, "METHOD_CHAIN", ["select_option"])
        for method in methods:
            if select.try_select_tag(el, f, ans, method=method):
                aft = _element_value(page, sel)
                if _check_delta(page, sel, before, aft, ans, label):
                    return True
        return _try_text_fallback(fr, f, ans, sel)

    if f.get("role") == "combobox" or f["tag"] == "DROPDOWN":
        ok = bool(combobox.fill(fr, f, ans))
        if ok:
            aft = _element_value(page, sel)
            if _check_delta(page, sel, before, aft, ans, label):
                return True
        return _try_text_fallback(fr, f, ans, sel)

    if f.get("datepicker") == "flatpickr":
        from apply.strategies import datepicker
        ok = bool(datepicker.fill(fr, sel, ans))
        if ok:
            aft = _element_value(page, sel)
            if _check_delta(page, sel, before, aft, ans, label):
                return True
        return _try_text_fallback(fr, f, ans, sel)

    if f["tag"] == "DIV" or f.get("contenteditable"):
        from apply.strategies import contenteditable as _ce
        ok = bool(_ce.fill(fr, sel, ans))
        if ok:
            aft = _element_value(page, sel)
            if _check_delta(page, sel, before, aft, ans, label):
                return True
        return _try_text_fallback(fr, f, ans, sel)

    if f["tag"] in ("INPUT", "TEXTAREA"):
        el = fr.query_selector(sel) if sel else None
        if not el:
            return False
        methods = getattr(text, "METHOD_CHAIN", ["fill"])
        for method in methods:
            if text.fill_text_field(fr, f, ans, sel, el, method=method):
                aft = _element_value(page, sel)
                if _check_delta(page, sel, before, aft, ans, label):
                    return True
        return False

    return False
