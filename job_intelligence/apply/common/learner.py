"""learner.py — Self-improving models for form filling.

Components:
    LabelRegistry: Maps observed label variants to canonical answer keys.
    ButtonIntentClassifier: Maps button text to action intent using heuristics + learning.
    SiteProfile: Per-domain learned behavior, persisted to JSON + LLM-readable markdown.
    LearnSession: Tracks a single learning pass.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

LEARNINGS_DIR = Path(os.path.expanduser("~")) / ".openclaw" / "learnings"
LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)


# ── LabelRegistry ──────────────────────────────────────────────────────────

class LabelRegistry:
    """Maps observed label variants to canonical answer keys.

    Self-approving: high-confidence matches auto-register.
    """

    _aliases = {}  # normalized_label → canonical_key
    _reverse = {}  # canonical_key → [normalized_label, ...]

    @classmethod
    def resolve(cls, label, knowledge_base=None):
        """Find canonical key for a label. Returns (key, confidence) or (None, 0)."""
        norm = cls._normalize(label)

        # Direct alias hit
        if norm in cls._aliases:
            return cls._aliases[norm], 1.0

        if not knowledge_base:
            return None, 0.0

        # Word overlap scoring
        words = cls._significant_words(norm)
        best_key, best_score = None, 0
        for key in knowledge_base:
            kw = cls._significant_words(cls._normalize(key))
            overlap = len(words & kw)
            if overlap >= 3:
                return key, 0.9
            if overlap > best_score:
                best_score = overlap
                best_key = key

        if best_score >= 2:
            return best_key, 0.7
        if best_score >= 1:
            return best_key, 0.5
        return None, 0.0

    @classmethod
    def register(cls, label, canonical_key, confidence=0.85):
        """Register a label→key mapping if confidence meets threshold."""
        if confidence >= 0.85:
            norm = cls._normalize(label)
            cls._aliases[norm] = canonical_key
            cls._reverse.setdefault(canonical_key, set()).add(norm)

    @classmethod
    def _normalize(cls, text):
        return re.sub(r'[^a-z0-9+#]+', ' ', text.lower()).strip()

    @classmethod
    def _significant_words(cls, text):
        STOP_WORDS = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of',
                      'with', 'and', 'or', 'is', 'are', 'do', 'does', 'will',
                      'would', 'have', 'has', 'you', 'your', 'please', 'select'}
        return {w for w in text.split() if len(w) > 2 and w not in STOP_WORDS}

    @classmethod
    def save(cls):
        path = LEARNINGS_DIR / "label_aliases.json"
        path.write_text(json.dumps({
            "aliases": cls._aliases,
            "reverse": {k: list(v) for k, v in cls._reverse.items()},
        }, indent=2))

    @classmethod
    def load(cls):
        path = LEARNINGS_DIR / "label_aliases.json"
        if path.exists():
            data = json.loads(path.read_text())
            cls._aliases = data.get("aliases", {})
            cls._reverse = {k: set(v) for k, v in data.get("reverse", {}).items()}


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
        "submit":  {"submit", "send", "apply"},
        "advance": {"next", "continue", "review", "proceed", "done"},
        "back":    {"back", "previous", "edit", "return"},
        "cancel":  {"cancel", "close", "discard", "never", "dismiss"},
    }

    _INTENT_SUBMIT_REGEX = re.compile(r'\b(submit|send|apply now?)\b', re.I)
    _INTENT_ADVANCE_REGEX = re.compile(r'\b(next|continue|review|proceed|save)\b', re.I)
    _INTENT_BACK_REGEX = re.compile(r'\b(back|previous|edit)\b', re.I)

    @classmethod
    def classify(cls, text):
        """Returns (intent, confidence). intent is 'submit' | 'advance' | 'back' | 'cancel' | 'unknown'."""
        raw = text
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

    @classmethod
    def record_ambiguity(cls, button_text, chosen_intent):
        """Record resolution of an ambiguous button for future learning."""
        path = LEARNINGS_DIR / "button_resolutions.json"
        history = []
        if path.exists():
            history = json.loads(path.read_text())
        history.append({
            "text": button_text,
            "chosen_intent": chosen_intent,
            "timestamp": datetime.now().isoformat(),
        })
        path.write_text(json.dumps(history[-100:], indent=2))  # keep last 100


# ── SiteProfile ────────────────────────────────────────────────────────────

class SiteProfile:
    """Per-domain learned behavior, persisted as JSON + LLM-readable markdown."""

    def __init__(self, domain):
        self.domain = domain
        self.first_seen = datetime.now().isoformat()
        self.last_seen = datetime.now().isoformat()
        self.best_strategy = None
        self.transitions = []
        self.page_range = [1, 10]
        self.widgets = set()
        self.has_eeo = False
        self.fill_count = 0
        self.last_success = None
        self.trusted = False

    @property
    def json_path(self):
        return LEARNINGS_DIR / f"{self.domain}.json"

    @property
    def md_path(self):
        return LEARNINGS_DIR / f"{self.domain}.md"

    def record_transition(self, from_page, button_text):
        self.transitions.append({
            "from": from_page,
            "button": button_text,
        })

    def record_widget(self, widget_type):
        self.widgets.add(widget_type)

    def record_page(self, field_count):
        self.fill_count += 1
        if len(self.transitions) + 1 > self.page_range[1]:
            self.page_range = (self.page_range[0], len(self.transitions) + 1)
        if len(self.transitions) + 1 < self.page_range[0]:
            self.page_range = (len(self.transitions) + 1, self.page_range[1])

    def save(self):
        self.last_seen = datetime.now().isoformat()
        data = {
            "domain": self.domain,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "best_strategy": self.best_strategy,
            "transitions": self.transitions,
            "page_range": list(self.page_range),
            "widgets": list(self.widgets),
            "has_eeo": self.has_eeo,
            "fill_count": self.fill_count,
            "last_success": self.last_success,
            "trusted": self.trusted,
        }
        self.json_path.write_text(json.dumps(data, indent=2))
        self._generate_markdown()

    @classmethod
    def load(cls, domain):
        path = LEARNINGS_DIR / f"{domain}.json"
        if not path.exists():
            return cls(domain)
        data = json.loads(path.read_text())
        profile = cls(domain)
        profile.first_seen = data.get("first_seen", profile.first_seen)
        profile.last_seen = data.get("last_seen", profile.last_seen)
        profile.best_strategy = data.get("best_strategy")
        profile.transitions = data.get("transitions", [])
        profile.page_range = tuple(data.get("page_range", [1, 10]))
        profile.widgets = set(data.get("widgets", []))
        profile.has_eeo = data.get("has_eeo", False)
        profile.fill_count = data.get("fill_count", 0)
        profile.last_success = data.get("last_success")
        profile.trusted = data.get("trusted", False)
        return profile

    def verify_match(self, observed_pages, observed_widgets):
        """Check if current page matches learned pattern. Returns bool."""
        page_ok = self.page_range[0] <= observed_pages <= self.page_range[1]
        widget_ok = observed_widgets <= self.widgets
        return page_ok and widget_ok

    def _dominant_button(self):
        if not self.transitions:
            return "unknown"
        from collections import Counter
        counts = Counter(t["button"] for t in self.transitions)
        return counts.most_common(1)[0][0]

    def _generate_markdown(self):
        """Generate LLM-readable site guide."""
        lines = [
            f"# {self.domain}",
            "",
            "## Navigation",
            f"- Pages: {self.page_range[0]}-{self.page_range[1]} (dynamic)",
            f"- Forward: \"{self._dominant_button()}\"",
            f"- Last page: Submit",
            f"- Transitions: {len(self.transitions)}",
        ]

        if self.transitions:
            lines.append("- Typical flow:")
            for t in self.transitions[:10]:
                lines.append(f"  - Page {t['from']+1}: Click \"{t['button']}\"")

        lines.extend([
            "",
            "## Widgets",
        ])
        for w in sorted(self.widgets):
            lines.append(f"- {w.replace('_', ' ').title()}")

        if self.has_eeo:
            lines.extend([
                "",
                "## Edge Cases",
                "- EEO/Demographics page may appear (conditional)",
            ])

        lines.extend([
            "",
            "---",
            f"*Learned: {self.last_seen}*",
            f"*Fills: {self.fill_count}*",
            f"*Trusted: {self.trusted}*",
        ])

        self.md_path.write_text("\n".join(lines))


# ── LearnSession ───────────────────────────────────────────────────────────

_SKIP_DOMAINS = {"linkedin.com", "linkedin.com/jobs", "indeed.com",
                 "ca.indeed.com", "indeed.ca", "glassdoor.com",
                 "monster.com", "ziprecruiter.com", "simplyhired.com"}


def _is_aggregator(domain):
    """Check if a domain is a job aggregator (not an ATS to learn from)."""
    for skip in _SKIP_DOMAINS:
        if skip in domain:
            return True
    return False


class LearnSession:
    """Tracks a single learning pass across a form fill cycle.

    Usage:
        session = LearnSession(domain, jid)
        # ... fill pages ...
        session.record_page(field_count)
        session.record_transition(button_text)
        # ... on submit gate ...
        session.complete(submit_reached=True)

    Skips aggregator domains (LinkedIn, Indeed, etc.) — only learns ATS/employer sites.
    """

    def __init__(self, domain, jid=None):
        self.domain = domain
        self.jid = jid
        self._active = not _is_aggregator(domain)
        self.page_count = 0
        self.transitions = []
        self.widgets = set()
        self.field_counts = []
        self.has_eeo = False
        self.submit_reached = False

    def record_page(self, fields, buttons):
        if not self._active:
            return
        self.page_count += 1
        self.field_counts.append(len(fields))
        for f in fields:
            tag = f.get("tag", "")
            if tag == "DROPDOWN":
                self.widgets.add("custom_dropdown")
            elif tag == "IFRAME":
                self.widgets.add("iframe")
            elif f.get("role") == "combobox":
                self.widgets.add("autocomplete")
            elif f.get("type") == "file":
                self.widgets.add("file_upload")

    def record_transition(self, button_label):
        if not self._active:
            return
        self.transitions.append({
            "from": self.page_count - 1,
            "button": button_label,
        })

    def detect_eeo(self, page):
        if not self._active:
            return
        text = page.evaluate("() => document.body.innerText || ''").lower()
        eeo_signals = ["race", "ethnicity", "veteran", "disability", "gender identity",
                       "eeo", "equal opportunity", "demographic"]
        self.has_eeo = any(s in text for s in eeo_signals)

    def complete(self, submit_reached=False, profile=None):
        """Save learning results. No submission occurs in this session."""
        if not self._active:
            return
        self.submit_reached = submit_reached

        # Update or create SiteProfile
        site = SiteProfile.load(self.domain)
        site.last_seen = datetime.now().isoformat()
        site.widgets |= self.widgets
        site.has_eeo = site.has_eeo or self.has_eeo
        site.fill_count += 1

        for t in self.transitions:
            site.record_transition(t["from"], t["button"])

        site.save()

        # Print learning summary
        print(f"\nLEARNED: {self.domain}", file=__import__('sys').stderr)
        print(f"  Pages: {self.page_count}", file=__import__('sys').stderr)
        print(f"  Transitions: {[t['button'] for t in self.transitions]}", file=__import__('sys').stderr)
        print(f"  Widgets: {sorted(self.widgets)}", file=__import__('sys').stderr)
        if self.has_eeo:
            print(f"  EEO page detected (conditional)", file=__import__('sys').stderr)
        print(f"  Guide: {site.md_path}", file=__import__('sys').stderr)
        if submit_reached:
            print(f"  Run with --trust to enable auto-submit on {self.domain}", file=__import__('sys').stderr)
