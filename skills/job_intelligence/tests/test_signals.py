"""Unit tests for success-signal detection (apply/common/signals.py).

These phrases gate DB writes (mark applied), so precision matters:
false positives would mark jobs as applied when they weren't.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.signals import SUCCESS_STRICT, has_success_text


class SignalDetection(unittest.TestCase):
    def test_detects_success_case_insensitively(self):
        self.assertTrue(has_success_text("Your Application Has Been received."))
        self.assertTrue(has_success_text("Thank you for applying to Acme!"))

    def test_no_false_positive_on_form_page(self):
        self.assertFalse(has_success_text("Fields marked * are required. Submit below."))
        self.assertFalse(has_success_text(""))
        self.assertFalse(has_success_text(None))

    def test_strict_phrases_are_specific(self):
        for phrase in SUCCESS_STRICT:
            self.assertTrue(len(phrase) > 5, f"Phrase too short: {phrase}")


if __name__ == "__main__":
    unittest.main()
