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
        with patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("1", None)):
            self.assertEqual(pick_option(opts, "Q?", "ans")["text"], "B")

    def test_none_reply(self):
        from lib.automation.llm import pick_option
        with patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("NONE", None)):
            self.assertIsNone(pick_option([{"text": "A"}], "Q", "A"))

    def test_unavailable(self):
        from lib.automation.llm import pick_option
        with patch("lib.ask_api.available", return_value=False):
            self.assertIsNone(pick_option([{"text": "A"}], "Q", "A"))

    def test_out_of_range_index(self):
        from lib.automation.llm import pick_option
        with patch("lib.ask_api.available", return_value=True), \
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
            blockers=[], decisions=[], mode="shadow", url="u")
        self.assertTrue(os.path.exists(path))
        doc = json.load(open(path, encoding="utf-8"))
        self.assertEqual(doc["summary"]["filled"], 1)
        self.assertEqual(doc["fields"][0]["label"], "A")
        # history retained
        self.assertEqual(len(os.listdir(os.path.join(tmp, "j1", "handoffs"))), 1)


if __name__ == "__main__":
    unittest.main()
