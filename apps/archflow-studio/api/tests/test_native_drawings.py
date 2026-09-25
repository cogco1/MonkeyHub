"""Imported model -> retained drawing, without a semantic state or an intermediate STEP."""
import base64
import hashlib
import importlib.util
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.adapters.model_formats import ThreeDM
from archflow.adapters.three_dm_inspector import inspect_three_dm_contents, inspect_three_dm_index
from archflow.project.refs import record_ref_from_uri
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_MODEL_ASSET
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest


class NativeDrawingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.head = self.repository.read_head()

    def upload(self, *, state=False):
        self.data = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        body = {"projectId": PROJECT_ID, "fileName": "wall.3dm", "contentBase64": base64.b64encode(self.data).decode()}
        if state:
            body.update(runId=REFERENCE_RUN_ID, stateDigest=runner_state_digest(self.repository, REFERENCE_RUN_ID))
        response = self.client.post("/api/model-assets", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def generate(self, asset, **extra):
        body = {"projectId": PROJECT_ID, "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50}
        if asset["modelSource"]:
            body["modelSource"] = asset["modelSource"]
        else:
            body["sourceAsset"] = {"runId": asset["runId"], "assetSha256": asset["sha256"]}
        response = self.client.post("/api/drawings/plans", json=body | extra)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_external_plan_reopens_without_inventing_state_or_step(self):
        asset = self.upload()
        drawing = self.generate(asset)
        source = {"runId": asset["runId"], "assetSha256": asset["sha256"]}
        self.assertIsNone(drawing["modelSource"])
        self.assertEqual(drawing["viewRecipe"]["sourceAsset"], source)
        self.assertEqual(drawing["viewRecipe"]["follow"], "frozen")
        receipt = self.repository.load_json(record_ref_from_uri(drawing["revisionRef"], PROJECT_ID))
        self.assertEqual(receipt["source"]["model"]["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertNotIn("step", receipt["source"])
        self.assertGreater(receipt["projection"]["section_regions"], 0)
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertFalse(list((self.root / PROJECT_ID).rglob("*.step")))
        self.client.close()
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        ref = {"runId": drawing["runId"], "assetSha256": drawing["assetSha256"], "revisionRef": drawing["revisionRef"]}
        status = self.client.post("/api/drawings/plans/status", json=ref)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "current")
        self.assertIsNone(status.json()["targetModelSource"])
        vector = self.client.get("/api/drawings/plans/vector", params=ref)
        self.assertEqual(vector.status_code, 200, vector.text)
        self.assertIn("<svg", vector.json()["svg"])
        # An imported model carries no semantics: its projected lines name their object and nothing more.
        self.assertIn("data-object=", vector.json()["svg"])
        self.assertNotIn("data-component", vector.json()["svg"])
        self.assertNotIn("data-material", vector.json()["svg"])
        updated = self.generate(asset, previousRevisionRef=drawing["revisionRef"], hatchSpacingMm=3)
        self.assertEqual(updated["drawingId"], drawing["drawingId"])
        self.assertNotEqual(updated["revisionRef"], drawing["revisionRef"])
        dimensions = self.client.get("/api/drawings/plans/dimensions", params={"sourceAssetRunId": asset["runId"], "sourceAssetSha256": asset["sha256"]})
        self.assertEqual(dimensions.json(), {"lengthUnit": "meter", "dimensions": []})

    def test_composed_3dm_draws_its_own_geometry(self):
        asset = self.upload(state=True)
        drawing = self.generate(asset)
        self.assertEqual(drawing["modelSource"], asset["modelSource"])
        self.assertEqual(self.repository.read_head(), self.head)
        status = self.client.post("/api/drawings/plans/status", json={"runId": drawing["runId"], "assetSha256": drawing["assetSha256"],
            "revisionRef": drawing["revisionRef"], "targetModelSource": asset["modelSource"]})
        self.assertEqual(status.json()["status"], "current", status.text)

    def test_legacy_external_registration_draws_with_its_retained_binding(self):
        data = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        run = self.repository.create_run("studio-model-" + digest)
        artifact = self.repository.ingest(run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id="external-model-" + digest, media_type="model/vnd.rhino", source=BytesIO(data))
        self.repository.put_json(run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STUDIO_MODEL_ASSET, payload={
                "schema": "StudioExternalModelAsset@1", "projectId": PROJECT_ID,
                "runId": run.run_id, "assetSha256": digest, "artifact": asdict(artifact),
                "fileName": "legacy.3dm", "sizeBytes": len(data),
                "objectCount": inspect_three_dm_contents(data).object_count, "lengthUnit": "meter",
            })
        listing = self.client.get("/api/artifacts", params={"runId": run.run_id})
        self.assertEqual(listing.status_code, 200, listing.text)
        asset = listing.json()["artifacts"][0]
        self.assertTrue(asset["available"])
        drawing = self.generate(asset)
        receipt = self.repository.load_json(record_ref_from_uri(drawing["revisionRef"], PROJECT_ID))
        self.assertEqual(receipt["source"]["model"]["sha256"], digest)
        self.assertIsNone(drawing["modelSource"])
        self.assertEqual(drawing["viewRecipe"]["sourceAsset"], {"runId": run.run_id, "assetSha256": digest})
        self.assertEqual(drawing["viewRecipe"]["follow"], "frozen")
        self.assertEqual(self.repository.read_head(), self.head)

    def test_external_rebuild_reports_removed_hidden_object(self):
        data = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        hidden_id = inspect_three_dm_index(data)["objects"][0]["object_id"]
        adapter = ThreeDM()
        revised = adapter.read(data)
        revised.meshes = [mesh for mesh in revised.meshes if mesh.source_object_id != hidden_id]
        self.assertEqual(len(revised.meshes), 1)

        def upload(content):
            response = self.client.post("/api/model-assets", json={"projectId": PROJECT_ID,
                "fileName": "revised.3dm", "contentBase64": base64.b64encode(content).decode()})
            self.assertEqual(response.status_code, 201, response.text)
            return response.json()

        first = self.generate(upload(data), hiddenObjectIds=[hidden_id])
        updated = self.generate(upload(adapter.write(revised)), previousRevisionRef=first["revisionRef"])
        ref = {key: updated[key] for key in ("runId", "assetSha256", "revisionRef")}
        receipt = self.repository.load_json(record_ref_from_uri(updated["revisionRef"], PROJECT_ID))
        self.assertEqual(receipt["projection"]["unresolvedObjectIds"], [hidden_id])
        self.client.close()
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        status = self.client.post("/api/drawings/plans/status", json=ref)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "partially-broken")
        self.assertEqual(status.json()["unresolvedObjectIds"], [hidden_id])

    def test_elevation_sheet_and_tampered_native_source(self):
        asset = self.upload()
        source = {"runId": asset["runId"], "assetSha256": asset["sha256"]}
        for route, options in (("elevations", {"view": "front"}), ("sheets", {"styleId": "arch400-white", "scaleDenominator": 50})):
            body = {"projectId": PROJECT_ID, "sourceAsset": source} | options
            first = self.client.post("/api/drawings/" + route, json=body)
            self.assertEqual(first.status_code, 201, first.text)
            self.assertIsNone(first.json()["modelSource"])
            repeated = self.client.post("/api/drawings/" + route, json=body)
            self.assertEqual(repeated.status_code, 201, repeated.text)
            self.assertEqual(first.json()["assetSha256"], repeated.json()["assetSha256"])
        registration = self.repository.load_json(record_ref_from_uri(asset["receiptRef"], PROJECT_ID))
        self.repository.layout.resolve_relative(registration["artifact"]["relative_path"]).write_bytes(b"tampered")
        failed = self.client.post("/api/drawings/plans", json={"projectId": PROJECT_ID, "sourceAsset": source})
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_no_asset_can_borrow_another_run_or_design_state(self):
        asset = self.upload()
        bad = self.client.post("/api/drawings/plans", json={"projectId": PROJECT_ID,
            "sourceAsset": {"runId": REFERENCE_RUN_ID, "assetSha256": asset["sha256"]}})
        self.assertIn(bad.status_code, (404, 409), bad.text)
        mixed = self.client.post("/api/drawings/plans", json={"projectId": PROJECT_ID,
            "sourceAsset": {"runId": asset["runId"], "assetSha256": asset["sha256"]}, "sourceStageRef": "fake"})
        self.assertEqual(mixed.status_code, 422)

    def assert_section_perspective_retains_source(self, asset, section):
        source = {"runId": asset["runId"], "assetSha256": asset["sha256"]}
        body = {"projectId": PROJECT_ID, "sourceAsset": source,
                "section": section, "scaleDenominator": 100}
        response = self.client.post("/api/drawings/section-perspectives", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        document = response.json()
        self.assertIsNone(document["modelSource"])
        self.assertIsNone(document["sourceStageRef"])
        self.assertEqual(document["viewRecipe"]["sourceAsset"], source)
        self.assertEqual(document["viewRecipe"]["follow"], "frozen")
        self.assertEqual(document["viewRecipe"]["kind"], "section-perspective")
        receipt = self.repository.load_json(record_ref_from_uri(document["revisionRef"], PROJECT_ID))
        self.assertEqual(receipt["source"]["model"]["sha256"], asset["sha256"])
        self.assertEqual(receipt["view"], document["viewRecipe"])
        self.assertNotIn("step", receipt["source"])
        if asset.get("sourceImport"):
            self.assertEqual(receipt["source"]["sourceImport"], asset["sourceImport"])
        with TestClient(create_app(self.settings)) as reopened:
            repeated = reopened.post("/api/drawings/section-perspectives", json=body)
            self.assertEqual(repeated.status_code, 201, repeated.text)
            self.assertEqual(repeated.json(), document)
            listed = reopened.get("/api/documents", params={"runId": asset["runId"]})
            self.assertIn(document, listed.json()["documents"])
            image = reopened.get(f"/api/documents/{document['assetSha256']}/bytes", params={
                "runId": document["runId"], "revisionRef": document["revisionRef"]})
            self.assertEqual(image.status_code, 200, image.text[:200] if image.status_code != 200 else "")
            self.assertEqual(hashlib.sha256(image.content).hexdigest(), document["assetSha256"])
        self.assertFalse(list((self.root / PROJECT_ID).rglob("*.step")))
        self.assertEqual(self.repository.read_head(), self.head)

    def test_external_three_dm_section_perspective_is_frozen_and_reused(self):
        asset = self.upload()
        self.assert_section_perspective_retains_source(asset, {"line": [[0, 1], [9, 1]], "keep": "right"})
        ambiguous = self.client.post("/api/drawings/section-perspectives", json={
            "projectId": PROJECT_ID, "sourceAsset": {"runId": asset["runId"], "assetSha256": asset["sha256"]},
            "sourceStageRef": "fake", "section": {"line": [[0, 1], [9, 1]], "keep": "right"}})
        self.assertEqual(ambiguous.status_code, 422)

    def test_real_skp_upload_keeps_original_and_draws_without_step(self):
        from archflow.adapters import sketchup_reader
        from archflow.adapters.model_formats import ConversionError
        try:
            sdk = sketchup_reader._sdk_location(None)
        except ConversionError as exc:
            self.skipTest(str(exc))
        # Reuse the adapter's native SDK fixture builder, not private design data.
        spec = importlib.util.spec_from_file_location("skp_test_fixture", Path(__file__).resolve().parents[4] / "tests/test_sketchup_reader.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        path = self.root / "source.skp"
        with sketchup_reader._open_sdk(sdk) as api:
            helper._write_native_fixture(api, path)
        original = path.read_bytes()
        body = {"projectId": PROJECT_ID, "fileName": "source.skp", "contentBase64": base64.b64encode(original).decode()}
        result = self.client.post("/api/model-assets", json=body)
        self.assertEqual(result.status_code, 201, result.text)
        asset = result.json()
        self.assertEqual(asset["format"], "3dm")
        self.assertEqual(asset["representation"], "external")
        registration = self.repository.load_json(record_ref_from_uri(asset["receiptRef"], PROJECT_ID))
        source_import = {key: registration[key] for key in ("sourceArtifact", "sourceFileName", "conversion")}
        self.assertEqual(asset["sourceImport"], source_import)
        self.assertEqual(asset["sourceImport"]["sourceFileName"], "source.skp")
        self.assertTrue(asset["sourceImport"]["conversion"]["warnings"])
        listing = self.client.get("/api/artifacts", params={"runId": asset["runId"]})
        self.assertEqual(listing.status_code, 200, listing.text)
        listed = next(row for row in listing.json()["artifacts"] if row["sha256"] == asset["sha256"])
        self.assertEqual(listed["sourceImport"], source_import)
        retained = self.repository.layout.resolve_relative(registration["sourceArtifact"]["relative_path"]).read_bytes()
        self.assertEqual(retained, original)
        with patch.object(sketchup_reader, "read_skp", side_effect=AssertionError("retry must reuse original")):
            again = self.client.post("/api/model-assets", json=body)
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.json()["sha256"], asset["sha256"])
        drawing = self.generate(asset, cutHeight=10, scaleDenominator=100)
        receipt = self.repository.load_json(record_ref_from_uri(drawing["revisionRef"], PROJECT_ID))
        self.assertTrue(receipt["source"]["geometry_quality"])
        self.assertEqual(set(receipt["source"]["geometry_quality"].values()), {"faceted"})
        self.assertEqual(receipt["source"]["sourceImport"],
                         {key: registration[key] for key in ("sourceArtifact", "sourceFileName", "conversion")})
        self.assertTrue(receipt["source"]["sourceImport"]["conversion"]["warnings"])
        self.assert_section_perspective_retains_source(asset, {
            "line": [[-1.143, .127], [2.159, 4.826]], "keep": "right"})
        self.assertFalse(list((self.root / PROJECT_ID).rglob("*.step")))
        self.assertEqual(self.repository.read_head(), self.head)

    def test_missing_skp_runtime_does_not_register_a_fake_model(self):
        from archflow.adapters.model_formats import ConversionError
        with patch("archflow.adapters.sketchup_reader.read_skp", side_effect=ConversionError("SKP runtime unavailable")):
            result = self.client.post("/api/model-assets", json={"projectId": PROJECT_ID, "fileName": "source.skp",
                "contentBase64": base64.b64encode(b"source").decode()})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json()["code"], "MODEL_ASSET_SKP_UNAVAILABLE")
        self.assertEqual(self.repository.read_head(), self.head)
