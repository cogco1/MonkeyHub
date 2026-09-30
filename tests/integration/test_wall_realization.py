"""#419 Stage C: a block given the meaning of a wall is realised as one, in place (spec §3.5).

``wall_fields_from_block`` turns a rectangular block's ``Element@1`` fields into
the fields the wall realisation reads: the line along the block's longest side,
the thickness across it, and everything else (its height or top, its voids, its
base, the row's own notes) as it was. The converted row makes the same solid
under the same ``obj-<element>`` and publishes ``<element>-top`` exactly as the
block did, so whatever stands on the block keeps standing. A block that is not a
straight rectangle standing on a level or another solid's top is refused with
the reason, and nothing is converted. ``along`` starts beside the longest side's
first corner, whichever way the outline winds. A door or window filled by its
family cites no interface it was not given.
"""
from __future__ import annotations

import copy
import json
import math
import unittest
from dataclasses import replace

from archflow.state.geometry_program import expected_object_bounds
from archflow.state.state_record import project_grids_of, project_levels_of
from monkeyarch.authoring.element_producers import (
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
from monkeyarch.application.geometry_proposal import GeometryProposalStatus
from monkeyarch.domain.reference_resolver import ReferenceContext
from monkeyarch.compilation.geometry import compile_geometry_program
from tests.integration.support import ProducerFixture, authored_record
from tests.integration.test_element_producers import BASIS, PN, _grids, _levels
from tests.integration.test_geometry_compiler import COMMITMENT, _only, _proposal, _state

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
            # rect() winds counter-clockwise: the line takes the opposite long face, running the same way.
            ("long in x", [[0, 0], [6, 0], [6, 0.3], [0, 0.3]],
             {"from": {"point": [0, 0.3]}, "to": {"point": [6, 0.3]}, "inward": [0.0, -1.0]}, 0.3),
            ("long in z: its second side", [[0, 0], [0.3, 0], [0.3, 6], [0, 6]],
             {"from": {"point": [0, 0]}, "to": {"point": [0, 6]}, "inward": [1.0, 0.0]}, 0.3),
            # Drawn clockwise, the same block keeps the line on its longest side: the same line.
            ("drawn clockwise", [[0, 0], [0, 0.3], [6, 0.3], [6, 0]],
             {"from": {"point": [0, 0.3]}, "to": {"point": [6, 0.3]}, "inward": [0.0, -1.0]}, 0.3),
            ("a square: its first side", [[1, 1], [3, 1], [3, 3], [1, 3]],
             {"from": {"point": [1, 3]}, "to": {"point": [3, 3]}, "inward": [0.0, -1.0]}, 2),
        ):
            with self.subTest(label):
                fields = wall_fields_from_block(_block(profile), "block-1-body")
                self.assertEqual(fields["producer"], "wall")
                self.assertEqual(fields["references"]["line"], line)
                self.assertEqual(fields["params"], {"height": 3.0, "thickness": thickness, "openings": []})
        turned = wall_fields_from_block(_block(_rectangle(30.0)), "block-1-body")
        inward = turned["references"]["line"]["inward"]
        self.assertAlmostEqual(math.hypot(*inward), 1.0, places=9)
        self.assertAlmostEqual(inward[0], math.sin(math.radians(30.0)), places=9)
        self.assertAlmostEqual(inward[1], -math.cos(math.radians(30.0)), places=9)
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
            "line": {"from": {"point": [0, 0.2]}, "to": {"point": [4, 0.2]}, "inward": [0.0, -1.0]}})
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
        self.assertEqual(wall_along_line(converted["references"]["line"]), ((0.0, 0.3), (6.0, 0.3)))

    def test_along_starts_at_the_same_corner_whichever_way_the_outline_winds(self) -> None:
        for degrees in (0.0, 30.0, 90.0, 217.5):
            with self.subTest(degrees=degrees):
                lines = [wall_fields_from_block(_block(_rectangle(degrees, clockwise=clockwise)), "block-1-body")
                         ["references"]["line"] for clockwise in (False, True)]
                (start, end), (cw_start, cw_end) = (wall_along_line(line) for line in lines)
                corner = _rectangle(degrees)[3]   # beside the first corner, across the thickness
                for got, want in ((start, corner), (cw_start, corner)):
                    self.assertAlmostEqual(got[0], want[0], places=9)
                    self.assertAlmostEqual(got[1], want[1], places=9)
                self.assertAlmostEqual(math.dist(start, end), LONG, places=9)
                self.assertAlmostEqual(math.dist(cw_start, cw_end), LONG, places=9)

    def test_a_line_that_is_not_two_stated_points_has_no_stated_along(self) -> None:
        for line in ({"from": {"grid": ["1", "W"]}, "to": {"grid": ["2", "W"]}},
                     {"from": {"point": ["@x", 0]}, "to": {"point": [6, 0]}},
                     {"from": {"point": [1, 1]}, "to": {"point": [1, 1]}}):
            with self.subTest(line=line), self.assertRaises(ElementProducerError):
                wall_along_line(line)


class ScriptOnARealisedBlockTests(unittest.TestCase):
    """A construction script still stands shapes on the block after it is realised as a wall, moved or not."""

    def test_top_of_the_realised_block_is_a_base_before_and_after_a_move(self) -> None:
        from monkeyarch.authoring.construction.lowering import compile_construction_script
        from tests.integration.test_construction_lowering import _apply, _compile, _record

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


class RoundedTurnTests(unittest.TestCase):
    """A turned block's corners are rounded to the nanometre the record keeps; it is still a rectangle."""

    def test_rounded_turned_rectangles_are_all_realised(self) -> None:
        for degrees in range(0, 360, 5):
            for long, deep in ((6.0, 0.3), (4.2, 0.25), (0.9, 0.12)):
                for clockwise in (False, True):
                    profile = [[round(x, 9), round(z, 9)] for x, z in
                               _rectangle(degrees + 0.3, clockwise=clockwise, at=(12.345678901, -7.654321), long=long, deep=deep)]
                    with self.subTest(degrees=degrees, long=long, clockwise=clockwise):
                        fields = wall_fields_from_block(_block(profile), "block-1-body")
                        self.assertAlmostEqual(fields["params"]["thickness"], deep, delta=2e-9)
                        start, end = wall_along_line(fields["references"]["line"])
                        self.assertAlmostEqual(math.dist(start, end), long, delta=2e-9)

    def test_a_rounded_turned_block_is_the_same_solid_to_the_nanometre(self) -> None:
        for degrees in (0.7, 17.0, 33.3, 71.9, 123.4, 211.1, 299.99):
            for clockwise in (False, True):
                with self.subTest(degrees=degrees, clockwise=clockwise):
                    block = _block([[round(x, 9), round(z, 9)] for x, z in _rectangle(degrees, clockwise=clockwise)])
                    before = _compiled(self, _row("block-1-body", block))["obj-block-1-body"]
                    after = _compiled(self, _converted("block-1-body", block))["obj-block-1-body"]
                    for corner in ("bbox_min", "bbox_max"):
                        for got, want in zip(after[corner], before[corner]):
                            self.assertAlmostEqual(got, want, delta=2e-9)

    def test_a_block_turned_by_a_script_is_realised(self) -> None:
        from tests.integration.test_construction_lowering import _apply, _compile, _record

        record = _apply(_record(), _compile("block = extrude(rect(0, 0, 6, 0.3), 3)\nrotate(block, 33.3)"))
        block = next(entity for entity in record.entities if entity.entity_id == "block-body")
        fields = wall_fields_from_block(block.fields, "block-body")
        self.assertAlmostEqual(fields["params"]["thickness"], 0.3, delta=2e-9)
        start, _ = wall_along_line(fields["references"]["line"])
        corner = block.fields["params"]["profile"][3]
        self.assertAlmostEqual(math.dist(start, corner), 0.0, delta=2e-9)


DOOR_TYPE = {"schema": "DoorType@1", "type_id": "opening-1-type", "frame_width": 0.06, "frame_depth": 0.12,
             "frame_projection": 0.0, "leaf_thickness": 0.04, "leaf_offset": 0.04, "leaf_count": 1, "leaf_gap": 0.0,
             "clearance_bottom": 0.01, "clearance_top": 0.005}
DOOR = {"opening_id": "opening-1", "kind": "door", "along": 1.5, "width": 0.9, "sill": 0, "head": 2.1,
        "type_id": "opening-1-type"}


class HostedDoorTests(unittest.TestCase):
    """A door in a realised block: the interface it cites is the one it names, and the model keeps the block's bounds."""

    def test_a_filled_opening_cites_only_the_interface_it_names(self) -> None:
        wall = _converted("block-1-body", _block([[0, 0], [6, 0], [6, 0.3], [0, 0.3]]))
        for opening, cited in ((DOOR, ()), ({**DOOR, "interface_ref": "relation:outside-to-room"},
                                             ("relation:outside-to-room",))):
            with self.subTest(cited=cited):
                door = replace(wall, params={**wall.params, "openings": [opening], "types": [DOOR_TYPE]})
                produced, _ = _produced(door)
                [assembly] = produced["block-1-body"].assemblies
                self.assertEqual(assembly.interface_refs, cited)
                self.assertLessEqual({"door-frame-block-1-body-opening-1-left", "door-leaf-block-1-body-opening-1-0"},
                                     {op.op_id for op in produced["block-1-body"].operations})

    def test_the_model_view_keeps_the_bounds_of_a_block_with_a_door(self) -> None:
        from monkeyarch.authoring.construction.lowering import geometry_view
        from tests.integration.test_construction_lowering import _apply, _compile, _record

        record = _apply(_record(), _compile("block = extrude(rect(0, 0, 6, 0.3), 3)"))
        block = next(entity for entity in record.entities if entity.entity_id == "block-body")
        fields = wall_fields_from_block(block.fields, "block-body")
        fields["params"] = {**fields["params"], "openings": [DOOR], "types": [DOOR_TYPE]}
        record = replace(record, entities=tuple(replace(block, fields=fields) if entity is block else entity
                                                for entity in record.entities))
        validate_element_contract(record, ("block-body",))
        view = {row["id"]: row for row in geometry_view(record)}
        self.assertEqual(view["block"]["bounds"], [[0.0, 0.0, 0.0], [6.0, 3.0, 0.3]])


class OpeningInterfaceTests(ProducerFixture):
    """An assembly cites only the interfaces its record states (R17): none is invented, an undeclared one is refused."""

    async def _produced_with(self, interface_refs: tuple[str, ...]):
        from monkeyarch.application.geometry_proposal import proposal_authoring_output
        from tests.integration.support import ScriptedProvider

        [assembly] = self.proposal.assemblies
        proposal = replace(self.proposal, assemblies=(replace(assembly, interface_refs=interface_refs),))
        result = await self.produce(ScriptedProvider((proposal_authoring_output(proposal),)))
        issues = [(issue["code"], issue["detail"]) for ref in result.round_refs
                  for issue in self.repository.load_json(ref).get("issues", [])]
        return result.status, issues

    async def test_an_assembly_that_names_no_interface_is_delivered(self) -> None:
        status, issues = await self._produced_with(())
        self.assertIs(status, GeometryProposalStatus.ACCEPTED, issues)

    async def test_a_declared_interface_is_delivered(self) -> None:
        from tests.integration.support import INTERFACE_REF

        status, issues = await self._produced_with((INTERFACE_REF,))
        self.assertIs(status, GeometryProposalStatus.ACCEPTED, issues)

    async def test_an_undeclared_interface_is_refused(self) -> None:
        status, issues = await self._produced_with(("relation:somewhere-else",))
        self.assertIsNot(status, GeometryProposalStatus.ACCEPTED)
        self.assertIn(("malformed_model_output", "assembly interface_refs are absent from the supplied spatial option; "
                                                 "unavailable=['relation:somewhere-else']"), issues)

    async def test_the_solvers_former_default_is_undeclared_too(self) -> None:
        status, issues = await self._produced_with(("interface:inside-to-outside",))
        self.assertIsNot(status, GeometryProposalStatus.ACCEPTED)
        self.assertIn(("malformed_model_output", "assembly interface_refs are absent from the supplied spatial option; "
                                                 "unavailable=['interface:inside-to-outside']"), issues)


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
