"""Field fill dispatch — routes to correct strategy by field type."""
from apply.strategies import combobox, text, select


def field_deterministic(page, f, ans):
    sel = f.get("_sel", "")
    if not sel:
        return False
    if f["tag"] == "INPUT" and f.get("type") == "checkbox":
        lbl = (f.get("label") or "").lower()
        if any(kw in lbl for kw in ["agree", "consent", "accept", "terms", "confirm", "understand"]):
            try:
                cb = page.locator(sel)
                if cb.count() and not cb.is_checked():
                    cb.check(force=True)
                    return True
            except Exception:
                pass
        return False
    if f["tag"] == "SELECT":
        el = page.query_selector(sel)
        return bool(el and select.try_select_tag(el, f, ans))
    if f.get("role") == "combobox" or f["tag"] == "DROPDOWN":
        return bool(combobox.fill(page, f, ans))
    if f.get("datepicker") == "flatpickr":
        from apply.strategies import datepicker
        return bool(datepicker.fill(page, sel, ans))
    if f["tag"] == "DIV" or f.get("contenteditable"):
        from apply.strategies import contenteditable
        return bool(contenteditable.fill(page, sel, ans))
    if f["tag"] in ("INPUT", "TEXTAREA"):
        el = page.query_selector(sel) if sel else None
        return text.fill_text_field(page, f, ans, sel, el)
    return False
