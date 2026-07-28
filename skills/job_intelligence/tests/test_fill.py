"""Unit tests for apply/act/fill.py — batch verify LLM integration."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.act.fill import _batch_verify


class BatchVerifyGuard2(unittest.TestCase):
    """Guard 2: LLM flags >50% of sent fields → return None."""

    @patch("lib.ask_api.ask_text")
    def test_range_dump_rejected(self, mock_ask):
        mock_ask.return_value = ("0,1,2,3,4,5,6,7,8", None)
        # 6 suspect + 3 clear = 9 sent → 9 flagged = 100% > 50% → None
        fields = [
            {"_suspect": True, "value": str(i), "label": f"S{i}", "tag": "INPUT"}
            for i in range(6)
        ] + [
            {"_suspect": False, "value": "x", "label": f"C{i}", "tag": "INPUT"}
            for i in range(3)
        ]
        result = _batch_verify(fields)
        self.assertIsNone(result)

    @patch("lib.ask_api.ask_text")
    def test_range_dump_partial_still_rejected(self, mock_ask):
        mock_ask.return_value = ("0,1,2,3,4", None)
        # 6 suspect + 3 clear = 9 sent → 5 flagged = 55% > 50% → None
        fields = [
            {"_suspect": True, "value": str(i), "label": f"S{i}", "tag": "INPUT"}
            for i in range(6)
        ] + [
            {"_suspect": False, "value": "x", "label": f"C{i}", "tag": "INPUT"}
            for i in range(3)
        ]
        result = _batch_verify(fields)
        self.assertIsNone(result)


class BatchVerifyNONE(unittest.TestCase):
    """LLM returns NONE (no wrong fields) → return empty dict."""

    @patch("lib.ask_api.ask_text")
    def test_none_with_suspects_returns_empty(self, mock_ask):
        mock_ask.return_value = ("NONE", None)
        fields = [
            {"_suspect": True, "value": "john@abc.com",
             "label": "Email", "tag": "INPUT", "type": "email"},
            {"_suspect": False, "value": "John",
             "label": "First name", "tag": "INPUT"},
        ]
        result = _batch_verify(fields)
        self.assertEqual(result, {})

    @patch("lib.ask_api.ask_text")
    def test_none_no_suspects_returns_empty(self, mock_ask):
        mock_ask.return_value = ("NONE", None)
        fields = [
            {"_suspect": False, "value": "John",
             "label": "First name", "tag": "INPUT"},
        ]
        result = _batch_verify(fields)
        self.assertEqual(result, {})


class BatchVerifyError(unittest.TestCase):
    """LLM errors → return None."""

    @patch("lib.ask_api.ask_text")
    def test_llm_error_returns_none(self, mock_ask):
        mock_ask.return_value = (None, "connection timeout")
        fields = [
            {"_suspect": True, "value": "John",
             "label": "First name", "tag": "INPUT"},
        ]
        result = _batch_verify(fields)
        self.assertIsNone(result)

    @patch("lib.ask_api.ask_text")
    def test_llm_empty_reply_returns_none(self, mock_ask):
        mock_ask.return_value = ("", None)
        fields = [
            {"_suspect": True, "value": "John",
             "label": "First name", "tag": "INPUT"},
        ]
        result = _batch_verify(fields)
        self.assertIsNone(result)


class BatchVerifyOK(unittest.TestCase):
    """LLM returns valid results → return dict of flagged indices."""

    @patch("lib.ask_api.ask_text")
    def test_llm_flags_valid_indices(self, mock_ask):
        mock_ask.return_value = ("0,2", None)
        # 2 suspect + 2 clear = 4 sent → 2/4 = 50%, Guard 2 NOT triggered
        fields = [
            {"_suspect": True, "value": "wrong@email",
             "label": "Email", "tag": "INPUT", "type": "email"},
            {"_suspect": True, "value": "123",
             "label": "Phone", "tag": "INPUT"},
            {"_suspect": False, "value": "Smith",
             "label": "Last name", "tag": "INPUT"},
            {"_suspect": False, "value": "Canada",
             "label": "Country", "tag": "INPUT"},
        ]
        result = _batch_verify(fields)
        self.assertIsNotNone(result)
        self.assertIn(0, result)
        self.assertEqual(result[0], "llm_reject")
        if 2 in result:
            self.assertEqual(result[2], "llm_reject")
        self.assertNotIn(1, result)

    @patch("lib.ask_api.ask_text")
    def test_out_of_range_index_filtered(self, mock_ask):
        mock_ask.return_value = ("0,99", None)
        # 3 suspect sent → 1/3 = 33% flagged → Guard 2 not triggered
        fields = [
            {"_suspect": True, "value": "wrong@email",
             "label": "Email", "tag": "INPUT", "type": "email"},
            {"_suspect": True, "value": "John",
             "label": "First name", "tag": "INPUT"},
            {"_suspect": True, "value": "Smith",
             "label": "Last name", "tag": "INPUT"},
        ]
        result = _batch_verify(fields)
        self.assertEqual(result, {0: "llm_reject"})


class BatchVerifyNoOp(unittest.TestCase):
    """No fields to verify → return empty dict without calling LLM."""

    @patch("lib.ask_api.ask_text")
    def test_empty_fields(self, mock_ask):
        result = _batch_verify([])
        self.assertEqual(result, {})
        mock_ask.assert_not_called()

    @patch("lib.ask_api.ask_text")
    def test_no_suspect_no_clear_values(self, mock_ask):
        fields = [
            {"_suspect": False, "value": "",
             "label": "First name", "tag": "INPUT"},
        ]
        result = _batch_verify(fields)
        self.assertEqual(result, {})
        mock_ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
