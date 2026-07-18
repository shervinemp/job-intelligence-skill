"""FieldValueReader cascade — single source of truth for reading field values from DOM.
Used by _element_value (dispatch), extract_fields (handlers), and read_fields (general).

Add new readers by subclassing FieldValueReader and inserting into the cascade.
The LLM sees READER: <name> tags for each value, making it easy to trace which
strategy succeeded and to add new strategies for new platforms.
"""
import json
from abc import ABC, abstractmethod
from typing import Optional


# ── Reader interface ──────────────────────────────────────────────────────

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


# ── Reader cascade ─────────────────────────────────────────────────────────

class StandardReader(FieldValueReader):
    """Read el.value — works for all standard INPUT/SELECT/TEXTAREA fields."""
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
    Standard WAI-ARIA pattern: role=combobox → aria-owns → role=option → aria-selected=true."""
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


# ── Default cascade ────────────────────────────────────────────────────────

_DEFAULT_CASCADE = [
    StandardReader(),
    AriaComboboxReader(),
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
