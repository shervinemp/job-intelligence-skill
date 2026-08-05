"""Per-field fill-method learning (META_FLOW.md Loop 4 gap #3).

The observation system learns which probe strategy wins per capability
profile, but not which FILL METHOD wins per (domain, field) — the Antigua
class. This store learns label → proven filler, scoped by host, S2-gated
(≥2 confirms before a preference).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JI_HOME", os.path.expanduser("~/.ji"))


class FieldMethodLearning(unittest.TestCase):
    def setUp(self):
        from apply.common.field_methods import clear_for_test
        clear_for_test()

    def tearDown(self):
        from apply.common.field_methods import clear_for_test
        clear_for_test()

    def test_single_confirm_no_preference(self):
        from apply.common.field_methods import record_method, prefer_method
        record_method("Phone country code", "combobox", "jobs.acme.com")
        self.assertEqual(prefer_method("Phone country code", "jobs.acme.com"), "")

    def test_two_confirms_prefer(self):
        from apply.common.field_methods import record_method, prefer_method
        record_method("Phone country code", "combobox", "jobs.acme.com")
        record_method("Phone country code", "combobox", "jobs.acme.com")
        self.assertEqual(prefer_method("Phone country code", "jobs.acme.com"),
                         "combobox")

    def test_host_scoped(self):
        from apply.common.field_methods import record_method, prefer_method
        record_method("Phone country code", "combobox", "jobs.acme.com")
        record_method("Phone country code", "combobox", "jobs.acme.com")
        # Same label on a DIFFERENT host has no preference.
        self.assertEqual(prefer_method("Phone country code", "boards.other.io"), "")
        # The acme preference still holds.
        self.assertEqual(prefer_method("Phone country code", "jobs.acme.com"),
                         "combobox")

    def test_conflicting_method_resets(self):
        from apply.common.field_methods import record_method, prefer_method
        record_method("Country", "combobox", "x.com")
        record_method("Country", "combobox", "x.com")
        # A conflicting method resets the count → no preference.
        record_method("Country", "select", "x.com")
        self.assertEqual(prefer_method("Country", "x.com"), "")

    def test_label_normalization(self):
        from apply.common.field_methods import record_method, prefer_method
        record_method("Phone country code*", "combobox", "a.com")
        record_method("  phone country code ", "combobox", "a.com")
        self.assertEqual(prefer_method("Phone country code*", "a.com"),
                         "combobox")

    def test_clear_host_drops_only_that_host(self):
        """B3: clearing a demoted host's preferences must not touch others."""
        from apply.common.field_methods import (record_method, prefer_method,
                                                clear_host)
        record_method("Country", "combobox", "boards.a.com")
        record_method("Country", "combobox", "boards.a.com")
        record_method("Country", "combobox", "boards.b.com")
        record_method("Country", "combobox", "boards.b.com")
        self.assertEqual(prefer_method("Country", "boards.a.com"), "combobox")
        self.assertEqual(prefer_method("Country", "boards.b.com"), "combobox")
        clear_host("https://boards.a.com/jobs/1")
        self.assertEqual(prefer_method("Country", "boards.a.com"), "")
        self.assertEqual(prefer_method("Country", "boards.b.com"), "combobox")

    def test_verify_strategy_learning(self):
        """META_FLOW Loop-4 gap #3: the verification STRATEGY (flag_class /
        combobox) that confirmed a (host, label) is learned, ≥2 confirms."""
        from apply.common.field_methods import (record_verify_strategy,
                                                prefer_verify_strategy)
        self.assertEqual(prefer_verify_strategy("Phone country code",
                                                "jobs.acme.com"), "")
        record_verify_strategy("Phone country code", "combobox", "jobs.acme.com")
        self.assertEqual(prefer_verify_strategy("Phone country code",
                                                "jobs.acme.com"), "")
        record_verify_strategy("Phone country code", "combobox", "jobs.acme.com")
        self.assertEqual(prefer_verify_strategy("Phone country code",
                                                "jobs.acme.com"), "combobox")
        # Different host is unaffected.
        self.assertEqual(prefer_verify_strategy("Phone country code",
                                                "other.io"), "")


if __name__ == "__main__":
    unittest.main()
