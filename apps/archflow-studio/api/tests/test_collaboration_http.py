"""Three real processes exchange and accept locally computed OCCT candidates.

ARCHFLOW_COLLABORATION_PYTHON and ARCHFLOW_COLLABORATION_SOURCE_ROOT can point
the subprocesses at an unpacked distribution. The test driver still owns only
temporary synthetic projects, temporary actor credentials and its own children.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from archflow.adapters import occt_backend
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository

from .collaboration_support import seed_collaboration_project
from .support import PROJECT_ID


SOURCE_ROOT = Path(__file__).resolve().parents[4]
DEADLINE_SECONDS = 60


class _Service:
    """A test-owned API process; shutdown uses its own managed stdin pipe."""

    def __init__(self, root: Path, name: str, environment: dict[str, str]):
        self.project = root / "workspace" / "projects" / name / PROJECT_ID
        self.log = root / f"{name}.log"
        self.environment = environment
        self.instance_id = "collaboration-test-" + secrets.token_hex(8)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        source = Path(os.environ.get("ARCHFLOW_COLLABORATION_SOURCE_ROOT", SOURCE_ROOT)).resolve()
        api = source / "apps" / "archflow-studio" / "api"
        if not (api / "archflow_studio_api" / "main.py").is_file():
            raise AssertionError(f"No Studio API in test source root: {source}")
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("ARCHFLOW_STUDIO_")
                       and key not in {"MONKEYMONITOR_DATA_DIR", "PYTHONPATH"}}
        environment.update({
            "ARCHFLOW_STUDIO_INTENT_PROVIDER": "deterministic",
            "ARCHFLOW_STUDIO_REFERENCE_RUN": "run-001",
            "ARCHFLOW_STUDIO_WORKERS": "1",
            "PYTHONUNBUFFERED": "1",
            **self.environment,
        })
        # Explicit paths also work with Windows embedded Python, whose _pth
        # intentionally ignores PYTHONPATH. A package override uses package
        # code throughout the child, never the driver's source checkout.
        bootstrap = (
            f"import sys; sys.path[:0] = {[str(api), str(source)]!r}; "
            "from archflow_studio_api.main import main; main()"
        )
        command = [os.environ.get("ARCHFLOW_COLLABORATION_PYTHON", sys.executable),
                   "-c", bootstrap, "--project-dir", str(self.project),
                   "--host", "127.0.0.1", "--port", str(self.port),
                   "--managed-stdin", "--managed-instance-id", self.instance_id]
        with self.log.open("ab") as log:
            self.process = subprocess.Popen(
                command, cwd=source, env=environment, stdin=subprocess.PIPE,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        deadline = time.monotonic() + DEADLINE_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(self.log.read_text(encoding="utf-8", errors="replace"))
            try:
                status, health = self.request("GET", "/api/health", timeout=1)
                if status == 200 and health.get("managedInstanceId") == self.instance_id:
                    # Windows venv Python may launch the serving process through
                    # a redirector; the private instance id above still binds it.
                    owned_pid = health["processId"] == self.process.pid or (
                        sys.platform == "win32" and health["parentProcessId"] == self.process.pid
                    )
                    assert owned_pid, health
                    return
            except (URLError, OSError, TimeoutError):
                pass
            time.sleep(0.05)
        raise AssertionError("Test service did not start: " + self.log.read_text(encoding="utf-8", errors="replace"))

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                self.process.stdin.write(b"stop\n")
                self.process.stdin.flush()
                self.process.wait(timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                # A wedged test child must not escape the temporary test. This
                # handle cannot name or stop an existing user-owned service.
                self.process.kill()
                self.process.wait(timeout=10)
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process = None

    def request(self, method, path, payload=None, *, token=None, binary=False, timeout=60):
        content = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.url + path, data=content, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                status, data = response.status, response.read()
        except HTTPError as response:
            status, data = response.code, response.read()
        return status, data if binary else json.loads(data)


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class CollaborationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="archflow-collaboration-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tokens = {actor: secrets.token_urlsafe(24) for actor in ("alice", "bob", "reader", "reviewer")}
        config = self.root / "actors.json"
        config.write_text(json.dumps({"actors": [
            {"actor_id": actor, "token": token,
             "projects": {PROJECT_ID: (["read"] if actor == "reader" else
                                       ["read", "accept"] if actor == "reviewer" else
                                       ["read", "propose", "accept"])}}
            for actor, token in self.tokens.items()
        ]}), encoding="utf-8")
        self.shared = _Service(self.root, "shared", {
            "ARCHFLOW_STUDIO_MODE": "remote",
            "ARCHFLOW_STUDIO_SERVICE_ROLE": "shared_project",
            "ARCHFLOW_STUDIO_ACTORS_FILE": str(config),
            "ARCHFLOW_STUDIO_ORIGINS": "http://127.0.0.1",
            "ARCHFLOW_STUDIO_CAD_EXPORT": "off",
        })
        self.addCleanup(self.shared.stop)
        seed = seed_collaboration_project(self.shared.project)
        self.seed = seed
        self.initial_head = FilesystemProjectRepository.open(self.shared.project).read_head()
        self.shared.start()
        self.stage0 = self.ok(self.shared, "POST", "/api/design-stages/initialize",
                              {"projectId": PROJECT_ID, "modelSource": seed["modelSource"]},
                              status=201, token=self.tokens["alice"])
        self.a = self.runtime("runtime-a", "alice")
        self.b = self.runtime("runtime-b", "bob")

    def runtime(self, name, actor):
        service = _Service(self.root, name, {
            "ARCHFLOW_STUDIO_MODE": "local",
            "ARCHFLOW_STUDIO_SERVICE_ROLE": "runtime",
            "ARCHFLOW_STUDIO_CAD_EXPORT": "occt",
            "ARCHFLOW_STUDIO_SYNC_URL": self.shared.url,
            "ARCHFLOW_STUDIO_SYNC_TOKEN": self.tokens[actor],
            "ARCHFLOW_STUDIO_SYNC_PROJECT_ID": PROJECT_ID,
        })
        self.addCleanup(service.stop)
        service.start()
        self.assertFalse(service.request("GET", "/api/health")[1]["projectBound"])
        first = self.ok(service, "POST", "/api/sync/pull")
        self.assertGreater(first["bytesTransferred"], 0)
        self.assertTrue(service.request("GET", "/api/health")[1]["projectBound"])
        self.assertEqual(self.ok(service, "GET", "/api/design-history")["stages"], [self.stage0])
        return service

    def ok(self, service, method, path, payload=None, *, status=200, token=None):
        actual_status, body = service.request(method, path, payload, token=token)
        self.assertEqual(actual_status, status, body)
        return body

    def candidate(self, service, stage, element, height):
        proposal = self.ok(service, "POST", "/api/proposals", {
            "stateDigest": stage["modelSource"]["stateDigest"],
            "sourceRunId": stage["candidateId"], "sourceStageRef": stage["stageRef"],
            "targetComponentId": "portico", "elementId": element,
            "utterance": f"set height to {height}",
        }, status=201)
        started = self.ok(service, "POST", f"/api/proposals/{proposal['proposalId']}/candidate", status=202)
        deadline = time.monotonic() + DEADLINE_SECONDS
        while time.monotonic() < deadline:
            job = self.ok(service, "GET", f"/api/jobs/{started['jobId']}")
            if job["status"] in {"succeeded", "failed"}:
                self.assertEqual(job["status"], "succeeded", job)
                result = self.ok(service, "GET", f"/api/candidates/{started['candidateId']}")
                self.assertTrue(any(item["format"] == "step" and item["available"] for item in result["artifacts"]), result)
                return started["candidateId"]
            time.sleep(0.05)
        self.fail(f"Candidate job did not finish: {started['jobId']}")

    def acceptance(self, service, candidate, source_stage, *, status=200):
        return self.ok(service, "POST", f"/api/candidates/{candidate}/accept", {
            "projectId": PROJECT_ID, "branchId": "main",
            "expectedHeadStageRef": source_stage["stageRef"],
        }, status=status)

    def test_local_execution_shared_acceptance_and_cold_recovery(self):
        alice = self.tokens["alice"]
        initial_manifest = self.ok(self.shared, "GET", "/api/sync/manifest", token=alice)
        a_candidate = self.candidate(self.a, self.stage0, "portico-base", 0.8)
        stale_candidate = self.candidate(self.b, self.stage0, "portico-base", 0.9)
        self.ok(self.a, "POST", "/api/sync/push?" + urlencode({"candidateId": a_candidate}))
        after_push = self.ok(self.shared, "GET", "/api/sync/manifest", token=alice)
        self.assertEqual(after_push["head"], initial_manifest["head"])
        self.assertEqual(after_push["branches"], initial_manifest["branches"])

        self.shared.stop()
        disconnected = self.acceptance(self.a, a_candidate, self.stage0, status=503)
        self.assertEqual(disconnected["code"], "SYNC_UNAVAILABLE")
        self.ok(self.a, "GET", f"/api/candidates/{a_candidate}")
        self.assertEqual(self.ok(self.a, "GET", "/api/design-history")["stages"], [self.stage0])
        self.shared.start()
        stage1 = self.acceptance(self.a, a_candidate, self.stage0)
        self.assertEqual(stage1["label"], "S1")
        self.assertEqual(self.acceptance(self.a, a_candidate, self.stage0), stage1)

        stale = self.acceptance(self.b, stale_candidate, self.stage0, status=409)
        self.assertEqual(stale["code"], "DESIGN_BRANCH_STALE")
        self.ok(self.b, "GET", f"/api/candidates/{stale_candidate}")
        self.ok(self.b, "POST", "/api/sync/pull")
        b_candidate = self.candidate(self.b, stage1, "portico-cornice", 0.4)
        stage2 = self.acceptance(self.b, b_candidate, stage1)
        self.assertEqual(stage2["label"], "S2")
        self.ok(self.a, "POST", "/api/sync/pull")
        self.assertEqual(self.ok(self.a, "POST", "/api/sync/pull")["bytesTransferred"], 0)

        expected_history = [self.stage0, stage1, stage2]
        model_sha = stage2["modelSource"]["assetSha256"]
        for service, token in ((self.shared, alice), (self.a, None), (self.b, None)):
            self.assertEqual(self.ok(service, "GET", "/api/design-history", token=token)["stages"], expected_history)
            status, data = service.request("GET", f"/api/artifacts/{model_sha}/bytes", token=token, binary=True)
            self.assertEqual(status, 200)
            self.assertEqual(hashlib.sha256(data).hexdigest(), model_sha)
            repository = FilesystemProjectRepository.open(service.project)
            self.assertEqual(repository.read_head(), self.initial_head)
            for stage, actor in ((stage1, "alice"), (stage2, "bob")):
                record = repository.load_json(record_ref_from_uri(stage["stageRef"], PROJECT_ID))
                self.assertEqual(record["accepted_by"], actor)

        # Identity is service configuration; a request cannot add an actor or
        # nominate another project to expand the authenticated actor's grant.
        accept_body = {"projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": stage1["stageRef"]}
        self.ok(self.shared, "POST", f"/api/candidates/{b_candidate}/accept", accept_body,
                status=403, token=self.tokens["reader"])
        self.ok(self.shared, "POST", f"/api/candidates/{b_candidate}/accept", {**accept_body, "actor": "alice"},
                status=422, token=self.tokens["bob"])
        status, _ = self.shared.request("POST", f"/api/candidates/{b_candidate}/accept",
                                         {**accept_body, "projectId": "another-project"}, token=self.tokens["bob"])
        self.assertIn(status, (403, 409, 422))
        self.ok(self.shared, "POST", "/api/proposals", {}, status=403, token=alice)
        self.ok(self.shared, "POST", "/api/sync/candidates", {}, status=403, token=self.tokens["reviewer"])

        self.shared.stop()
        self.a.stop()
        self.shared.start()
        # The reviewer has read+accept and no propose grant. Retrying a saved
        # candidate must not turn acceptance into another upload operation.
        self.a.environment["ARCHFLOW_STUDIO_SYNC_TOKEN"] = self.tokens["reviewer"]
        self.a.start()
        self.assertEqual(self.ok(self.shared, "GET", "/api/design-history", token=alice)["stages"], expected_history)
        self.assertEqual(self.ok(self.a, "GET", "/api/design-history")["stages"], expected_history)
        self.assertEqual(self.ok(self.a, "POST", "/api/sync/pull")["bytesTransferred"], 0)
        self.assertEqual(self.acceptance(self.a, a_candidate, self.stage0), stage1)
        accepted = FilesystemProjectRepository.open(self.shared.project).load_json(
            record_ref_from_uri(stage1["stageRef"], PROJECT_ID)
        )
        self.assertEqual(accepted["accepted_by"], "alice")

        # STEP cold read observes saved geometry, independently of compiler
        # bounds and of the 3DM viewer preview delivered over HTTP.
        final = self.ok(self.shared, "GET", f"/api/candidates/{b_candidate}", token=alice)
        step = next(item for item in final["artifacts"] if item["format"] == "step")
        step_path = self.shared.project / step["relativePath"]
        self.assertEqual(hashlib.sha256(step_path.read_bytes()).hexdigest(), step["sha256"])
        shapes = {entry.name: occt_backend.measure_shape(entry.shape)
                  for entry in occt_backend.read_step(step_path, length_unit="meter")}
        self.assertEqual(set(shapes), {"obj-portico-base", "obj-portico-cornice"})
        for shape in shapes.values():
            self.assertTrue(shape.valid and shape.closed)
        self.assertAlmostEqual(shapes["obj-portico-base"].bbox_max[2], 0.8)
        self.assertAlmostEqual(shapes["obj-portico-cornice"].bbox_min[2], 0.8)
        self.assertAlmostEqual(shapes["obj-portico-cornice"].bbox_max[2], 1.2)
        self.assertAlmostEqual(shapes["obj-portico-base"].volume, 6.4)
        self.assertAlmostEqual(shapes["obj-portico-cornice"].volume, 3.2)


if __name__ == "__main__":
    unittest.main()
