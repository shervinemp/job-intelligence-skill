"""Unit tests for verify's deterministic confirmation-URL detection."""
import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
