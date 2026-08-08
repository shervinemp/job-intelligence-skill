"""test_pdf_check.py — the resume PDF quality gate (one-page + overlap + clip)
and its wiring into build_resume / tailor retry / admit."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = os.path.join(tempfile.gettempdir(), "ji_pdf_check_tests")


def _one_page_resume():
    """A compact resume that fits one page cleanly."""
    return {
        "basics": {"name": "A Candidate", "label": "ML Engineer"},
        "work": [
            {"company": "Acme", "position": "Engineer",
             "startDate": "2021-03", "endDate": "2023-06",
             "highlights": ["Built a thing."]},
        ],
        "education": [
            {"institution": "U Ottawa", "area": "CS", "studyType": "MSc",
             "startDate": "2019-01", "endDate": "2020-12"},
        ],
        "skills": [{"name": "Python", "keywords": ["pandas", "numpy"]}],
    }


def _overflow_resume():
    """A resume with far too much content — spills to multiple pages."""
    return {
        "basics": {"name": "A Candidate", "label": "ML Engineer",
                   "summary": "words " * 30},
        "work": [
            {"company": "Acme", "position": "Engineer",
             "startDate": "2021-03", "endDate": "2023-06",
             "highlights": [
                 "This is a very long highlight line that keeps wrapping "
                 "across the full column width to take up vertical space. " * 3
                 for _ in range(60)]},
        ],
        "education": [
            {"institution": "University of Ottawa", "area": "Computer Science",
             "studyType": "MSc", "startDate": "2019-01", "endDate": "2020-12",
             "courses": ["course " * 10]},
        ],
        "skills": [{"name": "Python",
                    "keywords": ["pandas", "numpy", "scikit"] * 4}],
    }


def _build(resume, company="Acme"):
    os.makedirs(_TMP, exist_ok=True)
    path = os.path.join(_TMP, "resume.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resume, f)
    from lib.build_resume import build
    return build(path, _TMP, company=company)


class PdfCheckCore(unittest.TestCase):
    def test_one_page_resume_passes(self):
        from lib.pdf_check import check
        out = _build(_one_page_resume())
        self.assertIsNotNone(out)
        report = check(out["resume"], max_pages=1)
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["pages"], 1)

    def test_page_count_detects_overflow(self):
        from lib.pdf_check import check
        out = _build(_overflow_resume())
        self.assertIsNotNone(out)
        report = check(out["resume"], max_pages=1)
        self.assertFalse(report["ok"])
        self.assertTrue(report["page_overflow"])
        self.assertGreaterEqual(report["pages"], 2)

    def test_unreadable_pdf_reports_failure(self):
        from lib.pdf_check import check
        p = os.path.join(_TMP, "not_a_pdf.pdf")
        with open(p, "w", encoding="utf-8") as f:
            f.write("not a pdf")
        report = check(p, max_pages=1)
        self.assertFalse(report["ok"])

    def test_feedback_names_the_findings(self):
        from lib.pdf_check import check, feedback_for
        out = _build(_overflow_resume())
        fb = feedback_for(check(out["resume"], max_pages=1))
        self.assertIn("page", fb.lower())
        self.assertIn("ONE page", fb)


class BuildResumeGate(unittest.TestCase):
    def test_build_returns_check_report(self):
        out = _build(_one_page_resume())
        self.assertIn("check", out)
        self.assertTrue(out["check"]["ok"])

    def test_build_with_check_disabled(self):
        os.makedirs(_TMP, exist_ok=True)
        path = os.path.join(_TMP, "resume.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_one_page_resume(), f)
        from lib.build_resume import build
        out = build(path, _TMP, company="Acme", pdf_check=False)
        self.assertNotIn("check", out)


class TailorRetry(unittest.TestCase):
    """The gem route retries with PDF feedback when the resume fails the gate."""

    def test_extract_and_build_returns_check(self):
        from tailor import _extract_and_build_resume
        os.makedirs(_TMP, exist_ok=True)
        app_dir = os.path.join(_TMP, "extract_job")
        os.makedirs(app_dir, exist_ok=True)
        good = "```json\n" + json.dumps(_one_page_resume()) + "\n```"
        res = _extract_and_build_resume(good, app_dir, {"company": "Acme"})
        self.assertIsNotNone(res)
        self.assertIn("check", res)
        self.assertTrue(res["check"]["ok"])

    def test_extract_and_build_none_when_no_json(self):
        from tailor import _extract_and_build_resume
        res = _extract_and_build_resume("no json here", _TMP, {})
        self.assertIsNone(res)

    def test_retry_loop_fixes_bad_resume(self):
        """The gem route must re-invoke with PDF feedback when the first
        resume fails the one-page gate, and accept the fixed second pass."""
        from tailor import generate_tailored_docs
        bad_output = "```json\n" + json.dumps(_overflow_resume()) + "\n```"
        good_output = "```json\n" + json.dumps(_one_page_resume()) + "\n```"

        calls = []
        def fake_gem(args, timeout_seconds=None, gem=None):
            calls.append(args[0])
            if "PDF QUALITY CHECK FAILED" in args[0]:
                return True, good_output
            return True, bad_output

        entry = {"url": "https://x.com/job", "company": "Acme",
                 "category": "tech", "title": "ML Engineer",
                 "location": "Ottawa"}
        app_dir = os.path.join(_TMP, "retry_loop_job")
        os.makedirs(app_dir, exist_ok=True)
        with patch("tailor.call_gemini_node", side_effect=fake_gem), \
             patch("tailor.RESULTS_DIR", _TMP), \
             patch("tailor.app_save"), \
             patch("tailor.desc_get", return_value="desc " * 30), \
             patch("tailor.clean_desc", return_value="desc " * 30):
            success, _result = generate_tailored_docs(entry)
        # Second call carried the PDF feedback.
        self.assertTrue(any("PDF QUALITY CHECK FAILED" in c for c in calls))
        self.assertTrue(success)

    def test_retry_exhausts_then_reports_failure(self):
        """When every attempt fails the gate, the loop stops after the cap and
        reports PDF_FAILED (never loops forever)."""
        from tailor import generate_tailored_docs
        bad_output = "```json\n" + json.dumps(_overflow_resume()) + "\n```"

        calls = []
        def fake_gem(args, timeout_seconds=None, gem=None):
            calls.append(args[0])
            return True, bad_output

        entry = {"url": "https://x.com/job", "company": "Acme",
                 "category": "tech", "title": "ML Engineer",
                 "location": "Ottawa"}
        app_dir = os.path.join(_TMP, "retry_exhaust_job")
        os.makedirs(app_dir, exist_ok=True)
        import io, contextlib
        buf = io.StringIO()
        with patch("tailor.call_gemini_node", side_effect=fake_gem), \
             patch("tailor.RESULTS_DIR", _TMP), \
             patch("tailor.app_save"), \
             patch("tailor.desc_get", return_value="desc " * 30), \
             patch("tailor.clean_desc", return_value="desc " * 30), \
             contextlib.redirect_stderr(buf):
            success, result = generate_tailored_docs(entry)
        self.assertIn("PDF_FAILED", buf.getvalue())
        # A resume that cannot pass the gate must NOT be marked tailored — it
        # returns failure so the job goes to `failed` state for retry.
        self.assertFalse(success)
        self.assertIn("PDF quality gate", str(result))
        self.assertEqual(len(calls), 3)  # initial + JI_PDF_RETRY=2 retries

    def test_no_resume_json_is_a_failure_not_a_pass(self):
        """Regression: a gem response with NO resume JSON must fail the tailor
        (the resume does not exist), not silently advance."""
        from tailor import generate_tailored_docs
        calls = []
        def fake_gem(args, timeout_seconds=None, gem=None):
            calls.append(args[0])
            return True, "I could not generate a resume. Please try again."

        entry = {"url": "https://x.com/job", "company": "Acme",
                 "category": "tech", "title": "ML Engineer",
                 "location": "Ottawa"}
        app_dir = os.path.join(_TMP, "retry_nojson_job")
        os.makedirs(app_dir, exist_ok=True)
        import io, contextlib
        buf = io.StringIO()
        with patch("tailor.call_gemini_node", side_effect=fake_gem), \
             patch("tailor.RESULTS_DIR", _TMP), \
             patch("tailor.app_save"), \
             patch("tailor.desc_get", return_value="desc " * 30), \
             patch("tailor.clean_desc", return_value="desc " * 30), \
             contextlib.redirect_stderr(buf):
            success, result = generate_tailored_docs(entry)
        self.assertFalse(success)
        self.assertIn("no resume JSON", str(result))
        self.assertEqual(len(calls), 1)  # no retry on missing JSON


class AdmitPdfGate(unittest.TestCase):
    """The one-page/overlap gate blocks admit unless --force."""

    def _state(self):
        return {"jobs": {"j1": {"state": "active", "stage": "described",
                                "url": "https://x.com/j", "title": "T",
                                "company": "C"}}}

    def _build_overflow_pdf(self):
        os.makedirs(os.path.join(_TMP, "j1"), exist_ok=True)
        path = os.path.join(_TMP, "j1", "resume.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_overflow_resume(), f)
        from lib.build_resume import build
        return build(path, os.path.join(_TMP, "j1"), company="Acme")

    def test_admit_blocked_on_overflow_unless_force(self):
        from tailor import cmd_admit
        self._build_overflow_pdf()
        with patch("tailor.load", return_value=self._state()), \
             patch("tailor.RESULTS_DIR", _TMP), \
             patch("tailor.advance") as advance, \
             patch("lib.grounding.ground", return_value={"blocked": False,
                                                         "framing": [],
                                                         "material": [],
                                                         "figure": [],
                                                         "mismatches": []}):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                cmd_admit("j1")
        err = buf.getvalue()
        self.assertIn("PDF_CHECK_BLOCKED", err)
        advance.assert_not_called()

    def test_admit_force_overrides_gate(self):
        from tailor import cmd_admit
        self._build_overflow_pdf()
        with patch("tailor.load", return_value=self._state()), \
             patch("tailor.RESULTS_DIR", _TMP), \
             patch("tailor.advance") as advance, \
             patch("lib.grounding.ground", return_value={"blocked": False,
                                                         "framing": [],
                                                         "material": [],
                                                         "figure": [],
                                                         "mismatches": []}):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                cmd_admit("j1", force=True)
        err = buf.getvalue()
        self.assertIn("ADMITTED", err)
        advance.assert_called()

    def test_admit_clean_resume_passes(self):
        from tailor import cmd_admit
        os.makedirs(os.path.join(_TMP, "j1"), exist_ok=True)
        path = os.path.join(_TMP, "j1", "resume.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_one_page_resume(), f)
        from lib.build_resume import build
        build(path, os.path.join(_TMP, "j1"), company="Acme")
        with patch("tailor.load", return_value=self._state()), \
             patch("tailor.RESULTS_DIR", _TMP), \
             patch("tailor.advance") as advance, \
             patch("lib.grounding.ground", return_value={"blocked": False,
                                                         "framing": [],
                                                         "material": [],
                                                         "figure": [],
                                                         "mismatches": []}):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                cmd_admit("j1")
        self.assertIn("ADMITTED", buf.getvalue())
        advance.assert_called()


class TailorCheckCmd(unittest.TestCase):
    """tailor.py check <jid> re-runs the gate on an existing resume PDF."""

    def test_check_returns_zero_on_clean(self):
        from tailor import cmd_pdf_check
        os.makedirs(os.path.join(_TMP, "j2"), exist_ok=True)
        path = os.path.join(_TMP, "j2", "resume.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_one_page_resume(), f)
        from lib.build_resume import build
        build(path, os.path.join(_TMP, "j2"), company="Acme")
        with patch("tailor.RESULTS_DIR", _TMP):
            rc = cmd_pdf_check("j2")
        self.assertEqual(rc, 0)

    def test_check_returns_one_on_missing_pdf(self):
        from tailor import cmd_pdf_check
        os.makedirs(os.path.join(_TMP, "j3"), exist_ok=True)
        with patch("tailor.RESULTS_DIR", _TMP):
            rc = cmd_pdf_check("j3")
        self.assertEqual(rc, 1)


class OverlapDetection(unittest.TestCase):
    def test_forced_overlap_detected(self):
        """A PDF with two text lines at the same y must be flagged as overlap."""
        from lib.pdf_check import detect_overlaps
        from fpdf import FPDF, XPos, YPos
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=10)
        pdf.add_page()
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 6, "OVERLAPPING LINE ONE THAT IS LONG ENOUGH",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y = pdf.get_y() - 6
        pdf.set_y(y)
        pdf.cell(0, 6, "SECOND LINE DRAWN OVER THE FIRST",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        out = os.path.join(_TMP, "forced_overlap.pdf")
        pdf.output(out)
        overlaps = detect_overlaps(out)
        self.assertTrue(len(overlaps) >= 1,
                        "expected at least one overlapping word pair")


class ClippingDetection(unittest.TestCase):
    def test_right_edge_clip_detected(self):
        """A non-wrapping cell pushed past the right margin must be flagged."""
        from lib.pdf_check import detect_clipping
        from fpdf import FPDF, XPos, YPos
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=10)
        pdf.add_page()
        pdf.set_font("Helvetica", "", 12)
        # Draw a very wide string in a wide cell (width 300 on a 210mm page).
        pdf.cell(300, 6, "X" * 80, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        out = os.path.join(_TMP, "clip_test.pdf")
        pdf.output(out)
        clipped = detect_clipping(out)
        self.assertTrue(any(c["edge"] == "right" for c in clipped),
                        "expected a right-edge clip finding")

    def test_long_job_header_clips(self):
        """Regression: the realistic 'overlapping text' the user sees is a long
        role/company/location header drawn with non-wrapping RIGHT cells — it
        pushes past the right printable edge. Must be flagged as clipping."""
        from lib.pdf_check import check
        from fpdf import FPDF, XPos, YPos
        class R(FPDF):
            def job_header(self, role, company, location, date):
                self.set_font("Helvetica", "B", 9.5)
                self.cell(0, 4, role, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")
                self.set_font("Helvetica", "I", 9)
                cw = self.get_string_width(company)
                self.cell(cw, 4, company, new_x=XPos.RIGHT, new_y=YPos.TOP, align="L")
                loc_w = self.get_string_width(f" | {location}")
                self.cell(loc_w, 4, f" | {location}", new_x=XPos.RIGHT,
                          new_y=YPos.TOP, align="L")
                self.cell(0, 4, date, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
        pdf = R()
        pdf.set_auto_page_break(auto=True, margin=10)
        pdf.add_page()
        pdf.job_header(
            "Senior Machine Learning Engineer - Applied ML Platform and RecSys "
            "Recommendations Platform, Personalization, Ranking and Matching " * 2,
            "Wayfair LLC", "Toronto, Ontario, Canada", "2021-03 - 2024-06")
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(0, 4, "EXPERIENCE", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        out = os.path.join(_TMP, "real_header.pdf")
        pdf.output(out)
        report = check(out, max_pages=1)
        self.assertFalse(report["ok"])
        self.assertTrue(len(report["clipped"]) >= 1,
                        "long job header should clip at the right edge")


if __name__ == "__main__":
    unittest.main()
