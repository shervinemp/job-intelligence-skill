"""Pass 1 probe: enrich fields with selectors and structural info."""
import re


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


def normalize_label(lbl):
    return re.sub(r"[^a-z0-9+#]+", " ", lbl.lower()).strip()


def run(page, fields):
    """Pass 1: resolve selectors + capture available options for comboboxes.
    Read-only, no side effects — options read from hidden listboxes / aria-owns."""
    for field in fields:
        sel = resolve_selector(page, field)
        if sel:
            field["_sel"] = sel

        # Capture options for comboboxes without clicking (read-only DOM query)
        if field.get("role") == "combobox" and not field.get("options"):
            try:
                opts = page.evaluate(f"""() => {{
                    const el = document.querySelector('{sel}');
                    if (!el) return [];
                    // Read options from aria-owns listbox
                    const owns = el.getAttribute('aria-owns');
                    if (owns) {{
                        const list = document.getElementById(owns);
                        if (list) {{
                            const items = Array.from(list.querySelectorAll('[role="option"], li, [role="menuitem"]'))
                                .map(o => o.textContent.trim())
                                .filter(Boolean)
                                .slice(0, 30);
                            if (items.length) return items;
                        }}
                    }}
                    // Read options from <datalist>
                    const listId = el.getAttribute('list');
                    if (listId) {{
                        const dl = document.getElementById(listId);
                        if (dl) {{
                            const items = Array.from(dl.querySelectorAll('option'))
                                .map(o => o.textContent.trim())
                                .filter(Boolean)
                                .slice(0, 30);
                            if (items.length) return items;
                        }}
                    }}
                    return [];
                }}""")
                if opts:
                    field["options"] = opts
            except Exception:
                pass
    return fields
