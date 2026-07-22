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

import re
from typing import Optional


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

# String-valued profile facts resolved by exact (normalized) label match. Kept in
# sync with act.py's _KNOWN_PROFILE_KEYS (the typo-detection set), minus the
# boolean/parameterized facts (authorized_to_work, requires_sponsorship) that need
# yes/no + country transforms — those are deferred to the mapping layer (ADR-001
# Phase 3), not resolved here.
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

    return ephemeral


def _find_ephemeral_value(key: str, ephemeral: dict) -> Optional[str]:
    entry = ephemeral.get(key)
    return entry[0] if entry else None


# ─── Resolution chain ────────────────────────────────────────────────

def resolve(
    label: str,
    profile: dict,
    answers_override: Optional[dict] = None,
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

    # Step 2: profile ephemeral exact match (deterministic facts/derivations)
    ephemeral = _build_ephemeral(profile)
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

    return Resolution(None, None, label, "no_match")


# ─── Entry point for act.py ─────────────────────────────────────────

def resolution_for_fill(
    label: str,
    profile: dict,
    answers_override: Optional[dict] = None,
    available_options: Optional[list] = None,
) -> Resolution:
    return resolve(label, profile, answers_override=answers_override)
