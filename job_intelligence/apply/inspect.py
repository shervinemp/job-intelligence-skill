"""inspect.py — Page diagnostic analysis. Read-only: fields, buttons, probe, screenshot, HTML.
Always captures a screenshot. Pass --html to also dump full DOM.
"""
import json, os, sys, time
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state, read_page
from apply.common.output import emit_next, emit_error, emit_warn
from apply.common.page_manager import PageManager
from apply.common.inspector import probe as probe_page, probe_all
from apply.common.registry import resolve as resolve_registry


def _domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _screenshot(page, jid, label):
    """Save page screenshot and print IMG: path."""
    try:
        from lib.config import JI_HOME
        import pathlib
        ss_dir = pathlib.Path(JI_HOME) / "screenshots"
        ss_dir.mkdir(parents=True, exist_ok=True)
        path = str(ss_dir / f"{label}_{jid}_{int(time.time())}.png")
        page.screenshot(path=path)
        print(f"IMG: {path}")
        return path
    except Exception as e:
        print(f"IMG_FAILED: {e}", file=sys.stderr)
        return None


def _dump_html(page, jid, label):
    """Save full page DOM HTML to file and print HTML: path."""
    try:
        from lib.config import JI_HOME
        import pathlib
        html_dir = pathlib.Path(JI_HOME) / "screenshots"
        html_dir.mkdir(parents=True, exist_ok=True)
        html = page.evaluate("() => document.documentElement.outerHTML")
        path = str(html_dir / f"{label}_{jid}_{int(time.time())}.html")
        pathlib.Path(path).write_text(html, encoding="utf-8")
        print(f"HTML: {path}")
        return path
    except Exception as e:
        print(f"HTML_FAILED: {e}", file=sys.stderr)
        return None


def run(jid, candidate=None, dump_html=False):
    state = load_state()
    if state.get("jid") != jid:
        emit_error(f"state is for job {state.get('jid','?')}, not {jid}")
        print("  Run detect first.", file=sys.stderr)
        return

    b, ctx = connect()
    pm = PageManager(ctx, jid)
    ext = state.get("external_url", "")
    page, score, candidates = pm.find(fallback_url=ext)

    print(f"Open pages ({len(ctx.pages)}):", file=sys.stderr)
    for i, p in enumerate(ctx.pages):
        url = p.url[:100]
        match = " [MATCH]" if p == page else ""
        print(f"  [{i}] {url}{match}", file=sys.stderr)

    if not page:
        if candidate is not None and candidate < len(ctx.pages):
            page = ctx.pages[candidate]
            print(f"Picked page [{candidate}]: {page.url[:100]}", file=sys.stderr)
        else:
            emit_warn(f"no page matches job {jid}")
            print(f"  Wanted: {ext[:100] if ext else '?'}", file=sys.stderr)
            if ctx.pages:
                print(f"  Use --candidate N to pick one.", file=sys.stderr)
                emit_next("model_choice")
            else:
                emit_next("none")
            return

    _screenshot(page, jid, "inspect")
    if dump_html:
        _dump_html(page, jid, "inspect")

    print(f"URL: {page.url}", file=sys.stderr)
    print(f"Title: {page.title() or '?'}", file=sys.stderr)
    print(f"Platform: {state.get('platform', '?')}", file=sys.stderr)
    print(f"Filled: {state.get('filled', 0)} fields", file=sys.stderr)

    ps = read_page(page)
    domain = _domain(page.url)
    registry = resolve_registry(page.url)
    best = probe_page(page, domain=domain, registry_config=registry)
    if best and best.field_count > 0:
        ps = best.to_dict()
        print(f"Probe: {best.strategy} ({best.field_count} fields)", file=sys.stderr)
    else:
        best, all_results = probe_all(page, domain=domain, registry_config=registry)
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
    print(f"Dialog: {'yes' if page.evaluate('() => !!document.querySelector(\"[role=dialog], dialog\")') else 'no'}", file=sys.stderr)

    if fc > 0:
        emit_next("act --fill")
    else:
        raw = (page.evaluate("() => document.body.innerText") or "")[:500]
        if raw.strip():
            print(f"Page text (first 500 chars):", file=sys.stderr)
            for line in raw.split("\n")[:10]:
                if line.strip():
                    print(f"  {line.strip()[:120]}", file=sys.stderr)
        else:
            print("Page text: empty — page may be blank or not loaded.", file=sys.stderr)
        emit_next("none")
