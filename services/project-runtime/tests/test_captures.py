"""Studio viewport PNGs stay in the explicitly named run workspace."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project
from .support import runner_state_digest, retain_runner_receipt
from .test_working_copies import register_model


# A complete 2 x 2 RGBA PNG, not only a file signature.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR4nGNc"
    "d+bWfwYGBgYmEAHCADM8A1fAhwmJAAAAAElFTkSuQmCC"
)


def request_payload(run_id: str, data: bytes = PNG_BYTES) -> dict[str, str]:
    return {
        "runId": run_id,
        "pngBase64": base64.b64encode(data).decode("ascii"),
    }


class ViewportCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_capture_lands_in_the_named_run_workspace_and_does_not_change_head(
        self,
    ) -> None:
        before = self.repository.read_head()
        digest = hashlib.sha256(PNG_BYTES).hexdigest()

        response = self.client.post(
            "/api/captures",
            json=request_payload(REFERENCE_RUN_ID),
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        relative = (
            f"runs/{REFERENCE_RUN_ID}/workspaces/studio-captures/"
            f"viewport-{digest}.png"
        )
        self.assertEqual(
            body,
            {
                "projectId": PROJECT_ID,
                "runId": REFERENCE_RUN_ID,
                "relativePath": relative,
                "sha256": digest,
                "mediaType": "image/png",
                "sizeBytes": len(PNG_BYTES),
            },
        )
        self.assertEqual(
            self.repository.layout.resolve_relative(relative).read_bytes(),
            PNG_BYTES,
        )
        self.assertEqual(self.repository.read_head(), before)
        self.assertEqual(self.client.get("/api/artifacts").json()["artifacts"], [])
        with Image.open(BytesIO(PNG_BYTES)) as capture:
            capture.load()
            self.assertEqual(capture.size, (2, 2))
            self.assertEqual(capture.getpixel((0, 0)), (174, 204, 218, 255))

    def test_the_request_run_not_the_reference_rule_owns_the_capture(self) -> None:
        other = self.repository.create_run("loaded-model-run")

        response = self.client.post(
            "/api/captures",
            json=request_payload(other.run_id),
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["runId"], other.run_id)
        self.assertTrue(
            response.json()["relativePath"].startswith(
                f"runs/{other.run_id}/workspaces/studio-captures/"
            )
        )
        self.assertFalse(
            (
                self.repository.layout.run(REFERENCE_RUN_ID).workspaces
                / "studio-captures"
            ).exists()
        )

    def test_repeating_the_same_capture_is_idempotent(self) -> None:
        first = self.client.post(
            "/api/captures",
            json=request_payload(REFERENCE_RUN_ID),
        )
        second = self.client.post(
            "/api/captures",
            json=request_payload(REFERENCE_RUN_ID),
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        capture_dir = (
            self.repository.layout.run(REFERENCE_RUN_ID).workspaces
            / "studio-captures"
        )
        self.assertEqual(list(capture_dir.glob("*.png")), [
            self.repository.layout.resolve_relative(first.json()["relativePath"])
        ])

    def test_a_nonexistent_run_is_refused_without_falling_back(self) -> None:
        response = self.client.post(
            "/api/captures",
            json=request_payload("missing-run"),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "RUN_NOT_FOUND")
        self.assertFalse(
            (
                self.repository.layout.run(REFERENCE_RUN_ID).workspaces
                / "studio-captures"
            ).exists()
        )

    def test_non_png_bytes_are_refused(self) -> None:
        response = self.client.post(
            "/api/captures",
            json=request_payload(REFERENCE_RUN_ID, b"not-a-png"),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "CAPTURE_INVALID")

    def test_incomplete_or_corrupt_png_is_refused_before_persistence(self) -> None:
        damaged = bytearray(PNG_BYTES)
        damaged[50] ^= 1  # Corrupt IDAT without updating its checksum.
        before = self.repository.read_head()
        for data in (
            PNG_BYTES[:8],
            PNG_BYTES[:8] + b"viewport-pixels",
            PNG_BYTES[:45],
            PNG_BYTES[:-12],
            PNG_BYTES[:-1],
            bytes(damaged),
            # Valid chunk checksums, but an undecodable IDAT stream.
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVQAnGNc"
                "d+bWfwYGBgYmEAHCADM8A1fNuHo6AAAAAElFTkSuQmCC"
            ),
        ):
            with self.subTest(data=data):
                response = self.client.post(
                    "/api/captures",
                    json=request_payload(REFERENCE_RUN_ID, data),
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], "CAPTURE_INVALID")
        self.assertFalse(
            (
                self.repository.layout.run(REFERENCE_RUN_ID).workspaces
                / "studio-captures"
            ).exists()
        )
        self.assertEqual(self.repository.read_head(), before)


class UnboundViewportCaptureTests(unittest.TestCase):
    def test_capture_refuses_when_no_project_is_bound(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=root / "no-such-project"))
        )
        self.addCleanup(client.close)

        response = client.post(
            "/api/captures",
            json=request_payload(REFERENCE_RUN_ID),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PROJECT_NOT_BOUND")


class RetainedModelPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="retained-model-preview-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.model_bytes = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        self.source = register_model(self.client, REFERENCE_RUN_ID,
                                     runner_state_digest(self.repository, REFERENCE_RUN_ID), self.model_bytes)["modelSource"]

    def preview(self, source=None, client=None):
        source = source or self.source
        return (client or self.client).get(f"/api/model-assets/{source['assetSha256']}/preview", params={
            "runId": source["runId"], "stateDigest": source["stateDigest"]})

    def capture(self, source=None):
        source = source or self.source
        return self.client.post("/api/captures", json={**request_payload(source["runId"]), "modelSource": source})

    def test_exact_preview_is_idempotent_and_survives_project_copy_and_reopen(self):
        before = self.repository.read_head()
        self.assertIsNone(self.preview().json())
        saved = self.capture()
        self.assertEqual(saved.status_code, 201, saved.text)
        document = saved.json()["document"]
        self.assertEqual(document["modelSource"], self.source)
        self.assertEqual(document["viewRecipe"], {"kind": "viewport-preview"})
        self.assertEqual(self.capture().json(), saved.json())
        self.assertEqual(self.repository.read_head(), before)
        documents = self.client.get("/api/documents").json()["documents"]
        self.assertEqual(len(documents), 1)
        self.assertIsNone(documents[0]["drawingId"])
        self.assertIsNone(documents[0]["revisionRef"])
        self.assertEqual(self.client.get("/api/worktrees").json()["representations"], [])
        copied = self.root / "another-machine" / PROJECT_ID
        shutil.copytree(self.root / PROJECT_ID, copied)
        with TestClient(create_app(StudioSettings(cad_export="off", project_dir=copied))) as reopened:
            found = self.preview(client=reopened)
            self.assertEqual(found.status_code, 200, found.text)
            self.assertEqual(found.json(), documents[0])
            response = reopened.get(f"/api/documents/{document['assetSha256']}/bytes", params={"runId": self.source["runId"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(hashlib.sha256(response.content).hexdigest(), document["assetSha256"])
            with Image.open(BytesIO(response.content)) as image:
                self.assertEqual(image.getpixel((0, 0)), (174, 204, 218, 255))

    def test_run_state_and_asset_cannot_borrow_another_models_preview(self):
        self.assertEqual(self.capture().status_code, 201)
        other = register_model(self.client, REFERENCE_RUN_ID, self.source["stateDigest"],
                               (Path(__file__).parent / "fixtures/model-source-b.3dm").read_bytes())["modelSource"]
        self.assertIsNone(self.preview(other).json())
        other_run = self.repository.create_run("other-model-run")
        retain_runner_receipt(self.repository, other_run, design_state_digest=runner_state_digest(self.repository, other_run.run_id))
        same_bytes = register_model(self.client, other_run.run_id,
                                    runner_state_digest(self.repository, other_run.run_id), self.model_bytes)["modelSource"]
        self.assertIsNone(self.preview(same_bytes).json())
        rebound = self.capture(same_bytes)
        self.assertEqual(rebound.status_code, 201, rebound.text)
        self.assertEqual(rebound.json()["document"]["assetSha256"], self.preview().json()["assetSha256"])
        self.assertEqual(rebound.json()["document"]["modelSource"], same_bytes)
        # Identical PNG pixels in a single run can be bound to two real models.
        second = self.capture(other)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertNotEqual(self.preview().json()["assetSha256"], second.json()["document"]["assetSha256"])
        for key in ("stateDigest", "assetSha256"):
            stale = {**self.source, key: "f" * 64}
            self.assertEqual(self.preview(stale).status_code, 409)
            self.assertEqual(self.capture(stale).status_code, 409)
        mismatch = self.client.post("/api/captures", json={**request_payload(other_run.run_id), "modelSource": self.source})
        self.assertEqual(mismatch.status_code, 409)

    def test_ordinary_uploaded_image_and_plain_capture_are_not_model_previews(self):
        body = {"projectId": PROJECT_ID, "runId": self.source["runId"],
            "fileName": "viewport-fake.png", "mimeType": "image/png", "contentBase64": base64.b64encode(PNG_BYTES).decode(),
            "modelSource": self.source}
        spoofed = self.client.post("/api/documents", json={**body, "viewRecipe": {"kind": "viewport-preview"}})
        self.assertEqual(spoofed.status_code, 422)
        uploaded = self.client.post("/api/documents", json=body)
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertIsNone(self.preview().json())
        self.assertEqual(self.client.post("/api/captures", json=request_payload(self.source["runId"])).status_code, 201)
        self.assertIsNone(self.preview().json())

    def test_image_tampering_is_refused_after_reopen(self):
        document = self.capture().json()["document"]
        sha = document["assetSha256"]
        self.repository.layout.resolve_relative(f"objects/sha256/{sha[:2]}/{sha}").write_bytes(b"damaged")
        with TestClient(create_app(self.settings)) as reopened:
            response = self.preview(client=reopened)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "DOCUMENT_DIGEST_MISMATCH")

    def test_invalid_png_is_refused_before_bound_preview_registration(self):
        response = self.client.post("/api/captures", json={**request_payload(self.source["runId"], b"broken"), "modelSource": self.source})
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.preview().json())


if __name__ == "__main__":
    unittest.main()
