"""The Blender CAD slice, including real saved-scene and runner acceptance.

Set ARCHFLOW_BLENDER_EXECUTABLE to explicitly enable real Blender processes.
Without it, host tests skip; request rejection tests still run normally.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archflow.adapters import cad_backend, cad_execution
from archflow.adapters.cad_backend import CadExecutionRequest, CadExecutionSource
from archflow.adapters.cad_execution import CadExecutionError
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.geometry_program import (
    GeometryOperation, GeometryOperationKind, GeometryParameter,
    GeometryParameterKind, LengthUnit,
)
from tests.test_cad_execution import _binding
from tests.test_cad_backend_contract import _taller_plinth
from tests.test_occt_execution import _box, _loft, _program_of
from tests.test_project_runner import _ExportProject, _options, _prism_row, _record


BLENDER_EXECUTABLE = os.environ.get("ARCHFLOW_BLENDER_EXECUTABLE")
NEEDS_BLENDER = unittest.skipUnless(
    BLENDER_EXECUTABLE, "set ARCHFLOW_BLENDER_EXECUTABLE to run real Blender acceptance",
)
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


def _request(workspace, *, program=None, **changes):
    program = program or _two_objects()
    return CadExecutionRequest(
        program=program, binding=_binding(program), speculative_workspace=workspace,
        artifact_stem="blender-candidate", **changes,
    )


@contextmanager
def _without_other_backends():
    with patch.object(cad_backend.OcctBackend, "execute", side_effect=AssertionError("unexpected OCCT fallback")), \
         patch.object(cad_backend.RhinoBackend, "execute", side_effect=AssertionError("unexpected Rhino fallback")), \
         patch.object(cad_execution, "execute_occt_export", side_effect=AssertionError("unexpected native OCCT export")), \
         patch.object(cad_execution, "execute_rhino_three_dm_export", side_effect=AssertionError("unexpected native Rhino export")):
        yield


@contextmanager
def _without_processes():
    with patch.object(subprocess, "Popen", side_effect=AssertionError("rejected request started a process")), \
         patch.object(subprocess, "run", side_effect=AssertionError("rejected request started a process")):
        yield


class BlenderRequestTests(unittest.TestCase):
    def test_selection_uses_the_public_registry_and_rejects_foreign_options(self):
        with _without_processes(), _without_other_backends():
            self.assertIn("blender", cad_backend.cad_backend_ids())
            self.assertEqual(_options(cad_backend="blender").cad_backend, "blender")
            self.assertEqual(_options(cad_backend="blender", powershell=Path("C:/legacy/powershell.exe")).cad_backend, "blender")
            backend = cad_backend.get_cad_backend("blender")
            for options in ({"preview": True}, {"patch_oracle": True}, {"timeout_seconds": 0}, {"timeout_seconds": -1}, {"blender_executable": []}):
                with self.subTest(options=options), self.assertRaises(CadExecutionError):
                    backend.validate_options(options)

    def test_unsupported_operation_does_not_start_a_host_or_write_a_partial_scene(self):
        program = _program_of(_box("box", [0, 0, 0], [1, 2, 3]), _loft("unsupported-loft"))
        with tempfile.TemporaryDirectory() as temporary, _without_processes(), _without_other_backends():
            workspace = Path(temporary)
            keep = workspace / "keep.txt"
            keep.write_text("existing workspace content", encoding="utf-8")
            request = _request(workspace, program=program)
            result = cad_backend.get_cad_backend("blender").execute(request)
            result.validate(request, "blender")
            self.assertEqual(result.status, "unsupported")
            self.assertFalse(result.readback_verified)
            self.assertEqual(result.artifacts, ())
            self.assertTrue(any("unsupported-loft" in str(failure) for failure in result.failures))
            self.assertEqual(list(workspace.iterdir()), [keep])
            self.assertEqual(keep.read_text(encoding="utf-8"), "existing workspace content")

    def test_degenerate_supported_shapes_are_refused_before_host_execution(self):
        for operation in (_box("flat-box", [0, 0, 0], [1, 0, 3]), _extrusion(vector=(1, 0, 0))):
            with self.subTest(kind=operation.kind), tempfile.TemporaryDirectory() as temporary, _without_processes(), _without_other_backends():
                workspace = Path(temporary)
                request = _request(workspace, program=_program_of(operation))
                try:
                    result = cad_backend.get_cad_backend("blender").execute(request)
                except CadExecutionError:
                    pass
                else:
                    self.assertEqual(result.status, "unsupported")
                    self.assertFalse(result.readback_verified)
                    self.assertEqual(result.artifacts, ())
                self.assertEqual(list(workspace.iterdir()), [])

    def test_wrong_program_binding_and_workspace_traversal_are_refused_at_request_construction(self):
        with tempfile.TemporaryDirectory() as temporary, _without_processes():
            workspace = Path(temporary)
            request = _request(workspace)
            with self.assertRaises(CadExecutionError):
                replace(request, binding=replace(request.binding, program_digest="a" * 64))
            with self.assertRaises(CadExecutionError):
                replace(request, speculative_workspace=Path("."))
            for stem in ("../escape", "nested/model", "C:\\escape"):
                with self.subTest(stem=stem), self.assertRaises(CadExecutionError):
                    replace(request, artifact_stem=stem)
            self.assertEqual(list(workspace.iterdir()), [])


@NEEDS_BLENDER
class BlenderHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.workspace = Path(cls.temporary.name)
        cls.executable = Path(BLENDER_EXECUTABLE).resolve(strict=True)
        cls.backend = cad_backend.get_cad_backend("blender")
        cls.request = _request(
            cls.workspace, backend_options={"blender_executable": cls.executable, "timeout_seconds": 60},
            provenance={"source": "blender-acceptance"},
            material_by_component={"body-component": "stucco"},
            material_colors={"stucco": (210, 205, 190)},
        )
        with _without_other_backends(), patch.object(subprocess, "Popen", wraps=subprocess.Popen) as processes:
            cls.result = cls.backend.execute(cls.request)
        if cls.result.status != "succeeded":
            raise AssertionError(cls.result.failures)
        cls.process_commands = [call.args[0] for call in processes.call_args_list]
        cls.result.validate(cls.request, "blender")
        cls.artifact = next(artifact for artifact in cls.result.artifacts if artifact.name == "model")
        cls.model = cls.workspace / cls.artifact.relative_path

    def test_saved_scene_is_cold_read_in_another_real_process(self):
        self.assertEqual(len(self.process_commands), 2, self.process_commands)
        self.assertTrue(any("build" in command for command in self.process_commands))
        self.assertTrue(any("inspect" in command for command in self.process_commands))
        self.assertEqual(self.result.receipt_payload["schema"], "BlenderExecutionReceipt@1")
        self.assertEqual(self.artifact.format, "blend")
        self.assertEqual(self.model.suffix, ".blend")
        self.assertEqual(self.model.parent, self.workspace)
        self.assertTrue(self.artifact.verified)
        self.assertGreater(self.model.stat().st_size, 0)
        self.assertIsNone(self.result.inspection)
        readback = self.result.receipt_payload["readback"]
        self.assertEqual(json.loads(readback["binding_json"]), self.request.binding.to_dict())
        self.assertEqual(readback["length_unit"], "millimeter")
        self.assertAlmostEqual(readback["unit_scale"], 0.001, places=7)

    def test_box_and_nonrectangular_extrusion_keep_geometry_datum_and_stable_identity(self):
        rows = {row["object_id"]: row for row in self.result.receipt_payload["readback"]["objects"]}
        box_id = f"{LONG_BOX_ID}-object"
        self.assertEqual(set(rows), {box_id, "triangle-object"})
        expected = {
            box_id: ([10, 30, 20], [12, 34, 23], 24),
            "triangle-object": ([1, 2, 7], [7, 6, 10], 18),
        }
        for object_id, (low, high, volume) in expected.items():
            with self.subTest(object_id=object_id):
                row = rows[object_id]
                self.assertTrue(row["closed"])
                self.assertAlmostEqual(row["volume"], volume, places=5)
                for actual, wanted in zip(row["bounds"]["min"] + row["bounds"]["max"], low + high):
                    self.assertAlmostEqual(actual, wanted, places=5)
                self.assertEqual(row["object_digest"], self.request.program.object_digest(object_id))
                self.assertEqual(row["user_text"]["archflow:object_ref"], f"cad-object:{object_id}")
                self.assertEqual(row["user_text"], self.result.expected_semantics["objects"][object_id]["user_text"])
                self.assertEqual(row["layer"], self.result.expected_semantics["objects"][object_id]["layer"])
        self.assertEqual(len(rows["triangle-object"]["vertices"]), 6)
        self.assertNotEqual(rows[box_id]["display_name"], box_id, "the fixture must exercise Blender's shortened native name")
        self.assertIn(box_id, self.result.physical_object_ids)

    def test_retained_receipt_rejects_geometry_semantic_and_scene_binding_tampering(self):
        def vertex(payload):
            payload["readback"]["objects"][0]["vertices"][0][0] += 0.5

        def volume(payload):
            payload["readback"]["objects"][0]["volume"] += 1

        def closed(payload):
            payload["readback"]["objects"][0]["closed"] = False

        def semantics(payload):
            payload["readback"]["objects"][0]["user_text"]["archflow:component"] = "wrong-component"

        def object_digest(payload):
            payload["readback"]["objects"][0]["object_digest"] = "f" * 64

        def scene_binding(payload):
            binding = json.loads(payload["readback"]["binding_json"])
            binding["run_id"] = "other-run"
            payload["readback"]["binding_json"] = json.dumps(binding)

        def receipt_binding(payload):
            payload["identity"]["binding"]["program_digest"] = "f" * 64

        for mutate in (vertex, volume, closed, semantics, object_digest, scene_binding, receipt_binding):
            with self.subTest(tamper=mutate.__name__), _without_processes():
                payload = deepcopy(self.result.receipt_payload)
                mutate(payload)
                with self.assertRaises(CadExecutionError):
                    retained = self.backend.read_receipt(self.request, payload)
                    retained.validate(self.request, "blender")
        self.backend.read_receipt(self.request, self.result.receipt_payload).validate(self.request, "blender")

    def test_retained_receipt_cannot_certify_a_new_material_or_layer_mapping(self):
        for change in ({"material_by_component": {"body-component": "timber"}},
                       {"layer_by_component": {"body-component": "changed-layer"}}):
            with self.subTest(change=change), _without_processes():
                request = replace(self.request, **change)
                with self.assertRaises(CadExecutionError):
                    result = self.backend.read_receipt(request, self.result.receipt_payload)
                    result.validate(request, "blender")

    def test_changed_blend_bytes_fail_public_validation_and_same_stem_cannot_overwrite(self):
        original = self.model.read_bytes()
        try:
            self.model.write_bytes(original + b"changed after verified readback")
            with self.assertRaises(CadExecutionError):
                self.result.validate(self.request, "blender")
        finally:
            self.model.write_bytes(original)
        before = {path.name: path.read_bytes() for path in self.workspace.iterdir() if path.is_file()}
        with _without_processes(), self.assertRaises(CadExecutionError):
            self.backend.execute(self.request)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.workspace.iterdir() if path.is_file()})

    def test_large_coordinate_rounding_cannot_relax_the_absolute_readback_tolerance(self):
        program = _program_of(_box("survey-box", [500000.01, 0, 0], [2, 3, 4]))
        request = replace(
            _request(self.workspace, program=program, backend_options=self.request.backend_options),
            artifact_stem="survey-coordinate", readback_tolerance=0.003,
        )
        with _without_other_backends():
            result = self.backend.execute(request)
        self.assertEqual(result.status, "failed", result.failures)
        self.assertFalse(result.readback_verified)
        row = result.receipt_payload["readback"]["objects"][0]
        error = abs(row["bounds"]["min"][0] - 500000.01)
        self.assertGreater(error, request.readback_tolerance, f"actual saved coordinate error: {error} m")
        self.assertAlmostEqual(error, 0.01, places=5)
        self.assertTrue(any("geometry" in failure["detail"] or "bounds" in failure["detail"] for failure in result.failures))
        result.validate(request, "blender")

    def test_cold_read_rejects_changed_face_connections_but_accepts_equivalent_loop_order(self):
        from archflow.adapters import blender_cad

        extrusion = _extrusion(vector=(0, 1, 0))
        parameters = {
            "base_level": GeometryParameter.create(name="base_level", kind=GeometryParameterKind.NUMBER, value=0.0, unit=LengthUnit.METER),
            "base_offset": GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=0.0, unit=LengthUnit.METER),
            "profile": GeometryParameter.create(
                name="profile", kind=GeometryParameterKind.POINTS3,
                value=[[0, 0, 0], [2, 0, 0], [2, 0, 2], [1, 0, 1], [0, 0, 2]], unit=LengthUnit.METER,
            ),
        }
        program = _program_of(replace(extrusion, parameters=tuple(parameters.get(parameter.name, parameter) for parameter in extrusion.parameters)))
        request = replace(
            _request(self.workspace, program=program, backend_options=self.request.backend_options),
            artifact_stem="concave-original",
        )
        with _without_other_backends():
            original = self.backend.execute(request)
        self.assertEqual(original.status, "succeeded", original.failures)
        original.validate(request, "blender")
        original_row = original.receipt_payload["readback"]["objects"][0]
        self.assertTrue(original_row["closed"])
        self.assertAlmostEqual(original_row["volume"], 3.0, places=5)

        # Face-loop origins, normals and native vertex indices may change
        # without changing the actual surface represented by those loops.
        for change in ("rotate", "reverse", "reindex"):
            with self.subTest(equivalent=change), _without_processes():
                payload = deepcopy(original.receipt_payload)
                row = payload["readback"]["objects"][0]
                if change == "reindex":
                    count = len(row["vertices"])
                    row["vertices"].reverse()
                    row["faces"] = [[count - 1 - index for index in face] for face in row["faces"]]
                elif change == "rotate":
                    row["faces"] = [face[1:] + face[:1] for face in row["faces"]]
                else:
                    row["faces"] = [list(reversed(face)) for face in row["faces"]]
                row["faces"].reverse()
                retained = self.backend.read_receipt(request, payload)
                retained.validate(request, "blender")

        real_worker = blender_cad._run_worker
        changed_plans = []

        def change_connections(executable, workspace, timeout, *arguments):
            if arguments[0] == "build":
                plan_path = Path(arguments[1])
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                row = plan["objects"][0]
                # Change the V from the top to the bottom of the profile.
                # Vertex positions, bounds, volume and face count all remain.
                perimeter = [0, 3, 1, 2, 4]
                row["faces"] = [list(reversed(perimeter)), [index + 5 for index in perimeter]]
                row["faces"].extend(
                    [start, end, end + 5, start + 5]
                    for start, end in zip(perimeter, perimeter[1:] + perimeter[:1])
                )
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                changed_plans.append(plan_path)
            return real_worker(executable, workspace, timeout, *arguments)

        changed_request = replace(request, artifact_stem="concave-changed-connections")
        with _without_other_backends(), patch.object(blender_cad, "_run_worker", side_effect=change_connections):
            result = self.backend.execute(changed_request)
        self.assertEqual(len(changed_plans), 1)
        actual = result.receipt_payload["readback"]["objects"][0]
        self.assertEqual(actual["vertices"], original_row["vertices"])
        self.assertEqual(actual["bounds"], original_row["bounds"])
        self.assertAlmostEqual(actual["volume"], original_row["volume"], places=5)
        self.assertTrue(actual["closed"])
        self.assertEqual(len(actual["faces"]), len(original_row["faces"]))
        self.assertNotEqual(actual["faces"], original_row["faces"])
        self.assertEqual(result.status, "failed", result.failures)
        self.assertFalse(result.readback_verified)
        self.assertTrue(result.failures)
        result.validate(changed_request, "blender")

    def test_an_explicit_source_is_fully_rebuilt_without_overwriting_the_source(self):
        original = self.model.read_bytes()
        request = replace(
            self.request, artifact_stem="source-rebuild",
            source=CadExecutionSource(self.request.program, self.model, self.artifact.sha256),
            backend_options={"blender_executable": str(self.executable), "timeout_seconds": 60},
        )
        with _without_other_backends(), patch.object(subprocess, "Popen", wraps=subprocess.Popen) as processes:
            result = self.backend.execute(request)
        result.validate(request, "blender")
        self.assertEqual(result.status, "succeeded", result.failures)
        self.assertEqual(processes.call_count, 2, "the source must still be rebuilt and cold-read")
        self.assertEqual(result.reused_object_ids, ())
        self.assertEqual(result.physical_object_ids, self.result.physical_object_ids)
        self.assertNotEqual(result.artifacts[0].relative_path, self.artifact.relative_path)
        self.assertEqual(self.model.read_bytes(), original)


@NEEDS_BLENDER
class BlenderRunnerTests(unittest.TestCase):
    def test_runner_retains_and_reuses_after_restart_then_updates_the_same_objects(self):
        options = {"blender_executable": Path(BLENDER_EXECUTABLE).resolve(strict=True), "timeout_seconds": 60}
        record = _record(elements=(), extra_entities=(
            _prism_row(), _prism_row("exterior-walls", "envelope-plinth"),
        ))
        project = _ExportProject(self, record, cad_backend="blender", cad_backend_options=options)
        head = project.repository.read_head()

        def seats(receipt):
            return {seat["seat_id"]: seat["cad"] for seat in receipt["seat_results"] if seat.get("cad")}

        def retained(cad):
            ref = record_ref_from_uri(cad["execution_ref"], "demo")
            self.assertEqual(ref.record_kind, "seat-blender-execution")
            payload = project.repository.load_json(ref)
            self.assertEqual(payload["schema"], "BlenderExecutionReceipt@1")
            return payload

        with _without_other_backends():
            first = seats(project.run_once())
        self.assertEqual(set(first), {"seat-structure", "seat-envelope"})
        original_payloads = {}
        original_bytes = {}
        for seat_id, cad in first.items():
            self.assertEqual(cad["status"], "succeeded", cad)
            self.assertEqual(cad["backend"], "blender")
            original_payloads[seat_id] = retained(cad)
            original_bytes[seat_id] = Path(cad["model"]).read_bytes()
        self.assertEqual(project.repository.read_head(), head)
        self.assertEqual(project.records("seat-3dm-inspection"), [])

        project.repository = FilesystemProjectRepository.open(project.repository.layout.root)
        project.run = project.repository.load_run(project.run.run_id)
        backend = cad_backend.get_cad_backend("blender")
        with _without_other_backends(), patch.object(type(backend), "execute", side_effect=AssertionError("exact retained export reopened Blender")):
            restarted = seats(project.run_once())
        for seat_id, cad in restarted.items():
            self.assertEqual(cad["path"], "reused")
            self.assertEqual(cad["execution_ref"], first[seat_id]["execution_ref"])
            self.assertEqual(Path(cad["model"]).read_bytes(), original_bytes[seat_id])

        with _without_other_backends():
            changed = seats(project.run_once(_taller_plinth(record)))
        self.assertEqual(changed["seat-structure"]["status"], "succeeded", changed)
        self.assertEqual(changed["seat-envelope"]["status"], "succeeded", changed)
        envelope_after = retained(changed["seat-envelope"])
        old_envelope = {row["object_id"]: row for row in original_payloads["seat-envelope"]["readback"]["objects"]}
        new_envelope = {row["object_id"]: row for row in envelope_after["readback"]["objects"]}
        self.assertEqual(set(old_envelope), set(new_envelope))
        for object_id in old_envelope:
            for field in ("vertices", "bounds", "volume"):
                self.assertEqual(old_envelope[object_id][field], new_envelope[object_id][field])
            for key in ("archflow:object_ref", "archflow:component", "archflow:producer_op"):
                self.assertEqual(old_envelope[object_id]["user_text"][key], new_envelope[object_id]["user_text"][key])
        self.assertNotEqual(envelope_after["identity"]["binding"], original_payloads["seat-envelope"]["identity"]["binding"])
        before = original_payloads["seat-structure"]
        after = retained(changed["seat-structure"])
        old_rows = {row["object_id"]: row for row in before["readback"]["objects"]}
        new_rows = {row["object_id"]: row for row in after["readback"]["objects"]}
        self.assertEqual(set(old_rows), set(new_rows))
        self.assertNotEqual(before["identity"]["binding"]["program_digest"], after["identity"]["binding"]["program_digest"])
        for object_id in old_rows:
            self.assertAlmostEqual(old_rows[object_id]["bounds"]["max"][2], 4.0, places=5)
            self.assertAlmostEqual(new_rows[object_id]["bounds"]["max"][2], 4.3, places=5)
            self.assertGreater(new_rows[object_id]["volume"], old_rows[object_id]["volume"])
            self.assertNotEqual(new_rows[object_id]["object_digest"], old_rows[object_id]["object_digest"])
        for seat_id in first:
            self.assertEqual(Path(first[seat_id]["model"]).read_bytes(), original_bytes[seat_id])
        self.assertEqual(project.repository.read_head(), head)


if __name__ == "__main__":
    unittest.main()
