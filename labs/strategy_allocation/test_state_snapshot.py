"""The exact snapshot: immutable, self-verifying, schema-valid and pinned by the committed fixtures."""

import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from .benchmark import check_fixtures
from .environment import Environment
from .state_snapshot import StateSnapshot

try:
    from jsonschema import Draft202012Validator
except ImportError:  # the schema test needs jsonschema; everything else is stdlib
    Draft202012Validator = None

SCHEMA = Path(__file__).resolve().parent / "state_schema.json"
CONTEXT_PACK_KEYS = {"contextPack", "source", "target", "keep", "request", "contextTier", "escalation", "context",
                     "preflight", "confirmedStage", "scopedDecisions", "studyEvidence", "honesty"}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()

    def test_the_same_case_always_gives_the_same_snapshot(self):
        for case_id in self.env.case_ids:
            self.assertEqual(self.env.reset(case_id), Environment().reset(case_id))

    def test_a_snapshot_cannot_be_changed_in_place(self):
        snapshot = self.env.reset("open-direction")
        with self.assertRaises(FrozenInstanceError):
            snapshot.task = "another task"
        copy = snapshot.context_pack
        copy["context"]["facts"].clear()
        self.assertTrue(snapshot.context_pack["context"]["facts"])

    def test_the_digest_covers_everything_else(self):
        value = self.env.reset("local-conflict").to_dict()
        self.assertEqual(StateSnapshot.from_dict(value).to_dict(), value)
        for key, replacement in (("task", "Resolve every clash."), ("state", {**value["state"], "clash": "resolved"}),
                                 ("sourceRefs", [])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                StateSnapshot.from_dict({**value, key: replacement})

    def test_the_digest_is_plain_canonical_json_for_other_languages(self):
        for case_id in self.env.case_ids:
            value = self.env.reset(case_id).to_dict()
            body = {key: item for key, item in value.items() if key != "digest"}
            text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), value["digest"])

    def test_json_text_round_trips(self):
        snapshot = self.env.reset("protected-dependency")
        self.assertEqual(StateSnapshot.from_json(snapshot.to_json()), snapshot)

    def test_committed_fixtures_are_exactly_todays_snapshots(self):
        self.assertEqual(check_fixtures(self.env), [])

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
    def test_every_snapshot_matches_state_schema_json(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for case_id in self.env.case_ids:
            state = self.env.initial_state(case_id)
            for action in self.env.case(case_id).action_ids:  # later states too, not only the fixtures
                snapshot = self.env.snapshot(self.env.step(state, action).state_after)
                validator.validate(snapshot.to_dict())

    def test_the_context_pack_has_the_contextpack_at_1_shape(self):
        for case_id in self.env.case_ids:
            pack = self.env.reset(case_id).context_pack
            self.assertEqual(set(pack), CONTEXT_PACK_KEYS)
            self.assertEqual(pack["contextPack"], "ContextPack@1")
            self.assertTrue(pack["source"]["exactSource"])

    def test_unread_sources_appear_as_identities_not_content(self):
        provided = self.env.reset("provided-source").to_json()
        self.assertNotIn("1.8 m", provided)
        self.assertIn("#sha256=", provided)
        self.assertNotIn("600 mm", self.env.reset("protected-dependency").to_json())


if __name__ == "__main__":
    unittest.main()
