"""Hub HTTP admission and recovery against real isolated Studio/P036 workers."""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
import signal
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_monkeyhub_lifecycle import LocalHubCase, ROOT, project_fixture, wait_for
from archflow.project.repository import FilesystemProjectRepository


class ProjectRuntimeHttpTests(LocalHubCase):
    def setUp(self):
        super().setUp()
        self.fixture = project_fixture()
        self.repository, _ = self.fixture.make_project(self.root / "projects")
        self.project_id = self.fixture.PROJECT_ID
        self.project = self.root / "projects" / self.project_id
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<html>Project runtime fixture</html>", encoding="utf-8")

    def make_parallel_project(self):
        project_id = "parallel-project"
        root = self.root / "projects" / project_id
        payload = deepcopy(self.fixture.RECORD_PAYLOAD)
        payload["project_id"] = project_id
        # Reuse the real fixture's authored inputs and retained receipt writer,
        # selecting only this disposable project's independent identity.
        with patch.object(self.fixture, "PROJECT_ID", project_id):
            repository = FilesystemProjectRepository.initialize(root, project_id=project_id,
                initial_state={"project_id": project_id, "version": 0})
            run = repository.create_run(self.fixture.REFERENCE_RUN_ID)
            self.fixture.write_runner_record(repository, payload)
            self.fixture.write_runner_seats(repository)
            digest = self.fixture.runner_state_digest(repository, run.run_id, payload)
            self.fixture.retain_runner_receipt(repository, run, design_state_digest=digest, record_payload=payload)
        return project_id, root

    def open_project(self, client, project_id=None, project=None):
        project_id, project = project_id or self.project_id, project or self.project
        response = client.post("/api/runtime/projects/open", json={"projectId": project_id, "projectDir": str(project)})
        self.assertEqual(response.status_code, 200, response.text)
        runtime = response.json()
        started = client.post("/api/apps/monkeyarch/start", params={"projectDir": str(project)})
        self.assertEqual(started.status_code, 202, started.text)
        self.wait_state(client, "monkeyarch", "running", project_dir=project)
        self.wait_runtime(client, runtime["runtimeId"], lambda row: row["projection"] == "ready")
        return runtime["runtimeId"]

    def read_runtime(self, client, runtime_id):
        response = client.get(f"/api/runtime/projects/{runtime_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_runtime(self, client, runtime_id, predicate):
        def check():
            row = self.read_runtime(client, runtime_id)
            return row if predicate(row) else None
        return wait_for(check, "Project runtime did not reach the expected state", timeout=40)

    def proxy(self, client, runtime_id, path, method="GET", *, operation_id=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if operation_id:
            headers["Idempotency-Key"] = operation_id
        return client.request(method, f"/api/runtime/projects/{runtime_id}/studio{path}", headers=headers, **kwargs)

    def propose(self, client, runtime_id, *, project_id=None, stage=None):
        source = stage["candidateId"] if stage else self.fixture.REFERENCE_RUN_ID
        params = {"run": source}
        if stage:
            params["sourceStageRef"] = stage["stageRef"]
        state = self.proxy(client, runtime_id, "/api/state", params=params)
        self.assertEqual(state.status_code, 200, state.text)
        body = {"projectId": project_id or self.project_id, "stateDigest": state.json()["stateDigest"],
                "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 2.2",
                "sourceRunId": source}
        if stage:
            body["sourceStageRef"] = stage["stageRef"]
        response = self.proxy(client, runtime_id, "/api/proposals", "POST", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait_job(self, client, runtime_id, job_id):
        def check():
            response = self.proxy(client, runtime_id, f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            row = response.json()
            return row if row["status"] in {"succeeded", "failed"} else None
        row = wait_for(check, "Candidate job did not finish", timeout=120)
        self.assertEqual(row["status"], "succeeded", row)
        return row

    @staticmethod
    def project_bytes(project):
        return {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}

    def test_concurrent_duplicate_submission_parallel_projects_and_page_reopen(self):
        other_id, other_project = self.make_parallel_project()
        with self.hub(studio_web=self.web) as client:
            a = self.open_project(client)
            b = self.open_project(client, other_id, other_project)
            workers_before = {row["runtimeId"]: row["workers"][0] for row in client.get("/api/runtime").json()["projects"]}
            self.assertNotEqual(workers_before[a]["processId"], workers_before[b]["processId"])
            self.assertNotEqual(workers_before[a]["url"], workers_before[b]["url"])
            proposal_a = self.propose(client, a)
            proposal_b = self.propose(client, b, project_id=other_id)
            operation_id = str(uuid4())
            candidate_id = "hub-cand-" + operation_id.replace("-", "")
            barrier = threading.Barrier(3)

            def submit(runtime_id, proposal):
                barrier.wait(timeout=10)
                return self.proxy(client, runtime_id, f"/api/proposals/{proposal['proposalId']}/candidate", "POST", operation_id=operation_id)

            with ThreadPoolExecutor(max_workers=3) as pool:
                responses = [future.result(timeout=40) for future in [
                    pool.submit(submit, a, proposal_a), pool.submit(submit, a, proposal_a), pool.submit(submit, b, proposal_b),
                ]]
            for response in responses:
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(response.json()["candidateId"], candidate_id)
            self.assertEqual(responses[0].json(), responses[1].json())
            self.assertNotEqual(responses[0].json()["jobId"], responses[2].json()["jobId"])
            self.wait_job(client, a, responses[0].json()["jobId"])
            self.wait_job(client, b, responses[2].json()["jobId"])
            for runtime_id, project_id, project in ((a, self.project_id, self.project), (b, other_id, other_project)):
                snapshot = self.wait_runtime(client, runtime_id, lambda row: any(
                    operation["operationId"] == operation_id and operation["status"] == "completed" for operation in row["operations"]))
                operation = next(row for row in snapshot["operations"] if row["operationId"] == operation_id)
                self.assertEqual(operation["projectId"], project_id)
                self.assertEqual(operation["candidateId"], candidate_id)
                self.assertEqual(len([row for row in snapshot["retained"]["jobs"] if row["candidateId"] == candidate_id]), 1)
                self.assertEqual(sorted(path.name for path in (project / "runs").iterdir()), sorted([self.fixture.REFERENCE_RUN_ID, candidate_id]))
                reopened = client.post("/api/runtime/projects/open", json={"projectId": project_id, "projectDir": str(project)})
                self.assertEqual(reopened.status_code, 200, reopened.text)
                self.assertEqual(reopened.json()["runtimeId"], runtime_id)
                self.assertEqual(reopened.json()["workers"][0]["instanceId"], workers_before[runtime_id]["instanceId"])
            refreshed = client.get("/api/runtime").json()
            self.assertEqual({row["runtimeId"] for row in refreshed["projects"]}, {a, b})
            wrong = client.post(f"/api/runtime/projects/{a}/close", json={"projectId": other_id})
            self.assertEqual(wrong.status_code, 409, wrong.text)
            closed = client.post(f"/api/runtime/projects/{a}/close", json={"projectId": self.project_id})
            self.assertEqual(closed.status_code, 202, closed.text)
            self.assertEqual(closed.json()["state"], "closed")
            self.wait_state(client, "monkeyarch", "stopped", project_dir=self.project)
            other = self.read_runtime(client, b)
            self.assertEqual(other["workers"][0]["instanceId"], workers_before[b]["instanceId"])
            self.assertIn(other["workers"][0]["state"], {"ready", "busy"})

    def register_model(self, client, runtime_id, run_id, state_digest):
        model = (ROOT / "apps/archflow-studio/api/tests/fixtures/model-source-a.3dm").read_bytes()
        response = self.proxy(client, runtime_id, "/api/model-assets", "POST", json={
            "projectId": self.project_id, "runId": run_id, "stateDigest": state_digest,
            "fileName": "complete.3dm", "contentBase64": base64.b64encode(model).decode(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["modelSource"]

    def test_committed_candidate_survives_worker_crash_without_duplicate_modification(self):
        with self.hub(studio_web=self.web) as client:
            runtime_id = self.open_project(client)
            state = self.proxy(client, runtime_id, "/api/state").json()
            model = self.register_model(client, runtime_id, self.fixture.REFERENCE_RUN_ID, state["stateDigest"])
            initial = self.proxy(client, runtime_id, "/api/design-stages/initialize", "POST",
                json={"projectId": self.project_id, "modelSource": model})
            self.assertEqual(initial.status_code, 201, initial.text)
            initial = initial.json()
            proposal = self.propose(client, runtime_id, stage=initial)
            candidate_operation = str(uuid4())
            candidate_path = f"/api/proposals/{proposal['proposalId']}/candidate"
            submitted = self.proxy(client, runtime_id, candidate_path, "POST", operation_id=candidate_operation)
            self.assertEqual(submitted.status_code, 202, submitted.text)
            self.wait_job(client, runtime_id, submitted.json()["jobId"])
            candidate_id = submitted.json()["candidateId"]
            candidate_state = self.proxy(client, runtime_id, "/api/state",
                params={"run": candidate_id, "sourceStageRef": initial["stageRef"]})
            self.assertEqual(candidate_state.status_code, 200, candidate_state.text)
            self.register_model(client, runtime_id, candidate_id, candidate_state.json()["stateDigest"])
            accept_operation = str(uuid4())
            accept_path = f"/api/candidates/{candidate_id}/accept"
            accept_body = {"projectId": self.project_id, "expectedHeadStageRef": initial["stageRef"]}
            accepted = self.proxy(client, runtime_id, accept_path, "POST", operation_id=accept_operation, json=accept_body)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            completed = self.wait_runtime(client, runtime_id, lambda row: any(
                operation["operationId"] == accept_operation and operation["committed"] and operation["status"] == "completed" for operation in row["operations"]))
            original_worker = completed["workers"][0]
            before = self.project_bytes(self.project)
            owned = client.app.state.applications.supervisor._children[original_worker["workerId"]].process
            os.kill(original_worker["processId"], signal.SIGTERM)  # Only this test's verified managed worker.
            owned.wait(timeout=10)
            crashed = self.read_runtime(client, runtime_id)
            self.assertEqual(crashed["workers"][0]["state"], "crashed")
            self.assertIsNone(crashed["workers"][0]["processId"])
            recovered = client.post(f"/api/runtime/projects/{runtime_id}/recover", json={"projectId": self.project_id})
            self.assertEqual(recovered.status_code, 202, recovered.text)
            ready = self.wait_runtime(client, runtime_id, lambda row: row["projection"] == "ready" and row["workers"][0]["healthy"])
            self.assertEqual(ready["workers"][0]["url"], original_worker["url"])
            self.assertNotEqual(ready["workers"][0]["instanceId"], original_worker["instanceId"])
            self.assertEqual(ready["retained"]["jobs"], [])
            retained = next(row for row in ready["operations"] if row["operationId"] == accept_operation)
            self.assertTrue(retained["committed"])
            self.assertEqual(retained["status"], "completed")
            repeated_candidate = self.proxy(client, runtime_id, candidate_path, "POST", operation_id=candidate_operation)
            self.assertEqual(repeated_candidate.status_code, 202, repeated_candidate.text)
            self.assertEqual(repeated_candidate.json(), submitted.json())
            repeated_accept = self.proxy(client, runtime_id, accept_path, "POST", operation_id=accept_operation, json=accept_body)
            self.assertEqual(repeated_accept.status_code, 200, repeated_accept.text)
            self.assertEqual(repeated_accept.json(), accepted.json())
            history = self.proxy(client, runtime_id, "/api/design-history")
            self.assertEqual(history.status_code, 200, history.text)
            self.assertEqual(history.json()["stages"], [initial, accepted.json()])
            self.assertEqual(self.project_bytes(self.project), before)

    def test_wrong_project_and_stale_base_are_refused_without_runs(self):
        with self.hub(studio_web=self.web) as client:
            runtime_id = self.open_project(client)
            before = self.project_bytes(self.project)
            state = self.proxy(client, runtime_id, "/api/state").json()
            body = {"projectId": self.project_id, "stateDigest": state["stateDigest"],
                    "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 2.2"}
            wrong_open = client.post("/api/runtime/projects/open", json={"projectId": "other-project", "projectDir": str(self.project)})
            self.assertEqual(wrong_open.status_code, 409, wrong_open.text)
            wrong_body = self.proxy(client, runtime_id, "/api/proposals", "POST", json={**body, "projectId": "other-project"})
            self.assertEqual(wrong_body.status_code, 409, wrong_body.text)
            self.assertEqual(wrong_body.json()["code"], "PROJECT_MISMATCH")
            wrong_query = self.proxy(client, runtime_id, "/api/state", params={"projectId": "other-project"})
            self.assertEqual(wrong_query.status_code, 409, wrong_query.text)
            stale_id = str(uuid4())
            stale = self.proxy(client, runtime_id, "/api/proposals", "POST", operation_id=stale_id,
                json={**body, "stateDigest": "0" * 64})
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertIn("STALE", stale.json()["code"])
            snapshot = self.wait_runtime(client, runtime_id, lambda row: any(
                operation["operationId"] == stale_id and operation["status"] == "stale" for operation in row["operations"]))
            self.assertFalse(any(row["committed"] for row in snapshot["operations"]))
            self.assertEqual(self.project_bytes(self.project), before)

    def test_worker_exit_before_candidate_write_is_not_completed_or_replayed(self):
        # Patch only the disposable child interpreter. It retains the normal
        # Studio HTTP routes and managed identity, then exits at the real job's
        # execution boundary before execute_candidate writes its first run.
        fault_script = self.root / "exit-before-candidate.py"
        launcher = ROOT / "apps/monkeyhub/run.py"
        fault_script.write_text(
            "import os, runpy, sys\n"
            f"sys.path[:0] = {[str(ROOT / 'apps/archflow-studio/api'), str(ROOT)]!r}\n"
            "from archflow_studio_api.routes import candidates\n"
            "def exit_before_candidate(*args, **kwargs):\n    os._exit(73)\n"
            "candidates.execute_candidate = exit_before_candidate\n"
            f"sys.argv[0] = {str(launcher)!r}\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n",
            encoding="utf-8",
        )
        with self.hub(studio_web=self.web) as client:
            applications = client.app.state.applications
            original_command = applications._command

            def fault_command(service, settings):
                command, environment = original_command(service, settings)
                if service == "studio":
                    command[1] = str(fault_script)
                return command, environment

            with patch.object(applications, "_command", side_effect=fault_command):
                runtime_id = self.open_project(client)
            proposal = self.propose(client, runtime_id)
            operation_id = str(uuid4())
            path = f"/api/proposals/{proposal['proposalId']}/candidate"
            before = self.project_bytes(self.project)
            response = self.proxy(client, runtime_id, path, "POST", operation_id=operation_id)
            self.assertIn(response.status_code, {202, 503}, response.text)
            crashed = self.wait_runtime(client, runtime_id, lambda row:
                row["workers"][0]["state"] == "crashed" and any(
                    operation["operationId"] == operation_id and operation["status"] == "needs_recovery"
                    for operation in row["operations"]))
            original_instance = crashed["workers"][0]["instanceId"]
            recovered = client.post(f"/api/runtime/projects/{runtime_id}/recover", json={"projectId": self.project_id})
            self.assertEqual(recovered.status_code, 202, recovered.text)
            ready = self.wait_runtime(client, runtime_id, lambda row: row["projection"] == "ready" and row["workers"][0]["healthy"])
            worker = ready["workers"][0]
            self.assertNotEqual(worker["instanceId"], original_instance)
            operation = next(row for row in ready["operations"] if row["operationId"] == operation_id)
            self.assertEqual(operation["status"], "needs_recovery")
            self.assertFalse(operation["committed"])
            self.assertEqual(ready["retained"]["jobs"], [])
            repeated = self.proxy(client, runtime_id, path, "POST", operation_id=operation_id)
            self.assertIn(repeated.status_code, {202, 409}, repeated.text)
            self.assertEqual(self.read_runtime(client, runtime_id)["workers"][0]["instanceId"], worker["instanceId"])
            self.assertEqual(self.project_bytes(self.project), before)

    def test_crashed_worker_can_be_explicitly_stopped_and_started_again(self):
        with self.hub(studio_web=self.web) as client:
            runtime_id = self.open_project(client)
            original = self.read_runtime(client, runtime_id)["workers"][0]
            before = self.project_bytes(self.project)
            owned = client.app.state.applications.supervisor._children[original["workerId"]].process
            os.kill(original["processId"], signal.SIGTERM)
            owned.wait(timeout=10)
            crashed = self.read_runtime(client, runtime_id)["workers"][0]
            self.assertEqual(crashed["state"], "crashed")
            self.assertEqual(crashed["desiredState"], "running")
            for _ in range(2):
                stopped = client.post("/api/apps/monkeydiagram/stop", params={"projectDir": str(self.project)})
                self.assertEqual(stopped.status_code, 202, stopped.text)
                self.assertEqual(stopped.json()["state"], "stopped")
                snapshot = self.read_runtime(client, runtime_id)["workers"][0]
                self.assertEqual(snapshot["state"], "stopped")
                self.assertEqual(snapshot["desiredState"], "stopped")
                self.assertIsNone(snapshot["processId"])
                self.assertIsNone(snapshot["error"])
            refused = client.post(f"/api/runtime/projects/{runtime_id}/recover", json={"projectId": self.project_id})
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(refused.json()["code"], "WORKER_NOT_CRASHED")
            restarted = client.post("/api/apps/monkeyboard/start", params={"projectDir": str(self.project)})
            self.assertEqual(restarted.status_code, 202, restarted.text)
            self.wait_state(client, "monkeyarch", "running", project_dir=self.project)
            ready = self.wait_runtime(client, runtime_id, lambda row: row["projection"] == "ready" and row["workers"][0]["healthy"])
            self.assertNotEqual(ready["workers"][0]["instanceId"], original["instanceId"])
            self.assertEqual(ready["workers"][0]["desiredState"], "running")
            self.assertEqual(self.project_bytes(self.project), before)

    def test_idle_runtime_skips_history_scans_but_refreshes_on_request_and_crash(self):
        with self.hub(studio_web=self.web) as client:
            runtime_id = self.open_project(client)
            manager = client.app.state.runtimes
            original = self.read_runtime(client, runtime_id)["workers"][0]
            before = self.project_bytes(self.project)
            state_digest = self.fixture.runner_state_digest(self.repository, self.fixture.REFERENCE_RUN_ID)
            with patch.object(manager, "_read_retained", wraps=manager._read_retained) as scans:
                # Let the opening wake and ready transition finish, then cover
                # several normal status ticks with a healthy, idle real worker.
                time.sleep(1.2)
                idle_scans = scans.call_count
                time.sleep(3.2)
                self.assertEqual(scans.call_count, idle_scans, "Idle status polling rescanned retained history")
                operation_id = str(uuid4())
                proposed = self.proxy(client, runtime_id, "/api/proposals", "POST", operation_id=operation_id, json={
                    "projectId": self.project_id, "stateDigest": state_digest,
                    "sourceRunId": self.fixture.REFERENCE_RUN_ID, "targetComponentId": "portico",
                    "elementId": "portico-base", "utterance": "set height to 2.2",
                })
                self.assertEqual(proposed.status_code, 201, proposed.text)
                wait_for(lambda: scans.call_count > idle_scans,
                         "A forwarded mutation did not promptly refresh retained state", timeout=5)
                operation = next(row for row in self.read_runtime(client, runtime_id)["operations"]
                                 if row["operationId"] == operation_id)
                self.assertEqual(operation["status"], "completed")
                cold_scans = sum(call.kwargs.get("worker") is None for call in scans.call_args_list)
                owned = client.app.state.applications.supervisor._children[original["workerId"]].process
                os.kill(original["processId"], signal.SIGTERM)
                owned.wait(timeout=10)

                def crash_observed():
                    snapshot = self.read_runtime(client, runtime_id)
                    cold_read = sum(call.kwargs.get("worker") is None for call in scans.call_args_list) > cold_scans
                    return snapshot if snapshot["workers"][0]["state"] == "crashed" and snapshot["projection"] == "stale" and cold_read else None

                crashed = wait_for(crash_observed, "Idle runtime did not promptly observe and cold-read the crashed worker", timeout=5)
                self.assertIsNone(crashed["workers"][0]["processId"])
                self.assertEqual(crashed["workers"][0]["instanceId"], original["instanceId"])
                self.assertEqual(self.project_bytes(self.project), before)


if __name__ == "__main__":
    unittest.main()
