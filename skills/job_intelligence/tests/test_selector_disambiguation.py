"""Tests for selector disambiguation (the Ashby placeholder and bhvr
pronoun-name classes — both caused silent wrong-field targeting)."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class SelectorDisambiguation(unittest.TestCase):
    def _page(self, evaluate_return):
        page = MagicMock()
        page.evaluate.return_value = evaluate_return
        return page

    def test_name_ambiguous_disambiguated_by_label(self):
        from apply.steps.probe import resolve_selector
        page = self._page('[name="pronouns"][data-resolve-pick="1"]')
        sel = resolve_selector(page, {"name": "pronouns",
                                      "label": "He/him"})
        self.assertEqual(sel, '[name="pronouns"][data-resolve-pick="1"]')
        page.evaluate.assert_called_once()

    def test_name_unique_no_disambiguation(self):
        from apply.steps.probe import resolve_selector
        page = self._page("")  # JS reports ≤1 candidate
        sel = resolve_selector(page, {"name": "first_name",
                                      "label": "First Name"})
        self.assertEqual(sel, '[name="first_name"]')

    def test_placeholder_ambiguous_disambiguated_by_label(self):
        from apply.steps.probe import resolve_selector
        page = self._page('[placeholder="Start typing..."][data-resolve-pick="1"]')
        sel = resolve_selector(page, {"placeholder": "Start typing...",
                                      "label": "Current City & State"})
        self.assertEqual(sel,
                         '[placeholder="Start typing..."][data-resolve-pick="1"]')

    def test_id_wins(self):
        from apply.steps.probe import resolve_selector
        page = self._page("unused")
        sel = resolve_selector(page, {"id": "country--country", "name": "x"})
        self.assertEqual(sel, '[id="country--country"]')
        page.evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
