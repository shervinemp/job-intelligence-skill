"""preflight.py — profile readiness gate for fleet runs.

The framework used to ship incomplete profiles into the fleet and fail
identically on every "Current Company" field. Preflight catches that
BEFORE a batch: identity/contact/work-history must be present (hard),
education and links should be (soft), and the recurring question classes
(sponsorship, EEOC, salary, relocation...) should have answers.

Also forecasts coverage: how many of the KNOWN form labels (learned
mappings + recurring patterns) the current profile can actually answer —
so the orchestrator knows which jobs will run clean before any browser
work happens.

Manifest shape (consumed by the shadow supervisor, submit warnings, and
the orchestrator):
{
  "hard_missing": [...],   # identity/contact/work_history — batch should wait
  "soft_missing": [...],   # education/links — warn only
  "answer_gaps": [...],    # recurring question classes without answers
  "coverage_pct": 0-100,   # known labels resolvable with this profile
}
"""
import os
import re
import sys

_HARD_KEYS = ["first_name", "last_name", "email", "phone", "location"]
_SOFT_KEYS = ["education", "linkedin_url", "github_url", "portfolio_url"]
_WORK_KEY = "work_history"

# Recurring question classes the fill loop asks across platforms — each
# should have a profile key or an answers entry.
_ANSWER_CLASSES = {
    "authorization": ["authorized_to_work", "work_authorization",
                      "Are you legally eligible to work in the country that you are applying to?"],
    "sponsorship": ["need_us_sponsorship", "need_canada_sponsorship"],
    "how_did_you_hear": ["how_did_you_hear",
                         "How did you hear about this job opportunity?"],
    "salary": ["expected_salary",
               "What is your annual base salary expectations?"],
    "start": ["start_date", "available_start", "notice_period"],
    "relocate": ["willing_to_relocate"],
    "commute": ["willing_to_commute"],
    "office": ["office_preference"],
    "experience": ["years_of_experience", "years_core_skill"],
    "gender": ["gender", "Gender Identity"],
    "veteran": ["veteran_status"],
    "disability": ["disability_status"],
    "previously_employed": ["previously_employed", "have_you_ever_been"],
    "criminal": ["criminal_record"],
    "consent": ["consent_text_messages"],
}


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).strip()


def _available_keys(profile):
    """All keys/values the resolver can see: profile + profile.answers."""
    keys = {k: v for k, v in profile.items() if v}
    for k, v in (profile.get("answers") or {}).items():
        if v:
            keys[k] = v
    return keys


def preflight(profile=None, known_labels=None):
    """Compute the readiness manifest for the given profile (or the real
    one on disk when None)."""
    if profile is None:
        try:
            from lib.config import PROFILE_PATH
            import json as _json
            with open(PROFILE_PATH, encoding="utf-8") as f:
                profile = _json.load(f)
        except Exception:
            profile = {}

    hard_missing = [k for k in _HARD_KEYS if not profile.get(k)]
    wh = profile.get(_WORK_KEY) or profile.get("work") or []
    if not wh:
        hard_missing.append("work_history")
    soft_missing = [k for k in _SOFT_KEYS if not profile.get(k)]

    keys = _available_keys(profile)
    answer_gaps = []
    for cls, candidates in _ANSWER_CLASSES.items():
        if not any(keys.get(c) for c in candidates):
            answer_gaps.append(cls)

    # Coverage forecast over known labels (learned mappings + recurring
    # patterns as a corpus proxy for what forms ask).
    coverage_pct = 0
    if known_labels is None:
        known_labels = []
        try:
            from .common.resolve import _load_learned
            known_labels = list(_load_learned().keys())
        except Exception:
            pass
    if known_labels:
        from .common.resolve import resolve, _build_ephemeral
        ephemeral = _build_ephemeral(profile)
        resolvable = sum(
            1 for lbl in known_labels
            if resolve(lbl, profile, ephemeral=ephemeral).value is not None)
        coverage_pct = round(100 * resolvable / len(known_labels))

    return {
        "hard_missing": hard_missing,
        "soft_missing": soft_missing,
        "answer_gaps": answer_gaps,
        "coverage_pct": coverage_pct,
    }


def fmt_manifest(manifest):
    """One-line-per-category manifest for CLI output."""
    lines = []
    if manifest["hard_missing"]:
        lines.append("  HARD (blocking): " + ", ".join(manifest["hard_missing"]))
    if manifest["soft_missing"]:
        lines.append("  SOFT (warn): " + ", ".join(manifest["soft_missing"]))
    if manifest["answer_gaps"]:
        lines.append("  ANSWER GAPS: " + ", ".join(manifest["answer_gaps"]))
    lines.append(f"  COVERAGE: {manifest['coverage_pct']}% of known labels resolvable")
    return "\n".join(lines)


def cmd_preflight():
    """apply.py preflight — print the readiness manifest."""
    m = preflight()
    print(f"PREFLIGHT: hard_missing={len(m['hard_missing'])} "
          f"soft_missing={len(m['soft_missing'])} "
          f"answer_gaps={len(m['answer_gaps'])} "
          f"coverage={m['coverage_pct']}%", file=sys.stderr)
    print(fmt_manifest(m), file=sys.stderr)
    if m["hard_missing"]:
        print("  NEXT: complete profile.json (work_history, education) then "
              "re-run preflight — the fleet will fail identically without them.",
              file=sys.stderr)
        return 1
    return 0
