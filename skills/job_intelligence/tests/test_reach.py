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


class CommandSurface(unittest.TestCase):
    """The CLI surface is a contract: every documented command must exist
    and keep its safety-relevant kwargs. (These replaced a set of tests
    that only asserted callable(fn) — which cannot fail for any reason a
    reader would care about, and did not fail when the write path beneath
    every one of these commands raised AttributeError.)"""

    def test_every_subcommand_dispatches(self):
        import argparse
        import reach as _reach
        documented = {
            "discover", "list", "email", "message", "connect", "update",
            "attempts", "status", "retry", "undo", "help",
        }
        parser_src = _reach.main.__code__.co_consts
        # Cheap structural check: the module must expose a cmd_ for each.
        for name in documented - {"help"}:
            self.assertTrue(hasattr(_reach, f"cmd_{name}"),
                            f"reach.py documents '{name}' but has no cmd_{name}")

    def test_send_commands_all_accept_force(self):
        """--force is the documented one-shot override; if a send command
        loses it, the guard becomes unbypassable and operators improvise."""
        import inspect
        for fn in (cmd_email, cmd_message, cmd_connect):
            sig = inspect.signature(fn)
            self.assertIn("force", sig.parameters, f"{fn.__name__} lost --force")
            self.assertEqual(sig.parameters["force"].default, False,
                             f"{fn.__name__} defaults to force=True")


class PersonIdentity(unittest.TestCase):
    """Cross-job one-shot compares PEOPLE, not raw strings."""

    def test_url_variants_are_one_person(self):
        from reach import person_keys
        base = person_keys({"linkedin_url": "https://www.linkedin.com/in/carol"})
        for variant in (
            "https://www.linkedin.com/in/carol/",
            "http://linkedin.com/in/carol",
            "https://www.linkedin.com/in/Carol?miniProfileUrn=abc",
        ):
            self.assertTrue(base & person_keys({"linkedin_url": variant}),
                            f"{variant} should be the same person")

    def test_blank_fields_identify_nobody(self):
        """The bug: an empty linkedin_url matched every other empty one, so
        unrelated email-only contacts blocked each other."""
        from reach import person_keys
        self.assertEqual(person_keys({"linkedin_url": "", "email": ""}), set())
        a = person_keys({"linkedin_url": "", "email": "alice@x.com"})
        b = person_keys({"linkedin_url": "", "email": "bob@y.com"})
        self.assertFalse(a & b)

    def test_email_is_case_insensitive(self):
        from reach import person_keys
        self.assertTrue(person_keys({"email": "A@X.com"})
                        & person_keys({"email": "a@x.com"}))


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

    def _assert_reached_send_stage(self, err):
        """The command got PAST every one-shot guard and was stopped only by
        the transmission sandbox.

        The sandbox now refuses BEFORE opening a browser, so 'we passed the
        guards' reads as TEST_SANDBOX rather than the old 'Could not connect'
        (which required mocking chrome_manager just to fail). Asserting the
        absence of both guard messages is what actually pins the behaviour.
        """
        self.assertIn("TEST_SANDBOX", err)
        self.assertNotIn("ALREADY_REACHED", err)
        self.assertNotIn("UNCERTAIN_SEND", err)
        for banned in ("MESSAGE_SENT", "EMAIL_SENT", "CONNECT_SENT"):
            self.assertNotIn(banned, err)


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
        err = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1)
        self._assert_reached_send_stage(err)

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
        err = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1, force=True)
        self._assert_reached_send_stage(err)

    def test_connect_then_message_same_row_funnel_allowed(self):
        """A connect request and later DM on the SAME row is a natural
        human flow — the guard must not block it."""
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_connect", status="sent")
        err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
        self._assert_reached_send_stage(err)

    def test_duplicate_row_same_job_blocked(self):
        """Same person twice within one job must still only get one message."""
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("aaaaaaaaaaaaaaaa", reached=0)
        err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 2)
        self.assertIn("ALREADY_REACHED", err)

    def test_undo_refuses_without_confirm_when_outreach_exists(self):
        # undo deletes the evidence that a person was already contacted,
        # which disarms the cross-job guard. Deliberate act only.
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("bbbbbbbbbbbbbbbb", reached=0)
        cid_a = self.conn.execute(
            "SELECT id FROM contacts WHERE job_id='aaaaaaaaaaaaaaaa'").fetchone()[0]
        self._attempt(cid_a, channel="linkedin_message", status="sent")
        err = self._stderr_of(cmd_undo, "aaaaaaaaaaaaaaaa")
        self.assertIn("REFUSED", err)
        still = self.conn.execute(
            "SELECT COUNT(*) FROM contact_attempts").fetchone()[0]
        self.assertEqual(still, 1)
        err2 = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1)
        self.assertIn("ALREADY_REACHED", err2)

    def test_undo_with_confirm_clears_attempts_and_unblocks(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=1)
        self._contact("bbbbbbbbbbbbbbbb", reached=0)
        cid_a = self.conn.execute(
            "SELECT id FROM contacts WHERE job_id='aaaaaaaaaaaaaaaa'").fetchone()[0]
        self._attempt(cid_a, channel="linkedin_message", status="sent")
        err = self._stderr_of(cmd_undo, "aaaaaaaaaaaaaaaa", confirm=True)
        self.assertIn("WARNING", err)
        err2 = self._stderr_of(cmd_message, "bbbbbbbbbbbbbbbb", 1)
        self._assert_reached_send_stage(err2)

    def test_undo_without_outreach_needs_no_confirm(self):
        self._contact("aaaaaaaaaaaaaaaa", reached=0)
        err = self._stderr_of(cmd_undo, "aaaaaaaaaaaaaaaa")
        self.assertNotIn("REFUSED", err)
        self.assertIn("Undone", err)

    def test_uncertain_pending_blocks_resend(self):
        """Unconfirmed send (status=pending) must never silently re-send."""
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_message", status="pending")
        err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
        self.assertIn("UNCERTAIN_SEND", err)

    def test_uncertain_pending_force_bypasses(self):
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_message", status="pending")
        err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1, force=True)
        self._assert_reached_send_stage(err)

    def test_failed_attempt_does_not_block(self):
        cid = self._contact("aaaaaaaaaaaaaaaa")
        self._attempt(cid, channel="linkedin_message", status="failed")
        err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
        self._assert_reached_send_stage(err)

    # ------------------------------------------------------------------
    # Transmission sandbox: JI_TESTS (set by tests/conftest.py) must make
    # the send paths refuse at the LAST moment, even with a live browser.
    # ------------------------------------------------------------------

    def test_message_refused_by_sandbox_before_opening_a_browser(self):
        self._contact("aaaaaaaaaaaaaaaa")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect",
                        return_value=(object(), mock.Mock())) as m:
            err = self._stderr_of(cmd_message, "aaaaaaaaaaaaaaaa", 1)
            m.assert_not_called()
        self.assertIn("TEST_SANDBOX", err)
        self.assertNotIn("MESSAGE_SENT", err)

    def test_email_refused_by_sandbox(self):
        self._contact("aaaaaaaaaaaaaaaa", email="x@example.com")
        err = self._stderr_of(cmd_email, "aaaaaaaaaaaaaaaa", 1)
        self.assertIn("TEST_SANDBOX", err)
        self.assertNotIn("EMAIL_SENT", err)

    def test_connect_refused_by_sandbox_before_opening_a_browser(self):
        self._contact("aaaaaaaaaaaaaaaa")
        from unittest import mock
        with mock.patch("lib.chrome_manager.connect",
                        return_value=(object(), mock.Mock())) as m:
            err = self._stderr_of(cmd_connect, "aaaaaaaaaaaaaaaa", 1)
            m.assert_not_called()
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


class OutreachIsRecorded(_TempDBMixin, unittest.TestCase):
    """A send that is not recorded is a send that can happen twice.

    These execute the FULL path — guards, transport, flag write, attempt
    row, event row — with only the transport stubbed. The previous suite
    stopped at a guard or at the sandbox, so lib/db/contacts.py's
    `return c.lastrowid` (AttributeError: sqlite3.Connection has no
    lastrowid) went unnoticed through a 466-test run even though it
    crashed every reach command AFTER transmitting.
    """

    def setUp(self):
        super().setUp()
        import reach as _reach
        self.reach = _reach
        self._saved_tests_env = os.environ.pop("JI_TESTS", None)
        self.sent = []

    def tearDown(self):
        if self._saved_tests_env is not None:
            os.environ["JI_TESTS"] = self._saved_tests_env
        super().tearDown()

    def _contact(self, jid="aaaaaaaaaaaaaaaa", url="", email=""):
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, scripts) "
            "VALUES (?, 'https://example.com/j', 'Role', 'Co', 'tailored', 'active', '[]')",
            (jid,),
        )
        cur = self.conn.execute(
            "INSERT INTO contacts (job_id, name, linkedin_url, email) VALUES (?,?,?,?)",
            (jid, "Test Person", url, email),
        )
        self.conn.commit()
        return cur.lastrowid

    def _rows(self, table):
        return self.conn.execute(f"SELECT * FROM {table}").fetchall()

    def test_linkedin_message_records_attempt_flag_and_event(self):
        from unittest import mock
        self._contact(url="https://www.linkedin.com/in/test-person")
        with mock.patch.object(self.reach, "send_message",
                               return_value={"status": "sent",
                                             "conversation_url": "https://x/thread/1"}) as tx, \
             mock.patch("lib.chrome_manager.connect",
                        return_value=(mock.Mock(), mock.Mock())):
            err = self._stderr_of(self.reach.cmd_message, "aaaaaaaaaaaaaaaa", 1,
                                  body="hello")
            tx.assert_called_once()
        self.assertIn("MESSAGE_SENT", err)
        attempts = self._rows("contact_attempts")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["channel"], "linkedin_message")
        self.assertEqual(attempts[0]["status"], "sent")
        self.assertEqual(len(self._rows("events")), 1)
        c = self._rows("contacts")[0]
        self.assertEqual(c["message_sent"], 1)
        self.assertEqual(c["reached_out"], 1)

    def test_uncertain_message_records_pending_attempt_and_no_flag(self):
        """The uncertain path is the one that must never silently resend."""
        from unittest import mock
        self._contact(url="https://www.linkedin.com/in/test-person")
        with mock.patch.object(self.reach, "send_message",
                               return_value={"status": "uncertain",
                                             "detail": "unconfirmed"}), \
             mock.patch("lib.chrome_manager.connect",
                        return_value=(mock.Mock(), mock.Mock())):
            err = self._stderr_of(self.reach.cmd_message, "aaaaaaaaaaaaaaaa", 1,
                                  body="hello")
        self.assertIn("MESSAGE_UNCERTAIN", err)
        self.assertIn("--set-sent message", err)  # the hint that actually settles it
        attempts = self._rows("contact_attempts")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "pending")
        self.assertEqual(self._rows("contacts")[0]["message_sent"], 0)

    def test_connect_records_attempt_then_blocks_a_second_request(self):
        """The bug: connect had no same-row guard, so a re-run after a
        crash or an uncertain outcome sent a SECOND invitation."""
        from unittest import mock
        self._contact(url="https://www.linkedin.com/in/test-person")
        with mock.patch.object(self.reach, "send_connect_request",
                               side_effect=lambda ctx, u, note="": (
                                   self.sent.append(u) or {"status": "sent"})), \
             mock.patch("lib.chrome_manager.connect",
                        return_value=(mock.Mock(), mock.Mock())):
            err1 = self._stderr_of(self.reach.cmd_connect, "aaaaaaaaaaaaaaaa", 1)
            err2 = self._stderr_of(self.reach.cmd_connect, "aaaaaaaaaaaaaaaa", 1)
        self.assertIn("CONNECT_SENT", err1)
        self.assertIn("ALREADY_CONNECTED_REQUEST", err2)
        self.assertEqual(len(self.sent), 1, "a second invitation was transmitted")

    def test_email_records_attempt_flag_and_event(self):
        from unittest import mock
        import json as _json
        self._contact(email="person@example.com")
        fake = mock.Mock(returncode=0,
                         stdout=_json.dumps({"status": "sent",
                                             "message_id": "m1"}).encode(),
                         stderr=b"")
        with mock.patch.object(self.reach.subprocess, "run", return_value=fake) as run:
            err = self._stderr_of(self.reach.cmd_email, "aaaaaaaaaaaaaaaa", 1,
                                  body="hi")
            run.assert_called_once()
        self.assertIn("EMAIL_SENT", err)
        attempts = self._rows("contact_attempts")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "sent")
        self.assertEqual(len(self._rows("events")), 1)
        self.assertEqual(self._rows("contacts")[0]["email_sent"], 1)

    def test_failed_email_still_records_the_attempt(self):
        """Failures must be recorded too — an unrecorded failure is
        indistinguishable from 'never tried' on the next run."""
        from unittest import mock
        self._contact(email="person@example.com")
        fake = mock.Mock(returncode=1, stdout=b"", stderr=b"smtp exploded")
        with mock.patch.object(self.reach.subprocess, "run", return_value=fake):
            err = self._stderr_of(self.reach.cmd_email, "aaaaaaaaaaaaaaaa", 1,
                                  body="hi")
        self.assertIn("EMAIL_FAILED", err)
        attempts = self._rows("contact_attempts")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertEqual(self._rows("contacts")[0]["email_sent"], 0)

    def test_set_sent_settles_the_pending_attempt(self):
        cid = self._contact(url="https://www.linkedin.com/in/test-person")
        self.conn.execute(
            "INSERT INTO contact_attempts (contact_id, channel, direction, status) "
            "VALUES (?, 'linkedin_message', 'outbound', 'pending')", (cid,))
        self.conn.commit()
        self._stderr_of(self.reach.cmd_update, "aaaaaaaaaaaaaaaa", 1,
                        set_sent="message")
        self.assertEqual(self._rows("contact_attempts")[0]["status"], "sent")
        self.assertEqual(self._rows("contacts")[0]["message_sent"], 1)


if __name__ == "__main__":
    unittest.main()
