"""Merge-blocking contracts for generic Rhino externalization."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from archflow.adapters.cad_execution import (
    CadExecutionError,
    CadExecutionStatus,
    RhinoCadProgramBinding,
    build_rhino_com_powershell_command,
    build_rhino_com_powershell_source,
    execute_rhino_three_dm_export,
    prepare_rhino_three_dm_export,
    verify_rhino_export_readback,
)
from archflow.adapters.three_dm_inspector import ThreeDmInspection
from archflow.project.refs import BranchRef, ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.runtime.geometry_compiler import (
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


def _prepare(
    workspace: Path,
    *,
    program: CompiledGeometryProgram | None = None,
    provenance=None,
    material=False,
):
    program = program or _program()
    kwargs = {}
    if material:
        kwargs = {
            "material_by_component": {"body-component": "stucco"},
            "material_colors": {"stucco": (210, 205, 190)},
        }
    return prepare_rhino_three_dm_export(
        program,
        binding=_binding(program),
        speculative_workspace=workspace,
        artifact_name="candidate.3dm",
        readback_tolerance=0.001,
        provenance=provenance,
        **kwargs,
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
            named_rows.append(
                {
                    "object_id": f"named-{object_id}",
                    "name": object_id,
                    "type": "Brep",
                    "layer_path": semantic["layer"],
                    "bbox": plan.expected_bounds[object_id],
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


def _verified_readback(plan, inspection):
    return verify_rhino_export_readback(
        plan,
        inspection,
        executable_name="powershell.exe",
        completion_marker_sha256=plan.completion_witness_sha256,
        host_witness_sha256="a" * 64,
        cleanup_witness_sha256="b" * 64,
        cleanup_status="confirmed",
    )


class RhinoCadExportTest(unittest.TestCase):
    def test_plan_is_stable_and_script_has_no_absolute_model_path(self):
        program = _program()
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first = _prepare(Path(first_directory), program=program)
            second = _prepare(Path(second_directory), program=program)
            self.assertEqual(first.script_sha256, second.script_sha256)
            self.assertEqual(first.plan_digest, second.plan_digest)
            script = first.script_path.read_text(encoding="utf-8")
            self.assertNotIn(first_directory, script)
            self.assertNotIn(second_directory, script)
            self.assertNotIn("os.getcwd()", script)
            self.assertIn("Path(__file__).resolve().parent", script)
            self.assertIn("ActiveDoc.WriteFile(str(_output_path)", script)
            self.assertNotIn(first_directory, str(first.to_dict()))
            self.assertEqual(
                [2.0, 4.0, 3.0],
                first.expected_bounds["body-object"]["max"],
            )
            self.assertEqual("RhinoCadExportPlan@4", first.SCHEMA)
            self.assertEqual(
                first.completion_witness_sha256,
                hashlib.sha256(
                    json.dumps(
                        {
                            "schema": "RhinoCadCompletionMarker@1",
                            "artifact_relative_path": "candidate.3dm",
                            "completion_token": first.completion_token,
                            "status": "succeeded",
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            )

    def test_exact_program_binding_is_mechanical(self):
        program = _program()
        bad = replace(_binding(program), design_state_digest="a" * 64)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(CadExecutionError, "design-state"):
                prepare_rhino_three_dm_export(
                    program,
                    binding=bad,
                    speculative_workspace=Path(temporary_directory),
                    artifact_name="candidate.3dm",
                    readback_tolerance=0.001,
                )

    def test_program_binding_rejects_cross_branch_record(self):
        program = _program()
        with self.assertRaisesRegex(CadExecutionError, "run/branch/stage/SHA"):
            _binding(
                program,
                record_path=(
                    "runs/reconstruction-001/branches/candidate-b/records/"
                    f"stage-3-geometry-program-{'8' * 64}.json"
                ),
            )

    def test_program_binding_rejects_wrong_stage_record(self):
        program = _program()
        with self.assertRaisesRegex(CadExecutionError, "run/branch/stage/SHA"):
            _binding(
                program,
                record_path=(
                    "runs/reconstruction-001/branches/candidate-a/records/"
                    f"stage-4-geometry-program-{'8' * 64}.json"
                ),
            )

    def test_program_binding_rejects_wrong_suffix_sha(self):
        program = _program()
        with self.assertRaisesRegex(CadExecutionError, "run/branch/stage/SHA"):
            _binding(
                program,
                record_path=(
                    "runs/reconstruction-001/branches/candidate-a/records/"
                    f"stage-3-geometry-program-{'9' * 64}.json"
                ),
            )

    def test_program_binding_rejects_wrong_media_type(self):
        with self.assertRaisesRegex(CadExecutionError, "application/json"):
            _binding(_program(), media_type="application/octet-stream")

    def test_program_binding_rejects_run_level_pseudo_record(self):
        program = _program()
        with self.assertRaisesRegex(CadExecutionError, "run/branch/stage/SHA"):
            _binding(
                program,
                record_path=(
                    "runs/reconstruction-001/records/"
                    f"stage-3-geometry-program-{'8' * 64}.json"
                ),
            )

    def test_workspace_and_constructed_plan_reject_escape_or_symlink(self):
        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory() as outside_directory:
            workspace = Path(temporary_directory)
            plan = _prepare(workspace)
            outside = Path(outside_directory) / "outside.py"
            outside.write_text("pass", encoding="utf-8")
            with self.assertRaisesRegex(CadExecutionError, "escaped"):
                replace(plan, script_path=outside)
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(CadExecutionError, "symlink"):
                    _prepare(workspace)
            original_is_symlink = Path.is_symlink
            calls = []
            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda value: (
                    value == plan.model_path or original_is_symlink(value)
                ),
            ):
                receipt = execute_rhino_three_dm_export(
                    plan,
                    powershell_executable=_fake_executable(workspace),
                    runner=lambda *args, **kwargs: calls.append(1),
                )
            self.assertEqual([], calls)
            self.assertEqual(
                "cad_execution.path_boundary_failed",
                receipt.failures[0]["code"],
            )

    def test_materials_flow_into_script_plan_and_exact_readback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory), material=True)
            expected = plan.expected_semantics["objects"]["body-object"]
            self.assertEqual("stucco", expected["user_text"]["archflow:material"])
            self.assertEqual(
                (210, 205, 190),
                dict(plan.expected_layer_colors)["archflow::body-component"],
            )
            self.assertIn("(210, 205, 190)", plan.script_path.read_text())
            receipt = _verified_readback(plan, _inspection(plan))
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED)

    def test_readback_rejects_missing_extra_semantics_and_bounds_drift(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(
                Path(temporary_directory),
                provenance={"source_record": "record-1"},
            )
            inspection = _inspection(plan)
            inspection = replace(
                inspection,
                document_user_strings=tuple(
                    row
                    for row in inspection.document_user_strings
                    if row["key"] != "archflow:source_record"
                ),
                object_user_strings=(),
                aggregate_bbox={
                    "min": [0.0, 0.0, 0.0],
                    "max": [20.0, 20.0, 20.0],
                },
            )
            receipt = _verified_readback(plan, inspection)
            codes = {item["code"] for item in receipt.failures}
            self.assertIn("cad_execution.provenance_mismatch", codes)
            self.assertIn("cad_execution.semantic_witness_count_mismatch", codes)
            self.assertIn("cad_execution.aggregate_bounds_mismatch", codes)

    def test_readback_rejects_named_object_bounds_drift(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory))
            inspection = _inspection(plan)
            named = dict(inspection.named_object_bboxes[0])
            named["bbox"] = {
                "min": [0.0, 0.0, 0.0],
                "max": [2.0, 5.0, 3.0],
            }
            receipt = _verified_readback(
                plan,
                replace(inspection, named_object_bboxes=(named,)),
            )
            self.assertIn(
                "cad_execution.named_bounds_mismatch",
                {item["code"] for item in receipt.failures},
            )

    def test_readback_rejects_wrong_missing_layers_and_wrong_color(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory), material=True)
            inspection = _inspection(plan)
            missing = replace(inspection, layers=inspection.layers[:-1])
            missing_receipt = _verified_readback(plan, missing)
            self.assertIn(
                "cad_execution.layer_set_mismatch",
                {item["code"] for item in missing_receipt.failures},
            )

            wrong_path = dict(inspection.layers[-1])
            wrong_path["full_path"] = "archflow::wrong-component"
            wrong_path_receipt = _verified_readback(
                plan,
                replace(
                    inspection,
                    layers=(*inspection.layers[:-1], wrong_path),
                ),
            )
            self.assertIn(
                "cad_execution.layer_set_mismatch",
                {item["code"] for item in wrong_path_receipt.failures},
            )

            wrong_color = dict(inspection.layers[-1])
            wrong_color["color_rgba"] = [1, 2, 3, 255]
            wrong_color_receipt = _verified_readback(
                plan,
                replace(
                    inspection,
                    layers=(*inspection.layers[:-1], wrong_color),
                ),
            )
            self.assertIn(
                "cad_execution.layer_color_mismatch",
                {item["code"] for item in wrong_color_receipt.failures},
            )

    def test_readback_rejects_extra_archflow_document_key(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory))
            inspection = _inspection(plan)
            receipt = _verified_readback(
                plan,
                replace(
                    inspection,
                    document_user_strings=(
                        *inspection.document_user_strings,
                        {"key": "archflow:unexpected", "value": "leak"},
                    ),
                ),
            )
            self.assertIn(
                "cad_execution.provenance_mismatch",
                {item["code"] for item in receipt.failures},
            )

    def test_readback_rejects_wrong_object_name_and_layer(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory))
            inspection = _inspection(plan)
            wrong_name = dict(inspection.object_user_strings[0])
            wrong_name["name"] = "traditional-agent-placeholder"
            name_receipt = _verified_readback(
                plan,
                replace(
                    inspection,
                    object_user_strings=(
                        wrong_name,
                        *inspection.object_user_strings[1:],
                    ),
                ),
            )
            self.assertIn(
                "cad_execution.object_name_mismatch",
                {item["code"] for item in name_receipt.failures},
            )

            wrong_layer = dict(inspection.object_user_strings[0])
            wrong_layer["layer_path"] = "archflow::wrong-component"
            layer_receipt = _verified_readback(
                plan,
                replace(
                    inspection,
                    object_user_strings=(
                        wrong_layer,
                        *inspection.object_user_strings[1:],
                    ),
                ),
            )
            self.assertIn(
                "cad_execution.object_layer_mismatch",
                {item["code"] for item in layer_receipt.failures},
            )

    def test_array_blocks_are_completely_counted(self):
        program = _program(array=True)
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory), program=program)
            good = _verified_readback(plan, _inspection(plan))
            self.assertIs(good.status, CadExecutionStatus.SUCCEEDED)
            broken = replace(
                _inspection(plan),
                instance_references=_inspection(plan).instance_references[:-1],
            )
            bad = _verified_readback(plan, broken)
            self.assertIn(
                "cad_execution.block_reference_mismatch",
                {item["code"] for item in bad.failures},
            )

    def test_com_bridge_is_sta_bounded_and_uses_one_run_script_command(self):
        with tempfile.TemporaryDirectory(prefix="cad workspace ") as temporary_directory:
            workspace = Path(temporary_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)
            source = build_rhino_com_powershell_source(
                plan, timeout_seconds=30.0
            )
            command = build_rhino_com_powershell_command(
                plan,
                powershell_executable=executable,
                timeout_seconds=30.0,
            )
            self.assertIn("Rhino.Application.8", source)
            self.assertNotIn("Rhino.Interface", source)
            self.assertIn("$rhino.IsInitialized()", source)
            self.assertIn("$rhino.RunScript($macro, 0)", source)
            self.assertEqual(1, source.count("RunScript("))
            self.assertNotIn("CommandHistory", source)
            self.assertNotIn("'_Exit'", source)
            self.assertNotIn("MainWindowHandle", source)
            self.assertIn("RhinoComPowerShellWorkerResult@4", source)
            self.assertIn("RhinoCadHostWitness@1", source)
            self.assertIn("[IO.FileMode]::CreateNew", source)
            self.assertIn("System.Text.UTF8Encoding($false)", source)
            self.assertIn("if ($newPids.Count -eq 1)", source)
            self.assertIn("$hostWitness.executable_path", source)
            self.assertIn("start_time_utc_ticks", source)
            self.assertNotIn("Stop-Process", source)
            self.assertNotIn("Stop-Process -Name", source)
            self.assertNotIn("Get-Process | Stop-Process", source)
            self.assertEqual("-STA", command[3])
            self.assertEqual("-EncodedCommand", command[4])
            self.assertTrue(plan.script_path.read_text().startswith("#! python 3\n"))

    def test_execute_refuses_tampered_script_without_calling_runner(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)
            plan.script_path.write_text("tampered", encoding="utf-8")
            calls = []

            def runner(*args, **kwargs):
                calls.append((args, kwargs))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            receipt = execute_rhino_three_dm_export(
                plan, powershell_executable=executable, runner=runner
            )
            self.assertEqual([], calls)
            self.assertEqual(
                "cad_execution.script_tampered", receipt.failures[0]["code"]
            )

    def test_execute_refuses_existing_output_and_ambiguous_host(self):
        with tempfile.TemporaryDirectory() as first_directory:
            workspace = Path(first_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)
            plan.model_path.write_bytes(b"stale")
            calls = []
            receipt = execute_rhino_three_dm_export(
                plan,
                powershell_executable=executable,
                runner=lambda *args, **kwargs: calls.append(1),
            )
            self.assertEqual([], calls)
            self.assertEqual("cad_execution.output_exists", receipt.failures[0]["code"])
        with tempfile.TemporaryDirectory() as second_directory:
            workspace = Path(second_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)
            worker = _FakeWorker()

            def ambiguous_runner(*args, **kwargs):
                _write_host_witness(plan, exact=False, new_pid_count=2)
                return worker

            receipt = execute_rhino_three_dm_export(
                plan,
                powershell_executable=executable,
                runner=ambiguous_runner,
            )
            self.assertEqual(
                "cad_execution.host_ownership_ambiguous",
                receipt.failures[0]["code"],
            )
            self.assertTrue(worker.terminated)
            self.assertEqual("cleanup_unverified", receipt.cleanup_status)

    def test_success_marker_cannot_bypass_strict_cleanup_witness(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)
            worker = _FakeWorker()

            def runner(*args, **kwargs):
                _write_host_witness(plan)
                _write_success_marker(plan)
                return worker

            receipt = execute_rhino_three_dm_export(
                plan,
                powershell_executable=executable,
                runner=runner,
                cleanup_runner=lambda *args, **kwargs: _cleanup_result(
                    plan, confirmed=False
                ),
            )
            self.assertEqual(
                "cad_execution.cleanup_unverified",
                receipt.failures[0]["code"],
            )
            self.assertTrue(worker.terminated)

    def test_cleanup_json_rejects_extra_fields(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)

            def runner(*args, **kwargs):
                _write_host_witness(plan)
                _write_success_marker(plan)
                return _FakeWorker()

            malformed = json.loads(_cleanup_result(plan).stdout)
            malformed["unexpected"] = "not-authorized"
            receipt = execute_rhino_three_dm_export(
                plan,
                powershell_executable=executable,
                runner=runner,
                cleanup_runner=lambda *args, **kwargs: SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(malformed),
                    stderr="",
                ),
            )
            self.assertEqual(
                "cad_execution.cleanup_unverified",
                receipt.failures[0]["code"],
            )
            self.assertIsNone(receipt.cleanup_witness_sha256)

    def test_execute_timeout_is_typed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)

            worker = _FakeWorker()

            def runner(*args, **kwargs):
                _write_host_witness(plan)
                return worker

            clock_values = iter((0.0, 1.0))

            receipt = execute_rhino_three_dm_export(
                plan,
                powershell_executable=executable,
                timeout_seconds=0.5,
                runner=runner,
                cleanup_runner=lambda *args, **kwargs: _cleanup_result(plan),
                monotonic=lambda: next(clock_values),
                sleeper=lambda value: None,
            )
            self.assertEqual("cad_execution.timeout", receipt.failures[0]["code"])
            self.assertEqual("confirmed", receipt.cleanup_status)
            self.assertTrue(worker.terminated)

    def test_execute_rejects_stale_malformed_and_mismatched_markers(self):
        with tempfile.TemporaryDirectory() as stale_directory:
            workspace = Path(stale_directory)
            plan = _prepare(workspace)
            executable = _fake_executable(workspace)
            plan.completion_marker_path.write_text("stale", encoding="utf-8")
            calls = []
            receipt = execute_rhino_three_dm_export(
                plan,
                powershell_executable=executable,
                runner=lambda *args, **kwargs: calls.append(1),
            )
            self.assertEqual([], calls)
            self.assertEqual(
                "cad_execution.completion_marker_exists",
                receipt.failures[0]["code"],
            )

        for marker, expected_code in (
            ("not-json", "cad_execution.completion_marker_malformed"),
            (
                json.dumps(
                    {
                        "schema": "RhinoCadCompletionMarker@1",
                        "artifact_relative_path": "candidate.3dm",
                        "completion_token": "f" * 64,
                        "status": "succeeded",
                    }
                ),
                "cad_execution.completion_marker_mismatch",
            ),
        ):
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    workspace = Path(temporary_directory)
                    plan = _prepare(workspace)
                    executable = _fake_executable(workspace)

                    def runner(*args, **kwargs):
                        _write_host_witness(plan)
                        plan.completion_marker_path.write_text(
                            marker, encoding="utf-8"
                        )
                        return _FakeWorker(running=False)

                    receipt = execute_rhino_three_dm_export(
                        plan,
                        powershell_executable=executable,
                        runner=runner,
                        cleanup_runner=lambda *args, **kwargs: _cleanup_result(
                            plan
                        ),
                    )
                    self.assertEqual(expected_code, receipt.failures[0]["code"])

    def test_failure_marker_is_typed_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            plan = _prepare(workspace)
            executable = _fake_executable(workspace)

            def runner(*args, **kwargs):
                _write_host_witness(plan)
                payload = {
                    "schema": "RhinoCadCompletionMarker@1",
                    "artifact_relative_path": plan.artifact_relative_path,
                    "completion_token": plan.completion_token,
                    "status": "failed",
                    "error_type": "RuntimeError",
                    "error_detail": "save failed",
                }
                plan.completion_marker_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                return _FakeWorker()

            receipt = execute_rhino_three_dm_export(
                plan,
                powershell_executable=executable,
                runner=runner,
                cleanup_runner=lambda *args, **kwargs: _cleanup_result(plan),
            )
            self.assertEqual(
                "cad_execution.rhino_script_failed",
                receipt.failures[0]["code"],
            )

    def test_readback_alone_cannot_claim_com_completion(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory))
            receipt = verify_rhino_export_readback(
                plan,
                _inspection(plan),
                executable_name="powershell.exe",
            )
            self.assertEqual(CadExecutionStatus.FAILED, receipt.status)
            self.assertIn(
                "cad_execution.completion_witness_mismatch",
                {item["code"] for item in receipt.failures},
            )

    def test_validation_denominator_rejects_tolerance_provenance_and_nested_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory))
            with self.assertRaisesRegex(
                CadExecutionError,
                "validation denominator",
            ):
                replace(plan, readback_tolerance=0.25)

        mutations = (
            lambda plan: object.__setattr__(plan, "readback_tolerance", 0.25),
            lambda plan: object.__setattr__(
                plan,
                "expected_layer_colors",
                tuple(
                    (path, (1, 2, 3) if path != "archflow" else color)
                    for path, color in plan.expected_layer_colors
                ),
            ),
            lambda plan: object.__setattr__(
                plan,
                "expected_document_user_text",
                plan.expected_document_user_text[:-1],
            ),
            lambda plan: plan.expected_semantics["objects"]["body-object"][
                "user_text"
            ].pop("archflow:evidence"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    workspace = Path(temporary_directory)
                    plan = _prepare(workspace)
                    executable = _fake_executable(workspace)
                    mutate(plan)
                    calls = []
                    receipt = execute_rhino_three_dm_export(
                        plan,
                        powershell_executable=executable,
                        runner=lambda *args, **kwargs: calls.append(1),
                    )
                    self.assertEqual([], calls)
                    self.assertEqual(
                        "cad_execution.validation_denominator_tampered",
                        receipt.failures[0]["code"],
                    )

    def test_verify_recomputes_validation_denominator_before_readback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = _prepare(Path(temporary_directory))
            plan.expected_bounds["body-object"]["max"][0] = 999.0
            receipt = _verified_readback(plan, _inspection(plan))
            self.assertEqual(
                "cad_execution.validation_denominator_tampered",
                receipt.failures[0]["code"],
            )

    def test_supervisor_waits_for_in_progress_completion_marker(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)
            worker = _FakeWorker()

            def runner(*args, **kwargs):
                _write_host_witness(plan)
                plan.model_path.write_bytes(b"new model")
                plan.completion_marker_path.write_text("{", encoding="utf-8")
                return worker

            pauses = []

            def sleeper(value):
                pauses.append(value)
                _write_success_marker(plan)

            with patch(
                "archflow.adapters.cad_execution.inspect_three_dm",
                return_value=_inspection(plan),
            ):
                receipt = execute_rhino_three_dm_export(
                    plan,
                    powershell_executable=executable,
                    runner=runner,
                    cleanup_runner=lambda *args, **kwargs: _cleanup_result(plan),
                    sleeper=sleeper,
                )
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED)
            self.assertTrue(pauses)
            self.assertTrue(worker.terminated)

    def test_execute_success_still_requires_independent_inspection(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            executable = _fake_executable(workspace)
            plan = _prepare(workspace)

            def runner(*args, **kwargs):
                _write_host_witness(plan)
                plan.model_path.write_bytes(b"new model")
                _write_success_marker(plan)
                return _FakeWorker(stdout="worker blocked")

            cleanup_sources = []

            def cleanup_runner(command, **kwargs):
                cleanup_sources.append(
                    base64.b64decode(command[-1]).decode("utf-16-le")
                )
                return _cleanup_result(plan)

            with patch(
                "archflow.adapters.cad_execution.inspect_three_dm",
                return_value=_inspection(plan),
            ):
                receipt = execute_rhino_three_dm_export(
                    plan,
                    powershell_executable=executable,
                    runner=runner,
                    cleanup_runner=cleanup_runner,
                )
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED)
            self.assertEqual("rhino-com-powershell", receipt.adapter_id)
            self.assertEqual("RhinoCadExecutionReceipt@4", receipt.SCHEMA)
            self.assertEqual("confirmed", receipt.cleanup_status)
            self.assertTrue(receipt.host_witness_sha256)
            self.assertTrue(receipt.cleanup_witness_sha256)
            self.assertEqual(1, len(cleanup_sources))
            cleanup_source = cleanup_sources[0]
            self.assertIn("$actualPath -ieq $expectedPath", cleanup_source)
            self.assertIn("$actualTicks -eq $expectedTicks", cleanup_source)
            self.assertIn("Stop-Process -Id $expectedPid", cleanup_source)
            self.assertNotIn("Stop-Process -Name", cleanup_source)


if __name__ == "__main__":
    unittest.main()
