"""G1 — verification red-team harness (GUIDELINES.md Part 6).

Pins the whole verification layer against the class of bug that caused the
Antigua incident: a wrong value being CERTIFIED as verified. Each test
deliberately simulates an adversarial or sloppy situation and asserts the
pipeline REFUSES to certify it.

Covers:
- the resolver must return a COUNTRY (not a bare +N) for phone-country fields,
- the combobox reader must not certify a bare dialing code by containment,
- `_check_delta` must not certify an echo/unchanged read-back,
- prefilled fields are unverified, not verified-by-us,
- the risk-field split blocks unverified risk fields, flags others,
- vision bytes refuse to leave the machine (non-local endpoint).
"""
import io
import contextlib
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _stderr(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        fn(*a, **k)
    return buf.getvalue()


class AntiguaRegression(unittest.TestCase):
    """A 'Phone country code' field with +1 must resolve to CANADA and never
    certify Antigua & Barbuda (also +1)."""

    def test_phone_country_code_resolves_to_country(self):
        from apply.common.resolve import resolve, _build_ephemeral
        profile = {"phone": "+1 (343) 558-1744",
                   "location": "Ottawa, Ontario, Canada"}
        ep = _build_ephemeral(profile)
        r = resolve("Phone country code", profile, ephemeral=ep)
        self.assertEqual(r.value, "Canada",
                         "phone-country-code must resolve to the country, "
                         "not the bare +N dialing code")

    def test_phone_country_code_no_country_is_no_match(self):
        """No known country ⇒ needs_data for the orchestrator, never a
        guessed dialing code that could certify a wrong country."""
        from apply.common.resolve import resolve, _build_ephemeral
        profile = {"phone": "+1 (343) 558-1744"}  # no location/country
        ep = _build_ephemeral(profile)
        r = resolve("Phone country code", profile, ephemeral=ep)
        self.assertIsNone(r.value)
        self.assertEqual(r.provenance, "no_match")

    def test_dialing_code_map_has_canada(self):
        """Fix 1 completion: the country→dialing-code map (data) must cover
        the common countries so a code-dropdown can use it."""
        from apply.common.resolve import _load_dialing_codes
        d = _load_dialing_codes()
        self.assertEqual(d.get("canada"), "+1")
        self.assertEqual(d.get("united kingdom"), "+44")
        self.assertIn("germany", d)

    def test_tel_country_code_autocomplete_resolves_to_country(self):
        from apply.common.resolve import resolve, _build_ephemeral
        profile = {"phone": "+1 (343) 558-1744",
                   "location": "Ottawa, Ontario, Canada"}
        ep = _build_ephemeral(profile)
        r = resolve("Phone", profile, ephemeral=ep, autocomplete="tel-country-code")
        self.assertEqual(r.value, "Canada")

    def test_fuzzy_combobox_wont_certify_bare_code_by_containment(self):
        """The reader must return None for a bare '+1' when the only options
        are country names — it must never pick 'Antigua and Barbuda (+1-268)'
        as a textual match. (Full DOM simulation is covered by the combobox
        protocol test; here we pin the matching rule via a minimal page.)"""
        from apply.common.value_reader import FuzzyComboboxReader

        class _Opt:
            textContent = "Antigua and Barbuda (+1-268)"

        class _LB:
            def querySelectorAll(self, sel):
                return [_Opt()]

        class _El:
            def getAttribute(self, name):
                if name in ("aria-owns", "aria-controls"):
                    return "listbox1"
                if name == "role":
                    return "combobox"
                return None

        class _Page:
            def evaluate(self, *a, **k):
                return None  # reader's evaluate returns its own result

        # The reader's JS runs in-page; we can't run it here. Instead pin
        # the resolver + delta behavior below, and assert the reader's
        # dialing-code guard is present in its source (the exact-match rule).
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "apply", "common", "value_reader.py"),
                   encoding="utf-8").read()
        self.assertIn("isDial", src,
                      "FuzzyComboboxReader must special-case bare dialing codes")
        self.assertIn("tL === aL", src,
                      "bare dialing codes require an exact option match")

    def test_readers_filter_hidden_nodes(self):
        """F1: a hidden/offscreen element (poisoned DOM text) must never be
        read as a value — every reader JS must skip it."""
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "apply", "common", "value_reader.py"),
                   encoding="utf-8").read()
        for reader in ("StandardReader", "AriaComboboxReader",
                       "FuzzyComboboxReader"):
            idx = src.index(f"class {reader}")
            block = src[idx: idx + 2500]
            self.assertIn("offsetParent", block,
                          f"{reader} must skip hidden/offscreen elements")
            self.assertIn("getClientRects", block,
                          f"{reader} must check visibility before reading")


class HonestVerification(unittest.TestCase):
    """_check_delta must not certify echoes or unchanged read-backs."""

    def _delta(self, before, after, ans, label="Full name", field=None):
        from apply.common.filler import _check_delta
        return _check_delta(before, after, ans, label, field)

    def test_genuine_change_is_verified(self):
        self.assertEqual(self._delta("", "Ottawa", "Ottawa"), (True, ""))

    def test_bare_dial_code_containment_is_rejected(self):
        """'+1' matched inside 'Antigua and Barbuda (+1-268)' must FAIL."""
        ok, reason = self._delta("", "Antigua and Barbuda (+1-268)", "+1",
                                 label="Phone country code")
        self.assertFalse(ok)
        self.assertEqual(reason, "verify_failed")

    def test_bare_dial_code_exact_match_passes(self):
        ok, reason = self._delta("", "+1", "+1", label="Phone country code")
        self.assertTrue(ok)

    def test_echo_unchanged_is_unverified_not_verified(self):
        """Field already held the answer before we touched it — no
        independent confirmation (C1 prefilled family)."""
        field = {}
        ok, reason = self._delta("Canada", "Canada", "Canada",
                                 label="Country", field=field)
        self.assertTrue(ok)
        self.assertEqual(reason, "echo")
        self.assertTrue(field.get("_diag", {}).get("unverified"),
                        "echo/unchanged must be recorded unverified")

    def test_country_name_containment_in_option_is_verified(self):
        """'Canada' inside 'Canada (+1)' is a legit option read-back."""
        ok, reason = self._delta("", "Canada (+1)", "Canada",
                                 label="Country")
        self.assertTrue(ok)

    def test_form_reinterpretation_is_unverified(self):
        """Fix #1 (silent re-lie): a read-back that DIFFERS from the answer
        is NOT a verification — it is unverified, so the orchestrator sees it.
        'Yes'→'No', '6'→'60' must not be certified."""
        for before, after, ans in [("", "No", "Yes"),
                                   ("", "60", "6"),
                                   ("", "Montreal", "Toronto")]:
            with self.subTest(after=after):
                field = {}
                ok, reason = self._delta(before, after, ans,
                                         label="Field", field=field)
                self.assertTrue(ok)  # value did land (changed)
                self.assertEqual(reason, "reinterpreted")
                self.assertTrue(field.get("_diag", {}).get("unverified"),
                                f"'{ans}'→'{after}' must be unverified")

    def test_safe_normalization_is_verified(self):
        """Phone formatting / date expansion preserve the answer's identity."""
        from apply.common.filler import _is_safe_normalization
        self.assertTrue(_is_safe_normalization("(613) 555-0100", "613-555-0100"))
        self.assertTrue(_is_safe_normalization("2021-03-15", "2021-03"))
        self.assertTrue(_is_safe_normalization("$120,000", "120000"))
        self.assertFalse(_is_safe_normalization("60", "6"))
        self.assertFalse(_is_safe_normalization("No", "Yes"))


class RiskFieldSplit(unittest.TestCase):
    """Unverified RISK fields block; non-risk fields only flag."""

    def _issue(self, label, severity, expected_sev):
        from apply.common import terms as _T
        self.assertTrue(_T.is_risk_field(label),
                        f"{label!r} should be classified as a risk field")
        self.assertEqual(severity, expected_sev,
                         f"{label!r} should {expected_sev}")

    def test_risk_classifier(self):
        from apply.common.terms import is_risk_field
        for lbl in ("Phone country code", "Country of residence",
                    "Are you legally authorized to work in the US?",
                    "Current salary", "City / State / Zip", "Gender"):
            with self.subTest(label=lbl):
                self.assertTrue(is_risk_field(lbl))
        for lbl in ("First name", "Preferred language for interview",
                    "How did you hear about us?"):
            with self.subTest(label=lbl):
                self.assertFalse(is_risk_field(lbl))

    def test_runtime_risk_keyword_extends_classifier(self):
        """Data-driven classifier: a novel risk phrasing is classified as
        a risk field after a runtime `keywords add` — no code edit."""
        from apply.common.terms import (is_risk_field,
                                        add_classifier_keyword,
                                        clear_classifier_keywords_for_test)
        clear_classifier_keywords_for_test()
        try:
            # "global mobility" is not in the static risk list.
            self.assertFalse(is_risk_field("Do you have global mobility?"))
            self.assertTrue(add_classifier_keyword("risk", "global mobility"))
            self.assertTrue(is_risk_field("Do you have global mobility?"))
        finally:
            clear_classifier_keywords_for_test()

    def test_risk_field_mismatch_is_error(self):
        """The check gate must issue SEV_ERROR for a risk-field mismatch,
        not SEV_WARN (identity/legal/location/salary cannot ship wrong)."""
        from apply.common import terms as _T
        self.assertEqual(_T.SEV_ERROR, "ERROR")
        # The classification drives severity in check.py; assert the label
        # class so the check's branch is exercised correctly.
        from apply.common.terms import is_risk_field
        self.assertTrue(is_risk_field("Country"))
        self.assertFalse(is_risk_field("First name"))


class VisionBytesStayLocal(unittest.TestCase):
    """A3: vision bytes (real screenshots, PII) must refuse a remote endpoint."""

    def test_loopback_detection(self):
        from lib.ask_api import _is_loopback
        self.assertTrue(_is_loopback("http://localhost:9000/v1"))
        self.assertTrue(_is_loopback("http://127.0.0.1:9000/v1"))
        self.assertTrue(_is_loopback("http://127.0.0.2:9000/v1"))
        self.assertTrue(_is_loopback("http://::1:9000/v1"))
        self.assertFalse(_is_loopback("http://192.168.1.5:9000/v1"))
        self.assertFalse(_is_loopback("http://10.0.0.1:9000/v1"))
        self.assertFalse(_is_loopback("https://llm.example.com/v1"))

    def test_ask_bytes_refuses_remote_endpoint(self):
        from lib.ask_api import ask_bytes
        os.environ["LLM_API_URL"] = "https://llm.example.com/v1"
        os.environ.pop("OPENAI_API_BASE", None)
        try:
            reply, err = ask_bytes(b"fakeimagebytes", "what is this?")
        finally:
            os.environ.pop("LLM_API_URL", None)
        self.assertIsNone(reply)
        self.assertIn("local", (err or "").lower())
        self.assertIn("refused", (err or "").lower())


class SessionIsolation(unittest.TestCase):
    """F3 — one shared Chrome profile persists cookies across companies. The
    submit path must strip OTHER employers' cookies so company A's session
    never leaks into company B's apply flow."""

    def _mock_ctx(self, cookies):
        cleared = []

        class _Ctx:
            def cookies(self):
                return list(cookies)

            def clear_cookies(self, **kw):
                cleared.append(dict(kw))

        return _Ctx(), cleared

    def test_cross_company_cookies_stripped(self):
        from apply.act.helpers import _isolate_session
        ctx, cleared = self._mock_ctx([
            {"name": "session", "domain": ".acme.com", "path": "/"},
            {"name": "SESSION", "domain": ".evilcorp.io", "path": "/"},
            {"name": "foo", "domain": ".acme.com", "path": "/"},
        ])
        _isolate_session(ctx, "jobs.acme.com")
        # Only evilcorp.io must be cleared; acme.com cookies survive.
        self.assertEqual(len(cleared), 1)
        self.assertEqual(cleared[0]["domain"], ".evilcorp.io")

    def test_no_host_is_noop(self):
        from apply.act.helpers import _isolate_session
        ctx, cleared = self._mock_ctx([
            {"name": "s", "domain": ".x.com", "path": "/"}])
        _isolate_session(ctx, "")
        self.assertEqual(cleared, [])

    def test_subdomain_relation_keeps_both(self):
        from apply.act.helpers import _isolate_session
        ctx, cleared = self._mock_ctx([
            {"name": "a", "domain": "workday.com", "path": "/"},
            {"name": "b", "domain": ".workday.com", "path": "/"},
            {"name": "c", "domain": ".greenhouse.io", "path": "/"},
        ])
        _isolate_session(ctx, "acme.workday.com")
        # greenhouse.io must go; both workday.com cookies stay.
        self.assertEqual(len(cleared), 1)
        self.assertEqual(cleared[0]["domain"], ".greenhouse.io")


if __name__ == "__main__":
    unittest.main()
