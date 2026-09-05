"""The opening solver's window contract: one whole frame, one pane, arrayed as one object each.

A window's four bars are consumed by one ``boolean_union``; the assembly's
FRAME member names that fused frame (or its array), never a bar. A door
keeps its separate jambs and head. These are the program-level facts the
OCCT execution tests (``tests/test_occt_execution.py``) realize as solids.
"""

from __future__ import annotations

import json
import unittest

from archflow.capabilities.opening_solver import (
    DoorType,
    OpeningSolverError,
    WindowType,
    solve_door,
    solve_window,
)
from archflow.capabilities.wall_solver import OpeningKind, OpeningRequest, WallElement, solve_wall
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
