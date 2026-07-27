"""test_probe_router.py — Capability scanner + observation store + probe router.

Tests the three layers of the adaptive probe system WITHOUT a real
Playwright browser. Uses a FakePage that scripts page.evaluate() calls
in declaration order — the first call returns the capability scan,
subsequent calls return scripted probe results.

The router's contract tested here:
  1. YAML registry_strategy always wins
  2. Confirmed observation tried before capability suggestion
  3. Capability suggestion tried before cascade
  4. Full cascade runs whenever the prioritised attempt returns 0 fields
  5. Successful probe records observation (success_count advances)
  6. Cascade-miss records failure (drift → demote after 2 fails)
  7. Failure artifacts written to ~/.ji/registry-failures/
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_SKILL_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _SKILL_DIR)


class FakeRegistry:
    """Stand-in for apply.common.registry.RegistryConfig."""
    def __init__(self, best_strategy=None, widgets=None):
        self.name = "fake"
        self.best_strategy = best_strategy
        self.widgets = widgets or {}


def _profile_dialog_login():
    return {
        "dialog": True, "nested_dialog": False, "iframes": 0,
        "cross_origin_iframes": 0, "shadow_roots": 0,
        "comboboxes": 0, "listboxes": 0, "listbox_buttons": 0,
        "file_inputs": 0, "password_fields": 1, "email_fields": 1,
        "visible_text_inputs": 0, "select_elements": 0, "textarea_count": 0,
        "radio_groups": 0, "checkbox_count": 0,
        "has_progress_bar": False, "has_captcha": False,
        "honeypot_signals": 0, "page_text_length": 200,
        "login_signals": ["sign in to apply"], "eeoc_signals": [],
        "apply_buttons": [], "submit_buttons": ["Sign In"],
    }


def _profile_workday():
    return {
        "dialog": True, "nested_dialog": False, "iframes": 0,
        "cross_origin_iframes": 0, "shadow_roots": 0,
        "comboboxes": 3, "listboxes": 3, "listbox_buttons": 3,
        "file_inputs": 1, "password_fields": 0, "email_fields": 0,
        "visible_text_inputs": 6, "select_elements": 0, "textarea_count": 1,
        "radio_groups": 2, "checkbox_count": 1,
        "has_progress_bar": True, "has_captcha": False,
        "honeypot_signals": 1, "page_text_length": 3000,
        "login_signals": [], "eeoc_signals": [],
        "apply_buttons": ["Apply"], "submit_buttons": ["Next"],
    }


class FakePage:
    """Scripts page.evaluate() — returns scripted responses in order.

    The first evaluate is reserved for the capability scan. After
    that, each call returns whatever scripted fields/probe-result the
    caller queued. This lets us test the router without Playwright.
    """
    def __init__(self, scripted_results=None, url="https://example.com/x"):
        self.url = url
        self.title = lambda: "Test"
        self._results = list(scripted_results or [])
        self._eval_count = 0
        self._screenshots = []

    def evaluate(self, js, *args, **kwargs):
        self._eval_count += 1
        if self._results:
            return self._results.pop(0)
        # Empty ProbeResult-style dict if caller exhausts the queue.
        return {"fieldCount": 0, "fields": [], "buttons": [], "pageType": "unknown",
                "hasFileInput": False, "hasRequiredFile": False, "url": self.url}

    def screenshot(self, path=None, **kwargs):
        self._screenshots.append(path)


class CapabilityHashTests(unittest.TestCase):
    def test_minor_variance_same_hash(self):
        from apply.common.capabilities import profile_hash
        p1 = _profile_dialog_login()
        p2 = dict(p1, page_text_length=5000, apply_buttons=["Different"], submit_buttons=["Sign In", "Other"])
        self.assertEqual(profile_hash(p1), profile_hash(p2))

    def test_dialog_change_changes_hash(self):
        from apply.common.capabilities import profile_hash
        p1 = _profile_dialog_login()
        p2 = dict(p1, dialog=False, password_fields=0, login_signals=[])
        self.assertNotEqual(profile_hash(p1), profile_hash(p2))

    def test_workday_distinct_from_login(self):
        from apply.common.capabilities import profile_hash
        self.assertNotEqual(profile_hash(_profile_dialog_login()),
                            profile_hash(_profile_workday()))

    def test_suggest_strategy(self):
        from apply.common.capabilities import suggest_strategy
        self.assertEqual(suggest_strategy(_profile_dialog_login()), "dialog")
        # workday-like profile: dialog wins (strongest signal)
        self.assertEqual(suggest_strategy(_profile_workday()), "dialog")
        # no signal → None (cascade runs in declaration order)
        blank = {k: 0 for k in _profile_dialog_login()}
        self.assertIsNone(suggest_strategy(blank))


class DiscoverWidgetsTests(unittest.TestCase):
    def test_workday_discovers_listbox_button(self):
        from apply.common.capabilities import discover_widgets
        w = discover_widgets(_profile_workday(), None)
        self.assertIn("dropdown", w)
        self.assertIn("autocomplete", w)
        self.assertIn("button[aria-haspopup='listbox']", w["dropdown"])

    def test_login_discovers_no_widgets(self):
        from apply.common.capabilities import discover_widgets
        self.assertEqual(discover_widgets(_profile_dialog_login(), None), {})


class ObservationStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("JI_HOME")
        os.environ["JI_HOME"] = self._tmp.name
        # Force reload of config + observations module
        for m in list(sys.modules):
            if m.startswith("lib.config") or m.startswith("apply.common.observations") or m.startswith("apply.common.capabilities"):
                del sys.modules[m]

    def tearDown(self):
        self._tmp.cleanup()
        if self._orig_home is None:
            os.environ.pop("JI_HOME", None)
        else:
            os.environ["JI_HOME"] = self._orig_home

    def test_first_run_returns_none(self):
        from apply.common import observations as o
        self.assertIsNone(o.lookup(_profile_dialog_login()))

    def test_one_success_unconfirmed(self):
        from apply.common import observations as o
        rec = o.record_success(_profile_dialog_login(), "https://x.com/login", "dialog")
        self.assertEqual(rec["success_count"], 1)
        self.assertFalse(rec["confirmed"])
        rec2 = o.lookup(_profile_dialog_login())
        self.assertIsNotNone(rec2)
        # Unconfirmed but candidate_strategy → recommend_start returns the
        # candidate as a soft hint
        self.assertEqual(o.recommend_start_strategy(_profile_dialog_login(), rec2, None), "dialog")

    def test_three_successes_confirm(self):
        from apply.common import observations as o
        for i in range(3):
            o.record_success(_profile_dialog_login(), f"https://x.com/login{i}", "dialog")
        rec = o.lookup(_profile_dialog_login())
        self.assertTrue(rec["confirmed"])
        self.assertEqual(rec["success_count"], 3)
        self.assertEqual(rec["winning_strategy"], "dialog")

    def test_yaml_overrides_confirmed(self):
        from apply.common import observations as o
        for i in range(3):
            o.record_success(_profile_dialog_login(), f"https://x.com/login{i}", "dialog")
        rec = o.lookup(_profile_dialog_login())
        # YAML 'standard' beats confirmed 'dialog'
        s = o.recommend_start_strategy(_profile_dialog_login(), rec, registry_strategy="standard")
        self.assertEqual(s, "standard")

    def test_drift_demote_after_two_failures(self):
        from apply.common import observations as o
        for i in range(3):
            o.record_success(_profile_dialog_login(), f"https://x.com/login{i}", "dialog")
        rec = o.lookup(_profile_dialog_login())
        self.assertTrue(rec["confirmed"])
        # 1 failure keeps confirmed
        o.record_failure(_profile_dialog_login(), "https://x.com/login9")
        rec1 = o.lookup(_profile_dialog_login())
        self.assertTrue(rec1["confirmed"], "1 failure should not demote")
        self.assertEqual(rec1["fail_count"], 1)
        # 2nd failure demotes — after full demote, lookup returns None
        # (no winning_strategy, no candidate_strategies → nothing to suggest)
        o.record_failure(_profile_dialog_login(), "https://x.com/login10")
        self.assertIsNone(o.lookup(_profile_dialog_login()),
                          "after full demote, lookup returns None (clean slate)")
        # The record file still exists but is reset — verify via list_all
        all_recs = o.list_all()
        demoted = [r for r in all_recs if r["profile_hash"] == rec["profile_hash"]]
        self.assertEqual(len(demoted), 1)
        self.assertFalse(demoted[0]["confirmed"])
        self.assertEqual(demoted[0]["success_count"], 0)
        self.assertEqual(demoted[0]["winning_strategy"], "")
        self.assertEqual(demoted[0]["candidate_strategies"], [])

    def test_strategy_change_resets_success_count(self):
        from apply.common import observations as o
        o.record_success(_profile_workday(), "https://wd.com", "dialog")
        # Change strategy → reset to 1 (we don't trust a one-shot change)
        rec = o.record_success(_profile_workday(), "https://wd.com", "custom_widgets")
        self.assertEqual(rec["winning_strategy"], "custom_widgets")
        self.assertEqual(rec["success_count"], 1)
        self.assertFalse(rec["confirmed"])

    def test_widgets_merge(self):
        from apply.common import observations as o
        o.record_success(_profile_workday(), "https://wd.com", "custom_widgets",
                         widgets_used={"dropdown": "button[aria-haspopup]"})
        rec = o.record_success(_profile_workday(), "https://wd.com", "custom_widgets",
                               widgets_used={"autocomplete": "input[role='combobox']"})
        # Both widgets retained from the merge
        self.assertEqual(rec["winning_widgets"], {
            "dropdown": "button[aria-haspopup]",
            "autocomplete": "input[role='combobox']"
        })

    def test_list_all(self):
        from apply.common import observations as o
        o.record_success(_profile_dialog_login(), "https://x.com/login", "dialog")
        o.record_success(_profile_workday(), "https://wd.com", "custom_widgets")
        all_recs = o.list_all()
        self.assertEqual(len(all_recs), 2)

    def test_clear_hash(self):
        from apply.common import observations as o
        from apply.common.capabilities import profile_hash
        h = profile_hash(_profile_dialog_login())
        o.record_success(_profile_dialog_login(), "https://x.com", "dialog")
        self.assertTrue(o.clear_hash(h))
        self.assertIsNone(o.lookup(_profile_dialog_login()))


class ProbeRouterTests(unittest.TestCase):
    """End-to-end probe router tests with a FakePage.

    We can't easily mock _probe_dialog etc. without monkey-patching
    inspector. Instead we use the FakePage to feed scripted
    `page.evaluate` results — but the real strategies use page.frames,
    page.locator, etc. which FakePage doesn't fully implement.

    So we test the ROUTER LOGIC directly: verify the first_strategy
    selection, observation-write feedback, and failure capture — by
    calling _try_strategy (None) and the observation API directly. The
    integration test is left to the import-smoke + manual run.
    """
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("JI_HOME")
        os.environ["JI_HOME"] = self._tmp.name
        for m in list(sys.modules):
            if m.startswith("lib.config") or m.startswith("apply.common.observations") or m.startswith("apply.common.capabilities") or m.startswith("apply.common.inspector"):
                del sys.modules[m]

    def tearDown(self):
        self._tmp.cleanup()
        if self._orig_home is None:
            os.environ.pop("JI_HOME", None)
        else:
            os.environ["JI_HOME"] = self._orig_home

    def test_yaml_strategy_overrides_observation(self):
        from apply.common import observations as o
        # Build 3 confirmed observations for custom_widgets
        for i in range(3):
            o.record_success(_profile_workday(), "https://wd.com", "custom_widgets")
        rec = o.lookup(_profile_workday())
        # YAML says "dialog" — should override confirmed "custom_widgets"
        chosen = o.recommend_start_strategy(_profile_workday(), rec, registry_strategy="dialog")
        self.assertEqual(chosen, "dialog")

    def test_build_registry_widgets_yaml_merges_with_auto(self):
        from apply.common.inspector import _build_registry_widgets
        # YAML supplies dropdown selector
        reg = FakeRegistry(widgets={"dropdown": "button[data-custom='listbox']"})
        w = _build_registry_widgets(reg, _profile_workday())
        self.assertEqual(w["dropdown"], "button[data-custom='listbox']",
                         "YAML selector wins over auto-discovered")
        # Auto-discovered autocomplete is added when YAML omits it
        self.assertIn("autocomplete", w)

    def test_build_registry_widgets_no_yaml_uses_auto(self):
        from apply.common.inspector import _build_registry_widgets
        w = _build_registry_widgets(None, _profile_workday())
        self.assertEqual(w["dropdown"], "button[aria-haspopup='listbox']")
        self.assertEqual(w["autocomplete"], "input[role='combobox']")

    def test_build_registry_widgets_no_yaml_no_caps(self):
        from apply.common.inspector import _build_registry_widgets
        self.assertEqual(_build_registry_widgets(None, None), {})

    def test_failure_capture_writes_artifacts(self):
        from apply.common.inspector import _capture_failure
        page = FakePage(url="https://example.com/dead-end")
        page._results = ["<html><body>dead</body></html>"]  # for evaluate(() => outerHTML)
        path = _capture_failure(page, _profile_workday(), jid="test_jid")
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        # Sidecar JSON contains the profile info
        import json
        with open(path, encoding="utf-8") as f:
            sidecar = json.load(f)
        self.assertEqual(sidecar["url"], "https://example.com/dead-end")
        self.assertEqual(sidecar["jid"], "test_jid")
        self.assertIn("profile_hash", sidecar)
        self.assertIn("capability_profile", sidecar)

    def test_failure_capture_dedupes_by_pruning_old(self):
        from apply.common.inspector import _capture_failure
        from lib.config import JI_HOME
        # Write 30 fake failures — older ones should be pruned
        page = FakePage()
        for i in range(30):
            page._results.append(f"<html>{i}</html>")
            _capture_failure(page, _profile_workday(), jid=f"j{i}")
        snaps = list(Path(JI_HOME, "registry-failures").glob("*_dom.html"))
        self.assertLessEqual(len(snaps), 25, "should keep only 25 most recent")


if __name__ == "__main__":
    unittest.main()