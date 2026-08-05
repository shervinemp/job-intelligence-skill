"""Trust boundary for URLs harvested from email bodies.

Job URLs are attacker-influenceable: anyone who can email the user picks a
string that the pipeline later hands to `curl -L` and to a Chrome profile
holding live LinkedIn/ATS cookies. These pin the refusals.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.url_safety import is_safe_url, allows_session_profile


class SchemeRules(unittest.TestCase):
    def test_http_and_https_allowed(self):
        for u in ("http://jobs.example.com/careers/123",
                  "https://jobs.example.com/careers/123"):
            ok, why = is_safe_url(u, resolve=False)
            self.assertTrue(ok, f"{u} rejected: {why}")

    def test_dangerous_schemes_refused(self):
        for u in ("file:///etc/passwd",
                  "javascript:fetch('//evil')",
                  "data:text/html;base64,PHNjcmlwdD4=",
                  "ftp://example.com/x"):
            ok, why = is_safe_url(u, resolve=False)
            self.assertFalse(ok, f"{u} should be refused")
            self.assertIn("scheme", why)

    def test_userinfo_refused(self):
        """https://linkedin.com@evil.example/ reads as LinkedIn to a human."""
        ok, why = is_safe_url("https://www.linkedin.com@evil.example/x",
                              resolve=False)
        self.assertFalse(ok)
        self.assertIn("credentials", why)


class AddressRules(unittest.TestCase):
    def test_loopback_refused(self):
        """http://127.0.0.1:9222 is the pipeline's own CDP port — reaching
        it would let an emailed link drive the pipeline's browser."""
        ok, why = is_safe_url("http://127.0.0.1:9222/json/new?url=x")
        self.assertFalse(ok)
        self.assertIn("loopback", why)

    def test_localhost_name_refused(self):
        ok, why = is_safe_url("http://localhost:9222/json")
        self.assertFalse(ok)
        self.assertIn("loopback", why)

    def test_private_ranges_refused(self):
        for u in ("http://10.0.0.5/x", "http://192.168.1.1/x",
                  "http://172.16.0.9/x"):
            ok, why = is_safe_url(u)
            self.assertFalse(ok, f"{u} should be refused")
            self.assertIn("private", why)

    def test_link_local_metadata_refused(self):
        ok, why = is_safe_url("http://169.254.169.254/latest/meta-data/")
        self.assertFalse(ok)
        self.assertIn("link-local", why)

    def test_unresolvable_host_refused(self):
        """We do not fetch what we cannot vet."""
        ok, why = is_safe_url(
            "https://no-such-host-should-ever-exist-ji-audit.invalid/x")
        self.assertFalse(ok)
        self.assertIn("resolve", why)


class SessionProfileScoping(unittest.TestCase):
    def test_known_ats_hosts_may_use_the_session_profile(self):
        for u in ("https://www.linkedin.com/jobs/view/123",
                  "https://boards.greenhouse.io/acme/jobs/1",
                  "https://acme.myworkdayjobs.com/en-US/careers/job/x"):
            self.assertTrue(allows_session_profile(u), u)

    def test_unknown_hosts_do_not_get_the_cookie_jar(self):
        for u in ("https://evil.example/careers/1",
                  "https://linkedin.com.evil.example/x",
                  "https://notgreenhouse.io/x"):
            self.assertFalse(allows_session_profile(u), u)

    def test_suffix_match_is_boundary_aware(self):
        """'notlever.co' must not match 'lever.co'."""
        self.assertFalse(allows_session_profile("https://notlever.co/x"))
        self.assertTrue(allows_session_profile("https://jobs.lever.co/x"))


if __name__ == "__main__":
    unittest.main()
