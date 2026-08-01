"""Unit tests for the shadow batch runner and AutocompleteFiller verification."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _rec(jid, outcome, detail=""):
    return {"jid": jid, "outcome": outcome, "detail": detail,
            "title": "T", "company": "C", "ts": "x", "secs": 1}


class ShadowRunner(unittest.TestCase):
    """apply/shadow.py — resumable log + outcome mapping + check-error capture."""

    def setUp(self):
        self.log_dir = tempfile.mkdtemp()
        self.log = os.path.join(self.log_dir, "shadow_run.jsonl")
        self.log_patch = patch("apply.shadow.LOG_PATH", self.log)
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def test_held_shadow_mapping(self):
        from apply.shadow import run

        def fake_process(jid, job, quick=False, max_pages=4, results=None):
            results["stopped"].append((jid, "submit returned 0 but stage not applied"))

        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {"title": "T", "company": "C"})]), \
             patch("apply.auto._process_one", side_effect=fake_process):
            run(limit=None, quick=False)
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "held_shadow")
        self.assertIn("fill+check OK", rec["detail"])

    def test_check_errors_captured(self):
        from apply.shadow import run
        errors = [{"label": "Email", "reason": "Required field appears empty"}]

        def fake_process(jid, job, quick=False, max_pages=4, results=None):
            results["stopped"].append((jid, "check failed -- supply answers and retry"))

        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {"title": "T", "company": "C"})]), \
             patch("apply.auto._process_one", side_effect=fake_process), \
             patch("apply.common.page_helpers.load_state",
                   return_value={"check_errors": errors}):
            run(limit=None, quick=False)
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "stopped")
        self.assertEqual(rec["check_errors"][0]["label"], "Email")

    def test_resumable_skips_recorded(self):
        from apply.shadow import run
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(json.dumps(_rec("jid1", "held_shadow")) + "\n")
        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {})]), \
             patch("apply.auto._process_one") as proc:
            run(limit=None, quick=False)
        proc.assert_not_called()

    def test_specific_jid_only(self):
        from apply.shadow import run

        def fake_process(jid, job, quick=False, max_pages=4, results=None):
            results["skipped"].append((jid, "no apply path (expired)"))

        with patch("lib.db.get_job", return_value={"id": "abc123", "title": "T", "company": "C"}), \
             patch("apply.auto._process_one", side_effect=fake_process) as proc:
            run(jids=["abc123"], limit=None, quick=False)
        proc.assert_called_once()
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["jid"], "abc123")


class AutocompleteVerification(unittest.TestCase):
    """AutocompleteFiller must verify the clicked suggestion and only
    retry when a reader proves the first option wrong."""

    def _filler(self):
        from apply.common.filler import AutocompleteFiller
        return AutocompleteFiller()

    def test_verdict_match(self):
        f = self._filler()
        page = MagicMock()
        with patch("apply.common.value_reader.AriaComboboxReader") as ac, \
             patch("apply.common.value_reader.ReactSelectReader") as rs:
            ac.return_value.read.return_value = "Yes, I am authorized"
            self.assertTrue(f._selection_verdict(page, "#sel", "yes, i am authorized"))

    def test_verdict_mismatch(self):
        f = self._filler()
        page = MagicMock()
        with patch("apply.common.value_reader.AriaComboboxReader") as ac, \
             patch("apply.common.value_reader.ReactSelectReader") as rs:
            ac.return_value.read.return_value = "No, I am not authorized"
            self.assertFalse(f._selection_verdict(page, "#sel", "yes"))

    def test_verdict_unreadable(self):
        f = self._filler()
        page = MagicMock()
        with patch("apply.common.value_reader.AriaComboboxReader") as ac, \
             patch("apply.common.value_reader.ReactSelectReader") as rs:
            ac.return_value.read.return_value = None
            rs.return_value.read.return_value = None
            self.assertIsNone(f._selection_verdict(page, "#sel", "yes"))

    def _page_with_suggestions(self, click_result=True):
        page = MagicMock()
        el = MagicMock()
        el.count.return_value = 1
        page.locator.return_value.first = el
        page.evaluate.return_value = click_result
        return page, el

    def test_matching_suggestion_accepted(self):
        f = self._filler()
        page, el = self._page_with_suggestions()
        with patch.object(f, "_selection_verdict", return_value=True):
            self.assertTrue(f.fill(page, {"_sel": "#x", "label": "Location"}, "Ottawa"))
        el.press.assert_not_called()

    def test_wrong_suggestion_then_correct(self):
        f = self._filler()
        page, el = self._page_with_suggestions()
        with patch.object(f, "_selection_verdict", side_effect=[False, True]):
            self.assertTrue(f.fill(page, {"_sel": "#x", "label": "Location"}, "Ottawa"))

    def test_wrong_suggestion_no_better_option_fails(self):
        f = self._filler()
        page, el = self._page_with_suggestions()
        with patch.object(f, "_selection_verdict", side_effect=[False, False]):
            self.assertFalse(f.fill(page, {"_sel": "#x", "label": "Location"}, "Ottawa"))

    def test_no_dropdown_keeps_typed_value(self):
        f = self._filler()
        page, el = self._page_with_suggestions(click_result=False)
        with patch.object(f, "_selection_verdict") as verdict:
            self.assertTrue(f.fill(page, {"_sel": "#x", "label": "Location"}, "Ottawa"))
        el.press.assert_called_once_with("Tab")
        verdict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
