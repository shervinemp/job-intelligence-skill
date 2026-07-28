"""Unit tests for job deduplication (lib/db/jobs.py find_duplicate + add_job)."""
import os, sys, unittest, tempfile, shutil
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lib.db.schema as schema
from lib.db.jobs import find_duplicate, add_job, _whitespace_normalize, _token_overlap


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
        result = find_duplicate("bbb", "Junior QA Engineer", "Acme")
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
                        "title": "Junior QA Engineer", "company": "Acme"})
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



class WhitespaceNormalization(_TempDBMixin, unittest.TestCase):
    def test_tab_in_stored_title_matches(self):
        self.conn.execute(
            "INSERT INTO jobs (id, url, title, company, stage, state, created_at, updated_at, scripts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]')",
            ("aaa", "https://example.com/aaa", "Senior\tEngineer", "Acme", "extracted", "active",
             datetime.now().isoformat(), datetime.now().isoformat()),
        )
        self.conn.commit()
        result = find_duplicate("bbb", "Senior Engineer", "Acme")
        self.assertIsNotNone(result)

    def test_newline_in_stored_title_matches(self):
        self.conn.execute(
            "INSERT INTO jobs (id, url, title, company, stage, state, created_at, updated_at, scripts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]')",
            ("aaa", "https://example.com/aaa", "Senior\nEngineer", "Acme", "extracted", "active",
             datetime.now().isoformat(), datetime.now().isoformat()),
        )
        self.conn.commit()
        result = find_duplicate("bbb", "Senior Engineer", "Acme")
        self.assertIsNotNone(result)

    def test_multiple_spaces_normalized(self):
        self.conn.execute(
            "INSERT INTO jobs (id, url, title, company, stage, state, created_at, updated_at, scripts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]')",
            ("aaa", "https://example.com/aaa", "Senior   Engineer", "Acme", "extracted", "active",
             datetime.now().isoformat(), datetime.now().isoformat()),
        )
        self.conn.commit()
        result = find_duplicate("bbb", "Senior Engineer", "Acme")
        self.assertIsNotNone(result)

    def test_whitespace_normalize_helper(self):
        self.assertEqual(_whitespace_normalize("hello   world"), "hello world")
        self.assertEqual(_whitespace_normalize("hello\tworld"), "hello world")
        self.assertEqual(_whitespace_normalize("hello\nworld"), "hello world")
        self.assertEqual(_whitespace_normalize("  hello world  "), "hello world")
        self.assertEqual(_whitespace_normalize(None), "")


class TokenOverlap(_TempDBMixin, unittest.TestCase):
    def test_sr_matches_senior_same_company(self):
        self._insert("aaa", "Senior Python Engineer", "Acme")
        result = find_duplicate("bbb", "Sr. Python Engineer", "Acme")
        self.assertIsNotNone(result)

    def test_different_company_no_token_match(self):
        self._insert("aaa", "Senior Engineer", "Acme")
        result = find_duplicate("bbb", "Senior Engineer", "Beta")
        self.assertIsNone(result)

    def test_low_token_overlap_no_match(self):
        self._insert("aaa", "Senior Python Engineer Backend", "Acme")
        result = find_duplicate("bbb", "Senior QA Tester", "Acme")
        self.assertIsNone(result)

    def test_token_overlap_helper(self):
        self.assertGreaterEqual(_token_overlap("Senior Engineer", "Sr. Engineer"), 0.6)
        self.assertGreaterEqual(_token_overlap("Python Developer", "Python Developer"), 0.6)
        self.assertLess(_token_overlap("Senior Python Engineer", "Senior QA Tester"), 0.6)
        self.assertGreaterEqual(_token_overlap("Senior Python Engineer", "Senior Software Engineer"), 0.6)
        self.assertLess(_token_overlap("Senior Engineer", "Junior QA Engineer"), 0.6)
        self.assertEqual(_token_overlap("", "Anything"), 0.0)


class AddJobSkipKnown(_TempDBMixin, unittest.TestCase):
    def test_skip_known_returns_none_for_existing_url(self):
        jid1 = add_job({"url": "https://linkedin.com/jobs/view/100",
                        "title": "Engineer", "company": "Acme"})
        self.assertIsNotNone(jid1)
        result = add_job({"url": "https://linkedin.com/jobs/view/100",
                          "title": "Engineer", "company": "Acme"},
                         skip_known=True)
        self.assertIsNone(result)

    def test_skip_known_still_inserts_new_job(self):
        result = add_job({"url": "https://linkedin.com/jobs/view/999",
                          "title": "New Role", "company": "NewCo"},
                         skip_known=True)
        self.assertIsNotNone(result)

    def test_skip_known_true_but_dup_title_company_still_none(self):
        add_job({"url": "https://linkedin.com/jobs/view/100",
                 "title": "Engineer", "company": "Acme"})
        result = add_job({"url": "https://linkedin.com/jobs/view/200",
                          "title": "Engineer", "company": "Acme"},
                         skip_known=True)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
