"""terms.py — THE vocabulary. One source of truth for every cross-module
status, kind, outcome, and aggregate in the system.

Why this exists (ETHOS.md §5-adjacent): vocabulary drift caused a real
double-count bug (two implementations of one aggregate disagreed) and
repeated misreadings (no_match vs no_option_match vs needs_data). Every
value that crosses a module boundary lives HERE, as a constant; modules
import, never hardcode. `report.py glossary` is generated from this
module, so the documentation cannot drift either.

The aggregates: `summarize(fields)` is the ONLY implementation of the
filled/failed/skipped math — the DECISION line, the dossier summary and
the classifier all consume it, so they can never disagree again.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# ── Dossier kinds (epistemic state of a field) ────────────────────────
VERIFIED = "verified"                 # re-read after fill, matched
UNVERIFIED = "unverified"             # filled, read-back inconclusive
NEEDS_DATA = "needs_data"             # no answer supplied (data gap)
REJECTED_BY_FORM = "rejected_by_form" # the form rejected the value
INTERACTION_FAILED = "interaction_failed"  # fill mechanism errored
KINDS = (VERIFIED, UNVERIFIED, NEEDS_DATA,
         REJECTED_BY_FORM, INTERACTION_FAILED)

# ── Check severities ──────────────────────────────────────────────────
SEV_ERROR = "ERROR"   # blocks submit
SEV_WARN = "WARN"     # review
SEV_INFO = "INFO"     # unreadable/needs visual verify
SEVERITIES = (SEV_ERROR, SEV_WARN, SEV_INFO)

# ── Pipeline statuses (state["status"]) ───────────────────────────────
STATUS_LOGIN_REQUIRED = "login_required"
STATUS_LOGIN_FAILED = "login_failed"
STATUS_CAPTCHA_REQUIRED = "captcha_required"
STATUS_2FA_REQUIRED = "2fa_required"
STATUS_TIMED_OUT = "timed_out"
STATUS_NO_APPLY_PATH = "no_apply_path"
STATUS_FILLED = "filled"
STATUS_HOLD = "hold"
STATUS_BLOCKED = "blocked"
STATUS_CHECK_FAILED = "check_failed"
STATUS_REGRESSION_GATE = "regression_gate"
STATUS_UNKNOWN = "unknown"

# ── Shadow run outcomes (shadow log + worker outcome files) ───────────
OUTCOME_HELD_SHADOW = "held_shadow"   # fill+check OK, submit held
OUTCOME_STOPPED = "stopped"           # needs review (check errors etc.)
OUTCOME_SKIPPED = "skipped"           # login/captcha/expired/unconfirmed
OUTCOME_CRASH = "crash"               # hard death, evidence captured
OUTCOME_TIMEOUT = "timeout"           # wall-clock budget, not a crash
OUTCOME_ERROR = "error"               # worker-level error (job not found)
OUTCOME_ALREADY_APPLIED = "already_applied"
OUTCOME_SUBMITTED = "submitted"       # unexpected in shadow
OUTCOME_EXCEPTION = "exception"
OUTCOMES = (OUTCOME_HELD_SHADOW, OUTCOME_STOPPED, OUTCOME_SKIPPED,
            OUTCOME_CRASH, OUTCOME_TIMEOUT, OUTCOME_ERROR,
            OUTCOME_ALREADY_APPLIED, OUTCOME_SUBMITTED, OUTCOME_EXCEPTION)

# ── ask_api escape-hatch status (dossier llm_status) ──────────────────
LLM_UNUSED = "unused"       # escape hatch never reached
LLM_POLICY_OFF = "policy_off"  # JI_LLM_MODE gated it
LLM_API_DOWN = "api_down"   # ask_api unavailable
LLM_DECLINED = "declined"   # model returned nothing usable
LLM_USED = "used"           # model produced a value
LLM_STATUSES = (LLM_UNUSED, LLM_POLICY_OFF, LLM_API_DOWN,
                LLM_DECLINED, LLM_USED)

# ── Summary vocabulary (mutually exclusive, sums to unique total) ─────
K_FILLED = "filled"
K_FAILED = "failed"
K_SKIPPED_OPTIONAL = "skipped_optional"
SUMMARY_KEYS = (K_FILLED, K_FAILED, K_SKIPPED_OPTIONAL)

# ── Display-only truncation: identity lives in label_full; truncation
#    is ALWAYS visible via the marker. ─────────────────────────────────
TRUNC_W = 60
TRUNC_MARK = "…"


def trunc(text, width=TRUNC_W):
    """Display-only truncation — identity must never depend on this."""
    s = str(text or "")
    if len(s) <= width:
        return s
    return s[: max(0, width - len(TRUNC_MARK))].rstrip() + TRUNC_MARK


def summarize(fields: List[Dict[str, Any]]) -> Dict[str, int]:
    """THE single aggregate. filled + failed + skipped_optional equals the
    unique field total, always — failed EXCLUDES optional no-data fields,
    so a field is never double-counted. Used by the DECISION line, the
    dossier summary, and the classifier — one implementation, zero drift.
    """
    filled = 0
    failed = 0
    skipped_optional = 0
    for f in fields or []:
        kind = f.get("kind")
        if kind in (VERIFIED, UNVERIFIED):
            filled += 1
        elif kind == NEEDS_DATA:
            if f.get("required"):
                failed += 1
            else:
                skipped_optional += 1
        elif kind in (REJECTED_BY_FORM, INTERACTION_FAILED):
            failed += 1
        else:
            # Unknown kind = a vocabulary violation — fail closed: never
            # count it as filled. The pinned tests catch the violation.
            failed += 1
    return {K_FILLED: filled, K_FAILED: failed,
            K_SKIPPED_OPTIONAL: skipped_optional}


def failed_labels(fields):
    """Labels that would fail validation on submit (the honest failure
    set used by handoff decisions and the owner-split)."""
    return [f.get("label", "") for f in fields or []
            if f.get("kind") in (REJECTED_BY_FORM, INTERACTION_FAILED)
            or (f.get("kind") == NEEDS_DATA and f.get("required"))]


def skipped_labels(fields):
    """Optional no-data labels — excluded from the failed count."""
    return [f.get("label", "") for f in fields or []
            if f.get("kind") == NEEDS_DATA and not f.get("required")]


_GLOSSARY = [
    ("kind", "field epistemic state", "verified/unverified/needs_data/rejected_by_form/interaction_failed — written by fill, arbitrated by check"),
    ("summary", "mutually-exclusive counts", "filled + failed + skipped_optional = unique field total (summarize())"),
    ("status", "pipeline stop reason", "state[status] — login_required/no_apply_path/captcha_required/2fa_required/timed_out/filled"),
    ("outcome", "shadow-run classification", "held_shadow/stopped/skipped/crash/timeout/error/already_applied/submitted/exception"),
    ("llm_status", "escape-hatch outcome", "unused/policy_off/api_down/declined/used — per call, aggregated into dossiers"),
    ("severity", "check issue weight", "ERROR blocks submit, WARN review, INFO unreadable/visual-verify"),
    ("no_match", "RESOLVER verdict", "deterministic resolve() found no answer — distinct from no_option_match (combobox)"),
    ("no_option_match", "COMBOBOX verdict", "deterministic matcher found no option — distinct from no_match (resolver)"),
    ("no_answer", "field outcome", "fill-time: no answer supplied — maps to kind needs_data"),
    ("unconfirmed", "skip cause", "no_apply_path without an explicit closed signal — cookie/session variance, re-examined via shadow --recheck"),
    ("fill_answers", "answer map", "the ONE name for the label→value map (was answers/ans_dict/answers_override)"),
    ("dossier", "per-job truth", "results/{jid}/handoff.json + handoffs/ history — authoritative; apply_state.json is runtime cache"),
    ("profile", "canonical user data", "profile.json — the factual base for grounding and resolving"),
    ("resume", "per-job tailored artifact", "results/{jid}/resume.json — must trace to profile (grounding gate)"),
]


def glossary():
    """The orchestrator's dictionary — generated from this module, so it
    cannot drift. report.py glossary prints this."""
    return [(term, meaning, note) for term, meaning, note in _GLOSSARY]
