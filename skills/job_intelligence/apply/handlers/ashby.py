"""Ashby → PlatformHandler implementation.

Ashby uses React-based forms with standard HTML inputs (text, email, tel,
radio, checkbox, file). Form loads asynchronously after page load.
Radio inputs need grouping by name. File input uses standard set_input_files.
"""

from __future__ import annotations
import json, os, sys, time
from typing import Any

from apply.common.handler_base import (
    PlatformHandler, Field, PageState, FillResult, ActionResult,
    FieldType, Framework, FlowType, set_react_input,
)
from apply.common.page_helpers import check_applied_signal


class AshbyHandler(PlatformHandler):
    name = "ashby"
    domains = ["ashbyhq.com"]

    def _find_apply(self, page) -> bool:
        return page.evaluate("""() => {
            for (const el of document.querySelectorAll('button, a')) {
                if (el.offsetParent === null || el.disabled) continue;
                const t = (el.textContent || '').trim().toLowerCase();
                if (t.includes('apply now') || t.includes('apply for this job') || t === 'apply') {
                    el.click(); return true;
                }
            }
            return false;
        }""")

    def detect(self, page) -> PageState:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        applied = any(s in text for s in ("submitted", "thank you", "application received"))
        fields = self.extract_fields(page) if not applied else []
        buttons = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button'))
                .filter(b => b.offsetParent !== null && !b.disabled)
                .map(b => (b.textContent || '').trim().toLowerCase())
                .filter(Boolean);
        }""") or []
        submit = next((b for b in buttons if 'submit' in b), None)
        errors = self.get_errors(page) if not applied else []
        return PageState(
            flow_type=FlowType.PAGE, has_dialog=False,
            is_applied=applied, fields=fields,
            buttons=buttons, submit_button=submit,
            errors=errors,
            has_file_input=any(f.type == FieldType.FILE for f in fields),
        )

    def classify(self, page) -> str:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        if any(s in text for s in ("submitted", "thank you")):
            return "success"
        return "form"

    def extract_fields(self, page) -> list[Field]:
        raw: list[dict] = page.evaluate("""() => {
            const results = [];
            const sel = 'input:not([type=hidden]):not([type=submit]), select, textarea';
            for (const el of document.querySelectorAll(sel)) {
                if (el.offsetParent === null) continue;
                if (el.type === 'radio') continue;  // handled by grouping below
                let label = '';
                const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                if (lbl) label = lbl.textContent.trim();
                if (!label && el.placeholder) label = el.placeholder;
                if (!label && el.name) label = el.name.replace(/_/g, ' ');
                const val = (el.value || '').trim();
                const empty = !val || ['select','select...','select one','select an option'].includes(val.toLowerCase());
                let type = 'TEXT';
                if (el.tagName === 'SELECT') type = 'SELECT';
                else if (el.type === 'file') type = 'FILE';
                else if (el.tagName === 'TEXTAREA') type = 'TEXTAREA';
                results.push({
                    key: label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim() || el.name || 'unlabeled',
                    label: label.slice(0,80) || el.name || 'unlabeled',
                    type, required: el.required || false,
                    framework: 'REACT',
                    selector: '#' + CSS.escape(el.id),
                    value: empty ? '' : val,
                    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text) : [],
                    placeholder: el.placeholder || '', name: el.name || '',
                });
            }
            // Radio groups
            const groups = {};
            for (const r of document.querySelectorAll('input[type="radio"]')) {
                if (r.offsetParent === null) continue;
                const name = r.name || r.id;
                if (!groups[name]) groups[name] = { options: [], checked: null, label: '' };
                const lbl = document.querySelector('label[for="' + CSS.escape(r.id) + '"]');
                const optText = lbl ? lbl.textContent.trim() : r.value || ('Option ' + groups[name].options.length);
                groups[name].options.push({ text: optText, id: r.id });
                if (r.checked) groups[name].checked = r.id;
            }
            for (const [name, g] of Object.entries(groups)) {
                if (g.options.length < 2) continue;
                const firstId = g.options[0].id;
                const lbl = document.querySelector('label[for="' + CSS.escape(firstId) + '"]');
                const parent = lbl ? lbl.closest('fieldset,div') : null;
                let groupLabel = '';
                if (parent) {
                    const h = parent.querySelector('legend, label:not([for]), strong, span');
                    if (h) groupLabel = h.textContent.trim();
                }
                results.push({
                    key: (groupLabel || name).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(),
                    label: (groupLabel || name).slice(0,80),
                    type: 'RADIO',
                    required: true,
                    framework: 'REACT',
                    selector: g.checked ? '#' + CSS.escape(g.checked) : '#' + CSS.escape(g.options[0].id),
                    value: g.checked ? g.options.find(o => o.id === g.checked)?.text || '' : '',
                    options: g.options.map(o => o.text),
                    name: name,
                });
            }
            return results;
        }""") or []
        return [self._make(r) for r in raw]

    def _make(self, r: dict) -> Field:
        m = {"TEXT": FieldType.TEXT, "SELECT": FieldType.SELECT,
             "FILE": FieldType.FILE, "TEXTAREA": FieldType.TEXTAREA,
             "RADIO": FieldType.RADIO}
        return Field(
            key=r.get("key",""), label=r.get("label",""),
            type=m.get(r.get("type",""), FieldType.TEXT),
            required=r.get("required",False), framework=Framework.REACT,
            selector=r.get("selector",""), value=r.get("value",""),
            options=r.get("options",[]), placeholder=r.get("placeholder",""),
            name=r.get("name",""),
        )

    def fill(self, page, field: Field, value: str) -> FillResult:
        try:
            if field.type == FieldType.SELECT:
                ok = page.evaluate(f"""() => {{
                    const el = document.querySelector({json.dumps(field.selector)});
                    if (!el) return false;
                    el.value = {json.dumps(value)};
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}""")
            elif field.type == FieldType.RADIO:
                # Find the radio with matching label text
                ok = page.evaluate(f"""() => {{
                    const radios = document.querySelectorAll('input[type="radio"]');
                    const target = {json.dumps(value)};
                    for (const r of radios) {{
                        const lbl = document.querySelector('label[for="' + CSS.escape(r.id) + '"]');
                        const t = lbl ? lbl.textContent.trim() : '';
                        if (t === target || t.includes(target)) {{
                            r.click();
                            r.dispatchEvent(new Event('change', {{bubbles: true}}));
                            r.dispatchEvent(new Event('input', {{bubbles: true}}));
                            return true;
                        }}
                    }}
                    return false;
                }}""")
            elif field.type == FieldType.FILE:
                ok = True
            else:
                ok = set_react_input(page, field.selector, value)
            return FillResult(ok=ok, field_key=field.key)
        except Exception as e:
            return FillResult(ok=False, field_key=field.key, error=str(e))

    def upload(self, page, field: Field, file_path: str) -> bool:
        try:
            for sel in ['#_systemfield_resume', 'input[type="file"]']:
                inp = page.locator(sel).first
                if inp.count() > 0 and inp.first.is_visible():
                    inp.first.set_input_files(file_path)
                    time.sleep(2)
                    return True
        except Exception:
            pass
        return False

    def can_proceed(self, page) -> bool:
        btns = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button'))
                .filter(b => b.offsetParent !== null && !b.disabled)
                .map(b => (b.textContent||'').trim().toLowerCase());
        }""") or []
        return any('submit' in b for b in btns)

    def click_next(self, page) -> ActionResult:
        return ActionResult(ok=False)

    def click_submit(self, page) -> ActionResult:
        for t in ["submit application", "submit"]:
            try:
                btn = page.locator(f'button:has-text("{t}")')
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    time.sleep(3)
                    return ActionResult(ok=True, navigated=True)
            except Exception:
                pass
        return ActionResult(ok=False)

    def ensure_modal_open(self, page) -> bool:
        # Wait for React form to render (spa-wait up to 12s)
        import time as _time
        for _ in range(24):
            if len(self.extract_fields(page)) > 3:
                return True
            _time.sleep(0.5)
        # Try clicking apply button
        if self._find_apply(page):
            _time.sleep(3)
            for _ in range(12):
                if len(self.extract_fields(page)) > 3:
                    return True
                _time.sleep(0.5)
        return len(self.extract_fields(page)) > 3

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
            return True
        try:
            for sel in ['#_systemfield_resume', 'input[type="file"]']:
                inp = page.locator(sel).first
                if inp.count() > 0:
                    inp.first.set_input_files(pdf_path)
                    print(f"RESUME:{jid} uploaded {os.path.basename(pdf_path)}", file=sys.stderr)
                    time.sleep(2)
                    return True
        except Exception:
            pass
        return True

    def is_applied(self, page) -> bool:
        return check_applied_signal(page)

    def get_errors(self, page) -> list[str]:
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll('[role="alert"], .error, [class*="error"]'))
                .filter(e => e.offsetParent !== null)
                .map(e => e.textContent.trim()).filter(Boolean);
        }""") or []
