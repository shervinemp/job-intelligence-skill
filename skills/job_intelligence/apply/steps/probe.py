"""Pass 1 probe: enrich fields with selectors and structural info."""


def resolve_selector(page, f):
    """Resolve a CSS selector for a field element."""
    if f.get("id"):
        # Hydration-race guard (#b): a placeholder id (`«rn»`, `«rq»`) is a
        # pre-hydration React/Next marker that gets replaced after hydration.
        # Using it as a selector resolves to nothing by fill time → no_filler
        # wedges the loop. Skip it and fall through to name/label recovery.
        from apply.common.hydration import is_placeholder_id
        if is_placeholder_id(f["id"]):
            f = dict(f)
            f.pop("id", None)
        else:
            return f'[id="{f["id"]}"]'
    if f.get("name"):
        sel = f'[name="{f["name"]}"]'
        # Ambiguity hazard (bhvr pronouns: N checkboxes share one name):
        # disambiguate by label text — the candidate inside the label
        # whose text contains the field label wins.
        if f.get("label"):
            try:
                n = (page.evaluate(
                    """(args) => {
                    const [name, lbl] = args;
                    const cands = [...document.querySelectorAll(
                        '[name="' + CSS.escape(name) + '"]')]
                        .filter(el => el.offsetParent !== null);
                    if (cands.length <= 1) return '';
                    const target = lbl.trim().toLowerCase();
                    for (let i = 0; i < cands.length; i++) {
                        const l = cands[i].closest('label');
                        if (l && l.textContent.trim().toLowerCase().includes(target)) {
                            cands[i].setAttribute('data-resolve-pick', '1');
                            const picked = '[name="' + CSS.escape(name)
                                + '"][data-resolve-pick="1"]';
                            cands.forEach((x, j) => {
                                if (j !== i) x.removeAttribute('data-resolve-pick');
                            });
                            return picked;
                        }
                    }
                    return '';
                    }""", [f["name"], f.get("label") or ""]) or "")
                if n:
                    return n
            except Exception:
                pass
        return sel
    if f.get("data_automation_id"):
        return f'[data-automation-id="{f["data_automation_id"]}"]'
    if f.get("placeholder"):
        # Ambiguity hazard (Ashby Location vs School both use
        # "Start typing..."): a placeholder selector that matches N>1
        # elements would target the WRONG field silently. Disambiguate
        # by label context — the candidate whose aria-label / label[for]
        # / question-scope contains the field label's words wins.
        return (page.evaluate(
            """(args) => {
            const [ph, lbl] = args;
            const phSel = '[placeholder="' + CSS.escape(ph) + '"]';
            const cands = [...document.querySelectorAll(phSel)]
                .filter(el => el.offsetParent !== null);
            if (cands.length <= 1 || !lbl) return phSel;
            const words = lbl.toLowerCase()
                .split(/[^a-z0-9]+/).filter(w => w.length > 2);
            if (!words.length) return phSel;
            const ctx = (el) => {
                let s = (el.getAttribute('aria-label') || '') + ' ';
                const id = el.id;
                if (id) {
                    const lf = document.querySelector('label[for="' + CSS.escape(id) + '"]');
                    if (lf) s += lf.textContent + ' ';
                }
                let scope = el;
                for (let i = 0; i < 4 && scope; i++) {
                    scope = scope.parentElement;
                    if (!scope) break;
                    const q = scope.querySelector('label, legend, h1, h2, h3, h4, p');
                    if (q) { s += q.textContent + ' '; break; }
                }
                return s.toLowerCase();
            };
            for (let i = 0; i < cands.length; i++) {
                const c = ctx(cands[i]);
                if (words.every(w => c.includes(w))) {
                    cands[i].setAttribute('data-resolve-pick', '1');
                    const sel = phSel + '[data-resolve-pick="1"]';
                    cands.forEach((x, j) => {
                        if (j !== i) x.removeAttribute('data-resolve-pick');
                    });
                    return sel;
                }
            }
            return phSel;
            }""", [f.get("placeholder"), f.get("label") or ""]) or "")
    if f.get("label"):
        try:
            return (page.evaluate(
                """(lbl) => {
                for (const l of document.querySelectorAll('label')) {
                    if (l.textContent.trim().toLowerCase() === lbl.toLowerCase()) {
                        const forId = l.getAttribute('for');
                        if (forId && document.getElementById(forId)) return '#' + CSS.escape(forId);
                        const inp = l.querySelector('input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable]');
                        if (inp && inp.id) return '#' + CSS.escape(inp.id);
                    }
                }
                for (const el of document.querySelectorAll('[aria-labelledby]')) {
                    const ref = document.getElementById(el.getAttribute('aria-labelledby'));
                    if (ref && ref.textContent.trim().toLowerCase() === lbl.toLowerCase() && el.id) return '#' + CSS.escape(el.id);
                }
                return '';
            }""", f["label"]) or "")
        except Exception:
            pass
    return ""
