"""Plugin loader — discovers ATSPlugin subclasses."""
import importlib
import inspect
import pkgutil
import re
from pathlib import Path
from .ats_plugin import ATSPlugin


def discover_plugins() -> list[ATSPlugin]:
    """Scan plugins/ for ATSPlugin subclasses, instantiate, return sorted by priority."""
    plugins = []
    plugin_dir = Path(__file__).parent
    for imp, modname, _ in pkgutil.iter_modules([str(plugin_dir)]):
        if modname == "ats_plugin" or modname.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(modname, str(plugin_dir / f"{modname}.py"))
        if not spec or not spec.loader:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            continue
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, ATSPlugin) and cls is not ATSPlugin:
                plugins.append(cls())
    plugins.sort(key=lambda p: p.priority, reverse=True)
    return plugins


def match_plugins(url: str) -> list[ATSPlugin]:
    """Return plugins whose patterns match the given URL."""
    matched = []
    for p in discover_plugins():
        for pat in p.patterns:
            try:
                if re.search(pat, url, re.I):
                    matched.append(p)
                    break
            except Exception:
                continue
    return matched
