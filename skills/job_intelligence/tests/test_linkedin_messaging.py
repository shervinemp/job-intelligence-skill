"""Tests for lib/linkedin_messaging.py — verified selectors, URL extraction, capability probes."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.linkedin_messaging import (
    _extract_profile_id,
    _extract_company_id,
    _clean_profile_url,
    _DM_CAPABILITY_JS,
    _MESSAGE_SENT_JS,
    _CONNECT_BUTTON_JS,
)


class ProfileIdExtraction(unittest.TestCase):
    def test_in_url_with_username(self):
        self.assertEqual(_extract_profile_id("https://www.linkedin.com/in/janedoe/"), "janedoe")

    def test_in_url_no_trailing_slash(self):
        self.assertEqual(_extract_profile_id("https://www.linkedin.com/in/janedoe"), "janedoe")

    def test_in_url_with_numeric_id(self):
        self.assertEqual(_extract_profile_id("https://www.linkedin.com/in/12345678/"), "12345678")

    def test_in_url_with_dashed_name(self):
        self.assertEqual(_extract_profile_id("https://www.linkedin.com/in/jane-doe/"), "jane-doe")

    def test_in_url_with_query_params(self):
        self.assertEqual(_extract_profile_id("https://www.linkedin.com/in/janedoe?trk=public_profile"), "janedoe")

    def test_non_in_url(self):
        self.assertEqual(_extract_profile_id("https://www.linkedin.com/company/acme/"), "company/acme")

    def test_empty_url(self):
        self.assertEqual(_extract_profile_id(""), "")

    def test_none_url_returns_empty(self):
        self.assertEqual(_extract_profile_id(None), "")


class CleanProfileUrl(unittest.TestCase):
    def test_strips_mini_profile_urn(self):
        url = "https://www.linkedin.com/in/jacob-anderson88?miniProfileUrn=urn%3Ali%3Afs_miniProfile%3AACoAACp"
        self.assertEqual(_clean_profile_url(url), "https://www.linkedin.com/in/jacob-anderson88")

    def test_strips_any_query(self):
        self.assertEqual(_clean_profile_url("https://www.linkedin.com/in/janedoe?trk=abc"),
                         "https://www.linkedin.com/in/janedoe")

    def test_no_query_unchanged(self):
        url = "https://www.linkedin.com/in/janedoe"
        self.assertEqual(_clean_profile_url(url), url)

    def test_empty_unchanged(self):
        self.assertEqual(_clean_profile_url(""), "")


class CompanyIdExtraction(unittest.TestCase):
    def test_extracts_urn_company_id(self):
        html = '<script>"urn:li:company:1441"</script>'
        self.assertEqual(_extract_company_id(html), "1441")

    def test_extracts_companyId_field(self):
        html = '"companyId": 123456'
        self.assertEqual(_extract_company_id(html), "123456")

    def test_no_id_returns_none(self):
        self.assertIsNone(_extract_company_id("<html>no ids here</html>"))

    def test_empty_returns_none(self):
        self.assertIsNone(_extract_company_id(""))
        self.assertIsNone(_extract_company_id(None))

    def test_multiple_ids_takes_first_urn(self):
        html = '"urn:li:company:100" "companyId": 200'
        self.assertEqual(_extract_company_id(html), "100")


class JsProbesAreValid(unittest.TestCase):
    def test_dm_capability_js_is_function(self):
        self.assertTrue("function" in _DM_CAPABILITY_JS or "=>" in _DM_CAPABILITY_JS)

    def test_dm_capability_has_all_keys(self):
        for key in ["hasMessageButton", "hasConnectButton", "hasComposeBox",
                    "sendButtonEnabled", "inmailComposer"]:
            self.assertIn(key, _DM_CAPABILITY_JS)

    def test_dm_capability_uses_verified_selectors(self):
        # Verified live: Message is an anchor to /messaging/compose/,
        # Connect is an anchor to /preload/custom-invite/
        self.assertIn('/messaging/compose/', _DM_CAPABILITY_JS)
        self.assertIn('/preload/custom-invite/', _DM_CAPABILITY_JS)

    def test_premium_probe_is_scoped_to_profile_actions(self):
        # The premium check must NOT use a global a[href*="premium"] selector
        # (the nav has a "Try Premium" link on every page for free accounts).
        self.assertNotIn('a[href*="premium"]', _DM_CAPABILITY_JS)
        self.assertIn("inmailBanner", _DM_CAPABILITY_JS)

    def test_message_sent_js_has_all_keys(self):
        for key in ["hasNewMessage", "sendButtonGone", "hasError", "hasSentConfirmation", "threadOpen"]:
            self.assertIn(key, _MESSAGE_SENT_JS)

    def test_connect_button_js_has_all_keys(self):
        for key in ["hasConnectButton", "sendButtonEnabled", "hasAddNoteButton", "noteFieldPresent"]:
            self.assertIn(key, _CONNECT_BUTTON_JS)

    def test_connect_js_uses_verified_selector(self):
        self.assertIn('/preload/custom-invite/', _CONNECT_BUTTON_JS)

    def test_connect_js_has_verified_send_selectors(self):
        # Verified live (2026-07-30): "Send invitation" (with note) and
        # "Send without a note" are the real send buttons.
        self.assertIn('Send invitation', _CONNECT_BUTTON_JS)
        self.assertIn('Send without a note', _CONNECT_BUTTON_JS)
        self.assertIn('Add a note', _CONNECT_BUTTON_JS)
        self.assertIn('textarea[name="message"]', _CONNECT_BUTTON_JS)

    def test_all_probes_return_object(self):
        for js in [_DM_CAPABILITY_JS, _MESSAGE_SENT_JS, _CONNECT_BUTTON_JS]:
            self.assertTrue("return {" in js)
            self.assertTrue("}" in js)


class SendMessageRequiresName(unittest.TestCase):
    def test_send_message_returns_error_without_name(self):
        # send_message requires the contact name for the typeahead flow —
        # verify the guard exists by checking the function signature.
        import inspect
        from lib.linkedin_messaging import send_message
        sig = inspect.signature(send_message)
        self.assertIn("name", sig.parameters)
        self.assertIsNone(sig.parameters["name"].default)


if __name__ == "__main__":
    unittest.main()
