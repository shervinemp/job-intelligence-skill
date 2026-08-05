"""dom_diff.py — minimal structural observation of a field's fill interaction.

The value-string read-back tells us WHAT the field's value is, but not WHAT
THE PAGE DID in response. For dynamic fields (combobox/select/shadow widgets)
a minimal DOM-delta is the honest "the page reacted" signal — most useful for
the two classes value-read-back is weakest on:

  1. React-controlled inputs that reject/overwrite: el.value reads unchanged
     but the page re-rendered / showed an error / revealed a node.
  2. Opaque-widget fields (URN typeahead): el.value is the internal id but
     the VISIBLE DOM shows the real answer.

Mechanism: a scoped MutationObserver registered on the field's container
immediately before the fill, drained immediately after. An observer captures
only what changed DURING the interaction — immune to unrelated page churn that
a before/after HTML snapshot would catch.

Guardrail (C-O2, DOM_DIFF_OBSERVATION.md): this is OBSERVATION ONLY. It is
written into the field's dossier/audit record as `dom_delta` and NEVER used to
certify a value. `_check_delta` remains the certifier.
"""

import json

# Registered per-field just before fill. Scopes to the field's container
# (shadow root when present, else closest form/fieldset, else parent), records
# childList/subtree/attributes/characterData, and drops class/style-only churn
# to keep the signal minimal. Records are tagged by the field's selector.
_START_JS = r"""(args) => {
  const sel = args[0];
  const el = document.querySelector(sel);
  if (!el) return false;
  const root = el.shadowRoot || el.closest('form, fieldset, [data-test-form-builder]')
              || el.parentElement;
  if (!root) return false;
  window.__dom_obs = { records: [] };
  const keep = m => {
    if (m.type === 'attributes') {
      if (m.attributeName === 'class' || m.attributeName === 'style') return;
    }
    return true;
  };
  const obs = new MutationObserver(records => {
    for (const r of records) { if (keep(r)) window.__dom_obs.records.push(r); }
  });
  obs.observe(root, { childList: true, subtree: true,
                      attributes: true, characterData: true });
  window.__dom_obs.stop = () => obs.disconnect();
  return true;
}"""

# Drained after fill. Returns a compact summary (never raw records).
_SUMMARY_JS = r"""() => {
  if (!window.__dom_obs || !window.__dom_obs.records) return null;
  const records = window.__dom_obs.records;
  window.__dom_obs.stop && window.__dom_obs.stop();
  window.__dom_obs = null;
  const added = [], removed = [], attrs = [], texts = [];
  const seen = new Set();
  const describe = (node, limit) => {
    const out = [];
    if (!node) return out;
    if (node.nodeType === 1) {
      out.push(node.tagName.toLowerCase());
      const role = node.getAttribute && node.getAttribute('role');
      if (role) out.push('role=' + role);
      const placeholder = node.getAttribute && node.getAttribute('placeholder');
      if (placeholder) out.push('ph=' + placeholder);
    } else if (node.nodeType === 3) {
      const t = (node.textContent || '').trim();
      if (t) out.push('text:' + t.slice(0, limit));
    }
    return out;
  };
  for (const r of records) {
    if (r.type === 'childList') {
      for (const n of r.addedNodes) {
        const d = describe(n, 24).join(' ');
        if (d && !seen.has('a:' + d)) { seen.add('a:' + d); added.push(d); }
      }
      for (const n of r.removedNodes) {
        const d = describe(n, 24).join(' ');
        if (d && !seen.has('r:' + d)) { seen.add('r:' + d); removed.push(d); }
      }
    } else if (r.type === 'attributes') {
      if (r.attributeName && !seen.has('at:' + r.attributeName)) {
        seen.add('at:' + r.attributeName);
        attrs.push(r.attributeName);
      }
    } else if (r.type === 'characterData') {
      const t = (r.target && r.target.textContent || '').trim().slice(0, 24);
      if (t && !seen.has('t:' + t)) { seen.add('t:' + t); texts.push(t); }
    }
  }
  return {
    added: added.slice(0, 12),
    removed: removed.slice(0, 12),
    attrs: attrs.slice(0, 12),
    texts: texts.slice(0, 3),
  };
}"""


def _is_dynamic(f):
    """A field worth observing: combobox widgets, shadow-root fields, or
    listbox/aria-haspopup roles — the classes where value read-back is weak."""
    if f.get("role") == "combobox" or f.get("tag") == "DROPDOWN":
        return True
    if f.get("role") in ("listbox", "combobox"):
        return True
    try:
        if (f.get("aria") or {}).get("haspopup"):
            return True
    except (AttributeError, TypeError):
        pass
    # native selects can lazy-render (Antigua class) — observe them too
    if f.get("tag") == "SELECT":
        return True
    return False


def start_observation(page, sel):
    """Register the scoped observer for a field. Best-effort; returns True if
    the observer was installed (the page ran the JS), False otherwise."""
    try:
        return bool(page.evaluate(_START_JS, [sel]))
    except Exception:
        return False


def drain_summary(page):
    """Drain and stop the observer, returning the minimal DOM-delta summary
    (or None if no observer was active)."""
    try:
        return page.evaluate(_SUMMARY_JS)
    except Exception:
        return None


def summarize(records, max_added=12, max_removed=12, max_attrs=12, max_texts=3):
    """Pure Python summarizer — unit-testable without a browser. Consumes a
    list of MutationRecord-shaped dicts (or objects with .type/.addedNodes/
    .removedNodes/.attributeName/.target) and returns the same minimal shape as
    _SUMMARY_JS, so the dossier format is identical whether the page ran JS or
    a test fed records."""
    added, removed, attrs, texts = [], [], [], []
    seen = set()

    def describe(node):
        out = []
        node_type = getattr(node, "nodeType", None)
        if node_type == 1 or (node_type is None and isinstance(node, dict)):
            tag = node.get("tag") if isinstance(node, dict) else getattr(node, "tagName", "")
            if tag:
                out.append(str(tag).lower())
            role = (node.get("role") if isinstance(node, dict)
                    else getattr(node, "role", None))
            if role:
                out.append("role=" + role)
            ph = (node.get("placeholder") if isinstance(node, dict)
                  else getattr(node, "placeholder", None))
            if ph:
                out.append("ph=" + ph)
        elif node_type == 3 or (node_type is None and isinstance(node, str)):
            t = str(node).strip()
            if t:
                out.append("text:" + t[:24])
        return " ".join(out)

    for r in records:
        rtype = getattr(r, "type", None) or (r.get("type") if isinstance(r, dict) else None)
        if rtype == "childList":
            for n in getattr(r, "addedNodes", []) or (r.get("addedNodes") or []):
                d = describe(n)
                if d and d not in seen:
                    seen.add(d); added.append(d)
            for n in getattr(r, "removedNodes", []) or (r.get("removedNodes") or []):
                d = describe(n)
                if d and d not in seen:
                    seen.add(d); removed.append(d)
        elif rtype == "attributes":
            an = getattr(r, "attributeName", None) or (r.get("attributeName") if isinstance(r, dict) else None)
            if an and an not in ("class", "style") and an not in seen:
                seen.add(an); attrs.append(an)
        elif rtype == "characterData":
            tgt = getattr(r, "target", None) or (r.get("target") if isinstance(r, dict) else None)
            t = ""
            if tgt is not None:
                t = (getattr(tgt, "textContent", "") or (tgt.get("textContent", "") if isinstance(tgt, dict) else "") or "").strip()
            if t and t not in seen:
                seen.add(t); texts.append(t)

    return {
        "added": added[:max_added],
        "removed": removed[:max_removed],
        "attrs": attrs[:max_attrs],
        "texts": texts[:max_texts],
    }
