"""Shared matching semantics for form fill.

One place for text normalization, option scoring, and semantic aliases —
used by BOTH option selection and selection verification, so the two can
never disagree (the lesson from the Country verify regression).

Scoring tiers (score_option):
  4  exact (normalized)
  3  prefix (either direction)
  2  contains / word-overlap ≥ 0.6 / signature (≥50% of the answer's
     content words present in the option; 100% for single-word answers)
  0  no match

Aliases bridge answers whose phrasing never matches the option text:
  "Male"      -> score "he him",     type "He / Him"
  "MSc"       -> score "master",     type "Master's"
The TYPE text must match the option label's raw text (react-select
filters on the raw label); the SCORE form is normalized.
"""
import re
import unicodedata

STOPWORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
             "for", "with", "i", "am", "is", "are", "be", "will", "would",
             "have", "has", "my", "you", "your", "this", "that", "it", "as",
             "by", "from", "if", "not", "no", "yes", "can", "do", "does",
             "we", "they", "there", "about", "so", "up", "out", "into"}

# (triggers_in_answer, score_norm, type_text)
ALIASES = [
    (("male",), "he him", "He / Him"),
    (("female",), "she her", "She / Her"),
    (("non binary", "nonbinary", "non-binary"), "they them", "They / Them"),
    (("prefer not", "prefer not to say", "rather not"), "prefer not", "Prefer not to say"),
    (("msc", "master of science", "master s degree", "masters degree"), "master", "Master's"),
    (("bsc", "bachelor of science", "bachelor s degree", "bachelors degree"), "bachelor", "Bachelor's"),
    (("phd", "doctor of philosophy"), "phd", "PhD"),
    (("mba",), "mba", "MBA"),
]


def norm(s):
    """Accent/case/punctuation-normalized text for scoring."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def content_words(norm_text):
    return {w for w in (norm_text or "").split()
            if w not in STOPWORDS and len(w) > 2}


def alias_norms(candidates_norm):
    """Extra normalized candidates from semantic aliases (for scoring)."""
    extra = []
    for cand in candidates_norm:
        for triggers, score_form, _type_text in ALIASES:
            if any(t in cand for t in triggers):
                extra.append(norm(score_form))
    return extra


def typing_candidates(ans):
    """Texts to type: full answer, yes/no-stripped, and alias type-forms
    that match the option labels' raw text."""
    s = str(ans or "").strip()
    out = [s]
    stripped = re.sub(r"^(yes|no)[,\s:;/\-–—]+", "", s, flags=re.I).strip()
    if stripped and stripped != s:
        out.append(stripped)
    an = norm(s)
    for triggers, _score_form, type_text in ALIASES:
        if any(t in an for t in triggers):
            out.append(type_text)
    return out


def score_option(text, candidates_norm):
    """0-4 (see module docstring)."""
    t = norm(text)
    if not t:
        return 0
    best = 0
    for cand in candidates_norm:
        if t == cand:
            return 4
        if t.startswith(cand) or cand.startswith(t):
            best = max(best, 3)
        elif cand in t or t in cand:
            best = max(best, 2)
    # Signature: the answer's content words present in the option.
    # Requires ≥2 shared words for multi-word answers (Aalborg University
    # shares only "university" with "University of Ottawa" → no match);
    # single-word answers match on exact containment ("MSc" → "MSc").
    for cand in candidates_norm:
        cw = content_words(cand)
        if cw:
            tw = set(t.split())
            shared = cw & tw
            if len(cw) == 1:
                if shared:
                    best = max(best, 2)
            elif len(shared) >= 2 and len(shared) / len(cw) >= 0.5:
                best = max(best, 2)
    # Word overlap.
    tw = set(t.split())
    for cand in candidates_norm:
        cw = set(cand.split())
        if cw and tw and len(tw & cw) / max(len(tw), len(cw)) >= 0.6:
            best = max(best, 2)
    return best


# Country names → ISO-2 codes for intl-tel country pickers, whose
# selection is only readable via the flag class (iti__ca = Canada).
COUNTRY_ISO = {
    "canada": "ca", "united states": "us", "usa": "us", "united states of america": "us",
    "australia": "au", "new zealand": "nz", "united kingdom": "gb", "uk": "gb",
    "england": "gb", "germany": "de", "france": "fr", "netherlands": "nl",
    "belgium": "be", "switzerland": "ch", "austria": "at", "italy": "it",
    "spain": "es", "portugal": "pt", "ireland": "ie", "sweden": "se",
    "norway": "no", "denmark": "dk", "finland": "fi", "poland": "pl",
    "czech republic": "cz", "india": "in", "brazil": "br", "mexico": "mx",
    "japan": "jp", "south korea": "kr", "singapore": "sg", "israel": "il",
}


def country_iso(answer):
    """ISO-2 code from a country answer ('Canada' -> 'ca'), or ''."""
    an = norm(answer)
    for name, iso in COUNTRY_ISO.items():
        if name in an or (len(name) <= 6 and an and an in name):
            return iso
    return ""


def scoring_candidates(candidates):
    """Full normalized candidate set (originals + aliases) for scoring."""
    cn = [norm(c) for c in candidates]
    return cn + alias_norms(cn)
