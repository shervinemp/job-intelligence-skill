"""inspector.py ΓÇö Page analysis engine with 7-depth probe cascade.

Probe strategies:
    0: Standard DOM (querySelectorAll)
    1: Dialog-scoped
    2: Iframe piercing (same-origin)
    3: Shadow DOM (Playwright locator)
    4: Lazy-load trigger (click + MutationObserver)
    5: Custom widget scan (registry hints)
    6: Raw HTML scan (probe --deep only)
"""

import json
import sys
from datetime import datetime

from apply.common.field_reader import read_fields
from lib.config import SNAPSHOTS_DIR

SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class ProbeResult:
    """Result from a single or cascaded probe."""

    def __init__(self, fields=None, buttons=None, strategy="none", field_count=0,
                 page_type="unknown", has_file_input=False, has_required_file=False,
                 url="", iframe_srcs=None, error=None):
        self.fields = fields or []
        self.buttons = buttons or []
        self.strategy = strategy
        self.field_count = field_count or len(self.fields)
        self.page_type = page_type
        self.has_file_input = has_file_input
        self.has_required_file = has_required_file
        self.url = url
        self.iframe_srcs = iframe_srcs or []
        self.error = error

    def success(self):
        return self.field_count > 0 or self.error is None

    def to_dict(self):
        return {
            "fieldCount": self.field_count,
            "fields": self.fields,
            "buttons": self.buttons,
            "strategy": self.strategy,
            "pageType": self.page_type,
            "hasFileInput": self.has_file_input,
            "hasRequiredFile": self.has_required_file,
            "url": self.url,
            "iframe_srcs": self.iframe_srcs,
        }


def _snapshot_dom(page, jid=None):
    """Save full DOM HTML and metadata for debugging. Keeps last 20 snapshots."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    jid_part = f"_{jid}" if jid else ""
    prefix = f"{ts}{jid_part}"

    try:
        html = page.evaluate("() => document.documentElement.outerHTML")
        html_path = SNAPSHOTS_DIR / f"{prefix}_dom.html"
        html_path.write_text(html, encoding="utf-8")
    except Exception:
        html_path = None

    try:
        info_path = SNAPSHOTS_DIR / f"{prefix}_probe.json"
        info_path.write_text(json.dumps({
            "url": page.url,
            "title": page.title(),
            "timestamp": ts,
        }, indent=2))
    except Exception:
        info_path = None

    # Retention: keep last 20 snapshots, delete oldest
    try:
        snapshots = sorted(SNAPSHOTS_DIR.glob("*_dom.html"))
        while len(snapshots) > 20:
            oldest = snapshots.pop(0)
            oldest.unlink(missing_ok=True)
            # Also remove corresponding .json
            json_oldest = oldest.with_suffix(".json").with_stem(
                oldest.stem.replace("_dom", "_probe")
            )
            json_oldest.unlink(missing_ok=True)
    except Exception:
        pass

    return str(html_path) if html_path else None


def _probe_standard(page):
    """Depth 0: Standard DOM querySelectorAll over entire document."""
    result = read_fields(page, scope="document")
    return ProbeResult(
        fields=result["fields"],
        buttons=result["buttons"],
        strategy="standard",
        field_count=result["fieldCount"],
        page_type=result["pageType"],
        has_file_input=result["hasFileInput"],
        has_required_file=result["hasRequiredFile"],
        url=result["url"],
    )


def _probe_dialog(page):
    """Depth 1: Dialog-scoped ΓÇö looks within [role="dialog"]."""
    result = read_fields(page, scope="dialog")
    return ProbeResult(
        fields=result["fields"],
        buttons=result["buttons"],
        strategy="dialog",
        field_count=result["fieldCount"],
        page_type=result.get("pageType", "form"),
        has_file_input=result["hasFileInput"],
        has_required_file=result["hasRequiredFile"],
        url=result["url"],
    )


def _probe_iframes(page):
    """Depth 2: Probe same-origin iframes recursively."""
    all_fields = []
    all_buttons = []
    iframe_srcs = []

    try:
        frames = page.frames
        for frame in frames:
            if frame == page.main_frame:
                continue
            try:
                result = frame.evaluate("""() => {
                    const inputs = document.querySelectorAll(
                        'input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable="true"]'
                    );
                    const btns = document.querySelectorAll('button');
                    const fields = Array.from(inputs).map(el => {
                        let label = '';
                        if (el.id) { const lbl = document.querySelector('label[for="' + el.id + '"]'); if (lbl) label = lbl.textContent.trim(); }
                        if (!label) { const pl = el.closest('label'); if (pl) label = pl.textContent.trim(); }
                        if (!label && el.placeholder) label = el.placeholder;
                        if (!label) { const p = el.closest('div,fieldset,section,li,form'); const plbl = p ? p.querySelector('label, legend, strong, span') : null; if (plbl) label = plbl.textContent.trim(); }
                        return {
                            tag: el.tagName, type: el.getAttribute('type') || '',
                            id: el.id, name: el.getAttribute('name') || '',
                            label: (label || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                            placeholder: el.placeholder || '',
                            data_automation_id: el.getAttribute('data-automation-id') || '',
                            role: el.getAttribute('role') || '',
                            required: !!el.required || el.getAttribute('aria-required') === 'true',
                            value: el.value || '',
                            options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0, 15) : [],
                        };
                    });
                    return { fields: fields, buttons: Array.from(btns).filter(b => b.offsetParent).map(b => ({
                        text: (b.textContent || '').trim().slice(0, 30),
                        disabled: b.disabled || false,
                        type: b.getAttribute('type') || 'button',
                    })) };
                }""")
                if result and result.get("fields", []):
                    all_fields.extend(result["fields"])
                    all_buttons.extend(result.get("buttons", []))
                    iframe_srcs.append(frame.url)
            except Exception:
                pass  # cross-origin iframe ΓÇö can't access
    except Exception as e:
        print(f"PROBE_ERROR: iframe probe failed ΓÇö {e}", file=sys.stderr)

    if not all_fields:
        try:
            srcs = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('iframe'))
                    .map(f => f.src || f.getAttribute('data-src') || '')
                    .filter(Boolean);
            }""")
            iframe_srcs.extend(srcs)
        except Exception:
            pass

    return ProbeResult(
        fields=all_fields,
        buttons=all_buttons,
        strategy="iframe",
        field_count=len(all_fields),
        page_type="form" if all_fields else "unknown",
        url=page.url,
        iframe_srcs=iframe_srcs,
    )


probe_iframes = _probe_iframes  # public alias for page_helpers.read_page


def _probe_iframe_navigate(page, prev_result=None):
    """Depth 2.5: Navigate to cross-origin iframe src URLs and re-probe.
    Handles ATS that load forms in cross-origin iframes (UKG/UltiPro, etc.).
    Navigates back to original URL if no fields found in the iframe."""
    iframe_srcs = prev_result.iframe_srcs if prev_result else []
    if not iframe_srcs:
        try:
            iframe_srcs = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('iframe'))
                    .map(f => f.src || f.getAttribute('data-src') || '')
                    .filter(Boolean);
            }""")
        except Exception:
            pass
    original_url = page.url
    for src in iframe_srcs[:3]:
        if not src or 'http' not in src:
            continue
        try:
            page.goto(src, wait_until="domcontentloaded", timeout=15000)
            result = _probe_standard(page)
            if result.field_count > 0:
                return result
            page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            try:
                page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
    return ProbeResult(strategy="iframe_navigate", field_count=0, page_type="unknown", url=page.url)


probe_iframe_navigate = _probe_iframe_navigate  # public alias for act.py


def _probe_shadow_dom(page):
    """Depth 3: Find and read fields inside shadow roots via JS."""
    fields = []
    try:
        fields = page.evaluate("""() => {
            const result = [];
            function resolveLabel(el, root) {
                let label = '';
                if (el.getAttribute('aria-labelledby')) {
                    const ref = root.getElementById(el.getAttribute('aria-labelledby'));
                    if (ref) label = ref.textContent.trim();
                }
                if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
                if (!label) {
                    const lbl = root.querySelector('label[for="' + el.id + '"]');
                    if (lbl) label = lbl.textContent.trim();
                }
                if (!label) {
                    const parentLabel = el.closest('label');
                    if (parentLabel) label = parentLabel.textContent.trim();
                }
                if (!label && el.placeholder) label = el.placeholder;
                if (!label) {
                    const parent = el.closest('div,fieldset,section,li,form');
                    const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
                    if (plbl) label = plbl.textContent.trim();
                }
                return (label || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
            }
            function walk(root) {
                const hosts = root.querySelectorAll('*');
                for (const el of hosts) {
                    if (!el.shadowRoot) continue;
                    const inputs = el.shadowRoot.querySelectorAll(
                        'input:not([type=hidden]):not([type=submit]), select, textarea'
                    );
                    inputs.forEach(inp => {
                        const opts = inp.tagName === 'SELECT'
                            ? Array.from(inp.options).map(o => o.text.trim()).filter(Boolean).slice(0, 15)
                            : [];
                        result.push({
                            tag: inp.tagName, type: inp.getAttribute('type') || '',
                            id: inp.id, name: inp.getAttribute('name') || '',
                            label: resolveLabel(inp, el.shadowRoot),
                            placeholder: inp.placeholder || '',
                            data_automation_id: inp.getAttribute('data-automation-id') || '',
                            role: inp.getAttribute('role') || '',
                            required: !!inp.required || inp.getAttribute('aria-required') === 'true',
                            value: inp.value || '', checked: null, options: opts,
                        });
                    });
                    walk(el.shadowRoot);
                }
            }
            walk(document);
            return result;
        }""")
    except Exception:
        pass

    return ProbeResult(
        fields=fields,
        strategy="shadow_dom",
        field_count=len(fields),
        page_type="form" if fields else "unknown",
        url=page.url,
    )


def _probe_lazy_load(page):
    """Depth 4: Click visible 'Apply' buttons and watch for new fields via MutationObserver.

    Only fires when earlier depths found nothing and visible Apply buttons exist.
    """
    has_apply_button = page.evaluate("""() => {
        const btns = document.querySelectorAll('button, a');
        for (const b of btns) {
            if (b.offsetParent === null) continue;
            const t = (b.textContent || '').toLowerCase().trim();
            if (t === 'apply' || t === 'apply now' || t === 'easy apply') return true;
        }
        return false;
    }""")

    if not has_apply_button:
        return ProbeResult(strategy="lazy_load", field_count=0, page_type="unknown", url=page.url)

    # Click all Apply buttons and watch for new inputs
    new_fields = page.evaluate("""() => {
        const before = document.querySelectorAll(
            'input:not([type=hidden]):not([type=submit]), select, textarea'
        ).length;

        return new Promise((resolve) => {
            const observer = new MutationObserver(() => {
                const after = document.querySelectorAll(
                    'input:not([type=hidden]):not([type=submit]), select, textarea'
                ).length;
                if (after > before) {
                    observer.disconnect();
                    resolve(after - before);
                }
            });
            observer.observe(document.body, {
                childList: true, subtree: true,
                attributes: false,
            });

            // Click all visible Apply buttons
            document.querySelectorAll('button, a').forEach(el => {
                if (el.offsetParent === null) return;
                const t = (el.textContent || '').toLowerCase().trim();
                if (t === 'apply' || t === 'apply now' || t === 'easy apply') {
                    el.click();
                }
            });

            // Timeout after 5s
            setTimeout(() => {
                observer.disconnect();
                const after = document.querySelectorAll(
                    'input:not([type=hidden]):not([type=submit]), select, textarea'
                ).length;
                resolve(Math.max(0, after - before));
            }, 5000);
        });
    }""")

    if new_fields > 0:
        from apply.common.field_reader import read_fields as _rf
        result = _rf(page)
        return ProbeResult(
            fields=result["fields"], buttons=result["buttons"],
            strategy="lazy_load",
            field_count=result["fieldCount"],
            page_type="form" if result["fieldCount"] > 0 else "unknown",
            has_file_input=result["hasFileInput"],
            url=page.url,
        )
    return ProbeResult(strategy="lazy_load", field_count=0, page_type="unknown", url=page.url)


def _probe_custom_widgets(page, registry_config=None):
    """Depth 5: Use registry widget hints to find custom form controls."""
    if not registry_config:
        return ProbeResult(strategy="custom_widgets", field_count=0, page_type="unknown", url=page.url)

    custom_selectors = registry_config.widgets if hasattr(registry_config, 'widgets') else {}
    field_count = 0
    for widget_type, selector in custom_selectors.items():
        try:
            count = page.evaluate(f"""(sel) => {{
                return document.querySelectorAll(sel).length;
            }}""", selector)
            field_count += count
        except Exception:
            pass

    if field_count > 0:
        result = read_fields(page, custom_widgets=custom_selectors)
        return ProbeResult(
            fields=result["fields"],
            buttons=result["buttons"],
            strategy="custom_widgets",
            field_count=result["fieldCount"],
            page_type="form",
            has_file_input=result["hasFileInput"],
            url=result["url"],
        )

    return ProbeResult(strategy="custom_widgets", field_count=0, page_type="unknown", url=page.url)


def _probe_html_scan(page):
    """Depth 6: Raw HTML scan for form-like patterns. probe --deep only."""
    try:
        html = page.evaluate("() => document.documentElement.outerHTML || ''").lower()
        # Count input-like patterns in raw HTML
        input_count = html.count("<input")
        select_count = html.count("<select")
        textarea_count = html.count("<textarea")
        total = input_count + select_count + textarea_count
        return ProbeResult(
            strategy="html_scan",
            field_count=total if total > 5 else 0,
            page_type="maybe_form" if total > 5 else "unknown",
            url=page.url,
        )
    except Exception as e:
        return ProbeResult(strategy="html_scan", field_count=0, page_type="unknown", url=page.url, error=str(e))


# ΓöÇΓöÇ Main probe function ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def _probe_vision(page):
    """Depth 7: Vision LLM screenshot analysis ΓÇö fallback for non-standard
    form rendering (canvas, custom widgets, unusual frameworks).
    Only fires when ask_api.available() is True and earlier depths found nothing."""
    try:
        from lib import ask_api
        if not ask_api.available():
            return ProbeResult(strategy="vision", field_count=0, page_type="unknown", url=page.url)
    except Exception:
        return ProbeResult(strategy="vision", field_count=0, page_type="unknown", url=page.url)

    try:
        ss_path = SNAPSHOTS_DIR / f"vision_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        try:
            page.screenshot(path=str(ss_path), full_page=True)
        except Exception:
            try:
                page.screenshot(path=str(ss_path), full_page=False)
            except Exception:
                page.screenshot(path=str(ss_path))
        if not ss_path.exists():
            return ProbeResult(strategy="vision", field_count=0, page_type="unknown", url=page.url)
        prompt = (
            "Identify all visible form fields in this screenshot. "
            "For each field, output on ONE line: label | type (text/select/checkbox/radio/textarea/file) | options (comma-separated for select/radio, or empty) | required? (yes/no). "
            "For radio buttons, GROUP pairs: label=question_text | type=radio | options=Yes,No | required=yes/no. "
            "For checkboxes, use type=checkbox and options is the label text. "
            "If no form fields are visible, respond with exactly 'NONE'."
        )
        # Use chunked vision for long pages (splits into horizontal sections,
        # sends each separately, consolidates results).
        raw, err = ask_api.ask_chunked(open(ss_path, "rb").read(), prompt, max_tokens=2048)
        response = raw if isinstance(raw, str) else (raw[0] if raw and isinstance(raw, (list, tuple)) else "")
        if err or not response or response.strip().upper() == "NONE":
            return ProbeResult(strategy="vision", field_count=0, page_type="unknown", url=page.url)
        fields = _parse_vision_response(response, page)
        if fields:
            return ProbeResult(
                fields=fields,
                strategy="vision",
                field_count=len(fields),
                page_type="form",
                has_file_input=any("file" in (f.get("type","") or "").lower() for f in fields),
                url=page.url,
            )
    except Exception:
        pass
    return ProbeResult(strategy="vision", field_count=0, page_type="unknown", url=page.url)


def _parse_vision_response(response: str, page) -> list[dict]:
    """Parse vision LLM's structured field list into field dicts.
    Expected format per line: label | type | options | required"""
    fields = []
    import re
    for line in response.strip().splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        label = parts[0] if len(parts) > 0 else ""
        ftype = parts[1].lower() if len(parts) > 1 else "text"
        options_str = parts[2] if len(parts) > 2 else ""
        required = (parts[3] if len(parts) > 3 else "no").lower()
        if not label:
            continue
        opts = [o.strip() for o in options_str.split(",") if o.strip()] if options_str and options_str != "-" else []
        field = _match_label_to_element(page, label, ftype, required == "yes")
        if field:
            if opts:
                field["options"] = opts
            fields.append(field)
        elif ftype in ("radio", "checkbox"):
            # Create a synthetic field for radio/checkbox that the vision saw
            # but couldn't match to a DOM element
            fields.append({
                "tag": "INPUT", "type": ftype, "id": "", "name": "",
                "label": label, "required": required == "yes",
                "selector": "", "value": "", "placeholder": "",
                "options": opts, "vision_match": label, "vision_only": True,
            })
    return fields


def _match_label_to_element(page, label: str, ftype: str, required: bool) -> dict:
    """Best-effort match a vision-identified label to a DOM element."""
    import json, re as _re
    _norm = _re.sub(r"[^a-z0-9]", "", label.lower())
    _norm_json = json.dumps(_norm)
    candidates = page.evaluate(f"""() => {{
        const norm = {_norm_json};
        const sel = 'input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable="true"]';
        const results = [];
        document.querySelectorAll(sel).forEach(el => {{
            const txt = (el.getAttribute('aria-label') || el.placeholder || el.id || el.name || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const parent = el.closest('div,fieldset,section,li,form,label');
            const parentText = parent ? parent.textContent.toLowerCase().replace(/[^a-z0-9]/g, '') : '';
            const score = (txt.includes(norm) || norm.includes(txt) || parentText.includes(norm)) ? 1 : 0;
            if (score > 0) results.push({{
                tag: el.tagName, id: el.id, name: el.getAttribute('name') || '',
                label: el.getAttribute('aria-label') || el.placeholder || el.id || el.name,
                type: el.type || el.tagName,
                required: !!el.required,
                selector: '#' + CSS.escape(el.id),
            }});
        }});
        return results;
    }}""")
    if candidates:
        c = candidates[0]
        return {
            "tag": c["tag"], "type": c["type"], "id": c.get("id", ""),
            "name": c.get("name", ""), "label": c.get("label", label),
            "required": c.get("required", required),
            "selector": c.get("selector", ""), "value": "",
            "placeholder": "", "options": [], "vision_match": label,
        }
    return {
        "tag": "INPUT", "type": ftype, "id": "", "name": "",
        "label": label, "required": required,
        "selector": "", "value": "", "placeholder": "",
        "options": [], "vision_match": label, "vision_only": True,
    }


_PROBE_STRATEGIES = [
    ("standard", _probe_standard),
    ("dialog", _probe_dialog),
    ("iframe", _probe_iframes),
    ("iframe_navigate", _probe_iframe_navigate),
    ("shadow_dom", _probe_shadow_dom),
    ("lazy_load", _probe_lazy_load),
    ("custom_widgets", _probe_custom_widgets),
    ("vision", _probe_vision),
    ("html_scan", _probe_html_scan),
]

# Strategies that should never be auto-tried (only via --deep)
_DEEP_ONLY = {"html_scan"}

def _merge_with_widgets(result, registry_config, page):
    """If registry has widget selectors, probe for custom dropdowns and merge.
    Deduplicates by field label. Returns (merged_result, was_merged)."""
    if not registry_config or not registry_config.widgets:
        return result, False
    cw_result = _probe_custom_widgets(page, registry_config=registry_config)
    if cw_result.field_count == 0:
        return result, False
    existing_labels = {f.get("label", "") for f in result.fields}
    merged = list(result.fields)
    for f in cw_result.fields:
        if f.get("label", "") not in existing_labels:
            merged.append(f)
            existing_labels.add(f.get("label", ""))
    return ProbeResult(
        fields=merged, buttons=result.buttons,
        strategy=f"{result.strategy}+custom_widgets",
        field_count=len(merged),
        page_type=result.page_type,
        has_file_input=result.has_file_input or cw_result.has_file_input,
        url=result.url,
    ), True


def probe(page, domain=None, registry_config=None, deep=False, snapshot_on_fail=True, jid=None):
    """Run the probe cascade. Returns the first successful ProbeResult.

    Args:
        page: Playwright page object
        domain: Domain (unused, kept for compatibility)
        registry_config: Platform registry config (RegistryConfig or None)
        deep: If True, run all strategies including --deep only
        snapshot_on_fail: If True and all strategies fail, save DOM snapshot
        jid: Job ID for snapshot naming
    """
    # Try best_strategy from YAML config before full cascade
    best_strategy = getattr(registry_config, 'best_strategy', None) if registry_config else None
    best_strategy_failed = False
    if best_strategy:
        strategy_fn = dict(_PROBE_STRATEGIES).get(best_strategy)
        if strategy_fn:
            kw = {}
            if best_strategy == "custom_widgets":
                kw["registry_config"] = registry_config
            result = strategy_fn(page, **kw)
            if result.field_count > 0:
                if best_strategy != "custom_widgets":
                    result, _ = _merge_with_widgets(result, registry_config, page)
                return result
            best_strategy_failed = True

    # Full cascade: track previous result for iframe_navigate
    prev_result = None
    for name, strategy_fn in _PROBE_STRATEGIES:
        if not deep and name in _DEEP_ONLY:
            continue

        if name == "iframe_navigate" and prev_result:
            result = strategy_fn(page, prev_result=prev_result)
        elif name == "custom_widgets" and registry_config:
            result = strategy_fn(page, registry_config=registry_config)
        else:
            result = strategy_fn(page)
        prev_result = result
        if result.field_count > 0:
            if best_strategy_failed:
                print(f"CONFIG_STALE: {best_strategy} returned 0 fields, cascade found {name} with {result.field_count} fields", file=sys.stderr)
            if name != "custom_widgets":
                result, _ = _merge_with_widgets(result, registry_config, page)
            return result

    # All strategies failed
    snapshot_path = None
    if snapshot_on_fail:
        snapshot_path = _snapshot_dom(page, jid)

    result = ProbeResult(strategy="failed", field_count=0, page_type="unknown", url=page.url)
    result.snapshot_path = snapshot_path
    return result


def probe_all(page, domain=None, registry_config=None):
    """Run ALL probe strategies and return best result + full report.
    Unlike probe(), does not stop at first success ΓÇö runs every depth.
    Returns (best_ProbeResult, list_of_ProbeResults)."""
    prev_result = None
    results = []
    for name, strategy_fn in _PROBE_STRATEGIES:
        try:
            if name == "iframe_navigate" and prev_result:
                r = strategy_fn(page, prev_result=prev_result)
            elif name == "custom_widgets" and registry_config:
                r = strategy_fn(page, registry_config=registry_config)
            else:
                r = strategy_fn(page)
            prev_result = r
            results.append(r)
        except Exception:
            results.append(ProbeResult(strategy=name, field_count=0, url=page.url))
    best = max(results, key=lambda r: r.field_count) if results else ProbeResult(strategy="none", field_count=0, url=page.url)
    return best, results
