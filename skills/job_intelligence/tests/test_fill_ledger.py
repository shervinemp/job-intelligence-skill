"""The fill ledger — ETHOS §10's missing denominator.

The ledger exists because `kind` and `verdict` answer different
questions, and the pipeline only ever had the first:

    kind    : did the value land?   (mechanical, and tautological for
              semantic error — the verifier re-scores with the same
              scorer that chose the value)
    verdict : was the value RIGHT?  (adjudicated, the only correctness
              signal in the system)

These tests pin that separation, and pin the reporting honesty rule: an
un-adjudicated ledger yields UNKNOWN, never zero.
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
        self.conn.execute(
            "INSERT INTO jobs (id, url, title, company, stage, state, scripts) "
            "VALUES ('a'||'aaaaaaaaaaaaaaa', 'u', 'T', 'C', 'tailored', 'active', '[]')")
        self.conn.commit()
        self.jid = "a" + "a" * 15

    def tearDown(self):
        import shutil
        import lib.db.schema as schema
        if schema._conn:
            schema._conn.close()
        schema._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fields(self):
        return [
            {"label": "Country", "answer": "Canada", "kind": "verified",
             "method": "combobox", "reason": "verified", "required": True,
             "selected_text": "Canada"},
            {"label": "Gender", "answer": "Male", "kind": "unverified",
             "method": "select", "reason": "accepted_unverified",
             "required": False, "selected_text": ""},
            {"label": "Years of experience", "answer": "6",
             "kind": "verified", "method": "text", "reason": "verified",
             "required": True, "selected_text": "60"},
        ]


class Recording(_DB, unittest.TestCase):
    def test_records_one_row_per_field(self):
        from lib.db.fills import record_fills
        n = record_fills(self.jid, self._fields(), run_id="r1",
                         url="https://boards.greenhouse.io/acme/jobs/1")
        self.assertEqual(n, 3)
        rows = self.conn.execute("SELECT * FROM field_fills").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["platform"], "greenhouse.io")
        self.assertEqual(rows[0]["run_id"], "r1")

    def test_verdict_starts_null(self):
        """Recording is not judging. A fresh ledger has no opinion."""
        from lib.db.fills import record_fills
        record_fills(self.jid, self._fields(), url="https://x.com/j")
        verdicts = [r["verdict"] for r in
                    self.conn.execute("SELECT verdict FROM field_fills")]
        self.assertTrue(all(v is None for v in verdicts))

    def test_empty_field_list_is_a_noop(self):
        from lib.db.fills import record_fills
        self.assertEqual(record_fills(self.jid, [], url="https://x.com/j"), 0)


class Sampling(_DB, unittest.TestCase):
    def test_unverified_and_divergent_reads_come_first(self):
        """A uniform sample wastes the reviewer on the boring 90%. The
        risky ones are: unverified, then read-back != intended."""
        from lib.db.fills import record_fills, sample_for_adjudication
        record_fills(self.jid, self._fields(), url="https://x.com/j")
        got = sample_for_adjudication(limit=3)
        self.assertEqual(got[0]["kind"], "unverified")
        self.assertEqual(got[1]["label"], "Years of experience")  # 6 -> "60"

    def test_adjudicated_rows_leave_the_queue(self):
        from lib.db.fills import record_fills, sample_for_adjudication, adjudicate
        record_fills(self.jid, self._fields(), url="https://x.com/j")
        first = sample_for_adjudication(limit=1)[0]
        adjudicate(first["id"], "correct")
        self.assertNotIn(first["id"],
                         [r["id"] for r in sample_for_adjudication(limit=10)])


class Adjudication(_DB, unittest.TestCase):
    def test_rejects_an_unknown_verdict(self):
        from lib.db.fills import record_fills, adjudicate
        record_fills(self.jid, self._fields(), url="https://x.com/j")
        with self.assertRaises(ValueError):
            adjudicate(1, "probably-fine")

    def test_unknown_id_reports_false(self):
        from lib.db.fills import adjudicate
        self.assertFalse(adjudicate(99999, "correct"))


class WrongfillReporting(_DB, unittest.TestCase):
    def test_no_verdicts_means_unknown_not_zero(self):
        """The whole point of the instrument is to not overclaim."""
        from lib.db.fills import record_fills, wrongfill_stats
        record_fills(self.jid, self._fields(), url="https://x.com/j")
        s = wrongfill_stats()
        self.assertIsNone(s["overall"]["rate"])
        self.assertEqual(s["overall"]["n"], 0)
        self.assertEqual(s["overall"]["pending"], 3)

    def test_rate_counts_only_correct_and_wrong(self):
        """'unanswerable' is a question about the FORM, not about whether
        the pipeline chose correctly — it must not dilute the rate."""
        from lib.db.fills import record_fills, wrongfill_stats
        record_fills(self.jid, self._fields(), url="https://x.com/j")
        ids = [r["id"] for r in
               self.conn.execute("SELECT id FROM field_fills ORDER BY id")]
        from lib.db.fills import adjudicate
        adjudicate(ids[0], "correct")
        adjudicate(ids[1], "wrong")
        adjudicate(ids[2], "unanswerable")
        s = wrongfill_stats()
        self.assertEqual(s["overall"]["n"], 2)
        self.assertEqual(s["overall"]["wrong"], 1)
        self.assertAlmostEqual(s["overall"]["rate"], 0.5)

    def test_breaks_down_by_platform(self):
        from lib.db.fills import record_fills, adjudicate, wrongfill_stats
        record_fills(self.jid, self._fields()[:1],
                     url="https://boards.greenhouse.io/a/jobs/1")
        record_fills(self.jid, self._fields()[:1],
                     url="https://jobs.lever.co/b/1")
        ids = [r["id"] for r in
               self.conn.execute("SELECT id FROM field_fills ORDER BY id")]
        adjudicate(ids[0], "wrong")
        adjudicate(ids[1], "correct")
        by = {b["key"]: b for b in wrongfill_stats()["by_platform"]}
        self.assertAlmostEqual(by["greenhouse.io"]["rate"], 1.0)
        self.assertAlmostEqual(by["lever.co"]["rate"], 0.0)


class DossierShapeCompatibility(_DB, unittest.TestCase):
    def test_accepts_the_real_dossier_field_shape(self):
        """The ledger consumes the SAME structure the orchestrator reads,
        so the two can never disagree about what happened."""
        from lib.db.fills import record_fills
        real_shape = {
            "label": "Are you legally authorized to work?",
            "answer": "Yes", "outcome": "filled", "kind": "verified",
            "method": "deterministic", "reason": "verified",
            "required": True, "selector": "#q1", "selected_text": "Yes",
            "diag": {},
        }
        self.assertEqual(record_fills(self.jid, [real_shape],
                                      url="https://x.com/j"), 1)

    def test_missing_optional_keys_do_not_crash(self):
        from lib.db.fills import record_fills
        self.assertEqual(
            record_fills(self.jid, [{"label": "X", "kind": "needs_data"}],
                         url="https://x.com/j"), 1)


class B1SelfCorrection(_DB, unittest.TestCase):
    """A `wrong` adjudication must retract the learned mapping and drop the
    matching runtime rule — the falsification loop closes the source, not
    just the record."""

    def test_wrong_verdict_retracts_learned_mapping(self):
        from lib.db.fills import record_fills, adjudicate
        from apply.common.resolve import (learn_mapping, _lookup_learned,
                                          clear_learned_for_test)
        clear_learned_for_test()
        try:
            # Two consistent confirms promote the mapping to active.
            learn_mapping("Country", "Canada", domain="x.com")
            learn_mapping("Country", "Canada", domain="x.com")
            self.assertIsNotNone(_lookup_learned("country"))
            n = record_fills(self.jid, [{"label": "Country", "answer": "Canada",
                                         "kind": "verified", "method": "combobox",
                                         "required": True, "selected_text": ""}],
                             url="https://www.x.com/j")
            self.assertEqual(n, 1)
            fill_id = self.conn.execute(
                "SELECT id FROM field_fills ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            adjudicate(fill_id, "wrong")
            self.assertIsNone(_lookup_learned("country"),
                              "wrong verdict must retract the learned mapping")
        finally:
            clear_learned_for_test()

    def test_wrong_verdict_drops_matching_runtime_rule(self):
        from lib.db.fills import record_fills, adjudicate
        from apply.common.resolve import (add_alias_rule, list_alias_rules,
                                          clear_alias_rules)
        clear_alias_rules()
        try:
            self.assertTrue(add_alias_rule(r"\bcountry\b", ["location"]))
            self.assertEqual(len(list_alias_rules()), 1)
            n = record_fills(self.jid, [{"label": "Country", "answer": "Canada",
                                         "kind": "verified", "method": "combobox",
                                         "required": True, "selected_text": ""}],
                             url="https://www.x.com/j")
            fill_id = self.conn.execute(
                "SELECT id FROM field_fills ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            adjudicate(fill_id, "wrong")
            self.assertEqual(list_alias_rules(), [],
                             "wrong verdict must drop the suspect runtime rule")
        finally:
            clear_alias_rules()

    def test_correct_verdict_leaves_learning_alone(self):
        from lib.db.fills import record_fills, adjudicate
        from apply.common.resolve import (learn_mapping, _lookup_learned,
                                          clear_learned_for_test)
        clear_learned_for_test()
        try:
            learn_mapping("Country", "Canada", domain="x.com")
            learn_mapping("Country", "Canada", domain="x.com")
            self.assertIsNotNone(_lookup_learned("country"))
            n = record_fills(self.jid, [{"label": "Country", "answer": "Canada",
                                         "kind": "verified", "method": "combobox",
                                         "required": True, "selected_text": ""}],
                             url="https://www.x.com/j")
            fill_id = self.conn.execute(
                "SELECT id FROM field_fills ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            adjudicate(fill_id, "correct")
            self.assertIsNotNone(_lookup_learned("country"))
        finally:
            clear_learned_for_test()


class B2SptTripwire(_DB, unittest.TestCase):
    """Wrong-fill SPC: a platform over the bound on enough adjudicated fills
    trips and pauses autonomous submits."""

    def _seed_fills(self, platform, wrong, correct):
        from lib.db.fills import record_fills, adjudicate
        for i in range(wrong):
            record_fills(self.jid, [{"label": f"F{i}", "answer": "x",
                                     "kind": "verified", "method": "text",
                                     "required": True, "selected_text": "y"}],
                         url=f"https://{platform}/j")
            fid = self.conn.execute(
                "SELECT id FROM field_fills ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            adjudicate(fid, "wrong")
        for i in range(correct):
            record_fills(self.jid, [{"label": f"G{i}", "answer": "x",
                                     "kind": "verified", "method": "text",
                                     "required": True, "selected_text": "x"}],
                         url=f"https://{platform}/j")
            fid = self.conn.execute(
                "SELECT id FROM field_fills ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            adjudicate(fid, "correct")

    def test_high_wrong_rate_trips(self):
        from lib.db.fills import spc_trip
        self._seed_fills("boards.trippy.io", wrong=6, correct=2)  # 75% wrong
        tripped = spc_trip(apply=False)
        self.assertIn("trippy.io", tripped)

    def test_low_wrong_rate_does_not_trip(self):
        from lib.db.fills import spc_trip
        self._seed_fills("boards.clean.io", wrong=1, correct=10)
        tripped = spc_trip(apply=False)
        self.assertNotIn("clean.io", tripped)

    def test_small_sample_never_trips(self):
        from lib.db.fills import spc_trip
        self._seed_fills("boards.small.io", wrong=2, correct=0)  # 100% but n<min
        tripped = spc_trip(apply=False)
        self.assertNotIn("small.io", tripped)


if __name__ == "__main__":
    unittest.main()
