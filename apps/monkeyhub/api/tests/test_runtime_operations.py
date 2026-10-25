"""Hub operation recovery uses real Studio runs and committed P036 history."""

import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, RUNNER_RUN_RECEIPT
from archflow.project.refs import record_ref_from_uri
from project_runtime.binding import ProjectBinding, bound_project
from project_runtime.status import inspect_runtime
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from project_runtime.api.dto.runtime import runtime_dto
from monkeyhub_api.models import HubFailure
from monkeyhub_api.runtime.manager import ProjectRuntime, ProjectRuntimeManager, _still_there
from monkeyhub_api.attention import producers
from monkeyhub_api.runtime.operations import EXECUTING_LIMIT_S, LOST_AFTER_MISSES, RECHECK_LOST_S, OperationManager
from monkeyhub_api.runtime.workers import earlier_project_keys, project_key
from monkeyhub_api.runtime.worker_http import HttpResult

from test_monkeyhub_lifecycle import project_fixture


class OperationRecoveryTests(unittest.TestCase):
    def test_liveness_reads_status_without_copying_admission_history(self):
        manager = OperationManager("idle-project")
        oldest, _ = manager.admit(str(uuid4()), "POST", "/api/proposals", b"{}",
                                  retained=None, source="studio", session_id=None)
        for _ in range(60):
            recent, _ = manager.admit(str(uuid4()), "POST", "/api/proposals", b"{}",
                                      retained=None, source="studio", session_id=None)
            manager.replied(recent, HttpResult(200, b"{}", {}))
        with patch.object(manager, "_shown", side_effect=AssertionError("Liveness copied a display record")):
            self.assertTrue(manager._has_active(), "Old active work must outlive the recent display window")
            manager.interrupted(oldest, "lost reply")
            self.assertFalse(manager._has_active(), "Recovery needs attention, not busy polling")
            manager.reconcile({"candidates": [{"candidateId": "external", "status": "running"}]}, worker_alive=True)
            self.assertTrue(manager._has_active(), "An external active candidate also keeps polling responsive")
            manager.reconcile({"candidates": [{"candidateId": "external", "status": "succeeded"}]}, worker_alive=True)
            self.assertFalse(manager._has_active())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-operation-recovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = project_fixture()
        self.repository, _ = self.fixture.make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / self.fixture.PROJECT_ID, cad_export="off")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.jobs.shutdown)
        self.manager = OperationManager(self.fixture.PROJECT_ID)
        self.state_digest = self.fixture.runner_state_digest(self.repository, self.fixture.REFERENCE_RUN_ID)

    def snapshot(self, *, live=False):
        binding = bound_project(self.app.state) if live else ProjectBinding.open(self.settings)
        return runtime_dto(inspect_runtime(binding,
            jobs=self.app.state.jobs if live else None, run_ids=self.manager.candidate_ids())).model_dump(by_alias=True)

    def test_recipe_inspection_does_not_journal_notify_or_wake_but_import_does(self):
        from archflow.contracts.canonical import canonical_digest

        body = {"schema": "DrawingRecipeExport@1", "recipe": {"targetRef": "drawing:hatch",
                "strength": "strong_preference", "graphics": {"hatchSpacingMm": 3.0}},
                "source": {"decisionId": "synthetic-recipe", "revisionSha256": "a" * 64}}
        content = json.dumps({**body, "sha256": canonical_digest(body)})
        journal = self.root / "recipe-operations.json"
        operations = OperationManager(self.fixture.PROJECT_ID, journal_path=journal, project_dir=str(self.settings.project_dir))
        runtime = ProjectRuntime("recipe-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 operations, ProjectBinding.open(self.settings))
        manager = ProjectRuntimeManager(None, None)

        def studio(base, target, method, body, headers, *, timeout):
            response = self.client.request(method, target, content=body, headers=headers)
            return HttpResult(response.status_code, response.content, {"content-type": "application/json"})

        with patch.object(manager, "service", return_value=SimpleNamespace(url="http://isolated-worker")), \
             patch("monkeyhub_api.runtime.manager.request_http", side_effect=studio), patch.object(manager, "emit") as notify:
            for source, status in (("{}", 422), (content, 200)):
                payload = {"projectId": self.fixture.PROJECT_ID, "content": source}
                response = manager.forward(runtime, "/api/drawing-recipes/inspect", "POST", json.dumps(payload).encode(),
                                           {"content-type": "application/json", "idempotency-key": str(uuid4())})
                self.assertEqual(response.status, status, response.body)
                self.assertEqual(operations.records(), [])
                self.assertFalse(journal.exists())
                self.assertFalse(runtime.wake.is_set())
                notify.assert_not_called()
            payload.update(confirmed=True, sourceKind="human", rawLanguage="Import the inspected recipe.")
            response = manager.forward(runtime, "/api/drawing-recipes/import", "POST", json.dumps(payload).encode(),
                                       {"content-type": "application/json", "idempotency-key": str(uuid4())})
            self.assertEqual(response.status, 201, response.body)
            self.assertEqual(len(operations.records()), 1)
            self.assertTrue(journal.exists())
            self.assertTrue(runtime.wake.is_set())
            notify.assert_called()

    def admission(self, path, payload=None, *, operation_id=None, session_id=None):
        body = b"" if payload is None else json.dumps(payload, sort_keys=True).encode()
        admission, fresh = self.manager.admit(operation_id or str(uuid4()), "POST", path, body,
            retained=self.snapshot(), source="chat" if session_id else "studio", session_id=session_id)
        self.assertTrue(fresh)
        return admission, body

    def finished(self, job_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                self.assertEqual(job["status"], "succeeded", job)
                return job
            time.sleep(0.02)
        self.fail(f"Job {job_id} did not finish")

    def candidate(self, *, initial=None, session_id=None):
        payload = {"projectId": self.fixture.PROJECT_ID, "stateDigest": self.state_digest,
                   "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 2.2"}
        if initial:
            payload.update(sourceRunId=initial["candidateId"], sourceStageRef=initial["stageRef"],
                           stateDigest=initial["modelSource"]["stateDigest"])
        proposal = self.client.post("/api/proposals", json=payload)
        self.assertEqual(proposal.status_code, 201, proposal.text)
        path = f"/api/proposals/{proposal.json()['proposalId']}/candidate"
        admission, _ = self.admission(path, session_id=session_id)
        # Allocate the exact run before dispatch, then lose the HTTP reply.
        # Only id allocation is controlled; Studio executes and retains the run.
        with patch("project_runtime.api.routes.candidates._run_id", return_value=admission.record.candidateId):
            accepted = self.client.post(path)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.manager.interrupted(admission, "injected lost candidate response")
        self.finished(accepted.json()["jobId"])
        return admission, accepted.json()

    def model(self, run_id, state_digest):
        data = (ROOT / "services/project-runtime/tests/fixtures/model-source-a.3dm").read_bytes()
        response = self.client.post("/api/model-assets", json={"projectId": self.fixture.PROJECT_ID,
            "runId": run_id, "stateDigest": state_digest, "fileName": "model.3dm",
            "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["modelSource"]

    def acceptance(self):
        model = self.model(self.fixture.REFERENCE_RUN_ID, self.state_digest)
        response = self.client.post("/api/design-stages/initialize", json={"projectId": self.fixture.PROJECT_ID, "modelSource": model})
        self.assertEqual(response.status_code, 201, response.text)
        initial = response.json()
        _, candidate = self.candidate(initial=initial)
        candidate_id = candidate["candidateId"]
        state = self.client.get("/api/state", params={"run": candidate_id, "sourceStageRef": initial["stageRef"]}).json()
        self.model(candidate_id, state["stateDigest"])
        payload = {"projectId": self.fixture.PROJECT_ID, "branchId": "main", "expectedHeadStageRef": initial["stageRef"]}
        path = f"/api/candidates/{candidate_id}/accept"
        admission, _ = self.admission(path, payload)
        return initial, candidate_id, path, payload, admission

    def record(self, admission):
        return next(row for row in self.manager.records() if row.operationId == admission.record.operationId)

    def durable_manager(self, *, project_id=None, project_dir=None):
        return OperationManager(project_id or self.fixture.PROJECT_ID,
            project_dir=str(project_dir or self.settings.project_dir),
            journal_path=self.root / "hub-runtime/runtime/operations/project.json")

    def test_cold_start_refuses_unretained_dispatched_request_and_changed_key(self):
        self.manager = self.durable_manager()
        coordinator = ProjectRuntimeManager(None, None)
        runtime = ProjectRuntime("journal-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        worker = SimpleNamespace(url="http://127.0.0.1:1", instance_id="test-worker")
        operation_id = str(uuid4())
        body = b'{"privateRequestBody":"must never be stored"}'
        with patch.object(coordinator, "service", return_value=worker), \
             patch("monkeyhub_api.runtime.manager.request_http", side_effect=OSError("lost worker before retained output")) as forwarded:
            with self.assertRaises(HubFailure) as failure:
                coordinator.forward(runtime, "/api/program", "POST", body, {"idempotency-key": operation_id})
            self.assertEqual(failure.exception.error.code, "OPERATION_INTERRUPTED")
            self.assertEqual(forwarded.call_count, 1)
        self.assertNotIn("privateRequestBody", self.manager.journal_path.read_text(encoding="utf-8"))
        runtime.operations = self.durable_manager()  # No process-local state survives.
        with patch("monkeyhub_api.runtime.manager.request_http", side_effect=AssertionError("cold replay")) as forwarded:
            for repeated_body, expected in ((body, "OPERATION_NEEDS_RECOVERY"), (b"{}", "OPERATION_ID_CONFLICT")):
                with self.subTest(expected=expected), self.assertRaises(HubFailure) as failure:
                    coordinator.forward(runtime, "/api/program", "POST", repeated_body, {"idempotency-key": operation_id})
                self.assertEqual(failure.exception.error.code, expected)
            forwarded.assert_not_called()
        restored = runtime.operations.records()[0]
        self.assertEqual(restored.status, "needs_recovery")
        self.assertFalse(restored.committed)
        self.assertEqual(bound_project(self.app.state).run_ids(), (self.fixture.REFERENCE_RUN_ID,))

    def test_operation_log_failure_prevents_dispatch_and_does_not_admit_request(self):
        self.manager = self.durable_manager()
        coordinator = ProjectRuntimeManager(None, None)
        runtime = ProjectRuntime("journal-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        with patch("monkeyhub_api.runtime.operations.os.replace", side_effect=OSError("disk write failed")), \
             patch("monkeyhub_api.runtime.manager.request_http", side_effect=AssertionError("dispatch without durable identity")) as forwarded:
            with self.assertRaises(HubFailure) as failure:
                coordinator.forward(runtime, "/api/program", "POST", b"{}", {"idempotency-key": str(uuid4())})
            self.assertEqual(failure.exception.error.code, "OPERATION_LOG_UNAVAILABLE")
            forwarded.assert_not_called()
        self.assertEqual(self.manager.records(), [])
        self.assertFalse(self.manager.journal_path.exists())

    def test_operation_journal_does_not_copy_response_details_or_request_content(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/program", {"private": "private input text"})
        self.manager.replied(admission, HttpResult(422,
            b'{"detail":"Invalid private input text"}', {"content-type": "application/json"}))
        self.assertIn("private input text", self.record(admission).reason)
        self.assertNotIn("private input text", self.manager.journal_path.read_text(encoding="utf-8"))
        self.assertEqual(self.durable_manager().records()[0].status, "refused")

    def test_new_operation_keeps_its_admission_time_and_older_rows_have_none(self):
        self.manager = self.durable_manager()
        before = datetime.now(timezone.utc)
        admission, _ = self.admission("/api/drawings/sheets", {})
        admitted = self.record(admission).createdAt
        self.assertTrue(before <= datetime.fromisoformat(admitted) <= datetime.now(timezone.utc))
        self.assertEqual(self.durable_manager().records()[0].createdAt, admitted, "the time is kept with the journal row")
        # A journal written before admission times were kept still restores, without one.
        saved = json.loads(self.manager.journal_path.read_text(encoding="utf-8"))
        del saved["operations"][0]["record"]["createdAt"]
        self.manager.journal_path.write_text(json.dumps(saved), encoding="utf-8")
        self.assertIsNone(self.durable_manager().records()[0].createdAt)

    def test_dismissed_failure_stays_dismissed_after_restart_and_is_otherwise_unchanged(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/drawings/sheets", {"private": "private sheet request"})
        self.manager.replied(admission, HttpResult(500, b'{"code":"SHEET_FAILED","detail":"private refusal text"}', {}))
        before = self.record(admission)
        self.assertIsNone(before.acknowledgedAt)
        dismissed = self.manager.acknowledge(admission.record.operationId)
        self.assertTrue(dismissed.acknowledgedAt)
        self.assertEqual(dismissed.model_dump(exclude={"acknowledgedAt"}), before.model_dump(exclude={"acknowledgedAt"}),
                         "a dismissal changes no status, reason, order or result")
        self.assertEqual(self.record(admission).acknowledgedAt, dismissed.acknowledgedAt)
        self.assertEqual(self.manager.acknowledge(admission.record.operationId).acknowledgedAt, dismissed.acknowledgedAt,
                         "dismissing again keeps the first time")
        self.assertNotIn("private", self.manager.journal_path.read_text(encoding="utf-8"))
        restored = self.durable_manager().records()[0]
        self.assertEqual((restored.status, restored.acknowledgedAt), ("failed", dismissed.acknowledgedAt))
        # A journal kept before a dismissal named the status it read still restores it.
        saved = json.loads(self.manager.journal_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["acknowledged"], {admission.record.operationId: {
            "acknowledgedAt": dismissed.acknowledgedAt, "status": "failed"}})
        saved["acknowledged"] = {admission.record.operationId: dismissed.acknowledgedAt}
        self.manager.journal_path.write_text(json.dumps(saved), encoding="utf-8")
        self.assertEqual(self.durable_manager().records()[0].acknowledgedAt, dismissed.acknowledgedAt)

    def test_stale_operation_can_be_dismissed(self):
        admission, _ = self.admission("/api/proposals", {})
        self.manager.replied(admission, HttpResult(409, b'{"code":"PROPOSAL_BASE_STALE","proposalId":"prop-stale","detail":"The base moved."}', {}))
        self.assertEqual(self.record(admission).status, "stale")
        self.assertTrue(self.manager.acknowledge(admission.record.operationId).acknowledgedAt)
        self.assertTrue(self.record(admission).acknowledgedAt)

    def test_agent_withdraws_only_its_own_candidate_and_it_reads_back_as_its_act(self):
        """#404 F13: a withdrawal is the Hub Agent's recorded act, never the user's rejection."""
        managed = create_app(self.settings)
        managed.state.managed_instance_id = "hub-test"
        studio = TestClient(managed)
        self.addCleanup(studio.close)
        self.addCleanup(managed.state.jobs.shutdown)
        chat_id = str(uuid4())
        mine, _ = self.candidate(session_id=chat_id)
        other, _ = self.candidate()
        older, _ = self.candidate()
        mine, other, older = (row.record.candidateId for row in (mine, other, older))
        # A person's rejection, written as every record has been: it reads back exactly as before.
        rejected = self.client.post("/api/admissions", json={"projectId": self.fixture.PROJECT_ID, "task": {"kind": "ui"},
                                                             "results": [{"runId": older, "outcome": "rejected"}]})
        self.assertEqual(rejected.status_code, 201, rejected.text)
        self.assertEqual(set(rejected.json()), {"admissionRef", "admissionId", "projectId", "previousRevisionRef", "occurredAt", "actor",
                                                "messageSource", "rawLanguage", "task", "study", "results"})

        runtime = ProjectRuntime("withdraw-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        chats = SimpleNamespace(get=lambda session_id: SimpleNamespace(
            projectId=self.fixture.PROJECT_ID, projectDir=str(self.settings.project_dir), status="running"))
        coordinator = ProjectRuntimeManager(None, chats)
        message = {"sessionId": chat_id, "messageId": "message-3"}

        def admit(*results, **body):
            return json.dumps({"projectId": self.fixture.PROJECT_ID, "task": {"kind": "hub-chat"}, "messageSource": message,
                               "results": list(results), **body}).encode()

        def dispatch(base, target, method, body, headers, *, timeout):
            response = studio.request(method, target, content=body, headers=headers)
            return HttpResult(response.status_code, response.content, {"content-type": "application/json"})

        headers = {"content-type": "application/json", "x-monkey-chat": chat_id}
        before = len(self.manager.records())
        with patch.object(coordinator, "service", return_value=SimpleNamespace(url="http://managed", instance_id="hub-test")), \
             patch("monkeyhub_api.runtime.manager.request_http", side_effect=dispatch):
            with self.assertRaises(HubFailure) as foreign:
                coordinator.forward(runtime, "/api/admissions", "POST", admit({"runId": other, "outcome": "withdrawn"}),
                                    {**headers, "idempotency-key": str(uuid4())})
            self.assertEqual((foreign.exception.status, foreign.exception.error.code), (409, "CANDIDATE_NOT_THIS_CHATS"))
            self.assertEqual(len(self.manager.records()), before, "a refused withdrawal admits nothing")
            # A user's rejection still needs their words, as before.
            unworded = coordinator.forward(runtime, "/api/admissions", "POST", admit({"runId": other, "outcome": "rejected"}),
                                           {**headers, "idempotency-key": str(uuid4())})
            self.assertEqual(unworded.status, 422, unworded.body)
            self.assertIn("rawLanguage", unworded.json()["detail"])
            withdrawn = coordinator.forward(runtime, "/api/admissions", "POST",
                                            admit({"runId": mine, "outcome": "withdrawn", "reason": "the taller one reads better"}),
                                            {**headers, "idempotency-key": str(uuid4())})
        self.assertEqual(withdrawn.status, 201, withdrawn.body)
        record = withdrawn.json()
        self.assertEqual(record["actor"]["origin"], "hub-agent", "recorded as the Agent's act")
        self.assertEqual((record["messageSource"], record["rawLanguage"]), (message, None), "with the message it acted on, no user words")
        self.assertEqual([(row["runId"], row["outcome"]) for row in record["results"]], [(mine, "withdrawn")])
        # A person cannot withdraw; they reject.
        person = self.client.post("/api/admissions", json={"projectId": self.fixture.PROJECT_ID, "task": {"kind": "ui"},
                                                           "results": [{"runId": other, "outcome": "withdrawn"}]})
        self.assertEqual((person.status_code, person.json()["code"]), (422, "ADMISSION_INVALID"))

        # Read back: gone from the pool, retained on request, and never shown as the architect's rejection.
        history = lambda **params: {row["candidateId"]: row for row in studio.get("/api/design-history", params=params).json()["candidates"]}
        self.assertNotIn(mine, history())
        self.assertEqual((history(include="rejected")[mine]["outcome"], history(include="rejected")[older]["outcome"]),
                         ("withdrawn", "rejected"))
        listed = studio.get("/api/admissions", params={"include": "rejected"}).json()["admissions"]
        self.assertEqual({row["admissionId"] for row in listed}, {rejected.json()["admissionId"], record["admissionId"]})
        self.assertEqual(next(row for row in listed if row["admissionId"] == rejected.json()["admissionId"]), rejected.json())
        lines = {line["runId"]: line["admission"] for line in studio.get("/api/worktrees").json()["lines"] if line["kind"] == "result"}
        self.assertEqual((lines[mine], lines[older]), ("superseded", "rejected"))

    @staticmethod
    def unfinished(records):
        """What ChatShell's "did not finish" notice stands for (chatProcess.unfinishedOperations)."""
        return [row for row in records if (row.status in {"failed", "stale"} and not row.acknowledgedAt)
                or (row.status == "needs_recovery" and not (row.recoverable is False and row.acknowledgedAt))]

    def test_refusal_before_admission_is_finished_and_leaves_no_notice(self):
        # #404 F10: a validation failure or stale base the caller already read is not unfinished work.
        self.manager = self.durable_manager()
        for path, status, body in (
                ("/api/proposals", 422, b'{"code":"PROPOSAL_INVALID","detail":"height must be positive"}'),
                ("/api/proposals", 409, b'{"code":"STALE_BASE","detail":"Re-read /api/state."}'),
                ("/api/proposals/p-1/candidate", 409, b'{"code":"STALE_BASE","detail":"The base moved."}')):
            with self.subTest(path=path, status=status):
                admission, _ = self.admission(path, {})
                self.manager.replied(admission, HttpResult(status, body, {}))
                record = self.record(admission)
                self.assertEqual(record.status, "refused")
                self.assertEqual(record.reason, json.loads(body)["detail"], "the refusal stays in the record")
                self.assertNotIn(record.candidateId, self.manager.candidate_ids())
        self.assertEqual(self.unfinished(self.manager.records()), [])
        self.assertEqual({row.status for row in self.durable_manager().records()}, {"refused"})
        with self.assertRaises(HubFailure) as refusal:
            self.manager.acknowledge(admission.record.operationId)
        self.assertEqual(refusal.exception.error.code, "OPERATION_NOT_ACKNOWLEDGEABLE", "nothing to dismiss")

    def test_refusal_answered_through_forward_emits_refused_and_no_notice(self):
        runtime = ProjectRuntime("refusal-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        coordinator = ProjectRuntimeManager(None, None)
        answer = HttpResult(422, b'{"code":"SKETCH_INVALID","detail":"closed profile repeats its first point"}',
                            {"content-type": "application/json"})
        with patch.object(coordinator, "service", return_value=SimpleNamespace(url="http://isolated-worker")), \
             patch("monkeyhub_api.runtime.manager.request_http", return_value=answer), patch.object(coordinator, "emit") as notify:
            response = coordinator.forward(runtime, "/api/proposals", "POST", b"{}", {"idempotency-key": str(uuid4())})
        self.assertEqual(response.status, 422)
        self.assertIn("operation/refused", [call.args[0] for call in notify.call_args_list])
        self.assertEqual([row.status for row in self.manager.records()], ["refused"])
        self.assertEqual(self.unfinished(self.manager.records()), [])

    def test_failure_after_admission_still_leaves_its_notice(self):
        for status, body, expected in (
                (500, b'{"code":"SEAT_FAILED","detail":"the seat crashed"}', "failed"),
                (409, b'{"code":"STALE_BASE","proposalId":"prop-made","detail":"moved"}', "stale"),
                (409, b'{"code":"CANDIDATE_ALREADY_RETAINED","detail":"exists"}', "failed")):
            with self.subTest(status=status, expected=expected):
                admission, _ = self.admission("/api/proposals", {})
                self.manager.replied(admission, HttpResult(status, body, {}))
                self.assertEqual(self.record(admission).status, expected)
                self.assertIn(admission.record.operationId, [row.operationId for row in self.unfinished(self.manager.records())])
        lost, _ = self.admission("/api/proposals/unexecuted/candidate")
        self.manager.interrupted(lost, "lost reply")
        self.assertIn(lost.record.operationId, [row.operationId for row in self.unfinished(self.manager.records())])

    def test_lost_candidate_reply_stays_until_a_read_finds_no_run_to_recover(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/proposals/unexecuted/candidate")
        self.manager.interrupted(admission, "lost reply")
        # Unread, the run this request named may yet be retained with its receipt.
        self.assertEqual((self.record(admission).status, self.record(admission).recoverable), ("needs_recovery", True))
        for operation_id, code in ((admission.record.operationId, "OPERATION_NOT_ACKNOWLEDGEABLE"),
                                   (str(uuid4()), "OPERATION_NOT_FOUND")):
            with self.subTest(code=code), self.assertRaises(HubFailure) as refusal:
                self.manager.acknowledge(operation_id)
            self.assertEqual(refusal.exception.error.code, code)
        self.assertNotIn("acknowledged", json.loads(self.manager.journal_path.read_text(encoding="utf-8")))
        restored = self.durable_manager()
        self.assertEqual([(row.status, row.recoverable, row.acknowledgedAt) for row in restored.records()],
                         [("needs_recovery", True, None)], "a restarted Hub has read nothing yet either")
        # The project retains no such run, and nothing will run that request again.
        restored.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual([(row.status, row.recoverable) for row in restored.records()], [("needs_recovery", False)])
        dismissed = restored.acknowledge(admission.record.operationId)
        self.assertEqual((dismissed.status, dismissed.recoverable), ("needs_recovery", False))
        self.assertTrue(dismissed.acknowledgedAt)
        self.assertEqual(json.loads(self.manager.journal_path.read_text(encoding="utf-8"))["acknowledged"],
                         {admission.record.operationId: {"acknowledgedAt": dismissed.acknowledgedAt, "status": "needs_recovery"}})
        # After another restart it stays dismissed before and after the next read.
        self.manager = self.durable_manager()
        for read in (False, True):
            if read:
                self.manager.reconcile(self.snapshot(), worker_alive=False)
            with self.subTest(read=read):
                row = self.record(admission)
                self.assertEqual((row.status, row.recoverable, row.acknowledgedAt),
                                 ("needs_recovery", False, dismissed.acknowledgedAt))
        self.assertEqual(bound_project(self.app.state).run_ids(), (self.fixture.REFERENCE_RUN_ID,))

    def test_interrupted_request_that_named_no_run_cannot_be_recovered_and_can_be_dismissed(self):
        self.manager = self.durable_manager()
        proposal, _ = self.admission("/api/proposals", {})
        sheet, _ = self.admission("/api/drawings/sheets", {})
        for admission in (proposal, sheet):
            self.manager.interrupted(admission, "lost reply")
            # No run was named, so no read is needed to know nothing retained can resolve it.
            self.assertEqual((self.record(admission).status, self.record(admission).recoverable), ("needs_recovery", False))
        before = self.record(proposal)
        dismissed = self.manager.acknowledge(proposal.record.operationId)
        self.assertTrue(dismissed.acknowledgedAt)
        self.assertEqual(dismissed.model_dump(exclude={"acknowledgedAt"}), before.model_dump(exclude={"acknowledgedAt"}),
                         "a dismissal changes no status, reason, order or result")
        self.assertEqual(self.manager.acknowledge(proposal.record.operationId).acknowledgedAt, dismissed.acknowledgedAt,
                         "dismissing again keeps the first time")
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual(self.record(proposal).acknowledgedAt, dismissed.acknowledgedAt)
        restarted = self.durable_manager()
        rows = restarted.records()
        # Dismissed, it no longer holds its place ahead of finished requests.
        self.assertEqual([row.operationId for row in rows], [sheet.record.operationId, proposal.record.operationId])
        self.assertEqual([(row.status, row.recoverable, row.acknowledgedAt) for row in rows],
                         [("needs_recovery", False, None), ("needs_recovery", False, dismissed.acknowledgedAt)])

    def test_dismissal_of_an_unrecoverable_operation_ends_when_it_reads_otherwise(self):
        admission, _ = self.admission("/api/program", {})
        operation_id, candidate_id = admission.record.operationId, admission.record.candidateId
        self.manager.interrupted(admission, "lost reply")

        def read(**candidate):
            self.manager.reconcile({"candidates": [{"candidateId": candidate_id, "status": "needs_recovery", **candidate}],
                                    "jobs": [], "stages": [], "branches": []}, worker_alive=False)
            row = self.record(admission)
            return row.status, row.recoverable, row.acknowledgedAt

        self.assertEqual(read(), ("needs_recovery", False, None))
        dismissed = self.manager.acknowledge(operation_id).acknowledgedAt
        # Its run turns up with a receipt that does not verify yet: recoverable after all.
        receipt = f"project://{self.fixture.PROJECT_ID}/runs/{candidate_id}/receipt.json"
        self.assertEqual(read(receiptRef=receipt), ("needs_recovery", True, None))
        with self.assertRaises(HubFailure) as refusal:
            self.manager.acknowledge(operation_id)
        self.assertEqual(refusal.exception.error.code, "OPERATION_NOT_ACKNOWLEDGEABLE")
        self.assertEqual(read(), ("needs_recovery", False, dismissed), "the receipt is gone again")
        # A failure is news the dismissal never read.
        self.assertEqual(read(receiptRef=receipt, status="failed", error="incomplete seat execution"),
                         ("failed", False, None))

    def test_dismissal_that_cannot_be_saved_leaves_the_notice(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/drawings/sheets", {})
        self.manager.replied(admission, HttpResult(500, b'{"detail":"failed"}', {}))
        with patch("monkeyhub_api.runtime.operations.os.replace", side_effect=OSError("disk write failed")), \
             self.assertRaises(HubFailure) as failure:
            self.manager.acknowledge(admission.record.operationId)
        self.assertEqual(failure.exception.error.code, "OPERATION_LOG_UNAVAILABLE")
        self.assertIsNone(self.record(admission).acknowledgedAt)
        self.assertIsNone(self.durable_manager().records()[0].acknowledgedAt)

    def test_dismissed_retained_failure_is_kept_by_id_until_it_reads_otherwise(self):
        self.manager = self.durable_manager()
        retained = {"candidates": [{"candidateId": "old-run", "status": "failed", "error": "incomplete"}],
                    "jobs": [], "stages": [], "branches": []}
        self.manager.reconcile(retained, worker_alive=False)
        dismissed = self.manager.acknowledge("candidate:old-run")
        self.assertEqual((dismissed.source, dismissed.status), ("retained", "failed"))
        restarted = self.durable_manager()
        restarted.reconcile(retained, worker_alive=False)
        observed = lambda manager: next(row for row in manager.records() if row.operationId == "candidate:old-run")
        self.assertEqual(observed(restarted).acknowledgedAt, dismissed.acknowledgedAt)
        # The same run read later as needing recovery is reported again, undismissed.
        restarted.reconcile({**retained, "candidates": [{"candidateId": "old-run", "status": "needs_recovery"}]}, worker_alive=False)
        self.assertEqual((observed(restarted).status, observed(restarted).acknowledgedAt), ("needs_recovery", None))
        # Retained without a receipt, nothing can recover it, so that reading can be dismissed in turn;
        # one with its receipt can still be verified and cannot.
        self.assertFalse(observed(restarted).recoverable)
        self.assertTrue(restarted.acknowledge("candidate:old-run").acknowledgedAt)
        restarted.reconcile({**retained, "candidates": [{"candidateId": "old-run", "status": "needs_recovery",
                                                         "receiptRef": "project://old-run/receipt"}]}, worker_alive=False)
        self.assertEqual((observed(restarted).recoverable, observed(restarted).acknowledgedAt), (True, None))
        with self.assertRaises(HubFailure) as refusal:
            restarted.acknowledge("candidate:old-run")
        self.assertEqual(refusal.exception.error.code, "OPERATION_NOT_ACKNOWLEDGEABLE")

    def test_dismiss_route_is_bound_to_its_runtime_and_project(self):
        from monkeyhub_api.projects import _project
        from monkeyhub_api.app.composition import HubSettings, create_app as create_hub
        hub = create_hub(HubSettings(runtime_root=self.root / "hub-app-runtime"))
        client = TestClient(hub, base_url="http://127.0.0.1:8790")  # Without its lifespan, nothing is started.
        self.addCleanup(client.close)
        project_id, project_dir = _project(str(self.settings.project_dir))
        self.manager = self.durable_manager()
        runtime = ProjectRuntime("route-runtime", project_id, project_dir, self.manager, ProjectBinding.open(self.settings))
        hub.state.runtimes._projects[runtime.runtime_id] = runtime
        failed, _ = self.admission("/api/drawings/sheets", {})
        self.manager.replied(failed, HttpResult(500, b'{"detail":"failed"}', {}))
        waiting, _ = self.admission("/api/proposals/unexecuted/candidate")
        self.manager.interrupted(waiting, "lost reply")
        lost, _ = self.admission("/api/proposals", {})
        self.manager.interrupted(lost, "lost reply")

        def dismiss(operation_id, **body):
            return client.post(f"/api/runtime/operations/{operation_id}/acknowledge",
                               json={"runtimeId": runtime.runtime_id, "projectId": project_id, **body})

        def listed():
            return {row["operationId"]: row for row in client.get(f"/api/runtime/projects/{runtime.runtime_id}").json()["operations"]}

        rows = listed()
        self.assertEqual([(rows[row.record.operationId]["status"], rows[row.record.operationId]["recoverable"])
                          for row in (failed, waiting, lost)],
                         [("failed", False), ("needs_recovery", True), ("needs_recovery", False)])
        answered = dismiss(failed.record.operationId)
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual((answered.json()["operationId"], answered.json()["status"]), (failed.record.operationId, "failed"))
        self.assertTrue(answered.json()["acknowledgedAt"])
        self.assertEqual(listed()[failed.record.operationId]["acknowledgedAt"], answered.json()["acknowledgedAt"])
        self.assertIsNone(listed()[waiting.record.operationId]["acknowledgedAt"])
        unrecoverable = dismiss(lost.record.operationId)
        self.assertEqual(unrecoverable.status_code, 200, unrecoverable.text)
        self.assertEqual((unrecoverable.json()["status"], unrecoverable.json()["recoverable"]), ("needs_recovery", False))
        self.assertEqual(listed()[lost.record.operationId]["acknowledgedAt"], unrecoverable.json()["acknowledgedAt"])
        for operation_id, body, status, code in (
                (waiting.record.operationId, {}, 409, "OPERATION_NOT_ACKNOWLEDGEABLE"),
                (str(uuid4()), {}, 404, "OPERATION_NOT_FOUND"),
                (failed.record.operationId, {"projectId": "another-project"}, 409, "PROJECT_MISMATCH"),
                (failed.record.operationId, {"runtimeId": "another-runtime"}, 404, "RUNTIME_NOT_FOUND")):
            with self.subTest(code=code):
                refused = dismiss(operation_id, **body)
                self.assertEqual((refused.status_code, refused.json()["code"]), (status, code))
        self.assertEqual(dismiss(failed.record.operationId, note="extra").status_code, 422)

    def test_cold_operation_binding_refuses_other_project_or_same_id_at_other_path(self):
        self.manager = self.durable_manager()
        self.admission("/api/program", {})
        for binding in ({"project_id": "another-project"}, {"project_dir": self.root / "another-copy"}):
            with self.subTest(binding=binding), self.assertRaises(HubFailure) as failure:
                self.durable_manager(**binding)
            self.assertEqual(failure.exception.error.code, "OPERATION_LOG_INVALID")

    def test_cold_start_after_commit_reconciles_from_p036_without_reexecution(self):
        self.manager = self.durable_manager()
        _, _, path, payload, admission = self.acceptance()
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.manager.interrupted(admission, "lost committed response")
        stages_before = self.snapshot()["stages"]
        self.manager = self.durable_manager()
        self.assertFalse(self.record(admission).committed)
        self.assertIsNone(self.record(admission).resultDigest)
        with patch("project_runtime.application.candidate.execute_candidate", side_effect=AssertionError("cold replay")):
            self.manager.reconcile(self.snapshot(), worker_alive=False)
        record = self.record(admission)
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.committed)
        self.assertEqual(record.resultDigest, response.json()["recordDigest"])
        self.assertEqual(self.snapshot()["stages"], stages_before)

    def test_cold_candidate_order_uses_admissions_and_verified_receipts_without_jobs(self):
        self.manager = self.durable_manager()
        older, _ = self.candidate()
        newer, _ = self.candidate()
        unfinished, _ = self.admission("/api/proposals/unexecuted/candidate")
        refused, _ = self.admission("/api/program", {})
        self.manager.replied(refused, HttpResult(422, b'{"detail":"refused"}', {}))
        journal_before = self.manager.journal_path.read_bytes()
        runs_before = bound_project(self.app.state).run_ids()
        head_before = self.repository.read_head()

        cold_app = create_app(self.settings)
        self.addCleanup(cold_app.state.jobs.shutdown)
        cold = self.durable_manager()
        self.assertTrue(all(row.resultDigest is None for row in cold.records()))
        with TestClient(cold_app) as client, patch(
            "project_runtime.application.candidate.execute_candidate", side_effect=AssertionError("cold replay"),
        ):
            response = client.get("/api/runtime", params=[("candidateId", row.record.candidateId)
                for row in (older, newer, unfinished)])
            self.assertEqual(response.status_code, 200, response.text)
            retained = response.json()
            self.assertEqual(retained["jobs"], [], "a fresh Studio has no process jobs or job timestamps")
            cold.reconcile(retained, worker_alive=False)
        records = {row.operationId: row for row in cold.records()}
        successful = [row for row in records.values() if row.status == "completed" and row.resultDigest]
        self.assertEqual([row.candidateId for row in sorted(successful, key=lambda row: row.admissionSequence)],
                         [older.record.candidateId, newer.record.candidateId])
        self.assertEqual(records[unfinished.record.operationId].status, "needs_recovery")
        self.assertEqual(records[refused.record.operationId].status, "refused")
        self.assertEqual([records[row.record.operationId].admissionSequence for row in (older, newer, unfinished, refused)],
                         [1, 2, 3, 4])
        for row in successful:
            candidate = next(item for item in retained["candidates"] if item["candidateId"] == row.candidateId)
            self.assertTrue(candidate["receiptRef"])
            self.assertEqual(row.resultDigest, candidate["resultStateDigest"])
        self.assertEqual(self.manager.journal_path.read_bytes(), journal_before, "reading order does not rewrite the journal")
        self.assertNotIn(b"admissionSequence", journal_before, "order is projected from the existing array, not newly persisted")
        self.assertEqual(bound_project(self.app.state).run_ids(), runs_before)
        self.assertEqual(self.repository.read_head(), head_before)

    def test_lost_candidate_reply_recovers_exact_retained_result_without_resubmission(self):
        admission, accepted = self.candidate()
        before_runs = bound_project(self.app.state).run_ids()
        retained = self.snapshot()
        candidate = next(row for row in retained["candidates"] if row["candidateId"] == accepted["candidateId"])
        with patch("project_runtime.application.candidate.execute_candidate", side_effect=AssertionError("replayed")):
            self.manager.reconcile(retained, worker_alive=False)
            repeated, fresh = self.manager.admit(admission.record.operationId, *admission.signature[:2], b"",
                retained=retained, source="studio", session_id=None)
        self.assertFalse(fresh)
        self.assertIs(repeated, admission)
        record = self.record(admission)
        self.assertEqual(record.status, "completed")
        self.assertFalse(record.committed)
        self.assertEqual(record.baseDigest, candidate["baseStateDigest"])
        self.assertEqual(record.resultDigest, candidate["resultStateDigest"])
        delta = bound_project(self.app.state).candidate_delta(record.candidateId)
        self.assertEqual(record.baseDigest, delta["operator"]["base_state_digest"])
        self.assertEqual(candidate["resultRecordDigest"], delta["result_record_digest"])
        self.assertEqual(bound_project(self.app.state).run_ids(), before_runs)
        restarted = OperationManager(self.fixture.PROJECT_ID)
        restarted.reconcile(retained, worker_alive=False)
        observed = next(row for row in restarted.records() if row.candidateId == record.candidateId)
        self.assertEqual(observed.status, "completed")
        self.assertEqual(observed.source, "retained")
        self.assertIsNone(observed.admissionSequence, "a receipt without an admission journal cannot invent request order")

    def test_a_refresh_reads_the_recent_runs_in_one_page_and_smaller_ones_only_when_it_does_not_finish(self):
        # S15: fifty reads of one run each cost a 319-run project 3.3 s a refresh; one page of fifty, 0.08 s.
        first, first_result = self.candidate()
        second, second_result = self.candidate()
        binding = bound_project(self.app.state)
        snapshot = self.snapshot(live=True)
        self.manager.reconcile(snapshot, worker_alive=True)
        runtime = ProjectRuntime("paged-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, binding, retained=snapshot)
        manager = ProjectRuntimeManager(None, None)
        worker = SimpleNamespace(url="http://owned-worker/")
        limits, refuse_above = [], [None]

        def read(_url, path, **_kwargs):
            limit = int(parse_qs(urlsplit(path).query)["limit"][0])
            limits.append(limit)
            if refuse_above[0] is not None and limit > refuse_above[0]:
                raise TimeoutError("this page does not finish within the budget")
            response = self.client.get(path)
            return HttpResult(response.status_code, response.content, dict(response.headers))

        expected = {first_result["candidateId"], second_result["candidateId"]}
        with patch("monkeyhub_api.runtime.manager.request_http", side_effect=read):
            paged = manager._read_retained(runtime, worker=worker)
            self.assertEqual(limits, [50], "the recent window is one request")
            self.assertEqual({row["candidateId"] for row in paged["candidates"]}, expected)
            self.assertTrue(all(row["status"] == "completed" for row in paged["candidates"]))

            limits.clear()
            refuse_above[0] = 10
            smaller = manager._read_retained(runtime, worker=worker)
            self.assertEqual(limits[:2], [50, 10], "a page that does not finish is read again in smaller ones")
            self.assertEqual({row["candidateId"] for row in smaller["candidates"]}, expected)
            limits.clear()
            manager._read_retained(runtime, worker=worker)
            self.assertNotIn(50, limits, "for a while the project starts from the page that finished")
            runtime.retained_page_until = 0.0
            limits.clear()
            refuse_above[0] = None
            manager._read_retained(runtime, worker=worker)
            self.assertEqual(limits, [50], "then the largest page is tried again")

    def test_runtime_pages_revalidate_completed_and_new_candidates_without_a_full_history_probe(self):
        first, first_result = self.candidate()
        second, second_result = self.candidate()
        binding = bound_project(self.app.state)
        snapshot = self.snapshot(live=True)
        self.manager.reconcile(snapshot, worker_alive=True)
        runtime = ProjectRuntime("bounded-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, binding, retained=snapshot)
        manager = ProjectRuntimeManager(None, None)
        worker = SimpleNamespace(url="http://owned-worker/")
        queried = []

        def bounded_read(_url, path, **_kwargs):
            query = parse_qs(urlsplit(path).query)
            # Model the reported failure: a broad history response exceeds
            # the transport budget, while the existing exact API can finish.
            if query.get("limit") != ["1"] or len(query.get("candidateId", [])) > 1:
                raise TimeoutError("a full project history cannot finish in one probe")
            response = self.client.get(path)
            queried.extend(row["candidateId"] for row in response.json()["candidates"])
            return HttpResult(response.status_code, response.content, dict(response.headers))

        with patch("monkeyhub_api.runtime.manager.request_http", side_effect=bounded_read):
            runtime.retained = manager._read_retained(runtime, worker=worker)
            self.assertIn(first_result["candidateId"], queried)
            self.assertIn(second_result["candidateId"], queried)
            self.assertEqual({row["candidateId"] for row in runtime.retained["candidates"]},
                             {first_result["candidateId"], second_result["candidateId"]})
            self.assertTrue(all(row["status"] == "completed" for row in runtime.retained["candidates"]))

            # A new run must be found even if its operation is not in this
            # Hub's journal; the project remains the source of its identity.
            third, third_result = self.candidate()
            self.manager._operations.pop(third.record.operationId)
            queried.clear()
            runtime.retained = manager._read_retained(runtime, worker=worker)
            self.assertIn(third_result["candidateId"], queried)
            self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                  if row["candidateId"] == third_result["candidateId"]), "completed")

            # Every completed run is still checked. Losing its retained
            # evidence must replace the old completed observation, and the
            # same run can complete again when that evidence becomes readable.
            row = next(row for row in runtime.retained["candidates"] if row["candidateId"] == second_result["candidateId"])
            receipt_path = binding.repository.layout.resolve_record(record_ref_from_uri(row["receiptRef"], self.fixture.PROJECT_ID))
            receipt_bytes = receipt_path.read_bytes()
            receipt_path.unlink()
            try:
                runtime.retained = manager._read_retained(runtime, worker=worker)
                self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                      if row["candidateId"] == second_result["candidateId"]), "needs_recovery")
            finally:
                receipt_path.write_bytes(receipt_bytes)
            runtime.retained = manager._read_retained(runtime, worker=worker)
            self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                  if row["candidateId"] == second_result["candidateId"]), "completed")

            # Cold/recovery reads do not reuse even a formerly completed row.
            row = next(row for row in runtime.retained["candidates"] if row["candidateId"] == first_result["candidateId"])
            receipt_path = binding.repository.layout.resolve_record(record_ref_from_uri(row["receiptRef"], self.fixture.PROJECT_ID))
            receipt_bytes = receipt_path.read_bytes()
            receipt_path.unlink()
            try:
                cold = manager._read_retained(runtime)
                self.assertEqual(next(row["status"] for row in cold["candidates"]
                                      if row["candidateId"] == first_result["candidateId"]), "needs_recovery")
            finally:
                receipt_path.write_bytes(receipt_bytes)

    def test_slow_projection_preserves_verified_candidate_and_rebuilds_for_a_new_worker(self):
        admission, accepted = self.candidate()
        binding = bound_project(self.app.state)
        runtime = ProjectRuntime("projection-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, binding)
        worker = SimpleNamespace(url="http://owned-worker/", instance_id="worker-a", state="ready", healthy=True)
        applications = SimpleNamespace(worker_snapshots=lambda **_kwargs: (worker,), set_busy=lambda **_kwargs: None)
        manager = ProjectRuntimeManager(applications, None)
        state_reads = []

        def read(_url, path, **_kwargs):
            if path == "/api/state":
                state_reads.append(worker.instance_id)
                if len(state_reads) == 1:
                    raise TimeoutError("projection still rebuilding")
            response = self.client.get(path)
            return HttpResult(response.status_code, response.content, dict(response.headers))

        with patch("monkeyhub_api.runtime.manager.request_http", side_effect=read):
            with self.assertRaises(TimeoutError):
                manager.refresh(runtime)
            self.assertNotEqual(runtime.projection, "ready")
            self.assertEqual(self.record(admission).status, "completed")
            self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                  if row["candidateId"] == accepted["candidateId"]), "completed")
            manager.refresh(runtime)
            self.assertEqual(runtime.projection, "ready")
            manager.refresh(runtime)
            self.assertEqual(state_reads, ["worker-a", "worker-a"], "same worker and base reuse the ready projection")
            worker.instance_id = "worker-b"
            manager.refresh(runtime)
            self.assertEqual(runtime.projection, "ready")
            self.assertEqual(state_reads[-1], "worker-b", "a new process must rebuild its projection")

    def test_http_acceptance_and_live_process_do_not_prove_a_candidate_finished(self):
        admission, _ = self.admission("/api/proposals/unexecuted/candidate")
        self.manager.replied(admission, HttpResult(202, json.dumps({"jobId": "no-such-job",
            "candidateId": admission.record.candidateId, "status": "queued"}).encode(), {}))
        self.manager.reconcile(self.snapshot(), worker_alive=True)
        self.assertNotEqual(self.record(admission).status, "completed")
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual(self.record(admission).status, "needs_recovery")
        self.assertFalse(self.record(admission).recoverable, "no run was retained, and none will be")
        self.assertEqual(bound_project(self.app.state).run_ids(), (self.fixture.REFERENCE_RUN_ID,))

    def test_truncated_http_response_is_interrupted_and_duplicate_is_not_dispatched(self):
        self.manager = self.durable_manager()
        received = []

        class TruncatedResponse(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                self.send_response(202)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"{")
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), TruncatedResponse)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        manager = ProjectRuntimeManager(None, None)
        runtime = ProjectRuntime("truncated-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        worker = SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", instance_id="truncated-worker")
        operation_id = str(uuid4())
        try:
            with patch.object(manager, "service", return_value=worker):
                for expected in ("OPERATION_INTERRUPTED", "OPERATION_NEEDS_RECOVERY"):
                    if expected == "OPERATION_NEEDS_RECOVERY":
                        self.manager = self.durable_manager()
                        runtime.operations = self.manager
                    with self.assertRaises(HubFailure) as failure:
                        manager.forward(runtime, "/api/program", "POST", b"{}", {"idempotency-key": operation_id})
                    self.assertEqual(failure.exception.error.code, expected)
            self.manager.reconcile(self.snapshot(), worker_alive=False)
            record = next(row for row in self.manager.records() if row.operationId == operation_id)
            self.assertEqual(record.status, "needs_recovery")
            self.assertFalse(record.committed)
            self.assertEqual(received, [b"{}"])
            self.assertEqual(bound_project(self.app.state).run_ids(), (self.fixture.REFERENCE_RUN_ID,))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_same_operation_id_refuses_changed_payload_method_or_path(self):
        admission, body = self.admission("/api/proposals", {"stateDigest": self.state_digest})
        for method, path, changed in (("POST", "/api/proposals", b'{}'),
                                     ("PUT", "/api/proposals", body),
                                     ("POST", "/api/program", body)):
            with self.subTest(method=method, path=path), self.assertRaises(HubFailure) as failure:
                self.manager.admit(admission.record.operationId, method, path, changed,
                    retained=self.snapshot(), source="studio", session_id=None)
            self.assertEqual(failure.exception.error.code, "OPERATION_ID_CONFLICT")

    def test_lost_reply_after_commit_is_reconciled_from_reachable_stage(self):
        initial, candidate_id, path, payload, admission = self.acceptance()
        before_head = self.repository.read_head()
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        committed = response.json()
        self.manager.interrupted(admission, "injected response loss after branch CAS")
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        record = self.record(admission)
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.committed)
        self.assertEqual(record.resultDigest, committed["recordDigest"])
        self.assertIsNone(record.resultRevision)
        self.assertEqual(self.repository.read_head(), before_head)
        self.assertEqual(self.client.post(path, json=payload).json(), committed)
        self.assertEqual(len(self.snapshot()["stages"]), 2)
        cold = OperationManager(self.fixture.PROJECT_ID)
        retained = self.snapshot()
        cold.reconcile(retained, worker_alive=False)
        restored = next(row for row in cold.records() if row.candidateId == candidate_id)
        candidate = next(row for row in retained["candidates"] if row["candidateId"] == candidate_id)
        self.assertTrue(restored.committed)
        self.assertEqual(restored.baseRevision, candidate["base"]["version"])
        self.assertEqual(restored.baseRecordDigest, candidate["baseRecordDigest"])
        self.assertEqual(restored.resultDigest, candidate["resultStateDigest"])
        for wrong in ({**payload, "branchId": "different-branch"},
                      {**payload, "expectedHeadStageRef": committed["stageRef"]}):
            other, _ = self.admission(path, wrong)
            self.manager.interrupted(other, "injected unknown response")
            self.manager.reconcile(self.snapshot(), worker_alive=False)
            self.assertEqual(self.record(other).status, "needs_recovery")
            self.assertFalse(self.record(other).committed)

    def test_failure_before_commit_excludes_prepared_stage_even_with_complete_candidate(self):
        initial, candidate_id, path, payload, admission = self.acceptance()
        with patch.object(bound_project(self.app.state).repository, "compare_and_swap_design_branch",
                          side_effect=OSError("injected interruption before branch CAS")):
            with self.assertRaises(OSError):
                self.client.post(path, json=payload)
        self.manager.interrupted(admission, "worker exited before commit")
        review = self.repository.list_json(run=self.repository.load_run(candidate_id),
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=candidate_id))
        self.assertTrue(any(ref.record_kind == DESIGN_STAGE for ref in review))
        retained = self.snapshot()
        self.assertEqual([stage["stageRef"] for stage in retained["stages"]], [initial["stageRef"]])
        self.manager.reconcile(retained, worker_alive=False)
        self.assertEqual(self.record(admission).status, "needs_recovery")
        self.assertFalse(self.record(admission).committed)
        # The accepted run is retained with its receipt and its branch still ends at the
        # Stage the acceptance named, so a read can still find its commit: it stays.
        self.assertTrue(self.record(admission).recoverable)
        with self.assertRaises(HubFailure) as refusal:
            self.manager.acknowledge(admission.record.operationId)
        self.assertEqual(refusal.exception.error.code, "OPERATION_NOT_ACKNOWLEDGEABLE")
        # Once the branch has moved past that Stage, its compare-and-swap can never land.
        moved = {**retained, "branches": [{**branch, "headStageRef": f"{branch['headStageRef']}-successor"}
                                          for branch in retained["branches"]]}
        self.manager.reconcile(moved, worker_alive=False)
        self.assertEqual((self.record(admission).status, self.record(admission).recoverable), ("needs_recovery", False))
        self.assertTrue(self.manager.acknowledge(admission.record.operationId).acknowledgedAt)

    def test_retained_seat_failure_is_not_relabelled_as_unknown_worker_failure(self):
        admission, accepted = self.candidate()
        candidate_id = accepted["candidateId"]
        _, receipt = bound_project(self.app.state).newest_runner_receipt(candidate_id)
        self.repository.put_json(run=self.repository.load_run(candidate_id),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=candidate_id),
            record_kind=RUNNER_RUN_RECEIPT, payload={**receipt, "seat_execution_complete": False})
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual(self.record(admission).status, "failed")
        self.assertIn("incomplete", self.record(admission).reason)

    def test_invalid_accept_cannot_borrow_the_initial_stage_after_lost_refusal(self):
        model = self.model(self.fixture.REFERENCE_RUN_ID, self.state_digest)
        initialized = self.client.post("/api/design-stages/initialize", json={
            "projectId": self.fixture.PROJECT_ID, "modelSource": model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        initial = initialized.json()
        self.assertIsNone(initial["parentStageRef"])
        path = f"/api/candidates/{initial['candidateId']}/accept"
        payload = {"projectId": self.fixture.PROJECT_ID}
        admission, _ = self.admission(path, payload)
        refused = self.client.post(path, json=payload)
        self.assertEqual(refused.status_code, 422, refused.text)
        self.manager.interrupted(admission, "lost invalid acceptance response")
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual(self.record(admission).status, "needs_recovery")
        self.assertFalse(self.record(admission).committed)
        # Without a parent Stage there is no commit a read could find for it.
        self.assertFalse(self.record(admission).recoverable)
        self.assertTrue(self.manager.acknowledge(admission.record.operationId).acknowledgedAt)

    def test_refused_redispatch_after_hub_restart_cannot_claim_old_candidate_result(self):
        original, _ = self.candidate()
        retained = self.snapshot()
        restarted = OperationManager(self.fixture.PROJECT_ID)
        runtime = ProjectRuntime("fixture-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            restarted, ProjectBinding.open(self.settings), retained=retained)
        manager = ProjectRuntimeManager(None, None)
        ready = SimpleNamespace(url="http://127.0.0.1:1", instance_id="fixture-instance")
        with patch.object(manager, "service", return_value=ready), \
             patch("monkeyhub_api.runtime.manager.request_http", side_effect=AssertionError("refused operation dispatched")) as forwarded:
            with self.assertRaises(HubFailure) as refusal:
                manager.forward(runtime, "/api/proposals/a-different-proposal/candidate", "POST", b"",
                    {"idempotency-key": original.record.operationId})
        self.assertEqual(refusal.exception.error.code, "OPERATION_RETAINED")
        forwarded.assert_not_called()
        restarted.reconcile(retained, worker_alive=False)
        record = next(row for row in restarted.records() if row.operationId == original.record.operationId)
        self.assertEqual(record.status, "failed")
        self.assertFalse(record.committed)
        self.assertIsNone(record.resultDigest)

    def test_acceptance_is_dispatched_while_a_refresh_names_its_candidate_proposal(self):
        # A retained refresh names the candidate's job and proposal on every
        # operation bound to that candidate, an acceptance included. Its own
        # path is not a proposal, so the acceptance is still dispatched once
        # and nothing else is read from the worker on its behalf.
        initial, candidate_id, path, payload, _ = self.acceptance()
        accepting = OperationManager(self.fixture.PROJECT_ID)
        runtime = ProjectRuntime("accepting-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            accepting, ProjectBinding.open(self.settings), retained=self.snapshot(live=True))
        manager = ProjectRuntimeManager(None, None)
        ready = SimpleNamespace(url="http://127.0.0.1:1", instance_id="fixture-instance")
        requests = []

        def studio(base, target, method="GET", body=None, headers=None, *, timeout=10):
            requests.append((method, target))
            answer = self.client.request(method, target, content=body, headers=headers or {})
            return HttpResult(answer.status_code, answer.content,
                              {"content-type": answer.headers.get("content-type", "application/json")})

        def refreshed(_runtime):
            # The watcher's own reconcile, at the window it really occupies:
            # after this request is admitted and before it is dispatched.
            accepting.reconcile(self.snapshot(live=True), worker_alive=True)
            return ready

        with patch.object(manager, "service", side_effect=refreshed), \
             patch("monkeyhub_api.runtime.manager.request_http", side_effect=studio):
            result = manager.forward(runtime, path, "POST", json.dumps(payload, sort_keys=True).encode(),
                {"idempotency-key": str(uuid4()), "content-type": "application/json"})
        self.assertEqual(requests, [("POST", path)])
        self.assertEqual(result.status, 200, result.body)
        self.assertEqual(result.json()["parentStageRef"], initial["stageRef"])
        accepting.reconcile(self.snapshot(live=True), worker_alive=True)
        record = next(row for row in accepting.records() if row.candidateId == candidate_id and row.source == "studio")
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.committed)


class LostResultTests(unittest.TestCase):
    """S15: a run is called lost only after several reads find nothing working on it, and found again it completes."""

    def started(self, manager):
        admission, _ = manager.admit(str(uuid4()), "POST", "/api/proposals/studio-p1/candidate", b"",
                                     retained=None, source="studio", session_id=None)
        manager.replied(admission, HttpResult(202, json.dumps({"jobId": "job-1", "candidateId": admission.record.candidateId,
                                                               "status": "queued"}).encode(), {}))
        return admission

    @staticmethod
    def status(manager, admission):
        return next(row for row in manager.records() if row.operationId == admission.record.operationId).status

    def test_a_job_over_before_its_result_is_read_is_not_lost_and_completes_once_read(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        candidate_id = admission.record.candidateId
        over = {"jobs": [{"candidateId": candidate_id, "jobId": "job-1", "status": "succeeded"}], "candidates": []}
        for _ in range(LOST_AFTER_MISSES - 1):
            manager.reconcile(over, worker_alive=True)
            self.assertEqual(self.status(manager, admission), "executing")
        found = {**over, "candidates": [{"candidateId": candidate_id, "status": "completed", "receiptRef": "project://p/r"}]}
        self.assertTrue(manager.reconcile(found, worker_alive=True), "a read that settles an operation says so")
        self.assertEqual(self.status(manager, admission), "completed")
        self.assertFalse(manager.reconcile(found, worker_alive=True), "nothing moved on the next read")

    def test_a_worker_late_with_its_health_check_loses_a_run_only_after_several_reads(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        for _ in range(LOST_AFTER_MISSES - 1):
            manager.reconcile({"candidates": []}, worker_alive=False, worker_unanswered=True)
            self.assertEqual(self.status(manager, admission), "executing")
        manager.reconcile({"candidates": []}, worker_alive=False, worker_unanswered=True)
        self.assertEqual(self.status(manager, admission), "needs_recovery")
        # The result read after all: the operation completes and its notice has nothing left to say.
        manager.reconcile({"candidates": [{"candidateId": admission.record.candidateId, "status": "completed"}]}, worker_alive=True)
        self.assertEqual(self.status(manager, admission), "completed")
        self.assertEqual(producers.operations(manager.records()), [])

    def test_a_worker_that_is_gone_loses_its_run_at_once(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        manager.reconcile({"candidates": []}, worker_alive=False)
        self.assertEqual(self.status(manager, admission), "needs_recovery")

    def test_the_notice_of_a_lost_run_reads_in_chinese(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        manager.reconcile({"candidates": []}, worker_alive=False)
        [card] = producers.operations(manager.records())
        self.assertEqual(card.titleZh, "生成方案的结果找不回来了")
        self.assertRegex(card.summaryZh, r"[一-鿿]")
        self.assertNotIn("worker", card.summaryZh)
        self.assertIn("worker", card.summary, "the English record keeps the runner's own sentence")


class RecordReliabilityTests(unittest.TestCase):
    """S19: a record whose result is written completes; a slow Studio loses nothing; executing always ends."""

    started = LostResultTests.started
    status = staticmethod(LostResultTests.status)

    def test_a_written_result_completes_whatever_page_the_job_was_read_on(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        candidate_id = admission.record.candidateId
        # The run's row was read on an early page, while it ran; the jobs, on the last page, after it finished.
        early = {"jobs": [{"candidateId": candidate_id, "jobId": "job-1", "status": "succeeded"}],
                 "candidates": [{"candidateId": candidate_id, "status": "running"}]}
        for _ in range(LOST_AFTER_MISSES + 2):
            manager.reconcile(early, worker_alive=True)
            self.assertEqual(self.status(manager, admission), "executing")
        written = {**early, "candidates": [{"candidateId": candidate_id, "status": "completed", "receiptRef": "project://p/r"}]}
        manager.reconcile(written, worker_alive=True)
        self.assertEqual(self.status(manager, admission), "completed")
        self.assertEqual(producers.operations(manager.records()), [])

    def test_a_result_written_after_the_run_was_called_lost_completes_it(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        manager.reconcile({"candidates": []}, worker_alive=False)
        self.assertEqual(self.status(manager, admission), "needs_recovery")
        self.assertTrue(manager.awaiting_result(), "a run called lost just now is still looked for")
        manager.reconcile({"candidates": [{"candidateId": admission.record.candidateId, "status": "completed",
                                           "receiptRef": "project://p/r"}]}, worker_alive=True)
        self.assertEqual(self.status(manager, admission), "completed")
        self.assertFalse(manager.awaiting_result())

    def test_a_studio_slow_to_answer_does_not_lose_the_run_it_builds(self):
        process = dict(instance_id="w", desired_state="running", process_id=7)
        self.assertTrue(_still_there(SimpleNamespace(state="unavailable", **process)),
                        "a Studio that stopped answering while its process runs is still there")
        self.assertTrue(_still_there(SimpleNamespace(state="busy", **process)))
        self.assertFalse(_still_there(SimpleNamespace(state="unavailable", instance_id="w", desired_state="stopped", process_id=7)))
        self.assertFalse(_still_there(SimpleNamespace(state="crashed", instance_id="w", desired_state="running", process_id=None)))
        manager = OperationManager("p")
        admission = self.started(manager)
        # Its process is there and only does not answer: no read in a row of these calls the run lost at once.
        manager.reconcile({"candidates": [{"candidateId": admission.record.candidateId, "status": "needs_recovery"}]},
                          worker_alive=False, worker_unanswered=True)
        self.assertEqual(self.status(manager, admission), "executing")

    def test_a_run_nothing_works_on_does_not_stay_executing(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        # No job (the Studio that ran it started again) and a row that reads neither working nor done.
        stuck = {"jobs": [], "candidates": [{"candidateId": admission.record.candidateId, "status": "needs_recovery",
                                             "receiptRef": "project://p/r", "error": "unreadable"}]}
        for _ in range(LOST_AFTER_MISSES):
            manager.reconcile(stuck, worker_alive=True)
        record = next(row for row in manager.records() if row.operationId == admission.record.operationId)
        self.assertEqual(record.status, "needs_recovery")
        self.assertTrue(record.recoverable, "its receipt is there, so reading can still resolve it")

    def test_executing_ends_when_no_read_sees_the_run_worked_on(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        now = admission.working_at
        with patch("monkeyhub_api.runtime.operations.time.monotonic", return_value=now + EXECUTING_LIMIT_S - 1):
            self.assertFalse(manager.expire())
        with patch("monkeyhub_api.runtime.operations.time.monotonic", return_value=now + EXECUTING_LIMIT_S):
            self.assertTrue(manager.expire())
            self.assertTrue(manager.awaiting_result())
        self.assertEqual(self.status(manager, admission), "needs_recovery")
        with patch("monkeyhub_api.runtime.operations.time.monotonic", return_value=now + EXECUTING_LIMIT_S + RECHECK_LOST_S):
            self.assertFalse(manager.awaiting_result(), "looked for a while, not forever")

    def test_a_run_seen_running_is_not_expired(self):
        manager = OperationManager("p")
        admission = self.started(manager)
        running = {"jobs": [{"candidateId": admission.record.candidateId, "jobId": "job-1", "status": "running"}], "candidates": []}
        with patch("monkeyhub_api.runtime.operations.time.monotonic", return_value=admission.working_at + EXECUTING_LIMIT_S):
            manager.reconcile(running, worker_alive=True)
            self.assertFalse(manager.expire())
        self.assertEqual(self.status(manager, admission), "executing")


class JournalSpellingTests(unittest.TestCase):
    """Gap 26: one folder, one journal, however an earlier Hub spelled it."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "Project Folder"
        self.project.mkdir()
        self.journals = self.root / "operations"
        self.journals.mkdir()

    @staticmethod
    def row(operation_id, status):
        return {"record": {"operationId": operation_id, "projectId": "spelled", "kind": "POST /api/program",
                           "source": "hub", "status": status},
                "signature": ["POST", "/api/program", "0" * 64], "expectedStage": None,
                "branchId": "main", "acceptingCandidate": False}

    def write(self, name, project_dir, rows, acknowledged=None):
        path = self.journals / name
        saved = {"projectId": "spelled", "projectDir": project_dir, "operations": rows}
        if acknowledged:
            saved["acknowledged"] = acknowledged
        path.write_text(json.dumps(saved), encoding="utf-8")
        return path

    @unittest.skipUnless(sys.platform == "win32", "the extended-length spelling is a Windows one")
    def test_both_spellings_of_a_folder_have_one_key(self):
        plain = str(self.project)
        self.assertEqual(project_key("\\\\?\\" + plain), project_key(plain))
        self.assertEqual(project_key(plain.upper()), project_key(plain))
        self.assertFalse(project_key(plain).startswith("\\\\?\\"))
        self.assertEqual(earlier_project_keys(plain), ("\\\\?\\" + project_key(plain),))

    @unittest.skipUnless(sys.platform == "win32", "the extended-length spelling is a Windows one")
    def test_an_earlier_journal_joins_this_one_and_is_kept_under_another_name(self):
        only_earlier, both = str(uuid4()), str(uuid4())
        earlier = self.write("earlier.json", "\\\\?\\" + project_key(str(self.project)),
                             [self.row(only_earlier, "failed"), self.row(both, "failed")],
                             {only_earlier: {"acknowledgedAt": "2026-10-01T00:00:00Z", "status": "failed"}})
        current = self.write("current.json", project_key(str(self.project)), [self.row(both, "refused")])
        manager = OperationManager("spelled", project_dir=str(self.project), journal_path=current)
        manager.merge([earlier, self.journals / "missing.json", current])
        shown = {row.operationId: row for row in manager.records()}
        self.assertEqual(set(shown), {only_earlier, both})
        self.assertEqual(shown[both].status, "refused", "this journal wins where both name an operation")
        self.assertEqual(shown[only_earlier].acknowledgedAt, "2026-10-01T00:00:00Z")
        self.assertFalse(earlier.exists())
        self.assertTrue((self.journals / "earlier.json.merged-into-current").is_file())
        again = OperationManager("spelled", project_dir=str(self.project), journal_path=current)
        self.assertEqual({row.operationId for row in again.records()}, {only_earlier, both})

    def test_an_unreadable_or_foreign_earlier_journal_is_left_where_it_is(self):
        broken = self.journals / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        foreign = self.write("foreign.json", project_key(str(self.root)), [self.row(str(uuid4()), "failed")])
        current = self.journals / "current.json"
        manager = OperationManager("spelled", project_dir=str(self.project), journal_path=current)
        manager.merge([broken, foreign])
        self.assertEqual(manager.records(), [])
        self.assertTrue(broken.is_file() and foreign.is_file())
        self.assertFalse(current.exists(), "nothing was taken in, so nothing was written")



class ResendTests(unittest.TestCase):
    """A failure that did nothing passes without the person: the Hub sends the same request again (#300 4-3)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.operations = OperationManager("demo", project_dir=str(root / "demo"), journal_path=root / "operations.json")
        self.runtime = ProjectRuntime("resend-runtime", "demo", str(root / "demo"), self.operations,
                                      SimpleNamespace(run_ids=lambda: ()))
        self.hub = ProjectRuntimeManager(None, None)
        self.worker = SimpleNamespace(url="http://isolated-worker", instance_id="worker-1")
        paused = patch("monkeyhub_api.runtime.manager._pause")
        self.pauses = paused.start()
        self.addCleanup(paused.stop)

    @staticmethod
    def answer(status, code=None):
        body = {"code": code, "detail": code} if code else {"documentId": "d1"}
        return HttpResult(status, json.dumps(body).encode("utf-8"), {"content-type": "application/json"})

    def draw(self, *answers):
        with patch.object(self.hub, "service", return_value=self.worker), patch.object(self.hub, "emit"),                 patch("monkeyhub_api.runtime.manager.request_http", side_effect=list(answers)) as sent:
            result = self.hub.forward(self.runtime, "/api/drawings/elevations", "POST", b"{}",
                                      {"idempotency-key": str(uuid4())})
        record, = self.operations.records()
        return result, record, sent.call_count

    def test_a_drawing_whose_worker_stopped_is_sent_again_and_says_how_often(self):
        result, record, sends = self.draw(self.answer(503, "DRAWING_WORKER_LOST"), self.answer(503, "DRAWING_WORKER_LOST"),
                                          self.answer(201))
        self.assertEqual((result.status, sends, record.status, record.attempts, record.errorCode), (201, 3, "completed", 3, None))
        self.assertEqual(self.pauses.call_count, 2)

    def test_a_failure_that_keeps_coming_back_ends_the_operation_with_its_code(self):
        result, record, sends = self.draw(*[self.answer(503, "DRAWING_WORKER_LOST")] * 5)
        self.assertEqual((result.status, sends, record.status, record.attempts, record.errorCode),
                         (503, 3, "failed", 3, "DRAWING_WORKER_LOST"))

    def test_a_failure_a_person_answers_is_sent_once(self):
        result, record, sends = self.draw(self.answer(500, "SEAT_FAILED"), self.answer(201))
        self.assertEqual((result.status, sends, record.status, record.attempts, record.errorCode),
                         (500, 1, "failed", 1, "SEAT_FAILED"))

    def test_a_read_waits_for_a_starting_worker(self):
        starting = HubFailure(503, "WORKER_STARTING", "The project's Studio is starting.")
        with patch.object(self.hub, "service", side_effect=[starting, starting, self.worker]),                 patch("monkeyhub_api.runtime.manager.request_http", return_value=self.answer(200)):
            result = self.hub.forward(self.runtime, "/api/documents", "GET", b"", {})
        self.assertEqual((result.status, self.pauses.call_count), (200, 2))

    def test_a_background_read_answers_at_once_and_is_not_sent_again(self):
        starting = HubFailure(503, "WORKER_STARTING", "The project's Studio is starting.")
        with patch.object(self.hub, "service", side_effect=starting), self.assertRaises(HubFailure) as refused:
            self.hub.forward(self.runtime, "/api/worktrees", "GET", b"", {}, wait=False)
        self.assertEqual(refused.exception.error.code, "WORKER_STARTING")
        with patch.object(self.hub, "service", return_value=self.worker),                 patch("monkeyhub_api.runtime.manager.request_http",
                      return_value=self.answer(503, "INDEX_UNAVAILABLE")) as sent:
            result = self.hub.forward(self.runtime, "/api/worktrees", "GET", b"", {}, wait=False)
        self.assertEqual((result.status, sent.call_count, self.pauses.call_count), (503, 1, 0))


class StudioRestartTests(unittest.TestCase):
    """A project's Studio that stopped is started again by the Hub, as often as the repair table allows (#300 4-2)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        operations = OperationManager("demo", project_dir=str(root / "demo"), journal_path=root / "operations.json")
        self.runtime = ProjectRuntime("restart-runtime", "demo", str(root / "demo"), operations,
                                      SimpleNamespace(run_ids=lambda: ()))
        self.hub = ProjectRuntimeManager(None, None)
        self.stopped = SimpleNamespace(state="crashed", desired_state="running",
                                       error=SimpleNamespace(detail="The application exited with code 3221225477."))

    def test_a_stopped_studio_is_restarted_three_times_then_left_for_a_person(self):
        with patch.object(self.hub, "recover") as recovered, patch.object(self.hub, "emit") as told:
            for _ in range(5):
                self.hub._restart_stopped(self.runtime, [self.stopped])
            self.assertFalse(self.hub._restart_stopped(self.runtime, [SimpleNamespace(state="ready", desired_state="running")]))
        self.assertEqual(recovered.call_count, 3)
        self.assertEqual([row.kind for row in self.runtime.repairs],
                         ["studio-restarted", "studio-restarted", "studio-restarted", "studio-restart-held"])
        self.assertEqual([call.args for call in told.call_args_list], [("repair/applied", "restart-runtime")] * 4)
        self.assertIn("3221225477", self.runtime.repairs[0].detail)

    def test_a_studio_stopped_on_purpose_or_in_a_closed_project_is_left_alone(self):
        with patch.object(self.hub, "recover") as recovered:
            self.hub._restart_stopped(self.runtime, [SimpleNamespace(state="crashed", desired_state="stopped", error=None)])
            self.runtime.state = "closed"
            self.hub._restart_stopped(self.runtime, [self.stopped])
        self.assertEqual((recovered.call_count, self.runtime.repairs), (0, []))

    def test_a_start_that_timed_out_is_started_again_but_a_rejected_one_is_not(self):
        def rejected(code):
            return SimpleNamespace(state="unavailable", desired_state="stopped", process_id=None,
                                   error=SimpleNamespace(code=code, detail="The application did not become ready."))

        with patch.object(self.hub, "recover") as recovered, patch.object(self.hub, "emit"):
            self.assertFalse(self.hub._restart_stopped(self.runtime, [rejected("SERVICE_IDENTITY_MISMATCH")]),
                             "another source version needs the Hub restarted, not the Studio")
            exiting = rejected("START_TIMEOUT")
            exiting.process_id = 4242
            self.assertFalse(self.hub._restart_stopped(self.runtime, [exiting]), "started again once it has exited")
            self.assertTrue(self.hub._restart_stopped(self.runtime, [rejected("START_TIMEOUT")]))
        self.assertEqual((recovered.call_count, [row.kind for row in self.runtime.repairs]), (1, ["studio-start-retried"]))

    def test_restarting_a_start_that_timed_out_starts_it_as_a_person_would(self):
        timed_out = SimpleNamespace(state="unavailable", desired_state="stopped", process_id=None,
                                    error=SimpleNamespace(code="START_TIMEOUT", detail="not ready"))
        self.hub.applications = SimpleNamespace(worker_snapshots=lambda **_: [timed_out], start=Mock(), recover=Mock())
        with patch.object(self.hub, "refresh"), patch.object(self.hub, "emit"),                 patch.object(self.hub, "project_snapshot", return_value="snapshot"):
            self.assertEqual(self.hub.recover(self.runtime), "snapshot")
        self.hub.applications.start.assert_called_once_with("monkeyarch", project_dir=self.runtime.project_dir)
        self.hub.applications.recover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
