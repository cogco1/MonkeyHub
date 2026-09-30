"""P036 runner integration and two source revisions through real Blender."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from archflow.adapters import cad_backend, cad_execution as cad
from archflow.adapters.occt_backend import occt_available
BLENDER = os.environ.get("ARCHFLOW_BLENDER_EXECUTABLE")

@unittest.skipUnless(occt_available(), "cadquery-ocp is optional")
@unittest.skipUnless(BLENDER, "set ARCHFLOW_BLENDER_EXECUTABLE for runner/render acceptance")
class ProjectionRunnerTests(unittest.TestCase):
    def test_architectural_model_revision_rebuild_and_reopen(self):
        from tests.test_project_runner import _record, _options, _stage_guard, _seats
        from archflow.project.repository import FilesystemProjectRepository
        from archflow.project.refs import record_ref_from_uri
        from archflow.state.stage_workflow import DesignPhase
        from monkeyarch.runtime.project_runner import run_project
        from archflow.project.layout import cad_workspace_path

        demo = os.environ.get("ARCHFLOW_PROJECTION_DEMO_ROOT")
        if demo:
            root = Path(demo).resolve() / "demo"
            self.assertFalse(root.exists(), "choose a new demo directory")
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name) / "demo"
        repository = FilesystemProjectRepository.initialize(root, project_id="demo", initial_state={"schema": "TestState@1"})
        original_head = repository.read_head()
        records = []
        for run_id, opening_along in (("before", 6.0), ("after", 6.1)):
            run = repository.create_run(run_id)
            options = _options(export=True, workspace_root=repository.layout.run(run_id).workspaces,
                               blender_projection={"blender_executable": BLENDER,
                                                   "presentation": {"resolution": 384, "samples": 8, "azimuth": -65.0}})
            record = _record(opening_along=opening_along)
            seats = _seats(DesignPhase.DESIGN_DEVELOPMENT)
            for seat in seats:
                if not seat.reviewer:
                    cad_workspace_path(options.workspace_root, "stage-0-test-production-" + seat.seat_id).mkdir(parents=True, exist_ok=True)
            guard = _stage_guard(repository, run, record, options)
            with patch.object(cad_backend.RhinoBackend, "execute", side_effect=AssertionError("Rhino must not run")), \
                    patch.object(cad_backend.BlenderBackend, "execute", side_effect=AssertionError("Blender must not model")):
                result = run_project(repository, run=run, stage_guard=guard, record=record, seats=seats, options=options)
            self.assertTrue(result["seat_execution_complete"], result["seat_results"])
            repository = FilesystemProjectRepository.open(root)
            by_seat = {}
            for seat in result["seat_results"]:
                if not seat.get("cad"):
                    continue
                evidence = seat["cad"]["projection"]
                self.assertEqual(evidence["status"], "succeeded", evidence["failures"])
                retained = repository.load_json(record_ref_from_uri(evidence["receipt_ref"], "demo"))
                self.assertEqual(retained["binding"]["run_id"], run_id)
                self.assertEqual(retained["source_execution_ref"], seat["cad"]["execution_ref"])
                by_seat[seat["seat_id"]] = retained
                workspace = cad_workspace_path(options.workspace_root, retained["binding"]["stage_id"])
                for artifact in retained["artifacts"]:
                    cad._require_file_digest(workspace / artifact["relative_path"], artifact["sha256"], "retained render artifact")
            records.append(by_seat)
            self.assertEqual(repository.read_head(), original_head)
        before, after = (r["seat-envelope"] for r in records)
        self.assertNotEqual(before["binding"]["design_state_digest"], after["binding"]["design_state_digest"])
        self.assertNotEqual(before["source_artifact"]["sha256"], after["source_artifact"]["sha256"])
        self.assertEqual(before["presentation"], after["presentation"])
        self.assertEqual([r["object_id"] for r in before["readback"]["objects"]],
                         [r["object_id"] for r in after["readback"]["objects"]])
        self.assertNotEqual(before["readback"]["objects"], after["readback"]["objects"])
        # The unaffected column geometry remains identical across source revisions.
        for first, second in zip(records[0]["seat-structure"]["readback"]["objects"], records[1]["seat-structure"]["readback"]["objects"]):
            for key in ("object_id", "vertices", "faces", "bounds", "material"):
                self.assertEqual(first[key], second[key], key)
        print("Verified architectural projection demo:", root)
