"""Tests for the OOD-adaptability items: i18n resolver layer, matrix
row-aware field keys, and multi-select list answers."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class I18nResolver(unittest.TestCase):
    """French-form vocabulary must resolve through the English rules."""

    def test_french_city(self):
        from apply.common.resolve import resolve
        prof = {"answers": {"city": "Ottawa"}}
        r = resolve("Ville", prof)
        self.assertEqual(r.value, "Ottawa")

    def test_french_country(self):
        from apply.common.resolve import resolve
        prof = {"answers": {"country": "Canada"}}
        r = resolve("Pays", prof)
        self.assertEqual(r.value, "Canada")

    def test_french_first_name(self):
        from apply.common.resolve import resolve
        prof = {"first_name": "Shervin"}
        r = resolve("Prénom", prof)
        self.assertEqual(r.value, "Shervin")

    def test_french_email(self):
        from apply.common.resolve import resolve
        prof = {"email": "a@b.c"}
        r = resolve("Adresse courriel", prof)
        self.assertEqual(r.value, "a@b.c")

    def test_french_work_auth(self):
        from apply.common.resolve import resolve
        prof = {"answers": {"authorized_to_work": "Yes"}}
        r = resolve("Autorisation de travail", prof)
        self.assertEqual(r.value, "Yes")

    def test_english_unaffected(self):
        from apply.common.resolve import resolve
        prof = {"first_name": "Shervin"}
        r = resolve("First Name", prof)
        self.assertEqual(r.value, "Shervin")
        r2 = resolve("City", {"answers": {"city": "Ottawa"}})
        self.assertEqual(r2.value, "Ottawa")

    def test_french_learned_mapping_roundtrip(self):
        from apply.common import resolve as R
        with patch.object(R, "_LEARNED_PATH",
                          os.path.join(__import__("tempfile").mkdtemp(),
                                       "fm.json")):
            R._learned_cache = None
            R.learn_mapping("Ville", "Ottawa", domain="")
            R.learn_mapping("Ville", "Ottawa", domain="")  # 2nd confirm
            r = R.resolve("Ville", {})
            self.assertEqual(r.value, "Ottawa")
            R._learned_cache = None


class MatrixRowKeys(unittest.TestCase):
    """Same-label fields on one page must not collapse into one."""

    def test_duplicate_keys_get_occurrence_suffix(self):
        from apply.act.helpers import _fill_with_playwright
        fields = [
            {"label": "Start date", "name": "q1", "tag": "INPUT",
             "type": "text", "_sel": "#q1"},
            {"label": "Start date", "name": "q2", "tag": "INPUT",
             "type": "text", "_sel": "#q2"},
        ]
        page = MagicMock()
        page.evaluate.return_value = ""  # pre-check read: empty value
        profile = {"answers": {"start_date": "2020-01"}}
        with patch("apply.strategies.dispatch.field_deterministic",
                   return_value=True), \
             patch("apply.act.helpers._build_ans_dict",
                   return_value={"Start date": "2020-01"}):
            filled, failed = _fill_with_playwright(page, fields, profile,
                                                   {}, "j")
        keys = [r["key"] for r in filled]
        self.assertEqual(len(set(keys)), 2, keys)
        self.assertIn("#q2", keys[1])


class MultiSelectList(unittest.TestCase):
    """List answers fill all values or fail (all-or-nothing)."""

    def test_list_loops_each_value(self):
        from apply.common.filler import fill_field
        page = MagicMock()
        with patch("apply.common.filler.SelectFiller.fill",
                   side_effect=[True, True]), \
             patch("apply.common.filler._read_element_value",
                   side_effect=["", "Python", "", "Go"]):
            ok, name = fill_field(page, {"tag": "SELECT", "type": "select-multiple",
                                         "name": "skills", "_sel": "#s"},
                                  ["Python", "Go"])
        self.assertTrue(ok)

    def test_list_fails_closed_on_partial(self):
        from apply.common.filler import fill_field
        page = MagicMock()
        with patch("apply.common.filler.SelectFiller.fill",
                   side_effect=[True, False]), \
             patch("apply.common.filler._read_element_value",
                   side_effect=["", "Python", ""]):
            ok, name = fill_field(page, {"tag": "SELECT", "type": "select-multiple",
                                         "name": "skills", "_sel": "#s"},
                                  ["Python", "Go"])
        self.assertFalse(ok)

    def test_empty_list_is_success(self):
        from apply.common.filler import fill_field
        ok, _ = fill_field(MagicMock(), {"tag": "SELECT", "_sel": "#s"}, [])
        self.assertTrue(ok)

    def test_dispatch_skips_format_validation_for_lists(self):
        from apply.strategies.dispatch import field_deterministic
        page = MagicMock()
        f = {"tag": "SELECT", "type": "select-multiple", "name": "skills",
             "_sel": "#s", "label": "Skills"}
        with patch("apply.common.filler.fill_field",
                   return_value=(True, "select")) as ff:
            ok = field_deterministic(page, f, ["Python", "Go"])
        self.assertTrue(ok)
        ff.assert_called_once()


if __name__ == "__main__":
    unittest.main()
