"""lib/build_resume.py — Validate + build resume/cover PDFs from JSON Resume.

Usage:
    python -m lib.build_resume <resume.json> <output_dir>   # validate + build
    python -m lib.build_resume --validate <resume.json>      # validate only

Accepts standard JSON Resume schema (https://jsonresume.org/schema/) with
two custom extensions:
  _style: dict of formatting overrides (spacing, font sizes, margins)
  coverLetter: string (optional cover letter body)
"""

import json, os, re, sys
from fpdf import FPDF, XPos, YPos


def _clean_fn(s):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)

_DEFAULT_STYLE = {
    "font": "Helvetica",
    "font_size": 9,
    "title_font_size": 18,
    "section_font_size": 10.5,
    "header_font_size": 9.5,
    "small_font_size": 8.5,
    "section_spacing": 1.5,
    "bullet_spacing": 3.6,
    "line_height": 4,
    "page_margin": 10,
    "max_col_width": 190,
    "text_color": [0, 0, 0],
    "link_color": [0, 0, 255],
}


def _s(text):
    # fpdf2 core fonts are latin-1 only. Map the common typographic chars to ASCII,
    # then drop anything still outside latin-1 so the build can't crash on an
    # unexpected glyph (bullet, emoji, accented name from a pasted JD, etc.).
    text = (text.replace("\u2014", "-").replace("\u2013", "-")
                .replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2022", "-").replace("\u2026", "..."))
    return text.encode("latin-1", "replace").decode("latin-1")


class _ResumePDF(FPDF):
    def __init__(self, style):
        super().__init__()
        self.st = {**_DEFAULT_STYLE, **(style or {})}

    def header(self):
        pass

    def footer(self):
        pass

    def section_title(self, title):
        st = self.st
        self.set_font(st["font"], "B", st["section_font_size"])
        self.set_text_color(*st["text_color"])
        self.cell(0, st["line_height"], title.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")
        self.line(st["page_margin"], self.get_y(), 210 - st["page_margin"], self.get_y())
        self.ln(st["section_spacing"])

    def bullet_point(self, txt):
        st = self.st
        self.set_font(st["font"], "", st["font_size"])
        self.cell(3, st["bullet_spacing"], "", new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.cell(2, st["bullet_spacing"], "-", new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.multi_cell(st["max_col_width"], st["bullet_spacing"], txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def job_header(self, role, company, location, date):
        st = self.st
        self.set_font(st["font"], "B", st["header_font_size"])
        self.cell(0, st["line_height"], role, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")
        self.set_font(st["font"], "I", st["font_size"])
        loc_w = self.get_string_width(f" | {location}")
        cw = self.get_string_width(company)
        self.cell(cw, st["line_height"], company, new_x=XPos.RIGHT, new_y=YPos.TOP, align="L")
        self.cell(loc_w, st["line_height"], f" | {location}", new_x=XPos.RIGHT, new_y=YPos.TOP, align="L")
        self.cell(0, st["line_height"], date, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
        self.ln(0.3)

    def project_header(self, title, link=None):
        st = self.st
        self.set_font(st["font"], "B", st["header_font_size"])
        if link:
            w = self.get_string_width(title)
            self.set_text_color(*st["link_color"])
            self.cell(w, st["line_height"], title, new_x=XPos.RIGHT, new_y=YPos.TOP, link=link)
            self.set_text_color(*st["text_color"])
            self.cell(3, st["line_height"], " | ", new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.set_font(st["font"], "U", st["small_font_size"])
            self.set_text_color(*st["link_color"])
            self.cell(0, st["line_height"], link, new_x=XPos.LMARGIN, new_y=YPos.NEXT, link=link)
            self.set_text_color(*st["text_color"])
        else:
            self.cell(0, st["line_height"], title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.3)


class _CoverLetterPDF(FPDF):
    def __init__(self, style):
        super().__init__()
        self.st = {**_DEFAULT_STYLE, **(style or {})}

    def header(self):
        st = self.st
        self.set_font(st["font"], "B", 14)
        self.cell(0, 5, st.get("name", ""), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        self.set_font(st["font"], "", 10)
        addr = st.get("address", "")
        if addr:
            self.cell(0, 5, addr, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        contact_parts = [p for p in [st.get("email"), st.get("phone")] if p]
        if contact_parts:
            self.cell(0, 5, " | ".join(contact_parts), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        if st.get("portfolio"):
            self.set_text_color(*st.get("link_color", [0, 0, 255]))
            self.cell(0, 5, st["portfolio"], new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C",
                      link=st.get("portfolio_url", ""))
            self.set_text_color(*st.get("text_color", [0, 0, 0]))
        self.ln(10)

    def footer(self):
        pass


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_resume(pdf, data, company=None):
    """Render resume from JSON Resume format data."""
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    st = pdf.st
    basics = data.get("basics", {})

    # Name + label
    name = basics.get("name", "")
    label = basics.get("label", "")
    pdf.set_font(st["font"], "B", st["title_font_size"])
    pdf.cell(0, 6, name, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    if label:
        pdf.set_font(st["font"], "B", 11)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, label, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        pdf.set_text_color(*st["text_color"])
    pdf.ln(1)

    # Contact: email | phone | location
    loc = basics.get("location", {})
    addr = ", ".join(filter(None, [loc.get("city", ""), loc.get("region", "")]))
    email = basics.get("email", "")
    phone = basics.get("phone", "")
    contact_parts = [p for p in [email, phone, addr] if p]
    if contact_parts:
        pdf.set_font(st["font"], "", st["font_size"])
        pdf.cell(0, 4.5, " | ".join(contact_parts), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    # Links from profiles
    profiles = basics.get("profiles", [])
    if profiles:
        sep = "  |  "
        txts = [p.get("url", p.get("username", "")) for p in profiles]
        tw = pdf.get_string_width(sep.join(txts))
        pdf.set_x((210 - tw) / 2)
        pdf.set_font(st["font"], "", st["font_size"])
        for i, p in enumerate(profiles):
            t = p.get("url", p.get("username", ""))
            if i > 0:
                pdf.cell(pdf.get_string_width(sep), 4.5, sep, new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.set_text_color(*st["link_color"])
            pdf.cell(pdf.get_string_width(t), 4.5, t, new_x=XPos.RIGHT, new_y=YPos.TOP, link=p.get("url"))
            pdf.set_text_color(*st["text_color"])
        pdf.ln(2)
    else:
        pdf.ln(1.5)

    # Summary
    summary = basics.get("summary", "")
    if summary:
        pdf.section_title("Professional Summary")
        pdf.set_font(st["font"], "", st["font_size"])
        pdf.multi_cell(st["max_col_width"], st["line_height"], _s(summary), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1.5)

    # Skills
    skills = data.get("skills", [])
    if skills:
        pdf.section_title("Skills")
        for sk in skills:
            sk_name = sk.get("name", "")
            kw = sk.get("keywords", [])
            items = ", ".join(kw) if kw else ""
            pdf.set_font(st["font"], "B", st["font_size"])
            pdf.cell(32, st["line_height"], sk_name + ":", new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.set_font(st["font"], "", st["font_size"])
            pdf.multi_cell(158, st["line_height"], items, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1.5)

    # Work experience
    work = data.get("work", [])
    if work:
        pdf.section_title("Experience")
        for w in work:
            co = w.get("company", "")
            position = w.get("position", "")
            sd = (w.get("startDate") or "")[:7]
            ed = (w.get("endDate") or "")[:7]
            date_str = f"{sd} - {ed}" if sd or ed else ""
            pdf.job_header(position, co, w.get("location", ""), date_str)
            for h in w.get("highlights", []):
                pdf.bullet_point(_s(h))
            pdf.ln(1.5)

    # Projects
    projects = data.get("projects", [])
    if projects:
        pdf.section_title("Projects")
        for proj in projects:
            pdf.project_header(proj.get("name", ""), proj.get("url"))
            for h in proj.get("highlights", []):
                pdf.bullet_point(_s(h))
            pdf.ln(1.0)

    # Education
    education = data.get("education", [])
    if education:
        pdf.section_title("Education")
        for edu in education:
            inst = edu.get("institution", "")
            area = edu.get("area", "")
            study = edu.get("studyType", "")
            title = f"{study} {area}".strip() or area
            sd = (edu.get("startDate") or "")[:7]
            ed = (edu.get("endDate") or "")[:7]
            date_str = f"{sd} - {ed}" if sd or ed else ""
            pdf.job_header(title, inst, "", date_str)
            for c in edu.get("courses", []):
                pdf.bullet_point(c)
            pdf.ln(0.5)

    # Filename from data — use target company, fall back to most recent work entry
    name_slug = _clean_fn(name.replace(" ", "_"))
    target = company or (work[0].get("company", "") if work else "")
    company_slug = _clean_fn(target.replace(" ", "_"))
    label_slug = _clean_fn(label.replace(" ", "_")[:30]) if label else "Resume"
    fn = f"{name_slug}_{company_slug}_{label_slug}_Resume.pdf"
    pdf.output(fn)
    return os.path.abspath(fn)


def _build_cover_letter(pdf, data, company=None):
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    st = pdf.st
    pdf.set_font(st["font"], size=11)

    body = data.get("coverLetter") or data.get("cover_letter", "")
    if not body:
        return None

    pdf.multi_cell(0, 6, _s(body), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    basics = data.get("basics", {})
    work = data.get("work", [])
    target = company or (work[0].get("company", "") if work else "")
    company_slug = _clean_fn(target.replace(" ", "_"))
    name_slug = _clean_fn(basics.get("name", "Cover_Letter").replace(" ", "_"))
    fn = f"{name_slug}_{company_slug}_Cover_Letter.pdf" if company_slug else f"{name_slug}_Cover_Letter.pdf"
    pdf.output(fn)
    return os.path.abspath(fn)


def _load_schema():
    """Load JSON Resume schema. Returns dict or None."""
    import urllib.request
    url = "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json"
    cache = os.path.join(os.path.dirname(__file__), ".schema_cache.json")
    if os.path.exists(cache):
        try:
            return json.load(open(cache, encoding="utf-8"))
        except Exception:
            pass
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        schema = json.loads(resp.read().decode())
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(schema, f)
        return schema
    except Exception:
        return None


_SCHEMA = None  # lazy-loaded


def validate(data):
    """Validate resume data against JSON Resume schema + quality rules.

    Uses the official JSON Resume schema for structural validation, then
    applies custom quality checks on top.

    Returns: list of error dicts, each with "severity" ("error"|"warn"),
             "field", "message". Empty list = valid.
    """
    global _SCHEMA
    errors = []
    basics = data.get("basics", {})

    # Schema validation via official JSON Resume schema (type checks)
    if _SCHEMA is None:
        _SCHEMA = _load_schema()
    if _SCHEMA:
        try:
            import jsonschema
            # Strip custom extensions before schema validation (schema doesn't allow them)
            clean = {k: v for k, v in data.items() if k in ("$schema", "basics", "work", "education", "skills", "projects", "publications", "volunteer", "awards", "certificates", "interests", "languages", "references")}
            jsonschema.validate(clean, _SCHEMA)
        except ImportError:
            pass
        except jsonschema.ValidationError as e:
            path = " → ".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
            errors.append({"severity": "error", "field": path, "message": e.message.split("\n")[0]})

    # Required field checks (JSON Resume schema is permissive — doesn't enforce these)
    if not basics.get("name"):
        errors.append({"severity": "error", "field": "basics.name", "message": "Name is required"})
    if not basics.get("label"):
        errors.append({"severity": "error", "field": "basics.label", "message": "Job title is required"})
    if not data.get("work"):
        errors.append({"severity": "error", "field": "work", "message": "At least one work entry is required"})
    else:
        for i, w in enumerate(data.get("work", [])):
            if not w.get("company"):
                errors.append({"severity": "error", "field": f"work[{i}].company", "message": "Company is required"})
            if not w.get("position"):
                errors.append({"severity": "error", "field": f"work[{i}].position", "message": "Position is required"})

    # Quality checks (custom, not in schema)
    if not data.get("skills"):
        errors.append({"severity": "warn", "field": "skills", "message": "No skills section"})
    if not data.get("education"):
        errors.append({"severity": "warn", "field": "education", "message": "No education section"})



    summary = basics.get("summary", "")
    if summary and len(summary) < 100:
        errors.append({"severity": "warn", "field": "basics.summary", "message": "Summary very short (<100 chars)"})

    return errors


def validate_file(path):
    """Validate a resume JSON file. Prints results to stderr. Returns True if valid."""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"VALIDATION_ERROR: invalid JSON — {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(f"VALIDATION_ERROR: file not found — {path}", file=sys.stderr)
        return False

    errors = validate(data)
    if not errors:
        print("VALIDATION: PASS", file=sys.stderr)
        return True

    has_errors = any(e["severity"] == "error" for e in errors)
    for e in errors:
        label = "ERROR" if e["severity"] == "error" else "WARN"
        print(f"  VALIDATION_{label}: {e['field']} — {e['message']}", file=sys.stderr)

    if has_errors:
        print("VALIDATION: FAIL (fix errors above)", file=sys.stderr)
        return False
    print("VALIDATION: PASS (with warnings)", file=sys.stderr)
    return True


def build(data_path, output_dir, company=None):
    """Validate + build resume and cover letter PDFs from a JSON Resume data file.

    Args:
        data_path: Path to resume JSON
        output_dir: Directory to write PDFs into
        company: Target company name for filenames (falls back to most recent work entry)

    Returns: dict with keys "resume" and "cover" (paths to generated PDFs, or None),
             or None if validation fails.
    """
    if not validate_file(data_path):
        return None

    data = _read_json(data_path)
    cwd = os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    os.chdir(output_dir)

    try:
        rpdf = _ResumePDF(data.get("_style", {}))
        rpath = _build_resume(rpdf, data, company=company)
        basics = data.get("basics", {})
        cover_style = dict(data.get("_style", {}))
        cover_style.update(name=basics.get("name", ""), email=basics.get("email", ""), phone=basics.get("phone", ""),
                          address=", ".join(filter(None, [basics.get("location", {}).get("city", ""), basics.get("location", {}).get("region", "")])))
        profiles = basics.get("profiles", [])
        if profiles:
            cover_style["portfolio"] = profiles[0].get("url", "")
            cover_style["portfolio_url"] = profiles[0].get("url", "")
        cpdf = _CoverLetterPDF(cover_style)
        cpath = _build_cover_letter(cpdf, data, company=company)
        return {"resume": rpath, "cover": cpath}
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--validate":
        sys.exit(0 if validate_file(sys.argv[2]) else 1)
    elif len(sys.argv) >= 3:
        result = build(sys.argv[1], sys.argv[2])
        if result is None:
            sys.exit(1)
        print(f"Resume: {result['resume']}", file=sys.stderr)
        if result["cover"]:
            print(f"Cover: {result['cover']}", file=sys.stderr)
    else:
        print("Usage:", file=sys.stderr)
        print("  python -m lib.build_resume <resume.json> <output_dir>   # validate + build", file=sys.stderr)
        print("  python -m lib.build_resume --validate <resume.json>      # validate only", file=sys.stderr)
        sys.exit(1)
