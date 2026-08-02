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
