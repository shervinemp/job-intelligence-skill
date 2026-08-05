"""Unit tests for the lib/automation reusable core."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class ObsLib(unittest.TestCase):
    def test_round_trip(self):
        from lib.automation import obs
        tmp = tempfile.mkdtemp()
        with patch("lib.automation.obs.JI_HOME", tmp):
            run_id, path = obs.begin_run("test")
            obs.obs("actor", "action", jid="j1", target="t",
                    pre="a", post="b", outcome="ok", detail="d")
            obs.end_run()
            events = obs.load(run_id)
        self.assertEqual(len(events), 3)  # begin + action + end
        self.assertEqual(events[1]["actor"], "actor")
        self.assertEqual(events[1]["pre"], "a")
        self.assertEqual(events[1]["post"], "b")


class NormalizeLib(unittest.TestCase):
    def test_accents_and_case(self):
        from lib.automation.normalize import norm
        self.assertEqual(norm("Université de Montréal"), "universite de montreal")
        self.assertEqual(norm("  Yes, I  Am! "), "yes i am")

    def test_empty(self):
        from lib.automation.normalize import norm
        self.assertEqual(norm(None), "")
        self.assertEqual(norm(""), "")


class LLMPick(unittest.TestCase):
    def test_picks_index(self):
        from lib.automation.llm import pick_option
        opts = [{"text": "A"}, {"text": "B"}]
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("1", None)):
            self.assertEqual(pick_option(opts, "Q?", "ans")["text"], "B")

    def test_none_reply(self):
        from lib.automation.llm import pick_option
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("NONE", None)):
            self.assertIsNone(pick_option([{"text": "A"}], "Q", "A"))

    def test_unavailable(self):
        from lib.automation.llm import pick_option
        with patch("lib.ask_api.available", return_value=False):
            self.assertIsNone(pick_option([{"text": "A"}], "Q", "A"))

    def test_out_of_range_index(self):
        from lib.automation.llm import pick_option
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("9", None)):
            self.assertIsNone(pick_option([{"text": "A"}], "Q", "A"))


class DiffLib(unittest.TestCase):
    def _h(self, fields, filled):
        return {"summary": {"filled": filled},
                "fields": [{"label": l, "outcome": o} for l, o in fields]}

    def test_detects_regression_and_improvement(self):
        from lib.automation.diff import compare_handoffs
        old = self._h([("A", "filled"), ("B", "failed"), ("C", "filled")], 2)
        new = self._h([("A", "failed"), ("B", "filled"), ("C", "filled")], 2)
        d = compare_handoffs(new, old)
        self.assertEqual([x[0] for x in d["regressed"]], ["A"])
        self.assertEqual(d["improved"], ["B"])
        self.assertEqual(d["filled_before"], 2)

    def test_load_handoffs_newest_first(self):
        from lib.automation.diff import load_handoffs
        tmp = tempfile.mkdtemp()
        d = os.path.join(tmp, "j1", "handoffs")
        os.makedirs(d)
        for name in ("20260101_000000.json", "20260102_000000.json"):
            with open(os.path.join(d, name), "w") as f:
                json.dump({"ts": name}, f)
        hs = load_handoffs("j1", tmp)
        self.assertEqual(len(hs), 2)
        self.assertIn("20260102", hs[0]["ts"])


class DossierLib(unittest.TestCase):
    def test_writes_and_keeps_history(self):
        from lib.automation.dossier import write_dossier
        tmp = tempfile.mkdtemp()
        path = write_dossier(
            "j1", tmp,
            summary={"filled": 1, "failed": 0, "skipped_optional": 0},
            fields=[{"label": "A", "outcome": "filled"}],
            blockers=[], decisions=[], mode="shadow", url="u", run_id="r1")
        self.assertTrue(os.path.exists(path))
        doc = json.load(open(path, encoding="utf-8"))
        self.assertEqual(doc["summary"]["filled"], 1)
        self.assertEqual(doc["fields"][0]["label"], "A")
        self.assertEqual(doc["run_id"], "r1")  # timeline linkage
        # history retained
        self.assertEqual(len(os.listdir(os.path.join(tmp, "j1", "handoffs"))), 1)

    def test_merge_check_updates_dossier(self):
        from lib.automation.dossier import write_dossier, merge_check
        tmp = tempfile.mkdtemp()
        write_dossier("j1", tmp, summary={}, fields=[],
                      blockers=[], decisions=[])
        merge_check("j1", tmp, passed=False,
                    errors=[{"label": "X", "reason": "required empty"}],
                    warnings=[], infos=[])
        doc = json.load(open(os.path.join(tmp, "j1", "handoff.json"),
                             encoding="utf-8"))
        self.assertFalse(doc["check"]["passed"])
        self.assertEqual(doc["check"]["errors"][0]["label"], "X")


class StaleVsDossier(unittest.TestCase):
    def test_ready_record_stale_when_dossier_has_gaps(self):
        from lib.automation.diff import stale_vs_dossier
        rec = {"outcome": "held_shadow"}
        dossier = {"summary": {"failed": 8, "filled": 19},
                   "blockers": []}
        self.assertTrue(stale_vs_dossier(rec, dossier))

    def test_not_stale_when_consistent(self):
        from lib.automation.diff import stale_vs_dossier
        rec = {"outcome": "held_shadow"}
        dossier = {"summary": {"failed": 0, "filled": 19},
                   "blockers": []}
        self.assertFalse(stale_vs_dossier(rec, dossier))

    def test_stopped_record_never_stale(self):
        from lib.automation.diff import stale_vs_dossier
        self.assertFalse(stale_vs_dossier({"outcome": "stopped"}, None))


class HandoffVocabulary(unittest.TestCase):
    """The unified outcome vocabulary (kind) + honest counts."""

    def test_write_handoff_kinds_and_counts(self):
        from apply.act.fill import _write_handoff
        from lib.automation.obs import current_run_id
        tmp = tempfile.mkdtemp()
        filled = [{"label": "A", "answer": "x", "unverified": False,
                   "method": "deterministic"},
                  {"label": "Country", "answer": "Canada +1",
                   "unverified": True, "method": "combobox"}]
        failed = [
            {"label": "Company", "_why": "no_answer", "required": True,
             "attempted": "", "_sel": "#c", "_diag": {}},
            {"label": "Optional", "_why": "no_answer", "required": False,
             "attempted": "", "_sel": "#o", "_diag": {}},
            {"label": "Work Auth", "_why": "fill_failed", "required": True,
             "attempted": "Yes...", "_sel": "#w",
             "_diag": {"method": "combobox", "reason": "no_option_match"}},
        ]
        with patch("apply.act.fill.RESULTS_DIR", tmp):
            _write_handoff("j1", "u", filled, failed, {}, mode="shadow")
        doc = json.load(open(os.path.join(tmp, "j1", "handoff.json"),
                             encoding="utf-8"))
        kinds = {f["label"]: f["kind"] for f in doc["fields"]}
        self.assertEqual(kinds["A"], "verified")
        self.assertEqual(kinds["Country"], "unverified")
        self.assertEqual(kinds["Company"], "needs_data")
        self.assertEqual(kinds["Optional"], "needs_data")
        self.assertEqual(kinds["Work Auth"], "rejected_by_form")
        # Honest mutually-exclusive counts: 2 filled, 2 failed
        # (Company + Work Auth), 1 skipped-optional (Optional).
        self.assertEqual(doc["summary"]["filled"], 2)
        self.assertEqual(doc["summary"]["failed"], 2)
        self.assertEqual(doc["summary"]["skipped_optional"], 1)
        self.assertTrue(doc["run_id"] or current_run_id())


class LLMPolicyTest(unittest.TestCase):
    """The ask_api contract: deterministic core is the source of truth;
    the local LLM is a SELECTIVE escape hatch (vision + code-weak spots
    only) — never a default reviewer of the deterministic core."""

    def _set(self, mode):
        from unittest.mock import patch
        if mode is None:
            return patch.dict("os.environ", {}, clear=False)
        return patch.dict("os.environ", {"JI_LLM_MODE": mode})

    def test_default_auto(self):
        from lib.automation.llm import allow
        with self._set(None):
            self.assertEqual(allow("vision"), True)          # orchestrator can't see
            self.assertEqual(allow("option_pick"), False)    # orchestrator decides
            self.assertEqual(allow("gap_fill"), False)       # orchestrator decides
            self.assertEqual(allow("batch_verify"), False)   # LLM re-reviews ALL
            self.assertEqual(allow("verify_reads"), False)   # LLM verifies ALL
            self.assertEqual(allow("auto_retry"), False)     # pipeline never LLM-retries

    def test_off_kills_everything(self):
        from lib.automation.llm import allow
        with self._set("off"):
            for kind in ("vision", "option_pick", "gap_fill",
                         "batch_verify", "verify_reads", "auto_retry"):
                self.assertFalse(allow(kind), kind)

    def test_on_allows_everything(self):
        from lib.automation.llm import allow
        with self._set("on"):
            for kind in ("vision", "option_pick", "gap_fill",
                         "batch_verify", "verify_reads", "auto_retry"):
                self.assertTrue(allow(kind), kind)

    def test_unknown_kind_false_in_auto(self):
        from lib.automation.llm import allow
        with self._set(None):
            self.assertFalse(allow("nonsense"))

    def test_pick_option_gated_off_without_calling_api(self):
        """Policy off must short-circuit BEFORE touching ask_api — the
        escape hatch is closed, not 'tried and failed'."""
        from lib.automation.llm import pick_option
        from unittest.mock import patch as _patch
        opts = [{"text": "Canada"}]
        with self._set("off"), \
             _patch("lib.automation.llm.allow", return_value=False) as g, \
             _patch("lib.ask_api.available", side_effect=AssertionError("must not call")):
            self.assertIsNone(pick_option(opts, "Country", "Canada"))
        g.assert_called_once_with("option_pick")


class FakeSelectEl:
    """A minimal fake Playwright element modeling a native <select> where the
    option VALUE differs from its TEXT (country pickers: value='CA',
    text='Canada (+1)'). Records evaluate calls so tests can assert the
    selection went through el.options + selectedIndex, not el.value=<text>."""

    def __init__(self, options):
        # options: list of (value, text)
        self._options = [{"value": v, "text": t} for v, t in options]
        self.selected_index = -1
        self.value = ""
        self.evaluate_calls = []

    def evaluate(self, js, *args):
        self.evaluate_calls.append(js)
        if "'value': o.value" in js:
            return [dict(o) for o in self._options]
        if "selectedIndex = idx" in js:
            idx = args[0][1]
            self.selected_index = idx
            self.value = self._options[idx]["value"]
            return True
        if "o.textContent.trim()" in js:
            return [o["text"] for o in self._options]
        if js.strip() == "el => el.value":
            return self.value
        if "selectedIndex = opt.index" in js:
            opt_text = args[0][1]
            for i, o in enumerate(self._options):
                if o["text"] == opt_text or opt_text in o["text"]:
                    self.selected_index = i
                    self.value = o["value"]
                    return True
            return False
        return None

    def select_option(self, value):
        for i, o in enumerate(self._options):
            if o["value"] == value or o["text"] == value:
                self.selected_index = i
                self.value = o["value"]
                return
        raise Exception(f"no option {value!r}")


class NativeSelectStrategy(unittest.TestCase):
    """CURVEBALL B: the lazy native <select> success path. The visible DOM can
    be truncated (partial A-list) and the option value differs from its text,
    so the fix selects by INDEX from the authoritative el.options list."""

    def _field(self, opts, ans, method=None, country="canada"):
        from apply.strategies.select import try_select_tag
        el = FakeSelectEl(opts)
        f = {"tag": "SELECT", "options": [t for _, t in opts][:3],
             "value": "", "_country": country}
        ok = try_select_tag(el, f, ans, method=method)
        return ok, el

    def test_value_differs_from_text_selects_by_index(self):
        """value='CA', text='Canada (+1)' — the option is the last one so a
        truncated A-list DOM would not contain it; el.options must."""
        opts = [("AF", "Afghanistan"), ("AL", "Albania"), ("CA", "Canada (+1)")]
        ok, el = self._field(opts, "Canada", method="native_setter")
        self.assertTrue(ok, "native_setter must succeed on index-based select")
        self.assertEqual(el.selected_index, 2)
        self.assertEqual(el.value, "CA")

    def test_select_option_method_still_works(self):
        opts = [("AF", "Afghanistan"), ("CA", "Canada (+1)")]
        ok, el = self._field(opts, "Canada", method=None)
        self.assertTrue(ok)
        self.assertEqual(el.value, "CA")

    def test_bare_code_known_country_not_loaded_is_no_match(self):
        """Antigua guard: country known but not among loaded options must NOT
        fall through to a bare-code pick (would select Antigua for +1)."""
        from apply.strategies.select import _pick_option
        opts = ["Algeria (+213)", "Angola (+244)"]  # Canada absent
        self.assertIsNone(_pick_option(opts, "+1", country_words=["canada"]))

    def test_bare_code_known_country_loaded_picks_country(self):
        from apply.strategies.select import _pick_option
        opts = ["Antigua and Barbuda (+1-268)", "Canada (+1)"]
        self.assertEqual(_pick_option(opts, "+1", country_words=["canada"]),
                         "Canada (+1)")

    def test_no_country_falls_to_bare_code(self):
        from apply.strategies.select import _pick_option
        opts = ["Antigua and Barbuda (+1-268)"]
        self.assertEqual(_pick_option(opts, "+1", country_words=[]),
                         "Antigua and Barbuda (+1-268)")


if __name__ == "__main__":
    unittest.main()
