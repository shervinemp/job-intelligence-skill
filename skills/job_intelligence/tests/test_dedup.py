"""Unit tests for job deduplication (lib/db/jobs.py find_duplicate + add_job)."""
import os, sys, unittest, tempfile, shutil
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lib.db.schema as schema
from lib.db.jobs import find_duplicate, add_job


class _TempDBMixin:
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        schema._conn = None
        schema.DB_PATH = os.path.join(self.tmpdir, "test.db")
        schema.DB_DIR = self.tmpdir
        self.conn = schema.get_conn()

    def tearDown(self):
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _insert(self, jid, title, company, stage="extracted", state="active"):
        self.conn.execute(
            "INSERT INTO jobs (id, url, title, company, stage, state, created_at, updated_at, scripts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]')",
            (jid, f"https://example.com/{jid}", title, company, stage, state,
             datetime.now().isoformat(), datetime.now().isoformat()),
        )
        self.conn.commit()


class FindDuplicate(_TempDBMixin, unittest.TestCase):
    def test_no_match_returns_none(self):
        self._insert("aaa", "Engineer", "Acme")
        result = find_duplicate("bbb", "Manager", "Beta")
        self.assertIsNone(result)

    def test_exact_match_finds_dup(self):
        self._insert("aaa", "Senior Engineer", "Acme", stage="tailored")
        result = find_duplicate("bbb", "Senior Engineer", "Acme")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "aaa")
        self.assertEqual(result["stage"], "tailored")

    def test_case_insensitive(self):
        self._insert("aaa", "Senior Engineer", "Acme")
        result = find_duplicate("bbb", "senior engineer", "acme")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "aaa")

    def test_different_title_no_dup(self):
        self._insert("aaa", "Senior Engineer", "Acme")
        result = find_duplicate("bbb", "Junior Engineer", "Acme")
        self.assertIsNone(result)

    def test_different_company_no_dup(self):
        self._insert("aaa", "Senior Engineer", "Acme")
        result = find_duplicate("bbb", "Senior Engineer", "Beta")
        self.assertIsNone(result)

    def test_excludes_self(self):
        self._insert("aaa", "Engineer", "Acme")
        result = find_duplicate("aaa", "Engineer", "Acme")
        self.assertIsNone(result)

    def test_skips_rejected(self):
        self._insert("aaa", "Engineer", "Acme", state="rejected")
        result = find_duplicate("bbb", "Engineer", "Acme")
        self.assertIsNone(result)

    def test_prefers_applied_over_tailored(self):
        self._insert("aaa", "Engineer", "Acme", stage="tailored")
        self._insert("bbb", "Engineer", "Acme", stage="applied")
        result = find_duplicate("ccc", "Engineer", "Acme")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "bbb")
        self.assertEqual(result["stage"], "applied")

    def test_empty_title_returns_none(self):
        self._insert("aaa", "Engineer", "Acme")
        result = find_duplicate("bbb", "", "Acme")
        self.assertIsNone(result)

    def test_empty_company_returns_none(self):
        self._insert("aaa", "Engineer", "Acme")
        result = find_duplicate("bbb", "Engineer", "")
        self.assertIsNone(result)

    def test_whitespace_normalized(self):
        self._insert("aaa", "Senior  Engineer", "Acme")
        result = find_duplicate("bbb", "Senior Engineer", "Acme")
        self.assertIsNotNone(result)


class AddJobDedup(_TempDBMixin, unittest.TestCase):
    def test_add_job_with_dup_title_company_returns_none(self):
        jid1 = add_job({"url": "https://linkedin.com/jobs/view/100",
                        "title": "Senior Engineer", "company": "Acme"})
        self.assertIsNotNone(jid1)
        jid2 = add_job({"url": "https://linkedin.com/jobs/view/200",
                        "title": "Senior Engineer", "company": "Acme"})
        self.assertIsNone(jid2)

    def test_add_job_different_title_inserts(self):
        jid1 = add_job({"url": "https://linkedin.com/jobs/view/100",
                        "title": "Senior Engineer", "company": "Acme"})
        jid2 = add_job({"url": "https://linkedin.com/jobs/view/200",
                        "title": "Junior Engineer", "company": "Acme"})
        self.assertIsNotNone(jid1)
        self.assertIsNotNone(jid2)

    def test_add_job_no_title_inserts(self):
        jid1 = add_job({"url": "https://linkedin.com/jobs/view/100",
                        "title": "Senior Engineer", "company": "Acme"})
        jid2 = add_job({"url": "https://linkedin.com/jobs/view/200"})
        self.assertIsNotNone(jid1)
        self.assertIsNotNone(jid2)

    def test_add_job_same_url_returns_existing_jid(self):
        jid1 = add_job({"url": "https://linkedin.com/jobs/view/100",
                        "title": "Senior Engineer", "company": "Acme"})
        jid2 = add_job({"url": "https://linkedin.com/jobs/view/100",
                        "title": "Senior Engineer", "company": "Acme"})
        self.assertEqual(jid1, jid2)


if __name__ == "__main__":
    unittest.main()
