"""Platform: linkedin.com — pre-fetch and description cleaner."""

import re


def pre_fetch(page):
    """NO-OP for description extraction.

    LinkedIn job pages carry 10-20 `… more` (expandable-text-button) elements
    inside a scrollable container, and the OLD code clicked ALL of them. Each
    click expands text → LinkedIn re-renders the scrollable parent → the
    container scrolls and the DOM shifts under the live locator — a hang that
    looks like infinite scrolling. But the full description is ALREADY in the
    DOM: `textContent` reads it without expanding (verified: 28K chars incl.
    'About the job'). Expanding is pointless for extraction, so we do nothing.
    """
    return None


def extract_text(page):
    """Fast description extraction for LinkedIn's job page.

    body.innerText forces a full layout reflow on LinkedIn's ~3MB DOM and can
    stall (the observed 'scroll looping' / hang). textContent reads the raw
    text without reflow. The job description is in the DOM even when below the
    fold, so textContent captures it without scrolling."""
    try:
        return page.evaluate("() => document.body.textContent") or ""
    except Exception:
        return ""


def extract_company(page):
    """Company name from the page's `/company/` anchor links.

    LinkedIn does NOT emit JSON-LD hiringOrganization on the job page (verified
    live: 0 JSON-LD blocks), so _enrich_from_ld finds nothing. The company
    lives in plain anchor links (a[href*="/company/"]) — 'Google', 'EY', etc.
    Returns the first non-empty match, or ''.
    """
    try:
        return page.evaluate("""() => {
            const seen = {};
            for (const a of document.querySelectorAll('a[href*="/company/"]')) {
                const t = (a.textContent || '').trim();
                if (t) { seen[t] = true; return t; }
            }
            return '';
        }""") or ""
    except Exception:
        return ""


def clean(text):
    # The description is everything from "About the job" onward. textContent
    # is NOT newline-separated (LinkedIn puts everything on a few long lines),
    # so a line-split would miss the marker, and a MULTILINE regex with
    # nav keywords (e.g. "easy apply") matches keywords that appear AFTER the
    # description in the script/nav text — eating the whole description and
    # leaving the trailing junk. Correct approach: slice at the marker; do NOT
    # run a nav-strip regex on the sliced description.
    idx = (text or "").lower().find("about the job")
    if idx >= 0:
        text = text[idx:]
    # cut any trailing nav/script junk by stopping at known end markers
    for end in ("set alert for similar jobs", "show more jobs", "view profile"):
        e = text.lower().find(end)
        if e != -1:
            text = text[:e]
            break
    return text.strip()
