"""Combobox/dropdown fill strategies."""
import json, time


def _find_click_target(page, sel):
    """Find a clickable element: input itself, or nearest visible sibling/icon
    (hidden input + visible icon pattern used by many widget frameworks)."""
    target = page.evaluate(f"""() => {{
        const el = document.querySelector('{sel}');
        if (!el) return null;

        // If element is visible, click it directly
        const style = window.getComputedStyle(el);
        if (style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null)
            return {{sel: '{sel}', tag: el.tagName}};

        // Hidden input: find visible sibling icon/button in same container
        const parent = el.parentElement;
        if (!parent) return null;

        // Check siblings for visible clickable elements
        for (const child of parent.children) {{
            if (child === el) continue;
            const cs = window.getComputedStyle(child);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            if (child.offsetParent === null) continue;
            // Found a visible element — use it if it looks like a widget trigger
            if (child.id) return {{sel: '[id="' + child.id + '"]', tag: child.tagName}};
        }}

        // Check parent container for visible clickable children
        const container = parent.closest('[class*="ComboBox"], [class*="Dropdown"], [class*="Select"], [class*="widget"]');
        if (container) {{
            for (const child of container.children) {{
                const cs = window.getComputedStyle(child);
                if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                if (child.offsetParent === null) continue;
                if (child.id) return {{sel: '[id="' + child.id + '"]', tag: child.tagName}};
            }}
        }}

        return null;
    }}""")
    return target


def fill(page, f, ans):
    """Fill a combobox/dropdown widget via cascading strategy.
    Returns True if filled."""
    sel = f.get("_sel", "")
    if not sel:
        return False
    from apply.strategies import text as _text

    # Find the right element to click (input itself or visible sibling trigger)
    target = _find_click_target(page, sel)
    click_sel = target["sel"] if target else sel

    try:
        page.locator(click_sel).click(force=True, timeout=5000)
    except Exception:
        return bool(_text.native_setter(page, sel, ans))
    opt = None
    url_before = page.url
    for _ in range(20):
        time.sleep(0.25)
        if page.url != url_before:
            page.goto(url_before, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
            break
        opt = page.evaluate(f"""() => {{
            const a = {json.dumps(ans)};
            const sel = '[role="option"], li, [role="menuitem"], [class*="option"], [class*="item"]';
            const all = Array.from(document.querySelectorAll(sel));
            document.querySelectorAll(':defined').forEach(el => {{
                if (el.shadowRoot) all.push(...el.shadowRoot.querySelectorAll(sel));
            }});
            const m = all.find(o => o.offsetParent !== null && (o.textContent.trim().toLowerCase() === a.toLowerCase() || o.textContent.trim().toLowerCase().includes(a.toLowerCase())));
            return m ? (m.id ? '[id="' + m.id + '"]' : m.textContent.trim().slice(0, 30)) : null;
        }}""")
        if opt:
            break
    if opt:
        try:
            if opt.startswith("["):
                page.locator(opt).click(force=True, timeout=3000)
            else:
                page.locator(f'[role="option"]:has-text("{opt}")').first.click(force=True, timeout=3000)
            time.sleep(0.3)
            return True
        except Exception:
            pass
    try:
        container = page.evaluate(f"""() => {{
            const el = document.querySelector('{sel}');
            if (!el) return '';
            const c = el.closest('[class*="ComboBox"], [class*="Dropdown"], [class*="Select"], [class*="widget"], .fieldComponentInput');
            if (c && c.id) return '[id="' + c.id + '"]';
            if (c && el.parentElement && el.parentElement.id) return '[id="' + el.parentElement.id + '"]';
            return '';
        }}""")
        if container:
            page.locator(container).click(force=True, timeout=3000)
            time.sleep(0.5)
            opt = page.evaluate(f"""() => {{
                const a = {json.dumps(ans)};
                const m = Array.from(document.querySelectorAll('[role="option"], li, [role="menuitem"]')).find(o => o.offsetParent !== null && o.textContent.trim().toLowerCase().includes(a.toLowerCase()));
                return m ? (m.id ? '[id="' + m.id + '"]' : null) : null;
            }}""")
            if opt:
                page.locator(opt).click(force=True, timeout=3000)
                time.sleep(0.3)
                return True
    except Exception:
        pass
    return bool(_text.native_setter(page, sel, ans))
