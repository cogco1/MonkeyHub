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
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow.project.repository import FilesystemProjectRepository

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project
from .test_cad_export import NEEDS_OCCT, no_process, no_rhino

JOB_DEADLINE = 180.0
# The Studio API may not import ``tools``, and #56's rehearsal contract names
# the CLI anyway, so export and restore go through the landed command itself.
CREATE_PROJECT = Path(__file__).resolve().parents[4] / "tools/create_project.py"


def create_project_cli(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(CREATE_PROJECT), *args], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def export_archive(project_root: Path, archive: Path) -> None:
    create_project_cli("--project", str(project_root), "--export-archive", str(archive))


def restore_archive(restored_root: Path, archive: Path) -> FilesystemProjectRepository:
    create_project_cli("--project", str(restored_root), "--restore-archive", str(archive))
    return FilesystemProjectRepository.open(restored_root)


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
        export_archive(self.source, archive)
        rows = manifest_rows(archive)

        # The source is gone: nothing below can read it by accident.
        shutil.rmtree(self.root / "source")
        restored_root = self.root / "restored" / PROJECT_ID
        restored = restore_archive(restored_root, archive)
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


def runner_receipt(project_root: Path, run_id: str) -> dict:
    paths = sorted((project_root / "runs" / run_id / "records").glob("runner-run-receipt-*.json"))
    assert len(paths) == 1, paths
    return json.loads(paths[0].read_text(encoding="utf-8"))


def exact_step_path(project_root: Path, run_id: str, file_name: str) -> Path:
    matches = sorted((project_root / "runs" / run_id / "workspaces").glob(f"cad-*/{file_name}"))
    assert len(matches) == 1, matches
    return matches[0]


@NEEDS_OCCT
class RestoredContinuationWithOcctTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="restored-occt-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root / "source")
        self.source = self.root / "source" / PROJECT_ID
        rhino = no_rhino()
        rhino.start()
        self.addCleanup(rhino.stop)

    def client(self, project_dir: Path) -> TestClient:
        client = TestClient(create_app(StudioSettings(project_dir=project_dir)))
        self.addCleanup(client.close)
        return client

    def test_continuation_reuses_the_source_export_on_the_source_project(self) -> None:
        """Precondition: A -> B on the untouched source reuses A's exact export."""

        with self.client(self.source) as client, no_process():
            _, job_a = continue_candidate(
                client, source_run_id=None, utterance="set height to 2.2", element_id="portico-base"
            )
            self.assertEqual(job_a["status"], "succeeded", job_a)
            _, job_b = continue_candidate(
                client,
                source_run_id=job_a["candidateId"],
                utterance="set height to 0.5",
                element_id="portico-cornice",
            )
            self.assertEqual(job_b["status"], "succeeded", job_b)
        seat = runner_receipt(self.source, job_b["candidateId"])["seat_results"][0]
        self.assertTrue(seat["cad"].get("reused_object_ids"), seat["cad"])
        self.assertIn("source_execution_ref", seat["cad"])

    def test_a_restored_project_reuses_its_own_copy_and_never_the_source(self) -> None:
        with self.client(self.source) as client, no_process():
            _, job_a = continue_candidate(
                client, source_run_id=None, utterance="set height to 2.2", element_id="portico-base"
            )
        self.assertEqual(job_a["status"], "succeeded", job_a)
        run_a = job_a["candidateId"]
        seat_a = runner_receipt(self.source, run_a)["seat_results"][0]
        exact_name = Path(str(seat_a["cad"]["model"]).replace("\\", "/")).name
        archive = self.root / f"{PROJECT_ID}.monkeyhub.zip"
        export_archive(self.source, archive)

        restored_root = self.root / "restored" / PROJECT_ID
        restore_archive(restored_root, archive)
        # The source project stays where it was, but its exact STEP is gone:
        # anything that still reads the retained absolute path fails loudly.
        exact_step_path(self.source, run_a, exact_name).unlink()
        self.assertTrue(exact_step_path(restored_root, run_a, exact_name).is_file())

        with self.client(restored_root) as client, no_process():
            _, job_b = continue_candidate(
                client,
                source_run_id=run_a,
                utterance="set height to 0.5",
                element_id="portico-cornice",
            )
        self.assertEqual(job_b["status"], "succeeded", job_b)
        seat_b = runner_receipt(restored_root, job_b["candidateId"])["seat_results"][0]
        self.assertTrue(seat_b["cad"].get("reused_object_ids"), seat_b["cad"])
        self.assertEqual(seat_b["cad"]["source_execution_ref"], seat_a["cad"]["execution_ref"])
        # B's retained model path names the restored root, never the source.
        self.assertTrue(Path(seat_b["cad"]["model"]).is_relative_to(restored_root), seat_b["cad"]["model"])

    def test_a_restored_project_continues_after_the_source_is_deleted(self) -> None:
        with self.client(self.source) as client, no_process():
            _, job_a = continue_candidate(
                client, source_run_id=None, utterance="set height to 2.2", element_id="portico-base"
            )
        self.assertEqual(job_a["status"], "succeeded", job_a)
        run_a = job_a["candidateId"]
        archive = self.root / f"{PROJECT_ID}.monkeyhub.zip"
        export_archive(self.source, archive)
        shutil.rmtree(self.root / "source")
        restored_root = self.root / "restored" / PROJECT_ID
        restore_archive(restored_root, archive)
        with self.client(restored_root) as client, no_process():
            _, job_b = continue_candidate(
                client,
                source_run_id=run_a,
                utterance="set height to 0.5",
                element_id="portico-cornice",
            )
            self.assertEqual(job_b["status"], "succeeded", job_b)
            candidate_b = client.get(f"/api/candidates/{job_b['candidateId']}").json()
            exact_b = [row for row in candidate_b["artifacts"] if row["representation"] == "exact"]
            self.assertEqual(len(exact_b), 1, candidate_b["artifacts"])
        seat_b = runner_receipt(restored_root, job_b["candidateId"])["seat_results"][0]
        self.assertTrue(seat_b["cad"].get("reused_object_ids"), seat_b["cad"])
