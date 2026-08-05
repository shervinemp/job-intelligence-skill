"""lib/db/jobs.py — job CRUD and parsing helpers."""

import hashlib
import json
import re
import sys
from datetime import datetime

from .schema import _JOBS_COLS, get_conn


def _row_to_job(r):
    d = dict(r)
    if isinstance(d.get("scripts"), str):
        try:
            d["scripts"] = json.loads(d["scripts"])
        except (json.JSONDecodeError, TypeError):
            d["scripts"] = []
    return d


def _insert_job(jid, replace=False, commit=True, **fields):
    conn = get_conn()
    now = fields.pop("updated_at", datetime.now().isoformat())
    scripts = fields.pop("scripts", [])
    if isinstance(scripts, list):
        scripts = json.dumps(scripts)
    fields.setdefault("created_at", now)
    fields["updated_at"] = now
    fields["scripts"] = scripts or "[]"
    fields.setdefault("state", "active")
    fields.setdefault("stage", "extracted")
    if "title" in fields:
        fields["title"] = _whitespace_normalize(fields["title"])
    if "company" in fields:
        fields["company"] = _whitespace_normalize(fields["company"])
    fields["id"] = jid
    cols = ", ".join(fields.keys())
    qs = ", ".join("?" for _ in fields)
    cmd = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
    conn.execute(f"{cmd} jobs ({cols}) VALUES ({qs})", list(fields.values()))
    if commit:
        conn.commit()


def _whitespace_normalize(s):
    """Collapse all whitespace (tabs, newlines, etc) to single spaces and strip."""
    return re.sub(r'\s+', ' ', str(s)).strip() if s else (s or "")


def _normalize_url(url):
    """Normalize URL for consistent hashing. Strips ad-tracking params only, preserves routing params."""
    if not url:
        return url
    import urllib.parse

    try:
        parsed = urllib.parse.urlparse(url)
        tracked = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
            "ref",
            "source",
            "trk",
            "lipi",
            "email_id",
            "eid",
            "refid",
            "pos",
            "ao",
            "s",
            "guid",
            "src",
            "gh_src",
        }
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        clean_qs = {k: v for k, v in qs.items() if k.lower() not in tracked}
        clean_query = urllib.parse.urlencode(clean_qs, doseq=True) if clean_qs else ""
        clean_path = parsed.path.rstrip("/") or "/"
        return urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc.lower(), clean_path, "", clean_query, "")
        )
    except Exception:
        return url.strip("/")


def _sql_norm_col(col):
    """SQLite expression that normalizes whitespace like _whitespace_normalize.
       Replaces tabs, newlines, carriage returns with space, collapses multiple,
       then strips."""
    # The '  '->' ' collapse is applied repeatedly: SQLite's REPLACE is a
    # single non-overlapping pass, so one application turns 'a   b' into
    # 'a  b' (not 'a b') and the comparison against Python's fully
    # collapsed re.sub(r'\s+') would silently miss the match.
    inner = f"REPLACE(REPLACE(REPLACE({col}, CHAR(9), ' '), CHAR(10), ' '), CHAR(13), ' ')"
    for _ in range(4):
        inner = f"REPLACE({inner}, '  ', ' ')"
    return f"LOWER(TRIM({inner}))"


_ABBREV = {
    "sr": "senior", "sr.": "senior",
    "jr": "junior", "jr.": "junior",
    "sw": "software", "sde": "software development engineer",
    "eng": "engineer", "mgr": "manager", "dev": "developer",
    "dept": "department", "dir": "director",
    "ml": "machine learning", "ai": "artificial intelligence",
    "ui": "user interface", "ux": "user experience",
    "fe": "frontend", "be": "backend",
    "infra": "infrastructure", "admin": "administrator",
    "db": "database",
}


def _expand_abbrev(text):
    """Expand common abbreviations in a title string."""
    words = _whitespace_normalize(text).lower().split()
    expanded = [_ABBREV.get(w, w) for w in words]
    return " ".join(expanded)


# Tokens that MAKE two roles different even when everything else matches.
# "Senior Software Engineer" vs "Software Engineer" at the same company are
# two distinct postings a candidate may want both of; collapsing them
# silently discards a real opportunity.
_DISTINGUISHING_TOKENS = {
    "senior", "staff", "principal", "lead", "head", "director", "manager",
    "junior", "intern", "internship", "co-op", "coop", "associate",
    "apprentice", "graduate", "contract", "temporary", "part-time",
    "i", "ii", "iii", "iv", "v", "1", "2", "3",
}


def _seniority_conflict(a, b):
    """True when the two titles disagree on a distinguishing token."""
    at = set(_expand_abbrev(a).split()) & _DISTINGUISHING_TOKENS
    bt = set(_expand_abbrev(b).split()) & _DISTINGUISHING_TOKENS
    return at != bt


def _token_overlap(a, b, threshold=0.6):
    """Token overlap ratio after abbreviation expansion."""
    if not a or not b:
        return 0.0
    a_tokens = set(_expand_abbrev(a).split())
    b_tokens = set(_expand_abbrev(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))


def find_duplicate(jid, title, company, conn=None):
    """Find an existing active job with the same title+company (different JID).

    Returns the duplicate job row (id, stage) or None.
    Prefers jobs in more advanced stages (applied > tailored > described > extracted).

    Phase 1: exact match after SQL-level whitespace normalization.
    Phase 2: token-overlap fallback (same company, similar title).
    """
    if not title or not company:
        return None
    if conn is None:
        conn = get_conn()
    norm_title = _whitespace_normalize(title).lower()
    norm_company = _whitespace_normalize(company).lower()

    # Phase 1: exact match after full whitespace normalization
    sn = _sql_norm_col
    row = conn.execute(
        f"SELECT id, stage, title, company FROM jobs "
        f"WHERE id != ? AND state = 'active' "
        f"AND title IS NOT NULL AND company IS NOT NULL "
        f"AND {sn('title')} = ? AND {sn('company')} = ? "
        f"ORDER BY CASE stage "
        f"  WHEN 'applied' THEN 0 "
        f"  WHEN 'tailored' THEN 1 "
        f"  WHEN 'described' THEN 2 "
        f"  ELSE 3 END "
        f"LIMIT 1",
        (jid, norm_title, norm_company),
    ).fetchone()
    if row:
        return {"id": row["id"], "stage": row["stage"]}

    # Phase 2: token-overlap fallback — same company, similar title
    sn_c = _sql_norm_col("company")
    rows = conn.execute(
        f"SELECT id, stage, title, company FROM jobs "
        f"WHERE id != ? AND state = 'active' "
        f"AND title IS NOT NULL AND company IS NOT NULL "
        f"AND {sn_c} = ?",
        (jid, norm_company),
    ).fetchall()
    # Fuzzy phase is deliberately CONSERVATIVE: a false positive here
    # silently discards a real posting (add_job returns None and the
    # caller has nothing to report), which is strictly worse than
    # carrying a duplicate the operator can reject in one command.
    # 0.6 collapsed "Senior Software Engineer" into "Software Engineer";
    # 0.85 + a seniority veto keeps genuinely distinct roles apart.
    # B1: 0.70–0.85 with no seniority conflict is the AMBIGUOUS gray band —
    # not a confident duplicate, not clearly distinct. Return it tagged so
    # the caller surfaces a decision instead of silently accepting a real
    # duplicate OR silently dropping a real role.
    for r in rows:
        if _seniority_conflict(title, r["title"]):
            continue
        ov = _token_overlap(title, r["title"])
        if ov >= 0.85:
            return {"id": r["id"], "stage": r["stage"]}
        if 0.70 <= ov < 0.85:
            return {"id": r["id"], "stage": r["stage"], "ambiguous": True,
                    "overlap": round(ov, 2)}
    return None


def add_job(job_data, skip_known=False):
    """Insert a new job. Returns jid on insert, existing jid if URL known,
    None if blocked by title+company dedup.

    With skip_known=True, returns None for URL-known jobs (avoids
    wasted work in scrapers that don't need to re-fetch descriptions).
    Notes are still updated on URL-known jobs regardless of skip_known.
    """
    from .companies import company_upsert
    conn = get_conn()
    raw_url = job_data.get("url", "")
    url = _normalize_url(raw_url)
    jid = hashlib.md5(url.encode()).hexdigest()[:16] if url else None
    if not jid:
        return None
    if conn.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
        if "notes" in job_data:
            conn.execute(
                "UPDATE jobs SET notes=?, updated_at=? WHERE id=?",
                (job_data["notes"], datetime.now().isoformat(), jid),
            )
            conn.commit()
        if skip_known:
            return None
        return jid

    title = job_data.get("title", "")
    company = job_data.get("company", "")
    if title and company:
        dup = find_duplicate(jid, title, company, conn)
        if dup:
            if dup.get("ambiguous"):
                # B1: gray-band — not confident enough to skip, but close
                # enough to flag. Insert the job and surface the decision so
                # a human/orchestrator resolves same-role-vs-distinct.
                print(f"DUPLICATE_AMBIGUOUS: {title[:50]} @ {company[:30]} "
                      f"— close to {dup['id']} (overlap={dup.get('overlap')}); "
                      f"review: extract.py reject {jid} if same role",
                      file=sys.stderr)
            else:
                # SAY SO. `None` is also the return for "no url", and callers
                # collapse both into `if jid:` — so a dedup decision used to
                # discard a posting with no trace anywhere. A dropped job the
                # operator never hears about is indistinguishable from a job
                # that was never in the email.
                print(f"DUPLICATE_SKIPPED: {title[:50]} @ {company[:30]} "
                      f"— matches existing job {dup['id']} (stage={dup['stage']}); "
                      f"url={url[:80]}", file=sys.stderr)
                return None

    _insert_job(jid,
        email_id=job_data.get("email_id", ""),
        title=job_data.get("title", ""),
        company=job_data.get("company", ""),
        location=job_data.get("location", ""),
        url=url,
        salary=job_data.get("salary"),
        salary_min=_parse_salary_min(job_data.get("salary", "")),
        salary_max=_parse_salary_max(job_data.get("salary", "")),
        salary_currency=_parse_salary_currency(job_data.get("salary", "")),
        remote_status=_parse_remote_status(job_data.get("location", ""), job_data.get("title", "")),
        job_type=job_data.get("job_type", "Full-Time"),
        department=job_data.get("department", ""),
        source=job_data.get("source", ""),
        source_url=job_data.get("source_url", ""),
        stage="extracted",
        fit_score=job_data.get("fit_score"),
        fit_summary=job_data.get("fit_summary"),
        company_vibe=job_data.get("company_vibe"),
        scripts=job_data.get("scripts", []),
        response_path=job_data.get("response_path"),
        notes=job_data.get("notes", ""),
        category=job_data.get("category"),
    )
    company_name = job_data.get("company", "")
    if company_name and company_name != "Unknown":
        try:
            company_upsert(company_name)
        except Exception:
            pass
    return jid


def record_failure(jid, reason, detail=""):
    """Record a structured failure reason for a job.

    NOTE: currently has no callers. The probe router's own
    `record_failure` (apply/common/observations.py) is a different
    2-argument function — the name collision is why this one looked used.
    Kept because `advance(..., state='failed')` + an event is the correct
    shape for structured failure recording and the failure paths in
    enrich/tailor should route through it rather than writing `error`
    strings directly.
    """
    from .events import event_add
    from .pipeline import advance  # circular (pipeline→jobs) — keep lazy
    job = get_job(jid)
    if not job:
        return
    advance(job, job.get("stage"), state="failed", error=f"{reason}: {detail}".strip()[:500])
    event_add(jid, "failure", f"Failed: {reason}", description=detail)


def failure_stats():
    """Aggregate failure reasons across failed jobs."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT error, COUNT(*) as cnt FROM jobs "
        "WHERE state='failed' AND error IS NOT NULL AND error != '' "
        "GROUP BY error ORDER BY cnt DESC LIMIT 20"
    ).fetchall()
    return [{"error": r["error"], "cnt": r["cnt"]} for r in rows]


def advance_job(jid, new_stage, **updates):
    conn = get_conn()
    sets = ["stage=?", "updated_at=?"]
    vals = [new_stage, datetime.now().isoformat()]
    for k, v in updates.items():
        if k == "scripts" and isinstance(v, list):
            v = json.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(jid)
    # Retry on SQLite locked with backoff
    import time as _time

    for attempt in range(3):
        try:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit()
            return
        except Exception as e:
            if "database is locked" in str(e).lower() and attempt < 2:
                _time.sleep(0.1 * (attempt + 1))
                continue
            raise


def get_job(jid):
    """Fetch a job by id — accepts the full 16-hex id OR the 12-char
    prefix that logs/transcripts display (identity must survive
    truncation; see terms.trunc: display-only truncation is never an
    identity boundary).

    The prefix path is deliberately strict:
      * only hex prefixes resolve. '%' and '_' are LIKE WILDCARDS, so an
        unescaped prefix let `get_job('%')` return an arbitrary job;
      * an AMBIGUOUS prefix resolves to nothing rather than to whichever
        row SQLite happened to return first. Silently picking one of two
        jobs is how the wrong application gets submitted.
    """
    conn = get_conn()
    r = conn.execute(f"SELECT {_JOBS_COLS} FROM jobs WHERE id=?", (jid,)).fetchone()
    if r is None:
        s = str(jid)
        if not s or len(s) >= 16 or not all(c in "0123456789abcdef" for c in s.lower()):
            return None
        rows = conn.execute(
            f"SELECT {_JOBS_COLS} FROM jobs WHERE id LIKE ? ESCAPE '\\' LIMIT 2",
            (s + "%",)
        ).fetchall()
        if len(rows) != 1:
            if len(rows) > 1:
                print(f"AMBIGUOUS_JID: '{s}' matches multiple jobs — "
                      f"use the full 16-hex id", file=sys.stderr)
            return None
        r = rows[0]
    return _row_to_job(r) if r else None


def get_jobs_by_stage(stage):
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE stage=? AND state='active' ORDER BY created_at", (stage,)
    ).fetchall()
    return [(r["id"], _row_to_job(r)) for r in rows]


def next_pending_job():
    conn = get_conn()
    r = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE stage='described' AND state='active' LIMIT 1"
    ).fetchone()
    return (r["id"], _row_to_job(r)) if r else (None, None)


def get_failed_jobs():
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE state='failed' ORDER BY created_at"
    ).fetchall()
    return [(r["id"], _row_to_job(r)) for r in rows]


def search_jobs(query, stage=None, limit=50):
    conn = get_conn()
    clauses = []
    params = []
    if query:
        clauses.append("(title LIKE ? OR company LIKE ? OR location LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q, q])
    if stage:
        clauses.append("stage=?")
        params.append(stage)
    where = " AND ".join(clauses) if clauses else "1"
    rows = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    return [(_row_to_job(r)) for r in rows]


def job_count():
    return get_conn().execute("SELECT COUNT(*) as c FROM jobs").fetchone()["c"]


def job_count_by_stage():
    rows = (
        get_conn()
        .execute(
            "SELECT stage, COUNT(*) as cnt FROM jobs WHERE state='active' GROUP BY stage ORDER BY stage"
        )
        .fetchall()
    )
    return {r["stage"]: r["cnt"] for r in rows}


def _parse_salary_min(salary_text):
    if not salary_text:
        return None
    nums = re.findall(
        r"\$?([\d,]+)", salary_text.replace("K", "000").replace("k", "000")
    )
    if nums:
        return int(nums[0].replace(",", ""))
    return None


def _parse_salary_max(salary_text):
    if not salary_text:
        return None
    nums = re.findall(
        r"\$?([\d,]+)", salary_text.replace("K", "000").replace("k", "000")
    )
    if len(nums) >= 2:
        return int(nums[1].replace(",", ""))
    if len(nums) == 1:
        return int(nums[0].replace(",", ""))
    return None


def _parse_salary_currency(salary_text):
    if not salary_text:
        return "USD"
    if "CA$" in salary_text or "C$" in salary_text:
        return "CAD"
    return "USD"


def _parse_remote_status(location, title):
    text = f"{location} {title}".lower()
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if "onsite" in text or "on-site" in text:
        return "onsite"
    return ""