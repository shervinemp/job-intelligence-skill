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
    """Read el.value ΓÇö works for all standard INPUT/SELECT/TEXTAREA fields."""
    name = "standard"

    def read(self, page, sel, ans=None):
        try:
            return (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el) return null;
                if (el.tagName === 'SELECT') return el.options[el.selectedIndex]?.text || el.value || null;
                if (el.type === 'checkbox') return el.checked ? '__checked__' : '';
                if (el.tagName === 'DIV' || el.isContentEditable) return el.textContent?.trim() || null;
                return el.value || null;
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
                const owns = el.getAttribute('aria-owns');
                if (!owns) return null;
                const lb = document.getElementById(owns);
                if (!lb) return null;
                for (const o of lb.querySelectorAll('[role="option"]')) {{
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
    The selected option text is rendered as a styled div, not in el.value."""
    name = "react_select"

    def read(self, page, sel, ans=None):
        try:
            v = (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el) return null;
                const sv = el.parentElement?.querySelector('.select__single-value');
                return sv ? sv.textContent?.trim() || null : null;
            }}""") or "").strip() or None
            return v
        except Exception:
            return None


class FuzzyComboboxReader(FieldValueReader):
    """Fallback: fuzzy-match listbox options against expected answer.
    Used by platforms (e.g. Greenhouse) that don't set aria-selected on selection.
    Only fires when ans is provided and no other reader found the value."""
    name = "fuzzy_combobox"

    def read(self, page, sel, ans=None):
        if not ans:
            return None
        try:
            v = (page.evaluate(f"""() => {{
                const el = document.querySelector({json.dumps(sel)});
                if (!el || el.getAttribute('role') !== 'combobox') return null;
                const owns = el.getAttribute('aria-owns');
                if (!owns) return null;
                const lb = document.getElementById(owns);
                if (!lb) return null;
                const a = {json.dumps(ans)};
                const aL = a.toLowerCase();
                for (const o of lb.querySelectorAll('[role="option"]')) {{
                    const t = (o.textContent || '').trim();
                    if (t.toLowerCase().includes(aL) || aL.includes(t.toLowerCase())) return t;
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
    StandardReader(),
    AriaComboboxReader(),
    ReactSelectReader(),
    FuzzyComboboxReader(),
    VisionReader(),
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
