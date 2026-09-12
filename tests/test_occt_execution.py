"""In-process OCCT execution of compiled programs (P107, lane A).

The same ``CompiledGeometryProgram`` the Rhino path scripts is realized here
in process: a State Record's rows go through the existing producers and the
compiler, the program is persisted through P036 and bound exactly as the
runner binds it, and ``execute_occt_export`` writes one STEP file and one
mesh ``.3dm`` preview from one model.  Every assertion below reads the
written files back independently; no Rhino and no PowerShell is ever
started, and the tests refuse any attempt to.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archflow.adapters import cad_execution, occt_backend
from archflow.adapters.cad_execution import (
    CadCapabilityError,
    CadExecutionError,
    CadExecutionStatus,
    CadProgramBinding,
    OcctExecutionReceipt,
    RhinoCadProgramBinding,
    StepImportSource,
    execute_occt_export,
    prepare_rhino_three_dm_export,
    project_occt_lines,
    section_occt_lines,
    section_occt_regions,
    split_step_objects,
)
from archflow.adapters import cad_program
from archflow.adapters.cad_program import CadTranslationError
from archflow.adapters.three_dm_inspector import inspect_three_dm
from monkeyarch.capabilities.element_producers import ProductionContext, element_rows_of, produce_rows
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from archflow.state.geometry_program import CompiledGeometryObject, CompiledGeometryProgram
from monkeyarch.compilers.geometry import compile_geometry_program
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import stage_geometry_program
from archflow.project.refs import BranchRef
from archflow.state.geometry_program import (
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramError,
    LengthUnit,
)
from archflow.state.state_record import StateRecord, project_grids_of, project_levels_of
from tests.support import EVIDENCE, RECORD_PAYLOAD, authored_record, shared_bound_state
from tests.test_cad_execution import _binding as _synthetic_binding, _program as _synthetic_program
from tests.test_geometry_compiler import COMMITMENT, _only, _proposal, _state

OCCT_AVAILABLE = occt_backend.occt_available()
NEEDS_OCCT = unittest.skipUnless(
    OCCT_AVAILABLE, "cadquery-ocp is not installed: python -m pip install -e '.[cad-occt]'"
)
PYTHON = sys.executable


# ---------------------------------------------------------------- a record, produced and compiled


def _stair_section(z: float) -> list[list[float]]:
    """One closed eight-vertex stair outline: 0.9 m long, three 0.2 m steps to 0.6 m, at plan offset ``z``."""

    return [
        [0.0, 0.0, z], [0.9, 0.0, z], [0.9, 0.6, z], [0.6, 0.6, z],
        [0.6, 0.4, z], [0.3, 0.4, z], [0.3, 0.2, z], [0.0, 0.2, z],
    ]


STAIR_ELEMENT = {
    "entity_id": "stair-east",
    "schema": "Element@1",
    "parent_id": "primary-support",
    "fields": {
        "component_id": "primary-support",
        "producer": "loft",
        "references": {"base": {"level": "level-ground"}},
        "params": {"profiles": [_stair_section(-0.6), _stair_section(0.6)], "profile_size": 8},
    },
    "basis_refs": [EVIDENCE],
}


def _ring(radius: float, y: float, n: int = 8) -> list[list[float]]:
    """One closed ring section of ``n`` points at height ``y`` over the base datum."""

    return [[round(radius * math.cos(2 * math.pi * j / n), 9), y, round(radius * math.sin(2 * math.pi * j / n), 9)] for j in range(n)]


DRUM_ELEMENT = {
    "entity_id": "drum-east",
    "schema": "Element@1",
    "parent_id": "primary-support",
    "fields": {
        "component_id": "primary-support",
        "producer": "loft",
        "references": {"base": {"level": "level-ground"}},
        "params": {"profiles": [_ring(1.0, 0.0), _ring(1.0, 1.0)], "profile_size": 8, "cap_ends": False},
    },
    "basis_refs": [EVIDENCE],
}


def _stair_record() -> StateRecord:
    """The fixture record with its plinth and wall replaced by one lofted flight."""

    return _record_with(STAIR_ELEMENT)


def _record_with(element: dict) -> StateRecord:
    """The fixture record with its plinth and wall replaced by one element row."""

    payload = json.loads(json.dumps(RECORD_PAYLOAD))
    payload["entities"] = [
        entity for entity in payload["entities"] if entity["entity_id"] not in ("plinth", "wall-south")
    ] + [element]
    payload["relations"] = [
        relation for relation in payload["relations"] if relation["relation_id"] != "plinth-supports-wall-south"
    ]
    return StateRecord.from_dict(payload)


def _compile(record: StateRecord) -> CompiledGeometryProgram:
    """Producers, then the compiler, exactly as the authored-record tests do it.

    The producers' hosted assemblies travel into the proposal as the runner
    carries them, re-homed onto the fixture's one semantic binding.
    """

    levels = project_levels_of(record)
    context = ProductionContext(
        references=ReferenceContext(grids=project_grids_of(record), levels=levels), published={}, frame_id="world"
    )
    produced = produce_rows(element_rows_of(record), context)
    state = _state()
    operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for element in produced for op in element.operations)
    assemblies = tuple(replace(a, semantic_binding_ids=("building-binding",)) for element in produced for a in element.assemblies)
    datums = tuple(sorted(list(context.published.values()) + list(levels.datums()), key=lambda d: d.datum_id))
    result = compile_geometry_program(
        state,
        _only(_proposal(state, extra_operations=operations), operations, assemblies),
        active_commitment_refs=(COMMITMENT,),
        interface_datums=datums,
        datum_bindings=tuple(b for element in produced for b in element.bindings),
    )
    if result.program is None:
        raise AssertionError([(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
    return result.program


@NEEDS_OCCT
class OcctDrawingTests(unittest.TestCase):
    """Draw exact geometry cold-read from STEP: real curves and a real through hole,
    occlusion, identity, depth range and cut sections."""

    def setUp(self) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "drawing-source.step"
        panel = BRepPrimAPI_MakeBox(gp_Pnt(2, 3, 4), 2, 4, 3).Shape()
        hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(1, 5, 5.5), gp_Dir(1, 0, 0)), 0.4, 4).Shape()
        panel = BRepAlgoAPI_Cut(panel, hole).Shape()
        cover = BRepPrimAPI_MakeBox(gp_Pnt(0.5, 4.5, 5.0), 0.5, 1, 1).Shape()
        occt_backend.write_step(self.path, (
            occt_backend.StepObject("panel", panel, "panels"),
            occt_backend.StepObject("cover", cover, "panels"),
        ), length_unit="meter")
        self.entries = occt_backend.read_step(self.path, length_unit="meter")
        # Looking along +X from x = 0: right is -Y, up is +Z, right x up = -X faces the viewer.
        self.frame = dict(origin=(0, 3, 4), right=(0, -1, 0), up=(0, 0, 1), linear_deflection=0.0001)

    def assert_circle(self, lines, *, center=(-2, 1.5), radius=0.4, deflection=0.0001):
        curves = [line for line in lines if len(line.points) > 2]
        self.assertTrue(curves, "the circular hole must not become four bounding-box edges")
        for line in curves:
            for x, y in line.points:
                self.assertAlmostEqual(math.hypot(x - center[0], y - center[1]), radius, places=7)
            for a, b in zip(line.points, line.points[1:]):
                midpoint = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                deviation = radius - math.hypot(midpoint[0] - center[0], midpoint[1] - center[1])
                self.assertLessEqual(deviation, deflection * 1.00001)

    def test_projection_retains_hole_curves_and_declared_camera_coordinates(self) -> None:
        lines = project_occt_lines(self.entries, object_ids=("panel",), **self.frame)
        self.assertEqual({line.object_id for line in lines}, {"panel"})
        visible = [line for line in lines if line.kind == "visible"]
        self.assert_circle(visible)
        self.assertEqual(min(x for line in visible for x, _ in line.points), -4.0)
        self.assertEqual(max(y for line in visible for _, y in line.points), 3.0)
        rotated = project_occt_lines(self.entries, object_ids=("panel",), **{
            **self.frame, "origin": (0, 2, 3), "right": (0, 0, 1), "up": (0, 1, 0),
        })
        self.assert_circle([line for line in rotated if line.kind == "visible"], center=(2.5, 3.0))

    def test_selected_objects_occlude_each_other_and_keep_their_identity(self) -> None:
        lines = project_occt_lines(self.entries, object_ids=("panel", "cover"), **self.frame)
        self.assertEqual({line.object_id for line in lines}, {"panel", "cover"})
        panel = [line for line in lines if line.object_id == "panel"]
        self.assert_circle([line for line in panel if line.kind == "hidden"])
        self.assertFalse(any(len(line.points) > 2 for line in panel if line.kind == "visible"))
        self.assertTrue(any(line.kind == "visible" for line in lines if line.object_id == "cover"))
        self.assertEqual(lines, project_occt_lines(tuple(reversed(self.entries)),
                                                  object_ids=("cover", "panel"), **self.frame))

    def test_depth_range_excludes_shapes_beyond_it_and_cuts_crossing_shapes_exactly(self) -> None:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

        rotation = gp_Trsf()
        rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.pi / 4)
        diamond = BRepBuilderAPI_Transform(BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, 0), 2, 2, 2).Shape(), rotation, True).Shape()
        screen = BRepPrimAPI_MakeBox(gp_Pnt(-3, -3, 0), 6, 1, 3).Shape()
        entries = (occt_backend.StepEntry("diamond", (), None, diamond), occt_backend.StepEntry("screen", (), None, screen))
        # From y = -5 looking along +Y: the screen sits at depth 2..3, the diamond's centre at depth 5, its tips at 5 +- sqrt(2).
        frame = dict(origin=(0, -5, 0), right=(1, 0, 0), up=(0, 0, 1), linear_deflection=0.0001)
        ids = ("diamond", "screen")

        def widest_visible(lines, name):
            return max(abs(x) for line in lines if line.object_id == name and line.kind == "visible" for x, _ in line.points)

        full = project_occt_lines(entries, object_ids=ids, **frame)
        self.assertEqual(full, project_occt_lines(entries, object_ids=ids, depth_range=(0, 10), **frame))
        self.assertFalse(any(line.object_id == "diamond" and line.kind == "visible" for line in full),
                         "the screen hides the whole diamond at full depth")
        behind_screen = project_occt_lines(entries, object_ids=ids, depth_range=(3.5, 10), **frame)
        self.assertEqual({line.object_id for line in behind_screen}, {"diamond"})
        self.assertAlmostEqual(widest_visible(behind_screen, "diamond"), math.sqrt(2), places=6)
        cut = project_occt_lines(entries, object_ids=ids, depth_range=(3.5, 4.5), **frame)
        self.assertEqual({line.object_id for line in cut}, {"diamond"})
        self.assertAlmostEqual(widest_visible(cut, "diamond"), math.sqrt(2) - 0.5, places=6)
        self.assertEqual(project_occt_lines(entries, object_ids=ids, depth_range=(7, 10), **frame), ())

    def test_section_intersects_the_hole_in_the_same_drawing_frame(self) -> None:
        lines = section_occt_lines(self.entries, object_ids=("panel",), **{**self.frame, "origin": (3, 3, 4)})
        self.assertEqual({(line.object_id, line.kind) for line in lines}, {("panel", "section")})
        self.assert_circle(lines)
        self.assertEqual(min(x for line in lines for x, _ in line.points), -4.0)
        self.assertEqual(max(y for line in lines for _, y in line.points), 3.0)
        self.assertEqual(section_occt_lines(self.entries, object_ids=("panel",),
                                            **{**self.frame, "origin": (10, 3, 4)}), ())

    @staticmethod
    def region_contains(region, point):
        x, y = point
        inside = False
        for loop in region.loops:
            for (ax, ay), (bx, by) in zip(loop, loop[1:]):
                if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
                    inside = not inside
        return inside

    def test_section_regions_retain_real_hole_and_camera_coordinates(self) -> None:
        for frame, center, material in (
            ({**self.frame, "origin": (3, 3, 4)}, (-2, 1.5), (-0.5, 0.5)),
            ({**self.frame, "origin": (3, 2, 3), "right": (0, 0, 1), "up": (0, 1, 0)},
             (2.5, 3.0), (1.5, 1.5)),
        ):
            with self.subTest(frame=frame):
                (region,) = section_occt_regions(self.entries, object_ids=("panel",), **frame)
                self.assertEqual(region.object_id, "panel")
                self.assertEqual(len(region.loops), 2)
                self.assertTrue(all(loop[0] == loop[-1] for loop in region.loops))
                curves = [occt_backend.OcctDrawingPolyline("panel", "section", loop)
                          for loop in region.loops if len(loop) > 5]
                self.assert_circle(curves, center=center)
                self.assertTrue(self.region_contains(region, material))
                self.assertFalse(self.region_contains(region, center), "the through hole must stay unfilled")
                self.assertFalse(self.region_contains(region, (20, 20)))
        self.assertEqual(section_occt_regions(self.entries, object_ids=("panel",),
                                              **{**self.frame, "origin": (10, 3, 4)}), ())

    def test_section_regions_preserve_disconnected_cuts_of_one_solid(self) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 4, 2, 4).Shape()
        notch = BRepPrimAPI_MakeBox(gp_Pnt(1, -1, 1), 2, 4, 4).Shape()
        fork = BRepAlgoAPI_Cut(block, notch).Shape()
        occt_backend.write_step(self.path, (occt_backend.StepObject("fork", fork, "panels"),),
                                length_unit="meter")
        entries = occt_backend.read_step(self.path, length_unit="meter")
        (region,) = section_occt_regions(entries, object_ids=("fork",), origin=(0, 0, 2),
                                        right=(1, 0, 0), up=(0, 1, 0), linear_deflection=0.0001)
        self.assertEqual(len(region.loops), 2)
        self.assertTrue(all(loop[0] == loop[-1] for loop in region.loops))
        self.assertTrue(self.region_contains(region, (0.5, 1)))
        self.assertTrue(self.region_contains(region, (3.5, 1)))
        self.assertFalse(self.region_contains(region, (2, 1)), "separate legs must not gain a filled bridge")

    def test_section_regions_do_not_turn_open_shells_into_material(self) -> None:
        from OCP.BRep import BRep_Builder
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.TopoDS import TopoDS_Shell

        builder = BRep_Builder()
        shell = TopoDS_Shell()
        builder.MakeShell(shell)
        builder.Add(shell, BRepPrimAPI_MakeCylinder(1, 3).Face())
        entries = (occt_backend.StepEntry("skin", (), None, shell),)
        frame = dict(origin=(0, 0, 1), right=(1, 0, 0), up=(0, 1, 0), linear_deflection=0.0001)
        self.assertTrue(section_occt_lines(entries, object_ids=("skin",), **frame))
        self.assertEqual(section_occt_regions(entries, object_ids=("skin",), **frame), ())

    def test_compound_solids_remain_separate_even_odd_regions(self) -> None:
        from OCP.BRep import BRep_Builder
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.TopoDS import TopoDS_Compound
        from OCP.gp import gp_Pnt

        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        for start in (0, 1):
            builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(start, 0, 0), 2, 2, 2).Shape())
        entries = (occt_backend.StepEntry("pair", (), None, compound),)
        regions = section_occt_regions(entries, object_ids=("pair",), origin=(0, 0, 1),
                                       right=(1, 0, 0), up=(0, 1, 0), linear_deflection=0.0001)
        self.assertEqual(len(regions), 2)
        self.assertEqual({region.object_id for region in regions}, {"pair"})
        self.assertTrue(all(self.region_contains(region, (1.5, 1)) for region in regions))

    def test_drawing_coordinates_and_deflection_use_the_step_read_unit(self) -> None:
        millimeters = occt_backend.read_step(self.path, length_unit="millimeter")
        frame = dict(origin=(3000, 3000, 4000), right=(0, -1, 0), up=(0, 0, 1), linear_deflection=0.1)
        for operation in (project_occt_lines, section_occt_lines):
            with self.subTest(operation=operation.__name__):
                lines = operation(millimeters, object_ids=("panel",), **frame)
                self.assert_circle(lines, center=(-2000, 1500), radius=400, deflection=0.1)
                self.assertEqual(min(x for line in lines for x, _ in line.points), -4000.0)
        (region,) = section_occt_regions(millimeters, object_ids=("panel",), **frame)
        curves = [occt_backend.OcctDrawingPolyline("panel", "section", loop)
                  for loop in region.loops if len(loop) > 5]
        self.assert_circle(curves, center=(-2000, 1500), radius=400, deflection=0.1)
        self.assertEqual(min(x for loop in region.loops for x, _ in loop), -4000.0)
        self.assertFalse(self.region_contains(region, (-2000, 1500)))
        self.assertTrue(self.region_contains(region, (-500, 500)))

    def test_unknown_ambiguous_and_empty_selections_and_invalid_frames_are_refused(self) -> None:
        from OCP.TopoDS import TopoDS_Shape

        panel = next(entry for entry in self.entries if entry.name == "panel")
        for operation in (project_occt_lines, section_occt_lines, section_occt_regions):
            for entries, ids, frame in (
                (self.entries, (), self.frame),
                (self.entries, ("missing",), self.frame),
                (self.entries, ("panel", "panel"), self.frame),
                ((panel, panel), ("panel",), self.frame),
                ((replace(panel, shape=TopoDS_Shape()),), ("panel",), self.frame),
                (self.entries, ("panel",), {**self.frame, "right": (0, -2, 0)}),
                (self.entries, ("panel",), {**self.frame, "up": (0, -1, 0)}),
                (self.entries, ("panel",), {**self.frame, "origin": (float("nan"), 0, 0)}),
                (self.entries, ("panel",), {**self.frame, "linear_deflection": 0}),
            ):
                with self.subTest(operation=operation.__name__, ids=ids, frame=frame), self.assertRaises(occt_backend.OcctBackendError):
                    operation(entries, object_ids=ids, **frame)
        # The depth range belongs to the projection, which is the entry point
        # that takes one; a section is cut at a plane and has no slab.
        for frame in ({**self.frame, "depth_range": (2, 1)},
                      {**self.frame, "depth_range": (0, float("inf"))}):
            with self.subTest(frame=frame), self.assertRaises(occt_backend.OcctBackendError):
                project_occt_lines(self.entries, object_ids=("panel",), **frame)


@NEEDS_OCCT
class OpenLoftExecutionTests(unittest.TestCase):
    """A loft row with ``cap_ends: false`` is delivered as the lofted surface, open at both rings, with no volume claimed.

    The drum and the shallow dome the source gives as surfaces without a
    thickness travel this way: the producer forwards the row's word, the
    kernel lofts uncapped, the STEP cold read finds an open shell, and the
    preview is the same shape tessellated.
    """

    def test_an_uncapped_ring_loft_is_an_open_surface_in_step_and_preview(self) -> None:
        program = _compile(_record_with(DRUM_ELEMENT))
        loft = next(op for op in program.proposal.operations if op.op_id == "drum-east")
        params = {p.name: json.loads(p.value_json) for p in loft.parameters}
        self.assertEqual((params["cap_ends"], params["profile_basis"], params["profile_size"], len(params["profiles"])), (False, "polyline", 8, 16))
        binding = _persisted_binding(program, "stage-occt-drum")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, binding, workspace, "drum@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.physical_object_ids, ("obj-drum-east",))
            self.assertEqual(receipt.exact_artifact["deliveries"], {"obj-drum-east": "open_surface"})
            self.assertEqual(receipt.exact_artifact["carries"][2],
                             "exact B-rep in the CAD frame and the program unit: 0 closed solid object(s), 1 open surface object(s) from planar faces or uncapped lofts")
            self.assertEqual(receipt.expected_bounds["obj-drum-east"], {"min": [-1.0, -1.0, 0.0], "max": [1.0, 1.0, 1.0]})

            # the exact delivery: an open shell of eight ruled faces, sixteen free edges (two open rings), no solid, no volume
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            entry = entries["obj-drum-east"]
            self.assertEqual(entry.layers, ("archflow::building",))
            measure = occt_backend.measure_shape(entry.shape)
            self.assertEqual((measure.valid, measure.solid_count, measure.closed, measure.face_count, measure.free_edge_count), (True, 0, False, 8, 16))
            self.assertIsNone(measure.volume)
            _assert_bbox(self, measure, (-1.0, -1.0, 0.0), (1.0, 1.0, 1.0), places=6)
            row = receipt.readback["obj-drum-east"]
            self.assertEqual((row["solid_count"], row["closed"], row["valid"], row["free_edge_count"], row["volume"], row["declared_delivery"]),
                             (0, False, True, 16, None, "open_surface"))

            # the preview: the same open shape as a mesh, on the STEP bounds, with the viewer's semantics
            inspection = inspect_three_dm(workspace / receipt.preview_artifact["relative_path"])
            self.assertEqual(inspection.top_level_object_count, 1)
            (named,) = inspection.named_object_bboxes
            self.assertEqual((named["name"], named["type"]), ("obj-drum-east", "Mesh"))
            self.assertEqual(receipt.preview_artifact["mesh_counts"]["obj-drum-east"]["mesh_face_count"], 16)     # eight ruled quads
            for axis in range(3):
                self.assertAlmostEqual(named["bbox"]["min"][axis], row["bbox"]["min"][axis], places=3)
                self.assertAlmostEqual(named["bbox"]["max"][axis], row["bbox"]["max"][axis], places=3)

    def test_a_shape_of_the_other_closure_fails_the_readback_in_either_direction(self) -> None:
        """A declared solid that reads back open, and a declared surface that reads back closed, are both refused by name."""

        from archflow.adapters.cad_execution import _verify_step_readback

        drum, flight = _compile(_record_with(DRUM_ELEMENT)), _compile(_stair_record())
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            open_receipt, _ = _execute(drum, _persisted_binding(drum, "stage-occt-drum-swap"), workspace, "drum@swap", preview=False)
            solid_receipt, _ = _execute(flight, _persisted_binding(flight, "stage-occt-stair-swap"), workspace, "stair@swap", preview=False)
            self.assertIs(open_receipt.status, CadExecutionStatus.SUCCEEDED, open_receipt.failures)
            self.assertIs(solid_receipt.status, CadExecutionStatus.SUCCEEDED, solid_receipt.failures)
            for receipt, object_id, declared, codes in (
                (open_receipt, "obj-drum-east", "closed_solid", {"cad_execution.step_solid_count_mismatch", "cad_execution.step_not_closed_solid"}),
                (solid_receipt, "obj-stair-east", "open_surface", {"cad_execution.step_not_open_surface"}),
            ):
                with self.subTest(declared=declared):
                    entries = occt_backend.read_step(workspace / receipt.exact_artifact["relative_path"], length_unit="meter")
                    readback, failures = _verify_step_readback(
                        entries, physical=(object_id,), semantics=receipt.expected_semantics, expected_bounds=receipt.expected_bounds,
                        expected_counts={object_id: 1}, expected_deliveries={object_id: declared}, layer_colors={}, tolerance=0.003,
                    )
                    self.assertEqual({f["code"] for f in failures}, codes, failures)
                    self.assertEqual(readback[object_id]["declared_delivery"], declared)
                    _, agreed = _verify_step_readback(
                        entries, physical=(object_id,), semantics=receipt.expected_semantics, expected_bounds=receipt.expected_bounds,
                        expected_counts={object_id: 1}, expected_deliveries=receipt.exact_artifact["deliveries"], layer_colors={}, tolerance=0.003,
                    )
                    self.assertEqual(agreed, [])


@NEEDS_OCCT
class NativeLoftHeightExecutionTests(unittest.TestCase):
    """Native section heights survive datum placement in the exact exported solid."""

    DATUM = 2.25

    def _record(self, profiles):
        element = {**DRUM_ELEMENT, "fields": {**DRUM_ELEMENT["fields"], "params": {
            "profiles": profiles, "profile_size": len(profiles[0]), "cap_ends": True,
        }}}
        record = _record_with(element)
        return replace(record, entities=tuple(replace(entity, fields={**entity.fields, "elevation": self.DATUM})
                                              if entity.entity_id == "level-ground" else entity for entity in record.entities))

    def test_positive_zero_and_negative_section_heights_are_relative_to_the_nonzero_datum(self) -> None:
        for bottom in (-0.4, 0.0, 0.4):
            with self.subTest(bottom=bottom), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp).resolve()
                program = _compile(self._record([_ring(1.0, bottom), _ring(1.0, bottom + 1.0)]))
                receipt, _ = _execute(program, _persisted_binding(program, "stage-native-height"), workspace, "native-height@occt")
                self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
                entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
                body = occt_backend.measure_shape(entries["obj-drum-east"].shape)
                self.assertEqual((body.valid, body.closed, body.solid_count), (True, True, 1))
                _assert_bbox(self, body, (-1.0, -1.0, self.DATUM + bottom), (1.0, 1.0, self.DATUM + bottom + 1.0), places=6)
                self.assertTrue(receipt.readback_verified)

    def test_a_vertical_circular_section_keeps_its_authored_center_height(self) -> None:
        center, radius = 1.35, 0.5
        profiles = [[[radius * math.cos(2.0 * math.pi * index / 16), center + radius * math.sin(2.0 * math.pi * index / 16), depth]
                     for index in range(16)] for depth in (-0.1, 0.1)]
        program = _compile(self._record(profiles))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _persisted_binding(program, "stage-native-circle-height"), workspace, "circle-height@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            body = occt_backend.measure_shape(entries["obj-drum-east"].shape)
            self.assertEqual((body.valid, body.closed, body.solid_count), (True, True, 1))
            _assert_bbox(self, body, (-radius, -0.1, self.DATUM + center - radius), (radius, 0.1, self.DATUM + center + radius), places=6)
            self.assertAlmostEqual((body.bbox_min[2] + body.bbox_max[2]) / 2.0, self.DATUM + center, places=8)
            self.assertEqual(occt_backend.classify_program_point(entries["obj-drum-east"].shape, (0.0, self.DATUM + center, 0.0)), "inside")


WINDOW_TYPE = {"schema": "WindowType@1", "type_id": "window-type-1", "frame_width": 0.09, "frame_depth": 0.18,
               "frame_projection": 0.1, "glazing_thickness": 0.025, "glazing_offset": 0.01}


def _window_record(*, count: int = 1, step: float = 0.0, along: float = 3.0) -> StateRecord:
    """The fixture record with its south window typed (and, with ``count``, repeated along the wall)."""

    payload = json.loads(json.dumps(RECORD_PAYLOAD))
    wall = next(entity for entity in payload["entities"] if entity["entity_id"] == "wall-south")
    wall["fields"]["params"]["types"] = [WINDOW_TYPE]
    opening = wall["fields"]["params"]["openings"][0]
    opening.update({"type_id": "window-type-1", "along": along})
    if count > 1:
        opening.update({"count": count, "step": step})
    return StateRecord.from_dict(payload)


def _persisted_binding(program: CompiledGeometryProgram, stage_id: str) -> RhinoCadProgramBinding:
    """Persist the compiled program through P036 first and bind to that record, as the runner does."""

    repository, run, _, _ = shared_bound_state()
    branch = BranchRef(run=run, branch_id="runner-v1", epoch=1)
    destination = PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=run.run_id, branch_id=branch.branch_id)
    program_ref = repository.put_json(
        run=run, destination=destination, record_kind=stage_geometry_program(stage_id), payload=program.to_dict()
    )
    return CadProgramBinding(
        program_ref=program_ref,
        branch=branch,
        stage_id=stage_id,
        program_digest=program.program_digest,
        design_state_digest=program.proposal.design_state_digest,
        predecessor_program_digest=None,
    )


def _no_process():
    """Any attempt to start a process (Rhino, PowerShell) fails the test."""

    return patch.multiple(subprocess, Popen=_refuse_process, run=_refuse_process)


def _refuse_process(*args, **kwargs):
    raise AssertionError(f"the OCCT executor must not start a process: {args[:1]}")


def _execute(program, binding, workspace: Path, stem: str, **options) -> tuple[OcctExecutionReceipt, float]:
    with _no_process():
        started = time.perf_counter()
        receipt = execute_occt_export(
            program,
            binding=binding,
            speculative_workspace=workspace,
            artifact_stem=stem,
            provenance={"export_path": "occt-test"},
            **options,
        )
        return receipt, time.perf_counter() - started


def _entries_by_name(step: Path) -> dict[str, occt_backend.StepEntry]:
    entries = occt_backend.read_step(step, length_unit="meter")
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise AssertionError(f"duplicate names in STEP: {names}")
    return {entry.name: entry for entry in entries}


def _assert_bbox(case: unittest.TestCase, measure: occt_backend.ShapeMeasure, low, high, places: int = 5) -> None:
    for actual, expected in zip(measure.bbox_min, low):
        case.assertAlmostEqual(actual, expected, places=places)
    for actual, expected in zip(measure.bbox_max, high):
        case.assertAlmostEqual(actual, expected, places=places)


# ---------------------------------------------------------------- acceptance


@NEEDS_OCCT
class PlanarSurfaceExecutionTests(unittest.TestCase):
    def test_surface_retains_its_outline_and_level_without_solid_or_volume(self) -> None:
        # Concave outline, intentionally no construction thickness. Moving
        # its datum is a revision of the same visible surface.
        element = {
            "entity_id": "ceiling", "schema": "Element@1", "parent_id": "primary-support",
            "fields": {"component_id": "primary-support", "producer": "planar-surface",
                       "references": {"base": {"offset_from": {"level": "level-ground", "offset": 0.2}}},
                       "params": {"elevation": 2.5, "profile": [[0, 0], [3, 0], [3, 1], [1, 1], [1, 2], [0, 2], [0, 0]]}},
            "basis_refs": [EVIDENCE],
        }
        for elevation in (2.7, 3.1):
            with self.subTest(elevation=elevation), tempfile.TemporaryDirectory() as tmp:
                current = json.loads(json.dumps(element))
                current["fields"]["params"]["elevation"] = elevation - 0.2
                program = _compile(_record_with(current))
                workspace = Path(tmp).resolve()
                receipt, _ = _execute(program, _persisted_binding(program, "stage-planar-surface"), workspace, "surface")
                self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
                self.assertEqual(receipt.exact_artifact["deliveries"], {"obj-ceiling": "open_surface"})
                self.assertEqual(receipt.expected_bounds["obj-ceiling"], {"min": [0.0, 0.0, elevation], "max": [3.0, 2.0, elevation]})
                entry = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])["obj-ceiling"]
                measure = occt_backend.measure_shape(entry.shape)
                self.assertEqual((measure.valid, measure.solid_count, measure.closed, measure.face_count, measure.free_edge_count), (True, 0, False, 1, 6))
                self.assertIsNone(measure.volume)
                _assert_bbox(self, measure, (0, 0, elevation), (3, 2, elevation), places=6)
                # Read the actual render mesh too: the concave missing corner
                # must not be filled in by a bounding-box or fan stand-in.
                import rhino3dm
                model = rhino3dm.File3dm.Read(str(workspace / receipt.preview_artifact["relative_path"]))
                self.assertEqual(len(model.Objects), 1)
                mesh = model.Objects[0].Geometry
                self.assertIsInstance(mesh, rhino3dm.Mesh)
                self.assertFalse(mesh.IsClosed)
                area = 0.0
                for i in range(len(mesh.Faces)):
                    a, b, c, d = mesh.Faces[i]
                    for triangle in ((a, b, c),) if c == d else ((a, b, c), (a, c, d)):
                        p, q, r = (mesh.Vertices[index] for index in triangle)
                        self.assertAlmostEqual(p.Z, elevation, places=6)
                        area += abs((q.X - p.X) * (r.Y - p.Y) - (q.Y - p.Y) * (r.X - p.X)) / 2
                self.assertAlmostEqual(area, 4.0, places=6)


@NEEDS_OCCT
class PrismElevationExecutionTests(unittest.TestCase):
    def test_saved_panel_bottom_tracks_elevation_while_thickness_grows_upward(self) -> None:
        for elevation, height in ((2.9, 0.012), (3.0, 0.012), (2.9, 0.024)):
            with self.subTest(elevation=elevation, height=height), tempfile.TemporaryDirectory() as tmp:
                element = {
                    "entity_id": "panel", "schema": "Element@1", "parent_id": "primary-support",
                    "fields": {"component_id": "primary-support", "producer": "prism",
                               "references": {"base": {"level": "level-ground"}},
                               "params": {"elevation": elevation, "height": height,
                                          "profile": [[0, 0], [2, 0], [2, 3], [0, 3], [0, 0]]}},
                    "basis_refs": [EVIDENCE],
                }
                program = _compile(_record_with(element))
                workspace = Path(tmp).resolve()
                receipt, _ = _execute(program, _persisted_binding(program, "stage-panel"), workspace, "panel")
                self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
                entry = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])["obj-panel"]
                measure = occt_backend.measure_shape(entry.shape)
                self.assertTrue(measure.closed)
                self.assertEqual(measure.solid_count, 1)
                self.assertAlmostEqual(measure.volume, 6 * height, places=6)
                _assert_bbox(self, measure, (0, 0, elevation), (2, 3, elevation + height), places=6)


class PlanarSurfaceBoundaryTests(unittest.TestCase):
    def test_open_nonplanar_crossing_and_degenerate_boundaries_are_refused(self) -> None:
        invalid = (
            ([[0, 0, 0], [2, 0, 0], [2, 0, 2], [0, 0, 2]], "explicitly close"),
            ([[0, 0, 0], [2, 0, 0], [2, 1, 2], [0, 0, 2], [0, 0, 0]], "not planar"),
            ([[0, 0, 0], [3, 0, 0], [0, 0, 2], [2, 0, 2], [0, 0, 0]], "self-intersecting"),
            ([[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 0, 0]], "zero area"),
            ([[0, 0, 0], [3, 0, 0], [2, 0, 0], [2, 0, 2], [0, 0, 0]], "overlapping|self-intersecting"),
        )
        for points, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(GeometryProgramError, message):
                GeometryOperation(
                    op_id="face", kind=GeometryOperationKind.PLANAR_SURFACE, output_object_ids=("face-object",),
                    input_object_ids=(), frame_id="world", semantic_binding_ids=("binding",),
                    parameters=(GeometryParameter.create(name="profile", kind=GeometryParameterKind.POINTS3, value=points, unit=LengthUnit.METER),),
                )


@NEEDS_OCCT
class StairLoftExecutionTests(unittest.TestCase):
    """Two closed eight-vertex sections, lofted by the existing producer, become one closed solid flight."""

    def test_the_flight_is_one_valid_closed_solid_with_the_stated_steps(self) -> None:
        program = _compile(_stair_record())
        loft = next(op for op in program.proposal.operations if op.op_id == "stair-east")
        params = {p.name: json.loads(p.value_json) for p in loft.parameters}
        self.assertEqual((loft.kind, params["profile_size"], len(params["profiles"])), (GeometryOperationKind.LOFT, 8, 16))
        binding = _persisted_binding(program, "stage-occt-stair")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, elapsed = _execute(program, binding, workspace, "stair@occt")
            print(f"\n[occt] stair flight: {elapsed:.3f} s wall clock; timings={ {k: round(v, 3) for k, v in receipt.timings.items()} }")

            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.physical_object_ids, ("obj-stair-east",))
            self.assertEqual((receipt.adapter_id, receipt.evidence_tier), ("occt-in-process", "self_measured_cold_read"))
            self.assertEqual(receipt.backend["binding"], "cadquery-ocp")
            self.assertTrue(receipt.exact_artifact["exact_brep"])
            self.assertFalse(receipt.preview_artifact["exact_brep"])
            step = workspace / receipt.exact_artifact["relative_path"]
            preview = workspace / receipt.preview_artifact["relative_path"]
            self.assertEqual(sorted(p.name for p in workspace.iterdir()), sorted([step.name, preview.name]))
            self.assertLess(elapsed, 30.0, "in-process execution must not take a Rhino-sized time")

            # the exact delivery, read back by a fresh reader from the bytes on disk
            entries = _entries_by_name(step)
            self.assertEqual(list(entries), ["obj-stair-east"])
            entry = entries["obj-stair-east"]
            self.assertEqual(entry.layers, ("archflow::building",))
            measure = occt_backend.measure_shape(entry.shape)
            self.assertTrue(measure.valid)
            self.assertEqual(measure.solid_count, 1)
            self.assertTrue(measure.closed)
            self.assertEqual(measure.face_count, 10)                                 # eight side faces and two caps
            self.assertAlmostEqual(measure.volume, 1.2 * (0.3 * 0.2 + 0.3 * 0.4 + 0.3 * 0.6), places=6)
            # endpoints, width and height in the CAD frame (x, plan z, up y)
            _assert_bbox(self, measure, (0.0, -0.6, 0.0), (0.9, 0.6, 0.6))
            # the step profile, not just its hull: inside each tread, outside above it and beyond the width
            probes = {
                (0.15, 0.1, 0.0): "inside", (0.15, 0.3, 0.0): "outside",
                (0.45, 0.3, 0.0): "inside", (0.45, 0.5, 0.0): "outside",
                (0.75, 0.5, 0.0): "inside", (0.75, 0.7, 0.0): "outside",
                (0.45, 0.3, 0.59): "inside", (0.45, 0.3, 0.61): "outside",
            }
            for point, expected in probes.items():
                self.assertEqual(occt_backend.classify_program_point(entry.shape, point), expected, point)

            # the receipt's own cold-read agrees with the analytic predictor and the file
            row = receipt.readback["obj-stair-east"]
            self.assertEqual((row["solid_count"], row["closed"], row["valid"]), (1, True, True))
            self.assertEqual(receipt.expected_bounds["obj-stair-east"], {"min": [0.0, -0.6, 0.0], "max": [0.9, 0.6, 0.6]})

            # the preview: the same model as a mesh, with the viewer's semantics, and labelled as such
            self.assertIn("not a NURBS/B-rep delivery", receipt.preview_artifact["note"])
            inspection = inspect_three_dm(preview)
            self.assertEqual(inspection.units["name"], "Meters")
            self.assertEqual(inspection.top_level_object_count, 1)
            (named,) = inspection.named_object_bboxes
            self.assertEqual((named["name"], named["type"], named["layer_path"]), ("obj-stair-east", "Mesh", "archflow::building"))
            for axis in range(3):
                self.assertAlmostEqual(named["bbox"]["min"][axis], measure.bbox_min[axis], places=5)
                self.assertAlmostEqual(named["bbox"]["max"][axis], measure.bbox_max[axis], places=5)
            (strings,) = inspection.object_user_strings
            self.assertEqual(
                {pair["key"]: pair["value"] for pair in strings["attributes"]},
                receipt.expected_semantics["objects"]["obj-stair-east"]["user_text"],
            )
            document = {row["key"]: row["value"] for row in inspection.document_user_strings}
            self.assertEqual(document["archflow:program_digest"], program.program_digest)
            self.assertEqual(document["archflow:program_record_sha256"], binding.program_ref.sha256)
            self.assertEqual(document["archflow:export_schema"], OcctExecutionReceipt.SCHEMA)
            self.assertEqual(document["archflow:up_axis"], "Z-up")
            self.assertEqual({row["full_path"] for row in inspection.layers}, {"archflow", "archflow::building"})
            self.assertEqual(receipt.to_dict()["status"], "succeeded")


@NEEDS_OCCT
class WallOpeningBooleanTests(unittest.TestCase):
    """The authored record's wall with its window void: a real Boolean cut, saved and verified."""

    def test_repeated_half_round_openings_need_no_straight_jamb_segment(self) -> None:
        payload = json.loads(json.dumps(RECORD_PAYLOAD))
        wall = next(row for row in payload["entities"] if row["entity_id"] == "wall-south")
        wall["fields"]["params"]["openings"] = [{
            "opening_id": "arch", "kind": "window", "along": 1.7, "width": 2.4,
            "sill": 1.1, "head": 2.3, "shape": "semicircular_arch", "spring_height": 1.1,
            "count": 2, "step": 2.6,
        }]
        program = _compile(StateRecord.from_dict(payload))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _persisted_binding(program, "stage-occt-half-round"), workspace, "half-round@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            cut = entries["obj-wall-south-cut"].shape
            self.assertAlmostEqual(occt_backend.measure_shape(cut).volume,
                                   6.0 * 0.3 * 2.97 - math.pi * 1.2 ** 2 * 0.3, places=6)
            for index, centre in enumerate((1.7, 4.3)):
                aperture = occt_backend.measure_shape(entries[f"obj-wall-south-aperture-arch-{index}"].shape)
                self.assertAlmostEqual(aperture.volume, math.pi * 1.2 ** 2 * 0.3 / 2, places=6)
                _assert_bbox(self, aperture, (centre - 1.2, -0.3, 1.7), (centre + 1.2, 0.0, 2.9))
                self.assertEqual(occt_backend.classify_program_point(cut, (centre, 2.0, -0.15)), "outside")
                self.assertEqual(occt_backend.classify_program_point(cut, (centre, 1.6, -0.15)), "inside")
            self.assertEqual(occt_backend.classify_program_point(cut, (3.0, 2.0, -0.15)), "inside")

    def test_semicircular_arch_is_an_exact_through_opening_with_solid_shoulders(self) -> None:
        payload = json.loads(json.dumps(RECORD_PAYLOAD))
        wall = next(row for row in payload["entities"] if row["entity_id"] == "wall-south")
        wall["fields"]["params"]["openings"] = [{
            "opening_id": "arch", "kind": "door", "along": 3.0, "width": 2.4,
            "sill": 0.0, "head": 2.7, "shape": "semicircular_arch", "spring_height": 1.5,
        }]
        program = _compile(StateRecord.from_dict(payload))
        binding = _persisted_binding(program, "stage-occt-arch")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, binding, workspace, "arch@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.physical_object_ids,
                             ("obj-plinth", "obj-wall-south-aperture-arch", "obj-wall-south-cut"))
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            shape = entries["obj-wall-south-cut"].shape
            aperture_shape = entries["obj-wall-south-aperture-arch"].shape
            cut = occt_backend.measure_shape(shape)
            aperture = occt_backend.measure_shape(aperture_shape)
            area = 2.4 * 1.5 + math.pi * 1.2 ** 2 / 2.0
            self.assertEqual((cut.valid, cut.closed, cut.solid_count), (True, True, 1))
            self.assertEqual((aperture.valid, aperture.closed, aperture.solid_count), (True, True, 1))
            self.assertAlmostEqual(aperture.volume, area * 0.3, places=6)
            self.assertAlmostEqual(cut.volume, 6.0 * 2.97 * 0.3 - area * 0.3, places=6)
            _assert_bbox(self, aperture, (1.8, -0.3, 0.6), (4.2, 0.0, 3.3))
            # Cold-read points in program coordinates: the same opening on three depth slices.
            for depth in (-0.02, -0.15, -0.28):
                for x, y, result in ((3.0, 1.0, "outside"), (3.0, 3.29, "outside"),
                                     (3.0, 3.31, "inside"), (4.0, 2.9, "inside"),
                                     (4.0, 2.6, "outside"), (1.79, 1.5, "inside"),
                                     (1.81, 1.5, "outside"), (4.19, 1.5, "outside"),
                                     (4.21, 1.5, "inside")):
                    point = (x, y, depth)
                    self.assertEqual(occt_backend.classify_program_point(shape, point), result, point)
                    self.assertEqual(occt_backend.classify_program_point(aperture_shape, point),
                                     "inside" if result == "outside" else "outside", point)
            # The reveal survives STEP as a cylinder, without faceted loft approximation.
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            from OCP.GeomAbs import GeomAbs_Cylinder
            from OCP.TopAbs import TopAbs_FACE
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS
            faces = TopExp_Explorer(shape, TopAbs_FACE)
            radii = []
            while faces.More():
                surface = BRepAdaptor_Surface(TopoDS.Face_s(faces.Current()))
                if surface.GetType() == GeomAbs_Cylinder:
                    radii.append(surface.Cylinder().Radius())
                faces.Next()
            self.assertEqual(len(radii), 1)
            self.assertAlmostEqual(radii[0], 1.2, places=8)

    def test_the_cut_wall_is_the_saved_solid_with_its_opening(self) -> None:
        program = _compile(authored_record())
        binding = _persisted_binding(program, "stage-occt-wall")
        expected_ids = ("obj-plinth", "obj-wall-south-aperture-window-south", "obj-wall-south-cut")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, elapsed = _execute(program, binding, workspace, "wall@occt")
            print(f"\n[occt] wall with opening: {elapsed:.3f} s wall clock; timings={ {k: round(v, 3) for k, v in receipt.timings.items()} }")

            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.physical_object_ids, expected_ids)
            step = workspace / receipt.exact_artifact["relative_path"]
            entries = _entries_by_name(step)
            self.assertEqual(sorted(entries), list(expected_ids))

            plinth = occt_backend.measure_shape(entries["obj-plinth"].shape)
            self.assertEqual((plinth.valid, plinth.solid_count, plinth.closed, plinth.face_count), (True, 1, True, 6))
            self.assertAlmostEqual(plinth.volume, 6.0 * 1.2 * 0.6, places=6)

            cut = occt_backend.measure_shape(entries["obj-wall-south-cut"].shape)
            self.assertEqual((cut.valid, cut.solid_count, cut.closed), (True, 1, True))
            self.assertEqual(cut.face_count, 10)                                       # the box's six faces and the opening's four reveals
            self.assertAlmostEqual(cut.volume, 6.0 * 0.3 * 2.97 - 1.2 * 0.3 * 1.5, places=6)
            _assert_bbox(self, cut, (0.0, cut.bbox_min[1], 0.6), (6.0, cut.bbox_max[1], 0.6 + 2.97))
            self.assertAlmostEqual(cut.bbox_max[1] - cut.bbox_min[1], 0.3, places=6)

            # through the opening (x 2.4..3.6, y 1.5..3.0 above ground) the wall is gone; around it, it is there
            plan_z = (cut.bbox_min[1] + cut.bbox_max[1]) / 2.0
            probes = {
                (3.0, 2.2, plan_z): "outside", (1.0, 2.2, plan_z): "inside",
                (3.0, 1.0, plan_z): "inside", (3.0, 3.3, plan_z): "inside",
                (2.3, 2.2, plan_z): "inside", (2.5, 2.2, plan_z): "outside",
            }
            for point, expected in probes.items():
                self.assertEqual(occt_backend.classify_program_point(entries["obj-wall-south-cut"].shape, point), expected, point)

            aperture = occt_backend.measure_shape(entries["obj-wall-south-aperture-window-south"].shape)
            self.assertEqual((aperture.valid, aperture.solid_count, aperture.closed), (True, 1, True))
            self.assertAlmostEqual(aperture.volume, 1.2 * 0.3 * 1.5, places=6)         # the void clipped to the wall's thickness

            # the receipt measured the same file the same way
            self.assertAlmostEqual(receipt.readback["obj-wall-south-cut"]["volume"], cut.volume, places=9)
            self.assertEqual(receipt.readback["obj-wall-south-aperture-window-south"]["layers"], ["archflow::building"])

            # the preview carries all three, the aperture hidden as its semantics say
            preview = workspace / receipt.preview_artifact["relative_path"]
            inspection = inspect_three_dm(preview)
            self.assertEqual(inspection.top_level_object_count, 3)
            self.assertEqual(sorted(row["name"] for row in inspection.named_object_bboxes), list(expected_ids))
            import rhino3dm

            model = rhino3dm.File3dm.Read(str(preview))
            visibility = {obj.Attributes.Name: obj.Attributes.Visible for obj in model.Objects}
            self.assertEqual(visibility, {"obj-plinth": True, "obj-wall-south-aperture-window-south": False, "obj-wall-south-cut": True})
            self.assertEqual(
                receipt.expected_semantics["objects"]["obj-wall-south-aperture-window-south"]["user_text"]["archflow:inspection_witness"],
                "hidden",
            )


FRAME_ID = "obj-frame-wall-south-window-south"
PANE_ID = "obj-glazing-wall-south-window-south"
FRAME_VOLUME = (1.2 * 1.5 - 1.02 * 1.32) * 0.18        # the ring between the void and the aperture, 0.18 deep
PANE_VOLUME = 1.02 * 1.32 * 0.025                        # the aperture's pane, 25 mm thick
WINDOW_STEP = 1.8


def _preview_materials(preview: Path) -> tuple[dict[str, dict], dict[str, tuple[str, int]]]:
    """The saved ``.3dm`` reopened natively: its material table and each object's material binding."""

    import rhino3dm

    model = rhino3dm.File3dm.Read(str(preview))
    materials = {
        material.Name: {
            "index": index,
            "diffuse": tuple(material.DiffuseColor)[:3],
            "transparency": material.Transparency,
            "material_id": material.GetUserString("archflow:material_id"),
        }
        for index, material in enumerate(model.Materials)
    }
    bindings = {obj.Attributes.Name: (obj.Attributes.MaterialSource.name, obj.Attributes.MaterialIndex) for obj in model.Objects}
    return materials, bindings


@NEEDS_OCCT
class WindowFrameExecutionTests(unittest.TestCase):
    """A typed window: one closed frame with the aperture through it, a separate pane, and a preview that shows glass as glass."""

    def test_the_frame_is_one_closed_solid_with_a_hole_and_the_pane_is_another(self) -> None:
        program = _compile(_window_record())
        frame_op = next(op for op in program.proposal.operations if op.op_id == "frame-wall-south-window-south")
        self.assertEqual((frame_op.kind, len(frame_op.input_object_ids)), (GeometryOperationKind.BOOLEAN_UNION, 4))
        (assembly,) = program.proposal.assemblies
        self.assertEqual([m.to_dict()["object_ids"] for m in assembly.members], [[FRAME_ID], [PANE_ID], ["obj-wall-south-aperture-window-south"]])
        binding = _persisted_binding(program, "stage-occt-window")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, elapsed = _execute(program, binding, workspace, "window@occt")
            print(f"\n[occt] window frame and pane: {elapsed:.3f} s wall clock; timings={ {k: round(v, 3) for k, v in receipt.timings.items()} }")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(
                receipt.physical_object_ids,
                (FRAME_ID, PANE_ID, "obj-plinth", "obj-wall-south-aperture-window-south", "obj-wall-south-cut"),
            )
            # the bars were consumed by the union: none of them is a delivered object
            self.assertFalse(any(name.endswith(("-bottom", "-left", "-right", "-top")) for name in receipt.physical_object_ids))

            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            frame = occt_backend.measure_shape(entries[FRAME_ID].shape)
            self.assertEqual((frame.valid, frame.solid_count, frame.closed), (True, 1, True))
            self.assertEqual(frame.face_count, 10)                                   # front, back, four outer and four reveal faces
            self.assertAlmostEqual(frame.volume, FRAME_VOLUME, places=6)
            _assert_bbox(self, frame, (2.4, -0.08, 0.6 + 0.9), (3.6, 0.1, 0.6 + 2.4))  # the void, 0.18 deep, standing on the plinth
            plan_z = (frame.bbox_min[1] + frame.bbox_max[1]) / 2.0
            probes = {
                (3.0, 2.25, plan_z): "outside",                                      # the aperture: the hole through the frame
                (3.0, 1.545, plan_z): "inside", (3.0, 2.955, plan_z): "inside",     # bottom rail, top rail
                (2.445, 2.25, plan_z): "inside", (3.555, 2.25, plan_z): "inside",   # left stile, right stile
                (2.445, 1.545, plan_z): "inside",                                    # a corner, where two bars overlapped: one body
            }
            for point, expected in probes.items():
                self.assertEqual(occt_backend.classify_program_point(entries[FRAME_ID].shape, point), expected, point)

            pane = occt_backend.measure_shape(entries[PANE_ID].shape)
            self.assertEqual((pane.valid, pane.solid_count, pane.closed, pane.face_count), (True, 1, True, 6))
            self.assertAlmostEqual(pane.volume, PANE_VOLUME, places=6)
            _assert_bbox(self, pane, (2.49, -0.035, 0.6 + 0.99), (3.51, -0.01, 0.6 + 2.31))
            self.assertEqual(occt_backend.classify_program_point(entries[PANE_ID].shape, (3.0, 2.25, -0.0225)), "inside")
            self.assertEqual(occt_backend.classify_program_point(entries[FRAME_ID].shape, (3.0, 2.25, -0.0225)), "outside")

            cut = occt_backend.measure_shape(entries["obj-wall-south-cut"].shape)
            self.assertAlmostEqual(cut.volume, 6.0 * 0.3 * 2.97 - 1.2 * 0.3 * 1.5, places=6)
            self.assertEqual(occt_backend.classify_program_point(entries["obj-wall-south-cut"].shape, (3.0, 2.25, -0.15)), "outside")

            # the receipt's cold read agrees, and the analytic predictor already knew the union's bounds
            self.assertAlmostEqual(receipt.readback[FRAME_ID]["volume"], FRAME_VOLUME, places=6)
            self.assertEqual(receipt.expected_bounds[FRAME_ID], {"min": [2.4, -0.08, 1.5], "max": [3.6, 0.1, 3.0]})

            # the preview, reopened natively: distinct frame and glass materials, the glass actually transparent
            preview = workspace / receipt.preview_artifact["relative_path"]
            materials, bindings = _preview_materials(preview)
            self.assertEqual(sorted(materials), ["frame", "glazing"])
            self.assertEqual(materials["frame"]["transparency"], 0.0)
            self.assertGreater(materials["glazing"]["transparency"], 0.0)
            self.assertLess(materials["glazing"]["transparency"], 1.0)
            self.assertNotEqual(materials["frame"]["diffuse"], materials["glazing"]["diffuse"])
            self.assertEqual({name: row["material_id"] for name, row in materials.items()}, {"frame": "frame", "glazing": "glazing"})
            self.assertEqual(bindings[FRAME_ID], ("MaterialFromObject", materials["frame"]["index"]))
            self.assertEqual(bindings[PANE_ID], ("MaterialFromObject", materials["glazing"]["index"]))
            for other in ("obj-plinth", "obj-wall-south-cut", "obj-wall-south-aperture-window-south"):
                self.assertEqual(bindings[other], ("MaterialFromLayer", -1))
            self.assertEqual(
                receipt.preview_artifact["materials"],
                {FRAME_ID: {"name": "frame", "diffuse": [107, 82, 102], "transparency": 0.0},
                 PANE_ID: {"name": "glazing", "diffuse": [150, 200, 225], "transparency": 0.6}},
            )
            self.assertIn("native object materials for assembly frame and glazing members", receipt.preview_artifact["carries"])
            # the inspector sees the same binding, and everything else the preview always carried
            inspection = inspect_three_dm(preview)
            by_name = {row["name"]: row for row in inspection.object_material_bindings}
            self.assertEqual((by_name[FRAME_ID]["material_source"], by_name[FRAME_ID]["material_name"]), ("MaterialFromObject", "frame"))
            self.assertEqual((by_name[PANE_ID]["material_source"], by_name[PANE_ID]["archflow_material_id"]), ("MaterialFromObject", "glazing"))
            # and the inspector reads the stored transparency itself: the glass as declared, the frame opaque
            table = {row["index"]: row for row in inspection.materials}
            self.assertEqual(table[by_name[PANE_ID]["material_index"]]["transparency"], 0.6)
            self.assertEqual((by_name[PANE_ID]["material_transparency"], by_name[FRAME_ID]["material_transparency"]), (0.6, 0.0))
            self.assertEqual(sorted(row["name"] for row in inspection.named_object_bboxes), list(receipt.physical_object_ids))
            strings = {row["name"]: {p["key"]: p["value"] for p in row["attributes"]} for row in inspection.object_user_strings}
            self.assertEqual(strings[FRAME_ID], receipt.expected_semantics["objects"][FRAME_ID]["user_text"])
            self.assertEqual(strings[FRAME_ID]["archflow:producer_op"], "frame-wall-south-window-south")

    def test_three_repeated_windows_are_three_frames_three_panes_and_three_empty_openings(self) -> None:
        program = _compile(_window_record(count=3, step=WINDOW_STEP, along=1.2))
        binding = _persisted_binding(program, "stage-occt-windows")
        frame_array, pane_array = f"{FRAME_ID}-array", f"{PANE_ID}-array"
        apertures = tuple(f"obj-wall-south-aperture-window-south-{index}" for index in range(3))
        centres = tuple(1.2 + index * WINDOW_STEP for index in range(3))                  # 1.2, 3.0, 4.8 along the wall

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, elapsed = _execute(program, binding, workspace, "windows@occt")
            print(f"\n[occt] three windows: {elapsed:.3f} s wall clock; timings={ {k: round(v, 3) for k, v in receipt.timings.items()} }")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.physical_object_ids, (frame_array, pane_array, "obj-plinth", *apertures, "obj-wall-south-cut"))
            self.assertEqual(receipt.expected_bounds[frame_array], {"min": [0.6, -0.08, 1.5], "max": [5.4, 0.1, 3.0]})

            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            frames = occt_backend.measure_shape(entries[frame_array].shape)
            self.assertEqual((frames.valid, frames.solid_count, frames.closed, frames.face_count), (True, 3, True, 30))
            self.assertAlmostEqual(frames.volume, 3 * FRAME_VOLUME, places=6)
            _assert_bbox(self, frames, (0.6, -0.08, 1.5), (5.4, 0.1, 3.0))
            panes = occt_backend.measure_shape(entries[pane_array].shape)
            self.assertEqual((panes.valid, panes.solid_count, panes.closed, panes.face_count), (True, 3, True, 18))
            self.assertAlmostEqual(panes.volume, 3 * PANE_VOLUME, places=6)
            _assert_bbox(self, panes, (0.69, -0.035, 1.59), (5.31, -0.01, 2.91))
            self.assertEqual(entries[frame_array].layers, ("archflow::building",))
            self.assertEqual(entries[pane_array].layers, ("archflow::building",))

            # each copy stands where its aperture is: a rail inside, the aperture centre outside, the pane inside
            frame_shape, pane_shape, wall_shape = entries[frame_array].shape, entries[pane_array].shape, entries["obj-wall-south-cut"].shape
            for centre in centres:
                self.assertEqual(occt_backend.classify_program_point(frame_shape, (centre, 1.545, 0.01)), "inside", centre)
                self.assertEqual(occt_backend.classify_program_point(frame_shape, (centre - 0.555, 2.25, 0.01)), "inside", centre)
                self.assertEqual(occt_backend.classify_program_point(frame_shape, (centre, 2.25, 0.01)), "outside", centre)
                self.assertEqual(occt_backend.classify_program_point(pane_shape, (centre, 2.25, -0.0225)), "inside", centre)
                self.assertEqual(occt_backend.classify_program_point(wall_shape, (centre, 2.25, -0.15)), "outside", centre)  # the opening is empty
            for between in (2.1, 3.9):                                                   # the pier between two windows
                self.assertEqual(occt_backend.classify_program_point(wall_shape, (between, 2.25, -0.15)), "inside", between)
                self.assertEqual(occt_backend.classify_program_point(frame_shape, (between, 2.25, 0.01)), "outside", between)
            cut = occt_backend.measure_shape(wall_shape)
            self.assertAlmostEqual(cut.volume, 6.0 * 0.3 * 2.97 - 3 * (1.2 * 0.3 * 1.5), places=6)
            for aperture, centre in zip(apertures, centres):
                measure = occt_backend.measure_shape(entries[aperture].shape)
                self.assertAlmostEqual(measure.volume, 1.2 * 0.3 * 1.5, places=6)
                self.assertAlmostEqual((measure.bbox_min[0] + measure.bbox_max[0]) / 2.0, centre, places=6)

            # the receipt counted every copy from the cold read
            self.assertEqual((receipt.readback[frame_array]["solid_count"], receipt.readback[pane_array]["solid_count"]), (3, 3))
            # the semantic denominator names the families a Rhino build would instance; here they are real copies
            self.assertEqual(
                receipt.expected_semantics["blocks"],
                {"archflow-family-frame-wall-south-window-south-array": 3, "archflow-family-glazing-wall-south-window-south-array": 3},
            )
            self.assertEqual(receipt.preview_inspection["instance_definitions"], [])
            # and the preview carries the arrays as two named meshes wearing frame and glass
            preview = workspace / receipt.preview_artifact["relative_path"]
            inspection = inspect_three_dm(preview)
            self.assertEqual(inspection.top_level_object_count, 7)
            named = {row["name"]: row for row in inspection.named_object_bboxes}
            self.assertAlmostEqual(named[frame_array]["bbox"]["min"][0], 0.6, places=5)
            self.assertAlmostEqual(named[frame_array]["bbox"]["max"][0], 5.4, places=5)
            materials, bindings = _preview_materials(preview)
            self.assertEqual(bindings[frame_array], ("MaterialFromObject", materials["frame"]["index"]))
            self.assertEqual(bindings[pane_array], ("MaterialFromObject", materials["glazing"]["index"]))
            self.assertGreater(materials["glazing"]["transparency"], 0.0)

    def test_a_declared_component_material_names_the_frame_and_glass_keeps_its_fallback(self) -> None:
        program = _compile(_window_record())
        binding = _persisted_binding(program, "stage-occt-window-oak")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(
                program, binding, workspace, "oak@occt",
                material_by_component={"building": "oak"}, material_colors={"oak": (120, 80, 40)},
            )
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.expected_semantics["objects"][FRAME_ID]["user_text"]["archflow:material"], "oak")
            materials, bindings = _preview_materials(workspace / receipt.preview_artifact["relative_path"])
            self.assertEqual(sorted(materials), ["glazing", "oak"])
            self.assertEqual((materials["oak"]["diffuse"], materials["oak"]["transparency"], materials["oak"]["material_id"]), ((120, 80, 40), 0.0, "oak"))
            self.assertEqual(bindings[FRAME_ID], ("MaterialFromObject", materials["oak"]["index"]))
            self.assertEqual(bindings[PANE_ID], ("MaterialFromObject", materials["glazing"]["index"]))
            self.assertEqual(materials["glazing"]["transparency"], 0.6)

    def test_a_preview_whose_material_binding_is_lost_fails_the_readback(self) -> None:
        program = _compile(_window_record())
        binding = _persisted_binding(program, "stage-occt-window-unbound")
        original = occt_backend.write_preview_three_dm

        def forgetting_materials(path, objects, **options):
            return original(path, tuple(replace(item, material=None) for item in objects), **options)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            with patch("archflow.adapters.cad_execution.write_preview_three_dm", forgetting_materials):
                receipt, _ = _execute(program, binding, workspace, "unbound@occt")
            self.assertIs(receipt.status, CadExecutionStatus.FAILED)
            self.assertEqual(
                sorted((f["code"], f["detail"]) for f in receipt.failures),
                [("cad_execution.preview_material_mismatch", f"preview object {FRAME_ID} does not wear material frame"),
                 ("cad_execution.preview_material_mismatch", f"preview object {PANE_ID} does not wear material glazing")],
            )

    def test_a_preview_whose_glass_is_written_opaque_fails_the_readback(self) -> None:
        """Same name, same colour, same user text, but the stored transparency is not the declared one: refused."""

        program = _compile(_window_record())
        binding = _persisted_binding(program, "stage-occt-window-opaque-glass")
        original = occt_backend.write_preview_three_dm

        def opaque_glass(path, objects, **options):
            tampered = tuple(
                replace(item, material=replace(item.material, transparency=0.0)) if item.object_id == PANE_ID else item
                for item in objects
            )
            return original(path, tampered, **options)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            with patch("archflow.adapters.cad_execution.write_preview_three_dm", opaque_glass):
                receipt, _ = _execute(program, binding, workspace, "opaque-glass@occt")
            self.assertIs(receipt.status, CadExecutionStatus.FAILED)
            self.assertEqual(
                [(f["code"], f["detail"]) for f in receipt.failures],
                [("cad_execution.preview_material_mismatch", f"preview object {PANE_ID} does not wear material glazing")],
            )
            # the declaration still says 0.6; the file the inspector read says 0.0, and that is what was refused
            self.assertEqual(receipt.preview_artifact["materials"][PANE_ID]["transparency"], 0.6)
            by_name = {row["name"]: row for row in inspect_three_dm(workspace / receipt.preview_artifact["relative_path"]).object_material_bindings}
            self.assertEqual((by_name[PANE_ID]["material_name"], by_name[PANE_ID]["material_transparency"]), ("glazing", 0.0))


WEB_ROOT = Path(__file__).resolve().parents[1] / "apps" / "archflow-studio" / "web"
LOADER_TEST = WEB_ROOT / "test" / "rhino3dmMaterials.test.ts"
WEB_DEPENDENCIES = (
    WEB_ROOT / "node_modules" / "three" / "examples" / "jsm" / "loaders" / "3DMLoader.js",
    WEB_ROOT / "node_modules" / "rhino3dm" / "rhino3dm.wasm",
)


def _node_executable() -> str | None:
    """The installed Node: PATH first, then the standard install root. No user path is written here."""

    found = shutil.which("node")
    if found:
        return found
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")):
        candidate = Path(root) / "nodejs" / "node.exe" if root else None
        if candidate is not None and candidate.is_file():
            return str(candidate)
    return None


def _tap_count(report: str, field: str) -> int | None:
    match = re.search(rf"^# {field} (\d+)$", report, re.MULTILINE)
    return int(match.group(1)) if match else None


@NEEDS_OCCT
class StudioLoaderBridgeTests(unittest.TestCase):
    """The preview this execution writes, opened by the Studio's installed Rhino3dmLoader in Node.

    The Studio test has no fixture binary: it is handed the path of the
    preview exported here, into a temporary workspace, and reads that file
    only. Node is started exactly once, after the exporter has returned and
    the no-process guard has been lifted; it never calls back into Python.
    """

    def test_the_current_preview_reaches_the_studio_loader_with_its_materials_and_hidden_aperture(self) -> None:
        node = _node_executable()
        if node is None:
            self.skipTest("Node is not installed (not on PATH, not under the standard install root)")
        missing = [str(path.relative_to(WEB_ROOT)) for path in WEB_DEPENDENCIES if not path.is_file()]
        if missing:
            self.skipTest(f"the Studio web dependencies are not installed under {WEB_ROOT}: {missing}")
        self.assertTrue(LOADER_TEST.is_file(), LOADER_TEST)

        program = _compile(_window_record())
        binding = _persisted_binding(program, "stage-occt-window-loader")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, binding, workspace, "loader@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            preview = workspace / receipt.preview_artifact["relative_path"]
            self.assertTrue(preview.is_file(), preview)
            self.assertEqual(receipt.expected_semantics["objects"]["obj-wall-south-aperture-window-south"]["visible"], False)

            completed = subprocess.run(
                [node, "--test", "--test-reporter=tap", str(LOADER_TEST)],
                cwd=str(WEB_ROOT),
                env={**os.environ, "ARCHFLOW_PREVIEW_3DM": str(preview)},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
        report = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, report)
        # both Studio tests ran against the current export; a skip would mean the path never arrived
        self.assertEqual((_tap_count(report, "pass"), _tap_count(report, "fail"), _tap_count(report, "skipped")), (2, 0, 0), report)


@NEEDS_OCCT
class SolidBoxRoundTripTests(unittest.TestCase):
    def test_a_box_program_round_trips_through_step_and_preview(self) -> None:
        program = _synthetic_program()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "body@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            body = occt_backend.measure_shape(entries["body-object"].shape)
            self.assertAlmostEqual(body.volume, 24.0, places=9)
            _assert_bbox(self, body, (0.0, 0.0, 0.0), (2.0, 4.0, 3.0), places=9)          # program (2, 3, 4) written as (x, z, y)
            self.assertEqual(entries["body-object"].color, (
                *receipt.preview_inspection["layers"][1]["color_rgba"][:3],
            ))

    def test_an_existing_output_is_refused_before_execution(self) -> None:
        program = _synthetic_program()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            (workspace / "body@occt.step").write_bytes(b"")
            with self.assertRaisesRegex(CadExecutionError, "already exists"):
                _execute(program, _synthetic_binding(program), workspace, "body@occt")
            self.assertEqual([p.name for p in workspace.iterdir()], ["body@occt.step"])


# ---------------------------------------------------------------- capability boundary


def _single_operation_program(operation: GeometryOperation) -> CompiledGeometryProgram:
    """The synthetic fixture program with its one operation replaced."""

    program = _synthetic_program()
    output = operation.output_object_ids[0]
    binding = replace(program.proposal.semantic_bindings[0], object_ids=(output,))
    proposal = replace(program.proposal, operations=(operation,), semantic_bindings=(binding,))
    return replace(
        program,
        proposal=proposal,
        operation_order=(operation.op_id,),
        objects=(CompiledGeometryObject(object_id=output, producer_op_id=operation.op_id, object_digest="3" * 64),),
    )


def _loft(op_id: str, *, profile_basis: str = "polyline", cap_ends: bool = True) -> GeometryOperation:
    square = lambda y: [[0.0, y, 0.0], [1.0, y, 0.0], [1.0, y, 1.0], [0.0, y, 1.0]]
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.LOFT,
        output_object_ids=(f"{op_id}-object",),
        input_object_ids=(),
        frame_id="world",
        parameters=(
            GeometryParameter.create(name="cap_ends", kind=GeometryParameterKind.BOOLEAN, value=cap_ends),
            GeometryParameter.create(name="loft_type", kind=GeometryParameterKind.TEXT, value="straight"),
            GeometryParameter.create(name="profile_basis", kind=GeometryParameterKind.TEXT, value=profile_basis),
            GeometryParameter.create(name="profile_size", kind=GeometryParameterKind.INTEGER, value=4),
            GeometryParameter.create(name="profiles", kind=GeometryParameterKind.POINTS3, value=square(0.0) + square(2.0), unit=LengthUnit.METER),
        ),
        semantic_binding_ids=("body-binding",),
    )


def _box(op_id: str, origin: list[float], size: list[float]) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.SOLID,
        output_object_ids=(f"{op_id}-object",),
        input_object_ids=(),
        frame_id="world",
        parameters=(
            GeometryParameter.create(name="origin", kind=GeometryParameterKind.VECTOR3, value=origin, unit=LengthUnit.METER),
            GeometryParameter.create(name="size", kind=GeometryParameterKind.VECTOR3, value=size, unit=LengthUnit.METER),
        ),
        semantic_binding_ids=("body-binding",),
    )


def _intersection_program(*boxes: GeometryOperation) -> CompiledGeometryProgram:
    """The synthetic fixture with the given boxes met by one ``boolean_intersection``; only the meet is physical."""

    meet = GeometryOperation(
        op_id="meet",
        kind=GeometryOperationKind.BOOLEAN_INTERSECTION,
        output_object_ids=("meet-object",),
        input_object_ids=tuple(box.output_object_ids[0] for box in boxes),
        frame_id="world",
        parameters=(),
        semantic_binding_ids=("body-binding",),
    )
    return _program_of(*boxes, meet)


def _array(op_id: str, source: GeometryOperation, *, count: int, step: list[float]) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.ARRAY,
        output_object_ids=(f"{op_id}-object",),
        input_object_ids=(source.output_object_ids[0],),
        frame_id="world",
        parameters=(
            GeometryParameter.create(name="count", kind=GeometryParameterKind.INTEGER, value=count),
            GeometryParameter.create(name="step", kind=GeometryParameterKind.VECTOR3, value=step, unit=LengthUnit.METER),
        ),
        semantic_binding_ids=("body-binding",),
    )


def _radial_array(op_id: str, source: GeometryOperation) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.RADIAL_ARRAY,
        output_object_ids=(f"{op_id}-object",),
        input_object_ids=(source.output_object_ids[0],),
        frame_id="world",
        parameters=(
            GeometryParameter.create(name="angle_step_degrees", kind=GeometryParameterKind.NUMBER, value=90.0),
            GeometryParameter.create(name="center", kind=GeometryParameterKind.VECTOR3, value=[0.0, 0.0, 0.0], unit=LengthUnit.METER),
            GeometryParameter.create(name="count", kind=GeometryParameterKind.INTEGER, value=4),
        ),
        semantic_binding_ids=("body-binding",),
    )


def _program_of(*operations: GeometryOperation) -> CompiledGeometryProgram:
    """The synthetic fixture carrying exactly these operations, executed in the given order."""

    program = _synthetic_program()
    binding = replace(program.proposal.semantic_bindings[0], object_ids=tuple(sorted(op.output_object_ids[0] for op in operations)))
    proposal = replace(
        program.proposal, operations=tuple(sorted(operations, key=lambda op: op.op_id)), semantic_bindings=(binding,)
    )
    objects = tuple(
        sorted(
            (
                CompiledGeometryObject(object_id=op.output_object_ids[0], producer_op_id=op.op_id, object_digest=f"{index}" * 64)
                for index, op in enumerate(operations, start=1)
            ),
            key=lambda item: item.object_id,
        )
    )
    return replace(program, proposal=proposal, operation_order=tuple(op.op_id for op in operations), objects=objects)


@NEEDS_OCCT
class OcctOperationObservationTests(unittest.TestCase):
    def test_build_reports_actual_dependency_work_and_reused_final_shape(self) -> None:
        seed = _box("seed", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0])
        left = _array("left", seed, count=2, step=[2.0, 0.0, 0.0])
        right = _array("right", seed, count=2, step=[0.0, 0.0, 2.0])
        initial_events = []
        prior = occt_backend.build_program_shapes(_program_of(seed, left, right), operation_observer=initial_events.append)
        self.assertEqual(initial_events[0]["details"]["cache_status"], "miss")
        changed_right = _array("right", seed, count=3, step=[0.0, 0.0, 2.0])
        program = _program_of(seed, left, changed_right)
        events = []
        with _no_process(), patch.object(occt_backend, "_build_operation", wraps=occt_backend._build_operation) as executed:
            build = occt_backend.build_program_shapes(
                program,
                reusable_shapes={"left-object": prior.objects["left-object"].shape},
                operation_observer=events.append,
                observation_parent_id="export-operation",
            )
        self.assertEqual([call.args[2].op_id for call in executed.call_args_list], ["seed", "right"])
        self.assertEqual(build.executed_operation_ids, ("seed", "right"))
        self.assertEqual(build.recomputed_object_ids, ("right-object", "seed-object"))
        self.assertEqual(build.reused_object_ids, ("left-object",))
        self.assertIs(build.objects["left-object"].shape, prior.objects["left-object"].shape)
        self.assertAlmostEqual(occt_backend.measure_shape(build.objects["right-object"].shape).volume, 3.0)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["phase"], event["status"], event["parent_event_id"]),
                         ("geometry_build", "succeeded", "export-operation"))
        self.assertEqual(event["details"]["input_identity"], {"program_digest": program.program_digest})
        self.assertEqual(event["details"]["input_object_ids"], ["left-object", "seed-object"])
        self.assertEqual(event["details"]["recomputed_object_ids"], ["right-object", "seed-object"])
        self.assertEqual(event["details"]["reused_object_ids"], ["left-object"])
        self.assertEqual(event["details"]["emitted_object_ids"], ["left-object", "right-object"])
        self.assertEqual(event["details"]["executed_stages"], ["build_program_shapes"])
        self.assertEqual(event["details"]["cache_status"], "partial")
        self.assertNotIn("input_equivalent", event["details"])
        self.assertIs(type(event["duration_ms"]), int)
        self.assertGreaterEqual(event["duration_ms"], 0)
        events.clear()
        with patch.object(occt_backend, "_build_operation", side_effect=AssertionError("fully reused shapes must not rebuild")):
            reused = occt_backend.build_program_shapes(
                program, reusable_shapes={key: build.objects[key].shape for key in build.physical_object_ids},
                operation_observer=events.append,
            )
        self.assertEqual(reused.executed_operation_ids, ())
        self.assertEqual(events[0]["details"]["executed_stages"], [])
        self.assertEqual(events[0]["details"]["reused_object_ids"], ["left-object", "right-object"])
        self.assertEqual(events[0]["details"]["cache_status"], "hit")

    def test_build_observer_failure_preserves_geometry_and_original_exception(self) -> None:
        program = _single_operation_program(_box("body", [0.0, 0.0, 0.0], [1.0, 2.0, 3.0]))
        for observer_failure in (RuntimeError, asyncio.CancelledError):
            with self.subTest(observer_failure=observer_failure):
                events = []

                def broken_observer(event):
                    events.append(event)
                    raise observer_failure("observer unavailable")

                with _no_process():
                    build = occt_backend.build_program_shapes(program, operation_observer=broken_observer)
                self.assertAlmostEqual(occt_backend.measure_shape(build.objects["body-object"].shape).volume, 6.0)
                for failure in (occt_backend.OcctBuildError("kernel failed"), asyncio.CancelledError("kernel cancelled")):
                    with patch.object(occt_backend, "_build_operation", side_effect=failure):
                        with self.assertRaises(type(failure)) as caught:
                            occt_backend.build_program_shapes(program, operation_observer=broken_observer)
                    self.assertIs(caught.exception, failure)
                self.assertEqual([event["status"] for event in events], ["succeeded", "failed", "failed"])
                for event in events:
                    self.assertIs(type(event["duration_ms"]), int)
                self.assertEqual(events[-1]["details"]["emitted_object_ids"], [])

    def _preview_inputs(self):
        program = _program_of(
            _box("left", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]),
            _box("right", [2.0, 0.0, 0.0], [1.0, 1.0, 1.0]),
        )
        build = occt_backend.build_program_shapes(program)
        objects = tuple(occt_backend.PreviewObject(
            object_id=name, shape=build.objects[name].shape, layer="body", user_text={},
        ) for name in build.physical_object_ids)
        return objects, dict(
            layer_colors={"body": (100, 100, 100)},
            document_user_text={"archflow:program_digest": program.program_digest},
            length_unit="meter", linear_deflection=0.01,
        )

    def test_preview_observer_separates_active_tessellation_from_file_write(self) -> None:
        objects, options = self._preview_inputs()
        events = []
        with tempfile.TemporaryDirectory() as tmp, _no_process():
            preview = Path(tmp) / "observed.preview.3dm"
            with patch.object(occt_backend.time, "perf_counter", side_effect=[100.0, 100.125, 105.0, 105.25, 110.0, 110.5]):
                counts = occt_backend.write_preview_three_dm(
                    preview, objects, **options,
                    operation_observer=events.append, observation_parent_id="export-operation",
                )
            inspection = inspect_three_dm(preview)
            self.assertEqual(inspection.object_count, 2)
        self.assertEqual(set(counts), {"left-object", "right-object"})
        self.assertEqual([event["phase"] for event in events], ["tessellation", "preview_write"])
        self.assertEqual([event["duration_ms"] for event in events], [375, 500])
        self.assertEqual(events[0]["details"]["scope"], "aggregate_active_time")
        self.assertEqual(events[0]["details"]["cache_status"], "unknown")
        self.assertEqual(events[0]["details"]["executed_stages"], ["tessellate_shape"])
        self.assertEqual(events[1]["details"]["scope"], "file_write")
        self.assertEqual(events[1]["details"]["executed_stages"], ["write_preview_three_dm"])
        for event in events:
            self.assertIs(type(event["duration_ms"]), int)
            self.assertEqual((event["status"], event["parent_event_id"]), ("succeeded", "export-operation"))
            self.assertEqual(event["details"]["input_object_ids"], ["left-object", "right-object"])
            self.assertEqual(event["details"]["emitted_object_ids"], ["left-object", "right-object"])

    def test_preview_observer_failure_preserves_written_file_and_write_refusal(self) -> None:
        objects, options = self._preview_inputs()
        for observer_failure in (RuntimeError, asyncio.CancelledError):
            with self.subTest(observer_failure=observer_failure):
                events = []

                def broken_observer(event):
                    events.append(event)
                    raise observer_failure("observer unavailable")

                with tempfile.TemporaryDirectory() as tmp, _no_process():
                    preview = Path(tmp) / "observed.preview.3dm"
                    occt_backend.write_preview_three_dm(preview, objects, **options, operation_observer=broken_observer)
                    self.assertEqual(inspect_three_dm(preview).object_count, 2)
                    with self.assertRaisesRegex(occt_backend.OcctBuildError, "preview .3dm write failed"):
                        occt_backend.write_preview_three_dm(
                            Path(tmp) / "missing" / "refused.preview.3dm", objects, **options,
                            operation_observer=broken_observer,
                        )
                self.assertEqual([event["status"] for event in events], ["succeeded", "succeeded", "succeeded", "failed"])
                for event in events:
                    self.assertIs(type(event["duration_ms"]), int)
                self.assertEqual(events[-1]["details"]["emitted_object_ids"], [])

    def test_tessellation_failure_preserves_exception_and_reports_only_attempted_work(self) -> None:
        objects, options = self._preview_inputs()
        first_mesh = occt_backend.tessellate_shape(objects[0].shape, linear_deflection=0.01)
        failure = occt_backend.OcctBuildError("tessellation failed")
        events = []

        def broken_observer(event):
            events.append(event)
            raise RuntimeError("observer unavailable")

        with tempfile.TemporaryDirectory() as tmp, _no_process():
            preview = Path(tmp) / "refused.preview.3dm"
            with patch.object(occt_backend, "tessellate_shape", side_effect=[first_mesh, failure]):
                with self.assertRaises(occt_backend.OcctBuildError) as caught:
                    occt_backend.write_preview_three_dm(preview, objects, **options, operation_observer=broken_observer)
            self.assertFalse(preview.exists())
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["phase"], events[0]["status"]), ("tessellation", "failed"))
        self.assertIs(type(events[0]["duration_ms"]), int)
        self.assertEqual(events[0]["details"]["input_object_ids"], ["left-object", "right-object"])
        self.assertEqual(events[0]["details"]["emitted_object_ids"], ["left-object"])


@NEEDS_OCCT
class RevolveExecutionTests(unittest.TestCase):
    def test_cylinder_and_oblique_frustum_keep_the_declared_axis_radii_and_base_datum(self) -> None:
        for endpoint, radii in (([0.0, 0.0, -0.4], (0.005, 0.005)), ([2.0, 3.0, 4.0], (0.8, 0.4))):
            with self.subTest(endpoint=endpoint, radii=radii), tempfile.TemporaryDirectory() as tmp:
                params = {"axis_start": [0.0, 0.0, 0.0], "axis_end": endpoint,
                          "start_radius": radii[0], "end_radius": radii[1],
                          "base_level": 0.6, "base_offset": 1.5}
                operation = GeometryOperation(
                    op_id="revolve", kind=GeometryOperationKind.REVOLVE,
                    output_object_ids=("revolve-object",), input_object_ids=(), frame_id="world",
                    semantic_binding_ids=("body-binding",),
                    parameters=tuple(GeometryParameter.create(
                        name=name, kind=GeometryParameterKind.VECTOR3 if name.startswith("axis_") else GeometryParameterKind.NUMBER,
                        value=value, unit=LengthUnit.METER,
                    ) for name, value in sorted(params.items())),
                )
                program = _program_of(operation)
                workspace = Path(tmp).resolve()
                receipt, _ = _execute(program, _synthetic_binding(program), workspace, "revolve@occt")
                self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
                shape = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])["revolve-object"].shape
                measure = occt_backend.measure_shape(shape)
                self.assertEqual((measure.valid, measure.closed, measure.solid_count), (True, True, 1))
                r0, r1 = (max(radius, 0.01) for radius in radii)
                length = math.sqrt(sum(value * value for value in endpoint))
                self.assertAlmostEqual(measure.volume, math.pi * length * (r0 * r0 + r0 * r1 + r1 * r1) / 3.0, places=7)
                midpoint = (endpoint[0] / 2, 2.1 + endpoint[1] / 2, endpoint[2] / 2)
                self.assertEqual(occt_backend.classify_program_point(shape, midpoint), "inside")


@NEEDS_OCCT
class JointIntersectionTests(unittest.TestCase):
    """``boolean_intersection`` with n inputs is the volume common to all of them, as the IR's bounds contract states.

    ``BRepAlgoAPI_Common`` with the first input as argument and the rest as tools computes A ∩ (B ∪ C)
    instead; for the boxes below that left x 0..3 with volume 3 where the contract requires x 1..2 with volume 1.
    """

    def test_three_boxes_meet_in_their_common_volume_only(self) -> None:
        program = _intersection_program(
            _box("a", [0.0, 0.0, 0.0], [3.0, 1.0, 1.0]),
            _box("b", [0.0, 0.0, 0.0], [2.0, 1.0, 1.0]),
            _box("c", [1.0, 0.0, 0.0], [2.0, 1.0, 1.0]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "meet@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.physical_object_ids, ("meet-object",))
            self.assertEqual(receipt.expected_bounds["meet-object"], {"min": [1.0, 0.0, 0.0], "max": [2.0, 1.0, 1.0]})

            # the saved solid, cold-read from disk
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            self.assertEqual(list(entries), ["meet-object"])
            meet = occt_backend.measure_shape(entries["meet-object"].shape)
            self.assertEqual((meet.valid, meet.solid_count, meet.closed, meet.face_count), (True, 1, True, 6))
            self.assertAlmostEqual(meet.volume, 1.0, places=9)
            _assert_bbox(self, meet, (1.0, 0.0, 0.0), (2.0, 1.0, 1.0), places=9)
            # inside the common slab; outside where only two of the three inputs overlap
            probes = {(1.5, 0.5, 0.5): "inside", (0.5, 0.5, 0.5): "outside", (2.5, 0.5, 0.5): "outside"}
            for point, expected in probes.items():
                self.assertEqual(occt_backend.classify_program_point(entries["meet-object"].shape, point), expected, point)
            self.assertAlmostEqual(receipt.readback["meet-object"]["volume"], 1.0, places=9)

    def test_two_boxes_still_meet_as_before(self) -> None:
        program = _intersection_program(
            _box("a", [0.0, 0.0, 0.0], [3.0, 1.0, 1.0]),
            _box("c", [1.0, 0.0, 0.0], [2.0, 1.0, 1.0]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "pair@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertAlmostEqual(receipt.readback["meet-object"]["volume"], 2.0, places=9)
            self.assertEqual(receipt.readback["meet-object"]["bbox"], {"min": [1.0, 0.0, 0.0], "max": [3.0, 1.0, 1.0]})

    def test_inputs_sharing_no_volume_fail_by_name_before_anything_is_written(self) -> None:
        program = _intersection_program(
            _box("a", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]),
            _box("b", [0.0, 0.0, 0.0], [3.0, 1.0, 1.0]),
            _box("c", [2.0, 0.0, 0.0], [1.0, 1.0, 1.0]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "empty@occt")
            self.assertIs(receipt.status, CadExecutionStatus.FAILED)
            (failure,) = receipt.failures
            self.assertEqual(failure["code"], "cad_execution.occt_build_failed")
            self.assertIn("meet (boolean_intersection)", failure["detail"])
            self.assertIn("intersection is empty", failure["detail"])
            self.assertEqual(list(workspace.iterdir()), [])


# ---------------------------------------------------------------- STEP unit statics under interleaving


@NEEDS_OCCT
class FinalSolidPairMeasurementTests(unittest.TestCase):
    """Measure only requested final objects from real STEP readback, including legitimate joints."""

    def test_cold_read_boxes_distinguish_separation_contact_and_positive_common_volume(self) -> None:
        from archflow.adapters.cad_execution import measure_occt_solid_pairs

        program = _program_of(*(_box(name, [x, 0.0, 0.0], [1.0, 1.0, 1.0]) for name, x in (
            ("body", 0.0), ("separated", 2.0), ("touching", 1.0), ("penetrating", 0.75),
        )))
        pairs = tuple(("body-object", f"{name}-object") for name in ("separated", "touching", "penetrating"))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "pairs@occt", preview=False)
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = occt_backend.read_step(workspace / receipt.exact_artifact["relative_path"], length_unit="meter")
            measured = measure_occt_solid_pairs(entries, object_pairs=pairs, length_unit="meter")
        self.assertEqual(set(measured), set(pairs))
        for pair, status, distance, volume in zip(pairs, ("separated", "contact", "penetrating"), (1.0, 0.0, 0.0), (0.0, 0.0, 0.25)):
            with self.subTest(pair=pair):
                self.assertEqual(measured[pair]["status"], status, measured[pair])
                self.assertAlmostEqual(measured[pair]["distance_m"], distance, places=8)
                self.assertAlmostEqual(measured[pair]["common_volume_m3"], volume, places=8)

    def test_millimeter_step_reports_distance_in_meters_and_volume_in_cubic_meters(self) -> None:
        from archflow.adapters.cad_execution import measure_occt_solid_pairs
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        objects = tuple(occt_backend.StepObject(name, BRepPrimAPI_MakeBox(gp_Pnt(x, 0.0, 0.0), 1000.0, 1000.0, 1000.0).Shape(), "test")
                        for name, x in (("body", 0.0), ("separated", 2000.0), ("penetrating", 750.0)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "millimeter-pairs.step"
            occt_backend.write_step(path, objects, length_unit="millimeter")
            entries = occt_backend.read_step(path, length_unit="millimeter")
            measured = measure_occt_solid_pairs(entries, object_pairs=(("body", "separated"), ("body", "penetrating")), length_unit="millimeter")
        self.assertEqual(measured[("body", "separated")]["status"], "separated")
        self.assertAlmostEqual(measured[("body", "separated")]["distance_m"], 1.0, places=8)
        self.assertEqual(measured[("body", "penetrating")]["status"], "penetrating")
        self.assertAlmostEqual(measured[("body", "penetrating")]["common_volume_m3"], 0.25, places=8)

    def test_a_window_ring_and_pane_can_touch_inside_overlapping_bounds_without_checking_consumed_bars(self) -> None:
        from archflow.adapters.cad_execution import measure_occt_solid_pairs

        program = _compile(_window_record())
        frame_op = next(op for op in program.proposal.operations if op.op_id == "frame-wall-south-window-south")
        pair = (FRAME_ID, PANE_ID)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _persisted_binding(program, "stage-solid-pair-window"), workspace, "window-pair@occt", preview=False)
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            frame, pane = (occt_backend.measure_shape(entries[name].shape) for name in pair)
            for axis in range(3):
                self.assertGreater(min(frame.bbox_max[axis], pane.bbox_max[axis]) - max(frame.bbox_min[axis], pane.bbox_min[axis]), 0.0)
            self.assertTrue(set(frame_op.input_object_ids).isdisjoint(entries))
            measured = measure_occt_solid_pairs(tuple(entries.values()), object_pairs=(pair,), length_unit="meter")
            self.assertEqual(set(measured), {pair})
            self.assertEqual(measured[pair]["status"], "contact", measured[pair])
            self.assertAlmostEqual(measured[pair]["distance_m"], 0.0, places=8)
            self.assertAlmostEqual(measured[pair]["common_volume_m3"], 0.0, places=8)
            consumed_pair = (frame_op.input_object_ids[0], FRAME_ID)
            requested = measure_occt_solid_pairs(tuple(entries.values()), object_pairs=(pair, consumed_pair), length_unit="meter")
            self.assertEqual(set(requested), {pair, consumed_pair})
            self.assertEqual(requested[pair]["status"], "contact")
            self.assertEqual(requested[consumed_pair]["status"], "unchecked")
            self.assertIsNone(requested[consumed_pair]["distance_m"])
            self.assertIsNone(requested[consumed_pair]["common_volume_m3"])
            self.assertIn(consumed_pair[0], requested[consumed_pair]["detail"])

    def test_missing_duplicate_or_null_final_objects_stay_unchecked(self) -> None:
        from archflow.adapters.cad_execution import measure_occt_solid_pairs
        from OCP.TopoDS import TopoDS_Shape

        program = _program_of(_box("body", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]), _box("other", [2.0, 0.0, 0.0], [1.0, 1.0, 1.0]))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "unchecked-pairs@occt", preview=False)
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            body, other = entries["body-object"], entries["other-object"]
            pair = (body.name, other.name)
            for case, selected_entries in (
                ("missing", (body,)), ("duplicate", (body, other, other)),
                ("null", (body, replace(other, shape=TopoDS_Shape()))),
            ):
                with self.subTest(case=case):
                    measured = measure_occt_solid_pairs(selected_entries, object_pairs=(pair,), length_unit="meter")
                    self.assertEqual(set(measured), {pair})
                    self.assertEqual(measured[pair]["status"], "unchecked", measured[pair])
                    self.assertIsNone(measured[pair]["distance_m"])
                    self.assertIsNone(measured[pair]["common_volume_m3"])
                    self.assertTrue(measured[pair]["detail"])

    def test_a_cold_read_open_surface_is_not_certified_as_nonpenetrating(self) -> None:
        from archflow.adapters.cad_execution import measure_occt_solid_pairs

        program = _program_of(_box("body", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]), _loft("surface", cap_ends=False))
        pair = ("body-object", "surface-object")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "surface-pair@occt", preview=False)
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = occt_backend.read_step(workspace / receipt.exact_artifact["relative_path"], length_unit="meter")
            measured = measure_occt_solid_pairs(entries, object_pairs=(pair,), length_unit="meter")
        self.assertEqual(measured[pair]["status"], "unchecked", measured[pair])
        self.assertIsNone(measured[pair]["distance_m"])
        self.assertIsNone(measured[pair]["common_volume_m3"])
        self.assertIn("surface-object", measured[pair]["detail"])


METER_BOX = (2.0, 4.0, 3.0)      # CAD-frame extents of the metre body: the fixture program's (2, 3, 4) as (x, z, y)
INCH_BOX = (10.0, 30.0, 20.0)    # a body that is only right when written and read as inches
INCH_IN_METERS = 0.0254
METRE_UNIT_ENTITY = "SI_UNIT($,.METRE.)"
INCH_UNIT_ENTITY = "CONVERSION_BASED_UNIT('INCH'"


@NEEDS_OCCT
class StepUnitInterleavingTests(unittest.TestCase):
    """``write.step.unit`` and ``xstep.cascade.unit`` are process-global and read by OCCT at ``Transfer``.

    Observed with the real binding: a metre export paused after setting its unit while an inch export
    completed, then resumed, produced a metre file marked INCH whose body read back as
    (0.0508, 0.1016, 0.0762) instead of (2, 4, 3).  The tests below stage exactly that interleaving with
    real OCP and temporary files: the primary operation is stopped right after it has set its unit, the
    other-unit operation is started and given a grace period, then the primary resumes.  Without a
    critical section the intruder finishes inside the pause and corrupts the primary; with it the intruder
    can only wait.  Either way both files must end up carrying their own unit and their own dimensions.
    """

    GRACE_SECONDS = 1.0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name).resolve()
        self.meter_path = self.workspace / "meter.step"
        self.inch_path = self.workspace / "inch.step"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _box(extents: tuple[float, float, float]):
        occ = occt_backend._occt()
        return occ.BRepPrimAPI.BRepPrimAPI_MakeBox(occ.gp.gp_Pnt(0.0, 0.0, 0.0), occ.gp.gp_Pnt(*extents)).Shape()

    def _write(self, path: Path, extents, unit: str):
        return lambda: occt_backend.write_step(path, [occt_backend.StepObject("body", self._box(extents), "archflow::building")], length_unit=unit)

    def _read(self, path: Path, unit: str):
        return lambda: occt_backend.read_step(path, length_unit=unit)

    def _interleave(self, primary, intruder, *, pause_after_unit: str) -> dict[str, object]:
        """Run ``primary`` until it has just set ``pause_after_unit``; start ``intruder``; give it the grace period; resume."""

        original = occt_backend._step_units
        paused, resume = threading.Event(), threading.Event()

        def pausing(occ, unit):
            original(occ, unit)
            if unit == pause_after_unit and not paused.is_set():
                paused.set()
                resume.wait(30)

        outcomes: dict[str, object] = {}

        def run(name, operation):
            try:
                outcomes[name] = operation()
            except BaseException as exc:  # reported by the test, never swallowed
                outcomes[name] = exc

        with patch.object(occt_backend, "_step_units", pausing):
            first = threading.Thread(target=run, args=("primary", primary), name="primary")
            first.start()
            self.assertTrue(paused.wait(30), "the primary operation never set its unit")
            second = threading.Thread(target=run, args=("intruder", intruder), name="intruder")
            second.start()
            second.join(self.GRACE_SECONDS)
            resume.set()
            first.join(60)
            second.join(60)
        self.assertFalse(first.is_alive() or second.is_alive(), "an interleaved STEP operation did not finish")
        for name, outcome in outcomes.items():
            if isinstance(outcome, BaseException):
                raise AssertionError(f"{name} failed: {outcome!r}") from outcome
        return outcomes

    def _assert_meter_file(self) -> None:
        text = self.meter_path.read_text()
        self.assertIn(METRE_UNIT_ENTITY, text)
        self.assertNotIn("INCH", text, "the metre file was written under the inch export's unit")
        (entry,) = occt_backend.read_step(self.meter_path, length_unit="meter")
        measure = occt_backend.measure_shape(entry.shape)
        self.assertEqual((entry.name, measure.valid, measure.solid_count, measure.closed), ("body", True, 1, True))
        _assert_bbox(self, measure, (0.0, 0.0, 0.0), METER_BOX, places=9)
        self.assertAlmostEqual(measure.volume, 24.0, places=9)

    def _assert_inch_file(self) -> None:
        text = self.inch_path.read_text()
        self.assertIn(INCH_UNIT_ENTITY, text)
        (entry,) = occt_backend.read_step(self.inch_path, length_unit="inch")
        measure = occt_backend.measure_shape(entry.shape)
        self.assertEqual((entry.name, measure.valid, measure.solid_count, measure.closed), ("body", True, 1, True))
        _assert_bbox(self, measure, (0.0, 0.0, 0.0), INCH_BOX, places=9)
        self.assertAlmostEqual(measure.volume, 6000.0, places=9)

    def test_an_inch_export_during_a_paused_metre_export_leaves_both_files_in_their_own_unit(self) -> None:
        self._interleave(
            self._write(self.meter_path, METER_BOX, "meter"),
            self._write(self.inch_path, INCH_BOX, "inch"),
            pause_after_unit="meter",
        )
        self._assert_meter_file()
        self._assert_inch_file()
        # the reported corruption, stated so a regression is recognised by its numbers
        (entry,) = occt_backend.read_step(self.meter_path, length_unit="meter")
        self.assertNotAlmostEqual(occt_backend.measure_shape(entry.shape).bbox_max[0], METER_BOX[0] * INCH_IN_METERS, places=6)

    def test_an_inch_readback_during_a_paused_metre_readback_scales_neither_file(self) -> None:
        self._write(self.meter_path, METER_BOX, "meter")()
        self._write(self.inch_path, INCH_BOX, "inch")()
        outcomes = self._interleave(
            self._read(self.meter_path, "meter"),
            self._read(self.inch_path, "inch"),
            pause_after_unit="meter",
        )
        (meter_entry,) = outcomes["primary"]
        (inch_entry,) = outcomes["intruder"]
        _assert_bbox(self, occt_backend.measure_shape(meter_entry.shape), (0.0, 0.0, 0.0), METER_BOX, places=9)
        _assert_bbox(self, occt_backend.measure_shape(inch_entry.shape), (0.0, 0.0, 0.0), INCH_BOX, places=9)

    def test_an_inch_readback_during_a_paused_metre_export_does_not_reunit_the_export(self) -> None:
        self._write(self.inch_path, INCH_BOX, "inch")()
        outcomes = self._interleave(
            self._write(self.meter_path, METER_BOX, "meter"),
            self._read(self.inch_path, "inch"),
            pause_after_unit="meter",
        )
        (inch_entry,) = outcomes["intruder"]
        _assert_bbox(self, occt_backend.measure_shape(inch_entry.shape), (0.0, 0.0, 0.0), INCH_BOX, places=9)
        self._assert_meter_file()

    def test_a_metre_readback_during_a_paused_inch_export_keeps_the_inch_file_in_inches(self) -> None:
        self._write(self.meter_path, METER_BOX, "meter")()
        outcomes = self._interleave(
            self._write(self.inch_path, INCH_BOX, "inch"),
            self._read(self.meter_path, "meter"),
            pause_after_unit="inch",
        )
        (meter_entry,) = outcomes["intruder"]
        _assert_bbox(self, occt_backend.measure_shape(meter_entry.shape), (0.0, 0.0, 0.0), METER_BOX, places=9)
        self._assert_inch_file()


@NEEDS_OCCT
class CapabilityBoundaryTests(unittest.TestCase):
    """An operation outside the realized vocabulary fails by name, before anything is written."""

    def _refused(self, program: CompiledGeometryProgram) -> CadCapabilityError:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            with self.assertRaises(CadCapabilityError) as context:
                _execute(program, _synthetic_binding(program), workspace, "refused@occt")
            self.assertEqual(list(workspace.iterdir()), [])
        self.assertIsInstance(context.exception, CadExecutionError)
        return context.exception

    def test_a_radial_array_is_refused_by_operation_and_kind(self) -> None:
        seed = _box("seed", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0])
        error = self._refused(_program_of(seed, _radial_array("ring", seed)))
        self.assertEqual((error.op_id, error.kind), ("ring", "radial_array"))
        self.assertIn("block instancing", str(error))

    def test_a_linear_array_is_realized_as_real_copies_at_each_step(self) -> None:
        """The fixture's ``row``: two copies of the 2 x 3 x 4 body, 3 m apart along x, delivered as one object."""

        program = _synthetic_program(array=True)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "row@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertEqual(receipt.physical_object_ids, ("row-object",))                # the seed was consumed
            self.assertEqual(receipt.expected_bounds["row-object"], {"min": [0.0, 0.0, 0.0], "max": [5.0, 4.0, 3.0]})
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            self.assertEqual(list(entries), ["row-object"])
            row = occt_backend.measure_shape(entries["row-object"].shape)
            self.assertEqual((row.valid, row.solid_count, row.closed, row.face_count), (True, 2, True, 12))
            self.assertAlmostEqual(row.volume, 48.0, places=9)
            _assert_bbox(self, row, (0.0, 0.0, 0.0), (5.0, 4.0, 3.0), places=9)          # program x-step 3 stays x in CAD
            probes = {(1.0, 1.0, 1.0): "inside", (2.5, 1.0, 1.0): "outside", (4.0, 1.0, 1.0): "inside", (6.0, 1.0, 1.0): "outside"}
            for point, expected in probes.items():
                self.assertEqual(occt_backend.classify_program_point(entries["row-object"].shape, point), expected, point)
            self.assertEqual(entries["row-object"].layers, ("archflow::body-component",))
            self.assertEqual(receipt.readback["row-object"]["solid_count"], 2)

    def test_a_vertical_step_is_converted_to_the_cad_frame_once(self) -> None:
        seed = _box("seed", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0])
        program = _program_of(seed, _array("stack", seed, count=3, step=[0.0, 2.0, 0.0]))   # up in the program frame
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "stack@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            entries = _entries_by_name(workspace / receipt.exact_artifact["relative_path"])
            stack = occt_backend.measure_shape(entries["stack-object"].shape)
            self.assertEqual(stack.solid_count, 3)
            _assert_bbox(self, stack, (0.0, 0.0, 0.0), (1.0, 1.0, 5.0), places=9)          # CAD z is program y-up
            for height, expected in ((0.5, "inside"), (1.5, "outside"), (2.5, "inside"), (4.5, "inside"), (5.5, "outside")):
                self.assertEqual(occt_backend.classify_program_point(entries["stack-object"].shape, (0.5, height, 0.5)), expected, height)

    def test_coincident_copies_are_refused(self) -> None:
        seed = _box("seed", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0])
        error = self._refused(_program_of(seed, _array("heap", seed, count=2, step=[0.0, 0.0, 0.0])))
        self.assertEqual((error.op_id, error.kind), ("heap", "array"))
        self.assertIn("zero step", str(error))

    def test_an_interpolated_loft_is_refused(self) -> None:
        error = self._refused(_single_operation_program(_loft("smooth", profile_basis="interpolated")))
        self.assertEqual((error.op_id, error.kind), ("smooth", "loft"))
        self.assertIn("polyline only", str(error))

    def test_a_capped_polyline_loft_is_realized(self) -> None:
        program = _single_operation_program(_loft("prism"))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "prism@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertAlmostEqual(receipt.readback["prism-object"]["volume"], 2.0, places=9)
            self.assertEqual((receipt.readback["prism-object"]["free_edge_count"], receipt.readback["prism-object"]["declared_delivery"]), (0, "closed_solid"))
            self.assertEqual(receipt.exact_artifact["deliveries"], {"prism-object": "closed_solid"})
            self.assertEqual(receipt.exact_artifact["carries"][2], "exact B-rep in the CAD frame and the program unit: 1 closed solid object(s)")

    def test_an_uncapped_polyline_loft_is_realized_as_the_open_lofted_surface(self) -> None:
        """``cap_ends`` false is ThruSections' isSolid false: the four side faces, open at both squares, no volume."""

        program = _single_operation_program(_loft("tube", cap_ends=False))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "tube@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            row = receipt.readback["tube-object"]
            self.assertEqual((row["valid"], row["solid_count"], row["closed"], row["face_count"], row["free_edge_count"], row["volume"], row["declared_delivery"]),
                             (True, 0, False, 4, 8, None, "open_surface"))
            for axis, (low, high) in enumerate(((0.0, 1.0), (0.0, 1.0), (0.0, 2.0))):               # program y 0..2 is CAD z
                self.assertAlmostEqual(row["bbox"]["min"][axis], low, places=6)
                self.assertAlmostEqual(row["bbox"]["max"][axis], high, places=6)
            self.assertEqual(receipt.exact_artifact["deliveries"], {"tube-object": "open_surface"})
            self.assertIn("1 open surface object(s) from planar faces or uncapped lofts", receipt.exact_artifact["carries"][2])

    def test_an_open_surface_is_not_a_solid_for_the_array_or_the_booleans(self) -> None:
        """The solid-only checks stay: repeating or fusing an open loft is a build failure, never a solid."""

        tube = _loft("tube", cap_ends=False)
        program = _program_of(tube, _array("tubes", tube, count=2, step=[3.0, 0.0, 0.0]))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "tubes@occt")
            self.assertIs(receipt.status, CadExecutionStatus.FAILED)
            self.assertEqual([f["code"] for f in receipt.failures], ["cad_execution.occt_build_failed"])
            self.assertIn("holds no solid to repeat", receipt.failures[0]["detail"])
            self.assertEqual(list(workspace.iterdir()), [])
        union = GeometryOperation(op_id="fused", kind=GeometryOperationKind.BOOLEAN_UNION, output_object_ids=("fused-object",),
                                  input_object_ids=("seed-object", "tube-object"), frame_id="world", parameters=(), semantic_binding_ids=("body-binding",))
        program = _program_of(tube, _box("seed", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]), union)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "fused@occt")
            self.assertIs(receipt.status, CadExecutionStatus.FAILED)
            self.assertEqual([f["code"] for f in receipt.failures], ["cad_execution.occt_build_failed"])
            self.assertEqual(list(workspace.iterdir()), [])

    def test_a_binding_for_another_program_is_refused_mechanically(self) -> None:
        program, other = _synthetic_program(), _synthetic_program(array=True)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            with self.assertRaisesRegex(CadExecutionError, "digest differs from binding"):
                _execute(program, _synthetic_binding(other), workspace, "cross@occt")
            self.assertEqual(list(workspace.iterdir()), [])


class ImportBoundaryTests(unittest.TestCase):
    def test_the_execution_owner_imports_without_loading_occt_or_rhino3dm(self) -> None:
        """Ordinary create/save/reopen paths import the owner; they must not pay for a kernel."""

        completed = subprocess.run(
            [PYTHON, "-c", "import sys, archflow.adapters.cad_execution; print(sorted(m for m in sys.modules if m in ('OCP', 'rhino3dm')))"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
            timeout=120,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "[]")

    def test_the_backend_names_what_it_does_not_realize(self) -> None:
        self.assertEqual(
            occt_backend.SUPPORTED_OPERATION_KINDS,
            {"solid", "revolve", "extrusion", "planar_surface", "loft", "boolean_union", "boolean_difference", "boolean_intersection", "array"},
        )
        for unsupported in ("radial_array", "transform", "sweep", "curve", "asset_instance"):
            self.assertNotIn(unsupported, occt_backend.SUPPORTED_OPERATION_KINDS)


@NEEDS_OCCT
class LongExportPathTests(unittest.TestCase):
    """A run's export workspace is long, and every file call has to survive it.

    Project, run id, stage, seat and attempt together pass 260 characters
    easily. Windows refuses an ordinary name that long unless the process opts
    in, and the Rhino host's own interpreter does not: a real export wrote its
    model and then failed to create the completion marker beside it, so the
    supervisor saw a run that never reported. These exercise the actual files
    at that length rather than the shape of the generated text.
    """

    def deep_workspace(self) -> Path:
        """A real directory whose child paths are past the ordinary limit."""

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cad_execution.long_path(root), True)
        workspace = root
        while len(str(workspace)) < 230:
            workspace = workspace / "cad-studio-candidate-seat-portico"
        cad_execution.long_path(workspace).mkdir(parents=True, exist_ok=True)
        return workspace

    def script_helper(self):
        """The ``_long`` the emitted scripts actually carry, as a callable."""

        namespace: dict[str, object] = {"Path": Path}
        exec(chr(10).join(cad_program.LONG_PATH_HELPER_SOURCE), namespace)
        return namespace["_long"]

    def test_the_emitted_helper_creates_reads_and_removes_a_long_file(self) -> None:
        workspace = self.deep_workspace()
        long = self.script_helper()
        marker = workspace / "studio-candidate-seat-portico@0123456789ab.archflow-completion.json"
        self.assertGreater(len(str(marker)), 260, str(marker))

        # Exactly what the script does with the marker: exclusive create,
        # write, and - for the raw file - read back and remove.
        with long(marker).open("x", encoding="utf-8", newline=chr(10)) as stream:
            stream.write('{"status":"succeeded"}')
        self.assertTrue(long(marker).exists())
        self.assertEqual(long(marker).read_bytes(), b'{"status":"succeeded"}')
        # The extended-length name is a Windows spelling; elsewhere the same
        # path is already the one the host can open, and stays as it is.
        if os.name == "nt":
            self.assertEqual(str(long(marker))[:4], chr(92) * 2 + "?" + chr(92))
        else:
            self.assertEqual(long(marker), Path(os.path.abspath(marker)))
        long(marker).unlink()
        self.assertFalse(long(marker).exists())
        # The name that gets persisted is still the ordinary one.
        self.assertEqual(marker.name, "studio-candidate-seat-portico@0123456789ab.archflow-completion.json")

    def test_an_export_plan_is_written_and_read_back_at_that_length(self) -> None:
        workspace = self.deep_workspace()
        program = _compile(_window_record())
        binding = _persisted_binding(program, "stage-long-path")
        receipt, _ = _execute(program, binding, workspace, "studio-candidate-seat-portico@longpath")

        self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
        step = workspace / receipt.exact_artifact["relative_path"]
        self.assertGreater(len(str(step)), 260, str(step))
        # The STEP was written, cold-read and split where the run actually
        # keeps it, and each single-object file read back to the same shape.
        objects = split_step_objects(step, destination=workspace, length_unit="meter")
        self.assertEqual(len(objects), len(receipt.physical_object_ids))
        for item in objects:
            source = workspace / item.file_name
            self.assertGreater(len(str(source)), 260, str(source))
            self.assertEqual(
                hashlib.sha256(cad_execution.long_path(source).read_bytes()).hexdigest(), item.sha256
            )

        plan = prepare_rhino_three_dm_export(
            program, binding=binding, speculative_workspace=workspace,
            artifact_name="studio-candidate-seat-portico@longpath.work.3dm",
            readback_tolerance=0.003, provenance={"export_path": "work-model"},
            step_import=StepImportSource(
                step_path=step, step_sha256=hashlib.sha256(cad_execution.long_path(step).read_bytes()).hexdigest(),
                objects=objects,
            ),
        )

        # The script exists at that depth and the supervisor can hash it back.
        self.assertGreater(len(str(plan.script_path)), 260, str(plan.script_path))
        script = cad_execution.long_path(plan.script_path).read_text(encoding="utf-8")
        self.assertIn("def _long(_path):", script)
        # Every file the host touches is named the way that length needs.
        for call in (
            "_long(_marker_path).open('x'",
            "str(_long(_raw_path))",
            "str(_long(_output_path))",
            "_long(_raw_path).unlink()",
            "_path = _long(_script_directory / file_name)",
        ):
            self.assertIn(call, script)

    def test_a_saved_work_model_is_verified_at_that_length(self) -> None:
        import rhino3dm

        workspace = self.deep_workspace()
        occ = occt_backend._occt()
        box = occ.BRepPrimAPI.BRepPrimAPI_MakeBox(
            occt_backend._gp_point(occ, (0.0, 0.0, 0.0)), occt_backend._gp_point(occ, (2.0, 3.0, 1.0))
        ).Shape()
        step = workspace / "studio-candidate-seat-portico@longpath-source.step"
        occt_backend.write_step(
            cad_execution.long_path(step),
            [occt_backend.StepObject(object_id="obj-block", shape=box, layer="archflow")],
            length_unit="meter",
        )
        objects = split_step_objects(step, destination=workspace, length_unit="meter")
        source = StepImportSource(
            step_path=step,
            step_sha256=hashlib.sha256(cad_execution.long_path(step).read_bytes()).hexdigest(),
            objects=objects,
        )

        saved = workspace / "studio-candidate-seat-portico@longpath.work.3dm"
        self.assertGreater(len(str(saved)), 260, str(saved))
        model = rhino3dm.File3dm()
        model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = "obj-block"
        model.Objects.AddBrep(rhino3dm.Brep.CreateFromBoundingBox(
            rhino3dm.BoundingBox(rhino3dm.Point3d(0, 0, 0), rhino3dm.Point3d(2, 1, 3))
        ), attributes)
        self.assertTrue(model.Write(str(cad_execution.long_path(saved)), 7))

        self.assertEqual(
            cad_execution.verify_work_model_geometry(saved, source),
            ({"object_id": "obj-block", "objects": 1, "solids": 1, "faces": 6, "closed": True},),
        )


@NEEDS_OCCT
class StepWorkModelImportTests(unittest.TestCase):
    """What an editable work model is made from: the exported STEP itself.

    A host that imports these files gets the geometry the STEP already holds,
    one named shape at a time, so the object a person edits in Rhino is the
    same solid the engineering STEP carries - including its openings.
    """

    def _exported(self, workspace: Path, stem: str):
        """One real OCCT export of the authored wall with a through window."""

        program = _compile(_window_record())
        receipt, _ = _execute(program, _persisted_binding(program, f"stage-{stem}"), workspace, f"{stem}@occt")
        self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
        return program, workspace / receipt.exact_artifact["relative_path"]

    def test_each_named_shape_becomes_its_own_step_with_the_same_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            program, step = self._exported(workspace, "work-split")
            destination = workspace / "import"
            destination.mkdir()
            objects = split_step_objects(step, destination=destination, length_unit="meter")

            source = _entries_by_name(step)
            self.assertEqual([item.object_id for item in objects], sorted(source))
            for item in objects:
                measured = occt_backend.measure_shape(source[item.object_id].shape)
                self.assertEqual(
                    (item.solid_count, item.face_count, item.closed),
                    (measured.solid_count, measured.face_count, measured.closed),
                    item.object_id,
                )
                self.assertAlmostEqual(item.volume, measured.volume, places=9)
                # The file on disk is the one the script will read, and it
                # hashes to what the plan says it does.
                written = destination / item.file_name
                self.assertTrue(written.is_file())
                self.assertEqual(
                    hashlib.sha256(written.read_bytes()).hexdigest(), item.sha256
                )

            cut = next(item for item in objects if item.object_id == "obj-wall-south-cut")
            solid = 6.0 * 0.3 * 2.97
            self.assertTrue(cut.closed and cut.solid_count == 1)
            # The opening is still missing from the solid: a healed or dropped
            # void would put the volume back up at the plain wall's.
            self.assertLess(cut.volume, solid - 0.5)
            self.assertGreater(cut.face_count, 6)

    def test_a_repeated_or_foreign_name_is_refused_rather_than_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            program, step = self._exported(workspace, "work-refuse")
            shapes = [entry.shape for entry in occt_backend.read_step(step, length_unit="meter")][:2]
            destination = workspace / "import"
            destination.mkdir()

            # Two shapes under one name cannot say which object each is.
            repeated = workspace / "repeated.step"
            occt_backend.write_step(
                repeated,
                [occt_backend.StepObject(object_id="obj-same", shape=shape, layer="archflow") for shape in shapes],
                length_unit="meter",
            )
            with self.assertRaises(CadExecutionError) as repeated_refusal:
                split_step_objects(repeated, destination=destination, length_unit="meter")
            self.assertIn("obj-same", str(repeated_refusal.exception))

            # A file written without object ids reads back under the STEP
            # translator's own default label. It is a name, but it is not one
            # of this program's objects, and the plan says so instead of
            # pairing it with whatever object happens to be left.
            foreign = workspace / "foreign.step"
            occt_backend.write_step(
                foreign,
                [occt_backend.StepObject(object_id="", shape=shapes[0], layer="archflow")],
                length_unit="meter",
            )
            stray = split_step_objects(foreign, destination=destination, length_unit="meter")
            self.assertTrue(stray[0].object_id.startswith("Open CASCADE"), stray[0].object_id)
            real = split_step_objects(step, destination=destination, length_unit="meter")
            with self.assertRaises(CadTranslationError) as foreign_refusal:
                prepare_rhino_three_dm_export(
                    program, binding=_persisted_binding(program, "stage-work-foreign"),
                    speculative_workspace=destination, artifact_name="foreign.work.3dm",
                    readback_tolerance=0.003, provenance={"export_path": "work-model"},
                    step_import=StepImportSource(
                        step_path=foreign, step_sha256=hashlib.sha256(foreign.read_bytes()).hexdigest(),
                        objects=(*real, *stray),
                    ),
                )
            self.assertIn(stray[0].object_id, str(foreign_refusal.exception))

    def test_an_import_source_that_changed_after_the_split_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            program, step = self._exported(workspace, "work-bytes")
            objects = split_step_objects(step, destination=workspace, length_unit="meter")
            source = StepImportSource(
                step_path=step, step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(), objects=objects
            )
            replaced = workspace / objects[0].file_name
            replaced.write_text(replaced.read_text(encoding="utf-8") + "\n/* edited */\n", encoding="utf-8")
            with self.assertRaises(CadExecutionError) as refusal:
                prepare_rhino_three_dm_export(
                    program, binding=_persisted_binding(program, "stage-work-bytes"),
                    speculative_workspace=workspace, artifact_name="work-bytes.work.3dm",
                    readback_tolerance=0.003, provenance={"export_path": "work-model"},
                    step_import=source,
                )
            self.assertIn(objects[0].object_id, str(refusal.exception))
            self.assertIn("changed after it was read", str(refusal.exception))

    def test_the_import_plan_reads_every_file_and_keeps_the_rebuild_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            program, step = self._exported(workspace, "work-plan")
            binding = _persisted_binding(program, "stage-work-plan")
            objects = split_step_objects(step, destination=workspace, length_unit="meter")
            source = StepImportSource(
                step_path=step, step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(), objects=objects
            )
            imported = prepare_rhino_three_dm_export(
                program, binding=binding, speculative_workspace=workspace,
                artifact_name="work-plan.work.3dm", readback_tolerance=0.003,
                provenance={"export_path": "work-model", "source_step_sha256": source.step_sha256},
                step_import=source,
            )
            rebuilt = prepare_rhino_three_dm_export(
                program, binding=binding, speculative_workspace=workspace,
                artifact_name="work-plan.rebuild.3dm", readback_tolerance=0.003,
                provenance={"export_path": "rebuild"},
            )

            script = imported.script_path.read_text(encoding="utf-8")
            self.assertIn("Rhino.FileIO.FileStp.Read", script)
            for item in objects:
                self.assertIn(
                    f"_import_one({item.object_id!r}, {item.file_name!r}, {item.sha256!r})", script
                )
            # Nothing is modelled again: the import script carries no geometry
            # command of the program translator's vocabulary.
            for built in ("rs.AddBox", "rs.ExtrudeCurveStraight", "rs.AddLoftSrf", "rs.BooleanDifference"):
                self.assertNotIn(built, script)
            # It is verified as the same document: same objects, same semantics,
            # same expected bounds and counts as a rebuild of the same program.
            self.assertEqual(imported.physical_object_ids, rebuilt.physical_object_ids)
            self.assertEqual(imported.expected_semantics, rebuilt.expected_semantics)
            self.assertEqual(imported.expected_bounds, rebuilt.expected_bounds)
            self.assertEqual(imported.expected_object_counts, rebuilt.expected_object_counts)
            self.assertEqual(imported.expected_layer_colors, rebuilt.expected_layer_colors)
            self.assertIn(
                ("archflow:source_step_sha256", source.step_sha256),
                imported.expected_document_user_text,
            )

    def test_each_part_is_read_in_its_own_document_so_no_import_layer_arrives(self) -> None:
        """A STEP read brings its own layer table; the delivery must not get it.

        The real host showed what that costs: beside the program's own
        ``archflow::portico`` child layer, the import left an empty top-level
        layer whose literal name was ``archflow::portico`` too, and the saved
        document then had two layers of the same full path - which the
        readback refuses, rightly. So each file is read into a document of its
        own and only its geometry is carried over.
        """

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            program, step = self._exported(workspace, "work-import-layers")
            objects = split_step_objects(step, destination=workspace, length_unit="meter")
            plan = prepare_rhino_three_dm_export(
                program, binding=_persisted_binding(program, "stage-work-import-layers"),
                speculative_workspace=workspace, artifact_name="work-import-layers.work.3dm",
                readback_tolerance=0.003, provenance={"export_path": "work-model"},
                step_import=StepImportSource(
                    step_path=step, step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(), objects=objects,
                ),
            )
            script = plan.script_path.read_text(encoding="utf-8")

            # The read happens in a document of this script's own making, and
            # the delivered document is never handed to the STEP reader.
            self.assertIn("_source = Rhino.RhinoDoc.CreateHeadless(None)", script)
            self.assertIn("Rhino.FileIO.FileStp.Read(str(_path), _source, _import_options)", script)
            self.assertNotIn("FileStp.Read(str(_path), _document", script)
            # Units before geometry: a headless document starts in millimetres.
            self.assertIn(
                "_source.AdjustModelUnitSystem(_document.ModelUnitSystem, False)", script
            )
            self.assertLess(
                script.index("_source.AdjustModelUnitSystem"),
                script.index("Rhino.FileIO.FileStp.Read(str(_path), _source"),
            )
            # Geometry is copied object by object, with attributes of this
            # document's own - no layer, name or material comes from the STEP.
            for line in (
                "for _object in list(_source.Objects):",
                "_attributes = Rhino.DocObjects.ObjectAttributes()",
                "_document.Objects.AddBrep(_geometry, _attributes)",
                "_source.Dispose()",
            ):
                self.assertIn(line, script)
            # A named shape may arrive as several B-reps and stays one object
            # with that many under it.
            self.assertIn("counts[object_id] = len(_added)", script)
            # The layers the document ends up with are the program's own.
            for layer_path, _color in plan.expected_layer_colors:
                self.assertIn(f"rs.AddLayer({layer_path!r}", script)

    def test_the_delivery_s_own_retained_material_is_what_the_import_wears(self) -> None:
        """A material the export actually wrote, not one derived a second time."""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            program, step = self._exported(workspace, "work-source-materials")
            objects = split_step_objects(step, destination=workspace, length_unit="meter")
            # What this run's own receipt retained beside its preview: a named
            # bronze frame and a darker, more transparent glass than the
            # role default.
            retained = {
                "obj-frame-wall-south-window-south": {
                    "name": "bronze-anodised", "diffuse": [120, 85, 40], "transparency": 0.0,
                },
                "obj-glazing-wall-south-window-south": {
                    "name": "low-e-glass", "diffuse": [90, 140, 160], "transparency": 0.82,
                },
            }
            plan = prepare_rhino_three_dm_export(
                program, binding=_persisted_binding(program, "stage-work-source-materials"),
                speculative_workspace=workspace, artifact_name="work-source.work.3dm",
                readback_tolerance=0.003, provenance={"export_path": "work-model"},
                step_import=StepImportSource(
                    step_path=step, step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(), objects=objects,
                ),
                source_materials=retained,
            )
            script = plan.script_path.read_text(encoding="utf-8")

            self.assertIn('"name": "low-e-glass"', script)
            self.assertIn('"transparency": 0.82', script)
            self.assertIn('"name": "bronze-anodised"', script)
            self.assertIn('"diffuse": [120, 85, 40]', script)
            # The role default is not what this document gets.
            self.assertNotIn(f'"name": "{cad_execution._GLAZING_FALLBACK.name}"', script)
            # Every material is applied before any metadata, and this
            # delivery's own is the last one applied: assigning a material in
            # Rhino replaces the object's attributes, so a material written
            # after the user text would leave the object with none - which is
            # exactly how a real six-part export lost the semantics of its two
            # materialed objects and failed its witness count.
            native = script.index("_assign_native_material(_g, _meta)")
            declared = script.index("if _declared is not None: _assign_work_material(_g, _declared)")
            named = script.index("rs.ObjectName(_g, _oid)")
            user_text = script.index("rs.SetUserText(_g, _k, _meta['user_text'][_k])")
            self.assertLess(native, declared)
            self.assertLess(declared, named)
            self.assertLess(named, user_text)
            # Nothing re-applies a material after the metadata is written.
            self.assertLess(script.rindex("_assign_work_material(_g"), named)
            self.assertLess(script.rindex("_assign_native_material(_g"), named)

    def test_the_imported_document_wears_the_same_materials_as_the_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            program, step = self._exported(workspace, "work-materials")
            objects = split_step_objects(step, destination=workspace, length_unit="meter")
            plan = prepare_rhino_three_dm_export(
                program, binding=_persisted_binding(program, "stage-work-materials"),
                speculative_workspace=workspace, artifact_name="work-materials.work.3dm",
                readback_tolerance=0.003, provenance={"export_path": "work-model"},
                step_import=StepImportSource(
                    step_path=step, step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(), objects=objects,
                ),
            )
            script = plan.script_path.read_text(encoding="utf-8")

            # The glazing of the window this program builds is glass in the
            # mesh preview; the editable model carries the same material, and
            # the glass is still see-through rather than a solid pane.
            glass = cad_execution._GLAZING_FALLBACK
            self.assertGreater(glass.transparency, 0.0)
            self.assertIn('"obj-glazing-wall-south-window-south"', script)
            self.assertIn(f'"transparency": {glass.transparency}', script)
            self.assertIn(f'"name": "{glass.name}"', script)
            self.assertIn(f'"diffuse": {list(glass.diffuse)}', script)
            self.assertIn("_material.Transparency = _declared.get('transparency', 0.0)", script)
            # The frame of the same window is its own material, not the glass.
            self.assertIn('"obj-frame-wall-south-window-south"', script)
            self.assertIn('"name": "frame"', script)
            # Layers are the program's own, the same paths the preview used.
            for layer_path, _color in plan.expected_layer_colors:
                self.assertIn(f"rs.AddLayer({layer_path!r}", script)


@NEEDS_OCCT
class WorkModelReadbackTests(unittest.TestCase):
    """The saved work model is read cold and compared with the source shape."""

    def _source(self, workspace: Path):
        """One exported box, split into the file an import would read."""

        occ = occt_backend._occt()
        box = occ.BRepPrimAPI.BRepPrimAPI_MakeBox(
            occt_backend._gp_point(occ, (0.0, 0.0, 0.0)), occt_backend._gp_point(occ, (2.0, 3.0, 1.0))
        ).Shape()
        step = workspace / "one.step"
        occt_backend.write_step(
            step, [occt_backend.StepObject(object_id="obj-block", shape=box, layer="archflow")],
            length_unit="meter",
        )
        objects = split_step_objects(step, destination=workspace, length_unit="meter")
        return StepImportSource(
            step_path=step, step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(), objects=objects
        ), box

    def _write(self, path: Path, geometry, name: str = "obj-block") -> None:
        import rhino3dm

        model = rhino3dm.File3dm()
        model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        if isinstance(geometry, rhino3dm.Mesh):
            model.Objects.AddMesh(geometry, attributes)
        else:
            model.Objects.AddBrep(geometry, attributes)
        self.assertTrue(model.Write(str(path), 7))

    def test_a_matching_brep_passes_and_reports_what_it_found(self) -> None:
        import rhino3dm

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            source, _ = self._source(workspace)
            saved = workspace / "match.3dm"
            box = rhino3dm.BoundingBox(rhino3dm.Point3d(0, 0, 0), rhino3dm.Point3d(2, 1, 3))
            self._write(saved, rhino3dm.Brep.CreateFromBoundingBox(box))
            self.assertEqual(
                cad_execution.verify_work_model_geometry(saved, source),
                ({"object_id": "obj-block", "objects": 1, "solids": 1, "faces": 6, "closed": True},),
            )

    def test_a_mesh_or_a_changed_solid_is_not_an_editable_work_model(self) -> None:
        import rhino3dm

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            source, shape = self._source(workspace)

            mesh_file = workspace / "mesh.3dm"
            vertices, triangles = occt_backend.tessellate_shape(shape, linear_deflection=0.05)
            mesh = rhino3dm.Mesh()
            for x, y, z in vertices:
                mesh.Vertices.Add(x, y, z)
            for a, b, c in triangles:
                mesh.Faces.AddFace(a, b, c)
            self._write(mesh_file, mesh)
            with self.assertRaises(CadExecutionError) as mesh_refusal:
                cad_execution.verify_work_model_geometry(mesh_file, source)
            self.assertIn("never a mesh", str(mesh_refusal.exception))

            missing = workspace / "missing.3dm"
            self._write(missing, rhino3dm.Brep.CreateFromBoundingBox(
                rhino3dm.BoundingBox(rhino3dm.Point3d(0, 0, 0), rhino3dm.Point3d(2, 1, 3))
            ), name="obj-other")
            with self.assertRaises(CadExecutionError) as name_refusal:
                cad_execution.verify_work_model_geometry(missing, source)
            self.assertIn("no object of that name", str(name_refusal.exception))

            # A solid that arrived as a single surface: not the closed shape
            # the STEP holds, whatever its outline looks like.
            open_box = workspace / "open.3dm"
            brep = rhino3dm.Brep.CreateFromBoundingBox(
                rhino3dm.BoundingBox(rhino3dm.Point3d(0, 0, 0), rhino3dm.Point3d(2, 1, 3))
            )
            self._write(open_box, rhino3dm.Brep.CreateFromSurface(brep.Faces[0].UnderlyingSurface()))
            with self.assertRaises(CadExecutionError) as open_refusal:
                cad_execution.verify_work_model_geometry(open_box, source)
            self.assertIn("closed solid", str(open_refusal.exception))


if __name__ == "__main__":
    unittest.main()
