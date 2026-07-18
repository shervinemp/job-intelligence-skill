"""Standard HTML form handler — generic fallback for most ATS platforms.

Covers the common case: <form> with <input>, <select>, <textarea> elements
using vanilla or React frameworks. Handles multi-page forms via Next/Submit detection.
"""

from __future__ import annotations
import json, os, sys, time, re
from typing import Any, Optional

from apply.common.handler_base import (
    PlatformHandler, Field, PageState, FillResult, ActionResult,
    FieldType, Framework, FlowType, set_react_input, set_vanilla_input,
    upload_file_by_text, wait_for_fields, safe_eval,
)

from apply.common.page_helpers import check_applied_signal, mark_applied

_SUCCESS_TEXTS = (
    "application has been submitted", "application submitted",
    "thank you for your application", "you have successfully applied",
    "your application was sent", "application received",
    "we have received your application",
)
_SUBMIT_TEXTS = ("submit application", "submit", "send application")
_NEXT_TEXTS = ("next", "continue", "review", "done")
_FILE_SEL = 'input[type="file"]'

COMMON_ATS_CONFIGS: dict[str, dict] = {
    "lever.co": {
        "apply_btn": ["apply for this job", "apply now", "apply"],
        "guest_btn": ["apply as guest", "continue without signing in"],
        "submit_btn": ["submit application", "submit"],
    },
    "ashbyhq.com": {
        "apply_btn": ["apply", "apply now", "apply for this job"],
        "submit_btn": ["submit application", "submit"],
    },
    "bamboohr.com": {
        "apply_btn": ["apply now", "apply for this job", "apply"],
        "submit_btn": ["submit application", "submit"],
    },
    "myworkdayjobs.com": {
        "apply_btn": ["apply now", "apply"],
        "submit_btn": ["submit", "review & submit"],
        "next_btn": ["next", "continue"],
    },
}


class StandardFormHandler(PlatformHandler):
    """Handles standard HTML forms with profile-based auto-fill.
    
    Auto-detects ATS platform from URL for apply-button specifics.
    Falls back to generic heuristics for unknown platforms.
    """

    name = "standard_form"
    domains = []  # Registered per-ATS via YAML, or used as generic fallback

    def __init__(self):
        self._config: dict = {}

    def _cfg(self, page_url: str = "") -> dict:
        if self._config:
            return self._config
        if page_url:
            for domain, cfg in COMMON_ATS_CONFIGS.items():
                if domain in page_url.lower():
                    self._config = cfg
                    return cfg
        self._config = {}
        return self._config

    # ── Page state ────────────────────────────────────────────────────

    def detect(self, page) -> PageState:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        applied = any(s in text for s in _SUCCESS_TEXTS)
        fields = self.extract_fields(page) if not applied else []
        buttons = self._get_buttons(page)
        cfg = self._cfg(page.url)

        submit = next((b for b in buttons if any(s in b for s in cfg.get("submit_btn", _SUBMIT_TEXTS))), None)
        if not submit:
            submit = next((b for b in buttons if any(s in b for s in _SUBMIT_TEXTS)), None)
        errors = self.get_errors(page) if not applied else []
        has_file = bool(page.evaluate(f"() => !!document.querySelector('{_FILE_SEL}')"))

        return PageState(
            flow_type=FlowType.PAGE,
            has_dialog=False,
            is_applied=applied,
            fields=fields,
            buttons=[b for b in buttons if b],
            submit_button=submit,
            errors=errors,
            has_file_input=has_file,
        )

    def classify(self, page) -> str:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        if any(s in text for s in _SUCCESS_TEXTS):
            return "success"
        btns = self._get_buttons(page)
        if any("submit" in b for b in btns):
            return "review"
        if any(b in ("next", "continue", "review") for b in btns):
            return "form"
        return "form"

    # ── Field ops ─────────────────────────────────────────────────────

    def extract_fields(self, page) -> list[Field]:
        # Try the quick DOM scan first
        raw: list[dict[str, Any]] = page.evaluate("""() => {
            const form = document.querySelector('form') || document.body;
            const sel = 'input:not([type=hidden]):not([type=submit]):not([type=radio]), select, textarea';
            const results = [];
            const seen = new Set();

            for (const el of form.querySelectorAll(sel)) {
                if (el.offsetParent === null) continue;
                if (el.type === 'file' || seen.has(el.id)) continue;
                seen.add(el.id);

                let label = '';
                const lbl = form.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                if (lbl) label = lbl.textContent.trim();
                if (!label && el.placeholder) label = el.placeholder;
                if (!label) {
                    const parent = el.closest('div,fieldset,section,label');
                    if (parent) {
                        const h = parent.querySelector('label, legend, strong, span, p');
                        if (h) label = h.textContent.trim();
                    }
                }
                if (!label) label = el.getAttribute('aria-label') || el.title || '';
                if (!label) label = el.name || '';

                const val = (el.value || '').trim();
                const empty = !val || ['select', 'select...', 'select one', 'select an option', 'none', 'no selection', 'choose'].includes(val.toLowerCase());
                let type = 'TEXT', framework = 'REACT';
                if (el.tagName === 'SELECT') { type = 'SELECT'; framework = 'VANILLA'; }
                else if (el.type === 'file') { type = 'FILE'; framework = 'VANILLA'; }
                else if (el.tagName === 'TEXTAREA') type = 'TEXTAREA';

                results.push({
                    key: label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim() || el.name || 'unlabeled',
                    label: label.slice(0, 80) || el.name || 'unlabeled',
                    type: type,
                    required: el.required || el.hasAttribute('aria-required') || false,
                    framework: framework,
                    selector: '#' + CSS.escape(el.id),
                    value: empty ? '' : val,
                    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text) : [],
                    placeholder: el.placeholder || '',
                    name: el.name || '',
                });
            }
            return results;
        }""") or []
        if raw:
            return [self._make_field(r) for r in raw]

        # Fallback: use the field_reader's more sophisticated scan (handles
        # Backbone/Marionette SPAs, dynamic rendering, shadow DOM, etc.)
        from apply.common.field_reader import read_fields as _rf
        result = _rf(page)
        return [self._make_field(r) for r in result.get("fields", [])]

    def _make_field(self, r: dict) -> Field:
        type_map = {"TEXT": FieldType.TEXT, "SELECT": FieldType.SELECT,
                     "FILE": FieldType.FILE, "TEXTAREA": FieldType.TEXTAREA}
        fw = Framework.REACT if r.get("framework") == "REACT" else Framework.VANILLA
        return Field(
            key=r.get("key", ""),
            label=r.get("label", ""),
            type=type_map.get(r.get("type", ""), FieldType.TEXT),
            required=r.get("required", False),
            framework=fw,
            selector=r.get("selector", ""),
            value=r.get("value", ""),
            options=r.get("options", []),
            placeholder=r.get("placeholder", ""),
            name=r.get("name", ""),
        )

    @staticmethod
    def _verify_fill(page, field: Field, expected: str) -> bool:
        """Check if the field value was actually set after a fill attempt.
        Waits a beat to let Backbone re-render settle."""
        import time as _t
        _t.sleep(0.3)
        try:
            actual = page.evaluate(f"() => document.querySelector({json.dumps(field.selector)})?.value || ''")
            return bool(actual) and expected in actual
        except Exception:
            return False

    def fill(self, page, field: Field, value: str) -> FillResult:
        # Strip phone number formatting for phone-like fields (maxlength=10
        # fields reject formatted numbers like "+1 (343) 558-1744").
        # North American numbers with country code (11 digits) are stripped to
        # 10 digits by removing the leading 1 (maxlength=10 form expects local).
        if re.search(r'phone|contact|mobile|cell', field.label, re.I):
            digits = re.sub(r'\D', '', value)
            if len(digits) == 11 and digits.startswith('1'):
                digits = digits[1:]  # Strip leading country code → 10-digit local
            if 7 <= len(digits) <= 15:
                value = digits
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
                return FillResult(ok=True, field_key=field.key)
            elif field.framework == Framework.VANILLA:
                ok = set_vanilla_input(page, field.selector, value)
            else:
                # jQuery-first: if jQuery is loaded, use .val() which stores
                # in jQuery's data cache AND survives Backbone re-renders.
                # Fallback: Playwright fill → React nativeValueSetter.
                el_js = json.dumps(field.selector)
                val_js = json.dumps(str(value))
                try:
                    has_jq = page.evaluate("typeof jQuery !== 'undefined'")
                except Exception:
                    has_jq = False
                if has_jq:
                    page.evaluate(f"""() => {{
                        const el = document.querySelector({el_js});
                        if (!el) return;
                        jQuery(el).val({val_js}).trigger('change').trigger('input');
                    }}""")
                    ok = True
                else:
                    try:
                        loc = page.locator(field.selector)
                        if loc.count() > 0:
                            loc.first.fill(value)
                            ok = _verify_fill(page, field, value)
                            if not ok:
                                ok = set_react_input(page, field.selector, value)
                            if not ok:
                                loc.first.click()
                                loc.first.press_sequentially(value, delay=15)
                                ok = _verify_fill(page, field, value)
                        else:
                            ok = set_react_input(page, field.selector, value)
                    except Exception:
                        ok = set_react_input(page, field.selector, value)
            return FillResult(ok=ok, field_key=field.key)
        except Exception as e:
            return FillResult(ok=False, field_key=field.key, error=str(e))

    def upload(self, page, field: Field, file_path: str) -> bool:
        try:
            inp = page.locator(_FILE_SEL).first
            if inp.count() > 0:
                inp.set_input_files(file_path)
                time.sleep(2)
                return True
        except Exception:
            pass
        return False

    # ── Navigation ────────────────────────────────────────────────────

    def _get_buttons(self, page) -> list[str]:
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'))
                .filter(el => el.offsetParent !== null && !el.disabled)
                .map(el => (el.textContent || el.value || '').trim().toLowerCase());
        }""") or []

    def can_proceed(self, page) -> bool:
        btns = self._get_buttons(page)
        cfg = self._cfg(page.url)
        kws = list(cfg.get("submit_btn", list(_SUBMIT_TEXTS)))
        kws += list(cfg.get("next_btn", list(_NEXT_TEXTS)))
        kws.append("review")
        return any(any(k in b for k in kws) for b in btns)

    def _click_by_text(self, page, texts: list[str]) -> bool:
        for t in texts:
            try:
                for sel in [
                    f'button:has-text("{t}")',
                    f'a:has-text("{t}")',
                    f'a[role="button"]:has-text("{t}")',
                    f'input[type="submit"][value*="{t}"]',
                ]:
                    btn = page.locator(sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        # Playwright's click() auto-waits for enabled + stable
                        btn.first.click()
                        return True
            except Exception:
                pass
        return False

    def click_next(self, page) -> ActionResult:
        cfg = self._cfg(page.url)
        texts = cfg.get("next_btn", _NEXT_TEXTS)
        if self._click_by_text(page, texts):
            time.sleep(2)
            return ActionResult(ok=True, navigated=True)
        return ActionResult(ok=False)

    def click_submit(self, page) -> ActionResult:
        cfg = self._cfg(page.url)
        texts = cfg.get("submit_btn", _SUBMIT_TEXTS)
        if self._click_by_text(page, texts):
            time.sleep(3)
            navigated = safe_eval(page, "() => window.location.href", "")
            return ActionResult(ok=True, navigated=True)
        return ActionResult(ok=False)

    def ensure_modal_open(self, page) -> bool:
        if wait_for_fields(self, page, timeout=12):
            return True

        text = safe_eval(page, "() => document.body.innerText", "") or ""
        text = text.lower()
        cfg = self._cfg(page.url)

        for bt in cfg.get("guest_btn", ["apply as guest", "continue without signing in"]):
            if bt in text:
                if self._click_by_text(page, [bt]):
                    time.sleep(3)
                    return wait_for_fields(self, page, timeout=6)

        apply_texts = cfg.get("apply_btn", ["apply now", "apply for this job", "apply", "quick apply"])
        if self._click_by_text(page, apply_texts):
            time.sleep(3)
            return wait_for_fields(self, page, timeout=6)

        return False

    def ensure_resume(self, page, jid: str) -> bool:
        """Upload tailored resume if file input is present."""
        from lib.config import RESULTS_DIR
        rd = os.path.join(RESULTS_DIR, jid)
        pdf_path = None
        if os.path.isdir(rd):
            for f in sorted(os.listdir(rd)):
                if "Resume" in f and f.endswith(".pdf"):
                    pdf_path = os.path.join(rd, f)
                    break
        if not pdf_path or not os.path.exists(pdf_path):
            return True  # Don't block — form may not require upload
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

    # ── Signals ───────────────────────────────────────────────────────

    def is_applied(self, page) -> bool:
        return check_applied_signal(page)

    def get_errors(self, page) -> list[str]:
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll('[role="alert"], .error, [class*="error"], .field-error, [class*="field-error"]'))
                .filter(e => e.offsetParent !== null && (e.textContent || '').length < 200)
                .map(e => e.textContent.trim())
                .filter(Boolean);
        }""") or []
