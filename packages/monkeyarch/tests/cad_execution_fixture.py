"""The CAD suite's synthetic program, binding and Rhino host pieces, and OCCT program builders.

A copy of MonkeyCAD's own fixture (packages/monkeycad/tests/cad_fixture.py)
and of the OCCT execution tests' program builders
(tests/integration/test_occt_execution.py), for the MonkeyArch tests that run a
CAD backend under the runner: the backend contract (test_cad_backend_contract.py),
the Blender slice (test_blender_cad.py) and the incremental patch
(test_cad_patch.py). A package's tests cannot import another suite's.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from monkeycad.execution import RhinoCadProgramBinding
from monkeycad.formats.three_dm_inspector import ThreeDmInspection
from archflow.project.refs import BranchRef, ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.state.geometry_program import (
    CompiledGeometryObject,
    CompiledGeometryProgram,
)
from archflow.state.geometry_program import (
    AffineTransform,
    CoordinateFrame,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    SemanticBinding,
)


BASE_SHA = "1" * 64
DESIGN_SHA = "2" * 64


def _vector(name: str, value: list[float]) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.VECTOR3,
        value=value,
        unit=LengthUnit.METER,
    )


def _program(*, array: bool = False) -> CompiledGeometryProgram:
    base = ProjectVersionRef("generic-building", 0, BASE_SHA)
    binding = SemanticBinding(
        binding_id="body-binding",
        component_id="body-component",
        object_ids=(
            ("row-object", "seed-object") if array else ("body-object",)
        ),
        commitment_refs=("commitment:body",),
        evidence_refs=("evidence:body",),
    )
    seed = GeometryOperation(
        op_id="seed" if array else "body",
        kind=GeometryOperationKind.SOLID,
        output_object_ids=(("seed-object",) if array else ("body-object",)),
        input_object_ids=(),
        frame_id="world",
        parameters=(
            _vector("origin", [0.0, 0.0, 0.0]),
            _vector("size", [2.0, 3.0, 4.0]),
        ),
        semantic_binding_ids=("body-binding",),
    )
    operations = (seed,)
    operation_order = (seed.op_id,)
    objects = (
        CompiledGeometryObject(
            object_id=seed.output_object_ids[0],
            producer_op_id=seed.op_id,
            object_digest="3" * 64,
        ),
    )
    if array:
        row = GeometryOperation(
            op_id="row",
            kind=GeometryOperationKind.ARRAY,
            output_object_ids=("row-object",),
            input_object_ids=("seed-object",),
            frame_id="world",
            parameters=(
                GeometryParameter.create(
                    name="count",
                    kind=GeometryParameterKind.INTEGER,
                    value=2,
                ),
                _vector("step", [3.0, 0.0, 0.0]),
            ),
            semantic_binding_ids=("body-binding",),
        )
        operations = (row, seed)
        operation_order = ("seed", "row")
        objects = tuple(
            sorted(
                (
                    *objects,
                    CompiledGeometryObject(
                        object_id="row-object",
                        producer_op_id="row",
                        object_digest="4" * 64,
                    ),
                ),
                key=lambda item: item.object_id,
            )
        )
    proposal = GeometryProgramProposal(
        proposal_id="generic-proposal",
        project_id="generic-building",
        run_id="reconstruction-001",
        base=base,
        design_state_digest=DESIGN_SHA,
        predecessor_program_digest=None,
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=("evidence:frame",),
            ),
        ),
        assets=(),
        semantic_bindings=(binding,),
        operations=operations,
        assemblies=(),
    )
    return CompiledGeometryProgram(
        proposal=proposal,
        operation_order=operation_order,
        frame_digests=(("world", "5" * 64),),
        component_digests=(("body-component", "6" * 64),),
        semantic_binding_digests=(("body-binding", "7" * 64),),
        objects=objects,
        asset_substitutions=(),
    )


def _binding(
    program: CompiledGeometryProgram,
    *,
    branch_id: str = "candidate-a",
    stage_id: str = "stage-3",
    record_sha: str = "8" * 64,
    record_path: str | None = None,
    media_type: str = "application/json",
) -> RhinoCadProgramBinding:
    base = program.proposal.base
    branch = BranchRef(
        run=RunRef(program.proposal.project_id, program.proposal.run_id, base),
        branch_id=branch_id,
        epoch=3,
    )
    if record_path is None:
        record_path = (
            f"runs/{program.proposal.run_id}/branches/{branch_id}/records/"
            f"{stage_id}-geometry-program-{record_sha}.json"
        )
    return RhinoCadProgramBinding(
        program_ref=ProjectRecordRef(
            project_id=program.proposal.project_id,
            relative_path=record_path,
            sha256=record_sha,
            media_type=media_type,
        ),
        branch=branch,
        stage_id=stage_id,
        program_digest=program.program_digest,
        design_state_digest=program.proposal.design_state_digest,
        predecessor_program_digest=program.proposal.predecessor_program_digest,
    )


def _inspection(plan) -> ThreeDmInspection:
    expected_counts = dict(plan.expected_object_counts)
    expected_objects = plan.expected_semantics["objects"]
    expected_blocks = plan.expected_semantics["blocks"]
    array_ops = {
        name.removeprefix("archflow-family-") for name in expected_blocks
    }
    user_rows = []
    named_rows = []
    instance_rows = []
    visible_witnesses = []
    counter = 0
    for object_id, semantic in sorted(expected_objects.items()):
        producer = semantic["user_text"]["archflow:producer_op"]
        for index in range(expected_counts[object_id]):
            counter += 1
            object_ref = f"object-{counter}"
            user_rows.append(
                {
                    "object_id": object_ref,
                    "name": semantic["name"],
                    "layer_path": semantic["layer"],
                    "attributes": tuple(
                        {"key": key, "value": value}
                        for key, value in sorted(semantic["user_text"].items())
                    ),
                    "geometry": (),
                }
            )
            if producer in array_ops:
                instance_rows.append(
                    {
                        "object_id": object_ref,
                        "definition_id": f"definition-{producer}",
                        "definition_name": f"archflow-family-{producer}",
                        "layer_index": 0,
                        "layer_id": "layer-0",
                        "layer_path": semantic["layer"],
                        "is_instance_definition_object": False,
                        "transform": [],
                    }
                )
        if producer not in array_ops:
            # An object is delivered as the number of Breps its count states:
            # one for an ordinary solid, and - where there are no blocks, as in
            # an imported document - one per copy of an array.
            for copy in range(expected_counts[object_id]):
                named_rows.append(
                    {
                        "object_id": f"named-{object_id}-{copy}",
                        "name": object_id,
                        "type": "Brep",
                        "layer_path": semantic["layer"],
                        "bbox": plan.expected_bounds[object_id],
                        "bbox_source": "brep_face_render_mesh_vertices",
                        "mesh_face_count": 1,
                        "mesh_vertex_count": 8,
                    }
                )
                visible_witnesses.append(
                    {
                        "object_id": f"named-{object_id}-{copy}",
                        "name": object_id,
                        "type": "Brep",
                        "source": "brep_face_render_mesh_vertices",
                        "mesh_face_count": 1,
                        "mesh_vertex_count": 8,
                    }
                )
    definitions = tuple(
        {
            "id": f"definition-{name.removeprefix('archflow-family-')}",
            "name": name,
            "description": "",
            "update_type": "Static",
            "is_linked": False,
            "object_ids": [f"definition-member-{name}"],
            "object_count": 1,
            "user_strings": [],
            "reference_count": count,
        }
        for name, count in sorted(expected_blocks.items())
    )
    visible_witnesses.extend(
        {
            "object_id": definition["object_ids"][0],
            "name": "",
            "type": "Brep",
            "source": "brep_face_render_mesh_vertices",
            "mesh_face_count": 1,
            "mesh_vertex_count": 8,
        }
        for definition in definitions
    )
    aggregate = {
        "min": [
            min(row["min"][axis] for row in plan.expected_bounds.values())
            for axis in range(3)
        ],
        "max": [
            max(row["max"][axis] for row in plan.expected_bounds.values())
            for axis in range(3)
        ],
    }
    layers = tuple(
        {
            "index": index,
            "id": f"layer-{index}",
            "name": full_path.rsplit("::", 1)[-1],
            "full_path": full_path,
            "color_rgba": [*color, 255],
            "parent_id": None,
            "visible": True,
            "locked": False,
            "object_count": 0,
        }
        for index, (full_path, color) in enumerate(plan.expected_layer_colors)
    )
    return ThreeDmInspection(
        file_sha256="9" * 64,
        file_bytes=1024,
        three_dm_version=8,
        archive_version=80,
        units={"name": "Meters", "code": 4},
        layers=layers,
        object_count=counter + len(definitions),
        top_level_object_count=counter,
        instance_definition_member_count=len(definitions),
        object_counts_by_type={},
        object_counts_by_layer=(),
        instance_definitions=definitions,
        instance_references=tuple(instance_rows),
        document_user_strings=tuple(
            {"key": key, "value": value}
            for key, value in plan.expected_document_user_text
        ),
        object_user_strings=tuple(user_rows),
        aggregate_bbox=aggregate,
        bbox_contributing_geometry_count=counter,
        named_object_bboxes=tuple(named_rows),
        visible_bounds_witnesses=tuple(visible_witnesses),
    )


def _fake_executable(workspace: Path) -> Path:
    path = workspace / "powershell.exe"
    path.write_bytes(b"")
    return path


def _write_success_marker(plan) -> None:
    payload = {
        "schema": "RhinoCadCompletionMarker@1",
        "artifact_relative_path": plan.artifact_relative_path,
        "completion_token": plan.completion_token,
        "status": "succeeded",
    }
    plan.completion_marker_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
        newline="\n",
    )


def _write_host_witness(plan, *, exact: bool = True, new_pid_count: int = 1) -> None:
    payload = {
        "schema": "RhinoCadHostWitness@1",
        "completion_token": plan.completion_token,
        "ownership_status": "exact" if exact else "ambiguous",
        "new_pid_count": new_pid_count,
        "pid": 4242 if exact else None,
        "executable_path": (
            r"C:\Program Files\Rhino 8\System\Rhino.exe" if exact else None
        ),
        "start_time_utc_ticks": 638900000000000000 if exact else None,
    }
    plan.host_witness_path.write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )


def _cleanup_result(plan, *, confirmed: bool = True):
    payload = {
        "schema": "RhinoCadCleanupWitness@1",
        "completion_token": plan.completion_token,
        "pid": 4242,
        "status": "stopped" if confirmed else "identity_mismatch",
        "identity_matched": confirmed,
        "stop_requested": confirmed,
        "cleanup_confirmed": confirmed,
        "error_detail": None,
    }
    return SimpleNamespace(
        returncode=0 if confirmed else 1,
        stdout=json.dumps(payload, separators=(",", ":")),
        stderr="",
    )


class _FakeWorker:
    def __init__(self, *, stdout: str = "", stderr: str = "", running: bool = True):
        self.stdout_text = stdout
        self.stderr_text = stderr
        self.returncode = None if running else 0
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, timeout=None):
        return self.stdout_text, self.stderr_text


# ---------------------------------------------------------------- programs of chosen operations


def _single_operation_program(operation: GeometryOperation) -> CompiledGeometryProgram:
    """The synthetic fixture program with its one operation replaced."""

    program = _program()
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

    program = _program()
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


def _no_process():
    """Any attempt to start a process (Rhino, PowerShell) fails the test."""

    return patch.multiple(subprocess, Popen=_refuse_process, run=_refuse_process)


def _refuse_process(*args, **kwargs):
    raise AssertionError(f"the OCCT executor must not start a process: {args[:1]}")
