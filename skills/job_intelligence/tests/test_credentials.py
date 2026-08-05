"""test_credentials.py — the pure credential logic (lib/credentials.py).

No keyring, no real browser: these tests pin the pure functions — multi-tenant
domain keys, password masking, complexity scoring, platform rule resolution,
deterministic rule extraction from page text, and password validity. The
architecture survey flagged these call-site decisions as the untested seams;
they are now the test surface.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class DomainKey(unittest.TestCase):
    def test_multi_tenant_uses_full_host(self):
        from lib.credentials import _domain_from_url
        # Workday tenant is in the subdomain — keep the full hostname
        self.assertEqual(_domain_from_url("https://acme.wd5.myworkdayjobs.com/x"),
                         "acme.wd5.myworkdayjobs.com")
        self.assertEqual(_domain_from_url("https://x.greenhouse.io/j"),
                         "x.greenhouse.io")

    def test_corporate_collapses_www(self):
        from lib.credentials import _domain_from_url
        self.assertEqual(_domain_from_url("https://www.acme.com/j"), "www.acme.com")
        self.assertEqual(_domain_from_url("https://acme.com/j"), "acme.com")

    def test_bare_host(self):
        from lib.credentials import _domain_from_url
        self.assertEqual(_domain_from_url(""), "")


class Mask(unittest.TestCase):
    def test_masks_password(self):
        from lib.credentials import _mask
        self.assertNotIn("secret", _mask("secretpw"))
        self.assertIn("(len", _mask("secretpw"))

    def test_reveal_only_when_explicit(self):
        from lib.credentials import _mask
        self.assertEqual(_mask("pw", reveal=True), "pw")

    def test_empty(self):
        from lib.credentials import _mask
        self.assertEqual(_mask(""), "")


class Complexity(unittest.TestCase):
    def test_score_counts_conditions(self):
        from lib.credentials import _score_password_complexity
        self.assertEqual(_score_password_complexity("Abcdef12!xyz"), 6)  # all 6
        self.assertEqual(_score_password_complexity("abcdef"), 1)      # only lower
        self.assertEqual(_score_password_complexity("abcdefgh"), 2)    # len>=8 + lower

    def test_password_valid_rules(self):
        from lib.credentials import _password_valid
        self.assertTrue(_password_valid("Abcdef12!x", 8, True, True, True, True))
        self.assertFalse(_password_valid("abcdefgh", 8, True, True, True, True))  # no upper/digit/sym
        self.assertFalse(_password_valid("abc", 8, False, False, False, False))   # too short


class PlatformRuleResolution(unittest.TestCase):
    """pick_password_for_platform / gen_password_for_platform with page=None —
    the hardcoded _RULES table is the only source."""

    def test_picks_most_complex_acceptable(self):
        from lib.credentials import pick_password_for_platform
        pws = ["abc", "Abcdef12!", "Short1"]
        self.assertEqual(pick_password_for_platform("https://x.com", pws),
                         "Abcdef12!")

    def test_none_when_no_candidate_fits(self):
        from lib.credentials import pick_password_for_platform
        self.assertIsNone(pick_password_for_platform("https://x.com", ["abc"]))

    def test_workday_requires_symbol(self):
        """Workday's rule table requires a symbol — a strong-looking password
        without one must be rejected for workday but accepted elsewhere."""
        from lib.credentials import pick_password_for_platform
        strong_no_sym = "Abcdef12xyz"
        self.assertIsNone(pick_password_for_platform(
            "https://acme.wd5.myworkdayjobs.com", [strong_no_sym]))
        self.assertEqual(pick_password_for_platform(
            "https://boards.acme.io", [strong_no_sym]), strong_no_sym)

    def test_empty_pool_returns_none(self):
        from lib.credentials import pick_password_for_platform
        self.assertIsNone(pick_password_for_platform("https://x.com", []))


class DeterministicHints(unittest.TestCase):
    def _page_with_text(self, text):
        p = MagicMock()
        p.evaluate.return_value = text
        return p

    def test_extracts_min_length(self):
        from lib.credentials import _extract_password_hints_deterministic
        page = self._page_with_text("Password must be at least 10 characters long")
        h = _extract_password_hints_deterministic(page)
        self.assertEqual(h["min_len"], 10)

    def test_extracts_requirements(self):
        from lib.credentials import _extract_password_hints_deterministic
        page = self._page_with_text(
            "Must contain at least a uppercase letter, at least a number, "
            "and at least a special character")
        h = _extract_password_hints_deterministic(page)
        self.assertTrue(h["require_upper"])
        self.assertTrue(h["require_digit"])
        self.assertTrue(h["require_sym"])

    def test_none_when_no_hints(self):
        from lib.credentials import _extract_password_hints_deterministic
        page = self._page_with_text("Enter your password")
        self.assertIsNone(_extract_password_hints_deterministic(page))

    def test_none_on_page_none(self):
        from lib.credentials import _extract_password_hints_deterministic
        self.assertIsNone(_extract_password_hints_deterministic(None))

    def test_none_on_evaluate_error(self):
        from lib.credentials import _extract_password_hints_deterministic
        page = MagicMock()
        page.evaluate.side_effect = Exception("detached")
        self.assertIsNone(_extract_password_hints_deterministic(page))

    def test_hints_override_defaults_in_pick(self):
        """min_len from page text (10) must override the default 8."""
        from lib.credentials import pick_password_for_platform
        page = self._page_with_text("Password must be at least 10 characters long")
        pws = ["Abcdef12x", "Abcdef12xyz"]  # 8 vs 10 chars
        with patch("lib.credentials._extract_password_hints_via_llm",
                   return_value=None):
            picked = pick_password_for_platform("https://x.com", pws, page=page)
        self.assertEqual(picked, "Abcdef12xyz")


class FreeformRules(unittest.TestCase):
    def test_parse_freeform(self):
        from lib.credentials import _parse_freeform_rules
        r = _parse_freeform_rules("min 12 chars, 1 uppercase, 1 number, 1 symbol")
        self.assertEqual(r["min_len"], 12)
        self.assertTrue(r["require_upper"])
        self.assertTrue(r["require_digit"])
        self.assertTrue(r["require_sym"])


if __name__ == "__main__":
    unittest.main()
