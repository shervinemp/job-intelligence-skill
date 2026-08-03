"""Pass 1 probe: enrich fields with selectors and structural info."""


def resolve_selector(page, f):
    """Resolve a CSS selector for a field element."""
    if f.get("id"):
        return f'[id="{f["id"]}"]'
    if f.get("name"):
        return f'[name="{f["name"]}"]'
    if f.get("data_automation_id"):
        return f'[data-automation-id="{f["data_automation_id"]}"]'
    if f.get("placeholder"):
        return f'[placeholder="{f["placeholder"]}"]'
    if f.get("label"):
        try:
            return (page.evaluate(
                """(lbl) => {
                for (const l of document.querySelectorAll('label')) {
                    if (l.textContent.trim().toLowerCase() === lbl.toLowerCase()) {
                        const forId = l.getAttribute('for');
                        if (forId && document.getElementById(forId)) return '#' + CSS.escape(forId);
                        const inp = l.querySelector('input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable]');
                        if (inp && inp.id) return '#' + CSS.escape(inp.id);
                    }
                }
                for (const el of document.querySelectorAll('[aria-labelledby]')) {
                    const ref = document.getElementById(el.getAttribute('aria-labelledby'));
                    if (ref && ref.textContent.trim().toLowerCase() === lbl.toLowerCase() && el.id) return '#' + CSS.escape(el.id);
                }
                return '';
            }""", f["label"]) or "")
        except Exception:
            pass
    return ""
