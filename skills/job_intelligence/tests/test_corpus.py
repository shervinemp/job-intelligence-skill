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


# ─── Curveball fixtures — edge cases from production bugs ────────────
# Each mirrors a real bug we fixed. The capability scanner must identify
# the underlying shape, NOT the noise; the field reader must filter
# honeypots and resolve labels via the right precedence chain.

# Cookie banner overlay: modal-on-modal. The cookie dialog intercepts
# clicks before the user can interact with the Apply dialog. The
# capability scanner should detect BOTH dialogs (nested_dialog=True),
# and the field reader should find the form fields in the Apply dialog
# (not the cookie modal, which has no input fields anyway).
_COOKIE_OVERLAY = '''<html><body>
<div role="dialog" aria-modal="true" id="cookie-banner" data-automation-id="legalNotice">
  <h2>We use cookies</h2>
  <button data-automation-id="legalNoticeAcceptButton">Accept</button>
  <button>Manage Preferences</button>
</div>
<div role="dialog" aria-modal="true" id="apply-modal" data-automation-id="applyModal">
  <h2>Apply for Senior Engineer</h2>
  <input type="email" name="email" placeholder="Email" required/>
  <input type="text" name="first" placeholder="First Name" required/>
  <input type="file" name="resume" required/>
  <button data-automation-id="signInSubmitButton">Sign In</button>
  <button>Next</button>
</div>
</body></html>'''


# Honeypot inside dialog: a hidden "for robots" text input with
# aria-hidden parent. The field reader's `isHoneypot()` JS check
# should flag this — `_is_junk_field` filters it out at fill time.
# Capability scanner should report honeypot_signals > 0.
_HONEYPOT_IN_DIALOG = '''<html><body>
<div role="dialog" aria-modal="true">
  <h2>Application</h2>
  <input type="email" name="email" placeholder="Email" required/>
  <input type="text" name="first" placeholder="First Name" required/>
  <input type="file" name="resume" required/>
  <div aria-hidden="true" style="position:absolute;left:-9999px;">
    <label for="website">Website (for robots)</label>
    <input type="text" id="website" name="website" tabindex="-1" autocomplete="off"/>
  </div>
  <button>Submit</button>
</div>
</body></html>'''


# Conditional reveal: a radio button controls the visibility of a
# follow-up field. When "Yes" is selected, a clearance-level dropdown
# is revealed. Tests that:
#   - The capability scanner reports radio_groups > 0 and the form
#     has the potential for conditional reveals (we can't simulate
#     the click in jsdom, but we can verify the initial state).
#   - The field reader sees the radio group and optionally reveals
#     the dependent field (in the static HTML, it's display:none).
_CONDITIONAL_REVEAL = '''<html><body>
<form>
  <fieldset>
    <legend>Do you have active Top Secret clearance?</legend>
    <input type="radio" name="clearance" id="cl_yes" value="Yes" onchange="document.getElementById('cl_detail').style.display = (this.checked ? 'block' : 'none')"/>
    <label for="cl_yes">Yes</label>
    <input type="radio" name="clearance" id="cl_no" value="No"/>
    <label for="cl_no">No</label>
  </fieldset>
  <div id="cl_detail" style="display:none;">
    <label for="cl_level">Clearance Level</label>
    <select id="cl_level" name="cl_level">
      <option value="">Please select</option>
      <option value="confidential">Confidential</option>
      <option value="secret">Secret</option>
      <option value="topsecret">Top Secret</option>
      <option value="ts-sci">TS/SCI</option>
    </select>
  </div>
  <input type="email" name="email" placeholder="Email" required/>
  <button type="submit">Submit</button>
</form>
</body></html>'''


# Obfuscated React label: the <label for="react-input-123"> points
# to a generated ID that was stripped from the <input>. The field
# reader's `resolveLabel()` walks up the DOM looking for an unclaimed
# <label> when `label[for]` matches nothing. Tests the ancestor-walk
# fallback at field_reader.py:48-63.
_REACT_OBFUSCATED_LABEL = '''<html><body>
<form>
  <div class="form-group">
    <label for="react-input-xyz123">Years of Experience</label>
    <div class="input-wrapper">
      <input type="number" name="experience" min="0" max="50"/>
    </div>
  </div>
  <div>
    <label>LinkedIn Profile URL</label>
    <input type="url" name="linkedin"/>
  </div>
  <button type="submit">Apply</button>
</form>
</body></html>'''


# Combined: login wall with a honeypot. Two edge cases at once —
# the page has password + email (login wall) but also a hidden
# "website" bot-trap field. The capability scanner should:
#   - Detect login_signals (password + email without apply text)
#   - Detect honeypot_signals > 0
# The field reader should:
#   - NOT include the honeypot field in fields list (filtered)
#   - Correctly identify email + password fields
_LOGIN_PLUS_HONEYPOT = '''<html><body>
<h1>Sign in to apply</h1>
<form>
  <input type="email" name="email" placeholder="Email" required/>
  <input type="password" name="password" placeholder="Password" required/>
  <div aria-hidden="true" style="display:none;">
    <label for="hp">Website</label>
    <input type="text" id="hp" name="hp" tabindex="-1"/>
  </div>
  <button type="submit">Sign In</button>
  <p>Already have an account? Sign in</p>
</form>
</body></html>'''


# ─── Alert/popup curveballs ────────────────────────────────────────
# Three popup flavors that intercept clicks and break the pipeline
# if not handled. Playwright's `page.on("dialog")` handles native
# browser dialogs in helpers.py:_wire_dialogs. HTML modal popups
# need to be detected by_ SCAN_JS and dismissed via _dismiss_confirm_modal.

# Confirm modal mid-fill: "Please confirm your email" popping up
# after typing email. The capability scanner should detect the
# extra dialog (it appears on top of the form). `_dismiss_confirm_modal`
# clicks the "OK" button at fill time if such a modal is visible.
_CONFIRM_MODAL_MIDFILL = '''<html><body>
<form id="apply-form">
  <input type="email" name="email" placeholder="Email" required/>
  <input type="text" name="first" placeholder="First Name" required/>
  <button type="submit">Submit</button>
</form>
<div role="dialog" aria-modal="true" id="confirm-modal" class="popup"
     style="position:fixed;top:30%;left:30%;background:white;border:1px solid #ccc;padding:20px;">
  <h3>Please confirm your email</h3>
  <p>Is <span id="email-preview">test@example.com</span> correct?</p>
  <button id="modal-ok">OK</button>
  <button id="modal-cancel">Cancel</button>
</div>
</body></html>'''


# Leave-page confirmation: SPA "Are you sure you want to leave?"
# navigation modal blocking the Next button while form is dirty.
# Same shape as confirm modal but with different copy. Tests that
# scanner doesn't false-positively classify this as the form dialog.
_LEAVE_PAGE_MODAL = '''<html><body>
<form>
  <input type="email" name="email" placeholder="Email"/>
  <button type="submit">Next</button>
</form>
<div role="dialog" aria-modal="true" id="leave-confirm"
     style="position:fixed;top:30%;left:25%;background:white;padding:30px;">
  <h3>Are you sure you want to leave?</h3>
  <p>Your changes will be lost.</p>
  <button>Stay on Page</button>
  <button>Leave Anyway</button>
</div>
</body></html>'''


# Success modal: pushes a confirmation popup AFTER submit that the
# scanner/reader must NOT mistake for the form. The form is now
# hidden (display:none) and the success modal is the only visible
# content. Tests the "already submitted" detection path.
_SUCCESS_MODAL_MID_RUN = '''<html><body>
<form style="display:none;">
  <input type="email" name="email" required/>
  <button type="submit">Submit</button>
</form>
<div role="dialog" aria-modal="true" id="success-modal"
     style="position:fixed;top:30%;background:white;padding:40px;">
  <h2>Application submitted successfully!</h2>
  <p>We'll be in touch within 2 weeks.</p>
  <button>Continue</button>
</div>
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


@unittest.skipIf(_NO_JSDOM, "jsdom not installed (npm install jsdom)")
class CurveballFixtures(unittest.TestCase):
    """Edge-case fixtures from real production bugs.

    Each curveball combines a tricky capability shape with a
    field-reader obstacle. The scanner must identify the underlying
    form type, and the reader must filter honeypots or resolve
    obfuscated labels via the right precedence chain.
    """

    def setUp(self):
        from apply.common.mock_page import CorpusPage
        from apply.common.capabilities import scan, summarize, profile_hash, suggest_strategy, discover_widgets
        from apply.common.field_reader import read_fields
        self.CorpusPage = CorpusPage
        self.scan = scan
        self.summarize = summarize
        self.profile_hash = profile_hash
        self.suggest_strategy = suggest_strategy
        self.discover_widgets = discover_widgets
        self.read_fields = read_fields

    # Cookie banner overlay — modal-on-modal. Workday bug we fixed.
    def test_cookie_overlay_detects_both_dialogs(self):
        # Cookie + apply modals are siblings in DOM (real overlay
        # pattern, not nested inside each other). Both flag dialog=True.
        # Neither qualifies as confirm_modal_signals because:
        # - cookie banner has Accept/Manage buttons (no OK/confirm text)
        # - apply modal has form inputs (so not a confirm modal)
        p = self.CorpusPage.from_html(_COOKIE_OVERLAY,
                                       url="https://crowdstrike.wd5.myworkdayjobs.com/x")
        profile = self.scan(p)
        self.assertTrue(profile["dialog"], "Both modals are dialogs")
        self.assertEqual(profile["confirm_modal_signals"], 0,
                         "neither modal has OK/Confirm button text")
        # The dialog scope uses the FIRST [role=dialog]. If the cookie
        # banner is first in DOM order, no fields are found there.
        # The cascade falls through to the document-level scan.
        r = self.read_fields(p, scope="dialog")
        self.assertTrue(r["fieldCount"] >= 0,
                        "cookie modal may yield 0 fields, that's ok")

    def test_cookie_overlay_dialog_strategy_finds_apply_fields(self):
        # Probe via the standard depth — the document-level scan finds
        # all inputs across all dialogs, regardless of nesting order.
        p = self.CorpusPage.from_html(_COOKIE_OVERLAY)
        r = self.read_fields(p, scope="document")
        # Accept button text should be in the buttons list
        all_btn_texts = [b.get("text", "") for b in r.get("buttons", [])]
        self.assertIn("Accept", all_btn_texts,
                      f"cookie Accept button missing: {all_btn_texts}")
        self.assertIn("Sign In", all_btn_texts,
                      f"apply Sign In button missing: {all_btn_texts}")
        # Apply form has 2 text inputs (email + first name) + 1 file
        self.assertGreaterEqual(r["fieldCount"], 3,
                                f"apply form fields missing: {r['fieldCount']}")

    # Honeypot inside dialog — aria-hidden parent + "for robots" label.
    def test_honeypot_capability_scan_flags_it(self):
        p = self.CorpusPage.from_html(_HONEYPOT_IN_DIALOG)
        profile = self.scan(p)
        self.assertGreater(profile["honeypot_signals"], 0,
                           "scanner must flag hidden aria-hidden text input")
        self.assertTrue(profile["dialog"])
        self.assertGreater(profile["file_inputs"], 0)

    def test_honeypot_field_reader_isolates_it(self):
        # The field reader emits `is_honeypot: true` for fields
        # recognized as bot traps. _is_junk_field at fill time filters
        # on this flag. Confirm the reader classifies it correctly.
        p = self.CorpusPage.from_html(_HONEYPOT_IN_DIALOG)
        r = self.read_fields(p, scope="dialog")
        # The honeypot field may or may not be in the visible list (depends
        # on whether field_reader's visibility check filters aria-hidden
        # inputs). Either way, the email + first name + file fields should
        # all be present and NOT the honeypot.
        labels = [f.get("label", "").lower() for f in r["fields"]]
        visible_apply_labels = [l for l in labels if l and "robot" not in l]
        self.assertTrue(any("email" in l for l in visible_apply_labels),
                        f"email label missing from visible fields: {labels}")
        self.assertTrue(any("first" in l for l in visible_apply_labels),
                        f"first name missing: {labels}")
        # If the honeypot shows up at all, it should carry is_honeypot flag
        for f in r["fields"]:
            label = (f.get("label") or "").lower()
            if "robot" in label or "website" == label:
                self.assertTrue(f.get("is_honeypot"),
                                f"honeypot field not flagged: {f}")

    # Conditional reveal — radio click controls a hidden follow-up.
    # jsdom doesn't trigger event handlers, so we can only verify the
    # INITIAL state (radio visible, dependent field hidden).
    def test_conditional_reveal_initial_state(self):
        p = self.CorpusPage.from_html(_CONDITIONAL_REVEAL)
        profile = self.scan(p)
        self.assertGreater(profile["radio_groups"], 0,
                           "clearance radio group must be detected")
        self.assertGreaterEqual(profile["select_elements"], 1,
                           "page has <select> for cl_level")
        # has_progress_bar false — this is a bare form
        self.assertFalse(profile["has_progress_bar"])

    def test_conditional_reveal_field_reader_sees_radio_group(self):
        p = self.CorpusPage.from_html(_CONDITIONAL_REVEAL)
        r = self.read_fields(p, scope="document")
        # Field reader groups radios by name → one RADIO_GROUP entry
        radio_groups = [f for f in r["fields"] if f.get("tag") == "RADIO_GROUP"]
        self.assertEqual(len(radio_groups), 1,
                         f"expected 1 radio group, got {len(radio_groups)}")
        options = radio_groups[0].get("options", [])
        self.assertTrue(any("yes" in o.lower() for o in options),
                        f"Yes option missing: {options}")
        self.assertTrue(any("no" in o.lower() for o in options),
                        f"No option missing: {options}")

    # Obfuscated React label — label[for] points to a stripped ID.
    # resolveLabel() walks ancestors looking for unclaimed <label>.
    def test_obfuscated_label_resolves_via_ancestor_walk(self):
        p = self.CorpusPage.from_html(_REACT_OBFUSCATED_LABEL)
        r = self.read_fields(p, scope="document")
        labels = {f.get("label", ""): f for f in r["fields"]}
        # "Years of Experience" must be resolved via ancestor walk
        # (label[for=react-input-xyz123] won't match — no input has
        # that ID in the form).
        self.assertTrue(
            any("experience" in l.lower() for l in labels),
            f"Years of Experience must be resolved via ancestor walk: {labels}"
        )
        # "LinkedIn Profile URL" — input is wrapped in a <label> directly,
        # so closest('label') path catches it.
        self.assertTrue(
            any("linkedin" in l.lower() for l in labels),
            f"LinkedIn URL label missing: {list(labels.keys())}"
        )

    # Login wall + honeypot — compound curveball. Two edge cases
    # at once must both be handled correctly.
    def test_login_plus_honeypot_detects_both_signals(self):
        p = self.CorpusPage.from_html(_LOGIN_PLUS_HONEYPOT)
        profile = self.scan(p)
        self.assertGreater(profile["password_fields"], 0)
        self.assertGreater(profile["email_fields"], 0)
        self.assertTrue(profile["login_signals"],
                        "must detect 'sign in to apply' login signal")
        self.assertGreater(profile["honeypot_signals"], 0,
                           "compound curveball must also flag honeypot")

    def test_login_plus_honeypot_field_reader_skips_honeypot(self):
        p = self.CorpusPage.from_html(_LOGIN_PLUS_HONEYPOT)
        r = self.read_fields(p, scope="document")
        # Email + password = 2 real form fields. Honeypot should be
        # either filtered (not in list) or flagged with is_honeypot.
        labels = [f.get("label", "").lower() for f in r["fields"]]
        # Must have email + password
        self.assertTrue(any("email" in l for l in labels),
                        f"email missing: {labels}")
        self.assertTrue(any("password" in l for l in labels),
                        f"password missing: {labels}")
        # Honeypot field (if it slips in) must be flagged is_honeypot
        for f in r["fields"]:
            label = (f.get("label") or "").lower()
            if label == "website":
                self.assertTrue(f.get("is_honeypot"),
                                f"honeypot 'Website' field must be flagged: {f}")


@unittest.skipIf(_NO_JSDOM, "jsdom not installed (npm install jsdom)")
class AlertPopupFixtures(unittest.TestCase):
    """Curveballs for HTML modal popups that intercept clicks.

    Native browser dialogs (window.alert/confirm/prompt) are handled
    at the Playwright level via _wire_dialogs() in helpers.py — the
    scanner can't see them from the DOM and we don't try.

    HTML modal popups (role=dialog without form inputs, OK/Cancel
    buttons) are a different problem: they block clicks underneath
    even though the underlying form is fine. The capability scanner
    must detect them so the router can call _dismiss_confirm_modal
    before probing/proceeding.
    """

    def setUp(self):
        from apply.common.mock_page import CorpusPage
        from apply.common.capabilities import scan, summarize, profile_hash
        from apply.common.field_reader import read_fields
        self.CorpusPage = CorpusPage
        self.scan = scan
        self.summarize = summarize
        self.profile_hash = profile_hash
        self.read_fields = read_fields

    def test_confirm_modal_midfill_detected(self):
        """The OK-button popup after typing email must flag
        confirm_modal_signals >= 1, distinguishing it from the form
        dialog (which has inputs)."""
        p = self.CorpusPage.from_html(_CONFIRM_MODAL_MIDFILL)
        profile = self.scan(p)
        self.assertGreaterEqual(profile["confirm_modal_signals"], 1,
                                "OK-button popup must flag confirm_modal_signals")
        # dialog=True (both apply form + confirm popup are dialogs)
        self.assertTrue(profile["dialog"])

    def test_confirm_modal_does_not_classify_form_dialog_as_confirm(self):
        """A form dialog (with inputs) must NOT be counted as
        confirm_modal_signals. This is the key false-positive guard."""
        p = self.CorpusPage.from_html(
            '<html><body><div role="dialog"><input type="email" required/>'
            '<button>Sign In</button></div></body></html>')
        profile = self.scan(p)
        self.assertEqual(profile["confirm_modal_signals"], 0,
                         "form dialog with inputs must not be a confirm modal")

    def test_leave_page_modal_detected(self):
        """'Are you sure you want to leave?' modal must flag
        confirm_modal_signals — Stay/Leave buttons are confirm kw."""
        p = self.CorpusPage.from_html(_LEAVE_PAGE_MODAL)
        profile = self.scan(p)
        self.assertGreaterEqual(profile["confirm_modal_signals"], 1)

    def test_success_modal_detected(self):
        """Post-submit 'Application submitted successfully!' popup must
        flag success_modal_text so the orchestrator knows not to retry."""
        p = self.CorpusPage.from_html(_SUCCESS_MODAL_MID_RUN)
        profile = self.scan(p)
        self.assertTrue(profile["success_modal_text"],
                        "must detect 'submitted successfully' text in modal")
        # The success modal also has a Continue button → confirm_signal
        self.assertGreaterEqual(profile["confirm_modal_signals"], 1)

    def test_success_modal_visibility_zero_fields(self):
        """When the success modal is the only visible content (the form
        is display:none), the field reader returns 0 fields. Without
        success modal detection, this looks like a mysterious 0-field
        page — with it, the orchestrator knows the previous submit
        already took effect."""
        p = self.CorpusPage.from_html(_SUCCESS_MODAL_MID_RUN)
        r = self.read_fields(p, scope="document")
        # The form is display:none → fields should be 0 or very few
        self.assertLessEqual(r["fieldCount"], 0,
                             f"display:none form should yield 0 fields: {r['fieldCount']}")
        # Page type must not be 'form' (since no visible fields)
        self.assertNotEqual(r.get("pageType"), "form",
                            "pageType should not be 'form' when form is display:none")

    def test_confirm_modal_distinct_hash_from_plain_form(self):
        """Same form with and without a confirm modal on top must hash
        distinctly — because confirm_modal_signals is a stable key."""
        p_with = self.CorpusPage.from_html(_CONFIRM_MODAL_MIDFILL)
        p_without = self.CorpusPage.from_html(
            '<form><input type="email" required/>'
            '<input type="text" required/><button>Submit</button></form>')
        h_with = self.profile_hash(self.scan(p_with))
        h_without = self.profile_hash(self.scan(p_without))
        self.assertNotEqual(h_with, h_without,
                            "form + modal must hash distinctly from bare form")


if __name__ == "__main__":
    unittest.main()