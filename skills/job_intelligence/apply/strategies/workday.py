"""strategies/workday.py — Workday-specific fill semantics (COMPARISON §S4).

Workday is the ATS where a generic outside-in fill is least reliable:
its widgets are React-fiber driven, the skills/school typeahead confirms
on Enter (a click on the suggestion can be ignored), and a stale
"requires a value" error state blocks the next field until cleared.

This module implements our OWN guarded Workday protocol (no code copied
from any extension):

  1. clear_field_errors — before filling, dispatch an input/change and
     focus so Workday's error underline re-evaluates (best-effort).
  2. enter_to_confirm — for typeahead fields (skills, school, location)
     with `enter_to_confirm` hint: type, wait for the suggestion list,
     press Enter to confirm instead of clicking.
  3. fiber read — a *guarded* MAIN-world React-fiber reader used only to
     disambiguate option lists / confirm a selection. Never certifies:
     the deterministic read-back (filler._check_delta) remains the
     certifier. If fiber is absent or the read fails, returns None and
     the caller falls through to the generic path.

Every function is fail-closed: on any exception it returns False/None
and never raises into the fill loop.
"""
import json
import re
import time


# ─── Field-class classification ──────────────────────────────────────

_SKILLS_LABEL_RE = re.compile(r"skill|competenc|school|education|degree|certif", re.I)


def is_typeahead_field(f):
    """True for Workday fields that confirm via Enter (skills/school/location)."""
    label = (f.get("label") or "")
    hint = (f.get("hint_skills_enter") or "")
    if hint:
        return True
    if _SKILLS_LABEL_RE.search(label):
        # "Education" free text can be a plain input — only treat it as a
        # typeahead when the widget signals a list (aria-haspopup / combobox).
        role = (f.get("role") or "")
        return role == "combobox" or "haspopup" in (f.get("aria_autocomplete") or "")
    return False


# ─── Workday error-clearing (best-effort) ────────────────────────────

def clear_field_errors(page, sel):
    """Dispatch focus + input on the field so Workday re-evaluates its
    error state before we type. Best-effort; never raises."""
    try:
        page.evaluate(f"""() => {{
            const el = document.querySelector({json.dumps(sel)});
            if (!el) return;
            el.focus();
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}""")
        time.sleep(0.2)
        return True
    except Exception:
        return False


# ─── Guarded React-fiber helpers (COMPARISON §S2) ────────────────────
# We deliberately do NOT walk fiber to mutate state — that is Jobright's
# approach and it is fragile + invasive. Our fiber use is READ-ONLY and
# strictly a disambiguation aid: extract the option list / selected label
# Workday keeps in its fiber props when the DOM gives us nothing. The
# read-back certifier decides truth.

def _fiber_read_js(sel):
    """MAIN-world JS that locates the nearest React fiber for `sel` and
    returns {props_type, options, selected, value} or null."""
    return f"""() => {{
        const el = document.querySelector({json.dumps(sel)});
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$')
            || k.startsWith('__reactInternalInstance$'));
        if (!key) return null;
        let node = el[key];
        for (let hops = 0; hops < 12 && node; hops++) {{
            const p = node.memoizedProps;
            if (p && (p.options || p.items || p.selectedOptions
                      || p.ariaLabel || p.placeholder)) {{
                const out = {{ type: typeof node.type === 'function'
                                ? (node.type.displayName || node.type.name || '')
                                : String(node.type) }};
                if (p.options) out.options = Array.isArray(p.options)
                    ? p.options.map(o => typeof o === 'string' ? o : (o && (o.label ?? o.name) ?? ''))
                    : [String(p.options)];
                if (p.items) out.options = Array.isArray(p.items)
                    ? p.items.map(o => typeof o === 'string' ? o : (o && (o.label ?? o.name) ?? ''))
                    : [String(p.items)];
                if (p.selectedOptions) out.selected = Array.isArray(p.selectedOptions)
                    ? p.selectedOptions.map(o => typeof o === 'string' ? o : (o && (o.label ?? o.value) ?? ''))
                    : [];
                if (p.value !== undefined && p.value !== null) out.value = String(p.value);
                return out;
            }}
            node = node.return;
        }}
        return null;
    }}"""


def read_fiber(page, sel):
    """Guarded React-fiber read of a field's option list / selected value.

    Returns a dict {type, options, selected, value} or None when no fiber
    is present or the read fails. READ-ONLY — never mutates the page.
    The caller (combobox filler) uses it only to disambiguate; the
    deterministic read-back remains the certifier.
    """
    try:
        result = page.evaluate(_fiber_read_js(sel))
        if isinstance(result, dict) and result:
            return result
    except Exception:
        pass
    return None


def confirm_with_enter(page, sel, ans, max_options_seen=8):
    """Type `ans`, wait for the suggestion list, press Enter to confirm.

    Returns True only when the selection is VERIFIED via the deterministic
    value-reader cascade (or is unverifiable-but-typed, matching the
    combobox filler's accept-unverified semantics). The combobox branch of
    _fill_one TRUSTS the filler's True — so returning True without a
    read-back would certify an Enter that never landed (verification gap).
    Falls back to returning False so the caller can try the generic
    combobox path.
    """
    from apply.strategies.combobox import (_open_menu, _type_and_poll,
                                           _close_menu, _listbox_root_id,
                                           _read_selection_values, _score_option)
    from apply.common.match import scoring_candidates as _msc
    try:
        root_id = _listbox_root_id(page, sel)
        if not _open_menu(page, sel, root_id):
            _close_menu(page)
            return False
        opts = _type_and_poll(page, sel, ans, root_id)
        if not opts:
            _close_menu(page)
            return False
        # Press Enter on the highlighted first suggestion.
        kb = page if hasattr(page, "keyboard") else getattr(page, "page", page)
        if hasattr(kb, "keyboard"):
            kb.keyboard.press("Enter")
        else:
            return False
        time.sleep(0.6)
        # VERIFY the selection actually landed. Read back the combobox value
        # and score it against the answer (same shared matcher as combobox).
        # A verified match → True. Unreadable but non-empty → accepted-
        # unverified True (the read-back certifier in check.py arbitrates).
        # Provably wrong / empty → False so the generic path retries.
        values = _read_selection_values(page, sel)
        if not values:
            _close_menu(page)
            return False
        cnorms = _msc([str(ans)])
        best = max((_score_option(str(v), cnorms) for v in values), default=0)
        if best >= 2:
            return True
        # Nothing matched — Enter did not land on the answer. Fall through
        # to the generic combobox filler which types + clicks + verifies.
        _close_menu(page)
        return False
    except Exception:
        return False
