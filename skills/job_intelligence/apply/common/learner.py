"""learner.py — Button intent classification for form actions.

ButtonIntentClassifier: Maps button text to action intent using heuristics.
"""

import re


# ── ButtonIntentClassifier ────────────────────────────────────────────────

class ButtonIntentClassifier:
    """Maps button text to action intent using cascading strategy.

    Strategy 1: Known exact matches (high confidence)
    Strategy 2: Word-level scoring
    Strategy 3: Regex fallback
    """

    _KNOWN = {
        "submit application":      ("submit", 1.0),
        "submit":                  ("submit", 0.95),
        "send application":        ("submit", 0.95),
        "send":                    ("submit", 0.85),
        "apply":                   ("submit", 0.85),
        "apply now":               ("submit", 0.85),
        "review":                  ("advance", 0.9),
        "continue":                ("advance", 0.9),
        "next":                    ("advance", 0.95),
        "save and continue":       ("advance", 0.85),
        "save & continue":         ("advance", 0.85),
        "done":                    ("advance", 0.7),
        "proceed":                 ("advance", 0.7),
        "back":                    ("back", 0.95),
        "previous":                ("back", 0.9),
        "go back":                 ("back", 0.85),
        "cancel":                  ("cancel", 0.95),
        "close":                   ("cancel", 0.85),
        "never mind":              ("cancel", 0.85),
    }

    _INTENT_WORDS = {
        "submit":  {"submit", "send", "apply", "finish", "complete"},
        "advance": {"next", "continue", "review", "proceed", "done"},
        "back":    {"back", "previous", "edit", "return"},
        "cancel":  {"cancel", "close", "discard", "never", "dismiss"},
    }

    _INTENT_SUBMIT_REGEX = re.compile(r'\b(submit|send|apply now?|finish|complete)\b', re.I)
    _INTENT_ADVANCE_REGEX = re.compile(r'\b(next|continue|review|proceed|save)\b', re.I)
    _INTENT_BACK_REGEX = re.compile(r'\b(back|previous|edit)\b', re.I)

    @classmethod
    def classify(cls, text):
        """Returns (intent, confidence). intent is 'submit' | 'advance' | 'back' | 'cancel' | 'unknown'."""
        text = text.strip().lower()

        # Strategy 1: Exact known match
        if text in cls._KNOWN:
            return cls._KNOWN[text]

        # Strategy 2: Word scoring
        scores = {"submit": 0, "advance": 0, "back": 0, "cancel": 0}
        for intent, words in cls._INTENT_WORDS.items():
            for w in words:
                if w in text:
                    scores[intent] += 1

        best = max(scores, key=scores.get)
        best_score = scores[best]
        if best_score >= 2:
            return best, 0.8
        if best_score == 1:
            return best, 0.5

        # Strategy 3: Regex fallback
        if cls._INTENT_SUBMIT_REGEX.search(text):
            return "submit", 0.6
        if cls._INTENT_ADVANCE_REGEX.search(text):
            return "advance", 0.6
        if cls._INTENT_BACK_REGEX.search(text):
            return "back", 0.6

        return "unknown", 0.0

    @classmethod
    def pick(cls, buttons, intent):
        """Pick the best button for a given intent from a list of candidate buttons."""
        scored = []
        for i, btn in enumerate(buttons):
            it, conf = cls.classify(btn["text"])
            if it == intent:
                scored.append((conf, i, btn))

        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        return {"index": scored[0][1], "text": scored[0][2]["text"], "confidence": scored[0][0]}
