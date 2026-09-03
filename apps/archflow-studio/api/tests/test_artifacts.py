"""Artifacts are what receipts certify: ref, SHA, run and base travel together.

A ``.3dm`` on disk proves nothing. Everything listed here comes from a retained
``seat-rhino-execution`` receipt, is located by the digest that receipt claims,
and is served only after those bytes hash to that digest again.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application import artifacts
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    PROJECT_ID,
    REFERENCE_RUN_ID,
    RHINO_BRANCH_EPOCH,
    RHINO_BRANCH_ID,
    RHINO_DESIGN_STATE_DIGEST,
    RHINO_PROGRAM_DIGEST,
    add_unreadable_run,
    make_project,
    retain_rhino_receipt,
)

MODEL_BYTES = b"3dm-bytes"
MOVED_BYTES = b"moved-3dm"
FAILED_BYTES = b"failed-3dm"
GONE_BYTES = b"gone-3dm"
STALE_BYTES = b"stale-3dm"


def sha256_of(payload: bytes) -> str:
    """The file identity a receipt claims for those bytes."""

    return hashlib.sha256(payload).hexdigest()


class ArtifactTests(unittest.TestCase):
    """One run with five receipts: every resolution outcome, once."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.run = self.repository.load_run(REFERENCE_RUN_ID)
        self.workspaces = self.repository.layout.run(REFERENCE_RUN_ID).workspaces

        # Exported where the convention says, and still there.
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="studio-stage",
            file_name="model.3dm",
            payload_bytes=MODEL_BYTES,
        )
        # Same file name, different content, somewhere else entirely: only
        # content addressing can tell these two apart.
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="moved-stage",
            file_name="model.3dm",
            payload_bytes=MOVED_BYTES,
            workspace_subdir="other-dir/sub",
        )
        # A failed export: the receipt claims no digest at all.
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="failed-stage",
            file_name="failed.3dm",
            payload_bytes=FAILED_BYTES,
            status="failed",
            inspection=False,
        )
        # Certified, then deleted.
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="gone-stage",
            file_name="gone.3dm",
            payload_bytes=GONE_BYTES,
        )
        (self.workspaces / "cad-gone-stage" / "gone.3dm").unlink()
        # Certified, then overwritten by something else.
        self.stale_ref = retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="stale-stage",
            file_name="stale.3dm",
            payload_bytes=STALE_BYTES,
        )
        (self.workspaces / "cad-stale-stage" / "stale.3dm").write_bytes(
            b"someone-edited-this-in-rhino"
        )

        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.payload = self.client.get("/api/artifacts").json()

    def artifact(self, stage_id: str) -> dict:
        """The one listed artifact of that stage."""

        found = [
            item
            for item in self.payload["artifacts"]
            if item["stageId"] == stage_id
        ]
        self.assertEqual(len(found), 1, stage_id)
        return found[0]

    # ---- the listing

    def test_the_listing_names_the_project_and_sorts_its_artifacts(self) -> None:
        self.assertEqual(self.payload["projectId"], PROJECT_ID)
        self.assertEqual(self.payload["skippedRuns"], [])
        self.assertEqual(
            [
                (item["runId"], item["stageId"], item["fileName"])
                for item in self.payload["artifacts"]
            ],
            [
                (REFERENCE_RUN_ID, "failed-stage", "failed.3dm"),
                (REFERENCE_RUN_ID, "gone-stage", "gone.3dm"),
                (REFERENCE_RUN_ID, "moved-stage", "model.3dm"),
                (REFERENCE_RUN_ID, "stale-stage", "stale.3dm"),
                (REFERENCE_RUN_ID, "studio-stage", "model.3dm"),
            ],
        )
        # No schema tag on the payload: the API stamps none.
        self.assertNotIn("schema", self.payload)

    def test_an_available_artifact_carries_its_run_base_status_and_sha(self) -> None:
        item = self.artifact("studio-stage")
        digest = sha256_of(MODEL_BYTES)

        self.assertEqual(item["artifactId"], digest)
        self.assertEqual(item["sha256"], digest)
        self.assertEqual(item["sizeBytes"], len(MODEL_BYTES))
        self.assertEqual(item["objectCount"], 1)
        self.assertEqual(item["status"], "succeeded")
        self.assertIs(item["readbackVerified"], True)
        self.assertIs(item["available"], True)
        self.assertIsNone(item["unavailableReason"])
        self.assertEqual(
            item["relativePath"],
            f"runs/{REFERENCE_RUN_ID}/workspaces/cad-studio-stage/model.3dm",
        )
        # The run's own base, straight off the receipt.
        self.assertEqual(item["base"]["version"], self.run.base.version)
        self.assertEqual(
            item["base"]["stateSha256"], self.run.base.state_sha256
        )
        self.assertEqual(item["branchId"], RHINO_BRANCH_ID)
        self.assertEqual(item["branchEpoch"], RHINO_BRANCH_EPOCH)
        self.assertEqual(item["programDigest"], RHINO_PROGRAM_DIGEST)
        self.assertEqual(
            item["designStateDigest"], RHINO_DESIGN_STATE_DIGEST
        )
        self.assertTrue(
            item["programRef"].startswith(f"project://{PROJECT_ID}/runs/")
        )
        self.assertEqual(item["lengthUnit"], "meter")
        self.assertEqual(item["upAxis"], "Z-up")
        self.assertTrue(
            item["receiptRef"].startswith(
                f"project://{PROJECT_ID}/runs/{REFERENCE_RUN_ID}/records/"
                "seat-rhino-execution-"
            ),
            item["receiptRef"],
        )

    def test_a_file_outside_the_convention_resolves_by_its_content(self) -> None:
        # Two receipts name ``model.3dm``; each one resolves to the copy whose
        # bytes hash to the digest it claims, not to the first match by name.
        item = self.artifact("moved-stage")

        self.assertIs(item["available"], True)
        self.assertEqual(item["sha256"], sha256_of(MOVED_BYTES))
        self.assertEqual(
            item["relativePath"],
            f"runs/{REFERENCE_RUN_ID}/workspaces/other-dir/sub/model.3dm",
        )

    def test_a_receipt_without_an_inspection_claims_no_digest(self) -> None:
        item = self.artifact("failed-stage")

        self.assertIs(item["available"], False)
        self.assertEqual(item["unavailableReason"], "no inspection digest")
        self.assertIsNone(item["sha256"])
        self.assertIsNone(item["sizeBytes"])
        self.assertIsNone(item["objectCount"])
        self.assertIsNone(item["relativePath"])
        self.assertEqual(item["status"], "failed")
        self.assertIs(item["readbackVerified"], False)
        # Nothing to address it by, so it is addressed by its receipt.
        self.assertTrue(
            item["artifactId"].startswith("receipt:"), item["artifactId"]
        )

    def test_a_deleted_file_is_reported_missing_not_mismatched(self) -> None:
        item = self.artifact("gone-stage")

        self.assertIs(item["available"], False)
        self.assertEqual(item["unavailableReason"], "file missing")
        self.assertIsNone(item["relativePath"])
        # The receipt still says what it certified.
        self.assertEqual(item["sha256"], sha256_of(GONE_BYTES))
        self.assertEqual(item["sizeBytes"], len(GONE_BYTES))

    def test_an_overwritten_file_is_a_digest_mismatch(self) -> None:
        item = self.artifact("stale-stage")

        self.assertIs(item["available"], False)
        self.assertEqual(item["unavailableReason"], "digest mismatch")
        self.assertIsNone(item["relativePath"])
        self.assertEqual(item["sha256"], sha256_of(STALE_BYTES))

    def test_a_run_that_cannot_be_read_is_skipped_and_named(self) -> None:
        broken = add_unreadable_run(self.repository)

        payload = self.client.get("/api/artifacts").json()

        self.assertEqual(payload["skippedRuns"], [broken])
        self.assertEqual(len(payload["artifacts"]), 5)

    def test_a_copy_in_another_runs_workspaces_does_not_count(self) -> None:
        # Content addressing is scoped to the run that claims to have produced
        # the file. A matching copy under another run is another run's business.
        other = self.repository.create_run("scoped-run")
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="scoped-stage",
            file_name="scoped.3dm",
            payload_bytes=b"scoped-3dm",
        )
        here = self.workspaces / "cad-scoped-stage" / "scoped.3dm"
        there = (
            self.repository.layout.run(other.run_id).workspaces
            / "cad-scoped-stage"
        )
        there.mkdir(parents=True, exist_ok=True)
        (there / "scoped.3dm").write_bytes(here.read_bytes())
        here.unlink()

        self.payload = self.client.get("/api/artifacts").json()
        item = self.artifact("scoped-stage")

        self.assertIs(item["available"], False)
        self.assertEqual(item["unavailableReason"], "file missing")
        self.assertIsNone(item["relativePath"])

    def test_a_failed_export_whose_file_is_intact_says_both(self) -> None:
        # Availability and success are separate facts; the villa holds this
        # exact combination, and neither may be inferred from the other.
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="failed-but-there-stage",
            file_name="failed-but-there.3dm",
            payload_bytes=b"failed-but-there-3dm",
            status="failed",
            inspection=True,
        )

        self.payload = self.client.get("/api/artifacts").json()
        item = self.artifact("failed-but-there-stage")

        self.assertIs(item["available"], True)
        self.assertEqual(item["status"], "failed")
        self.assertIs(item["readbackVerified"], False)
        self.assertEqual(item["sha256"], sha256_of(b"failed-but-there-3dm"))

    def test_a_copy_that_cannot_be_read_is_not_called_corrupt(self) -> None:
        # A directory wearing the file's name: present, named right, and
        # impossible to read. Nothing has disagreed with the receipt.
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="unreadable-stage",
            file_name="unreadable.3dm",
            payload_bytes=b"unreadable-3dm",
        )
        blocked = self.workspaces / "cad-unreadable-stage" / "unreadable.3dm"
        blocked.unlink()
        blocked.mkdir()

        self.payload = self.client.get("/api/artifacts").json()
        item = self.artifact("unreadable-stage")

        self.assertIs(item["available"], False)
        self.assertEqual(item["unavailableReason"], "file unreadable")
        self.assertIsNone(item["relativePath"])
        # The receipt's claims survive: only the reading failed.
        self.assertEqual(item["sha256"], sha256_of(b"unreadable-3dm"))

    def test_an_unreadable_copy_refuses_without_claiming_corruption(self) -> None:
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="unreadable-stage",
            file_name="unreadable.3dm",
            payload_bytes=b"unreadable-3dm",
        )
        blocked = self.workspaces / "cad-unreadable-stage" / "unreadable.3dm"
        blocked.unlink()
        blocked.mkdir()

        response = self.client.get(
            f"/api/artifacts/{sha256_of(b'unreadable-3dm')}/bytes"
        )

        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "ARTIFACT_UNREADABLE")
        self.assertEqual(sorted(body), ["code", "detail"])
        self.assertIn("could not be read", body["detail"])
        # The OSError that stopped the read is named, whichever it is here.
        self.assertTrue(
            any(
                name in body["detail"]
                for name in ("PermissionError", "IsADirectoryError", "OSError")
            ),
            body["detail"],
        )
        # Never the mismatch wording: nothing read, nothing disproved.
        self.assertNotIn("is not the exported model", body["detail"])
        # And never the server's own filesystem: the failure is named by its
        # class and the system's message, not by the absolute path OSError
        # would otherwise hand to whoever asked for the file.
        self.assertNotIn(str(self.root), body["detail"])

    def test_a_not_found_names_the_runs_it_could_not_search(self) -> None:
        broken = add_unreadable_run(self.repository)

        response = self.client.get(f"/api/artifacts/{'a' * 64}/bytes")

        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertIn("could not be read", detail)
        self.assertIn("may hold the claiming receipt", detail)
        self.assertIn(broken, detail)

    def test_the_listing_never_walks_the_project_for_3dm_files(self) -> None:
        source = Path(artifacts.__file__).read_text(encoding="utf-8")

        self.assertNotIn('rglob("*.3dm")', source)
        self.assertNotIn("rglob", source)

    # ---- the bytes

    def test_the_bytes_are_served_under_their_own_digest(self) -> None:
        digest = sha256_of(MODEL_BYTES)

        response = self.client.get(f"/api/artifacts/{digest}/bytes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, MODEL_BYTES)
        self.assertEqual(
            response.headers["content-type"], "application/octet-stream"
        )
        self.assertEqual(response.headers["etag"], f'"{digest}"')
        self.assertEqual(
            response.headers["content-disposition"],
            "attachment; filename=\"model.3dm\"; filename*=UTF-8''model.3dm",
        )
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_a_name_no_header_can_spell_is_served_the_rfc_6266_way(self) -> None:
        # Header values go out as latin-1. This name cannot, so the response
        # carries an ASCII stand-in and the real name percent-encoded beside it.
        name = "别墅-façade.3dm"
        payload = b"unicode-named-3dm"
        retain_rhino_receipt(
            self.repository,
            self.run,
            stage_id="unicode-stage",
            file_name=name,
            payload_bytes=payload,
        )

        response = self.client.get(f"/api/artifacts/{sha256_of(payload)}/bytes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, payload)
        disposition = response.headers["content-disposition"]
        self.assertIn(
            "filename*=UTF-8''%E5%88%AB%E5%A2%85-fa%C3%A7ade.3dm", disposition
        )
        self.assertIn('filename="__-fa_ade.3dm"', disposition)
        self.assertTrue(disposition.isascii(), disposition)
        # The real name is still readable where it is not a header value.
        self.payload = self.client.get("/api/artifacts").json()
        self.assertEqual(self.artifact("unicode-stage")["fileName"], name)

    def test_two_receipts_sharing_a_file_name_serve_different_bytes(self) -> None:
        response = self.client.get(
            f"/api/artifacts/{sha256_of(MOVED_BYTES)}/bytes"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, MOVED_BYTES)

    def test_a_digest_no_receipt_claims_is_not_found(self) -> None:
        response = self.client.get(f"/api/artifacts/{'a' * 64}/bytes")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "ARTIFACT_NOT_FOUND")

    def test_a_malformed_digest_is_not_found_rather_than_a_crash(self) -> None:
        for value in ("not-a-sha", sha256_of(MODEL_BYTES).upper(), "0" * 63):
            with self.subTest(value=value):
                response = self.client.get(f"/api/artifacts/{value}/bytes")

                self.assertEqual(response.status_code, 404)
                body = response.json()
                self.assertEqual(body["code"], "ARTIFACT_NOT_FOUND")
                self.assertEqual(sorted(body), ["code", "detail"])

    def test_a_receipt_sha_is_not_an_artifact_digest(self) -> None:
        # The record's own digest addresses the receipt, never its artifact.
        response = self.client.get(
            f"/api/artifacts/{self.stale_ref.sha256}/bytes"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "ARTIFACT_NOT_FOUND")

    def test_a_missing_file_is_not_found_not_a_mismatch(self) -> None:
        response = self.client.get(
            f"/api/artifacts/{sha256_of(GONE_BYTES)}/bytes"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "ARTIFACT_NOT_FOUND")

    def test_an_overwritten_file_refuses_to_serve_its_digest(self) -> None:
        response = self.client.get(
            f"/api/artifacts/{sha256_of(STALE_BYTES)}/bytes"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "ARTIFACT_DIGEST_MISMATCH")

    def test_a_file_tampered_after_the_listing_is_never_served(self) -> None:
        digest = sha256_of(MODEL_BYTES)
        self.assertEqual(
            self.client.get(f"/api/artifacts/{digest}/bytes").status_code, 200
        )

        (self.workspaces / "cad-studio-stage" / "model.3dm").write_bytes(
            b"tampered"
        )

        response = self.client.get(f"/api/artifacts/{digest}/bytes")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "ARTIFACT_DIGEST_MISMATCH")

    def test_bytes_that_hash_to_something_else_are_refused_even_when_the_size_and_time_are_unchanged(
        self,
    ) -> None:
        # The listing remembers a file's digest by (path, size, mtime). Here
        # all three survive the edit, so only recomputing the hash of the bytes
        # actually being served can catch it — which is why the route does.
        digest = sha256_of(MODEL_BYTES)
        path = self.workspaces / "cad-studio-stage" / "model.3dm"
        self.assertEqual(
            self.client.get(f"/api/artifacts/{digest}/bytes").status_code, 200
        )
        before = path.stat()
        self.assertEqual(len(MODEL_BYTES), 9)
        path.write_bytes(b"9-bytes!!")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

        response = self.client.get(f"/api/artifacts/{digest}/bytes")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "ARTIFACT_DIGEST_MISMATCH")


class UnboundArtifactTests(unittest.TestCase):
    """With no project to bind, artifacts refuse in the binding's own words."""

    def test_both_routes_refuse_without_a_bound_project(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        client = TestClient(
            create_app(StudioSettings(project_dir=root / "no-such-project"))
        )
        self.addCleanup(client.close)

        for path in ("/api/artifacts", f"/api/artifacts/{'a' * 64}/bytes"):
            with self.subTest(path=path):
                response = client.get(path)

                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "PROJECT_NOT_BOUND")


if __name__ == "__main__":
    unittest.main()
