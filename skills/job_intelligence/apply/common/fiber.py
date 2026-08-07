"""apply/common/fiber.py — Guarded, READ-ONLY React-fiber helpers (COMPARISON §S2).

Three hard ATS keep the truth of a field inside React fiber props rather
than the DOM: Workday (options/selected for its dropdowns and typeahead),
Ashby (field type / serialization / locationTypes), and Recruitee
(phone-country selection). A generic DOM reader can't see those.

Design contract (from COMPARISON §S2):
  - READ-ONLY. We never mutate fiber state — walking fiber to write is
    exactly the invasive, fragile technique that breaks on any ATS update.
    Our value-setting stays outside-in (native setters + real events);
    fiber is used only to *disambiguate* (which option list / which label
    is selected) when the DOM gives nothing.
  - FAIL-CLOSED. Every function returns None/False on any exception or
    missing fiber; nothing raises into the fill loop.
  - NEVER CERTIFIES. The deterministic read-back (_check_delta) remains
    the sole certifier of a fill. Fiber output is a hint the filler can
    use, not a verdict the pipeline trusts.

This is our own implementation of the technique — no extension source
was copied.
"""
import json


# ─── Core fiber locator ──────────────────────────────────────────────

def _fiber_read_js(sel):
    """MAIN-world JS that locates the nearest React fiber for `sel` and
    returns {type, options, selected, value, props} or null.

    Walks up the fiber chain (max 12 hops) looking for a memoizedProps
    object that carries option-like fields. Keys seen across the hard
    three: options, items, selectedOptions, value, ariaLabel, placeholder,
    and Ashby's serialization metadata (fieldType, fieldTypeLabel, options)."""
    return f"""() => {{
        const el = document.querySelector({json.dumps(sel)});
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$')
            || k.startsWith('__reactInternalInstance$'));
        if (!key) return null;
        let node = el[key];
        for (let hops = 0; hops < 12 && node; hops++) {{
            const p = node.memoizedProps;
            if (p && (p.options || p.items || p.selectedOptions || p.fieldType
                      || p.ariaLabel || p.placeholder || p.serialization)) {{
                const out = {{ type: typeof node.type === 'function'
                                ? (node.type.displayName || node.type.name || '')
                                : String(node.type) }};
                const opt = (p.options || p.items || []);
                if (Array.isArray(opt) && opt.length) {{
                    out.options = opt.map(o => typeof o === 'string' ? o
                        : (o && (o.label ?? o.name ?? o.value) ?? String(o)));
                }}
                if (Array.isArray(p.selectedOptions) && p.selectedOptions.length) {{
                    out.selected = p.selectedOptions.map(o => typeof o === 'string' ? o
                        : (o && (o.label ?? o.value) ?? String(o)));
                }}
                if (p.value !== undefined && p.value !== null) out.value = String(p.value);
                if (p.fieldType) out.field_type = String(p.fieldType);
                if (p.fieldTypeLabel) out.field_type_label = String(p.fieldTypeLabel);
                if (p.serialization) out.serialization = String(p.serialization);
                return out;
            }}
            node = node.return;
        }}
        return null;
    }}"""


def read_fiber(page, sel):
    """Guarded React-fiber read of a field's option list / selected value.

    Returns a dict {type, options, selected, value, field_type, ...} or
    None when no fiber is present or the read fails. READ-ONLY.
    """
    try:
        result = page.evaluate(_fiber_read_js(sel))
        if isinstance(result, dict) and result:
            return result
    except Exception:
        pass
    return None


# ─── Ashby field metadata (COMPARISON §S2) ───────────────────────────

def read_ashby_metadata(page, sel):
    """Ashby keeps each field's type/serialization in fiber props. Returns
    {field_type, serialization, options} or None. Used by the combobox to
    know whether a field is a dropdown (options) vs free text."""
    try:
        f = read_fiber(page, sel)
        if not f:
            return None
        out = {}
        if f.get("field_type"):
            out["field_type"] = f["field_type"]
        if f.get("serialization"):
            out["serialization"] = f["serialization"]
        if f.get("options"):
            out["options"] = f["options"]
        return out or None
    except Exception:
        return None


# ─── Recruitee phone-country (COMPARISON §S2) ────────────────────────

def read_recruitee_country(page, sel):
    """Recruitee keeps the selected phone country in fiber props
    (candidate.phoneCountry / metadata.country_calling_codes). Returns the
    selected ISO-2 country code or None. READ-ONLY disambiguation."""
    try:
        f = read_fiber(page, sel)
        if not f:
            return None
        selected = f.get("selected") or []
        if selected:
            return str(selected[0]).lower()
        v = f.get("value")
        if v and len(v) == 2 and v.isalpha():
            return v.lower()
        return None
    except Exception:
        return None


# ─── Option-list disambiguation helper ───────────────────────────────

def options_from_fiber(page, sel):
    """Option texts a fiber holds for `sel`, or []. Callers merge this into
    their DOM-derived option list when the DOM is empty (Workday dropdowns
    render options only after focus; fiber has them immediately)."""
    try:
        f = read_fiber(page, sel)
        if not f or not f.get("options"):
            return []
        out = []
        for o in f["options"]:
            s = str(o).strip()
            if s and s not in out:
                out.append(s)
        return out
    except Exception:
        return []
