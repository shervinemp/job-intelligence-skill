"""Unit tests for the adaptive combobox protocol (apply/strategies/combobox.py)."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class Norm(unittest.TestCase):
    def test_accents_and_case(self):
        from apply.strategies.combobox import _norm
        self.assertEqual(_norm("Université de Montréal"), "universite de montreal")
        self.assertEqual(_norm("  Yes, I  Am! "), "yes i am")

    def test_punctuation_collapsed(self):
        from apply.strategies.combobox import _norm
        self.assertEqual(_norm("C++ / C#"), "c c")


class TypingCandidates(unittest.TestCase):
    def test_full_and_stripped(self):
        from apply.strategies.combobox import _typing_candidates
        cands = _typing_candidates("Yes, I am legally authorized to work in Canada")
        self.assertEqual(cands[0], "Yes, I am legally authorized to work in Canada")
        self.assertEqual(cands[1], "I am legally authorized to work in Canada")

    def test_no_strip_when_not_yesno(self):
        from apply.strategies.combobox import _typing_candidates
        cands = _typing_candidates("University of Ottawa")
        self.assertEqual(cands, ["University of Ottawa"])

    def test_strip_no_prefix(self):
        from apply.strategies.combobox import _typing_candidates
        cands = _typing_candidates("No - I am not interested")
        self.assertEqual(cands[1], "I am not interested")


class Scoring(unittest.TestCase):
    def test_exact(self):
        from apply.strategies.combobox import _score_option
        self.assertEqual(_score_option("University of Ottawa", ["university of ottawa"]), 4)

    def test_prefix(self):
        from apply.strategies.combobox import _score_option
        self.assertEqual(_score_option("University of Ottawa - Main", ["university of ottawa"]), 3)

    def test_contains(self):
        from apply.strategies.combobox import _score_option
        self.assertEqual(_score_option("Master of Science (MSc)", ["msc"]), 2)

    def test_word_overlap(self):
        from apply.strategies.combobox import _score_option
        self.assertEqual(_score_option("I am legally authorized to work in Canada",
                                       ["yes i am legally authorized to work in canada"]), 2)

    def test_accent_insensitive(self):
        from apply.strategies.combobox import _score_option
        self.assertEqual(_score_option("Université de Montréal", ["universite de montreal"]), 4)

    def test_no_match(self):
        from apply.strategies.combobox import _score_option
        self.assertEqual(_score_option("Aalborg University", ["university of ottawa"]), 0)


class PickBest(unittest.TestCase):
    def test_picks_best_with_runner_up(self):
        from apply.strategies.combobox import _pick_best
        opts = [
            {"text": "Aalborg University", "id": "a"},
            {"text": "University of Ottawa", "id": "b"},
            {"text": "University of Oxford", "id": "c"},
        ]
        best, second = _pick_best(opts, ["University of Ottawa"])
        self.assertEqual(best["text"], "University of Ottawa")
        # Oxford shares 2/3 normalized words → score 2; margin 4-2 ≥ 1 → confident
        self.assertEqual(second, 2)
        self.assertGreaterEqual(best["score"] - second, 1)

    def test_tie_surfaces_runner_up(self):
        from apply.strategies.combobox import _pick_best
        opts = [
            {"text": "University of Ottawa", "id": "b"},
            {"text": "University of Ottawa Press", "id": "c"},
        ]
        best, second = _pick_best(opts, ["Ottawa"])
        # both contain "Ottawa" → 2 vs 2 → margin 0 → NOT confident
        self.assertEqual(best["score"], 2)
        self.assertEqual(second, 2)
        self.assertLess(best["score"] - second, 1)

    def test_no_match_none(self):
        from apply.strategies.combobox import _pick_best
        best, second = _pick_best([{"text": "Aalborg University"}], ["ottawa"])
        self.assertIsNone(best)


class FakePage:
    """Scripted Playwright page: evaluate dispatches on JS markers
    (//COLLECT, //SCROLLROOT, //SCROLLMOVE) so tests can script the
    exact option-set per protocol stage."""

    def __init__(self, typed_options=None, unfiltered_options=None,
                 menu_options=None, collect_budget=3):
        self.typed_options = typed_options or []
        self.unfiltered_options = unfiltered_options or []
        self.menu_options = menu_options if menu_options is not None else (typed_options or [])
        self.collect_budget = collect_budget  # how many COLLECT calls return menu/typed options
        self._collect_calls = 0
        self.evaluate_calls = []
        self.locators = {}
        self._keyboard = MagicMock()

    @property
    def keyboard(self):
        return self._keyboard

    def evaluate(self, js, arg=None):
        self.evaluate_calls.append((js[:60], arg))
        if "//LISTBOXROOT" in js:
            return ""
        if "//SCROLLMOVE" in js:
            return False
        if "//SCROLLROOT" in js:
            return ""
        if "//COLLECT" in js:
            self._collect_calls += 1
            if self._collect_calls <= self.collect_budget:
                return self.menu_options
            return self.unfiltered_options
        if "dispatchEvent" in js:
            return None
        if "querySelector(" in js and ".value = ''" in js:
            return None
        return None

    def locator(self, sel):
        if sel not in self.locators:
            el = MagicMock()
            el.count.return_value = 1
            el.first.count.return_value = 1
            el.first.click.return_value = None
            el.click.return_value = None
            self.locators[sel] = el
        return self.locators[sel]

    @property
    def url(self):
        return "https://example.com/form"

    @property
    def mouse(self):
        return MagicMock()


class FillFlow(unittest.TestCase):
    def _field(self):
        return {"label": "School", "tag": "INPUT", "type": "text",
                "_sel": "#school--0", "id": "school--0", "name": "",
                "placeholder": "", "autocomplete": "", "role": "combobox"}

    def test_typed_match_verified(self):
        from apply.strategies.combobox import fill
        page = FakePage(typed_options=[
            {"text": "University of Ottawa", "id": "opt-1", "x": 10, "y": 10}])
        field = self._field()
        with patch("apply.common.value_reader.AriaComboboxReader") as ac, \
             patch("apply.common.value_reader.ReactSelectReader") as rs, \
             patch("apply.common.value_reader.FuzzyComboboxReader") as fc:
            ac.return_value.read.return_value = "University of Ottawa"
            self.assertTrue(fill(page, field, "University of Ottawa"))
        self.assertEqual(field["_diag"]["reason"], "typed_match")
        self.assertNotIn("unverified", field["_diag"])

    def test_unfiltered_fallback_matches_phrasing(self):
        """Typing produces nothing; the FULL list contains the option with
        different phrasing (Work-Authorization class)."""
        from apply.strategies.combobox import fill
        placeholder = [{"text": "Select...", "id": "ph", "x": 1, "y": 1}]
        page = FakePage(
            typed_options=[],
            menu_options=placeholder,  # open_menu sees a placeholder option
            unfiltered_options=[
                {"text": "I require sponsorship now or in the future", "id": "o1", "x": 1, "y": 1},
                {"text": "I am legally authorized to work in Canada", "id": "o2", "x": 2, "y": 2},
            ],
            collect_budget=4,
        )
        field = self._field()
        with patch("apply.common.value_reader.AriaComboboxReader") as ac, \
             patch("apply.common.value_reader.ReactSelectReader") as rs, \
             patch("apply.common.value_reader.FuzzyComboboxReader") as fc:
            ac.return_value.read.return_value = None
            rs.return_value.read.return_value = None
            fc.return_value.read.return_value = "I am legally authorized to work in Canada"
            self.assertTrue(fill(page, field, "Yes, I am legally authorized to work in Canada"))
        self.assertTrue(field["_diag"]["reason"].startswith("unfiltered_match"))

    def test_no_option_match_fails_with_diag(self):
        from apply.strategies.combobox import fill
        page = FakePage(typed_options=[{"text": "Aalborg University", "id": "a", "x": 1, "y": 1}],
                        unfiltered_options=[{"text": "Aalborg University", "id": "a", "x": 1, "y": 1}])
        field = self._field()
        self.assertFalse(fill(page, field, "University of Ottawa"))
        self.assertEqual(field["_diag"]["reason"], "no_option_match")
        self.assertIn("options_seen", field["_diag"])

    def test_menu_never_opens(self):
        from apply.strategies.combobox import fill
        page = FakePage()
        page._collect_calls = 0

        def no_options(js, arg=None):
            return [] if 'role="option"' in js else None
        page.evaluate = no_options
        field = self._field()
        self.assertFalse(fill(page, field, "Anything"))
        self.assertEqual(field["_diag"]["reason"], "menu_closed")

    def test_no_selector(self):
        from apply.strategies.combobox import fill
        page = FakePage()
        field = {"label": "X", "tag": "INPUT", "type": "text"}
        self.assertFalse(fill(page, field, "v"))
        self.assertEqual(field["_diag"]["reason"], "no_selector")


class LLMFallback(unittest.TestCase):
    """Expectation-free last resort: deterministic scoring fails, the
    LLM picks from the REAL option texts."""

    def _field(self):
        return {"label": "Custom question", "tag": "INPUT", "type": "text",
                "_sel": "#q", "id": "q", "name": "",
                "placeholder": "", "autocomplete": "", "role": "combobox"}

    def test_llm_pick_parses_index(self):
        from apply.strategies.combobox import _llm_pick
        page = MagicMock()
        opts = [{"text": "Option A", "id": "a", "x": 1, "y": 1},
                {"text": "Option B", "id": "b", "x": 2, "y": 2}]
        with patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("1", None)):
            picked = _llm_pick(page, "#q", opts, "Question?", "Some answer")
        self.assertEqual(picked["text"], "Option B")

    def test_llm_pick_none_returns_none(self):
        from apply.strategies.combobox import _llm_pick
        page = MagicMock()
        opts = [{"text": "Option A", "id": "a", "x": 1, "y": 1}]
        with patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("NONE", None)):
            self.assertIsNone(_llm_pick(page, "#q", opts, "Q", "A"))

    def test_llm_pick_unavailable_returns_none(self):
        from apply.strategies.combobox import _llm_pick
        with patch("lib.ask_api.available", return_value=False):
            self.assertIsNone(_llm_pick(MagicMock(), "#q", [{"text": "A"}], "Q", "A"))

    def test_fill_uses_llm_when_deterministic_fails(self):
        """Deterministic scoring finds no confident option; the LLM picks
        one and it gets clicked and accepted."""
        from apply.strategies.combobox import fill
        placeholder = [{"text": "Select...", "id": "ph", "x": 1, "y": 1}]
        options = [
            {"text": "I am willing to relocate", "id": "r1", "x": 1, "y": 1},
            {"text": "I am not willing to relocate", "id": "r2", "x": 2, "y": 2},
        ]
        page = FakePage(typed_options=[], menu_options=placeholder,
                        unfiltered_options=options, collect_budget=3)
        field = self._field()
        with patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("0", None)), \
             patch("apply.common.value_reader.AriaComboboxReader") as ac, \
             patch("apply.common.value_reader.ReactSelectReader") as rs, \
             patch("apply.common.value_reader.FuzzyComboboxReader") as fc:
            ac.return_value.read.return_value = None
            rs.return_value.read.return_value = "I am willing to relocate"
            fc.return_value.read.return_value = None
            self.assertTrue(fill(page, field,
                                 "Currently open to relocation"))
        self.assertEqual(field["_diag"]["reason"], "llm_unfiltered")
        self.assertTrue(field["_diag"]["llm_tried"])

    def test_fill_fails_when_llm_unavailable(self):
        from apply.strategies.combobox import fill
        placeholder = [{"text": "Select...", "id": "ph", "x": 1, "y": 1}]
        page = FakePage(typed_options=[], menu_options=placeholder,
                        unfiltered_options=[{"text": "I am willing to relocate",
                                             "id": "r1", "x": 1, "y": 1}],
                        collect_budget=3)
        field = self._field()
        with patch("lib.ask_api.available", return_value=False):
            self.assertFalse(fill(page, field, "Currently open to relocation"))
        self.assertEqual(field["_diag"]["reason"], "no_option_match")
        self.assertTrue(field["_diag"]["llm_tried"])


if __name__ == "__main__":
    unittest.main()
