"""Greenhouse → PlatformHandler implementation.

Greenhouse uses standard HTML form fields (input, select, textarea)
with React. The apply form can be single or multi-page.
"""

from __future__ import annotations
import json, os, sys, time
from typing import Any

from apply.common.handler_base import (
    PlatformHandler,
    Field,
    PageState,
    FillResult,
    ActionResult,
    FieldType,
    Framework,
    FlowType,
    set_react_input,
)

from apply.common.page_helpers import check_applied_signal, mark_applied

_SUCCESS_TEXTS = (
    "application has been submitted",
    "application submitted",
    "thank you for your application",
    "you have successfully applied",
)

_SUBMIT_TEXTS = ("submit application", "submit")
_NEXT_TEXTS = ("next", "continue")
_FILE_SEL = 'input[type="file"]'


class GreenhouseHandler(PlatformHandler):
    name = "greenhouse"
    domains = ["greenhouse.io", "boards.greenhouse.io", "grnh.se"]

    def detect(self, page) -> PageState:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        applied = any(s in text for s in _SUCCESS_TEXTS)
        fields = self.extract_fields(page) if not applied else []

        buttons = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'))
                .filter(el => el.offsetParent !== null && !el.disabled)
                .map(el => (el.textContent || el.value || '').trim().toLowerCase())
                .filter(Boolean);
        }""") or []

        submit = next((b for b in buttons if any(s in b for s in _SUBMIT_TEXTS)), None)
        errors = self.get_errors(page) if not applied else []
        has_file = bool(page.evaluate(f"() => document.querySelector('{_FILE_SEL}')"))

        return PageState(
            flow_type=FlowType.PAGE,
            has_dialog=False,
            is_applied=applied,
            fields=fields,
            buttons=buttons,
            submit_button=submit,
            errors=errors,
            has_file_input=has_file,
        )

    def classify(self, page) -> str:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        if any(s in text for s in _SUCCESS_TEXTS):
            return "success"
        btns = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'))
                .filter(el => el.offsetParent !== null)
                .map(el => (el.textContent || el.value || '').trim().toLowerCase());
        }""") or []
        if any("submit" in b for b in btns):
            return "review"
        if any("next" in b for b in btns):
            return "form"
        return "form"

    def extract_fields(self, page) -> list[Field]:
        raw: list[dict[str, Any]] = page.evaluate("""() => {
            const results = [];
            const form = document.querySelector('form') || document.body;
            const sel = 'input:not([type=hidden]):not([type=submit]):not([type=radio]), select, textarea';
            for (const el of form.querySelectorAll(sel)) {
                if (el.offsetParent === null) continue;
                const lbl = form.querySelector('label[for="' + el.id + '"]');
                let label = '';
                if (lbl) label = lbl.textContent.trim();
                if (!label && el.placeholder) label = el.placeholder;
                if (!label) {
                    const parent = el.closest('div,fieldset,section');
                    if (parent) {
                        const h = parent.querySelector('label, legend, strong, span, p');
                        if (h) label = h.textContent.trim();
                    }
                }
                if (!label) label = el.name || '';
                const val = (el.value || '').trim();
                const empty = !val || ['select', 'select...', 'select one', 'select an option', 'none', 'no selection'].includes(val.toLowerCase());
                let type = 'TEXT';
                if (el.tagName === 'SELECT') type = 'SELECT';
                else if (el.type === 'file') type = 'FILE';
                else if (el.tagName === 'TEXTAREA') type = 'TEXTAREA';
                results.push({
                    key: label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim() || el.name,
                    label: label.slice(0, 80) || el.name || 'unlabeled',
                    type: type,
                    required: el.required || el.hasAttribute('aria-required'),
                    framework: 'REACT',
                    selector: '#' + CSS.escape(el.id),
                    value: empty ? '' : val,
                    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text) : [],
                    placeholder: el.placeholder || '',
                    name: el.name || '',
                });
            }
            return results;
        }""") or []
        return [self._raw_to_field(r) for r in raw]

    def _raw_to_field(self, r: dict) -> Field:
        type_map = {
            "TEXT": FieldType.TEXT, "SELECT": FieldType.SELECT,
            "FILE": FieldType.FILE, "TEXTAREA": FieldType.TEXTAREA,
        }
        return Field(
            key=r.get("key", ""),
            label=r.get("label", ""),
            type=type_map.get(r.get("type", ""), FieldType.TEXT),
            required=r.get("required", False),
            framework=Framework.REACT,
            selector=r.get("selector", ""),
            value=r.get("value", ""),
            options=r.get("options", []),
            placeholder=r.get("placeholder", ""),
            name=r.get("name", ""),
        )

    def fill(self, page, field: Field, value: str) -> FillResult:
        try:
            if field.type == FieldType.SELECT:
                ok = page.evaluate(f"""() => {{
                    const el = document.querySelector({json.dumps(field.selector)});
                    if (!el) return false;
                    el.value = {json.dumps(value)};
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    return true;
                }}""")
            elif field.type == FieldType.FILE:
                ok = page.evaluate(f"""() => {{
                    const el = document.querySelector({json.dumps(field.selector)});
                    if (!el) return false;
                    el.style.display = 'block';
                    return true;
                }}""")
            else:
                ok = set_react_input(page, field.selector, value)
            return FillResult(ok=ok, field_key=field.key)
        except Exception as e:
            return FillResult(ok=False, field_key=field.key, error=str(e))

    def upload(self, page, field: Field, file_path: str) -> bool:
        try:
            inp = page.locator(_FILE_SEL).first
            inp.set_input_files(file_path)
            time.sleep(2)
            return True
        except Exception:
            return False

    def can_proceed(self, page) -> bool:
        btns = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'))
                .filter(el => el.offsetParent !== null && !el.disabled)
                .map(el => (el.textContent || el.value || '').trim().toLowerCase());
        }""") or []
        kw = _SUBMIT_TEXTS + _NEXT_TEXTS + ("review", "done")
        return any(any(k in b for k in kw) for b in btns)

    def click_next(self, page) -> ActionResult:
        for t in _NEXT_TEXTS:
            try:
                btn = page.locator(f'button:has-text("{t}"), a[role="button"]:has-text("{t}"), input[type="submit"][value*="{t}"]')
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    time.sleep(2)
                    return ActionResult(ok=True, navigated=True)
            except Exception:
                pass
        return ActionResult(ok=False)

    def click_submit(self, page) -> ActionResult:
        for t in _SUBMIT_TEXTS:
            try:
                btn = page.locator(f'button:has-text("{t}"), a[role="button"]:has-text("{t}"), input[type="submit"][value*="{t}"]')
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    time.sleep(3)
                    return ActionResult(ok=True, navigated=True)
            except Exception:
                pass
        return ActionResult(ok=False)

    def ensure_modal_open(self, page) -> bool:
        """Open the Greenhouse apply form. Handles login walls and 'Apply Now' buttons."""
        # Check if form fields are already visible
        fields = self.extract_fields(page)
        if len(fields) > 2:
            return True

        text = (page.evaluate("() => document.body.innerText") or "").lower()

        # Handle guest apply / login wall
        if "sign in to apply" in text or "already have an account" in text:
            guest = page.evaluate("""() => {
                for (const el of document.querySelectorAll('button, a')) {
                    const t = (el.textContent || '').trim().toLowerCase();
                    if (t.includes('continue without signing in') || t.includes('apply as guest')) {
                        el.click(); return true;
                    }
                }
                return false;
            }""")
            if guest:
                time.sleep(3)
                return len(self.extract_fields(page)) > 1

        # Click "Apply Now" or "Apply for this job" to reveal the form
        clicked = page.evaluate("""() => {
            const kws = ['apply now', 'apply for this job', 'apply', 'submit application'];
            for (const el of document.querySelectorAll('button, a')) {
                if (el.offsetParent === null || el.disabled) continue;
                const t = (el.textContent || '').trim().toLowerCase();
                for (const kw of kws) {
                    if (t.includes(kw)) { el.click(); return true; }
                }
            }
            return false;
        }""")
        if clicked:
            time.sleep(3)
            return True

        return len(self.extract_fields(page)) > 1

    def ensure_resume(self, page, jid: str) -> bool:
        from lib.config import RESULTS_DIR
        rd = os.path.join(RESULTS_DIR, jid)
        pdf_path = None
        if os.path.isdir(rd):
            for f in sorted(os.listdir(rd)):
                if "Resume" in f and f.endswith(".pdf"):
                    pdf_path = os.path.join(rd, f)
                    break
        if not pdf_path or not os.path.exists(pdf_path):
            print(f"RESUME:{jid} no tailored resume found", file=sys.stderr)
            return True  # Greenhouse may auto-use profile, don't block
        try:
            inp = page.locator(_FILE_SEL).first
            if inp.count() > 0:
                inp.set_input_files(pdf_path)
                print(f"RESUME:{jid} uploaded {os.path.basename(pdf_path)}", file=sys.stderr)
                time.sleep(2)
            return True
        except Exception as e:
            print(f"RESUME:{jid} upload error: {e}", file=sys.stderr)
            return True

    def is_applied(self, page) -> bool:
        return check_applied_signal(page)

    def get_errors(self, page) -> list[str]:
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll('[role="alert"], .error, [class*="error"], .field-error'))
                .filter(e => e.offsetParent !== null)
                .map(e => e.textContent.trim())
                .filter(Boolean);
        }""") or []
