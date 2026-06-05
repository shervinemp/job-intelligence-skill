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
        ? (document.querySelector('[role="dialog"]') || document)
        : document;

    const inputs = root.querySelectorAll(
        'input:not([type=hidden]):not([type=submit]), select, textarea'
    );
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

        const opts = el.tagName === 'SELECT'
            ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0, 15)
            : [];

        return {
            tag: el.tagName,
            type: el.getAttribute('type') || '',
            id: el.id,
            name: el.getAttribute('name') || '',
            label: (label || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
            placeholder: el.placeholder || '',
            data_automation_id: el.getAttribute('data-automation-id') || '',
            role: el.getAttribute('role') || '',
            required: !!el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || '',
            checked: el.type === 'radio' ? el.checked : null,
            options: opts,
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
    """
    return page.evaluate(_READER_JS, {
        "scope": scope,
        "custom_widgets": custom_widgets or {},
    })


def count_fields(page):
    """Quick field count without full field details. ~2x faster than read_fields."""
    return page.evaluate("""(scope) => {
        const root = scope === 'dialog'
            ? (document.querySelector('[role="dialog"]') || document)
            : document;
        return root.querySelectorAll(
            'input:not([type=hidden]):not([type=submit]), select, textarea'
        ).length;
    }""", scope)
