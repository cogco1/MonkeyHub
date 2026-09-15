"""A restored project continues from its restored run.

The #56 archive restores HEAD, run manifests, records and receipts byte for
byte, so the candidate/base contract has to hold on the restored side. These
tests go through the real routes on a project restored into a *different*
directory after the source was deleted, so nothing here can succeed by
reading the original.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow.project.repository import FilesystemProjectRepository
from tools.create_project import _restore_project_archive, _write_project_archive

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project

JOB_DEADLINE = 180.0


def finished(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + JOB_DEADLINE
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in ("succeeded", "failed"):
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished")


def continue_candidate(
    client: TestClient, *, source_run_id: str | None, utterance: str, element_id: str
) -> tuple[dict, dict]:
    """Propose against the displayed run and run the candidate, the way the Studio does."""

    params = {"run": source_run_id} if source_run_id else {}
    state = client.get("/api/state", params=params)
    assert state.status_code == 200, state.text
    body = {
        "stateDigest": state.json()["stateDigest"],
        "targetComponentId": "portico",
        "elementId": element_id,
        "utterance": utterance,
    }
    if source_run_id:
        body["sourceRunId"] = source_run_id
    proposal = client.post("/api/proposals", json=body)
    assert proposal.status_code == 201, proposal.text
    accepted = client.post(f"/api/proposals/{proposal.json()['proposalId']}/candidate")
    assert accepted.status_code == 202, accepted.text
    return state.json(), {**accepted.json(), **finished(client, accepted.json()["jobId"])}


def manifest_rows(archive_path: Path) -> dict[str, str]:
    """path -> sha256 of every retained file the archive carries."""

    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return {row["path"]: row["sha256"] for row in manifest["transfer"]["files"]}


class RestoredContinuationWithoutCadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="restored-continuation-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root / "source")
        self.source = self.root / "source" / PROJECT_ID
        self.settings = StudioSettings(project_dir=self.source, cad_export="off")

    def client(self, project_dir: Path) -> TestClient:
        client = TestClient(create_app(StudioSettings(project_dir=project_dir, cad_export="off")))
        self.addCleanup(client.close)
        return client

    def test_a_restored_project_continues_from_its_restored_run(self) -> None:
        # A: one candidate on the source, then export it.
        with self.client(self.source) as source_client:
            _, job_a = continue_candidate(
                source_client,
                source_run_id=None,
                utterance="set height to 2.2",
                element_id="portico-base",
            )
        self.assertEqual(job_a["status"], "succeeded", job_a)
        run_a = job_a["candidateId"]
        source_head = self.repository.read_head()
        archive = self.root / f"{PROJECT_ID}.monkeyhub.zip"
        _write_project_archive(FilesystemProjectRepository.open(self.source), archive)
        rows = manifest_rows(archive)

        # The source is gone: nothing below can read it by accident.
        shutil.rmtree(self.root / "source")
        restored_root = self.root / "restored" / PROJECT_ID
        restored, _ = _restore_project_archive(restored_root, archive)
        self.assertEqual(restored.read_head(), source_head)

        # B: continue from A on the restored project.
        with self.client(restored_root) as client:
            state_a, job_b = continue_candidate(
                client,
                source_run_id=run_a,
                utterance="set height to 0.5",
                element_id="portico-cornice",
            )
            self.assertEqual(state_a["referenceRun"]["runId"], run_a)
            self.assertTrue(state_a["matchesReferenceReceipt"])
            self.assertEqual(job_b["status"], "succeeded", job_b)
            run_b = job_b["candidateId"]
            candidate_b = client.get(f"/api/candidates/{run_b}")
            self.assertEqual(candidate_b.status_code, 200, candidate_b.text)
            self.assertEqual(candidate_b.json()["base"]["version"], source_head.version)
            self.assertEqual(candidate_b.json()["base"]["stateSha256"], source_head.state_sha256)

        # B's run is based on the restored HEAD and remembers A as its source.
        restored = FilesystemProjectRepository.open(restored_root)
        self.assertEqual(restored.load_run(run_b).base, source_head)
        self.assertEqual(restored.read_head(), source_head)
        delta = next(
            json.loads(path.read_text(encoding="utf-8"))
            for path in (restored_root / "runs" / run_b / "records").glob(
                "studio-candidate-delta-*.json"
            )
        )
        self.assertEqual(delta["source_run_ref"]["run_id"], run_a)
        # Every restored file is exactly what the archive carried; only B was added.
        for path, digest in rows.items():
            self.assertEqual(
                hashlib.sha256((restored_root / path).read_bytes()).hexdigest(), digest, path
            )
        self.assertEqual(
            {p.name for p in (restored_root / "runs").iterdir()} - {REFERENCE_RUN_ID, run_a, run_b},
            set(),
        )
