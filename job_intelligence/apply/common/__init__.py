"""Shared utilities for apply scripts.
Critical: always check common_answers for gaps before filling forms.
"""

def find_apply_page(ctx, fallback_url=None):
    """Find the page marked with window.__applyPage.
    If fallback_url is given, prefer pages matching that URL over LinkedIn.
    If not found and fallback_url is given, navigate a new page there and mark it.
    Returns (page, navigated_fresh) tuple.
    """
    import json, os, time
    
    # First pass: prefer external URL pages
    best = None
    for p in ctx.pages:
        try:
            if p.evaluate("() => window.__applyPage === true"):
                url = p.url
                if fallback_url and (url in fallback_url or fallback_url in url):
                    return p, False
                if best is None:
                    best = p
        except Exception:
            continue
    
    if best:
        return best, False
    
    if fallback_url:
        p = ctx.new_page()
        p.goto(fallback_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(5)
        p.evaluate("() => window.__applyPage = true")
        print(f"  Navigated fresh to {fallback_url[:80]}", file=__import__('sys').stderr)
        return p, True
    
    return None, False
