"""registry.py — Platform registry resolver. Loads YAML configs by domain match.

Pure data layer: platform knowledge lives in apply/registry/<name>.yaml and is
consumed declaratively by the generic engine (probe order, widget selectors,
URL rewrites, pagination bounds, text patterns). No Python handler dispatch —
the engine has no per-platform code branches.
"""

import os, sys
from pathlib import Path
from urllib.parse import urlparse

REGISTRY_DIR = Path(os.path.dirname(__file__)).parent / "registry"
_noted_platforms = set()


class RegistryConfig:
    """Loaded platform configuration from a registry YAML."""

    def __init__(self, data):
        self.name = data.get("name", "unknown")
        self.version = data.get("version", 1)
        self.domains = data.get("detect", {}).get("domains", [])
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


def resolve(url):
    """Resolve a URL to a RegistryConfig. Returns None if no match.

    Matches by checking if any configured domain is a substring of the URL host.
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
    return None
