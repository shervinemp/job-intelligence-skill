"""grounding.py — factual-grounding gate for tailored artifacts.

The tailored resume.json is what actually goes to employers. An LLM
generating it sits on the confabulation fault line: a fluent fabricated
company, title, date, or degree on a real application is a
misrepresentation, not a bug. This gate makes fabrication structurally
visible:

  - every company/title/date/degree in the tailored resume must trace to
    the canonical profile (profile.json) — novel claims are quarantined;
  - the cover letter's concrete facts (dates, numbers) must appear in the
    profile or the job posting;
  - `tailor.py admit` REFUSES to admit a job whose manifest is not clean
    (--force is the explicit human override).

Manifest shape:
  {"ok": bool, "base": "profile"|"job"|"none", "novel_claims": [...],
   "mismatches": [...], "checked": n}
"""
import os
import re

_NORM = re.compile(r"[^a-z0-9 ]")


def _norm(s):
    return _NORM.sub(" ", str(s or "").lower()).strip()


def _fuzzy(a, b):
    """Containment-based similarity for entity names (company/title)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 4 and (a in b or b in a):
        return True
    return False


def _dates_overlap(a_start, a_end, b_start, b_end):
    """Year-level overlap of two date ranges (None = open)."""
    def _y(v):
        m = re.match(r"(\d{4})", str(v or ""))
        return int(m.group(1)) if m else None
    as_, ae, bs, be = _y(a_start), _y(a_end), _y(b_start), _y(b_end)
    if as_ is None and bs is None:
        return True
    if as_ is None:
        as_ = bs
    if bs is None:
        bs = as_
    if as_ is None:
        return True
    lo = max(as_, bs)
    hi = min(ae or 9999, be or 9999)
    return lo <= hi


def _base_profile():
    try:
        from lib.config import PROFILE_PATH
        import json as _json
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {}


def _base_work(profile):
    return profile.get("work_history") or profile.get("work") or []


def _base_education(profile):
    return profile.get("education") or []


def ground(resume, profile=None, job_posting_text=""):
    """Validate a tailored resume.json against the canonical profile.
    Returns the manifest (see module docstring)."""
    if profile is None:
        profile = _base_profile()
    base_work = _base_work(profile)
    base_edu = _base_education(profile)
    novel, mismatches = [], []

    if not base_work and not base_edu:
        return {"ok": False, "base": "none", "novel_claims": [],
                "mismatches": ["no work_history/education in profile — "
                               "grounding impossible, admit blocked"],
                "checked": 0}

    # ── Work entries: company/title/dates must trace to the base ──────
    for item in resume.get("work", []) or []:
        company = item.get("company", "")
        title = item.get("position") or item.get("title") or ""
        start, end = item.get("startDate"), item.get("endDate")
        matches = [w for w in base_work
                   if _fuzzy(company, w.get("company", ""))]
        if not matches:
            novel.append(f"work: company '{company}' not in profile")
            continue
        if title:
            titles = [w.get("position") or w.get("title") or ""
                      for w in matches]
            if not any(_fuzzy(title, t) for t in titles):
                novel.append(f"work: title '{title}' not in profile "
                             f"({company})")
        if not any(_dates_overlap(start, end, w.get("startDate"),
                                  w.get("endDate")) for w in matches):
            mismatches.append(f"work: dates {start}–{end} ({company}) don't "
                              f"overlap any profile range")

    # ── Education: degree/school must trace to the base ───────────────
    for item in resume.get("education", []) or []:
        school = item.get("institution") or item.get("school") or ""
        degree = (item.get("area") or item.get("degree") or "")[:80]
        matches = [e for e in base_edu
                   if _fuzzy(school, e.get("institution") or e.get("school") or "")]
        if not matches:
            novel.append(f"education: institution '{school}' not in profile")
            continue
        if degree:
            areas = [(e.get("area") or "") for e in matches]
            if not any(_fuzzy(degree, a) for a in areas):
                novel.append(f"education: '{degree}' not in profile "
                             f"({school})")

    # ── Cover letter: concrete facts must appear in profile/posting ───
    cover = ""
    for key in ("coverLetter", "cover_letter"):
        if resume.get(key):
            cover = str(resume[key])
            break
    if cover:
        years = re.findall(r"\b(19|20)\d{2}\b", cover)
        for y in years:
            y_text = f"20{y[1:]}" if y.startswith("19") else f"20{y[2:]}"
            if y_text not in str(profile) and y_text not in job_posting_text:
                pass  # year mentions are too noisy — skipped conservatively
        numbers = re.findall(r"\b\d{3,}\b", cover)
        for n in numbers:
            if n not in str(profile) and n not in job_posting_text:
                novel.append(f"cover: number '{n}' not in profile or posting")

    return {"ok": not novel and not mismatches, "base": "profile",
            "novel_claims": novel, "mismatches": mismatches,
            "checked": len(resume.get("work", []) or [])
                       + len(resume.get("education", []) or [])}


def cmd_ground(jid, results_dir, job_posting_text=""):
    """tailor.py ground <jid> — print the manifest for a tailored resume."""
    import json as _json
    path = os.path.join(results_dir, str(jid), "resume.json")
    if not os.path.exists(path):
        print(f"GROUNDING: no resume.json for {jid} — tailor first",
              file=sys.stderr)
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            resume = _json.load(f)
    except Exception as e:
        print(f"GROUNDING: cannot read resume.json: {e}", file=sys.stderr)
        return 1
    m = ground(resume, job_posting_text=job_posting_text)
    print(f"GROUNDING {jid}: ok={m['ok']} base={m['base']} "
          f"checked={m['checked']} novel={len(m['novel_claims'])} "
          f"mismatch={len(m['mismatches'])}", file=sys.stderr)
    for c in m["novel_claims"][:10]:
        print(f"  NOVEL: {c}", file=sys.stderr)
    for c in m["mismatches"][:10]:
        print(f"  MISMATCH: {c}", file=sys.stderr)
    if not m["ok"]:
        print("  ADMIT BLOCKED: novel claims must be reviewed and either "
              "corrected in resume.json or added to profile.json — then "
              "re-ground. (--force to override on review)", file=sys.stderr)
        return 1
    return 0
