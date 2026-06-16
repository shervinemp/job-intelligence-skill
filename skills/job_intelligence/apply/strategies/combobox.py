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


def _parse_number(s):
    """Extract numeric value from a string like '$150,001' or '150000'."""
    import re
    digits = re.sub(r'[^0-9]', '', s)
    return int(digits) if digits else None


def _match_option(ans, opt_text):
    """Check if answer matches option text. Returns True if match.
    Strategies: exact, contains, word-level, then numeric range."""
    a = ans.lower()
    o = opt_text.lower().strip()
    if o == a or o == "no selection":
        return o == a  # exact match (but not "No Selection" default)
    if o == a or o.startswith(a) or a in o:
        return True
    # Word-level: all significant answer words appear in option
    words = [w for w in a.split() if len(w) > 2]
    if words and all(w in o for w in words):
        return True
    # Numeric range: if both answer and option contain numbers,
    # check if answer number falls within option's numeric range
    ans_num = _parse_number(a)
    if ans_num is not None:
        opt_nums = [n for n in [_parse_number(t) for t in o.replace('-', ' ').replace('to', ' ').split()] if n is not None]
        if len(opt_nums) >= 2:
            return opt_nums[0] <= ans_num <= opt_nums[-1]
    return False


def _select_option(page, sel, ans):
    """Poll for option matching `ans` within the combobox's own listbox.
    All matching in JS (atomic). Returns True if selected."""
    for _ in range(20):
        time.sleep(0.25)
        clicked = page.evaluate(f"""() => {{
            const a = {json.dumps(ans)};
            const input = document.querySelector('{sel}');
            if (!input) return false;
            const owns = input.getAttribute('aria-owns');
            const root = owns ? document.getElementById(owns) : document;
            if (!root) return false;

            // Helper: extract number from string
            function parseNum(s) {{
                const d = s.replace(/[^0-9]/g, '');
                return d ? parseInt(d, 10) : null;
            }}

            // Helper: check if answer matches option text
            function match(aText, optText) {{
                const aLow = aText.toLowerCase().trim();
                const oLow = optText.trim().toLowerCase();
                if (oLow === aLow) return true;
                if (oLow.includes(aLow) || aLow.includes(oLow)) return true;
                // Word-level: all significant answer words appear in option
                const words = aLow.split(' ').filter(w => w.length > 2);
                if (words.length && words.every(w => oLow.includes(w))) return true;
                // Numeric range
                const aNum = parseNum(aLow);
                if (aNum !== null) {{
                    const parts = oLow.replace(/-/g, ' ').replace(/to/g, ' ').split(' ');
                    const nums = parts.map(p => parseNum(p)).filter(n => n !== null);
                    if (nums.length >= 2 && nums[0] <= aNum && aNum <= nums[nums.length - 1]) return true;
                }}
                return false;
            }}

            const opts = Array.from(root.querySelectorAll('[role="option"], li, [role="menuitem"]'));
            const found = opts.find(o => match(a, o.textContent.trim()));
            if (found) {{ found.click(); return true; }}
            return false;
        }}""")
        if clicked:
            time.sleep(0.3)
            return True
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
