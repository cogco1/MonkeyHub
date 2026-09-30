"""Existing model options and source-specific annotations survive cold reads."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from archflow.adapters.three_dm_inspector import ThreeDmInspectionError, ThreeDmInspectionErrorCode
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SOURCE_DOCUMENT, STUDIO_WORKING_COPY
from archflow_studio_api.application.binding import record_kind
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import PROJECT_ID, REFERENCE_RUN_ID, RECORD_PAYLOAD, make_project, retain_runner_receipt, runner_state_digest
from .test_documents import image_bytes
from .test_document_annotations import stroke, page_ref, page_visual
from .test_gestures import gesture, hit, Scripted
from .test_intents import scripted


def register_model(client: TestClient, run_id: str, state_digest: str, data: bytes) -> dict:
    response = client.post("/api/model-assets", json={"projectId": PROJECT_ID, "runId": run_id,
                           "stateDigest": state_digest, "fileName": "complete.3dm", "contentBase64": base64.b64encode(data).decode()})
    if response.status_code != 201:
        raise AssertionError(response.text)
    return response.json()


class WorkingCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-working-copies-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        changed = copy.deepcopy(RECORD_PAYLOAD)
        next(row for row in changed["entities"] if row["entity_id"] == "portico-base")["fields"]["params"]["height"] = 0.8
        run_b = self.repository.create_run("run-b")
        self.digest_a = runner_state_digest(self.repository, REFERENCE_RUN_ID)
        self.digest_b = runner_state_digest(self.repository, "run-b", changed)
        retain_runner_receipt(self.repository, run_b, design_state_digest=self.digest_b, record_payload=changed)
        self.bytes_a = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        self.bytes_b = (Path(__file__).parent / "fixtures/model-source-b.3dm").read_bytes()
        self.asset_a = register_model(self.client, REFERENCE_RUN_ID, self.digest_a, self.bytes_a)
        self.asset_b = register_model(self.client, "run-b", self.digest_b, self.bytes_b)
        self.a, self.b = self.asset_a["modelSource"], self.asset_b["modelSource"]

    def group(self, group_id: str = "local-cabinets", base_stage_ref: str | None = None) -> dict:
        """A retained Exploration, as the retired creation route wrote it into its common-base run."""

        self.repository.put_json(
            run=self.repository.load_run(REFERENCE_RUN_ID),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=REFERENCE_RUN_ID),
            record_kind=STUDIO_WORKING_COPY,
            payload={"schema": "StudioWorkingCopy@1", "projectId": PROJECT_ID, "groupId": group_id,
                     "label": "Local cabinets", "stageId": "stage02", "commonBase": self.a,
                     "baseStageRef": base_stage_ref, "scope": ["element:portico-base"],
                     "options": [{"id": "A", "label": "Original", "modelSource": self.a},
                                 {"id": "B", "label": "Higher", "modelSource": self.b}],
                     "selectedOptionId": None, "previousRevisionSha256": None},
        )
        response = self.client.get(f"/api/working-copies/{group_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_exploration_creation_is_retired_and_retained_explorations_stay_readable(self) -> None:
        # A Study is declared on a candidate admission now (#294 owner decision Q4).
        retired = self.client.post("/api/working-copies", json={
            "projectId": PROJECT_ID, "groupId": "new-cabinets", "label": "Local cabinets", "stageId": "stage02",
            "commonBase": self.a, "scope": ["element:portico-base"],
            "options": [{"id": "A", "label": "Original", "modelSource": self.a},
                        {"id": "B", "label": "Higher", "modelSource": self.b}]})
        self.assertEqual(retired.status_code, 405, retired.text)
        self.assertEqual(retired.json()["code"], "METHOD_NOT_ALLOWED")
        self.assertNotIn("/api/working-copies", {
            path for path, operations in self.app.openapi()["paths"].items() if "post" in operations})
        legacy = self.group("legacy-cabinets")
        self.assertIsNone(legacy["baseStageRef"])
        self.assertEqual(self.client.get("/api/design-history").json()["stages"], [])
        initial = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": self.a})
        self.assertEqual(initial.status_code, 201, initial.text)
        current = self.group("stage-cabinets", initial.json()["stageRef"])
        self.assertEqual(current["baseStageRef"], initial.json()["stageRef"])
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(restarted.get("/api/working-copies/stage-cabinets").json(), current)
            self.assertEqual(restarted.get("/api/working-copies/legacy-cabinets").json(), legacy)
            self.assertEqual(restarted.get("/api/working-copies").json()["workingCopies"], [legacy, current])
            self.assertEqual(len(restarted.get("/api/design-history").json()["stages"]), 1)

    def events(self) -> list[dict]:
        response = self.client.get("/api/events", params={"limit": 1000})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        return [json.loads(line.partition(":")[2].strip())
                for line in response.text.splitlines() if line.startswith("data:")]

    def assert_asset_event(self, event: dict, event_type: str, run_id: str) -> None:
        null_fields = {"jobId", "candidateId", "proposalId", "wallTimeS", "error", "reviewReady", "blockedBy"}
        self.assertEqual(set(event), {"seq", "at", "type", "runId"} | null_fields)
        self.assertIsInstance(event["seq"], int)
        self.assertGreater(event["seq"], 0)
        self.assertTrue(event["at"])
        self.assertEqual((event["type"], event["runId"]), (event_type, run_id))
        for key in null_fields:
            self.assertIsNone(event[key], (key, event))

    def test_model_registration_events_skip_duplicate_and_invalid_requests(self) -> None:
        # setUp registered both models through the real HTTP/P036 path.
        before = self.events()
        self.assertEqual(len(before), 2, before)
        self.assert_asset_event(before[0], "model_asset.registered", REFERENCE_RUN_ID)
        self.assert_asset_event(before[1], "model_asset.registered", "run-b")
        self.assertLess(before[0]["seq"], before[1]["seq"])
        repeated = register_model(self.client, REFERENCE_RUN_ID, self.digest_a, self.bytes_a)
        self.assertEqual(repeated, self.asset_a)
        self.assertEqual(self.events(), before)
        invalid = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "stateDigest": self.digest_b,
            "fileName": "complete.3dm", "contentBase64": base64.b64encode(self.bytes_a).decode(),
        })
        self.assertEqual(invalid.status_code, 409, invalid.text)
        self.assertEqual(self.events(), before)

    def test_invalid_model_registration_leaves_no_objects_records_events_or_head_changes(self) -> None:
        from types import SimpleNamespace

        # Unit decoding belongs to the inspector. Exercise its value/error
        # boundary here; the API tests do not construct geometry themselves.
        unsupported_units = b"3D Geometry File Format unit-boundary-test"
        unreadable = b"3D Geometry File Format inspection-boundary-test"
        failure = ThreeDmInspectionError(ThreeDmInspectionErrorCode.INVALID_FILE, "injected inspector refusal")
        cases = (
            ("header only", b"3D Geometry File Format      80", None, None),
            ("truncated archive", self.bytes_a[:len(self.bytes_a) // 2], None, None),
            ("unsupported units", unsupported_units, SimpleNamespace(units={"name": "Centimeters"}), None),
            ("inspection error", unreadable, None, failure),
        )

        def tree_snapshot(root: Path) -> dict:
            return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
                    for path in root.rglob("*")}

        layout = self.repository.layout
        objects = tree_snapshot(layout.objects)
        records = {run_id: tree_snapshot(layout.run(run_id).records) for run_id in (REFERENCE_RUN_ID, "run-b")}
        canonical_events = tree_snapshot(layout.events)
        events = self.events()
        head = layout.head.read_bytes()
        for label, data, inspection_value, inspection_error in cases:
            with self.subTest(case=label):
                inspection = (mock.patch("archflow_studio_api.application.artifacts.inspect_three_dm_contents",
                                         return_value=inspection_value, side_effect=inspection_error)
                              if inspection_value is not None or inspection_error else nullcontext())
                with inspection as inspected:
                    response = self.client.post("/api/model-assets", json={
                        "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "stateDigest": self.digest_a,
                        "fileName": "invalid.3dm", "contentBase64": base64.b64encode(data).decode(),
                    })
                if inspected is not None:
                    inspected.assert_called_once_with(data)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], "MODEL_ASSET_INVALID")
                self.assertEqual(tree_snapshot(layout.objects), objects)
                self.assertEqual({run_id: tree_snapshot(layout.run(run_id).records)
                                  for run_id in (REFERENCE_RUN_ID, "run-b")}, records)
                self.assertEqual(tree_snapshot(layout.events), canonical_events)
                self.assertEqual(self.events(), events)
                self.assertEqual(layout.head.read_bytes(), head)

    def test_option_append_event_skips_stale_duplicate_and_invalid_requests(self) -> None:
        group = self.group()
        before = self.events()
        request = {"projectId": PROJECT_ID, "baseRevisionSha256": group["revisionSha256"],
                   "option": {"id": "C", "label": "Another look at B", "modelSource": self.b}}
        added = self.client.post("/api/working-copies/local-cabinets/options", json=request)
        self.assertEqual(added.status_code, 200, added.text)
        after = self.events()
        self.assertEqual(after[:-1], before)
        self.assertEqual(len(after), len(before) + 1)
        self.assert_asset_event(after[-1], "working_copy.option_added", "run-b")
        self.assertGreater(after[-1]["seq"], before[-1]["seq"])
        with TestClient(create_app(self.settings)) as reopened:
            self.assertEqual(reopened.get("/api/working-copies/local-cabinets").json(), added.json())
        failures = (
            ({**request, "option": {**request["option"], "id": "D"}}, "WORKING_COPY_STALE"),
            ({**request, "baseRevisionSha256": added.json()["revisionSha256"]}, "WORKING_COPY_OPTION_EXISTS"),
            ({**request, "baseRevisionSha256": added.json()["revisionSha256"],
              "option": {"id": "D", "label": "Missing model", "modelSource": {**self.b, "assetSha256": "0" * 64}}},
             "MODEL_SOURCE_UNREGISTERED"),
        )
        for body, code in failures:
            with self.subTest(code=code):
                refused = self.client.post("/api/working-copies/local-cabinets/options", json=body)
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertEqual(refused.json()["code"], code)
                self.assertEqual(self.events(), after)
                self.assertEqual(self.client.get("/api/working-copies/local-cabinets").json(), added.json())

    def test_failed_option_persistence_publishes_no_success_event(self) -> None:
        group = self.group()
        before = self.events()
        repository = bound_project(self.app.state).repository
        with TestClient(self.app, raise_server_exceptions=False) as client:
            with mock.patch.object(repository, "put_json", side_effect=OSError("injected P036 write failure")) as save:
                refused = client.post("/api/working-copies/local-cabinets/options", json={
                    "projectId": PROJECT_ID, "baseRevisionSha256": group["revisionSha256"],
                    "option": {"id": "C", "label": "Unsaved option", "modelSource": self.b},
                })
        save.assert_called_once()
        self.assertEqual(refused.status_code, 500, refused.text)
        self.assertEqual(self.events(), before)
        self.assertEqual(self.client.get("/api/working-copies/local-cabinets").json(), group)

    def test_concurrent_model_registration_publishes_one_event(self) -> None:
        run_id = "concurrent-registration"
        run = self.repository.create_run(run_id)
        digest = runner_state_digest(self.repository, run_id)
        retain_runner_receipt(self.repository, run, design_state_digest=digest)
        before = self.events()
        start = threading.Barrier(2)

        def register():
            start.wait(timeout=10)
            return register_model(self.client, run_id, digest, self.bytes_a)

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(register) for _ in range(2)]
            first, second = [future.result(timeout=30) for future in futures]
        self.assertEqual(first, second)
        after = self.events()
        self.assertEqual(after[:-1], before)
        self.assertEqual(len(after), len(before) + 1)
        self.assert_asset_event(after[-1], "model_asset.registered", run_id)
        with TestClient(create_app(self.settings)) as reopened:
            rows = [row for row in reopened.get("/api/artifacts").json()["artifacts"] if row["runId"] == run_id]
        self.assertEqual(rows, [first])

    def test_registered_bytes_and_explicit_state_are_read_without_native_export_claim(self) -> None:
        before = self.repository.read_head()
        self.assertEqual(self.asset_b["representation"], "composed")
        self.assertEqual(self.asset_b["status"], "registered")
        self.assertIsNone(self.asset_b["readbackVerified"])
        with TestClient(create_app(self.settings)) as reopened:
            response = reopened.get(f"/api/artifacts/{self.b['assetSha256']}/bytes")
            self.assertEqual(response.content, self.bytes_b)
            self.assertIn(self.asset_b, reopened.get("/api/artifacts").json()["artifacts"])
        bad = self.client.post("/api/model-assets", json={"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID,
                               "stateDigest": self.digest_b, "fileName": "complete.3dm", "contentBase64": base64.b64encode(self.bytes_b).decode()})
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(self.repository.read_head(), before)

    def test_choice_survives_restart_without_closing_other_group_or_moving_head(self) -> None:
        head = self.repository.read_head()
        authored = self.repository.layout.authored_record.read_bytes()
        run_names = sorted(path.name for path in self.repository.layout.runs.iterdir())
        group = self.group()
        other = self.group("another-task")
        self.assertIsNone(group["selectedOptionId"])
        chosen = self.client.put("/api/working-copies/local-cabinets/selection", json={"projectId": PROJECT_ID,
                                 "baseRevisionSha256": group["revisionSha256"], "optionId": "B"})
        self.assertEqual(chosen.status_code, 200, chosen.text)
        self.assertEqual(chosen.json()["selectedOptionId"], "B")
        stale = self.client.put("/api/working-copies/local-cabinets/selection", json={"projectId": PROJECT_ID,
                                "baseRevisionSha256": group["revisionSha256"], "optionId": "A"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "WORKING_COPY_STALE")
        with TestClient(create_app(self.settings)) as reopened:
            self.assertEqual(reopened.get("/api/working-copies/local-cabinets").json(), chosen.json())
            self.assertEqual(reopened.get("/api/working-copies/another-task").json(), other)
            self.assertEqual(reopened.get("/api/working-copies/local-cabinets", params={"revisionSha256": group["revisionSha256"]}).json(), group)
        self.assertEqual(self.repository.read_head(), head)
        self.assertEqual(self.repository.layout.authored_record.read_bytes(), authored)
        self.assertEqual(sorted(path.name for path in self.repository.layout.runs.iterdir()), run_names)

    def test_unknown_drawing_can_be_associated_once_without_rewriting_old_ink_or_comment(self) -> None:
        upload = self.client.post("/api/documents", json={"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID,
                                  "fileName": "existing.png", "mimeType": "image/png", "contentBase64": base64.b64encode(image_bytes()).decode()})
        self.assertEqual(upload.status_code, 201)
        document = upload.json()
        query = {"runId": REFERENCE_RUN_ID, "assetSha256": document["assetSha256"], "pageIndex": 0}
        page = self.client.put("/api/document-annotations", json={"projectId": PROJECT_ID, **query,
                               "baseRevisionSha256": None, "annotations": [stroke()], "comment": "原批注"}).json()
        body = {"projectId": PROJECT_ID, "stateDigest": self.digest_a, "utterance": "change the cabinet",
                "targetComponentId": "portico", "elementId": "portico-base", "documentAnnotations": [page_ref(page)],
                "documentVisuals": [page_visual(page)]}
        compiler = Scripted("set height to 0.9")
        self.app.state.intent_compiler = compiler
        unknown = self.client.post("/api/intents", json=body)
        self.assertEqual(unknown.status_code, 409, unknown.text)
        self.assertEqual(unknown.json()["code"], "DOCUMENT_MODEL_SOURCE_UNKNOWN")
        self.assertEqual(compiler.calls, [])
        associated = self.client.post(f"/api/documents/{document['assetSha256']}/model-source",
                                      json={"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "modelSource": self.b})
        self.assertEqual(associated.status_code, 200, associated.text)
        self.assertTrue(associated.json()["modelSourceBindingRef"])
        wrong_base = self.client.post("/api/intents", json=body)
        self.assertEqual(wrong_base.status_code, 409, wrong_base.text)
        self.assertEqual(wrong_base.json()["code"], "DOCUMENT_MODEL_SOURCE_MISMATCH")
        self.assertEqual(compiler.calls, [])
        valid = self.client.post("/api/intents", json={**body, "sourceRunId": "run-b", "stateDigest": self.digest_b,
                                 "modelSource": self.b, "utterance": "set height to 0.9"})
        self.assertEqual(valid.status_code, 201, valid.text)
        self.assertEqual(valid.json()["proposal"]["sourceRunId"], "run-b")
        self.assertEqual(valid.json()["proposal"]["modelSource"], self.b)
        comments = self.client.get("/api/document-comments", params={"runId": REFERENCE_RUN_ID}).json()["comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["sourceRunId"], "run-b")
        self.assertEqual(self.client.get("/api/document-annotations", params=query).json(), page)
        rebound = self.client.post(f"/api/documents/{document['assetSha256']}/model-source",
                                  json={"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "modelSource": self.a})
        self.assertEqual(rebound.status_code, 409)
        with TestClient(create_app(self.settings)) as reopened:
            self.assertEqual(reopened.get("/api/documents", params={"runId": REFERENCE_RUN_ID}).json()["documents"], [associated.json()])
            self.assertEqual(reopened.get("/api/document-annotations", params=query).json(), page)

    def test_concurrent_document_uploads_cannot_install_competing_sources(self) -> None:
        before = self.repository.read_head()
        body = {"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "fileName": "parallel.png",
                "mimeType": "image/png", "contentBase64": base64.b64encode(image_bytes()).decode()}
        start = threading.Barrier(2)

        def upload(source):
            start.wait(timeout=10)
            return self.client.post("/api/documents", json={**body, "modelSource": source})

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(upload, source) for source in (self.a, self.b)]
            responses = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])
        saved = next(response.json() for response in responses if response.status_code == 201)
        refused = next(response.json() for response in responses if response.status_code == 409)
        self.assertEqual(refused["code"], "DOCUMENT_SOURCE_IMMUTABLE")
        binding = bound_project(self.app.state)
        self.assertEqual(len([ref for ref in binding.record_refs(REFERENCE_RUN_ID)
                              if record_kind(ref) == STUDIO_SOURCE_DOCUMENT]), 1)
        repeated = self.client.post("/api/documents", json={**body, "modelSource": saved["modelSource"]})
        self.assertEqual(repeated.status_code, 201, repeated.text)
        self.assertEqual(repeated.json(), saved)
        with TestClient(create_app(self.settings)) as reopened:
            self.assertEqual(reopened.get("/api/documents", params={"runId": REFERENCE_RUN_ID}).json()["documents"], [saved])
        self.assertEqual(self.repository.read_head(), before)

    def test_retained_duplicate_documents_only_refuse_conflicting_model_sources(self) -> None:
        data = image_bytes()
        response = self.client.post("/api/documents", json={"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID,
                                   "fileName": "original.png", "mimeType": "image/png",
                                   "contentBase64": base64.b64encode(data).decode(), "modelSource": self.a})
        self.assertEqual(response.status_code, 201, response.text)
        binding = bound_project(self.app.state)
        ref = next(ref for ref in binding.record_refs(REFERENCE_RUN_ID) if record_kind(ref) == STUDIO_SOURCE_DOCUMENT)
        original = self.repository.load_json(ref)
        run = self.repository.load_run(REFERENCE_RUN_ID)
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=REFERENCE_RUN_ID)
        same_source = {**original, "file_name": "same-source.png"}
        unbound = {key: value for key, value in original.items() if key != "modelSource"}
        for payload in (same_source, {**unbound, "file_name": "unbound.png"}):
            self.repository.put_json(run=run, destination=destination, record_kind=STUDIO_SOURCE_DOCUMENT, payload=payload)
        with TestClient(create_app(self.settings)) as reopened:
            listed = reopened.get("/api/documents", params={"runId": REFERENCE_RUN_ID})
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(len(listed.json()["documents"]), 1)
            self.assertEqual(listed.json()["documents"][0]["modelSource"], self.a)
            self.assertEqual(reopened.get(f"/api/documents/{response.json()['assetSha256']}/bytes",
                                         params={"runId": REFERENCE_RUN_ID}).content, data)
        self.repository.put_json(run=run, destination=destination, record_kind=STUDIO_SOURCE_DOCUMENT,
                                 payload={**original, "modelSource": self.b})
        with TestClient(create_app(self.settings)) as reopened:
            conflicting = reopened.get("/api/documents", params={"runId": REFERENCE_RUN_ID})
            self.assertEqual(conflicting.status_code, 409, conflicting.text)
            self.assertEqual(conflicting.json()["code"], "DOCUMENT_SOURCE_CONFLICT")

    def test_3d_ink_is_bound_to_exact_model_and_cas_preserves_old_revisions(self) -> None:
        mark = {"id": "first", **gesture("circle", hit("portico-base"))}
        request = {"projectId": PROJECT_ID, "modelSource": self.a, "baseRevisionSha256": None,
                   "annotations": [mark], "comment": "keep this view"}
        saved = self.client.put("/api/model-annotations", json=request)
        self.assertEqual(saved.status_code, 200, saved.text)
        snapshot = saved.json()
        self.assertEqual(snapshot["annotations"][0]["camera"], mark["camera"])
        self.assertEqual(snapshot["annotations"][0]["hits"], mark["hits"])
        stale = self.client.put("/api/model-annotations", json={**request, "annotations": []})
        self.assertEqual(stale.status_code, 409)
        erased = self.client.put("/api/model-annotations", json={**request, "baseRevisionSha256": snapshot["revisionSha256"], "annotations": []}).json()
        with TestClient(create_app(self.settings)) as reopened:
            self.assertEqual(reopened.get("/api/model-annotations", params=self.a).json(), erased)
            self.assertEqual(reopened.get("/api/model-annotations", params={**self.a, "revisionSha256": snapshot["revisionSha256"]}).json(), snapshot)
            self.assertEqual(reopened.get("/api/model-annotations", params=self.b).json()["annotations"], [])
            self.assertEqual(reopened.get("/api/model-annotations", params={**self.b, "revisionSha256": snapshot["revisionSha256"]}).status_code, 404)

    def test_model_source_survives_clarification_without_resubmitting_it(self) -> None:
        self.app.state.intent_compiler = scripted(status="question", question="What height?", component_id="portico", element_id="portico-base")
        body = {"projectId": PROJECT_ID, "stateDigest": self.digest_a, "sourceRunId": REFERENCE_RUN_ID,
                "targetComponentId": "portico", "elementId": "portico-base"}
        first = self.client.post("/api/intents", json={**body, "modelSource": self.a, "utterance": "make the base a little taller"})
        self.assertEqual(first.status_code, 422, first.text)
        token = first.json()["pendingIntent"]["continuationToken"]
        second = self.client.post("/api/intents", json={**body, "continuationToken": token, "utterance": "set height to 0.9"})
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["proposal"]["modelSource"], self.a)


if __name__ == "__main__":
    unittest.main()
