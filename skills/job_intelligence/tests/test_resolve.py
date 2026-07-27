"""Unit tests for the answer-resolution chain (apply/common/resolve.py).

Covers the two live steps: --answers override (exact + truncation prefix) and
profile ephemeral exact match (facts + derivations + static answers map).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common.resolve import normalize, resolve, _build_ephemeral


PROFILE = {
    "first_name": "Bilal",
    "last_name": "M",
    "email": "b@x.com",
    "phone": "613-555-0100",
    "location": "Ottawa, ON, Canada",
    "linkedin_url": "https://linkedin.com/in/bilal",
    "answers": {
        "authorized to work in canada": "Yes",
        "disability_status": "I do not have a disability",
    },
}


class Normalize(unittest.TestCase):
    def test_lowercases_and_collapses_punctuation(self):
        self.assertEqual(normalize("  Full  Name?? "), "full name")

    def test_keeps_plus_and_hash(self):
        self.assertEqual(normalize("C++ / C#"), "c++ c#")

    def test_empty(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(None), "")


class Ephemeral(unittest.TestCase):
    def test_derives_full_name_and_location_parts(self):
        e = _build_ephemeral(PROFILE)
        self.assertEqual(e["full_name"][0], "Bilal M")
        self.assertEqual(e["city"][0], "Ottawa")
        self.assertEqual(e["state_province"][0], "ON")
        self.assertEqual(e["country"][0], "Canada")

    def test_static_answers_included(self):
        e = _build_ephemeral(PROFILE)
        self.assertEqual(e["authorized to work in canada"][0], "Yes")


class ProfileFactMatch(unittest.TestCase):
    def _val(self, label, override=None):
        return resolve(label, PROFILE, answers_override=override or {}).value

    def test_email(self):
        self.assertEqual(self._val("Email"), "b@x.com")

    def test_full_name(self):
        self.assertEqual(self._val("Full name"), "Bilal M")

    def test_city_country_from_location(self):
        self.assertEqual(self._val("City"), "Ottawa")
        self.assertEqual(self._val("Country"), "Canada")

    def test_static_answer_exact_label(self):
        # The static-answer key must match the normalized label exactly.
        self.assertEqual(self._val("Authorized to work in Canada"), "Yes")

    def test_long_ats_question_matches_static_answer(self):
        # Previously finding #5: long ATS question text did NOT match the short
        # static key. The improved resolver now matches it via alias/fuzzy
        # channels — this is the desired behavior (less NEEDS_ANSWER escalation).
        self.assertEqual(self._val("Are you legally authorized to work in Canada?"), "Yes")

    def test_empty_label(self):
        self.assertIsNone(self._val(""))

    def test_expanded_string_fact_key(self):
        # Phase 2 widened the resolvable set to string-valued facts.
        prof = dict(PROFILE, expected_salary=95000)  # numeric on purpose
        r = resolve("Expected salary", prof)
        self.assertEqual(r.value, "95000")  # coerced to str

    def test_explicit_city_wins_over_derived(self):
        prof = dict(PROFILE, city="Kanata")  # location says Ottawa
        self.assertEqual(resolve("City", prof).value, "Kanata")


class AnswersOverride(unittest.TestCase):
    def _res(self, label, override):
        return resolve(label, PROFILE, answers_override=override)

    def test_exact_override_wins(self):
        r = self._res("Expected salary", {"expected salary": "95000"})
        self.assertEqual(r.value, "95000")
        self.assertEqual(r.provenance, "user_typed")

    def test_truncation_prefix_match(self):
        # field_reader truncates labels to 60 chars; a >=10-char key that is a
        # prefix of the (longer) label still matches.
        label = "Cover letter - describe why you are a great fit for this role"
        r = self._res(label, {"cover letter": "see attached"})
        self.assertEqual(r.value, "see attached")

    def test_short_override_key_does_not_loosely_prefix_match(self):
        # Keys under 10 chars must not prefix-match (avoids "a"/"id" false hits).
        r = self._res("Country", {"co": "WRONG"})
        self.assertNotEqual(r.value, "WRONG")


class AttrMatchGuard(unittest.TestCase):
    """Step 1.6 attribute matching must not fire for radio/select fields.

    EEOC questions (e.g. 'Are you Hispanic/Latino?') often have name/id
    attributes like 'custom_question_location' that incidentally contain
    _ATTR_MAP keys. Without the guard, resolve returns the user's location
    instead of no_match.
    """

    def test_radio_skips_attr_match(self):
        r = resolve("Are you Hispanic/Latino?", PROFILE,
                    field_name="custom_question_location", field_tag="RADIO_GROUP")
        self.assertIsNone(r.value)

    def test_select_skips_attr_match(self):
        r = resolve("Are you Hispanic/Latino?", PROFILE,
                    field_id="eeoc_location", field_tag="SELECT")
        self.assertIsNone(r.value)

    def test_dropdown_skips_attr_match(self):
        r = resolve("What is your country of birth?", PROFILE,
                    field_name="custom_question_country", field_tag="DROPDOWN")
        self.assertIsNone(r.value)

    def test_combobox_role_skips_attr_match(self):
        r = resolve("What is your country of birth?", PROFILE,
                    field_name="candidate_country", field_role="combobox")
        self.assertIsNone(r.value)

    def test_text_input_still_uses_attr_match(self):
        r = resolve("Are you Hispanic/Latino?", PROFILE,
                    field_name="custom_question_location", field_tag="INPUT")
        self.assertIsNotNone(r.value)

    def test_disability_excludes_accommodation(self):
        r = resolve("Will you require disability accommodations?", PROFILE)
        self.assertIsNone(r.value)

    def test_disability_status_still_matches(self):
        r = resolve("Do you identify as having a disability?", PROFILE)
        self.assertIsNotNone(r.value)

    def test_veteran_excludes_spouse(self):
        r = resolve("Are you a spouse of a veteran?", PROFILE)
        self.assertIsNone(r.value)

    def test_veteran_status_still_matches(self):
        prof = dict(PROFILE, answers={**PROFILE["answers"], "veteran_status": "I am not a protected veteran"})
        r = resolve("Are you a protected veteran?", prof)
        self.assertIsNotNone(r.value)


if __name__ == "__main__":
    unittest.main()
