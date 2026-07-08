"""LinkedIn Easy Apply → PlatformHandler implementation.

Maps all the LinkedIn-specific logic into the 11-method PlatformHandler
interface. The generic run_modal_flow() in handler_base.py drives the loop.
"""

from __future__ import annotations
import json, os, time
from typing import Optional

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
    click_text_element,
    upload_file_by_text,
)

from apply.common.output import emit_status, emit_next
from apply.common.page_helpers import check_applied_signal, mark_applied

_DIALOG = '[role="dialog"], dialog'
_DIALOG_BTN = f'{_DIALOG} button'


class LinkedinHandler(PlatformHandler):
    name = "linkedin"
    domains = ["linkedin.com"]

    # ── Page state ────────────────────────────────────────────────────

    def detect(self, page) -> PageState:
        has_dialog = _dialog_open(page)
        fields = self.extract_fields(page) if has_dialog else []
        buttons = _get_buttons(page, _DIALOG) if has_dialog else []
        errors = self.get_errors(page) if has_dialog else []
        applied = self.is_applied(page)

        submit = None
        for b in buttons:
            bl = b.lower()
            if "submit" in bl:
                submit = b
                break

        # Estimate progress from dialog content
        progress = -1.0
        spans = page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return [];
            return Array.from(d.querySelectorAll('span')).map(s => s.textContent.trim());
        }}""")
        for s in spans:
            if s.endswith("%"):
                try:
                    progress = float(s.replace("%", ""))
                except ValueError:
                    pass

        has_resume_spans = any(".pdf" in s for s in spans)

        return PageState(
            flow_type=FlowType.MODAL,
            has_dialog=has_dialog,
            is_applied=applied,
            fields=fields,
            buttons=buttons,
            submit_button=submit,
            errors=errors,
            resume_step=has_resume_spans and not _has_contact_fields(spans),
            has_file_input=bool(fields and any(f.type == FieldType.FILE for f in fields)),
            progress_pct=progress,
        )

    def classify(self, page) -> str:
        dlg = page.evaluate(f"() => !!document.querySelector('{_DIALOG}')")
        if not dlg:
            if self.is_applied(page):
                return "success"
            return "unknown"
        spans = page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return [];
            return Array.from(d.querySelectorAll('span')).map(s => s.textContent.trim());
        }}""")
        if any(".pdf" in s for s in spans):
            return "form"
        if any("email" in s.lower() or "phone" in s.lower() for s in spans):
            return "form"
        if any("review" in s.lower() for s in spans):
            return "review"
        if any("submit" in s.lower() for s in spans):
            return "form"
        return "form"

    # ── Field ops ─────────────────────────────────────────────────────

    def extract_fields(self, page) -> list[Field]:
        return page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return [];
            const sel = 'input:not([type=hidden]):not([type=submit]):not([type=radio]), select, textarea';
            const inputs = d.querySelectorAll(sel);
            return Array.from(inputs).filter(el => el.offsetParent !== null).map(el => {{
                const lbl = d.querySelector('label[for="' + el.id + '"]');
                let label = lbl ? lbl.textContent.trim() : '';
                if (!label) {{
                    const ph = el.placeholder || '';
                    const aria = el.getAttribute('aria-label') || '';
                    label = ph || aria || el.name || '';
                }}
                if (!label) {{
                    const parent = el.closest('div,fieldset,section');
                    if (parent) {{
                        const h = parent.querySelector('label, legend, strong, span');
                        if (h) label = h.textContent.trim();
                    }}
                }}
                const val = (el.value || '').trim();
                const isEmpty = !val || ['select an option','select one','select...','no selection'].includes(val.toLowerCase());
                return {{
                    key: label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(),
                    label: label.slice(0, 80),
                    type: el.tagName === 'SELECT' ? 'SELECT' : (el.type === 'file' ? 'FILE' : (el.tagName === 'TEXTAREA' ? 'TEXTAREA' : 'TEXT')),
                    required: el.required || false,
                    framework: 'EMBER',
                    selector: '#' + CSS.escape(el.id),
                    value: isEmpty ? '' : val,
                    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text) : [],
                    placeholder: el.placeholder || '',
                    name: el.name || '',
                }};
            }});
        }}""")

    def fill(self, page, field: Field, value: str) -> FillResult:
        try:
            ok = set_ember_input(page, field.selector, value)
            return FillResult(ok=ok, field_key=field.key)
        except Exception as e:
            return FillResult(ok=False, field_key=field.key, error=str(e))

    def upload(self, page, field: Field, file_path: str) -> bool:
        return upload_file_by_text(page, _DIALOG, "Upload resume", file_path)

    # ── Navigation ────────────────────────────────────────────────────

    def can_proceed(self, page) -> bool:
        return page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return false;
            const btns = d.querySelectorAll('button');
            const kws = ['next', 'continue', 'done', 'review', 'submit'];
            for (const b of btns) {{
                if (b.offsetParent === null || b.disabled) continue;
                const t = (b.textContent || '').trim().toLowerCase();
                for (const kw of kws) {{
                    if (t === kw || t.startsWith(kw)) return true;
                }}
            }}
            return false;
        }}""")

    def click_next(self, page) -> ActionResult:
        clicked = _click_button_texts(page, _DIALOG, ["Next", "Continue", "Done", "Review"])
        if not clicked:
            return ActionResult(ok=False)
        time.sleep(2)
        return ActionResult(ok=True, navigated=True)

    def click_submit(self, page) -> ActionResult:
        clicked = _click_button_texts(page, _DIALOG, ["Submit application", "Submit"])
        if not clicked:
            return ActionResult(ok=False)
        time.sleep(3)
        dlg = _dialog_open(page)
        return ActionResult(ok=True, navigated=not dlg)

    def ensure_modal_open(self, page) -> bool:
        if _dialog_open(page):
            return True
        clicked = page.evaluate("""() => {
            const all = document.querySelectorAll('button, a');
            for (const el of all) {
                if (el.offsetParent === null) continue;
                const t = (el.textContent || '').trim().toLowerCase();
                if (t === 'easy apply' || t.startsWith('easy apply')) {
                    el.click(); return true;
                }
            }
            return false;
        }""")
        if not clicked:
            return False
        time.sleep(2)
        for _ in range(10):
            if _dialog_open(page):
                return True
            time.sleep(0.5)
        return _dialog_open(page)

    # ── Resume ────────────────────────────────────────────────────────

    def ensure_resume(self, page, jid: str) -> bool:
        from lib.config import RESULTS_DIR
        rd = os.path.join(RESULTS_DIR, jid)
        pdf_path = None
        target_name = None
        if os.path.isdir(rd):
            for f in sorted(os.listdir(rd)):
                if "Resume" in f and f.endswith(".pdf"):
                    pdf_path = os.path.join(rd, f)
                    target_name = f.replace(".pdf", "")
                    break
        if not pdf_path or not os.path.exists(pdf_path):
            print(f"RESUME:{jid} no tailored resume PDF found", file=sys.stderr)
            return False

        # Phase 1: find and click the tailored resume in the dialog
        safe = json.dumps(target_name)
        selected = page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return 'no_dialog';
            const target = {safe};
            const spans = d.querySelectorAll('span');
            for (const s of spans) {{
                const txt = s.textContent.trim();
                if (txt.includes('.pdf') && txt.includes(target)) {{
                    let el = s;
                    for (let i = 0; i < 15 && el; i++) {{
                        const a = el.closest('a');
                        if (a && a.offsetParent !== null) {{ a.click(); return 'selected'; }}
                        el = el.parentElement;
                    }}
                    return 'click_failed';
                }}
            }}
            return 'not_found';
        }}""")
        if selected == 'selected':
            print(f"RESUME:{jid} selected {target_name}", file=sys.stderr)
            return True
        if selected == 'not_found':
            print(f"RESUME:{jid} {target_name} not on LinkedIn — uploading...", file=sys.stderr)
        else:
            print(f"RESUME:{jid} resume selection result: {selected}", file=sys.stderr)

        # Expand resume list
        page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return;
            const btns = d.querySelectorAll('button');
            for (const b of btns) {{
                if (b.offsetParent && !b.disabled && b.textContent.trim() === 'Show 3 more resumes') {{
                    b.click(); return;
                }}
            }}
        }}""")
        time.sleep(1)

        # Phase 2: upload
        upload_ok = upload_file_by_text(page, _DIALOG, "Upload resume", pdf_path)
        if not upload_ok:
            print(f"RESUME:{jid} upload failed", file=sys.stderr)
            return False
        print(f"RESUME:{jid} uploaded {os.path.basename(pdf_path)}", file=sys.stderr)
        time.sleep(4)

        # Select after upload
        second = page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return false;
            const target = {safe};
            const spans = d.querySelectorAll('span');
            for (const s of spans) {{
                const txt = s.textContent.trim();
                if (txt.includes('.pdf') && txt.includes(target)) {{
                    let el = s;
                    for (let i = 0; i < 15 && el; i++) {{
                        const a = el.closest('a');
                        if (a && a.offsetParent !== null) {{ a.click(); return true; }}
                        el = el.parentElement;
                    }}
                }}
            }}
            return false;
        }}""")
        if second:
            print(f"RESUME:{jid} selected after upload", file=sys.stderr)
            return True
        print(f"RESUME:{jid} uploaded but selection not confirmed", file=sys.stderr)
        return True  # optimistically proceed

    # ── Signals ───────────────────────────────────────────────────────

    def is_applied(self, page) -> bool:
        return check_applied_signal(page)

    def get_errors(self, page) -> list[str]:
        return page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return [];
            const errs = d.querySelectorAll('[role="alert"], .error, .validation-error, [class*="error"]');
            return Array.from(errs).filter(e => e.offsetParent !== null).map(e => e.textContent.trim()).filter(Boolean);
        }}""")


# ─── Internal helpers ──────────────────────────────────────────────────

def _dialog_open(page) -> bool:
    return page.evaluate(f"() => !!document.querySelector('{_DIALOG}')")


def _click_button_texts(page, container: str, texts: list[str]) -> bool:
    for t in texts:
        try:
            btn = page.locator(f'{container} button:has-text("{t}")')
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                return True
        except Exception:
            pass
    return False


def _get_buttons(page, container: str) -> list[str]:
    return page.evaluate(f"""() => {{
        const d = document.querySelector('{container}');
        if (!d) return [];
        return Array.from(d.querySelectorAll('button'))
            .filter(b => b.offsetParent !== null)
            .map(b => (b.textContent || '').trim());
    }}""")


def _has_contact_fields(spans: list) -> bool:
    return any(w in s.lower() for s in spans for w in ["email", "phone", "name"])
