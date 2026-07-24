"""inspect_lib.py ΓÇö Reusable page inspection helpers.
Save screenshot + dump HTML (universal). Run probes + analyze fields (apply-specific).
All output to stdout for SLM consumption. Filenames overwrite on re-run.

Capture vs file handling separated:
  page_jpeg(page) / page_html(page) ΓÇö pure capture, no I/O
  save_persistent(data, jid, ext, prefix) ΓÇö saves to screenshots/ dir
  save_temp(data, suffix) ΓÇö saves to system temp dir, caller must clean up
  capture(page, jid, prefix) ΓÇö combines them for the standard persistent flow
"""
import os, sys
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


def form_jpeg(page):
    """Capture form-scoped screenshot as JPEG bytes. Smaller than full-page —
    avoids API downscaling and focuses vision on field values.
    Tries <form> element first, falls back to viewport to preserve context
    (success messages may appear outside or replace the form)."""
    try:
        form = page.query_selector("form")
        if form:
            box = form.bounding_box()
            if box and box["height"] > 0 and box["width"] > 0:
                return form.screenshot(type="jpeg", quality=80)
    except Exception:
        pass
    return page.screenshot(type="jpeg", quality=80, full_page=False)


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
