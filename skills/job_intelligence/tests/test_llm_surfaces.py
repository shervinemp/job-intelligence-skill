"""test_llm_surfaces.py — the orchestrator's DECISION-INPUT fidelity.

The strong LLM (orchestrator) decides from the evidence surfaces; the weak
served model (ask_api) decides from the escape-hatch prompts. This file pins
what each surface actually PRESENTS to an LLM — the fidelity question "can a
model decide correctly from this evidence?" — deterministically, no API needed.

Split:
- Deterministic half (this file): the surface assembles the deciding evidence
  (top_options, answer, option list). Assert the prompt/group content.
- Gated half (scripts/eval_llm_surfaces.py): run a golden set through the real
  weak model and score per surface. Runnable only when ask_api is up.

The routing hierarchy (lib/automation/llm.py) is the contract under test:
deterministic first, then ORCHESTRATOR (strong, from evidence), then the weak
model only where the orchestrator physically cannot act (vision), then user.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _SurfaceFixture(unittest.TestCase):
    """Temp RESULTS_DIR so handoff.json lands somewhere inspectable."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._results = os.path.join(self._tmp, "results")
        os.makedirs(self._results, exist_ok=True)
        import lib.report as R
        self._R = R
        self._patchers = [
            patch.object(R, "RESULTS_DIR", self._results),
            patch("lib.config.RESULTS_DIR", self._results),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _dossier(self, jid, fields):
        d = os.path.join(self._results, jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "fields": fields}, f)

    def _stderr(self, fn, *args, **kwargs):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            fn(*args, **kwargs)
        return buf.getvalue()


class HandoversEvidence(_SurfaceFixture):
    """The ORCHESTRATOR queue must carry the top_options the model decides
    from — 'the answer IS in the evidence'."""

    def test_orchestrator_group_gets_top_options(self):
        from lib.report import cmd_handovers
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "City", "kind": "rejected_by_form",
             "diag": {"reason": "no_option_match",
                      "top_options": [
                          {"text": "Toronto", "score": 5},
                          {"text": "Ottawa", "score": 4},
                          {"text": "Waterloo", "score": 3}]}},
        ])
        with patch.object(self._R, "_profile_has_answer", return_value=True):
            err = self._stderr(cmd_handovers)
        # the deciding evidence (options) is rendered for the orchestrator
        self.assertIn("Toronto", err)
        self.assertIn("Ottawa", err)

    def test_no_option_match_without_profile_answer_is_data_gap(self):
        """A widget failure with NO profile answer is a DATA gap, not an
        orchestrator decision — routing must not misclassify it."""
        from lib.report import cmd_handovers
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "City", "kind": "rejected_by_form",
             "diag": {"reason": "no_option_match",
                      "top_options": [{"text": "Toronto", "score": 5}]}},
        ])
        with patch.object(self._R, "_profile_has_answer", return_value=False):
            err = self._stderr(cmd_handovers)
        # routed to the DATA group (the section header), not the ORCHESTRATOR
        # section (the footer mentions all owners generically)
        self.assertIn("== DATA (1)", err)
        self.assertNotIn("== ORCHESTRATOR", err)

    def test_need_data_field_routes_to_owner(self):
        from lib.report import cmd_handovers
        self._dossier("aaaaaaaaaaaaaaaa", [
            {"label": "Work authorization", "kind": "needs_data"},
        ])
        with patch.object(self._R, "_profile_has_answer", return_value=False), \
             patch.object(self._R, "_handover_kw",
                          return_value=("work authorization", "citizenship")):
            err = self._stderr(cmd_handovers)
        self.assertIn("USER", err)


class PickOptionPrompt(unittest.TestCase):
    """The weak model's option_pick prompt must carry enough to choose —
    options + answer + exact reply format."""

    def test_prompt_includes_options_and_answer(self):
        import lib.automation.llm as llm
        opts = [{"text": "Canada"}, {"text": "USA"}]
        captured = {}
        def fake_ask_text(prompt, **kw):
            captured["prompt"] = prompt
            return ("0", None)
        with patch("lib.automation.llm.allow", return_value=True), \
             patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", side_effect=fake_ask_text):
            got = llm.pick_option(opts, "Country", "Canada")
        self.assertEqual(got, opts[0])
        p = captured["prompt"]
        self.assertIn("Canada", p)          # the answer is visible
        self.assertIn("[0] Canada", p)      # options with indices
        self.assertIn("Reply with ONLY the index", p)
        self.assertIn("NONE", p)            # the escape hatch is stated

    def test_out_of_range_index_is_declined(self):
        import lib.automation.llm as llm
        opts = [{"text": "Canada"}]
        with patch("lib.automation.llm.allow", return_value=True), \
             patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("99", None)):
            got = llm.pick_option(opts, "Country", "Canada")
        self.assertIsNone(got)
        self.assertEqual(llm.last_status()["state"], "declined")


class RoutingHierarchy(unittest.TestCase):
    """The routing contract (llm.py docstring): what may invoke the weak model
    in auto mode. The orchestrator is the semantic fallback — NOT ask_api."""

    def test_auto_mode_allows_only_vision(self):
        from lib.automation.llm import _KIND_AUTO
        self.assertTrue(_KIND_AUTO["vision"])
        for kind in ("option_pick", "gap_fill", "batch_verify",
                     "verify_reads", "auto_retry"):
            self.assertFalse(_KIND_AUTO[kind],
                             f"{kind} must be OFF in auto — the orchestrator "
                             "decides from evidence instead")

    def test_allow_auto_gates(self):
        import lib.automation.llm as llm
        with patch.dict(os.environ, {"JI_LLM_MODE": "auto"}):
            self.assertTrue(llm.allow("vision"))
            self.assertFalse(llm.allow("option_pick"))
            self.assertFalse(llm.allow("gap_fill"))

    def test_on_mode_unlocks_escapes(self):
        import lib.automation.llm as llm
        with patch.dict(os.environ, {"JI_LLM_MODE": "on"}):
            self.assertTrue(llm.allow("option_pick"))
            self.assertTrue(llm.allow("gap_fill"))

    def test_off_mode_disables_all(self):
        import lib.automation.llm as llm
        with patch.dict(os.environ, {"JI_LLM_MODE": "off"}):
            self.assertFalse(llm.allow("vision"))


if __name__ == "__main__":
    unittest.main()
