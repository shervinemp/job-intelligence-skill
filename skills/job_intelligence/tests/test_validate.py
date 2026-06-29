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
        self.assertTrue(validate_value(f, "b@x.com")[0])
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
        self.assertTrue(validate_value({"tag": "INPUT", "label": "First name"}, "Bilal")[0])


if __name__ == "__main__":
    unittest.main()
