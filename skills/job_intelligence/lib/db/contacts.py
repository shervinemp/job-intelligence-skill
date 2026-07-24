"""lib/db/contacts.py — recruiter/contact records."""

from .schema import get_conn


def contact_add(job_id, name, **kw):
    c = get_conn()
    c.execute(
        """INSERT INTO contacts (job_id, company_id, name, role, email, linkedin_url, notes, reached_out)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            job_id,
            kw.get("company_id"),
            name,
            kw.get("role", ""),
            kw.get("email", ""),
            kw.get("linkedin_url", ""),
            kw.get("notes", ""),
            1 if kw.get("reached_out") else 0,
        ),
    )
    c.commit()
    return c.lastrowid


def contact_list(job_id=None):
    c = get_conn()
    if job_id:
        rows = c.execute(
            "SELECT * FROM contacts WHERE job_id=? ORDER BY created_at", (job_id,)
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM contacts ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


def contact_update(cid, **kw):
    if not kw:
        return
    c = get_conn()
    sets = []
    vals = []
    for k, v in kw.items():
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(cid)
    c.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", vals)
    c.commit()