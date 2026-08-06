"""lib/outreach_ledger.py — one seam for the outreach write choreography.

Deepens the reach side (architecture C3): email/message/connect each previously
hand-rolled the same "flag UPDATE → attempt INSERT → event INSERT → commit"
sequence across SEPARATE connections (reach.py:322-328 / 438-444 / 542-546,
each `attempt_add`/`event_add` opening its own connection and committing). A
failure between writes left `email_sent=1` with no attempt row, or an attempt
row with no flag — the partial-write hole.

This module owns ALL writes for one outcome in a single transaction on the
caller's connection. Atomicity is the point (grilling item J). It also owns
`settle` (the `update --set-sent` path) and the undo at-risk gate (grilling
items K/C8: flag-based evidence must be treated like attempt-based evidence).

Trace contract (C-O2): the ledger records faithfully and never lies about an
outcome. `pending` stays pending (no flags), a failed write rolls back and
raises so the CALLER prints a FAILED signal — never a SENT one.
"""

from .db.schema import get_conn

# Channel → contacts column holding the "sent" flag. `linkedin_connect` has no
# dedicated column: it marks `reached_out` only (the deliberate connect→DM
# funnel on one row stays legal).
CHANNEL_FLAGS = {
    "email": "email_sent",
    "linkedin_message": "message_sent",
    "linkedin_connect": None,
}

# Channel → human note prefix appended to contact notes on a sent outcome.
CHANNEL_NOTE = {
    "email": "Emailed",
    "linkedin_message": "Messaged",
    "linkedin_connect": "Connected",
}


def _conn():
    return get_conn()


def record_outcome(conn, channel, contact_id, jid, status, *,
                   subject="", body="", message_id="", error="",
                   event_type=None, event_title="", event_desc=""):
    """Record one outreach outcome atomically on `conn`.

    status='sent'   → contact flag (if any) + reached_out + last_contacted_at +
                      notes append + jobs.outreach_attempted, attempt(status=sent),
                      event. The full row, one commit.
    status='pending'→ attempt(status=pending) ONLY (the uncertain path — no
                      flags, no reached_out, no event; the guard stays armed).
    status='failed' → attempt(status=failed) ONLY (no flags, no reached_out).

    On any exception the transaction is rolled back and the error re-raised so
    the caller prints a FAILED signal, never a SENT one.
    """
    if channel not in CHANNEL_FLAGS:
        raise ValueError(f"unknown outreach channel {channel!r}")

    if status == "sent":
        flag = CHANNEL_FLAGS[channel]
        note = CHANNEL_NOTE.get(channel, "")
        sets = ["reached_out=1", "last_contacted_at=datetime('now')"]
        if flag:
            sets.append(f"{flag}=1")
        if note:
            sets.append(f"notes=notes || char(10) || '{note}: ' || datetime('now')")
        conn.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?",
                     (contact_id,))
        conn.execute("UPDATE jobs SET outreach_attempted=1 WHERE id=?", (jid,))
        # attempt row — written on the SAME connection, before the commit
        conn.execute(
            "INSERT INTO contact_attempts (contact_id, channel, direction, "
            "subject, body, status, message_id, error, sent_at) "
            "VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
            (contact_id, channel, "outbound", subject, body, "sent",
             message_id, error),
        )
        if event_type:
            conn.execute(
                "INSERT INTO events (job_id, event_type, title, description, "
                "event_at, completed) VALUES (?,?,?,?,datetime('now'),0)",
                (jid, event_type, event_title, event_desc),
            )
    elif status in ("pending", "failed"):
        conn.execute(
            "INSERT INTO contact_attempts (contact_id, channel, direction, "
            "subject, body, status, message_id, error, sent_at) "
            "VALUES (?,?,?,?,?,?,?,?,NULL)",
            (contact_id, channel, "outbound", subject, body, status,
             message_id, error),
        )
    else:
        raise ValueError(f"unknown outcome status {status!r}")

    conn.commit()


def settle(conn, contact_id, channel, sent=True):
    """`update --set-sent`: confirm an UNCERTAIN send after the human verified
    the inbox. Sets the channel flag + reached_out and settles any pending
    attempt to 'sent' — one transaction. Returns the number of attempts settled
    (0 if the send was flag-only, i.e. never had a pending row; the flag still
    gets set so the one-shot guard reads the truth).
    """
    if channel not in CHANNEL_FLAGS:
        raise ValueError(f"unknown outreach channel {channel!r}")
    flag = CHANNEL_FLAGS[channel]
    if flag:
        conn.execute(
            f"UPDATE contacts SET {flag}=1, reached_out=1 WHERE id=?",
            (contact_id,))
    else:
        conn.execute("UPDATE contacts SET reached_out=1 WHERE id=?", (contact_id,))
    cur = conn.execute(
        "UPDATE contact_attempts SET status='sent', "
        "sent_at=COALESCE(sent_at, datetime('now')) "
        "WHERE contact_id=? AND channel=? AND status='pending'",
        (contact_id, channel),
    )
    conn.commit()
    return cur.rowcount


def outreach_at_risk(conn, jid):
    """Contacts of a job that carry ANY outreach evidence — attempt rows OR
    send flags (C8). Undo must refuse to discard these without --confirm, or a
    `update --set-sent`-only send (no attempt row) would silently disarm the
    cross-job guard."""
    rows = conn.execute(
        "SELECT DISTINCT c.name, c.linkedin_url, c.email FROM contacts c "
        "LEFT JOIN contact_attempts a ON a.contact_id = c.id "
        "WHERE c.job_id=? AND (c.reached_out = 1 OR c.email_sent = 1 "
        "     OR c.message_sent = 1 OR a.status IN ('sent','pending','backfilled'))",
        (jid,),
    ).fetchall()
    return [dict(r) for r in rows]


def clear_outreach(conn, jid):
    """Reset a job's contact + attempt + outreach state (the `undo` action).
    Only called after the caller has decided the at-risk gate is cleared."""
    conn.execute("UPDATE contacts SET email_sent=0, message_sent=0, "
                 "reached_out=0 WHERE job_id=?", (jid,))
    conn.execute("DELETE FROM contact_attempts WHERE contact_id IN "
                 "(SELECT id FROM contacts WHERE job_id=?)", (jid,))
    conn.execute("UPDATE jobs SET outreach_attempted=0 WHERE id=?", (jid,))
    conn.commit()
