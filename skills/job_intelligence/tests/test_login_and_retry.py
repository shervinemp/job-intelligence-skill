"""Unit tests for the login-wall handler and the auto.py LLM retry paths.

These are the most security-sensitive, previously-untested flows:
auto-login with password candidates, 2FA detection, account creation,
and the LLM-assisted fill/submit retries.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


_LOGIN_JS_INFO = {
    "hasEmail": True,
    "createText": None,
    "createTag": None,
}


class LoginWallNoWall(unittest.TestCase):
    def test_no_password_field_returns_empty(self):
        from apply.act.fill import _handle_login_wall
        page = MagicMock()
        page.evaluate.return_value = None
        self.assertEqual(_handle_login_wall(page, "jid", quick=False), "")

    def test_evaluate_error_returns_empty(self):
        from apply.act.fill import _handle_login_wall
        page = MagicMock()
        page.evaluate.side_effect = Exception("detached")
        self.assertEqual(_handle_login_wall(page, "jid", quick=False), "")


class LoginWallAutoLogin(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.page.evaluate.return_value = dict(_LOGIN_JS_INFO)
        self.page.url = "https://workday.example.com/job"
        self.sleep_patch = patch("apply.act.fill.time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        # These tests exercise the auto-login MECHANICS; the credential guard
        # (ADVERSARIAL #2-A) requires an approved domain — approve it here.
        self.approve_patch = patch(
            "apply.act.fill._domain_approved", return_value=True)
        self.approve_patch.start()
        self.addCleanup(self.approve_patch.stop)

    def test_unapproved_domain_refuses_creds(self):
        """The credential guard: an unapproved domain must NOT get a password
        typed (persuasive-fake-ATS protection)."""
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("apply.act.fill._domain_approved", return_value=False), \
             patch("apply.act.fill._fill_signin_form") as fill_mock:
            rc = _handle_login_wall(self.page, "jid", quick=False)
        fill_mock.assert_not_called()  # no password typed into an unapproved domain
        self.assertNotEqual(rc, "")

    def test_login_success_returns_empty(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("apply.act.fill._fill_signin_form"), \
             patch("apply.act.fill._login_check", return_value="yes"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "")
        # primary password worked — nothing promoted
        save_mock.assert_not_called()

    def test_alt_password_promoted_to_primary(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("apply.act.fill._fill_signin_form"), \
             patch("apply.act.fill._login_check", side_effect=["no", "yes"]):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "")
        save_mock.assert_called_once()
        args = save_mock.call_args[0]
        self.assertEqual(args[1], "a@b.com")
        self.assertEqual(args[2], "pw2")  # winner promoted to primary
        self.assertEqual(save_mock.call_args[1]["passwords"], ["pw1"])  # winner → primary, others stay alternates

    def test_2fa_required_status(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("apply.act.fill._fill_signin_form") as fill_mock, \
             patch("apply.act.fill._login_check", return_value="2fa"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "2fa_required")
        fill_mock.assert_called_once()  # never tried the second password
        save_mock.assert_not_called()  # 2FA = credentials accepted, nothing promoted

    def test_all_passwords_fail_status(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("apply.act.fill._fill_signin_form"), \
             patch("apply.act.fill._login_check", return_value="no"), \
             patch("apply.act.fill._re_open_signin_form"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "login_failed")


class LoginWallNoCreds(unittest.TestCase):
    def test_login_required_when_no_creds(self):
        from apply.act.fill import _handle_login_wall
        page = MagicMock()
        info = {"hasEmail": True, "createText": None, "createTag": None}
        page.evaluate.return_value = info
        page.url = "https://example.com/job"
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=None), \
             patch("lib.credentials.get_account_defaults", return_value={}):
            self.assertEqual(_handle_login_wall(page, "jid", quick=False), "login_required")


class AutoLLMRetryFill(unittest.TestCase):
    def test_llm_unavailable_returns_false(self):
        from apply.auto import _retry_fill_with_llm
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), patch("lib.ask_api.available", return_value=False):
            self.assertFalse(_retry_fill_with_llm("jid", {}, None))

    def test_no_remaining_fields_returns_false(self):
        from apply.auto import _retry_fill_with_llm
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("apply.common.page_helpers.load_state",
                   return_value={"remaining_fields": []}):
            self.assertFalse(_retry_fill_with_llm("jid", {}, None))

    def test_llm_mapping_fills_and_checks(self):
        from apply.auto import _retry_fill_with_llm
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("lib.ask_api.available", return_value=True), \
             patch("apply.common.page_helpers.load_state",
                   return_value={"remaining_fields": [{"label": "Email"}]}), \
             patch("apply.act.helpers._load_profile", return_value={}), \
             patch("apply.common.fill_runner.llm_field_key_mapping",
                   return_value={"Email": "a@b.com"}) as map_mock, \
             patch("apply.act.fill.cmd_fill", return_value=0) as fill_mock, \
             patch("apply.act.check.cmd_check", return_value=0):
            self.assertTrue(_retry_fill_with_llm("jid", {}, None))
        map_mock.assert_called_once()
        fill_mock.assert_called_once()

    def test_llm_mapping_empty_returns_false(self):
        from apply.auto import _retry_fill_with_llm
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("apply.common.page_helpers.load_state",
                   return_value={"remaining_fields": [{"label": "Email"}]}), \
             patch("apply.act.helpers._load_profile", return_value={}), \
             patch("apply.common.fill_runner.llm_field_key_mapping", return_value={}):
            self.assertFalse(_retry_fill_with_llm("jid", {}, None))


class AutoLLMRetrySubmit(unittest.TestCase):
    def _conn(self, stage="applied"):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"stage": stage}
        return conn

    def test_no_parseable_labels_returns_false(self):
        from apply.auto import _retry_submit_with_llm
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("apply.common.page_helpers.load_state",
                   return_value={"submit_errors": ["???"]}):
            self.assertFalse(_retry_submit_with_llm("jid", {}, None))

    def test_success_path_resubmits(self):
        from apply.auto import _retry_submit_with_llm
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("lib.ask_api.available", return_value=True), \
             patch("apply.common.page_helpers.load_state",
                   return_value={"submit_errors":
                                 ["Missing entry for required field: Email"]}), \
             patch("apply.act.helpers._load_profile", return_value={}), \
             patch("apply.common.fill_runner.llm_field_key_mapping",
                   return_value={"Email": "a@b.com"}), \
             patch("apply.act.fill.cmd_fill", return_value=0), \
             patch("apply.act.check.cmd_check", return_value=0), \
             patch("apply.act.submit.cmd_submit", return_value=0), \
             patch("lib.db.get_conn", return_value=self._conn("applied")):
            self.assertTrue(_retry_submit_with_llm("jid", {}, None))

    def test_stage_not_applied_after_resubmit_returns_false(self):
        from apply.auto import _retry_submit_with_llm
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("apply.common.page_helpers.load_state",
                   return_value={"submit_errors":
                                 ["Missing entry for required field: Email"]}), \
             patch("apply.act.helpers._load_profile", return_value={}), \
             patch("apply.common.fill_runner.llm_field_key_mapping",
                   return_value={"Email": "a@b.com"}), \
             patch("apply.act.fill.cmd_fill", return_value=0), \
             patch("apply.act.check.cmd_check", return_value=0), \
             patch("apply.act.submit.cmd_submit", return_value=0), \
             patch("lib.db.get_conn", return_value=self._conn("tailored")):
            self.assertFalse(_retry_submit_with_llm("jid", {}, None))


if __name__ == "__main__":
    unittest.main()

