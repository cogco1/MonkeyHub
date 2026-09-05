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
from archflow.capabilities.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    element_rows_of,
    produce_rows,
    production_order,
)
from archflow.capabilities.geometry_proposal import GeometryProposalStatus
from archflow.capabilities.reference_resolver import ReferenceContext
from archflow.compilers.geometry import compile_geometry_program
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
        from archflow.capabilities.geometry_proposal import proposal_authoring_output
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
        from archflow.capabilities.geometry_proposal import _validate_function_contracts

        (wedge,), _ = _produce((_wedge_row(),))
        blank = replace(wedge.operations[0], statements={**wedge.operations[0].statements, "wedge_axis": "  "})
        issues: list = []

        _validate_function_contracts(replace(self.proposal, operations=(blank,)), issues=issues)

        self.assertEqual([issue.code for issue in issues], ["malformed_model_output"])
        self.assertIn("wedge_axis", issues[0].detail)


if __name__ == "__main__":
    unittest.main()
