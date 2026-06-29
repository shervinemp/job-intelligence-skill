"""Unit tests for ButtonIntentClassifier (apply/common/learner.py).

The classifier decides whether a form button advances, submits, goes back, or
cancels. Misclassification here = clicking the wrong button in an unattended run,
so the high-confidence known mappings and the safe fallbacks are worth pinning.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.learner import ButtonIntentClassifier as BIC


class ClassifyKnown(unittest.TestCase):
    def test_submit_application(self):
        self.assertEqual(BIC.classify("Submit application"), ("submit", 1.0))

    def test_next_is_advance(self):
        intent, conf = BIC.classify("Next")
        self.assertEqual(intent, "advance")
        self.assertGreaterEqual(conf, 0.9)

    def test_back(self):
        self.assertEqual(BIC.classify("Back")[0], "back")

    def test_case_insensitive(self):
        self.assertEqual(BIC.classify("  SUBMIT  ")[0], "submit")


class ClassifyHeuristics(unittest.TestCase):
    def test_word_scoring_picks_submit(self):
        self.assertEqual(BIC.classify("Submit my form")[0], "submit")

    def test_regex_fallback_continue_variants(self):
        self.assertEqual(BIC.classify("Save and proceed")[0], "advance")

    def test_unknown_text(self):
        self.assertEqual(BIC.classify("xyzzy frobnicate"), ("unknown", 0.0))


class Pick(unittest.TestCase):
    def test_picks_highest_confidence_matching_intent(self):
        buttons = [
            {"text": "Cancel"},
            {"text": "Submit application"},
            {"text": "Back"},
        ]
        best = BIC.pick(buttons, "submit")
        self.assertIsNotNone(best)
        self.assertEqual(best["index"], 1)

    def test_returns_none_when_no_match(self):
        self.assertIsNone(BIC.pick([{"text": "Back"}, {"text": "Cancel"}], "submit"))


if __name__ == "__main__":
    unittest.main()
