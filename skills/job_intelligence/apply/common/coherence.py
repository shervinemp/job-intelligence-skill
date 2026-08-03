"""coherence.py — deterministic cross-field contradiction checks.

Per-field verification can't see the application as a whole. These rules
catch pairs of answers that cannot both be true — the pipeline's
checksum, mirroring the LLM weakness of long-range self-contradiction
with code. Rules are deterministic, conservative, and unit-tested;
every finding is evidence-attached (both values + the rule that fired).

Rules (all conservative — a wrong finding is worse than a missed one):
  1. sponsorship ↔ authorization/visa
  2. city ↔ province/state
  3. pronouns ↔ gender

Input: filled field records [{label, answer, kind}...] from a fill run.
Output: [{left, right, rule, detail}] — empty when coherent.
"""
import re

# Known provinces/states — the only values a province/state field should
# hold. Everything else in such a field is either a city (wrong) or junk.
_CA_PROVINCES = {
    "alberta", "british columbia", "manitoba", "new brunswick",
    "newfoundland and labrador", "nova scotia", "ontario",
    "prince edward island", "quebec", "saskatchewan", "nunavut",
    "northwest territories", "yukon", "ab", "bc", "mb", "nb", "nl", "ns",
    "on", "pe", "qc", "sk", "nt", "nu", "yt",
}
_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "al", "ak", "az", "ar", "ca",
    "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky",
    "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv",
    "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
}
_KNOWN_REGIONS = _CA_PROVINCES | _US_STATES

_PRONOUN_FAMILIES = {
    "he him": "he", "she her": "she", "they them": "they",
    "xe xem": "xe", "ze hir": "ze", "ey em": "ey",
}
_GENDER_FAMILY = {
    "male": "he", "man": "he", "female": "she", "woman": "she",
    "nonbinary": "they", "non binary": "they", "genderqueer": "they",
    "genderfluid": "they", "agender": "they",
}


def _norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def check_coherence(fields):
    """fields: [{label, answer, ...}] — filled records. Returns a list
    of contradiction findings."""
    filled = [(f.get("label") or "", f.get("answer") or "")
              for f in fields if f.get("kind") in ("verified", "unverified")
              and f.get("answer")]
    findings = []

    # ── Rule 1: sponsorship ↔ authorization/visa ─────────────────────
    sponsor_ans = _find(filled, r"\bsponsor")
    auth_ans = _find(filled, r"\b(authorized to work|work authorization|"
                             r"legally eligible|visa status|citizenship)\b")
    if sponsor_ans and auth_ans:
        s = _norm(sponsor_ans[1])
        a = _norm(auth_ans[1])
        wants = s.startswith("yes")
        is_authorized = any(t in a for t in ("authorized", "citizen", "eligible", "pr"))
        requires = any(t in a for t in ("sponsor", "visa required", "requires"))
        if wants and is_authorized and not requires:
            findings.append({
                "left": sponsor_ans[0], "right": auth_ans[0],
                "rule": "sponsorship_vs_authorization",
                "detail": (f"'{sponsor_ans[1]}' but '{auth_ans[1]}' — "
                           f"if already authorized, sponsorship is not needed"),
            })
        elif not wants and requires:
            findings.append({
                "left": sponsor_ans[0], "right": auth_ans[0],
                "rule": "sponsorship_vs_authorization",
                "detail": (f"'{sponsor_ans[1]}' but '{auth_ans[1]}' — "
                           f"visa support requested contradicts 'no sponsorship'"),
            })

    # ── Rule 2: city ↔ province/state ────────────────────────────────
    city_ans = _find(filled, r"\bcity\b")
    prov_ans = _find(filled, r"\b(province|territory|state|region)\b")
    if city_ans and prov_ans:
        c, p = _norm(city_ans[1]), _norm(prov_ans[1])
        if c and p and c != p:
            if p not in _KNOWN_REGIONS:
                findings.append({
                    "left": prov_ans[0], "right": city_ans[0],
                    "rule": "province_must_be_region",
                    "detail": (f"'{prov_ans[1]}' is not a known province/state "
                              f"and differs from city '{city_ans[1]}'"),
                })
            elif c in _KNOWN_REGIONS:
                findings.append({
                    "left": city_ans[0], "right": prov_ans[0],
                    "rule": "city_must_be_city",
                    "detail": (f"'{city_ans[1]}' reads like a province/state, "
                              f"not a city (province field: '{prov_ans[1]}')"),
                })

    # ── Rule 3: pronouns ↔ gender ────────────────────────────────────
    gender_ans = _find(filled, r"\b(gender identity|gender)\b")
    if gender_ans:
        g = _norm(gender_ans[1]).split()[0]
        fam = _GENDER_FAMILY.get(g)
        if fam:
            for lbl, ans in filled:
                n = _norm(lbl)
                if n in _PRONOUN_FAMILIES and _PRONOUN_FAMILIES[n] != fam:
                    if _norm(ans).startswith("yes"):
                        findings.append({
                            "left": gender_ans[0], "right": lbl,
                            "rule": "pronoun_vs_gender",
                            "detail": (f"gender '{gender_ans[1]}' but pronoun "
                                      f"'{lbl}' selected — confirm intent"),
                        })
    return findings


def _find(filled, pattern):
    """First (label, answer) whose label matches the pattern."""
    for lbl, ans in filled:
        if re.search(pattern, lbl, re.IGNORECASE):
            return (lbl, ans)
    return None
