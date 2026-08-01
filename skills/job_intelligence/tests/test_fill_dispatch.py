"""Unit tests for apply/act/helpers.py — fill dispatch and field resolution."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FillWithPlaywright(unittest.TestCase):
    """_fill_with_playwright must not raise NameError on `filled_keys`
    and must actually dispatch fields to the deterministic filler."""

    def test_fills_field_and_returns_it(self):
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        page.evaluate.return_value = ""
        field = {
            "label": "First name", "tag": "INPUT", "type": "text",
            "_sel": "#first_name", "name": "first_name", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None, "_why": "",
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.strategies.dispatch.field_deterministic", return_value=True), \
             patch("apply.act.helpers.resolve", ) as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = "John"
            resolve_mock.return_value.provenance = "profile"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        self.assertEqual(len(filled), 1)
        self.assertEqual(len(failed), 0)

    def test_prefilled_field_skipped_not_failed(self):
        """A field whose current DOM value already matches the answer must be
        counted as filled (skip), NOT recorded as fill_failed."""
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        # querySelector(...).value returns the already-correct value
        page.evaluate.return_value = "John"
        field = {
            "label": "First name", "tag": "INPUT", "type": "text",
            "_sel": "#first_name", "name": "", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.strategies.dispatch.field_deterministic") as fd_mock, \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = "John"
            resolve_mock.return_value.provenance = "profile"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        fd_mock.assert_not_called()
        self.assertEqual(len(filled), 1)
        self.assertEqual(len(failed), 0)

    def test_filled_keys_dedupes_across_calls(self):
        """The caller populates filled_keys from returned filled records;
        a field already present must be skipped (not re-filled, not failed)."""
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        page.evaluate.return_value = ""
        field = {
            "label": "Email", "tag": "INPUT", "type": "email",
            "_sel": "#email", "name": "email", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        filled_keys = set()
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.strategies.dispatch.field_deterministic", return_value=True), \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = "a@b.com"
            resolve_mock.return_value.provenance = "profile"
            filled, _ = _fill_with_playwright(page, [field], {"location": ""}, None,
                                              filled_keys=filled_keys)
            self.assertEqual(len(filled), 1)
            # Caller-side bookkeeping (mirrors cmd_fill)
            for rec in filled:
                filled_keys.add(rec["key"])
            self.assertEqual(len(filled_keys), 1)
            # Second call with the same filled_keys: field already filled
            filled2, failed2 = _fill_with_playwright(page, [field], {"location": ""}, None,
                                                     filled_keys=filled_keys)
        self.assertEqual(len(filled2), 0)
        self.assertEqual(len(failed2), 0)

    def test_no_answer_recorded_not_failed(self):
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        field = {
            "label": "Some optional field", "tag": "INPUT", "type": "text",
            "_sel": "#opt", "name": "", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}):
            resolve_mock.return_value.value = None
            resolve_mock.return_value.provenance = "no_match"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        self.assertEqual(len(filled), 0)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["_why"], "no_answer")


class HasFormExcludesHidden(unittest.TestCase):
    """has_form must not count hidden/submit inputs — otherwise
    no_apply_path detection never fires (every page has CSRF inputs)."""

    def _page_with(self, selector_present):
        page = MagicMock()
        page.query_selector.return_value = selector_present
        return page

    def test_hidden_input_not_a_form(self):
        from apply.common.page_state import has_form
        page = self._page_with(None)
        self.assertFalse(has_form(page))
        page.query_selector.assert_called_once_with(
            'input:not([type=hidden]):not([type=submit]), select, textarea'
        )

    def test_visible_input_is_a_form(self):
        from apply.common.page_state import has_form
        page = self._page_with(MagicMock())
        self.assertTrue(has_form(page))

    def test_has_any_form_without_real_fields(self):
        """A page with only hidden inputs and no widgets/iframes/dialog
        must report no form (allows no_apply_path to trigger)."""
        from apply.common.page_state import has_any_form
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: None
        page.evaluate.side_effect = lambda expr: False
        page.frames = []
        self.assertFalse(has_any_form(page))


class FillAnswersPersisted(unittest.TestCase):
    """cmd_fill must persist the effective answers (incl. --answers
    overrides) into state so `act --check` can verify against them."""

    @staticmethod
    def _chrome_session(state):
        from contextlib import contextmanager

        @contextmanager
        def _cm(state):
            page = MagicMock()
            page.url = "https://example.com/job"
            yield page, MagicMock()
        return _cm(state)

    def _patch_fill(self, saved_states):
        from apply.common.inspector import ProbeResult
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"stage": "tailored"}
        patches = [
            patch("apply.act.fill.load_state",
                  return_value={"jid": "testjid", "external_url": "https://example.com/job"}),
            patch("apply.act.fill.save_state", side_effect=saved_states.append),
            patch("apply.act.fill.get_conn", return_value=conn),
            patch("apply.act.fill._load_profile",
                  return_value={"answers": {"email": "a@b.com"}}),
            patch("apply.act.fill.chrome_session", side_effect=self._chrome_session),
            patch("apply.act.fill._host", return_value=""),
            patch("apply.act.fill._url_fallbacks", return_value=[]),
            patch("apply.act.fill._is_error_page", return_value=False),
            patch("apply.act.helpers._resolve_standalone_form_url", return_value=None),
            patch("apply.act.fill.tag_page"),
            patch("apply.common.registry.resolve", return_value=None),
            patch("apply.act.fill.handle_captcha", return_value=False),
            patch("apply.common.page_state.wait_for_form", return_value=True),
            patch("apply.common.page_state.has_form", return_value=True),
            patch("apply.common.page_state.has_any_form", return_value=True),
            patch("apply.act.fill._handle_login_wall", return_value=""),
            patch("lib.ask_api.available", return_value=False),
            patch("apply.act.fill._probe_form",
                  return_value=ProbeResult(fields=[], strategy="standard")),
            patch("apply.act.fill._scan_capability", return_value=None),
            patch("apply.act.fill._dismiss_popups_if_present"),
            patch("apply.act.fill._find_next_button", return_value=None),
            patch("apply.act.fill._detect_submit_button", return_value=False),
            patch("apply.act.fill._fill_with_playwright", return_value=([], [])),
            patch("apply.act.fill.time.sleep"),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

    def test_fill_answers_saved_with_override(self):
        from apply.act.fill import cmd_fill
        saved = []
        self._patch_fill(saved)
        rc = cmd_fill("testjid", answers={"email": "override@x.com"},
                      verify=False, max_pages=4, quick=True)
        self.assertEqual(rc, 1)  # no fields found (quick, empty probe)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["fill_answers"], {"email": "override@x.com"})

    def test_fill_answers_saved_without_override(self):
        from apply.act.fill import cmd_fill
        saved = []
        self._patch_fill(saved)
        cmd_fill("testjid", answers=None, verify=False, max_pages=4, quick=True)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["fill_answers"], {"email": "a@b.com"})


class CheckUsesFillAnswers(unittest.TestCase):
    """cmd_check must pass the fill-time answers as the resolve override,
    so LLM key-mapped answers are verified instead of invisible."""

    def test_resolve_called_with_override(self):
        from apply.common.inspector import ProbeResult
        from contextlib import contextmanager
        from apply.act.check import cmd_check

        fill_answers = {"email": "override@x.com"}

        @contextmanager
        def _cm(state):
            page = MagicMock()
            page.url = "https://example.com/job"
            yield page, MagicMock()

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"stage": "tailored"}
        field = {
            "label": "Email", "tag": "INPUT", "type": "email",
            "_sel": "#email", "name": "email", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
        }
        with patch("apply.act.check.get_conn", return_value=conn), \
             patch("apply.act.check.load_state",
                   return_value={"jid": "testjid",
                                 "external_url": "https://example.com/job",
                                 "fill_answers": fill_answers}), \
             patch("apply.act.check._load_profile", return_value={"answers": {}}), \
             patch("apply.act.check.resolve_registry", return_value=None), \
             patch("apply.act.check.chrome_session", side_effect=_cm), \
             patch("apply.act.check.tag_page"), \
             patch("apply.act.check._probe_form",
                   return_value=ProbeResult(fields=[field], strategy="standard")), \
             patch("apply.act.check._build_ans_dict", return_value={}), \
             patch("apply.act.check._build_ephemeral", return_value={}), \
             patch("apply.act.check._read_element_value", return_value="override@x.com"), \
             patch("apply.act.check.resolve") as resolve_mock, \
             patch("apply.common.page_helpers.save_state"):
            resolve_mock.return_value.value = "override@x.com"
            rc = cmd_check("testjid")
        self.assertEqual(rc, 0)
        resolve_mock.assert_called_once()
        self.assertEqual(resolve_mock.call_args[0][2], fill_answers)


class NormalizeForCompare(unittest.TestCase):
    """check.py _normalize_for_compare must mirror the filler's
    fill-time normalizations so check doesn't false-warn."""

    def test_phone_formatted_vs_e164(self):
        from apply.act.check import _normalize_for_compare
        self.assertEqual(_normalize_for_compare("Phone Number", "+1 (343) 558-1744"),
                         _normalize_for_compare("Phone", "+13435581744"))

    def test_postal_spaces_stripped(self):
        from apply.act.check import _normalize_for_compare
        self.assertEqual(_normalize_for_compare("Postal Code", "K2P 1J6"), "k2p1j6")

    def test_normal_text_just_lowercased(self):
        from apply.act.check import _normalize_for_compare
        self.assertEqual(_normalize_for_compare("Email", "A@B.com"), "a@b.com")

    def test_contact_label_counts_as_phone(self):
        from apply.act.check import _normalize_for_compare
        self.assertEqual(_normalize_for_compare("Contact Number", "+1 343 558 1744"), "13435581744")


class AutoConsentScoped(unittest.TestCase):
    """JI_AUTO_CONSENT=1 must only auto-check consent-type checkboxes —
    never work-history/sponsorship/location checkboxes."""

    def test_consent_checkbox_auto_checked(self):
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        page.evaluate.return_value = ""
        field = {
            "label": "I agree to the terms and conditions", "tag": "INPUT",
            "type": "checkbox", "_sel": "#agree", "name": "", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.strategies.dispatch.field_deterministic", return_value=True), \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}), \
             patch.dict(os.environ, {"JI_AUTO_CONSENT": "1"}):
            resolve_mock.return_value.value = None
            resolve_mock.return_value.provenance = "no_match"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        self.assertEqual(len(filled), 1)
        self.assertEqual(len(failed), 0)

    def test_non_consent_checkbox_not_auto_checked(self):
        """'Current role' (work-history) must stay unfilled, not auto-true."""
        from apply.act.helpers import _fill_with_playwright
        page = MagicMock()
        field = {
            "label": "Current role", "tag": "INPUT", "type": "checkbox",
            "_sel": "#current_role", "name": "", "id": "",
            "placeholder": "", "autocomplete": "", "role": "",
            "accept": None,
        }
        with patch("apply.act.helpers.load_state", return_value={}), \
             patch("apply.act.helpers.resolve") as resolve_mock, \
             patch("apply.common.resolve._build_ephemeral", return_value={}), \
             patch.dict(os.environ, {"JI_AUTO_CONSENT": "1"}):
            resolve_mock.return_value.value = None
            resolve_mock.return_value.provenance = "no_match"
            filled, failed = _fill_with_playwright(page, [field], {"location": ""}, None)
        self.assertEqual(len(filled), 0)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["_why"], "no_answer")


if __name__ == "__main__":
    unittest.main()
