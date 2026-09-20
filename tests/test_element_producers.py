"""P100 / P102: reference-reading producers — the portico vertical slice.

Column → capital → entablature → pediment all evaluate from shared datums:
the column array publishes its top, the capitals bind it and publish
theirs, the entablature binds the capital top, the pediment binds the
entablature top. No operation carries an offset unless the row declares an
engagement with a depth.
"""
from __future__ import annotations

import json
import math
import unittest
from dataclasses import replace

from archflow.adapters.cad_program import expected_object_bounds, expected_object_semantics
from monkeyarch.capabilities.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    element_rows_of,
    edit_drawn_element,
    produce_rows,
    production_order,
    producer_signatures,
    validate_element_contract,
)
from monkeyarch.capabilities.geometry_proposal import GeometryProposalStatus
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeyarch.compilers.geometry import compile_geometry_program
from archflow.state.geometry_program import GeometryOperationKind, ProjectGridAxis, ProjectGrids, ProjectLevel, ProjectLevels, SemanticBinding
from archflow.state.state_record import project_grids_of, project_levels_of
from tests.support import ProducerFixture, authored_record
from tests.test_geometry_compiler import COMMITMENT, _only, _proposal, _state

BASIS = ("reading:plate",)
PN = "level-piano-nobile"


def _grids() -> ProjectGrids:
    axes = [ProjectGridAxis(f"axis-{k + 1}", str(k + 1), ((k - 2.5) * 1.6065, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS) for k in range(6)]
    axes.append(ProjectGridAxis("axis-w", "W", (0.0, 0.0, -13.85), (1.0, 0.0, 0.0), BASIS))       # the west facade line
    axes.append(ProjectGridAxis("axis-ox", "OX", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), BASIS))        # three lines through the origin, for
    axes.append(ProjectGridAxis("axis-oz", "OZ", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS))        # reading a stated direction off a
    axes.append(ProjectGridAxis("axis-od", "OD", (0.0, 0.0, 0.0), (1.0, 0.0, 1.0), BASIS))        # run whose plan coordinates are plain
    return ProjectGrids(project_id="demo", published_by="seat-coordination", axes=tuple(sorted(axes, key=lambda a: a.axis_id)))


def _on(axis: str, along: float) -> dict:
    """A plan point at ``along`` metres from an axis's origin, along that axis."""

    return {"axis_point": {"axis": axis, "along": along}}


def _levels(piano: float = 3.57) -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS), ProjectLevel(PN, "piano-nobile", piano, BASIS)))


def _rows(column_height: float = 6.426, engagement: float | None = None) -> tuple[ElementRow, ...]:
    capital_params = {"height": 0.18, "half_extent": 0.56}
    if engagement is not None:
        capital_params["engagement"] = {"depth": engagement}
    return (
        ElementRow("columns-west", "portico-columns", "column-array", {"axes": ["1", "2", "3", "4", "5", "6"], "facade": "W", "base": {"level": PN}},
                   {"radius": 0.357, "height": column_height, "segments": 24}, BASIS),
        ElementRow("capitals-west", "portico-capitals", "capitals", {"axes": ["1", "2", "3", "4", "5", "6"], "facade": "W", "columns": "columns-west", "base": {"datum": "columns-west-top"}},
                   capital_params, BASIS),
        ElementRow("entablature-west", "portico-entablature", "beam", {"from": {"grid": ["1", "W"]}, "to": {"grid": ["6", "W"]}, "base": {"datum": "capitals-west-top"}, "support": "capitals-west"},
                   {"depth": 0.92, "height": 1.33875, "end_overhang": 0.36}, BASIS),
        ElementRow("pediment-west", "portico-pediments", "pediment", {"from": {"grid": ["1", "W"]}, "to": {"grid": ["6", "W"]}, "base": {"datum": "entablature-west-top"}, "support": "entablature-west"},
                   {"rise": 1.78, "thickness": 0.3}, BASIS),
    )


def _produce(rows, levels=None):
    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=levels or _levels()), published={})
    return produce_rows(rows, context), context


def _op_params(operation) -> dict:
    return {p.name: json.loads(p.value_json) for p in operation.parameters}


def _assert_bbox(case, points, expected) -> None:
    """The produced profile points against hand-computed extremes, per axis (X, Y, Z)."""

    for axis, (low, high) in enumerate(expected):
        case.assertAlmostEqual(min(p[axis] for p in points), low, places=6)
        case.assertAlmostEqual(max(p[axis] for p in points), high, places=6)


def _radius(point, centre=(0.0, -13.85)) -> float:
    return math.hypot(point[0] - centre[0], point[2] - centre[1])


class VerticalSliceTests(unittest.TestCase):
    def test_every_interface_is_one_published_datum(self) -> None:
        produced, context = _produce(_rows())
        columns, capitals, entablature, pediment = produced
        self.assertEqual(columns.datums[0].datum_id, "columns-west-top")
        self.assertEqual(capitals.datums[0].datum_id, "capitals-west-top")
        self.assertEqual(entablature.datums[0].datum_id, "entablature-west-top")
        self.assertTrue(all(b.datum_id == PN for b in columns.bindings))
        self.assertTrue(all(b.datum_id == "columns-west-top" for b in capitals.bindings))
        self.assertEqual(entablature.bindings[0].datum_id, "capitals-west-top")
        self.assertEqual(pediment.bindings[0].datum_id, "entablature-west-top")
        self.assertAlmostEqual(context.datum_value("columns-west-top"), 3.57 + 6.426)
        self.assertAlmostEqual(context.datum_value("capitals-west-top"), 3.57 + 6.426 + 0.18)
        self.assertAlmostEqual(context.datum_value("entablature-west-top"), 3.57 + 6.426 + 0.18 + 1.33875)
        # no unexplained epsilon: no operation carries a base_offset without a declared engagement
        for element in produced:
            for op in element.operations:
                self.assertNotIn("base_offset", {p.name for p in op.parameters}, op.op_id)
        # relations are produced by construction, with the datum they hold
        kinds = [(r.kind, r.subject, r.object, r.datum_id) for e in produced for r in e.relations]
        self.assertIn(("support", "columns-west", "capitals-west", "columns-west-top"), kinds)
        self.assertIn(("support", "capitals-west", "entablature-west", "capitals-west-top"), kinds)
        self.assertIn(("support", "entablature-west", "pediment-west", "entablature-west-top"), kinds)

    def test_a_declared_engagement_is_the_only_way_to_embed(self) -> None:
        produced, _ = _produce(_rows(engagement=0.02))
        capitals = produced[1]
        params = {p.name: json.loads(p.value_json) for p in capitals.operations[0].parameters}
        self.assertAlmostEqual(params["base_offset"], -0.02)                     # embed by exactly the declared depth
        self.assertAlmostEqual(params["vector"][1], 0.18 + 0.02)
        self.assertEqual(capitals.relations[0].parameters, {"engagement_depth": 0.02})
        with self.assertRaises(ElementProducerError):
            _produce((replace(_rows()[1], params={"height": 0.18, "half_extent": 0.56, "engagement": {"why": "it touched"}}),))

    def test_supports_must_come_first(self) -> None:
        rows = _rows()
        with self.assertRaises(ElementProducerError):
            _produce((rows[1],))                                                 # capitals before their columns published a top

    def test_slice_compiles_and_a_column_height_change_moves_the_whole_chain(self) -> None:
        def compile_slice(height: float):
            # the compiler fixture knows one component ("building") and one frame ("world"); the slice binds to them
            context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
            produced = produce_rows(_rows(column_height=height), context)
            operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for e in produced for op in e.operations)
            bindings = tuple(b for e in produced for b in e.bindings)
            datums = tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id))
            state = _state()
            proposal = _only(_proposal(state, extra_operations=operations), operations, ())
            result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,), interface_datums=datums, datum_bindings=bindings)
            self.assertIsNotNone(result.program, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
            return expected_object_bounds(result.program)

        a = compile_slice(6.426)
        self.assertAlmostEqual(a["obj-columns-west-0"]["bbox_max"][1], a["obj-capitals-west-0"]["bbox_min"][1])          # contact by construction
        self.assertAlmostEqual(a["obj-capitals-west-0"]["bbox_max"][1], a["obj-entablature-west"]["bbox_min"][1])
        self.assertAlmostEqual(a["obj-entablature-west"]["bbox_max"][1], a["obj-pediment-west"]["bbox_min"][1])
        self.assertAlmostEqual((a["obj-columns-west-0"]["bbox_min"][0] + a["obj-columns-west-0"]["bbox_max"][0]) / 2, -2.5 * 1.6065)   # on axis 1
        b = compile_slice(7.0)
        for oid in ("obj-capitals-west-0", "obj-entablature-west", "obj-pediment-west"):
            self.assertAlmostEqual(b[oid]["bbox_min"][1] - a[oid]["bbox_min"][1], 7.0 - 6.426)                     # the chain moved as one


WINDOW_TYPE = {"schema": "WindowType@1", "type_id": "window-type-1", "frame_width": 0.09, "frame_depth": 0.18,
               "frame_projection": 0.1, "glazing_thickness": 0.025, "glazing_offset": 0.01}


def _wall_row(element_id: str, axis_from: str, axis_to: str, opening_id: str = "window") -> ElementRow:
    """A wall on the west facade line carrying one typed opening of its own."""

    return ElementRow(
        element_id, "envelope", "wall",
        {"base": {"level": PN}, "line": {"from": {"grid": [axis_from, "W"]}, "to": {"grid": [axis_to, "W"]}}},
        {"thickness": 0.3, "height": 3.0, "types": [WINDOW_TYPE],
         "openings": [{"opening_id": opening_id, "kind": "window", "along": 0.8, "width": 1.2,
                       "sill": 0.9, "head": 2.4, "type_id": "window-type-1"}]},
        BASIS,
    )


class SemanticWallContractTests(unittest.TestCase):
    def test_an_authored_opening_ending_at_the_wall_end_is_hosted(self) -> None:
        # the interior finish wall as authored: length 2.8199999999999994 resolved from its axis
        # points, the window's along 1.4849999999999985 rounded by the producer to 1.485
        row = ElementRow("finish-wall", "finish", "wall",
                         {"base": {"level": "level-ground"}, "line": {"from": _on("OZ", 0.0), "to": _on("OZ", 2.8199999999999994)}},
                         {"thickness": 0.02, "height": 3.0,
                          "openings": [{"opening_id": "window", "kind": "window", "along": 1.4849999999999985, "width": 2.67, "sill": 0.19, "head": 2.69}]},
                         BASIS)
        produced, _ = _produce((row,))
        void = next(op for op in produced[0].operations if op.op_id == "finish-wall-void-window")
        self.assertEqual({round(p[2], 9) for p in _op_params(void)["profile"]}, {0.15, 2.82})
        with self.assertRaisesRegex(ElementProducerError, "lies outside the wall length"):
            _produce((replace(row, params={**row.params, "openings": [{**row.params["openings"][0], "along": 1.485 + 1e-8}]}),))

    def test_a_wall_ends_at_the_referenced_slab_underside(self) -> None:
        original = _wall_row("support-wall", "1", "2")
        row = replace(original, references={**original.references, "base": {"level": "level-ground"},
                                           "top": {"offset_from": {"level": PN, "offset": -0.24}}},
                      params={"thickness": 0.3})
        produced, _ = _produce((row,))
        body = next(op for op in produced[0].operations if op.op_id == row.element_id)
        self.assertAlmostEqual(_op_params(body)["vector"][1], 3.33)
        with self.assertRaisesRegex(ElementProducerError, "conflicts with the top reference"):
            _produce((replace(row, params={"thickness": 0.3, "height": 3.57}),))

    def test_a_type_height_cannot_override_an_instances_top_reference(self) -> None:
        record = authored_record()
        wall = next(e for e in record.entities if e.entity_id == "wall-south")
        family = replace(wall, entity_id="wall-family", schema="Type@1", parent_id=None,
                         fields={"producer": "wall", "params": {"height": 2.97}})
        fields = dict(wall.fields)
        fields["type_ref"] = family.entity_id
        fields["params"] = {k: v for k, v in fields["params"].items() if k != "height"}
        fields["references"] = {**fields["references"], "top": {"offset_from": {"level": PN, "offset": -0.24}}}
        candidate = replace(record, entities=tuple(replace(e, fields=fields) if e.entity_id == wall.entity_id else e
                                                   for e in record.entities) + (family,))
        with self.assertRaisesRegex(ElementProducerError, "conflicts with the top reference"):
            validate_element_contract(candidate, (wall.entity_id,))

    def test_the_advertised_arch_is_the_wall_solvers_actual_cut(self) -> None:
        record = authored_record()
        wall = next(e for e in record.entities if e.entity_id == "wall-south")
        opening = {"opening_id": "passage", "kind": "door", "shape": "semicircular_arch", "along": 3.0,
                   "width": 1.2, "sill": 0.0, "spring_height": 1.2, "head": 1.8}
        fields = {**wall.fields, "params": {**wall.fields["params"], "openings": [opening]}}
        candidate = replace(record, entities=tuple(replace(e, fields=fields) if e.entity_id == wall.entity_id else e
                                                   for e in record.entities))
        validate_element_contract(candidate, (wall.entity_id,))
        self.assertIn("semicircular_arch", producer_signatures()["wall"]["parameters"]["properties"]["openings"]["items"]["properties"]["shape"]["enum"])
        broken = {**fields, "params": {**fields["params"], "openings": [{**opening, "spring_height": 1.0}]}}
        with self.assertRaises(ElementProducerError):
            validate_element_contract(replace(candidate, entities=tuple(replace(e, fields=broken) if e.entity_id == wall.entity_id else e
                                                                         for e in candidate.entities)), (wall.entity_id,))

    def test_unadvertised_nested_fields_are_refused_before_production(self) -> None:
        record = authored_record()
        wall = next(e for e in record.entities if e.entity_id == "wall-south")
        opening = {**wall.fields["params"]["openings"][0], "radius_override": 8.0}
        fields = {**wall.fields, "params": {**wall.fields["params"], "openings": [opening]}}
        candidate = replace(record, entities=tuple(replace(e, fields=fields) if e.entity_id == wall.entity_id else e
                                                   for e in record.entities))
        with self.assertRaisesRegex(ElementProducerError, "radius_override"):
            validate_element_contract(candidate, (wall.entity_id,))


class PlanarSurfaceProducerTests(unittest.TestCase):
    def test_a_bound_elevation_edit_moves_the_surface_and_keeps_the_base(self) -> None:
        from archflow.state.state_record import Parameter, StateRecordEditKind, StateRecordOperator, apply_state_record_operator
        from tests.support import shared_bound_state

        record = authored_record()
        surface = replace(next(e for e in record.entities if e.entity_id == "wall-south"), fields={
            "producer": "planar-surface", "component_id": "envelope",
            "references": {"base": {"offset_from": {"level": PN, "offset": -0.1}}},
            "params": {"elevation": "@ceiling_height", "profile": [[0, 0], [2, 0], [2, 3], [0, 0]]},
        })
        record = replace(record, entities=tuple(surface if e.entity_id == surface.entity_id else e for e in record.entities),
                         parameters=record.parameters + (Parameter("ceiling_height", 2.7, "m"),)).bound_to(shared_bound_state()[1])
        changed = apply_state_record_operator(record, StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
            target_ref="parameter:ceiling_height", key="ceiling_height", value=3.2,
        ))
        elevations = []
        for value in (record, changed):
            validate_element_contract(value, (surface.entity_id,))
            row = next(row for row in element_rows_of(value) if row.element_id == surface.entity_id)
            produced, context = _produce((row,))
            element = produced[0]
            self.assertEqual(row.references, surface.fields["references"])
            self.assertEqual(element.bindings[0].datum_id, PN)
            self.assertEqual((element.datums, element.relations, context.published), ((), (), {}))
            elevations.append(_op_params(element.operations[0])["base_offset"])
        self.assertAlmostEqual(elevations[1] - elevations[0], 0.5)

    def test_missing_closure_and_unimplemented_thickness_are_not_repaired(self) -> None:
        row = ElementRow("surface", "envelope", "planar-surface", {"base": {"level": PN}},
                         {"profile": [[0, 0], [2, 0], [2, 3], [0, 0]]}, BASIS)
        for params, message in (({"profile": row.params["profile"][:-1]}, "explicitly close"),
                                ({**row.params, "thickness": 0.1}, "does not support")):
            with self.subTest(message=message), self.assertRaisesRegex(ElementProducerError, message):
                _produce((replace(row, params=params),))


class CurveProducerTests(unittest.TestCase):
    def test_line_polyline_and_sampled_arc_keep_their_authored_points_and_datum(self):
        arc = [[math.cos(t * math.pi / 8), math.sin(t * math.pi / 8)] for t in range(5)]
        for profile in ([[0, 0], [3, 4]], [[0, 0], [2, 0], [2, 3]], arc):
            with self.subTest(profile=profile):
                row = ElementRow("path", "envelope", "curve", {"base": {"level": PN}}, {"profile": profile}, BASIS)
                produced, context = _produce((row,))
                element = produced[0]
                op = element.operations[0]
                self.assertIs(op.kind, GeometryOperationKind.CURVE)
                self.assertEqual(op.output_object_ids, ("obj-path",))
                self.assertEqual(_op_params(op), {"basis": "polyline", "points": [[round(x, 9), 0, round(y, 9)] for x, y in profile], "retain_for_inspection": True})
                self.assertEqual(element.bindings[0].datum_id, PN)
                self.assertEqual((element.datums, element.relations, context.published), ((), (), {}))

    def test_plane_origin_and_negative_coordinate_follow_the_bound_level_plus_offsets(self):
        from archflow.adapters.cad_program import lift_to_base_level
        plane = {"origin": [10, 4, 20], "xAxis": [1, 0, 0], "yAxis": [0, -1, 0], "normal": [0, 0, 1]}
        row = ElementRow("path", "envelope", "curve", {"base": {"offset_from": {"level": PN, "offset": 0.2}}},
                         {"profile": [[0, 0], [3, 2]], "work_plane": plane, "elevation": -0.5}, BASIS)
        produced, _ = _produce((row,))
        params = _op_params(produced[0].operations[0])
        self.assertNotIn("base_level", params)
        points = lift_to_base_level(params["points"], {**params, "base_level": 3.57}, "path")
        self.assertEqual([(round(x, 6), round(y, 6), round(z, 6)) for x, y, z in points], [(10, 7.27, 20), (13, 5.27, 20)])

    def test_semantic_curve_resolves_parameter_coordinates_and_refuses_thickness_or_bad_points(self):
        from archflow.state.state_record import Parameter
        record = authored_record()
        original = next(e for e in record.entities if e.entity_id == "wall-south")
        curve = replace(original, fields={"producer": "curve", "component_id": "envelope",
                        "references": {"base": {"level": PN}}, "params": {"profile": [[0, 0], ["@path_x", 2]]}})
        record = replace(record, entities=tuple(curve if e.entity_id == curve.entity_id else e for e in record.entities),
                         parameters=record.parameters + (Parameter("path_x", 3, "m"),))
        validate_element_contract(record, (curve.entity_id,))
        row = next(row for row in element_rows_of(record) if row.element_id == curve.entity_id)
        self.assertEqual(row.params["profile"], [[0, 0], [3, 2]])
        for params in ({"profile": [[0, 0]]}, {"profile": [[0, 0], [0, 0]]},
                       {"profile": [[0, 0], [float("nan"), 1]]}, {**row.params, "height": 1}):
            with self.subTest(params=params), self.assertRaises(ElementProducerError):
                _produce((replace(row, params=params),))


class DrawingPlaneTests(unittest.TestCase):
    def row(self, **params):
        return ElementRow("drawn", "envelope", "prism", {"base": {"level": PN}},
                          {"profile": [[0, 0], [3, 0], [3, 2], [0, 2]], "height": 1.5, **params}, BASIS)

    def test_vertical_plane_preserves_datum_relative_origin_and_normal(self):
        plane = {"origin": [10, 4, 20], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0], "normal": [0, 0, 1]}
        produced, context = _produce((self.row(work_plane=plane),))
        params = _op_params(produced[0].operations[0])
        self.assertEqual(params["vector"], [0, 0, 1.5])
        self.assertEqual(params["base_offset"], 4)
        _assert_bbox(self, params["profile"], ((10, 13), (4, 6), (20, 20)))
        self.assertEqual(produced[0].bindings[0].datum_id, PN)
        self.assertEqual((produced[0].datums, produced[0].relations), ((), ()))
        self.assertNotIn("drawn-top", context.published)

    def test_negative_vertical_profile_coordinate_is_not_lost_during_datum_lift(self):
        from archflow.adapters.cad_program import lift_to_base_level
        plane = {"origin": [10, 4, 20], "xAxis": [1, 0, 0], "yAxis": [0, -1, 0], "normal": [0, 0, 1]}
        produced, _ = _produce((self.row(work_plane=plane),))
        params = _op_params(produced[0].operations[0])
        params["base_level"] = 3.57
        points = lift_to_base_level(params["profile"], params, "drawn")
        _assert_bbox(self, points, ((10, 13), (5.57, 7.57), (20, 20)))

    def test_left_handed_frame_is_valid_but_skewed_frame_is_refused(self):
        plane = {"origin": [0, 0, 0], "xAxis": [1, 0, 0], "yAxis": [0, 0, 1], "normal": [0, 1, 0]}
        self.assertTrue(_produce((self.row(work_plane=plane),))[0][0].operations)
        with self.assertRaisesRegex(ElementProducerError, "perpendicular"):
            _produce((self.row(work_plane={**plane, "normal": [1, 0, 0]}),))

    def edit(self, row=None, **action):
        row = row or self.row()
        context = ProductionContext(ReferenceContext(grids=_grids(), levels=_levels()), {})
        produce_rows((row,), context)
        return edit_drawn_element(row, context, **action)

    def test_move_rotate_scale_remain_editable_profile_and_height(self):
        moved = self.edit(kind="move", translation=[2, 3, 4])
        for actual, expected in zip(moved.params["work_plane"]["origin"], [2, 3, 4]):
            self.assertAlmostEqual(actual, expected)
        rotated = self.edit(moved, kind="rotate", axis=[0, 1, 0], angle_degrees=90, origin=[0, 0, 0])
        self.assertAlmostEqual(rotated.params["work_plane"]["origin"][0], 4)
        self.assertAlmostEqual(rotated.params["work_plane"]["origin"][2], -2)
        scaled = self.edit(rotated, kind="scale", scale=[2, 2, 2])
        self.assertEqual(scaled.params["profile"][2], [6, 4])
        self.assertEqual(scaled.params["height"], 3)
        produced, _ = _produce((scaled,))
        self.assertTrue(produced[0].operations)

    def test_nonuniform_scale_and_mirror_preserve_rotated_profile_geometry(self):
        rotated = self.edit(kind="rotate", axis=[0, 1, 0], angle_degrees=30)
        before, _ = _produce((rotated,))
        original = _op_params(before[0].operations[0])
        for factors in ([2, 1, 1], [-1, 0.5, 1.5]):
            with self.subTest(factors=factors):
                scaled = self.edit(rotated, kind="scale", scale=factors, origin=[0, 3.57, 0])
                produced, _ = _produce((scaled,))
                actual = _op_params(produced[0].operations[0])
                for source, target in zip(original["profile"], actual["profile"]):
                    for coordinate, expected, factor in zip(target, source, factors):
                        self.assertAlmostEqual(coordinate, expected * factor)
                for coordinate, expected, factor in zip(actual["vector"], original["vector"], factors):
                    self.assertAlmostEqual(coordinate, expected * factor)
                self.assertTrue(self.edit(scaled, kind="push_pull", distance=0.5).params["height"] > scaled.params["height"])

    def test_nonuniform_scaling_refuses_only_an_oblique_extrusion(self):
        rotated = self.edit(kind="rotate", axis=[1, 0, 0], angle_degrees=45)
        with self.assertRaisesRegex(ElementProducerError, "oblique"):
            self.edit(rotated, kind="scale", scale=[1, 2, 1])

    def test_tilted_planar_face_can_be_scaled_then_pulled(self):
        face = self.edit(kind="push_pull", distance=-1.5)
        rotated = self.edit(face, kind="rotate", axis=[1, 0, 0], angle_degrees=45)
        scaled = self.edit(rotated, kind="scale", scale=[1, -2, 1])
        produced, _ = _produce((scaled,))
        self.assertTrue(produced[0].operations)
        pulled = self.edit(scaled, kind="push_pull", distance=1)
        self.assertEqual(pulled.producer, "prism")
        self.assertTrue(_produce((pulled,))[0][0].operations)

    def test_mirror_keeps_positive_dimensions_and_reverses_explicit_axes(self):
        mirrored = self.edit(kind="scale", scale=[-1, -2, 1], origin=[0, 0, 0])
        self.assertEqual(mirrored.params["height"], 3)
        self.assertEqual(mirrored.params["profile"], self.row().params["profile"])
        self.assertEqual(mirrored.params["work_plane"]["xAxis"], [-1, 0, 0])
        self.assertEqual(mirrored.params["work_plane"]["normal"], [0, -1, 0])
        produced, _ = _produce((mirrored,))
        self.assertEqual(_op_params(produced[0].operations[0])["vector"], [0, -3, 0])

    def test_push_pull_bottom_keeps_the_opposite_face_fixed(self):
        changed = self.edit(kind="push_pull", distance=0.5, normal=[0, -1, 0])
        produced, context = _produce((changed,))
        self.assertEqual(changed.params["height"], 2)
        self.assertEqual(changed.params["work_plane"]["origin"], [0, -0.5, 0])
        self.assertAlmostEqual(context.datum_value("drawn-top"), 3.57 + 1.5)

    def test_push_pull_to_face_and_back_has_no_invented_thickness(self):
        surface = self.edit(kind="push_pull", distance=-1.5)
        self.assertEqual(surface.producer, "planar-surface")
        self.assertNotIn("height", surface.params)
        self.assertEqual(surface.params["profile"][0], surface.params["profile"][-1])
        prism = self.edit(surface, kind="push_pull", distance=-2)
        self.assertEqual(prism.producer, "prism")
        self.assertEqual(prism.params["height"], 2)
        self.assertEqual(prism.params["work_plane"]["normal"], [0, -1, 0])
        self.assertEqual(len(prism.params["profile"]), 4)

    def test_all_four_rectangle_sides_pull_without_changing_height(self):
        for normal, expected in (([1, 0, 0], ((0, 4), (0, 2))),
                                 ([-1, 0, 0], ((-1, 3), (0, 2))),
                                 ([0, 0, 1], ((0, 3), (0, 3))),
                                 ([0, 0, -1], ((0, 3), (-1, 2)))):
            with self.subTest(normal=normal):
                changed = self.edit(kind="push_pull", distance=1, normal=normal)
                points = changed.params["profile"]
                for axis, bounds in enumerate(expected):
                    self.assertEqual((min(p[axis] for p in points), max(p[axis] for p in points)), bounds)
                self.assertEqual(changed.params["height"], 1.5)

    def test_rotated_rectangle_side_pull_and_crossing_refusal(self):
        rotated = self.edit(kind="rotate", angle_degrees=90, axis=[0, 1, 0])
        changed = self.edit(rotated, kind="push_pull", distance=-1, normal=[0, 0, -1])
        self.assertAlmostEqual(max(point[0] for point in changed.params["profile"]), 2)
        with self.assertRaisesRegex(ElementProducerError, "collapse or cross"):
            self.edit(kind="push_pull", distance=-4, normal=[1, 0, 0])

    def test_side_pull_refuses_an_ambiguous_or_concave_profile(self):
        row = self.row(profile=[[0, 0], [3, 0], [3, 2], [1, 1], [0, 2]])
        with self.assertRaisesRegex(ElementProducerError, "convex profiles"):
            self.edit(row, kind="push_pull", distance=1, normal=[1, 0, 0])

    def test_triangle_side_pull_cannot_invert_after_crossing_its_opposite_vertex(self):
        vertices = [[0, 0], [4, 0], [0, 3]]
        for profile in (vertices, list(reversed(vertices))):
            row = self.row(profile=profile)
            for distance in (-0.5, 1):
                with self.subTest(profile=profile, distance=distance):
                    changed = self.edit(row, kind="push_pull", distance=distance, normal=[0.6, 0, 0.8])
                    scale = (2.4 + distance) / 2.4
                    for actual, original in zip(changed.params["profile"], profile):
                        for coordinate, source in zip(actual, original):
                            self.assertAlmostEqual(coordinate, source * scale)
                    self.assertEqual(changed.params["height"], row.params["height"])
            for distance in (-2.4, -3):
                with self.subTest(profile=profile, distance=distance):
                    with self.assertRaisesRegex(ElementProducerError, "collapse or cross"):
                        self.edit(row, kind="push_pull", distance=distance, normal=[0.6, 0, 0.8])


class PrismElevationTests(unittest.TestCase):
    def test_top_reference_accounts_for_both_base_offset_and_elevation(self) -> None:
        row = ElementRow("panel", "envelope", "prism",
                         {"base": {"offset_from": {"level": "level-ground", "offset": 0.2}}, "top": {"level": PN}},
                         {"profile": [[0, 0], [2, 0], [2, 3], [0, 3]], "elevation": 3.358, "height": 0.012}, BASIS)
        for params in (row.params, {k: v for k, v in row.params.items() if k != "height"}):
            produced, context = _produce((replace(row, params=params),))
            self.assertAlmostEqual(_op_params(produced[0].operations[0])["vector"][1], 0.012)
            self.assertAlmostEqual(context.datum_value("panel-top"), 3.57)
        with self.assertRaisesRegex(ElementProducerError, "conflicts with the top reference"):
            _produce((replace(row, params={**row.params, "elevation": 3.35}),))

    def test_parameter_edits_keep_panel_elevation_and_thickness_independent(self) -> None:
        from archflow.state.state_record import Parameter, StateRecordEditKind, StateRecordOperator, apply_state_record_operator
        from tests.support import shared_bound_state

        record = authored_record()
        panel = replace(next(e for e in record.entities if e.entity_id == "wall-south"), fields={
            "producer": "prism", "component_id": "envelope", "references": {"base": {"level": "level-ground"}},
            "params": {"elevation": "@ceiling_level", "height": "@board_thickness", "profile": [[0, 0], [2, 0], [2, 3], [0, 3]]},
        })
        record = replace(record, entities=tuple(panel if e.entity_id == panel.entity_id else e for e in record.entities),
                         parameters=record.parameters + (Parameter("ceiling_level", 2.9, "m"), Parameter("board_thickness", 0.012, "m"))).bound_to(shared_bound_state()[1])
        for key, value, expected_bottom, expected_thickness in (("ceiling_level", 3.0, 3.0, 0.012), ("board_thickness", 0.024, 2.9, 0.024)):
            with self.subTest(key=key):
                changed = apply_state_record_operator(record, StateRecordOperator(
                    kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                    target_ref=f"parameter:{key}", key=key, value=value,
                ))
                validate_element_contract(changed, (panel.entity_id,))
                row = next(row for row in element_rows_of(changed) if row.element_id == panel.entity_id)
                self.assertEqual(row.references, panel.fields["references"])
                produced, context = _produce((row,))
                params = _op_params(produced[0].operations[0])
                self.assertAlmostEqual(params["base_offset"], expected_bottom)
                self.assertAlmostEqual(params["vector"][1], expected_thickness)
                self.assertAlmostEqual(context.datum_value(f"{panel.entity_id}-top"), expected_bottom + expected_thickness)


class PlanarSurfaceProposalTests(ProducerFixture):
    async def test_surface_passes_the_real_proposal_contract_and_datum_compiler(self) -> None:
        from monkeyarch.capabilities.geometry_proposal import proposal_authoring_output
        from tests.support import ScriptedProvider

        context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
        row = ElementRow("surface", "building", "planar-surface", {"base": {"level": PN}},
                         {"elevation": -0.1, "profile": [[0, 0], [2, 0], [2, 3], [0, 0]]}, BASIS)
        produced = produce_rows((row,), context)
        binding = self.proposal.semantic_bindings[0]
        operations = tuple(replace(op, semantic_binding_ids=(binding.binding_id,)) for op in produced[0].operations)
        proposal = replace(self.proposal, operations=operations, assemblies=(),
                           semantic_bindings=(replace(binding, object_ids=("obj-surface",)),))
        result = await self.produce(ScriptedProvider((proposal_authoring_output(proposal),)),
                                    interface_datums=_levels().datums(), datum_bindings=produced[0].bindings)
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED, [
            (issue["code"], issue["detail"]) for ref in result.round_refs
            for issue in self.repository.load_json(ref).get("issues", [])
        ])
        bounds = expected_object_bounds(result.program)["obj-surface"]
        self.assertAlmostEqual(bounds["bbox_min"][1], 3.47)
        self.assertEqual(bounds["bbox_min"][1], bounds["bbox_max"][1])


class OpeningIdScopeTests(unittest.TestCase):
    """An opening id is unique inside its wall; the operation id says which wall."""

    def test_one_wall_names_its_opening_members_under_the_wall(self) -> None:
        produced, _ = _produce((_wall_row("wall-south", "1", "2"),))
        ops = {op.op_id for op in produced[0].operations}
        self.assertLessEqual(
            {"frame-wall-south-window-bottom", "frame-wall-south-window-left", "frame-wall-south-window-right",
             "frame-wall-south-window-top", "glazing-wall-south-window"}, ops)
        self.assertEqual([a.assembly_id for a in produced[0].assemblies], ["wall-south-window-assembly"])
        self.assertEqual(produced[0].assemblies[0].host_socket_id, "void-wall-south-window")
        self.assertEqual({op.output_object_ids[0] for op in produced[0].operations if op.op_id.startswith("glazing-")},
                         {"obj-glazing-wall-south-window"})
        # the wall's own operations are untouched by the scoping
        self.assertLessEqual({"wall-south", "wall-south-void-window", "wall-south-aperture-window", "wall-south-cut"}, ops)

    def test_three_walls_may_each_carry_an_opening_called_window(self) -> None:
        rows = (_wall_row("wall-south", "1", "2"), _wall_row("wall-middle", "3", "4"), _wall_row("wall-north", "5", "6"))
        produced, _ = _produce(rows)
        op_ids = [op.op_id for element in produced for op in element.operations]
        object_ids = [oid for element in produced for op in element.operations for oid in op.output_object_ids]
        self.assertEqual(len(op_ids), len(set(op_ids)))                          # what element_producers refused before
        self.assertEqual(len(object_ids), len(set(object_ids)))
        self.assertEqual(sorted(a.assembly_id for e in produced for a in e.assemblies),
                         ["wall-middle-window-assembly", "wall-north-window-assembly", "wall-south-window-assembly"])
        for wall in ("wall-south", "wall-middle", "wall-north"):
            self.assertIn(f"glazing-{wall}-window", op_ids)

    def test_a_wall_that_already_prefixes_its_opening_is_not_doubled(self) -> None:
        produced, _ = _produce((_wall_row("wall-south", "1", "2", opening_id="wall-south-window"),))
        op_ids = {op.op_id for op in produced[0].operations}
        self.assertIn("glazing-wall-south-window", op_ids)
        self.assertNotIn("glazing-wall-south-wall-south-window", op_ids)

class StairTests(unittest.TestCase):
    """A flight is measured by its two plan references: one stepped solid on the line, rises above the base, one top."""

    def _row(self, **params) -> ElementRow:
        p = {"count": 10, "rise": 0.18, "width": 1.2}
        p.update(params)
        return ElementRow("stair-north", "monument-stair", "stair",
                          {"from": {"axis_point": {"axis": "W", "along": 0.0}}, "to": {"axis_point": {"axis": "W", "along": 3.0}}, "base": {"level": PN}}, p, BASIS)

    def _compiled_bounds(self, row):
        context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
        (stair,) = produce_rows((row,), context)
        operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for op in stair.operations)
        datums = tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id))
        state = _state()
        result = compile_geometry_program(state, _only(_proposal(state, extra_operations=operations), operations, ()),
                                          active_commitment_refs=(COMMITMENT,), interface_datums=datums, datum_bindings=stair.bindings)
        self.assertIsNotNone(result.program, [(i.code.value, i.detail) for i in result.receipt.issues])
        return expected_object_bounds(result.program)

    def test_declared_top_matches_the_compiled_flight_including_base_offset(self) -> None:
        # Synthetic endpoint regression, not measured dimensions of the villa.
        row = self._row(count=19)
        for base, offset in (({"level": "level-ground"}, 0.0),
                             ({"offset_from": {"level": "level-ground", "offset": 0.18}}, 0.18),
                             ({"datum": "level-ground", "offset": 0.18}, 0.18)):
            with self.subTest(base=base):
                rise = (3.57 - offset) / 19
                flight = replace(row, references={**row.references, "base": base, "top": {"level": PN}},
                                 params={**row.params, "rise": rise})
                bounds = self._compiled_bounds(flight)
                self.assertNotIn("obj-stair-north-0", bounds)                                  # one whole flight, not a step per object
                self.assertAlmostEqual(bounds["obj-stair-north"]["bbox_min"][1], offset)
                self.assertAlmostEqual(bounds["obj-stair-north"]["bbox_max"][1], 3.57)
                self.assertEqual(flight.params["rise"], rise)

    def test_declared_top_refuses_a_rise_change_or_an_unaccounted_base_offset(self) -> None:
        row = self._row(count=19, rise=3.57 / 19)
        references = {**row.references, "base": {"level": "level-ground"}, "top": {"level": PN}}
        for flight in (replace(row, references=references, params={**row.params, "rise": row.params["rise"] * 1.1}),
                       replace(row, references={**references, "base": {"offset_from": {"level": "level-ground", "offset": 0.18}}})):
            with self.subTest(flight=flight):
                with self.assertRaisesRegex(ElementProducerError, "stair-north:.*declared top.*level-piano-nobile"):
                    _produce((flight,))

    def test_declared_top_resolves_offsets_and_published_datums(self) -> None:
        row = self._row(count=19, rise=3.57 / 19)
        for top in ({"offset_from": {"level": PN, "offset": 0.18}}, {"datum": PN, "offset": 0.18}):
            with self.subTest(top=top):
                flight = replace(row, references={**row.references, "base": {"level": "level-ground"}, "top": top},
                                 params={**row.params, "rise": (3.57 + 0.18) / 19})
                self.assertAlmostEqual(self._compiled_bounds(flight)["obj-stair-north"]["bbox_max"][1], 3.75)
        landing = ElementRow("landing", "monument-landing", "prism", {"base": {"level": "level-ground"}},
                             {"profile": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], "height": 3.57}, BASIS)
        flight = replace(row, references={**row.references, "base": {"level": "level-ground"}, "top": {"datum": "landing-top"}})
        produced, _ = _produce(production_order((flight, landing)))
        self.assertEqual([op.op_id for op in produced[1].operations], ["stair-north"])

    def test_a_tread_thickness_is_refused_because_separate_treads_are_not_one_solid(self) -> None:
        row = self._row(count=19, rise=3.57 / 19)
        row = replace(row, references={**row.references, "base": {"level": "level-ground"}})
        for thickness in (0.05, 0.2):
            with self.subTest(thickness=thickness):
                with self.assertRaisesRegex(ElementProducerError, "stair-north: tread thickness.*not one closed solid"):
                    _produce((replace(row, params={**row.params, "thickness": thickness}),))
        with self.assertRaisesRegex(ElementProducerError, "must not be negative"):
            _produce((replace(row, params={**row.params, "thickness": -0.05}),))
        (flight,), _ = _produce((replace(row, params={**row.params, "thickness": 0.0}),))    # a stated zero (a re-indexed solid step) is a solid step
        self.assertEqual([op.op_id for op in flight.operations], ["stair-north"])

    def test_the_flight_is_one_stepped_solid_on_the_base_datum_and_publishes_its_top(self) -> None:
        (stair,), context = _produce((self._row(),))
        (op,) = stair.operations
        self.assertEqual((op.op_id, op.kind, op.output_object_ids), ("stair-north", GeometryOperationKind.LOFT, ("obj-stair-north",)))
        self.assertEqual(op.semantic_binding_ids, ("binding-monument-stair",))
        self.assertEqual([(b.op_id, b.datum_id) for b in stair.bindings], [("stair-north", PN)])
        self.assertEqual(stair.datums[0].datum_id, "stair-north-top")
        self.assertAlmostEqual(context.datum_value("stair-north-top"), 3.57 + 10 * 0.18)
        params = _op_params(op)
        self.assertNotIn("base_offset", params)                                       # the flight stands on the datum itself
        self.assertEqual((params["cap_ends"], params["loft_type"], params["profile_basis"], params["profile_size"]), (True, "straight", "polyline", 22))
        profiles = params["profiles"]
        self.assertEqual(len(profiles), 44)                                           # two sides, each 2 · 10 + 2 vertices
        _assert_bbox(self, profiles, ((0.0, 3.0), (0.0, 1.8), (-14.45, -13.25)))      # run 3.0 along W, ten rises of 0.18, width 1.2 across the line
        near, far = profiles[:22], profiles[22:]
        self.assertEqual(({round(p[2], 9) for p in near}, {round(p[2], 9) for p in far}), ({-13.25}, {-14.45}))
        section = [(round(p[0], 9), round(p[1], 9)) for p in near]
        self.assertEqual(section[:4], [(0.0, 0.0), (3.0, 0.0), (3.0, 1.8), (2.7, 1.8)])   # the floor line, then the top tread back
        self.assertEqual(section, [(round(p[0], 9), round(p[1], 9)) for p in far])          # the far side is the same outline
        for k in range(1, 11):                                                         # every nosing stands at k · going, k · rise
            self.assertIn((round(k * 0.3, 9), round(k * 0.18, 9)), section)
        self.assertEqual([(r.relation_id, r.kind, r.subject, r.object, r.datum_id) for r in stair.relations],
                         [("stair-north-stands-on", "support", PN, "stair-north", PN)])

    def test_a_flight_refuses_a_zero_length_line_and_a_going_that_does_not_span_it(self) -> None:
        row = self._row()
        with self.assertRaises(ElementProducerError):
            _produce((replace(row, references={**row.references, "to": {"axis_point": {"axis": "W", "along": 0.0}}}),))
        with self.assertRaises(ElementProducerError):
            _produce((self._row(going=0.4),))                                     # ten steps of 0.4 m span 4 m, not the 3 m line
        (stair,), _ = _produce((self._row(going=0.3),))                           # the declared going that does span it is taken
        self.assertEqual([op.op_id for op in stair.operations], ["stair-north"])
        self.assertEqual(_op_params(stair.operations[0])["profile_size"], 22)

    def test_production_order_puts_the_flight_before_what_seats_on_its_top(self) -> None:
        landing = ElementRow("landing", "monument-landing", "prism", {"base": {"datum": "stair-north-top"}},
                             {"profile": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], "height": 0.2}, BASIS)
        self.assertEqual([r.element_id for r in production_order((landing, self._row()))], ["stair-north", "landing"])


def _wedge_row(line=None, **params) -> ElementRow:
    start, end = line or (_on("W", 0.0), _on("W", 4.0))
    return ElementRow("abutment-north", "roof-abutments", "wedge",
                      {"from": start, "to": end, "base": {"level": "level-ground"}},
                      {"depth": 2.0, "low": 0.5, "high": 2.5, **params}, BASIS)


def _shell_row(**params) -> ElementRow:
    return ElementRow("rotunda-shell", "rotunda-wall", "shell",
                      {"at": _on("W", 0.0), "base": {"level": "level-ground"}},
                      {"outer_radius": 5.0, "thickness": 0.6, "height": 4.0, "kind": "cylinder", "segments": 8, **params}, BASIS)


class WedgeTests(unittest.TestCase):
    """A five-face wedge: one loft through two end rectangles, the far one taller."""

    def _row(self, line=None, **params) -> ElementRow:
        return _wedge_row(line, **params)

    def _stated(self, line=None, **params) -> dict:
        (wedge,), _ = _produce((self._row(line, **params),))
        operation = wedge.operations[0]
        # what the box cannot show is declared, not smuggled in as a loft parameter
        self.assertEqual(set(_op_params(operation)) & set(operation.statements), set())
        return dict(operation.statements)

    def _sense(self, line, **params) -> str:
        return self._stated(line, **params)["wedge_sense"]

    def test_the_top_plane_rises_along_the_line_and_the_wedge_publishes_it(self) -> None:
        (wedge,), context = _produce((self._row(),))
        op, = wedge.operations
        self.assertEqual((op.op_id, op.kind, op.semantic_binding_ids), ("abutment-north", GeometryOperationKind.LOFT, ("binding-roof-abutments",)))
        params = _op_params(op)
        self.assertEqual(params["profile_size"], 4)
        self.assertEqual(len(params["profiles"]), 8)
        _assert_bbox(self, params["profiles"][:4], ((0.0, 0.0), (0.0, 0.5), (-14.85, -12.85)))     # the near face stops at low
        _assert_bbox(self, params["profiles"][4:], ((4.0, 4.0), (0.0, 2.5), (-14.85, -12.85)))     # the far face reaches high
        self.assertEqual(wedge.datums[0].datum_id, "abutment-north-top")
        self.assertAlmostEqual(context.datum_value("abutment-north-top"), 2.5)
        self.assertEqual([(r.relation_id, r.kind, r.subject, r.object, r.datum_id) for r in wedge.relations],
                         [("abutment-north-stands-on", "support", "level-ground", "abutment-north", "level-ground")])

    def test_slope_across_tips_the_plane_across_the_depth_instead(self) -> None:
        (wedge,), context = _produce((self._row(slope_across=True),))
        profiles = _op_params(wedge.operations[0])["profiles"]
        self.assertEqual([round(p[1], 9) for p in profiles[:4]], [0.0, 0.0, 2.5, 0.5])
        self.assertEqual([round(p[1], 9) for p in profiles[4:]], [0.0, 0.0, 2.5, 0.5])   # the same face at both ends: the slope is across
        self.assertAlmostEqual(profiles[3][2], -12.85)                                   # low sits on the -normal side
        self.assertAlmostEqual(profiles[2][2], -14.85)
        self.assertAlmostEqual(context.datum_value("abutment-north-top"), 2.5)

    def test_the_stated_sense_is_the_world_direction_the_top_rises_in(self) -> None:
        """Along the run the low edge is at ``from``, so the rise is the from→to direction itself.

        The string is anchored to the kernel plan axes, not to the row's
        reference order, so the same line named backwards is the mirror
        wedge and says so — which is the one thing the saved box cannot show.
        """

        origin, ten_x = _on("OX", 0.0), _on("OX", 10.0)                # (0, 0) → (10, 0)
        self.assertEqual(self._sense((origin, ten_x)), "+x")
        self.assertEqual(self._sense((ten_x, origin)), "-x")           # the same line, named the other way round
        ten_z = _on("OZ", 10.0)                                        # (0, 0) → (0, 10): z dominates
        self.assertEqual(self._sense((origin, ten_z)), "+z")
        self.assertEqual(self._sense((ten_z, origin)), "-z")
        self.assertEqual(self._sense((_on("OD", 0.0), _on("OD", 10.0))), "+x")   # a 45° run: an exact tie picks x

    def test_across_the_run_the_stated_sense_is_the_normal_it_rises_on(self) -> None:
        """``slope_across`` puts high on the +normal side, and the normal of from→to is (uz, −ux)."""

        origin, ten_x = _on("OX", 0.0), _on("OX", 10.0)
        self.assertEqual(self._sense((origin, ten_x), slope_across=True), "-z")  # u = (1, 0), so the normal is (0, −1)
        self.assertEqual(self._sense((ten_x, origin), slope_across=True), "+z")  # u = (−1, 0), so the normal is (0, 1)
        self.assertEqual(self._stated((origin, ten_x), slope_across=True)["wedge_axis"], "across")
        self.assertEqual(self._stated()["wedge_axis"], "along")

    def test_a_wedge_refuses_a_top_that_does_not_rise_and_a_zero_length_line(self) -> None:
        with self.assertRaises(ElementProducerError):
            _produce((self._row(high=0.5),))                                       # high == low is a prism, and says so
        row = self._row()
        with self.assertRaises(ElementProducerError):
            _produce((replace(row, references={**row.references, "to": {"axis_point": {"axis": "W", "along": 0.0}}}),))


class ShellTests(unittest.TestCase):
    """A hollow revolved shell: one annulus extruded, or annuli lofted up a cap."""

    def _row(self, **params) -> ElementRow:
        return _shell_row(**params)

    def test_a_cylinder_shell_is_one_annulus_extruded_by_its_height(self) -> None:
        (shell,), context = _produce((self._row(),))
        op, = shell.operations
        self.assertEqual((op.op_id, op.kind, op.semantic_binding_ids), ("rotunda-shell", GeometryOperationKind.EXTRUSION, ("binding-rotunda-wall",)))
        params = _op_params(op)
        self.assertEqual(len(params["profile"]), 16)                               # eight points out, eight back
        self.assertAlmostEqual(params["vector"][1], 4.0)
        _assert_bbox(self, params["profile"], ((-5.0, 5.0), (0.0, 0.0), (-18.85, -8.85)))
        for point in params["profile"][:8]:
            self.assertAlmostEqual(_radius(point), 5.0)
        for point in params["profile"][8:]:
            self.assertAlmostEqual(_radius(point), 4.4)                            # outer_radius - thickness, the same way round back
        self.assertEqual(shell.datums[0].datum_id, "rotunda-shell-top")
        self.assertAlmostEqual(context.datum_value("rotunda-shell-top"), 4.0)
        self.assertEqual([(r.relation_id, r.kind, r.subject, r.object, r.datum_id) for r in shell.relations],
                         [("rotunda-shell-stands-on", "support", "level-ground", "rotunda-shell", "level-ground")])

    def test_a_dome_shell_lofts_its_rings_up_the_cap_and_closes_on_a_small_annulus(self) -> None:
        (shell,), context = _produce((self._row(kind="dome", height=3.0, rings=4),))
        op, = shell.operations
        self.assertEqual(op.kind, GeometryOperationKind.LOFT)
        params = _op_params(op)
        self.assertEqual(params["profile_size"], 16)
        self.assertEqual(len(params["profiles"]), 64)
        first_of_ring = [params["profiles"][i * 16] for i in range(4)]
        self.assertEqual([round(p[1], 6) for p in first_of_ring], [0.0, 1.5, 2.598076, 3.0])          # 3 m * sin(0, 30, 60, 90 degrees)
        self.assertEqual([round(_radius(p), 6) for p in first_of_ring], [5.0, 4.330127, 2.5, 0.3])    # 5 m * cos(...), the crown at thickness / 2
        self.assertAlmostEqual(_radius(params["profiles"][8]), 4.4)                                   # the inner circle of the base ring
        _assert_bbox(self, params["profiles"], ((-5.0, 5.0), (0.0, 3.0), (-18.85, -8.85)))
        self.assertAlmostEqual(context.datum_value("rotunda-shell-top"), 3.0)

    def test_a_shell_refuses_a_thickness_that_is_not_inside_the_outer_radius_and_an_unknown_kind(self) -> None:
        with self.assertRaises(ElementProducerError):
            _produce((self._row(outer_radius=0.5),))                               # a 0.6 m wall has nowhere to stand inside a 0.5 m radius
        with self.assertRaises(ElementProducerError):
            _produce((self._row(kind="vault"),))


def _ring(radius: float, y: float, n: int = 8) -> list[list[float]]:
    """One closed ring section of ``n`` points at height ``y`` over the base datum, in the building frame."""

    return [[round(radius * math.cos(2 * math.pi * j / n), 9), y, round(radius * math.sin(2 * math.pi * j / n), 9)] for j in range(n)]


def _loft_row(**params) -> ElementRow:
    return ElementRow("drum", "rotunda-drum", "loft", {"base": {"level": "level-ground"}},
                      {"profiles": [_ring(1.0, 0.0), _ring(1.0, 1.0)], "profile_size": 8, **params}, BASIS)


class DirectLoftTests(unittest.TestCase):
    """A loft row states its own closure: capped into a solid unless it says ``cap_ends: false``.

    A drum or a dome the source gives as a surface without thickness is
    lofted open at both end rings rather than closed with an invented
    thickness; the row's word travels to the operation as it was stated.
    """

    def test_direct_transforms_keep_sections_identity_closure_and_nonzero_datum(self) -> None:
        for loft_type in ("straight", "normal"):
            with self.subTest(loft_type=loft_type):
                row = replace(_loft_row(loft_type=loft_type, cap_ends=False), references={"base": {"level": PN}})
                original = json.loads(json.dumps(row.params))
                _, context = _produce((row,))
                moved = edit_drawn_element(row, context, kind="move", translation=[2, 3, 4])
                rotated = edit_drawn_element(moved, context, kind="rotate", axis=[0, 0, 1], angle_degrees=90,
                                             origin=[0, 3.57, 0])
                mirrored = edit_drawn_element(rotated, context, kind="scale", scale=[-2, 2, 2], origin=[0, 3.57, 0])
                copied = edit_drawn_element(mirrored, context, kind="copy", translation=[10, 0, 0])
                for source, target in zip([p for s in row.params["profiles"] for p in s],
                                          [p for s in copied.params["profiles"] for p in s]):
                    x, y, z = source
                    for value, expected in zip(target, [2 * (y + 3) + 10, 2 * (x + 2), 2 * (z + 4)]):
                        self.assertAlmostEqual(value, expected)
                self.assertEqual((copied.element_id, copied.component_id, copied.producer, copied.references, copied.basis_refs),
                                 (row.element_id, row.component_id, "loft", row.references, row.basis_refs))
                self.assertEqual({k: v for k, v in copied.params.items() if k != "profiles"},
                                 {k: v for k, v in row.params.items() if k != "profiles"})
                self.assertEqual(row.params, original, "the source definition remains unchanged")
                produced, _ = _produce((copied,))
                self.assertEqual(produced[0].operations[0].output_object_ids, ("obj-drum",))
                self.assertFalse(_op_params(produced[0].operations[0])["cap_ends"])
                self.assertEqual(produced[0].bindings[0].datum_id, PN)

    def test_loft_default_scale_origin_is_the_world_section_center(self) -> None:
        row = replace(_loft_row(), references={"base": {"level": PN}})
        _, context = _produce((row,))
        changed = edit_drawn_element(row, context, kind="scale", scale=[2, 3, 4])
        _assert_bbox(self, [p for s in changed.params["profiles"] for p in s], ((-2, 2), (-1, 2), (-4, 4)))

    def test_loft_refuses_shape_changing_smooth_stretch_push_pull_and_host_detachment(self) -> None:
        row = _loft_row(loft_type="normal")
        _, context = _produce((row,))
        with self.assertRaisesRegex(ElementProducerError, "non-uniform scale.*refit"):
            edit_drawn_element(row, context, kind="scale", scale=[2, 1, 1])
        with self.assertRaisesRegex(ElementProducerError, "section controls"):
            edit_drawn_element(row, context, kind="push_pull", distance=1)
        with self.assertRaisesRegex(ElementProducerError, "nonzero"):
            edit_drawn_element(row, context, kind="scale", scale=[0, 1, 1])
        rows = (*_rows(), replace(row, references={"base": {"datum": "capitals-west-top"}}))
        _, context = _produce(rows)
        with self.assertRaisesRegex(ElementProducerError, "cannot detach its host"):
            edit_drawn_element(rows[-1], context, kind="move", translation=[1, 0, 0])

    def test_section_heights_are_relative_to_the_datum_without_moving_the_profiles(self) -> None:
        for bottom, top, support in ((0.4, 1.4375, {"rise": 0.4}),
                                     (-0.2, 0.8, {"engagement_depth": 0.2}),
                                     (0.0, 1.0, {})):
            for datum_height in (3.57, 7.2):
                with self.subTest(bottom=bottom, datum_height=datum_height):
                    sections = [_ring(1.0, bottom), _ring(0.8, top)]
                    row = replace(_loft_row(profiles=sections), references={"base": {"level": PN}})
                    levels = _levels(piano=datum_height)
                    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=levels),
                                                published={}, frame_id="world")
                    (element,) = produce_rows((row,), context)
                    params = _op_params(element.operations[0])
                    self.assertEqual(params["profiles"], [point for section in sections for point in section])
                    self.assertEqual(row.params["profiles"], sections)
                    self.assertEqual(element.relations[0].parameters, support)
                    operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for op in element.operations)
                    state = _state()
                    result = compile_geometry_program(
                        state, _only(_proposal(state, extra_operations=operations), operations, ()),
                        active_commitment_refs=(COMMITMENT,), interface_datums=levels.datums(),
                        datum_bindings=element.bindings,
                    )
                    self.assertIsNotNone(result.program, result.receipt.issues)
                    bounds = expected_object_bounds(result.program)["obj-drum"]
                    self.assertAlmostEqual(bounds["bbox_min"][1], datum_height + bottom)
                    self.assertAlmostEqual(bounds["bbox_max"][1], datum_height + top)
                    self.assertEqual([bounds["bbox_min"][axis] for axis in (0, 2)], [-1.0, -1.0])
                    self.assertEqual([bounds["bbox_max"][axis] for axis in (0, 2)], [1.0, 1.0])

    def test_a_direct_loft_still_refuses_a_second_authored_height_offset(self) -> None:
        row = replace(_loft_row(profiles=[_ring(1.0, 0.4), _ring(1.0, 1.4)]),
                      references={"base": {"datum": "level-ground", "offset": 0.4}})
        with self.assertRaisesRegex(ElementProducerError, "sections carry their own heights"):
            _produce((row,))

    def test_a_loft_row_is_capped_unless_it_says_otherwise(self) -> None:
        (drum,), _ = _produce((_loft_row(),))
        (op,) = drum.operations
        self.assertEqual((op.kind, op.output_object_ids, op.semantic_binding_ids), (GeometryOperationKind.LOFT, ("obj-drum",), ("binding-rotunda-drum",)))
        params = _op_params(op)
        self.assertEqual((params["cap_ends"], params["loft_type"], params["profile_basis"], params["profile_size"]), (True, "straight", "polyline", 8))
        self.assertNotIn("closed_profile", params)
        (drum,), _ = _produce((_loft_row(cap_ends=False, loft_type="normal"),))
        params = _op_params(drum.operations[0])
        self.assertEqual((params["cap_ends"], params["loft_type"], params["profile_basis"]), (False, "normal", "polyline"))
        self.assertEqual(len(params["profiles"]), 16)
        _assert_bbox(self, params["profiles"], ((-1.0, 1.0), (0.0, 1.0), (-1.0, 1.0)))
        self.assertEqual([(b.op_id, b.datum_id) for b in drum.bindings], [("drum", "level-ground")])

    def test_closure_is_a_boolean_not_a_word_or_a_number(self) -> None:
        for value in ("false", 0, 1, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ElementProducerError, "drum: cap_ends must be true or false"):
                    _produce((_loft_row(cap_ends=value),))

    def test_a_stated_closed_profile_travels_and_an_open_one_is_refused(self) -> None:
        (drum,), _ = _produce((_loft_row(closed_profile=True),))
        self.assertIs(_op_params(drum.operations[0])["closed_profile"], True)
        with self.assertRaisesRegex(ElementProducerError, "drum: an open section profile is not produced"):
            _produce((_loft_row(closed_profile=False),))
        with self.assertRaisesRegex(ElementProducerError, "closed_profile must be true or false"):
            _produce((_loft_row(closed_profile="yes"),))

    def test_profile_basis_passes_through_the_ir_vocabulary_and_refuses_the_rest(self) -> None:
        (drum,), _ = _produce((_loft_row(profile_basis="interpolated"),))
        self.assertEqual(_op_params(drum.operations[0])["profile_basis"], "interpolated")   # the executor that cannot realize it refuses it by name
        with self.assertRaisesRegex(ElementProducerError, "drum: profile_basis must be 'polyline' or 'interpolated'"):
            _produce((_loft_row(profile_basis="bezier"),))

    def test_the_producers_that_build_their_own_sections_stay_closed_whatever_the_row_carries(self) -> None:
        stair = ElementRow("stair-north", "monument-stair", "stair",
                           {"from": _on("W", 0.0), "to": _on("W", 3.0), "base": {"level": PN}},
                           {"count": 10, "rise": 0.18, "width": 1.2, "cap_ends": False}, BASIS)
        produced, _ = _produce((stair, _wedge_row(cap_ends=False), _shell_row(kind="dome", height=3.0, cap_ends=False, closed_profile=False)))
        for element in produced:
            (op,) = element.operations
            params = _op_params(op)
            self.assertIs(params["cap_ends"], True, op.op_id)
            self.assertNotIn("closed_profile", params, op.op_id)


class SemanticLoftContractTests(unittest.TestCase):
    def _record(self, **params):
        record = authored_record()
        element = replace(next(e for e in record.entities if e.entity_id == "wall-south"), fields={
            "producer": "loft", "component_id": "envelope", "references": {"base": {"level": "level-ground"}},
            "params": {"profiles": [_ring(1.0, 0.0), _ring(0.6, 1.0), _ring(0.8, 2.0)], "profile_size": 8, **params},
        })
        return replace(record, entities=tuple(element if e.entity_id == element.entity_id else e for e in record.entities))

    def test_advertised_solid_and_open_surface_each_produce_one_loft(self) -> None:
        for cap_ends in (True, False):
            for loft_type in ("straight", "normal"):
                with self.subTest(cap_ends=cap_ends, loft_type=loft_type):
                    record = self._record(cap_ends=cap_ends, loft_type=loft_type, closed_profile=True, profile_basis="polyline")
                    validate_element_contract(record, ("wall-south",))
                    row = next(row for row in element_rows_of(record) if row.element_id == "wall-south")
                    produced, _ = _produce((row,))
                    (operation,) = produced[0].operations
                    self.assertEqual((operation.kind, operation.output_object_ids), (GeometryOperationKind.LOFT, ("obj-wall-south",)))
                    self.assertEqual(_op_params(operation)["cap_ends"], cap_ends)
                    self.assertEqual(produced[0].datums, ())

    def test_unavailable_or_mismatched_sections_fail_before_a_candidate(self) -> None:
        bad_sections = [_ring(1.0, 0.0), _ring(0.6, 1.0, 7), _ring(0.8, 2.0, 9)]
        repeated = [_ring(1.0, 0.0) + [[1.0, 0.0, 0.0]], _ring(1.0, 2.0) + [[1.0, 2.0, 0.0]]]
        for params in ({"profile_basis": "interpolated"}, {"loft_type": "loose"}, {"cap_ends": 1},
                       {"closed_profile": False}, {"profiles": [_ring(1.0, 0.0)]},
                       {"profiles": bad_sections}, {"profiles": repeated, "profile_size": 9},
                       {"profile_size": 8.5}, {"profiles": [[[0, 0]] * 8] * 2}):
            with self.subTest(params=params), self.assertRaises(ElementProducerError):
                validate_element_contract(self._record(**params), ("wall-south",))


class AuthoredRecordTests(unittest.TestCase):
    """A record is producible from its own levels, grids and references."""

    def test_the_record_orders_its_rows_and_makes_the_support_it_declares(self) -> None:
        record = authored_record()
        context = ProductionContext(references=ReferenceContext(grids=project_grids_of(record), levels=project_levels_of(record)), published={}, frame_id="world")
        produced = produce_rows(element_rows_of(record), context)
        plinth, wall = produced
        self.assertEqual([r.element_id for r in element_rows_of(record)], ["plinth", "wall-south"])   # the wall stands on a datum the plinth has yet to publish
        self.assertEqual(plinth.datums[0].datum_id, "plinth-top")
        self.assertAlmostEqual(context.datum_value("plinth-top"), 0.6)
        self.assertEqual(wall.bindings[0].datum_id, "plinth-top")                                    # the wall reads the datum, not a number
        self.assertEqual([(r.kind, r.subject, r.object) for r in wall.relations], [("hosts_void", "wall-south", "window-south")])
        declared = record.relations[0]
        self.assertEqual((declared.kind, declared.subject, declared.object, declared.datum_role), ("support", "plinth", "wall-south", "plinth-top"))
        self.assertEqual(declared.validator.check_kind, "support_contact")
        state = _state()
        operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for e in produced for op in e.operations)
        datums = tuple(sorted(list(context.published.values()) + list(project_levels_of(record).datums()), key=lambda d: d.datum_id))
        result = compile_geometry_program(state, _only(_proposal(state, extra_operations=operations), operations, ()), active_commitment_refs=(COMMITMENT,),
                                          interface_datums=datums, datum_bindings=tuple(b for e in produced for b in e.bindings))
        self.assertIsNotNone(result.program, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
        bounds = expected_object_bounds(result.program)
        # the wall's physical object is what the void was cut out of; it sits on the plinth's top face
        self.assertAlmostEqual(bounds["obj-plinth"]["bbox_max"][1], bounds["obj-wall-south-cut"]["bbox_min"][1])
        self.assertAlmostEqual(bounds["obj-wall-south-cut"]["bbox_max"][1] - bounds["obj-wall-south-cut"]["bbox_min"][1], 2.97)


class BoundRowTests(unittest.TestCase):
    """A row reads a parameter only through an explicit ``@key`` binding; a literal is never rebound."""

    def _bound(self, height="@wall_height", wall_height: float = 2.97):
        record = authored_record()
        entities = tuple(replace(e, fields={**e.fields, "params": {**e.fields["params"], "height": height}}) if e.entity_id == "wall-south" else e for e in record.entities)
        parameters = tuple(replace(p, value=wall_height) if p.key == "wall_height" else p for p in record.parameters)
        return replace(record, entities=entities, parameters=parameters)

    def test_a_bound_height_is_the_evaluated_parameter_and_the_literal_next_to_it_stays(self) -> None:
        rows = {r.element_id: r for r in element_rows_of(self._bound())}
        self.assertAlmostEqual(rows["wall-south"].params["height"], 2.97)                           # 3 * module - 0.63, from the record's own module
        self.assertEqual(rows["wall-south"].params["thickness"], 0.3)
        self.assertEqual(rows["plinth"].params["height"], 0.6)                                      # a literal is a literal
        context = ProductionContext(references=ReferenceContext(grids=project_grids_of(self._bound()), levels=project_levels_of(self._bound())), published={}, frame_id="world")
        plinth, wall = produce_rows(element_rows_of(self._bound()), context)
        body = {op.op_id: _op_params(op)["vector"][1] for op in wall.operations if "vector" in _op_params(op)}
        self.assertAlmostEqual(body["wall-south"], 2.97)                                            # the wall body rises by the bound height; the void keeps its own

    def test_a_stale_bound_derived_value_fails_typed_before_any_producer_runs(self) -> None:
        with self.assertRaisesRegex(ElementProducerError, r"element wall-south: params.height binds @wall_height: stored value 2.5 of derived parameter wall_height disagrees"):
            element_rows_of(self._bound(wall_height=2.5))
        self.assertEqual([r.element_id for r in element_rows_of(self._bound(height=2.5, wall_height=2.5))], ["plinth", "wall-south"])   # unbound: the literal runs


class BoundProfileContractTests(unittest.TestCase):
    def _record(self, producer):
        from archflow.state.state_record import Parameter
        from tests.support import shared_bound_state

        record = authored_record()
        profile = [[0, 0], ["@width", 0], ["@width", "@half_width"], [0, "@half_width"]]
        params = {"profile": profile, "elevation": 0.4}
        if producer == "planar-surface":
            profile.append([0, 0])
        else:
            params["height"] = 1.5
        element = replace(next(e for e in record.entities if e.entity_id == "wall-south"), fields={
            "producer": producer, "component_id": "envelope", "references": {"base": {"level": "level-ground"}},
            "params": params,
        })
        return replace(record, entities=tuple(element if e.entity_id == element.entity_id else e for e in record.entities),
                       parameters=record.parameters + (Parameter("width", 4.0, "m"),
                                                       Parameter("half_width", 2.0, "m", expr="width / 2"))).bound_to(shared_bound_state()[1])

    def test_advertised_profile_bindings_drive_geometry_after_a_parameter_edit(self) -> None:
        from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator
        from monkeyarch.capabilities.element_producers import _check_signature_value

        for producer in ("prism", "planar-surface"):
            with self.subTest(producer=producer):
                record = self._record(producer)
                authored = next(e for e in record.entities if e.entity_id == "wall-south")
                _check_signature_value(authored.fields["params"], producer_signatures()[producer]["parameters"], "params")
                changed = apply_state_record_operator(record, StateRecordOperator(
                    kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                    target_ref="parameter:width", key="width", value=6.0,
                ))
                for current, width, depth in ((record, 4.0, 2.0), (changed, 6.0, 3.0)):
                    validate_element_contract(current, (authored.entity_id,))
                    row = next(row for row in element_rows_of(current) if row.element_id == authored.entity_id)
                    produced, _ = _produce((row,))
                    operation = _op_params(produced[0].operations[0])
                    _assert_bbox(self, operation["profile"], ((0, width), (0, 0), (0, depth)))
                    self.assertAlmostEqual(operation["base_offset"], 0.4)
                    if producer == "prism":
                        self.assertEqual(operation["vector"], [0, 1.5, 0])
                    self.assertEqual(next(e for e in current.entities if e.entity_id == authored.entity_id).fields["params"],
                                     authored.fields["params"])

    def test_advertised_profile_coordinates_refuse_invalid_values_and_bindings(self) -> None:
        from monkeyarch.capabilities.element_producers import _check_signature_value

        for producer in ("prism", "planar-surface"):
            schema = producer_signatures()[producer]["parameters"]["properties"]["profile"]
            for value in (True, None, float("inf"), float("nan"), "4", "@", "@width + 1", {"parameter": "width"}):
                with self.subTest(producer=producer, value=value), self.assertRaises(ElementProducerError):
                    _check_signature_value([[0, 0], [value, 0], [4, 2], [0, 2], [0, 0]], schema, "profile")


class StatedRowsThroughTheProposalTests(ProducerFixture):
    """A wedge row and a shell row travel the whole way: producer, contract, export.

    The two producers that declare facts a solid cannot show are the two
    the function contract can refuse, because their statements used to ride
    on ``parameters``: the loft contract named them ``extra`` and the seat
    exhausted with nothing built. Nothing here is scripted around — the
    real rows are produced, the real proposal producer decodes and checks
    them, and the accepted program is asked for its export text.
    """

    def _accepted(self):
        from monkeyarch.capabilities.geometry_proposal import proposal_authoring_output
        from tests.support import ScriptedProvider

        context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
        produced = produce_rows((_wedge_row(), _shell_row()), context)
        binding = self.proposal.semantic_bindings[0]
        stated = tuple(replace(op, semantic_binding_ids=(binding.binding_id,)) for e in produced for op in e.operations)
        proposal = replace(
            self.proposal,
            operations=tuple(sorted(self.proposal.operations + stated, key=lambda op: op.op_id)),
            semantic_bindings=(replace(binding, object_ids=tuple(sorted(binding.object_ids + tuple(o for op in stated for o in op.output_object_ids)))),),
        )
        provider = ScriptedProvider((proposal_authoring_output(proposal),))
        return self.produce(
            provider,
            interface_datums=tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id)),
            datum_bindings=tuple(b for e in produced for b in e.bindings),
        )

    async def test_the_contract_accepts_the_stated_rows_and_the_export_carries_the_strings(self) -> None:
        result = await self._accepted()

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED, [
            (row["code"], row["detail"])
            for ref in result.round_refs
            for row in self.repository.load_json(ref).get("issues", [])
        ])
        assert result.program is not None
        objects = expected_object_semantics(result.program)["objects"]
        self.assertEqual(
            {k: v for k, v in objects["obj-abutment-north"]["user_text"].items() if k.startswith("archflow:wedge_")},
            {"archflow:wedge_axis": "along", "archflow:wedge_high": "2.5", "archflow:wedge_low": "0.5", "archflow:wedge_sense": "+x"},
        )
        self.assertEqual(
            {k: v for k, v in objects["obj-rotunda-shell"]["user_text"].items() if k.startswith("archflow:shell_")},
            {"archflow:shell_kind": "cylinder", "archflow:shell_thickness": "0.6"},
        )

    async def test_the_statements_survive_the_provider_payload_they_travelled_in(self) -> None:
        """The proposal is encoded for the provider and decoded back; nothing declared is lost."""

        result = await self._accepted()

        assert result.program is not None
        wedge = next(op for op in result.program.proposal.operations if op.op_id == "abutment-north")
        self.assertEqual(sorted(wedge.statements), ["wedge_axis", "wedge_high", "wedge_low", "wedge_sense"])
        self.assertEqual(set(wedge.statements) & {p.name for p in wedge.parameters}, set())

    def test_a_blank_statement_is_refused_by_the_contract_not_exported_empty(self) -> None:
        from monkeyarch.capabilities.geometry_proposal import _validate_function_contracts

        (wedge,), _ = _produce((_wedge_row(),))
        blank = replace(wedge.operations[0], statements={**wedge.operations[0].statements, "wedge_axis": "  "})
        issues: list = []

        _validate_function_contracts(replace(self.proposal, operations=(blank,)), issues=issues)

        self.assertEqual([issue.code for issue in issues], ["malformed_model_output"])
        self.assertIn("wedge_axis", issues[0].detail)


if __name__ == "__main__":
    unittest.main()
