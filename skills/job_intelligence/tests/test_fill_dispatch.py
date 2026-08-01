"""Unit tests for apply/act/helpers.py — fill dispatch and field resolution."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FillWithPlaywright(unittest.TestCase):
    """_fill_with_playwright must not raise NameError on `filled_keys`
    and must actually dispatch fields to the deterministic filler."""

    def test_fills_field_and_returns_it(self):
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        page.evaluate.return_value = ""
        field = {
            "label": "First name", "tag": "INPUT", "type": "text",
            "_sel": "#first_name", "name": "first_name", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None, "_why": "",
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.strategies.dispatch.field_deterministic", return_value=True), \
             patch("apply.act.helpers.resolve", ) as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = "John"
            resolve_mock.return_value.provenance = "profile"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        self.assertEqual(len(filled), 1)
        self.assertEqual(len(failed), 0)

    def test_prefilled_field_skipped_not_failed(self):
        """A field whose current DOM value already matches the answer must be
        counted as filled (skip), NOT recorded as fill_failed."""
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        # querySelector(...).value returns the already-correct value
        page.evaluate.return_value = "John"
        field = {
            "label": "First name", "tag": "INPUT", "type": "text",
            "_sel": "#first_name", "name": "", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.strategies.dispatch.field_deterministic") as fd_mock, \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = "John"
            resolve_mock.return_value.provenance = "profile"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        fd_mock.assert_not_called()
        self.assertEqual(len(filled), 1)
        self.assertEqual(len(failed), 0)

    def test_filled_keys_dedupes_across_calls(self):
        """The caller populates filled_keys from returned filled records;
        a field already present must be skipped (not re-filled, not failed)."""
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        page.evaluate.return_value = ""
        field = {
            "label": "Email", "tag": "INPUT", "type": "email",
            "_sel": "#email", "name": "email", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        filled_keys = set()
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.strategies.dispatch.field_deterministic", return_value=True), \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = "a@b.com"
            resolve_mock.return_value.provenance = "profile"
            filled, _ = _fill_with_playwright(page, [field], {"location": ""}, None,
                                              filled_keys=filled_keys)
            self.assertEqual(len(filled), 1)
            # Caller-side bookkeeping (mirrors cmd_fill)
            for rec in filled:
                filled_keys.add(rec["key"])
            self.assertEqual(len(filled_keys), 1)
            # Second call with the same filled_keys: field already filled
            filled2, failed2 = _fill_with_playwright(page, [field], {"location": ""}, None,
                                                     filled_keys=filled_keys)
        self.assertEqual(len(filled2), 0)
        self.assertEqual(len(failed2), 0)

    def test_no_answer_recorded_not_failed(self):
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        field = {
            "label": "Some optional field", "tag": "INPUT", "type": "text",
            "_sel": "#opt", "name": "", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = None
            resolve_mock.return_value.provenance = "no_match"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        self.assertEqual(len(filled), 0)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["_why"], "no_answer")


class HasFormExcludesHidden(unittest.TestCase):
    """has_form must not count hidden/submit inputs — otherwise
    no_apply_path detection never fires (every page has CSRF inputs)."""

    def _page_with(self, selector_present):
        page = MagicMock()
        page.query_selector.return_value = selector_present
        return page

    def test_hidden_input_not_a_form(self):
        from apply.common.page_state import has_form
        page = self._page_with(None)
        self.assertFalse(has_form(page))
        page.query_selector.assert_called_once_with(
            'input:not([type=hidden]):not([type=submit]), select, textarea'
        )

    def test_visible_input_is_a_form(self):
        from apply.common.page_state import has_form
        page = self._page_with(MagicMock())
        self.assertTrue(has_form(page))

    def test_has_any_form_without_real_fields(self):
        """A page with only hidden inputs and no widgets/iframes/dialog
        must report no form (allows no_apply_path to trigger)."""
        from apply.common.page_state import has_any_form
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: None
        page.evaluate.side_effect = lambda expr: False
        page.frames = []
        self.assertFalse(has_any_form(page))


if __name__ == "__main__":
    unittest.main()
