"""L4 domain readiness: a domain reads only the semantics it needs.

``monkeyarch.domain.domain_readiness`` is the honest entry gate every
technical domain calls before it runs (spec docs/design/construction-api.md
§3.6): it never infers ``architectural.role``, ``structural.role``,
``architectural.enclosure`` or ``material.name`` from an entity's shape,
producer or name, and it says exactly what is missing and why when it cannot
evaluate an entity yet.
"""

from __future__ import annotations

import unittest

from archflow.semantics.facets import FACETS
from archflow.state.state_record import Entity, StateRecord
from monkeyarch.domain.domain_readiness import DOMAINS, DomainUnknown, describe, readiness

_NO_ELEMENT = object()


def _component(component_id: str, facets: dict[str, str] | None = None, *, element: object = None) -> tuple[Entity, ...]:
    """A ``Component@1`` (optionally faceted) and the one ``Element@1`` it owns.

    Pass ``element=_NO_ELEMENT`` for a component the geometry compiler has not
    reached yet: it is not read and never asked about (spec §3.6: readiness
    is over every ``Component@1`` that owns at least one ``Element@1``).
    """

    fields: dict[str, object] = {"intent": component_id}
    if facets:
        fields["facets"] = dict(facets)
    entities: list[Entity] = [Entity(component_id, "Component@1", fields)]
    if element is not _NO_ELEMENT:
        entities.append(Entity(
            f"{component_id}-body", "Element@1",
            {"component_id": component_id, "producer": "prism"},
            parent_id=component_id,
        ))
    return tuple(entities)


def _record(*groups: tuple[Entity, ...]) -> StateRecord:
    entities: list[Entity] = [entity for group in groups for entity in group]
    return StateRecord(project_id="demo", run_id="run-1", entities=tuple(entities))


class DomainRegistryTests(unittest.TestCase):
    """``DOMAINS``, in the style of ``tests/integration/test_facets.py``'s registry tests."""

    def test_registry_has_exactly_structure_and_envelope(self) -> None:
        self.assertEqual(set(DOMAINS), {"structure", "envelope"})

    def test_structure_reads_and_needs_exactly_the_spec_sets(self) -> None:
        structure = DOMAINS["structure"]
        self.assertEqual(structure.reads, frozenset({
            "wall", "column", "beam", "slab", "floor", "roof", "foundation", "stair",
        }))
        self.assertEqual(structure.needs, ("structural.role", "material.name"))

    def test_envelope_reads_and_needs_exactly_the_spec_sets(self) -> None:
        envelope = DOMAINS["envelope"]
        self.assertEqual(envelope.reads, frozenset({"wall", "roof", "slab", "floor", "window", "door"}))
        self.assertEqual(envelope.needs, ("architectural.enclosure", "material.name"))


class UnclassifiedEntityTests(unittest.TestCase):
    """A block with no ``architectural.role`` at all: a domain cannot even tell whether it is its business."""

    def test_structure_asks_for_architectural_role_with_its_own_reason(self) -> None:
        result = readiness(_record(_component("block-1")), "structure")
        self.assertEqual(result["status"], "enrichment_required")
        self.assertEqual(result["reads"], [])
        self.assertEqual(result["requests"], [{
            "id": "block-1",
            "missing": ["architectural.role"],
            "reason": "no architectural.role: the structure domain cannot tell whether block-1 carries load",
        }])

    def test_envelope_asks_for_architectural_role_with_its_own_reason(self) -> None:
        result = readiness(_record(_component("block-1")), "envelope")
        self.assertEqual(result["status"], "enrichment_required")
        self.assertEqual(result["requests"], [{
            "id": "block-1",
            "missing": ["architectural.role"],
            "reason": "no architectural.role: the envelope domain cannot tell whether block-1 belongs to the envelope",
        }])

    def test_a_component_with_no_owned_element_is_not_read_or_asked_about(self) -> None:
        record = _record(_component("floating-intent", element=_NO_ELEMENT))
        result = readiness(record, "structure")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["reads"], [])
        self.assertEqual(result["requests"], [])


class MissingFacetTests(unittest.TestCase):
    """A read role short of what the domain needs: a request naming exactly the missing keys."""

    def test_a_wall_missing_material_lists_exactly_that_key(self) -> None:
        record = _record(_component("wall-1", {"architectural.role": "wall", "structural.role": "load_bearing"}))
        result = readiness(record, "structure")
        self.assertEqual(result["status"], "enrichment_required")
        self.assertEqual(result["reads"], [])
        self.assertEqual(result["requests"], [{
            "id": "wall-1",
            "missing": ["material.name"],
            "reason": "structure reads wall and needs material.name to evaluate it",
        }])

    def test_a_wall_missing_every_needed_facet_lists_them_joined_with_and(self) -> None:
        record = _record(_component("wall-2", {"architectural.role": "wall"}))
        [request] = readiness(record, "structure")["requests"]
        self.assertEqual(request["missing"], ["material.name", "structural.role"])
        self.assertEqual(request["reason"], "structure reads wall and needs material.name and structural.role to evaluate it")

    def test_envelope_asks_a_roof_for_enclosure_not_structural_role(self) -> None:
        record = _record(_component("roof-1", {"architectural.role": "roof", "material.name": "membrane"}))
        [request] = readiness(record, "envelope")["requests"]
        self.assertEqual(request["missing"], ["architectural.enclosure"])
        self.assertEqual(request["reason"], "envelope reads roof and needs architectural.enclosure to evaluate it")


class CompleteFacetsTests(unittest.TestCase):
    """Every needed facet present: the component is read, and the domain is ready."""

    def test_a_fully_faceted_wall_is_read_and_structure_is_ready(self) -> None:
        record = _record(_component("wall-3", {
            "architectural.role": "wall", "structural.role": "load_bearing", "material.name": "concrete",
        }))
        result = readiness(record, "structure")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["reads"], ["wall-3"])
        self.assertEqual(result["requests"], [])

    def test_the_same_wall_is_also_ready_for_envelope(self) -> None:
        record = _record(_component("wall-3", {
            "architectural.role": "wall", "architectural.enclosure": "exterior", "material.name": "concrete",
        }))
        result = readiness(record, "envelope")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["reads"], ["wall-3"])


class RoleOutsideReadSetTests(unittest.TestCase):
    """A registered role neither domain reads: not read, and never asked about."""

    def test_furniture_is_neither_read_nor_requested_by_structure(self) -> None:
        record = _record(_component("chair-1", {"architectural.role": "furniture"}))
        result = readiness(record, "structure")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["reads"], [])
        self.assertEqual(result["requests"], [])

    def test_furniture_is_neither_read_nor_requested_by_envelope(self) -> None:
        record = _record(_component("chair-1", {"architectural.role": "furniture"}))
        result = readiness(record, "envelope")
        self.assertEqual(result["reads"], [])
        self.assertEqual(result["requests"], [])

    def test_a_column_is_structures_business_but_not_envelopes(self) -> None:
        record = _record(_component("column-1", {
            "architectural.role": "column", "structural.role": "load_bearing", "material.name": "steel",
        }))
        self.assertEqual(readiness(record, "structure")["reads"], ["column-1"])
        envelope = readiness(record, "envelope")
        self.assertEqual(envelope["reads"], [])
        self.assertEqual(envelope["requests"], [])


_LOAD_BEARING_WALL = {"architectural.role": "wall", "structural.role": "load_bearing", "material.name": "brick"}


def _host(component_id: str, facets: dict[str, str], *, cuts: tuple[str, ...]) -> tuple[Entity, ...]:
    """A faceted component whose one element names ``cuts`` in ``references.voids`` (none: it cuts nothing)."""

    component, element = _component(component_id, facets)
    references = {"voids": list(cuts)} if cuts else {}
    return component, Entity(element.entity_id, element.schema, {**element.fields, "references": references},
                             parent_id=component_id)


class HiddenCutterTests(unittest.TestCase):
    """A component whose every element cuts another is a construction helper, not something to classify (#419).

    What it removes is part of its host's form; asked about, the architect
    would have to give a helper meaning. Uncut, it is ordinary geometry again.
    """

    def test_a_hidden_cutter_is_neither_asked_about_nor_read(self) -> None:
        for facets in (None, {"architectural.role": "column", "structural.role": "load_bearing", "material.name": "steel"}):
            with self.subTest(facets=facets):
                record = _record(_host("mass", _LOAD_BEARING_WALL, cuts=("block-body",)), _component("block", facets))
                result = readiness(record, "structure")
                self.assertEqual((result["status"], result["reads"], result["requests"]), ("ready", ["mass"], []))

    def test_the_same_component_uncut_is_asked_about_again(self) -> None:
        record = _record(_host("mass", _LOAD_BEARING_WALL, cuts=()), _component("block"))
        result = readiness(record, "structure")
        self.assertEqual((result["status"], result["reads"]), ("enrichment_required", ["mass"]))
        self.assertEqual(result["requests"], [{
            "id": "block", "missing": ["architectural.role"],
            "reason": "no architectural.role: the structure domain cannot tell whether block carries load",
        }])

    def test_a_component_with_an_ordinary_element_beside_its_cutter_is_still_considered(self) -> None:
        pair = (Entity("pair", "Component@1", {"intent": "pair"}),
                *(Entity(element_id, "Element@1", {"component_id": "pair", "producer": "prism"}, parent_id="pair")
                  for element_id in ("pair-cutter", "pair-body")))
        record = _record(_host("mass", _LOAD_BEARING_WALL, cuts=("pair-cutter",)), pair)
        result = readiness(record, "structure")
        self.assertEqual(result["reads"], ["mass"])
        self.assertEqual([(request["id"], request["missing"]) for request in result["requests"]],
                         [("pair", ["architectural.role"])])


class OrderingTests(unittest.TestCase):
    """``reads`` and ``requests`` both come back sorted by id, not in record order."""

    def test_reads_and_requests_are_sorted_by_id(self) -> None:
        record = _record(
            _component("z-wall", {"architectural.role": "wall", "structural.role": "load_bearing", "material.name": "steel"}),
            _component("a-wall", {"architectural.role": "wall", "structural.role": "load_bearing", "material.name": "steel"}),
            _component("y-block"),
            _component("b-block"),
        )
        result = readiness(record, "structure")
        self.assertEqual(result["reads"], ["a-wall", "z-wall"])
        self.assertEqual([request["id"] for request in result["requests"]], ["b-block", "y-block"])


class KnownFacetsTests(unittest.TestCase):
    """``known_facets`` names exactly the keys the domain asks about, with their registered values."""

    def test_structures_known_facets_cover_role_structural_role_and_material(self) -> None:
        result = readiness(_record(_component("block-1")), "structure")
        self.assertEqual(set(result["known_facets"]), {"architectural.role", "structural.role", "material.name"})
        self.assertEqual(result["known_facets"]["material.name"], "free text")
        self.assertEqual(result["known_facets"]["structural.role"], list(FACETS["structural.role"]))
        self.assertEqual(result["known_facets"]["architectural.role"], list(FACETS["architectural.role"]))

    def test_envelopes_known_facets_cover_role_enclosure_and_material(self) -> None:
        result = readiness(_record(_component("block-1")), "envelope")
        self.assertEqual(set(result["known_facets"]), {"architectural.role", "architectural.enclosure", "material.name"})
        self.assertEqual(result["known_facets"]["architectural.enclosure"], list(FACETS["architectural.enclosure"]))


class DescribeDomainTests(unittest.TestCase):
    """``describe``: a domain's own description, with no record in hand at all."""

    def test_describe_names_reads_needs_and_known_facets(self) -> None:
        info = describe("envelope")
        self.assertEqual(info["domain"], "envelope")
        self.assertEqual(info["reads"], sorted({"wall", "roof", "slab", "floor", "window", "door"}))
        self.assertEqual(info["needs"], ["architectural.enclosure", "material.name"])
        self.assertEqual(set(info["known_facets"]), {"architectural.role", "architectural.enclosure", "material.name"})

    def test_describe_refuses_an_unknown_domain(self) -> None:
        with self.assertRaises(DomainUnknown):
            describe("acoustics")


class UnknownDomainTests(unittest.TestCase):
    """An unrecognised domain name is refused, naming the domains that do exist."""

    def test_readiness_refuses_an_unknown_domain_naming_the_known_ones(self) -> None:
        record = _record(_component("block-1"))
        with self.assertRaises(DomainUnknown) as raised:
            readiness(record, "acoustics")
        message = str(raised.exception)
        self.assertIn("acoustics", message)
        self.assertIn("structure", message)
        self.assertIn("envelope", message)

    def test_domain_unknown_is_a_key_error(self) -> None:
        self.assertIsInstance(DomainUnknown("acoustics"), KeyError)


if __name__ == "__main__":
    unittest.main()
