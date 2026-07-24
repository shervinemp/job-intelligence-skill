"""lib/db/stages.py — raw email stage content."""

from .schema import get_conn


def stage_save(tid, content):
    c = get_conn()
    c.execute(
        "INSERT OR REPLACE INTO stages (id, content) VALUES (?,?)", (tid, content)
    )
    c.commit()


def stage_get(tid):
    r = get_conn().execute("SELECT content FROM stages WHERE id=?", (tid,)).fetchone()
    return r["content"] if r else None


def stage_exists(tid):
    return (
        get_conn().execute("SELECT 1 FROM stages WHERE id=?", (tid,)).fetchone()
        is not None
    )


def stage_list_all():
    rows = get_conn().execute("SELECT id, content FROM stages ORDER BY id").fetchall()
    return [(r["id"], r["content"]) for r in rows]


def stage_delete(tid):
    c = get_conn()
    c.execute("DELETE FROM stages WHERE id=?", (tid,))
    c.commit()


def stage_count():
    return get_conn().execute("SELECT COUNT(*) as c FROM stages").fetchone()["c"]