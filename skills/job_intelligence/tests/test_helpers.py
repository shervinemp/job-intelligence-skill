"""Unit tests for apply/act/helpers.py — cross-origin redirect and error extraction."""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.act.helpers import _resolve_standalone_form_url


class ResolveStandaloneFormURL(unittest.TestCase):
    """_resolve_standalone_form_url detects cross-origin iframes with job forms."""

    def test_returns_url_from_page_evaluate(self):
        page = MagicMock()
        page.evaluate.return_value = "https://jobs.ashbyhq.com/company/123"
        result = _resolve_standalone_form_url(page)
        self.assertEqual(result, "https://jobs.ashbyhq.com/company/123")

    def test_no_iframe_returns_none(self):
        page = MagicMock()
        page.evaluate.return_value = None
        result = _resolve_standalone_form_url(page)
        self.assertIsNone(result)

    def test_evaluate_exception_returns_none(self):
        page = MagicMock()
        page.evaluate.side_effect = Exception("evaluation failed")
        result = _resolve_standalone_form_url(page)
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        page = MagicMock()
        evaluate_result = None
        page.evaluate.return_value = evaluate_result
        result = _resolve_standalone_form_url(page)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
