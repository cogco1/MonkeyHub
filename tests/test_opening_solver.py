"""The opening solver's window contract: one whole frame, one pane, arrayed as one object each.

A window's four bars are consumed by one ``boolean_union``; the assembly's
FRAME member names that fused frame (or its array), never a bar. A door
keeps its separate jambs and head. These are the program-level facts the
OCCT execution tests (``tests/test_occt_execution.py``) realize as solids.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from dataclasses import replace

from monkeyarch.capabilities.opening_solver import (
    DoorType,
    OpeningSolverError,
    WindowType,
    solve_door,
    solve_window,
)
from monkeyarch.capabilities.wall_solver import OpeningKind, OpeningRequest, WallElement, solve_wall
from archflow.state.geometry_program import AssemblyRole, GeometryOperationKind

WINDOW = WindowType("window-type-1", frame_width=0.09, frame_depth=0.18, frame_projection=0.1,
                    glazing_thickness=0.025, glazing_offset=0.01)
DOOR = DoorType("door-type-1", frame_width=0.09, frame_depth=0.18, frame_projection=0.1, leaf_thickness=0.04,
                leaf_offset=0.02, leaf_count=1, leaf_gap=0.0, clearance_bottom=0.01, clearance_top=0.005)


def _void(kind: OpeningKind = OpeningKind.WINDOW, *, count: int = 1, step: float = 0.0, along: float = 3.0):
    wall = WallElement("wall-south", (0.0, 0.0), (1.0, 0.0), 6.0, 0.3, 2.97, "level-ground", "world", "binding-wall")
    request = OpeningRequest("window", kind, along, 1.2, 0.9, 2.4, "binding-opening", count, step)
    (void,) = solve_wall(wall, (request,)).voids
    return void


def _params(op) -> dict:
    return {p.name: json.loads(p.value_json) for p in op.parameters}


class WholeFrameTests(unittest.TestCase):
    def test_rectangular_types_refuse_an_arched_void(self) -> None:
        for kind, solve, fill in ((OpeningKind.WINDOW, solve_window, WINDOW), (OpeningKind.DOOR, solve_door, DOOR)):
            void = replace(_void(kind), shape="semicircular_arch", spring_height=1.8)
            with self.subTest(kind=kind), self.assertRaisesRegex(OpeningSolverError, "needs a rectangular void"):
                solve(void, fill, binding_id="binding-opening")

    def test_the_frame_is_one_union_of_four_consumed_bars_and_the_pane_stays_separate(self) -> None:
        solution = solve_window(_void(), WINDOW, binding_id="binding-opening")
        by_id = {op.op_id: op for op in solution.operations}
        bars = ("frame-wall-south-window-bottom", "frame-wall-south-window-left",
                "frame-wall-south-window-right", "frame-wall-south-window-top")
        self.assertEqual(sorted(by_id), sorted([*bars, "frame-wall-south-window", "glazing-wall-south-window"]))

        frame = by_id["frame-wall-south-window"]
        self.assertIs(frame.kind, GeometryOperationKind.BOOLEAN_UNION)
        self.assertEqual(frame.input_object_ids, tuple(f"obj-{bar}" for bar in bars))
        self.assertEqual(frame.output_object_ids, ("obj-frame-wall-south-window",))
        self.assertEqual(frame.frame_id, "world")
        self.assertEqual(frame.semantic_binding_ids, ("binding-opening",))
        for bar in bars:
            self.assertIs(by_id[bar].kind, GeometryOperationKind.EXTRUSION)
        self.assertIs(by_id["glazing-wall-south-window"].kind, GeometryOperationKind.EXTRUSION)
        self.assertEqual(by_id["glazing-wall-south-window"].input_object_ids, ())

        # the bars are consumed intermediates: what is delivered is the frame and the pane
        consumed = {o for op in solution.operations for o in op.input_object_ids}
        delivered = sorted(o for op in solution.operations for o in op.output_object_ids if o not in consumed)
        self.assertEqual(delivered, ["obj-frame-wall-south-window", "obj-glazing-wall-south-window"])

        assembly = solution.assembly
        self.assertEqual(assembly.objects_for(AssemblyRole.FRAME), ("obj-frame-wall-south-window",))
        self.assertEqual(assembly.objects_for(AssemblyRole.GLAZING), ("obj-glazing-wall-south-window",))
        self.assertEqual(assembly.objects_for(AssemblyRole.HOST_CUT), ("obj-wall-south-aperture-window",))

        # every extrusion binds the storey datum; the union has no level of its own
        self.assertEqual(sorted(b.op_id for b in solution.datum_bindings), sorted([*bars, "glazing-wall-south-window"]))
        self.assertTrue(all(b.datum_id == "level-ground" and b.parameter_name == "base_level" for b in solution.datum_bindings))

    def test_the_bars_overlap_at_the_corners_so_the_union_is_one_body(self) -> None:
        solution = solve_window(_void(), WINDOW, binding_id="binding-opening")
        by_id = {op.op_id: op for op in solution.operations}
        bottom, left = _params(by_id["frame-wall-south-window-bottom"]), _params(by_id["frame-wall-south-window-left"])
        # the bottom rail spans the full width at the sill; the left stile spans the full height at the edge
        self.assertEqual(bottom["base_offset"], 0.9)
        self.assertEqual(bottom["vector"], [0.0, 0.09, 0.0])
        self.assertEqual(left["base_offset"], 0.9)
        self.assertEqual(left["vector"], [0.0, 1.5, 0.0])
        xs_bottom = sorted({p[0] for p in bottom["profile"]})
        xs_left = sorted({p[0] for p in left["profile"]})
        self.assertEqual(xs_bottom, [2.4, 3.6])
        self.assertEqual(xs_left, [2.4, 2.49])                          # inside the rail's span: they share volume
        pane = _params(by_id["glazing-wall-south-window"])
        self.assertEqual(sorted({p[0] for p in pane["profile"]}), [2.49, 3.51])
        self.assertEqual((pane["base_offset"], pane["vector"]), (0.99, [0.0, 1.32, 0.0]))


class ArrayedWindowTests(unittest.TestCase):
    def test_three_windows_array_the_whole_frame_and_the_pane_once_each(self) -> None:
        solution = solve_window(_void(count=3, step=1.8, along=1.2), WINDOW, binding_id="binding-opening")
        by_id = {op.op_id: op for op in solution.operations}
        arrays = {op_id: op for op_id, op in by_id.items() if op.kind is GeometryOperationKind.ARRAY}
        self.assertEqual(sorted(arrays), ["frame-wall-south-window-array", "glazing-wall-south-window-array"])
        frame_array = arrays["frame-wall-south-window-array"]
        self.assertEqual(frame_array.input_object_ids, ("obj-frame-wall-south-window",))
        self.assertEqual(frame_array.output_object_ids, ("obj-frame-wall-south-window-array",))
        self.assertEqual(_params(frame_array), {"count": 3, "step": [1.8, 0.0, 0.0]})
        self.assertEqual(_params(arrays["glazing-wall-south-window-array"]), {"count": 3, "step": [1.8, 0.0, 0.0]})
        # the bars are never arrayed: only the fused frame and the pane are placed
        self.assertNotIn("frame-wall-south-window-bottom-array", by_id)

        assembly = solution.assembly
        self.assertEqual(assembly.objects_for(AssemblyRole.FRAME), ("obj-frame-wall-south-window-array",))
        self.assertEqual(assembly.objects_for(AssemblyRole.GLAZING), ("obj-glazing-wall-south-window-array",))
        self.assertEqual(
            assembly.objects_for(AssemblyRole.HOST_CUT),
            ("obj-wall-south-aperture-window-0", "obj-wall-south-aperture-window-1", "obj-wall-south-aperture-window-2"),
        )
        consumed = {o for op in solution.operations for o in op.input_object_ids}
        delivered = sorted(o for op in solution.operations for o in op.output_object_ids if o not in consumed)
        self.assertEqual(delivered, ["obj-frame-wall-south-window-array", "obj-glazing-wall-south-window-array"])

    def test_a_single_window_has_no_array(self) -> None:
        solution = solve_window(_void(), WINDOW, binding_id="binding-opening")
        self.assertFalse(any(op.kind is GeometryOperationKind.ARRAY for op in solution.operations))


class ContractTests(unittest.TestCase):
    def test_a_door_keeps_its_separate_jambs_and_head(self) -> None:
        solution = solve_door(_void(OpeningKind.DOOR), DOOR, binding_id="binding-opening")
        self.assertFalse(any(op.kind is GeometryOperationKind.BOOLEAN_UNION for op in solution.operations))
        self.assertEqual(
            solution.assembly.objects_for(AssemblyRole.FRAME),
            ("obj-door-frame-wall-south-window-left", "obj-door-frame-wall-south-window-right", "obj-door-frame-wall-south-window-top"),
        )

    def test_door_jambs_stop_under_the_head_without_changing_the_leaf_opening(self) -> None:
        void = _void(OpeningKind.DOOR)
        for leaf_count in (1, 2):
            door = replace(DOOR, frame_width=0.0381, frame_depth=0.1524,
                           leaf_count=leaf_count, leaf_gap=0.003)
            with self.subTest(leaf_count=leaf_count):
                solution = solve_door(void, door, binding_id="binding-opening")
                by_id = {op.output_object_ids[0]: _params(op) for op in solution.operations}
                left, right, top = (by_id[oid] for oid in solution.assembly.objects_for(AssemblyRole.FRAME))
                for jamb in (left, right):
                    self.assertAlmostEqual(jamb["base_offset"], void.sill)
                    self.assertAlmostEqual(jamb["base_offset"] + jamb["vector"][1], top["base_offset"])
                self.assertEqual(sorted({p[0] for p in top["profile"]}), [void.along0, void.along1])
                self.assertAlmostEqual(top["base_offset"] + top["vector"][1], void.head)
                leaves = [by_id[oid] for oid in solution.assembly.objects_for(AssemblyRole.LEAF)]
                self.assertEqual(len(leaves), leaf_count)
                for leaf in leaves:
                    self.assertAlmostEqual(leaf["base_offset"], void.sill + door.clearance_bottom)
                    self.assertAlmostEqual(leaf["base_offset"] + leaf["vector"][1],
                                           void.head - door.frame_width - door.clearance_top)
                spans = [sorted({p[0] for p in leaf["profile"]}) for leaf in leaves]
                self.assertAlmostEqual(spans[0][0], void.along0 + door.frame_width)
                self.assertAlmostEqual(spans[-1][-1], void.along1 - door.frame_width)
                if leaf_count == 2:
                    self.assertAlmostEqual(spans[1][0] - spans[0][-1], 2 * door.leaf_gap)

    @unittest.skipUnless(importlib.util.find_spec("OCP"), "cadquery-ocp is not installed")
    def test_real_door_frame_solids_touch_without_overlap_in_single_and_arrayed_openings(self) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer

        from archflow.adapters.occt_backend import build_program_shapes, measure_shape
        from monkeyarch.compilers.geometry import compile_geometry_program
        from archflow.state.geometry_program import InterfaceDatum, InterfaceDatumKind, LengthUnit
        from tests.test_geometry_compiler import COMMITMENT, _only, _proposal, _state

        for leaf_count in (1, 2):
            for count in (1, 3):
                with self.subTest(leaf_count=leaf_count, count=count):
                    void = _void(OpeningKind.DOOR, count=count, step=1.8, along=1.2)
                    if count > 1:
                        void = replace(void, wall=replace(void.wall, origin=(2.0, -3.0), direction=(0.6, 0.8)))
                    door = replace(DOOR, frame_width=0.0381, frame_depth=0.1524,
                                   leaf_count=leaf_count, leaf_gap=0.003)
                    solution = solve_door(void, door, binding_id="building-binding")
                    state = _state()
                    datum = InterfaceDatum.create(datum_id="level-ground", kind=InterfaceDatumKind.LEVEL,
                                                  published_by="building", value=0.6, unit=LengthUnit.METER)
                    result = compile_geometry_program(
                        state, _only(_proposal(state), solution.operations, ()),
                        active_commitment_refs=(COMMITMENT,), interface_datums=(datum,),
                        datum_bindings=solution.datum_bindings,
                    )
                    self.assertIsNotNone(result.program, result.receipt.issues)
                    built = build_program_shapes(result.program)
                    frame_ids = solution.assembly.objects_for(AssemblyRole.FRAME)
                    self.assertEqual(set(built.physical_object_ids),
                                     set(frame_ids + solution.assembly.objects_for(AssemblyRole.LEAF)))
                    shapes = [built.objects[oid].shape for oid in frame_ids]
                    instances = []
                    for shape in shapes:
                        measured = measure_shape(shape)
                        self.assertTrue(measured.valid)
                        self.assertEqual(measured.solid_count, count)
                        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
                        solids = []
                        while explorer.More():
                            solids.append(explorer.Current())
                            explorer.Next()
                        instances.append(sorted(solids, key=lambda solid: measure_shape(solid).bbox_min))
                    for jambs in instances[:2]:
                        for jamb, head in zip(jambs, instances[2], strict=True):
                            common = BRepAlgoAPI_Common(jamb, head)
                            common.Build()
                            self.assertTrue(common.IsDone())
                            volume = GProp_GProps()
                            BRepGProp.VolumeProperties_s(common.Shape(), volume)
                            self.assertAlmostEqual(volume.Mass(), 0.0, delta=1e-12)
                            contact = BRepExtrema_DistShapeShape(jamb, head)
                            contact.Perform()
                            self.assertTrue(contact.IsDone())
                            self.assertAlmostEqual(contact.Value(), 0.0, delta=1e-8)
                    expected = count * door.frame_depth * door.frame_width * (
                        2 * (void.height - door.frame_width) + void.width
                    )
                    self.assertAlmostEqual(sum(measure_shape(shape).volume for shape in shapes), expected, places=9)

    def test_a_frame_that_does_not_fit_the_void_is_refused(self) -> None:
        wide = WindowType("window-wide", frame_width=0.7, frame_depth=0.18, frame_projection=0.0,
                          glazing_thickness=0.025, glazing_offset=0.01)
        with self.assertRaisesRegex(OpeningSolverError, "does not fit"):
            solve_window(_void(), wide, binding_id="binding-opening")

    def test_a_door_void_is_not_a_window(self) -> None:
        with self.assertRaisesRegex(OpeningSolverError, "not a window"):
            solve_window(_void(OpeningKind.DOOR), WINDOW, binding_id="binding-opening")


if __name__ == "__main__":
    unittest.main()
