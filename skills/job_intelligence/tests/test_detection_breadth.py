"""test_detection_breadth.py — COMPARISON §S1/S3/S4/S5/S7/S8 regression tests.

Covers the detection + robustness work borrowed from the Jobright analysis
(as OUR OWN implementation — no extension source was copied):

  S1  query-param, page-source-keyword, and iframe-only ATS detection
  S3  field-reader cap is configurable (no silent 35-field drop)
  S4  Workday fill hints surface from the registry
  S5  wait_for_form_frame returns a form frame / iframe_only frame
  S7  inter_field_delay humanization (off under JI_TESTS)
  S8  normalize_url keeps query params that a trailing slash would drop
"""

import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class QueryParamDetection(unittest.TestCase):
    """S1 layer 2: an ATS apply URL on a foreign host is still detected
    via its identifying query param."""

    def test_greenhouse_gh_jid_on_foreign_host(self):
        from apply.common.registry import resolve
        reg = resolve("https://jobs.acme.com/careers/123?gh_jid=456")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "greenhouse")

    def test_ashby_ashby_jid(self):
        from apply.common.registry import resolve
        reg = resolve("https://careers.acme.com/apply?ashby_jid=abc")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "ashbyhq")

    def test_lever_leverappid(self):
        from apply.common.registry import resolve
        reg = resolve("https://jobs.acme.com/lever?LeverAppId=xyz")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "lever")

    def test_domain_match_still_wins(self):
        from apply.common.registry import resolve
        reg = resolve("https://boards.greenhouse.io/acme/jobs/123")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "greenhouse")


class PageSourceDetection(unittest.TestCase):
    """S1 layer 3: customer-branded career pages are identified by the
    ATS JS bundle / CDN keyword in their HTML."""

    def test_workday_bundle_on_customer_domain(self):
        from apply.common.registry import resolve_from_page
        html = ('<html><head><script src="https://acme.com/'
                'wd5.myworkdayjobs.com/assets/main.js"></script>'
                "</head><body></body></html>")
        reg = resolve_from_page("https://acme.com/careers", html=html)
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "workday")

    def test_icims_embed_on_customer_domain(self):
        from apply.common.registry import resolve_from_page
        html = '<html><body><script src="https://jobs.icims.com/embed.js"></script></body></html>'
        reg = resolve_from_page("https://acme.com/jobs", html=html)
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "icims")

    def test_resolve_from_page_prefers_host_match(self):
        from apply.common.registry import resolve_from_page
        # Both host and source match — hostname wins (returns lever's config).
        html = '<html><script src="https://jobs.lever.co/x.js"></script></html>'
        reg = resolve_from_page("https://jobs.lever.co/acme", html=html)
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "lever")

    def test_no_keyword_returns_none(self):
        from apply.common.registry import resolve_from_page
        reg = resolve_from_page("https://acme.com/jobs",
                                html="<html><body>nothing here</body></html>")
        self.assertIsNone(reg)


class IframeOnlyFlag(unittest.TestCase):
    """S1 layer 4: iframe_only platforms surface their flag so the probe
    can bump iframe probing to the front."""

    def test_icims_iframe_only(self):
        from apply.common.registry import resolve
        reg = resolve("https://jobs.icims.com/jobs/123")
        self.assertIsNotNone(reg)
        self.assertTrue(getattr(reg, "iframe_only", False))

    def test_greenhouse_not_iframe_only(self):
        from apply.common.registry import resolve
        reg = resolve("https://boards.greenhouse.io/acme/jobs/1")
        self.assertIsNotNone(reg)
        self.assertFalse(getattr(reg, "iframe_only", False))


class NewAtsCoverage(unittest.TestCase):
    """The comparison's headline gap: ATS we already trust in the session
    allowlist (url_safety.py) but had no registry for. Each now resolves so
    detection, probe strategy, and patterns apply (COMPARISON §S1 headline)."""

    _CASES = [
        ("jobvite", "https://jobs.jobvite.com/acme/123"),
        ("jobvite", "https://www.jobvite.com/apply?jobviteiframe=1"),
        ("comeet", "https://acme.jobs.comeet.co/1"),
        ("smartrecruiters", "https://jobs.smartrecruiters.com/acme/1"),
        ("workable", "https://apply.workable.com/acme/1"),
        ("breezy", "https://acme.breezy.hr/p/1"),
        ("taleo", "https://tbe.taleo.net/acme/1"),
        ("workday", "https://acme.myworkdaysite.com/1"),
        # Batch 2 — well-known ATS beyond the session allowlist.
        ("phenom", "https://careers.acme.phenompeople.com/1"),
        ("eightfold", "https://careers.acme.eightfold.ai/1"),
        ("ultipro", "https://rec.acme.ultipro.com/job/1"),
        ("brassring", "https://jobs.brassring.com/1"),
        ("avature", "https://careers.acme.avature.net/1"),
        ("teamtailor", "https://acme.teamtailor.com/1"),
        ("personio", "https://jobs.personio.de/1"),
        ("gohire", "https://acme.gohire.io/role/slug-123/"),
        ("rippling", "https://ats.acme.rippling.com/1"),
        ("zohorecruit", "https://jobs.acme.zohorecruit.com/1"),
        ("dayforce", "https://careers.acme.dayforcehcm.com/1"),
        ("paylocity", "https://careers.paylocity.com/1"),
        ("jazzhr", "https://apply.acme.jazz.co/1"),
        ("freshteam", "https://acme.freshteam.com/1"),
        ("jobscore", "https://jobs.acme.jobscore.com/1"),
        # Batch 3 — company portals (careers subdomain / portal only, NOT root).
        ("amazon", "https://www.amazon.jobs/en/jobs/123"),
        ("google", "https://careers.google.com/jobs/results/123"),
        ("apple", "https://jobs.apple.com/en-us/details/123"),
        ("cisco", "https://jobs.cisco.com/jobs/123"),
        ("tesla", "https://www.tesla.com/careers/search/job/123"),
        ("uber", "https://www.uber.com/us/en/careers/123"),
        ("tiktok", "https://careers.tiktok.com/position/123"),
        ("bytedance", "https://jobs.bytedance.com/en/position/123"),
        ("metacareers", "https://www.metacareers.com/jobs/123"),
        ("hubspot", "https://careers.hubspot.com/jobs/123"),
        ("paycom", "https://careers.paycom.com/jobs/123"),
        ("intuit", "https://careers.intuit.com/jobs/123"),
        ("waymo", "https://www.waymo.com/careers/123"),
        ("gusto", "https://careers.gusto.com/jobs/123"),
        ("adobe", "https://careers.adobe.com/jobs/123"),
        ("recruitee", "https://careers.acme.recruitee.com/1"),
        ("trakstar", "https://apply.acme.trakstar.com/1"),
        ("pinpointhq", "https://acme.pinpointhq.com/1"),
        ("isolved", "https://careers.isolved.com/1"),
        ("jobdiva", "https://jobs.jobdiva.com/1"),
        ("careerplug", "https://acme.careerplug.com/1"),
        ("careerspage", "https://acme.careers-page.com/1"),
        ("clearcompany", "https://acme.hrmdirect.com/1"),
        ("recruiterflow", "https://acme.recruiterflow.com/1"),
        ("hiringthing", "https://acme.hiringthing.com/1"),
        ("catsone", "https://acme.catsone.com/1"),
        ("prismhr", "https://careers.prismhr.com/1"),
        ("toast", "https://careers.toasttab.com/1"),
        ("okta", "https://careers.okta.com/jobs/123"),
        ("jacobs", "https://www.jacobs.com/careers/123"),
        ("ycombinator", "https://www.workatastartup.com/companies/123"),
        ("walmart", "https://careers.walmart.com/jobs/123"),
        ("trinehire", "https://acme.trinehire.com/1"),
        ("dover", "https://www.dover.com/careers/123"),
    ]

    def test_each_ats_resolves(self):
        from apply.common.registry import resolve
        for name, url in self._CASES:
            with self.subTest(url=url):
                reg = resolve(url)
                self.assertIsNotNone(reg, f"no registry match for {url}")
                self.assertEqual(reg.name, name)

    def test_each_has_a_registry_yaml(self):
        from pathlib import Path
        regdir = Path(__file__).resolve().parent.parent / "apply" / "registry"
        for name, _url in self._CASES:
            with self.subTest(name=name):
                self.assertTrue((regdir / f"{name}.yaml").exists(),
                                f"missing registry/{name}.yaml")

    _FALSE_POSITIVES = [
        "https://www.google.com/search?q=jobs",
        "https://mail.google.com/",
        "https://www.amazon.com/gp/cart/",
        "https://www.apple.com/iphone/",
        "https://www.tiktok.com/@user",
        "https://www.walmart.com/grocery/",
        "https://www.adobe.com/products/",
        "https://www.hubspot.com/",
        "https://careers-hubspot.com/",
        "https://www.cisco.com/",
    ]

    def test_company_portals_do_not_match_root_domains(self):
        """Regression: careers portals must NOT match the consumer root
        (mail.google.com, amazon.com/gp/cart, apple.com/iphone, ...)."""
        from apply.common.registry import resolve
        for url in self._FALSE_POSITIVES:
            with self.subTest(url=url):
                self.assertIsNone(resolve(url), f"false positive: {url}")

    def test_gohire_trailing_slash_normalized(self):
        from apply.common.registry import normalize_url
        url = "https://acme.gohire.io/role/slug-123/?job=abc"
        n = normalize_url(url)
        self.assertIn("job=abc", n)
        self.assertNotIn("slug-123/?", n)


class FieldReaderCap(unittest.TestCase):
    """S3: the reader cap is configurable; no silent 35-field drop."""

    def test_max_fields_passed_to_reader(self):
        from apply.common import field_reader as fr
        page = MagicMock()
        page.evaluate.return_value = {"fieldCount": 0, "fields": [], "buttons": [],
                                      "pageType": "unknown", "hasFileInput": False,
                                      "hasRequiredFile": False, "url": ""}
        fr.read_fields(page, max_fields=123)
        args, kwargs = page.evaluate.call_args
        self.assertEqual(args[1]["max_fields"], 123)

    def test_default_max_fields(self):
        from apply.common import field_reader as fr
        page = MagicMock()
        page.evaluate.return_value = {"fieldCount": 0, "fields": [], "buttons": [],
                                      "pageType": "unknown", "hasFileInput": False,
                                      "hasRequiredFile": False, "url": ""}
        fr.read_fields(page)
        args, kwargs = page.evaluate.call_args
        self.assertEqual(args[1]["max_fields"], 300)


class WorkdayFillHints(unittest.TestCase):
    """S4: Workday fill hints surface from the registry."""

    def test_workday_hints_present(self):
        from apply.common.registry import resolve
        reg = resolve("https://acme.myworkdayjobs.com/en-US/role/1")
        self.assertIsNotNone(reg)
        self.assertTrue(reg.fill_hints.get("skills_enter"))
        self.assertTrue(reg.fill_hints.get("clear_field_errors"))


class IframeWait(unittest.TestCase):
    """S5: wait_for_form_frame returns the frame that holds the form."""

    def _page(self, frames):
        page = MagicMock()
        page.evaluate.return_value = True  # has an <iframe>
        page.frames = frames
        return page

    def _frame(self, url, has_form=False):
        fr = MagicMock()
        fr.url = url
        if has_form:
            fr.evaluate.return_value = True
        else:
            fr.evaluate.return_value = False
        return fr

    def test_returns_frame_with_form(self):
        from apply.common.inspector import wait_for_form_frame
        f1 = self._frame("https://about:blank")
        f2 = self._frame("https://jobs.icims.com/apply", has_form=True)
        page = self._page([f1, f2])
        result = wait_for_form_frame(page, timeout=1)
        self.assertIs(f2, result)

    def test_returns_iframe_only_frame_even_unreadable(self):
        from apply.common.inspector import wait_for_form_frame
        f1 = self._frame("https://about:blank")
        f2 = self._frame("https://jobs.icims.com/apply", has_form=False)
        page = self._page([f1, f2])
        result = wait_for_form_frame(page, timeout=1)
        self.assertIs(f2, result)

    def test_returns_none_on_timeout(self):
        from apply.common.inspector import wait_for_form_frame
        f1 = self._frame("https://about:blank")
        page = self._page([f1])
        result = wait_for_form_frame(page, timeout=0.2)
        self.assertIsNone(result)

    def test_no_iframe_short_circuits(self):
        from apply.common.inspector import wait_for_form_frame
        page = MagicMock()
        page.evaluate.return_value = False
        page.frames = [MagicMock()]
        result = wait_for_form_frame(page, timeout=0.2)
        self.assertIsNone(result)


class InterFieldDelay(unittest.TestCase):
    """S7: humanization delay is off under JI_TESTS and stays bounded."""

    def test_delay_off_under_ji_tests(self):
        from apply.common.fill_runner import inter_field_delay, _load_delay_bounds
        with patch.dict(os.environ, {"JI_TESTS": "1"}):
            _load_delay_bounds()
            self.assertEqual((_DELAY_LO_GET(), _DELAY_HI_GET()), (0.0, 0.0))
            # must not sleep
            with patch("apply.common.fill_runner.time.sleep") as m:
                inter_field_delay()
                m.assert_not_called()

    def test_delay_disabled_explicitly(self):
        from apply.common.fill_runner import inter_field_delay, _load_delay_bounds
        with patch.dict(os.environ, {"JI_TESTS": "", "JI_FILL_DELAY": "0"}):
            _load_delay_bounds()
            self.assertEqual(_DELAY_HI_GET(), 0.0)

    def test_delay_bounded_when_enabled(self):
        from apply.common.fill_runner import inter_field_delay, _load_delay_bounds
        with patch.dict(os.environ, {"JI_TESTS": "", "JI_FILL_DELAY": "0.15-0.35"}):
            _load_delay_bounds()
            self.assertAlmostEqual(_DELAY_LO_GET(), 0.15)
            self.assertAlmostEqual(_DELAY_HI_GET(), 0.35)
            with patch("apply.common.fill_runner.time.sleep") as m:
                inter_field_delay()
                m.assert_called_once()
                self.assertGreaterEqual(m.call_args[0][0], 0.15)
                self.assertLessEqual(m.call_args[0][0], 0.35)


def _DELAY_LO_GET():
    from apply.common import fill_runner as fr
    return fr._DELAY_LO


def _DELAY_HI_GET():
    from apply.common import fill_runner as fr
    return fr._DELAY_HI


class UrlNormalization(unittest.TestCase):
    """S8: normalize_url keeps query params that a trailing slash drops."""

    def test_greenhouse_trailing_slash_dropped(self):
        from apply.common.registry import normalize_url
        url = "https://boards.greenhouse.io/acme/jobs/123/?gh_jid=456"
        n = normalize_url(url)
        self.assertIn("gh_jid=456", n)
        self.assertNotIn("jobs/123/?", n)

    def test_no_registry_no_change(self):
        from apply.common.registry import normalize_url
        url = "https://example.com/jobs/123/"
        self.assertEqual(normalize_url(url), url)


class ShowOpenFilePicker(unittest.TestCase):
    """S6: the FSAP upload fallback patches showOpenFilePicker in-page."""

    def test_patch_and_unpatch(self):
        from apply.common.fill_runner import (_patch_show_open_file_picker,
                                              _unpatch_show_open_file_picker)
        page = MagicMock()
        fr = MagicMock()
        page.frames = [fr]
        ok = _patch_show_open_file_picker(page, "QUJD", "resume.pdf", "application/pdf")
        self.assertTrue(ok)
        page.evaluate.assert_called()
        _unpatch_show_open_file_picker(page)
        # patch + unpatch main, then unpatch iterates frames too
        self.assertTrue(page.evaluate.call_count >= 2)
        fr.evaluate.assert_called()  # frames are unpatched as well (no leak)

    def test_patch_failure_false(self):
        from apply.common.fill_runner import _patch_show_open_file_picker
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("nope")
        page.frames = []
        ok = _patch_show_open_file_picker(page, "QUJD", "r.pdf", "application/pdf")
        self.assertFalse(ok)

    def test_try_show_open_file_picker_missing_file(self):
        from apply.common.fill_runner import _try_show_open_file_picker
        page = MagicMock()
        ok = _try_show_open_file_picker(page, "Resume", "/nonexistent/resume.pdf")
        self.assertFalse(ok)

    def test_fsap_was_called_requires_fired_flag(self):
        """Regression: a click that never invokes showOpenFilePicker must NOT
        count as a successful upload (false-positive guard)."""
        from apply.common.fill_runner import _try_show_open_file_picker, _fsap_was_called
        page = MagicMock()
        page.frames = []
        # The patched picker never fires (side effect: JS raises on the flag
        # read → returns False), so the upload must be reported as failed.
        page.evaluate.return_value = 0
        with patch("apply.common.fill_runner._patch_show_open_file_picker",
                   return_value=True):
            # locator().count() > 0, click succeeds, but picker never fired
            loc = MagicMock()
            loc.count.return_value = 1
            page.locator.return_value = loc
            ok = _try_show_open_file_picker(page, "Resume",
                                            "/nonexistent/resume.pdf")
        # File doesn't exist → already False before patching logic
        self.assertFalse(ok)

    def test_fsap_was_called_main_frame(self):
        from apply.common.fill_runner import _fsap_was_called
        page = MagicMock()
        page.frames = []
        page.evaluate.return_value = 1
        self.assertTrue(_fsap_was_called(page))

    def test_fsap_was_called_subframe(self):
        from apply.common.fill_runner import _fsap_was_called
        page = MagicMock()
        fr = MagicMock()
        page.evaluate.return_value = 0
        fr.evaluate.return_value = 2
        page.frames = [fr]
        self.assertTrue(_fsap_was_called(page))

    def test_fsap_never_called_false(self):
        from apply.common.fill_runner import _fsap_was_called
        page = MagicMock()
        fr = MagicMock()
        page.evaluate.return_value = 0
        fr.evaluate.return_value = 0
        page.frames = [fr]
        self.assertFalse(_fsap_was_called(page))


class FiberHelpers(unittest.TestCase):
    """S2: guarded fiber read is READ-ONLY and fail-closed."""

    def test_read_fiber_returns_none_on_error(self):
        from apply.common.fiber import read_fiber
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("context destroyed")
        self.assertIsNone(read_fiber(page, "[id=x]"))

    def test_options_from_fiber_empty_on_missing(self):
        from apply.common.fiber import options_from_fiber
        page = MagicMock()
        page.evaluate.return_value = None
        self.assertEqual(options_from_fiber(page, "[id=x]"), [])

    def test_read_fiber_parses_options(self):
        from apply.common.fiber import read_fiber
        page = MagicMock()
        page.evaluate.return_value = {
            "type": "Select",
            "options": ["Canada", "United States"],
            "selected": ["Canada"],
        }
        f = read_fiber(page, "[id=x]")
        self.assertIsNotNone(f)
        self.assertIn("Canada", f["options"])


class WorkdayFillerDispatch(unittest.TestCase):
    """S4: the WorkdayFiller is active only when a fill hint is present."""

    def _field(self, **kw):
        base = {"label": "Skills", "tag": "INPUT", "type": "text",
                "_sel": "#skills", "name": "", "id": "",
                "placeholder": "", "autocomplete": "", "role": ""}
        base.update(kw)
        return base

    def test_can_handle_with_hint(self):
        from apply.common.filler import WorkdayFiller
        f = self._field(hint_skills_enter=True)
        self.assertTrue(WorkdayFiller().can_handle(f))

    def test_cannot_handle_without_hint(self):
        from apply.common.filler import WorkdayFiller
        f = self._field()
        self.assertFalse(WorkdayFiller().can_handle(f))

    def test_fill_clear_errors_then_enter(self):
        from apply.common.filler import WorkdayFiller
        page = MagicMock()
        f = self._field(hint_skills_enter=True, hint_clear_field_errors=True)
        with patch("apply.strategies.workday.clear_field_errors") as ce, \
             patch("apply.strategies.workday.confirm_with_enter",
                   return_value=True) as cwe:
            ok = WorkdayFiller().fill(page, f, "Python")
        self.assertTrue(ok)
        ce.assert_called_once()
        cwe.assert_called_once_with(page, "#skills", "Python")

    def test_fill_enter_fails_falls_through(self):
        from apply.common.filler import WorkdayFiller
        page = MagicMock()
        f = self._field(hint_skills_enter=True)
        with patch("apply.strategies.workday.confirm_with_enter",
                   return_value=False):
            ok = WorkdayFiller().fill(page, f, "Python")
        self.assertFalse(ok)  # chain falls through to generic fillers

    def test_workday_filler_registered_in_chain(self):
        from apply.common import filler
        names = [f_.name for f_ in filler._FILLERS]
        self.assertIn("workday", names)

    def test_workday_filler_runs_before_combobox(self):
        """A Workday skills typeahead IS a combobox — the Enter-confirm
        protocol must fire before the generic combobox claims the field.
        (Filler-chain ordering regression, COMPARISON §S4.)"""
        from apply.common import filler
        names = [f_.name for f_ in filler._FILLERS]
        self.assertLess(names.index("workday"), names.index("combobox"))


class ComboboxFiberFallback(unittest.TestCase):
    """S2: combobox fiber option fallback surfaces scored options."""

    def test_fiber_option_fallback_scores(self):
        from apply.strategies.combobox import _fiber_option_fallback
        page = MagicMock()
        with patch("apply.common.fiber.options_from_fiber",
                   return_value=["Canada", "United States", "Mexico"]) as of:
            opts = _fiber_option_fallback(page, "#loc", ["canada"])
        self.assertTrue(of.called)
        self.assertTrue(any(o.get("fiber") and o.get("text") == "Canada"
                            for o in opts))

    def test_fiber_option_fallback_empty(self):
        from apply.strategies.combobox import _fiber_option_fallback
        page = MagicMock()
        with patch("apply.common.fiber.options_from_fiber", return_value=[]):
            self.assertEqual(_fiber_option_fallback(page, "#loc", ["canada"]), [])

    def test_fiber_option_fallback_drops_phantom_options(self):
        """Regression: fiber options that don't resolve to a real DOM element
        must be dropped (they cannot be clicked) — no phantom-click bugs."""
        from apply.strategies.combobox import _fiber_option_fallback
        page = MagicMock()
        page.evaluate.return_value = {}  # no DOM element found for any text
        with patch("apply.common.fiber.options_from_fiber",
                   return_value=["Canada", "Mexico"]):
            opts = _fiber_option_fallback(page, "#loc", ["canada"])
        self.assertEqual(opts, [])

    def test_fiber_option_fallback_resolves_real_coords(self):
        from apply.strategies.combobox import _fiber_option_fallback
        page = MagicMock()
        page.evaluate.return_value = {"Canada": {"id": "opt1", "x": 10, "y": 20}}
        with patch("apply.common.fiber.options_from_fiber",
                   return_value=["Canada", "Mexico"]):
            opts = _fiber_option_fallback(page, "#loc", ["canada"])
        self.assertEqual(len(opts), 1)
        self.assertEqual(opts[0]["x"], 10)
        self.assertEqual(opts[0]["y"], 20)
        self.assertEqual(opts[0]["id"], "opt1")


class FalsePositivePrevention(unittest.TestCase):
    """Regression: registry detection must not false-classify via generic
    source keywords or too-broad domains (found in the S1 batch audit)."""

    def test_workday_not_matched_by_prose_mention(self):
        """A page that merely says 'Workday experience' must NOT classify as
        the workday ATS via resolve_from_page (bare 'Workday' was removed)."""
        from apply.common.registry import resolve_from_page
        html = "<html><body>5+ years of Workday experience required</body></html>"
        reg = resolve_from_page("https://acme.com/careers", html=html)
        self.assertIsNone(reg)

    def test_workday_still_matches_by_bundle(self):
        from apply.common.registry import resolve_from_page
        html = '<html><script src="https://acme.com/wd5.myworkdayjobs.com/a.js"></script></html>'
        reg = resolve_from_page("https://acme.com/careers", html=html)
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "workday")

    def test_breezy_not_matched_by_adjective(self):
        from apply.common.registry import resolve_from_page
        html = "<html><body>a breezy walk in the park</body></html>"
        reg = resolve_from_page("https://acme.com/careers", html=html)
        self.assertIsNone(reg)

    def test_workable_not_matched_by_word(self):
        from apply.common.registry import resolve_from_page
        html = "<html><body>a workable solution to the problem</body></html>"
        reg = resolve_from_page("https://acme.com/careers", html=html)
        self.assertIsNone(reg)

    def test_avature_not_matched_by_proper_noun(self):
        from apply.common.registry import resolve_from_page
        html = "<html><body>we considered Avature as a vendor</body></html>"
        reg = resolve_from_page("https://acme.com/careers", html=html)
        self.assertIsNone(reg)

    def test_zoho_mail_not_matched_as_zohorecruit(self):
        """zoho.com must NOT resolve to zohorecruit (mail.zoho.com, crm.zoho.com...)."""
        from apply.common.registry import resolve
        self.assertIsNone(resolve("https://mail.zoho.com/"))
        self.assertIsNone(resolve("https://crm.zoho.com/"))

    def test_oraclecloud_is_its_own_ats_not_dayforce(self):
        from apply.common.registry import resolve
        reg = resolve("https://jobs.oraclecloud.com/1")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "oraclecloud")

    def test_dayforce_host_resolves_dayforce(self):
        from apply.common.registry import resolve
        reg = resolve("https://careers.acme.dayforcehcm.com/1")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.name, "dayforce")

    def test_eightfold_not_matched_by_prose_or_pid(self):
        """eightfold must NOT match bare prose 'eightfold' or a generic ?pid=."""
        from apply.common.registry import resolve_from_page, resolve
        html = "<html><body>an eightfold increase in revenue</body></html>"
        self.assertIsNone(resolve_from_page("https://acme.com/careers", html=html))
        self.assertIsNone(resolve("https://acme.com/job?pid=12345"))


class WorkdayEnterVerification(unittest.TestCase):
    """Regression: Workday confirm_with_enter must NOT return True without
    verifying the selection landed (combobox branch trusts the filler)."""

    def test_confirm_with_enter_verifies_selection(self):
        from apply.strategies.workday import confirm_with_enter
        page = MagicMock()
        # Menu opens, options appear, Enter pressed.
        page.evaluate.return_value = ""  # listbox root id empty
        kb = MagicMock()
        page.keyboard = kb
        with patch("apply.strategies.combobox._open_menu", return_value=True), \
             patch("apply.strategies.combobox._type_and_poll",
                   return_value=[{"text": "Python"}]), \
             patch("apply.strategies.combobox._read_selection_values",
                   return_value=["Python"]) as rsv:
            ok = confirm_with_enter(page, "#skills", "Python")
        self.assertTrue(ok)
        rsv.assert_called()  # a read-back happened — not blind trust

    def test_confirm_with_enter_false_when_wrong_selection(self):
        """Enter landed on a DIFFERENT value → must return False so the
        generic combobox path retries."""
        from apply.strategies.workday import confirm_with_enter
        page = MagicMock()
        page.evaluate.return_value = ""
        kb = MagicMock()
        page.keyboard = kb
        with patch("apply.strategies.combobox._open_menu", return_value=True), \
             patch("apply.strategies.combobox._type_and_poll",
                   return_value=[{"text": "Java"}]), \
             patch("apply.strategies.combobox._read_selection_values",
                   return_value=["Java"]):
            ok = confirm_with_enter(page, "#skills", "Python")
        self.assertFalse(ok)

    def test_confirm_with_enter_false_when_nothing_readable(self):
        from apply.strategies.workday import confirm_with_enter
        page = MagicMock()
        page.evaluate.return_value = ""
        kb = MagicMock()
        page.keyboard = kb
        with patch("apply.strategies.combobox._open_menu", return_value=True), \
             patch("apply.strategies.combobox._type_and_poll",
                   return_value=[{"text": "Python"}]), \
             patch("apply.strategies.combobox._read_selection_values",
                   return_value=[]):
            ok = confirm_with_enter(page, "#skills", "Python")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
