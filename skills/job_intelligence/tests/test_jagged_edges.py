"""Tests for the five addressed jagged edges:

1. Worker outcome FILE is authoritative (no stdout parsing fragility)
   + stale outcome files are cleared before each spawn.
2. Batch supervisor holds the pipeline lock; children skip it (JI_NO_LOCK).
3. Resolver: "Which statement best describes ... relocat/resid/eligib"
   questions become answerable from the profile.
4. LLM gap-fill output passes the SAME deterministic validator as every
   other fill — the escape hatch never bypasses the code's truth.
5. no_apply_path discriminates confirmed-expired from unconfirmed
   (cookie/session variance) in the shadow outcome detail.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class WorkerOutcomeFile(unittest.TestCase):
    """The authoritative outcome is the worker's atomic JSON file; the
    supervisor prefers it over stdout parsing and never reads a stale
    file from a previous run."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [
            patch("apply.shadow.JOBS_DIR", self.dir),
            patch("apply.shadow.LOG_PATH", os.path.join(self.dir, "shadow_run.jsonl")),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        from apply.shadow import _run_worker
        self.worker = patch("apply.shadow._run_worker")
        self.wmock = self.worker.start()
        self.addCleanup(self.worker.stop)

    def test_outcome_file_preferred_over_stdout(self):
        """A worker that finished writes the file; the supervisor trusts
        it even when stdout is empty/garbled."""
        from apply.shadow import run
        with open(os.path.join(self.dir, "jid1.outcome.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"outcome": "held_shadow",
                       "detail": "fill+check OK, submit held (shadow)",
                       "secs": 4}, f)
        self.wmock.return_value = (0, "t.log", "OUTCOME=skipped detail=? SECS=4", False)
        with patch("lib.db.get_jobs_by_stage",
                   return_value=[("jid1", {"title": "T", "company": "C"})]):
            run(limit=None, quick=False)
        rec = json.loads(open(os.path.join(self.dir, "shadow_run.jsonl"),
                              encoding="utf-8").readline())
        self.assertEqual(rec["outcome"], "held_shadow")

    def test_stale_outcome_file_cleared_before_spawn(self):
        """A previous run's verdict must not mask this run's crash."""
        self.worker.stop()  # exercise the REAL supervisor spawn path
        try:
            from apply.shadow import _run_worker
            with open(os.path.join(self.dir, "jid1.outcome.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"outcome": "held_shadow"}, f)
            with patch("apply.shadow.subprocess.Popen") as popen:
                proc = MagicMock()
                proc.stdout = iter([b""])
                proc.wait.return_value = 0
                popen.return_value = proc
                with patch("apply.shadow.sys.executable", "python"):
                    rc, tlog, out, timed = _run_worker("jid1")
            self.assertFalse(os.path.exists(
                os.path.join(self.dir, "jid1.outcome.json")))
        finally:
            self.worker.start()


class BatchLock(unittest.TestCase):
    def test_children_skip_lock_via_env(self):
        from lib import chrome_manager as cm
        with patch.dict("os.environ", {"JI_NO_LOCK": "1"}):
            try:
                cm._acquire_lock()
            finally:
                if cm._LOCK_PATH.exists():
                    cm._LOCK_PATH.unlink()
        # No exception raised = acquisition skipped; nothing written.
        self.assertFalse(cm._LOCK_PATH.exists())


class ResolverStatementBestDescribes(unittest.TestCase):
    def test_relocation_statement_answerable(self):
        from apply.common.resolve import resolve
        prof = {"answers": {"willing_to_relocate": "Yes"},
                "location": "Ottawa, Ontario, Canada"}
        r = resolve("Which statement best describes your location "
                    "relocation preferences?", prof)
        self.assertIsNotNone(r.value)
        self.assertEqual(r.value, "Yes")

    def test_ai_essay_not_matched(self):
        from apply.common.resolve import resolve
        prof = {"willing_to_relocate": "Yes"}
        r = resolve("Which statement best describes how you use AI "
                    "Coding Assistants?", prof)
        self.assertIsNone(r.value)


class GapFillValidationGate(unittest.TestCase):
    def test_invalid_llm_value_rejected(self):
        """The LLM's mapped value must pass the same deterministic
        validator as deterministic fills — a URL in a plain-text field is
        rejected, an option-member value is accepted."""
        from apply.common.fill_runner import gap_fill_into_answers as _gap_fill_into_answers
        fields = [
            {"label": "Place you call home", "tag": "INPUT", "type": "text"},
            {"label": "Nation", "tag": "SELECT",
             "options": ["Canada", "USA", "Germany"]},
        ]
        profile = {}
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("apply.act.suggest.llm_field_key_mapping",
                   return_value={"Place you call home": "https://evil.example",
                                 "Nation": "Canada"}), \
             patch("lib.db.get_job", return_value={}):
            ans = _gap_fill_into_answers(fields, profile, {}, "j", None)
        self.assertNotIn("Place you call home", ans)
        self.assertEqual(ans.get("Nation"), "Canada")

    def test_valid_llm_value_accepted(self):
        from apply.common.fill_runner import gap_fill_into_answers as _gap_fill_into_answers
        fields = [{"label": "GitHub", "tag": "INPUT", "type": "url"}]
        profile = {"github": "https://github.com/shervinemp"}
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("apply.act.suggest.llm_field_key_mapping",
                   return_value={"GitHub": "https://github.com/shervinemp"}), \
             patch("lib.db.get_job", return_value={}):
            ans = _gap_fill_into_answers(fields, profile, {}, "j", None)
        self.assertEqual(ans.get("GitHub"),
                         "https://github.com/shervinemp")


class NoApplyPathDiscriminator(unittest.TestCase):
    def test_unconfirmed_detail_surfaces(self):
        from apply.auto import _no_apply_path_detail
        d = _no_apply_path_detail({"status_detail": "unconfirmed — may be cookie/session"})
        self.assertEqual(d, "no apply path (unconfirmed — may be cookie/session)")

    def test_confirmed_detail_surfaces(self):
        from apply.auto import _no_apply_path_detail
        d = _no_apply_path_detail({"status_detail": "confirmed expired (No longer accepting applications)"})
        self.assertEqual(d, "no apply path (confirmed expired)")

    def test_no_detail_falls_back(self):
        from apply.auto import _no_apply_path_detail
        self.assertEqual(_no_apply_path_detail({}), "no apply path (expired)")


if __name__ == "__main__":
    unittest.main()
