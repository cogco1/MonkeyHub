"""#419 Stage C: a block given the meaning of a wall is realised as one, in place (spec §3.5).

``wall_fields_from_block`` turns a rectangular block's ``Element@1`` fields into
the fields the wall realisation reads: the line along the block's longest side,
the thickness across it, and everything else (its height or top, its voids, its
base, the row's own notes) as it was. The converted row makes the same solid
under the same ``obj-<element>`` and publishes ``<element>-top`` exactly as the
block did, so whatever stands on the block keeps standing. A block that is not a
straight rectangle standing on a level or another solid's top is refused with
the reason, and nothing is converted. A door or window filled by its family is
delivered before any space it joins is modelled.
"""
from __future__ import annotations

import copy
import json
import math
import unittest
from dataclasses import replace

from archflow.adapters.cad_program import expected_object_bounds
from archflow.state.state_record import project_grids_of, project_levels_of
from monkeyarch.capabilities.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    element_rows_of,
    produce_rows,
    production_order,
    validate_element_contract,
    wall_along_line,
    wall_fields_from_block,
    with_void_hosts,
)
from monkeyarch.capabilities.geometry_proposal import GeometryProposalStatus
from monkeyarch.capabilities.opening_solver import INTERFACE_INSIDE_OUTSIDE
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeyarch.compilers.geometry import compile_geometry_program
from tests.support import ProducerFixture, authored_record
from tests.test_element_producers import BASIS, PN, _grids, _levels
from tests.test_geometry_compiler import COMMITMENT, _only, _proposal, _state

LONG, DEEP = 5.0, 0.25
NOT_A_RECTANGLE = "the block's footprint is not a rectangle"
TILTED = "the block is drawn on a tilted plane"
CUTOUTS = "the block has panel cutouts"
LIFTED = "the block is lifted off its base; place it on a level or another solid's top first"


def _rectangle(degrees: float = 0.0, *, clockwise: bool = False, at=(2.0, 3.0), long: float = LONG,
               deep: float = DEEP) -> list[list[float]]:
    """A long, shallow rectangle turned by ``degrees`` about its first corner, placed at ``at``."""

    corners = [(0.0, 0.0), (long, 0.0), (long, deep), (0.0, deep)]
    if clockwise:
        corners = [corners[0], *reversed(corners[1:])]
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [[at[0] + x * c - z * s, at[1] + x * s + z * c] for x, z in corners]


def _block(profile, *, references=None, **params) -> dict:
    """A block's Element@1 fields as a construction script lowers them: a profile pulled up from a base."""

    return {"component_id": "block-1", "producer": "prism",
            "references": references or {"base": {"level": PN}},
            "params": {"profile": profile, "height": 3.0, **params}}


def _row(element_id: str, fields: dict) -> ElementRow:
    return ElementRow(element_id, str(fields["component_id"]), str(fields["producer"]),
                      fields.get("references", {}), fields.get("params", {}), BASIS)


def _converted(element_id: str, fields: dict) -> ElementRow:
    return _row(element_id, wall_fields_from_block(fields, element_id))


def _produced(*rows: ElementRow):
    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={},
                                frame_id="world")
    ordered = production_order(with_void_hosts(tuple(rows)))
    return dict(zip((row.element_id for row in ordered), produce_rows(ordered, context))), context


def _compiled(case: unittest.TestCase, *rows: ElementRow) -> dict[str, dict]:
    """Every delivered object's bounds, through the compiler path the producer tests use."""

    produced, context = _produced(*rows)
    operations = tuple(replace(op, semantic_binding_ids=("building-binding",))
                       for element in produced.values() for op in element.operations)
    datums = tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id))
    state = _state()
    result = compile_geometry_program(
        state, _only(_proposal(state, extra_operations=operations), operations, ()),
        active_commitment_refs=(COMMITMENT,), interface_datums=datums,
        datum_bindings=tuple(b for element in produced.values() for b in element.bindings))
    case.assertIsNotNone(result.program, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
    return expected_object_bounds(result.program)


def _datum(context: ProductionContext, datum_id: str) -> tuple[str, float]:
    datum = context.published[datum_id]
    return datum.published_by, json.loads(datum.value_json)


class ConversionTests(unittest.TestCase):
    """The converted row is the same solid, under the same id, publishing the same top."""

    def test_a_rectangle_in_any_orientation_becomes_the_same_solid_under_the_same_object(self) -> None:
        cases = {f"{degrees} degrees, {'clockwise' if clockwise else 'counter-clockwise'}": _rectangle(degrees, clockwise=clockwise)
                 for degrees in (0.0, 30.0, 90.0, 217.5) for clockwise in (False, True)}
        cases["a square"] = _rectangle(30.0, deep=LONG)
        for label, profile in cases.items():
            with self.subTest(label):
                block = _block(profile)
                before = _compiled(self, _row("block-1-body", block))
                after = _compiled(self, _converted("block-1-body", block))
                self.assertEqual(sorted(after), ["obj-block-1-body"])
                self.assertEqual(after, before)

    def test_the_line_runs_along_the_longest_side_with_the_thickness_across_it(self) -> None:
        for label, profile, line, thickness in (
            ("long in x", [[0, 0], [6, 0], [6, 0.3], [0, 0.3]],
             {"from": {"point": [0, 0]}, "to": {"point": [6, 0]}, "inward": [0.0, 1.0]}, 0.3),
            ("long in z: its second side", [[0, 0], [0.3, 0], [0.3, 6], [0, 6]],
             {"from": {"point": [0.3, 0]}, "to": {"point": [0.3, 6]}, "inward": [-1.0, 0.0]}, 0.3),
            ("drawn clockwise", [[0, 0], [0, 0.3], [6, 0.3], [6, 0]],
             {"from": {"point": [0, 0.3]}, "to": {"point": [6, 0.3]}, "inward": [0.0, -1.0]}, 0.3),
            ("a square: its first side", [[1, 1], [3, 1], [3, 3], [1, 3]],
             {"from": {"point": [1, 1]}, "to": {"point": [3, 1]}, "inward": [0.0, 1.0]}, 2),
        ):
            with self.subTest(label):
                fields = wall_fields_from_block(_block(profile), "block-1-body")
                self.assertEqual(fields["producer"], "wall")
                self.assertEqual(fields["references"]["line"], line)
                self.assertEqual(fields["params"], {"height": 3.0, "thickness": thickness, "openings": []})
        turned = wall_fields_from_block(_block(_rectangle(30.0)), "block-1-body")
        inward = turned["references"]["line"]["inward"]
        self.assertAlmostEqual(math.hypot(*inward), 1.0, places=9)
        self.assertAlmostEqual(inward[0], -math.sin(math.radians(30.0)), places=9)
        self.assertAlmostEqual(inward[1], math.cos(math.radians(30.0)), places=9)
        self.assertAlmostEqual(turned["params"]["thickness"], DEEP, places=9)

    def test_everything_but_the_outline_is_kept_and_the_given_fields_are_untouched(self) -> None:
        stacked = {**_block([[0, 0], [4, 0], [4, 0.2], [0, 0.2]], references={
            "base": {"datum": "plinth-top"}, "top": {"level": PN}, "voids": ["cutter-body"]}),
            "name": "block one", "label": "B1", "note": "from the survey",
            "sourceDocumentTrace": {"documentId": "survey", "page": 2}}
        stacked["params"] = {"profile": stacked["params"]["profile"], "elevation": 0}
        given = copy.deepcopy(stacked)
        fields = wall_fields_from_block(stacked, "block-1-body")
        self.assertEqual(stacked, given, "the fields given are not changed")
        self.assertEqual({key: value for key, value in fields.items() if key not in ("producer", "references", "params")},
                         {key: value for key, value in given.items() if key not in ("producer", "references", "params")})
        self.assertEqual(fields["references"], {
            "base": {"datum": "plinth-top"}, "top": {"level": PN}, "voids": ["cutter-body"],
            "line": {"from": {"point": [0, 0]}, "to": {"point": [4, 0]}, "inward": [0.0, 1.0]}})
        self.assertEqual(fields["params"], {"thickness": 0.2, "openings": []})
        bound = wall_fields_from_block(_block(_rectangle(), references={"base": {"datum": "plinth-top", "offset": 0}},
                                              height="@storey"), "block-1-body")
        self.assertEqual(bound["params"]["height"], "@storey")
        self.assertEqual(bound["references"]["base"], {"datum": "plinth-top"}, "a zero offset is no offset")

    def test_what_stands_on_the_block_keeps_standing(self) -> None:
        block = _block([[0, 0], [6, 0], [6, 0.3], [0, 0.3]])
        shelf = _row("shelf-body", {"component_id": "shelf", "producer": "prism",
                                    "references": {"base": {"datum": "block-1-body-top"}},
                                    "params": {"profile": [[1, 0], [2, 0], [2, 0.3], [1, 0.3]], "height": 0.5}})
        before_rows = (shelf, _row("block-1-body", block))
        after_rows = (shelf, _converted("block-1-body", block))
        before, after = _compiled(self, *before_rows), _compiled(self, *after_rows)
        self.assertEqual(after, before)
        (_, block_context), (produced, wall_context) = _produced(*before_rows), _produced(*after_rows)
        self.assertEqual(_datum(wall_context, "block-1-body-top"), _datum(block_context, "block-1-body-top"))
        publisher, value = _datum(wall_context, "block-1-body-top")
        self.assertEqual(publisher, "obj-block-1-body")
        self.assertAlmostEqual(value, 3.57 + 3.0, places=9)
        self.assertEqual([binding.datum_id for binding in produced["shelf-body"].bindings], ["block-1-body-top"])

    def test_a_cut_block_keeps_its_cutters(self) -> None:
        cutter = _row("cutter-body", {"component_id": "cutter", "producer": "prism", "references": {"base": {"level": PN}},
                                      "params": {"profile": [[1, -0.1], [2, -0.1], [2, 0.4], [1, 0.4]], "height": 1.2,
                                                 "elevation": 0.8}})
        block = _block([[0, 0], [6, 0], [6, 0.3], [0, 0.3]], references={"base": {"level": PN}, "voids": ["cutter-body"]})
        before = _compiled(self, _row("block-1-body", block), cutter)
        after = _compiled(self, _converted("block-1-body", block), cutter)
        self.assertEqual(after, before)
        self.assertEqual(sorted(after), ["obj-block-1-body", "obj-cutter-body"])
        produced, _ = _produced(_converted("block-1-body", block), cutter)
        [cut] = [op for op in produced["block-1-body"].operations if op.op_id == "block-1-body"]
        self.assertEqual(cut.input_object_ids, ("obj-block-1-body-body", "obj-cutter-body"))

    def test_a_record_whose_plinth_becomes_a_wall_still_carries_what_stands_on_it(self) -> None:
        record = authored_record()
        plinth = next(entity for entity in record.entities if entity.entity_id == "plinth")
        converted = replace(plinth, fields=wall_fields_from_block(plinth.fields, "plinth"))
        record = replace(record, entities=tuple(converted if entity.entity_id == "plinth" else entity
                                                for entity in record.entities))
        validate_element_contract(record, ("plinth", "wall-south"))
        context = ProductionContext(references=ReferenceContext(grids=project_grids_of(record),
                                                                levels=project_levels_of(record)), published={})
        rows = element_rows_of(record)
        produced = dict(zip((row.element_id for row in rows), produce_rows(rows, context)))
        self.assertEqual(sorted(produced), ["plinth", "wall-south"])
        self.assertEqual(_datum(context, "plinth-top"), ("obj-plinth", 0.6))
        self.assertIn("plinth-top", {binding.datum_id for binding in produced["wall-south"].bindings})


class WallTopTests(unittest.TestCase):
    """Every wall publishes ``<element>-top`` as a block does: its base plus its height, published by its object."""

    def _wall(self, **references) -> ElementRow:
        return ElementRow("block-2-body", "block-2", "wall", {
            "base": {"level": PN}, "line": {"from": {"point": [0, 0]}, "to": {"point": [4, 0]}}, **references},
            {"thickness": 0.3, "height": 2.8,
             "openings": [{"opening_id": "opening-1", "kind": "window", "along": 2, "width": 1, "sill": 0.9,
                           "head": 2.1}]}, BASIS)

    def test_the_top_is_the_base_plus_the_height(self) -> None:
        produced, context = _produced(self._wall())
        self.assertEqual([datum.datum_id for datum in produced["block-2-body"].datums], ["block-2-body-top"])
        publisher, value = _datum(context, "block-2-body-top")
        self.assertEqual(publisher, "obj-block-2-body")
        self.assertAlmostEqual(value, 3.57 + 2.8, places=9)

    def test_a_wall_ending_at_a_level_publishes_that_level(self) -> None:
        wall = self._wall(top={"offset_from": {"level": PN, "offset": 3.0}})
        wall = replace(wall, params={key: value for key, value in wall.params.items() if key != "height"})
        _, context = _produced(wall)
        self.assertAlmostEqual(_datum(context, "block-2-body-top")[1], 3.57 + 3.0, places=9)


class AlongLineTests(unittest.TestCase):
    """Where ``along`` is measured on a wall line: the wall keeps thickness on the right of its line."""

    def test_along_starts_where_the_thickness_is_on_the_right(self) -> None:
        line = {"from": {"point": [0, 0]}, "to": {"point": [6, 0]}}
        self.assertEqual(wall_along_line(line), ((0.0, 0.0), (6.0, 0.0)))
        self.assertEqual(wall_along_line({**line, "inward": [0, -1]}), ((0.0, 0.0), (6.0, 0.0)))
        self.assertEqual(wall_along_line({**line, "inward": [0, 1]}), ((6.0, 0.0), (0.0, 0.0)))
        converted = wall_fields_from_block(_block([[0, 0], [6, 0], [6, 0.3], [0, 0.3]]), "block-1-body")
        self.assertEqual(wall_along_line(converted["references"]["line"]), ((6.0, 0.0), (0.0, 0.0)))

    def test_a_line_that_is_not_two_stated_points_has_no_stated_along(self) -> None:
        for line in ({"from": {"grid": ["1", "W"]}, "to": {"grid": ["2", "W"]}},
                     {"from": {"point": ["@x", 0]}, "to": {"point": [6, 0]}},
                     {"from": {"point": [1, 1]}, "to": {"point": [1, 1]}}):
            with self.subTest(line=line), self.assertRaises(ElementProducerError):
                wall_along_line(line)


class ScriptOnARealisedBlockTests(unittest.TestCase):
    """A construction script still stands shapes on the block after it is realised as a wall, moved or not."""

    def test_top_of_the_realised_block_is_a_base_before_and_after_a_move(self) -> None:
        from monkeyarch.construction import compile_construction_script
        from tests.test_construction_lowering import _apply, _compile, _record

        record = _apply(_record(), _compile("block = extrude(rect(0, 0, 6, 0.3), 3)"))
        block = next(entity for entity in record.entities if entity.entity_id == "block-body")
        record = replace(record, entities=tuple(
            replace(block, fields=wall_fields_from_block(block.fields, "block-body")) if entity is block else entity
            for entity in record.entities))
        for script, placed in (
            ('shelf = extrude(rect(1, 0, 1, 0.3), 0.2, at=top(get("block")))', [[1.0, 0.0], [2.0, 0.0], [2.0, 0.3], [1.0, 0.3]]),
            ('b = get("block")\nmove(b, dx=1)\nshelf = extrude(rect(2, 0, 1, 0.3), 0.2, at=top(b))',
             [[2.0, 0.0], [3.0, 0.0], [3.0, 0.3], [2.0, 0.3]]),
        ):
            with self.subTest(script=script):
                result = compile_construction_script(script, record, root_component_id="model")
                [shelf] = [row for row in result.entities if row["entity_id"] == "shelf-body"]
                self.assertEqual(shelf["fields"]["references"]["base"], {"datum": "block-body-top"})
                self.assertEqual(shelf["fields"]["params"]["profile"], placed)
                _apply(record, result)


class OpeningInterfaceTests(ProducerFixture):
    """A filled door or window is delivered before the spaces it joins are modelled (D-419-0).

    Its assembly cites the opening solver's own interface (the opening passes
    through its host) until a record states a connection it serves; that one
    needs no declaration. Any other interface is still a fact the record must state.
    """

    async def _produced_with(self, interface_ref: str):
        from monkeyarch.capabilities.geometry_proposal import proposal_authoring_output
        from tests.support import ScriptedProvider

        [assembly] = self.proposal.assemblies
        proposal = replace(self.proposal, assemblies=(replace(assembly, interface_refs=(interface_ref,)),))
        result = await self.produce(ScriptedProvider((proposal_authoring_output(proposal),)))
        issues = [(issue["code"], issue["detail"]) for ref in result.round_refs
                  for issue in self.repository.load_json(ref).get("issues", [])]
        return result.status, issues

    async def test_the_opening_solvers_own_interface_needs_no_declared_connection(self) -> None:
        status, issues = await self._produced_with(INTERFACE_INSIDE_OUTSIDE)
        self.assertIs(status, GeometryProposalStatus.ACCEPTED, issues)

    async def test_any_other_undeclared_interface_is_still_refused(self) -> None:
        status, issues = await self._produced_with("relation:somewhere-else")
        self.assertIsNot(status, GeometryProposalStatus.ACCEPTED)
        self.assertIn(("malformed_model_output", "assembly interface_refs are absent from the supplied spatial option; "
                                                 "unavailable=['relation:somewhere-else']"), issues)


class RefusalTests(unittest.TestCase):
    """Anything but a straight rectangle standing on a level or a solid's top is refused with the reason."""

    def test_each_refusal_names_its_reason(self) -> None:
        rectangle = [[0, 0], [6, 0], [6, 0.3], [0, 0.3]]
        cases = {
            NOT_A_RECTANGLE: [
                _block([[0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [0, 3]]),                      # an L
                _block([[0, 0], [4, 0], [5, 1], [1, 1]]),                                        # a parallelogram
                _block([[0, 0], [4, 0], [4, 1]]),                                                # a triangle
                _block([[0, 0], [4, 0], [4, 0], [0, 1]]),                                        # a side of no length
                _block([[0, 0], [4, 0], [4, 1], [0, 1.000001]]),                                 # almost
            ],
            TILTED: [_block(rectangle, work_plane={"origin": [0, 0, 0], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0],
                                                   "normal": [0, 0, 1]})],
            CUTOUTS: [_block(rectangle, rectangular_cutouts=[{"cutout_id": "c", "span0": 1, "span1": 2,
                                                              "bottom": 0, "top": 1}])],
            LIFTED: [
                _block(rectangle, elevation=0.4),
                _block(rectangle, elevation="@lift"),
                _block(rectangle, references={"base": {"offset_from": {"level": PN, "offset": 0.2}}}),
                _block(rectangle, references={"base": {"datum": "plinth-top", "offset": 0.2}}),
                _block(rectangle, references={"base": {"elevation": 1.0}}),
            ],
            "the shape is not a block extruded up from a plan outline": [
                {"component_id": "block-1", "producer": "loft", "references": {"base": {"level": PN}},
                 "params": {"profiles": [[[0, 0, 0], [1, 0, 0], [1, 0, 1]], [[0, 1, 0], [1, 1, 0], [1, 1, 1]]],
                            "profile_size": 3}},
            ],
            "the block's outline is bound to project parameters": [
                _block([[0, 0], ["@long", 0], ["@long", 0.3], [0, 0.3]]),
            ],
        }
        for reason, blocks in cases.items():
            for index, fields in enumerate(blocks):
                with self.subTest(reason=reason, case=index):
                    with self.assertRaises(ElementProducerError) as raised:
                        wall_fields_from_block(fields, "block-1-body")
                    self.assertEqual(str(raised.exception), f"block-1-body: {reason}")


if __name__ == "__main__":
    unittest.main()
