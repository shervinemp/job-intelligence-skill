"""Workday → PlatformHandler implementation.

Workday is a multi-step SPA. Some instances allow guest apply,
others require account creation. Uses data-automation-id for stable selectors.
"""

from __future__ import annotations
import json, os, sys, time, re
from typing import Any

from apply.common.handler_base import (
    PlatformHandler, Field, PageState, FillResult, ActionResult,
    FieldType, Framework, FlowType, set_react_input, set_vanilla_input,
)
from apply.common.page_helpers import check_applied_signal, mark_applied

_GUEST_TEXTS = ("continue without signing in", "apply as guest", "apply manually")
_LOGIN_TEXTS = ("sign in", "create account", "password", "email address")
_SUCCESS_TEXTS = ("thank you", "submitted", "application received")


class WorkdayHandler(PlatformHandler):
    name = "workday"
    domains = ["myworkdayjobs.com", "workday.com", "wday.com"]

    def detect(self, page) -> PageState:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        applied = any(s in text for s in _SUCCESS_TEXTS)
        if applied:
            return PageState(flow_type=FlowType.PAGE, is_applied=True)

        # Detect login wall
        login_required = any(w in text for w in _LOGIN_TEXTS)
        has_guest = any(w in text for w in _GUEST_TEXTS) if login_required else False

        fields = self.extract_fields(page) if not login_required else []
        buttons = self._get_buttons(page)
        submit = next((b for b in buttons if 'submit' in b), None)
        errors = self.get_errors(page) if not applied and not login_required else []

        return PageState(
            flow_type=FlowType.PAGE, has_dialog=False,
            login_required=login_required,
            is_applied=applied, fields=fields,
            buttons=buttons, submit_button=submit,
            errors=errors,
        )

    def classify(self, page) -> str:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        if any(s in text for s in _SUCCESS_TEXTS): return "success"
        if any(w in text for w in _LOGIN_TEXTS): return "login"
        return "form"

    def extract_fields(self, page) -> list[Field]:
        raw: list[dict] = page.evaluate("""() => {
            const results = [];
            // Standard inputs
            for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea')) {
                if (el.offsetParent === null || el.type === 'radio') continue;
                let label = '';
                const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                if (lbl) label = lbl.textContent.trim();
                if (!label) label = el.placeholder || el.getAttribute('aria-label') || el.name || '';
                if (!label) {
                    const p = el.closest('div,fieldset');
                    if (p) { const h = p.querySelector('label, legend, span'); if (h) label = h.textContent.trim(); }
                }
                const val = (el.value || '').trim();
                let type = 'TEXT';
                if (el.tagName === 'SELECT') type = 'SELECT';
                else if (el.type === 'file') type = 'FILE';
                else if (el.tagName === 'TEXTAREA') type = 'TEXTAREA';
                results.push({
                    key: label.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim() || el.name || 'unlabeled',
                    label: label.slice(0,80) || el.name || 'unlabeled',
                    type, required: el.required || false,
                    framework: 'REACT', selector: '#' + CSS.escape(el.id),
                    value: val, placeholder: el.placeholder || '', name: el.name || '',
                });
            }
            // Custom dropdowns (aria-haspopup)
            for (const btn of document.querySelectorAll('button[aria-haspopup]')) {
                if (btn.offsetParent === null) continue;
                const text = (btn.textContent || '').trim();
                if (!text || text.length > 40) continue;
                results.push({
                    key: 'dropdown_' + text.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim(),
                    label: text.slice(0,80), type: 'SELECT',
                    required: true, framework: 'VANILLA',
                    selector: '#' + CSS.escape(btn.id || ''),
                    value: text, placeholder: '', name: btn.id || '',
                });
            }
            return results;
        }""") or []
        return [self._make(r) for r in raw]

    def _make(self, r: dict) -> Field:
        m = {"TEXT": FieldType.TEXT, "SELECT": FieldType.SELECT,
             "FILE": FieldType.FILE, "TEXTAREA": FieldType.TEXTAREA}
        return Field(
            key=r.get("key",""), label=r.get("label",""),
            type=m.get(r.get("type",""), FieldType.TEXT),
            required=r.get("required",False),
            framework=Framework.REACT if r.get("framework") == "REACT" else Framework.VANILLA,
            selector=r.get("selector",""), value=r.get("value",""),
            placeholder=r.get("placeholder",""), name=r.get("name",""),
        )

    def fill(self, page, field: Field, value: str) -> FillResult:
        try:
            if 'dropdown_' in field.key:
                ok = page.evaluate(f"""() => {{
                    const btn = document.querySelector({json.dumps(field.selector)});
                    if (!btn) return false;
                    btn.click();
                    setTimeout(() => {{
                        const opts = document.querySelectorAll('[role="listbox"] [role="option"], [role="listbox"] li, .menuItems button');
                        const target = {json.dumps(value)};
                        for (const opt of opts) {{
                            if ((opt.textContent || '').trim().toLowerCase() === target.toLowerCase()) {{
                                opt.click(); return;
                            }}
                        }}
                    }}, 300);
                    return true;
                }}""")
                time.sleep(0.5)
            elif field.framework == Framework.VANILLA:
                ok = set_vanilla_input(page, field.selector, value)
                time.sleep(0.3)
            else:
                ok = set_react_input(page, field.selector, value)
            return FillResult(ok=ok, field_key=field.key)
        except Exception as e:
            return FillResult(ok=False, field_key=field.key, error=str(e))

    def upload(self, page, field: Field, file_path: str) -> bool:
        try:
            inp = page.locator('input[type="file"]').first
            if inp.count() > 0:
                inp.set_input_files(file_path)
                time.sleep(2)
                return True
        except Exception:
            pass
        return False

    def _get_buttons(self, page) -> list[str]:
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button'))
                .filter(b => b.offsetParent !== null && !b.disabled)
                .map(b => (b.textContent || '').trim().toLowerCase())
                .filter(Boolean);
        }""") or []

    def can_proceed(self, page) -> bool:
        btns = self._get_buttons(page)
        return any(w in ' '.join(btns) for w in ['next', 'review', 'submit', 'done'])

    def click_next(self, page) -> ActionResult:
        for t in ["next", "continue"]:
            try:
                btn = page.locator(f'button:has-text("{t}")')
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(); time.sleep(2)
                    return ActionResult(ok=True, navigated=True)
            except Exception: pass
        return ActionResult(ok=False)

    def click_submit(self, page) -> ActionResult:
        for t in ["submit", "review", "done"]:
            try:
                btn = page.locator(f'button:has-text("{t}")')
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(); time.sleep(3)
                    return ActionResult(ok=True, navigated=True)
            except Exception: pass
        return ActionResult(ok=False)

    def ensure_modal_open(self, page) -> bool:
        """Handle login wall or open the form."""
        # Wait up to 12s for SPA to render
        for _ in range(24):
            if len(self.extract_fields(page)) > 2:
                return True
            time.sleep(0.5)

        text = (page.evaluate("() => document.body.innerText") or "").lower()

        # If login wall, check for guest option
        if any(w in text for w in _LOGIN_TEXTS):
            for t in _GUEST_TEXTS:
                if t in text:
                    # Click guest apply link/button
                    clicked = page.evaluate(f"""() => {{
                        for (const el of document.querySelectorAll('button, a')) {{
                            if (el.offsetParent === null || el.disabled) continue;
                            if ((el.textContent||'').trim().toLowerCase().includes({json.dumps(t)})) {{
                                el.click(); return true;
                            }}
                        }}
                        return false;
                    }}""")
                    if clicked:
                        time.sleep(4)
                        for _ in range(12):
                            if len(self.extract_fields(page)) > 1:
                                return True
                            time.sleep(0.5)
                        return len(self.extract_fields(page)) > 1
            return False  # login wall, no guest option

        # Try clicking an "Apply" button if not on login wall
        clicked = page.evaluate("""() => {
            for (const el of document.querySelectorAll('button, a')) {
                if (el.offsetParent === null || el.disabled) continue;
                const t = (el.textContent||'').trim().toLowerCase();
                if (t === 'apply' || t.includes('apply now')) { el.click(); return true; }
                if (t.includes('back to job posting')) { el.click(); return true; }
            }
            return false;
        }""")
        if clicked:
            time.sleep(4)
            return len(self.extract_fields(page)) > 1

        return len(self.extract_fields(page)) > 1

    def ensure_resume(self, page, jid: str) -> bool:
        from lib.config import RESULTS_DIR
        rd = os.path.join(RESULTS_DIR, jid)
        pdf_path = None
        if os.path.isdir(rd):
            for f in sorted(os.listdir(rd)):
                if "Resume" in f and f.endswith(".pdf"):
                    pdf_path = os.path.join(rd, f); break
        if not pdf_path or not os.path.exists(pdf_path): return True
        try:
            inp = page.locator('input[type="file"]').first
            if inp.count() > 0:
                inp.set_input_files(pdf_path)
                print(f"RESUME:{jid} uploaded", file=sys.stderr); time.sleep(2)
            return True
        except Exception: return True

    def is_applied(self, page) -> bool: return check_applied_signal(page)

    def get_errors(self, page) -> list[str]:
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll('[role="alert"], .error, [class*="error"]'))
                .filter(e => e.offsetParent !== null)
                .map(e => e.textContent.trim()).filter(Boolean);
        }""") or []
