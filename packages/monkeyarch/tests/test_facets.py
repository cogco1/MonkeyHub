"""L3 facets: meaning added to a component's stable identity without touching its geometry.

``Component@1.fields.facets`` is a map of namespaced keys to string values,
validated against ``packages/archflow/src/archflow/semantics/facets.py`` (spec
docs/design/construction-api.md §3.4): an enumerated key accepts one of
its closed set of values, a free-text key accepts 1-120 characters. An
unknown key or an unregistered value is a ``StateRecordError`` naming the
nearest key or the allowed values. ``component_facets`` reads the map back
off a Component@1, empty when absent. Facets are the component's only L3
meaning: editing them leaves every other entity, and the record's dependency
edges and element rows, byte-identical (D-419-0).
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.project.refs import ProjectVersionRef
from archflow.semantics.facets import (
    FACET_KEYS,
    FACET_REASONS,
    FACETS,
    FREE_TEXT_MAX,
    FREE_TEXT_MIN,
    allowed_facet_values,
    facet_format,
    suggest_facet_key,
)
from archflow.state.state_record import (
    Entity,
    StateRecord,
    StateRecordError,
    apply_state_record_operator,
    compile_component_edit,
    component_facets,
)
from monkeyarch.authoring.element_producers import element_rows_of
from state_record_fixture import _record

_OMIT = object()


def _facet_record(facets: object = _OMIT) -> StateRecord:
    fields: dict[str, object] = {"intent": "declared"}
    if facets is not _OMIT:
        fields["facets"] = facets
    return StateRecord(project_id="demo", run_id="run-1", entities=(Entity("c", "Component@1", fields),))


class FacetRegistryTests(unittest.TestCase):
    """packages/archflow/src/archflow/semantics/facets.py, in the style of packages/archflow/tests/test_semantics.py."""

    def test_registry_has_exactly_the_spec_table_keys(self) -> None:
        """The spec table's keys, and material.color, added with its written reason (#560)."""

        self.assertEqual(FACET_KEYS, frozenset({
            "architectural.role", "architectural.enclosure", "structural.role",
            "material.name", "material.color", "fabrication.method",
        }))
        self.assertEqual(set(FACET_REASONS), {"material.color"})

    def test_registry_has_exactly_the_spec_table_values(self) -> None:
        self.assertEqual(set(FACETS["architectural.role"]), {
            "wall", "slab", "floor", "roof", "column", "beam", "stair", "ramp", "door", "window",
            "opening", "railing", "ceiling", "partition", "canopy", "screen", "foundation", "space",
            "furniture", "site",
        })
        self.assertEqual(set(FACETS["architectural.enclosure"]), {"exterior", "interior"})
        self.assertEqual(set(FACETS["structural.role"]), {"load_bearing", "non_load_bearing", "bracing"})
        self.assertIsNone(FACETS["material.name"])
        self.assertIsNone(FACETS["fabrication.method"])
        self.assertIsNone(allowed_facet_values("material.name"))
        self.assertEqual(allowed_facet_values("structural.role"), FACETS["structural.role"])

    def test_suggest_facet_key_finds_the_near_miss(self) -> None:
        self.assertIn("architectural.role", suggest_facet_key("architectural.rol"))
        self.assertIn("material.name", suggest_facet_key("material.nam"))
        self.assertEqual(suggest_facet_key("totally_unrelated_xyz_key"), ())


class ComponentFacetsValidationTests(unittest.TestCase):
    """Every key/value the registry names is accepted; everything else is refused."""

    def test_every_registered_enumerated_value_is_accepted(self) -> None:
        for key, allowed in FACETS.items():
            if allowed is None:
                continue
            for value in allowed:
                _facet_record({key: value})  # must not raise

    def test_free_text_keys_accept_ordinary_text(self) -> None:
        for key, allowed in FACETS.items():
            if allowed is not None or facet_format(key) is not None:
                continue
            _facet_record({key: "reclaimed oak"})  # must not raise

    def test_a_formatted_key_refuses_ordinary_text(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _facet_record({"material.name": "reclaimed oak", "material.color": "reclaimed oak"})
        self.assertIn("#RRGGBB", str(raised.exception))

    def test_free_text_boundary_lengths_are_accepted(self) -> None:
        _facet_record({"material.name": "x" * FREE_TEXT_MIN})
        _facet_record({"material.name": "x" * FREE_TEXT_MAX})

    def test_no_facets_and_empty_facets_are_both_valid(self) -> None:
        _facet_record()
        _facet_record({})

    def test_unknown_key_is_refused_naming_the_nearest_key(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _facet_record({"architectural.rol": "wall"})
        message = str(raised.exception)
        self.assertIn("architectural.rol", message)
        self.assertIn("not registered", message)
        self.assertIn("architectural.role", message)

    def test_wrong_enumerated_value_is_refused_naming_the_allowed_values(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _facet_record({"architectural.role": "shed"})
        message = str(raised.exception)
        for value in FACETS["architectural.role"]:
            self.assertIn(value, message)

    def test_wrong_enclosure_value_is_refused_naming_the_allowed_values(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _facet_record({"architectural.enclosure": "outdoor"})
        message = str(raised.exception)
        self.assertIn("exterior", message)
        self.assertIn("interior", message)

    def test_empty_free_text_is_refused(self) -> None:
        with self.assertRaises(StateRecordError):
            _facet_record({"material.name": ""})

    def test_overlong_free_text_is_refused(self) -> None:
        with self.assertRaises(StateRecordError):
            _facet_record({"fabrication.method": "x" * (FREE_TEXT_MAX + 1)})

    def test_non_mapping_facets_is_refused(self) -> None:
        with self.assertRaises(StateRecordError):
            _facet_record(["architectural.role"])
        with self.assertRaises(StateRecordError):
            _facet_record("architectural.role")

    def test_non_string_value_is_refused(self) -> None:
        with self.assertRaises(StateRecordError):
            _facet_record({"architectural.role": 1})
        with self.assertRaises(StateRecordError):
            _facet_record({"structural.role": None})

    def test_non_string_key_is_refused(self) -> None:
        with self.assertRaises(StateRecordError):
            _facet_record({1: "wall"})


class ComponentFacetsRecordTests(unittest.TestCase):
    """A StateRecord whose Component@1 carries facets loads, round-trips and reads back."""

    def test_component_facets_round_trips_through_to_dict_and_from_dict(self) -> None:
        record = _facet_record({"architectural.role": "wall", "material.name": "concrete"})
        self.assertEqual(component_facets(record.entity("c")), {"architectural.role": "wall", "material.name": "concrete"})
        reloaded = StateRecord.from_dict(record.to_dict())
        self.assertEqual(component_facets(reloaded.entity("c")), {"architectural.role": "wall", "material.name": "concrete"})
        self.assertEqual(reloaded.entity("c"), record.entity("c"))

    def test_component_facets_is_an_empty_dict_when_absent(self) -> None:
        self.assertEqual(component_facets(_facet_record().entity("c")), {})
        self.assertEqual(component_facets(_facet_record({}).entity("c")), {})

    def test_component_facets_is_unrelated_to_semantic_kind_roles_and_conditions(self) -> None:
        record = StateRecord(project_id="demo", run_id="run-1", entities=(
            Entity("c", "Component@1", {"semantic_kind": "whole-building", "roles": ["role.access"],
                                         "facets": {"architectural.role": "door"}}),
        ))
        self.assertEqual(component_facets(record.entity("c")), {"architectural.role": "door"})


class ComponentFacetsEditIdentityTests(unittest.TestCase):
    """D-419-0: a facets-only edit leaves every non-component entity byte-identical."""

    def test_editing_only_a_components_facets_leaves_elements_dependencies_and_datums_untouched(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        before_non_component = {e.entity_id: e.to_dict() for e in record.entities if e.schema != "Component@1"}
        before_edges = record.dependency_edges()
        before_rows = element_rows_of(record)

        building = record.entity("building")
        self.assertEqual(component_facets(building), {})
        edited = replace(building, fields={**building.fields, "facets": {"architectural.role": "wall", "architectural.enclosure": "exterior"}})
        successor = apply_state_record_operator(record, compile_component_edit(record, entities=(edited,)))

        self.assertEqual(component_facets(successor.entity("building")),
                          {"architectural.role": "wall", "architectural.enclosure": "exterior"})
        # Every other Component@1 is untouched too.
        for entity_id in ("portico-west", "portico-columns", "portico-entablature"):
            self.assertEqual(successor.entity(entity_id), record.entity(entity_id))
        after_non_component = {e.entity_id: e.to_dict() for e in successor.entities if e.schema != "Component@1"}
        self.assertEqual(after_non_component, before_non_component)
        self.assertEqual(successor.dependency_edges(), before_edges)
        self.assertEqual(element_rows_of(successor), before_rows)

    def test_removing_a_components_facets_is_also_a_component_only_edit(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        building = record.entity("building")
        faceted = apply_state_record_operator(record, compile_component_edit(
            record, entities=(replace(building, fields={**building.fields, "facets": {"architectural.role": "wall"}}),)))
        before_non_component = {e.entity_id: e.to_dict() for e in faceted.entities if e.schema != "Component@1"}
        before_edges = faceted.dependency_edges()
        before_rows = element_rows_of(faceted)

        refaceted_building = faceted.entity("building")
        cleared = {k: v for k, v in refaceted_building.fields.items() if k != "facets"}
        successor = apply_state_record_operator(faceted, compile_component_edit(
            faceted, entities=(replace(refaceted_building, fields=cleared),)))

        self.assertEqual(component_facets(successor.entity("building")), {})
        after_non_component = {e.entity_id: e.to_dict() for e in successor.entities if e.schema != "Component@1"}
        self.assertEqual(after_non_component, before_non_component)
        self.assertEqual(successor.dependency_edges(), before_edges)
        self.assertEqual(element_rows_of(successor), before_rows)


if __name__ == "__main__":
    unittest.main()
