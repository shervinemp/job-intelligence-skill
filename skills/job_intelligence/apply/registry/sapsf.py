"""SAP SuccessFactors platform hooks."""


def pre_fill(page):
    """Expand all sections before filling."""
    page.evaluate("""() => {
        for (const el of document.querySelectorAll('button')) {
            const t = (el.textContent || '').trim().toLowerCase();
            if (t.includes('expand all')) {
                el.click();
                return true;
            }
        }
        return false;
    }""")
    return True


def post_fill(page):
    """After native value setter fills combobox INPUTs, notify SAP SF's juic
    framework by dispatching 'change' and 'blur' events one at a time.
    Also tries clicking through the widget trigger hierarchy to sync state."""
    page.evaluate("""() => {
        const boxes = document.querySelectorAll('input[role="combobox"]');
        boxes.forEach((el, i) => {
            if (el.value && el.value.length > 0) {
                // Standard events
                setTimeout(() => {
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    // Direct juic API
                    const id = el.id;
                    if (id && window.juic && window.juic.fire) {
                        window.juic.fire(id + ':', '_handleChange', new Event('change'));
                    }
                }, i * 150);

                // SAP SF widget trigger cascade: parent elements may have the real click handler
                setTimeout(() => {
                    let p = el.parentElement;
                    for (let j = 0; j < 3 && p; j++) {
                        try { p.click(); break; } catch(e) {}
                        p = p.parentElement;
                    }
                    // Also try clicking container trigger icons
                    const c = el.closest('[class*="RCMFormField"], [class*="fieldComponent"], [class*="sfComboBox"]');
                    if (c) {
                        const t = c.querySelector('button, [role="button"], [tabindex], i, span.glyphicon, [class*="sapUiIcon"]');
                        if (t && t !== el) { try { t.click(); } catch(e) {} }
                    }
                    // Open dropdown, select matching option
                    const selOpt = document.querySelector('[role="option"]');
                    if (selOpt) {
                        const opts = document.querySelectorAll('[role="option"]');
                        for (const o of opts) {
                            if (o.textContent.trim().toLowerCase() === el.value.toLowerCase()) {
                                o.click();
                                break;
                            }
                        }
                        document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
                    }
                }, i * 150 + 300);
            }
        });
    }""")
    import time
    count = page.evaluate("() => document.querySelectorAll('input[role=\"combobox\"]').length")
    time.sleep(count * 0.4 + 2)


def pre_submit(page):
    """Prepare the page for submission."""
    import time
    pre_fill(page)
    time.sleep(1)
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
