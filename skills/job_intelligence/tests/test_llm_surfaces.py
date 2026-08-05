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


class JiVerifySurface(_SurfaceFixture):
    """`ji verify` — the sanctioned PII review the strong model approves before
    submit. Every suspicious value must be VISIBLE (the C-O2 faithful-evidence
    invariant at the surface layer): a value the orchestrator can't see can't
    be vetoed."""

    def test_risk_field_shows_value_and_provenance(self):
        import ji
        jid = "aaaaaaaaaaaaaaaa"
        d = os.path.join(self._results, jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "mode": "shadow", "fields": [
                {"label": "Country", "answer": "Canada", "kind": "verified",
                 "method": "combobox", "provenance": "profile",
                 "selected_text": "Canada"},
            ]}, f)
        with patch.object(ji, "_T", None):  # _imports() reloads terms
            ji._imports()
            err = self._stderr(ji.cmd_verify, jid)
        self.assertIn("Canada", err)
        self.assertIn("(profile)", err)      # provenance is visible
        self.assertIn("[read-back: Canada]", err)  # the verification evidence

    def test_prefilled_value_is_visible(self):
        """A prefilled field's KIND + VALUE must both surface — the URN-trap
        class: a prefilled value that looks wrong must be catchable."""
        import ji
        jid = "bbbbbbbbbbbbbbbb"
        d = os.path.join(self._results, jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "mode": "live", "fields": [
                {"label": "LinkedIn URL", "answer": "urn:li:member:12345",
                 "kind": "unverified", "method": "prefilled",
                 "prefilled_value": "urn:li:member:12345"},
            ]}, f)
        with patch.object(ji, "_T", None):
            ji._imports()
            err = self._stderr(ji.cmd_verify, jid)
        self.assertIn("urn:li:member:12345", err)
        self.assertIn("[prefilled:", err)    # kind surfaced
        self.assertIn("unverified", err)     # not silently accepted

    def test_all_fields_reveals_suspicious(self):
        """--all must reveal EVERY field — the fix for the location/URN miss
        where a suspicious field was invisible because it wasn't classified."""
        import ji
        jid = "cccccccccccccccc"
        d = os.path.join(self._results, jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "mode": "shadow", "fields": [
                {"label": "City", "answer": "Toronto", "kind": "verified",
                 "method": "fill"},
                {"label": "Odd Field", "answer": "x", "kind": "verified",
                 "method": "combobox"},
            ]}, f)
        with patch.object(ji, "_T", None):
            ji._imports()
            err = self._stderr(ji.cmd_verify, jid, all_fields=True)
        self.assertIn("Odd Field", err)
        self.assertIn("City", err)


class JiReturnContract(unittest.TestCase):
    """`ji` docstring contract: every command ends on exactly one line —
    NEXT: | DECISION: | READY: | DONE:. A surface that ends silent breaks the
    orchestrator's read loop."""

    def test_ready_prints_readiness_line(self):
        import ji
        with patch("ji._ready_jids", return_value=["aaaaaaaaaaaaaaaa"]):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = ji.cmd_ready()
            out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("READY:"),
                        f"must be a one-line READY surface, got {out!r}")

    def test_ready_empty_is_still_a_readiness_line(self):
        import ji
        with patch("ji._ready_jids", return_value=[]):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = ji.cmd_ready()
            out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("READY:"),
                        f"empty READY must still be a one-line surface")


class JiStatusSurface(unittest.TestCase):
    """`ji status` — the one-screen fleet + READY + HOLD aggregate the
    orchestrator reads first. Every count must be derivable from the evidence
    the orchestrator can re-check (DB stage + dossier risk fields)."""

    def _tmp_db(self):
        tmp = tempfile.mkdtemp()
        import lib.db.schema as schema
        schema._conn = None
        schema.DB_PATH = os.path.join(tmp, "ji.db")
        schema.DB_DIR = tmp
        conn = schema.get_conn()
        self._tmp = tmp
        self._conn = conn
        return conn, tmp

    def tearDown(self):
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        if hasattr(self, "_tmp"):
            shutil.rmtree(self._tmp, ignore_errors=True)

    def _job(self, jid, stage, state="active"):
        self._conn.execute(
            "INSERT OR IGNORE INTO jobs (id, url, title, company, stage, state, "
            "created_at, updated_at, scripts) "
            "VALUES (?,?,?,?,?,?,?,?, '[]')",
            (jid, f"https://x.com/{jid}", "Role", "Co", stage, state,
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        self._conn.commit()

    def test_status_counts_and_one_line_next(self):
        import ji
        conn, tmp = self._tmp_db()
        self._job("aaaaaaaaaaaaaaaa", "tailored")  # no dossier → HOLD
        self._job("bbbbbbbbbbbbbbbb", "applied")
        with patch("lib.config.RESULTS_DIR", os.path.join(tmp, "results")), \
             patch.object(ji, "_T", None), \
             patch("ji._risk_unverified",
                   return_value=["Country (unverified)"]):
            ji._imports()
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                ji.cmd_status()
            out = buf.getvalue()
        self.assertIn("FLEET: 2 jobs | applied=1", out)
        self.assertIn("READY: 0", out)
        self.assertIn("HOLD: 1", out)
        self.assertIn("Country", out)  # the HOLD reason is visible
        # the one-line footer survives
        self.assertIn("NEXT: ji decisions", out)

    def test_ready_counts_observed_risk_free(self):
        import ji
        conn, tmp = self._tmp_db()
        self._job("aaaaaaaaaaaaaaaa", "tailored")
        self._job("bbbbbbbbbbbbbbbb", "tailored")
        with patch("lib.config.RESULTS_DIR", os.path.join(tmp, "results")), \
             patch.object(ji, "_T", None), \
             patch("ji._risk_unverified", return_value=[]):
            ji._imports()
            ready = ji._ready_jids()
        self.assertEqual(len(ready), 2)

    def test_ready_excludes_unverified_risk(self):
        import ji
        conn, tmp = self._tmp_db()
        self._job("aaaaaaaaaaaaaaaa", "tailored")
        self._job("bbbbbbbbbbbbbbbb", "tailored")
        with patch("lib.config.RESULTS_DIR", os.path.join(tmp, "results")), \
             patch.object(ji, "_T", None), \
             patch("ji._risk_unverified",
                   side_effect=lambda jid: ["Country (unverified)"]
                   if jid.startswith("aaaa") else []):
            ji._imports()
            ready = ji._ready_jids()
        self.assertEqual(ready, ["bbbbbbbbbbbbbbbb"])


class JiVerifyAdversarial(_SurfaceFixture):
    """Poisoned evidence must be VISIBLE in `ji verify` — a bad value the
    orchestrator can't see can't be vetoed (the C-O2 faithful-evidence
    invariant, adversarial form)."""

    def test_antigua_option_value_is_visible(self):
        """The Antigua-class wrong value (bare +1 picked as country) must
        surface so the orchestrator catches it before submit."""
        import ji
        jid = "dddddddddddddddd"
        d = os.path.join(self._results, jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "mode": "shadow", "fields": [
                {"label": "Phone country code", "answer": "+1",
                 "kind": "unverified", "method": "combobox",
                 "provenance": "learned",
                 "selected_text": "Antigua and Barbuda (+1-268)"},
            ]}, f)
        with patch.object(ji, "_T", None):
            ji._imports()
            err = self._stderr(ji.cmd_verify, jid)
        self.assertIn("+1", err)                        # the answer is shown
        self.assertIn("Antigua", err)                   # the read-back shows the trap
        self.assertIn("unverified", err)                # not certified as good

    def test_wrong_prefilled_value_is_visible(self):
        """A prefilled value that contradicts the profile must be catchable —
        value + kind + provenance all on the line."""
        import ji
        jid = "eeeeeeeeeeeeeeee"
        d = os.path.join(self._results, jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "handoff.json"), "w", encoding="utf-8") as f:
            json.dump({"jid": jid, "mode": "live", "fields": [
                {"label": "Location", "answer": "M5V 2T6", "kind": "verified",
                 "method": "prefilled", "prefilled_value": "M5V 2T6",
                 "provenance": "profile"},
            ]}, f)
        with patch.object(ji, "_T", None):
            ji._imports()
            err = self._stderr(ji.cmd_verify, jid)
        self.assertIn("M5V 2T6", err)                   # postal code as location — visible
        self.assertIn("[prefilled:", err)               # kind surfaced
        self.assertIn("(profile)", err)                 # provenance surfaced


class EvalHarnessSelfTest(unittest.TestCase):
    """The gated eval script's SCORING LOGIC — tested with a perfect stub
    model. A 100% stub proves the harness measures correctly; any shortfall
    with the real model is then attributable to the model, not the harness."""

    def _run(self, surface):
        import subprocess, sys as _sys
        skill = os.path.join(os.path.dirname(__file__), "..")
        script = os.path.join(skill, "scripts", "eval_llm_surfaces.py")
        r = subprocess.run(
            [_sys.executable, script, "--stub", "--surface", surface],
            capture_output=True, text=True, cwd=skill)
        return r.stdout

    def test_stub_option_pick_reports_100(self):
        out = self._run("option_pick")
        self.assertIn("option_pick: 4/4 (100%)", out)

    def test_stub_skips_when_not_stubbed(self):
        """Without --stub and with ask_api down, the eval must SKIP, not
        crash — the gating is part of the contract."""
        import subprocess, sys as _sys
        skill = os.path.join(os.path.dirname(__file__), "..")
        script = os.path.join(skill, "scripts", "eval_llm_surfaces.py")
        r = subprocess.run([_sys.executable, script, "--surface", "option_pick"],
                           capture_output=True, text=True, cwd=skill)
        # if the local model happens to be up this test is moot; assert the
        # script ran and returned a contract line either way
        self.assertTrue(r.returncode == 0)
        out = r.stdout + r.stderr
        self.assertTrue("SKIPPED" in out or "option_pick" in out or ":" in out,
                        "eval must either SKIP or produce a scored surface")


class JiAnswerSurface(unittest.TestCase):
    """`ji answer` — the command that turns an orchestrator decision into the
    `apply.py act --fill --answers` invocation. The JSON encoding + CLI parse
    are the decision's delivery path."""

    def test_answer_routes_json_to_fill(self):
        import ji
        with patch("ji._run", return_value=0) as run:
            rc = ji.cmd_answer("aaaaaaaaaaaaaaaa", "City", "Toronto")
        self.assertEqual(rc, 0)
        args = run.call_args[0]
        self.assertEqual(args[0], "apply.py")
        self.assertEqual(args[2], "--fill")
        # the label/value must be JSON-encoded into --answers
        self.assertEqual(json.loads(args[5]), {"City": "Toronto"})

    def test_main_parses_label_value_spec(self):
        """`ji answer <jid> "label": "value"` — the label and value arrive as
        separate argv tokens and get joined+split on the first colon."""
        import ji
        import sys as _sys
        old = _sys.argv
        _sys.argv = ["ji.py", "answer", "aaaaaaaaaaaaaaaa",
                     '"City"', '": "Toronto, ON"']
        try:
            with patch.object(ji, "_run", return_value=0) as run:
                rc = ji.main()
        finally:
            _sys.argv = old
        self.assertEqual(rc, 0)
        call = run.call_args
        self.assertIn("Toronto, ON", json.dumps(call.args))


if __name__ == "__main__":
    unittest.main()
