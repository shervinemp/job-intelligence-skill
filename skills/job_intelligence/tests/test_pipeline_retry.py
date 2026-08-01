"""Unit tests for pipeline-script retry/failure-count bugs."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class EnrichRetry(unittest.TestCase):
    """enrich.py cmd_retry must retry jobs with state='failed'.
    (stage can never be 'failed' — schema CHECK constraint — so the
    old stage-based filter silently matched nothing.)"""

    def _state(self):
        return {
            "jobs": {
                "1111111111111111": {
                    "id": "1111111111111111", "title": "A",
                    "stage": "extracted", "state": "failed",
                    "url": "https://example.com/a",
                },
                "2222222222222222": {
                    "id": "2222222222222222", "title": "B",
                    "stage": "described", "state": "failed",
                    "url": "https://example.com/b",
                },
                "3333333333333333": {
                    "id": "3333333333333333", "title": "C",
                    "stage": "extracted", "state": "active",
                    "url": "https://example.com/c",
                },
            }
        }

    def test_retry_fetches_failed_state_jobs_only(self):
        from enrich import cmd_retry
        state = self._state()
        fetch_calls = []

        def fake_fetch(url, use_playwright=False):
            fetch_calls.append(url)
            return True, f"description for {url}", "Page Title", "<html></html>"

        with patch("enrich.load", return_value=state), \
             patch("enrich._fetch_from_url", side_effect=fake_fetch), \
             patch("enrich.save_description"), \
             patch("enrich.auth_walls.remove"), \
             patch("enrich.advance"):
            cmd_retry(use_playwright=False)

        self.assertEqual(sorted(fetch_calls), [
            "https://example.com/a",
            "https://example.com/b",
        ])
        self.assertNotIn("https://example.com/c", fetch_calls)

    def test_retry_none_failed_prints_no_failed(self):
        from enrich import cmd_retry
        state = {"jobs": {
            "3333333333333333": {
                "id": "3333333333333333", "title": "C",
                "stage": "extracted", "state": "active",
                "url": "https://example.com/c",
            }
        }}
        with patch("enrich.load", return_value=state), \
             patch("enrich._fetch_from_url") as fetch_mock:
            cmd_retry(use_playwright=False)
        fetch_mock.assert_not_called()


class TailorFailedCounts(unittest.TestCase):
    """tailor.py must count failures from states.failed, not stages.failed
    (stages only ever contains stage names)."""

    def test_cmd_craft_reports_failed_count(self):
        from tailor import cmd_craft
        state = {
            "jobs": {
                "1111111111111111": {
                    "id": "1111111111111111", "title": "A",
                    "stage": "described", "state": "failed",
                    "url": "https://example.com/a",
                }
            },
            "stages": {"extracted": 0, "described": 1, "tailored": 0, "applied": 0},
            "states": {"active": 0, "rejected": 0, "failed": 1},
        }
        with patch("tailor.load", return_value=state), \
             patch("sys.stderr") as stderr_mock:
            cmd_craft(auto=False)
        printed = "".join(call.args[0] for call in stderr_mock.write.call_args_list)
        self.assertIn("1 failed", printed)
        self.assertIn("NO_PENDING", printed)

    def test_cmd_craft_all_done_when_no_failed(self):
        from tailor import cmd_craft
        state = {
            "jobs": {},
            "stages": {"extracted": 0, "described": 0, "tailored": 0, "applied": 0},
            "states": {"active": 0, "rejected": 0, "failed": 0},
        }
        with patch("tailor.load", return_value=state), \
             patch("sys.stderr") as stderr_mock:
            cmd_craft(auto=False)
        printed = "".join(call.args[0] for call in stderr_mock.write.call_args_list)
        self.assertIn("ALL_DONE", printed)


if __name__ == "__main__":
    unittest.main()
