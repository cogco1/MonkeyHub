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
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
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
            create_app(StudioSettings(project_dir=root / "no-such-project"))
        )
        self.addCleanup(client.close)

        response = client.post(
            "/api/captures",
            json=request_payload(REFERENCE_RUN_ID),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PROJECT_NOT_BOUND")


if __name__ == "__main__":
    unittest.main()
