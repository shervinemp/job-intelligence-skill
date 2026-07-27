"""Unit tests for success-signal detection (apply/common/signals.py).

These phrases gate DB writes (mark applied), so precision matters:
false positives would mark jobs as applied when they weren't.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.signals import SUCCESS_STRICT, has_success_text, has_already_applied_text


class SignalDetection(unittest.TestCase):
    def test_detects_success_case_insensitively(self):
        self.assertTrue(has_success_text("Your Application Has Been received."))
        self.assertTrue(has_success_text("Thank you for applying to Acme!"))
        self.assertTrue(has_success_text("Votre candidature a \u00e9t\u00e9 re\u00e7ue."))
        self.assertTrue(has_success_text("Merci d'avoir postul\u00e9!"))

    def test_no_false_positive_on_form_page(self):
        self.assertFalse(has_success_text("Fields marked * are required. Submit below."))
        self.assertFalse(has_success_text(""))
        self.assertFalse(has_success_text(None))

    def test_strict_phrases_are_specific(self):
        for phrase in SUCCESS_STRICT:
            self.assertTrue(len(phrase) > 5, f"Phrase too short: {phrase}")


class AlreadyAppliedDetection(unittest.TestCase):
    def test_detects_already_applied(self):
        self.assertTrue(has_already_applied_text("Your application was sent 3 days ago"))
        self.assertTrue(has_already_applied_text("You've already applied to this job"))
        self.assertTrue(has_already_applied_text("Withdraw application"))
        self.assertTrue(has_already_applied_text("Applied 2 weeks ago"))
        self.assertTrue(has_already_applied_text("Vous avez d\u00e9j\u00e0 postul\u00e9 \u00e0 ce poste"))

    def test_no_false_positive_on_fresh_form(self):
        self.assertFalse(has_already_applied_text("Fields marked * are required."))
        self.assertFalse(has_already_applied_text("Submit your application below."))
        self.assertFalse(has_already_applied_text(""))
        self.assertFalse(has_already_applied_text(None))


if __name__ == "__main__":
    unittest.main()
