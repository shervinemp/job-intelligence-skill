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
    """apply/shadow.py — subprocess supervisor: resumable log + outcome
    mapping + check-error capture + timeout/crash handling."""

    def setUp(self):
        self.log_dir = tempfile.mkdtemp()
        self.log = os.path.join(self.log_dir, "shadow_run.jsonl")
        self.log_patch = patch("apply.shadow.LOG_PATH", self.log)
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.jobs_patch = patch("apply.shadow.JOBS_DIR", self.log_dir)
        self.jobs_patch.start()
        self.addCleanup(self.jobs_patch.stop)
        # Keep the worker never actually spawning: _run_worker is mocked.
        self.worker_patch = patch("apply.shadow._run_worker")
        self.worker_mock = self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)

    def _out(self, outcome, detail="", check_errors=None, secs=3):
        lines = [f"OUTCOME={outcome}", f"DETAIL={detail}", f"SECS={secs}"]
        if check_errors:
            lines.append(f"CHECK_ERRORS={json.dumps(check_errors)}")
        return "\n".join(lines)

    def test_held_shadow_mapping(self):
        from apply.shadow import run
        self.worker_mock.return_value = (0, "t.log", self._out("held_shadow", "fill+check OK, submit held (shadow)"), False)
        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {"title": "T", "company": "C"})]):
            run(limit=None, quick=False)
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "held_shadow")
        self.assertIn("fill+check OK", rec["detail"])
        self.assertEqual(rec["exit_code"], 0)

    def test_check_errors_captured(self):
        from apply.shadow import run
        errors = [{"label": "Email", "reason": "Required field appears empty"}]
        self.worker_mock.return_value = (0, "t.log", self._out("stopped", "check failed -- supply answers and retry", errors), False)
        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {"title": "T", "company": "C"})]):
            run(limit=None, quick=False)
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "stopped")
        self.assertEqual(rec["check_errors"][0]["label"], "Email")

    def test_resumable_skips_recorded(self):
        from apply.shadow import run
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(json.dumps(_rec("jid1", "held_shadow")) + "\n")
        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {})]):
            run(limit=None, quick=False)
        self.worker_mock.assert_not_called()

    def test_specific_jid_only(self):
        from apply.shadow import run
        self.worker_mock.return_value = (0, "t.log", self._out("skipped", "no apply path (expired)"), False)
        with patch("lib.db.get_job", return_value={"id": "abc123", "title": "T", "company": "C"}):
            run(jids=["abc123"], limit=None, quick=False)
        self.worker_mock.assert_called_once()
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["jid"], "abc123")

    def test_crash_reprobes_once_then_records(self):
        from apply.shadow import run
        self.worker_mock.side_effect = [
            (1, "t1.log", "traceback\nFatal Python error", False),
            (0, "t2.log", self._out("held_shadow"), False),
        ]
        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {"title": "T", "company": "C"})]):
            run(limit=None, quick=False)
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "held_shadow")
        self.assertTrue(rec["after_crash"])
        self.assertEqual(self.worker_mock.call_count, 2)

    def test_double_crash_records_crash(self):
        from apply.shadow import run
        self.worker_mock.side_effect = [
            (1, "t1.log", "Fatal Python error", False),
            (1, "t2.log", "Fatal Python error again", False),
        ]
        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {"title": "T", "company": "C"})]):
            run(limit=None, quick=False)
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "crash")
        self.assertIn("Fatal", rec["tail"])

    def test_timeout_recorded_without_reprobe(self):
        """A slow job killed by the budget is NOT a crash — no re-probe."""
        from apply.shadow import run
        self.worker_mock.return_value = (-9, "t.log", "SHADOW: per-job timeout (600s) — killed\n", True)
        with patch("lib.db.get_jobs_by_stage", return_value=[("jid1", {"title": "T", "company": "C"})]):
            run(limit=None, quick=False)
        rec = json.loads(open(self.log, encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "timeout")
        self.assertEqual(self.worker_mock.call_count, 1)

    def test_consecutive_failures_abort_batch(self):
        from apply.shadow import run
        self.worker_mock.return_value = (-9, "t.log", "SHADOW: per-job timeout (600s) — killed\n", True)
        jobs = [(f"jid{i}", {"title": "T", "company": "C"}) for i in range(5)]
        with patch("lib.db.get_jobs_by_stage", return_value=jobs), \
             patch("apply.shadow.ABORT_AFTER_CONSECUTIVE_FAILS", 2):
            run(limit=None, quick=False)
        self.assertEqual(self.worker_mock.call_count, 2)
        n = sum(1 for _ in open(self.log, encoding="utf-8"))
        self.assertEqual(n, 2)


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
