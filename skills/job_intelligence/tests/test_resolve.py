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
    "first_name": "John",
    "last_name": "Smith",
    "email": "john.smith@example.com",
    "phone": "613-555-0100",
    "location": "Ottawa, ON, Canada",
    "linkedin_url": "https://linkedin.com/in/johnsmith",
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
        self.assertEqual(e["full_name"][0], "John Smith")
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
        self.assertEqual(self._val("Email"), "john.smith@example.com")

    def test_full_name(self):
        self.assertEqual(self._val("Full name"), "John Smith")

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
        self.assertEqual(r.provenance, "answers_override")

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

    def test_text_input_weak_attr_requires_label_corroboration(self):
        # EEOC 'other' free-text with a location-ish name must NOT be
        # filled (the Scribd race-field false positive).
        r = resolve("Are you Hispanic/Latino?", PROFILE,
                    field_name="custom_question_location", field_tag="INPUT")
        self.assertIsNone(r.value)
        # With a corroborating label, weak attr tokens still work.
        r2 = resolve("Enter your city", PROFILE,
                     field_name="custom_question_location", field_tag="INPUT")
        self.assertIsNotNone(r2.value)

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

    def test_stopword_key_does_not_over_fire(self):
        prof = dict(PROFILE, answers={**PROFILE["answers"], "have_you_ever_been": "No"})
        r = resolve("Have you ever been convicted of a felony?", prof)
        self.assertIsNone(r.value)


class Step3WordCountGuard(unittest.TestCase):
    """Step 3 requires ≥2 content words to prevent single-content-word keys
    like how_did_you_hear (content={hear}) from matching generic labels."""

    def _val(self, label, extra_answers=None):
        prof = dict(PROFILE)
        if extra_answers:
            prof["answers"] = {**PROFILE.get("answers", {}), **extra_answers}
        return resolve(label, prof).value

    def test_two_content_words_still_matches(self):
        prof = dict(PROFILE, answers={**PROFILE["answers"], "willing_to_relocate": "Yes"})
        r = resolve("Are you willing to relocate?", prof)
        self.assertEqual(r.value, "Yes")

    def test_single_content_word_does_not_match(self):
        prof = dict(PROFILE, answers={**PROFILE["answers"], "how_did_you_hear": "LinkedIn"})
        r = resolve("Heard about us via", prof)
        self.assertIsNone(r.value)

    def test_single_content_word_falls_back_to_alias(self):
        prof = dict(PROFILE, answers={**PROFILE["answers"], "how_did_you_hear": "LinkedIn"})
        # "How did you hear about us?" has "how" as content word + alias pattern
        r = resolve("How did you hear about us?", prof)
        self.assertEqual(r.value, "LinkedIn")


class Step3bWordLimit(unittest.TestCase):
    """Step 3b suffix-stripped match only fires on labels ≤6 words to avoid
    matching entity names that appear inside long option-list text."""

    def _val(self, label):
        return resolve(label, PROFILE).value

    def test_short_label_matches_linkedin(self):
        self.assertEqual(self._val("LinkedIn"), "https://linkedin.com/in/johnsmith")

    def test_six_word_label_still_matches(self):
        self.assertEqual(
            self._val("Please enter your LinkedIn profile URL"),
            "https://linkedin.com/in/johnsmith",
        )

    def test_seven_word_label_does_not_match_via_suffix(self):
        # "How did you hear about us? LinkedIn" = 7 words → no match via Step 3b
        # Falls through to step 5 alias, which doesn't match, so None.
        r = resolve("How did you hear about us? LinkedIn", PROFILE)
        self.assertIsNone(r.value)

    def test_four_word_label_matches_github(self):
        prof = dict(PROFILE, github_url="https://github.com/johnsmith")
        self.assertEqual(
            resolve("GitHub profile URL", prof).value,
            "https://github.com/johnsmith",
        )

    def test_website_in_short_label_matches(self):
        prof = dict(PROFILE, website="https://john.dev")
        self.assertEqual(
            resolve("Portfolio", prof).value,
            "https://john.dev",
        )

    def test_portfolio_in_short_label_matches(self):
        prof = dict(PROFILE, portfolio_url="https://john.dev")
        self.assertEqual(
            resolve("Website", prof).value,
            "https://john.dev",
        )


class DatePartDerivation(unittest.TestCase):
    """'Start date month/year' fields (Greenhouse work-history) must derive
    the current month/year instead of stuffing 'Immediately' into a picker."""

    def _prof(self):
        return dict(PROFILE, answers={
            **PROFILE["answers"],
            "available_start": "Immediately",
            "start_date": "Immediately",
        })

    def test_start_date_month_derives_current_month(self):
        from datetime import datetime as _dt
        r = resolve("Start date month*", self._prof())
        self.assertEqual(r.value, _dt.now().strftime("%B"))
        self.assertEqual(r.provenance, "derived")

    def test_start_date_year_derives_current_year(self):
        from datetime import datetime as _dt
        r = resolve("Start date year*", self._prof())
        self.assertEqual(r.value, str(_dt.now().year))

    def test_month_you_can_start_derives(self):
        from datetime import datetime as _dt
        r = resolve("Month you can start", self._prof())
        self.assertEqual(r.value, _dt.now().strftime("%B"))

    def test_when_can_you_start_still_uses_answer(self):
        r = resolve("When can you start?", self._prof())
        self.assertEqual(r.value, "Immediately")

    def test_end_date_parts_not_derived(self):
        r = resolve("End date month*", self._prof())
        self.assertIsNone(r.value)


class AttrWeakTokenGuard(unittest.TestCase):
    """EEOC-free-text guard: a 'location'-named OTHER field must not be
    filled with the user's city (the Scribd race-field false positive)."""

    def test_other_race_field_not_filled_with_city(self):
        r = resolve("Other race, ethnicity, or origin", PROFILE,
                    None, field_name="location_other")
        self.assertIsNone(r.value)

    def test_location_field_still_fills_from_name(self):
        r = resolve("Location (City)*", PROFILE, None, field_name="location")
        self.assertEqual(r.value, "Ottawa, ON, Canada")

    def test_email_strong_token_ignores_label(self):
        r = resolve("Contact details", PROFILE, None, field_name="email")
        self.assertEqual(r.value, "john.smith@example.com")


if __name__ == "__main__":
    unittest.main()
