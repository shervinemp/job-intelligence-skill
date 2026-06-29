"""Unit tests for JSON-LD JobPosting extraction (lib/extract_structured.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.extract_structured import extract_job_postings, _num


def _ld(obj_json):
    return f'<html><script type="application/ld+json">{obj_json}</script></html>'


class Num(unittest.TestCase):
    def test_coerces_strings_and_numbers(self):
        self.assertEqual(_num(85000), 85000)
        self.assertEqual(_num("85,000"), 85000.0)
        self.assertEqual(_num("$90000"), 90000.0)
        self.assertIsNone(_num("competitive"))
        self.assertIsNone(_num(None))


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


if __name__ == "__main__":
    unittest.main()
