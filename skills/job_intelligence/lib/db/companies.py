"""lib/db/companies.py — company table operations."""

import hashlib
from datetime import datetime

from .schema import _JOBS_COLS, get_conn


def company_upsert(name, **kw):
    c = get_conn()
    cid = hashlib.md5(name.lower().encode()).hexdigest()[:16]
    now = datetime.now().isoformat()
    existing = c.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
    if existing:
        sets = ["updated_at=?"]
        vals = [now]
        for k, v in kw.items():
            if v is not None:
                sets.append(f"{k}=?")
                vals.append(v)
        vals.append(cid)
        c.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id=?", vals)
    else:
        c.execute(
            """INSERT INTO companies (id, name, domain, description, size, industry,
               culture_notes, rating, source_url, linkedin_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                name,
                kw.get("domain", ""),
                kw.get("description", ""),
                kw.get("size", ""),
                kw.get("industry", ""),
                kw.get("culture_notes", ""),
                kw.get("rating"),
                kw.get("source_url", ""),
                kw.get("linkedin_id", ""),
                now,
                now,
            ),
        )
    c.commit()
    return cid


def company_get(name_or_id):
    c = get_conn()
    r = c.execute(
        "SELECT * FROM companies WHERE id=? OR name=?", (name_or_id, name_or_id)
    ).fetchone()
    return dict(r) if r else None


def company_search(query, limit=20):
    rows = (
        get_conn()
        .execute(
            "SELECT * FROM companies WHERE name LIKE ? OR domain LIKE ? OR industry LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def company_list_jobs(company_name):
    from .jobs import _row_to_job
    c = get_conn()
    rows = c.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE company=? ORDER BY created_at DESC",
        (company_name,),
    ).fetchall()
    return [_row_to_job(r) for r in rows]