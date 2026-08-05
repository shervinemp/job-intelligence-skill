"""FieldValueReader cascade ΓÇö single source of truth for reading field values from DOM.
Used by _element_value (dispatch), extract_fields (handlers), and read_fields (general).

Add new readers by subclassing FieldValueReader and inserting into the cascade.
The LLM sees READER: <name> tags for each value, making it easy to trace which
strategy succeeded and to add new strategies for new platforms.
"""
import json
from abc import ABC, abstractmethod
from typing import Optional


# ΓöÇΓöÇ Reader interface ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

class FieldValueReader(ABC):
    """Base class for a single value-reading strategy.
    Each reader implements read() and provides a name for traceability."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def read(self, page, sel: str, ans: Optional[str] = None) -> Optional[str]:
        """Return the field value, or None if this reader can't determine it."""
        ...


# ΓöÇΓöÇ Reader cascade ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

class StandardReader(FieldValueReader):
    """Read el.value — works for all standard INPUT/SELECT/TEXTAREA fields.
    Radio inputs are handled by RadioReader instead (el.value is 'on' for all).

    F1: only a VISIBLE element is read — a hidden/offscreen node (poisoned
    DOM text an attacker can place) must never certify a value."""
    name = "standard"

    def read(self, page, sel, ans=None):
        try:
            return (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el) return null;
                // F1: skip hidden/offscreen — a poisoned hidden element
                // must not certify the read-back.
                if (el.offsetParent === null && el.getClientRects().length === 0) return null;
                if (el.getAttribute('hidden') !== null) return null;
                const cs = getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden') return null;
                if (el.tagName === 'SELECT') return (el.options[el.selectedIndex] ? (el.options[el.selectedIndex].textContent || el.options[el.selectedIndex].text || '').trim() : '') || el.value || null;
                if (el.type === 'checkbox') return el.checked ? '__checked__' : '';
                if (el.type === 'radio') return null;
                if (el.tagName === 'BUTTON') return el.textContent?.trim() || null;
                if (el.tagName === 'DIV' || el.isContentEditable) return el.textContent?.trim() || null;
                const v = el.value || null;
                // URN/opaque-id trap (LinkedIn location typeahead): the widget
                // stores 'urn:li:geo:...' as el.value while the visible text
                // is the real answer. An opaque ID is NEVER the answer — return
                // it as-is and let the caller refuse to certify it.
                if (v && /^urn:/i.test(v)) return '__URN__:' + v;
                return v;
            }}""") or "").strip() or None
        except Exception:
            return None


class RadioReader(FieldValueReader):
    """Read radio group value by finding the checked radio's label text.
    LinkedIn radios all have value='on', so we walk up the DOM to find
    the visible label (Yes/No) of the checked radio.
    Ashby radios: label is a SIBLING (label[for=radio-id]), not a parent."""
    name = "radio"

    def read(self, page, sel, ans=None):
        try:
            return (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el || el.type !== 'radio') return null;
                const name = el.name;
                if (!name) return el.checked ? el.value : null;
                const _escN = name.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
                const radios = [...document.querySelectorAll('input[type=radio][name="' + _escN + '"]')];
                const checked = radios.find(r => r.checked);
                if (!checked) return '';
                // Try label[for] by radio id (Ashby pattern: label is sibling)
                if (checked.id) {{
                    const lbl = document.querySelector('label[for="' + checked.id + '"]');
                    if (lbl) {{
                        const txt = lbl.textContent.trim();
                        if (txt && txt.toLowerCase() !== 'on') return txt;
                    }}
                }}
                // Walk up to find label text (LinkedIn pattern)
                let el2 = checked;
                for (let i = 0; i < 4; i++) {{
                    el2 = el2.parentElement;
                    if (!el2) break;
                    const txt = el2.textContent.trim();
                    if (txt && txt.length < 200 && txt.toLowerCase() !== 'on') return txt;
                }}
                // Try <label> wrapper
                const lbl = checked.closest('label');
                if (lbl) {{
                    const txt = lbl.textContent.replace(checked.value || '', '').trim();
                    if (txt) return txt;
                }}
                return checked.value || '';
            }}""") or "").strip() or None
        except Exception:
            return None


class AriaComboboxReader(FieldValueReader):
    """Read combobox value from aria-owns listbox via aria-selected.
    Standard WAI-ARIA pattern: role=combobox ΓåÆ aria-owns ΓåÆ role=option ΓåÆ aria-selected=true."""
    name = "aria_combobox"

    def read(self, page, sel, ans=None):
        try:
            v = (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el || el.getAttribute('role') !== 'combobox') return null;
                const owns = el.getAttribute('aria-owns') || el.getAttribute('aria-controls');
                if (!owns) return null;
                const lb = document.getElementById(owns);
                if (!lb) return null;
                for (const o of lb.querySelectorAll('[role="option"]')) {{
                    // F1: skip hidden/offscreen options.
                    if (o.offsetParent === null && o.getClientRects().length === 0) continue;
                    if (o.getAttribute('hidden') !== null) continue;
                    if (o.getAttribute('aria-selected') === 'true') return o.textContent?.trim() || null;
                }}
                return null;
            }}""") or "").strip() or None
            return v
        except Exception:
            return None


class ReactSelectReader(FieldValueReader):
    """React-Select: selected value rendered in sibling div.select__single-value.
    React-Select is used by thousands of sites (Greenhouse, many others).
    The selected option text is rendered as a styled div, not in el.value.
    Lookup goes via the value-container/control ancestor — the single-value
    div is a SIBLING of the input-container, not a child of the input.
    Also handles legacy Select2 (.select2-chosen)."""
    name = "react_select"

    def read(self, page, sel, ans=None):
        try:
            v = (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el) return null;
                const scope = el.closest('.select__value-container') || el.closest('.select__control');
                if (scope) {{
                    const sv = scope.querySelector('.select__single-value');
                    if (sv) return sv.textContent?.trim() || null;
                }}
                // Legacy Select2
                const s2 = el.closest('.select2-container');
                if (s2) {{
                    const chosen = s2.querySelector('.select2-chosen');
                    if (chosen) return chosen.textContent?.trim() || null;
                }}
                return null;
            }}""") or "").strip() or None
            return v
        except Exception:
            return None


class FuzzyComboboxReader(FieldValueReader):
    """Fallback: fuzzy-match listbox options against expected answer.
    Used by platforms (e.g. Greenhouse) that don't set aria-selected on selection.
    Only fires when ans is provided and no other reader found the value.

    Bare dialing codes are handled specially: a "+1" answer must match the
    option whose COUNTRY is Canada — never the first option whose text merely
    contains "+1" (Antigua & Barbuda is also +1-268 and can sort first)."""
    name = "fuzzy_combobox"

    def read(self, page, sel, ans=None):
        if not ans:
            return None
        try:
            v = (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el || el.getAttribute('role') !== 'combobox') return null;
                const owns = el.getAttribute('aria-owns') || el.getAttribute('aria-controls');
                if (!owns) return null;
                const lb = document.getElementById(owns);
                if (!lb) return null;
                const a = {json.dumps(ans)};
                const aL = a.toLowerCase();
                const isDial = /^\\\\+\\\\d{{1,3}}$/.test(aL.trim());
                const opts = Array.from(lb.querySelectorAll('[role="option"]'));
                for (const o of opts) {{
                    // F1: skip hidden/offscreen options — poisoned hidden
                    // text must never certify a selection.
                    if (o.offsetParent === null && o.getClientRects().length === 0) continue;
                    if (o.getAttribute('hidden') !== null) continue;
                    const t = (o.textContent || '').trim();
                    if (!t) continue;
                    const tL = t.toLowerCase();
                    if (isDial) {{
                        // Dialing code answers: require an EXACT option text
                        // equal to the code (some pickers show bare "+1"),
                        // else defer to a country-name match handled outside.
                        if (tL === aL) return t;
                        continue;
                    }}
                    if (tL.includes(aL) || aL.includes(tL)) return t;
                }}
                return null;
            }}""") or "").strip() or None
            return v
        except Exception:
            return None


class VisionReader(FieldValueReader):
    """Last resort: screenshot and ask vision API if the expected value is visible.
    Only fires when ans is provided and all other readers returned empty.
    No platform assumptions ΓÇö works for any custom widget that renders values visually."""
    name = "vision"

    def read(self, page, sel, ans=None):
        if not ans:
            return None
        try:
            from lib.ask_api import ask
            import tempfile, os
            path = os.path.join(tempfile.gettempdir(), f'vision_read_{id(self)}.jpg')
            page.screenshot(path=path, full_page=False)
            result = ask(path, f'Look at this screenshot carefully. Is the value "{ans}" selected or filled in any field? Answer only YES or NO.')
            if result and result[0] and 'YES' in result[0].upper():
                return ans
            return None
        except Exception:
            return None


# ΓöÇΓöÇ Default cascade ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

_DEFAULT_CASCADE = [
    RadioReader(),
    StandardReader(),
    AriaComboboxReader(),
    ReactSelectReader(),
    FuzzyComboboxReader(),
]


def read_value(page, sel: str, ans: Optional[str] = None,
               cascade: Optional[list[FieldValueReader]] = None) -> str:
    """Read field value using the cascade. Returns empty string if all readers fail."""
    if cascade is None:
        cascade = _DEFAULT_CASCADE
    for reader in cascade:
        v = reader.read(page, sel, ans=ans)
        if v is not None:
            return v
    return ""


def read_value_vision(page, sel, ans=None):
    """Explicit vision escape — the only ask_api use in the read path.

    The routing hierarchy reserves the served local model for vision; the
    CALLER (the fill runner) decides when to invoke it, never the
    deterministic cascade. Gated on JI_LLM_MODE via lib.automation.llm.
    Returns the read value, or None when the mode forbids it, ask_api is
    down, or the model does not confirm the value.
    """
    try:
        from lib.automation.llm import allow, set_last_status
        if not allow("vision"):
            set_last_status("policy_off", "vision escape gated by JI_LLM_MODE")
            return None
    except Exception:
        return None
    if not ans:
        return None
    try:
        v = VisionReader().read(page, sel, ans=ans)
        set_last_status("used" if v else "declined", "vision read")
        return v
    except Exception:
        return None
