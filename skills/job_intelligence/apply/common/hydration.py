"""hydration.py — React/Next.js hydration-race detection + selector recovery.

When a probe runs against a page BEFORE hydration finishes, React renders
template nodes with PLACEHOLDER ids — e.g. `id="«rn»"`, `id="«rq»"` — that get
replaced with real ids once hydration completes. A fill that uses the stale
placeholder selector resolves to nothing → `no_filler`, and (worse) the fill
loop can wedge before reaching later steps (the resume upload on LinkedIn Easy
Apply).

This module:
  - detects placeholder-style ids (`«…»`, React's legacy `__reactFiber`, and
    empty/synthetic ids),
  - provides a hydration-safe recovery: when the stale selector no longer
    matches, re-resolve the field by LABEL (which survives hydration) or by
    scanning for a now-hydrated element.

Observation-only in spirit: it fixes the selector, it never certifies a value.
"""

import re

# React/Next.js placeholder ids: «rn», «rq», and friends (guillemet-wrapped
# template node markers), plus React's fiber attributes on synthetic nodes.
_PLACEHOLDER_ID = re.compile(r"^[\u00ab\u00bb\uff08\uff09<>].*?[\u00ab\u00bb\uff08\uff09>]$")
_REACT_FIBER_ATTR = re.compile(r"^__react")

# A selector is hydration-stale when it is a bare id selector whose id looks
# like a React placeholder marker. Label/name/placeholder selectors survive
# hydration and are fine.
_SEL_PLACEHOLDER = re.compile(
    r'^\[id="(.*)"\]$')


def is_placeholder_id(id_value):
    """True when an element id is a React/Next hydration placeholder marker
    (`«rn»`, `«rq»`, or an empty/synthetic marker) rather than a real id."""
    if not id_value:
        return True
    return bool(_PLACEHOLDER_ID.match(id_value)) or bool(
        _REACT_FIBER_ATTR.match(id_value))


def is_hydration_stale_selector(sel):
    """True when a CSS selector targets a placeholder id (`[id="«rn»"]`).
    Only id-based selectors are hydration-stale — name/label/placeholder
    selectors resolve against the hydrated DOM fine."""
    if not sel:
        return False
    m = _SEL_PLACEHOLDER.match(sel)
    if not m:
        return False
    return is_placeholder_id(m.group(1))


def resolve_hydration_safe(page, field):
    """Recover a fillable selector for a field whose captured selector is a
    hydration-stale placeholder id. Prefers re-resolving by label/name (both
    survive hydration); falls back to a page.evaluate scan that re-locates
    the element by its label text and returns a fresh, real id selector.

    Returns a fresh selector string, or "" when no reliable selector can be
    found (the caller keeps the field as no_filler / surfaces it).
    """
    try:
        from apply.steps.probe import resolve_selector
        # Re-resolve by structural hints first — a placeholder id is dropped
        # so label/name/placeholder take over.
        f2 = dict(field)
        f2.pop("id", None)
        f2.pop("_sel", None)
        f2.pop("selector", None)
        sel = resolve_selector(page, f2)
        if sel:
            return sel
    except Exception:
        pass
    # Last resort: scan for an element whose label text matches and return a
    # real id selector. Tolerant match: ATS labels carry `*` (required),
    # trailing question marks, and whitespace, so compare on the normalized
    # core (letters+digits only) rather than exact equality.
    lbl = (field.get("label") or "").strip()
    if not lbl:
        return ""
    import re as _re
    core = _re.sub(r"[^a-z0-9]", "", lbl.lower())
    if len(core) < 3:
        return ""
    try:
        return page.evaluate(
            """(core) => {
            const target = core;
            const norm = s => (s || '').toLowerCase()
                .replace(/[^a-z0-9]/g, '');
            for (const l of document.querySelectorAll('label')) {
                if (norm(l.textContent) === target) {
                    const forId = l.getAttribute('for');
                    if (forId && document.getElementById(forId)) {
                        return '#' + CSS.escape(forId);
                    }
                    const inp = l.querySelector('input:not([type=hidden]):not([type=submit]), '
                        + 'select, textarea, [contenteditable]');
                    if (inp && inp.id) return '#' + CSS.escape(inp.id);
                }
            }
            // fallback: a label whose normalized text CONTAINS the target
            // (labels with extra hint text still identify the field)
            for (const l of document.querySelectorAll('label')) {
                const ln = norm(l.textContent);
                if (ln && ln.includes(target)) {
                    const inp = l.querySelector('input:not([type=hidden]):not([type=submit]), '
                        + 'select, textarea, [contenteditable]');
                    if (inp && inp.id) return '#' + CSS.escape(inp.id);
                }
            }
            for (const el of document.querySelectorAll('[aria-labelledby]')) {
                const ref = document.getElementById(el.getAttribute('aria-labelledby'));
                if (ref && norm(ref.textContent) === target && el.id) {
                    return '#' + CSS.escape(el.id);
                }
            }
            return '';
            }""", core) or ""
    except Exception:
        return ""
