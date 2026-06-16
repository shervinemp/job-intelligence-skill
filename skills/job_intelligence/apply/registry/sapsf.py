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
    framework by dispatching 'change' and 'blur' events on all filled comboboxes.
    This triggers the widget's internal state update so form validation passes."""
    page.evaluate("""() => {
        const evt = new Event('change', { bubbles: true });
        const blr = new Event('blur', { bubbles: true });
        for (const el of document.querySelectorAll('input[role="combobox"]')) {
            if (el.value && el.value.length > 0) {
                el.dispatchEvent(evt);
                el.dispatchEvent(blr);
            }
        }
    }""")


def pre_submit(page):
    """Prepare the page for submission."""
    import time
    pre_fill(page)
    time.sleep(1)
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
