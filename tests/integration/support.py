"""What the cross-package tests share.

Copies of owners' fixtures, because the tests here run from the repository root,
where a package's tests are not importable: the portico slice's grid and levels
(packages/monkeyarch/tests/portico_fixture.py) and a villa's State Record
(state_record_fixture.py). The spine fixture, which MonkeyArch's tests use too,
is not copied: it is tests/support/spine_fixture.py.

This suite's own: the CAD synthetic compiled program, its exact binding and a
controlled Rhino host; the OCCT program builders; a Blender request; and
``ProjectFormatFixture``, complete current and format-1 projects. The runner's
fixtures are in runner_support.py and the window's in window_support.py.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from monkeycad.backends.rhino import export as rhino_export
from monkeycad.execution import CadExecutionRequest, RhinoCadProgramBinding
from monkeycad.formats.three_dm_inspector import ThreeDmInspection
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, STATE_RECORD
from archflow.project.refs import BranchRef, ProjectRecordRef, ProjectVersionRef, RunRef, record_file_name
from archflow.project.repository import (
    LEGACY_FORMAT_VERSION,
    FilesystemProjectRepository,
    _json_bytes,
    _replace_atomic,
    _sha256,
    _write_immutable,
)
from archflow.state.operational_state import DesignObligation, ObligationStatus
from archflow.state.geometry_program import (
    AffineTransform,
    CompiledGeometryObject,
    CompiledGeometryProgram,
    CoordinateFrame,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    ProjectGridAxis,
    ProjectGrids,
    ProjectLevel,
    ProjectLevels,
    SemanticBinding,
)
from archflow.state.state_record import (
    Entity,
    Lineage,
    Parameter,
    Relation,
    StateRecord,
    ValidatorBinding,
)


# ---------------------------------------------------------------- the portico slice's grid and levels
# A copy of MonkeyArch's portico_fixture.py, for the prism cut-out tests.

BASIS = ("reading:plate",)
PN = "level-piano-nobile"


def _grids() -> ProjectGrids:
    axes = [ProjectGridAxis(f"axis-{k + 1}", str(k + 1), ((k - 2.5) * 1.6065, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS) for k in range(6)]
    axes.append(ProjectGridAxis("axis-w", "W", (0.0, 0.0, -13.85), (1.0, 0.0, 0.0), BASIS))       # the west facade line
    axes.append(ProjectGridAxis("axis-ox", "OX", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), BASIS))        # three lines through the origin, for
    axes.append(ProjectGridAxis("axis-oz", "OZ", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS))        # reading a stated direction off a
    axes.append(ProjectGridAxis("axis-od", "OD", (0.0, 0.0, 0.0), (1.0, 0.0, 1.0), BASIS))        # run whose plan coordinates are plain
    return ProjectGrids(project_id="demo", published_by="seat-coordination", axes=tuple(sorted(axes, key=lambda a: a.axis_id)))


def _levels(piano: float = 3.57) -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS), ProjectLevel(PN, "piano-nobile", piano, BASIS)))


def _op_params(operation) -> dict:
    return {p.name: json.loads(p.value_json) for p in operation.parameters}


# ---------------------------------------------------------------- a villa's State Record

def _record() -> StateRecord:
    entities = (
        Entity("building", "Component@1", {"semantic_kind": "whole-building", "intent": "villa", "typology": "centralized villa"}),
        Entity("portico-west", "Component@1", {"semantic_kind": "arrival-and-buttress", "intent": "west portico"}, parent_id="building"),
        Entity("portico-columns", "Component@1", {"semantic_kind": "vertical-support", "intent": "six columns"}, parent_id="portico-west"),
        Entity("portico-entablature", "Component@1", {"semantic_kind": "horizontal-load-transfer", "intent": "entablature"}, parent_id="portico-west"),
        Entity("level-piano-nobile", "Level@1", {"role": "piano-nobile", "elevation": 3.57}, basis_refs=("reading:plan",)),
        Entity("axis-1", "GridAxis@1", {"role": "1", "origin": [-10.71, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}),
        Entity("columns-west", "Element@1", {"component_id": "portico-columns", "producer": "column-array", "base_level": "level-piano-nobile"}, lineage=Lineage(introduced_at="stage-2")),
        Entity("entablature-west", "Element@1", {"component_id": "portico-entablature", "producer": "beam", "host": "columns-west"}, lineage=Lineage(introduced_at="stage-2")),
    )
    parameters = (
        Parameter("column_diameter", 0.714, "m", epistemic_status="declared", source_ref="reading:plan"),
        Parameter("column_height", 6.426, "m", expr="9 * column_diameter", inputs=("column_diameter",), source_ref="rule:ionic-nine-diameters"),
    )
    relations = (
        Relation("columns-support-entablature", "support", "columns-west", "entablature-west", datum_role="columns-west-top", propagation="revalidate",
                 validator=ValidatorBinding("support_contact", tolerance=0.001), basis_refs=("reading:plan",)),
    )
    obligations = (DesignObligation(obligation_id="continuous-load-path", statement="a column grid was chosen: complete the load path to the foundation",
                                    source_ref="relation:columns-support-entablature", status=ObligationStatus.OPEN, subject_refs=("entity:columns-west",)),)
    return StateRecord("demo", "run-1", entities, parameters, relations, obligations, evidence_refs=("reading:plan",), decision_ref="decision:declared")


# ---------------------------------------------------------------- a synthetic compiled program and its binding

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


def _no_process():
    """Any attempt to start a process (Rhino, PowerShell) fails the test."""

    return patch.multiple(subprocess, Popen=_refuse_process, run=_refuse_process)


def _refuse_process(*args, **kwargs):
    raise AssertionError(f"the OCCT executor must not start a process: {args[:1]}")


# ---------------------------------------------------------------- a controlled Rhino host and its request

def _rhino_request(workspace, *, program=None, binding=None, **options):
    program = program or _program()
    return CadExecutionRequest(
        program=program,
        binding=binding or _binding(program),
        speculative_workspace=workspace,
        artifact_stem="contract-candidate",
        **options,
    )


@contextmanager
def _controlled_rhino(root, *, bad_inspection=False, plans=None, oracle_shift=0.0):
    """Inject only host I/O and inspection; leave execution and validation real."""
    prepare = rhino_export.prepare_rhino_three_dm_export
    plans = [] if plans is None else plans

    def capture_plan(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        plans.append(plan)
        return plan

    def runner(*args, **kwargs):
        plan = plans[-1]
        _write_host_witness(plan)
        plan.model_path.write_bytes(b"controlled Rhino contract fixture, not a real 3dm")
        _write_success_marker(plan)
        return _FakeWorker(stdout="controlled worker")

    def inspect(path, **kwargs):
        plan = plans[-1]
        data = plan.model_path.read_bytes()
        inspection = replace(_inspection(plan), file_sha256=hashlib.sha256(data).hexdigest(), file_bytes=len(data))
        if bad_inspection:
            inspection = replace(inspection, object_user_strings=())
        if oracle_shift and ".rebuild-oracle" in plan.model_path.name:
            def shifted(bounds):
                return {key: [values[0] + oracle_shift, *values[1:]] for key, values in bounds.items()}

            inspection = replace(inspection, aggregate_bbox=shifted(inspection.aggregate_bbox),
                named_object_bboxes=tuple({**row, "bbox": shifted(row["bbox"])} for row in inspection.named_object_bboxes))
        return inspection

    with patch.object(rhino_export, "prepare_rhino_three_dm_export", side_effect=capture_plan), \
         patch.object(rhino_export, "inspect_three_dm", side_effect=inspect), \
         _no_process():
        yield {
            "powershell_executable": _fake_executable(root),
            "runner": runner,
            "cleanup_runner": lambda *args, **kwargs: _cleanup_result(plans[-1]),
        }


# ---------------------------------------------------------------- a Blender request over two objects

LONG_BOX_ID = "box-" + "identity-preserved-" * 4


def _extrusion(*, vector=(2.0, 3.0, 1.0)):
    return GeometryOperation(
        op_id="triangle", kind=GeometryOperationKind.EXTRUSION,
        output_object_ids=("triangle-object",), input_object_ids=(),
        frame_id="world", semantic_binding_ids=("body-binding",),
        parameters=(
            GeometryParameter.create(name="base_level", kind=GeometryParameterKind.NUMBER, value=5.0, unit=LengthUnit.METER),
            GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=2.0, unit=LengthUnit.METER),
            GeometryParameter.create(name="profile", kind=GeometryParameterKind.POINTS3,
                                     value=[[1.0, 0.0, 2.0], [5.0, 0.0, 2.0], [1.0, 0.0, 5.0]], unit=LengthUnit.METER),
            GeometryParameter.create(name="vector", kind=GeometryParameterKind.VECTOR3, value=list(vector), unit=LengthUnit.METER),
        ),
    )


def _two_objects(unit=LengthUnit.MILLIMETER):
    operations = (_box(LONG_BOX_ID, [10.0, 20.0, 30.0], [2.0, 3.0, 4.0]), _extrusion())
    operations = tuple(replace(op, parameters=tuple(
        replace(parameter, unit=unit) if parameter.unit is not None else parameter
        for parameter in op.parameters
    )) for op in operations)
    program = _program_of(*operations)
    return replace(program, proposal=replace(program.proposal, length_unit=unit))


def _blender_request(workspace, *, program=None, **changes):
    program = program or _two_objects()
    return CadExecutionRequest(
        program=program, binding=_binding(program), speculative_workspace=workspace,
        artifact_stem="blender-candidate", **changes,
    )


# ---------------------------------------------------------------- a current and a format-1 project


class ProjectFormatFixture(unittest.TestCase):
    """Disposable current and format-1 projects, and a fingerprint of a whole project directory.

    The format migration tests (test_project_format_migration.py) run on these, and the
    create_project scan tests (test_migration_scan_cli.py) set one up to reuse the same
    complete retained project rather than define a second, weaker one.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    # ---- fixtures

    def current_project(self, name: str = "current-building") -> FilesystemProjectRepository:
        """A project this build creates itself, at the current format version."""

        repository = FilesystemProjectRepository.initialize(
            self.root / name,
            project_id=name,
            initial_state={"phase": "design"},
            authored_record={"schema": "StateRecord@1", "draft": "initial"},
            seat_pack={"schema": "SeatPack@1", "seats": []},
        )
        self.populate(repository)
        return repository

    def legacy_envelope(self, name: str) -> Path:
        """A format-1 project exactly as an older build first wrote one.

        The bytes are the historical ones: ``CanonicalSnapshot@1`` carries no
        semantic digest, and the version reference names the snapshot file.
        Nothing here has ever been opened for writing, so the project carries
        none of the repository's advisory lock files.
        """

        root = self.root / name
        for directory in ("canonical", "events", "runs"):
            (root / directory).mkdir(parents=True)
        _write_immutable(
            root / "project.json",
            _json_bytes({
                "schema": "ArchFlowProject@1",
                "project_id": name,
                "format_version": LEGACY_FORMAT_VERSION,
            }),
        )
        self.write_legacy_version(root, {"phase": "design"})
        return root

    def write_legacy_version(
        self, root: Path, state: dict, **event_fields: object,
    ) -> ProjectVersionRef:
        """Publish one more version exactly as an older build wrote it.

        ``compare_and_swap`` needs a promotion receipt and a run of its own, so
        a multi-version legacy fixture is written as the historical bytes. The
        same helper writes v0, so the fixture has one spelling of a published
        legacy version rather than two that can drift apart.
        """

        head_path = root / "HEAD"
        previous = (
            json.loads(head_path.read_text(encoding="utf-8"))
            if head_path.exists() else None
        )
        project_id = root.name
        parent = previous["current"] if previous else None
        version = 0 if parent is None else parent["version"] + 1
        snapshot_bytes = _json_bytes({
            "schema": "CanonicalSnapshot@1",
            "project_id": project_id,
            "version": version,
            "parent": parent,
            "state": dict(state),
        })
        snapshot_digest = _sha256(snapshot_bytes)
        snapshot_path = f"canonical/{record_file_name(f'state-v{version:06d}', snapshot_digest)}"
        _write_immutable(root / snapshot_path, snapshot_bytes)
        head_ref = ProjectVersionRef(project_id, version, snapshot_digest)
        event_bytes = _json_bytes({
            "schema": "ProjectEvent@1",
            "project_id": project_id,
            "event_type": "project.initialized" if parent is None else "candidate.promoted",
            "decision": "accepted",
            "run_id": None,
            "from": parent,
            "to": head_ref.to_dict(),
            "previous_event": previous["event"] if previous else None,
            "decision_receipt": None,
            **event_fields,
        })
        event_digest = _sha256(event_bytes)
        event_path = f"events/{record_file_name(f'event-v{version:06d}', event_digest)}"
        _write_immutable(root / event_path, event_bytes)
        head_payload = _json_bytes({
            "schema": "ProjectHead@1",
            "project_id": project_id,
            "current": head_ref.to_dict(),
            "snapshot": {"relative_path": snapshot_path, "sha256": snapshot_digest,
                         "media_type": "application/json"},
            "event": {"relative_path": event_path, "sha256": event_digest,
                      "media_type": "application/json"},
        })
        if previous is None:
            _write_immutable(head_path, head_payload)
        else:
            _replace_atomic(head_path, head_payload)
        return head_ref

    def legacy_project_in(self, root: Path, name: str) -> FilesystemProjectRepository:
        """The same legacy fixture, built under a directory of the caller's."""

        previous, self.root = self.root, root
        try:
            return self.legacy_project(name)
        finally:
            self.root = previous

    def legacy_project(self, name: str = "legacy-building") -> FilesystemProjectRepository:
        """A format-1 project an older build went on to author and use."""

        repository = FilesystemProjectRepository.open(self.legacy_envelope(name))
        repository.initialize_authored_inputs(
            expected_head=repository.read_head(),
            expected_record=None,
            authored_record={"schema": "StateRecord@1", "draft": "initial"},
            seat_pack={"schema": "SeatPack@1", "seats": []},
        )
        self.populate(repository)
        return repository

    def populate(self, repository: FilesystemProjectRepository) -> None:
        """Give a project retained runs, records, a design branch and bytes.

        The second run is never referenced from HEAD or from the design branch,
        so a closure that only followed published history would miss it.
        """

        stage = self.stage(repository, "first")
        repository.compare_and_swap_design_branch(
            branch_id="main",
            expected_head=None,
            branch={"branch_id": "main", "parent_branch": None,
                    "fork_stage": stage.to_dict(), "head_stage": stage.to_dict()},
        )
        self.stage(repository, "extra")

    def stage(self, repository: FilesystemProjectRepository, name: str):
        run = repository.create_run(name)
        artifact = repository.ingest(
            run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id=name,
            media_type="model/3dm",
            source=io.BytesIO(f"model-{name}".encode()),
        )
        record = repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=name),
            record_kind=STATE_RECORD,
            payload={"schema": "StateRecord@1", "run": run.to_dict(), "model": {
                "project_id": run.project_id, "relative_path": artifact.relative_path,
                "sha256": artifact.sha256, "media_type": artifact.media_type}},
        )
        return repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=name),
            record_kind=DESIGN_STAGE,
            payload={"schema": "DesignStage@1", "candidate_id": name,
                     "parent_stage": None, "record": record.to_dict()},
        )

    def install_historical_record(
        self, repository: FilesystemProjectRepository, kind: str,
        *, embeds_base: bool = True, note: str = "historical",
    ) -> Path:
        """Write a retained record whose kind this build no longer registers.

        Archived lanes left records like this behind; ``put_json`` refuses to
        write one now, but readers must keep them. Whether such a record
        *embeds a project-version identity* is the whole question a migration
        has to answer, so the fixture can write either kind.
        """

        payload = {
            "schema": "RetiredLaneNote@1",
            "project_id": repository.layout.project_id,
            "note": note,
        }
        if embeds_base:
            payload["base"] = repository.read_head().to_dict()
        data = _json_bytes(payload)
        path = (repository.layout.run("extra").records
                / record_file_name(kind, _sha256(data)))
        _write_immutable(path, data)
        return path

    def corrupt_a_retained_record(self, repository: FilesystemProjectRepository) -> None:
        """Make one retained record disagree with the digest in its file name."""

        for path in sorted((repository.layout.run("extra").records).glob("*.json")):
            path.chmod(0o644)
            path.write_bytes(b'{"schema": "StateRecord@1"}\n')

    # ---- the whole project directory, locks included

    def fingerprint(self, root: Path) -> dict[str, tuple[int, str]]:
        return {
            path.relative_to(root).as_posix(): (
                path.stat().st_size, _sha256(path.read_bytes()),
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def assert_unchanged(self, root: Path, before: dict[str, tuple[int, str]]) -> None:
        self.assertEqual(self.fingerprint(root), before)
