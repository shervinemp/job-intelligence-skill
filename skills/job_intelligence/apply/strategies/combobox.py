"""Combobox/dropdown fill strategies."""
import json, time


def _try_open_dropdown(page, sel):
    """Click the element at `sel`. Returns True if dropdown opened, None if click failed."""
    try:
        page.locator(sel).click(force=True, timeout=5000)
    except Exception:
        return None
    # Poll for options (dynamic SAP SF listboxes need up to 1s)
    for _ in range(4):
        time.sleep(0.25)
        if _dropdown_opened(page):
            return True
    return False


def _dropdown_opened(page):
    """Check if any [role="option"] is now visible in the page."""
    return page.evaluate("() => Array.from(document.querySelectorAll('[role=\"option\"]')).some(o => o.offsetParent !== null)")


def _find_any_trigger(page, sel):
    """Click chain: visible siblings → parent → input.
    Skips the input if it's display:none (saves time on widget-type combos)."""
    orig = sel

    # Check if input is visible — skip if hidden (widget pattern)
    is_hidden = page.evaluate(f"() => {{ const el = document.querySelector('{sel}'); if (!el) return true; const s = window.getComputedStyle(el); return s.display === 'none' || s.visibility === 'hidden'; }}")

    if not is_hidden:
        opened = _try_open_dropdown(page, sel)
        if opened:
            return sel

    # Try each visible sibling
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

    # Try parent
    parent_id = page.evaluate(f"document.querySelector('{sel}')?.parentElement?.id || ''")
    if parent_id:
        opened = _try_open_dropdown(page, f'[id="{parent_id}"]')
        if opened:
            return f'[id="{parent_id}"]'

    return orig

    return orig


def _select_option(page, sel, ans):
    """Poll for option matching `ans` within the combobox's own listbox.
    Returns selector string for Playwright to click (trusted event)."""
    for _ in range(20):
        time.sleep(0.25)
        opt_sel = page.evaluate(f"""() => {{
            const a = {json.dumps(ans)};
            const input = document.querySelector('{sel}');
            if (!input) return null;
            const owns = input.getAttribute('aria-owns');
            const root = owns ? document.getElementById(owns) : document;
            if (!root) return null;
            const opts = Array.from(root.querySelectorAll('[role="option"], li, [role="menuitem"]'));
            const found = opts.find(o =>
                o.textContent.trim().toLowerCase() === a.toLowerCase() ||
                o.textContent.trim().toLowerCase().includes(a.toLowerCase()) ||
                a.split(' ').filter(w => w.length > 2).every(w => o.textContent.trim().toLowerCase().includes(w))
            );
            if (found && found.id) return '[id="' + found.id + '"]';
            return null;
        }}""")
        if opt_sel:
            try:
                page.locator(opt_sel).click(force=True, timeout=3000)
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
    if _select_option(page, sel, ans):
        return True

    # URL changed — navigate back
    if page.url != url_before:
        page.goto(url_before, wait_until="domcontentloaded", timeout=15000)

    return bool(_text.native_setter(page, sel, ans))
