"""lib/db.py — SQLite backend for the job pipeline. v3 with companies, contacts, events."""

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(LIB_DIR)
WORKSPACE_ROOT = os.path.abspath(os.path.join(SKILL_DIR, "..", ".."))
DB_DIR = os.path.join(WORKSPACE_ROOT, "state")
DB_PATH = os.path.join(DB_DIR, "jobs.db")

STAGES = ["extracted", "described", "tailored", "applied", "skipped", "failed"]
SCHEMA_VERSION = 3

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


# =========================================================================
# Schema management
# =========================================================================

def _ensure_settings():
    c = get_conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)
    """)
    c.commit()


def _schema_version():
    _ensure_settings()
    r = get_conn().execute(
        "SELECT value FROM settings WHERE key='schema_version'"
    ).fetchone()
    return int(r["value"]) if r else 0


def _set_schema_version(v):
    c = get_conn()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('schema_version', ?)",
              (json.dumps(v),))
    c.commit()


def _has_old_tables():
    tables = {r[0] for r in get_conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    return "descriptions" in tables or "application_files" in tables


def _create_v3_tables():
    c = get_conn()
    c.executescript("""
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
            stage TEXT NOT NULL DEFAULT 'extracted',
            fit_score REAL,
            fit_summary TEXT,
            company_vibe TEXT,
            error TEXT,
            scripts TEXT NOT NULL DEFAULT '[]',
            response_path TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS job_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL CHECK(doc_type IN ('description','application')),
            job_id TEXT NOT NULL REFERENCES jobs(id),
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
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT REFERENCES jobs(id),
            company_id TEXT REFERENCES companies(id),
            name TEXT NOT NULL DEFAULT '',
            role TEXT DEFAULT '',
            email TEXT DEFAULT '',
            linkedin_url TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            reached_out INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            event_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            event_at TEXT,
            completed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    # Add columns that might be missing on existing DBs
    for col in ["response_path TEXT"]:
        try:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_jd_job ON job_documents(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_jd_type ON job_documents(doc_type, job_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_job ON contacts(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_company_name ON companies(name)",
    ]:
        try:
            c.execute(idx)
        except sqlite3.OperationalError:
            pass
    c.commit()


def _migrate_v1_to_v2():
    print("Migrating v1 -> v2...", file=sys.stderr)
    conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS job_documents")
    conn.execute("DROP TABLE IF EXISTS stages_v2")
    conn.execute("DROP TABLE IF EXISTS jobs_v2")

    has_stages = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stages'"
    ).fetchone()
    has_jobs = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stages_v2 (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS jobs_v2 (
            id TEXT PRIMARY KEY, email_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
            salary TEXT, job_type TEXT NOT NULL DEFAULT 'Full-Time',
            department TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT 'extracted', error TEXT,
            scripts TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')), applied_at TEXT
        );
    """)
    conn.commit()

    if has_stages:
        conn.execute("INSERT OR IGNORE INTO stages_v2 SELECT id, content, created_at FROM stages")
        conn.commit()
    if has_jobs:
        conn.execute("""INSERT OR IGNORE INTO jobs_v2
            (id, email_id, title, company, location, url, salary,
             job_type, department, source, stage, error, scripts, created_at, applied_at)
            SELECT id, email_id, title, company, location, url, salary,
                job_type, department, source, stage, error,
                COALESCE(scripts, '[]'), created_at, applied_at FROM jobs""")
        conn.commit()
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='descriptions'").fetchone():
        conn.execute("""
            INSERT INTO job_documents (doc_type, job_id, filename, content, created_at)
            SELECT 'description', job_id, 'content', content, created_at FROM descriptions
        """)
        conn.commit()
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='application_files'").fetchone():
        conn.execute("""
            INSERT INTO job_documents (doc_type, job_id, filename, content, created_at)
            SELECT 'application', job_id, filename, content, created_at FROM application_files
        """)
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jd_job ON job_documents(job_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jd_type ON job_documents(doc_type, job_id)")
    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        DROP TABLE IF EXISTS descriptions; DROP TABLE IF EXISTS application_files;
        DROP TABLE IF EXISTS stages; DROP TABLE IF EXISTS jobs;
        ALTER TABLE stages_v2 RENAME TO stages; ALTER TABLE jobs_v2 RENAME TO jobs;
        PRAGMA foreign_keys=ON;
    """)
    conn.commit()


def _migrate_v2_to_v3():
    print("Migrating v2 -> v3...", file=sys.stderr)
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys=OFF")

    # Backup v2 tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs_v2_backup AS SELECT * FROM jobs
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jd_v2_backup AS SELECT * FROM job_documents
    """)
    conn.commit()

    # Drop old tables
    conn.execute("DROP TABLE IF EXISTS job_documents")
    conn.execute("DROP TABLE IF EXISTS jobs")

    # Create v3 jobs with new columns (no stage CHECK)
    conn.executescript("""
        CREATE TABLE jobs (
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
            stage TEXT NOT NULL DEFAULT 'extracted',
            fit_score REAL,
            fit_summary TEXT,
            company_vibe TEXT,
            error TEXT,
            scripts TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            applied_at TEXT
        );
        INSERT INTO jobs (id, email_id, title, company, location, url, salary,
            job_type, department, source, stage, error, scripts, created_at, applied_at)
        SELECT id, email_id, title, company, location, url, salary,
            job_type, department, source, stage, error, scripts, created_at, applied_at
        FROM jobs_v2_backup;
    """)
    conn.commit()

    # Create job_documents v3 with FK to new jobs
    conn.executescript("""
        CREATE TABLE job_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL CHECK(doc_type IN ('description','application')),
            job_id TEXT NOT NULL REFERENCES jobs(id),
            filename TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO job_documents (id, doc_type, job_id, filename, content, created_at)
            SELECT id, doc_type, job_id, filename, content, created_at FROM jd_v2_backup;
    """)
    conn.commit()

    # New tables
    conn.executescript("""
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
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT REFERENCES jobs(id),
            company_id TEXT REFERENCES companies(id),
            name TEXT NOT NULL DEFAULT '',
            role TEXT DEFAULT '',
            email TEXT DEFAULT '',
            linkedin_url TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            reached_out INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            event_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            event_at TEXT,
            completed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # Indexes
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_jd_job ON job_documents(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_jd_type ON job_documents(doc_type, job_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_job ON contacts(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_company_name ON companies(name)",
    ]:
        try:
            conn.execute(idx)
        except sqlite3.OperationalError:
            pass
    conn.commit()

    # Cleanup
    conn.execute("DROP TABLE IF EXISTS jobs_v2_backup")
    conn.execute("DROP TABLE IF EXISTS jd_v2_backup")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()

    # Pre-populate companies from existing job data
    _sync_companies_from_jobs()


_COMPANY_SKIP = re.compile(
    r'^(https?://|[$]|[-]|[CA]+\$|[A-Fa-f0-9]{8,}[-]|'
    r'\d|\byr\b|\bapply\b|\bsave\b|\bshare\b|\bview\b|'
    r'\bjobalert\b|\binstant\b|\breferral\b)'
)

def _sync_companies_from_jobs():
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT company FROM jobs WHERE company != '' AND company != 'Unknown'"
    ).fetchall()
    now = datetime.now().isoformat()
    inserted = 0
    for r in rows:
        name = r["company"].strip()
        if not name or len(name) < 3 or _COMPANY_SKIP.match(name):
            continue
        if any(kw in name.lower() for kw in ['jobright', 'referral', 'instant', 'apply', 'alert']):
            continue
        cid = hashlib.md5(name.lower().encode()).hexdigest()[:16]
        existing = conn.execute("SELECT 1 FROM companies WHERE id=?", (cid,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO companies (id, name, created_at, updated_at) VALUES (?,?,?,?)",
                (cid, name, now, now),
            )
            inserted += 1
    conn.commit()
    print(f"  Synced {inserted} companies from {len(rows)} job entries.", file=sys.stderr)


def _migrate_schema():
    conn = get_conn()
    _ensure_settings()
    version = _schema_version()

    if version >= SCHEMA_VERSION and not _has_old_tables():
        _create_v3_tables()
        return
    if _has_old_tables():
        _migrate_v1_to_v2()
        version = 2
    if version == 2:
        _migrate_v2_to_v3()
    _create_v3_tables()
    _set_schema_version(SCHEMA_VERSION)
    print(f"Schema now at v{SCHEMA_VERSION}.", file=sys.stderr)


# =========================================================================
# Jobs
# =========================================================================

def _row_to_job(r):
    d = dict(r)
    if isinstance(d.get("scripts"), str):
        try:
            d["scripts"] = json.loads(d["scripts"])
        except (json.JSONDecodeError, TypeError):
            d["scripts"] = []
    return d


_JOBS_COLS = (
    "id, email_id, title, company, location, url, salary,"
    "salary_min, salary_max, salary_currency, remote_status,"
    "job_type, department, source, source_url, stage, fit_score,"
    "fit_summary, company_vibe, error, scripts, response_path, created_at, updated_at, applied_at"
)

_JOBS_Q = ",".join("?" for _ in range(25))


def load_state():
    conn = get_conn()
    rows = conn.execute(f"SELECT {_JOBS_COLS} FROM jobs ORDER BY created_at").fetchall()
    jobs = {}
    for r in rows:
        d = _row_to_job(r)
        jobs[d["id"]] = d
    stage_rows = conn.execute(
        "SELECT stage, COUNT(*) as cnt FROM jobs GROUP BY stage"
    ).fetchall()
    stage_counts = {s: 0 for s in STAGES}
    for sr in stage_rows:
        stage_counts[sr["stage"]] = sr["cnt"]
    return {"jobs": jobs, "stages": stage_counts}


def save_state(state):
    conn = get_conn()
    now = datetime.now().isoformat()
    for jid, entry in state["jobs"].items():
        scripts = entry.get("scripts")
        if isinstance(scripts, list):
            scripts = json.dumps(scripts)
        conn.execute(
            f"""INSERT OR REPLACE INTO jobs ({_JOBS_COLS})
               VALUES ({_JOBS_Q})""",
            (
                jid,
                entry.get("email_id", ""),
                entry.get("title", ""),
                entry.get("company", ""),
                entry.get("location", ""),
                entry.get("url", ""),
                entry.get("salary"),
                entry.get("salary_min"),
                entry.get("salary_max"),
                entry.get("salary_currency", "USD"),
                entry.get("remote_status", ""),
                entry.get("job_type", "Full-Time"),
                entry.get("department", ""),
                entry.get("source", ""),
                entry.get("source_url", ""),
                entry.get("stage", "extracted"),
                entry.get("fit_score"),
                entry.get("fit_summary"),
                entry.get("company_vibe"),
                entry.get("error"),
                scripts or "[]",
                entry.get("response_path"),
                entry.get("created_at", now),
                now,
                entry.get("applied_at"),
            ),
        )
    conn.commit()


def add_job(job_data):
    conn = get_conn()
    url = job_data.get("url", "")
    jid = hashlib.md5(url.encode()).hexdigest()[:16] if url else None
    if not jid:
        return None
    if conn.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
        return jid
    now = datetime.now().isoformat()
    scripts = job_data.get("scripts", [])
    if isinstance(scripts, list):
        scripts = json.dumps(scripts)
    conn.execute(
        f"""INSERT INTO jobs ({_JOBS_COLS}) VALUES ({_JOBS_Q})""",
        (
            jid,
            job_data.get("email_id", ""),
            job_data.get("title", ""),
            job_data.get("company", ""),
            job_data.get("location", ""),
            url,
            job_data.get("salary"),
            _parse_salary_min(job_data.get("salary", "")),
            _parse_salary_max(job_data.get("salary", "")),
            _parse_salary_currency(job_data.get("salary", "")),
            _parse_remote_status(job_data.get("location", ""), job_data.get("title", "")),
            job_data.get("job_type", "Full-Time"),
            job_data.get("department", ""),
            job_data.get("source", ""),
            job_data.get("source_url", ""),
            "extracted",
            job_data.get("fit_score"),
            job_data.get("fit_summary"),
            job_data.get("company_vibe"),
            None,
            scripts or "[]",
            job_data.get("response_path"),
            now,
            now,
            None,
        ),
    )
    conn.commit()
    return jid


def advance_job(jid, new_stage, **updates):
    conn = get_conn()
    sets = ["stage=?", "updated_at=?"]
    vals = [new_stage, datetime.now().isoformat()]
    for k, v in updates.items():
        if k == "scripts" and isinstance(v, list):
            v = json.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(jid)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()


def get_job(jid):
    conn = get_conn()
    r = conn.execute(f"SELECT {_JOBS_COLS} FROM jobs WHERE id=?", (jid,)).fetchone()
    return _row_to_job(r) if r else None


def get_jobs_by_stage(stage):
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE stage=? ORDER BY created_at", (stage,)
    ).fetchall()
    return [(r["id"], _row_to_job(r)) for r in rows]


def next_pending_job():
    conn = get_conn()
    r = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE stage='described' LIMIT 1"
    ).fetchone()
    return (r["id"], _row_to_job(r)) if r else (None, None)


def get_failed_jobs():
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE stage='failed' ORDER BY created_at"
    ).fetchall()
    return [(r["id"], _row_to_job(r)) for r in rows]


def search_jobs(query, stage=None, limit=50):
    conn = get_conn()
    clauses = []
    params = []
    if query:
        clauses.append("(title LIKE ? OR company LIKE ? OR location LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q, q])
    if stage:
        clauses.append("stage=?")
        params.append(stage)
    where = " AND ".join(clauses) if clauses else "1"
    rows = conn.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    return [(_row_to_job(r)) for r in rows]


def job_count():
    return get_conn().execute("SELECT COUNT(*) as c FROM jobs").fetchone()["c"]


def job_count_by_stage():
    rows = get_conn().execute(
        "SELECT stage, COUNT(*) as cnt FROM jobs GROUP BY stage ORDER BY stage"
    ).fetchall()
    return {r["stage"]: r["cnt"] for r in rows}


# ── Salary/remote parsing helpers ──

def _parse_salary_min(salary_text):
    if not salary_text:
        return None
    nums = re.findall(r'\$?([\d,]+)', salary_text.replace('K', '000').replace('k', '000'))
    if nums:
        return int(nums[0].replace(',', ''))
    return None


def _parse_salary_max(salary_text):
    if not salary_text:
        return None
    nums = re.findall(r'\$?([\d,]+)', salary_text.replace('K', '000').replace('k', '000'))
    if len(nums) >= 2:
        return int(nums[1].replace(',', ''))
    if len(nums) == 1:
        return int(nums[0].replace(',', ''))
    return None


def _parse_salary_currency(salary_text):
    if not salary_text:
        return 'USD'
    if 'CA$' in salary_text or 'C$' in salary_text:
        return 'CAD'
    return 'USD'


def _parse_remote_status(location, title):
    text = f"{location} {title}".lower()
    if 'remote' in text:
        return 'remote'
    if 'hybrid' in text:
        return 'hybrid'
    if 'onsite' in text or 'on-site' in text:
        return 'onsite'
    return ''


# =========================================================================
# Stages (raw email content)
# =========================================================================

def stage_save(tid, content):
    c = get_conn()
    c.execute("INSERT OR REPLACE INTO stages (id, content) VALUES (?,?)", (tid, content))
    c.commit()


def stage_get(tid):
    r = get_conn().execute("SELECT content FROM stages WHERE id=?", (tid,)).fetchone()
    return r["content"] if r else None


def stage_exists(tid):
    return get_conn().execute("SELECT 1 FROM stages WHERE id=?", (tid,)).fetchone() is not None


def stage_list_all():
    rows = get_conn().execute("SELECT id, content FROM stages ORDER BY id").fetchall()
    return [(r["id"], r["content"]) for r in rows]


def stage_delete(tid):
    c = get_conn()
    c.execute("DELETE FROM stages WHERE id=?", (tid,))
    c.commit()


def stage_count():
    return get_conn().execute("SELECT COUNT(*) as c FROM stages").fetchone()["c"]


# =========================================================================
# Job Documents
# =========================================================================

def doc_save(doc_type, job_id, filename, content):
    c = get_conn()
    existing = c.execute(
        "SELECT id FROM job_documents WHERE doc_type=? AND job_id=? AND filename=?",
        (doc_type, job_id, filename),
    ).fetchone()
    if existing:
        c.execute("UPDATE job_documents SET content=? WHERE id=?", (content, existing["id"]))
    else:
        c.execute(
            "INSERT INTO job_documents (doc_type, job_id, filename, content) VALUES (?,?,?,?)",
            (doc_type, job_id, filename, content),
        )
    c.commit()


def doc_get(doc_type, job_id, filename="content"):
    r = get_conn().execute(
        "SELECT content FROM job_documents WHERE doc_type=? AND job_id=? AND filename=?",
        (doc_type, job_id, filename),
    ).fetchone()
    return r["content"] if r else None


def doc_exists(doc_type, job_id):
    return get_conn().execute(
        "SELECT 1 FROM job_documents WHERE doc_type=? AND job_id=? LIMIT 1",
        (doc_type, job_id),
    ).fetchone() is not None


def doc_list_ids(doc_type):
    return {r["job_id"] for r in get_conn().execute(
        "SELECT DISTINCT job_id FROM job_documents WHERE doc_type=?", (doc_type,)
    ).fetchall()}


def doc_list_files(job_id, doc_type="application"):
    return [dict(r) for r in get_conn().execute(
        "SELECT filename, created_at FROM job_documents WHERE job_id=? AND doc_type=? ORDER BY filename",
        (job_id, doc_type),
    ).fetchall()]


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


# =========================================================================
# Companies
# =========================================================================

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
               culture_notes, rating, source_url, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, name, kw.get("domain", ""), kw.get("description", ""),
             kw.get("size", ""), kw.get("industry", ""), kw.get("culture_notes", ""),
             kw.get("rating"), kw.get("source_url", ""), now, now),
        )
    c.commit()
    return cid


def company_get(name_or_id):
    c = get_conn()
    r = c.execute("SELECT * FROM companies WHERE id=? OR name=?", (name_or_id, name_or_id)).fetchone()
    return dict(r) if r else None


def company_search(query, limit=20):
    rows = get_conn().execute(
        "SELECT * FROM companies WHERE name LIKE ? OR domain LIKE ? OR industry LIKE ? LIMIT ?",
        (f"%{query}%", f"%{query}%", f"%{query}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def company_list_jobs(company_name):
    c = get_conn()
    rows = c.execute(
        f"SELECT {_JOBS_COLS} FROM jobs WHERE company=? ORDER BY created_at DESC",
        (company_name,),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


# =========================================================================
# Contacts
# =========================================================================

def contact_add(job_id, name, **kw):
    c = get_conn()
    c.execute(
        """INSERT INTO contacts (job_id, company_id, name, role, email, linkedin_url, notes, reached_out)
           VALUES (?,?,?,?,?,?,?,?)""",
        (job_id, kw.get("company_id"), name, kw.get("role", ""),
         kw.get("email", ""), kw.get("linkedin_url", ""),
         kw.get("notes", ""), 1 if kw.get("reached_out") else 0),
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


# =========================================================================
# Events
# =========================================================================

def event_add(job_id, event_type, title, **kw):
    c = get_conn()
    c.execute(
        """INSERT INTO events (job_id, event_type, title, description, event_at, completed)
           VALUES (?,?,?,?,?,?)""",
        (job_id, event_type, title, kw.get("description", ""),
         kw.get("event_at"), 1 if kw.get("completed") else 0),
    )
    c.commit()
    return c.lastrowid


def event_list(job_id=None, upcoming=False):
    c = get_conn()
    if job_id:
        rows = c.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY event_at, created_at", (job_id,)
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


# =========================================================================
# Settings
# =========================================================================

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


# =========================================================================
# Legacy file migration
# =========================================================================

def needs_migration():
    return os.path.exists(os.path.join(SKILL_DIR, "results", "jobs.json")) and stage_count() == 0


def migrate_from_files():
    print("Migrating legacy file data to SQLite...", file=sys.stderr)
    conn = get_conn()
    legacy_dirs = {
        "stage": os.path.join(SKILL_DIR, "stage"),
        "jobs": os.path.join(SKILL_DIR, "results", "jobs.json"),
        "desc": os.path.join(SKILL_DIR, "results", "descriptions"),
        "apps": os.path.join(SKILL_DIR, "results", "applications"),
        "data": os.path.join(SKILL_DIR, "data"),
    }
    if os.path.isdir(legacy_dirs["stage"]):
        for fname in sorted(os.listdir(legacy_dirs["stage"])):
            if not fname.endswith(".txt"):
                continue
            tid = fname.replace(".txt", "")
            try:
                with open(os.path.join(legacy_dirs["stage"], fname), "r", encoding="utf-8", errors="replace") as f:
                    conn.execute("INSERT OR IGNORE INTO stages (id, content) VALUES (?,?)", (tid, f.read()))
            except Exception as e:
                print(f"  SKIP stage {fname}: {e}", file=sys.stderr)
    if os.path.exists(legacy_dirs["jobs"]):
        try:
            with open(legacy_dirs["jobs"], "r", encoding="utf-8") as f:
                state = json.load(f)
            now = datetime.now().isoformat()
            for jid, entry in state.get("jobs", {}).items():
                scripts = entry.get("scripts", [])
                if isinstance(scripts, list):
                    scripts = json.dumps(scripts)
                conn.execute(
                    f"""INSERT OR IGNORE INTO jobs ({_JOBS_COLS}) VALUES ({_JOBS_Q})""",
                    (jid, entry.get("email_id", ""), entry.get("title", ""),
                     entry.get("company", ""), entry.get("location", ""),
                     entry.get("url", ""), entry.get("salary"),
                     None, None, 'USD', '',
                     entry.get("job_type", "Full-Time"), entry.get("department", ""),
                     entry.get("source", ""), "", entry.get("stage", "extracted"),
                     None, None, None, entry.get("error"), scripts or "[]",
                     entry.get("created_at", now), now, entry.get("applied_at")),
                )
        except Exception as e:
            print(f"  SKIP jobs.json: {e}", file=sys.stderr)
    if os.path.isdir(legacy_dirs["desc"]):
        for fname in os.listdir(legacy_dirs["desc"]):
            if not fname.endswith(".txt"):
                continue
            jid = fname.replace(".txt", "")
            try:
                with open(os.path.join(legacy_dirs["desc"], fname), "r", encoding="utf-8", errors="replace") as f:
                    conn.execute(
                        "INSERT OR IGNORE INTO job_documents (doc_type, job_id, filename, content) VALUES ('description', ?, 'content', ?)",
                        (jid, f.read()),
                    )
            except Exception as e:
                print(f"  SKIP desc {fname}: {e}", file=sys.stderr)
    if os.path.isdir(legacy_dirs["apps"]):
        for jid in os.listdir(legacy_dirs["apps"]):
            ad = os.path.join(legacy_dirs["apps"], jid)
            if not os.path.isdir(ad):
                continue
            for fname in os.listdir(ad):
                fpath = os.path.join(ad, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        conn.execute(
                            "INSERT INTO job_documents (doc_type, job_id, filename, content) VALUES ('application', ?, ?, ?)",
                            (jid, fname, f.read()),
                        )
                except Exception as e:
                    print(f"  SKIP app {jid}/{fname}: {e}", file=sys.stderr)
    if os.path.isdir(legacy_dirs["data"]):
        for fn in ["stage_tracker.json", "extract_tracker.json", "job_tracker_state.json"]:
            fp = os.path.join(legacy_dirs["data"], fn)
            if os.path.exists(fp):
                try:
                    setting_set(f"legacy:{fn.replace('.json', '')}", json.load(open(fp)))
                except Exception:
                    pass
    conn.commit()
    _sync_companies_from_jobs()
    print("Migration complete.", file=sys.stderr)


# =========================================================================
# State wrappers (merged from lib.state for SLM simplicity)
# =========================================================================

def load():
    if needs_migration():
        migrate_from_files()
    return load_state()


def save(state):
    save_state(state)


def job_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:16] if url else None


def add(state, job_data):
    jid = add_job(job_data)
    if jid:
        state["jobs"][jid] = get_job(jid)
        company = job_data.get("company", "")
        if company and company != "Unknown":
            try:
                company_upsert(company)
            except Exception:
                pass
    return jid


def advance(entry, new_stage, **updates):
    jid = entry.get("id")
    entry["stage"] = new_stage
    for k, v in updates.items():
        entry[k] = v
    advance_job(jid, new_stage, **updates)


def get_by_stage(state, stage):
    return get_jobs_by_stage(stage)


def next_pending(state):
    return next_pending_job()


def get_failed(state):
    return get_failed_jobs()


def close():
    global _conn
    if _conn:
        _conn.close()
        _conn = None
