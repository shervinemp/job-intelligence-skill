"""test_corpus.py — Probe corpus regression tests.

Runs the real `_SCAN_JS` (capability scan) and `_READER_JS` (field
reader) against synthetic HTML fixtures that mirror known capability
shapes. If a refactor breaks one of these shapes, the test fails
BEFORE a real job application is affected.

These tests are the platform-agnostic invariant: the capability
scanner and field reader must correctly identify form fields, widgets,
login walls, and EEOC signals in the canonical shapes we've
encountered in production. When we capture a new corpus snapshot from
a real platform (via `apply/common/corpus.py`), we add a fixture here
that mirrors its capability profile.
"""
import os
import sys
import unittest

_SKILL_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _SKILL_DIR)


def _has_jsdom():
    try:
        from apply.common.mock_page import is_available
        return is_available()
    except Exception:
        return False


# Skip the whole module if jsdom is unavailable — clear reason.
_NO_JSDOM = not _has_jsdom()


# ─── Synthetic fixtures ─────────────────────────────────────────────
# Each fixture mirrors a capability shape we've seen in production.

_LOGIN_WALL = '''<html><body>
<h1>Sign in to apply</h1>
<form>
  <input type="email" name="email" placeholder="Email" required/>
  <input type="password" name="password" placeholder="Password" required/>
  <button type="submit">Sign In</button>
  <a href="/create">Create account</a>
</form>
</body></html>'''


_WORKDAY_LIKE = '''<html><body>
<div role="dialog" aria-modal="true">
  <h2>Application</h2>
  <input type="email" name="email" placeholder="Email" required/>
  <input type="text" name="first" placeholder="First Name" required/>
  <input type="text" name="last" placeholder="Last Name" required/>
  <input type="file" name="resume" required/>
  <button aria-haspopup="listbox" data-automation-id="country">Select Country</button>
  <div role="combobox"><input type="text" aria-expanded="false"/></div>
  <input type="radio" name="eeoc_gender" id="g1"/><label for="g1">Male</label>
  <input type="radio" name="eeoc_gender" id="g2"/><label for="g2">Female</label>
  <input type="checkbox" name="consent" id="c1"/><label for="c1">I agree</label>
  <button data-automation-id="signInSubmitButton">Sign In to Apply</button>
  <button>Next</button>
  <div role="progressbar" aria-valuenow="1">Step 1 of 7</div>
</div>
</body></html>'''


_ASHBY_LIKE = '''<html><body>
<h1>Apply for Senior Engineer</h1>
<form>
  <label>Full Name<input type="text" name="name" required/></label>
  <label>Email<input type="email" name="email" required/></label>
  <label>Phone<input type="tel" name="phone"/></label>
  <label>Resume<input type="file" name="resume" required/></label>
  <label>Cover Letter<input type="file" name="cover"/></label>
  <label>LinkedIn URL<input type="url" name="linkedin"/></label>
  <textarea name="notes" placeholder="Why this role?"></textarea>
  <button type="submit">Submit Application</button>
</form>
</body></html>'''


_GREENHOUSE_LIKE = '''<html><body>
<form>
  <input type="text" name="name" required/>
  <input type="email" name="email" required/>
  <input type="tel" name="phone"/>
  <select name="source">
    <option value="">Please select</option>
    <option value="linkedin">LinkedIn</option>
    <option value="referral">Referral</option>
  </select>
  <input type="file" name="resume" required/>
  <button type="submit">Submit</button>
</form>
</body></html>'''


_EMPTY_PAGE = '''<html><body>
<h1>Job expired</h1>
<p>This position is no longer accepting applications.</p>
</body></html>'''


@unittest.skipIf(_NO_JSDOM, "jsdom not installed (npm install jsdom)")
class CapabilityScanFixtures(unittest.TestCase):
    """Each fixture must produce the expected capability profile shape."""

    def setUp(self):
        from apply.common.mock_page import CorpusPage
        from apply.common.capabilities import scan, summarize, profile_hash, suggest_strategy
        self.CorpusPage = CorpusPage
        self.scan = scan
        self.summarize = summarize
        self.profile_hash = profile_hash
        self.suggest_strategy = suggest_strategy

    def test_login_wall_detected(self):
        p = self.CorpusPage.from_html(_LOGIN_WALL, url="https://x.com/login")
        profile = self.scan(p)
        self.assertGreater(profile["password_fields"], 0,
                           "login wall must have password field")
        self.assertGreater(profile["email_fields"], 0,
                           "login wall must have email field")
        self.assertTrue(profile["login_signals"],
                        "login_signals must be non-empty for sign-in text")
        self.assertIn("sign in to apply", profile["login_signals"])
        self.assertFalse(profile["dialog"], "no dialog on bare login wall")
        self.assertFalse(profile["has_progress_bar"])

    def test_workday_dialog_widgets_detected(self):
        p = self.CorpusPage.from_html(_WORKDAY_LIKE, url="https://wd.com/x")
        profile = self.scan(p)
        self.assertTrue(profile["dialog"], "Workday form is in a dialog")
        self.assertGreater(profile["listbox_buttons"], 0,
                           "Workday uses listbox-button dropdowns")
        self.assertGreater(profile["comboboxes"], 0,
                           "Workday has combobox widgets")
        self.assertGreater(profile["file_inputs"], 0)
        self.assertGreater(profile["radio_groups"], 0)
        self.assertTrue(profile["has_progress_bar"])
        self.assertEqual(self.suggest_strategy(profile), "dialog")

    def test_ashby_bare_form(self):
        p = self.CorpusPage.from_html(_ASHBY_LIKE, url="https://jobs.ashbyhq.com/x")
        profile = self.scan(p)
        self.assertFalse(profile["dialog"], "Ashby form is not in a dialog")
        self.assertEqual(profile["password_fields"], 0)
        self.assertGreaterEqual(profile["file_inputs"], 2,
                           "Ashby has resume + cover letter file inputs")
        self.assertGreaterEqual(profile["visible_text_inputs"], 3,
                           "Ashby: name + phone + linkedin url = 3 visible text inputs (email counts separately)")
        self.assertGreater(profile["textarea_count"], 0)
        # Apply buttons should include "Submit Application" text
        self.assertTrue(any("submit" in b for b in profile["apply_buttons"]),
                        f"apply_buttons should include submit: {profile['apply_buttons']}")

    def test_greenhouse_select_detected(self):
        p = self.CorpusPage.from_html(_GREENHOUSE_LIKE, url="https://greenhouse.io/x")
        profile = self.scan(p)
        self.assertGreater(profile["select_elements"], 0,
                           "Greenhouse has native <select>")
        self.assertEqual(profile["comboboxes"], 0,
                         "no ARIA combobox in this fixture")
        self.assertEqual(profile["dialog"], False)

    def test_expired_page_no_capabilities(self):
        p = self.CorpusPage.from_html(_EMPTY_PAGE, url="https://x.com/expired")
        profile = self.scan(p)
        self.assertEqual(profile["visible_text_inputs"], 0)
        self.assertEqual(profile["password_fields"], 0)
        self.assertEqual(profile["file_inputs"], 0)
        self.assertEqual(profile["radio_groups"], 0)
        # suggest_strategy returns None for empty profiles
        self.assertIsNone(self.suggest_strategy(profile))


@unittest.skipIf(_NO_JSDOM, "jsdom not installed (npm install jsdom)")
class FieldReaderFixtures(unittest.TestCase):
    """Field reader must correctly count and label fields per shape."""

    def setUp(self):
        from apply.common.mock_page import CorpusPage
        from apply.common.field_reader import read_fields
        from apply.common.capabilities import discover_widgets, scan
        self.CorpusPage = CorpusPage
        self.read_fields = read_fields
        self.discover_widgets = discover_widgets
        self.scan = scan

    def test_login_wall_fields(self):
        p = self.CorpusPage.from_html(_LOGIN_WALL)
        r = self.read_fields(p, scope="document")
        self.assertEqual(r["fieldCount"], 2, "email + password")
        labels = [f.get("label", "").lower() for f in r["fields"]]
        self.assertTrue(any("email" in l for l in labels), f"no email label: {labels}")
        self.assertTrue(any("password" in l for l in labels), f"no password label: {labels}")
        self.assertEqual(r["pageType"], "form",
                     "pageType is 'form' when fields exist; login_wall only for empty pages")

    def test_workday_dialog_fields(self):
        p = self.CorpusPage.from_html(_WORKDAY_LIKE)
        profile = self.scan(p)
        widgets = self.discover_widgets(profile, None)
        r = self.read_fields(p, scope="dialog", custom_widgets=widgets)
        self.assertGreater(r["fieldCount"], 4, "Workday fixture has 7+ fields")
        self.assertTrue(r["hasFileInput"], "Workday has file input (resume)")
        self.assertTrue(r["hasRequiredFile"])
        # Dialog scope found at least one dropdown (custom widget)
        tags = [f.get("tag") for f in r["fields"]]
        self.assertIn("DROPDOWN", tags, f"custom_widgets must yield DROPDOWN tag: {tags}")

    def test_ashby_bare_form_fields(self):
        p = self.CorpusPage.from_html(_ASHBY_LIKE)
        r = self.read_fields(p, scope="document")
        self.assertGreaterEqual(r["fieldCount"], 6, "Ashby has ~6 fields")
        self.assertTrue(r["hasFileInput"])
        # Submit button should appear in buttons list
        btn_texts = [b.get("text", "").lower() for b in r.get("buttons", [])]
        self.assertTrue(any("submit" in t for t in btn_texts),
                        f"Submit button not found: {btn_texts}")

    def test_greenhouse_select_fields(self):
        p = self.CorpusPage.from_html(_GREENHOUSE_LIKE)
        r = self.read_fields(p, scope="document")
        # select element should appear with options
        selects = [f for f in r["fields"] if f.get("tag") == "SELECT"]
        self.assertGreaterEqual(len(selects), 1, "Greenhouse has a <select>")
        # Source select has at least "Please select" + 2 options
        self.assertGreaterEqual(len(selects[0].get("options", [])), 2,
                                f"select should have options: {selects[0].get('options')}")

    def test_expired_page_zero_fields(self):
        p = self.CorpusPage.from_html(_EMPTY_PAGE)
        r = self.read_fields(p, scope="document")
        self.assertEqual(r["fieldCount"], 0)
        self.assertNotEqual(r["pageType"], "form")


@unittest.skipIf(_NO_JSDOM, "jsdom not installed (npm install jsdom)")
class ProfileHashStability(unittest.TestCase):
    """The same capability shape must hash identically across runs."""

    def test_login_wall_stable_hash(self):
        from apply.common.mock_page import CorpusPage
        from apply.common.capabilities import scan, profile_hash
        p1 = CorpusPage.from_html(_LOGIN_WALL, url="https://a.com/login")
        p2 = CorpusPage.from_html(_LOGIN_WALL, url="https://b.com/login")
        # Different URLs but same DOM shape → same hash
        self.assertEqual(profile_hash(scan(p1)), profile_hash(scan(p2)))

    def test_workday_distinct_from_ashby(self):
        from apply.common.mock_page import CorpusPage
        from apply.common.capabilities import scan, profile_hash
        wd = profile_hash(scan(CorpusPage.from_html(_WORKDAY_LIKE)))
        ash = profile_hash(scan(CorpusPage.from_html(_ASHBY_LIKE)))
        self.assertNotEqual(wd, ash, "Workday-like and Ashby-like must hash distinctly")


if __name__ == "__main__":
    unittest.main()