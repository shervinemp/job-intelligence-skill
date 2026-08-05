"""test_readiness_gate.py — the READY/HOLD decision and post-submit surfaces.

The orchestrator's first screen (`ji status`) splits jobs into READY (risk
fields all observed) vs HOLD (unverified/needs-data risk). This file exercises
the ACTUAL gate logic — `_risk_unverified` reading real dossiers, not mocked —
plus the post-submit confirmation surface (`report.py applied`) and the
regression-diff branches (`compare_handoffs`).

These are the surfaces that turn raw dossier fields into a fleet decision; a
regression here silently mis-routes a job between READY and HOLD.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _RiskFixture(unittest.TestCase):
    """Temp RESULTS_DIR so _risk_unverified reads REAL dossiers."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._results = os.path.join(self._tmp, "results")
        os.makedirs(self._results, exist_ok=True)
        self._results_patcher = patch("lib.config.RESULTS_DIR", self._results)
        self._results_patcher.start()
        self.addCleanup(self._results_patcher.stop)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _dossier(self, jid, fields):
        d = os.path.join(self._results, jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "fields": fields}, f)


class RiskUnverifiedGate(_RiskFixture):
    """_risk_unverified — the READY/HOLD split, against real dossier files."""

    def test_no_dossier_means_ready(self):
        from ji import _risk_unverified
        self.assertEqual(_risk_unverified("aaaaaaaaaaaaaaaa"), [])

    def test_verified_risk_field_is_ready(self):
        from ji import _risk_unverified
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "Country", "kind": "verified", "answer": "Canada"},
        ])
        self.assertEqual(_risk_unverified("aaaaaaaaaaaaaaaa"), [])

    def test_unverified_risk_field_is_hold(self):
        from ji import _risk_unverified
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "Country", "kind": "unverified", "answer": ""},
        ])
        self.assertIn("Country", _risk_unverified("aaaaaaaaaaaaaaaa"))

    def test_required_needs_data_is_hold(self):
        from ji import _risk_unverified
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "Work authorization", "kind": "needs_data",
             "required": True},
        ])
        out = _risk_unverified("aaaaaaaaaaaaaaaa")
        self.assertTrue(any("needs data" in x for x in out))

    def test_optional_needs_data_is_not_hold(self):
        """An optional needs_data field must NOT block READY — only required
        gaps do."""
        from ji import _risk_unverified
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "Salary expectation", "kind": "needs_data",
             "required": False},
        ])
        self.assertEqual(_risk_unverified("aaaaaaaaaaaaaaaa"), [])

    def test_non_risk_field_unverified_is_not_hold(self):
        from ji import _risk_unverified
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "Personal website", "kind": "unverified", "answer": ""},
        ])
        self.assertEqual(_risk_unverified("aaaaaaaaaaaaaaaa"), [])

    def test_dossier_missing_true_when_no_dossier(self):
        from ji import _dossier_missing
        self.assertTrue(_dossier_missing("aaaaaaaaaaaaaaaa"))

    def test_dossier_missing_false_when_dossier_exists(self):
        from ji import _dossier_missing
        self._dossier("aaaaaaaaaaaaaaaa", [{"label": "Country", "kind": "verified"}])
        self.assertFalse(_dossier_missing("aaaaaaaaaaaaaaaa"))


class DriftReporting(_RiskFixture):
    """Finding #6: DB-tailored job with no dossier = cross-store drift. ji
    status must REPORT it (DRIFT line) without re-bucketing READY/HOLD."""

    def _db(self):
        tmp = tempfile.mkdtemp()
        import lib.db.schema as schema
        schema._conn = None
        schema.DB_PATH = os.path.join(tmp, "drift.db")
        schema.DB_DIR = tmp
        conn = schema.get_conn()
        self._tmp = tmp
        self._conn = conn
        return conn

    def _job(self, jid, stage, state="active"):
        self._conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, "
            "created_at, updated_at, scripts) "
            "VALUES (?,?,?,?,?,?,?,?, '[]')",
            (jid, f"https://x.com/{jid}", "Role", "Co", stage, state,
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        self._conn.commit()

    def tearDown(self):
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_status_reports_drift_for_dossierless_tailored(self):
        """A tailored-active job with no dossier is reported as DRIFT — the
        READY/HOLD claim for it is unverified."""
        import ji
        conn = self._db()
        self._job("aaaaaaaaaaaaaaaa", "tailored")  # no dossier
        self._job("bbbbbbbbbbbbbbbb", "tailored")
        self._dossier("bbbbbbbbbbbbbbbb", [{"label": "Country", "kind": "verified"}])
        with patch("lib.config.RESULTS_DIR", self._results), \
             patch.object(ji, "_T", None), \
             patch("ji._risk_unverified", return_value=[]):
            ji._imports()
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                ji.cmd_status()
            out = buf.getvalue()
        self.assertIn("DRIFT: 1", out)
        self.assertIn("aaaaaaaaaa", out)

    def test_status_no_drift_when_all_have_dossiers(self):
        import ji
        conn = self._db()
        self._job("aaaaaaaaaaaaaaaa", "tailored")
        self._dossier("aaaaaaaaaaaaaaaa", [{"label": "Country", "kind": "verified"}])
        with patch("lib.config.RESULTS_DIR", self._results), \
             patch.object(ji, "_T", None), \
             patch("ji._risk_unverified", return_value=[]):
            ji._imports()
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                ji.cmd_status()
            out = buf.getvalue()
        self.assertNotIn("DRIFT:", out)


class AppliedSurface(unittest.TestCase):
    """`report.py applied` — post-submit confirmation + the unrecoverable
    suspect class (applied with no applied_at)."""

    def _setup(self):
        tmp = tempfile.mkdtemp()
        import lib.db.schema as schema
        schema._conn = None
        schema.DB_PATH = os.path.join(tmp, "applied.db")
        schema.DB_DIR = tmp
        conn = schema.get_conn()
        self._tmp = tmp
        self._conn = conn
        return conn

    def tearDown(self):
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _applied(self, jid, applied_at="2026-01-01T00:00:00"):
        self._conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, "
            "applied_at, created_at, updated_at, scripts) "
            "VALUES (?,?,?,?,?,?,?,?,?, '[]')",
            (jid, f"https://x.com/{jid}", "Role", "Co", "applied", "active",
             applied_at, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        self._conn.commit()

    def _stderr(self, fn, *args, **kwargs):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            fn(*args, **kwargs)
        return buf.getvalue()

    def test_applied_none(self):
        from lib.report import cmd_applied
        self._setup()
        err = self._stderr(cmd_applied)
        self.assertIn("APPLIED: none", err)

    def test_suspects_flags_missing_applied_at(self):
        """The unrecoverable class: applied with NO applied_at must be flagged
        for portal verification — never silently trusted."""
        from lib.report import cmd_applied
        self._setup()
        self._applied("aaaaaaaaaaaaaaaa", applied_at=None)
        with patch("lib.report._applied_confirmed", return_value={}):
            err = self._stderr(cmd_applied, suspects_only=True)
        self.assertIn("APPLIED SUSPECTS: 1", err)
        self.assertIn("aaaaaaaaaa", err)

    def test_unconfirmed_lists_missing_confirmation(self):
        from lib.report import cmd_applied
        self._setup()
        self._applied("aaaaaaaaaaaaaaaa")
        with patch("lib.report._applied_confirmed", return_value={}):
            err = self._stderr(cmd_applied, unconfirmed_only=True)
        self.assertIn("aaaaaaaaaa", err)


class CompareHandoffsBranches(unittest.TestCase):
    """The regression-diff branches: regressed / improved / still_failed."""

    def test_improved(self):
        from lib.report import compare_handoffs
        new = {"summary": {"filled": 1},
               "fields": [{"label": "Email", "outcome": "filled"}]}
        old = {"summary": {"filled": 0},
               "fields": [{"label": "Email", "outcome": "no_answer"}]}
        d = compare_handoffs(new, old)
        self.assertIn("Email", d["improved"])
        self.assertEqual(d["regressed"], [])
        self.assertEqual(d["filled_before"], 0)
        self.assertEqual(d["filled_now"], 1)

    def test_still_failed(self):
        from lib.report import compare_handoffs
        new = {"summary": {"filled": 0},
               "fields": [{"label": "Salary", "outcome": "no_answer"}]}
        old = {"summary": {"filled": 0},
               "fields": [{"label": "Salary", "outcome": "rejected_by_form"}]}
        d = compare_handoffs(new, old)
        self.assertIn("Salary", d["still_failed"])
        self.assertEqual(d["improved"], [])
        self.assertEqual(d["regressed"], [])

    def test_field_gone_counts_as_regressed(self):
        """A field present in old but absent in new = regressed (was filled,
        now not visible)."""
        from lib.report import compare_handoffs
        new = {"summary": {"filled": 0}, "fields": []}
        old = {"summary": {"filled": 1},
               "fields": [{"label": "Email", "outcome": "filled"}]}
        d = compare_handoffs(new, old)
        self.assertEqual(d["regressed"], [("Email", "-")])


class JiSupersetDispatch(unittest.TestCase):
    """`ji` is the ONE surface (SURFACE_AUDIT v2): every stage engine is
    reachable as `ji <stage> <verb>`. The namespace disambiguates verbs that
    collide across engines (flag/undo/retry/reject) — a raw forward, verbatim."""

    def _main(self, *argv):
        import ji
        import sys as _sys
        old = _sys.argv
        _sys.argv = ["ji.py"] + list(argv)
        try:
            with patch.object(ji, "_run", return_value=0) as run:
                rc = ji.main()
        finally:
            _sys.argv = old
        return rc, run

    def test_reach_forwards_to_reach_engine(self):
        rc, run = self._main("reach", "email", "aaaaaaaaaaaaaaaa",
                             "--contact", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args[0][0], "reach.py")
        self.assertEqual(run.call_args[0][1:],
                         ("email", "aaaaaaaaaaaaaaaa", "--contact", "1"))

    def test_tailor_is_stage_forwarded(self):
        """`ji tailor <verb> ...` forwards VERBATIM to tailor.py via the stage
        namespace — `ji tailor admit <jid>` must reach tailor.py's admit, and
        `ji tailor --auto` must reach tailor.py --auto. Fix #1: the native
        branch that swallowed the verb is gone."""
        import ji
        import sys as _sys
        old = _sys.argv
        _sys.argv = ["ji.py", "tailor", "admit", "aaaaaaaaaaaaaaaa"]
        try:
            with patch.object(ji, "_run", return_value=0) as run:
                rc = ji.main()
        finally:
            _sys.argv = old
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args[0][0], "tailor.py")
        self.assertEqual(run.call_args[0][1:],
                         ("admit", "aaaaaaaaaaaaaaaa"))

    def test_tailor_auto_forwards(self):
        import ji
        import sys as _sys
        old = _sys.argv
        _sys.argv = ["ji.py", "tailor", "--auto"]
        try:
            with patch.object(ji, "_run", return_value=0) as run:
                ji.main()
        finally:
            _sys.argv = old
        self.assertEqual(run.call_args[0][1:], ("--auto",))

    def test_enrich_forwards_flag(self):
        """`ji enrich flag` must reach enrich.py — NOT apply.py's flag."""
        rc, run = self._main("enrich", "flag", "aaaaaaaaaaaaaaaa")
        self.assertEqual(run.call_args[0][0], "enrich.py")

    def test_stage_emails_forwards_days(self):
        rc, run = self._main("stage_emails", "--days", "30")
        self.assertEqual(run.call_args[0][0], "stage_emails.py")
        self.assertEqual(run.call_args[0][1:], ("--days", "30"))

    def test_extract_forwards_admit(self):
        rc, run = self._main("extract", "admit", "--category", "tech",
                             "aaaaaaaaaaaaaaaa")
        self.assertEqual(run.call_args[0][0], "extract.py")


if __name__ == "__main__":
    unittest.main()
