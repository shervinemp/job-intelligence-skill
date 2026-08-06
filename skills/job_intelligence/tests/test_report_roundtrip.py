"""test_report_roundtrip.py — the dossier readers consume what the writers emit.

The orchestrator reads the evidence through `report.py handoff|diff|audit`.
These readers must round-trip the dossiers the fill/submit writers produce —
a reader that can't parse its own writer's output is an invisible break (the
orchestrator gets "no handoff" for a job that WAS filled). This file pins that
round-trip with temp RESULTS_DIR + temp DB.

Mock vs stub:
- Stub: temp RESULTS_DIR (with handoff.json + handoffs/ history + apply_audit.jsonl),
  temp DB for the DB-driven commands. Nothing here needs a browser or an LLM.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _ReportFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._results = os.path.join(self._tmp, "results")
        os.makedirs(self._results, exist_ok=True)
        # lib/report.py binds RESULTS_DIR at import — patch both it and config.
        import lib.report as R
        self._r = R
        self._results_patchers = [
            patch.object(R, "RESULTS_DIR", self._results),
            patch("lib.config.RESULTS_DIR", self._results),
        ]
        for p in self._results_patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._results_patchers])

    def tearDown(self):
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stderr(self, fn, *args, **kwargs):
        buf = io.StringIO()
        out = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(out):
            fn(*args, **kwargs)
        return buf.getvalue() + out.getvalue()

    def _job_dir(self, jid):
        d = os.path.join(self._results, str(jid))
        os.makedirs(d, exist_ok=True)
        return d

    def _write_dossier(self, jid, fields, summary=None, mode="shadow", error=""):
        """Writer-shaped dossier: the same dict _write_handoff emits."""
        d = self._job_dir(jid)
        h = {
            "jid": jid, "mode": mode, "ts": "2026-08-05T00:00:00",
            "error": error, "summary": summary or {"filled": 0},
            "fields": fields,
        }
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump(h, f)
        # history (what load_handoffs reads) — timestamped copies
        hist = os.path.join(d, "handoffs")
        os.makedirs(hist, exist_ok=True)
        with open(os.path.join(hist, "2026-08-05T000000.json"),
                  "w", encoding="utf-8") as f:
            json.dump(h, f)
        return h


class HandoffRoundTrip(_ReportFixture):
    def test_cmd_handoff_renders_writer_output(self):
        from lib.report import cmd_handoff
        jid = "aaaaaaaaaaaaaaaa"
        self._write_dossier(jid, [
            {"label": "Email", "answer": "a@b.com", "outcome": "filled",
             "kind": "verified"},
            {"label": "Salary", "outcome": "no_answer", "kind": "needs_data"},
        ], summary={"filled": 1, "failed": 1})
        err = self._stderr(cmd_handoff, jid)
        self.assertIn(f"HANDOFF {jid}", err)
        self.assertIn("filled=1", err)

    def test_cmd_handoff_missing_says_no_handoff(self):
        from lib.report import cmd_handoff
        err = self._stderr(cmd_handoff, "bbbbbbbbbbbbbbbb")
        self.assertIn("No handoff", err)


class DiffRoundTrip(_ReportFixture):
    def test_cmd_diff_detects_regression(self):
        """Two runs: Email was filled, now needs_data → REGRESSED."""
        from lib.report import cmd_diff
        jid = "cccccccccccccccc"
        d = self._job_dir(jid)
        hist = os.path.join(d, "handoffs")
        os.makedirs(hist, exist_ok=True)
        old = {"ts": "1", "summary": {"filled": 1},
               "fields": [{"label": "Email", "outcome": "filled"}]}
        new = {"ts": "2", "summary": {"filled": 0},
               "fields": [{"label": "Email", "outcome": "no_answer",
                           "kind": "needs_data"}]}
        with open(os.path.join(hist, "2.json"), "w", encoding="utf-8") as f:
            json.dump(new, f)
        with open(os.path.join(hist, "1.json"), "w", encoding="utf-8") as f:
            json.dump(old, f)
        err = self._stderr(cmd_diff, jid)
        self.assertIn("REGRESSED", err)
        self.assertIn("Email", err)

    def test_cmd_diff_needs_two_runs(self):
        from lib.report import cmd_diff
        jid = "dddddddddddddddd"
        d = self._job_dir(jid)
        os.makedirs(os.path.join(d, "handoffs"), exist_ok=True)
        err = self._stderr(cmd_diff, jid)
        self.assertIn("Need ≥2 fill runs", err)


class AuditRoundTrip(_ReportFixture):
    def test_cmd_audit_reads_writer_log(self):
        from lib.report import cmd_audit
        jid = "eeeeeeeeeeeeeeee"
        d = self._job_dir(jid)
        with open(os.path.join(d, "apply_audit.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "field", "label": "City",
                                "filled": False, "reason": "fill_exception",
                                "method": "text"}) + "\n")
        err = self._stderr(cmd_audit, jid)
        self.assertIn("FAIL", err)
        self.assertIn("City", err)

    def test_cmd_audit_missing_says_no_log(self):
        from lib.report import cmd_audit
        err = self._stderr(cmd_audit, "ffffffffffffffff")
        self.assertIn("No audit log", err)


class DBDrivenReport(_ReportFixture):
    """The DB-driven readers (stats/candidates/archive/applied-confirm) — no
    browser, no LLM; they are testable against a temp schema DB."""

    def _seed(self):
        import lib.db.schema as schema
        schema._conn = None
        schema.DB_PATH = os.path.join(self._tmp, "report.db")
        schema.DB_DIR = self._tmp
        conn = schema.get_conn()
        for i, (jid, stage, state) in enumerate([
            ("1000000000000001", "tailored", "active"),
            ("1000000000000002", "tailored", "active"),
            ("1000000000000003", "applied", "active"),
        ]):
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, "
                "state, created_at, updated_at, scripts) "
                "VALUES (?,?,?,?,?,?,?,?, '[]')",
                (jid, f"https://www.linkedin.com/jobs/view/{i}", "Role", "Co",
                 stage, state, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
            )
        conn.commit()
        return conn

    def test_cmd_stats_shows_matrix(self):
        from lib.report import cmd_stats
        self._seed()
        err = self._stderr(cmd_stats)
        self.assertIn("tailored", err)

    def test_cmd_candidates_lists_tailored_active(self):
        from lib.report import cmd_candidates
        self._seed()
        err = self._stderr(cmd_candidates)
        # JIDs are truncated to 14 chars in the candidates output
        self.assertIn("10000000000000", err)
        self.assertIn("EASY_APPLY", err)
        self.assertIn("ALREADY APPLIED (1)", err)  # the applied job excluded

    def test_cmd_applied_confirm_records_confirmation(self):
        import json
        from lib.report import cmd_applied_confirm
        self._seed()
        conf_path = os.path.join(self._tmp, "applied_confirmations.json")
        with patch("lib.config.STATE_DIR", self._tmp), \
             patch("lib.g2.linkedin_tracker_confirm",
                   return_value=(True, "found")):
            err = self._stderr(cmd_applied_confirm, "1000000000000001")
        self.assertIn("APPLIED", err)
        data = json.load(open(conf_path, encoding="utf-8"))
        self.assertIn("1000000000000001", data)

    def test_applied_confirm_linkedin_gate_fails_closed(self):
        """G2 enforcement: a LinkedIn job whose tracker check comes back NOT
        confirmed must NOT be marked confirmed — the flag must not be flipped
        on the pipeline's own belief alone."""
        import json
        from lib.report import cmd_applied_confirm
        self._seed()
        conf_path = os.path.join(self._tmp, "applied_confirmations.json")
        with patch("lib.config.STATE_DIR", self._tmp), \
             patch("lib.g2.linkedin_tracker_confirm", return_value=(False, "not found")):
            err = self._stderr(cmd_applied_confirm, "1000000000000001")
        self.assertIn("NOT CONFIRMED", err)
        self.assertFalse(os.path.exists(conf_path))

    def test_applied_confirm_linkedin_gate_confirms_on_match(self):
        """G2: a LinkedIn job confirmed present on the tracker IS recorded."""
        import json
        from lib.report import cmd_applied_confirm
        self._seed()
        conf_path = os.path.join(self._tmp, "applied_confirmations.json")
        with patch("lib.config.STATE_DIR", self._tmp), \
             patch("lib.g2.linkedin_tracker_confirm",
                   return_value=(True, "found 'Role' in the Applied tracker")):
            err = self._stderr(cmd_applied_confirm, "1000000000000001")
        self.assertIn("G2 confirmed", err)
        data = json.load(open(conf_path, encoding="utf-8"))
        self.assertIn("1000000000000001", data)

    def test_applied_confirm_manual_skips_gate(self):
        """--manual records the confirmation without the tracker check
        (external ATS where the operator verified via email/portal)."""
        import json
        from lib.report import cmd_applied_confirm
        self._seed()
        conf_path = os.path.join(self._tmp, "applied_confirmations.json")
        with patch("lib.config.STATE_DIR", self._tmp), \
             patch("lib.g2.linkedin_tracker_confirm") as g2m:
            err = self._stderr(cmd_applied_confirm, "1000000000000001", True)
        g2m.assert_not_called()
        data = json.load(open(conf_path, encoding="utf-8"))
        self.assertIn("1000000000000001", data)

    def test_applied_confirm_non_linkedin_requires_manual(self):
        """G2: a non-LinkedIn (external ATS) submission must NOT be confirmed
        without --manual — there is no tracker check, so an auto-confirm would
        be the blind-submit class again."""
        import json
        from lib.report import cmd_applied_confirm
        from lib.db import get_conn
        conn = self._seed()
        # reseed one job with an external ATS URL
        conn.execute(
            "UPDATE jobs SET url=?, title='X', company='Y' WHERE id=?",
            ("https://autodesk.wd1.myworkdayjobs.com/job/1", "1000000000000001"))
        conn.commit()
        conf_path = os.path.join(self._tmp, "applied_confirmations.json")
        with patch("lib.config.STATE_DIR", self._tmp), \
             patch("lib.g2.linkedin_tracker_confirm") as g2m:
            err = self._stderr(cmd_applied_confirm, "1000000000000001")
        g2m.assert_not_called()
        self.assertIn("non-LinkedIn", err)
        self.assertFalse(os.path.exists(conf_path))
        # with --manual it records
        with patch("lib.config.STATE_DIR", self._tmp):
            err2 = self._stderr(cmd_applied_confirm, "1000000000000001", True)
        data = json.load(open(conf_path, encoding="utf-8"))
        self.assertIn("1000000000000001", data)


class ObserveRoundTrip(_ReportFixture):
    """`report.py observe` — the LLM observation brief: dossier + regression
    diff in one view. It composes the two surfaces; the composition must not
    lose either half."""

    def test_observe_renders_dossier_when_one_run(self):
        from lib.report import cmd_observe
        jid = "aaaaaaaaaaaaaaaa"
        self._write_dossier(jid, [
            {"label": "Email", "answer": "a@b.com", "outcome": "filled",
             "kind": "verified"},
        ], summary={"filled": 1})
        err = self._stderr(cmd_observe, jid)
        self.assertIn(f"HANDOFF {jid}", err)
        self.assertIn("Email", err)

    def test_observe_adds_diff_when_two_runs(self):
        """Two runs → observe shows BOTH the current dossier and the
        regression diff — the orchestrator's full observation brief."""
        from lib.report import cmd_observe
        jid = "bbbbbbbbbbbbbbbb"
        d = self._job_dir(jid)
        # current dossier (what cmd_handoff reads)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "ts": "2", "summary": {"filled": 0},
                       "fields": [{"label": "Email", "outcome": "no_answer"}]}, f)
        hist = os.path.join(d, "handoffs")
        os.makedirs(hist, exist_ok=True)
        for ts, summary, outcome in [("1", {"filled": 1}, "filled"),
                                     ("2", {"filled": 0}, "no_answer")]:
            with open(os.path.join(hist, f"{ts}.json"), "w", encoding="utf-8") as f:
                json.dump({"ts": ts, "summary": summary,
                           "fields": [{"label": "Email", "outcome": outcome}]}, f)
        err = self._stderr(cmd_observe, jid)
        self.assertIn("HANDOFF", err)
        self.assertIn("DIFF", err)
        self.assertIn("REGRESSED", err)


class SessionTimeline(_ReportFixture):
    """`report.py session` — the obs event timeline the orchestrator reads to
    reconstruct what happened in a run."""

    def test_session_empty_is_graceful(self):
        from lib.report import cmd_session
        import lib.automation.obs as obs
        with patch("lib.automation.obs.load", return_value=[]):
            err = self._stderr(cmd_session)
        self.assertIn("No session events found", err)

    def test_session_renders_events(self):
        from lib.report import cmd_session
        import lib.automation.obs as obs
        with patch("lib.automation.obs.load", return_value=[
                {"run_id": "r1", "ts": "1", "actor": "fill",
                 "action": "filled_field", "jid": "x", "target": "Email",
                 "outcome": "ok", "detail": "verified"},
        ]):
            err = self._stderr(cmd_session)
        self.assertIn("filled_field", err)
        self.assertIn("Email", err)


if __name__ == "__main__":
    unittest.main()
