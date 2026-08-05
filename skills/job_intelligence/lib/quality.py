"""Data-quality validators for the pipeline's inputs.

The fill engine is only as good as its data: profile.json (contact +
work history + answers) and the tailored resume.json. These validators
surface missing fields as actionable issues — the orchestrator's guide
for what to fix upstream instead of working around gaps at fill time.
"""


def validate_profile(p):
    """Missing/weak profile fields. Returns list of issue strings."""
    issues = []
    for key in ("first_name", "last_name", "email", "phone"):
        if not (p.get(key) or "").strip():
            issues.append(f"profile.{key} missing")
    if not (p.get("location") or "").strip():
        issues.append("profile.location missing (drives city/country answers)")

    wh = p.get("work_history") or []
    if not wh:
        issues.append(
            "profile.work_history missing — add employer entries so the "
            "history filler can fill Company/Title/date rows (see guide)")
    for i, w in enumerate(wh):
        if not (w.get("company") or "").strip():
            issues.append(f"profile.work_history[{i}].company missing")
        if not (w.get("position") or "").strip():
            issues.append(f"profile.work_history[{i}].position missing")
        if not (w.get("startDate") or "").strip():
            issues.append(f"profile.work_history[{i}].startDate missing")

    edu = p.get("education") or []
    for i, e in enumerate(edu):
        if not (e.get("institution") or "").strip():
            issues.append(f"profile.education[{i}].institution missing")
        if not (e.get("area") or "").strip():
            issues.append(f"profile.education[{i}].area missing")
    return issues


def validate_resume(r):
    """Missing fields in a tailored resume.json. Returns issue strings."""
    issues = []
    for i, w in enumerate(r.get("work") or []):
        if not (w.get("name") or "").strip():
            issues.append(f"resume.work[{i}].name missing (company) — tailoring "
                          "dropped it; strengthen tailor_prompt.md or add to profile.work_history")
        if not (w.get("position") or "").strip():
            issues.append(f"resume.work[{i}].position missing")
    for i, e in enumerate(r.get("education") or []):
        if not (e.get("institution") or "").strip():
            issues.append(f"resume.education[{i}].institution missing")
    return issues


GUIDE = """Profile data guide (profile.json):
  "work_history": [
    {"company": "Acme Corp", "position": "Senior Engineer",
     "startDate": "2021-03", "endDate": "2023-06"}   # omit endDate for current role
  ],
  "education": [
    {"institution": "University of Ottawa", "area": "Computer Science",
     "studyType": "MSc", "endDate": "2019-04"}
  ]
The history filler, date derivation, and check expectations all read these."""


# C3 — profile answer harmonization. Duplicate keys that mean the same thing
# (profile has both `gender` and `Gender Identity`, both `authorized_to_work`
# and `work_authorization`) are a latent drift risk: they can silently
# disagree later. This consolidates them.
import re as _re


def _answer_norm(label):
    return _re.sub(r"[^a-z0-9 ]", " ", (label or "").lower()).strip()


def harmonize_answers(profile):
    """Find answer keys that resolve to the same normalized meaning with the
    same value. Returns a list of groups, each:
        {"meaning": norm, "keys": [k1, k2, ...], "value": v, "canonical": k}
    The canonical key is the longest (most descriptive) label; the others are
    duplicates the orchestrator can drop or alias to it.

    Two keys are the SAME meaning when one's normalized form is a subset of
    the other's (gender ⊆ gender identity, authorized to work ⊆ work
    authorization — not equal, but equivalent for the same value)."""
    answers = profile.get("answers") or {}
    normed = [(k, v, _answer_norm(k)) for k, v in answers.items()
              if v not in (None, "")]
    groups = []
    used = set()
    for i, (ki, vi, ni) in enumerate(normed):
        if ki in used or not ni:
            continue
        group = [(ki, vi)]
        used.add(ki)
        for kj, vj, nj in normed:
            if kj in used or not nj:
                continue
            same_value = str(vi) == str(vj)
            subset = ni in nj or nj in ni
            if same_value and subset:
                group.append((kj, vj))
                used.add(kj)
        if len(group) >= 2:
            keys = [k for k, _ in group]
            canonical = max(keys, key=len)
            groups.append({"meaning": ni, "keys": keys,
                           "value": vi, "canonical": canonical})
    return groups


def alias_harmonized_answers(profile):
    """Merge harmonized duplicate keys into aliases so resolve() can find the
    answer under either key. Returns a dict of {non-canonical_key: canonical_key}
    the resolver can consult — no data is dropped, no value changes."""
    aliases = {}
    for g in harmonize_answers(profile):
        for k in g["keys"]:
            if k != g["canonical"]:
                aliases[k] = g["canonical"]
    return aliases


# C2 — profile-level contradiction detection. coherence.py checks FILLED FORM
# values; this checks the profile itself: two answers that cannot both be true
# (willing_to_relocate=Yes vs a fixed-location answer, visa No vs
# work_authorization No, etc.). Deterministic rules only; the orchestrator
# resolves what it finds.
def check_profile_contradictions(profile):
    """List of contradiction strings, or [] when consistent."""
    a = profile.get("answers") or {}
    out = []

    def _yes(v):
        return str(v or "").strip().lower().startswith("yes")

    def _no(v):
        return str(v or "").strip().lower().startswith("no")

    # Relocation vs fixed-location: any pair of relocation answers that
    # disagree (Yes vs No) is a contradiction. Compare across all keys that
    # mention relocation.
    rel_keys = [k for k in a if "relocat" in k.lower()]
    yes_rel = [k for k in rel_keys if _yes(a[k])]
    no_rel = [k for k in rel_keys if _no(a[k])]
    if yes_rel and no_rel:
        out.append(
            f"relocation answers conflict: {yes_rel[0]}={a[yes_rel[0]]!r} vs "
            f"{no_rel[0]}={a[no_rel[0]]!r}")
    # Visa sponsorship vs work authorization.
    auth = a.get("authorized_to_work") or a.get("work_authorization")
    sponsor = a.get("need_canada_sponsorship") or a.get("need_us_sponsorship")
    if auth and sponsor:
        if _yes(auth) and _yes(sponsor):
            # Authorized to work AND needs sponsorship is possible (e.g. an
            # open work permit expiring) — flag for review, not a hard error.
            out.append(
                f"authorized_to_work={auth!r} with need_sponsorship={sponsor!r} "
                f"— review (open work permit?)")
    # Commute vs remote preference.
    remote = a.get("remote_preference")
    if remote and _yes(a.get("willing_to_commute")) and _no(remote):
        out.append(
            f"willing_to_commute=Yes but remote_preference={remote!r}")
    # Years of experience vs work_history tenure.
    years = a.get("years_of_experience")
    wh = profile.get("work_history") or []
    if years and wh:
        total = 0
        import re as _re
        for w in wh:
            sd = _re.match(r"\d{4}", str(w.get("startDate") or ""))
            ed = _re.match(r"\d{4}", str(w.get("endDate") or ""))
            if sd:
                total += (int(ed.group(0)) if ed else 9999) - int(sd.group(0))
        try:
            yv = int(str(years).replace("+", "").strip())
        except Exception:
            yv = None
        if yv is not None and total and total < yv - 1:
            out.append(
                f"years_of_experience={years} but work_history sums to ~{total}")
    return out


# C5 — EEO preference clustering. Many profiles carry N separate
# "Prefer not to answer" entries (ethnicity, disability, indigenous, veteran,
# gender). A single preference ("always PNA on EEO") covers them all and is
# far easier to maintain. Detects the cluster; the orchestrator consolidates.
_EEO_KW = ("ethnic", "race", "veteran", "disabilit", "indigenous", "gender",
           "sexual orientation", "marital", "military")
_PNA = ("prefer not", "decline", "rather not")


def eeo_cluster(profile):
    """The set of EEO answer keys all set to a PNA-style value. Returns
    {"keys": [...], "preference": "Prefer not to answer"} when ≥2 match,
    else None."""
    a = profile.get("answers") or {}
    matched = []
    for k, v in a.items():
        kl = k.lower()
        if any(kw in kl for kw in _EEO_KW):
            vl = str(v or "").lower()
            if any(p in vl for p in _PNA):
                matched.append(k)
    if len(matched) < 2:
        return None
    return {"keys": matched,
            "preference": str(a[matched[0]])[:60]}
