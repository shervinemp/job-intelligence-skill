"""Unit tests for the login-wall handler and the auto.py LLM retry paths.

These are the most security-sensitive, previously-untested flows:
auto-login with password candidates, 2FA detection, account creation,
and the LLM-assisted fill/submit retries.
"""

import os
import sys
import tempfile
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
            "apply.act.auth_flow._domain_approved", return_value=True)
        self.approve_patch.start()
        self.addCleanup(self.approve_patch.stop)

    def test_unapproved_domain_refuses_creds(self):
        """The credential guard: an unapproved domain must NOT get a password
        typed (persuasive-fake-ATS protection)."""
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("apply.act.auth_flow._domain_approved", return_value=False), \
             patch("apply.act.auth_flow.fill_signin_form") as fill_mock:
            rc = _handle_login_wall(self.page, "jid", quick=False)
        fill_mock.assert_not_called()  # no password typed into an unapproved domain
        self.assertNotEqual(rc, "")

    def test_login_success_returns_empty(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("apply.act.auth_flow.fill_signin_form"), \
             patch("apply.act.auth_flow.login_check", return_value="yes"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "")
        # primary password worked — nothing promoted
        save_mock.assert_not_called()

    def test_alt_password_promoted_to_primary(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("apply.act.auth_flow.fill_signin_form"), \
             patch("apply.act.auth_flow.login_check", side_effect=["no", "yes"]):
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
             patch("apply.act.auth_flow.fill_signin_form") as fill_mock, \
             patch("apply.act.auth_flow.login_check", return_value="2fa"), \
             patch("apply.act.auth_flow._try_complete_2fa_from_inbox",
                   return_value="skip"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "2fa_required")
        fill_mock.assert_called_once()  # never tried the second password
        save_mock.assert_not_called()  # 2FA = credentials accepted, nothing promoted

    def test_2fa_completed_via_inbox(self):
        """When the inbox yields a valid security code, the flow enters it and
        continues — the manual 2FA handoff must not fire."""
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds"), \
             patch("apply.act.auth_flow.fill_signin_form"), \
             patch("apply.act.auth_flow.login_check", return_value="2fa"), \
             patch("apply.act.auth_flow._try_complete_2fa_from_inbox",
                   return_value="yes") as inbox_mock:
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "")
        inbox_mock.assert_called_once()

    def test_all_passwords_fail_status(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("apply.act.auth_flow.fill_signin_form"), \
             patch("apply.act.auth_flow.login_check", return_value="no"), \
             patch("apply.act.auth_flow.reopen_signin_form"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "login_failed")


class LoginWallCaptcha(unittest.TestCase):
    """CAPTCHA on the auth form must surface as captcha_required — never
    folded into the 'uncertain → assume OK' path that records a login (or
    saves creds for an account) that never happened."""

    def setUp(self):
        self.page = MagicMock()
        self.page.evaluate.return_value = dict(_LOGIN_JS_INFO)
        self.page.url = "https://workday.example.com/job"
        self.sleep_patch = patch("apply.act.fill.time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.approve_patch = patch(
            "apply.act.auth_flow._domain_approved", return_value=True)
        self.approve_patch.start()
        self.addCleanup(self.approve_patch.stop)

    def test_login_captcha_returns_captcha_required_and_never_promotes(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1", "pw2"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("apply.act.auth_flow.fill_signin_form"), \
             patch("apply.act.auth_flow.login_check", return_value="captcha"):
            rc = _handle_login_wall(self.page, "jid", quick=False)
        self.assertEqual(rc, "captcha_required")
        save_mock.assert_not_called()  # never promote/save on a captcha-blocked login

    def test_uncertain_then_captcha_is_not_assumed_ok(self):
        from apply.act.fill import _handle_login_wall
        creds = {"email": "a@b.com", "password": "pw1", "passwords": ["pw1"]}
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=creds), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("apply.act.auth_flow.fill_signin_form"), \
             patch("apply.act.auth_flow.login_check",
                   side_effect=["uncertain", "captcha"]):
            rc = _handle_login_wall(self.page, "jid", quick=False)
        self.assertEqual(rc, "captcha_required")
        save_mock.assert_not_called()

    def test_login_check_surfaces_captcha_when_widget_present(self):
        """_login_check must translate an unresolved form + visible CAPTCHA
        widget into 'captcha', not 'uncertain' (which callers treat as OK)."""
        from apply.act.fill import _login_check
        page = MagicMock()
        page.evaluate.return_value = "uncertain"
        with patch("apply.common.page_helpers.check_captcha",
                   return_value=True):
            self.assertEqual(_login_check(page), "captcha")
        with patch("apply.common.page_helpers.check_captcha",
                   return_value=False):
            self.assertEqual(_login_check(page), "uncertain")

    def test_check_account_created_surfaces_captcha(self):
        from apply.act.fill import _check_account_created
        page = MagicMock()
        page.evaluate.return_value = "uncertain"
        with patch("apply.common.page_helpers.check_captcha",
                   return_value=True):
            self.assertEqual(_check_account_created(page), "captcha")
        with patch("apply.common.page_helpers.check_captcha",
                   return_value=False):
            self.assertEqual(_check_account_created(page), "uncertain")

    def test_check_captcha_detects_recaptcha_iframe(self):
        """The runtime CAPTCHA detector must match reCAPTCHA/hCaptcha widgets —
        the probe-time detector already does, so the runtime must too."""
        from apply.common.page_helpers import check_captcha
        page = MagicMock()
        page.evaluate.return_value = True  # widget present (visible)
        self.assertTrue(check_captcha(page))


class CaptchaDetectionExtra(unittest.TestCase):
    """C-C1 extension: ARIA-only and non-English CAPTCHAs must be detected,
    and the per-domain skip list must be honored."""

    def test_aria_role_captcha_detected(self):
        from apply.common.page_helpers import check_captcha
        page = MagicMock()
        page.evaluate.return_value = True  # widget query: aria/captcha found
        self.assertTrue(check_captcha(page))

    def test_french_captcha_text_detected(self):
        from apply.common.page_helpers import check_captcha
        page = MagicMock()
        page.evaluate.side_effect = [False, "je ne suis pas un robot"]
        self.assertTrue(check_captcha(page))

    def test_russian_captcha_text_detected(self):
        from apply.common.page_helpers import check_captcha
        page = MagicMock()
        page.evaluate.side_effect = [False, "подтвердите, что вы не робот"]
        self.assertTrue(check_captcha(page))

    def test_handle_captcha_skips_listed_domain(self):
        from apply.common.page_helpers import handle_captcha
        page = MagicMock()
        page.url = "https://jobs.example.com/apply"
        with patch("apply.common.page_helpers.check_captcha", return_value=True), \
             patch("apply.common.submit_policy.load_policy",
                   return_value={"captcha_skip": False,
                                 "captcha_skip_domains": ["jobs.example.com"]}):
            self.assertTrue(handle_captcha(page, {}))

    def test_handle_captcha_waits_unlisted_domain(self):
        from apply.common.page_helpers import handle_captcha
        page = MagicMock()
        page.url = "https://jobs.other.com/apply"
        with patch("apply.common.page_helpers.check_captcha", return_value=True), \
             patch("apply.common.submit_policy.load_policy",
                   return_value={"captcha_skip": False,
                                 "captcha_skip_domains": ["jobs.example.com"]}), \
             patch("apply.common.page_helpers.is_cloudflare_challenge",
                   return_value=False), \
             patch("apply.common.page_helpers.wait_cloudflare", return_value=False), \
             patch("apply.common.page_helpers.time.sleep") as sl:
            self.assertTrue(handle_captcha(page, {}, wait_s=6, poll_s=2))
        sl.assert_called()  # it waited instead of skipping

    def test_policy_default_has_no_domain_list(self):
        from apply.common.submit_policy import load_policy
        with patch("apply.common.submit_policy._policy_path",
                   return_value=os.path.join(tempfile.gettempdir(),
                                             "nope_policy.json")):
            pol = load_policy()
        self.assertIn("captcha_skip_domains", pol)
        self.assertEqual(pol["captcha_skip_domains"], [])


class PostLoginGates(unittest.TestCase):
    """C1: post_login_gates is the sequential 2FA → captcha gate the fill loop
    calls after sign-in. Each gate blocks independently (grilling item A/B:
    two gates, not one verdict)."""

    def test_2fa_blocks_before_captcha(self):
        from apply.act.auth_flow import post_login_gates
        page = MagicMock()
        state = {}
        prof = MagicMock()
        prof.get.return_value = 2  # two_factor_signals truthy
        with patch("apply.common.capabilities.scan", return_value=prof), \
             patch("apply.common.page_helpers.handle_captcha") as hc, \
             patch("apply.common.page_helpers.save_state") as ss:
            blocked = post_login_gates(page, state, deadline=None)
        self.assertTrue(blocked)
        hc.assert_not_called()  # 2FA decided first
        self.assertEqual(state["status"], "2fa_required")
        ss.assert_called_once()

    def test_captcha_blocks_when_no_2fa(self):
        from apply.act.auth_flow import post_login_gates
        page = MagicMock()
        state = {}
        prof = MagicMock()
        prof.get.return_value = 0  # no 2FA
        with patch("apply.common.capabilities.scan", return_value=prof), \
             patch("apply.common.page_helpers.handle_captcha",
                   return_value=True) as hc, \
             patch("apply.common.page_helpers.save_state") as ss:
            blocked = post_login_gates(page, state, deadline=None)
        self.assertTrue(blocked)
        hc.assert_called_once()
        self.assertEqual(state["status"], "captcha_required")

    def test_continues_when_neither_blocks(self):
        from apply.act.auth_flow import post_login_gates
        page = MagicMock()
        state = {}
        prof = MagicMock()
        prof.get.return_value = 0
        with patch("apply.common.capabilities.scan", return_value=prof), \
             patch("apply.common.page_helpers.handle_captcha",
                   return_value=False), \
             patch("apply.common.page_helpers.save_state") as ss:
            blocked = post_login_gates(page, state, deadline=None)
        self.assertFalse(blocked)
        ss.assert_not_called()

    def test_scan_error_falls_through_to_captcha(self):
        from apply.act.auth_flow import post_login_gates
        page = MagicMock()
        state = {}
        with patch("apply.common.capabilities.scan",
                   side_effect=Exception("boom")), \
             patch("apply.common.page_helpers.handle_captcha",
                   return_value=True), \
             patch("apply.common.page_helpers.save_state"):
            blocked = post_login_gates(page, state, deadline=None)
        self.assertTrue(blocked)


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


class PlaintextFallbackGate(unittest.TestCase):
    """ADVERSARIAL #2 residual: plaintext credential fallback
    (~/.ji/credentials.json) must be opt-in, and files written only with
    owner-only ACL. A silent keychain→plaintext downgrade is a security
    change the operator must explicitly allow."""

    def setUp(self):
        self._old_env = os.environ.get("JI_ALLOW_PLAINTEXT")
        os.environ.pop("JI_ALLOW_PLAINTEXT", None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("JI_ALLOW_PLAINTEXT", None)
        if self._old_env is not None:
            os.environ["JI_ALLOW_PLAINTEXT"] = self._old_env

    def test_refused_by_default(self):
        from lib.credentials import _plaintext_fallback_allowed
        with patch("lib.db.settings.setting_get", return_value=None):
            self.assertFalse(_plaintext_fallback_allowed())

    def test_env_var_allows(self):
        from lib.credentials import _plaintext_fallback_allowed
        with patch.dict(os.environ, {"JI_ALLOW_PLAINTEXT": "1"}):
            self.assertTrue(_plaintext_fallback_allowed())

    def test_fallback_write_refused_loudly(self):
        from lib.credentials import _fallback_write
        tmp = tempfile.mkdtemp()
        with patch("lib.credentials._FALLBACK_PATH",
                   os.path.join(tmp, "credentials.json")), \
             patch("lib.credentials._plaintext_fallback_allowed",
                   return_value=False), \
             patch("sys.stderr") as err:
            ok = _fallback_write({"x": "y"})
        self.assertFalse(ok)
        err.write.assert_called()
        self.assertFalse(os.path.exists(os.path.join(tmp, "credentials.json")))

    def test_fallback_write_allowed_writes_and_chmods(self):
        from lib.credentials import _fallback_write
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "credentials.json")
        with patch("lib.credentials._FALLBACK_PATH", path), \
             patch("lib.credentials._plaintext_fallback_allowed",
                   return_value=True):
            ok = _fallback_write({"x": "y"})
        self.assertTrue(ok)
        import json as _json
        self.assertEqual(_json.load(open(path, encoding="utf-8")), {"x": "y"})


if __name__ == "__main__":
    unittest.main()

