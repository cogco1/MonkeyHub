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

import json
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

from archflow.adapters import occt_backend
from archflow.adapters.cad_execution import (
    CadCapabilityError,
    CadExecutionError,
    CadExecutionStatus,
    CadProgramBinding,
    OcctExecutionReceipt,
    RhinoCadProgramBinding,
    execute_occt_export,
)
from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.capabilities.element_producers import ProductionContext, element_rows_of, produce_rows
from archflow.capabilities.reference_resolver import ReferenceContext
from archflow.compilers.geometry import CompiledGeometryObject, CompiledGeometryProgram, compile_geometry_program
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import stage_geometry_program
from archflow.project.refs import BranchRef
from archflow.state.geometry_program import (
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
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


def _stair_record() -> StateRecord:
    """The fixture record with its plinth and wall replaced by one lofted flight."""

    payload = json.loads(json.dumps(RECORD_PAYLOAD))
    payload["entities"] = [
        entity for entity in payload["entities"] if entity["entity_id"] not in ("plinth", "wall-south")
    ] + [STAIR_ELEMENT]
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

    def test_an_interpolated_or_uncapped_loft_is_refused(self) -> None:
        error = self._refused(_single_operation_program(_loft("smooth", profile_basis="interpolated")))
        self.assertEqual((error.op_id, error.kind), ("smooth", "loft"))
        error = self._refused(_single_operation_program(_loft("open", cap_ends=False)))
        self.assertIn("closed-solid", str(error))

    def test_a_capped_polyline_loft_is_realized(self) -> None:
        program = _single_operation_program(_loft("prism"))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt, _ = _execute(program, _synthetic_binding(program), workspace, "prism@occt")
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertAlmostEqual(receipt.readback["prism-object"]["volume"], 2.0, places=9)

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
            {"solid", "extrusion", "loft", "boolean_union", "boolean_difference", "boolean_intersection", "array"},
        )
        for unsupported in ("radial_array", "transform", "revolve", "sweep", "curve", "asset_instance"):
            self.assertNotIn(unsupported, occt_backend.SUPPORTED_OPERATION_KINDS)


if __name__ == "__main__":
    unittest.main()
