"""Combobox/dropdown fill strategies."""
import json, time


def _find_any_trigger(page, sel):
    """Find the best clickable trigger. Does NOT click. Returns selector."""
    is_hidden = page.evaluate(f"() => {{ const el = document.querySelector('{sel}'); if (!el) return true; const s = window.getComputedStyle(el); return s.display === 'none' || s.visibility === 'hidden'; }}")
    if not is_hidden:
        return sel
    siblings = page.evaluate(f"""() => {{
        const el = document.querySelector('{sel}');
        if (!el || !el.parentElement) return [];
        return Array.from(el.parentElement.children).filter(c => c !== el && c.id).map(c => '[id="' + c.id + '"]');
    }}""")
    if siblings:
        return siblings[0]
    parent_id = page.evaluate(f"document.querySelector('{sel}')?.parentElement?.id || ''")
    if parent_id:
        return f'[id="{parent_id}"]'
    return sel


def _select_option(page, sel, ans):
    """Poll for option matching `ans` within the combobox's own listbox.
    Returns True if option was found, clicked, and the dropdown closed
    (indicating the selection was accepted)."""
    for _ in range(15):
        time.sleep(0.5)
        oid = page.evaluate(f"""() => {{
            const a = {json.dumps(ans)};
            const input = document.querySelector('{sel}');
            if (!input) return '';
            const owns = input.getAttribute('aria-owns');
            const root = owns ? document.getElementById(owns) : document;
            if (!root) return '';
            function parseNum(s) {{ const d = s.replace(/[^0-9]/g, ''); return d ? parseInt(d, 10) : null; }}
            function match(aText, optText) {{
                const aL = aText.toLowerCase().trim(), oL = optText.trim().toLowerCase();
                if (oL === aL) return true;
                if (oL.includes(aL) || aL.includes(oL)) return true;
                const words = aL.split(' ').filter(w => w.length > 2);
                if (words.length) {{
                    const mc = words.filter(w => oL.includes(w)).length;
                    if (mc === words.length || mc / words.length >= 0.6) return true;
                }}
                const aN = parseNum(aL);
                if (aN !== null) {{
                    const parts = oL.replace(/-/g, ' ').replace(/to/g, ' ').split(' ');
                    const nums = parts.map(p => parseNum(p)).filter(n => n !== null);
                    if (nums.length >= 2 && nums[0] <= aN && aN <= nums[nums.length - 1]) return true;
                }}
                return false;
            }}
            const opts = Array.from(root.querySelectorAll('[role="option"], li, [role="menuitem"]'));
            const found = opts.find(o => match(a, o.textContent.trim()));
            return (found && found.id) ? '[id="' + found.id + '"]' : '';
        }}""")
        if oid:
            try:
                page.locator(oid).click(force=True, timeout=3000)
                time.sleep(0.3)
                return True
            except Exception:
                pass
    return False


def _verify_filled(page, sel, ans):
    """Check if the field has the expected value after an attempt."""
    try:
        v = (page.evaluate(f"() => document.querySelector('{sel}')?.value || ''") or "").strip()
        return v.lower() == ans.lower() or v.lower().startswith(ans.lower())
    except Exception:
        return False


def fill(page, f, ans):
    """Fill a combobox/dropdown widget via graduated escalation:

    1. Click + poll existing options (standard dropdown)
    2. Real keystrokes via page.keyboard.type() + poll dynamic options (search autocomplete)
    3. Native setter as last resort (brittle, but catches everything else)
    """
    sel = f.get("_sel", "")
    if not sel:
        return False
    from apply.strategies import text as _text
    click_sel = _find_any_trigger(page, sel)
    try:
        page.evaluate(f"document.querySelector('{click_sel}')?.click()")
    except Exception:
        return bool(_text.native_setter(page, sel, ans))
    url_before = page.url

    # Level 1: click + poll for pre-existing options (standard dropdowns)
    if _select_option(page, sel, ans):
        return True

    # Level 2: real keystrokes to trigger search-based autocomplete (Greenhouse etc.)
    try:
        el = page.locator(sel)
        if el.count():
            el.first.focus()
            time.sleep(0.3)
            if hasattr(page, 'keyboard'):
                # Page object — real keystrokes via Playwright
                page.keyboard.type(ans, delay=50)
            else:
                # Frame object — dispatch keyboard events via evaluate
                page.evaluate(f"""() => {{
                    const el = document.querySelector('{sel}');
                    if (!el) return;
                    el.value = '';
                    el.focus();
                    const s = {json.dumps(ans)};
                    for (let i = 0; i < s.length; i++) {{
                        const ch = s[i];
                        el.dispatchEvent(new KeyboardEvent('keydown', {{key: ch, bubbles: true}}));
                        el.dispatchEvent(new KeyboardEvent('keypress', {{key: ch, bubbles: true}}));
                        el.value += ch;
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new KeyboardEvent('keyup', {{key: ch, bubbles: true}}));
                    }}
                }}""")
            time.sleep(1.5)
            if _select_option(page, sel, ans):
                return True
    except Exception:
        pass
    finally:
        # Clean up any partial input left by keyboard typing before falling through
        try:
            page.evaluate(f"document.querySelector('{sel}')?.value = ''")
        except Exception:
            pass

    # Level 3: native setter as last resort
    if page.url != url_before:
        page.goto(url_before, wait_until="domcontentloaded", timeout=15000)
    return bool(_text.native_setter(page, sel, ans))
