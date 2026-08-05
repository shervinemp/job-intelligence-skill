"""Unit tests for verify's deterministic confirmation-URL detection."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.verify import _is_confirmation_url, _registrable_domain


class ConfirmationUrl(unittest.TestCase):
    def test_positive_paths(self):
        for url in (
            "https://boards.greenhouse.io/acme/jobs/123/confirmation",
            "https://jobs.lever.co/acme/abc/thanks/thank-you",
            "https://careers.example.com/apply?success=true",
            "https://example.com/application-received",
            "https://example.com/jobs/applied",
        ):
            self.assertTrue(_is_confirmation_url(url), url)

    def test_negative_paths(self):
        for url in (
            "https://boards.greenhouse.io/acme/jobs/123",
            "https://jobs.lever.co/acme/abc/apply",
            "https://example.com/complete-your-profile",  # 'complete' is intentionally NOT a token
            "https://www.linkedin.com/jobs/view/456",
            "",
        ):
            self.assertFalse(_is_confirmation_url(url), url)


class RegistrableDomain(unittest.TestCase):
    """Scopes the redirect scan: unrelated tabs (webmail etc.) must never match."""

    def test_same_site(self):
        self.assertEqual(_registrable_domain("https://careers.acme.com/x"), "acme.com")
        self.assertEqual(
            _registrable_domain("https://acme.wd5.myworkdayjobs.com/apply?y=1"),
            "myworkdayjobs.com",
        )

    def test_different_site_does_not_match(self):
        self.assertNotEqual(
            _registrable_domain("https://mail.google.com/inbox"),
            _registrable_domain("https://boards.greenhouse.io/acme"),
        )

    def test_degenerate_inputs(self):
        self.assertEqual(_registrable_domain(""), "")
        self.assertEqual(_registrable_domain("not a url"), "")
        self.assertEqual(_registrable_domain("https://localhost:9222/x"), "localhost")


class PlaywrightVerifyFlow(unittest.TestCase):
    """The 4 deterministic verification strategies in _playwright_verify —
    the rest of verify.py that was at ~15% coverage. Page + DB stubbed."""

    def _mk_page(self, url="https://x.com/j", text="", has_modal=True,
                 buttons=(), inputs=True):
        p = MagicMock()
        p.url = url
        p._text = text
        def _eval(js):
            j = js.strip()
            if 'role="dialog"' in j:
                return has_modal
            if "offsetParent" in j and "inputs" in j:
                return inputs
            if "textContent.trim()" in j:
                return list(buttons)
            return None
        p.evaluate.side_effect = _eval
        return p

    def test_success_text_marks_applied(self):
        page = self._mk_page(text="Congratulations, your application has been submitted")
        from apply.common import signals
        with patch("apply.common.signals.has_success_text", return_value=True), \
             patch("apply.verify.page_text", return_value=page._text), \
             patch("apply.verify.mark_applied") as mk, \
             patch("apply.verify.emit_status") as es, \
             patch("apply.verify.emit_next"):
            from apply.verify import _playwright_verify
            res = _playwright_verify(page, "aaaaaaaaaaaaaaaa",
                                     {"_last_submit": "submitted"})
        self.assertTrue(res)
        mk.assert_called_once()

    def test_confirmation_url_marks_applied(self):
        page = self._mk_page(url="https://x.com/application/confirmed")
        from apply.common import signals
        with patch("apply.common.signals.has_success_text", return_value=False), \
             patch("apply.verify.mark_applied") as mk, \
             patch("apply.verify.emit_status"), \
             patch("apply.verify.emit_next"):
            from apply.verify import _playwright_verify
            res = _playwright_verify(page, "aaaaaaaaaaaaaaaa",
                                     {"_last_submit": "submitted"})
        self.assertTrue(res)
        mk.assert_called_once()

    def test_modal_closed_no_inputs_marks_applied(self):
        page = self._mk_page(has_modal=False, inputs=False)
        with patch("apply.common.signals.has_success_text", return_value=False), \
             patch("apply.verify._is_confirmation_url", return_value=False), \
             patch("apply.verify.mark_applied") as mk, \
             patch("apply.verify.emit_status"), \
             patch("apply.verify.emit_next"):
            from apply.verify import _playwright_verify
            res = _playwright_verify(page, "aaaaaaaaaaaaaaaa",
                                     {"_last_submit": "submitted"})
        self.assertTrue(res)
        mk.assert_called_once()

    def test_applied_button_marks_applied(self):
        page = self._mk_page(has_modal=True, buttons=["Applied", "View"])
        with patch("apply.common.signals.has_success_text", return_value=False), \
             patch("apply.verify._is_confirmation_url", return_value=False), \
             patch("apply.verify.mark_applied") as mk, \
             patch("apply.verify.emit_status"), \
             patch("apply.verify.emit_next"):
            from apply.verify import _playwright_verify
            res = _playwright_verify(page, "aaaaaaaaaaaaaaaa",
                                     {"_last_submit": "submitted"})
        self.assertTrue(res)
        mk.assert_called_once()

    def test_inconclusive_on_validation_error(self):
        """A prior validation_error must NOT mark applied — the submit never
        happened, so verify must be inconclusive."""
        page = self._mk_page()
        from apply.verify import _playwright_verify
        with patch("apply.verify.page_text") as pt, \
             patch("apply.verify.mark_applied") as mk:
            res = _playwright_verify(page, "aaaaaaaaaaaaaaaa",
                                     {"_last_submit": "validation_error"})
        self.assertIsNone(res)
        mk.assert_not_called()

    def test_no_signal_no_mark(self):
        page = self._mk_page(has_modal=True, inputs=True, buttons=["Submit"])
        from apply.verify import _playwright_verify
        with patch("apply.common.signals.has_success_text", return_value=False), \
             patch("apply.verify._is_confirmation_url", return_value=False), \
             patch("apply.verify.mark_applied") as mk:
            res = _playwright_verify(page, "aaaaaaaaaaaaaaaa",
                                     {"_last_submit": "submitted"})
        self.assertFalse(res)
        mk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
