"""Combobox/dropdown fill strategies."""
import json, time


def _try_open_dropdown(page, sel):
    """Click the element at `sel`. If dropdown opens (visible [role="option"]), return True.
    Returns None if click fails, else True/False for opened/not opened."""
    try:
        page.locator(sel).click(force=True, timeout=5000)
    except Exception:
        return None
    time.sleep(0.5)
    return len(page.locator('[role="option"]').all()) > 0


def _find_any_trigger(page, sel):
    """Click chain: input → siblings → parent → container.
    Returns selector that successfully opened the dropdown, or None."""
    orig = sel
    # 1. Try the input itself
    opened = _try_open_dropdown(page, sel)
    if opened:
        return sel

    # 2. Try each visible sibling in the same parent
    siblings = page.evaluate(f"""() => {{
        const el = document.querySelector('{sel}');
        if (!el || !el.parentElement) return [];
        const ids = [];
        for (const c of el.parentElement.children) {{
            if (c === el) continue;
            const s = window.getComputedStyle(c);
            if (s.display !== 'none' && s.visibility !== 'hidden' && c.offsetParent !== null && c.id)
                ids.push('[id="' + c.id + '"]');
        }}
        return ids;
    }}""")
    for sib in siblings:
        opened = _try_open_dropdown(page, sib)
        if opened:
            return sib

    # 3. Try parent
    parent_id = page.evaluate(f"document.querySelector('{sel}')?.parentElement?.id || ''")
    if parent_id:
        opened = _try_open_dropdown(page, f'[id="{parent_id}"]')
        if opened:
            return f'[id="{parent_id}"]'

    return orig


def _select_option(page, ans):
    """Poll for option matching `ans`, click it. Returns True if selected."""
    for _ in range(20):
        time.sleep(0.25)
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
            try:
                if opt.startswith("["):
                    page.locator(opt).click(force=True, timeout=3000)
                else:
                    page.locator(f'[role="option"]:has-text("{opt}")').first.click(force=True, timeout=3000)
                time.sleep(0.3)
                return True
            except Exception:
                pass
    return False


def fill(page, f, ans):
    """Fill a combobox/dropdown widget via cascading strategy."""
    sel = f.get("_sel", "")
    if not sel:
        return False
    from apply.strategies import text as _text

    click_sel = _find_any_trigger(page, sel)
    try:
        page.locator(click_sel).click(force=True, timeout=5000)
    except Exception:
        return bool(_text.native_setter(page, sel, ans))

    url_before = page.url
    if _select_option(page, ans):
        return True

    # URL changed — navigate back
    if page.url != url_before:
        page.goto(url_before, wait_until="domcontentloaded", timeout=15000)

    return bool(_text.native_setter(page, sel, ans))
