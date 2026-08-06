"""Tests for lib/inbox.py — inbox-read auth completion.

The safety contract: extraction is fail-closed. A code is only extracted when
a strong keyword is adjacent to a standalone 4-8 digit number; a verification
link is only returned when its text says verify/confirm/activate AND its host
attributes to the auth domain or a known verify host. No guessing.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.inbox import (
    extract_security_code,
    extract_verification_link,
    find_security_email,
    clean_text,
)


class CleanText(unittest.TestCase):
    def test_strips_html(self):
        self.assertIn("your code",
                      clean_text("From: x\nSubject: y\n\n<div>your <b>code</b></div>"))
        self.assertNotIn("<", clean_text("<p>hi</p>"))

    def test_strips_headers(self):
        raw = "From: no-reply@workday.com\nSubject: Verify\nDate: today\n\nBody here"
        self.assertIn("Body here", clean_text(raw))
        self.assertNotIn("From:", clean_text(raw))


class ExtractCode(unittest.TestCase):
    def test_code_after_keyword(self):
        self.assertEqual(
            extract_security_code("Your verification code is 123456. It expires soon."),
            "123456")

    def test_code_before_keyword(self):
        self.assertEqual(
            extract_security_code("Enter 482913 to complete your security code step."),
            "482913")

    def test_otp_variant(self):
        self.assertEqual(
            extract_security_code("Your one-time password (OTP): 736521"),
            "736521")

    def test_bare_digits_in_prose_not_extracted(self):
        """A number in plain prose with no auth keyword must not be read as a
        code — false-positive extraction would enter a wrong value."""
        self.assertIsNone(
            extract_security_code("We processed 12 items on 2026-08-06 and shipped 7 orders."))

    def test_short_number_not_extracted(self):
        self.assertIsNone(extract_security_code("Your code is 42. Now verify."))

    def test_empty_returns_none(self):
        self.assertIsNone(extract_security_code(""))
        self.assertIsNone(extract_security_code(None))


class ExtractLink(unittest.TestCase):
    _BODY = ("From: no-reply@workday.com\n\n"
             "<a href=\"https://wd5.myworkday.com/acme/verify?token=abc\">"
             "Verify your email</a>")

    def test_verify_link_attributes_to_auth_domain(self):
        link = extract_verification_link(self._BODY, "https://acme.wd5.myworkday.com/")
        self.assertTrue(link and link.startswith("https://wd5.myworkday.com/"))

    def test_link_without_verify_text_rejected(self):
        body = ("From: x\n\n"
                "<a href=\"https://evil.example.com/phish?t=1\">Click here</a>")
        self.assertIsNone(extract_verification_link(body, "https://acme.com/"))

    def test_unrelated_host_rejected(self):
        body = ("From: x\n\n"
                "<a href=\"https://evil.example.com/verify?t=1\">Verify your email</a>")
        self.assertIsNone(extract_verification_link(body, "https://acme.com/"))

    def test_known_verify_host_allowed(self):
        body = ("From: x\n\n"
                "<a href=\"https://myworkdayjobs.com/acme/verify?t=1\">Confirm email</a>")
        self.assertTrue(extract_verification_link(body, "https://acme.com/"))


class FindSecurityEmail(unittest.TestCase):
    def _search(self, msgs):
        return patch("lib.inbox.search_mail", return_value=msgs), \
            patch("lib.inbox.fetch_body",
                  return_value="Your verification code is 555666.")

    def test_finds_code_email_for_domain(self):
        msgs = [{"id": "m1", "date": "d", "from": "no-reply@workday.com",
                 "subject": "Your verification code"}]
        s, f = self._search(msgs)
        with s, f, patch("lib.inbox.search_mail", return_value=msgs):
            res = find_security_email("https://acme.wd5.myworkday.com/", kind="code")
        self.assertIsNotNone(res)
        self.assertEqual(res["code"], "555666")

    def test_no_code_email_returns_none(self):
        with patch("lib.inbox.search_mail", return_value=[]):
            self.assertIsNone(find_security_email("https://acme.com/", kind="code"))

    def test_query_scoped_to_domain(self):
        """The search must be scoped from:{registrable-domain} — never an open
        inbox search that could surface an unrelated code."""
        seen = {}

        def _fake_search(q, max_results=8, timeout=60):
            seen["q"] = q
            return []

        with patch("lib.inbox.search_mail", side_effect=_fake_search):
            find_security_email("https://wd5.myworkday.com/acme/job/1", kind="code")
        self.assertIn("from:myworkday.com", seen["q"])


if __name__ == "__main__":
    unittest.main()
