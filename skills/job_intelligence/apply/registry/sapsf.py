"""SAP SuccessFactors platform hooks."""


def pre_fill(page):
    """Expand collapsed sections before filling (SAP SF multi-section forms)."""
    found = page.evaluate("""() => {
        for (const el of document.querySelectorAll('button')) {
            const t = (el.textContent || '').trim().toLowerCase();
            if (t.includes('expand all')) {
                el.click();
                return true;
            }
        }
        return false;
    }""")
    return found
