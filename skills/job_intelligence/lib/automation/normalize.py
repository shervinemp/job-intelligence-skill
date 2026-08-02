"""normalize — pure text folding for matching.

Accent/case/punctuation normalization only — no domain knowledge, no
calibration. Safe for every consumer. Scoring logic stays with the
consuming skill (thresholds are domain-calibrated).
"""
import re
import unicodedata


def norm(s):
    """Accent/case/punctuation-normalized text for scoring."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()
