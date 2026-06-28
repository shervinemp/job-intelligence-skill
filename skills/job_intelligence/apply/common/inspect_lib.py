"""inspect_lib.py — Reusable page inspection helpers.
Save screenshot + dump HTML (universal). Run probes + analyze fields (apply-specific).
All output to stdout for SLM consumption. Filenames overwrite on re-run.

Capture vs file handling separated:
  page_jpeg(page) / page_html(page) — pure capture, no I/O
  save_persistent(data, jid, ext, prefix) — saves to screenshots/ dir
  save_temp(data, suffix) — saves to system temp dir, caller must clean up
  capture(page, jid, prefix) — combines them for the standard persistent flow
"""
import json, os, sys, tempfile
from urllib.parse import urlparse

from lib.config import JI_HOME
from apply.common.inspector import probe as probe_page, probe_all
from apply.common.registry import resolve as resolve_registry
from apply.common.page_helpers import read_page


def domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _path(jid, ext, prefix=""):
    prefix = prefix + "_" if prefix else ""
    path = os.path.join(JI_HOME, "screenshots", f"{prefix}inspect_{jid}.{ext}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def page_jpeg(page, full=True):
    """Capture page screenshot as JPEG bytes. No file I/O.
    full=True captures the entire scrollable page (for inspect/debug).
    full=False captures only the viewport (for vision checks — avoids API downscaling)."""
    return page.screenshot(type="jpeg", quality=80, full_page=full)


def page_html(page):
    """Capture page HTML including shadow DOM as string. No file I/O."""
    return page.evaluate("""() => {
        const VOID = new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
        function serialize(node) {
            if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            if (node.nodeType !== Node.ELEMENT_NODE) return '';
            const tag = node.tagName.toLowerCase();
            let a = '';
            for (const attr of node.attributes) {
                const v = attr.value.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
                a += ' ' + attr.name + '="' + v + '"';
            }
            let inner = '';
            if (node.shadowRoot) {
                inner += '<template shadowrootmode="' + node.shadowRoot.mode + '">';
                for (const c of node.shadowRoot.childNodes) inner += serialize(c);
                inner += '</template>';
            }
            for (const c of node.childNodes) {
                if (c.nodeType === Node.ELEMENT_NODE && c.tagName === 'SLOT') continue;
                inner += serialize(c);
            }
            if (VOID.has(tag)) return '<' + tag + a + '>';
            return '<' + tag + a + '>' + inner + '</' + tag + '>';
        }
        return '<!DOCTYPE html>\\n' + serialize(document.documentElement);
    }""")


def capture(page, jid, prefix=""):
    """Universal: save screenshot (JPEG) + HTML dump. Outputs IMG: and HTML: paths.
    Optional prefix (e.g. 'fetch') separates files per pipeline stage. Overwrites on re-run.
    Safe to call from any pipeline stage (fetch, tailor, apply). Returns img path."""
    img_path = _path(jid, "jpg", prefix)
    try:
        img_data = page_jpeg(page, full=True)
        with open(img_path, "wb") as f:
            f.write(img_data)
        print(f"IMG: {img_path}")
    except Exception as e:
        print(f"IMG_FAILED: {e}", file=sys.stderr)
    html_path = _path(jid, "html", prefix)
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page_html(page))
        print(f"HTML: {html_path}")
    except Exception as e:
        print(f"HTML_FAILED: {e}", file=sys.stderr)
    return img_path


def probe_state(page):
    """Apply-specific: run probes, dump fields/buttons/type. Returns (fieldCount, fields, buttons, pageType)."""
    ps = read_page(page)
    page_domain = domain(page.url)
    registry = resolve_registry(page.url)
    best = probe_page(page, domain=page_domain, registry_config=registry)
    if best and best.field_count > ps.get("fieldCount", 0):
        ps = best.to_dict()
        print(f"Probe: {best.strategy} ({best.field_count} fields, deeper than read_page)", file=sys.stderr)
    elif best and best.field_count > 0:
        print(f"Probe: {best.strategy} ({best.field_count} fields, read_page had more)", file=sys.stderr)
    else:
        best, all_results = probe_all(page, domain=page_domain, registry_config=registry)
        if best and best.field_count > 0:
            ps = best.to_dict()
            print(f"Probe results ({len(all_results)} strategies):", file=sys.stderr)
            for r in all_results:
                if r.field_count > 0 or r is best:
                    marker = " [BEST]" if r is best else ""
                    print(f"  {r.strategy}: {r.field_count} fields{marker}", file=sys.stderr)
        else:
            print("Probe: all strategies failed", file=sys.stderr)

    fc = ps.get("fieldCount", 0)
    print(f"Fields: {fc}", file=sys.stderr)
    for f in ps.get("fields", []):
        opts = f.get("options", [])
        extra = f" -> {opts[:5]}" if opts else ""
        print(f"  [{f.get('tag','?')}] {f.get('label','?')} req={f.get('required')}{extra}", file=sys.stderr)

    btns = ps.get("buttons", [])
    print(f"Buttons: {len(btns)}", file=sys.stderr)
    for b in btns:
        d = " [DISABLED]" if b.get("disabled") else ""
        print(f"  '{b.get('text','?')}'{d}", file=sys.stderr)

    print(f"Page type: {ps.get('pageType', '?')}", file=sys.stderr)
    dialog = page.evaluate('() => !!document.querySelector("[role=dialog], dialog")')
    print(f"Dialog: {'yes' if dialog else 'no'}", file=sys.stderr)

    if fc == 0:
        raw = (page.evaluate("() => document.body.innerText") or "")[:500]
        if raw.strip():
            print(f"Page text (first 500 chars):", file=sys.stderr)
            for line in raw.split("\n")[:10]:
                if line.strip():
                    print(f"  {line.strip()[:120]}", file=sys.stderr)
        else:
            print("Page text: empty — page may be blank or not loaded.", file=sys.stderr)

    return fc, ps.get("fields", []), btns, ps.get("pageType", "unknown")
