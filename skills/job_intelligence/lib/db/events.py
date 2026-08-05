"""lib/db/events.py — job events / reminders."""

from .schema import get_conn


def event_add(job_id, event_type, title, **kw):
    c = get_conn()
    # Row id from the CURSOR — sqlite3.Connection has no .lastrowid.
    cur = c.execute(
        """INSERT INTO events (job_id, event_type, title, description, event_at, completed)
           VALUES (?,?,?,?,?,?)""",
        (
            job_id,
            event_type,
            title,
            kw.get("description", ""),
            kw.get("event_at"),
            1 if kw.get("completed") else 0,
        ),
    )
    c.commit()
    return cur.lastrowid


def event_list(job_id=None, upcoming=False):
    c = get_conn()
    if job_id:
        rows = c.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY event_at, created_at",
            (job_id,),
        ).fetchall()
    elif upcoming:
        rows = c.execute(
            "SELECT e.*, j.title as job_title, j.company as job_company FROM events e "
            "JOIN jobs j ON j.id=e.job_id "
            "WHERE e.completed=0 AND e.event_at >= datetime('now') "
            "ORDER BY e.event_at LIMIT 20"
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


def event_complete(eid):
    c = get_conn()
    c.execute("UPDATE events SET completed=1 WHERE id=?", (eid,))
    c.commit()