"""test_linkedin_clean.py — LinkedIn description cleaner regression tests.

Two real bugs fixed this session:
  1. pre_fetch clicked ALL 14 '… more' buttons inside the page's scrollable
     container → re-render + scroll per click → a hang that looked like
     infinite scrolling. The description is ALREADY in the DOM, so pre_fetch
     is now a no-op for extraction.
  2. clean() assumed newline-separated text: a line-split missed 'About the
     job' (LinkedIn's textContent has few long lines), and a MULTILINE regex
     with nav keywords ('easy apply') matched keywords that appear AFTER the
     description in script/nav text — deleting the description and leaving
     trailing junk. clean() now slices at the marker and does NOT run the
     nav-strip regex on the description.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class CleanRegression(unittest.TestCase):
    def test_keeps_about_the_job_onward(self):
        from lib.platforms.linkedin import clean
        text = ("nav junk\nscript window.__x\nAbout the jobThis is the real "
                "description with lots of detail.\nmore detail\nset alert "
                "for similar jobs\ntrailing")
        out = clean(text)
        self.assertTrue(out.startswith("About the job"), out)
        self.assertIn("real description", out)
        self.assertNotIn("nav junk", out)
        self.assertNotIn("window.__x", out)

    def test_easy_apply_keyword_after_description_does_not_mangle(self):
        """The bug: 'easy apply' appears in nav/script AFTER the description,
        so a MULTILINE nav-strip regex deleted the description. It must not."""
        from lib.platforms.linkedin import clean
        text = ("About the jobSenior Software Engineer role. Easy Apply "
                "button available. Full description here. More content.")
        out = clean(text)
        # the description survives even though 'easy apply' appears after it
        self.assertIn("Senior Software Engineer role", out)

    def test_missing_marker_returns_original(self):
        from lib.platforms.linkedin import clean
        out = clean("some text without the marker")
        self.assertIn("some text", out)

    def test_empty_input(self):
        from lib.platforms.linkedin import clean
        self.assertEqual(clean(""), "")

    def test_real_shape_linkedin_text_content(self):
        """LinkedIn's textContent is few long lines — clean must handle it
        without newline-separated assumptions."""
        from lib.platforms.linkedin import clean
        text = ("0 notificationsHomeGoogleResearch Scientist, Paradigms of "
                "IntelligenceAbout the jobFor Canada Applicants:This posting "
                "is for a new vacancy.set alert for similar jobsMore nav")
        out = clean(text)
        self.assertTrue(out.startswith("About the job"), out)
        self.assertIn("For Canada Applicants", out)
        self.assertNotIn("set alert", out)


class PreFetchNoOp(unittest.TestCase):
    def test_pre_fetch_returns_none_without_touching_page(self):
        """pre_fetch must be a no-op — it must not query or click the 14
        '… more' buttons (the scroll/hang source)."""
        from lib.platforms.linkedin import pre_fetch
        page = unittest.mock.MagicMock()
        pre_fetch(page)
        page.locator.assert_not_called()

    def test_extract_text_uses_text_content_not_inner_text(self):
        """innerText forces layout reflow on LinkedIn's huge DOM (the other
        stall); textContent reads raw text without reflow."""
        from lib.platforms.linkedin import extract_text
        page = unittest.mock.MagicMock()
        page.evaluate.return_value = "about the job full text"
        out = extract_text(page)
        self.assertEqual(out, "about the job full text")
        js = page.evaluate.call_args[0][0]
        self.assertIn("textContent", js)
        self.assertNotIn("innerText", js)


if __name__ == "__main__":
    unittest.main()
