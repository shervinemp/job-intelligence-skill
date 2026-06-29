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

_PROFILE_KEYS = {
    "first_name", "last_name", "email", "phone",
    "linkedin_url", "github_url", "portfolio_url",
    "resume_path", "location",
}


def _build_ephemeral(profile: dict) -> dict:
    ephemeral = {}

    for k in _PROFILE_KEYS:
        v = profile.get(k)
        if v:
            ephemeral[k] = (v, "profile")

    fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
    if fn and ln:
        ephemeral["full_name"] = (f"{fn} {ln}", "derived")
    elif fn or ln:
        ephemeral["full_name"] = (fn or ln, "derived")

    loc = profile.get("location", "")
    if loc and "," in loc:
        parts = [p.strip() for p in loc.split(",")]
        if len(parts) >= 1 and parts[0]:
            ephemeral["city"] = (parts[0], "derived")
        if len(parts) >= 2 and parts[1]:
            ephemeral["state_province"] = (parts[1], "derived")
        if len(parts) >= 3 and parts[-1]:
            ephemeral["country"] = (parts[-1], "derived")

    answers = profile.get("answers", {})
    if isinstance(answers, dict):
        for k, v in answers.items():
            if v:
                ephemeral[k] = (v, "static")

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

    return Resolution(None, None, label, "no_match")


# ─── Entry point for act.py ─────────────────────────────────────────

def resolution_for_fill(
    label: str,
    profile: dict,
    answers_override: Optional[dict] = None,
) -> Resolution:
    return resolve(label, profile, answers_override=answers_override)
