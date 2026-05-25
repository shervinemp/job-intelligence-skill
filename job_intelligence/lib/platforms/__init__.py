"""Platform registry for site-specific job description handling.
Each platform module in lib/platforms/ can export:
  - pre_fetch(page): page interactions before text extraction
  - extract_text(page): DOM-based text extraction (fallback: body.innerText)
  - clean(text): platform-specific post-processing

Shared helpers in _shared.py are applied universally to all results
(collapse_blank_lines, strip). Platform modules should NOT import _shared.

Upper modules should use:
  - fetch_description(url, page): full pipeline for fetch.py
  - clean(url, text): text + universal cleaning for tailor.py
"""

import importlib
from urllib.parse import urlparse

from ._shared import collapse_blank_lines

PLATFORMS = {
    "linkedin.com": "linkedin",
    "jobright.ai": "jobright",
}


def _load(name):
    return importlib.import_module(f"lib.platforms.{name}")


def _lookup(url):
    domain = urlparse(url).netloc
    for pattern, name in PLATFORMS.items():
        if pattern in domain:
            return _load(name)
    return None


def _universal(text):
    return collapse_blank_lines(text.strip())


def fetch_description(url, page):
    mod = _lookup(url)
    if mod:
        if hasattr(mod, "pre_fetch"):
            mod.pre_fetch(page)
        if hasattr(mod, "extract_text"):
            text = mod.extract_text(page) or ""
        else:
            text = page.evaluate("document.body.innerText") or ""
        if hasattr(mod, "clean"):
            text = mod.clean(text)
        return _universal(text)
    return _universal(page.evaluate("document.body.innerText") or "")


def clean(url, text):
    if not text:
        return text
    mod = _lookup(url)
    if mod and hasattr(mod, "clean"):
        text = mod.clean(text)
    return _universal(text)
