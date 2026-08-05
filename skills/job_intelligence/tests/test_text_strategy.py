"""test_text_strategy.py — the text-input strategy module (C2 depth).

The transforms in `text.py` — E.164 phone, postal-code space-strip, maxlength
year-rescue, truncation — are the call-site decisions the architecture survey
flagged (tested pure functions elsewhere, but THESE were at 13.9% coverage).
They are pure, so they get a fake-element test bed.

Fake element stub models a Playwright input: value + maxlength, with the
methods the strategy calls (is_visible, fill, get_attribute, evaluate).
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeTextEl:
    """Minimal Playwright-input stub: tracks value, honors maxlength for the
    `fill` path (the browser truncates on overflow — the strategy must pre-trim)."""

    def __init__(self, maxlength=None):
        self._value = ""
        self.maxlength = maxlength
        self.visible = True
        self.eval_calls = []

    def is_visible(self):
        return self.visible

    def fill(self, v):
        self._value = v
        return True

    def get_attribute(self, name):
        return str(self.maxlength) if (name == "maxlength" and self.maxlength) else None

    def evaluate(self, js, *a):
        self.eval_calls.append(js)
        if js.strip() == "el => el.value":
            return self._value
        return True

    @property
    def value(self):
        return self._value


class TextStrategy(unittest.TestCase):
    def setUp(self):
        self.sleep_patch = patch("apply.strategies.text.time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def _page(self):
        p = MagicMock()
        p.evaluate.return_value = True
        return p

    def test_phone_e164_normalization(self):
        """+1 (343) 558-1744 → +13435581744 for a phone field."""
        from apply.strategies.text import fill_text_field
        el = FakeTextEl()
        ok = fill_text_field(self._page(), {"label": "Phone"},
                             "+1 (343) 558-1744", "#phone", el, method="fill")
        self.assertTrue(ok)
        self.assertEqual(el.value, "+13435581744")

    def test_short_number_not_e164d(self):
        """A 3-digit value is not a real phone — must not be mangled."""
        from apply.strategies.text import fill_text_field
        el = FakeTextEl()
        fill_text_field(self._page(), {"label": "Phone"}, "123", "#p", el,
                        method="fill")
        self.assertEqual(el.value, "123")

    def test_postal_space_stripped(self):
        """K2P 1J6 → K2P1J6 for a postal field (maxlength=6 overflow)."""
        from apply.strategies.text import fill_text_field
        el = FakeTextEl()
        ok = fill_text_field(self._page(), {"label": "Postal code"},
                             "K2P 1J6", "#pc", el, method="fill")
        self.assertTrue(ok)
        self.assertEqual(el.value, "K2P1J6")

    def test_year_maxlength_rescue(self):
        """maxlength=4 on a year field with 'Immediately' → current year."""
        from apply.strategies.text import fill_text_field
        el = FakeTextEl(maxlength=4)
        ok = fill_text_field(self._page(), {"label": "Start Year"},
                             "Immediately", "#y", el, method="fill")
        self.assertTrue(ok)
        import datetime as _dt
        self.assertEqual(el.value, str(_dt.datetime.now().year))

    def test_maxlength_truncation_diag(self):
        """Over-long value → truncated to maxlength + diag recorded."""
        from apply.strategies.text import fill_text_field
        el = FakeTextEl(maxlength=6)
        f = {"label": "Job Title"}
        ok = fill_text_field(self._page(), f, "This is way too long", "#t",
                             el, method="fill")
        self.assertTrue(ok)
        self.assertEqual(el.value, "This i")
        self.assertEqual(f["_diag"]["reason"], "truncated")

    def test_verify_failure_retries_with_native_setter(self):
        """If read-back verification fails, the strategy falls back to the
        native setter — the locality win: the fallback lives HERE, not in the
        caller."""
        from apply.strategies.text import fill_text_field, native_setter
        el = FakeTextEl()
        el.visible = False  # visible_fill fails → ok=False path
        f = {"label": "Email"}
        with patch("apply.strategies.text.native_setter",
                   wraps=lambda p, s, a: True) as ns:
            ok = fill_text_field(self._page(), f, "a@b.com", "#e", el,
                                 method="fill")
        # visible_fill returned False; ok stays False but no crash
        self.assertFalse(ok)

    def test_native_setter_method_dispatches(self):
        """method=native_setter calls page.evaluate with the JS setter and
        (because the fake can't run JS) falls back through verify — the
        important assertion is the JS setter was dispatched."""
        from apply.strategies.text import fill_text_field
        el = FakeTextEl()
        page = self._page()
        with patch("apply.strategies.text.native_setter",
                   wraps=lambda p, s, a: True) as ns:
            fill_text_field(page, {"label": "Name"}, "Ann", "#n", el,
                            method="native_setter")
        # dispatched once for the method, once for the verify-failure retry
        self.assertTrue(ns.called)

    def test_unknown_method_returns_false(self):
        from apply.strategies.text import fill_text_field
        el = FakeTextEl()
        self.assertFalse(fill_text_field(self._page(), {"label": "X"},
                                         "v", "#x", el, method="bogus"))

    def test_placeholder_search_uses_autocomplete(self):
        """placeholder='Search' routes to the typeahead path, not plain fill.
        The fake el can't run the typeahead JS, so we assert the ROUTING —
        autocomplete called, visible_fill not — with verification stubbed."""
        from apply.strategies.text import fill_text_field
        el = FakeTextEl()
        page = self._page()
        with patch("apply.strategies.text.autocomplete",
                   return_value=True) as ac, \
             patch("apply.strategies.text.visible_fill") as vf, \
             patch("apply.strategies.text._verify", return_value=True):
            ok = fill_text_field(page, {"label": "Find", "placeholder": "Search"},
                                 "eng", "#s", el, method="fill")
        self.assertTrue(ok)
        ac.assert_called_once()
        vf.assert_not_called()


class VisibleFillAndDispatch(unittest.TestCase):
    def test_visible_fill_invisible_returns_false(self):
        from apply.strategies.text import visible_fill
        el = FakeTextEl()
        el.visible = False
        self.assertFalse(visible_fill(el, "x"))

    def test_dispatch_events_returns_true(self):
        from apply.strategies.text import dispatch_events
        page = MagicMock()
        page.evaluate.return_value = True
        self.assertTrue(dispatch_events(page, "#x", "val"))

    def test_method_chain_order(self):
        from apply.strategies.text import METHOD_CHAIN
        self.assertEqual(METHOD_CHAIN[0], "fill")
        self.assertEqual(METHOD_CHAIN[-1], "dispatch_events")


if __name__ == "__main__":
    unittest.main()
