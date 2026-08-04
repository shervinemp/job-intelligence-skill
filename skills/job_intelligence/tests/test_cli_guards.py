"""Unit tests for CLI guard rails:
- extract.py/enrich.py reject warn on unknown or truncated jids (silent
  no-ops burned the fleet twice — the warning is the guard).
- report.py pending lists the extraction queue for triage.
"""
import io, os, sys, unittest, tempfile, shutil, contextlib
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lib.db.schema as schema
import extract
import enrich
import lib.report as report_mod


class _TempDBMixin:
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        schema._conn = None
        schema.DB_PATH = os.path.join(self.tmpdir, "test.db")
        schema.DB_DIR = self.tmpdir
        self.conn = schema.get_conn()

    def tearDown(self):
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _insert(self, jid, title="Engineer", company="Acme", stage="extracted",
                state="active", category=None):
        self.conn.execute(
            "INSERT INTO jobs (id, url, title, company, stage, state, category, "
            "created_at, updated_at, scripts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')",
            (jid, f"https://example.com/{jid}", title, company, stage, state,
             category, datetime.now().isoformat(), datetime.now().isoformat()),
        )
        self.conn.commit()

    def _stderr_of(self, fn, *args):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            fn(*args)
        return buf.getvalue()

    def _stdout_of(self, fn, *args, **kwargs):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*args, **kwargs)
        return buf.getvalue()


class ExtractRejectGuards(_TempDBMixin, unittest.TestCase):
    def test_reject_unknown_jid_warns(self):
        self._insert("aaaaaaaaaaaaaaaa")
        err = self._stderr_of(extract.cmd_reject, "bbbbbbbbbbbbbbbb")
        self.assertIn("WARN", err)
        row = self.conn.execute(
            "SELECT state FROM jobs WHERE id='aaaaaaaaaaaaaaaa'").fetchone()
        self.assertEqual(row[0], "active")

    def test_reject_truncated_jid_warns(self):
        self._insert("aaaaaaaaaaaaaaaa")
        err = self._stderr_of(extract.cmd_reject, "aaaaaaaa")
        self.assertIn("WARN", err)
        self.assertNotIn("REJECT:1", err)

    def test_reject_valid_jid_flips_state(self):
        self._insert("aaaaaaaaaaaaaaaa")
        err = self._stderr_of(extract.cmd_reject, "aaaaaaaaaaaaaaaa")
        self.assertIn("REJECT:1", err)
        row = self.conn.execute(
            "SELECT state FROM jobs WHERE id='aaaaaaaaaaaaaaaa'").fetchone()
        self.assertEqual(row[0], "rejected")

    def test_reject_mixed_valid_and_unknown(self):
        self._insert("aaaaaaaaaaaaaaaa")
        err = self._stderr_of(extract.cmd_reject, "aaaaaaaaaaaaaaaa", "nope")
        self.assertIn("REJECT:1", err)
        self.assertIn("WARN", err)


class EnrichRejectGuards(_TempDBMixin, unittest.TestCase):
    def test_reject_unknown_jid_warns(self):
        self._insert("aaaaaaaaaaaaaaaa")
        err = self._stderr_of(enrich.cmd_reject, "bbbbbbbbbbbbbbbb")
        self.assertIn("WARN", err)

    def test_reject_valid_jid_flips_state(self):
        self._insert("aaaaaaaaaaaaaaaa")
        err = self._stderr_of(enrich.cmd_reject, "aaaaaaaaaaaaaaaa")
        self.assertIn("REJECT:1", err)
        row = self.conn.execute(
            "SELECT state FROM jobs WHERE id='aaaaaaaaaaaaaaaa'").fetchone()
        self.assertEqual(row[0], "rejected")


class ReportPending(_TempDBMixin, unittest.TestCase):
    def test_pending_lists_extracted_queue(self):
        self._insert("aaaaaaaaaaaaaaaa", title="Engineer", company="Acme",
                     category="tech")
        self._insert("bbbbbbbbbbbbbbbb", title="Manager", company="Beta",
                     stage="described")
        out = self._stdout_of(report_mod.cmd_pending)
        self.assertIn("aaaaaaaaaaaa", out)
        self.assertIn("tech", out)
        self.assertNotIn("bbbbbbbbbbbb", out)

    def test_pending_stage_filter(self):
        self._insert("aaaaaaaaaaaaaaaa", title="Engineer", company="Acme")
        self._insert("bbbbbbbbbbbbbbbb", title="Manager", company="Beta",
                     stage="described")
        out = self._stdout_of(report_mod.cmd_pending, stage="described")
        self.assertIn("bbbbbbbbbbbb", out)
        self.assertNotIn("aaaaaaaaaaaa", out)

    def test_pending_empty_queue(self):
        out = self._stdout_of(report_mod.cmd_pending)
        self.assertIn("queue clear", out)


if __name__ == "__main__":
    unittest.main()
