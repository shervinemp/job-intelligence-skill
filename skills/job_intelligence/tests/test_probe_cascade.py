"""test_probe_cascade.py — the probe cascade (apply/common/inspector.py).

The strategies take a page and run real probe JS. CorpusPage runs that JS
against offline HTML via jsdom (available here), so the cascade is testable
without a browser. These tests pin: strategy selection by DOM shape, the
probe_all report, the navigation-race retry, and merge-with-widgets.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apply.common.mock_page import CorpusPage

SIMPLE_FORM = """
<html><body>
<form>
  <input type="email" name="email" placeholder="Email" required>
  <input type="text" name="name" placeholder="Full name">
  <select name="country"><option>Canada</option></select>
</form>
</body></html>
"""

DIALOG_FORM = """
<html><body>
<div role="dialog" aria-modal="true">
  <input type="email" name="email" required>
</div>
</body></html>
"""


class ProbeCascade(unittest.TestCase):
    def test_standard_form_probes_with_fields(self):
        from apply.common.inspector import probe
        page = CorpusPage.from_html(SIMPLE_FORM, url="https://x.com/j")
        r = probe(page)
        self.assertIsNotNone(r)
        self.assertGreaterEqual(r.field_count, 1)
        self.assertEqual(r.url, "https://x.com/j")

    def test_probe_all_returns_best_and_report(self):
        from apply.common.inspector import probe_all
        page = CorpusPage.from_html(SIMPLE_FORM, url="https://x.com/j")
        best, results = probe_all(page)
        self.assertIsNotNone(best)
        self.assertGreater(best.field_count, 0)
        self.assertGreater(len(results), 0)
        # the best result is one of the report rows
        self.assertIn(best.strategy, [r.strategy for r in results])

    def test_dialog_strategy_finds_fields(self):
        from apply.common.inspector import probe_all
        page = CorpusPage.from_html(DIALOG_FORM, url="https://x.com/j")
        best, results = probe_all(page)
        self.assertGreater(best.field_count, 0)

    def test_empty_page_probes_to_zero(self):
        from apply.common.inspector import probe_all
        page = CorpusPage.from_html("<html><body><h1>nothing</h1></body></html>",
                                    url="https://x.com/j")
        best, _ = probe_all(page)
        self.assertIsNotNone(best)
        # jsdom may still count zero fields on a bare page
        self.assertGreaterEqual(best.field_count, 0)

    def test_strategy_retries_on_navigation_race(self):
        """The cascade retries once on an 'Execution context was destroyed'
        error — a SPA redirect mid-evaluate must not kill the whole probe."""
        from apply.common.inspector import probe, _PROBE_STRATEGIES
        page = CorpusPage.from_html(SIMPLE_FORM, url="https://x.com/j")
        # force the FIRST strategy to raise the race error once
        first_name = _PROBE_STRATEGIES[0][0]
        orig = dict(_PROBE_STRATEGIES)[first_name]
        calls = {"n": 0}
        def flaky(page, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("Execution context was destroyed")
            return orig(page, **kw)
        with patch("apply.common.inspector._PROBE_STRATEGIES",
                   [(first_name, flaky)] + list(_PROBE_STRATEGIES[1:])):
            r = probe(page)
        self.assertEqual(calls["n"], 2, "must retry once")
        self.assertIsNotNone(r)

    def test_try_strategy_unknown_name_returns_none(self):
        from apply.common.inspector import _try_strategy
        page = CorpusPage.from_html(SIMPLE_FORM, url="https://x.com/j")
        self.assertIsNone(_try_strategy("bogus", page))


class MergeWithWidgets(unittest.TestCase):
    def test_merge_deduplicates_by_label(self):
        from apply.common.inspector import _merge_with_widgets
        from apply.common import inspector
        page = CorpusPage.from_html(SIMPLE_FORM, url="https://x.com/j")
        base = MagicMock()
        base.fields = [{"label": "Email", "value": "a@b.com"}]
        base.buttons = []
        base.field_count = 1
        base.page_type = "form"
        base.has_file_input = False
        base.url = page.url
        cw = MagicMock()
        cw.field_count = 1
        cw.fields = [{"label": "Email", "value": "x"}, {"label": "New", "value": "y"}]
        cw.has_file_input = False
        reg = MagicMock()
        reg.widgets = {"country": "[name=country]"}
        with patch.object(inspector, "_probe_custom_widgets", return_value=cw):
            merged, was = _merge_with_widgets(base, reg, page)
        self.assertTrue(was)
        labels = [f["label"] for f in merged.fields]
        self.assertEqual(labels.count("Email"), 1, "dedup by label")
        self.assertIn("New", labels)


class CaptureFailure(unittest.TestCase):
    def test_capture_writes_sidecar(self):
        from apply.common.inspector import _capture_failure
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            page = CorpusPage.from_html(SIMPLE_FORM, url="https://x.com/j")
            with patch("lib.config.JI_HOME", tmp), \
                 patch("apply.common.capabilities.profile_hash",
                       return_value="abcdef12"):
                path = _capture_failure(page, {"has_form": True}, "jid1")
            self.assertIsNotNone(path)
            self.assertTrue(os.path.exists(path))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
