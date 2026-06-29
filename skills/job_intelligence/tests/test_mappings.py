"""Unit tests for the field→meaning mapping store (apply/common/mappings.py).

Covers the lifecycle (learn → promote/confirm → resolve), the safety gates
(off-by-default, never-learn categories, corrected-only auto-promote), and the
core principle: the *mapping* is cached, the value is recomputed from the profile.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apply.common import mappings

PROFILE = {"_version": 1, "first_name": "Bilal", "phone": "613-555-0100"}
LEGAL_FIELD = {"label": "Are you eligible to work in Canada?", "options": ["Yes", "No"], "tag": "radio"}


class _Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in ("JI_HOME", "JI_APPLY_MODE")}
        os.environ["JI_HOME"] = self.home
        os.environ.pop("JI_APPLY_MODE", None)
        self._set_enabled(True)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _set_enabled(self, on):
        with open(os.path.join(self.home, "apply_policy.json"), "w") as f:
            json.dump({"use_mappings": on}, f)


class Gating(_Base):
    def test_disabled_is_inert(self):
        self._set_enabled(False)
        mappings.learn("j1", LEGAL_FIELD, "Yes", "user_typed", PROFILE, corrected=True)
        self.assertEqual(mappings.list_pending("j1"), {})
        self.assertIsNone(mappings.resolve_field(LEGAL_FIELD, PROFILE))

    def test_only_learns_user_typed(self):
        mappings.learn("j1", LEGAL_FIELD, "Yes", "ephemeral", PROFILE, corrected=True)
        self.assertEqual(mappings.list_pending("j1"), {})

    def test_never_learns_eeo_or_freetext(self):
        eeo = {"label": "Gender", "options": ["Male", "Female", "Prefer not to say"], "tag": "SELECT"}
        free = {"label": "Why us?", "tag": "TEXTAREA"}
        mappings.learn("j1", eeo, "Male", "user_typed", PROFILE, corrected=True)
        mappings.learn("j1", free, "Because reasons", "user_typed", PROFILE, corrected=True)
        self.assertEqual(mappings.list_pending("j1"), {})


class Lifecycle(_Base):
    def test_one_shot_not_auto_promoted(self):
        mappings.learn("j1", LEGAL_FIELD, "Yes", "user_typed", PROFILE, corrected=False)
        self.assertEqual(len(mappings.list_pending("j1")), 1)
        self.assertEqual(mappings.promote("j1"), 0)            # not corrected → not promoted
        self.assertIsNone(mappings.resolve_field(LEGAL_FIELD, PROFILE))

    def test_corrected_auto_promotes_then_resolves(self):
        mappings.learn("j1", LEGAL_FIELD, "Yes", "user_typed", PROFILE, corrected=True)
        self.assertEqual(mappings.promote("j1"), 1)
        self.assertEqual(mappings.list_pending("j1"), {})      # moved out of pending
        res = mappings.resolve_field(LEGAL_FIELD, PROFILE)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "Yes")
        self.assertEqual(res[1], "mapping")

    def test_confirm_promotes_uncorrected(self):
        mappings.learn("j1", LEGAL_FIELD, "Yes", "user_typed", PROFILE, corrected=False)
        self.assertEqual(mappings.confirm("j1"), 1)
        self.assertEqual(mappings.resolve_field(LEGAL_FIELD, PROFILE)[0], "Yes")


class CoreBehavior(_Base):
    def test_caches_mapping_not_value(self):
        # An oddly-labeled field answered with the profile phone learns a
        # profile-KEY mapping; changing the profile changes the resolved value.
        field = {"label": "Best contact digits", "tag": "INPUT", "type": "tel"}
        mappings.learn("j1", field, "613-555-0100", "user_typed", PROFILE, corrected=True)
        entry = list(mappings.list_pending("j1").values())[0]
        self.assertEqual(entry["target_kind"], "profile_key")
        self.assertEqual(entry["target"], "phone")
        mappings.promote("j1")

        self.assertEqual(mappings.resolve_field(field, PROFILE)[0], "613-555-0100")
        moved = dict(PROFILE, phone="999-888-7777")
        self.assertEqual(mappings.resolve_field(field, moved)[0], "999-888-7777")

    def test_profile_version_invalidates(self):
        mappings.learn("j1", LEGAL_FIELD, "Yes", "user_typed", PROFILE, corrected=True)
        mappings.promote("j1")
        self.assertIsNotNone(mappings.resolve_field(LEGAL_FIELD, PROFILE))
        bumped = dict(PROFILE, _version=2)
        self.assertIsNone(mappings.resolve_field(LEGAL_FIELD, bumped))


if __name__ == "__main__":
    unittest.main()
