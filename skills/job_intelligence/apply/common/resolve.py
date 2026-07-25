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
            ephemeral.setdefault("country", (parts[-1], "derived"))

    answers = profile.get("answers", {})
    if isinstance(answers, dict):
        for k, v in answers.items():
            if v:
                ephemeral[k] = (str(v) if not isinstance(v, list) else [str(x) for x in v], "static")

    # Aliases: forms ask "Website" when the profile has portfolio_url (and vice versa)
    if ephemeral.get("portfolio_url") and "website" not in ephemeral:
        ephemeral["website"] = ephemeral["portfolio_url"]
    if ephemeral.get("website") and "portfolio_url" not in ephemeral:
        ephemeral["portfolio_url"] = ephemeral["website"]

    return ephemeral


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


def _autocomplete_key(token: str) -> Optional[str]:
    """'section-blue shipping tel' → 'tel' → 'phone'."""
    if not token:
        return None
    for part in reversed(token.lower().split()):
        if part in _AUTOCOMPLETE_MAP:
            return _AUTOCOMPLETE_MAP[part]
    return None


# ─── Resolution chain ────────────────────────────────────────────────

def resolve(
    label: str,
    profile: dict,
    answers_override: Optional[dict] = None,
    autocomplete: str = "",
    field_name: str = "",
    field_id: str = "",
) -> Resolution:
    if answers_override is None:
        answers_override = {}

    norm = normalize(label)
    if not norm:
        return Resolution(None, None, label, "no_match")

    # Step 1: --answers override (explicit user/assistant value for this run)
    for k, v in answers_override.items():
        nk = normalize(k)
        if nk == norm:
            return Resolution(v, "answers_override", label, "user_typed")
        # Prefix match for field_reader's 60-char label truncation
        if len(nk) >= 10 and norm.startswith(nk):
            return Resolution(v, "answers_override", label, "user_typed")

    # Step 1.5a: phone country code — extract from phone before generic matching
    if "country code" in norm and "phone" in norm:
        phone_val = _find_ephemeral_value("phone", _build_ephemeral(profile))
        if phone_val:
            m = re.match(r'\+?(\d{1,3})', phone_val)
            return Resolution(("+" + m.group(1)) if m else "+1", "phone", label, "country_code")

    # Step 1.5: HTML autocomplete attribute (standardized semantics, free)
    ephemeral = _build_ephemeral(profile)
    ac_key = _autocomplete_key(autocomplete)
    if ac_key:
        val = _find_ephemeral_value(ac_key, ephemeral)
        if val:
            if ac_key == "tel-country-code":
                import re as _re
                m = _re.match(r'\+?(\d{1,3})', val)
                val = ("+" + m.group(1)) if m else "+1"
            return Resolution(val, ac_key, label, "autocomplete")

    # Step 1.6: name/id attribute semantics — ATS vendors use consistent
    # attribute names (first_name, last_name, email, phone, country, etc.)
    # that are more reliable than visible labels. Mirrors Jobright's pattern.
    for attr_val in (field_name, field_id):
        if not attr_val:
            continue
        av = attr_val.lower().replace("-", "_")
        for pat, ekey in _ATTR_MAP.items():
            if pat in av:
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

    # Step 3: keyword-level match — profile answer keys often contain key terms
    # that appear inside the field label (e.g. profile:willing_to_relocate →
    # field:"Are you willing to relocate"). Match when profile key's keywords
    # are a strong subset of the field label's keywords.
    _norm_words = set(norm.split())
    for key, (val, _source) in ephemeral.items():
        _key_words = set(normalize(key.replace("_", " ")).split())
        if len(_key_words) >= 2 and _key_words.issubset(_norm_words):
            return Resolution(val, key, label, "ephemeral")

    # Step 3b: suffix-stripped match — profile keys like linkedin_url / github_url
    # end in _url, _path, _handle. The entity name (linkedin, github) appears in
    # the field label ("LinkedIn Profile", "Github"). Strip suffix, check name match.
    for key, (val, _source) in ephemeral.items():
        for suffix in ("_url", "_path", "_handle", "_email", "_phone"):
            if key.endswith(suffix):
                name = key[:-len(suffix)]
                if name in _norm_words:
                    return Resolution(val, key, label, "ephemeral")

    # Step 3c: single-word whole-word match for unambiguous contact/location
    # keys ("Location (City)" → location, "Country" → country). Whitelist keeps
    # it conservative — no fuzzy guessing on multi-intent words.
    _SINGLE_OK = {"email", "phone", "location", "website", "portfolio",
                  "linkedin", "github", "city", "country", "address", "zip",
                  "pronouns", "headline"}
    for key, (val, _source) in ephemeral.items():
        kw = key.replace("_", " ")
        if " " in kw:
            continue
        if kw in _SINGLE_OK and kw in _norm_words:
            return Resolution(val, key, label, "ephemeral")

    # Step 4: learned mappings — labels the user/orchestrator answered in
    # prior runs (persisted to state/field_mappings.json on confirmed fills).
    lv = _lookup_learned(norm)
    if lv is not None:
        return Resolution(lv, "learned", label, "learned")

    # Step 5: curated alias patterns → candidate profile keys.
    # Same-vocabulary matching can't bridge phrasing ("How did you learn..."
    # vs how_did_you_hear). Candidates resolve unanimously: if multiple
    # candidate keys exist with DIFFERENT values, we abstain (no guessing).
    for pattern, candidates in _ALIAS_RULES:
        if not re.search(pattern, norm):
            continue
        vals = []
        for ck in candidates:
            v = _find_ephemeral_value(ck, ephemeral)
            if v is not None and str(v) != "":
                vals.append((ck, v))
        if vals and all(str(v) == str(vals[0][1]) for _k, v in vals):
            return Resolution(vals[0][1], vals[0][0], label, "alias")

    # Step 6: conservative form defaults for common optional questions where
    # "No" is the universally safe answer (marketing, alerts, current-employee).
    # NOT a user assumption — a privacy-conservative form default. The
    # orchestrator can always override via --answers (which takes priority).
    for pattern, default in _DEFAULT_ANSWERS:
        if re.search(pattern, norm):
            return Resolution(default, "default", label, "default")

    return Resolution(None, None, label, "no_match")


# (regex on normalized label, candidate profile/answer keys in priority order)
_ALIAS_RULES = [
    (r"\bpreferred name\b", ["first_name"]),
    (r"\bworked (at|for) .* before|have you ever worked|previously worked|been employed (at|by)\b",
     ["previously_employed", "have_you_ever_been"]),
    (r"\bsponsorship\b", ["need_canada_sponsorship", "need_us_sponsorship", "visa_status"]),
    (r"\bauthorized to work|legally eligible to work|work authorization\b",
     ["authorized_to_work", "work_authorization",
      "Are you legally eligible to work in the country that you are applying to?"]),
    (r"\bhow did you (hear|learn|find)\b",
     ["how_did_you_hear", "How did you hear about this job opportunity?"]),
    (r"\bgender\b", ["gender", "Gender Identity"]),
    (r"\bveteran\b", ["veteran_status"]),
    (r"\bdisabilit", ["disability_status",
      "Do you identify as a person with a visible or non-visible disability?"]),
    (r"\bsalary|compensation\b",
     ["expected_salary", "What is your annual base salary expectations?"]),
    (r"\bstart date|when can you start|available to start|earliest start\b",
     ["start_date", "available_start", "notice_period"]),
    (r"\byears? (of )?experience\b", ["years_of_experience", "years_core_skill"]),
    (r"\brelocat", ["willing_to_relocate"]),
    (r"\bcommute\b", ["willing_to_commute"]),
    (r"\bhybrid role|comfortable with this|in (our |the )?office\b", ["office_preference"]),
]


# Conservative form defaults — NOT user assumptions. "No" is the safe answer
# for marketing consent, job alerts, and current-employee questions. Only
# fires for optional fields; the orchestrator can override via --answers.
_DEFAULT_ANSWERS = [
    (r"\b(stay up to date|marketing|newsletter|promotional|email updates|communications from)\b", "No"),
    (r"\b(receive alerts|job alerts|similar jobs|email me|notify me)\b", "No"),
    (r"\bcurrent\b.*\b(employee|staff|team member)\b", "No"),
]


# ─── Learned label→value mappings (lean successor of mappings.py) ─────

_LEARNED_PATH = os.path.join(STATE_DIR, "field_mappings.json")
_learned_cache = None


def _load_learned():
    global _learned_cache
    if _learned_cache is None:
        try:
            with open(_LEARNED_PATH, encoding="utf-8") as f:
                _learned_cache = json.load(f)
        except Exception:
            _learned_cache = {}
    return _learned_cache


def _lookup_learned(norm_label: str):
    return _load_learned().get(norm_label)


def learn_mapping(label: str, value):
    """Persist a confirmed label→value mapping for future runs."""
    if not label or value in (None, ""):
        return
    norm = normalize(label)
    if not norm:
        return
    m = _load_learned()
    if m.get(norm) == value:
        return
    m[norm] = value
    try:
        from lib.config import atomic_write_json
        atomic_write_json(_LEARNED_PATH, m, indent=2)
    except Exception:
        pass
