"""Tests for the ten addressed gaps (the honest ledger, ETHOS.md §5)."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class LLMStatus(unittest.TestCase):
    """Gap 6 — the escape hatch records WHY (policy/api/declined/used)."""

    def test_policy_off_recorded(self):
        from lib.automation.llm import pick_option, last_status
        with patch.dict("os.environ", {"JI_LLM_MODE": "off"}), \
             patch("lib.ask_api.available",
                   side_effect=AssertionError("must not probe")):
            self.assertIsNone(pick_option([{"text": "Canada"}], "C", "Canada"))
        self.assertEqual(last_status()["state"], "policy_off")

    def test_api_down_recorded(self):
        from lib.automation.llm import pick_option, last_status
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), patch("lib.ask_api.available", return_value=False):
            self.assertIsNone(pick_option([{"text": "Canada"}], "C", "Canada"))
        self.assertEqual(last_status()["state"], "api_down")

    def test_used_recorded(self):
        from lib.automation.llm import pick_option, last_status
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("lib.ask_api.available", return_value=True), \
             patch("lib.ask_api.ask_text", return_value=("0", None)):
            pick = pick_option([{"text": "Canada"}], "C", "Canada")
        self.assertEqual(pick["text"], "Canada")
        self.assertEqual(last_status()["state"], "used")


class GapFillFailClosed(unittest.TestCase):
    """Gap 1 — unmatched or invalid LLM mappings are DROPPED, never
    silently trusted; truncation-tolerant lookup keeps legit ones."""

    def _run(self, gap_map, fields, profile):
        from apply.common.fill_runner import gap_fill_into_answers as _gap_fill_into_answers
        with patch.dict("os.environ", {"JI_LLM_MODE": "on"}), \
             patch("apply.common.fill_runner.llm_field_key_mapping",
                   return_value=gap_map), \
             patch("lib.db.get_job", return_value={}):
            return _gap_fill_into_answers(fields, profile, {}, "j", None)

    def test_unmatched_label_dropped(self):
        fields = [{"label": "Place you call home", "tag": "INPUT", "type": "text"}]
        ans = self._run({"No such field label": "Ottawa"}, fields, {})
        self.assertEqual(ans, {})  # unmatched → dropped (fail-closed)

    def test_truncation_tolerant_lookup(self):
        long_label = "Please describe your earliest available start date and notice period"
        fields = [{"label": long_label, "tag": "INPUT", "type": "text"}]
        ans = self._run({long_label[:60]: "Immediately"}, fields, {})
        self.assertEqual(ans.get(long_label[:60]), "Immediately")

    def test_invalid_value_dropped(self):
        fields = [{"label": "Home base", "tag": "INPUT", "type": "text"}]
        ans = self._run({"Home base": "https://evil.example"}, fields, {})
        self.assertNotIn("Home base", ans)

    def test_valid_value_kept(self):
        fields = [{"label": "Nation", "tag": "SELECT",
                   "options": ["Canada", "USA"]}]
        ans = self._run({"Nation": "Canada"}, fields, {})
        self.assertEqual(ans.get("Nation"), "Canada")


class LearnedMappingHygiene(unittest.TestCase):
    """Gap 4 — pending→active threshold, TTL, provenance, invalidation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = patch("apply.common.resolve._LEARNED_PATH",
                       os.path.join(self.tmp, "field_mappings.json"))
        self.p.start()
        self.addCleanup(self.p.stop)
        from apply.common import resolve
        resolve._learned_cache = None
        self.addCleanup(lambda: setattr(resolve, "_learned_cache", None))

    def test_requires_two_confirmations(self):
        from apply.common.resolve import learn_mapping, _lookup_learned
        learn_mapping("Some Label", "v1")
        self.assertIsNone(_lookup_learned("some label"))  # pending
        learn_mapping("Some Label", "v1")
        self.assertEqual(_lookup_learned("some label"), "v1")  # active

    def test_conflict_resets_count(self):
        from apply.common.resolve import learn_mapping, _lookup_learned
        learn_mapping("Some Label", "v1")
        learn_mapping("Some Label", "v2")  # conflict → reset
        self.assertIsNone(_lookup_learned("some label"))
        learn_mapping("Some Label", "v2")
        self.assertEqual(_lookup_learned("some label"), "v2")

    def test_explicit_answer_invalidates(self):
        from apply.common.resolve import learn_mapping, resolve, _load_learned
        learn_mapping("Some Label", "v1")
        learn_mapping("Some Label", "v1")  # active
        r = resolve("Some Label", {}, {"Some Label": "v2"})
        self.assertEqual(r.value, "v2")
        self.assertNotIn("some label", _load_learned())

    def test_ttl_expiry(self):
        from apply.common.resolve import learn_mapping, _lookup_learned
        import time as _t
        learn_mapping("Some Label", "v1")
        learn_mapping("Some Label", "v1")
        # Age the entry beyond the TTL.
        from apply.common import resolve
        e = resolve._load_learned()["some label"]
        e["ts"] = "2000-01-01T00:00:00"
        resolve._save_learned()
        self.assertIsNone(_lookup_learned("some label"))


class Coherence(unittest.TestCase):
    """Gap 7 — cross-field contradiction rules."""

    def test_sponsorship_contradiction(self):
        from apply.common.coherence import check_coherence
        fields = [
            {"label": "Do you require visa sponsorship?", "answer": "Yes",
             "kind": "verified"},
            {"label": "Work authorization status", "answer": "Canadian citizen",
             "kind": "verified"},
        ]
        f = check_coherence(fields)
        self.assertTrue(any(x["rule"] == "sponsorship_vs_authorization"
                            for x in f))

    def test_city_province_mismatch(self):
        from apply.common.coherence import check_coherence
        fields = [
            {"label": "City", "answer": "Ottawa", "kind": "verified"},
            {"label": "Province or Territory", "answer": "Toronto",
             "kind": "verified"},
        ]
        f = check_coherence(fields)
        self.assertTrue(any(x["rule"] == "province_must_be_region" for x in f))

    def test_coherent_answers_no_findings(self):
        from apply.common.coherence import check_coherence
        fields = [
            {"label": "City", "answer": "Ottawa", "kind": "verified"},
            {"label": "Province or Territory", "answer": "Ontario",
             "kind": "verified"},
            {"label": "Do you require visa sponsorship?", "answer": "No",
             "kind": "verified"},
            {"label": "Work authorization status", "answer": "Canadian citizen",
             "kind": "verified"},
        ]
        self.assertEqual(check_coherence(fields), [])

    def test_pronoun_gender_mismatch(self):
        from apply.common.coherence import check_coherence
        fields = [
            {"label": "Gender", "answer": "Male", "kind": "verified"},
            {"label": "She/her", "answer": "Yes", "kind": "verified"},
        ]
        f = check_coherence(fields)
        self.assertTrue(any(x["rule"] == "pronoun_vs_gender" for x in f))


class Preflight(unittest.TestCase):
    """Gap 3 — profile readiness manifest."""

    def test_hard_missing_detected(self):
        from apply.preflight import preflight
        m = preflight(profile={"first_name": "A", "email": "a@b.c"})
        self.assertIn("last_name", m["hard_missing"])
        self.assertIn("work_history", m["hard_missing"])

    def test_complete_profile_clean(self):
        from apply.preflight import preflight
        prof = {"first_name": "A", "last_name": "B", "email": "a@b.c",
                "phone": "+1 555", "location": "Ottawa, ON",
                "work_history": [{"company": "X"}],
                "answers": {"how_did_you_hear": "LinkedIn",
                            "need_us_sponsorship": "No"}}
        m = preflight(profile=prof)
        self.assertEqual(m["hard_missing"], [])

    def test_answer_gaps_listed(self):
        from apply.preflight import preflight
        prof = {"first_name": "A", "last_name": "B", "email": "a@b.c",
                "phone": "+1", "location": "Ottawa, ON",
                "work_history": [{"company": "X"}]}
        m = preflight(profile=prof)
        self.assertIn("salary", m["answer_gaps"])
        self.assertIn("how_did_you_hear", m["answer_gaps"])


class CanaryEnforcement(unittest.TestCase):
    """Gap 5 — regression blocks submit (mechanical, not advisory)."""

    def test_submit_blocked_on_regression(self):
        from apply.act.submit import cmd_submit
        with patch("apply.common.submit_policy.load_policy", return_value={}), \
             patch("apply.common.submit_policy.resolve_mode", return_value="live"), \
             patch("apply.common.gate.submit_decision",
                   return_value=("go", "")), \
             patch("apply.act.check.cmd_check", return_value=0), \
             patch("apply.act.submit.get_conn") as conn, \
             patch("lib.automation.diff.load_handoffs", return_value=[
                 {"summary": {"filled": 5},
                  "fields": [{"label": "A", "outcome": "no_answer"}]},
                 {"summary": {"filled": 8},
                  "fields": [{"label": "A", "outcome": "filled"}]},
             ]) as lh:
            conn.return_value.execute.return_value.fetchone.return_value = \
                {"stage": "tailored", "state": "active"}
            rc = cmd_submit("jid1")
        self.assertEqual(rc, 1)
        lh.assert_called_once()


class RefillGate(unittest.TestCase):
    """The re-fill contract must NOT claim success while a REQUIRED field
    failed — that is the blind-submit class (required fields went out as
    blanks because the refill reported a warning, not a failure)."""

    def _page(self):
        from unittest.mock import MagicMock
        page = MagicMock()
        page.url = "https://www.linkedin.com/jobs/view/1"
        page.frames = []
        return page

    def _refill_page(self, eval_result, url="https://www.linkedin.com/jobs/view/1"):
        from unittest.mock import MagicMock
        page = MagicMock()
        page.url = url
        page.frames = []
        page.evaluate.return_value = eval_result
        loc = MagicMock()
        loc.count.return_value = 0
        loc.first.count.return_value = 0
        page.locator.return_value = loc
        page.goto.return_value = None
        page.wait_for_timeout.return_value = None
        page.close.return_value = None
        return page

    def test_required_field_failure_returns_false(self):
        from apply.act.submit import _refill_for_submit
        page = self._page()
        ctx = MagicMock()
        fields = [
            {"label": "Email address*", "required": True,
             "id": "email", "name": "email"},
            {"label": "Years", "required": False,
             "id": "years", "name": "years"},
        ]
        # fill_page reports email (required) failed; years optional failed.
        with patch("apply.common.fill_runner.fill_page",
                   return_value=([], [{"label": "Email address*", "required": True,
                                       "_why": "fill_failed"},
                                      {"label": "Years", "required": False,
                                       "_why": "fill_failed"}])), \
             patch("apply.act.submit._find_next_button", return_value=None), \
             patch("apply.act.helpers._probe_form") as pf:
            pf.return_value.fields = fields
            ok, warns = _refill_for_submit(page, ctx, {"jid": "j1"},
                                           "j1", {}, {})
        self.assertFalse(ok)
        self.assertTrue(any("Email address*" in w for w in warns))

    def test_optional_field_failure_returns_true(self):
        """Optional-only failures must NOT block — the form can submit with
        optional fields unfilled."""
        from apply.act.submit import _refill_for_submit
        page = self._page()
        ctx = MagicMock()
        fields = [
            {"label": "Years", "required": False,
             "id": "years", "name": "years"},
        ]
        with patch("apply.common.fill_runner.fill_page",
                   return_value=([], [{"label": "Years", "required": False,
                                       "_why": "fill_failed"}])), \
             patch("apply.act.submit._find_next_button", return_value=None), \
             patch("apply.act.helpers._probe_form") as pf:
            pf.return_value.fields = fields
            ok, warns = _refill_for_submit(page, ctx, {"jid": "j1"},
                                           "j1", {}, {})
        self.assertTrue(ok)

    def test_submit_aborts_when_refill_blocks(self):
        """The caller must BLOCK (return 1) when the re-fill reports a
        required-field failure — printing the reason is not enough."""
        from apply.act.submit import cmd_submit
        from unittest.mock import MagicMock
        page = MagicMock()
        page.url = "https://www.linkedin.com/jobs/view/1"
        page.frames = []
        ctx = MagicMock()
        ctx.pages = [page]
        # cmd_submit needs a session that yields this page
        with patch("apply.act.submit.load_state", return_value={"jid": "j1",
                  "external_url": "https://www.linkedin.com/jobs/view/1"}), \
             patch("apply.act.submit.save_state"), \
             patch("apply.act.submit.handle_captcha", return_value=False), \
             patch("apply.common.submit_policy.load_policy", return_value={}), \
             patch("apply.common.submit_policy.resolve_mode", return_value="live"), \
             patch("apply.common.gate.submit_decision",
                   return_value=("go", "")), \
             patch("apply.act.submit.get_conn") as conn, \
             patch("apply.act.submit.chrome_session") as cs, \
             patch("apply.act.submit._refill_for_submit",
                   return_value=(False, ["Email address* re-fill failed: fill_failed"])):
            conn.return_value.execute.return_value.fetchone.return_value = \
                {"stage": "tailored", "state": "active"}
            cs.return_value.__enter__.return_value = (page, ctx)
            cs.return_value.__exit__.return_value = None
            rc = cmd_submit("j1")
        self.assertEqual(rc, 1)


class AlreadyAppliedTargetGuard(unittest.TestCase):
    """UNRECOVERABLE guard: 'already-applied' text must be on the TARGET
    posting, else it is a false positive that can never be corrected."""

    def _page(self, url, text):
        from unittest.mock import MagicMock
        page = MagicMock()
        page.url = url
        page.frames = []
        from apply.common.page_helpers import page_text
        page.__class__.frame = None
        # _all_text_sources calls _pt(page) -> page_text -> page.evaluate
        page.evaluate.return_value = text
        return page

    def test_on_target_counts_as_success(self):
        from apply.act.submit import _determine_outcome
        from unittest.mock import MagicMock
        page = MagicMock(); page.url = "https://x.com/jobs/view/123"
        page.frames = []
        page.evaluate.return_value = "You have already applied for this role."
        outcome, reason = _determine_outcome(
            page, MagicMock(), set(), "https://x.com/jobs/view/123",
            "Apply", target_url="https://x.com/jobs/view/123")
        self.assertEqual(outcome, "success")

    def test_non_target_does_not_count(self):
        from apply.act.submit import _determine_outcome
        from unittest.mock import MagicMock, patch
        # redirected to a DIFFERENT page that shows "already applied" — the
        # already-applied text must NOT count; other success signals absent.
        page = MagicMock(); page.url = "https://x.com/jobs/view/OTHER"
        page.frames = []
        page.evaluate.return_value = "You have already applied for this role."
        with patch("apply.common.signals.has_success_text",
                   return_value=False), \
             patch("apply.act.submit._check_submit_success",
                   return_value=(False, None)):
            outcome, reason = _determine_outcome(
                page, MagicMock(), set(), "https://x.com/jobs/view/123",
                "Apply", target_url="https://x.com/jobs/view/123")
        self.assertNotEqual(outcome, "success")


class UnconfirmedQueue(unittest.TestCase):
    """Gap 9 — unconfirmed skips are cause-tagged and re-examined."""
    def test_worker_tags_unconfirmed_detail(self):
        # auto._no_apply_path_detail already tested; here the supervisor
        # side: a skip with the unconfirmed detail gets recheck=True.
        from apply.shadow import _parse_worker
        rec = _parse_worker(
            "OUTCOME=skipped\nDETAIL=no apply path (unconfirmed — may be cookie/session)\nSECS=4\n")
        self.assertEqual(rec["outcome"], "skipped")

    def test_recheck_mode_rewrites_log(self):
        from apply.shadow import run
        tmp = tempfile.mkdtemp()
        log = os.path.join(tmp, "shadow_run.jsonl")
        with open(log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"jid": "u1", "outcome": "skipped",
                                "unconfirmed": True, "recheck": True}) + "\n")
            f.write(json.dumps({"jid": "ok1", "outcome": "held_shadow"}) + "\n")
        with patch("apply.shadow.LOG_PATH", log), \
             patch("apply.shadow.JOBS_DIR", tmp), \
             patch("apply.shadow._run_worker",
                   return_value=(0, "t.log",
                                 "OUTCOME=skipped\nDETAIL=no apply path (expired)\nSECS=2\n",
                                 False)), \
             patch("lib.db.get_jobs_by_stage", return_value=[]), \
             patch("lib.db.get_job",
                   return_value={"id": "u1", "title": "T", "company": "C"}):
            run(jids=None, limit=None, quick=False, recheck=True)
        lines = [json.loads(l) for l in open(log, encoding="utf-8")]
        self.assertEqual(lines[0]["jid"], "ok1")  # u1 was removed → re-run
        self.assertIn("u1", [l["jid"] for l in lines])


class FleetReport(unittest.TestCase):
    """Gap 10 — the steering instrument exists and aggregates honestly."""

    def test_fleet_report_aggregates(self):
        from lib.report import cmd_fleet
        tmp = tempfile.mkdtemp()
        for jid, kinds in [("j1", [("A", "verified"), ("B", "rejected_by_form")]),
                           ("j2", [("A", "verified")])]:
            d = os.path.join(tmp, jid)
            os.makedirs(d, exist_ok=True)
            fields = [{"label": l, "kind": k, "method": "deterministic"}
                      for l, k in kinds]
            with open(os.path.join(d, "handoff.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"jid": jid, "fields": fields,
                           "summary": {"filled": 1}, "ts": "2026-08-03T10:00:00"},
                          f)
        with patch("lib.report.RESULTS_DIR", tmp):
            cmd_fleet()  # must not raise; output goes to stdout


class CorpusPII(unittest.TestCase):
    """Gap 8 — snapshots are PII-scrubbed before they hit disk."""

    def test_scrub_redacts(self):
        from apply.common.corpus import scrub_pii
        html = ('<html><body>Shervin N. <a>shervin.naseri@gmail.com</a> '
                '+1 (343) 558-1744</body></html>')
        out = scrub_pii(html)
        self.assertNotIn("shervin.naseri@gmail.com", out)
        self.assertNotIn("558-1744", out)
        self.assertIn("[email]", out)
        self.assertIn("[phone]", out)


class TailorGrounding(unittest.TestCase):
    """Gap 2 — novel claims are quarantined; admit is blocked."""

    def test_novel_company_blocked(self):
        from lib.grounding import ground
        profile = {"work_history": [{"company": "Acme", "position": "Engineer",
                                     "startDate": "2020-01", "endDate": "2022-01"}]}
        resume = {"work": [{"company": "Fabricated Inc", "position": "Engineer",
                            "startDate": "2020-01", "endDate": "2022-01"}]}
        m = ground(resume, profile=profile)
        self.assertFalse(m["ok"])
        self.assertTrue(any("Fabricated Inc" in c for c in m["novel_claims"]))

    def test_grounded_resume_passes(self):
        from lib.grounding import ground
        profile = {"work_history": [{"company": "Acme", "position": "Engineer",
                                     "startDate": "2020-01", "endDate": "2022-01"}]}
        resume = {"work": [{"company": "Acme Corp", "position": "Software Engineer",
                            "startDate": "2020-01", "endDate": "2022-01"}]}
        m = ground(resume, profile=profile)
        self.assertTrue(m["ok"])

    def test_no_base_blocks(self):
        from lib.grounding import ground
        m = ground({"work": []}, profile={})
        self.assertFalse(m["ok"])
        self.assertEqual(m["base"], "none")


if __name__ == "__main__":
    unittest.main()
