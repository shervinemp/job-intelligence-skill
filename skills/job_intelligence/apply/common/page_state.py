"""page_state.py — Single source of truth for page state queries.

Consolidates patterns that were duplicated across fill.py, submit.py,
and helpers.py: form detection, dialog detection, iframe form scanning,
and button finding.

All functions are read-only (no clicks, no navigation)."""
import time


def has_dialog(page) -> bool:
    """True if a <dialog> or [role="dialog"] element exists on the page."""
    try:
        return bool(page.evaluate(
            "() => !!document.querySelector('dialog, [role=\"dialog\"]')"
        ))
    except Exception:
        return False


def has_form(page) -> bool:
    """True if any form element (input, select, textarea) exists in the
    main document. Does NOT check iframes — use has_any_form() for that."""
    try:
        return bool(page.query_selector('input, select, textarea'))
    except Exception:
        return False


def has_iframe_form(page) -> bool:
    """True if any iframe contains form elements. Checks all frames
    except the main frame."""
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            if fr.query_selector('input, select, textarea'):
                return True
        except Exception:
            continue
    return False


def has_any_form(page, custom_widget_selectors=None) -> bool:
    """True if form elements exist anywhere: main document, dialog,
    iframes, or custom widget elements (e.g. Workday's
    button[aria-haspopup='listbox']).

    This is the conservative check for 'is there a form to interact
    with?' used by no_apply_path detection.

    Args:
        custom_widget_selectors: dict of widget_type -> CSS selector
            from registry config (e.g. {"dropdown": "button[aria-haspopup]"})
    """
    if has_form(page) or has_dialog(page) or has_iframe_form(page):
        return True
    if custom_widget_selectors:
        try:
            for selector in custom_widget_selectors.values():
                if page.query_selector(selector):
                    return True
        except Exception:
            pass
    return False


def find_buttons(page, keywords, scope="any"):
    """Find buttons matching keywords. Returns list of {text, score, tag,
    disabled, in_dialog} sorted by score descending.

    scope:
      "dialog" — only buttons inside dialog/modal (LinkedIn Easy Apply)
      "page"   — only buttons NOT inside dialog/modal
      "any"    — all buttons (default), dialog buttons scored higher

    Handles LinkedIn Easy Apply <dialog open> where children have
    offsetParent === null — dialog buttons are NOT filtered by offsetParent.
    """
    try:
        return page.evaluate("""(args) => {
            const [kws, scope] = args;
            const inDlg = (el) => !!el.closest('dialog, [role="dialog"]');
            const isDisabled = (el) => el.disabled || el.getAttribute('aria-disabled') === 'true';
            const all = document.querySelectorAll('button, a, [role="button"], input[type=submit]');
            const out = [];
            for (const el of all) {
                const dlg = inDlg(el);
                // Filter by scope
                if (scope === 'dialog' && !dlg) continue;
                if (scope === 'page' && dlg) continue;
                // offsetParent: skip for dialog buttons (Easy Apply fix),
                // required for page-level buttons
                if (!dlg && el.offsetParent === null) continue;
                if (isDisabled(el)) continue;
                if (!dlg && el.closest('nav, header, footer, [role=navigation], [role=banner], [role=contentinfo]')) continue;
                const t = ((el.textContent || el.value || '')).trim().toLowerCase().replace(/\\s+/g, ' ');
                if (!t || t.length > 30) continue;
                let score = 0;
                for (const kw of kws) {
                    if (t === kw) score = Math.max(score, 4);
                    else if (t.startsWith(kw)) score = Math.max(score, 3);
                    else if (t.includes(kw)) score = Math.max(score, 2);
                }
                if (score > 0) {
                    out.push({text: t.slice(0, 30), score: dlg ? score + 10 : score,
                              tag: el.tagName, disabled: false, in_dialog: dlg});
                }
            }
            out.sort((a, b) => b.score - a.score);
            return out;
        }""", [keywords, scope])
    except Exception:
        return []


def click_button_by_text(page, text, prefer_dialog=True) -> bool:
    """Click a button by exact text match, with substring fallback.
    Handles dialog offsetParent issue. Returns True if clicked.

    prefer_dialog: when True, tries dialog buttons first (for Easy Apply).
    """
    target = (text or "").strip().lower()
    if not target:
        return False
    try:
        return bool(page.evaluate("""(args) => {
            const [t, preferDlg] = args;
            const inDlg = (el) => !!el.closest('dialog, [role="dialog"]');
            const isDisabled = (el) => el.disabled || el.getAttribute('aria-disabled') === 'true';
            const all = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            const vis = all.filter(el => {
                if (isDisabled(el)) return false;
                if (inDlg(el)) return true;  // dialog buttons: skip offsetParent
                return el.offsetParent !== null;
            });
            const inModal = vis.filter(el => el.closest('dialog, [role="dialog"], [class*="modal"], [class*=" Modal"], [data-test*="modal"], [class*="easy-apply"]'));
            const onPage = vis.filter(el => !el.closest('dialog, [role="dialog"], [class*="modal"], [class*=" Modal"], [data-test*="modal"], [class*="easy-apply"]'));
            const pools = preferDlg ? [inModal, onPage] : [onPage, inModal];
            for (const pool of pools) {
                for (const el of pool) {
                    if ((el.textContent || '').trim().toLowerCase() === t) { el.click(); return true; }
                }
                for (const el of pool) {
                    if ((el.textContent || '').trim().toLowerCase().includes(t)) { el.click(); return true; }
                }
            }
            return false;
        }""", [target, prefer_dialog]))
    except Exception:
        return False


def wait_for_form(page, timeout=8) -> bool:
    """Wait up to `timeout` seconds for form elements to appear.
    Returns True if fields appeared, False on timeout."""
    for _ in range(timeout):
        try:
            n = page.evaluate("""() => document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]), select, textarea'
            ).length""")
            if n:
                return True
        except Exception:
            return False
        time.sleep(1)
    return False
