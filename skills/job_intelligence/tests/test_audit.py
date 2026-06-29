"""Unit tests for the apply audit log (apply/common/audit.py)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common import audit


class Categorize(unittest.TestCase):
    def test_eeo_by_decline_option(self):
        self.assertEqual(
            audit.categorize("Gender", options=["Male", "Female", "Prefer not to say"]),
            "eeo",
        )

    def test_salary(self):
        self.assertEqual(audit.categorize("Expected salary (CAD)"), "salary")

    def test_legal(self):
        self.assertEqual(audit.categorize("Are you authorized to work in Canada?"), "legal")

    def test_freetext_textarea(self):
        self.assertEqual(audit.categorize("Why do you want this role?", tag="TEXTAREA"), "freetext")

    def test_generic(self):
        self.assertEqual(audit.categorize("First name", tag="INPUT"), "generic")


class LogRoundTrip(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._saved = os.environ.get("JI_HOME")
        os.environ["JI_HOME"] = self._home

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("JI_HOME", None)
        else:
            os.environ["JI_HOME"] = self._saved

    def test_write_and_summarize(self):
        jid = "job123"
        audit.log_field(jid, "Email", "b@x.com", provenance="ephemeral", category="generic", filled=True)
        audit.log_field(jid, "Cover letter", "", provenance="no_match", category="freetext", filled=False)
        audit.log_event(jid, "submit_blocked", mode="shadow", detail="Submit application")

        s = audit.summarize(jid)
        self.assertEqual(s["fields"], 2)
        self.assertEqual(s["filled"], 1)
        self.assertEqual(s["by_provenance"]["ephemeral"], 1)
        self.assertEqual(s["by_provenance"]["no_match"], 1)
        self.assertEqual(s["by_category"]["freetext"], 1)
        self.assertIn("submit_blocked", s["events"])

    def test_summarize_missing_log_is_empty(self):
        s = audit.summarize("nonexistent")
        self.assertEqual(s["fields"], 0)
        self.assertEqual(s["events"], [])


if __name__ == "__main__":
    unittest.main()
