"""lib/pdf_check.py — post-build PDF quality gate for tailored resumes.

The tailoring stage's most common failure is a resume that breaks the
one-page rule or renders overlapping/clipped text (long role + company +
location headers colliding in `_ResumePDF.job_header`, or a section that
pushes past the printable area). These are the two things a human notices
first and an employer rejects immediately.

This module inspects a generated PDF deterministically and returns a
structured report the pipeline can act on:

  check(path, max_pages=1) -> {
      "ok": bool,
      "pages": int,
      "max_pages": int,
      "page_overflow": bool,     # pages > max_pages
      "overlaps": [...],         # overlapping word pairs (text, area)
      "clipped": [...],          # words extending past the printable area
      "issues": [str],           # human-readable, one per finding
  }

Design constraints:
  - Deterministic and cheap (runs right after every build).
  - Uses pypdf for page count (fast) and pdfplumber for geometry (available
    in this environment). Both are already installed; import failures degrade
    to "no geometry checks" rather than raising.
  - Fail-closed on the one-page rule (page_overflow is authoritative);
    overlap/clipping are reported with their location so retry-with-feedback
    can name exactly what to fix.
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional


# Minimum overlap area (square points) before two words count as colliding.
# A single character is ~5-10 pt tall and ~4-8 pt wide; anything above this
# is a real collision, not a kerning/anti-alias artifact.
_MIN_OVERLAP_AREA = 12.0

# A word whose right edge passes within this many points of the page edge is
# considered clipped (fpdf2 cells don't wrap, so long headers push off).
_CLIP_EPS = 1.5


def page_count(path: str) -> int:
    """Number of pages in the PDF. Returns 0 on failure (caller treats as
    unverifiable)."""
    try:
        from pypdf import PdfReader
        with open(path, "rb") as f:
            return len(PdfReader(f).pages)
    except Exception:
        return 0


def _word_rect(w):
    return (w["x0"], w["top"], w["x1"], w["bottom"])


def _intersect_area(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def detect_overlaps(path: str, min_area: float = _MIN_OVERLAP_AREA) -> List[dict]:
    """Word-pairs whose bounding boxes intersect by more than min_area.

    Returns a list of {"a": text, "b": text, "area": float, "page": int}.
    Empty when pdfplumber is unavailable or the page has no extractable words.
    """
    out = []
    try:
        import pdfplumber
    except Exception:
        return out
    try:
        with pdfplumber.open(path) as pdf:
            for pi, page in enumerate(pdf.pages):
                words = page.extract_words()
                for i in range(len(words)):
                    ra = _word_rect(words[i])
                    for j in range(i + 1, len(words)):
                        rb = _word_rect(words[j])
                        area = _intersect_area(ra, rb)
                        if area > min_area:
                            out.append({
                                "a": words[i]["text"],
                                "b": words[j]["text"],
                                "area": round(area, 1),
                                "page": pi,
                            })
    except Exception:
        pass
    return out


def detect_clipping(path: str, eps: float = _CLIP_EPS) -> List[dict]:
    """Words whose boxes extend past the page's right or bottom printable edge.

    A long role/company/location header drawn with a non-wrapping cell will
    push off the right edge; a section at the page bottom can clip at the
    bottom. Returns [{"text", "edge", "page"}] (right/bottom/top/left).
    """
    out = []
    try:
        import pdfplumber
    except Exception:
        return out
    try:
        with pdfplumber.open(path) as pdf:
            for pi, page in enumerate(pdf.pages):
                W, H = page.width, page.height
                for w in page.extract_words():
                    if w["x1"] > W - eps:
                        out.append({"text": w["text"], "edge": "right", "page": pi})
                    elif w["x0"] < eps:
                        out.append({"text": w["text"], "edge": "left", "page": pi})
                    if w["bottom"] > H - eps:
                        out.append({"text": w["text"], "edge": "bottom", "page": pi})
                    elif w["top"] < eps:
                        out.append({"text": w["text"], "edge": "top", "page": pi})
    except Exception:
        pass
    return out


def check(path: str, max_pages: int = 1) -> dict:
    """Run the full quality gate on a generated resume PDF.

    Returns a dict with ok/pages/overlaps/clipped/issues. page_overflow is
    authoritative; overlap and clip findings are listed individually so the
    retry feedback can name the offending text.
    """
    pages = page_count(path)
    overlaps = detect_overlaps(path)
    clipped = detect_clipping(path)

    issues: List[str] = []
    if pages == 0:
        issues.append("PDF unreadable — could not count pages")
    elif pages > max_pages:
        issues.append(f"{pages} page(s) — must be <= {max_pages}")

    seen = set()
    for o in overlaps:
        key = (o["a"], o["b"], o["page"])
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            f"overlapping text on page {o['page'] + 1}: "
            f"{o['a']!r} x {o['b']!r} (area {o['area']})")

    for c in clipped:
        issues.append(
            f"text clipped at {c['edge']} edge on page {c['page'] + 1}: "
            f"{c['text']!r}")

    # Clip at a sane cap so a pathological page doesn't produce hundreds of
    # findings (each one becomes retry feedback).
    issues = issues[:25]

    return {
        "ok": not issues and pages > 0 and pages <= max_pages,
        "pages": pages,
        "max_pages": max_pages,
        "page_overflow": pages > max_pages,
        "overlaps": overlaps,
        "clipped": clipped,
        "issues": issues,
    }


def feedback_for(report: dict) -> str:
    """Turn a check report into human/LLM-facing retry feedback."""
    if not report["issues"]:
        return ""
    lines = ["The generated resume fails the PDF quality check:"]
    for i in report["issues"]:
        lines.append(f"- {i}")
    lines.append(
        "Fix by tightening the content to fit ONE page with no overlapping "
        "or clipped text: shorten highlights, trim the summary, reduce "
        "skills keywords, or drop the least relevant role. Never let text "
        "overlap.")
    return "\n".join(lines)


def check_file(path: str, max_pages: int = 1, label: str = "resume") -> dict:
    """check() + print PDF_CHECK lines to stderr (the orchestrator's signal
    contract, mirroring VALIDATION_* / GROUNDING_* lines). Returns the report."""
    report = check(path, max_pages=max_pages)
    if report["ok"]:
        print(f"PDF_CHECK: {label} OK — {report['pages']} page(s), "
              f"no overlap, no clipping", file=sys.stderr)
    else:
        print(f"PDF_CHECK: {label} FAIL", file=sys.stderr)
        for i in report["issues"]:
            print(f"  PDF_CHECK: {i}", file=sys.stderr)
    return report
