"""Combobox/dropdown fill strategies."""
import json, time


def fill(page, f, ans):
    """Fill a combobox/dropdown widget via cascading strategy.
    Returns True if filled."""
    sel = f.get("_sel", "")
    if not sel:
        return False
    page.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
    time.sleep(0.1)
    from apply.strategies import text as _text
    try:
        page.locator(sel).click(force=True, timeout=5000)
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
