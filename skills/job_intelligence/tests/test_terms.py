"""Tests for the vocabulary unification (terms.py + shapes.py):

- KINDS/OUTCOMES/STATUS/SEVERITIES/LLM_STATUSES are the ONLY values —
  pinned here so drift is a test failure, not a misread.
- summarize() is the single aggregate: the dossier summary, the
  DECISION line, and the classifier all derive from it.
- trunc() is display-only and always visible.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TermsVocabulary(unittest.TestCase):
    """Gap: vocabulary drift caused a double-count bug and repeated
    misreads. The constants are THE vocabulary — pinned here."""

    def test_kinds_exact(self):
        from apply.common import terms as T
        self.assertEqual(set(T.KINDS),
                         {"verified", "unverified", "needs_data",
                          "rejected_by_form", "interaction_failed"})

    def test_outcomes_exact(self):
        from apply.common import terms as T
        self.assertEqual(set(T.OUTCOMES),
                         {"held_shadow", "stopped", "skipped", "crash",
                          "timeout", "error", "already_applied",
                          "submitted", "exception"})

    def test_llm_statuses_exact(self):
        from apply.common import terms as T
        self.assertEqual(set(T.LLM_STATUSES),
                         {"unused", "policy_off", "api_down", "declined",
                          "used"})

    def test_summarize_sums_to_total(self):
        """filled + failed + skipped_optional == unique total, always —
        the double-count regression, pinned at the single implementation."""
        from apply.common import terms as T
        fields = [
            {"kind": T.VERIFIED},
            {"kind": T.UNVERIFIED},
            {"kind": T.NEEDS_DATA, "required": True},
            {"kind": T.NEEDS_DATA, "required": False},
            {"kind": T.REJECTED_BY_FORM},
            {"kind": T.INTERACTION_FAILED},
        ]
        s = T.summarize(fields)
        self.assertEqual(s, {"filled": 2, "failed": 3, "skipped_optional": 1})
        self.assertEqual(sum(s.values()), len(fields))

    def test_summarize_unknown_kind_fails_closed(self):
        from apply.common import terms as T
        s = T.summarize([{"kind": "not_a_kind"}])
        self.assertEqual(s["failed"], 1)  # unknown kinds count as failures

    def test_failed_labels_exclude_optional(self):
        from apply.common import terms as T
        fields = [
            {"kind": T.REJECTED_BY_FORM, "label": "A"},
            {"kind": T.NEEDS_DATA, "required": False, "label": "B"},
            {"kind": T.NEEDS_DATA, "required": True, "label": "C"},
        ]
        self.assertEqual(T.failed_labels(fields), ["A", "C"])
        self.assertEqual(T.skipped_labels(fields), ["B"])

    def test_trunc_visible(self):
        from apply.common import terms as T
        s = "x" * 100
        t = T.trunc(s)
        self.assertTrue(t.endswith(T.TRUNC_MARK))
        self.assertLessEqual(len(t), T.TRUNC_W)
        self.assertEqual(T.trunc("short"), "short")


class HandoffUsesSingleAggregate(unittest.TestCase):
    """_write_handoff writes terms.summarize output — one implementation
    for the dossier summary and the DECISION line."""

    def test_handoff_summary_is_summarize(self):
        from apply.act import fill
        from apply.common import terms as T
        tmp = tempfile.mkdtemp()
        filled = [{"label": "A", "answer": "1", "method": "deterministic"}]
        failed = [
            {"label": "B", "attempted": "2", "_why": "no_answer",
             "required": True},
            {"label": "C", "attempted": "3", "_why": "fill_failed",
             "required": False,
             "_diag": {"method": "combobox", "reason": "no_option_match"}},
            {"label": "D", "attempted": "4", "_why": "no_answer",
             "required": False},
        ]
        with patch("apply.act.fill.RESULTS_DIR", tmp):
            fill._write_handoff("j1", "u", filled, failed, {},
                                mode="shadow")
        doc = json.load(open(os.path.join(tmp, "j1", "handoff.json"),
                             encoding="utf-8"))
        fields = doc["fields"]
        self.assertEqual(doc["summary"], T.summarize(fields))
        # A filled; B required-no-answer failed; C form-rejected = failed
        # (rejected is a failure regardless of optionality); D optional
        # no-answer = skipped.
        self.assertEqual(doc["summary"]["filled"], 1)
        self.assertEqual(doc["summary"]["failed"], 2)
        self.assertEqual(doc["summary"]["skipped_optional"], 1)


class GlossaryGenerated(unittest.TestCase):
    def test_glossary_terms_are_constant_backed(self):
        from apply.common.terms import glossary
        g = dict((t, n) for t, n, _ in glossary())
        self.assertIn("no_match", g)
        self.assertIn("fill_answers", g)
        self.assertIn("dossier", g)


if __name__ == "__main__":
    unittest.main()
