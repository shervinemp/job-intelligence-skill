"""Import smoke test — every module under apply/ and lib/ must import cleanly.

This is the cheapest guard against the class of bug that silently killed the whole
pipeline (the `apply.py` vs `apply/` package collision, a missing __init__.py, a
broken `from x import y`). Pure import, no Chrome / DB / network.

Runs with stdlib only:  python -m unittest discover -s tests
(pytest also discovers this if installed.)
"""
import importlib
import os
import pathlib
import sys
import unittest
import warnings

_SKILL_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _SKILL_DIR)


def _module_names():
    names = []
    for base in ("apply", "lib"):
        root = pathlib.Path(_SKILL_DIR) / base
        for p in root.rglob("*.py"):
            if p.name == "__init__.py":
                continue
            rel = p.relative_to(_SKILL_DIR).with_suffix("")
            names.append(".".join(rel.parts))
    return sorted(names)


class ImportSmoke(unittest.TestCase):
    def test_all_modules_import(self):
        warnings.simplefilter("ignore")
        names = _module_names()
        self.assertGreater(len(names), 20, "expected to discover the apply/lib modules")
        for name in names:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
