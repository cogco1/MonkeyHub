"""Native identities stay source-bound, paged and read-only across a restart."""

import base64
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application import artifacts
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest


class ModelSourceIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-source-index-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.ids = [str(UUID(int=i + 1)) for i in range(5)]
        self.data = (Path(__file__).parent / "fixtures/native-source-index.3dm").read_bytes()
        self.digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)
        response = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "stateDigest": self.digest,
            "fileName": "facade.3dm", "contentBase64": base64.b64encode(self.data).decode(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.source = response.json()["modelSource"]
        self.path = f"/api/model-assets/{self.source['assetSha256']}/index"
        self.params = {"runId": REFERENCE_RUN_ID, "stateDigest": self.digest}

    def test_pages_and_exact_guid_selection_do_not_infer_roles_or_write_project(self):
        before = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        first = self.client.get(self.path, params={**self.params, "limit": 2})
        self.assertEqual(first.status_code, 200, first.text)
        first = first.json()
        self.assertEqual(first["modelSource"], self.source)
        self.assertEqual(first["units"]["name"], "Millimeters")
        self.assertEqual((first["objectCount"], first["matchedCount"], first["nextOffset"]), (5, 5, 2))
        self.assertEqual([row["object_id"] for row in first["objects"]], self.ids[:2])
        self.assertEqual([row["name"] for row in first["objects"]], ["Window", "Window"])
        for row in first["objects"]:
            self.assertNotIn("role", row)
            self.assertNotIn("geometry_sha256", row)
            self.assertEqual(row["layer"]["full_path"], "East facade")
        second = self.client.get(self.path, params={**self.params, "limit": 2, "offset": 2}).json()
        self.assertEqual([row["object_id"] for row in second["objects"]], self.ids[2:4])
        selected = self.client.get(self.path, params={**self.params, "objectId": self.ids[1]}).json()
        self.assertEqual([row["object_id"] for row in selected["objects"]], [self.ids[1]])
        self.assertEqual(selected["matchedCount"], 1)
        self.assertIsNone(selected["nextOffset"])
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(restarted.get(self.path, params={**self.params, "objectId": self.ids[1]}).json(), selected)
        after = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_wrong_state_and_unknown_guids_are_not_approximately_matched(self):
        response = self.client.get(self.path, params={**self.params, "stateDigest": "0" * 64})
        self.assertEqual(response.status_code, 409, response.text)
        response = self.client.get(self.path, params={**self.params, "objectId": str(UUID(int=99))})
        self.assertEqual(response.status_code, 404, response.text)
        for params in ({"limit": 201}, {"offset": -1}, {"objectId": "Window"}):
            self.assertEqual(self.client.get(self.path, params={**self.params, **params}).status_code, 422)

    def test_source_replacement_between_resolution_and_read_is_refused(self):
        binding = bound_project(self.app.state)
        model_source = artifacts.ModelSource.from_dict(self.source)
        retained = artifacts.require_model_source(binding, model_source)
        retained.path.write_bytes(b"not the exact retained model")
        with patch.object(artifacts, "require_model_source", return_value=retained):
            response = self.client.get(self.path, params=self.params)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "MODEL_SOURCE_MISMATCH")

    def test_source_index_does_not_run_full_geometry_inspection(self):
        with patch("archflow.adapters.three_dm_inspector._encoded_geometry_sha256", side_effect=AssertionError("full inspection")):
            response = self.client.get(self.path, params={**self.params, "objectId": self.ids[1]})
        self.assertEqual(response.status_code, 200, response.text)
