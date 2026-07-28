"""Unit tests for fill-time value validation (apply/common/validate.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.validate import value_matches_option, validate_value


class MatchOption(unittest.TestCase):
    def test_no_options_is_unconstrained(self):
        self.assertTrue(value_matches_option("anything", []))

    def test_exact_and_substring(self):
        opts = ["Yes", "No", "Prefer not to say"]
        self.assertTrue(value_matches_option("yes", opts))
        self.assertTrue(value_matches_option("prefer not", opts))

    def test_no_match(self):
        self.assertFalse(value_matches_option("Maybe", ["Yes", "No"]))


class ValidateValue(unittest.TestCase):
    def test_empty_is_invalid(self):
        ok, _ = validate_value({"tag": "INPUT"}, "")
        self.assertFalse(ok)

    def test_option_in_and_out(self):
        f = {"tag": "SELECT", "options": ["Canada", "United States"]}
        self.assertTrue(validate_value(f, "Canada")[0])
        self.assertFalse(validate_value(f, "Mexico")[0])

    def test_email(self):
        f = {"tag": "INPUT", "type": "email"}
        self.assertTrue(validate_value(f, "john.smith@example.com")[0])
        self.assertFalse(validate_value(f, "not-an-email")[0])

    def test_phone_by_label(self):
        f = {"tag": "INPUT", "label": "Phone number"}
        self.assertTrue(validate_value(f, "613-555-0100")[0])
        self.assertFalse(validate_value(f, "12")[0])

    def test_number(self):
        f = {"tag": "INPUT", "type": "number"}
        self.assertTrue(validate_value(f, "95000")[0])
        self.assertFalse(validate_value(f, "lots")[0])

    def test_plain_text_passes(self):
        self.assertTrue(validate_value({"tag": "INPUT", "label": "First name"}, "John")[0])

    # ── Reverse URL check ─────────────────────────────────────────────

    def test_reverse_url_rejects_url_in_non_url_field(self):
        f = {"tag": "INPUT", "label": "Location"}
        ok, _ = validate_value(f, "https://linkedin.com/in/johnsmith")
        self.assertFalse(ok)

    def test_reverse_url_passes_url_in_url_typed_field(self):
        f = {"tag": "INPUT", "type": "url"}
        ok, _ = validate_value(f, "https://linkedin.com/in/johnsmith")
        self.assertTrue(ok)

    def test_reverse_url_passes_url_with_website_in_label(self):
        f = {"tag": "INPUT", "label": "Company website"}
        ok, _ = validate_value(f, "https://mycompany.com")
        self.assertTrue(ok)

    def test_reverse_url_passes_url_with_profile_in_label(self):
        f = {"tag": "INPUT", "label": "LinkedIn Profile"}
        ok, _ = validate_value(f, "https://linkedin.com/in/johnsmith")
        self.assertTrue(ok)

    def test_reverse_url_passes_url_with_link_in_label(self):
        f = {"tag": "INPUT", "label": "Portfolio link"}
        ok, _ = validate_value(f, "https://github.com/johnsmith")
        self.assertTrue(ok)

    def test_reverse_url_passes_url_with_portfolio_in_label(self):
        f = {"tag": "INPUT", "label": "Portfolio URL"}
        ok, _ = validate_value(f, "https://john.dev")
        self.assertTrue(ok)

    def test_reverse_url_passes_url_with_site_in_label(self):
        f = {"tag": "INPUT", "label": "Personal site"}
        ok, _ = validate_value(f, "https://john.dev")
        self.assertTrue(ok)

    def test_reverse_url_rejects_url_in_label_like_blog(self):
        f = {"tag": "INPUT", "label": "Blog"}
        ok, _ = validate_value(f, "https://myblog.com")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
