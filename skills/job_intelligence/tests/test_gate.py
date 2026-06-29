"""Unit tests for the submission gate (apply/common/gate.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.gate import submit_decision

LIVE = {"paused": False, "gate_submit": False}


class SubmitDecision(unittest.TestCase):
    def test_live_default_submits(self):
        action, _ = submit_decision("live", LIVE, {"invalid": 0})
        self.assertEqual(action, "submit")

    def test_paused_blocks(self):
        action, reason = submit_decision("live", {"paused": True}, {})
        self.assertEqual(action, "blocked")
        self.assertIn("paused", reason)

    def test_shadow_holds(self):
        action, _ = submit_decision("shadow", LIVE, {})
        self.assertEqual(action, "hold")

    def test_hold_mode_holds(self):
        action, _ = submit_decision("hold", LIVE, {})
        self.assertEqual(action, "hold")

    def test_gate_holds_on_invalid(self):
        action, reason = submit_decision("live", {"gate_submit": True}, {"invalid": 2})
        self.assertEqual(action, "hold")
        self.assertIn("2", reason)

    def test_gate_submits_when_clean(self):
        action, _ = submit_decision("live", {"gate_submit": True}, {"invalid": 0})
        self.assertEqual(action, "submit")

    def test_paused_beats_gate_and_mode(self):
        action, _ = submit_decision("live", {"paused": True, "gate_submit": True}, {"invalid": 0})
        self.assertEqual(action, "blocked")


if __name__ == "__main__":
    unittest.main()
