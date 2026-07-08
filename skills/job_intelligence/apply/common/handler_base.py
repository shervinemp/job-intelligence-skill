"""Platform handler interface for job application forms.

── How to add a new platform ──────────────────────────────────────────

1. Create apply/handlers/<name>.py  with a class that inherits PlatformHandler.

2. Implement all 11 abstract methods (your IDE can auto-generate stubs).
   See LinkedinHandler in apply/handlers/linkedin.py as a reference.

3. Create apply/registry/<name>.yaml with at minimum:
     name: <name>
     detect:
       domains: [<domain>]
     handler_class: apply.handlers.<name>.<ClassName>

4. That's it. The run_modal_flow() runner loops detect → fill → advance.
   No changes needed to act.py, registry.py, or any other file.

── What each method should do ─────────────────────────────────────────

  detect(page)         → Full page snapshot: fields, buttons, errors, progress.
  classify(page)       → Quick string: 'form' | 'review' | 'success' | 'login'.

  extract_fields(page) → Visible fillable fields with labels.
  fill(page, field, v) → Set field using the right framework setter.
  upload(page, f, p)   → File upload (widget-specific).

  can_proceed(page)    → True if a next/submit button exists & is enabled.
  click_next(page)     → Click next/continue. Return navigated=True if URL changed.
  click_submit(page)   → Click submit. Return navigated=True if dialog closed.
  ensure_modal_open(p) → Open the form overlay if closed (for MODAL flow_type).

  ensure_resume(p, jid)→ Select or upload the tailored resume. Return False to block.
  is_applied(page)     → True if success signal detected.
  get_errors(page)     → Validation error messages on the current page.

── Shared utilities you can use ──────────────────────────────────────

  set_react_input(page, selector, value)    React nativeValueSetter
  set_ember_input(page, selector, value)    Ember click+events / nativeValueSetter
  set_vanilla_input(page, selector, value)  element.value + change

  find_text_in_dialog(page, text)           Search dialog for text
  click_text_element(page, container, text) Click first <a>/<button> containing text
  upload_file_by_text(page, container, t, p) Click + file chooser

  Field, PageState, FillResult, ActionResult  Return these from your methods.

── FlowType ──────────────────────────────────────────────────────────

  MODAL   — Ephemeral overlay (LinkedIn). Runner re-opens if closed.
  PAGE    — Multi-step form on sequential pages (Workday).
  SINGLE  — One-page form, single submit (most ATS).
  REDIRECT — Redirects to external URL (Greenhouse).
  MAILTO  — Opens mail client (cannot automate).
  LOGIN_WALL — Auth required before form.
"""

from __future__ import annotations
import json, os, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from apply.common.page_helpers import check_applied_signal, mark_applied
from apply.common.output import emit_status, emit_next
from apply.common.resolve import resolution_for_fill
from apply.common import audit


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
    REACT = auto()
    EMBER = auto()
    VUE = auto()
    ANGULAR = auto()
    VANILLA = auto()
    CONTENTEDITABLE = auto()


class FlowType(Enum):
    """Top-level flow pattern. The runner dispatches on this."""
    MODAL = auto()
    PAGE = auto()
    REDIRECT = auto()
    SINGLE = auto()
    MAILTO = auto()
    LOGIN_WALL = auto()


# ─── Dataclasses ───────────────────────────────────────────────────────

@dataclass
class Field:
    """One fillable field on the current page."""

    key: str
    label: str
    type: FieldType
    required: bool
    framework: Framework
    selector: str
    value: str = ""
    options: list[str] = field(default_factory=list)
    placeholder: str = ""
    name: str = ""


@dataclass
class PageState:
    """Snapshot of the current form page."""

    flow_type: FlowType = FlowType.MODAL
    step: int = 0
    total_steps: int = 0
    has_dialog: bool = False
    captured: bool = False
    login_required: bool = False
    session_timed_out: bool = False
    rate_limited: bool = False
    is_applied: bool = False

    fields: list[Field] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    submit_button: Optional[str] = None
    errors: list[str] = field(default_factory=list)

    resume_step: bool = False
    has_file_input: bool = False
    progress_pct: float = -1.0


@dataclass
class FillResult:
    ok: bool
    field_key: str = ""
    error: str = ""


@dataclass
class ActionResult:
    ok: bool
    error: str = ""
    navigated: bool = False


# ─── Handler interface ─────────────────────────────────────────────────

class PlatformHandler(ABC):
    """Implement one per platform/ATS."""

    name: str = ""
    domains: list[str] = []

    # ── Page state ────────────────────────────────────────────────────

    @abstractmethod
    def detect(self, page) -> PageState:
        ...

    @abstractmethod
    def classify(self, page) -> str:
        ...

    # ── Field ops ─────────────────────────────────────────────────────

    @abstractmethod
    def extract_fields(self, page) -> list[Field]:
        ...

    @abstractmethod
    def fill(self, page, field: Field, value: str) -> FillResult:
        ...

    @abstractmethod
    def upload(self, page, field: Field, file_path: str) -> bool:
        ...

    # ── Navigation ────────────────────────────────────────────────────

    @abstractmethod
    def can_proceed(self, page) -> bool:
        ...

    @abstractmethod
    def click_next(self, page) -> ActionResult:
        ...

    @abstractmethod
    def click_submit(self, page) -> ActionResult:
        ...

    @abstractmethod
    def ensure_modal_open(self, page) -> bool:
        ...

    # ── Resume ────────────────────────────────────────────────────────

    @abstractmethod
    def ensure_resume(self, page, jid: str) -> bool:
        ...

    # ── Signals ───────────────────────────────────────────────────────

    @abstractmethod
    def is_applied(self, page) -> bool:
        ...

    @abstractmethod
    def get_errors(self, page) -> list[str]:
        ...


# ─── JS helper: safe string interpolation ──────────────────────────────

def _js(val: str) -> str:
    """JSON-encode a Python string for safe embedding in a JS template literal."""
    return json.dumps(val)


def safe_eval(page, js: str, default=None):
    """Evaluate JS on page, returning `default` if the page navigated or crashed.
    
    Handles the common case where page.evaluate() is called right after a
    navigation action (click next/submit) and the execution context is gone.
    """
    import time as _time
    _time.sleep(0.5)  # brief settle before evaluating
    try:
        return page.evaluate(js)
    except Exception as e:
        err = str(e)
        if "Execution context was destroyed" in err or "Connection closed" in err:
            return default
        raise


# ─── Framework setters (shared) ────────────────────────────────────────

def _dialog_sel() -> str:
    return '[role="dialog"], dialog'


def set_react_input(page, selector: str, value: str) -> bool:
    """React-aware value setter: nativeValueSetter + input/change dispatch."""
    return page.evaluate(f"""() => {{
        const el = document.querySelector({_js(selector)});
        if (!el) return false;
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(el, {_js(value)});
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }}""")


def set_ember_input(page, selector: str, value: str) -> bool:
    """Ember.js value setter: handles select, radio, and text inputs."""
    return page.evaluate(f"""() => {{
        const el = document.querySelector({_js(selector)});
        if (!el) return false;
        if (el.tagName === 'SELECT') {{
            // LinkedIn uses native <select> hidden under a custom widget.
            // Set value + dispatch change to trigger LinkedIn's handlers.
            el.value = {_js(value)};
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            return true;
        }}
        if (el.tagName === 'INPUT' && el.type === 'radio') {{
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl && lbl.offsetParent !== null) {{ lbl.click(); return true; }}
            let parent = el.parentElement;
            for (let i = 0; i < 8 && parent; i++) {{
                if (parent.offsetParent === null) {{ parent = parent.parentElement; continue; }}
                if (parent.tagName === 'LABEL' || parent.classList.contains('fb-form-element')) {{
                    parent.click(); return true;
                }}
                parent = parent.parentElement;
            }}
            el.click();
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('click', {{bubbles: true}}));
            return true;
        }}
        // Text / textarea / other inputs
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        if (setter) {{
            setter.call(el, {_js(value)});
        }} else {{
            el.value = {_js(value)};
        }}
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }}""")


def set_vanilla_input(page, selector: str, value: str) -> bool:
    """Vanilla JS setter: element.value + change dispatch."""
    return page.evaluate(f"""() => {{
        const el = document.querySelector({_js(selector)});
        if (!el) return false;
        el.value = {_js(value)};
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }}""")


# ─── DOM traversal helpers (shared) ────────────────────────────────────

def find_text_in_dialog(page, text: str) -> Optional[dict]:
    """Search all visible elements in the dialog for the given text.
    Returns {tag, text} dict, or None.
    """
    dsel = _dialog_sel()
    return page.evaluate(f"""() => {{
        const d = document.querySelector({_js(dsel)});
        if (!d) return null;
        for (const el of d.querySelectorAll('button, a, span, div, label, p')) {{
            if (el.offsetParent === null) continue;
            const t = (el.textContent || '').trim();
            if (t.includes({_js(text)})) return {{ tag: el.tagName, text: t.slice(0, 60) }};
        }}
        return null;
    }}""")


def click_text_element(page, dialog_selector: str, text: str) -> bool:
    """Find an element containing `text` and click the first clickable ancestor.
    Walks up 15 parents looking for <a>, <button>, [tabindex], [role=button].
    """
    return page.evaluate(f"""() => {{
        const d = document.querySelector({_js(dialog_selector)});
        if (!d) return false;
        for (const el of d.querySelectorAll('button, a, span, div, label, p')) {{
            if (el.offsetParent === null) continue;
            if (!(el.textContent || '').trim().includes({_js(text)})) continue;
            let parent = el;
            for (let i = 0; i < 15 && parent; i++) {{
                const clickable = parent.tagName === 'A' || parent.tagName === 'BUTTON'
                    || parent.getAttribute('tabindex') === '0'
                    || parent.getAttribute('role') === 'button';
                if (clickable && parent.offsetParent !== null) {{
                    parent.click();
                    return true;
                }}
                parent = parent.parentElement;
            }}
        }}
        return false;
    }}""")


def upload_file_by_text(page, dialog_selector: str, text: str, file_path: str) -> bool:
    """Click an element with `text` and set the file via file chooser.
    Returns True only if the file was accepted (chooser opened and file set).
    """
    import os as _os
    if not _os.path.exists(file_path):
        return False
    try:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            if not click_text_element(page, dialog_selector, text):
                return False
        fc = fc_info.value
        fc.set_files(file_path)
        time.sleep(3)
        return True
    except Exception:
        return False


def wait_for_fields(handler, page, timeout: float = 12.0, min_fields: int = 3) -> bool:
    """Poll handler.extract_fields() until enough fields appear or timeout.
    
    Returns True if enough fields were found within the timeout.
    Use in ensure_modal_open() for SPA platforms that load forms asynchronously.
    """
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if len(handler.extract_fields(page)) >= min_fields:
            return True
        _time.sleep(0.5)
    return len(handler.extract_fields(page)) >= min_fields


# ─── Generic flow runner ──────────────────────────────────────────────

def run_modal_flow(
    handler: PlatformHandler,
    page,
    jid: str,
    profile: dict,
    *,
    allow_submit: bool = False,
    max_steps: int = 10,
    dry_run: bool = False,
) -> str:
    """Generic multi-step modal flow.

    Loops: detect → fill required fields → try submit → try next → repeat.

    When dry_run=True: resolves all field→answer mappings and prints them
    without modifying the DOM. Returns "paused" after preview.

    Returns:
        "done"    — application submitted successfully
        "paused"  — needs LLM input or manual action
        "failed"  — cannot proceed
    """
    if not handler.ensure_modal_open(page):
        if handler.is_applied(page):
            mark_applied(jid)
            return "done"
        return "failed"

    # Upload tailored resume if applicable (handler checks current page state)
    if not handler.ensure_resume(page, jid):
        emit_status("blocked", "tailored resume not available")
        emit_next("detect to retry")
        return "failed"

    for _ in range(max_steps):
        state = handler.detect(page)

        if state.is_applied:
            mark_applied(jid)
            return "done"

        if not state.has_dialog and state.flow_type == FlowType.MODAL:
            if handler.is_applied(page):
                mark_applied(jid)
                return "done"
            emit_status("dialog_closed", "modal closed without submit")
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

        # Preview: show all field→value mappings before any DOM changes.
        # Always prints — both in dry-run and apply mode. In apply mode, proceeds
        # immediately after printing so the user can see what's about to happen.
        print(f"\n  ── FIELDS ({len(state.fields)} total) ──", file=sys.stderr)
        unfilled_count = 0
        for f in state.fields:
            if f.value:
                print(f"  ✓ [{f.type.name:6s}] {f.label[:45]:45s} = {f.value[:50]}", file=sys.stderr)
            else:
                res = resolution_for_fill(f.key, profile)
                val = res.value if res and res.value else ""
                if val:
                    print(f"  ∼ [{f.type.name:6s}] {f.label[:45]:45s} → {val[:50]} (from profile)", file=sys.stderr)
                else:
                    print(f"  ? [{f.type.name:6s}] {f.label[:45]:45s}  <-- needs your input", file=sys.stderr)
                    unfilled_count += 1

        if dry_run:
            emit_status("paused", "preview complete — re-run with --apply to fill and submit")
            emit_next("act --fill --answers '{\"<label>\": \"<value>\"}' --apply")
            return "paused"

        # Fill unfilled required fields — try profile match only (no guesses)
        filled_any = False
        for f in state.fields:
            if f.required and not f.value:
                res = resolution_for_fill(f.key, profile)
                val = res.value if res and res.value else ""
                if val:
                    r = handler.fill(page, f, val)
                    if r.ok:
                        audit.log_field(jid, f.key, val, provenance="profile")
                        filled_any = True

        # Don't pause on validation errors — try advancing anyway.
        # The error might clear, be non-blocking, or surface on the next page.
        if state.errors:
            emit_status("validation_errors", "; ".join(state.errors[:3]))

        # Submit
        if allow_submit and state.submit_button:
            r = handler.click_submit(page)
            if r.navigated or r.ok:
                time.sleep(2)
                if not handler.is_applied(page):
                    mark_applied(jid)  # page navigated = submit assumed successful
                else:
                    mark_applied(jid)
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
