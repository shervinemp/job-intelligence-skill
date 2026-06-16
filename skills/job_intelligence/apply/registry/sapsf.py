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
    This triggers the widget's internal state update so form validation passes."""
    page.evaluate("""() => {
        const boxes = document.querySelectorAll('input[role="combobox"]');
        boxes.forEach((el, i) => {
            if (el.value && el.value.length > 0) {
                setTimeout(() => {
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }, i * 100);
            }
        });
    }""")
    import time
    count = page.evaluate("() => document.querySelectorAll('input[role=\"combobox\"]').length")
    time.sleep(count * 0.15 + 0.5)


def pre_submit(page):
    """Prepare the page for submission."""
    import time
    pre_fill(page)
    time.sleep(1)
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
