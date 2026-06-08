"""field_reader.py — Canonical DOM field reader. Single JS block, configurable scope and widgets.

Usage:
    fields = read_fields(page)
    fields = read_fields(page, scope="dialog")
    fields = read_fields(page, custom_widgets={"dropdown": "button[aria-haspopup='listbox']"})
"""

_READER_JS = """(config) => {
    const scope = config.scope || 'document';
    const customWidgets = config.custom_widgets || {};
    const root = scope === 'dialog'
        ? (document.querySelector('[role="dialog"], dialog') || document)
        : document;

    const inputs = Array.from(root.querySelectorAll(
        'input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable="true"]'
    )).filter(el => {
        // Skip honeypot fields: off-screen, clipped, or zero-size
        const s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        if (s.position === 'absolute' && parseInt(s.left) < -100) return false;
        if (s.clip === 'rect(0px, 0px, 0px, 0px)' || s.clip === 'rect(0,0,0,0)') return false;
        return true;
    });
    const dropdowns = root.querySelectorAll(customWidgets.dropdown || 'none');
    const fileInputs = root.querySelectorAll('input[type="file"]');
    const btns = root.querySelectorAll('button');

    const fields = Array.from(inputs).map(el => {
        let label = '';

        // WCAG priority: aria-labelledby > aria-label > label for > placeholder > parent label
        if (el.getAttribute('aria-labelledby')) {
            const ref = document.getElementById(el.getAttribute('aria-labelledby'));
            if (ref) label = ref.textContent.trim();
        }
        if (!label && el.getAttribute('aria-label')) {
            label = el.getAttribute('aria-label');
        }
        if (!label) {
            const lbl = root.querySelector('label[for="' + el.id + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parentLabel = el.closest('label');
            if (parentLabel) label = parentLabel.textContent.trim();
        }
        if (!label && el.placeholder) {
            label = el.placeholder;
        }
        if (!label) {
            const parent = el.closest('div,fieldset,section,li,form');
            const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
            if (plbl) label = plbl.textContent.trim();
        }
        // Table grid: row label is in the first cell of the same row
        if (!label) {
            const td = el.closest('td');
            if (td) {
                const firstCell = td.parentElement ? td.parentElement.querySelector('td:first-child, th:first-child') : null;
                if (firstCell && firstCell !== td) label = firstCell.textContent.trim();
            }
        }

        // For radio inputs, extract option label (the choice text, distinct from question label)
        let optLabel = '';
        if (el.type === 'radio') {
            const td = el.closest('td');
            if (td) {
                // Table grid: column header IS the option label
                const colIdx = Array.from(td.parentNode.children).indexOf(td);
                const tbl = td.closest('table');
                if (tbl) {
                    const hr = tbl.querySelector('thead tr, tbody tr:first-child');
                    if (hr && hr.children[colIdx]) optLabel = hr.children[colIdx].textContent.trim();
                }
            } else if (el.id) {
                // Label[for] with full choice text
                const lblFor = root.querySelector('label[for="' + el.id + '"]');
                if (lblFor) optLabel = lblFor.textContent.trim();
            }
            if (!optLabel) {
                const pl = el.closest('label');
                if (pl) optLabel = pl.textContent.trim();
            }
            if (!optLabel) {
                const tn = el.nextSibling;
                if (tn && tn.nodeType === 3) optLabel = tn.textContent.trim();
                else if (el.parentElement) {
                    const parentText = el.parentElement.textContent.trim();
                    if (label && parentText.includes(label)) optLabel = parentText.replace(label, '').replace(/^[-:,\\s]+/, '').trim();
                }
            }
            if (!optLabel) optLabel = el.value || '';
        }

        const opts = el.tagName === 'SELECT'
            ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0, 15)
            : [];

        return {
            tag: el.tagName,
            type: el.getAttribute('type') || '',
            id: el.id,
            name: el.getAttribute('name') || '',
            label: (label || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
            option_label: optLabel.slice(0, 40),
            placeholder: el.placeholder || '',
            data_automation_id: el.getAttribute('data-automation-id') || '',
            role: el.getAttribute('role') || '',
            required: !!el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || '',
            checked: el.type === 'radio' ? el.checked : null,
            multiple: el.tagName === 'SELECT' && el.multiple || false,
            options: opts,
            datepicker: el.type === 'date'
                ? 'native'
                : el.classList.contains('flatpickr-input') || (el.closest && el.closest('.flatpickr'))
                    ? 'flatpickr'
                    : '',
        };
    });

    // Custom dropdown widgets (e.g. Workday province selector)
    if (customWidgets.dropdown) {
        Array.from(dropdowns).forEach(btn => {
            const parent = btn.closest('[data-automation-id^="formField"]');
            if (!parent) return;
            const labelEl = parent.querySelector('label, legend, span');
            const lbl = labelEl
                ? labelEl.textContent.trim().replace(/\\s+/g, ' ').slice(0, 80)
                : '';
            const current = (btn.textContent || '').trim().slice(0, 30);
            fields.push({
                tag: 'DROPDOWN',
                type: 'custom',
                id: btn.id,
                name: btn.getAttribute('name') || '',
                label: lbl || btn.getAttribute('aria-label') || '',
                placeholder: '',
                data_automation_id: btn.getAttribute('data-automation-id') || '',
                role: btn.getAttribute('role') || '',
                required: (lbl || '').includes('*'),
                value: current,
                checked: null,
                options: [],
            });
        });
    }

    // Buttons
    const buttons = Array.from(btns)
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
        hasFileInput: fileInputs.length > 0,
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
    except Exception:
        return {"fieldCount": 0, "fields": [], "buttons": [], "pageType": "error", "hasFileInput": False,
                "hasRequiredFile": False, "url": ""}


def count_fields(page):
    """Quick field count without full field details. ~2x faster than read_fields."""
    try:
        return page.evaluate("""(scope) => {
            const root = scope === 'dialog'
                ? (document.querySelector('[role="dialog"]') || document)
                : document;
            return root.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]), select, textarea'
            ).length;
        }""", scope)
    except Exception:
        return 0
