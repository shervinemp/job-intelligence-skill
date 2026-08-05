"""lib/db/schema.py — connection, tables, migration."""

import json
import os
import sqlite3

from ..config import AUTH_WALLS_PATH, DB_PATH, STATE_DIR as DB_DIR

STAGES = ["extracted", "described", "tailored", "applied"]

_conn = None


def get_conn():
    global _conn
    if _conn is None:
        os.makedirs(DB_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _migrate_schema()
    return _conn


def close():
    """Close the singleton connection. Idempotent; public so callers
    never poke _conn directly."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _import_legacy_auth_walls():
    old_path = AUTH_WALLS_PATH
    if not os.path.exists(old_path):
        return
    try:
        with open(old_path) as f:
            entries = json.load(f)
        conn = get_conn()
        for e in entries:
            jid = e.get("jid")
            if jid:
                conn.execute("UPDATE jobs SET auth_wall=1 WHERE id=?", (jid,))
        conn.commit()
        os.remove(old_path)
    except Exception:
        pass


def _create_v3_tables():
    c = get_conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stages (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            email_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            salary TEXT,
            salary_min INTEGER,
            salary_max INTEGER,
            salary_currency TEXT DEFAULT 'USD',
            remote_status TEXT DEFAULT '',
            job_type TEXT NOT NULL DEFAULT 'Full-Time',
            department TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            source_url TEXT DEFAULT '',
            stage TEXT NOT NULL DEFAULT 'extracted' CHECK(stage IN ('extracted','described','tailored','applied')),
            state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','rejected','failed')),
            fit_score REAL,
            fit_summary TEXT,
            company_vibe TEXT,
            error TEXT,
            auth_wall INTEGER NOT NULL DEFAULT 0,
            scripts TEXT NOT NULL DEFAULT '[]',
            response_path TEXT,
            notes TEXT NOT NULL DEFAULT '',
            category TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS job_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL CHECK(doc_type IN ('description','application')),
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            filename TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS companies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            domain TEXT DEFAULT '',
            description TEXT DEFAULT '',
            size TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            culture_notes TEXT DEFAULT '',
            rating REAL,
            source_url TEXT DEFAULT '',
            linkedin_id TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
            company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
            name TEXT NOT NULL DEFAULT '',
            role TEXT DEFAULT '',
            email TEXT DEFAULT '',
            linkedin_url TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            reached_out INTEGER DEFAULT 0,
            source TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            message_sent INTEGER DEFAULT 0,
            email_sent INTEGER DEFAULT 0,
            last_contacted_at TEXT,
            profile_picture_url TEXT DEFAULT '',
            headline TEXT DEFAULT '',
            connection_degree TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS company_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            linkedin_url TEXT DEFAULT '',
            headline TEXT DEFAULT '',
            connection_degree TEXT DEFAULT '1st',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS contact_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
            channel TEXT NOT NULL CHECK(channel IN ('email','linkedin_message','linkedin_connect')),
            direction TEXT NOT NULL DEFAULT 'outbound' CHECK(direction IN ('outbound','inbound')),
            subject TEXT DEFAULT '',
            body TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed','opened','replied')),
            message_id TEXT DEFAULT '',
            error TEXT DEFAULT '',
            sent_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS search_threads (
            thread_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            from_addr TEXT NOT NULL DEFAULT '',
            searched_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- The fill ledger: one immutable row per field decision.
        --
        -- ETHOS §10 names wrong-fill rate "the falsification instrument
        -- for the ethos itself", but nothing computed it, and nothing
        -- COULD: `kind=verified` means "the value I intended is present",
        -- and the verifier re-scores with the same scorer that chose the
        -- value, so it is tautological for semantic error. Every feedback
        -- loop in the pipeline therefore measured completion, not
        -- correctness.
        --
        -- This table is the missing denominator. `answer` (what we meant)
        -- and `selected_text` (what the form actually shows) sit side by
        -- side so a human or the orchestrator can rule on correctness;
        -- `verdict` stays NULL until someone does. Nothing here feeds
        -- back into the hot path — it only makes the claim measurable.
        CREATE TABLE IF NOT EXISTS field_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            label_norm TEXT NOT NULL DEFAULT '',
            answer TEXT NOT NULL DEFAULT '',
            selected_text TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            required INTEGER NOT NULL DEFAULT 0,
            verdict TEXT CHECK(verdict IN ('correct','wrong','unanswerable')),
            verdict_note TEXT NOT NULL DEFAULT '',
            adjudicated_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            event_at TEXT,
            completed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """
    )
    # Add columns that might be missing on existing DBs
    for col in [
        "response_path TEXT",
        "notes TEXT NOT NULL DEFAULT ''",
        "category TEXT",
        "auth_wall INTEGER NOT NULL DEFAULT 0",
        "external_url TEXT NOT NULL DEFAULT ''",
        "recruiter_name TEXT NOT NULL DEFAULT ''",
        "recruiter_url TEXT NOT NULL DEFAULT ''",
        "team_name TEXT NOT NULL DEFAULT ''",
        "contact_discovered INTEGER DEFAULT 0",
        "outreach_attempted INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    for col in [
        "source TEXT DEFAULT ''",
        "confidence REAL DEFAULT 0.0",
        "message_sent INTEGER DEFAULT 0",
        "email_sent INTEGER DEFAULT 0",
        "last_contacted_at TEXT",
        "profile_picture_url TEXT DEFAULT ''",
        "headline TEXT DEFAULT ''",
        "connection_degree TEXT DEFAULT ''",
    ]:
        try:
            c.execute(f"ALTER TABLE contacts ADD COLUMN {col}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    try:
        c.execute("ALTER TABLE companies ADD COLUMN linkedin_id TEXT DEFAULT ''")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise
    _import_legacy_auth_walls()

    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_jd_job ON job_documents(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_jd_type ON job_documents(doc_type, job_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_job ON contacts(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_company_name ON companies(name)",
        "CREATE INDEX IF NOT EXISTS idx_fills_job ON field_fills(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_fills_verdict ON field_fills(verdict)",
        "CREATE INDEX IF NOT EXISTS idx_fills_platform ON field_fills(platform, kind)",
    ]:
        try:
            c.execute(idx)
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e).lower():
                raise
    c.commit()


def _migrate_schema():
    _create_v3_tables()
    c = get_conn()
    try:
        c.execute("ALTER TABLE jobs ADD COLUMN state TEXT NOT NULL DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass


_JOBS_COLS = (
    "id, email_id, title, company, location, url, salary,"
    "salary_min, salary_max, salary_currency, remote_status,"
    "job_type, department, source, source_url, stage, state, fit_score,"
    "fit_summary, company_vibe, error, scripts, response_path,"
    "notes, created_at, updated_at, applied_at, category, external_url,"
    "recruiter_name, recruiter_url, team_name, contact_discovered, outreach_attempted"
)