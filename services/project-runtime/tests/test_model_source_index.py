"""Native identities stay source-bound, paged and read-only across a restart."""

import base64
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from project_runtime.binding import bound_project
from project_runtime.application import artifacts
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
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
        with patch("monkeycad.formats.three_dm_inspector._encoded_geometry_sha256", side_effect=AssertionError("full inspection")):
            response = self.client.get(self.path, params={**self.params, "objectId": self.ids[1]})
        self.assertEqual(response.status_code, 200, response.text)


class AttachedModelIndexTests(unittest.TestCase):
    """A model retained from outside the project (no design state) reads through the same index."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="attached-model-index-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.data = (Path(__file__).parent / "fixtures/native-source-index.3dm").read_bytes()
        response = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "fileName": "returned.3dm", "contentBase64": base64.b64encode(self.data).decode(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.run = f"studio-model-{self.sha}"
        self.assertEqual(response.json()["runId"], self.run)
        self.path = f"/api/model-assets/{self.sha}/index"

    def test_an_attachment_reads_with_its_run_alone_and_bounds_on_request(self):
        plain = self.client.get(self.path, params={"runId": self.run})
        self.assertEqual(plain.status_code, 200, plain.text)
        plain = plain.json()
        self.assertEqual((plain["runId"], plain["assetSha256"], plain["modelSource"]), (self.run, self.sha, None))
        self.assertEqual((plain["objectCount"], plain["fileName"]), (5, "returned.3dm"))
        self.assertTrue(all("bounds" not in row for row in plain["objects"]))
        bounded = self.client.get(self.path, params={"runId": self.run, "bounds": "true"})
        self.assertEqual(bounded.status_code, 200, bounded.text)
        positions = sorted((row["name"], row["bounds"]["min"], row["bounds"]["max"], row["bounds_space"])
                           for row in bounded.json()["objects"])
        self.assertEqual(positions, sorted([
            ("Window", [0.0, 0.0, 3000.0], [0.0, 0.0, 3000.0], "world"),
            ("Window", [1000.0, 0.0, 3000.0], [1000.0, 0.0, 3000.0], "world"),
            ("", [2000.0, 0.0, 3000.0], [2000.0, 0.0, 3000.0], "world"),
            ("Roof", [3000.0, 0.0, 3000.0], [3000.0, 0.0, 3000.0], "world"),
            ("Ambiguous", [4000.0, 0.0, 3000.0], [4000.0, 0.0, 3000.0], "world"),
        ]))
        self.assertEqual(bounded.json()["units"]["name"], "Millimeters")

    def test_state_digest_belongs_to_a_design_run_only(self):
        with_state = self.client.get(self.path, params={"runId": self.run, "stateDigest": "0" * 64})
        self.assertEqual(with_state.status_code, 422, with_state.text)
        self.assertEqual(with_state.json()["code"], "MODEL_SOURCE_INVALID")
        design = self.client.get(self.path, params={"runId": REFERENCE_RUN_ID})
        self.assertEqual(design.status_code, 422, design.text)
        self.assertEqual(design.json()["code"], "MODEL_SOURCE_INVALID")
        other = "a" * 64
        missing = self.client.get(f"/api/model-assets/{other}/index", params={"runId": f"studio-model-{other}"})
        self.assertEqual(missing.status_code, 404, missing.text)
        mismatched = self.client.get(f"/api/model-assets/{other}/index", params={"runId": self.run})
        self.assertEqual(mismatched.status_code, 422, mismatched.text)

    def test_the_construction_model_refuses_a_run_that_holds_only_an_attachment(self):
        response = self.client.get("/api/construction/model", params={"run": self.run})
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["code"], "REFERENCE_STATE_NOT_EXACT")
        self.assertIn(f"/api/model-assets/{self.sha}/index?runId={self.run}", body["detail"])
        # The project's own model is still read where no run is named.
        self.assertEqual(self.client.get("/api/construction/model").status_code, 200)


class KeptModelIndexTests(unittest.TestCase):
    """A model's index is read once per content and kept; every later page is a lookup (#300)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kept-model-index-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        make_project(self.root)
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID,
                                             cad_export="off", cache_dir=self.root / "cache"))
        self.addCleanup(self.app.state.stop_index_events)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        binding = bound_project(self.app.state)
        self.addCleanup(binding.close)
        self.assertIsNotNone(binding.await_index(30), "the index loads")
        self.addCleanup(lambda: self.app.state.projections and self.app.state.projections.shutdown())
        artifacts._KEPT_INDEXES.clear()
        self.addCleanup(artifacts._KEPT_INDEXES.clear)
        self.data = (Path(__file__).parent / "fixtures/native-source-index.3dm").read_bytes()
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.run = f"studio-model-{self.sha}"
        self.path = f"/api/model-assets/{self.sha}/index"

    def read(self, **params):
        response = self.client.get(self.path, params={"runId": self.run, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_model_is_decoded_once_and_every_page_reads_the_kept_index(self):
        with patch.object(artifacts, "inspect_three_dm_index", wraps=artifacts.inspect_three_dm_index) as decoded:
            registered = self.client.post("/api/model-assets", json={
                "projectId": PROJECT_ID, "fileName": "returned.3dm", "contentBase64": base64.b64encode(self.data).decode()})
            self.assertEqual(registered.status_code, 201, registered.text)
            plain = self.read()
            bounded = self.read(bounds="true")
            window = next(row["object_id"] for row in plain["objects"] if row["name"] == "Roof")
            chosen = self.read(objectId=window, bounds="true")
        self.assertEqual(decoded.call_count, 1, "the model is decoded once, whoever asks first")
        self.assertEqual(plain["objectCount"], 5)
        self.assertTrue(all("bounds" not in row for row in plain["objects"]))
        self.assertEqual(sorted(row["bounds"]["min"] for row in bounded["objects"]),
                         [[0.0, 0.0, 3000.0], [1000.0, 0.0, 3000.0], [2000.0, 0.0, 3000.0], [3000.0, 0.0, 3000.0],
                          [4000.0, 0.0, 3000.0]])
        self.assertEqual([(row["name"], row["bounds"]["min"]) for row in chosen["objects"]], [("Roof", [3000.0, 0.0, 3000.0])])

        artifacts._KEPT_INDEXES.clear()  # a restarted process: the projection cache keeps the index
        with patch.object(artifacts, "inspect_three_dm_index", wraps=artifacts.inspect_three_dm_index) as again:
            self.assertEqual(self.read(), plain)
        self.assertEqual(again.call_count, 0)
