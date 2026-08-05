"""test_dom_diff.py — the dynamic-field DOM-diff observation (observation-only).

The value-string read-back tells what a field's value IS; the DOM-diff tells
what the PAGE DID in response. These tests pin:
  - the pure summarizer (no browser needed — feeds fake MutationRecords),
  - which fields qualify as dynamic (combobox/select/shadow),
  - that dom_delta lands in the field record only for dynamic fields,
  - that the dossier/audit carry dom_delta (evidence, never certification).

Guardrail: dom_delta must NEVER feed _check_delta (certification stays
value-string). These tests assert it is attached, not consulted.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _node(tag=None, role=None, placeholder=None, text=None):
    """A fake DOM node the summarizer can describe: element (tag/role/ph) or
    text node (text)."""
    if tag is not None:
        return {"tag": tag, "role": role, "placeholder": placeholder}
    return text


def _rec(rtype, added=None, removed=None, attr=None, target_text=None):
    d = {"type": rtype}
    if added is not None:
        d["addedNodes"] = added
    if removed is not None:
        d["removedNodes"] = removed
    if attr is not None:
        d["attributeName"] = attr
    if target_text is not None:
        d["target"] = {"textContent": target_text}
    return d


class Summarizer(unittest.TestCase):
    def test_added_and_removed_nodes(self):
        from apply.common.dom_diff import summarize
        records = [
            _rec("childList", added=[_node(tag="DIV", role="listbox")]),
            _rec("childList", removed=[_node(tag="SPAN")]),
        ]
        s = summarize(records)
        self.assertEqual(s["added"], ["div role=listbox"])
        self.assertEqual(s["removed"], ["span"])

    def test_attribute_changes(self):
        from apply.common.dom_diff import summarize
        records = [
            _rec("attributes", attr="aria-expanded"),
            _rec("attributes", attr="class"),   # dropped (churn)
            _rec("attributes", attr="style"),   # dropped (churn)
        ]
        s = summarize(records)
        self.assertEqual(s["attrs"], ["aria-expanded"])

    def test_text_changes(self):
        from apply.common.dom_diff import summarize
        records = [_rec("characterData", target_text="Canada (+1)")]
        s = summarize(records)
        self.assertEqual(s["texts"], ["Canada (+1)"])

    def test_dedup_and_caps(self):
        from apply.common.dom_diff import summarize
        records = [
            _rec("childList", added=[_node(tag="LI"), _node(tag="LI")]),
            _rec("childList", added=[_node(tag="LI")]),
            _rec("childList", added=[_node(tag="OPTION"), _node(tag="OPTION"),
                                     _node(tag="OPTION"), _node(tag="OPTION"),
                                     _node(tag="OPTION"), _node(tag="OPTION"),
                                     _node(tag="OPTION"), _node(tag="OPTION"),
                                     _node(tag="OPTION"), _node(tag="OPTION"),
                                     _node(tag="OPTION"), _node(tag="OPTION"),
                                     _node(tag="OPTION"), _node(tag="OPTION")]),
        ]
        s = summarize(records)
        self.assertEqual(s["added"].count("li"), 1, "dedup by description")
        self.assertEqual(len(s["added"]), 2, "li + option")
        self.assertEqual(len(s["added"]), 2)  # capped at 12 anyway

    def test_empty_records(self):
        from apply.common.dom_diff import summarize
        s = summarize([])
        self.assertEqual(s, {"added": [], "removed": [], "attrs": [], "texts": []})


class DynamicDetection(unittest.TestCase):
    def test_combobox_is_dynamic(self):
        from apply.common.dom_diff import _is_dynamic
        self.assertTrue(_is_dynamic({"role": "combobox"}))
        self.assertTrue(_is_dynamic({"tag": "DROPDOWN"}))

    def test_select_is_dynamic(self):
        from apply.common.dom_diff import _is_dynamic
        self.assertTrue(_is_dynamic({"tag": "SELECT"}))

    def test_plain_text_is_not_dynamic(self):
        from apply.common.dom_diff import _is_dynamic
        self.assertFalse(_is_dynamic({"tag": "INPUT", "type": "text"}))
        self.assertFalse(_is_dynamic({"tag": "TEXTAREA"}))

    def test_aria_haspopup_is_dynamic(self):
        from apply.common.dom_diff import _is_dynamic
        self.assertTrue(_is_dynamic({"aria": {"haspopup": "listbox"}}))


class FieldRecord(unittest.TestCase):
    """The observer JS is live-only (jsdom can't run MutationObserver); the
    wiring — register before fill, drain after, attach dom_delta — is unit
    tested by stubbing start_observation/drain_summary."""

    def test_dynamic_field_gets_dom_delta(self):
        from apply.common import fill_runner as FR
        page = MagicMock()
        f = {"label": "Country", "tag": "SELECT", "_sel": "#c"}
        delta = {"added": ["div role=listbox"], "removed": [],
                 "attrs": ["aria-expanded"], "texts": []}
        with patch.object(FR, "_is_combobox", return_value=False), \
             patch("apply.common.filler.fill_field", return_value=(True, "select_option")), \
             patch("apply.common.dom_diff.start_observation",
                   return_value=True) as start, \
             patch("apply.common.dom_diff.drain_summary",
                   return_value=delta) as drain, \
             patch("apply.common.fill_runner.resolve_selector", return_value="#c"):
            ok = FR.field_deterministic(page, f, "Canada")
        self.assertTrue(ok)
        start.assert_called_once()
        drain.assert_called_once()
        self.assertEqual(f["dom_delta"], delta)

    def test_non_dynamic_field_skips_observer(self):
        from apply.common import fill_runner as FR
        page = MagicMock()
        f = {"label": "Name", "tag": "INPUT", "type": "text", "_sel": "#n"}
        with patch.object(FR, "_is_combobox", return_value=False), \
             patch("apply.common.filler.fill_field", return_value=(True, "fill")), \
             patch("apply.common.dom_diff.start_observation") as start, \
             patch("apply.common.fill_runner.resolve_selector", return_value="#n"):
            ok = FR.field_deterministic(page, f, "Ann")
        self.assertTrue(ok)
        start.assert_not_called()
        self.assertNotIn("dom_delta", f)

    def test_observer_failure_does_not_break_fill(self):
        """If the observer JS can't run (navigation, jsdom, weird page), the
        fill must still succeed — observation is best-effort."""
        from apply.common import fill_runner as FR
        page = MagicMock()
        f = {"label": "Country", "tag": "SELECT", "_sel": "#c"}
        with patch.object(FR, "_is_combobox", return_value=False), \
             patch("apply.common.filler.fill_field", return_value=(True, "select_option")), \
             patch("apply.common.dom_diff.start_observation",
                   side_effect=RuntimeError("detached")), \
             patch("apply.common.fill_runner.resolve_selector", return_value="#c"):
            ok = FR.field_deterministic(page, f, "Canada")
        self.assertTrue(ok, "fill must survive observer failure")
        self.assertNotIn("dom_delta", f)


class DossierAndAudit(unittest.TestCase):
    def test_dossier_carries_dom_delta(self):
        from apply.act import fill as F
        filled = [{"label": "Country", "answer": "Canada", "kind": "verified",
                   "method": "combobox", "required": True, "provenance": "profile",
                   "dom_delta": {"added": ["div"], "removed": [], "attrs": [],
                                 "texts": ["Canada (+1)"]}}]
        with patch("lib.config.RESULTS_DIR",
                   __import__("tempfile").mkdtemp()):
            pass  # _write_handoff writes to disk; call it directly below
        import tempfile, json
        tmp = tempfile.mkdtemp()
        with patch("apply.act.fill.RESULTS_DIR", tmp), \
             patch("lib.config.RESULTS_DIR", tmp):
            F._write_handoff("aaaaaaaaaaaaaaaa", "https://x.com/j", filled, [],
                             {}, mode="shadow")
            path = os.path.join(tmp, "aaaaaaaaaaaaaaaa", "handoff.json")
            d = json.load(open(path, encoding="utf-8"))
        self.assertEqual(d["fields"][0]["dom_delta"]["texts"], ["Canada (+1)"])

    def test_audit_log_carries_dom_delta(self):
        from apply.common.audit import log_field
        import tempfile, json
        tmp = tempfile.mkdtemp()
        with patch("apply.common.audit.RESULTS_DIR", tmp):
            log_field("aaaaaaaaaaaaaaaa", "Country", "Canada", "profile",
                      dom_delta={"added": ["div"]})
            path = os.path.join(tmp, "aaaaaaaaaaaaaaaa", "apply_audit.jsonl")
            rec = json.loads(open(path, encoding="utf-8").readline())
        self.assertEqual(rec["dom_delta"], {"added": ["div"]})


if __name__ == "__main__":
    unittest.main()
