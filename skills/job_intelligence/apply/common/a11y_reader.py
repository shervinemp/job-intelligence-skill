"""a11y_reader.py — accessibility-tree fallback field detection.

Closed shadow DOM is invisible to JS queries (document.querySelector
cannot pierce closed roots), so a form rendered inside them looks like
"no fields found" — which the pipeline then misclassifies (login wall,
expired). The browser's OWN accessibility tree (CDP AX tree) exposes
closed-root content: role + name are the field identity. Playwright's
page.accessibility.snapshot() is that tree.

Scope: DETECTION + synthesis. Fields come back with label/type/role so
resolve + check can act; interaction reuses resolve_selector-by-label
(the AX name normally matches the visible label). This is the OOD
escape hatch for the closed-root class — never the primary path.
"""
from typing import List, Dict, Any

_ROLE_TYPE = {
    "textbox": "text",
    "combobox": "combobox",
    "checkbox": "checkbox",
    "radio": "radio",
    "spinbutton": "number",
    "listbox": "select",
    "searchbox": "text",
    "menuitemcheckbox": "checkbox",
    "menuitemradio": "radio",
    "slider": "number",
}

_SKIP_ROLES = {"button", "link", "menuitem", "treeitem", "tab", "heading",
               "statictext", "text", "paragraph", "label", "none",
               "generic", "group", "list", "listitem"}


def _walk(node, out):
    role = (node.get("role") or "").lower()
    name = (node.get("name") or "").strip()
    if role in _ROLE_TYPE and name:
        out.append({
            "tag": "INPUT",
            "type": _ROLE_TYPE[role],
            "label": name[:80],
            "label_full": name,
            "role": role,
            "required": bool(node.get("required")),
            "a11y": True,
            "_a11y_id": node.get("id", ""),
        })
    for child in node.get("children") or []:
        _walk(child, out)


def read_fields_from_a11y(page) -> List[Dict[str, Any]]:
    """Fallback field detection via the AX tree. Returns [] when the
    snapshot is unavailable or yields nothing usable."""
    try:
        snap = page.accessibility.snapshot()
    except Exception:
        return []
    if not snap:
        return []
    out = []
    try:
        _walk(snap, out)
    except Exception:
        return []
    return out
