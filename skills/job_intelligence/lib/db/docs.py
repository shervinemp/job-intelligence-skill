"""lib/db/docs.py — job documents, descriptions, and applications."""

from .schema import get_conn


def doc_save(doc_type, job_id, filename, content):
    c = get_conn()
    existing = c.execute(
        "SELECT id FROM job_documents WHERE doc_type=? AND job_id=? AND filename=?",
        (doc_type, job_id, filename),
    ).fetchone()
    if existing:
        c.execute(
            "UPDATE job_documents SET content=? WHERE id=?", (content, existing["id"])
        )
    else:
        c.execute(
            "INSERT INTO job_documents (doc_type, job_id, filename, content) VALUES (?,?,?,?)",
            (doc_type, job_id, filename, content),
        )
    c.commit()


def doc_get(doc_type, job_id, filename="content"):
    r = (
        get_conn()
        .execute(
            "SELECT content FROM job_documents WHERE doc_type=? AND job_id=? AND filename=?",
            (doc_type, job_id, filename),
        )
        .fetchone()
    )
    return r["content"] if r else None


def doc_exists(doc_type, job_id):
    return (
        get_conn()
        .execute(
            "SELECT 1 FROM job_documents WHERE doc_type=? AND job_id=? LIMIT 1",
            (doc_type, job_id),
        )
        .fetchone()
        is not None
    )


def doc_list_ids(doc_type):
    return {
        r["job_id"]
        for r in get_conn()
        .execute(
            "SELECT DISTINCT job_id FROM job_documents WHERE doc_type=?", (doc_type,)
        )
        .fetchall()
    }


def doc_list_files(job_id, doc_type="application"):
    return [
        dict(r)
        for r in get_conn()
        .execute(
            "SELECT filename, created_at FROM job_documents WHERE job_id=? AND doc_type=? ORDER BY filename",
            (job_id, doc_type),
        )
        .fetchall()
    ]


def doc_delete_all(job_id):
    c = get_conn()
    c.execute("DELETE FROM job_documents WHERE job_id=?", (job_id,))
    c.commit()


def desc_save(jid, content):
    doc_save("description", jid, "content", content)


def desc_get(jid):
    return doc_get("description", jid, "content")


def desc_exists(jid):
    return doc_exists("description", jid)


def desc_list_ids():
    return doc_list_ids("description")


def app_save(jid, filename, content):
    doc_save("application", jid, filename, content)


def app_get(jid, filename):
    return doc_get("application", jid, filename)


def app_list(jid):
    return doc_list_files(jid, "application")


def app_list_job_ids():
    return doc_list_ids("application")


def app_delete_all(jid):
    doc_delete_all(jid)