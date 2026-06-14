"""answer_matcher.py — Label→value resolution with cascading strategy.
Consolidates _find_answer (act.py) and resolve_label (page_helpers.py).
"""

import re

def normalize(label):
    """Normalize a field label for comparison."""
    return re.sub(r'[^a-z0-9+#]+', ' ', (label or '').lower()).strip()


def match_answer(label, answers=None, common_answers=None, profile=None, required=False):
    """Resolve a field label to an answer value.

    Conservative strategy — code handles exact matches only.
    All ambiguity (prefix/word-overlap) defers to the LLM at ~50 tokens.

    Priority:
      1. --answers: exact match only (user typed these keys)
      2. common_answers: exact match only
      3. profile: exact key match, plus "full name" special case

    Returns value or None.
    """
    norm = normalize(label)
    if not norm:
        return None

    # 1. --answers: exact match only
    if answers:
        for k, v in answers.items():
            kn = normalize(k)
            if kn and kn == norm:
                return v

    # 2. common_answers: exact match only
    if common_answers:
        for ck, cv in common_answers.items():
            if not cv:
                continue
            kn = normalize(ck.replace('_', ' '))
            if kn == norm:
                return cv

    # 3. profile: exact key match + "full name" special case
    if profile:
        return _match_profile(norm, profile)

    return None


def _match_profile(norm, profile):
    """Match a normalized label against profile keys. Exact match only + name special case.
    Word-overlap fallback: if exactly one profile key shares a word with the label, use it.
    """
    fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
    if norm in ("full name", "name", "your name"):
        return f"{fn} {ln}" if fn and ln else fn or ln or None

    for pk, pv in profile.items():
        if not pv or not isinstance(pv, str) or len(pv) < 2:
            continue
        pn = normalize(pk.replace('_', ' '))
        if pn == norm:
            return pv

    # Word-overlap fallback: find profile keys sharing a word with the label
    label_words = set(w for w in norm.split() if len(w) > 2)
    if label_words:
        candidates = []
        for pk, pv in profile.items():
            if not pv or not isinstance(pv, str) or len(pv) < 2:
                continue
            pn = normalize(pk.replace('_', ' '))
            key_words = set(w for w in pn.split() if len(w) > 2)
            if key_words & label_words:
                candidates.append((pk, pv))
        if len(candidates) == 1:
            return candidates[0][1]

    return None
