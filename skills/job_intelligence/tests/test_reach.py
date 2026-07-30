"""Tests for reach.py and lib/contacts/discover.py — contact discovery helpers, keyword building, URL logic."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.contacts.discover import _build_team_keywords
from reach import (
    cmd_discover, cmd_discover_all, cmd_list, cmd_email, cmd_message, cmd_connect,
    cmd_status, cmd_retry, cmd_undo, cmd_update,
)


class BuildTeamKeywords(unittest.TestCase):
    def test_team_name_produces_keywords(self):
        kws = _build_team_keywords("AI/ML", "")
        self.assertIn("ai", kws)
        self.assertIn("ml", kws)

    def test_team_name_filters_stopwords(self):
        kws = _build_team_keywords("Product and Engineering", "")
        self.assertNotIn("and", kws)
        self.assertNotIn("the", kws)

    def test_team_name_multi_word(self):
        kws = _build_team_keywords("Data Science & Analytics", "")
        self.assertIn("data", kws)
        self.assertIn("science", kws)
        self.assertNotIn("&", kws)

    def test_job_title_fallback_engineer(self):
        kws = _build_team_keywords("", "Senior Software Engineer")
        self.assertTrue(any(k in kws for k in ["engineering", "software", "development"]))

    def test_job_title_fallback_scientist(self):
        kws = _build_team_keywords("", "Machine Learning Scientist")
        self.assertTrue(any(k in kws for k in ["science", "data", "research", "ai", "ml"]))

    def test_job_title_fallback_designer(self):
        kws = _build_team_keywords("", "Product Designer")
        self.assertTrue(any(k in kws for k in ["design", "ux", "ui"]))

    def test_no_team_no_title(self):
        kws = _build_team_keywords("", "")
        self.assertEqual(kws, [])

    def test_no_team_job_title_none(self):
        kws = _build_team_keywords("", "Platform Engineer")
        self.assertTrue(any(k in kws for k in ["engineering", "software", "development"]))


class CommandFunctionsExist(unittest.TestCase):
    def test_cmd_discover_exists(self):
        self.assertTrue(callable(cmd_discover))

    def test_cmd_list_exists(self):
        self.assertTrue(callable(cmd_list))

    def test_cmd_email_exists(self):
        self.assertTrue(callable(cmd_email))

    def test_cmd_message_exists(self):
        self.assertTrue(callable(cmd_message))

    def test_cmd_connect_exists(self):
        self.assertTrue(callable(cmd_connect))

    def test_cmd_status_exists(self):
        self.assertTrue(callable(cmd_status))

    def test_cmd_retry_exists(self):
        self.assertTrue(callable(cmd_retry))

    def test_cmd_undo_exists(self):
        self.assertTrue(callable(cmd_undo))

    def test_cmd_update_exists(self):
        self.assertTrue(callable(cmd_update))

    def test_cmd_discover_all_exists(self):
        self.assertTrue(callable(cmd_discover_all))

    def test_cmd_email_accepts_force_kwarg(self):
        import inspect
        sig = inspect.signature(cmd_email)
        self.assertIn("force", sig.parameters)
        self.assertEqual(sig.parameters["force"].default, False)

    def test_cmd_message_accepts_force_kwarg(self):
        import inspect
        sig = inspect.signature(cmd_message)
        self.assertIn("force", sig.parameters)
        self.assertEqual(sig.parameters["force"].default, False)


class AttemptHelpersExist(unittest.TestCase):
    def test_attempt_add_importable(self):
        from lib.db.contacts import attempt_add, attempt_list
        self.assertTrue(callable(attempt_add))
        self.assertTrue(callable(attempt_list))

    def test_attempt_exported_from_lib_db(self):
        from lib.db import attempt_add, attempt_list
        self.assertTrue(callable(attempt_add))
        self.assertTrue(callable(attempt_list))


if __name__ == "__main__":
    unittest.main()
