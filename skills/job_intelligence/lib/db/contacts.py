"""lib/db/contacts.py — recruiter/contact records."""

from .schema import get_conn


def contact_add(job_id, name, **kw):
    c = get_conn()
    # NOTE: the row id must come from the CURSOR — sqlite3.Connection has no
    # .lastrowid. Reading it off the connection raised AttributeError on every
    # call, after the INSERT had already committed.
    cur = c.execute(
        """INSERT INTO contacts (job_id, company_id, name, role, email, linkedin_url, notes, reached_out,
           source, confidence, message_sent, email_sent, last_contacted_at, profile_picture_url,
           headline, connection_degree)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            job_id,
            kw.get("company_id"),
            name,
            kw.get("role", ""),
            kw.get("email", ""),
            kw.get("linkedin_url", ""),
            kw.get("notes", ""),
            1 if kw.get("reached_out") else 0,
            kw.get("source", ""),
            kw.get("confidence", 0.0),
            1 if kw.get("message_sent") else 0,
            1 if kw.get("email_sent") else 0,
            kw.get("last_contacted_at"),
            kw.get("profile_picture_url", ""),
            kw.get("headline", ""),
            kw.get("connection_degree", ""),
        ),
    )
    c.commit()
    return cur.lastrowid


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


def attempt_add(contact_id, channel, status="pending", **kw):
    """Record an outreach attempt (email / linkedin_message / linkedin_connect)."""
    from datetime import datetime
    c = get_conn()
    sent_at = kw.get("sent_at")
    if sent_at is None and status == "sent":
        sent_at = datetime.now().isoformat()
    # Row id from the CURSOR — see contact_add.
    cur = c.execute(
        """INSERT INTO contact_attempts (contact_id, channel, direction, subject, body, status, message_id, error, sent_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            contact_id,
            channel,
            kw.get("direction", "outbound"),
            kw.get("subject", ""),
            kw.get("body", ""),
            status,
            kw.get("message_id", ""),
            kw.get("error", ""),
            sent_at,
        ),
    )
    c.commit()
    return cur.lastrowid


def attempt_list(contact_id=None, job_id=None, status=None, limit=50):
    """List outreach attempts, optionally filtered by contact, job, or status."""
    c = get_conn()
    clauses = []
    params = []
    if contact_id:
        clauses.append("a.contact_id=?")
        params.append(contact_id)
    if job_id:
        clauses.append("c.job_id=?")
        params.append(job_id)
    if status:
        clauses.append("a.status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = c.execute(
        f"""SELECT a.*, c.name as contact_name, c.job_id as job_id
            FROM contact_attempts a JOIN contacts c ON c.id=a.contact_id
            {where} ORDER BY a.created_at DESC LIMIT ?""",
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]