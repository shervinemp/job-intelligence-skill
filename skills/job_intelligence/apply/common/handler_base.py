"""Platform handler interface for job application forms.

Every job board / ATS platform implements PlatformHandler.
The flow runner calls detect → fill → advance/submit in a loop.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ─── Enums ──────────────────────────────────────────────────────────────

class FieldType(Enum):
    TEXT = auto()
    EMAIL = auto()
    PHONE = auto()
    SELECT = auto()
    RADIO = auto()
    CHECKBOX = auto()
    FILE = auto()
    TEXTAREA = auto()


class Framework(Enum):
    """How the framework exposes its controlled input values."""
    REACT = auto()      # nativeValueSetter + dispatch input/change
    EMBER = auto()      # Ember.set() or trigger custom events
    VUE = auto()        # __v_model + dispatch input
    ANGULAR = auto()    # dispatch input/change on native element
    VANILLA = auto()    # element.value = x + dispatchEvent
    CONTENTEDITABLE = auto()  # element.innerText = x + dispatch


class FlowType(Enum):
    """Top-level flow pattern. The runner dispatches on this."""
    MODAL = auto()      # Ephemeral overlay (LinkedIn Easy Apply)
    PAGE = auto()       # Multi-step form on sequential pages (Workday)
    REDIRECT = auto()   # Redirects to external ATS (Greenhouse)
    SINGLE = auto()     # Single-page form, one-shot submit (most)
    MAILTO = auto()     # Opens mail client (cannot automate)
    LOGIN_WALL = auto()  # Auth required before form


# ─── Dataclasses ───────────────────────────────────────────────────────

@dataclass
class Field:
    """One fillable field on the current page."""

    key: str                     # Normalized label — matches resolution_for_fill
    label: str                   # Original text (for display / answers key)
    type: FieldType
    required: bool
    framework: Framework         # How to set this field
    selector: str                # Playwright locator string
    value: str = ""              # Current value; empty = unfilled
    options: list[str] = field(default_factory=list)  # SELECT / RADIO choices
    placeholder: str = ""
    name: str = ""               # HTML name attribute


@dataclass
class PageState:
    """Snapshot of the current form page."""

    flow_type: FlowType = FlowType.MODAL
    step: int = 0                # 0 = unknown, 1+ = which step
    total_steps: int = 0         # 0 = unknown
    has_dialog: bool = False
    captured: bool = False       # CAPTCHA / Turnstile present
    login_required: bool = False
    session_timed_out: bool = False
    rate_limited: bool = False
    is_applied: bool = False

    fields: list[Field] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    submit_button: Optional[str] = None
    errors: list[str] = field(default_factory=list)

    resume_step: bool = False    # Page is asking for resume selection
    has_file_input: bool = False  # <input type="file"> present

    progress_pct: float = -1.0   # -1 when unknown


# ─── Results ────────────────────────────────────────────────────────────

@dataclass
class FillResult:
    ok: bool
    field_key: str = ""
    error: str = ""


@dataclass
class ActionResult:
    ok: bool
    error: str = ""
    navigated: bool = False      # Page URL changed after action


# ─── Handler interface ─────────────────────────────────────────────────

class PlatformHandler(ABC):
    """Implement one per platform/ATS. See docstrings for contract."""

    name: str = ""
    domains: list[str] = field(default_factory=list)

    # ── Page state ────────────────────────────────────────────────────

    @abstractmethod
    def detect(self, page) -> PageState:
        """Full snapshot of the current form page.
        
        Must return a PageState that accurately reflects what's on screen.
        Called at the start of each flow iteration.
        """
        ...

    @abstractmethod
    def classify(self, page) -> str:
        """Quick page-type string: 'form', 'review', 'success', 'login', 'error'."""
        ...

    # ── Field ops ─────────────────────────────────────────────────────

    @abstractmethod
    def extract_fields(self, page) -> list[Field]:
        """Discover all visible fillable fields and their current values.
        
        Must return Fields with meaningful `key` values that
        resolution_for_fill() can match against profile + answers.
        The label discovery heuristic is per-platform because every
        ATS renders fields differently.
        """
        ...

    @abstractmethod
    def fill(self, page, field: Field, value: str) -> FillResult:
        """Set a field's value using the right framework setter.
        
        React:  nativeValueSetter + input/change dispatch
        Ember:  Ember.set() or label.click + custom events
        Vue:    __v_model setter + input dispatch
        Vanilla: element.value + change dispatch
        """
        ...

    @abstractmethod
    def upload(self, page, field: Field, file_path: str) -> bool:
        """Upload a file. Handles <input type="file"> and custom chooser widgets."""
        ...

    # ── Navigation ────────────────────────────────────────────────────

    @abstractmethod
    def can_proceed(self, page) -> bool:
        """True if a next/continue/submit button exists and is enabled.
        Checks both visible submit buttons and disabled states."""
        ...

    @abstractmethod
    def click_next(self, page) -> ActionResult:
        """Click the next/continue button. Returns True if page changed."""
        ...

    @abstractmethod
    def click_submit(self, page) -> ActionResult:
        """Click the submit button. Returns True if page changed or dialog closed."""
        ...

    @abstractmethod
    def ensure_modal_open(self, page) -> bool:
        """For MODAL flow_type: open the ephemeral overlay if closed.
        
        Called before the first detect and on session_timeout recovery.
        Returns True if modal is now open.
        """
        ...

    # ── Resume ────────────────────────────────────────────────────────

    @abstractmethod
    def ensure_resume(self, page, jid: str) -> bool:
        """Select or upload the tailored resume.
        
        Returns False if the right resume can't be found and can't be
        uploaded — the flow runner must not proceed without it.
        """
        ...

    # ── Signals ───────────────────────────────────────────────────────

    @abstractmethod
    def is_applied(self, page) -> bool:
        """Positive success signal: thank-you text, "You applied", page redirect."""
        ...

    @abstractmethod
    def get_errors(self, page) -> list[str]:
        """Validation errors currently visible on the page."""
        ...


# ─── Framework setters (shared) ────────────────────────────────────────

# These can be used by any handler.fill() implementation.
# They live here so they're co-located with the Framework enum.

def set_react_input(page, selector: str, value: str) -> bool:
    """React-aware value setter: nativeValueSetter + input/change dispatch."""
    return page.evaluate(f"""() => {{
        const el = document.querySelector({selector!r});
        if (!el) return false;
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(el, {value!r});
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }}""")


def set_ember_input(page, selector: str, value: str) -> bool:
    """Ember.js value setter: click + change/input/click dispatch on radio.
    For text inputs, tries nativeValueSetter first, then Ember.set().
    """
    return page.evaluate(f"""() => {{
        const el = document.querySelector({selector!r});
        if (!el) return false;
        if (el.tagName === 'INPUT' && el.type === 'radio') {{
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl) lbl.click();
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('click', {{bubbles: true}}));
        }} else {{
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, {value!r});
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        return true;
    }}""")


def set_vanilla_input(page, selector: str, value: str) -> bool:
    """Vanilla JS setter: element.value + change dispatch."""
    return page.evaluate(f"""() => {{
        const el = document.querySelector({selector!r});
        if (!el) return false;
        el.value = {value!r};
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }}""")


# ─── DOM traversal helpers (shared) ────────────────────────────────────

def find_text_in_dialog(page, text: str) -> Optional[str]:
    """Search all visible elements in the dialog for the given text.
    Returns the element tag name + first matching text, or None.
    """
    return page.evaluate(f"""() => {{
        const d = document.querySelector('[role="dialog"], dialog');
        if (!d) return null;
        const all = d.querySelectorAll('button, a, span, div, label, p');
        for (const el of all) {{
            if (el.offsetParent === null) continue;
            const t = (el.textContent || '').trim();
            if (t.includes({text!r})) return {{ tag: el.tagName, text: t.slice(0, 60) }};
        }}
        return null;
    }}""")


def click_text_element(page, dialog_selector: str, text: str) -> bool:
    """Find an element containing `text` and click the first clickable ancestor.
    
    Walks up 15 parents looking for <a>, <button>, or [tabindex].
    Returns True if clicked.
    """
    return page.evaluate(f"""() => {{
        const d = document.querySelector({dialog_selector!r});
        if (!d) return false;
        const all = d.querySelectorAll('button, a, span, div, label, p');
        for (const el of all) {{
            if (el.offsetParent === null) continue;
            if (!(el.textContent || '').trim().includes({text!r})) continue;
            let parent = el;
            for (let i = 0; i < 15 && parent; i++) {{
                if ((parent.tagName === 'A' || parent.tagName === 'BUTTON') && parent.offsetParent !== null) {{
                    parent.click();
                    return true;
                }}
                parent = parent.parentElement;
            }}
        }}
        return false;
    }}""")


def upload_file_by_text(page, dialog_selector: str, text: str, file_path: str) -> bool:
    """Click an element with `text` (e.g. 'Upload resume') and set the file.
    Uses Playwright's file chooser API. Returns True if upload was triggered.
    """
    import time, os
    try:
        from playwright.sync_api import expect
    except ImportError:
        return False
    with page.expect_file_chooser() as fc_info:
        clicked = click_text_element(page, dialog_selector, text)
        if not clicked:
            return False
    fc = fc_info.value
    fc.set_files(file_path)
    time.sleep(3)
    return True


# ─── Generic flow runner ──────────────────────────────────────────────

def run_modal_flow(
    handler: PlatformHandler,
    page,
    jid: str,
    profile: dict,
    *,
    allow_submit: bool = False,
    max_steps: int = 10,
) -> str:
    """Generic multi-step modal flow.
    
    Loops: detect → fill fields → try submit → try next → repeat.
    
    Returns:
        "done"    — application submitted successfully
        "paused"  — needs LLM input or manual action
        "failed"  — cannot proceed
    """
    import time
    from apply.common.page_helpers import check_applied_signal, mark_applied
    from apply.common.output import emit_status, emit_next

    if not handler.ensure_modal_open(page):
        if handler.is_applied(page):
            mark_applied(jid)
            return "done"
        return "failed"

    if not handler.ensure_resume(page, jid):
        emit_status("blocked", "tailored resume not available")
        emit_next("detect to retry")
        return "failed"

    for step in range(max_steps):
        state = handler.detect(page)

        if state.is_applied:
            mark_applied(jid)
            return "done"

        if not state.has_dialog and state.flow_type == FlowType.MODAL:
            if handler.is_applied(page):
                mark_applied(jid)
                return "done"
            emit_status("dialog_closed", "modal closed without submit reply")
            emit_next("verify")
            return "paused"

        if state.login_required:
            emit_status("login_required")
            emit_next("log in and retry")
            return "paused"

        if state.captured:
            emit_status("captcha")
            emit_next("solve captcha and retry")
            return "paused"

        if state.rate_limited:
            emit_status("rate_limited")
            emit_next("wait and retry")
            return "paused"

        if state.session_timed_out:
            handler.ensure_modal_open(page)
            continue

        # Fill unfilled required fields
        filled_any = False
        for f in state.fields:
            if f.required and not f.value:
                from apply.common.resolve import resolution_for_fill
                from apply.common import audit

                res = resolution_for_fill(f.key, profile)
                if res and res.value:
                    r = handler.fill(page, f, res.value)
                    if r.ok:
                        audit.log_field(jid, f.key, res.value, provenance=res.provenance)
                        filled_any = True

        if not filled_any and state.errors:
            emit_status("validation_errors", "; ".join(state.errors))
            emit_next("act --fill --answers '{\"<label>\": \"<value>\"}'")
            return "paused"

        # Submit
        if allow_submit and state.submit_button:
            r = handler.click_submit(page)
            if r.navigated or r.ok:
                time.sleep(2)
                if handler.is_applied(page):
                    mark_applied(jid)
                    return "done"
                emit_status("submitted")
                emit_next("verify")
                return "done"

        # Next
        if handler.can_proceed(page):
            handler.click_next(page)
            time.sleep(2)
            continue

        # Stuck
        emit_status("paused", "no actionable button")
        emit_next("act --fill")
        return "paused"

    emit_status("paused", "max steps reached")
    emit_next("verify")
    return "paused"
