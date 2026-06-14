"""Per-job auth wall tracking via the jobs table."""
from urllib.parse import urlparse

from .db import get_conn


def add(jid, url, title, company):
    conn = get_conn()
    conn.execute("UPDATE jobs SET auth_wall=1 WHERE id=?", (jid,))
    conn.commit()


def remove(jid):
    conn = get_conn()
    conn.execute("UPDATE jobs SET auth_wall=0 WHERE id=?", (jid,))
    conn.commit()


def _prune():
    conn = get_conn()
    conn.execute("UPDATE jobs SET auth_wall=0 WHERE auth_wall=1 AND stage NOT IN ('extracted', 'failed')")
    conn.commit()


def list_all():
    _prune()
    conn = get_conn()
    rows = conn.execute("SELECT id, url, title, company FROM jobs WHERE auth_wall=1").fetchall()
    results = []
    for r in rows:
        results.append({
            "jid": r["id"],
            "url": r["url"],
            "domain": urlparse(r["url"] or "").netloc,
            "title": r["title"],
            "company": r["company"],
        })
    return results


def count():
    _prune()
    conn = get_conn()
    return conn.execute("SELECT COUNT(*) as c FROM jobs WHERE auth_wall=1").fetchone()["c"]


def domains():
    conn = get_conn()
    rows = conn.execute("SELECT url FROM jobs WHERE auth_wall=1 AND url != ''").fetchall()
    return sorted(set(urlparse(r["url"]).netloc for r in rows))
