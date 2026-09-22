"""Real mesh -> queued Blender job -> retained PNG -> fresh-runtime readback."""
import base64
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import make_project, PROJECT_ID




class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo, _ = make_project(root)
        self.settings = StudioSettings(project_dir=root / PROJECT_ID, cad_export="off")
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        self.data = (Path(__file__).resolve().parents[4] / "tests/fixtures/render-mesh.3dm").read_bytes()
        self.payload = {"projectId": PROJECT_ID, "requestId": str(uuid4()), "fileName": "mesh.3dm",
            "contentBase64": base64.b64encode(self.data).decode(), "resolution": 64, "samples": 1}

    def test_invalid_input_or_missing_blender_does_not_change_project(self):
        original = self.repo.read_head()
        result = self.client.post("/api/render/jobs", json={**self.payload, "projectId": "other"})
        self.assertEqual(result.status_code, 403)
        result = self.client.post("/api/render/jobs", json={**self.payload, "contentBase64": "bad"})
        self.assertEqual(result.status_code, 422)
        with patch.dict(os.environ, {"ARCHFLOW_BLENDER_EXECUTABLE": "missing-render-host"}):
            result = self.client.post("/api/render/jobs", json=self.payload)
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(self.client.get("/api/render/jobs").json()["jobs"], [])
        self.assertEqual(self.repo.read_head(), original)

    @unittest.skipUnless(os.environ.get("ARCHFLOW_BLENDER_EXECUTABLE"), "requires real Blender")
    def test_real_job_persists_image_and_exact_source_across_restart(self):
        original = self.repo.read_head()
        response = self.client.post("/api/render/jobs", json=self.payload)
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["jobId"]
        repeat = self.client.post("/api/render/jobs", json=self.payload)
        self.assertEqual(repeat.json()["jobId"], job_id)
        conflict = self.client.post("/api/render/jobs", json={**self.payload, "samples": 2})
        self.assertEqual(conflict.status_code, 409)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = self.client.get("/api/render/jobs/" + job_id).json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(.1)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["sourceSha256"], hashlib.sha256(self.data).hexdigest())
        doc = job["document"]
        self.assertEqual(doc["viewRecipe"]["kind"], "render")
        self.assertIsNone(doc["modelSource"])
        png = self.client.get("/api/documents/" + doc["assetSha256"] + "/bytes", params={"runId": job_id})
        self.assertEqual(png.status_code, 200)
        self.assertEqual(hashlib.sha256(png.content).hexdigest(), doc["assetSha256"])
        self.assertTrue(png.content.startswith(b"\x89PNG"))
        with TestClient(create_app(self.settings)) as fresh:
            restored = fresh.get("/api/render/jobs/" + job_id).json()
            self.assertEqual(restored["status"], "succeeded")
            self.assertEqual(restored["document"], doc)
        self.assertEqual(self.repo.read_head(), original)
