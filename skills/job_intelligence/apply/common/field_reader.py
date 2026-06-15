"""field_reader.py — Canonical DOM field reader. Single JS block, configurable scope and widgets.

Usage:
    fields = read_fields(page)
    fields = read_fields(page, scope="dialog")
    fields = read_fields(page, custom_widgets={"dropdown": "button[aria-haspopup='listbox']"})
"""
import sys

_READER_JS = """(config) => {
    const scope = config.scope || 'document';
    const customWidgets = config.custom_widgets || {};
    const root = scope === 'dialog'
        ? (document.querySelector('[role="dialog"], dialog') || document)
        : document;
    const inputSel = 'input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable="true"]';

    // ── helpers ──────────────────────────────────────────────────────────────

    function resolveLabel(el, scopeRoot) {
        let label = '';
        if (el.getAttribute('aria-labelledby')) {
            const ref = document.getElementById(el.getAttribute('aria-labelledby'));
            if (ref) label = ref.textContent.trim();
        }
        if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
        if (!label) {
            const lbl = scopeRoot.querySelector('label[for="' + el.id + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parentLabel = el.closest('label');
            if (parentLabel) label = parentLabel.textContent.trim();
        }
        if (!label && el.placeholder) label = el.placeholder;
        if (!label) {
            const parent = el.closest('div,fieldset,section,li,form');
            const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
            if (plbl) label = plbl.textContent.trim();
        }
        if (!label) {
            const td = el.closest('td');
            if (td) {
                const firstCell = td.parentElement ? td.parentElement.querySelector('td:first-child, th:first-child') : null;
                if (firstCell && firstCell !== td) label = firstCell.textContent.trim();
            }
        }
        return (label || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
    }

    function resolveOptionLabel(el, scopeRoot, label) {
        if (el.type !== 'radio') return '';
        let o = '';
        const td = el.closest('td');
        if (td) {
            const colIdx = Array.from(td.parentNode.children).indexOf(td);
            const tbl = td.closest('table');
            if (tbl) {
                const hr = tbl.querySelector('thead tr, tbody tr:first-child');
                if (hr && hr.children[colIdx]) o = hr.children[colIdx].textContent.trim();
            }
        } else if (el.id) {
            const lblFor = scopeRoot.querySelector('label[for="' + el.id + '"]');
            if (lblFor) o = lblFor.textContent.trim();
        }
        if (!o) { const pl = el.closest('label'); if (pl) o = pl.textContent.trim(); }
        if (!o) {
            const tn = el.nextSibling;
            if (tn && tn.nodeType === 3) o = tn.textContent.trim();
            else if (el.parentElement) {
                const pt = el.parentElement.textContent.trim();
                if (label && pt.includes(label)) o = pt.replace(label, '').replace(/^[-:,\\s]+/, '').trim();
            }
        }
        if (!o) o = el.value || '';
        return o.slice(0, 40);
    }

    function fieldFromElement(el, scopeRoot) {
        const label = resolveLabel(el, scopeRoot);
        const opts = el.tagName === 'SELECT'
            ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0, 15)
            : [];
        return {
            tag: el.tagName, type: el.getAttribute('type') || '',
            id: el.id, name: el.getAttribute('name') || '',
            label: label, option_label: resolveOptionLabel(el, scopeRoot, label),
            placeholder: el.placeholder || '',
            data_automation_id: el.getAttribute('data-automation-id') || '',
            role: el.getAttribute('role') || '',
            required: !!el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || '', checked: el.type === 'radio' ? el.checked : null,
            multiple: el.tagName === 'SELECT' && el.multiple || false, options: opts,
            datepicker: el.type === 'date' ? 'native'
                : el.classList.contains('flatpickr-input') || (el.closest && el.closest('.flatpickr')) ? 'flatpickr' : '',
        };
    }

    function isVisible(el) {
        if (el.type === 'file') return true;
        const s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        if (s.position === 'absolute' && parseInt(s.left) < -100) return false;
        if (s.clip === 'rect(0px, 0px, 0px, 0px)' || s.clip === 'rect(0,0,0,0)') return false;
        return true;
    }

    function makeDropdown(btn, sr) {
        const parentSelector = customWidgets.parent || '[data-automation-id], [role="dialog"], dialog, form, fieldset';
        const parent = btn.closest(parentSelector);
        if (!parent) return null;
        const labelEl = parent.querySelector('label, legend, span');
        const lbl = labelEl ? labelEl.textContent.trim().replace(/\\s+/g, ' ').slice(0, 80) : '';
        return {
            tag: 'DROPDOWN', type: 'custom', id: btn.id,
            name: btn.getAttribute('name') || '',
            label: lbl || btn.getAttribute('aria-label') || '',
            placeholder: '', data_automation_id: btn.getAttribute('data-automation-id') || '',
            role: btn.getAttribute('role') || '',
            required: (lbl || '').includes('*'),
            value: (btn.textContent || '').trim().slice(0, 30), checked: null, options: [],
        };
    }

    function walkShadow(host, fields) {
        try {
            if (!host.shadowRoot) return;
            const sr = host.shadowRoot;
            sr.querySelectorAll(inputSel).forEach(el => { if (isVisible(el)) fields.push(fieldFromElement(el, sr)); });
            if (customWidgets.dropdown) {
                sr.querySelectorAll(customWidgets.dropdown).forEach(btn => { const d = makeDropdown(btn, sr); if (d) fields.push(d); });
            }
            // Recurse nested shadow roots (use :defined to avoid iterating every element)
            sr.querySelectorAll(':defined').forEach(el => { if (el.shadowRoot) walkShadow(el, fields); });
        } catch(e) { /* skip inaccessible shadow root */ }
    }

    // ── collect fields ──────────────────────────────────────────────────────

    const fields = [];

    // Standard DOM
    root.querySelectorAll(inputSel).forEach(el => { if (isVisible(el)) fields.push(fieldFromElement(el, root)); });

    // Shadow DOM (recursive) — `:defined` limits to custom elements (only they can have shadow roots)
    root.querySelectorAll(':defined').forEach(el => { if (el.shadowRoot) walkShadow(el, fields); });

    // Custom dropdown widgets (standard DOM)
    if (customWidgets.dropdown) {
        root.querySelectorAll(customWidgets.dropdown).forEach(btn => { const d = makeDropdown(btn, root); if (d) fields.push(d); });
    }

    // File inputs for hasFileInput flag (standard + shadow)
    let fileCount = root.querySelectorAll('input[type="file"]').length;
    root.querySelectorAll(':defined').forEach(el => { if (el.shadowRoot) fileCount += el.shadowRoot.querySelectorAll('input[type="file"]').length; });

    // Buttons (standard DOM)
    const buttons = Array.from(root.querySelectorAll('button'))
        .filter(b => b.offsetParent !== null)
        .map(b => ({
            text: (b.textContent || '').trim().slice(0, 30),
            disabled: b.disabled || false,
            type: b.getAttribute('type') || 'button',
        }));

    const text = (document.body.innerText || '').toLowerCase();
    const hasFormWords = text.includes('submit') || text.includes('apply') || text.includes('application');
    const hasPassword = document.querySelector('input[type="password"]') !== null;
    const isShort = (document.body.innerText || '').length < 500;

    let pageType = 'unknown';
    if (fields.length > 0) pageType = 'form';
    else if (hasPassword && (text.includes('sign in') || text.includes('log in'))) pageType = 'login_wall';
    else if (isShort && text.includes('sign in') && !text.includes('apply')) pageType = 'login_wall';
    else if (hasFormWords) pageType = 'maybe_form';

    return {
        fieldCount: fields.length,
        fields: fields.slice(0, 35),
        pageType: pageType,
        hasFileInput: fileCount > 0,
        hasRequiredFile: root.querySelectorAll('input[type="file"][required]').length > 0,
        buttons: buttons,
        url: location.href,
    };
}"""


def read_fields(page, scope="document", custom_widgets=None):
    """Read all form fields from a page. Returns dict with fieldCount, fields, buttons, etc.

    Args:
        page: Playwright page object
        scope: 'document' for full page, 'dialog' for modal only
        custom_widgets: dict of widget type → CSS selector from registry config

    Returns structured dict on success or empty dict on failure (dead tab, cross-origin, detached element).
    """
    try:
        return page.evaluate(_READER_JS, {
            "scope": scope,
            "custom_widgets": custom_widgets or {},
        })
    except Exception as e:
        print(f"FIELD_READ_ERROR: read_fields failed — {e}", file=sys.stderr)
        return {"fieldCount": 0, "fields": [], "buttons": [], "pageType": "error", "hasFileInput": False,
                "hasRequiredFile": False, "url": ""}


def count_fields(page):
    """Quick field count without full field details. ~2x faster than read_fields."""
    try:
        return page.evaluate("""(scope) => {
            const root = scope === 'dialog'
                ? (document.querySelector('[role="dialog"]') || document)
                : document;
            const sel = 'input:not([type=hidden]):not([type=submit]), select, textarea';
            let count = root.querySelectorAll(sel).length;
            // Shadow DOM
            root.querySelectorAll(':defined').forEach(el => {
                if (el.shadowRoot) count += el.shadowRoot.querySelectorAll(sel).length;
            });
            return count;
        }""", scope)
    except Exception as e:
        print(f"FIELD_READ_ERROR: count_fields failed — {e}", file=sys.stderr)
        return 0


_ERROR_SCAN_JS = """() => {
    const root = document.querySelector('[role="dialog"], dialog') || document;
    const errors = [];

    // NOTE: label resolution below mirrors resolveLabel() in _READER_JS.
    // If you change label resolution logic there, update this too.

    // 1. aria-invalid elements
    const invalid = root.querySelectorAll('[aria-invalid="true"]');
    invalid.forEach(el => {
        const tag = el.tagName;
        const id = el.id || '';
        // Find the label
        let label = '';
        if (el.getAttribute('aria-labelledby')) {
            const ref = document.getElementById(el.getAttribute('aria-labelledby'));
            if (ref) label = ref.textContent.trim();
        }
        if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
        if (!label && id) {
            const lbl = root.querySelector('label[for="' + id + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parent = el.closest('div,fieldset,section');
            if (parent) {
                const lbl = parent.querySelector('label, legend, [class*="label"]');
                if (lbl) label = lbl.textContent.trim();
            }
        }
        // Find associated error text
        let errorText = '';
        const errId = el.getAttribute('aria-errormessage');
        if (errId) {
            const errEl = document.getElementById(errId);
            if (errEl) errorText = errEl.textContent.trim();
        }
        if (!errorText) {
            const next = el.parentElement ? el.parentElement.querySelector('.error, [class*="error"], [class*="validation"], [class*="feedback"], [class*="hint"]') : null;
            if (next) errorText = next.textContent.trim();
        }
        if (!errorText) {
            // Some ATS show error as a sibling span/div after the field
            const sibling = el.nextElementSibling;
            if (sibling) errorText = sibling.textContent.trim();
        }
        errors.push({label: label.slice(0, 80) || tag || '?', error_text: errorText.slice(0, 120)});
    });

    // 2. Visible error banners/alerts inside the form
    const errorBanners = root.querySelectorAll('[role="alert"], .alert-error, .error-message, [class*="form-error"], [data-error]');
    errorBanners.forEach(el => {
        const text = el.textContent.trim();
        if (text && text.length > 5 && text.length < 300) {
            errors.push({label: '(form)', error_text: text.slice(0, 120)});
        }
    });

    return errors;
}"""


def scan_errors(page):
    """Scan page for field-level validation errors.
    Returns list of {label, error_text} dicts.
    Pure addition — no side effects, safe to call any time.
    """
    try:
        return page.evaluate(_ERROR_SCAN_JS)
    except Exception as e:
        print(f"FIELD_READ_ERROR: scan_errors failed — {e}", file=sys.stderr)
        return []
