"""Unit tests for verify's deterministic confirmation-URL detection."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.verify import _is_confirmation_url


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


if __name__ == "__main__":
    unittest.main()
