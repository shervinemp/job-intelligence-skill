"""Contenteditable DIV fill strategy."""
def fill(page, sel, ans):
    try:
        page.evaluate("""(sel, val) => {
            const el = document.querySelector(sel);
            if (el) { el.textContent = val; el.dispatchEvent(new Event('input', {bubbles:true})); }
        }""", [sel, ans])
        return True
    except Exception:
        return False
