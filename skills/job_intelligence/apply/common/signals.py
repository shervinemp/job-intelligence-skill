"""signals.py — Canonical success-text signals for submission detection.

Single source of truth: the same phrases previously lived in four modules and
had drifted apart (a signal the submit poll detected was ignored by the decision
loop right below it).

Three tiers:
  SUCCESS_STRICT   — specific enough to justify a DB write (mark applied).
  ALREADY_APPLIED  — phrases indicating the user already applied previously
                     (pre-flight check to prevent duplicate submissions).
  SUCCESS_BROAD    — strict + looser phrases, for polling/early-exit only.
                     NEVER use broad signals to mark a job applied.
"""

SUCCESS_STRICT = (
    "your application has been",
    "your application was",
    "has been sent",
    "application received",
    "you have applied",
    "application submitted",
    "successfully applied",
    "thank you for applying",
    "application complete",
    "successfully submitted",
    "your application is complete",
    "thanks for your application",
    "application has been received",
    "application has been submitted",
    "thanks for taking the time to apply",
    "thank you for your application",
    "your application has been received",
    "your application has been submitted",
)

# Pre-flight patterns: if these appear on the page BEFORE we click submit,
# the job was already applied in a prior session. Abort to prevent duplicates.
ALREADY_APPLIED = (
    "your application was sent",
    "you applied to this job",
    "applied on ",
    "application status:",
    "you've already applied",
    "you have already applied",
    "apply again",
    "withdraw application",
    "withdraw your application",
    "applied \\d",  # "Applied 3 days ago"
)


def has_success_text(text, signals=SUCCESS_STRICT):
    """True if any signal phrase appears in the (case-folded) text."""
    t = (text or "").lower()
    return any(s in t for s in signals)


def has_already_applied_text(text):
    """True if the page text indicates a prior application exists."""
    import re
    t = (text or "").lower()
    if any(s in t for s in ALREADY_APPLIED if not s.startswith("applied ")):
        return True
    # Regex patterns (those with \d or special chars)
    for pat in ALREADY_APPLIED:
        if "\\" in pat or pat.startswith("applied "):
            if re.search(pat, t):
                return True
    return False
