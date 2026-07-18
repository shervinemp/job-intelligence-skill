"""FieldFiller registry — extensible fill strategy chain.

Each filler implements:
  name         → traceable tag for LLM output
  can_handle   → does this filler apply to this field?
  fill         → attempt and return True/False

Fillers are tried in registry order. The last filler (TextFiller) always matches
via native_setter fallback, so the chain is always complete.

A filler that returns False causes the chain to fall through to the next.
"""
from abc import ABC, abstractmethod
from apply.steps.probe import resolve_selector
from apply.strategies import combobox, text, select, datepicker, contenteditable as ce
from apply.common.value_reader import read_value


class FieldFiller(ABC):
    @property
    @abstractmethod
    def name(self): ...

    @abstractmethod
    def can_handle(self, field: dict) -> bool: ...

    @abstractmethod
    def fill(self, page, field: dict, ans: str) -> bool: ...


# ── Concrete fillers ──────────────────────────────────────────────────────

class CheckboxFiller(FieldFiller):
    name = "checkbox"

    def can_handle(self, f):
        return f["tag"] == "INPUT" and f.get("type") == "checkbox"

    def fill(self, page, f, ans):
        lbl = (f.get("label") or "").lower()
        if not any(kw in lbl for kw in ["agree", "consent", "accept", "terms", "confirm", "understand"]):
            return False
        sel = f.get("_sel", "")
        try:
            cb = page.locator(sel)
            if cb.count() and not cb.is_checked():
                cb.check(force=True)
                return True
            return True
        except Exception:
            return False


class SelectFiller(FieldFiller):
    name = "select"

    def can_handle(self, f):
        return f["tag"] == "SELECT"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        methods = getattr(select, "METHOD_CHAIN", ["select_option"])
        for method in methods:
            if select.try_select_tag(el, f, ans, method=method):
                return True
        return False


class ComboboxFiller(FieldFiller):
    name = "combobox"

    def can_handle(self, f):
        return f.get("role") == "combobox" or f["tag"] == "DROPDOWN"

    def fill(self, page, f, ans):
        return bool(combobox.fill(page, f, ans))


class DatepickerFiller(FieldFiller):
    name = "datepicker"

    def can_handle(self, f):
        return f.get("datepicker") == "flatpickr"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        return bool(datepicker.fill(page, sel, ans))


class ContentEditableFiller(FieldFiller):
    name = "contenteditable"

    def can_handle(self, f):
        return f["tag"] == "DIV" or f.get("contenteditable")

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        return bool(ce.fill(page, sel, ans))


class TextFiller(FieldFiller):
    name = "text"

    def can_handle(self, f):
        return f["tag"] in ("INPUT", "TEXTAREA")

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        methods = getattr(text, "METHOD_CHAIN", ["fill"])
        for method in methods:
            if text.fill_text_field(page, f, ans, sel, el, method=method):
                return True
        return False


class NativeSetterFallback(FieldFiller):
    """Last-resort: tries all remaining field types via native setter."""
    name = "native_setter"

    def can_handle(self, f):
        return True  # Always matches — end of chain

    def fill(self, page, f, ans):
        from apply.strategies import text as _tx
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        if f["tag"] in ("INPUT", "TEXTAREA"):
            return bool(_tx.native_setter(page, sel, ans))
        return False


# ── Filler registry (order matters — matches existing dispatch if/elif chain) ──

_FILLERS = [
    CheckboxFiller(),
    SelectFiller(),
    ComboboxFiller(),
    DatepickerFiller(),
    ContentEditableFiller(),
    TextFiller(),
    NativeSetterFallback(),
]


def fill_field(page, field: dict, ans: str) -> tuple[bool, str]:
    """Try all fillers in order. Returns (success, filler_name)."""
    sel = field.get("_sel", "")
    if not sel:
        sel = resolve_selector(page, field)
        if not sel:
            return False, "no_selector"
        field["_sel"] = sel

    for filler in _FILLERS:
        if filler.can_handle(field):
            ok = filler.fill(page, field, ans)
            if ok:
                return True, filler.name
    return False, "none"
