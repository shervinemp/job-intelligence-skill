"""act/history.py — Fill work-history/education entry rows from resume.json.

The tailoring stage already produces results/<jid>/resume.json (JSON
Resume format: work[] with name/position/startDate/endDate/summary,
education[] with institution/area/studyType). Greenhouse-style ATS forms
render ONE empty entry row with generic labels (Company name, Title,
Start date month/year, End date month/year, Description, School,
Degree, Discipline). This module maps those labels to the FIRST
work/education entry so the deterministic filler covers what previously
needed Skyvern.

Gap-fill only: existing answers (--answers / profile) win via the
setdefault merge in the caller. Never fills the "Current role" checkbox
(ambiguous) or entry rows beyond the first.
"""
import calendar
import json
import os
import re

from lib.config import RESULTS_DIR

_EDU_ROW_RE = re.compile(r"\b(school|university|college|institution)\b")
_DEGREE_RE = re.compile(r"\bdegree\b")
_DISCIPLINE_RE = re.compile(r"discipline|field of study|\bmajor\b|area of study")
_GRAD_RE = re.compile(r"\bgraduat")
_WORK_ROW_RE = re.compile(r"\b(company|employer)\b")
# Title fields are SHORT labels — a long question like "This position is
# required to work out of..." must never match.
_TITLE_LABELS = {"title", "job title", "position", "job position", "current title",
                 "what is your title"}
_DATE_MONTH_RE = re.compile(r"\bmonth\b")
_DATE_YEAR_RE = re.compile(r"\byear\b")
_START_RE = re.compile(r"\bstart\b|started|begin")
_END_RE = re.compile(r"\bend\b|ended|finish|graduat")
_DESC_RE = re.compile(r"description|responsibilities|what did you do|summary|duties")
_CURRENT_ROLE_RE = re.compile(r"current role|currently work|currently employed|present role")
# Question stems that must never take history answers. ("What did you do
# in this role?" is handled by _DESC_RE — it's a work-description prompt.)
_QUESTION_RE = re.compile(
    r"\b(do you|are you|will you|would you|is this|if you|have you|"
    r"please|required to|must you|should you|how (many|long)|have a|"
    r"may we|can we|in which|when did)\b")


def _load_entries(jid):
    """(work, education) lists from the tailored resume.json. Best-effort."""
    if not jid:
        return [], []
    try:
        with open(os.path.join(RESULTS_DIR, str(jid), "resume.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], []
    work = [w for w in (data.get("work") or [])
            if (w.get("name") or "").strip() or (w.get("position") or "").strip()]
    edu = [e for e in (data.get("education") or [])
           if (e.get("institution") or "").strip()]
    return work, edu


def _date_parts(date_str):
    """('January', '2021') from '2021-01' / '2021-01-15'. (None, None) if absent."""
    m = re.match(r"^(\d{4})(?:-(\d{2}))?", str(date_str or "").strip())
    if not m:
        return None, None
    month = ""
    if m.group(2):
        try:
            month = calendar.month_name[int(m.group(2))]
        except (ValueError, IndexError):
            month = ""
    return month, m.group(1)


def _merge_history_answers(fields, jid):
    """Map generic history/education labels to the first resume.json entry.

    Returns {field_label: value} for fields that CAN be answered from the
    resume. DOM order drives the entry index (first Company → work[0]).
    """
    if not jid or not fields:
        return {}
    work, edu = _load_entries(jid)
    if not work and not edu:
        return {}
    from apply.common.resolve import normalize

    out = {}
    work_row = 0        # current work entry (advances on each Company label)
    work_seen = 0       # how many Company rows emitted so far
    edu_row = 0
    edu_seen = 0
    for f in fields:
        label = (f.get("label") or "").strip()
        if not label:
            continue
        norm = normalize(label)
        if not norm:
            continue
        # Questions (eligibility, location, consent) must never be answered
        # from work-history data.
        if _QUESTION_RE.search(norm):
            continue

        # ── Current-role checkbox (coordinates with end-date fields) ─
        # A row with no endDate is the current role — checking it disables
        # the required end-date month/year fields on Greenhouse.
        if _CURRENT_ROLE_RE.search(norm):
            if not work:
                continue
            w = work[min(work_row, len(work) - 1)]
            if not (w.get("endDate") or "").strip():
                out[label] = "true"
            continue

        # ── Education rows ─────────────────────────────────────────
        # Degree/Discipline/Graduation fields belong to education even
        # though their labels don't mention school/university.
        if (_EDU_ROW_RE.search(norm) or _DEGREE_RE.search(norm)
                or _DISCIPLINE_RE.search(norm) or _GRAD_RE.search(norm)):
            if not edu:
                continue
            e = edu[min(edu_row, len(edu) - 1)]
            if _DEGREE_RE.search(norm):
                if (e.get("studyType") or "").strip():
                    out[label] = e["studyType"]
            elif _DISCIPLINE_RE.search(norm):
                if (e.get("area") or "").strip():
                    out[label] = e["area"]
            elif _DATE_MONTH_RE.search(norm) or _DATE_YEAR_RE.search(norm):
                src = e.get("endDate") or e.get("startDate")
                month, year = _date_parts(src)
                if _DATE_MONTH_RE.search(norm) and month:
                    out[label] = month
                elif _DATE_YEAR_RE.search(norm) and year:
                    out[label] = year
            else:
                if (e.get("institution") or "").strip():
                    out[label] = e["institution"]
                    edu_row = min(edu_seen, len(edu) - 1)
                    edu_seen += 1
            continue

        # ── Work-history rows (exclude education-keyworded fields) ─
        if _WORK_ROW_RE.search(norm) or norm in _TITLE_LABELS or _DESC_RE.search(norm):
            if not work:
                continue
            if _WORK_ROW_RE.search(norm):
                # Next Company label advances to the next entry row.
                work_row = min(work_seen, len(work) - 1)
                w = work[work_row]
                if (w.get("name") or "").strip():
                    out[label] = w["name"]
                    work_seen += 1
                continue
            w = work[min(work_row, len(work) - 1)]
            if norm in _TITLE_LABELS:
                if (w.get("position") or "").strip():
                    out[label] = w["position"]
            elif _DESC_RE.search(norm):
                if (w.get("summary") or "").strip():
                    out[label] = w["summary"][:1000]
            continue

        # ── Date parts inside work rows ────────────────────────────
        if _DATE_MONTH_RE.search(norm) or _DATE_YEAR_RE.search(norm):
            if not work:
                continue
            w = work[min(work_row, len(work) - 1)]
            is_start = _START_RE.search(norm) and not _END_RE.search(norm)
            is_end = _END_RE.search(norm) and not _START_RE.search(norm)
            if is_start:
                month, year = _date_parts(w.get("startDate"))
            elif is_end:
                # Current role (no end date) → leave unfilled (safe).
                if not (w.get("endDate") or "").strip():
                    continue
                month, year = _date_parts(w.get("endDate"))
            else:
                continue
            if _DATE_MONTH_RE.search(norm) and month:
                out[label] = month
            elif _DATE_YEAR_RE.search(norm) and year:
                out[label] = year

    return out
