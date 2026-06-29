"""Unit tests for field fingerprinting (apply/common/fingerprint.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.fingerprint import field_fingerprint


class Fingerprint(unittest.TestCase):
    def test_stable_for_same_field(self):
        f = {"label": "Authorized to work in Canada?", "options": ["Yes", "No"], "tag": "radio"}
        self.assertEqual(field_fingerprint(f), field_fingerprint(dict(f)))

    def test_label_changes_key(self):
        a = {"label": "Authorized to work in Canada?", "options": ["Yes", "No"]}
        b = {"label": "Authorized to work in the US?", "options": ["Yes", "No"]}
        self.assertNotEqual(field_fingerprint(a), field_fingerprint(b))

    def test_options_change_key(self):
        a = {"label": "Experience", "options": ["0-2", "3-5"]}
        b = {"label": "Experience", "options": ["0-2", "3-5", "6+"]}
        self.assertNotEqual(field_fingerprint(a), field_fingerprint(b))

    def test_option_order_does_not_matter(self):
        a = {"label": "Country", "options": ["Canada", "United States"]}
        b = {"label": "Country", "options": ["United States", "Canada"]}
        self.assertEqual(field_fingerprint(a), field_fingerprint(b))


if __name__ == "__main__":
    unittest.main()
