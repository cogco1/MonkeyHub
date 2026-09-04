"""P092: wall and opening element families.

A wall by location line hosts voids the compiler cuts; window and door
types fill them; every level is a datum and every seat height rides on
it as base_offset. Exclusions refuse an opening at solve time.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace

from archflow.adapters.cad_program import CadTranslationError, expected_object_bounds, lift_to_base_level
from archflow.capabilities.opening_solver import (
    DoorType,
    OpeningSolverError,
    WindowType,
    solve_door,
    solve_openings,
    solve_window,
)
from archflow.capabilities.wall_solver import (
    OpeningKind,
    OpeningRequest,
    WallElement,
    WallSolverError,
    solve_wall,
)
from archflow.compilers.geometry import compile_geometry_program
from archive.archflow.realization.sandbox import SandboxRealizationError, _lift_to_base_level
from archflow.state.geometry_program import (
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    DetailMaturity,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramError,
    HostedAssembly,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
    required_assembly_roles,
)
from archive.tests.lane_state import _state
from tests.test_geometry_compiler import COMMITMENT, _proposal

BINDING = "building-binding"
LEVEL = "level-piano-nobile"
BASE = 3.57


def _wall(**overrides) -> WallElement:
    fields = dict(
        wall_id="wall-west", origin=(-10.71, -10.71), direction=(0.0, 1.0), length=21.42,
        thickness=0.42, height=9.818, base_level_datum_id=LEVEL, frame_id="world", binding_id=BINDING,
    )
    fields.update(overrides)
    return WallElement(**fields)


def _window(opening_id="window-left", along=3.16, width=1.32, sill=1.53, head=4.58) -> OpeningRequest:
    return OpeningRequest(opening_id=opening_id, kind=OpeningKind.WINDOW, along=along, width=width, sill=sill, head=head, binding_id=BINDING)


def _door(opening_id="door", along=10.71, width=2.5, sill=0.0, head=4.53) -> OpeningRequest:
    return OpeningRequest(opening_id=opening_id, kind=OpeningKind.DOOR, along=along, width=width, sill=sill, head=head, binding_id=BINDING)


WINDOW_TYPE = WindowType(type_id="palladian-principal-window", frame_width=0.09, frame_depth=0.18, frame_projection=0.10, glazing_thickness=0.025, glazing_offset=0.01)
DOOR_TYPE = DoorType(type_id="palladian-axial-door", frame_width=0.12, frame_depth=0.18, frame_projection=0.10, leaf_thickness=0.08, leaf_offset=-0.025, leaf_count=2, leaf_gap=0.03, clearance_bottom=0.02, clearance_top=0.02)


def _level() -> InterfaceDatum:
    return InterfaceDatum.create(datum_id=LEVEL, kind=InterfaceDatumKind.LEVEL, published_by="seat-coordination", value=BASE, unit=LengthUnit.METER)


def _params(op) -> dict:
    return {p.name: json.loads(p.value_json) for p in op.parameters}


class WallSolverTests(unittest.TestCase):
    def test_enumerates_solid_tools_and_one_cut(self) -> None:
        solution = solve_wall(_wall(), (_window(), _door(), _window("window-right", along=18.26)))
        kinds = [op.kind for op in solution.operations]
        self.assertEqual(kinds.count(GeometryOperationKind.EXTRUSION), 4)
        self.assertEqual(kinds.count(GeometryOperationKind.BOOLEAN_INTERSECTION), 3)
        self.assertEqual(kinds.count(GeometryOperationKind.BOOLEAN_DIFFERENCE), 1)
        aperture = next(op for op in solution.operations if op.op_id == "wall-west-aperture-door")
        self.assertEqual(aperture.input_object_ids, ("obj-wall-west", "obj-wall-west-void-door"))
        self.assertTrue(_params(aperture)["hidden_for_inspection"])
        cut = next(op for op in solution.operations if op.kind is GeometryOperationKind.BOOLEAN_DIFFERENCE)
        self.assertIn("obj-wall-west", cut.input_object_ids)
        self.assertEqual(sorted(cut.input_object_ids)[_params(cut)["base_index"]], "obj-wall-west")
        self.assertEqual(solution.cut_object_id, "obj-wall-west-cut")
        self.assertEqual(solution.realized_object_id, "obj-wall-west-cut")
        self.assertEqual(len(solution.datum_bindings), 4)
        self.assertTrue(all(b.datum_id == LEVEL and b.parameter_name == "base_level" for b in solution.datum_bindings))
        tool = next(op for op in solution.operations if op.op_id == "wall-west-void-window-left")
        self.assertEqual(_params(tool)["base_offset"], 1.53)
        self.assertEqual(_params(tool)["vector"], [0.0, 3.05, 0.0])
        self.assertEqual([v.opening_id for v in solution.voids], ["door", "window-left", "window-right"])
        self.assertEqual(solution.voids[0].cut_object_id, "obj-wall-west-cut")
        self.assertEqual(solution.voids[0].aperture_object_id, "obj-wall-west-aperture-door")
        # no numbers restate the level: no operation carries an absolute elevation
        for op in solution.operations:
            params = _params(op)
            if "profile" in params:
                self.assertTrue(all(p[1] == 0.0 for p in params["profile"]))

    def test_wall_without_openings_is_one_solid(self) -> None:
        solution = solve_wall(_wall())
        self.assertEqual(len(solution.operations), 1)
        self.assertIsNone(solution.cut_object_id)
        self.assertEqual(solution.realized_object_id, "obj-wall-west")

    def test_negative_paths_fail_typed(self) -> None:
        with self.assertRaises(WallSolverError):
            solve_wall(_wall(), (_window(along=0.5),))            # outside the wall length
        with self.assertRaises(WallSolverError):
            solve_wall(_wall(), (_window(head=12.0),))            # above the wall top
        with self.assertRaises(WallSolverError):
            solve_wall(_wall(), (_window(), _window("other", along=3.5)))   # overlap
        with self.assertRaises(WallSolverError):
            OpeningRequest(opening_id="x", kind=OpeningKind.WINDOW, along=3.0, width=1.0, sill=2.0, head=1.0, binding_id=BINDING)
        with self.assertRaises(WallSolverError):
            solve_wall(_wall(), (_window(),), exclusions=(((0, 0, 0), (1, 1, 1)),))   # no elevation to evaluate
        with self.assertRaises(WallSolverError):
            WallElement(wall_id="w", origin=(0.0, 0.0), direction=(0.0, 0.0), length=1.0, thickness=0.1, height=1.0,
                        base_level_datum_id=LEVEL, frame_id="world", binding_id=BINDING)

    def test_exclusion_refuses_an_opening_behind_a_roof(self) -> None:
        # attic window behind the west portico roof abutment (run-016 bounds)
        abutment = ((-10.754, 11.287, -6.69), (-10.19, 13.16, 6.69))
        attic = _window("attic-b", along=8.21, width=0.78, sill=8.08, head=8.93)      # y -2.89..-2.11, z 11.65..12.5
        clear = _window("attic-a", along=3.21, width=0.78, sill=8.08, head=8.93)      # y -7.89..-7.11: outside |y| 6.69
        with self.assertRaises(WallSolverError) as caught:
            solve_wall(_wall(), (attic,), exclusions=(abutment,), base_elevation=BASE)
        self.assertIn("intersects exclusion 0", str(caught.exception))
        solution = solve_wall(_wall(), (clear,), exclusions=(abutment,), base_elevation=BASE)
        self.assertEqual(len(solution.voids), 1)


class OpeningSolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.solution = solve_wall(_wall(), (_window(), _door()))
        self.voids = {v.opening_id: v for v in self.solution.voids}

    def test_window_fills_its_void(self) -> None:
        window = solve_window(self.voids["window-left"], WINDOW_TYPE, binding_id=BINDING)
        self.assertEqual(len(window.operations), 5)
        self.assertEqual(window.assembly.kind, AssemblyKind.WINDOW)
        self.assertEqual(window.assembly.maturity, DetailMaturity.ENVELOPE)
        self.assertEqual(window.assembly.objects_for(AssemblyRole.HOST_CUT), ("obj-wall-west-aperture-window-left",))
        self.assertEqual(window.assembly.interface_refs, ("interface:inside-to-outside",))
        named = solve_window(self.voids["window-left"], WINDOW_TYPE, binding_id=BINDING, interface_ref="relation:west-window-host-void")
        self.assertEqual(named.assembly.interface_refs, ("relation:west-window-host-void",))
        self.assertEqual(len(window.assembly.objects_for(AssemblyRole.FRAME)), 4)
        self.assertEqual(window.assembly.host_object_id, "obj-wall-west")
        pane = next(op for op in window.operations if op.op_id == "glazing-window-left")
        self.assertAlmostEqual(_params(pane)["base_offset"], 1.62)
        self.assertAlmostEqual(_params(pane)["vector"][1], 3.05 - 0.18)
        top = next(op for op in window.operations if op.op_id == "frame-window-left-top")
        self.assertAlmostEqual(_params(top)["base_offset"], 4.49)
        self.assertEqual(len(window.datum_bindings), 5)

    def test_door_fills_its_void_with_two_leaves(self) -> None:
        door = solve_door(self.voids["door"], DOOR_TYPE, binding_id=BINDING)
        self.assertEqual(len(door.operations), 5)
        self.assertEqual(door.assembly.kind, AssemblyKind.DOOR)
        self.assertEqual(len(door.assembly.objects_for(AssemblyRole.LEAF)), 2)
        leaf = next(op for op in door.operations if op.op_id == "door-leaf-door-0")
        self.assertAlmostEqual(_params(leaf)["base_offset"], 0.02)
        self.assertAlmostEqual(_params(leaf)["vector"][1], 4.53 - 0.12 - 0.04)

    def test_type_that_does_not_fit_and_wrong_kind_fail_typed(self) -> None:
        with self.assertRaises(OpeningSolverError):
            solve_window(self.voids["window-left"], replace(WINDOW_TYPE, frame_width=0.7), binding_id=BINDING)
        with self.assertRaises(OpeningSolverError):
            solve_window(self.voids["door"], WINDOW_TYPE, binding_id=BINDING)
        with self.assertRaises(OpeningSolverError):
            solve_openings(self.solution.voids, {"door": DOOR_TYPE}, binding_ids={"door": BINDING, "window-left": BINDING})
        both = solve_openings(self.solution.voids, {"door": DOOR_TYPE, "window-left": WINDOW_TYPE}, binding_ids={"door": BINDING, "window-left": BINDING})
        self.assertEqual([s.opening_id for s in both], ["door", "window-left"])


class MaturityRolesTests(unittest.TestCase):
    def test_envelope_maturity_requires_void_frame_and_infill_only(self) -> None:
        self.assertEqual(
            required_assembly_roles(AssemblyKind.WINDOW, DetailMaturity.ENVELOPE),
            (AssemblyRole.FRAME, AssemblyRole.GLAZING, AssemblyRole.HOST_CUT),
        )
        self.assertEqual(len(required_assembly_roles(AssemblyKind.WINDOW)), 5)
        self.assertEqual(len(required_assembly_roles(AssemblyKind.DOOR, DetailMaturity.FUNCTIONAL)), 5)
        members = (
            AssemblyMember(AssemblyRole.FRAME, ("frame",)),
            AssemblyMember(AssemblyRole.GLAZING, ("glazing",)),
            AssemblyMember(AssemblyRole.HOST_CUT, ("cut",)),
        )
        HostedAssembly(assembly_id="w", kind=AssemblyKind.WINDOW, host_object_id="wall", host_socket_id="s",
                       members=members, interface_refs=("interface:inside-to-outside",), semantic_binding_ids=("b",),
                       maturity=DetailMaturity.ENVELOPE)
        with self.assertRaises(GeometryProgramError):
            HostedAssembly(assembly_id="w", kind=AssemblyKind.WINDOW, host_object_id="wall", host_socket_id="s",
                           members=members, interface_refs=("interface:inside-to-outside",), semantic_binding_ids=("b",),
                           maturity=DetailMaturity.FUNCTIONAL)


class LiftTests(unittest.TestCase):
    def test_base_offset_rides_on_the_datum(self) -> None:
        pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 1.0)]
        self.assertEqual(lift_to_base_level(pts, {"base_level": 3.57, "base_offset": 1.53}, "op")[0][1], 5.1)
        self.assertEqual(_lift_to_base_level(tuple(pts), {"base_level": 3.57, "base_offset": 1.53}, "op")[0][1], 5.1)
        with self.assertRaises(CadTranslationError):
            lift_to_base_level(pts, {"base_offset": 1.0}, "op")
        with self.assertRaises(SandboxRealizationError):
            _lift_to_base_level(tuple(pts), {"base_offset": 1.0}, "op")
        with self.assertRaises(CadTranslationError):
            lift_to_base_level(pts, {"base_level": 1.0, "base_offset": float("nan")}, "op")


def _only(proposal, operations, assemblies):
    """Keep only the element operations: the fixture's own wall is not a box."""

    ids = tuple(sorted(o for op in operations for o in op.output_object_ids))
    binding = replace(proposal.semantic_bindings[0], object_ids=ids)
    return replace(
        proposal, operations=tuple(sorted(operations, key=lambda o: o.op_id)),
        semantic_bindings=(binding,), assemblies=tuple(sorted(assemblies, key=lambda a: a.assembly_id)),
    )


class CompileTests(unittest.TestCase):
    def _program(self, openings):
        state = _state()
        wall = solve_wall(_wall(), openings)
        types = {"window-left": WINDOW_TYPE, "window-right": WINDOW_TYPE, "door": DOOR_TYPE}
        fills = solve_openings(wall.voids, {v.opening_id: types[v.opening_id] for v in wall.voids},
                               binding_ids={v.opening_id: BINDING for v in wall.voids})
        operations = wall.operations + tuple(op for fill in fills for op in fill.operations)
        proposal = _only(_proposal(state, extra_operations=operations), operations, tuple(f.assembly for f in fills))
        bindings = wall.datum_bindings + tuple(b for fill in fills for b in fill.datum_bindings)
        result = compile_geometry_program(
            state, proposal, active_commitment_refs=(COMMITMENT,),
            interface_datums=(_level(),), datum_bindings=bindings,
        )
        return result, wall, fills

    def test_wall_with_openings_compiles_and_realizes_on_the_datum(self) -> None:
        result, wall, fills = self._program((_window(), _door(), _window("window-right", along=18.26)))
        self.assertIsNotNone(result.program, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
        program = result.program
        bounds = expected_object_bounds(program)
        cut = bounds["obj-wall-west-cut"]
        self.assertAlmostEqual(cut["bbox_min"][1], BASE)                    # the wall stands on the datum
        self.assertAlmostEqual(cut["bbox_max"][1], BASE + 9.818)            # through-cuts keep the wall's bounds
        self.assertAlmostEqual(cut["bbox_min"][0], -10.71)
        self.assertAlmostEqual(cut["bbox_max"][0], -10.29)
        sill = bounds["obj-frame-window-left-bottom"]
        self.assertAlmostEqual(sill["bbox_min"][1], BASE + 1.53)            # sill rides on the datum
        self.assertAlmostEqual(sill["bbox_min"][0], -10.81)                 # frame proud of the exterior face
        self.assertAlmostEqual(sill["bbox_max"][0], -10.63)
        pane = bounds["obj-glazing-window-left"]
        self.assertAlmostEqual(pane["bbox_min"][1], BASE + 1.62)
        self.assertAlmostEqual(pane["bbox_max"][1], BASE + 4.49)
        self.assertAlmostEqual(pane["bbox_min"][0], -10.70)
        leaf = bounds["obj-door-leaf-door-1"]
        self.assertAlmostEqual(leaf["bbox_min"][1], BASE + 0.02)
        self.assertAlmostEqual(leaf["bbox_min"][2], -10.71 + 10.71 + 0.03)
        self.assertEqual(len([a for a in program.proposal.assemblies if a.maturity is DetailMaturity.ENVELOPE]), 3)
        # uncut solid and tools are consumed: only realized objects carry bounds
        self.assertNotIn("obj-wall-west", bounds)
        self.assertNotIn("obj-wall-west-void-door", bounds)
        # the aperture volume is the opening evidence: wall ∩ tool, exactly the void
        aperture = bounds["obj-wall-west-aperture-window-left"]
        self.assertAlmostEqual(aperture["bbox_min"][0], -10.71)
        self.assertAlmostEqual(aperture["bbox_max"][0], -10.29)
        self.assertAlmostEqual(aperture["bbox_min"][1], BASE + 1.53)
        self.assertAlmostEqual(aperture["bbox_max"][1], BASE + 4.58)

    def test_cutter_that_would_remove_a_face_is_not_analytic(self) -> None:
        wall = solve_wall(_wall(), (_window(),))
        cut = next(op for op in wall.operations if op.kind is GeometryOperationKind.BOOLEAN_DIFFERENCE)
        tool = next(op for op in wall.operations if op.op_id == "wall-west-void-window-left")
        # widen the tool beyond the wall on every axis: no face survives analytically
        swaps = {
            "profile": GeometryParameter.create(
                name="profile", kind=GeometryParameterKind.POINTS3, unit=LengthUnit.METER,
                value=[[-20.0, 0.0, -20.0], [20.0, 0.0, -20.0], [20.0, 0.0, 20.0], [-20.0, 0.0, 20.0]]),
            "vector": GeometryParameter.create(
                name="vector", kind=GeometryParameterKind.VECTOR3, unit=LengthUnit.METER, value=[0.0, 30.0, 0.0]),
        }
        huge = replace(tool, parameters=tuple(swaps.get(p.name, p) for p in tool.parameters))
        state = _state()
        operations = tuple(op if op.op_id != tool.op_id else huge for op in wall.operations)
        proposal = _only(_proposal(state, extra_operations=operations), operations, ())
        result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,), interface_datums=(_level(),), datum_bindings=wall.datum_bindings)
        self.assertIsNotNone(result.program, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
        with self.assertRaises(CadTranslationError):
            expected_object_bounds(result.program)
        self.assertEqual(cut.kind, GeometryOperationKind.BOOLEAN_DIFFERENCE)


if __name__ == "__main__":
    unittest.main()
