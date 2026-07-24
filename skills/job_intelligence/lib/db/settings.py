"""lib/db/settings.py — key/value settings and search-thread tracking."""

import json

from .schema import get_conn


def setting_get(key, default=None):
    r = get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r:
        try:
            return json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            return r["value"]
    return default


def setting_set(key, value):
    c = get_conn()
    if not isinstance(value, str):
        value = json.dumps(value)
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.commit()


def search_threads_save(threads):
    c = get_conn()
    for t in threads:
        c.execute(
            "INSERT OR IGNORE INTO search_threads (thread_id, subject, date, from_addr) VALUES (?,?,?,?)",
            (t["id"], t.get("subject", ""), t.get("date", ""), t.get("from", "")),
        )
    c.commit()


def search_threads_pending():
    seen = set(setting_get("staged_ids", [])) | set(setting_get("skipped_ids", []))
    rows = (
        get_conn()
        .execute(
            "SELECT thread_id, subject, date, from_addr FROM search_threads ORDER BY date"
        )
        .fetchall()
    )
    return [
        (r["thread_id"], r["subject"], r["date"], r["from_addr"])
        for r in rows
        if r["thread_id"] not in seen
    ]


def search_threads_clear():
    c = get_conn()
    c.execute("DELETE FROM search_threads")
    c.commit()