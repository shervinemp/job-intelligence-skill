"""signals.py — Canonical success-text signals for submission detection.

Single source of truth: the same phrases previously lived in four modules and
had drifted apart (a signal the submit poll detected was ignored by the decision
loop right below it).

Two tiers:
  SUCCESS_STRICT — specific enough to justify a DB write (mark applied).
  SUCCESS_BROAD  — strict + looser phrases, for polling/early-exit only.
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
    "we received your application",
    "successfully submitted",
    "your application is complete",
    "thanks for your application",
    "application has been received",
    "application has been submitted",
    "thanks for taking the time to apply",
    "thank you for your application",
    "we received your application",
    "your application has been received",
    "your application has been submitted",
)


def has_success_text(text, signals=SUCCESS_STRICT):
    """True if any signal phrase appears in the (case-folded) text."""
    t = (text or "").lower()
    return any(s in t for s in signals)
