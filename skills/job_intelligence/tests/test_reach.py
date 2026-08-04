"""Tests for reach.py and lib/contacts/discover.py — contact discovery helpers, keyword building, URL logic."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.contacts.discover import _build_team_keywords
from lib.db import get_conn
from reach import (
    cmd_discover, cmd_discover_all, cmd_list, cmd_email, cmd_message, cmd_connect,
    cmd_status, cmd_retry, cmd_undo, cmd_update,
)


class BuildTeamKeywords(unittest.TestCase):
    def test_team_name_produces_keywords(self):
        kws = _build_team_keywords("AI/ML", "")
        self.assertIn("ai", kws)
        self.assertIn("ml", kws)

    def test_team_name_filters_stopwords(self):
        kws = _build_team_keywords("Product and Engineering", "")
        self.assertNotIn("and", kws)
        self.assertNotIn("the", kws)

    def test_team_name_multi_word(self):
        kws = _build_team_keywords("Data Science & Analytics", "")
        self.assertIn("data", kws)
        self.assertIn("science", kws)
        self.assertNotIn("&", kws)

    def test_job_title_fallback_engineer(self):
        kws = _build_team_keywords("", "Senior Software Engineer")
        self.assertTrue(any(k in kws for k in ["engineering", "software", "development"]))

    def test_job_title_fallback_scientist(self):
        kws = _build_team_keywords("", "Machine Learning Scientist")
        self.assertTrue(any(k in kws for k in ["science", "data", "research", "ai", "ml"]))

    def test_job_title_fallback_designer(self):
        kws = _build_team_keywords("", "Product Designer")
        self.assertTrue(any(k in kws for k in ["design", "ux", "ui"]))

    def test_no_team_no_title(self):
        kws = _build_team_keywords("", "")
        self.assertEqual(kws, [])

    def test_no_team_job_title_none(self):
        kws = _build_team_keywords("", "Platform Engineer")
        self.assertTrue(any(k in kws for k in ["engineering", "software", "development"]))


class CommandFunctionsExist(unittest.TestCase):
    def test_cmd_discover_exists(self):
        self.assertTrue(callable(cmd_discover))

    def test_cmd_list_exists(self):
        self.assertTrue(callable(cmd_list))

    def test_cmd_email_exists(self):
        self.assertTrue(callable(cmd_email))

    def test_cmd_message_exists(self):
        self.assertTrue(callable(cmd_message))

    def test_cmd_connect_exists(self):
        self.assertTrue(callable(cmd_connect))

    def test_cmd_status_exists(self):
        self.assertTrue(callable(cmd_status))

    def test_cmd_retry_exists(self):
        self.assertTrue(callable(cmd_retry))

    def test_cmd_undo_exists(self):
        self.assertTrue(callable(cmd_undo))

    def test_cmd_update_exists(self):
        self.assertTrue(callable(cmd_update))

    def test_cmd_discover_all_exists(self):
        self.assertTrue(callable(cmd_discover_all))

    def test_cmd_email_accepts_force_kwarg(self):
        import inspect
        sig = inspect.signature(cmd_email)
        self.assertIn("force", sig.parameters)
        self.assertEqual(sig.parameters["force"].default, False)

    def test_cmd_message_accepts_force_kwarg(self):
        import inspect
        sig = inspect.signature(cmd_message)
        self.assertIn("force", sig.parameters)
        self.assertEqual(sig.parameters["force"].default, False)


class AttemptHelpersExist(unittest.TestCase):
    def test_attempt_add_importable(self):
        from lib.db.contacts import attempt_add, attempt_list
        self.assertTrue(callable(attempt_add))
        self.assertTrue(callable(attempt_list))

    def test_attempt_exported_from_lib_db(self):
        from lib.db import attempt_add, attempt_list
        self.assertTrue(callable(attempt_add))
        self.assertTrue(callable(attempt_list))


class _TempDBMixin:
    def setUp(self):
        import tempfile
        import shutil
        import io
        import contextlib
        from datetime import datetime
        self._tmp = tempfile.mkdtemp()
        import lib.db.schema as schema
        schema._conn = None
        schema.DB_PATH = os.path.join(self._tmp, "test.db")
        schema.DB_DIR = self._tmp
        self.conn = schema.get_conn()
        self._io = io
        self._contextlib = contextlib
        self._datetime = datetime

    def tearDown(self):
        import shutil
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stderr_of(self, fn, *args, **kwargs):
        buf = self._io.StringIO()
        with self._contextlib.redirect_stderr(buf):
            fn(*args, **kwargs)
        return buf.getvalue()


class CrossJobGuard(_TempDBMixin, unittest.TestCase):
    """The one-shot guards are per contact row; the same person on two jobs
    must still only be contacted once (repeat outreach gives away the
    automation)."""

    URL = "https://example.com/in/not-a-real-person-xyz"

    def _job(self, jid):
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, "
            "created_at, updated_at, scripts) "
            "VALUES (?, ?, 'Role', 'Co', 'tailored', 'active', ?, ?, '[]')",
            (jid, f"https://example.com/{jid}",
             self._datetime.now().isoformat(), self._datetime.now().isoformat()),
        )
        self.conn.commit()

    def _contact(self, jid, url=URL, reached=0, email=""):
        self._job(jid)
        cur = self.conn.execute(
            "INSERT INTO contacts (job_id, name, linkedin_url, email, reached_out, source) "
            "VALUES (?, 'Keyvan K', ?, ?, ?, 'my_connection')",
            (jid, url, email, reached),
        )
        self.conn.commit()
        return cur.lastrowid

    def _attempt(self, contact_id, channel="linkedin_message", status="pending"):
        self.conn.execute(
            "INSERT INTO contact_attempts (contact_id, channel, direction, status) "
            "VALUES (?, ?, 'outbound', ?)",
            (contact_id, channel, status),
        )
        self.conn.commit()

    def test_message_blocked_when_person_reached_on_other_job(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("bbbbbbbbbbbbbbbb", reached=0)
        err = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1)
        self.assertIn("ALREADY_REACHED", err)

    def test_message_blocked_via_uncertain_attempt(self):
        """The uncertain path logs an attempt with status=pending but never
        sets reached_out — the attempt row must still block."""
        cid_a = self._contact("aaaaaaaaaaaaaaaa")
        self._contact("bbbbbbbbbbbbbbbb")
        self._attempt(cid_a)
        err = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1)
        self.assertIn("ALREADY_REACHED", err)

    def test_message_allowed_when_other_job_untouched(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=0)
        self._contact("bbbbbbbbbbbbbbbb", reached=0)
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect", return_value=(None, None)) as m:
            err = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1)
            m.assert_called()
        self.assertNotIn("ALREADY_REACHED", err)
        self.assertIn("Could not connect", err)

    def test_email_blocked_cross_job(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("bbbbbbbbbbbbbbbb", reached=0, email="k@lyft.com")
        err = self._stderr_of(cmd_email, "bbbbbbbbbbbbbbbb", 1)
        self.assertIn("ALREADY_REACHED", err)

    def test_connect_blocked_cross_job(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("bbbbbbbbbbbbbbbb", reached=0)
        err = self._stderr_of(cmd_connect, "bbbbbbbbbbbbbbbb", 1)
        self.assertIn("ALREADY_REACHED", err)

    def test_force_bypasses_guard(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("bbbbbbbbbbbbbbbb", reached=0)
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect", return_value=(None, None)) as m:
            err = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1, force=True)
            m.assert_called()
        self.assertNotIn("ALREADY_REACHED", err)
        self.assertIn("Could not connect", err)

    def test_connect_then_message_same_row_funnel_allowed(self):
        """A connect request and later DM on the SAME row is a natural
        human flow — the guard must not block it."""
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_connect", status="sent")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect", return_value=(None, None)) as m:
            err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
            m.assert_called()
        self.assertNotIn("ALREADY_REACHED", err)
        self.assertIn("Could not connect", err)

    def test_duplicate_row_same_job_blocked(self):
        """Same person twice within one job must still only get one message."""
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("aaaaaaaaaaaaaaaa", reached=0)
        err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 2)
        self.assertIn("ALREADY_REACHED", err)

    def test_undo_clears_attempts_and_unblocks(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("bbbbbbbbbbbbbbbb", reached=0)
        cid_a = self.conn.execute(
            "SELECT id FROM contacts WHERE job_id='aaaaaaaaaaaaaaaa'").fetchone()[0]
        self._attempt(cid_a, channel="linkedin_message", status="sent")
        cmd_undo("aaaaaaaaaaaaaaaa")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect", return_value=(None, None)) as m:
            err = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1)
            m.assert_called()
        self.assertNotIn("ALREADY_REACHED", err)
        self.assertIn("Could not connect", err)

    def test_uncertain_pending_blocks_resend(self):
        """Unconfirmed send (status=pending) must never silently re-send."""
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_message", status="pending")
        err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
        self.assertIn("UNCERTAIN_SEND", err)

    def test_uncertain_pending_force_bypasses(self):
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_message", status="pending")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect", return_value=(None, None)) as m:
            err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1, force=True)
            m.assert_called()
        self.assertNotIn("UNCERTAIN_SEND", err)
        self.assertIn("Could not connect", err)

    def test_failed_attempt_does_not_block(self):
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_message", status="failed")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect", return_value=(None, None)) as m:
            err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
            m.assert_called()
        self.assertNotIn("UNCERTAIN_SEND", err)
        self.assertNotIn("ALREADY_REACHED", err)
        self.assertIn("Could not connect", err)

    # ------------------------------------------------------------------
    # Transmission sandbox: JI_TESTS (set by tests/conftest.py) must make
    # the send paths refuse at the LAST moment, even with a live browser.
    # ------------------------------------------------------------------

    def test_message_refused_by_sandbox_even_with_live_context(self):
        self._contact("aaaaaaaaaaaaaaaa")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect",
                        return_value=(object(), mock.Mock())) as m:
            err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
            m.assert_called()
        self.assertIn("TEST_SANDBOX", err)
        self.assertNotIn("MESSAGE_SENT", err)

    def test_email_refused_by_sandbox(self):
        self._contact("aaaaaaaaaaaaaaaa", email="x@example.com")
        err = self._stderr_of(cmd_email, "aaaaaaaaaaaaaaaa", 1)
        self.assertIn("TEST_SANDBOX", err)
        self.assertNotIn("EMAIL_SENT", err)

    def test_connect_refused_by_sandbox_even_with_live_context(self):
        self._contact("aaaaaaaaaaaaaaaa")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect",
                        return_value=(object(), mock.Mock())) as m:
            err = self._stderr_of(cmd_connect, "aaaaaaaaaaaaaaaa", 1)
            m.assert_called()
        self.assertIn("TEST_SANDBOX", err)
        self.assertNotIn("CONNECT_SENT", err)

    def test_deep_sandbox_in_messaging_module(self):
        from unittest import mock
        from lib.linkedin_messaging import send_message, send_connect_request
        ctx = mock.Mock()
        res = send_message(ctx, self.URL, "hello", name="X")
        self.assertEqual(res["status"], "sandbox_refused")
        res2 = send_connect_request(ctx, self.URL)
        self.assertEqual(res2["status"], "sandbox_refused")


if __name__ == "__main__":
    unittest.main()
