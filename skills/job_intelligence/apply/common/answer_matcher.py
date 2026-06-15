"""answer_matcher.py — Thin wrapper around resolve.py for backward compatibility.
New code should import from resolve.py directly."""
from apply.common.resolve import resolution_for_fill, commit_resolutions, normalize, Resolution

def match_answer(label, answers=None, common_answers=None, profile=None, required=False):
    res = resolution_for_fill(label, profile or {}, answers_override=answers or {})
    return res.value
