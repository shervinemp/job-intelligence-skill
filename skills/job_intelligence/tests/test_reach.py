"""Tests for reach.py and lib/contacts/discover.py — contact discovery helpers, keyword building, URL logic."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.contacts.discover import _build_team_keywords
from lib.db import get_conn
from reach import (
    cmd_discover, cmd_discover_all, cmd_list, cmd_email, cmd_message, cmd_connect,
    cmd_retry, cmd_undo, cmd_update,
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
            "attempts", "retry", "undo", "threads", "help",
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

    def test_blank_identity_warns_but_does_not_block(self):
        """CURVEBALL C7: a person with no email and no LinkedIn URL cannot be
        compared against prior contacts — surface that gap at send time but do
        not hard-block (a name-only connect is legitimate)."""
        from reach import _block_if_prior, person_keys
        blank = {"id": 99, "name": "No Keys", "linkedin_url": "", "email": ""}
        self.assertEqual(person_keys(blank), set())
        err = self._stderr_of(_block_if_prior, self.conn, blank, False)
        self.assertIn("BLANK_IDENTITY", err)
        self.assertNotIn("ALREADY_REACHED", err)

    def test_undo_refuses_when_only_send_flag_set_no_attempt_row(self):
        """CURVEBALL C8: `update --set-sent` records a send via the flag
        WITHOUT an attempt row. Undo must treat that contact as at-risk too —
        otherwise --confirm is skipped and the cross-job guard is disarmed
        silently."""
        self._contact("aaaaaaaaaaaaaaaa", reached=1)  # flag-only evidence
        err = self._stderr_of(cmd_undo, "aaaaaaaaaaaaaaaa")
        self.assertIn("REFUSED", err)
        # evidence survived
        still = self.conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE reached_out=1").fetchone()[0]
        self.assertEqual(still, 1)

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
        # The tone-review gate is tested separately (PreflightSendGate). These
        # tests exercise the SEND path and must never hit the network or the
        # real ask_api — stub availability so the gate skips deterministically.
        from unittest import mock
        self._ask_patch = mock.patch("lib.ask_api.available", return_value=False)
        self._ask_patch.start()

    def tearDown(self):
        self._ask_patch.stop()
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

    def test_email_attaches_tailored_resume(self):
        """Outreach email must attach the SAME per-job tailored resume PDF the
        apply pipeline built — a generic profile attachment would mismatch the
        submission. The send command carries --attach <resume.pdf>."""
        from unittest import mock
        import json as _json
        self._contact(email="person@example.com")
        resume = os.path.join(self._tmp, "Shervin_Naseri_Co_Role_Resume.pdf")
        with open(resume, "wb") as f:
            f.write(b"%PDF-1.4 fake")
        fake = mock.Mock(returncode=0,
                         stdout=_json.dumps({"status": "sent",
                                             "message_id": "m1"}).encode(),
                         stderr=b"")
        with mock.patch.object(self.reach.subprocess, "run",
                               return_value=fake) as run, \
             mock.patch.object(self.reach, "_job_resume_pdf",
                               return_value=resume):
            err = self._stderr_of(self.reach.cmd_email, "aaaaaaaaaaaaaaaa", 1,
                                  body="hi")
        self.assertIn("EMAIL_SENT", err)
        cmd = run.call_args.args[0]
        self.assertIn("--attach", cmd)
        self.assertIn(resume, cmd)

    def test_email_dry_run_shows_attachment(self):
        """The dry-run must surface the attachment decision so the operator
        sees what would go out before any send."""
        from unittest import mock
        self._contact(email="person@example.com")
        resume = os.path.join(self._tmp, "Shervin_Naseri_Co_Role_Resume.pdf")
        with open(resume, "wb") as f:
            f.write(b"%PDF-1.4 fake")
        with mock.patch.object(self.reach, "_job_resume_pdf",
                               return_value=resume):
            err = self._stderr_of(self.reach.cmd_email, "aaaaaaaaaaaaaaaa", 1,
                                  body="hi", dry_run=True)
        self.assertIn("Attach: Shervin_Naseri_Co_Role_Resume.pdf", err)
        self.assertIn("tailored resume", err)

    def test_email_without_resume_warns_but_sends(self):
        """No tailored PDF on disk must not block the email — it warns in the
        dry-run and sends without --attach. A missing attachment is a weaker
        outreach, not a reason to silently drop the whole message."""
        from unittest import mock
        import json as _json
        self._contact(email="person@example.com")
        fake = mock.Mock(returncode=0,
                         stdout=_json.dumps({"status": "sent",
                                             "message_id": "m1"}).encode(),
                         stderr=b"")
        with mock.patch.object(self.reach.subprocess, "run",
                               return_value=fake) as run, \
             mock.patch.object(self.reach, "_job_resume_pdf",
                               return_value=None):
            err = self._stderr_of(self.reach.cmd_email, "aaaaaaaaaaaaaaaa", 1,
                                  body="hi")
        self.assertIn("EMAIL_SENT", err)
        cmd = run.call_args.args[0]
        self.assertNotIn("--attach", cmd)

    def test_message_dry_run_shows_attachment(self):
        """LinkedIn DM dry-run surfaces the tailored-resume attachment decision
        (DMs DO support .pdf attachments — verified against the live composer's
        document file input) so the operator sees it before any send."""
        from unittest import mock
        self._contact(url="https://www.linkedin.com/in/test-person")
        resume = os.path.join(self._tmp, "Shervin_Naseri_Co_Role_Resume.pdf")
        with open(resume, "wb") as f:
            f.write(b"%PDF-1.4 fake")
        with mock.patch.object(self.reach, "_job_resume_pdf",
                               return_value=resume):
            err = self._stderr_of(self.reach.cmd_message, "aaaaaaaaaaaaaaaa", 1,
                                  body="hi", dry_run=True)
        self.assertIn("Attach: Shervin_Naseri_Co_Role_Resume.pdf", err)
        self.assertIn("tailored resume", err)
        self.assertNotIn("MESSAGE_SENT", err)

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

    def test_threads_backfill_records_existing_thread(self):
        """cmd_threads --backfill records an existing inbox thread as a
        backfilled attempt row so the one-shot guards see the truth — a
        person messaged manually (or before the ledger existed) must not be
        re-messaged as if new."""
        from unittest import mock
        cid = self._contact(url="https://www.linkedin.com/in/sina-akbarian")
        fake_thread = {"exists": True, "checked": True,
                       "last_message_time": "2:47 AM",
                       "last_message_direction": "out",
                       "preview": "Hi Sina, I saw the posting",
                       "thread_url": "https://linkedin.com/messaging/thread/1"}
        fake_ctx = mock.MagicMock()
        with mock.patch.object(self.reach, "get_conn", return_value=self.conn), \
             mock.patch("lib.chrome_manager.connect",
                        return_value=(mock.MagicMock(), fake_ctx)), \
             mock.patch("lib.linkedin_messaging.thread_status",
                        return_value=fake_thread):
            err = self._stderr_of(self.reach.cmd_threads, "aaaaaaaaaaaaaaaa",
                                  backfill=True)
        self.assertIn("THREAD EXISTS", err)
        self.assertIn("backfilled", err)
        rows = self._rows("contact_attempts")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "backfilled")
        self.assertEqual(rows[0]["channel"], "linkedin_message")

    def test_threads_backfill_is_idempotent(self):
        """Re-running --backfill must not create duplicate rows."""
        from unittest import mock
        cid = self._contact(url="https://www.linkedin.com/in/sina-akbarian")
        fake_thread = {"exists": True, "checked": True,
                       "last_message_time": "2:47 AM",
                       "last_message_direction": "out",
                       "preview": "Hi Sina", "thread_url": "u"}
        fake_ctx = mock.MagicMock()
        with mock.patch.object(self.reach, "get_conn", return_value=self.conn), \
             mock.patch("lib.chrome_manager.connect",
                        return_value=(mock.MagicMock(), fake_ctx)), \
             mock.patch("lib.linkedin_messaging.thread_status",
                        return_value=fake_thread):
            self._stderr_of(self.reach.cmd_threads, "aaaaaaaaaaaaaaaa",
                            backfill=True)
            self._stderr_of(self.reach.cmd_threads, "aaaaaaaaaaaaaaaa",
                            backfill=True)
        rows = self._rows("contact_attempts")
        self.assertEqual(len(rows), 1)

    def test_backfilled_thread_blocks_repeat_dm(self):
        """A backfilled thread is prior-outreach evidence: the cross-job
        guard must treat it like a sent/pending attempt so a person we
        already have a thread with is not cold-messaged again."""
        from unittest import mock
        # contact on job A messaged manually (backfilled)
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, scripts) "
            "VALUES ('bbbbbbbbbbbbbbbb', 'https://example.com/b', 'Role2', 'Co2', "
            "'tailored', 'active', '[]')", ())
        cur = self.conn.execute(
            "INSERT INTO contacts (job_id, name, linkedin_url, source) "
            "VALUES (?, 'Sina Akbarian', 'https://www.linkedin.com/in/sina-akbarian', 'team_search')",
            ("bbbbbbbbbbbbbbbb",))
        cid = cur.lastrowid
        self.conn.execute(
            "INSERT INTO contact_attempts (contact_id, channel, direction, status) "
            "VALUES (?, 'linkedin_message', 'outbound', 'backfilled')", (cid,))
        self.conn.commit()
        # same person on job "aaaaaaaaaaaaaaaa" — DM must be blocked
        self._contact(url="https://www.linkedin.com/in/sina-akbarian")
        err = self._stderr_of(self.reach.cmd_message, "aaaaaaaaaaaaaaaa", 1,
                              body="hi", dry_run=True)
        self.assertIn("ALREADY_REACHED", err)
        self.assertNotIn("MESSAGE_SENT", err)

    def test_schema_migrates_backfilled_status(self):
        """A pre-backfill DB (status CHECK without 'backfilled') must be
        migrated in place so `threads --backfill` can write its rows."""
        import lib.db.schema as schema
        # Build a table with the OLD CHECK, exactly as an existing DB has it.
        c = self.conn
        c.execute("DROP TABLE IF EXISTS contact_attempts")
        # seed a real contact so the FK is satisfied
        self._contact(url="https://www.linkedin.com/in/test-person")
        c.execute("""
            CREATE TABLE contact_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                channel TEXT NOT NULL CHECK(channel IN ('email','linkedin_message','linkedin_connect')),
                direction TEXT NOT NULL DEFAULT 'outbound' CHECK(direction IN ('outbound','inbound')),
                subject TEXT DEFAULT '',
                body TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed','opened','replied')),
                message_id TEXT DEFAULT '',
                error TEXT DEFAULT '',
                sent_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cid = c.execute("SELECT id FROM contacts WHERE name='Test Person'").fetchone()["id"]
        c.execute("""
            INSERT INTO contact_attempts (contact_id, channel, direction, status)
            VALUES (?, 'linkedin_message', 'outbound', 'sent')
        """, (cid,))
        c.commit()
        schema._migrate_contact_attempts_backfill(c)
        src = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='contact_attempts'").fetchone()
        self.assertIn("'backfilled'", src["sql"])
        # data survived the rebuild
        row = c.execute("SELECT status FROM contact_attempts").fetchone()
        self.assertEqual(row["status"], "sent")
        # and a backfilled row is now insertable
        c.execute("""
            INSERT INTO contact_attempts (contact_id, channel, direction, status)
            VALUES (?, 'linkedin_message', 'outbound', 'backfilled')
        """, (cid,))
        c.commit()
        self.assertEqual(
            c.execute("SELECT COUNT(*) n FROM contact_attempts "
                      "WHERE status='backfilled'").fetchone()["n"], 1)


class OutreachLedger(_TempDBMixin, unittest.TestCase):
    """C3: lib/outreach_ledger is the single writer for the flag+attempt+event
    choreography. Atomicity (item J), the uncertain path, settle, and the
    flag-evidence undo gate (item K/C8) all live behind one seam."""

    def _job(self, jid):
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, "
            "created_at, updated_at, scripts) "
            "VALUES (?, ?, 'Role', 'Co', 'tailored', 'active', ?, ?, '[]')",
            (jid, f"https://example.com/{jid}",
             self._datetime.now().isoformat(), self._datetime.now().isoformat()),
        )
        self.conn.commit()

    def _contact(self, jid="aaaaaaaaaaaaaaaa", url="", email="", cid=None):
        self._job(jid)
        if cid is None:
            cur = self.conn.execute(
                "INSERT INTO contacts (job_id, name, linkedin_url, email) "
                "VALUES (?,?,?,?)", (jid, "Test Person", url, email),
            )
            self.conn.commit()
            return cur.lastrowid
        return cid

    def _rows(self, table):
        return self.conn.execute(f"SELECT * FROM {table}").fetchall()

    def test_sent_records_flag_attempt_event_atomically(self):
        from lib.outreach_ledger import record_outcome
        cid = self._contact()
        record_outcome(self.conn, "email", cid, "aaaaaaaaaaaaaaaa", "sent",
                       subject="S", body="B", message_id="m1",
                       event_type="email", event_title="Emailed",
                       event_desc="to x")
        c = self._rows("contacts")[0]
        self.assertEqual(c["email_sent"], 1)
        self.assertEqual(c["reached_out"], 1)
        self.assertIsNotNone(c["last_contacted_at"])
        a = self._rows("contact_attempts")
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["status"], "sent")
        self.assertEqual(a[0]["message_id"], "m1")
        self.assertEqual(len(self._rows("events")), 1)
        j = self.conn.execute("SELECT outreach_attempted FROM jobs WHERE id='aaaaaaaaaaaaaaaa'").fetchone()
        self.assertEqual(j["outreach_attempted"], 1)

    def test_pending_records_attempt_only_no_flags(self):
        """The uncertain path must NOT set flags/reached_out — the guard stays
        armed until the human settles it."""
        from lib.outreach_ledger import record_outcome
        cid = self._contact()
        record_outcome(self.conn, "linkedin_message", cid, "aaaaaaaaaaaaaaaa",
                       "pending", body="B", error="unconfirmed")
        a = self._rows("contact_attempts")
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["status"], "pending")
        c = self._rows("contacts")[0]
        self.assertEqual(c["message_sent"], 0)
        self.assertEqual(c["reached_out"], 0)

    def test_failed_records_attempt_only(self):
        from lib.outreach_ledger import record_outcome
        cid = self._contact()
        record_outcome(self.conn, "email", cid, "aaaaaaaaaaaaaaaa", "failed",
                       body="B", error="smtp")
        a = self._rows("contact_attempts")
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["status"], "failed")
        self.assertEqual(self._rows("contacts")[0]["email_sent"], 0)
        self.assertEqual(len(self._rows("events")), 0)

    def test_rollback_on_failure_leaves_no_partial_write(self):
        """Item J: a failed write must NOT leave flag=1 with no attempt row."""
        from lib.outreach_ledger import record_outcome
        cid = self._contact()
        # The events INSERT (4th write) references jobs(id) — a job id that
        # doesn't exist violates the FK AFTER the flag+attempt writes hit the
        # same connection. If the writes were not atomic, flag=1 and an attempt
        # row would survive the failed event write.
        with self.assertRaises(Exception):
            record_outcome(self.conn, "email", cid, "does-not-exist-0000",
                           "sent", body="B", event_type="email",
                           event_title="X", event_desc="d")
        self.conn.rollback()
        # nothing committed: no attempt row, flag still 0
        self.assertEqual(len(self._rows("contact_attempts")), 0)
        self.assertEqual(self._rows("contacts")[0]["email_sent"], 0)

    def test_settle_confirms_pending_and_sets_flag(self):
        from lib.outreach_ledger import record_outcome, settle
        cid = self._contact()
        record_outcome(self.conn, "linkedin_message", cid, "aaaaaaaaaaaaaaaa",
                       "pending", body="B")
        n = settle(self.conn, cid, "linkedin_message")
        self.assertEqual(n, 1)
        a = self._rows("contact_attempts")[0]
        self.assertEqual(a["status"], "sent")
        self.assertEqual(self._rows("contacts")[0]["message_sent"], 1)

    def test_settle_flag_only_no_pending_row_still_sets_flag(self):
        """Item K/C8: a flag-only send (via update --set-sent with no pending
        row) must still set the flag so the undo gate sees the evidence."""
        from lib.outreach_ledger import settle
        cid = self._contact()
        n = settle(self.conn, cid, "email")
        self.assertEqual(n, 0)  # nothing to settle
        self.assertEqual(self._rows("contacts")[0]["email_sent"], 1)

    def test_undo_gate_sees_flag_evidence(self):
        """Item K/C8: outreach_at_risk must include flag-only sends (no attempt
        row), or undo could silently disarm the cross-job guard."""
        from lib.outreach_ledger import outreach_at_risk
        cid = self._contact()
        self.conn.execute("UPDATE contacts SET email_sent=1, reached_out=1 WHERE id=?", (cid,))
        self.conn.commit()
        at_risk = outreach_at_risk(self.conn, "aaaaaaaaaaaaaaaa")
        self.assertEqual(len(at_risk), 1)

    def test_undo_gate_empty_when_no_evidence(self):
        from lib.outreach_ledger import outreach_at_risk
        self._contact()
        self.assertEqual(outreach_at_risk(self.conn, "aaaaaaaaaaaaaaaa"), [])

    def test_clear_outreach_resets_all(self):
        from lib.outreach_ledger import clear_outreach, record_outcome, outreach_at_risk
        cid = self._contact()
        record_outcome(self.conn, "email", cid, "aaaaaaaaaaaaaaaa", "sent",
                       body="B", event_type="email", event_title="X")
        self.assertEqual(len(outreach_at_risk(self.conn, "aaaaaaaaaaaaaaaa")), 1)
        clear_outreach(self.conn, "aaaaaaaaaaaaaaaa")
        self.assertEqual(outreach_at_risk(self.conn, "aaaaaaaaaaaaaaaa"), [])
        self.assertEqual(len(self._rows("contact_attempts")), 0)


class ToneCheck(unittest.TestCase):
    """No hardcoded tone lists: the orchestrator (LLM) reviews the message
    against the voice spec + thread reality. These tests pin the CONTRACT of
    the review seam — what a FAIL vs PASS vs no-review looks like — with the
    LLM stubbed."""

    def _tone_review(self, reply=None, mode="on"):
        from unittest.mock import patch
        import os
        old = os.environ.get("JI_LLM_MODE")
        os.environ["JI_LLM_MODE"] = mode
        try:
            from lib.outreach_llm import tone_review
            with patch("lib.ask_api.available", return_value=True), \
                 patch("lib.ask_api.ask_text",
                       return_value=(reply or "VERDICT: PASS\nNOTES: none", None)):
                return tone_review(
                    "Hi Sina, I applied. Would you be open to a quick chat?",
                    thread=None, voice_spec="short, warm, one ask",
                    channel="message")
        finally:
            if old is None:
                os.environ.pop("JI_LLM_MODE", None)
            else:
                os.environ["JI_LLM_MODE"] = old

    def test_pass_verdict_is_ok(self):
        ok, notes, detail = self._tone_review("VERDICT: PASS\nNOTES: none")
        self.assertTrue(ok)

    def test_fail_verdict_is_blocked(self):
        ok, notes, detail = self._tone_review(
            "VERDICT: FAIL\nNOTES: empty filler — say something specific "
            "about the role")
        self.assertFalse(ok)
        self.assertTrue(any("empty filler" in n for n in notes))

    def test_auto_mode_runs_review(self):
        """Tone review is ON in auto mode — the orchestrator LLM judges the
        message before it leaves; only a FAIL verdict blocks. No hardcoded
        phrase lists anywhere."""
        ok, notes, detail = self._tone_review(mode="auto")
        self.assertTrue(ok)
        self.assertNotIn("orchestrator", detail.lower())

    def test_voice_line_present(self):
        from reach import _voice_line
        self.assertIn("short, warm", _voice_line("message").lower())
        self.assertIn("friendly", _voice_line("email").lower())


class PreflightSendGate(unittest.TestCase):
    """The LLM tone review gates the ACTUAL send path, not just the dry-run.
    A FAIL verdict is a blind-send until --force clears it after review."""

    def _preflight(self, body, force=False, reply=None):
        from unittest.mock import patch
        import io, contextlib
        from reach import _preflight_send
        reply = reply or "VERDICT: PASS\nNOTES: none"
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), \
             patch("lib.automation.llm.mode", return_value="on"), \
             patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=(reply, None)):
            ok = _preflight_send(body, "message", force=force)
        return ok, buf.getvalue()

    def test_fail_blocks_send_without_force(self):
        ok, err = self._preflight(
            "Hi Sina, attaching the tailored resume for your reference.",
            reply="VERDICT: FAIL\nNOTES: a resume dump is not a message")
        self.assertFalse(ok)
        self.assertIn("TONE_BLOCK", err)

    def test_fail_overridden_by_force(self):
        ok, err = self._preflight(
            "Hi Sina, attaching the tailored resume for your reference.",
            force=True,
            reply="VERDICT: FAIL\nNOTES: a resume dump is not a message")
        self.assertTrue(ok)
        self.assertIn("--force", err)

    def test_clean_message_passes(self):
        ok, err = self._preflight(
            "Hi Sina, I applied. Would you be open to a quick chat sometime soon?")
        self.assertTrue(ok)
        self.assertNotIn("TONE_BLOCK", err)


class TeamExtraction(unittest.TestCase):
    """Posting-page hiring team + tracker people — connection-independent
    discovery (the sources the user flagged as missing)."""

    def _ctx_with_page(self, page):
        ctx = MagicMock()
        ctx.new_page.return_value = page
        return ctx

    def _page(self, eval_result):
        page = MagicMock()
        page.evaluate.return_value = eval_result
        page.goto.return_value = None
        page.wait_for_timeout.return_value = None
        page.close.return_value = None
        loc = MagicMock()
        loc.count.return_value = 0
        loc.first.count.return_value = 0
        page.locator.return_value = loc
        return page

    def test_extract_posting_team_parses_people(self):
        from lib.linkedin_messaging import _extract_posting_team
        page = self._page([
            {"name": "Sina R.", "role": "Hiring Manager",
             "linkedin_url": "https://www.linkedin.com/in/sinar", "connection_degree": ""},
            {"name": "Aida K.", "role": "Recruiter",
             "linkedin_url": "https://www.linkedin.com/in/aidak", "connection_degree": "2nd"},
        ])
        ctx = self._ctx_with_page(page)
        people = _extract_posting_team(ctx, "https://www.linkedin.com/jobs/view/1")
        self.assertEqual(len(people), 2)
        self.assertEqual(people[0]["name"], "Sina R.")
        self.assertEqual(people[0]["role"], "Hiring Manager")
        page.goto.assert_called_once()

    def test_extract_posting_team_empty_page(self):
        from lib.linkedin_messaging import _extract_posting_team
        page = self._page([])
        ctx = self._ctx_with_page(page)
        self.assertEqual(_extract_posting_team(ctx, "https://x.com"), [])

    def test_tracker_people_extracts_links(self):
        from lib.linkedin_messaging import search_tracker_applied_people
        page = self._page([
            {"name": "Ali M.", "role": "", "linkedin_url": "https://www.linkedin.com/in/alim"},
        ])
        ctx = self._ctx_with_page(page)
        with patch("lib.linkedin_messaging._check_auth", return_value=True):
            people = search_tracker_applied_people(ctx)
        self.assertEqual(people[0]["name"], "Ali M.")

    def test_tracker_people_auth_gate(self):
        from lib.linkedin_messaging import search_tracker_applied_people
        page = self._page([])
        ctx = self._ctx_with_page(page)
        with patch("lib.linkedin_messaging._check_auth", return_value=False):
            self.assertEqual(search_tracker_applied_people(ctx), [])


if __name__ == "__main__":
    unittest.main()
