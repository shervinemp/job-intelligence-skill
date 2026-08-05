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

THE PROFILE IS SEMI-IMMUTABLE. It is the canonical store of OBJECTIVE
facts — companies, dates, degrees, institutions, real publications. A
missing OBJECTIVE fact that is genuinely true (a real degree, a real
employer, a real paper) may be added so the claim becomes traceable.
What NEVER enters the profile is SUBJECTIVE content: framing (title
phrasing, how a project is described, keyword emphasis) resolves at the
RESUME layer (soften it or remove it) or via a conscious --force verdict
recorded in grounding_manifest.json. The profile is a fact store, not a
scratchpad for persuading the gate — writing a subjective gloss into it
to make a claim pass is backwards.

The gate is a DETECTOR, not a judge. Every claim carries a severity:
  - "material": employer, degree, institution, clearance/licence/patent/
    publication/certification. False = misrepresentation. admit refuses.
  - "figure": concrete numbers (7B, team size, scale). Verifiable; an
    interviewer can pin them down. admit refuses without an orchestrator
    verdict.
  - "framing": title phrasing, keyword emphasis, positioning. This is
    what tailoring is FOR — warn only, does not block.
The orchestrator (the strong LLM) renders the verdict on material/figure
claims; code only certifies.

Manifest shape:
  {"ok": bool, "blocked": bool, "base": "profile"|"none",
   "novel_claims": [...], "claims": [{"severity", "text"}, ...],
   "material": [...], "figure": [...], "framing": [...],
   "mismatches": [...], "checked": n}
"""
import os
import re
import sys

_NORM = re.compile(r"[^a-z0-9 ]")


def _norm(s):
    return _NORM.sub(" ", str(s or "").lower()).strip()


def _fuzzy(a, b):
    """Word-boundary containment similarity for entity names (company/title).

    C4: raw-substring containment is too loose — "AT" matches "Atlantic", a
    2-char name matches a longer unrelated one. Only a WHOLE-WORD containment
    (or full equality) counts, so "Acme" matches "Acme Corp" (same company)
    but not "Acme Corp" vs "SomeAcmeSuffix" (different entity)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4:
        # Word-boundary containment in either direction.
        if re.search(r"\b" + re.escape(a) + r"\b", b):
            return True
        if re.search(r"\b" + re.escape(b) + r"\b", a):
            return True
    return False


def _y(v):
    """Year (int) from ISO ('2021-03'), month-year ('March 2021'), or bare
    year — or None when unparseable. C3: an unparseable date must NOT count
    as overlapping; it becomes a suspicious gap, not a silent pass."""
    s = str(v or "").strip()
    m = re.match(r"(\d{4})", s)
    if m:
        return int(m.group(1))
    m = re.match(r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
                 r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
                 r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)[ ,]+(\d{4})", s, re.I)
    if m:
        return int(m.group(1))
    return None


def _dates_overlap(a_start, a_end, b_start, b_end):
    """Year-level overlap of two date ranges (None = open).

    C3: ranges whose dates are UNPARSEABLE do not overlap — they are a
    grounding gap (the claim's dates can't be traced), not a silent pass."""
    as_, ae, bs, be = _y(a_start), _y(a_end), _y(b_start), _y(b_end)
    # If either side's dates are entirely unparseable, we cannot confirm
    # overlap — fail toward review.
    if as_ is None and bs is None and ae is None and be is None:
        return False
    if as_ is None:
        as_ = bs
    if bs is None:
        bs = as_
    if as_ is None:
        return False
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


# Claim categories where a fabrication is materially harmful rather than
# merely embarrassing: legal status, credentials, and verifiable record.
_CREDENTIAL_PATTERNS = [
    (re.compile(r"\b(ts/sci|top secret|security clearance|clearance level|"
                r"public trust|nato secret)\b"), "security clearance"),
    (re.compile(r"\b(pmp|cissp|cpa|cfa|pe licen|professional engineer|"
                r"aws certified|azure certified|gcp certified|"
                r"certified kubernetes|comptia|itil)\b"), "certification"),
    (re.compile(r"\b(patent(s|ed)?\s+(no\.|number|granted|pending|filed)|"
                r"us\s?patent)\b"), "patent"),
    (re.compile(r"\b(neurips|icml|iclr|cvpr|siggraph|nature|science journal|"
                r"peer[- ]reviewed|published \d+ paper)\b"), "publication"),
    (re.compile(r"\b(phd|ph\.d|doctorate|mba|jd|md)\b"), "degree"),
    (re.compile(r"\b(licensed|registered nurse|bar admission|admitted to the bar)\b"),
     "licence"),
    (re.compile(r"\bteam of \d+|\bmanaged \d+ (engineer|people|report)"),
     "team size"),
]


def ground(resume, profile=None, job_posting_text=""):
    """Validate a tailored resume.json against the canonical profile.
    Returns the manifest (see module docstring).

    Claims carry a severity so the gate is a DETECTOR, not a judge:
      - "material": employer, degree, institution, clearance/licence/patent/
        publication/certification. False = misrepresentation. admit refuses.
      - "figure": concrete numbers (7B, team size, scale). Verifiable; an
        interviewer can pin them down. admit refuses without an orchestrator
        verdict.
      - "framing": title phrasing, keyword emphasis, positioning. This is
        what tailoring is FOR — warn only, does not block.
    The orchestrator (the strong LLM) renders the verdict on material/figure
    claims; code only certifies."""
    if profile is None:
        profile = _base_profile()
    base_work = _base_work(profile)
    base_edu = _base_education(profile)
    novel, mismatches = [], []
    claims = []

    if not base_work and not base_edu:
        return {"ok": False, "blocked": True, "base": "none",
                "novel_claims": [], "claims": [],
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
            claims.append({"severity": "material",
                           "text": f"work: company '{company}' not in profile"})
            continue
        if title:
            titles = [w.get("position") or w.get("title") or ""
                      for w in matches]
            if not any(_fuzzy(title, t) for t in titles):
                claims.append({"severity": "framing",
                               "text": f"work: title '{title}' not in profile "
                                       f"({company})"})
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
            claims.append({"severity": "material",
                           "text": f"education: institution '{school}' not in profile"})
            continue
        if degree:
            areas = [(e.get("area") or "") for e in matches]
            if not any(_fuzzy(degree, a) for a in areas):
                claims.append({"severity": "material",
                               "text": f"education: '{degree}' not in profile "
                                       f"({school})"})

    # ── Resume BULLETS: the claims a reader actually acts on ─────────
    #
    # Company/title/date grounding above proves the JOBS are real. It says
    # nothing about what the bullets assert, and the bullets are where a
    # tailoring LLM confabulates — it is handed an untrusted job
    # description ("we need someone with a clearance who led 40+
    # engineers") and asked to make the resume fit. Before this check, a
    # resume claiming an active TS/SCI clearance, a 45-person team and 12
    # NeurIPS papers passed the gate cleanly because the employer was
    # real. Falsely asserting a clearance or a licence on an application
    # is not a formatting bug.
    #
    # Conservative by construction: only HIGH-RISK claim categories and
    # concrete numbers are flagged, and a flag means "human reviews it"
    # (--force exists), not "discard".
    claim_text = []
    for item in resume.get("work", []) or []:
        claim_text.extend(str(h) for h in (item.get("highlights") or []))
        if item.get("summary"):
            claim_text.append(str(item["summary"]))
    for item in resume.get("projects", []) or []:
        claim_text.extend(str(h) for h in (item.get("highlights") or []))
        if item.get("description"):
            claim_text.append(str(item["description"]))
    if (resume.get("basics") or {}).get("summary"):
        claim_text.append(str(resume["basics"]["summary"]))

    # Grounded against the PROFILE ONLY — deliberately not the posting.
    # The posting is untrusted text written by someone else; letting it
    # satisfy a claim means a job ad that says "TS/SCI required" would
    # launder a fabricated clearance onto the resume. A claim about the
    # candidate can only be justified by the candidate's own record.
    haystack = str(profile).lower()
    for text in claim_text:
        low = text.lower()
        for pat, what in _CREDENTIAL_PATTERNS:
            m = pat.search(low)
            if m and m.group(0) not in haystack:
                claims.append({"severity": "material",
                               "text": f"claim: {what} — "
                                       f"'{text[:70]}' not in profile"})
                break
        # Bare integers, plus the scale suffixes inflation actually uses
        # ("200M users", "10x throughput", "1.5B requests").
        figures = set(re.findall(r"\b\d{3,}\b", low))
        figures |= {m.group(0) for m in re.finditer(
            r"\b\d+(?:\.\d+)?\s*(?:k|m|b|bn|x|million|billion|thousand)\b", low)}
        for n in sorted(figures):
            if n not in haystack:
                claims.append({"severity": "figure",
                               "text": f"claim: figure '{n}' not in profile — "
                                       f"'{text[:60]}'"})

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
                claims.append({"severity": "figure",
                               "text": f"cover: number '{n}' not in profile "
                                       f"or posting"})

    novel = [c["text"] for c in claims]
    material = [c for c in claims if c["severity"] == "material"]
    figure = [c for c in claims if c["severity"] == "figure"]
    blocked = bool(material or figure or mismatches)
    return {"ok": not blocked, "blocked": blocked, "base": "profile",
            "novel_claims": novel, "claims": claims,
            "material": [c["text"] for c in material],
            "figure": [c["text"] for c in figure],
            "framing": [c["text"] for c in claims
                        if c["severity"] == "framing"],
            "mismatches": mismatches,
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
          f"checked={m['checked']} material={len(m['material'])} "
          f"figure={len(m['figure'])} framing={len(m['framing'])} "
          f"mismatch={len(m['mismatches'])}", file=sys.stderr)
    for c in m["material"][:10]:
        print(f"  MATERIAL: {c}", file=sys.stderr)
    for c in m["figure"][:10]:
        print(f"  FIGURE: {c}", file=sys.stderr)
    for c in m["framing"][:10]:
        print(f"  FRAMING: {c}", file=sys.stderr)
    for c in m["mismatches"][:10]:
        print(f"  MISMATCH: {c}", file=sys.stderr)
    if m["ok"]:
        print("  OK — framing-only massaging rides; admit is open.", file=sys.stderr)
        return 0
    print("  REVIEW REQUIRED: material/figure claims or date mismatches. "
          "The orchestrator (LLM) renders the verdict. The profile is a "
          "semi-immutable store of OBJECTIVE facts — a real missing fact "
          "(degree, employer, publication) may be added; SUBJECTIVE/framing "
          "content never goes in. Resolve claims at the resume layer "
          "(soften/remove) or consciously override with --force (recorded in "
          "grounding_manifest.json).", file=sys.stderr)
    return 1
