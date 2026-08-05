"""Unit tests for JSON-LD JobPosting extraction (lib/extract_structured.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.extract_structured import extract_job_postings


def _ld(obj_json):
    return f'<html><script type="application/ld+json">{obj_json}</script></html>'


class Extract(unittest.TestCase):
    def test_basic_jobposting(self):
        out = extract_job_postings(_ld(
            '{"@type":"JobPosting","title":"Dev","hiringOrganization":{"name":"Acme"},'
            '"jobLocation":{"address":{"addressLocality":"Ottawa"}}}'))
        self.assertEqual(out[0]["title"], "Dev")
        self.assertEqual(out[0]["company"], "Acme")
        self.assertEqual(out[0]["location"], "Ottawa")

    def test_string_salary_does_not_crash(self):
        # JSON-LD salary as a string used to raise ValueError on the ',' format spec.
        out = extract_job_postings(_ld(
            '{"@type":"JobPosting","title":"X","baseSalary":{"value":'
            '{"minValue":"85000","maxValue":"95000","currency":"CAD"}}}'))
        self.assertEqual(out[0]["salary"], "$85,000 - $95,000 CAD")

    def test_graph_wrapper_unwrapped(self):
        out = extract_job_postings(_ld(
            '{"@context":"https://schema.org","@graph":['
            '{"@type":"WebSite"},{"@type":"JobPosting","title":"Graphed"}]}'))
        self.assertTrue(any(r.get("title") == "Graphed" for r in out))

    def test_prose_salary_range(self):
        from enrich import _extract_salary_prose
        self.assertEqual(_extract_salary_prose("Salary: $120k - $150k CAD"),
                         "120k - 150k CAD")
        self.assertIsNone(_extract_salary_prose("No salary mentioned"))

    def test_prose_location(self):
        from enrich import _extract_location_prose
        self.assertEqual(
            _extract_location_prose("Location: Toronto, Ontario, Canada"),
            "Toronto, Ontario")
        self.assertEqual(_extract_location_prose("City: Montreal, Quebec"),
                         "Montreal, Quebec")

    def test_auth_wall_classification(self):
        from enrich import _classify_auth_wall
        self.assertEqual(_classify_auth_wall("Your session has expired")[0],
                         "session_expired")
        self.assertEqual(_classify_auth_wall("Enter the verification code")[0],
                         "2fa")
        self.assertEqual(_classify_auth_wall("Create account to view")[0],
                         "create_account")
        self.assertEqual(_classify_auth_wall("Please sign in to continue")[0],
                         "login")
        self.assertIsNone(_classify_auth_wall("A normal job description"))


if __name__ == "__main__":
    unittest.main()
