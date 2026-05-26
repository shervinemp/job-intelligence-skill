"""Shared utilities for apply scripts."""

def find_apply_page(ctx, fallback_url=None):
    """Find the page marked with window.__applyPage.
    If not found and fallback_url is given, navigate a new page there and mark it.
    Returns (page, navigated_fresh) tuple.
    """
    import json, os, time
    for p in ctx.pages:
        try:
            if p.evaluate("() => window.__applyPage === true"):
                return p, False
        except Exception:
            continue
    
    if fallback_url:
        from playwright.sync_api import sync_playwright
        p = ctx.new_page()
        p.goto(fallback_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(5)
        p.evaluate("() => window.__applyPage = true")
        print(f"  Navigated fresh to {fallback_url[:80]}", file=__import__('sys').stderr)
        return p, True
    
    return None, False
