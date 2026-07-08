"""LinkedIn Easy Apply → PlatformHandler implementation.

Reference implementation for the PlatformHandler interface.
Methods below document the actual LinkedIn DOM structure so this
file serves as a template for other platforms.

LinkedIn Easy Apply modal steps:
  1. Resume selection — [role="dialog"] contains radio inputs + filename <span>s
  2. Contact info     — text inputs with placeholders (Email, Phone, etc.)
  3. Review           — summary page with "Review" button
  4. Submit           — "Submit application" button at 100% progress

DOM characteristics:
  - Labels are <span>s, not <label> elements (labels have empty for="")
  - Ember.js framework: nativeValueSetter doesn't trigger, need click+events
  - Radio buttons use <label for=...> that have empty textContent
  - File upload via "Upload resume" button triggers file chooser
  - Progress bar as "N%" text in a <span>
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
    set_ember_input,
    click_text_element,
    upload_file_by_text,
)

from apply.common.page_helpers import check_applied_signal, mark_applied
from apply.common.output import emit_status, emit_next

_DIALOG = '[role="dialog"], dialog'
_DIALOG_BTN_RADIO = f'{_DIALOG} input[type="radio"]'

_TYPE_MAP: dict[str, FieldType] = {
    "TEXT": FieldType.TEXT,
    "EMAIL": FieldType.EMAIL,
    "PHONE": FieldType.PHONE,
    "SELECT": FieldType.SELECT,
    "RADIO": FieldType.RADIO,
    "CHECKBOX": FieldType.CHECKBOX,
    "FILE": FieldType.FILE,
    "TEXTAREA": FieldType.TEXTAREA,
}

_CONTAINER_KWS = ("next", "continue", "done", "review", "submit")


class LinkedinHandler(PlatformHandler):
    name = "linkedin"
    domains = ["linkedin.com"]

    # ── Page state ────────────────────────────────────────────────────

    def detect(self, page) -> PageState:
        has_dialog = _dialog_open(page)
        fields = self.extract_fields(page) if has_dialog else []
        buttons = _get_buttons(page) if has_dialog else []
        errors = self.get_errors(page) if has_dialog else []
        applied = self.is_applied(page)

        submit = next((b for b in buttons if "submit" in b.lower()), None)
        progress = _extract_progress(page)
        spans = _get_spans(page)
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
            progress_pct=progress,
        )

    def classify(self, page) -> str:
        if not _dialog_open(page):
            return "success" if self.is_applied(page) else "unknown"
        spans = _get_spans(page)
        if any(".pdf" in s for s in spans):
            return "form"
        if any(w in " ".join(spans).lower() for w in ("email", "phone")):
            return "form"
        if any("review" in s.lower() for s in spans):
            return "review"
        return "form"

    # ── Field ops ─────────────────────────────────────────────────────

    def extract_fields(self, page) -> list[Field]:
        raw: list[dict[str, Any]] = page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return [];
            const sel = 'input:not([type=hidden]):not([type=submit]):not([type=radio]), select, textarea';
            return Array.from(d.querySelectorAll(sel))
                .filter(el => el.offsetParent !== null)
                .map(el => {{
                    const lbl = d.querySelector('label[for="' + el.id + '"]');
                    let label = lbl ? lbl.textContent.trim() : '';
                    if (!label) label = el.placeholder || el.getAttribute('aria-label') || el.name || '';
                    if (!label) {{
                        const parent = el.closest('div,fieldset,section');
                        if (parent) {{
                            const h = parent.querySelector('label, legend, strong, span');
                            if (h) label = h.textContent.trim();
                        }}
                    }}
                    const val = (el.value || '').trim();
                    const empty = !val || ['select an option','select one','select...','no selection'].includes(val.toLowerCase());
                    let type = 'TEXT';
                    if (el.tagName === 'SELECT') type = 'SELECT';
                    else if (el.type === 'file') type = 'FILE';
                    else if (el.tagName === 'TEXTAREA') type = 'TEXTAREA';
                    return {{
                        key: label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(),
                        label: label.slice(0, 80),
                        type: type,
                        required: !!el.required,
                        framework: 'EMBER',
                        selector: '#' + CSS.escape(el.id),
                        value: empty ? '' : val,
                        options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text) : [],
                        placeholder: el.placeholder || '',
                        name: el.name || '',
                    }};
                }});
        }}""")
        return [_raw_to_field(r) for r in raw]

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
            const kws = {json.dumps(list(_CONTAINER_KWS))};
            for (const b of d.querySelectorAll('button')) {{
                if (b.offsetParent === null || b.disabled) continue;
                const t = (b.textContent || '').trim().toLowerCase();
                if (kws.some(kw => t === kw || t.startsWith(kw))) return true;
            }}
            return false;
        }}""")

    def click_next(self, page) -> ActionResult:
        ok = _click_button_texts(page, ["Next", "Continue", "Done", "Review"])
        if not ok:
            return ActionResult(ok=False)
        time.sleep(2)
        return ActionResult(ok=True, navigated=True)

    def click_submit(self, page) -> ActionResult:
        ok = _click_button_texts(page, ["Submit application", "Submit"])
        if not ok:
            return ActionResult(ok=False)
        time.sleep(3)
        try:
            dlg = _dialog_open(page)
        except Exception:
            dlg = False  # page navigated during evaluate
        return ActionResult(ok=True, navigated=not dlg)

    def ensure_modal_open(self, page) -> bool:
        if _dialog_open(page):
            return True
        if not page.evaluate("""() => {
            for (const el of document.querySelectorAll('button, a')) {
                if (el.offsetParent === null) continue;
                const t = (el.textContent || '').trim().toLowerCase();
                if (t === 'easy apply' || t.startsWith('easy apply')) {
                    el.click(); return true;
                }
            }
            return false;
        }"""):
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
        target_name = None
        pdf_path = None
        if os.path.isdir(rd):
            for f in sorted(os.listdir(rd)):
                if "Resume" in f and f.endswith(".pdf"):
                    pdf_path = os.path.join(rd, f)
                    target_name = f.replace(".pdf", "")
                    break
        if not pdf_path or not os.path.exists(pdf_path):
            print(f"RESUME:{jid} no tailored resume PDF found", file=sys.stderr)
            return False

        safe = json.dumps(target_name)
        selected = page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return 'no_dialog';
            for (const s of d.querySelectorAll('span')) {{
                const txt = s.textContent.trim();
                if (!txt.includes('.pdf') || !txt.includes({safe})) continue;
                let el = s;
                for (let i = 0; i < 15 && el; i++) {{
                    const a = el.closest('a');
                    if (a && a.offsetParent !== null) {{ a.click(); return 'selected'; }}
                    el = el.parentElement;
                }}
                return 'click_failed';
            }}
            return 'not_found';
        }}""")
        if selected == 'selected':
            print(f"RESUME:{jid} selected {target_name}", file=sys.stderr)
            return True

        if selected == 'not_found':
            print(f"RESUME:{jid} {target_name} not on LinkedIn — uploading...", file=sys.stderr)
        else:
            print(f"RESUME:{jid} resume selection: {selected}", file=sys.stderr)

        _expand_resume_list(page)
        if not upload_file_by_text(page, _DIALOG, "Upload resume", pdf_path):
            print(f"RESUME:{jid} upload failed", file=sys.stderr)
            return False
        print(f"RESUME:{jid} uploaded {os.path.basename(pdf_path)}", file=sys.stderr)
        time.sleep(4)

        if page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return false;
            for (const s of d.querySelectorAll('span')) {{
                const txt = s.textContent.trim();
                if (!txt.includes('.pdf') || !txt.includes({safe})) continue;
                let el = s;
                for (let i = 0; i < 15 && el; i++) {{
                    const a = el.closest('a');
                    if (a && a.offsetParent !== null) {{ a.click(); return true; }}
                    el = el.parentElement;
                }}
            }}
            return false;
        }}"""):
            print(f"RESUME:{jid} selected after upload", file=sys.stderr)
            return True
        print(f"RESUME:{jid} uploaded, proceeding", file=sys.stderr)
        return True

    # ── Signals ───────────────────────────────────────────────────────

    def is_applied(self, page) -> bool:
        return check_applied_signal(page)

    def get_errors(self, page) -> list[str]:
        return page.evaluate(f"""() => {{
            const d = document.querySelector('{_DIALOG}');
            if (!d) return [];
            return Array.from(d.querySelectorAll('[role="alert"], .error, [class*="error"]'))
                .filter(e => e.offsetParent !== null)
                .map(e => e.textContent.trim())
                .filter(Boolean);
        }}""")


# ─── Internal helpers ──────────────────────────────────────────────────

def _raw_to_field(r: dict) -> Field:
    return Field(
        key=r.get("key", ""),
        label=r.get("label", ""),
        type=_TYPE_MAP.get(r.get("type", ""), FieldType.TEXT),
        required=r.get("required", False),
        framework=Framework.EMBER,
        selector=r.get("selector", ""),
        value=r.get("value", ""),
        options=r.get("options", []),
        placeholder=r.get("placeholder", ""),
        name=r.get("name", ""),
    )


def _dialog_open(page) -> bool:
    return page.evaluate(f"() => !!document.querySelector('{_DIALOG}')")


def _get_spans(page) -> list[str]:
    return page.evaluate(f"""() => {{
        const d = document.querySelector('{_DIALOG}');
        if (!d) return [];
        return Array.from(d.querySelectorAll('span')).map(s => s.textContent.trim());
    }}""")


def _extract_progress(page) -> float:
    for s in _get_spans(page):
        if s.endswith("%"):
            try:
                return float(s.replace("%", ""))
            except ValueError:
                pass
    return -1.0


def _get_buttons(page) -> list[str]:
    return page.evaluate(f"""() => {{
        const d = document.querySelector('{_DIALOG}');
        if (!d) return [];
        return Array.from(d.querySelectorAll('button'))
            .filter(b => b.offsetParent !== null)
            .map(b => (b.textContent || '').trim());
    }}""")


def _click_button_texts(page, texts: list[str]) -> bool:
    for t in texts:
        try:
            sel = f'[role="dialog"] button:has-text("{t}"), dialog button:has-text("{t}")'
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                return True
        except Exception:
            pass
    return False


def _expand_resume_list(page):
    page.evaluate(f"""() => {{
        const d = document.querySelector('{_DIALOG}');
        if (!d) return;
        for (const b of d.querySelectorAll('button')) {{
            if (b.offsetParent && !b.disabled && b.textContent.trim() === 'Show 3 more resumes') {{
                b.click(); return;
            }}
        }}
    }}""")
    time.sleep(1)


def _has_contact_fields(spans: list[str]) -> bool:
    text = " ".join(spans).lower()
    return any(w in text for w in ("email", "phone", "name"))
