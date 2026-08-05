"""test_drift.py — the drift detector (apply/common/drift.py).

Cheap self-test: all the heavy deps (corpus snapshots, jsdom CorpusPage,
probe_all) are stubbed; the tests pin the DETECTION LOGIC — when the recorded
strategy no longer matches the current best, the snapshot is flagged stale and
(auto)demoted, and dry-run reports without demoting.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _result(strategy, field_count):
    r = MagicMock()
    r.strategy = strategy
    r.field_count = field_count
    return r


class DriftDetector(unittest.TestCase):
    def _run(self, entries, best=None, dry_run=False, clear_returns=True):
        """Run drift.run with the corpus/probe stubbed.

        drift.run imports its deps INSIDE the function from the source modules,
        so the patches target those modules, not drift's namespace."""
        from apply.common.drift import run
        sidecar = {
            "url": "https://corpus.test/j", "platform": "acme",
            "winning_strategy": "standard",
            "winning_widgets": {},
            "capability_profile": {},
        }
        probe = best or _result("standard", 5)
        with patch("apply.common.mock_page.is_available", return_value=True), \
             patch("apply.common.corpus.list_all", return_value=entries), \
             patch("apply.common.corpus.load_html", return_value="<html/>"), \
             patch("apply.common.corpus.load_sidecar", return_value=sidecar), \
             patch("apply.common.capabilities.discover_widgets",
                   return_value={}), \
             patch("apply.common.inspector.probe_all",
                   return_value=(probe, [probe])), \
             patch("apply.common.observations.clear_hash",
                   return_value=clear_returns):
            return run(dry_run=dry_run)

    def test_no_jsdom_returns_error(self):
        from apply.common.drift import run
        with patch("apply.common.mock_page.is_available", return_value=False):
            s = run()
        self.assertEqual(s.get("error"), "jsdom not available")

    def test_no_entries_is_idle(self):
        s = self._run([])
        self.assertEqual(s["checked"], 0)

    def test_agree_when_current_matches_recorded(self):
        s = self._run([{"profile_hash": "aaaa"}])
        self.assertEqual(s["checked"], 1)
        self.assertEqual(s["agree"], 1)
        self.assertEqual(s["stale"], 0)
        self.assertEqual(s["demoted"], [])

    def test_stale_demotes_by_default(self):
        from apply.common.drift import run
        sidecar = {"winning_strategy": "vision", "winning_widgets": {},
                   "capability_profile": {}, "platform": "acme"}
        entries = [{"profile_hash": "aaaa"}]
        with patch("apply.common.mock_page.is_available", return_value=True), \
             patch("apply.common.corpus.list_all", return_value=entries), \
             patch("apply.common.corpus.load_html", return_value="<html/>"), \
             patch("apply.common.corpus.load_sidecar", return_value=sidecar), \
             patch("apply.common.capabilities.discover_widgets",
                   return_value={}), \
             patch("apply.common.inspector.probe_all",
                   return_value=(_result("standard", 5),
                                 [_result("standard", 5)])), \
             patch("apply.common.observations.clear_hash",
                   return_value=True) as clr:
            s = run(dry_run=False)
        self.assertEqual(s["stale"], 1)
        clr.assert_called_once()
        self.assertEqual(s["demoted"], ["aaaa"])

    def test_dry_run_does_not_demote(self):
        from apply.common.drift import run
        sidecar = {"winning_strategy": "vision", "winning_widgets": {},
                   "capability_profile": {}, "platform": "acme"}
        with patch("apply.common.mock_page.is_available", return_value=True), \
             patch("apply.common.corpus.list_all",
                   return_value=[{"profile_hash": "aaaa"}]), \
             patch("apply.common.corpus.load_html", return_value="<html/>"), \
             patch("apply.common.corpus.load_sidecar", return_value=sidecar), \
             patch("apply.common.capabilities.discover_widgets",
                   return_value={}), \
             patch("apply.common.inspector.probe_all",
                   return_value=(_result("standard", 5),
                                 [_result("standard", 5)])), \
             patch("apply.common.observations.clear_hash") as clr:
            s = run(dry_run=True)
        self.assertEqual(s["stale"], 1)
        clr.assert_not_called()
        self.assertEqual(s["demoted"], [])

    def test_probe_error_is_recorded_not_fatal(self):
        from apply.common.drift import run
        sidecar = {"winning_strategy": "standard", "winning_widgets": {},
                   "capability_profile": {}, "platform": "acme"}
        with patch("apply.common.mock_page.is_available", return_value=True), \
             patch("apply.common.corpus.list_all",
                   return_value=[{"profile_hash": "aaaa"}]), \
             patch("apply.common.corpus.load_html", return_value="<html/>"), \
             patch("apply.common.corpus.load_sidecar", return_value=sidecar), \
             patch("apply.common.capabilities.discover_widgets",
                   return_value={}), \
             patch("apply.common.inspector.probe_all",
                   side_effect=RuntimeError("jsdom exploded")):
            s = run()
        # the snapshot is recorded as an error, the run continues
        self.assertEqual(s["checked"], 0)
        self.assertEqual(len(s["details"]), 1)
        self.assertEqual(s["details"][0]["status"], "error")

    def test_skip_missing_hash(self):
        s = self._run([{"profile_hash": ""}])
        self.assertEqual(s["checked"], 0)


if __name__ == "__main__":
    unittest.main()
