"""F2 — new-domain approval gate (ALGORITHMS.md Part 6)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JI_HOME", os.path.expanduser("~/.ji"))


class DomainGate(unittest.TestCase):
    def setUp(self):
        from apply.common.domain_gate import deny
        for h in ("jobs.approved.io", "jobs.newfake.com", "prior.io"):
            deny(h)

    def tearDown(self):
        from apply.common.domain_gate import deny
        for h in ("jobs.approved.io", "jobs.newfake.com", "prior.io"):
            deny(h)

    def test_new_domain_blocked(self):
        from apply.common.domain_gate import gate
        allowed, reason = gate("jobs.newfake.com")
        self.assertFalse(allowed)
        self.assertIn("new domain", reason)

    def test_approve_opens_gate(self):
        from apply.common.domain_gate import gate, approve
        approve("jobs.approved.io")
        allowed, reason = gate("jobs.approved.io")
        self.assertTrue(allowed)

    def test_submit_decision_respects_gate(self):
        from apply.common.gate import submit_decision
        action, reason = submit_decision("live", {}, host="jobs.newfake.com")
        self.assertEqual(action, "blocked")
        self.assertIn("new domain", reason)

    def test_prior_applied_job_auto_approves(self):
        """A domain with a prior successful submission is approved without
        explicit sign-off (strongest evidence)."""
        from apply.common.domain_gate import has_prior_success
        # No DB write here — assert the helper returns False when no applied
        # job exists (the DB path is exercised in integration).
        self.assertFalse(has_prior_success("definitely-no-jobs.invalid"))

    def test_no_host_fails_open(self):
        """A URL-less submit must not be blocked by the domain gate."""
        from apply.common.gate import submit_decision
        action, reason = submit_decision("live", {}, host="")
        self.assertEqual(action, "submit")


if __name__ == "__main__":
    unittest.main()
