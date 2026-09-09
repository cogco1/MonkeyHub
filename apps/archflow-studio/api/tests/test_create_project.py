"""Production project creation can feed Studio's existing candidate path."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, RECORD_PAYLOAD, SEATS_PAYLOAD


class ProjectCreationTests(unittest.TestCase):
    def create_project(self, *args: str) -> None:
        command = Path(__file__).resolve().parents[4] / "tools/create_project.py"
        result = subprocess.run([sys.executable, str(command), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_project_connects_without_claiming_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "new-project"
            self.create_project("--project", str(project))
            with TestClient(create_app(StudioSettings(cad_export="off", project_dir=project))) as client:
                self.assertEqual(client.get("/api/project").status_code, 200)
                response = client.get("/api/state")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["elements"], [])
                self.assertIsNone(response.json()["stateDigest"])
            self.assertEqual(list((project / "runs").iterdir()), [])

    def test_authored_inputs_create_a_real_candidate_and_reopen_without_issuing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / PROJECT_ID
            record_path, seats_path = root / "record.json", root / "seats.json"
            record_path.write_text(json.dumps(RECORD_PAYLOAD), encoding="utf-8")
            seats_path.write_text(json.dumps(SEATS_PAYLOAD), encoding="utf-8")
            self.create_project("--project", str(project), "--state-record", str(record_path), "--seats-file", str(seats_path))
            before = (project / "HEAD").read_bytes()
            authored_before = (project / "input/runner/state-record.json").read_bytes()
            settings = StudioSettings(cad_export="off", project_dir=project)
            with TestClient(create_app(settings)) as client:
                state = client.get("/api/state").json()
                self.assertTrue(state["stateDigest"])
                proposal = client.post("/api/proposals", json={
                    "stateDigest": state["stateDigest"], "targetComponentId": "portico",
                    "elementId": "portico-base", "utterance": "set height to 2.2",
                })
                self.assertEqual(proposal.status_code, 201, proposal.text)
                accepted = client.post(f"/api/proposals/{proposal.json()['proposalId']}/candidate")
                self.assertEqual(accepted.status_code, 202, accepted.text)
                job = accepted.json()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    result = client.get(f"/api/jobs/{job['jobId']}").json()
                    if result["status"] in ("succeeded", "failed"):
                        break
                    time.sleep(0.02)
                self.assertEqual(result["status"], "succeeded", result)
            with TestClient(create_app(settings)) as reopened:
                state = reopened.get(f"/api/state?run={job['candidateId']}")
                self.assertEqual(state.status_code, 200, state.text)
                base = next(e for e in state.json()["elements"] if e["elementId"] == "portico-base")
                self.assertEqual(base["numericFields"]["height"], 2.2)
            repository = FilesystemProjectRepository.open(project)
            self.assertEqual(repository.load_run(job["candidateId"]).base.version, 0)
            self.assertEqual((project / "HEAD").read_bytes(), before)
            self.assertEqual((project / "input/runner/state-record.json").read_bytes(), authored_before)
