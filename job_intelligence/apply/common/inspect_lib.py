"""inspect_lib.py — Reusable page inspection helpers.
Save screenshot + dump HTML (universal). Run probes + analyze fields (apply-specific).
All output to stdout for SLM consumption. Filenames overwrite on re-run.
"""
import json, os, sys
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


def capture(page, jid, prefix=""):
    """Universal: save screenshot (JPEG) + HTML dump. Outputs IMG: and HTML: paths.
    Optional prefix (e.g. 'fetch') separates files per pipeline stage. Overwrites on re-run.
    Safe to call from any pipeline stage (fetch, tailor, apply). Returns img path."""
    img = _path(jid, "jpg", prefix)
    try:
        page.screenshot(path=img, type="jpeg", quality=80)
        print(f"IMG: {img}")
    except Exception as e:
        print(f"IMG_FAILED: {e}", file=sys.stderr)

    html = _path(jid, "html", prefix)
    try:
        h = page.evaluate("() => document.documentElement.outerHTML")
        with open(html, "w", encoding="utf-8") as f:
            f.write(h)
        print(f"HTML: {html}")
    except Exception as e:
        print(f"HTML_FAILED: {e}", file=sys.stderr)

    return img


def probe_state(page):
    """Apply-specific: run probes, dump fields/buttons/type. Returns (fieldCount, fields, buttons, pageType)."""
    ps = read_page(page)
    page_domain = domain(page.url)
    registry = resolve_registry(page.url)
    best = probe_page(page, domain=page_domain, registry_config=registry)
    if best and best.field_count > 0:
        ps = best.to_dict()
        print(f"Probe: {best.strategy} ({best.field_count} fields)", file=sys.stderr)
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


def analyze(page, jid, prefix=""):
    """Convenience: capture + probe_state. For apply-pipeline --inspect."""
    capture(page, jid, prefix)
    return probe_state(page)
