"""Job identity and duplicate detection.

Two failure modes matter here and they pull in opposite directions:

  * a FALSE duplicate silently discards a real posting (add_job returns
    None, and callers collapse that into `if jid:`), so the user simply
    never sees the job — the expensive, invisible error;
  * a MISSED duplicate costs one `reject` command.

So the fuzzy phase is deliberately conservative, and every drop is
announced.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _DB:
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        import lib.db.schema as schema
        schema._conn = None
        schema.DB_PATH = os.path.join(self._tmp, "t.db")
        schema.DB_DIR = self._tmp
        self.conn = schema.get_conn()

    def tearDown(self):
        import shutil
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)


class DuplicateDetection(_DB, unittest.TestCase):
    def _add(self, url, title, company):
        from lib.db.jobs import add_job
        return add_job({"url": url, "title": title, "company": company})

    def test_exact_duplicate_is_blocked(self):
        a = self._add("https://x.com/1", "Software Engineer", "Acme")
        self.assertIsNotNone(a)
        b = self._add("https://x.com/2", "Software Engineer", "Acme")
        self.assertIsNone(b, "an exact title+company repeat should collapse")

    def test_whitespace_variants_still_collapse(self):
        self._add("https://x.com/1", "Software   Engineer", "Acme")
        b = self._add("https://x.com/2", "Software Engineer", "Acme")
        self.assertIsNone(b)

    def test_seniority_variants_are_distinct_jobs(self):
        """0.6 token overlap collapsed these; they are different roles a
        candidate may want both of."""
        cases = [
            ("Senior Software Engineer", "Software Engineer"),
            ("Software Engineer II", "Software Engineer III"),
            ("Machine Learning Engineer", "Senior Machine Learning Engineer"),
            ("Data Scientist", "Senior Data Scientist"),
            ("Staff Engineer", "Engineer"),
        ]
        for i, (first, second) in enumerate(cases):
            with self.subTest(pair=(first, second)):
                self._add(f"https://x.com/a{i}", first, "Acme")
                got = self._add(f"https://x.com/b{i}", second, "Acme")
                self.assertIsNotNone(
                    got, f"{second!r} was discarded as a duplicate of {first!r}")

    def test_different_companies_never_collapse(self):
        self._add("https://x.com/1", "Software Engineer", "Acme")
        b = self._add("https://x.com/2", "Software Engineer", "Globex")
        self.assertIsNotNone(b)

    def test_duplicate_drop_is_announced(self):
        """A silent drop is indistinguishable from 'never in the email'."""
        import contextlib
        import io
        self._add("https://x.com/1", "Software Engineer", "Acme")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self._add("https://x.com/2", "Software Engineer", "Acme")
        self.assertIn("DUPLICATE_SKIPPED", buf.getvalue())


class JidResolution(_DB, unittest.TestCase):
    def _mk(self, jid):
        self.conn.execute(
            "INSERT INTO jobs (id, url, title, company, stage, state, scripts) "
            "VALUES (?, 'u', 'T', 'C', 'tailored', 'active', '[]')", (jid,))
        self.conn.commit()

    def test_full_id_resolves(self):
        from lib.db.jobs import get_job
        self._mk("abcdef0123456789")
        self.assertEqual(get_job("abcdef0123456789")["id"], "abcdef0123456789")

    def test_unambiguous_prefix_resolves(self):
        """Logs display a 12-char prefix; identity must survive that."""
        from lib.db.jobs import get_job
        self._mk("abcdef0123456789")
        self.assertEqual(get_job("abcdef012345")["id"], "abcdef0123456789")

    def test_ambiguous_prefix_resolves_to_nothing(self):
        """Picking one of two silently is how the wrong job gets submitted."""
        from lib.db.jobs import get_job
        self._mk("abcdef0123456789")
        self._mk("abcdef0123456780")
        self.assertIsNone(get_job("abcdef012345"))

    def test_like_wildcards_do_not_match(self):
        """'%' and '_' are LIKE wildcards — unescaped, they matched any job."""
        from lib.db.jobs import get_job
        self._mk("abcdef0123456789")
        for probe in ("%", "_", "____________", "abcdef01234_", "%789"):
            with self.subTest(probe=probe):
                self.assertIsNone(get_job(probe))

    def test_non_hex_prefix_rejected(self):
        from lib.db.jobs import get_job
        self._mk("abcdef0123456789")
        self.assertIsNone(get_job("zzzz"))


class SqlNormalizationParity(unittest.TestCase):
    def test_sql_and_python_normalizers_agree(self):
        """find_duplicate compares a Python-normalized param against a
        SQL-normalized column; if they disagree the exact-match phase
        silently misses. SQLite REPLACE is a single non-overlapping pass,
        so 'a   b' needed more than one collapse."""
        import sqlite3
        from lib.db.jobs import _sql_norm_col, _whitespace_normalize
        c = sqlite3.connect(":memory:")
        for s in ("a   b", "a  b", "  Senior    Software   Engineer  ",
                  "a\t\tb", "a\n\nb", "plain"):
            with self.subTest(s=s):
                got = c.execute(f"SELECT {_sql_norm_col('?')}", (s,)).fetchone()[0]
                self.assertEqual(got, _whitespace_normalize(s).lower())


if __name__ == "__main__":
    unittest.main()
