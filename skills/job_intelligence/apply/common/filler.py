"""FieldFiller registry — extensible fill strategy chain with built-in verification.

Each filler implements:
  name         → traceable tag for LLM output
  can_handle   → does this filler apply to this field?
  fill         → attempt and return True/False

Fillers are tried in registry order. The last filler (NativeSetterFallback) always
matches, so the chain is always complete.

fill_field() wraps the chain with pre/post-fill verification:
  - Reads value before fill
  - Dispatches to the matching filler
  - Reads value after fill
  - Verifies the value changed (delta check)
  - Falls back to text strategies if the filler reported success but value didn't stick

This merges the former dispatch.py verify logic into the filler itself,
eliminating the two-layer indirection (act → dispatch → filler → strategy).
"""
from abc import ABC, abstractmethod
import json
import os

from apply.common.field_types import is_combobox as _is_combobox
from apply.common.value_reader import read_value as _read_value
from apply.common.output import emit_diag
from apply.steps.probe import resolve_selector
from apply.strategies import combobox, text, select, datepicker, contenteditable as ce


# ─── Frame helpers ────────────────────────────────────────────────────

def _frame_for_sel(page, sel):
    """Find Playwright frame containing element matching sel."""
    for f in page.frames:
        try:
            if f.evaluate(f"() => !!document.querySelector({json.dumps(sel)})"):
                return f
        except Exception:
            continue
    return None


def _read_element_value(page, sel, ans=None, field=None):
    """Read field value using FieldValueReader cascade.
    Combobox-role fields skip StandardReader (phantom-success source)."""
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


def _check_delta(before, after, ans, label):
    """True if the value changed meaningfully."""
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


# ─── Filler ABC ───────────────────────────────────────────────────────

class FieldFiller(ABC):
    @property
    @abstractmethod
    def name(self): ...

    @abstractmethod
    def can_handle(self, field: dict) -> bool: ...

    @abstractmethod
    def fill(self, page, field: dict, ans: str) -> bool: ...


# ─── Concrete fillers ─────────────────────────────────────────────────

class CheckboxFiller(FieldFiller):
    name = "checkbox"

    def can_handle(self, f):
        return f["tag"] == "INPUT" and f.get("type") == "checkbox"

    def fill(self, page, f, ans):
        lbl = (f.get("label") or "").lower()
        if not any(kw in lbl for kw in ["agree", "consent", "accept", "terms", "confirm",
                                        "understand", "authorize", "certify", "marketing",
                                        "privacy", "notice", "future job", "updates"]):
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


class RadioFiller(FieldFiller):
    name = "radio"

    def can_handle(self, f):
        return f.get("tag") == "RADIO_GROUP" or f.get("type") == "radio"

    def fill(self, page, f, ans):
        sel = f.get("_sel", f.get("selector", ""))
        if not sel:
            return False
        ans_lower = str(ans).strip().lower()
        # For Yes/No radios, reduce long answers to just "yes" or "no"
        # (e.g. "Yes, in the Toronto office" → "yes")
        yn_prefix = ""
        if ans_lower.startswith("yes"):
            yn_prefix = "yes"
        elif ans_lower.startswith("no"):
            yn_prefix = "no"
        try:
            clicked = page.evaluate("""(args) => {
                const [sel, ans, ynPref] = args;
                const radios = [...document.querySelectorAll(sel)];
                if (!radios.length) return false;

                // Helper: get a radio's visible label text
                function radioLabel(r) {
                    const lbl = r.closest('label');
                    if (lbl) {
                        const txt = lbl.textContent.replace(r.value || '', '').trim().toLowerCase();
                        if (txt) return txt;
                    }
                    let el = r;
                    for (let i = 0; i < 4; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        const txt = el.textContent.trim().toLowerCase();
                        if (txt && txt.length < 30 && txt !== 'on') return txt;
                    }
                    let sib = r.nextElementSibling;
                    while (sib) {
                        const txt = sib.textContent.trim().toLowerCase();
                        if (txt && txt.length < 30) return txt;
                        sib = sib.nextElementSibling;
                    }
                    return (r.value || '').toLowerCase();
                }

                // Try 1: match by value (standard HTML)
                for (const r of radios) {
                    if (r.value && r.value.toLowerCase() === ans) { r.click(); return true; }
                }
                // Try 1b: Yes/No prefix match (e.g. ans="yes, in the toronto office" → match "yes")
                if (ynPref) {
                    for (const r of radios) {
                        const lbl = radioLabel(r);
                        if (lbl === ynPref || lbl.startsWith(ynPref)) { r.click(); return true; }
                    }
                }
                // Try 2: match by associated <label> text
                for (const r of radios) {
                    const lbl = r.closest('label');
                    if (lbl) {
                        const txt = lbl.textContent.replace(r.value || '', '').trim().toLowerCase();
                        if (txt === ans || txt.startsWith(ans) || ans.startsWith(txt)) { r.click(); return true; }
                    }
                }
                // Try 3: walk up 4 levels and match text (LinkedIn pattern)
                for (const r of radios) {
                    let el = r;
                    for (let i = 0; i < 4; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        const txt = el.textContent.trim().toLowerCase();
                        if (txt === ans || txt.startsWith(ans + ' ') || ans.startsWith(txt)) {
                            r.click(); return true;
                        }
                    }
                }
                // Try 4: check next sibling text (label after radio)
                for (const r of radios) {
                    let sib = r.nextElementSibling;
                    while (sib) {
                        const txt = sib.textContent.trim().toLowerCase();
                        if (txt && txt.length < 30 && (txt === ans || txt.startsWith(ans) || ans.startsWith(txt))) {
                            r.click(); return true;
                        }
                        sib = sib.nextElementSibling;
                    }
                }
                return false;
            }""", [sel, ans_lower, yn_prefix])
            return bool(clicked)
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
        for method in getattr(select, "METHOD_CHAIN", ["select_option"]):
            if select.try_select_tag(el, f, ans, method=method):
                return True
        return False


class ComboboxFiller(FieldFiller):
    name = "combobox"

    def can_handle(self, f):
        return _is_combobox(f)

    def fill(self, page, f, ans):
        return bool(combobox.fill(page, f, ans))


class DatepickerFiller(FieldFiller):
    name = "datepicker"

    def can_handle(self, f):
        return f.get("datepicker") == "flatpickr"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        try:
            page.evaluate(f"() => {{ const el = document.querySelector({json.dumps(sel)}); if (el && el._flatpickr) el._flatpickr.setDate('{ans}') }}")
            return True
        except Exception:
            return False


class ContentEditableFiller(FieldFiller):
    name = "contenteditable"

    def can_handle(self, f):
        return f["tag"] == "DIV" or f.get("contenteditable")

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        return bool(ce.fill(page, sel, ans))


class FileFiller(FieldFiller):
    name = "file"

    def can_handle(self, f):
        return (f.get("type") or "").lower() == "file" or f.get("tag") == "INPUT" and (f.get("type") or "").lower() == "file"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        if not sel:
            return False
        # ans should be a file path; if it's not a valid path, skip
        path = str(ans).strip()
        if not os.path.isfile(path):
            return False
        try:
            # Try direct set_input_files
            page.set_input_files(sel, path)
            return True
        except Exception:
            pass
        # Try in frames
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                fr.set_input_files(sel, path)
                return True
            except Exception:
                continue
        # Try JS: find hidden file input and set via DataTransfer
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(path)
                return True
        except Exception:
            pass
        return False


class TextFiller(FieldFiller):
    name = "text"

    def can_handle(self, f):
        if _is_combobox(f):
            return False
        if (f.get("type") or "").lower() in ("checkbox", "radio", "file"):
            return False
        return f["tag"] in ("INPUT", "TEXTAREA")

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        for method in getattr(text, "METHOD_CHAIN", ["fill"]):
            if text.fill_text_field(page, f, ans, sel, el, method=method):
                return True
        return False


class NativeSetterFallback(FieldFiller):
    name = "native_setter"

    def can_handle(self, f):
        return True

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        if _is_combobox(f):
            return False
        if f["tag"] in ("INPUT", "TEXTAREA"):
            return bool(text.native_setter(page, sel, ans))
        return False


# ─── Filler registry (order matters) ──────────────────────────────────

_FILLERS = [
    CheckboxFiller(),
    RadioFiller(),
    FileFiller(),
    SelectFiller(),
    ComboboxFiller(),
    DatepickerFiller(),
    ContentEditableFiller(),
    TextFiller(),
    NativeSetterFallback(),
]


def _try_text_fallback(page, f, ans, sel):
    """Last-resort cross-type fallback after primary filler failed to stick."""
    if _is_combobox(f):
        return False
    if f.get("contenteditable") or f["tag"] == "DIV":
        return bool(ce.fill(page, sel, ans))
    el = page.query_selector(sel)
    if el and f["tag"] in ("INPUT", "TEXTAREA"):
        for method in getattr(text, "METHOD_CHAIN", ["fill"]):
            if text.fill_text_field(page, f, ans, sel, el, method=method):
                return True
    return False


def fill_field(page, field: dict, ans: str) -> tuple[bool, str]:
    """Try all fillers in order. Returns (success, filler_name).
    Includes pre/post-fill verification — the value must actually change."""
    sel = field.get("_sel", "")
    if not sel:
        sel = resolve_selector(page, field)
        if not sel:
            return False, "no_selector"
        field["_sel"] = sel

    fr = _frame_for_sel(page, sel) or page
    label = field.get("label", "")

    # Comboboxes: trust the filler's own verification (option poll + click).
    # Read-back via value_reader races the DOM and can wipe real selections.
    if _is_combobox(field):
        for filler in _FILLERS:
            if filler.can_handle(field):
                if filler.fill(fr, field, ans):
                    return True, filler.name
        return False, "none"

    before = _read_element_value(page, sel, field=field)

    for filler in _FILLERS:
        if filler.can_handle(field):
            if filler.fill(fr, field, ans):
                aft = _read_element_value(page, sel, ans=ans, field=field)
                if _check_delta(before, aft, ans, label):
                    return True, filler.name

    # Primary chain failed — try text fallback
    if _try_text_fallback(fr, field, ans, sel):
        aft2 = _read_element_value(page, sel, ans=ans, field=field)
        if isinstance(ans, list):
            ans_s = ", ".join(str(v) for v in ans)
        else:
            ans_s = str(ans) if ans is not None else ""
        if aft2 and (aft2 != before) and (not ans_s or aft2 == ans_s or ans_s in aft2 or aft2 in ans_s):
            return True, "text_fallback"
        if ans_s and aft2 and (aft2 == ans_s or ans_s in aft2 or aft2 in ans_s):
            return True, "text_fallback"

    return False, "none"
