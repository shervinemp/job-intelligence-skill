"""resolve.py — Label→value resolution for form auto-fill.

No answer values are hardcoded here. Facts and derivations come from profile.json;
per-run overrides come from the --answers dict. Resolution is deterministic:

  1. --answers override   exact normalized-label match, or prefix match for
                          field_reader's 60-char label truncation
  2. profile ephemeral    profile facts + name/location derivations + the
                          profile["answers"] static map, exact key match

Anything unresolved returns no_match and is surfaced to the caller as an unfilled
field; the LLM then supplies it via --answers.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from lib.config import STATE_DIR


# ─── Resolution result ───────────────────────────────────────────────

class Resolution:
    __slots__ = ("value", "key", "label", "provenance", "ephemeral_only")
    def __init__(self, value, key, label, provenance, ephemeral_only=False):
        self.value = value
        self.key = key
        self.label = label
        self.provenance = provenance
        self.ephemeral_only = ephemeral_only


# ─── Normalization ───────────────────────────────────────────────────

def normalize(label: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", (label or "").lower()).strip()


# ─── Ephemeral answer builder ───────────────────────────────────────

# String-valued profile facts resolved by exact (normalized) label match.
# Boolean/parameterized facts (authorized_to_work, requires_sponsorship) need
# yes/no + country transforms — those use the static answers map below.
_PROFILE_KEYS = {
    "first_name", "last_name", "email", "phone",
    "linkedin_url", "github_url", "portfolio_url", "website",
    "address", "city", "state", "zip", "country",
    "visa_status", "expected_salary", "salary_currency",
    "work_preference", "remote_preference", "start_date", "pronouns",
    "resume_path", "location",
}


def _build_ephemeral(profile: dict) -> dict:
    ephemeral = {}

    for k in _PROFILE_KEYS:
        v = profile.get(k)
        if v:
            ephemeral[k] = (str(v), "profile")

    fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
    if fn and ln:
        ephemeral["full_name"] = (f"{fn} {ln}", "derived")
    elif fn or ln:
        ephemeral["full_name"] = (fn or ln, "derived")

    # Derive location parts from "location" only if not given as explicit keys
    # (explicit profile.city/country/state win over the derivation).
    loc = profile.get("location", "")
    if loc:
        ephemeral.setdefault("address", (loc, "derived"))
    if loc and "," in loc:
        parts = [p.strip() for p in loc.split(",")]
        if len(parts) >= 1 and parts[0]:
            ephemeral.setdefault("city", (parts[0], "derived"))
        if len(parts) >= 2 and parts[1]:
            ephemeral.setdefault("state_province", (parts[1], "derived"))
            ephemeral.setdefault("state", (parts[1], "derived"))
            ephemeral.setdefault("province", (parts[1], "derived"))
        if len(parts) >= 3 and parts[-1]:
            country = _strip_postal(parts[-1])
            if country:
                ephemeral.setdefault("country", (country, "derived"))

    answers = profile.get("answers", {})
    if isinstance(answers, dict):
        for k, v in answers.items():
            if v:
                ephemeral[k] = (str(v) if not isinstance(v, list) else [str(x) for x in v], "static")
        # C3: harmonize duplicate answer keys (gender == Gender Identity,
        # authorized_to_work == work_authorization) so either spelling finds
        # the same value — a latent drift risk collapses.
        try:
            from lib.quality import alias_harmonized_answers
            for dup_key, canonical in alias_harmonized_answers(profile).items():
                if dup_key not in ephemeral and canonical in ephemeral:
                    ephemeral[dup_key] = ephemeral[canonical]
        except Exception:
            pass

    # Aliases: forms ask "Website" when the profile has portfolio_url (and vice versa)
    if ephemeral.get("portfolio_url") and "website" not in ephemeral:
        ephemeral["website"] = ephemeral["portfolio_url"]
    if ephemeral.get("website") and "portfolio_url" not in ephemeral:
        ephemeral["portfolio_url"] = ephemeral["website"]

    return ephemeral


_POSTAL_RE = re.compile(
    # Canadian postal: A1A 1A1 · US ZIP: 12345 / 12345-6789
    r"(?:\b[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d\b|\b\d{5}(?:-\d{4})?\b)"
)


def _strip_postal(s):
    """Remove a trailing postal code from a location segment so a ZIP/ZIP-like
    suffix is never mistaken for a country (A1: 'Toronto, ON, M5V 2T6' must
    not yield country='M5V 2T6'). Keeps the country part when it is clean."""
    s = (s or "").strip()
    # A lone postal code is not a country.
    if re.fullmatch(_POSTAL_RE.pattern, s):
        return ""
    # Strip a trailing postal code (possibly following a space or comma).
    cleaned = _POSTAL_RE.sub("", s).strip(" ,")
    return cleaned or ""


def _find_ephemeral_value(key: str, ephemeral: dict) -> Optional[str]:
    entry = ephemeral.get(key)
    return entry[0] if entry else None


# ─── Autofill-token map (WHATWG autocomplete attribute) ───────────────
# The same free semantic layer browser autofill and chrome auto-apply
# extensions use. Checked before label heuristics.

_AUTOCOMPLETE_MAP = {
    "given-name": "first_name",
    "family-name": "last_name",
    "name": "full_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
    "tel-country-code": "phone",
    "country": "country",
    "country-name": "country",
    "address-level1": "state",
    "address-level2": "city",
    "address-level3": "city",
    "postal-code": "zip",
    "street-address": "address",
    "url": "website",
}

_ATTR_MAP = {
    "first_name": "first_name", "firstname": "first_name",
    "last_name": "last_name", "lastname": "last_name", "surname": "last_name",
    "email": "email", "phone": "phone", "tel": "phone",
    "country": "country", "city": "city", "state": "state",
    "location": "location", "address": "address", "zip": "zip",
    "postalcode": "zip", "postal_code": "zip",
    "linkedin": "linkedin_url", "github": "github_url",
    "website": "website", "portfolio": "portfolio_url",
    "resume": "resume_path",
}

# Strong tokens: safe to trust from the name/id alone (their labels are
# near-universally predictable). Everything else is WEAK — the label must
# corroborate, or an EEOC free-text 'other' input whose id happens to
# contain "location" gets filled with the user's city.
_STRONG_ATTR = {"email", "phone", "tel", "first_name", "firstname",
                "last_name", "lastname", "surname", "zip", "postalcode",
                "postal_code", "country"}

_ATTR_HINTS = {
    "city": ("city", "location", "where", "based", "reside"),
    "location": ("location", "city", "where", "based", "reside"),
    "state": ("state", "province", "region"),
    "address": ("address", "street", "residence"),
    "website": ("website", "portfolio", "site", "url", "link"),
    "portfolio_url": ("portfolio", "website", "site", "url"),
    "linkedin_url": ("linkedin",),
    "github_url": ("github",),
    "resume_path": ("resume", "cv"),
}


def _autocomplete_key(token: str) -> Optional[str]:
    """'section-blue shipping tel' → 'tel' → 'phone'."""
    if not token:
        return None
    for part in reversed(token.lower().split()):
        if part in _AUTOCOMPLETE_MAP:
            return _AUTOCOMPLETE_MAP[part]
    return None


# ─── Resolution chain ────────────────────────────────────────────────

def _i18n_norm(label: str) -> str:
    """Normalize + translate known foreign form vocabulary to English
    BEFORE the English rules run. A French form ("Ville", "Pays",
    "Courriel") otherwise falls to no_match → handover — a whole OOD
    class that the keyword layer can absorb for free.
    Word-boundary replacement only: never mangles English words."""
    n = normalize(label)
    for fr, en in _FR_EN:
        n = re.sub(r"\b" + re.escape(fr) + r"\b", en, n)
    return n


# French → English form vocabulary. KEYS ARE POST-NORMALIZATION forms —
# the normalizer turns accented letters into spaces ("prénom" → "pr nom",
# "téléphone" → "t l phone"), so the keys are the literal normalized
# strings, ordered longest-first so compound phrases win.
_FR_EN = [
    ("pr nom et nom", "full name"), ("pr nom", "first name"),
    ("nom de famille", "last name"),
    ("adresse courriel", "email"), ("courriel", "email"),
    ("e mail", "email"), ("courrier lectronique", "email"),
    ("num ro de t l phone", "phone"), ("t l phone", "phone"),
    ("code postal", "zip"), ("code postale", "zip"),
    ("ville", "city"), ("province", "province"), ("pays", "country"),
    ("adresse", "address"),
    ("nom de l entreprise", "company"), ("entreprise", "company"),
    ("titre du poste", "job title"), ("poste", "position"),
    ("date de d but", "start date"), ("date de fin", "end date"),
    ("tablissement", "institution"), ("tudes", "education"),
    ("site web", "website"), ("lien linkedin", "linkedin"),
    ("lien github", "github"), ("comp tences", "skills"),
    ("langue", "language"), ("parrain", "referral"),
    ("comment avez vous entendu", "how did you hear"),
    ("exigences salariales", "salary expectations"), ("salaire", "salary"),
    ("recommandation", "reference"),
    ("ann es d exp rience", "years of experience"),
    ("exp rience", "experience"),
    ("autorisation de travail", "work authorization"),
    ("parrainage", "sponsorship"), ("visa", "visa"),
    ("disponibilit", "availability"), ("disponible", "available"),
    ("genre", "gender"), ("pronoms", "pronouns"),
    ("ethnie", "ethnicity"), ("handicap", "disability"),
    ("ancien combattant", "veteran"), ("citoyennet", "citizenship"),
    ("r sidence", "residence"), ("relocalisation", "relocation"),
    ("d placement", "commute"), ("t l travail", "remote"),
    ("hybride", "hybrid"), ("question", "question"),
    ("r ponse", "answer"),
]


def resolve(
    label: str,
    profile: dict,
    answers_override: Optional[dict] = None,
    autocomplete: str = "",
    field_name: str = "",
    field_id: str = "",
    field_tag: str = "",
    field_type: str = "",
    field_role: str = "",
    ephemeral: Optional[dict] = None,
    domain: str = "",
) -> Resolution:
    if answers_override is None:
        answers_override = {}

    # i18n pre-pass: foreign-language form vocabulary translates to the
    # English rules (see _FR_EN). Autocomplete attributes are already
    # locale-independent — this catches the visible labels.
    norm = _i18n_norm(label)
    if not norm:
        return Resolution(None, None, label, "no_match")

    # Build ephemeral once if not provided (shared across calls in a loop)
    if ephemeral is None:
        ephemeral = _build_ephemeral(profile)

    # Step 1: --answers override (explicit user/assistant value for this run)
    for k, v in answers_override.items():
        nk = _i18n_norm(k)
        if nk == norm:
            # An explicit answer that CONTRADICTS a learned mapping proves
            # the learning wrong — invalidate it, don't outrank it forever.
            _le = _load_learned().get(norm)
            if _le is not None and str(_learned_value(_le)) != str(v):
                _invalidate_learned(norm)
            return Resolution(v, "answers_override", label, "answers_override")
        # Prefix match for field_reader's 60-char label truncation.
        # Bidirectional: field label may be truncated (nk longer than norm)
        # or answer key may be truncated (norm longer than nk).
        if len(nk) >= 10 and (norm.startswith(nk) or nk.startswith(norm)):
            return Resolution(v, "answers_override", label, "answers_override")

    # Step 1.5a: phone country code — a "Phone country code" field is a
    # COUNTRY dropdown (Canada, Antigua and Barbuda, ...), not a dialing-code
    # box. Returning the bare "+1" prefix made the combobox matcher pick the
    # first option whose text contains "+1" (e.g. Antigua & Barbuda, +1-268)
    # instead of Canada — the Antigua regression. Resolve to the COUNTRY
    # (ephemeral.country / profile location), which the picker can then match.
    # When no country is known, return no_match (→ needs_data for the
    # orchestrator) rather than guessing a dialing code that could certify a
    # wrong country.
    if "country code" in norm and "phone" in norm:
        country_val = _find_ephemeral_value("country", ephemeral)
        if country_val:
            return Resolution(str(country_val), "country", label, "country_code")
        return Resolution(None, None, label, "no_match")

    # Step 1.5: HTML autocomplete attribute (standardized semantics, free)
    ac_key = _autocomplete_key(autocomplete)
    if ac_key:
        # tel-country-code normalizes to "phone"; but the RAW attribute is a
        # country picker — return the country, never a bare +N dialing code
        # (see step 1.5a — bare codes certify wrong countries).
        raw_ac = (autocomplete or "").lower()
        if raw_ac == "tel-country-code":
            country_val = _find_ephemeral_value("country", ephemeral)
            if country_val:
                return Resolution(str(country_val), "country", label, "autocomplete")
            return Resolution(None, None, label, "no_match")
        val = _find_ephemeral_value(ac_key, ephemeral)
        if val:
            return Resolution(val, ac_key, label, "autocomplete")

    # Step 1.7: pronoun option rows ("He/him" checkboxes) derived from
    # gender. CHECK-positive only: the matching pronoun gets the check value
    # (data: pronouns.json), non-matching ones get no answer — the row starts
    # unchecked, so checking only the match achieves the exact desired state
    # without risking an uncheck on a user-chosen pronoun.
    if re.fullmatch(r"he him|she her|they them|xe xem|ze hir|ze zir|ey em",
                    norm):
        g = str(_find_ephemeral_value("gender", ephemeral)
                or _find_ephemeral_value("Gender Identity", ephemeral)
                or "").lower()
        _gmap, _check_val = _load_pronoun_data()
        if g:
            first = g.split()[0].rstrip(",")
            fam = _gmap.get(first)
            if fam and norm.startswith(fam + " "):
                return Resolution(_check_val, "pronoun", label, "pronoun")
        return Resolution(None, None, label, "no_match")

    # Step 1.6: name/id attribute semantics — ATS vendors use consistent
    # attribute names (first_name, last_name, email, phone, country, etc.)
    # that are more reliable than visible labels. Mirrors Jobright's pattern.
    # Skip for radio/select/combobox: their name/id are arbitrary DB IDs (e.g.
    # "custom_question_location") that can incidentally match _ATTR_MAP keys
    # and cause false answers on EEOC questions.
    _ft = (field_tag or "").upper()
    _fty = (field_type or "").lower()
    _role = (field_role or "").lower()
    _skip_attr = (
        _ft in ("RADIO_GROUP", "SELECT", "DROPDOWN")
        or _fty in ("radio", "select-one", "select-multiple", "custom")
        or _role == "combobox"
    )
    if not _skip_attr:
        for attr_val in (field_name, field_id):
            if not attr_val:
                continue
            av = attr_val.lower().replace("-", "_")
            for pat, ekey in _ATTR_MAP.items():
                if pat in av:
                    # Weak tokens (city/location/website/...) require the
                    # LABEL to corroborate — prevents EEOC 'other' inputs
                    # (id contains "location") being filled with the city.
                    # Word-boundary match: "ethnicity" must not count as
                    # containing "city".
                    if pat not in _STRONG_ATTR:
                        hints = _ATTR_HINTS.get(ekey, ())
                        if not re.search(
                                r"\b(" + "|".join(re.escape(h) for h in hints) + r")\b",
                                label.lower()):
                            continue
                    val = _find_ephemeral_value(ekey, ephemeral)
                    if val:
                        if "country_code" in av or "countrycode" in av:
                            import re as _re
                            m = _re.match(r'\+?(\d{1,3})', val)
                            val = ("+" + m.group(1)) if m else "+1"
                        return Resolution(val, ekey, label, "attr")

    # Step 2: profile ephemeral exact match (deterministic facts/derivations)
    for key, (val, _source) in ephemeral.items():
        if normalize(key.replace("_", " ")) == norm:
            return Resolution(val, key, label, "ephemeral")

    # Step 2.5: date-part derivation — Greenhouse-style work-history
    # "Start date month/year" fields. The profile carries "Immediately"
    # for start; stuffing it into a month picker always fails. Derive the
    # current month/year instead (start-as-soon-as-possible semantics).
    # "End date" parts are deliberately NOT derived (ambiguous — the last
    # role is usually still current).
    if re.search(r"\b(start date|start)\b", norm):
        if re.search(r"\bmonth\b", norm):
            from datetime import datetime as _dt
            return Resolution(_dt.now().strftime("%B"), "derived", label, "derived")
        if re.search(r"\byear\b", norm):
            from datetime import datetime as _dt
            return Resolution(str(_dt.now().year), "derived", label, "derived")

    # Step 3: keyword-level match — profile answer keys often contain key terms
    # that appear inside the field label (e.g. profile:willing_to_relocate →
    # field:"Are you willing to relocate"). Match when profile key's keywords
    # are a strong subset of the field label's keywords.
    # Guard: require at least one content word (not a stopword) in the key,
    # otherwise generic question stems like "have you ever been" match any
    # question containing those words (felony, disciplinary, etc.).
    _STOPWORDS = {"have", "you", "ever", "been", "do", "are", "the", "a", "an",
                  "of", "in", "to", "is", "what", "how", "did", "or", "as", "at",
                  "by", "for", "if", "with", "from", "that", "this", "your"}
    _norm_words = set(norm.split())
    for key, (val, _source) in ephemeral.items():
        _key_words = set(normalize(key.replace("_", " ")).split())
        _content = _key_words - _STOPWORDS
        if len(_key_words) >= 2 and _key_words.issubset(_norm_words):
            if len(_content) >= 2:
                return Resolution(val, key, label, "ephemeral")

    # Step 3b: suffix-stripped match — profile keys like linkedin_url / github_url
    # end in _url, _path, _handle. The entity name (linkedin, github) appears in
    # the field label ("LinkedIn Profile", "Github"). Strip suffix, check name match.
    # Guard: only match on short labels (≤6 words) to avoid matching when the entity
    # name appears as one option among many (e.g. "How did you hear about us? LinkedIn").
    if len(_norm_words) <= 6:
        for key, (val, _source) in ephemeral.items():
            for suffix in ("_url", "_path", "_handle", "_email", "_phone"):
                if key.endswith(suffix):
                    name = key[:-len(suffix)]
                    if name in _norm_words:
                        return Resolution(val, key, label, "ephemeral")

    # Step 3c: single-word whole-word match for unambiguous contact/location
    # keys ("Location (City)" → location, "Country" → country). Whitelist keeps
    # it conservative — only fires when the label is SHORT (≤3 words) so a word
    # like "country" doesn't match inside a long question like "...require visa
    # sponsorship to work in the United States or Canada?".
    _SINGLE_OK = {"email", "phone", "location", "website", "portfolio",
                  "linkedin", "github", "city", "country", "address", "zip",
                  "pronouns", "headline", "name", "province", "state"}
    _norm_word_count = len(_norm_words)
    for key, (val, _source) in ephemeral.items():
        kw = key.replace("_", " ")
        if " " in kw:
            continue
        if kw in _SINGLE_OK and kw in _norm_words and _norm_word_count <= 3:
            # "Phone Device Type" is a kind-of-device question (Landline/
            # Mobile), not a phone number — a bare "phone" must not match.
            if kw == "phone" and re.search(r"\btype\b|\bkind\b", norm):
                continue
            return Resolution(val, key, label, "ephemeral")

    # Step 4: learned mappings — labels the user/orchestrator answered in
    # prior runs (persisted to state/field_mappings.json on confirmed fills).
    lv = _lookup_learned(norm)
    if lv is not None:
        return Resolution(lv, "learned", label, "learned")

    # Step 5: curated alias patterns → candidate profile keys.
    # Same-vocabulary matching can't bridge phrasing ("How did you learn..."
    # vs how_did_you_hear). Candidates are in priority order — use the first
    # that has a value. This handles cases like authorized_to_work="Yes" vs
    # work_authorization="Yes, I am legally authorized..." where both are
    # valid but the short answer is preferred for Yes/No radio questions.
    for pattern, candidates in _alias_rules_all(host=domain):
        if not re.search(pattern, norm):
            continue
        for ck in candidates:
            v = _find_ephemeral_value(ck, ephemeral)
            if v is not None and str(v) != "":
                _touch_rule(pattern)
                return Resolution(v, ck, label, "alias")

    # Step 6: conservative form defaults — privacy-safe answers used ONLY
    # when no profile answer exists and the widget is a boolean/consent
    # control. The VALUES live in data (default_answers.json), never in code
    # (ETHOS: no hardcoded answers). The orchestrator can always override via
    # --answers (which takes priority).
    #
    # A2 gate: a "No" default must only apply to boolean/consent widgets. A
    # "Preferred name" TEXT field is a real answer ("Preferred name" got "No"
    # before), so defaults are skipped for text-like fields.
    _default_ok = True
    if _fty in ("text", "textarea", "email", "tel", "url", "number",
                "search") or _ft in ("TEXTAREA",) or _role == "textbox":
        _default_ok = False
    if _default_ok:
        for pattern, default, kinds, auto_only in _load_default_answers():
            if auto_only:
                continue  # consumed only by the fill consent path
            if re.search(pattern, norm):
                if not kinds or _fty in kinds or _role in kinds:
                    return Resolution(default, "default", label, "default")

    return Resolution(None, None, label, "no_match")


# (regex on normalized label, candidate profile/answer keys in priority order)
_ALIAS_RULES = [
    (r"^name$", ["full_name"]),
    (r"\bpreferred name\b", ["first_name"]),
    (r"\bworked (at|for) .* (before|previously)|have you ever worked|previously worked|been employed (at|by)\b",
     ["previously_employed", "have_you_ever_been"]),
    (r"\bsponsorship\b", ["need_canada_sponsorship", "need_us_sponsorship", "visa_status"]),
    (r"\bauthorized to work|legally eligible to work|eligible to work|work authorization\b",
     ["authorized_to_work", "work_authorization",
      "Are you legally eligible to work in the country that you are applying to?"]),
    (r"\bhow did you (hear|learn|find)\b",
     ["how_did_you_hear", "How did you hear about this job opportunity?"]),
    (r"\bgender\b", ["gender", "Gender Identity"]),
    (r"^(?!.*\b(spouse|dependent|family|preference)\b).*\bveteran\b", ["veteran_status"]),
    (r"\bdisabilit(?!.*\baccommodation)", ["disability_status",
      "Do you identify as a person with a visible or non-visible disability?"]),
    (r"\b(salary|compensation)\b",
     ["expected_salary", "What is your annual base salary expectations?"]),
    (r"\bstart date|when can you start|available to start|earliest start\b",
     ["start_date", "available_start", "notice_period"]),
    (r"\byears? (of )?experience\b", ["years_of_experience", "years_core_skill"]),
    (r"\brelocat", ["willing_to_relocate"]),
    (r"\bcommute\b", ["willing_to_commute"]),
    (r"\bhybrid role|comfortable with this|in (our |the )?office\b", ["office_preference"]),
    (r"\bwhich statement best describes\b.*\b(relocat|resid|eligib|office)",
     ["willing_to_relocate", "location", "city", "currently_based_ontario",
      "office_preference"]),
    (r"\bcity or location|enter city\b", ["location", "city"]),
    (r"\bexperience with ai|experience with llm|ai llm\b", ["ai_llm_experience", "Do you have experience with AI/LLMs?"]),
    (r"\bsoftware engineering heavy\b", ["software_engineering_confidence"]),
    (r"\binitialing below|by initialing|type your initials\b", ["initials"]),
    (r"\bcurrently based in.*ontario\b", ["currently_based_ontario"]),
]


# ─── Conservative defaults from DATA (not code) ────────────────────────
# Answer VALUES never live in code (ETHOS). The default set ships in
# default_answers.json (mirrors categories.json as a data file) and is
# loaded at runtime; each entry is {pattern, value, kinds} where kinds
# limits which widget types the default may apply to.
_DEFAULT_ANSWERS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "default_answers.json")
_default_answers_cache = None


def _load_default_answers():
    """[(pattern, value, kinds, auto_only)] from default_answers.json.
    auto_only entries are consumed by fill_runner's consent path, never by
    the Step 6 default loop."""
    global _default_answers_cache
    if _default_answers_cache is None:
        try:
            with open(_DEFAULT_ANSWERS_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            out = []
            for item in raw.get("defaults", []):
                pat = item.get("pattern", "")
                val = item.get("value", "")
                kinds = item.get("kinds") or []
                meta = item.get("meta") or ""
                if pat and val:
                    try:
                        re.compile(pat)
                    except re.error:
                        continue
                    out.append((pat, val, list(kinds),
                                meta == "auto_only"))
            _default_answers_cache = out
        except Exception:
            _default_answers_cache = []
    return _default_answers_cache


_PRONOUNS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pronouns.json")
_pronoun_cache = None


def _load_pronoun_data():
    """(gender→family map, check_value) from pronouns.json — language-referent
    data, not user data (SEPARATION.md Layer 1)."""
    global _pronoun_cache
    if _pronoun_cache is None:
        try:
            with open(_PRONOUNS_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _pronoun_cache = (raw.get("families") or {},
                              str(raw.get("check_value", "Yes")))
        except Exception:
            _pronoun_cache = ({}, "Yes")
    return _pronoun_cache


def _load_dialing_codes():
    """{country-norm: dialing code} from default_answers.json (Fix 1
    completion) — data, not code (no hardcoded answers)."""
    try:
        with open(_DEFAULT_ANSWERS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        d = raw.get("dialing-codes") or {}
        return {str(k).lower(): str(v) for k, v in d.items() if v}
    except Exception:
        return {}


# ─── Learned label→value mappings (lean successor of mappings.py) ─────

_LEARNED_PATH = os.path.join(STATE_DIR, "field_mappings.json")
_learned_cache = None
# Hygiene: a mapping must be confirmed consistently before it becomes
# active, and it expires unless re-confirmed. One wrong-but-verified-once
# fill must never poison a label forever.
_MIN_CONFIRMS = 2
_LEARNED_TTL_DAYS = 90


def _load_learned():
    global _learned_cache
    if _learned_cache is None:
        try:
            with open(_LEARNED_PATH, encoding="utf-8") as f:
                _learned_cache = json.load(f)
        except Exception:
            _learned_cache = {}
    return _learned_cache


def _save_learned():
    global _learned_cache
    try:
        from lib.config import atomic_write_json
        atomic_write_json(_LEARNED_PATH, _learned_cache, indent=2)
    except Exception:
        pass


def _learned_value(entry):
    """Value of an entry (dict with hygiene meta or legacy flat string)."""
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _lookup_learned(norm_label: str):
    m = _load_learned()
    e = m.get(norm_label)
    if e is None:
        return None
    if not isinstance(e, dict):
        return e  # legacy flat value (pre-hygiene)
    if e.get("state") != "active":
        return None
    if e.get("ts"):
        try:
            from datetime import datetime
            age = (datetime.now() - datetime.fromisoformat(e["ts"])).days
            if age > _LEARNED_TTL_DAYS:
                return None
        except Exception:
            pass
    return e.get("value")


def _invalidate_learned(norm_label):
    """Delete a learned mapping — an explicit answer that contradicts it
    proves the learning was wrong; it must not survive."""
    m = _load_learned()
    if norm_label in m:
        del m[norm_label]
        _save_learned()
        global _learned_cache
        _learned_cache = m


# ─── Runtime alias rules ──────────────────────────────────────────────
# The static _ALIAS_RULES above are source-edited; these are the runtime
# store the orchestrator/operator adds to WITHOUT a deploy (the "wired"
# version of report.py fleet's rule candidates). Same shape: (regex, [keys]).
_RUNTIME_RULES_PATH = os.path.join(STATE_DIR, "alias_rules.json")
_runtime_rules_cache = None
# Expiry: a rule that has never matched in this window is dropped — a stale
# runtime rule (like a stale learned mapping) must not persist forever.
_RULE_TTL_DAYS = 180


def _load_runtime_rules():
    """[[pattern, [candidate keys], last_seen_ts, domain]] added at runtime."""
    global _runtime_rules_cache
    if _runtime_rules_cache is None:
        try:
            with open(_RUNTIME_RULES_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = []
        rules = []
        for item in raw or []:
            if isinstance(item, dict):
                pat = item.get("pattern", "")
                keys = item.get("keys") or []
                last = item.get("last_seen") or ""
                domain = item.get("domain") or ""
            else:
                continue
            if pat and isinstance(keys, list) and keys:
                rules.append([pat, keys, last, domain])
        _runtime_rules_cache = rules
    return _runtime_rules_cache


def _save_runtime_rules(rules):
    global _runtime_rules_cache
    _runtime_rules_cache = rules
    try:
        from lib.config import atomic_write_json
        atomic_write_json(_RUNTIME_RULES_PATH, [
            {"pattern": p, "keys": k, "last_seen": last, "domain": dom}
            for p, k, last, dom in rules], indent=2)
    except Exception:
        pass


def _host_matches(rule_domain, host):
    """A rule applies when its domain is empty (global) or matches the host
    (A4: a rule learned for one platform must not fire on another)."""
    if not rule_domain:
        return True
    host = (host or "").lower().rstrip(".")
    rule_domain = rule_domain.lower().rstrip(".")
    if not host:
        return False
    return rule_domain == host or host.endswith("." + rule_domain) \
        or rule_domain.endswith("." + host)


def _alias_rules_all(host=""):
    """Static + runtime rules, with runtime expiry + last-seen + domain
    scoping. Runtime rules checked first so a confirmed orchestrator answer
    (no deploy needed) outranks the source defaults."""
    rules = _load_runtime_rules()
    if not rules:
        return _ALIAS_RULES
    kept, expired = [], []
    for entry in rules:
        pat, keys, last, domain = entry
        if last and _is_expired(last, _RULE_TTL_DAYS):
            expired.append(pat)
            continue
        if not _host_matches(domain, host):
            continue
        kept.append(entry)
    if expired:
        _save_runtime_rules([e for e in rules if e[0] not in expired])
    return [(p, k) for p, k, _, _ in kept] + _ALIAS_RULES


def _is_expired(ts, ttl_days):
    try:
        from datetime import datetime
        age = (datetime.now() - datetime.fromisoformat(ts)).days
        return age > ttl_days
    except Exception:
        return False


def _touch_rule(pattern):
    """Mark a runtime rule as recently matched (updates last_seen)."""
    for entry in _load_runtime_rules():
        if entry[0] == pattern:
            entry[2] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save_runtime_rules(_load_runtime_rules())
            return


def add_alias_rule(pattern, keys, dedupe=True, domain=""):
    """Add a runtime alias rule (pattern → candidate keys). Returns True on
    success. Dedupes by identical pattern by default. Invalid regex or empty
    keys are refused. `domain` scopes the rule to one host (A4); empty =
    global."""
    if not pattern or not keys:
        return False
    try:
        re.compile(pattern)
    except re.error:
        return False
    rules = _load_runtime_rules()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if dedupe:
        rules = [r for r in rules if r[0] != pattern]
    rules.append([pattern, list(keys), now, domain or ""])
    _save_runtime_rules(rules)
    return True


def list_alias_rules():
    """Runtime rules for inspection (report.py surface)."""
    return [(p, k) for p, k, _, _ in _load_runtime_rules()]


def clear_alias_rules():
    """Drop all runtime rules (operator escape hatch)."""
    _save_runtime_rules([])


def learn_mapping(label: str, value, domain: str = ""):
    """Persist a confirmed label→value mapping — after N consistent
    confirmations (pending → active), with provenance for rollback and a
    TTL so stale learnings expire. Conflicting values reset the count."""
    if not label or value in (None, ""):
        return
    norm = _i18n_norm(label)
    if not norm:
        return
    m = _load_learned()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    e = m.get(norm)
    if not isinstance(e, dict):
        m[norm] = {"value": value, "state": "pending", "count": 1,
                   "ts": now, "domain": domain, "last_jid": ""}
    elif _learned_value(e) == value:
        e["count"] = int(e.get("count", 0)) + 1
        e["ts"] = now
        if e.get("domain") != domain:
            # Same label confirmed on a DIFFERENT platform — the value is
            # platform-general or the scope is wider than first thought.
            e["domain"] = ""
        if e.get("count", 0) >= _MIN_CONFIRMS:
            e["state"] = "active"
    else:
        # Conflicting confirmation — the learning is unstable, reset.
        m[norm] = {"value": value, "state": "pending", "count": 1,
                   "ts": now, "domain": domain, "last_jid": ""}
    _save_learned()


def _norm_pattern(label):
    """A fleet-style content-word pattern from a label — the runtime-rule
    pattern used when promoting a learned mapping to a rule."""
    words = [w for w in re.split(r"[^a-z0-9]+", _i18n_norm(label))
             if len(w) > 2][:4]
    if len(words) < 2:
        return ""
    return r"\b" + r"\b.*\b".join(re.escape(w) for w in words) + r"\b"


def promote_learned_to_rule(label, domain="", force=False):
    """S2 auto-promotion gate: promote a LEARNED mapping to a RUNTIME alias
    rule — but only when it has reached `active` (≥2 consistent confirms,
    the S2 correction-buffer threshold). A single-or-conflicting answer must
    never create a global rule. `force` bypasses the gate for explicit
    operator confirmation.

    Returns ("promoted"|"not_active"|"no_pattern"|"conflict", detail)."""
    if not label:
        return "no_pattern", "empty label"
    norm = _i18n_norm(label)
    if not norm:
        return "no_pattern", "unnormalizable label"
    e = _load_learned().get(norm)
    if not isinstance(e, dict):
        return "no_pattern", "no learned mapping for label"
    if not force and e.get("state") != "active":
        return ("not_active",
                f"mapping has {e.get('count', 0)}/{_MIN_CONFIRMS} confirms "
                f"(state={e.get('state', '?')}) — S2 gate blocks promotion")
    pat = _norm_pattern(label)
    if not pat:
        return "no_pattern", "label too short for a content-word pattern"
    # The runtime rule maps the pattern to the PROFILE answer key that
    # produced the learned value — find the key whose value matches. Scoped
    # to the learned mapping's domain (#5): never a global rule from one
    # platform's answer.
    key = _find_key_for_value(str(e.get("value", "")), _load_profile_flat())
    if not key:
        return "conflict", "learned value not present in profile — cannot map to an answer key"
    add_alias_rule(pat, [key], domain=e.get("domain", ""))
    return "promoted", f"{pat[:60]} -> {key} (domain={e.get('domain','')})"


def _load_profile_flat():
    """Profile answers flattened to {key: str} — the space a runtime rule's
    candidate keys must live in."""
    flat = {}
    try:
        from lib.config import PROFILE_PATH
        import json as _json
        with open(PROFILE_PATH, encoding="utf-8") as f:
            p = _json.load(f)
        flat.update({k: str(v) for k, v in (p.get("answers") or {}).items() if v})
        for k in ("first_name", "last_name", "email", "phone", "location"):
            if p.get(k):
                flat[k] = str(p[k])
    except Exception:
        pass
    return flat


def _find_key_for_value(value, flat):
    """First profile key whose value equals `value` (case-insensitive)."""
    vl = value.lower()
    for k, v in flat.items():
        if v.lower() == vl:
            return k
    return None


def clear_learned_for_test():
    """Test-only: wipe the learned-mapping store so promotion-gate tests
    start clean. Never called by the pipeline."""
    global _learned_cache
    _learned_cache = {}
    try:
        from lib.config import atomic_write_json
        atomic_write_json(_LEARNED_PATH, {}, indent=2)
    except Exception:
        pass
