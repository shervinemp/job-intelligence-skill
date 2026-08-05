"""Contract: the resume schema WRITTEN by tailoring is the schema READ by
the form filler.

This test exists because that contract silently broke and nothing caught
it for the life of the feature:

    lib/build_resume.py  writes and REQUIRES  work[i].company
    apply/act/history.py read                 work[i].name

`name` is the upstream JSON Resume spelling, `company` is what this
pipeline actually produces. So every "Current Company" / "Company name" /
"Employer" field on every application form came back unanswered, while
Title, dates, School and Degree filled normally — it was the single
largest cause of unanswered REQUIRED fields across the fleet.

Twelve unit tests covered history.py and all of them passed, because
their fixtures also said `name`. The tests validated the spec; production
emitted something else. A schema shared by two modules needs a test that
reads from ONE source of truth, not two fixtures that agree with each
other and not with reality.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _canonical_resume():
    """A resume in exactly the shape lib/build_resume.py validates."""
    return {
        "basics": {"name": "A Candidate", "label": "Engineer"},
        "work": [
            {"company": "Acme Corp", "position": "Senior Engineer",
             "startDate": "2021-03", "endDate": "2023-06",
             "highlights": ["Built the data platform."]},
            {"company": "Beta Inc", "position": "Engineer",
             "startDate": "2019-01", "endDate": "2021-02"},
        ],
        "education": [
            {"institution": "University of Ottawa", "area": "Computer Science",
             "studyType": "MSc", "startDate": "2016-09", "endDate": "2018-06"},
        ],
        "skills": [{"name": "Python"}],
    }


class WriterReaderAgree(unittest.TestCase):
    def test_validator_accepts_the_canonical_shape(self):
        """If build_resume's validator rejects this fixture, the fixture is
        wrong and every other test here is meaningless."""
        import json
        import tempfile
        from lib.build_resume import validate_file
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "resume.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(_canonical_resume(), f)
            self.assertTrue(validate_file(p),
                            "build_resume rejected its own canonical shape")

    def test_filler_reads_the_employer_the_writer_wrote(self):
        """The regression itself: company must be readable by the filler."""
        from apply.act.history import entry_company
        for w in _canonical_resume()["work"]:
            self.assertTrue(entry_company(w),
                            f"filler cannot read employer from {sorted(w)}")
        self.assertEqual(
            entry_company(_canonical_resume()["work"][0]), "Acme Corp")

    def test_filler_reads_the_description_the_writer_wrote(self):
        """Same class of bug: writer emits `highlights` (list), the reader
        wanted `summary` (str)."""
        from apply.act.history import entry_summary
        got = entry_summary(_canonical_resume()["work"][0])
        self.assertIn("data platform", got)

    def test_upstream_json_resume_aliases_still_work(self):
        """Accepting both spellings is deliberate — a hand-written or
        externally-generated resume.json should not silently fail."""
        from apply.act.history import entry_company, entry_summary
        self.assertEqual(entry_company({"name": "Legacy Co"}), "Legacy Co")
        self.assertEqual(entry_summary({"summary": "Did things."}), "Did things.")

    def test_end_to_end_company_fields_resolve(self):
        """The labels that actually appeared unanswered on real forms."""
        import json
        import tempfile
        from unittest.mock import patch
        from apply.act.history import _merge_history_answers
        with tempfile.TemporaryDirectory() as d:
            jid = "a" * 16
            os.makedirs(os.path.join(d, jid))
            with open(os.path.join(d, jid, "resume.json"), "w",
                      encoding="utf-8") as f:
                json.dump(_canonical_resume(), f)
            with patch("apply.act.history.RESULTS_DIR", d):
                for label in ("Current Company", "Company name", "Employer",
                              "Current or Most Recent Employer",
                              "Please list your most recent employer"):
                    with self.subTest(label=label):
                        got = _merge_history_answers(
                            [{"label": label, "required": True}], jid)
                        self.assertEqual(got.get(label), "Acme Corp",
                                         f"{label!r} did not resolve")

    def test_questions_are_still_never_answered_from_history(self):
        """The fix widened a blocker; it must not have opened a leak.
        Filling 'Do you have experience at a SaaS Company?' with an
        employer name would be a confident wrong answer on a real form."""
        import json
        import tempfile
        from unittest.mock import patch
        from apply.act.history import _merge_history_answers
        with tempfile.TemporaryDirectory() as d:
            jid = "b" * 16
            os.makedirs(os.path.join(d, jid))
            with open(os.path.join(d, jid, "resume.json"), "w",
                      encoding="utf-8") as f:
                json.dump(_canonical_resume(), f)
            with patch("apply.act.history.RESULTS_DIR", d):
                for label in (
                    "Do you have experience at a SaaS Company? *",
                    "May we contact your current employer?*",
                    "Please review the linked document:*",
                    "Please confirm you are over 18",
                    "This position is required to work out of a Lyft Office",
                ):
                    with self.subTest(label=label):
                        got = _merge_history_answers(
                            [{"label": label, "required": True}], jid)
                        self.assertEqual(
                            got, {}, f"{label!r} was answered from work history")


if __name__ == "__main__":
    unittest.main()
