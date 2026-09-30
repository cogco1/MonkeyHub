"""Common CAD execution contract: OCCT, controlled Rhino and opt-in real Rhino.

The Rhino fixture exercises the actual plan, supervisor and receipt checks,
but supplies host witnesses and an inspection. It is not a real Rhino geometry
acceptance. OCCT writes and cold-reads its actual STEP and preview files.
Set ARCHFLOW_RHINO_ACCEPTANCE=1 to also run isolated Rhino 8 COM hosts.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from archflow.adapters import cad_backend, cad_execution, occt_backend
from archflow.adapters.cad_execution import CadExecutionError, CadProgramBinding, RhinoCadProgramBinding
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import stage_geometry_program
from archflow.project.refs import BranchRef, RunRef, record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from monkeyarch.runtime.project_runner import ProjectRunnerError
from tests.test_cad_execution import (
    _FakeWorker,
    _binding,
    _cleanup_result,
    _fake_executable,
    _inspection,
    _program,
    _write_host_witness,
    _write_success_marker,
)
from tests.test_occt_execution import _box, _loft, _no_process, _program_of, _radial_array, _single_operation_program
from tests.test_project_runner import _ExportProject, _options, _prism_row, _record


NEEDS_OCCT = unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
NEEDS_RHINO = unittest.skipUnless(
    os.environ.get("ARCHFLOW_RHINO_ACCEPTANCE") == "1",
    "set ARCHFLOW_RHINO_ACCEPTANCE=1 to run isolated real Rhino acceptance",
)


def _real_rhino_options():
    powershell = cad_execution.discover_powershell()
    if powershell is None:
        raise RuntimeError("real Rhino acceptance requires Windows PowerShell and Rhino 8 COM")
    return {"powershell_executable": powershell, "timeout_seconds": 120}


def _request(workspace, *, program=None, binding=None, **options):
    program = program or _program()
    return cad_backend.CadExecutionRequest(
        program=program,
        binding=binding or _binding(program),
        speculative_workspace=workspace,
        artifact_stem="contract-candidate",
        **options,
    )


def _persisted_program(repository, run):
    """Use P036's actual base and actual program record for integration checks."""
    program = _program()
    program = replace(program, proposal=replace(
        program.proposal, project_id=run.project_id, run_id=run.run_id, base=run.base,
    ))
    branch = BranchRef(run=run, branch_id="candidate-a", epoch=3)
    stage_id = "stage-3"
    reference = repository.put_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=run.run_id, branch_id=branch.branch_id),
        record_kind=stage_geometry_program(stage_id),
        payload=program.to_dict(),
    )
    return program, CadProgramBinding(
        program_ref=reference,
        branch=branch,
        stage_id=stage_id,
        program_digest=program.program_digest,
        design_state_digest=program.proposal.design_state_digest,
        predecessor_program_digest=program.proposal.predecessor_program_digest,
    )


@contextmanager
def _controlled_rhino(root, *, bad_inspection=False, plans=None, oracle_shift=0.0):
    """Inject only host I/O and inspection; leave execution and validation real."""
    prepare = cad_execution.prepare_rhino_three_dm_export
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

    with patch.object(cad_execution, "prepare_rhino_three_dm_export", side_effect=capture_plan), \
         patch.object(cad_execution, "inspect_three_dm", side_effect=inspect), \
         _no_process():
        yield {
            "powershell_executable": _fake_executable(root),
            "runner": runner,
            "cleanup_runner": lambda *args, **kwargs: _cleanup_result(plans[-1]),
        }


class CadRequestContractTests(unittest.TestCase):
    def test_public_binding_and_historical_name_have_the_same_serialized_contract(self):
        program = _program()
        self.assertIs(CadProgramBinding, RhinoCadProgramBinding)
        self.assertEqual(CadProgramBinding.__name__, "CadProgramBinding")
        binding = _binding(program)
        self.assertIsInstance(binding, CadProgramBinding)
        self.assertEqual(binding.to_dict()["schema"], "RhinoCadProgramBinding@1")
        binding.bind_program(program)

    def test_each_exact_binding_dimension_is_checked_before_backend_execution(self):
        program = _program()
        binding = _binding(program)
        other_run = replace(binding.branch.run, run_id="other-run")
        other_project_run = RunRef("other-project", binding.run_id, replace(binding.branch.run.base, project_id="other-project"))

        def changed_run(run):
            return replace(
                binding,
                branch=replace(binding.branch, run=run),
                program_ref=replace(
                    binding.program_ref,
                    project_id=run.project_id,
                    relative_path=binding.program_ref.relative_path.replace(f"runs/{binding.run_id}/", f"runs/{run.run_id}/"),
                ),
            )

        wrong_bindings = {
            "program": replace(binding, program_digest="a" * 64),
            "project": changed_run(other_project_run),
            "run": changed_run(other_run),
            "base": replace(binding, branch=replace(binding.branch, run=replace(binding.branch.run, base=replace(binding.branch.run.base, state_sha256="a" * 64)))),
            "design": replace(binding, design_state_digest="a" * 64),
            "predecessor": replace(binding, predecessor_program_digest="a" * 64),
        }
        with tempfile.TemporaryDirectory() as temporary, _no_process():
            workspace = Path(temporary)
            for dimension, wrong in wrong_bindings.items():
                with self.subTest(dimension=dimension), self.assertRaises(CadExecutionError):
                    _request(workspace, program=program, binding=wrong)
                self.assertEqual(list(workspace.iterdir()), [])

    def test_binding_rejects_records_outside_the_exact_branch_stage_and_digest(self):
        program = _program()
        binding = _binding(program)
        for old, new in (("candidate-a", "candidate-b"), ("stage-3-", "stage-4-"), ("8" * 64, "9" * 64)):
            with self.subTest(part=old), self.assertRaises(CadExecutionError):
                replace(binding, program_ref=replace(binding.program_ref, relative_path=binding.program_ref.relative_path.replace(old, new)))

    def test_request_requires_explicit_existing_workspace_and_portable_artifact_stem(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "keep.step"
            sentinel.write_bytes(b"existing user artifact")
            for workspace in (Path("."), root / "missing"):
                with self.subTest(workspace=workspace), self.assertRaises(CadExecutionError):
                    _request(workspace)
            for stem in ("../escape", "nested/output", "C:\\escape", ""):
                with self.subTest(stem=stem), self.assertRaises(CadExecutionError):
                    replace(_request(root), artifact_stem=stem)
            with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(CadExecutionError):
                _request(root)
            self.assertEqual(sentinel.read_bytes(), b"existing user artifact")
            self.assertEqual(list(root.iterdir()), [sentinel])


class CadBackendConformanceTests(unittest.TestCase):
    def _successful_run(self, backend_id, *, host_options=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = FilesystemProjectRepository.initialize(root / "project", project_id="generic-building", initial_state={"schema": "TestState@1"})
            head_before = repository.read_head()
            object_identity = []
            for run_id in ("contract-first", "contract-second"):
                run = repository.create_run(run_id)
                program, binding = _persisted_program(repository, run)
                workspace = root / run_id
                workspace.mkdir()
                if backend_id == "rhino":
                    host = _controlled_rhino(root) if host_options is None else nullcontext(host_options)
                    with host as backend_options:
                        request = _request(workspace, program=program, binding=binding, backend_options=backend_options)
                        result = cad_backend.get_cad_backend(backend_id).execute(request)
                else:
                    request = _request(workspace, program=program, binding=binding)
                    with _no_process():
                        result = cad_backend.get_cad_backend(backend_id).execute(request)
                result.validate(request, backend_id)
                self.assertEqual((result.backend_id, result.status, result.readback_verified), (backend_id, "succeeded", True), result.failures)
                self.assertEqual(result.binding, binding)
                self.assertEqual(result.physical_object_ids, ("body-object",))
                self.assertFalse(result.failures)
                self.assertIsNotNone(result.inspection)
                self.assertTrue(result.artifacts)
                for artifact in result.artifacts:
                    relative = PurePosixPath(artifact.relative_path)
                    self.assertFalse(relative.is_absolute())
                    self.assertNotIn("..", relative.parts)
                    self.assertTrue(artifact.verified)
                    path = workspace / artifact.relative_path
                    self.assertEqual(path.parent, workspace)
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact.sha256)
                receipt = result.receipt_payload
                self.assertEqual(receipt["identity"]["binding"], binding.to_dict())
                self.assertEqual(receipt["schema"], "RhinoCadExecutionReceipt@4" if backend_id == "rhino" else "OcctExecutionReceipt@1")
                # The public wrapper must also consume the existing native
                # receipt shape, without introducing a new persistent schema.
                retained = cad_backend.get_cad_backend(backend_id).read_receipt(request, receipt)
                retained.validate(request, backend_id)
                self.assertEqual(retained.artifacts, result.artifacts)
                self.assertEqual(retained.receipt_payload, receipt)
                object_identity.append(result.expected_semantics["objects"])
                self.assertEqual(repository.read_head(), head_before)
                if backend_id == "occt":
                    step = next(workspace / a.relative_path for a in result.artifacts if a.relative_path.endswith(".step"))
                    entries = occt_backend.read_step(step, length_unit=program.proposal.length_unit.value)
                    self.assertEqual([entry.name for entry in entries], ["body-object"])
                    self.assertEqual(entries[0].layers, (result.expected_semantics["objects"]["body-object"]["layer"],))
                else:
                    self.assertEqual(receipt["cleanup_status"], "confirmed")
                    self.assertTrue(receipt["host_witness_sha256"])
                    self.assertTrue(receipt["cleanup_witness_sha256"])
                    if host_options is not None:
                        saved = workspace / result.artifacts[0].relative_path
                        cold = cad_execution.inspect_three_dm(saved)
                        self.assertEqual(cold.to_dict(), result.inspection)

                with self.assertRaises(CadExecutionError):
                    replace(result, binding=replace(binding, program_digest="a" * 64)).validate(request, backend_id)
                with self.assertRaises(CadExecutionError):
                    replace(result, expected_semantics={}).validate(request, backend_id)
                with self.assertRaises(CadExecutionError):
                    replace(result, readback_verified=False).validate(request, backend_id)
                with self.assertRaises(CadExecutionError):
                    replace(result, artifacts=(replace(result.artifacts[0], relative_path="../escape.step"),)).validate(request, backend_id)
                first_path = workspace / result.artifacts[0].relative_path
                original = first_path.read_bytes()
                first_path.write_bytes(b"modified after readback")
                with self.assertRaises(CadExecutionError):
                    result.validate(request, backend_id)
                first_path.write_bytes(original)
            self.assertEqual(object_identity[0], object_identity[1])

    @NEEDS_OCCT
    def test_occt_conforms_with_real_step_and_preview_readback_and_unchanged_head(self):
        self._successful_run("occt")

    def test_rhino_conforms_with_controlled_host_evidence_and_unchanged_head(self):
        self._successful_run("rhino")

    @NEEDS_RHINO
    def test_real_rhino_conforms_with_saved_geometry_and_unchanged_head(self):
        self._successful_run("rhino", host_options=_real_rhino_options())

    def _reject_changed_maps(self, backend_id, *, host_options=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            maps = {
                "layer_by_component": {"body-component": "archflow"},
                "material_by_component": {"body-component": "stucco"},
                "material_colors": {"stucco": (210, 205, 190)},
            }
            backend = cad_backend.get_cad_backend(backend_id)
            if backend_id == "rhino":
                host = _controlled_rhino(root) if host_options is None else nullcontext(host_options)
                with host as options:
                    request = _request(workspace, backend_options=options, **maps)
                    result = backend.execute(request)
            else:
                request = _request(workspace, **maps)
                with _no_process():
                    result = backend.execute(request)
            self.assertEqual(result.status, "succeeded", result.failures)
            if host_options is not None:
                cold = cad_execution.inspect_three_dm(workspace / result.artifacts[0].relative_path)
                self.assertEqual(cold.to_dict(), result.inspection)
                self.assertEqual(len(cold.object_material_bindings), 1)
                material = cold.object_material_bindings[0]
                self.assertEqual(material["material_name"], "stucco")
                self.assertEqual(material["material_source"], "MaterialFromObject")
                self.assertEqual(tuple(material["material_diffuse_color_rgba"]), (210, 205, 190, 255))
            payload = deepcopy(result.receipt_payload)
            original_files = {artifact.relative_path: (workspace / artifact.relative_path).read_bytes() for artifact in result.artifacts}
            for name, value in (
                ("layer_by_component", {"body-component": "30_FINISH"}),
                ("material_by_component", {"body-component": "timber"}),
            ):
                with self.subTest(backend=backend_id, changed=name):
                    changed = replace(request, **{name: value})
                    self.assertEqual(changed.binding, request.binding)
                    self.assertEqual(changed.program.program_digest, request.program.program_digest)
                    with self.assertRaisesRegex(CadExecutionError, "semantic identity"):
                        retained = backend.read_receipt(changed, payload)
                        retained.validate(changed, backend_id)
            backend.read_receipt(request, payload).validate(request, backend_id)
            self.assertEqual(payload, result.receipt_payload)
            self.assertEqual({name: (workspace / name).read_bytes() for name in original_files}, original_files)

    @NEEDS_OCCT
    def test_occt_retained_receipt_rejects_changed_layer_and_material_maps(self):
        self._reject_changed_maps("occt")

    def test_rhino_retained_readback_rejects_changed_layer_and_material_maps(self):
        self._reject_changed_maps("rhino")

    @NEEDS_RHINO
    def test_real_rhino_preserves_native_material_and_rejects_changed_maps(self):
        self._reject_changed_maps("rhino", host_options=_real_rhino_options())

    @NEEDS_OCCT
    def test_occt_does_not_turn_a_failed_cold_read_into_a_verified_result(self):
        with tempfile.TemporaryDirectory() as temporary, _no_process():
            request = _request(Path(temporary))
            with patch.object(cad_execution, "read_step", side_effect=occt_backend.OcctBackendError("controlled cold-read failure")):
                result = cad_backend.get_cad_backend("occt").execute(request)
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.readback_verified)
            self.assertTrue(result.failures)
            self.assertEqual(result.receipt_payload["schema"], "OcctExecutionReceipt@1")

    def test_rhino_does_not_turn_completion_without_matching_semantics_into_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            with _controlled_rhino(root, bad_inspection=True) as options:
                result = cad_backend.get_cad_backend("rhino").execute(_request(workspace, backend_options=options))
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.readback_verified)
            self.assertTrue(result.failures)
            self.assertEqual(result.receipt_payload["schema"], "RhinoCadExecutionReceipt@4")

    @NEEDS_OCCT
    def test_occt_names_an_unsupported_operation_without_writing_or_falling_back(self):
        seed = _box("seed", [0, 0, 0], [1, 1, 1])
        program = _program_of(seed, _radial_array("ring", seed))
        with tempfile.TemporaryDirectory() as temporary, _no_process():
            workspace = Path(temporary)
            with patch.object(cad_execution, "prepare_rhino_three_dm_export", side_effect=AssertionError("unexpected Rhino fallback")):
                request = _request(workspace, program=program)
                result = cad_backend.get_cad_backend("occt").execute(request)
            result.validate(request, "occt")
            self.assertEqual((result.status, result.readback_verified), ("unsupported", False))
            self.assertEqual((result.failures[0]["op_id"], result.failures[0]["kind"]), ("ring", "radial_array"))
            self.assertEqual(result.artifacts, ())
            self.assertEqual(list(workspace.iterdir()), [])

    def test_rhino_names_an_unsupported_profile_before_writing_or_calling_the_host(self):
        program = _single_operation_program(_loft("unsupported-profile", profile_basis="nurbs"))
        with tempfile.TemporaryDirectory() as temporary, _no_process():
            workspace = Path(temporary)
            request = _request(workspace, program=program)
            result = cad_backend.get_cad_backend("rhino").execute(request)
            result.validate(request, "rhino")
            self.assertEqual((result.status, result.readback_verified), ("unsupported", False))
            self.assertIn("unsupported-profile", result.failures[0]["detail"])
            self.assertIn("profile_basis", result.failures[0]["detail"])
            self.assertEqual(result.artifacts, ())
            self.assertEqual(list(workspace.iterdir()), [])

    def test_both_backends_refuse_existing_artifacts_without_overwriting_them(self):
        for backend_id, suffix in (("occt", ".step"), ("rhino", ".3dm")):
            with self.subTest(backend=backend_id), tempfile.TemporaryDirectory() as temporary, _no_process():
                workspace = Path(temporary)
                request = _request(workspace)
                existing = workspace / (request.artifact_stem + suffix)
                existing.write_bytes(b"keep existing artifact")
                with self.assertRaises(CadExecutionError):
                    cad_backend.get_cad_backend(backend_id).execute(request)
                self.assertEqual(existing.read_bytes(), b"keep existing artifact")
                self.assertEqual(list(workspace.iterdir()), [existing])


def _cad_seats(receipt):
    return {seat["seat_id"]: seat["cad"] for seat in receipt["seat_results"]}


def _taller_plinth(record):
    return replace(record, entities=tuple(
        replace(entity, fields={**entity.fields, "params": {**entity.fields["params"], "height": 0.8}})
        if entity.entity_id == "columns-plinth" else entity
        for entity in record.entities
    ))


class _RegisteredContractBackend:
    """Only a test implementation; native OCCT remains the artifact producer."""
    backend_id = "contract-test"
    record_kind = "seat-occt-execution"
    patch_rebuild = False

    def __init__(self, *, unsupported=False):
        self.native = cad_backend.OcctBackend()
        self.requests = []
        self.unsupported = unsupported

    def validate_options(self, options):
        self.native.validate_options(options)

    def execute(self, request):
        self.requests.append(request)
        if self.unsupported:
            return cad_backend.CadExecutionResult(
                backend_id=self.backend_id, binding=request.binding, status="unsupported", readback_verified=False,
                artifacts=(), physical_object_ids=(), expected_semantics={}, receipt_payload=None, inspection=None,
                failures=({"code": "cad_execution.unsupported_operation", "detail": "contract-test does not realize this program"},),
                execution_path=self.backend_id,
            )
        return replace(self.native.execute(request), backend_id=self.backend_id, execution_path=self.backend_id)

    def read_receipt(self, request, payload):
        return replace(self.native.read_receipt(request, payload), backend_id=self.backend_id, execution_path=self.backend_id)


class RegisteredBackendRunnerTests(unittest.TestCase):
    @NEEDS_OCCT
    def test_one_registration_enables_runner_execution_retention_and_reuse(self):
        backend = _RegisteredContractBackend()
        with patch.dict(cad_backend.CAD_BACKEND_REGISTRY, {backend.backend_id: backend}), _no_process():
            project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)), cad_backend=backend.backend_id)
            head = project.repository.read_head()
            first = _cad_seats(project.run_once())
            self.assertEqual(len(backend.requests), 2)
            for seat in first.values():
                self.assertEqual((seat["status"], seat["backend"], seat["path"]), ("succeeded", "contract-test", "contract-test"))
                retained = project.repository.load_json(record_ref_from_uri(seat["execution_ref"], "demo"))
                self.assertEqual(retained["schema"], "OcctExecutionReceipt@1")
            second = _cad_seats(project.run_once())
            self.assertEqual(len(backend.requests), 2, "same-binding reuse must not call any executor")
            for seat_id in first:
                self.assertEqual(second[seat_id]["path"], "reused")
                self.assertEqual(second[seat_id]["execution_ref"], first[seat_id]["execution_ref"])
            self.assertEqual(project.repository.read_head(), head)
        with self.assertRaisesRegex(ProjectRunnerError, "unknown cad_backend"):
            _options(cad_backend=backend.backend_id)

    def test_registered_unsupported_result_is_retained_in_runner_without_native_fallback(self):
        backend = _RegisteredContractBackend(unsupported=True)
        with patch.dict(cad_backend.CAD_BACKEND_REGISTRY, {backend.backend_id: backend}), _no_process(), \
             patch.multiple(cad_execution,
                 execute_occt_export=lambda *a, **k: self.fail("unsupported backend must not call OCCT"),
                 prepare_rhino_three_dm_export=lambda *a, **k: self.fail("unsupported backend must not prepare Rhino"),
                 execute_rhino_three_dm_export=lambda *a, **k: self.fail("unsupported backend must not call Rhino")):
            project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)), cad_backend=backend.backend_id)
            head = project.repository.read_head()
            receipt = project.run_once()
            self.assertEqual(len(backend.requests), 2)
            self.assertFalse(receipt["seat_execution_complete"])
            for seat_id, cad in _cad_seats(receipt).items():
                self.assertEqual((cad["status"], cad["backend"], cad["readback_verified"]), ("unsupported", "contract-test", False))
                self.assertIn("contract-test", cad["failures"][0]["detail"])
                self.assertIsNone(cad["execution_ref"])
                self.assertEqual(project.files(seat_id), [])
            retained = project.repository.load_json(record_ref_from_uri(receipt["receipt_ref"], "demo"))
            self.assertEqual([s["cad"]["status"] for s in retained["seat_results"]], ["unsupported", "unsupported"])
            self.assertEqual(project.repository.read_head(), head)


class ControlledRhinoRunnerTests(unittest.TestCase):
    def test_first_export_reuses_then_changed_record_runs_patch_and_full_rebuild_oracle(self):
        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)), cad_backend="rhino", patch_oracle=True)
        plans = []
        head = project.repository.read_head()
        with _controlled_rhino(project.root, plans=plans) as options:
            project.options = replace(project.options, cad_backend_options=options)
            first = _cad_seats(project.run_once())
            self.assertEqual(len(plans), 2)
            self.assertTrue(all(plan.patch is None for plan in plans))
            self.assertTrue(all(cad["path"] == "rebuild" for cad in first.values()))
            same = _cad_seats(project.run_once())
            self.assertEqual(len(plans), 2, "reuse must not prepare or start the controlled host")
            for seat_id in first:
                self.assertEqual(same[seat_id]["path"], "reused")
                self.assertEqual(same[seat_id]["execution_ref"], first[seat_id]["execution_ref"])
            changed = _cad_seats(project.run_once(_taller_plinth(project.record)))
            self.assertEqual(len(plans), 6, "each seat must run its patch/restamp and its independent rebuild oracle")
            self.assertEqual(changed["seat-structure"]["path"], "patch")
            self.assertEqual(changed["seat-envelope"]["path"], "restamp")
            for seat_id, cad in changed.items():
                self.assertTrue(cad["oracle"]["equal"])
                self.assertGreater(cad["oracle"]["objects_compared"], 0)
                self.assertEqual(cad["oracle"]["worst_m"], 0.0)
                self.assertNotEqual(cad["execution_ref"], first[seat_id]["execution_ref"])
                self.assertNotEqual(cad["oracle"]["execution_ref"], cad["execution_ref"])
                self.assertEqual(cad["prior_model"], first[seat_id]["model"])
            self.assertEqual(project.repository.read_head(), head)

    def test_oracle_still_rejects_a_difference_within_individual_readback_tolerance(self):
        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)), cad_backend="rhino", patch_oracle=True)
        head = project.repository.read_head()
        with _controlled_rhino(project.root, oracle_shift=0.002) as options:
            project.options = replace(project.options, cad_backend_options=options)
            project.run_once()
            # Both native inspections pass their 3 mm tolerance; the original
            # runner's independent 1 mm patch/oracle comparison must reject.
            with self.assertRaisesRegex(ProjectRunnerError, "patch oracle disagrees"):
                project.run_once(_taller_plinth(project.record))
            self.assertEqual(project.repository.read_head(), head)

    def test_changed_file_and_hash_valid_wrong_binding_are_not_reused_or_patched(self):
        for corruption in ("artifact", "binding"):
            with self.subTest(corruption=corruption):
                project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)), cad_backend="rhino")
                plans = []
                head = project.repository.read_head()
                with _controlled_rhino(project.root, plans=plans) as options:
                    project.options = replace(project.options, cad_backend_options=options)
                    first = _cad_seats(project.run_once())
                    rejected = first["seat-structure"]
                    if corruption == "artifact":
                        Path(rejected["model"]).write_bytes(b"artifact changed after its receipt")
                    else:
                        ref = record_ref_from_uri(rejected["execution_ref"], "demo")
                        payload = deepcopy(project.repository.load_json(ref))
                        payload["identity"]["binding"]["run_id"] = "other-run"
                        wrong_ref = project.repository.put_json(
                            run=project.run,
                            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=project.run.run_id),
                            record_kind="seat-rhino-execution", payload=payload,
                        )
                        self.assertEqual(project.repository.load_json(wrong_ref)["identity"]["binding"]["run_id"], "other-run")
                        # Disposable fixture only: leave a hash-valid wrong
                        # receipt as the sole retained receipt for this seat.
                        (project.root / "demo" / ref.relative_path).unlink()
                    spans = []
                    second = _cad_seats(project.run_once(operation_observer=spans.append))
                    self.assertEqual(len(plans), 3)
                    self.assertIsNone(plans[-1].patch, "an invalid retained source must not become a patch base")
                    self.assertEqual(second["seat-structure"]["path"], "rebuild")
                    self.assertNotEqual(second["seat-structure"]["execution_ref"], rejected["execution_ref"])
                    self.assertNotEqual(second["seat-structure"]["model"], rejected["model"])
                    self.assertEqual(second["seat-envelope"]["execution_ref"], first["seat-envelope"]["execution_ref"])
                    self.assertEqual(second["seat-envelope"]["path"], "reused")
                    self.assertTrue(any(event["phase"] == "export_cache_lookup" and event["details"]["cache_status"] == "miss" for event in spans))
                    self.assertEqual(project.repository.read_head(), head)


@NEEDS_RHINO
class RealRhinoRunnerTests(unittest.TestCase):
    def test_real_runner_retains_and_reuses_exact_native_receipts_after_restart(self):
        record = _record(elements=(), extra_entities=(
            _prism_row(), _prism_row("exterior-walls", "envelope-plinth"),
        ))
        project = _ExportProject(self, record, cad_backend="rhino", cad_backend_options=_real_rhino_options())
        head = project.repository.read_head()
        first = _cad_seats(project.run_once())
        self.assertEqual(set(first), {"seat-structure", "seat-envelope"})
        original = {}
        for seat_id, result in first.items():
            self.assertEqual((result["status"], result["backend"]), ("succeeded", "rhino"), result)
            reference = record_ref_from_uri(result["execution_ref"], "demo")
            self.assertEqual(reference.record_kind, "seat-rhino-execution")
            payload = project.repository.load_json(reference)
            self.assertEqual(payload["schema"], "RhinoCadExecutionReceipt@4")
            self.assertTrue(payload["readback_verified"])
            self.assertEqual(payload["cleanup_status"], "confirmed")
            original[seat_id] = (reference, payload, Path(result["model"]).read_bytes())
        self.assertEqual(project.repository.read_head(), head)

        project.repository = FilesystemProjectRepository.open(project.repository.layout.root)
        project.run = project.repository.load_run(project.run.run_id)
        with _no_process(), patch.object(cad_backend.RhinoBackend, "execute", side_effect=AssertionError("exact reuse started Rhino")):
            restarted = _cad_seats(project.run_once())
        self.assertEqual(set(restarted), set(first))
        for seat_id, result in restarted.items():
            self.assertEqual(result["path"], "reused")
            self.assertEqual(result["execution_ref"], first[seat_id]["execution_ref"])
            reference, payload, model = original[seat_id]
            self.assertEqual(project.repository.load_json(reference), payload)
            self.assertEqual(Path(result["model"]).read_bytes(), model)
        self.assertEqual(project.repository.read_head(), head)


if __name__ == "__main__":
    unittest.main()
