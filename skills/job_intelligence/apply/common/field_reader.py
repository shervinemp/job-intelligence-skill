"""field_reader.py ΓÇö Canonical DOM field reader. Single JS block, configurable scope and widgets.

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

    // ΓöÇΓöÇ helpers ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    function resolveLabel(el, scopeRoot) {
        let label = '';
        if (el.getAttribute('aria-labelledby')) {
            const ref = document.getElementById(el.getAttribute('aria-labelledby'));
            if (ref) label = ref.textContent.trim();
        }
        if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
        // AOM: Chrome 121+ computed accessible name — bypasses obfuscated classes
        if (!label && el.computedName) {
            const cn = el.computedName.trim();
            if (cn && cn.length > 0 && cn.length < 100) label = cn;
        }
        if (!label && el.id) {
            const lbl = scopeRoot.querySelector('label[for="' + el.id + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label && el.name) {
            const lbl = scopeRoot.querySelector('label[for="' + el.name + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parentLabel = el.closest('label');
            if (parentLabel) label = parentLabel.textContent.trim();
        }
        // Before falling back to placeholder, look for unclaimed <label> by
        // walking up the DOM tree. Fixes Ashby/React apps where label[for]
        // points to a generated ID that was stripped from the input.
        if (!label) {
            let ancestor = el.parentElement;
            for (let level = 0; level < 5 && ancestor; level++) {
                const labels = [...ancestor.querySelectorAll('label')];
                const unclaimed = labels.filter(l => {
                    const forAttr = l.getAttribute('for');
                    if (!forAttr) return true;
                    return !document.getElementById(forAttr);
                });
                if (unclaimed.length === 1) {
                    const txt = unclaimed[0].textContent.trim();
                    if (txt.length > 1 && txt.length < 100) { label = txt; break; }
                }
                if (unclaimed.length > 1) break; // ambiguous — stop searching
                ancestor = ancestor.parentElement;
            }
        }
        // Generic placeholders are useless labels — try parent's label before using them
        const _GENERIC_PLACEHOLDERS = new Set(['start typing...', 'type here...', 'type to search...',
            'enter text...', 'select...', 'choose...', 'search...']);
        if (!label && el.placeholder && !_GENERIC_PLACEHOLDERS.has(el.placeholder.toLowerCase().trim())) {
            label = el.placeholder;
        }
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

    var PLACEHOLDER_VALUES = ['select an option', 'select one', 'select...', 'no selection', '- select -', 'choose'];
    function isEmptyValue(v) {
        return !v || PLACEHOLDER_VALUES.indexOf(v.trim().toLowerCase()) >= 0;
    }

    function resolveLabelFull(el, scopeRoot) {
        // Same derivation as resolveLabel but WITHOUT the 80-char cut —
        // the untruncated identity for label_full.
        let label = el.getAttribute('aria-label') || el.placeholder || el.name || '';
        const normName = (el.name || '').toLowerCase();
        if (el.type === 'radio' && normName && normName.length <= 24 && /^[a-z0-9_]+$/.test(normName)) {
            label = normName;
        }
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
        return (label || '').replace(/\\s+/g, ' ').trim();
    }

    function fieldFromElement(el, scopeRoot) {
        const label = resolveLabel(el, scopeRoot);
        const opts = el.tagName === 'SELECT'
            ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0, 15)
            : [];
        const rawVal = el.value || '';

        // Honeypot detection — mark but don't filter here (Python-side
        // _is_junk_field has the final say). Signals: label/placeholder
        // contains robot/honeypot/do-not-fill, OR aria-hidden on a text
        // input, OR name explicitly says "honeypot".
        function isHoneypot(el, label) {
            const lc = (label || '').toLowerCase();
            const ph = (el.placeholder || '').toLowerCase();
            const nm = (el.getAttribute('name') || '').toLowerCase();
            const ariaHidden = el.getAttribute('aria-hidden') === 'true';
            if (/\\brobot|honeypot|for bots|spam trap|do not fill|do not enter|leave this blank|leave empty\\b/.test(lc)) return true;
            if (/\\brobot|honeypot|for bots|spam trap|do not fill|do not enter\\b/.test(ph)) return true;
            if (nm === 'honeypot' || nm === 'spam_trap' || nm === 'anti_spam') return true;
            // aria-hidden text input with no required flag — likely a bot trap
            if (ariaHidden && el.tagName === 'INPUT' && (el.type === 'text' || el.type === '')
                && !el.required && el.getAttribute('aria-required') !== 'true') return true;
            return false;
        }

        return {
            tag: el.tagName, type: el.getAttribute('type') || '',
            id: el.id, name: el.getAttribute('name') || '',
            label: label,
            // IDENTITY, never truncated — label is display (may be cut to
            // 80 chars); consumers that key on labels must use label_full
            // when the field carries it.
            label_full: resolveLabelFull(el, scopeRoot),
            option_label: resolveOptionLabel(el, scopeRoot, label),
            placeholder: el.placeholder || '',
            autocomplete: el.getAttribute('autocomplete') || '',
            aria_autocomplete: el.getAttribute('aria-autocomplete') || '',
            data_automation_id: el.getAttribute('data-automation-id') || '',
            data_testid: el.getAttribute('data-testid') || '',
            role: el.getAttribute('role') || '',
            required: !!el.required || el.getAttribute('aria-required') === 'true',
            value: rawVal, isEmpty: isEmptyValue(rawVal),
            checked: el.type === 'radio' ? el.checked : null,
            multiple: el.tagName === 'SELECT' && el.multiple || false, options: opts,
            datepicker: el.type === 'date' ? 'native'
                : el.classList.contains('flatpickr-input') || (el.closest && el.closest('.flatpickr')) ? 'flatpickr' : '',
            is_honeypot: isHoneypot(el, label),
        };
    }

    function isVisible(el) {
        if (el.type === 'file') return true;
        // Ashby Yes/No: hidden checkbox but visible button container
        if (el.type === 'checkbox' && el.closest('[class*="yesno"]')) return true;
        // Check element AND ancestors for display:none/visibility:hidden
        // (LinkedIn Easy Apply hides non-current pages via parent display:none)
        let node = el;
        for (let i = 0; i < 10 && node; i++) {
            const s = window.getComputedStyle(node);
            if (s.display === 'none' || s.visibility === 'hidden') return false;
            if (s.position === 'absolute' && parseInt(s.left) < -100) return false;
            node = node.parentElement;
        }
        return true;
    }

    function makeDropdown(btn, sr) {
        // Junk rejection: controls inside navigation/utility regions are
        // page furniture (language selectors, hamburger menus), not form
        // fields. Workday's header is a div soup, so match data-automation-id
        // too — real form dropdowns never live in a nav/utility container.
        if (btn.closest('nav, header, [data-automation-id*="navigation" i], [data-automation-id*="utility" i], [data-automation-id*="header" i]')) return null;
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

    // ΓöÇΓöÇ collect fields ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    const fields = [];

    // Standard DOM
    root.querySelectorAll(inputSel).forEach(el => { if (isVisible(el)) fields.push(fieldFromElement(el, root)); });

    // Shadow DOM (recursive)
    root.querySelectorAll(':defined').forEach(el => { if (el.shadowRoot) walkShadow(el, fields); });

    // Custom dropdown widgets (standard DOM)
    if (customWidgets.dropdown) {
        root.querySelectorAll(customWidgets.dropdown).forEach(btn => { const d = makeDropdown(btn, root); if (d) fields.push(d); });
    }

    // ── Radio button grouping ──────────────────────────────────────────
    // Group radios by name attribute, extract parent question as label.
    // Individual radio -> "Yes" is useless; we need "Are you willing to relocate? → [Yes, No]"
    // CSS.escape mangles Unicode chars (\ufffd etc) in attribute values.
    // Use simple backslash/quote escaping for quoted attribute selectors instead.
    function escAttr(s) { return s.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"'); }
    const radioGroups = {};
    const radioNames = new Set();
    fields.forEach(f => { if (f.type === 'radio' && f.name) radioNames.add(f.name); });
    radioNames.forEach(name => {
        const radios = fields.filter(f => f.type === 'radio' && f.name === name);
        if (radios.length < 2) return;
        // Find parent question label from DOM structure.
        // Typical HTML: <div class="row"><div class="col-4"><label>Question?</label></div><div class="col-8"><input type="radio" name="..."></div></div>
        // LinkedIn: <div><p>Question? *</p><fieldset>...radios...</fieldset><p>This field is required</p></div>
        const firstEl = root.querySelector('input[name="' + escAttr(name) + '"]');
        let question = radios[0].label || '';
        const fieldset = firstEl ? firstEl.closest('fieldset') : null;
        if (firstEl) {
            // Step 0: fieldset's previous sibling (LinkedIn Easy Apply pattern)
            // <p>Question? *</p><fieldset>...radios...</fieldset>
            if (fieldset && fieldset.previousElementSibling) {
                const txt = (fieldset.previousElementSibling.textContent || '').trim();
                if (txt.length > 3 && txt.length < 500 && txt !== fieldset.textContent.trim()) {
                    question = txt;
                }
            }
            // Step 0b: walk backwards through fieldset's previous siblings
            // (LinkedIn EEOC: <p>Race/Ethnicity</p><p>definitions...</p><ul>...</ul><fieldset>)
            // Collect short <p> candidates and prefer question-like ones.
            if (question === radios[0].label || question === name) {
                let prev = fieldset ? fieldset.previousElementSibling : null;
                let candidates = [];
                while (prev) {
                    const txt = (prev.textContent || '').trim();
                    if (txt.length > 3 && txt.length < 100 && prev.tagName === 'P' && !prev.querySelector('ul, li, ol')) {
                        if (!/^\\d+\\/\\d+\\s*pages?$/.test(txt)) candidates.push(txt);
                    }
                    prev = prev.previousElementSibling;
                }
                if (candidates.length > 0) {
                    // Prefer: short category labels (no question words, not ending with ?)
                    let best = candidates.find(t =>
                        t.length <= 30 &&
                        !/^(why|how|what|please|race categories|gender)/i.test(t) &&
                        !/\\?\\s*$/.test(t) &&
                        !/defined|follows|please|check one|being asked/i.test(t)
                    );
                    question = best || candidates.find(t => !/being asked|defined|follows/i.test(t)) || candidates[0];
                }
            }
            // Step 1: look at the previous sibling's label (most common pattern)
            const myContainer = firstEl.closest('div,fieldset,section,li');
            if (question === radios[0].label && myContainer) {
                const prevSibling = myContainer.previousElementSibling;
                if (prevSibling) {
                    const prevLabel = prevSibling.querySelector('label, legend, strong, b, span, p');
                    if (prevLabel) {
                        const txt = (prevLabel.textContent || '').trim();
                        if (txt.length > 3 && txt.length < 200) question = txt;
                    }
                    if (question === radios[0].label) {
                        const txt = (prevSibling.textContent || '').trim();
                        if (txt.length > 3 && txt.length < 200) question = txt;
                    }
                }
            }
            // Step 2: walk up two levels and check previous sibling
            if (question === radios[0].label) {
                const level2 = firstEl.closest('.row, .form-group, fieldset, section');
                if (level2) {
                    const prev2 = level2.previousElementSibling;
                    if (prev2) {
                        const prevLabel2 = prev2.querySelector('label, legend, strong, b, span, p');
                        if (prevLabel2) {
                            const txt = (prevLabel2.textContent || '').trim();
                            if (txt.length > 3 && txt.length < 200) question = txt;
                        }
                    }
                }
            }
            // Step 3: fall back to container's direct text
            if (question === radios[0].label && myContainer) {
                const allText = myContainer.textContent || '';
                const beforeRadio = allText.split(radios[0].label)[0] || '';
                const clean = beforeRadio.replace(/[:*]\\s*$/, '').trim();
                if (clean.length > 3 && clean.length < 200) question = clean;
            }
        }
        // Options: extract visible text for each radio. LinkedIn Easy Apply
        // radios have NO value attribute and empty <label> — option text is
        // in a <p> inside the role="radio" container. Query all radios by name
        // and iterate (not by value, which may be absent).
        const allRadioEls = [...root.querySelectorAll('input[type="radio"][name="' + escAttr(name) + '"]')];
        const optionLabels = allRadioEls.map(el => {
            // 0) Try label[for="el.id"] (Ashby pattern: sibling <label>)
            if (el.id) {
                const lbl = root.querySelector('label[for="' + el.id + '"]');
                if (lbl) {
                    const txt = (lbl.textContent || '').trim();
                    if (txt && txt !== 'on' && txt.length < 100) return txt;
                }
            }
            // 1) Try parent <label> text (traditional forms)
            const parentLabel = el.closest('label');
            if (parentLabel) {
                const txt = (parentLabel.textContent || '').trim();
                const childText = Array.from(parentLabel.querySelectorAll('div,span'))
                    .map(c => (c.textContent || '').trim()).join(' ');
                const clean = txt.replace(childText, '').replace(/\\s+/g, ' ').trim();
                if (clean.length > 0 && clean.length < 30) return clean;
            }
            // 2) LinkedIn Easy Apply: role="radio" container → find <p> text
            const radioRole = el.closest('[role="radio"]');
            if (radioRole) {
                const pEls = radioRole.querySelectorAll('p');
                for (const p of pEls) {
                    const pt = (p.textContent || '').trim();
                    if (pt.length > 0 && pt.length < 30 && pt !== question) return pt;
                }
                const al = radioRole.getAttribute('aria-label');
                if (al && al.length < 30 && !/\\.pdf$|\\.doc/i.test(al)) return al;
            }
            // 3) Fallback: value attribute or id (skip default "on")
            const v = el.value;
            return (v && v !== 'on') ? v : (el.id || '');
        });
        const uniqueOpts = [...new Set(optionLabels)].filter(Boolean);
        // Skip resume/file-selection radio groups (LinkedIn Easy Apply page 2):
        // aria-label is a filename, no question text, already checked
        if (!question || question === name) {
            // Check radio labels AND aria-labels for filenames (resume selection)
            const hasFile = allRadioEls.some(el => {
                if (el.value && /\\.(pdf|docx?|txt|rtf)$/i.test(el.value)) return true;
                const rr = el.closest('[role="radio"]');
                if (rr) {
                    const al = rr.getAttribute('aria-label') || '';
                    if (/\\.(pdf|docx?|txt|rtf)$/i.test(al)) return true;
                }
                return false;
            });
            if (hasFile && allRadioEls.some(el => el.checked)) return;
        }
        // Remove individual radio entries, add one grouped entry
        const checkedIdx = allRadioEls.findIndex(el => el.checked);
        radioGroups[name] = {
            tag: 'RADIO_GROUP', name: name, id: firstEl ? firstEl.id || '' : '',
            label: question || name,
            type: 'radio', options: uniqueOpts.length >= 2 ? uniqueOpts : (allRadioEls.length >= 2 ? ['Yes', 'No'] : optionLabels),
            required: radios.some(r => r.required) || /\\*\\s*$/.test(question) || !!(fieldset && fieldset.nextElementSibling && /required/i.test(fieldset.nextElementSibling.textContent)),
            selector: 'input[name="' + escAttr(name) + '"]',
            value: checkedIdx >= 0 ? optionLabels[checkedIdx] : '',
            placeholder: '', data_automation_id: '', role: 'radiogroup',
        };
    });
    // Replace individual radios with grouped entries
    const finalFields = [];
    const groupedNames = new Set(Object.keys(radioGroups));
    fields.forEach(f => {
        if (f.type === 'radio' && f.name && groupedNames.has(f.name)) {
            if (!finalFields.find(ff => ff.name === f.name && ff.tag === 'RADIO_GROUP')) {
                finalFields.push(radioGroups[f.name]);
            }
        } else {
            finalFields.push(f);
        }
    });

    // File inputs for hasFileInput flag (standard + shadow)
    let fileCount = root.querySelectorAll('input[type="file"]').length;
    root.querySelectorAll(':defined').forEach(el => { if (el.shadowRoot) fileCount += el.shadowRoot.querySelectorAll('input[type="file"]').length; });

    // Buttons (standard DOM + shadow DOM)
    const buttons = [];
    function collectButtons(root) {
        root.querySelectorAll('button, a.btn, [role="button"]').forEach(b => {
            if (b.offsetParent !== null) buttons.push(b);
        });
    }
    collectButtons(root);
    root.querySelectorAll(':defined').forEach(el => { if (el.shadowRoot) collectButtons(el.shadowRoot); });
    const buttonData = buttons.map(b => ({
        text: (b.textContent || '').trim().slice(0, 30),
        disabled: b.disabled || false,
        type: 'button',
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
        fieldCount: finalFields.length,
        fields: finalFields.slice(0, config.max_fields || 300),
        pageType: pageType,
        hasFileInput: fileCount > 0,
        hasRequiredFile: root.querySelectorAll('input[type="file"][required]').length > 0,
        buttons: buttonData,
        url: location.href,
    };
}"""


def read_fields(page, scope="document", custom_widgets=None, max_fields=None):
    """Read all form fields from a page. Returns dict with fieldCount, fields, buttons, etc.

    Args:
        page: Playwright page object
        scope: 'document' for full page, 'dialog' for modal only
        custom_widgets: dict of widget type ΓåÆ CSS selector from registry config
        max_fields: upper bound on fields returned (default 300). The old
            hard-capped 35 silently dropped fields on large Workday/matrix
            forms (COMPARISON §S3); the cap is now explicit and configurable.

    Returns structured dict on success or empty dict on failure (dead tab, cross-origin, detached element).
    """
    try:
        return page.evaluate(_READER_JS, {
            "scope": scope,
            "custom_widgets": custom_widgets or {},
            "max_fields": max_fields or 300,
        })
    except Exception as e:
        print(f"FIELD_READ_ERROR: read_fields failed ΓÇö {e}", file=sys.stderr)
        return {"fieldCount": 0, "fields": [], "buttons": [], "pageType": "error", "hasFileInput": False,
                "hasRequiredFile": False, "url": ""}
