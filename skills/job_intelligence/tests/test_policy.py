"""Unit tests for apply submission policy (apply/common/policy.py).

Mode resolution gates whether an unattended run actually submits, so the default
(live) and the override precedence (cli > env > file > default) are worth pinning.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common import policy


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

    def test_default_is_live(self):
        self.assertEqual(policy.resolve_mode(), "live")
        self.assertTrue(policy.submits_for_real("live"))

    def test_env_override(self):
        os.environ["JI_APPLY_MODE"] = "shadow"
        self.assertEqual(policy.resolve_mode(), "shadow")
        self.assertFalse(policy.submits_for_real("shadow"))

    def test_invalid_mode_falls_back_to_live(self):
        os.environ["JI_APPLY_MODE"] = "bogus"
        self.assertEqual(policy.resolve_mode(), "live")

    def test_cli_override_wins(self):
        os.environ["JI_APPLY_MODE"] = "live"
        self.assertEqual(policy.resolve_mode("shadow"), "shadow")

    def test_file_policy(self):
        with open(os.path.join(self._home, "apply_policy.json"), "w") as f:
            f.write('{"mode": "hold"}')
        self.assertEqual(policy.resolve_mode(), "hold")
        self.assertFalse(policy.submits_for_real("hold"))


if __name__ == "__main__":
    unittest.main()
