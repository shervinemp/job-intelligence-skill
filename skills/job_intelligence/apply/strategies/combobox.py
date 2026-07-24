"""Combobox/dropdown fill — generalized for react-select, select2, and any
widget that opens a menu on real click and filters on keystroke.

Single unified flow (no branching by widget type):
  1. Scroll into view, real Playwright click to open menu (fires onMouseDown)
  2. Type a short prefix of the answer (first word(s) before comma)
  3. Dispatch input event (triggers API typeaheads that listen for onChange)
  4. Poll for visible options, click best match by text/id/coordinates
  5. On failure: clear typed text, fail honestly (no native_setter phantom)

The Frame keyboard fix (frame.page) makes this work for comboboxes inside
cross-origin iframes — the only difference between a Page and a Frame here
is that Frames lack the keyboard attribute.
"""
import json, time


def _select_option(page, sel, ans, max_polls=10):
    """Poll for a visible option matching `ans`, then click it.
    Click escalation: id-locator → JS click (in-evaluate) → text-locator
    → coordinate click. Returns True on successful click."""
    for i in range(max_polls):
        time.sleep(0.3)
        result = page.evaluate(f"""() => {{
            const a = {json.dumps(ans)};
            const input = document.querySelector('{sel}');
            if (!input) return null;
            // Find the listbox: aria-owns → aria-controls → aria-describedby
            // (react-select v5) → whole document (portal-rendered typeaheads)
            const owns = input.getAttribute('aria-owns') || input.getAttribute('aria-controls');
            let root = owns ? document.getElementById(owns) : null;
            if (!root) {{
                const desc = input.getAttribute('aria-describedby');
                if (desc) {{
                    const m = desc.match(/react-select-instance-(.*?)-placeholder/);
                    if (m && m[1]) root = document.getElementById('react-select-instance-' + m[1] + '-listbox');
                }}
            }}
            if (!root) root = document;
            function parseNum(s) {{ const d = s.replace(/[^0-9]/g, ''); return d ? parseInt(d, 10) : null; }}
            function score(aText, optText) {{
                const aL = aText.toLowerCase().trim(), oL = optText.trim().toLowerCase();
                if (oL === aL) return 4;
                if (oL.startsWith(aL)) return 3;
                if (oL.includes(aL) || aL.includes(oL)) return 2;
                const words = aL.split(' ').filter(w => w.length > 2);
                if (words.length) {{
                    const mc = words.filter(w => oL.includes(w)).length;
                    if (mc === words.length || mc / words.length >= 0.6) return 2;
                }}
                const aN = parseNum(aL);
                if (aN !== null) {{
                    const parts = oL.replace(/-/g, ' ').replace(/to/g, ' ').split(' ');
                    const nums = parts.map(p => parseNum(p)).filter(n => n !== null);
                    if (nums.length >= 2 && nums[0] <= aN && aN <= nums[nums.length - 1]) return 2;
                }}
                return 0;
            }}
            const opts = Array.from(root.querySelectorAll(
                '[role="option"], li, [role="menuitem"], .select2-results__option'
            )).filter(o => o.offsetParent !== null);
            let best = null, bestScore = 0;
            for (const o of opts) {{
                const s = score(a, o.textContent.trim());
                if (s > bestScore) {{ bestScore = s; best = o; }}
            }}
            if (!best) return null;
            const rect = best.getBoundingClientRect();
            if (!best.id) best.click();
            return {{
                text: best.textContent.trim().slice(0, 60),
                id: best.id || '',
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2,
            }};
        }}""")
        if not result or not (result.get("id") or result.get("text")):
            continue
        text = result.get("text", "")
        oid = result.get("id", "")
        x, y = result.get("x", 0), result.get("y", 0)
        # Click escalation: id-locator → text-locator → coordinate click
        if oid:
            try:
                page.locator(f'[id="{oid}"]').click(force=True, timeout=3000)
                time.sleep(0.3)
                return True
            except Exception:
                pass
        if text:
            try:
                page.locator(f'[role="option"]:has-text("{text}")').first.click(force=True, timeout=2000)
                time.sleep(0.3)
                return True
            except Exception:
                pass
        if x and y:
            try:
                page.mouse.click(x, y)
                time.sleep(0.3)
                return True
            except Exception:
                pass
    return False


def fill(page, f, ans):
    """Fill a combobox/dropdown widget. Returns True when a selection is made.
    Verification is handled by dispatch._element_value (combobox-aware reader
    cascade), NOT here — checking here would kill non-react-select dropdowns."""
    sel = f.get("_sel", "")
    if not sel:
        return False
    url_before = page.url

    # Short prefix for typeahead filtering (full answer yields zero suggestions)
    type_text = ans.split(",")[0].strip() if "," in ans else ans
    words = type_text.split()
    if len(words) > 2:
        type_text = " ".join(words[:2])

    try:
        el = page.locator(sel)
        if not el.count():
            return False
        try:
            el.first.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        # Real Playwright click opens the menu (fires onMouseDown — synthetic
        # JS clicks don't, so react-select menus never open from .click())
        el.first.click(timeout=2000)
        time.sleep(0.3)
        # Type the prefix — react-select opens/filters the menu on keystroke.
        # page may be a Frame (no keyboard) — get the parent Page for keyboard.
        kb = page if hasattr(page, "keyboard") else getattr(page, "page", page)
        if hasattr(kb, "keyboard"):
            kb.keyboard.type(type_text, delay=50)
            # Dispatch input event for typeaheads that listen for onChange
            page.evaluate(f"""() => {{
                const el = document.querySelector('{sel}');
                if (el) el.dispatchEvent(new Event('input', {{bubbles: true}}));
            }}""")
        # Poll for visible options and click the best match
        if _select_option(page, sel, ans):
            return True
    except Exception:
        pass

    # Clear leftover typed text on failure (not in finally — would wipe
    # a successful selection's input on some widgets)
    try:
        page.evaluate(f"document.querySelector('{sel}')?.value = ''")
    except Exception:
        pass

    if page.url != url_before:
        try:
            page.goto(url_before, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
    return False
