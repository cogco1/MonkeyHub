"""Real OCCT -> saved/reopened Blender -> PNG, with source-bound failures."""
import os
import json
import math
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


class CapturedCameraValidationTests(unittest.TestCase):
    def test_invalid_frames_and_inconsistent_projections_are_refused(self):
        values = dict(position=(2, -5, 3), target=(2, 0, 3), up=(0, 0, 1),
                      projection="perspective", near=.1, far=100, aspect=1.5, vertical_fov=60)
        for change in ({"up": (0, 1, 0)}, {"near": 0}, {"far": .01},
                       {"position": (float("nan"), 0, 0)}, {"vertical_fov": 180},
                       {"projection": "unknown"}, {"orthographic_bounds": (-1, 1, 1, -1)}):
            with self.subTest(change=change), self.assertRaises(cad.CadExecutionError):
                projection.BlenderCamera(**(values | change))
        with self.assertRaisesRegex(cad.CadExecutionError, "aspect"):
            projection.BlenderCamera(**(values | dict(projection="orthographic", vertical_fov=None,
                orthographic_bounds=(-1, 1, 1, -1))))

    def test_snapshot_is_immutable_and_legacy_settings_keep_their_shape(self):
        position = [2, -5, 3]
        camera = projection.BlenderCamera(position, [2, 0, 3], [0, 0, 1],
                                          "perspective", .1, 100, 1.5, 60)
        position[0] = 999
        self.assertEqual(camera.position, (2, -5, 3))
        settings = projection.BlenderPresentation(camera=camera, resolution=128)
        self.assertEqual(settings.image_size, (128, 85))
        retained = settings.to_dict()
        restored = projection.BlenderPresentation(**{
            key: value for key, value in retained.items() if key in projection.BlenderPresentation.__dataclass_fields__})
        self.assertEqual(restored.to_dict(), retained)
        self.assertNotIn("camera", projection.BlenderPresentation().to_dict())


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
    def test_captured_camera_projects_same_points_after_saved_scene_reopen(self):
        for kind, aspect in (("perspective", 1.7), ("orthographic", .75)):
            with self.subTest(projection=kind):
                camera = projection.BlenderCamera(
                    (2, -5, 3), (2, 0, 3), (1, 0, 1), kind, .1, 100, aspect,
                    vertical_fov=60 if kind == "perspective" else None,
                    orthographic_bounds=(-1, 2, 3, -1) if kind == "orthographic" else None)
                settings = projection.BlenderPresentation(camera=camera, resolution=64, samples=1)
                request = replace(self.request, artifact_stem=kind)
                result = projection.execute_blender_projection(
                    request, self.source, blender_executable=BLENDER, presentation=settings)
                self.assertEqual(result["status"], "succeeded", result["failures"])
                projection.verify_projection_artifacts(request, self.source, result)
                tampered = deepcopy(result)
                tampered["readback"]["visual_state"]["captured_camera"]["projection_matrix"][0][0] *= 2
                with self.assertRaisesRegex(cad.CadExecutionError, "projection differs"):
                    projection.verify_projection_artifacts(request, self.source, tampered)
                self.assertEqual(result["readback"]["visual_state"]["resolution"][:2], list(settings.image_size))
                # World point = camera position + forward*5 + right*1 + corrected-up*.5.
                # The nonstandard up vector makes this verify roll as well as field of view.
                k = math.sqrt(.5)
                point = (2 + 1.5 * k, 0, 3 - .5 * k)
                scene = self.workspace / result["artifacts"][0]["relative_path"]
                expression = ("import bpy,json; from mathutils import Vector; "
                    "from bpy_extras.object_utils import world_to_camera_view; "
                    f"bpy.ops.wm.open_mainfile(filepath={str(scene)!r},use_scripts=False); "
                    "bpy.context.view_layer.update(); "
                    f"p=world_to_camera_view(bpy.context.scene,bpy.context.scene.camera,Vector({point!r})); "
                    "print('POINT:'+json.dumps(list(p)))")
                output = subprocess.run([BLENDER, "--background", "--factory-startup", "--disable-autoexec",
                    "--python-exit-code", "1", "--python-expr", expression], capture_output=True,
                    text=True, check=True, timeout=120,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                actual = json.loads(next(row[6:] for row in output.stdout.splitlines() if row.startswith("POINT:")))
                expected = ((.5 + 1 / (10 * math.tan(math.pi / 6) * aspect),
                             .5 + .5 / (10 * math.tan(math.pi / 6))) if kind == "perspective" else
                            ((1 + 1) / 3, (.5 + 1) / 4))
                for a, b in zip(actual, (*expected, 5)):
                    self.assertAlmostEqual(a, b, places=5)
                # Stored camera pose and clipping remain in the original model unit.
                visual = result["readback"]["visual_state"]
                self.assertEqual(visual["camera_position"], [2, -5, 3])
                self.assertAlmostEqual(visual["clip"][0], .1, places=6)
                self.assertEqual(visual["clip"][1], 100)

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
