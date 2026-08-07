"""registry.py — Platform registry resolver. Loads YAML configs by domain match.

Pure data layer: platform knowledge lives in apply/registry/<name>.yaml and is
consumed declaratively by the generic engine (probe order, widget selectors,
URL rewrites, pagination bounds, text patterns). No Python handler dispatch —
the engine has no per-platform code branches.

Detection is layered, so a platform is recognized even when the URL hostname
is a customer-branded domain (the Jobright-detection gap, COMPARISON §S1):

  1. domain suffix match (the original, e.g. `boards.greenhouse.io`)
  2. query-param match (`gh_jid`/`gh_src` → greenhouse, `ashby_jid` → ashby,
     `LeverAppId` → lever) — an ATS's apply URL is often on a foreign host
  3. page-source keyword match (`resolve_from_page`) — careers sites load
     the ATS's JS bundles (`APPLY_form_renderer.js`, `teamtailor-cdn.com`,
     `recruiterflow.com`), which identifies the engine regardless of host
  4. iframe-only flag — the form lives inside an ATS iframe on a customer
     host (iCIMS, SmartRecruiters embeds); the probe must look in frames.
"""

import os, sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REGISTRY_DIR = Path(os.path.dirname(__file__)).parent / "registry"
_noted_platforms = set()


class RegistryConfig:
    """Loaded platform configuration from a registry YAML."""

    def __init__(self, data):
        self.name = data.get("name", "unknown")
        self.version = data.get("version", 1)
        detect = data.get("detect", {}) or {}
        self.domains = detect.get("domains", [])
        self.query_params = detect.get("query_params", [])
        self.source_keywords = detect.get("source_keywords", [])
        self.iframe_only = bool(detect.get("iframe_only", False))
        self.best_strategy = data.get("probe", {}).get("best_strategy", "standard")
        self.widgets = data.get("probe", {}).get("widgets", {})
        self.widget_parent = data.get("probe", {}).get("widget_parent",
            '[data-automation-id], [role="dialog"], dialog, form, fieldset')
        self.patterns = data.get("patterns", {})
        self.multi_page = data.get("properties", {}).get("multi_page", False)
        self.has_eeo = data.get("properties", {}).get("has_eeo", False)
        self.has_progress_bar = data.get("properties", {}).get("has_progress_bar", False)
        self.page_range = tuple(data.get("properties", {}).get("page_range", [1, 10]))
        self.url_rewrites = data.get("url_rewrites", []) or []
        self.url_normalize = data.get("url_normalize", []) or []
        self.fill_hints = data.get("fill", {}).get("hints", {}) or {}
        self.notes = data.get("notes", "")

    def emit_notes(self):
        """Print platform-specific quirks to stderr (once per platform per session).
        Helps the orchestrator understand platform behavior."""
        if self.notes and self.name not in _noted_platforms:
            _noted_platforms.add(self.name)
            for line in self.notes.strip().splitlines():
                if line.strip():
                    print(f"QUIRKS: {line.strip()}", file=sys.stderr)

    def rewrite_urls(self, url):
        """Apply this platform's url_rewrites rules to `url`.
        Returns list of alternate URLs (empty when no rule matches)."""
        import re
        out = []
        for rw in self.url_rewrites:
            try:
                alt = re.sub(rw.get("pattern", ""), rw.get("replace", ""), url or "")
                if alt and alt != url and alt not in out:
                    out.append(alt)
            except Exception:
                continue
        return out

    def normalize_url(self, url):
        """Apply this platform's url_normalize rules to `url`.

        Fixes query-param-dropping navigation (COMPARISON §S8): several ATS
        (GoHire, CatsOne, UltiPro) drop `?key=value` when the URL ends in a
        trailing slash. A normalize rule is a `{pattern, replace}` pair run in
        order; the returned URL keeps the query string intact."""
        import re
        out = url or ""
        for rw in self.url_normalize:
            try:
                alt = re.sub(rw.get("pattern", ""), rw.get("replace", ""), out)
                out = alt or out
            except Exception:
                continue
        return out

    def match_page_source(self, html):
        """True when a captured page's raw HTML carries this platform's
        source keyword (JS bundle path, CDN, or engine marker). Used by
        resolve_from_page for customer-branded career sites whose hostname
        is NOT the ATS's own domain."""
        if not html or not self.source_keywords:
            return False
        h = html.lower()
        return any(k.lower() in h for k in self.source_keywords)


def _load_yaml(path):
    """Load a YAML file, returning dict."""
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _cached_configs():
    """Lazy-load and cache all registry configs."""
    if not hasattr(_cached_configs, "_cache"):
        configs = []
        for path in sorted(REGISTRY_DIR.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                data = _load_yaml(path)
                if data and "name" in data:
                    configs.append(RegistryConfig(data))
            except Exception:
                pass
        _cached_configs._cache = configs
    return _cached_configs._cache


def _match_query_params(url, config):
    """True when the URL carries one of the config's identifying query params."""
    params = getattr(config, "query_params", []) or []
    if not params:
        return False
    try:
        q = parse_qs(urlparse(url).query)
    except Exception:
        return False
    return any(p in q for p in params)


def resolve(url):
    """Resolve a URL to a RegistryConfig. Returns None if no match.

    Layer 1: hostname domain-suffix match. Layer 2: query-param match
    (an ATS apply URL on a foreign host still names its engine via
    `gh_jid`, `ashby_jid`, `LeverAppId`, ...).
    """
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None

    for config in _cached_configs():
        for domain in config.domains:
            d = domain.lower()
            # Suffix match: "greenhouse.io" matches "boards.greenhouse.io"
            # but NOT "notgreenhouse.io" (substring would false-positive)
            if host == d or host.endswith("." + d):
                return config
    # Layer 2: no host match — an ATS query param may still identify it.
    for config in _cached_configs():
        if _match_query_params(url, config):
            return config
    return None


def resolve_from_page(url, page=None, html=None):
    """Resolve a RegistryConfig from a LIVE page, not just the URL.

    Layer 3 of detection (COMPARISON §S1): customer-branded career portals
    host the ATS's JS bundles but not its domain. When hostname + query
    params fail, scan the page's raw HTML for each platform's source
    keyword (APPLY_form_renderer.js, teamtailor-cdn.com, recruiterflow.com,
    eightfold, avature, ...). Prefers a hostname match when one exists.

    Accepts either a Playwright `page` (HTML is read once) or a raw `html`
    string (for offline/corpus tests). Returns a RegistryConfig or None.
    """
    if resolve(url):
        return resolve(url)
    if html is None and page is not None:
        try:
            html = page.evaluate("() => document.documentElement.outerHTML || ''")
        except Exception:
            html = ""
    if not html:
        return None
    for config in _cached_configs():
        if config.match_page_source(html):
            return config
    return None


def normalize_url(url):
    """Apply every configured platform's url_normalize rules to `url`.

    Keeps query params that a trailing slash would otherwise drop
    (COMPARISON §S8 — GoHire/CatsOne/UltiPro family). The URL is the
    identity of a job posting; normalization must be deterministic and
    idempotent so the same posting normalizes to the same canonical URL.
    """
    if not url:
        return url
    out = url
    for config in _cached_configs():
        n = config.normalize_url(out)
        out = n or out
    return out
