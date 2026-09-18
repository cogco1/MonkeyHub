"""Real OCCT -> saved/reopened Blender -> PNG, with source-bound failures."""
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archflow.adapters import cad_backend, cad_execution as cad, blender_projection as projection
from archflow.adapters.occt_backend import occt_available
from tests.test_occt_execution import _box, _program_of
from tests.test_cad_execution import _binding

BLENDER = os.environ.get("ARCHFLOW_BLENDER_EXECUTABLE")


@unittest.skipUnless(occt_available(), "cadquery-ocp is optional")
class ProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        program = _program_of(_box("floor", [0, 0, 0], [6, 0.2, 4]),
                              _box("wall", [0, 0.2, 0], [6, 3, 0.2]))
        self.request = cad_backend.CadExecutionRequest(program, _binding(program), self.workspace,
                                                       "pavilion", backend_options={"preview": False},
                                                       material_by_component={"body-component": "concrete"},
                                                       material_colors={"concrete": (160, 170, 180)})
        self.source = cad_backend.OcctBackend().execute(self.request)
        self.assertEqual(self.source.status, "succeeded", self.source.failures)

    def test_binding_and_source_bytes_refused_before_process(self):
        with patch.object(projection, "_run_worker", side_effect=AssertionError("unexpected Blender")):
            wrong = deepcopy(self.source.receipt_payload)
            wrong["identity"]["binding"]["run_id"] = "different"
            with self.assertRaises(cad.CadExecutionError):
                projection.execute_blender_projection(self.request, replace(self.source, receipt_payload=wrong), blender_executable="missing")
            exact = next(a for a in self.source.artifacts if a.name == "exact")
            (self.workspace / exact.relative_path).write_bytes(b"modified source")
            with self.assertRaises(cad.CadExecutionError):
                projection.execute_blender_projection(self.request, self.source, blender_executable="missing")

    def test_failure_has_no_success_artifacts(self):
        missing = projection.execute_blender_projection(self.request, self.source, blender_executable="missing-blender-159")
        self.assertEqual(missing["status"], "failed")
        self.assertEqual(missing["artifacts"], [])
        self.assertIn("unavailable", missing["failures"][0]["detail"])
        for error in (subprocess.TimeoutExpired("blender", 1, output="timeout log"),
                      subprocess.CalledProcessError(7, "blender", output="failure log")):
            with self.subTest(error=type(error)), patch.object(projection.shutil, "which", return_value="blender"), \
                    patch.object(projection, "_run_worker", side_effect=error):
                request = replace(self.request, artifact_stem=type(error).__name__)
                result = projection.execute_blender_projection(request, self.source, blender_executable="blender")
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["artifacts"])
                self.assertIn("log", (self.workspace / result["logs"][0]["relative_path"]).read_text())

    @unittest.skipUnless(BLENDER, "set ARCHFLOW_BLENDER_EXECUTABLE for real Blender")
    def test_full_rebuild_cold_read_and_artifact_provenance(self):
        settings = projection.BlenderPresentation(resolution=128, samples=2)
        results = []
        for stem in ("first", "repeat"):
            request = replace(self.request, artifact_stem=stem)
            result = projection.execute_blender_projection(request, self.source, blender_executable=BLENDER, presentation=settings)
            self.assertEqual(result["status"], "succeeded", result["failures"])
            projection.verify_projection_artifacts(request, self.source, result)
            results.append(result)
        self.assertEqual(results[0]["readback"], results[1]["readback"])
        from PIL import Image
        with Image.open(self.workspace / results[0]["artifacts"][1]["relative_path"]) as a, \
                Image.open(self.workspace / results[1]["artifacts"][1]["relative_path"]) as b:
            self.assertEqual(a.tobytes(), b.tobytes())
        self.assertEqual({r["object_id"] for r in results[0]["readback"]["objects"]}, {"floor-object", "wall-object"})
        for row in results[0]["readback"]["objects"]:
            self.assertEqual(row["user_text"]["archflow:material"], "concrete")
            self.assertEqual(row["material"]["name"], "concrete")
        with self.assertRaisesRegex(cad.CadExecutionError, "overwrite"):
            projection.execute_blender_projection(replace(self.request, artifact_stem="first"), self.source,
                                                   blender_executable=BLENDER, presentation=settings)
        # Retained proof must still bind exactly, even when no new host starts.
        bad = deepcopy(results[0])
        bad["source_artifact"]["sha256"] = "0" * 64
        with self.assertRaises(cad.CadExecutionError):
            projection.verify_projection_artifacts(self.request, self.source, bad)
        image = self.workspace / results[0]["artifacts"][1]["relative_path"]
        image.write_bytes(b"tampered PNG")
        with self.assertRaises(cad.CadExecutionError):
            projection.verify_projection_artifacts(self.request, self.source, results[0])

    @unittest.skipUnless(BLENDER, "set ARCHFLOW_BLENDER_EXECUTABLE for real Blender")
    def test_blender_geometry_edit_is_rejected_without_changing_source(self):
        request = replace(self.request, artifact_stem="edit-refusal")
        plan = projection._plan(request, self.source, projection.BlenderPresentation(resolution=64, samples=1))
        result = projection.execute_blender_projection(request, self.source, blender_executable=BLENDER,
                                                       presentation=projection.BlenderPresentation(resolution=64, samples=1))
        self.assertEqual(result["status"], "succeeded", result["failures"])
        scene = self.workspace / result["artifacts"][0]["relative_path"]
        edited = self.workspace / "edited.blend"
        expression = ("import bpy; "
                      f"bpy.ops.wm.open_mainfile(filepath={str(scene)!r},use_scripts=False); "
                      "next(o for o in bpy.context.scene.objects if o.type=='MESH').data.vertices[0].co.x += 1; "
                      f"bpy.ops.wm.save_as_mainfile(filepath={str(edited)!r})")
        subprocess.run([BLENDER, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                        "--python-expr", expression], capture_output=True, check=True, timeout=120,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        changed = projection._readback(projection._run_worker(BLENDER, self.workspace, 120, "inspect", edited))
        with self.assertRaisesRegex(cad.CadExecutionError, "reconcile"):
            projection._verify_scene(plan, changed)
        projection.verify_projection_artifacts(request, self.source, result)

if __name__ == "__main__":
    unittest.main()
