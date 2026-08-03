"""Unit tests for the resume.json history filler, account-exists routing,
and the report.py shadow aggregator."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

RESUME = {
    "work": [
        {"name": "Acme Corp", "position": "Senior Engineer",
         "startDate": "2021-03-01", "endDate": "2023-06-15",
         "summary": "Built the data platform."},
        {"name": "Beta Inc", "position": "Engineer",
         "startDate": "2019-01-01"},
    ],
    "education": [
        {"institution": "University of Ottawa", "area": "Computer Science",
         "studyType": "BSc", "startDate": "2014-09", "endDate": "2019-04"},
    ],
}


class HistoryMerge(unittest.TestCase):
    """_merge_history_answers maps generic ATS labels to resume.json entries."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        jid = "1111111111111111"
        os.makedirs(os.path.join(self.tmp, jid), exist_ok=True)
        with open(os.path.join(self.tmp, jid, "resume.json"), "w",
                  encoding="utf-8") as f:
            json.dump(RESUME, f)
        self.jid = jid
        self.results_patch = patch("apply.act.history.RESULTS_DIR", self.tmp)
        self.results_patch.start()
        self.addCleanup(self.results_patch.stop)

    def test_work_row_fields(self):
        from apply.act.history import _merge_history_answers
        fields = [
            {"label": "Company name*"},
            {"label": "Title*"},
            {"label": "Start date month*"},
            {"label": "Start date year*"},
            {"label": "End date month*"},
            {"label": "End date year*"},
            {"label": "What did you do in this role?"},
        ]
        ans = _merge_history_answers(fields, self.jid)
        self.assertEqual(ans["Company name*"], "Acme Corp")
        self.assertEqual(ans["Title*"], "Senior Engineer")
        self.assertEqual(ans["Start date month*"], "March")
        self.assertEqual(ans["Start date year*"], "2021")
        self.assertEqual(ans["End date month*"], "June")
        self.assertEqual(ans["End date year*"], "2023")
        self.assertIn("platform", ans["What did you do in this role?"])

    def test_current_role_end_date_left_empty(self):
        """Most-recent role has no endDate (current) → end-date fields unfilled."""
        from apply.act.history import _merge_history_answers
        tmp = tempfile.mkdtemp()
        jid2 = "2222222222222222"
        os.makedirs(os.path.join(tmp, jid2), exist_ok=True)
        with open(os.path.join(tmp, jid2, "resume.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"work": [{"name": "Current Co", "position": "Engineer",
                                 "startDate": "2022-05-01"}],
                       "education": []}, f)
        with patch("apply.act.history.RESULTS_DIR", tmp):
            ans = _merge_history_answers(
                [{"label": "Company name"}, {"label": "End date month"},
                 {"label": "End date year"}], jid2)
        self.assertEqual(ans["Company name"], "Current Co")
        self.assertNotIn("End date month", ans)
        self.assertNotIn("End date year", ans)

    def test_education_row_fields(self):
        from apply.act.history import _merge_history_answers
        fields = [
            {"label": "School*"},
            {"label": "Degree*"},
            {"label": "Discipline*"},
            {"label": "Graduation year"},
        ]
        ans = _merge_history_answers(fields, self.jid)
        self.assertEqual(ans["School*"], "University of Ottawa")
        self.assertEqual(ans["Degree*"], "BSc")
        self.assertEqual(ans["Discipline*"], "Computer Science")
        self.assertEqual(ans["Graduation year"], "2019")

    def test_no_resume_file_returns_empty(self):
        from apply.act.history import _merge_history_answers
        self.assertEqual(_merge_history_answers([{"label": "Company name"}], "nonexistentjid"), {})

    def test_second_work_row_uses_second_entry(self):
        from apply.act.history import _merge_history_answers
        fields = [
            {"label": "Company name"},
            {"label": "Title"},
            {"label": "Company name"},
            {"label": "Title"},
        ]
        ans = _merge_history_answers(fields, self.jid)
        # Same label keys collide — last row wins for Company, and the
        # Title belongs to its row (work[1]).
        self.assertEqual(ans["Company name"], "Beta Inc")
        self.assertEqual(ans["Title"], "Engineer")

    def test_question_labels_never_get_history_answers(self):
        """'This position is required to work out of a Lyft Office...' must
        NOT be answered with a job title (position-word regression)."""
        from apply.act.history import _merge_history_answers
        fields = [
            {"label": "This position is required to work out of a Lyft "
                       "Office in Toronto, if you do not"},
            {"label": "Are you currently based in Ottawa?"},
            {"label": "Please share your gender pronouns."},
        ]
        ans = _merge_history_answers(fields, self.jid)
        self.assertEqual(ans, {})

    def test_current_role_checkbox_checked_for_current_entry(self):
        """Most-recent entry (no endDate) is current → the Current-role
        checkbox gets 'true', coordinating with the required end-date
        fields (which Greenhouse disables once the box is checked)."""
        from apply.act.history import _merge_history_answers
        tmp = tempfile.mkdtemp()
        jid2 = "4444444444444444"
        os.makedirs(os.path.join(tmp, jid2), exist_ok=True)
        with open(os.path.join(tmp, jid2, "resume.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"work": [{"name": "Current Co", "position": "Engineer",
                                 "startDate": "2022-05-01"}],
                       "education": []}, f)
        with patch("apply.act.history.RESULTS_DIR", tmp):
            ans = _merge_history_answers([{"label": "Current role"}], jid2)
        self.assertEqual(ans["Current role"], "true")

    def test_current_role_not_checked_when_entry_has_end_date(self):
        from apply.act.history import _merge_history_answers
        tmp = tempfile.mkdtemp()
        jid2 = "3333333333333333"
        os.makedirs(os.path.join(tmp, jid2), exist_ok=True)
        with open(os.path.join(tmp, jid2, "resume.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"work": [{"name": "Past Co", "position": "X",
                                 "startDate": "2015-01", "endDate": "2019-12"}],
                       "education": []}, f)
        with patch("apply.act.history.RESULTS_DIR", tmp):
            ans = _merge_history_answers([{"label": "Current role"}], jid2)
        self.assertEqual(ans, {})

    def test_current_role_only_matches_role_question_not_employer_question(self):
        from apply.act.history import _merge_history_answers
        fields = [{"label": "May we contact your current employer?"}]
        ans = _merge_history_answers(fields, self.jid)
        self.assertEqual(ans, {})


class AccountExists(unittest.TestCase):
    """Create-account rejection 'exists' → sign-in attempt."""

    def setUp(self):
        self.page = MagicMock()
        self.page.evaluate.return_value = {
            "hasEmail": True, "createText": "Create Account", "createTag": "A"}
        self.page.url = "https://workday.example.com/job"
        # Two password inputs on the create-account form (iterable mocks)
        self.page.query_selector_all.return_value = [MagicMock(), MagicMock()]
        # Locator chains must return real ints for `.count()` comparisons
        loc = self.page.locator.return_value
        loc.first.count.return_value = 1
        loc.count.return_value = 1
        loc.first.is_checked.return_value = False
        self.sleep_patch = patch("apply.act.fill.time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def test_check_account_created_exists(self):
        from apply.act.fill import _check_account_created
        page = MagicMock()
        page.evaluate.return_value = "exists"
        self.assertEqual(_check_account_created(page), "exists")

    def test_signin_after_exists(self):
        from apply.act.fill import _handle_login_wall
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=None), \
             patch("lib.credentials.get_account_defaults",
                   return_value={"email": "a@b.com", "first_name": "A",
                                 "last_name": "B"}), \
             patch("lib.credentials.get_shared_passwords", return_value=["shared1"]), \
             patch("lib.credentials.save_creds") as save_mock, \
             patch("lib.credentials.pick_password_for_platform", return_value="genpw"), \
             patch("apply.act.fill._check_account_created", return_value="exists"), \
             patch("apply.act.fill._re_open_signin_form"), \
             patch("apply.act.fill._fill_signin_form"), \
             patch("apply.act.fill._login_check", return_value="yes"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False), "")
        save_mock.assert_called_once()
        self.assertEqual(save_mock.call_args[0][2], "genpw")

    def test_exists_no_password_works_falls_to_login_required(self):
        from apply.act.fill import _handle_login_wall
        with patch("apply.common.registry.resolve", return_value=None), \
             patch("lib.credentials.get_creds", return_value=None), \
             patch("lib.credentials.get_account_defaults",
                   return_value={"email": "a@b.com", "first_name": "A",
                                 "last_name": "B"}), \
             patch("lib.credentials.get_shared_passwords", return_value=[]), \
             patch("lib.credentials.save_creds"), \
             patch("lib.credentials.pick_password_for_platform", return_value="genpw"), \
             patch("apply.act.fill._check_account_created", return_value="exists"), \
             patch("apply.act.fill._re_open_signin_form"), \
             patch("apply.act.fill._fill_signin_form"), \
             patch("apply.act.fill._login_check", return_value="no"):
            self.assertEqual(_handle_login_wall(self.page, "jid", quick=False),
                             "login_required")


class PolicyBatchKeys(unittest.TestCase):
    def test_captcha_skip_defaults_false(self):
        from apply.common.submit_policy import load_policy
        with patch("apply.common.submit_policy._policy_path",
                   return_value=os.path.join(tempfile.mkdtemp(), "nope.json")):
            pol = load_policy()
        self.assertFalse(pol["captcha_skip"])
        self.assertEqual(pol["job_timeout_sec"], 0)


class FillTelemetry(unittest.TestCase):
    """Failed fills must be recorded with reason/method/selector/before/after
    — the audit path used to crash (log_field had no `reason` kwarg)."""

    def test_log_field_accepts_telemetry(self):
        from apply.common.audit import log_field, _path
        import tempfile
        tmp = tempfile.mkdtemp()
        with patch("apply.common.audit.RESULTS_DIR", tmp):
            log_field("a" * 16, "Phone", "123", "profile", filled=False,
                      reason="still_empty", selector="#phone", method="text",
                      before="", after="")
            rec = json.loads(open(_path("a" * 16), encoding="utf-8").readline())
        self.assertEqual(rec["reason"], "still_empty")
        self.assertEqual(rec["selector"], "#phone")
        self.assertEqual(rec["method"], "text")

    def test_check_delta_returns_reason(self):
        from apply.common.filler import _check_delta
        ok, reason = _check_delta("", "", "K2P 1J6", "Postal Code")
        self.assertFalse(ok)
        self.assertEqual(reason, "still_empty")
        ok, reason = _check_delta("x", "x", "y", "Field")
        self.assertFalse(ok)
        self.assertEqual(reason, "unchanged")
        ok, reason = _check_delta("a", "b", "c", "Field")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_fill_field_stashes_diag_on_rejection(self):
        """A value the ATS silently rejects must leave _diag on the field."""
        from apply.common.filler import fill_field
        page = MagicMock()
        field = {"label": "Postal Code", "tag": "INPUT", "type": "text",
                 "_sel": "#zip", "name": "zip", "id": "", "placeholder": "",
                 "autocomplete": "", "role": ""}
        # before read returns "" and after returns "" (ATS wipes it)
        with patch("apply.common.filler._frame_for_sel", return_value=page), \
             patch("apply.common.filler._read_element_value", return_value=""), \
             patch("apply.common.filler.resolve_selector", return_value="#zip"), \
             patch("apply.common.filler.text.fill_text_field",
                   return_value=False):
            ok, _ = fill_field(page, field, "K2P1J6")
        self.assertFalse(ok)
        self.assertIn("reason", field.get("_diag", {}))
        self.assertEqual(field["_diag"]["reason"], "still_empty")
        self.assertEqual(field["_diag"]["method"], "native_setter")


class ReportShadow(unittest.TestCase):
    def test_aggregates_log(self):
        from lib.report import cmd_shadow
        tmp = tempfile.mkdtemp()
        log = os.path.join(tmp, "state", "shadow_run.jsonl")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"jid": "a" * 16, "outcome": "held_shadow",
                                "detail": "fill+check OK", "secs": 10, "ts": "x",
                                "title": "T", "company": "C"}) + "\n")
            f.write(json.dumps({"jid": "b" * 16, "outcome": "stopped",
                                "detail": "check failed", "secs": 20, "ts": "x",
                                "title": "U", "company": "D",
                                "check_errors": [{"label": "Email",
                                                  "reason": "empty"}]}) + "\n")
        with patch("lib.config.JI_HOME", tmp):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_shadow()
        out = buf.getvalue()
        self.assertIn("READY TO SUBMIT (fill+check OK) (1)", out)
        self.assertIn("NEEDS REVIEW (1)", out)
        self.assertIn("Email", out)


if __name__ == "__main__":
    unittest.main()
