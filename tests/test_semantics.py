"""The semantic registry: text resolves to registered ids or is refused with the nearest ones."""

from __future__ import annotations

import unittest

from archflow.semantics.conditions import CONDITION_IDS
from archflow.semantics.registry import COMPOUND_PHRASES, resolve_semantic_kind, suggest_semantic
from archflow.semantics.roles import ROLE_IDS
from archflow.state.state_record import Entity, Relation, StateRecord, StateRecordError


def _entity(entity_id: str, schema: str, **fields: object) -> Entity:
    return Entity(entity_id=entity_id, schema=schema, parent_id=None, fields=fields)


class RegistryTests(unittest.TestCase):
    def test_ids_aliases_compositions_and_compound_phrases_resolve(self) -> None:
        self.assertEqual(resolve_semantic_kind("role.access").roles, ("role.access",))
        self.assertEqual(resolve_semantic_kind("入口").roles, ("role.access",))
        composed = resolve_semantic_kind("role.weather_enclosure+condition.void")
        self.assertEqual((composed.roles, composed.conditions), (("role.weather_enclosure",), ("condition.void",)))
        legacy = resolve_semantic_kind("weather-enclosure-and-opening-host")
        self.assertEqual(legacy.roles, ("role.weather_enclosure", "role.opening_host"))
        for phrase, (roles, conditions) in COMPOUND_PHRASES.items():
            self.assertTrue(set(roles) <= ROLE_IDS and set(conditions) <= CONDITION_IDS, phrase)

    def test_unknown_text_resolves_to_nothing_and_suggests(self) -> None:
        self.assertIsNone(resolve_semantic_kind("vertical_transition_space_v2"))
        self.assertTrue(suggest_semantic("clearence"))
        self.assertIn("condition.clearance", suggest_semantic("clearence"))


class RecordRulesTests(unittest.TestCase):
    def _record(self, kind: str | None, *, relations: tuple[Relation, ...] = (), refs: tuple[str, ...] = (), **extra: object) -> StateRecord:
        fields: dict[str, object] = {"intent": "declared", "maturity": "schematic", "revision": 0, **extra}
        if kind is not None:
            fields["semantic_kind"] = kind
        entities = (
            _entity("zone-a", "Space@1", program_node_refs=[], level_ids=[], volume_ids=[]),
            _entity("zone-b", "Space@1", program_node_refs=[], level_ids=[], volume_ids=[]),
            _entity("link", "Connection@1", source_zone_id="zone-a", target_zone_id="zone-b", relationship_refs=list(refs), directed=True),
            _entity("portico", "Component@1", **fields),
        )
        return StateRecord(project_id="demo", run_id="run-1", entities=entities, relations=relations)

    def test_component_semantics_must_be_registered(self) -> None:
        self._record("controlled-entry")
        self._record(None, roles=["role.access", "role.cover"], conditions=["condition.threshold"])
        with self.assertRaises(StateRecordError) as raised:
            self._record("semi_outdoor_transition_zone_v2")
        self.assertIn("nearest", str(raised.exception))
        with self.assertRaises(StateRecordError):
            self._record(None, roles=["role.made_up"])
        with self.assertRaises(StateRecordError):
            self._record("role.access+condition.threshold")  # ids belong in roles/conditions, not the label

    def test_connection_refs_must_name_declared_relations(self) -> None:
        with self.assertRaises(StateRecordError):
            self._record("access", refs=("relation:a-b-clearance",))
        declared = Relation(relation_id="a-b-clearance", kind="clearance", subject="zone-a", object="zone-b")
        self._record("access", relations=(declared,), refs=("relation:a-b-clearance",))


if __name__ == "__main__":
    unittest.main()
