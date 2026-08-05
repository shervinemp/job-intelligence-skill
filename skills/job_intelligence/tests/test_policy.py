"""Unit tests for apply submission policy (apply/common/submit_policy.py).

Mode resolution gates whether an unattended run actually submits, so the default
(hold) and the override precedence (cli > env > file > default) are worth pinning.

Every pin here is a fail-closed pin: absent, unreadable, malformed and typo'd
configuration must all resolve to 'hold'. A safety control whose failure mode
is "submit for real" is worse than no control at all.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common import submit_policy


class PolicyModes(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in ("JI_HOME", "JI_APPLY_MODE")}
        os.environ["JI_HOME"] = self._home
        os.environ.pop("JI_APPLY_MODE", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_is_hold(self):
        """ETHOS §11: hold-mode by default. Live submits require an
        explicit act (policy file or env) — never an inherited default."""
        self.assertEqual(submit_policy.resolve_mode(), "hold")
        self.assertFalse(submit_policy.submits_for_real(submit_policy.resolve_mode()))

    def test_missing_policy_file_holds(self):
        """The most likely failure of a safety control is its file being
        absent. That must close the gate, not open it."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.environ["JI_HOME"] = d  # no apply_policy.json inside
            self.assertEqual(submit_policy.load_policy()["mode"], "hold")

    def test_corrupt_policy_file_fails_closed(self):
        """Malformed JSON previously fell through to the 'live' default."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.environ["JI_HOME"] = d
            with open(os.path.join(d, "apply_policy.json"), "w") as f:
                f.write("{not valid json")
            self.assertEqual(submit_policy.load_policy()["mode"], "hold")

    def test_explicit_live_is_honoured(self):
        """Fail-closed must not mean un-usable: an explicit live policy works."""
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.environ["JI_HOME"] = d
            with open(os.path.join(d, "apply_policy.json"), "w") as f:
                _json.dump({"mode": "live"}, f)
            self.assertEqual(submit_policy.load_policy()["mode"], "live")
            self.assertTrue(submit_policy.submits_for_real("live"))

    def test_env_override(self):
        os.environ["JI_APPLY_MODE"] = "shadow"
        self.assertEqual(submit_policy.resolve_mode(), "shadow")
        self.assertFalse(submit_policy.submits_for_real("shadow"))

    def test_invalid_mode_fails_closed_to_hold(self):
        # A typo in a safety control must never cause real submits.
        os.environ["JI_APPLY_MODE"] = "bogus"
        self.assertEqual(submit_policy.resolve_mode(), "hold")
        self.assertFalse(submit_policy.submits_for_real(submit_policy.resolve_mode()))

    def test_invalid_file_mode_fails_closed_to_hold(self):
        with open(os.path.join(self._home, "apply_policy.json"), "w") as f:
            f.write('{"mode": "shdow"}')
        self.assertEqual(submit_policy.resolve_mode(), "hold")

    def test_cli_override_wins(self):
        os.environ["JI_APPLY_MODE"] = "live"
        self.assertEqual(submit_policy.resolve_mode("shadow"), "shadow")

    def test_file_policy(self):
        with open(os.path.join(self._home, "apply_policy.json"), "w") as f:
            f.write('{"mode": "hold"}')
        self.assertEqual(submit_policy.resolve_mode(), "hold")
        self.assertFalse(submit_policy.submits_for_real("hold"))


if __name__ == "__main__":
    unittest.main()
