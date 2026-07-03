"""Unit tests for the shared success-signal tiers (apply/common/signals.py).

These phrases gate DB writes (mark applied), so the tier separation is load-bearing:
broad signals may end a poll but must never be strict (never mark a job applied).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.signals import SUCCESS_STRICT, SUCCESS_BROAD, has_success_text


class SignalTiers(unittest.TestCase):
    def test_strict_is_subset_of_broad(self):
        self.assertTrue(set(SUCCESS_STRICT).issubset(set(SUCCESS_BROAD)))

    def test_loose_phrases_are_not_strict(self):
        # "thank you for" alone matches "thank you for signing in" — poll-only.
        self.assertIn("thank you for", SUCCESS_BROAD)
        self.assertNotIn("thank you for", SUCCESS_STRICT)

    def test_detects_success_case_insensitively(self):
        self.assertTrue(has_success_text("Your Application Has Been received."))
        self.assertTrue(has_success_text("Thank you for applying to Acme!"))

    def test_no_false_positive_on_form_page(self):
        self.assertFalse(has_success_text("Fields marked * are required. Submit below."))
        self.assertFalse(has_success_text(""))
        self.assertFalse(has_success_text(None))

    def test_broad_matches_where_strict_does_not(self):
        text = "Thank you for your interest in Acme."
        self.assertFalse(has_success_text(text))
        self.assertTrue(has_success_text(text, SUCCESS_BROAD))


if __name__ == "__main__":
    unittest.main()
