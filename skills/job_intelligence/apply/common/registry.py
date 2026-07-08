"""registry.py — Platform registry resolver. Loads YAML configs by domain match.

Supports two dispatch modes:
  - Legacy: flow_hook name → call function from registry/<name>.py
  - Handler: handler_class path → import and instantiate PlatformHandler subclass
"""

import importlib
import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse

REGISTRY_DIR = Path(os.path.dirname(__file__)).parent / "registry"

_noted_platforms = set()


class RegistryConfig:
    """Loaded platform configuration from a registry YAML."""

    def __init__(self, data, hook_module=None):
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
        self.flow_hook = data.get("flow_hook", "")
        self.handler_class = data.get("handler_class", "")
        self.notes = data.get("notes", "")
        self._hook_module = hook_module
        self._handler_instance = None

    def emit_notes(self):
        if self.notes and self.name not in _noted_platforms:
            _noted_platforms.add(self.name)
            for line in self.notes.strip().splitlines():
                print(f"QUIRKS: {line.strip()}")

    def has_hook(self, name):
        if not self._hook_module:
            return False
        return hasattr(self._hook_module, name)

    def call_hook(self, name, *args, **kwargs):
        if not self._hook_module:
            return None
        fn = getattr(self._hook_module, name, None)
        if fn:
            return fn(*args, **kwargs)
        return None

    def get_handler(self):
        """Lazy-import and return the PlatformHandler instance, or None."""
        if self._handler_instance is not None:
            return self._handler_instance
        if not self.handler_class:
            return None
        try:
            parts = self.handler_class.split(".")
            mod_path = ".".join(parts[:-1])
            cls_name = parts[-1]
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            self._handler_instance = cls()
            return self._handler_instance
        except Exception as e:
            print(f"ERROR: could not load handler {self.handler_class}: {e}", file=__import__('sys').stderr)
            return None


def _load_yaml(path):
    """Load a YAML file, returning dict. Uses pyyaml or falls back to json."""
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_hook_module(name):
    """Load a Python hook module from registry/ directory."""
    mod_path = REGISTRY_DIR / f"{name}.py"
    if not mod_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


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
                    mod = _load_hook_module(path.stem)
                    configs.append(RegistryConfig(data, hook_module=mod))
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
            if domain.lower() in host:
                return config
    return None


def available():
    """Return list of available platform names."""
    return [c.name for c in _cached_configs()]
