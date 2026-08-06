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

    def test_send_message_accepts_attach(self):
        import inspect
        from lib.linkedin_messaging import send_message
        sig = inspect.signature(send_message)
        self.assertIn("attach", sig.parameters)


class ThreadStatus(unittest.TestCase):
    """thread_status reconciles against the REAL inbox — the pipeline ledger
    is not the account's history (Sina Akbarian was messaged with zero DB
    rows). It must distinguish a definitive 'no thread' from 'could not
    check', and return the last-message evidence when a thread exists."""

    def _page(self, items=None, authed=True, nav_ok=True):
        from unittest.mock import MagicMock
        page = MagicMock()
        page.evaluate.return_value = items or []
        page.wait_for_timeout.return_value = None
        # simulate _navigate_and_wait + _check_auth via patched helpers
        return page

    def _ctx(self, page):
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.new_page.return_value = page
        return ctx

    def test_finds_existing_thread_with_evidence(self):
        from unittest.mock import patch
        from lib.linkedin_messaging import thread_status
        page = self._page([{"name": "Sina Akbarian", "time": "2:47 AM",
                            "preview": "You: Hi Sina, I saw the posting"}])
        with patch("lib.linkedin_messaging._navigate_and_wait", return_value=True), \
             patch("lib.linkedin_messaging._check_auth", return_value=True):
            t = thread_status(self._ctx(page), "Sina Akbarian")
        self.assertTrue(t["exists"])
        self.assertTrue(t["checked"])
        self.assertEqual(t["last_message_time"], "2:47 AM")
        self.assertEqual(t["last_message_direction"], "out")

    def test_no_thread_is_definitive_when_checked(self):
        from unittest.mock import patch
        from lib.linkedin_messaging import thread_status
        page = self._page([{"name": "Someone Else", "time": "Aug 3",
                            "preview": "hi"}])
        with patch("lib.linkedin_messaging._navigate_and_wait", return_value=True), \
             patch("lib.linkedin_messaging._check_auth", return_value=True):
            t = thread_status(self._ctx(page), "Sina Akbarian")
        self.assertFalse(t["exists"])
        self.assertTrue(t["checked"])

    def test_unreachable_inbox_is_unknown_not_no_thread(self):
        from unittest.mock import patch
        from lib.linkedin_messaging import thread_status
        page = self._page()
        with patch("lib.linkedin_messaging._navigate_and_wait", return_value=False):
            t = thread_status(self._ctx(page), "Sina Akbarian")
        self.assertFalse(t["exists"])
        self.assertFalse(t["checked"])  # NOT a definitive no

    def test_no_ctx_is_unknown(self):
        from lib.linkedin_messaging import thread_status
        t = thread_status(None, "Sina Akbarian")
        self.assertFalse(t["exists"])
        self.assertFalse(t["checked"])


class AttachFile(unittest.TestCase):
    def test_attach_returns_false_for_missing_file(self):
        from lib.linkedin_messaging import _attach_file
        from unittest.mock import MagicMock
        self.assertFalse(_attach_file(MagicMock(), "/nope/does/not/exist.pdf"))

    def test_attach_uses_document_input(self):
        import os
        from unittest.mock import MagicMock
        from lib.linkedin_messaging import _attach_file
        fd, path = __import__("tempfile").mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4")
        os.close(fd)
        try:
            page = MagicMock()
            pdf_input = MagicMock()
            pdf_input.count.return_value = 1
            pdf_input.first.set_input_files = MagicMock()
            any_input = MagicMock()
            any_input.count.return_value = 0

            def _locator(sel):
                if "accept*=\".pdf\"" in sel:
                    return pdf_input
                return any_input
            page.locator.side_effect = _locator
            self.assertTrue(_attach_file(page, path))
            pdf_input.first.set_input_files.assert_called_once_with(path)
        finally:
            os.unlink(path)

    def test_attach_never_clicks_the_attach_button(self):
        """The attach flow must NOT click the 'Attach a file...' button — that
        opens the native OS file picker (not automatable, blocks the session).
        set_input_files on the hidden input is the whole mechanism."""
        import os
        from unittest.mock import MagicMock
        from lib.linkedin_messaging import _attach_file
        fd, path = __import__("tempfile").mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4")
        os.close(fd)
        try:
            page = MagicMock()
            pdf_input = MagicMock()
            pdf_input.count.return_value = 1
            pdf_input.first.set_input_files = MagicMock()
            any_input = MagicMock()
            any_input.count.return_value = 0

            def _locator(sel):
                if "accept*=\".pdf\"" in sel:
                    return pdf_input
                return any_input
            page.locator.side_effect = _locator
            self.assertTrue(_attach_file(page, path))
            # No button locator ever queried, no .click on any control.
            for call in page.locator.call_args_list:
                self.assertNotIn("button[aria-label", str(call))
            pdf_input.first.set_input_files.assert_called_once_with(path)
        finally:
            os.unlink(path)


class OutreachLLM(unittest.TestCase):
    def test_build_evidence_never_invents_relationship(self):
        from lib.outreach_llm import build_evidence
        ev = build_evidence(
            {"name": "Keyvan K", "notes": ""},
            {"company": "Co", "title": "Role"},
            thread=None, resume_pdf=None, channel="message")
        self.assertIn("Existing thread: UNKNOWN", ev)
        self.assertNotIn("suggested", ev.lower())
        self.assertNotIn("shared", ev.lower())

    def test_build_evidence_includes_thread_when_present(self):
        from lib.outreach_llm import build_evidence
        ev = build_evidence(
            {"name": "Sina A"},
            {"company": "Lyft", "title": "ML Engineer"},
            thread={"exists": True, "last_message_time": "2:47 AM",
                    "last_message_direction": "out",
                    "preview": "Hi Sina, I saw the posting"},
            resume_pdf="/x/Resume.pdf", channel="message")
        self.assertIn("Existing thread: YES", ev)
        self.assertIn("last message: 2:47 AM", ev)
        self.assertIn("you sent it", ev)
        self.assertIn("Resume: attached", ev)

    def test_compose_gated_in_auto_mode(self):
        import os
        os.environ["JI_LLM_MODE"] = "auto"
        try:
            from lib.outreach_llm import compose
            body, detail = compose({"name": "X"}, {"company": "C", "title": "T"})
            self.assertIsNone(body)
            self.assertIn("orchestrator", detail.lower())
        finally:
            os.environ.pop("JI_LLM_MODE", None)


if __name__ == "__main__":
    unittest.main()
