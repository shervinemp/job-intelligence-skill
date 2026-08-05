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


class RuntimeAliasRules(unittest.TestCase):
    """The wired meta-flow loop (META_FLOW.md Loop 4): a repeated no_match
    label can be resolved at RUNTIME via report.py rules add — no source
    edit. Invalid rules are refused; the store is scoped and clearable."""

    def setUp(self):
        from apply.common.resolve import clear_alias_rules
        clear_alias_rules()

    def tearDown(self):
        from apply.common.resolve import clear_alias_rules
        clear_alias_rules()

    def test_novel_label_resolves_after_runtime_rule(self):
        from apply.common.resolve import (add_alias_rule, resolve,
                                          _build_ephemeral)
        profile = {"answers": {"preferred_pronouns_profile": "She/Her"},
                   "location": "Ottawa, ON, Canada"}
        ep = _build_ephemeral(profile)
        r = resolve("Which pronouns should we use for you?", profile,
                    ephemeral=ep)
        self.assertIsNone(r.value)  # no_match before the rule
        self.assertTrue(add_alias_rule(
            r"\bwhich pronouns should we use\b",
            ["preferred_pronouns_profile"]))
        r2 = resolve("Which pronouns should we use for you?", profile,
                     ephemeral=ep)
        self.assertEqual(r2.value, "She/Her")
        self.assertEqual(r2.provenance, "alias")

    def test_invalid_regex_refused(self):
        from apply.common.resolve import add_alias_rule, list_alias_rules
        self.assertFalse(add_alias_rule("(broken", ["x"]))
        self.assertFalse(add_alias_rule("", ["x"]))
        self.assertFalse(add_alias_rule(r"\bvalid\b", []))
        self.assertEqual(list_alias_rules(), [])

    def test_dedupe_and_clear(self):
        from apply.common.resolve import (add_alias_rule, list_alias_rules,
                                          clear_alias_rules)
        self.assertTrue(add_alias_rule(r"\bfoo bar\b", ["k1"]))
        self.assertTrue(add_alias_rule(r"\bfoo bar\b", ["k2"]))  # dedupe
        rules = list_alias_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0][1], ["k2"])
        clear_alias_rules()
        self.assertEqual(list_alias_rules(), [])

    def test_runtime_rule_outranks_static_default(self):
        """A runtime alias must beat a conservative default (Step 6)."""
        from apply.common.resolve import (add_alias_rule, resolve,
                                          _build_ephemeral)
        profile = {"answers": {"consent_marketing": "Yes"},
                   "location": "Ottawa, ON, Canada"}
        ep = _build_ephemeral(profile)
        # Static default would answer "No" for marketing; the runtime rule
        # must win when the profile actually has an answer.
        self.assertTrue(add_alias_rule(
            r"\bstay up to date\b.*\bmarketing\b", ["consent_marketing"]))
        r = resolve("Stay up to date on marketing?", profile, ephemeral=ep)
        self.assertEqual(r.value, "Yes")


class S2AutoPromotionGate(unittest.TestCase):
    """GUIDELINES.md S2: a learned mapping must reach `active` (≥2 consistent
    confirms) before it can promote to a RUNTIME alias rule. A single answer
    or a conflicting one must never create a global rule."""

    def setUp(self):
        from apply.common.resolve import clear_alias_rules, clear_learned_for_test
        clear_alias_rules()
        try:
            clear_learned_for_test()
        except AttributeError:
            pass

    def tearDown(self):
        from apply.common.resolve import clear_alias_rules
        clear_alias_rules()

    def _mapping(self):
        from apply.common.resolve import learn_mapping
        learn_mapping("How did you hear about this role?", "LinkedIn",
                      domain="job-boards.greenhouse.io")
        learn_mapping("How did you hear about this role?", "LinkedIn",
                      domain="boards.greenhouse.io")

    def test_single_confirm_blocks_promotion(self):
        from apply.common.resolve import (learn_mapping,
                                          promote_learned_to_rule,
                                          list_alias_rules)
        learn_mapping("Preferred language", "English")
        status, detail = promote_learned_to_rule("Preferred language")
        self.assertEqual(status, "not_active")
        self.assertEqual(list_alias_rules(), [])

    def test_two_confirms_promote(self):
        """Two consistent confirms reach active → promotion is allowed."""
        self._mapping()
        # The learned value must map to a profile answer key to promote.
        # Without a real profile here, promotion reports conflict — which is
        # correct: the gate is PASSED but the rule can't point at a missing key.
        from apply.common.resolve import promote_learned_to_rule
        status, detail = promote_learned_to_rule(
            "How did you hear about this role?", domain="")
        self.assertEqual(status, "promoted")

    def test_conflicting_answers_never_promote(self):
        from apply.common.resolve import (learn_mapping,
                                          promote_learned_to_rule,
                                          list_alias_rules)
        learn_mapping("Preferred language", "English")
        learn_mapping("Preferred language", "French")  # conflict → reset to 1
        status, detail = promote_learned_to_rule("Preferred language")
        self.assertEqual(status, "not_active")
        self.assertEqual(list_alias_rules(), [])

    def test_short_label_never_promotes(self):
        from apply.common.resolve import (learn_mapping,
                                          promote_learned_to_rule,
                                          list_alias_rules)
        # A single-confirm short label: the S2 gate blocks promotion (it is
        # not active), and even under --force a too-short label yields no
        # pattern. Either way, no global rule is created.
        learn_mapping("A", "v")
        status, detail = promote_learned_to_rule("A")
        self.assertIn(status, ("not_active", "no_pattern"))
        self.assertEqual(list_alias_rules(), [])
        status2, detail2 = promote_learned_to_rule("A", force=True)
        self.assertEqual(status2, "no_pattern")
        self.assertEqual(list_alias_rules(), [])

    def test_expired_rules_are_dropped(self):
        """A runtime rule with a stale last_seen must be reaped (rule TTL)."""
        from apply.common.resolve import (add_alias_rule, _load_runtime_rules,
                                          _save_runtime_rules, _alias_rules_all,
                                          _RULE_TTL_DAYS)
        self.assertTrue(add_alias_rule(r"\bexpired rule pattern\b", ["k1"]))
        # Age the rule past the TTL.
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(days=_RULE_TTL_DAYS + 1)
               ).strftime("%Y-%m-%dT%H:%M:%S")
        rules = _load_runtime_rules()
        for r in rules:
            r[2] = old
        _save_runtime_rules(rules)
        # _alias_rules_all reaps expired rules lazily.
        _alias_rules_all()
        from apply.common.resolve import _load_runtime_rules as _reload
        self.assertEqual(_reload(), [])

    def test_domain_scoped_rule_does_not_fire_elsewhere(self):
        """A4: a runtime rule learned for one host must not fire on another."""
        from apply.common.resolve import (add_alias_rule, resolve,
                                          _build_ephemeral, clear_alias_rules)
        clear_alias_rules()
        try:
            self.assertTrue(add_alias_rule(
                r"\bwhich office location\b", ["location"],
                domain="boards.greenhouse.io"))
            prof = {"location": "Ottawa, Ontario, Canada"}
            ep = _build_ephemeral(prof)
            # On the scoped host, the rule fires.
            r_yes = resolve("Which office location do you prefer?",
                            prof, ephemeral=ep, domain="boards.greenhouse.io")
            self.assertEqual(r_yes.value, "Ottawa, Ontario, Canada")
            # On a DIFFERENT host, the rule must NOT fire (no_match).
            r_no = resolve("Which office location do you prefer?",
                           prof, ephemeral=ep, domain="workday.com")
            self.assertIsNone(r_no.value)
        finally:
            clear_alias_rules()

    def test_global_rule_fires_everywhere(self):
        """An empty-domain rule is global (intended for universal phrasings)."""
        from apply.common.resolve import (add_alias_rule, resolve,
                                          _build_ephemeral, clear_alias_rules)
        clear_alias_rules()
        try:
            self.assertTrue(add_alias_rule(r"\bwhich office location\b",
                                           ["location"]))  # no domain
            prof = {"location": "Ottawa, Ontario, Canada"}
            ep = _build_ephemeral(prof)
            for host in ("a.com", "b.io", ""):
                r = resolve("Which office location do you prefer?", prof,
                            ephemeral=ep, domain=host)
                self.assertEqual(r.value, "Ottawa, Ontario, Canada",
                                 f"global rule failed on host {host!r}")
        finally:
            clear_alias_rules()


    def test_postal_code_not_used_as_country(self):
        """A1: 'Toronto, ON, M5V 2T6' must not derive country='M5V 2T6'."""
        from apply.common.resolve import _build_ephemeral, resolve
        prof = {"location": "Toronto, ON, M5V 2T6"}
        ep = _build_ephemeral(prof)
        c = ep.get("country")
        self.assertTrue(c is None or not str(c[0]).startswith("M5V"),
                        f"postal code leaked as country: {c!r}")
        r = resolve("Country", prof, ephemeral=ep)
        self.assertNotIn("M5V", str(r.value or ""))

    def test_postal_suffix_stripped_from_country(self):
        """A1: 'Quebec City, QC, Canada G1R 5J4' → country 'Canada'."""
        from apply.common.resolve import _build_ephemeral
        ep = _build_ephemeral({"location": "Quebec City, QC, Canada G1R 5J4"})
        c = ep.get("country")
        self.assertEqual(str(c[0]), "Canada")

    def test_preferred_name_text_field_not_filled_with_no(self):
        """A2: 'Preferred name' is a TEXT field — must resolve to the name,
        never the consent default 'No'."""
        from apply.common.resolve import resolve, _build_ephemeral
        prof = {"first_name": "Shervin", "location": "Ottawa, Ontario, Canada"}
        ep = _build_ephemeral(prof)
        r = resolve("Preferred name", prof, ephemeral=ep, field_type="text")
        self.assertEqual(r.value, "Shervin")

    def test_no_answer_values_hardcoded_in_resolver(self):
        """ETHOS: answer values live in data (default_answers.json / profile),
        never in resolve.py source. The default mechanism must LOAD its values
        from the data file, not hold them as source literals."""
        from apply.common.resolve import _load_default_answers
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "apply", "common", "resolve.py"),
                   encoding="utf-8").read()
        # The old hardcoded default list shape must be gone (pattern, "No").
        self.assertNotIn("_DEFAULT_ANSWERS = [", src)
        # The default VALUES must come from the data file.
        defaults = _load_default_answers()
        self.assertTrue(defaults, "default_answers.json must load")
        self.assertTrue(any(v == "No" for _, v, _, _ in defaults))


class C3Harmonization(unittest.TestCase):
    """Profile answer harmonization: duplicate keys with the same value and
    subset meanings must resolve under either spelling (drift risk gone)."""

    def test_duplicate_keys_find_same_answer(self):
        from lib.quality import harmonize_answers
        from apply.common.resolve import resolve, _build_ephemeral
        prof = {"answers": {"gender": "Male", "Gender Identity": "Male"}}
        groups = harmonize_answers(prof)
        self.assertTrue(any(g["meaning"] == "gender" for g in groups))
        ep = _build_ephemeral(prof)
        r = resolve("gender", prof, ephemeral=ep)
        self.assertEqual(r.value, "Male")
        r2 = resolve("Gender Identity", prof, ephemeral=ep)
        self.assertEqual(r2.value, "Male")

    def test_no_false_grouping(self):
        from lib.quality import harmonize_answers
        prof = {"answers": {"gender": "Male", "Gender Identity": "Female"}}
        groups = harmonize_answers(prof)
        # Different values must NOT group.
        self.assertFalse(any(g["meaning"] == "gender" for g in groups))


class C2ProfileContradictions(unittest.TestCase):
    """Profile-level contradiction detection (coherence checks form values;
    this checks the profile itself)."""

    def test_conflicting_relocation(self):
        from lib.quality import check_profile_contradictions
        cons = check_profile_contradictions(
            {"answers": {"willing_to_relocate": "Yes", "relocat": "No"}})
        self.assertTrue(any("relocation" in c for c in cons))

    def test_years_vs_history(self):
        from lib.quality import check_profile_contradictions
        cons = check_profile_contradictions(
            {"answers": {"years_of_experience": "10"},
             "work_history": [{"company": "A", "startDate": "2021-01",
                               "endDate": "2023-01"}]})
        self.assertTrue(any("years_of_experience" in c for c in cons))

    def test_consistent_profile_no_contradictions(self):
        from lib.quality import check_profile_contradictions
        cons = check_profile_contradictions(
            {"answers": {"willing_to_relocate": "Yes"}, "work_history": []})
        self.assertEqual(cons, [])


if __name__ == "__main__":
    unittest.main()
