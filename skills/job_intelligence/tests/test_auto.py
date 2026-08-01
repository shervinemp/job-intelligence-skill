"""Unit tests for apply/auto.py — autonomous pipeline orchestrator."""
import os, sys, unittest, tempfile, json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apply.auto import run, _process_one, _print_summary, _extract_error_labels


class AutoDryRun(unittest.TestCase):
    def test_no_tailored_jobs_returns_zero(self):
        with patch("lib.db.get_jobs_by_stage", return_value=[]):
            rc = run()
            self.assertEqual(rc, 0)

    def test_jid_not_found_returns_one(self):
        with patch("lib.db.get_job", return_value=None):
            rc = run(jid="nonexistent")
            self.assertEqual(rc, 1)


class AutoProcessOne(unittest.TestCase):
    def test_detect_fail_skips(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        with patch("apply.detect.run", return_value=1), \
             patch("apply.common.page_helpers.load_state", return_value={}):
            _process_one("testjid", {}, False, 4, results)
        self.assertEqual(len(results["skipped"]), 1)
        self.assertEqual(len(results["submitted"]), 0)

    def test_already_applied_detected(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        with patch("apply.detect.run", return_value=0), \
             patch("apply.common.page_helpers.load_state", return_value={"type": "already_applied"}):
            _process_one("testjid", {}, False, 4, results)
        self.assertEqual(len(results["already_applied"]), 1)
        self.assertEqual(len(results["submitted"]), 0)

    def test_unknown_type_skipped(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        with patch("apply.detect.run", return_value=0), \
             patch("apply.common.page_helpers.load_state", return_value={"type": "unknown"}):
            _process_one("testjid", {}, False, 4, results)
        self.assertEqual(len(results["skipped"]), 1)
        self.assertEqual(len(results["submitted"]), 0)

    def test_check_fail_stops(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        state = {"type": "ats_direct"}
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"stage": "tailored"}
        with patch("apply.detect.run", return_value=0), \
             patch("apply.common.page_helpers.load_state", return_value=state), \
             patch("apply.act.fill.cmd_fill", return_value=0), \
             patch("apply.act.check.cmd_check", return_value=1), \
             patch("lib.db.get_conn", return_value=conn):
            _process_one("testjid", {}, False, 4, results)
        self.assertEqual(len(results["stopped"]), 1)
        self.assertEqual(len(results["submitted"]), 0)

    def test_fill_exception_retried_once(self):
        """A fill exception (no status set, e.g. execution-context destroyed)
        must retry the fill once before declaring failure."""
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        state = {"type": "ats_direct"}
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [
            {"stage": "tailored"},  # after first (failed) fill
            {"stage": "tailored"},  # after retried fill
            {"stage": "applied"},   # after submit
        ]
        with patch("apply.detect.run", return_value=0), \
             patch("apply.common.page_helpers.load_state", return_value=state), \
             patch("apply.act.fill.cmd_fill", side_effect=[1, 0]) as fill_mock, \
             patch("apply.act.check.cmd_check", return_value=0), \
             patch("apply.act.submit.cmd_submit", return_value=0), \
             patch("apply.auto.time.sleep"), \
             patch("lib.db.get_conn", return_value=conn):
            _process_one("testjid", {}, False, 4, results)
        self.assertEqual(fill_mock.call_count, 2)
        self.assertEqual(len(results["submitted"]), 1)
        self.assertEqual(len(results["stopped"]), 0)

    def test_fill_exception_still_fails_after_retry(self):
        """If the retry also throws, the job must be diagnosed+stopped,
        not silently skipped."""
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        state = {"type": "ats_direct"}
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"stage": "tailored"}
        with patch("apply.detect.run", return_value=0), \
             patch("apply.common.page_helpers.load_state", return_value=state), \
             patch("apply.act.fill.cmd_fill", return_value=1) as fill_mock, \
             patch("apply.act.inspect.cmd_inspect"), \
             patch("apply.auto._retry_fill_with_llm", return_value=False), \
             patch("apply.auto.time.sleep"), \
             patch("lib.db.get_conn", return_value=conn):
            _process_one("testjid", {}, False, 4, results)
        self.assertEqual(fill_mock.call_count, 2)
        self.assertEqual(len(results["stopped"]), 1)

    def test_full_pipeline_submits(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        state = {"type": "ats_direct"}
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [
            {"stage": "tailored"},  # after fill
            {"stage": "applied"},   # after submit
        ]
        with patch("apply.detect.run", return_value=0), \
             patch("apply.common.page_helpers.load_state", return_value=state), \
             patch("apply.act.fill.cmd_fill", return_value=0), \
             patch("apply.act.check.cmd_check", return_value=0), \
             patch("apply.act.submit.cmd_submit", return_value=0), \
             patch("lib.db.get_conn", return_value=conn):
            _process_one("testjid", {}, False, 4, results)
        self.assertEqual(len(results["submitted"]), 1)
        self.assertEqual(len(results["stopped"]), 0)


class AutoDedupGuard(unittest.TestCase):
    def test_duplicate_applied_skips(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        job = {"title": "Senior Engineer", "company": "Acme", "url": "https://example.com"}
        dup = {"id": "existing1234567", "stage": "applied"}
        with patch("lib.db.get_job", return_value=job), \
             patch("lib.db.find_duplicate", return_value=dup):
            _process_one("testjid", job, False, 4, results)
        self.assertEqual(len(results["already_applied"]), 1)
        self.assertIn("duplicate", results["already_applied"][0][1])
        self.assertEqual(len(results["submitted"]), 0)

    def test_duplicate_not_applied_continues(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        job = {"title": "Senior Engineer", "company": "Acme", "url": "https://example.com"}
        dup = {"id": "existing1234567", "stage": "tailored"}
        with patch("lib.db.get_job", return_value=job), \
             patch("lib.db.find_duplicate", return_value=dup), \
             patch("apply.detect.run", return_value=1), \
             patch("apply.common.page_helpers.load_state", return_value={}):
            _process_one("testjid", job, False, 4, results)
        self.assertEqual(len(results["already_applied"]), 0)
        self.assertEqual(len(results["skipped"]), 1)

    def test_no_title_skips_dedup(self):
        results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}
        job = {"title": "", "company": "", "url": "https://example.com"}
        with patch("lib.db.get_job", return_value=job), \
             patch("lib.db.find_duplicate", return_value=None) as fd_mock, \
             patch("apply.detect.run", return_value=1), \
             patch("apply.common.page_helpers.load_state", return_value={}):
            _process_one("testjid", job, False, 4, results)
        fd_mock.assert_not_called()
        self.assertEqual(len(results["skipped"]), 1)


class ExtractErrorLabels(unittest.TestCase):
    """_extract_error_labels extracts field labels from validation error strings."""

    def test_missing_entry_prefix(self):
        errors = ["Missing entry for required field: Select your country of employment"]
        self.assertEqual(_extract_error_labels(errors), ["Select your country of employment"])

    def test_bare_field_name(self):
        errors = ["Company name", "Company name is required."]
        result = _extract_error_labels(errors)
        self.assertIn("Company name", result)

    def test_deduplicates(self):
        errors = [
            "Missing entry for required field: Email",
            "Email",
            "Missing entry for required field: Email",
        ]
        self.assertEqual(len(_extract_error_labels(errors)), 1)

    def test_empty_input(self):
        self.assertEqual(_extract_error_labels([]), [])


if __name__ == "__main__":
    unittest.main()
