"""test_registry_and_stage_cli.py — registry CLI dispatch + stage-CLI commands.

registry_cli.py is a thin wrapper over the (already-tested) observations/corpus
stores; the value here is the DISPATCH and the error paths. enrich.py cmd_flag
and cmd_status are DB/state-driven and testable against a temp schema DB.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _StageDB(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        import lib.db.schema as schema
        schema._conn = None
        schema.DB_PATH = os.path.join(self._tmp, "stage.db")
        schema.DB_DIR = self._tmp
        self.conn = schema.get_conn()

    def tearDown(self):
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _job(self, jid, url, stage="extracted", state="active"):
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, "
            "created_at, updated_at, scripts) "
            "VALUES (?,?,?,?,?,?,?,?, '[]')",
            (jid, url, "Role", "Co", stage, state,
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        self.conn.commit()

    def _stderr(self, fn, *args, **kwargs):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            fn(*args, **kwargs)
        return buf.getvalue()


class RegistryCliDispatch(unittest.TestCase):
    def test_unknown_action_errors(self):
        """Fix #4: an unknown registry action must fail loudly, not silently
        return 0 — a typo'd action must never look like success."""
        from apply.common.registry_cli import cmd_registry
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cmd_registry("nonsense")
        self.assertEqual(rc, 1)
        self.assertIn("unknown registry action", buf.getvalue())

    def test_confirm_requires_hash(self):
        from apply.common.registry_cli import cmd_registry
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cmd_registry("confirm")
        self.assertEqual(rc, 1)
        self.assertIn("ERROR: confirm requires a profile hash", buf.getvalue())

    def test_clear_requires_hash(self):
        from apply.common.registry_cli import cmd_registry
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cmd_registry("clear")
        self.assertEqual(rc, 1)
        self.assertIn("ERROR: clear requires a profile hash", buf.getvalue())

    def test_confirm_prefix_resolves_and_promotes(self):
        from apply.common.registry_cli import cmd_registry, _cmd_confirm
        with patch("apply.common.observations.list_all",
                   return_value=[{"profile_hash": "abcdef1234567890",
                                  "winning_strategy": "standard",
                                  "confirmed": False}]):
            with patch("apply.common.observations.confirm_hash",
                       return_value=True) as cf:
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    _cmd_confirm("abcdef12")  # 8-char prefix
                cf.assert_called_once_with("abcdef1234567890")
                self.assertIn("CONFIRMED", buf.getvalue())

    def test_clear_unknown_hash_reports_error(self):
        from apply.common.registry_cli import _cmd_clear
        with patch("apply.common.observations.list_all", return_value=[]), \
             patch("apply.common.observations.clear_hash", return_value=False):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                _cmd_clear("nope")
            self.assertIn("ERROR: no observation found", buf.getvalue())

    def test_candidates_empty_message(self):
        from apply.common.registry_cli import _cmd_candidates
        with patch("apply.common.observations.list_all", return_value=[]):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                _cmd_candidates()
            self.assertIn("No observations yet", buf.getvalue())

    def test_failures_empty_message(self):
        from apply.common.registry_cli import _cmd_failures
        import tempfile as _t
        tmp = _t.mkdtemp()
        try:
            with patch("apply.common.registry_cli.JI_HOME", tmp):
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    _cmd_failures()
                self.assertIn("No failure artifacts", buf.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EnrichFlagAndStatus(_StageDB):
    def test_flag_adds_auth_wall_for_known_job(self):
        from enrich import cmd_flag
        self._job("aaaaaaaaaaaaaaaa", "https://x.com/j")
        with patch("enrich.auth_walls.add") as aw, \
             patch("enrich.load", return_value={
                 "jobs": {"aaaaaaaaaaaaaaaa": {
                     "url": "https://x.com/j", "title": "Role",
                     "company": "Co"}}}), \
             patch("enrich.pipeline_status", return_value={"next_step": "tailor.py"}):
            err = self._stderr(cmd_flag, "aaaaaaaaaaaaaaaa")
        aw.assert_called_once()
        self.assertIn("FLAGGED:1", err)

    def test_flag_no_jids_prints_usage(self):
        from enrich import cmd_flag
        err = self._stderr(cmd_flag)
        self.assertIn("Usage: python3 enrich.py flag", err)

    def test_status_no_jobs_message(self):
        from enrich import cmd_status
        with patch("enrich.pipeline_status",
                   return_value={"jobs": 0, "stages": {}, "staged": {},
                                 "auth_walls": {"count": 0, "domains": []}}):
            err = self._stderr(cmd_status)
            self.assertIn("No jobs in state", err)

    def test_status_prints_stage_counts(self):
        from enrich import cmd_status
        with patch("enrich.pipeline_status",
                   return_value={"jobs": 3,
                                 "stages": {"tailored": 2, "applied": 1},
                                 "staged": {"pending": 0},
                                 "auth_walls": {"count": 0, "domains": []},
                                 "next_step": "tailor.py"}):
            err = self._stderr(cmd_status)
            self.assertIn("Jobs: 3 total", err)
            self.assertIn("tailored: 2", err)
            self.assertIn("applied: 1", err)


if __name__ == "__main__":
    unittest.main()
