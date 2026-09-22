"""What the architect settled survives the turn it was said in.

Three families, the ones a person actually says: don't hatch this drawing,
stop writing it that way, keep this parameter. Each is retained against the
exact evidence it was said about — a board revision, a registered page, a real
design run — and read back by a later turn without its transcript.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SCOPED_DECISION, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectRecordRef, record_file_name
from archflow.project.repository import undeclared_version_identities
from archflow.project.version_refs import locations, restate
from archflow.state.state_record import StateRecord
from archflow_studio_api.application.binding import ProjectBinding, bound_project
from archflow_studio_api.application.decisions import (
    DECISIONS_RUN_ID,
    compile_scoped_decisions,
    decision_context_for,
    focus_refs,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    retain_runner_receipt,
    runner_state_digest,
)
from .test_candidate import CandidateTestCase
from .test_design_history import DesignHistoryFixture
from .test_documents import image_bytes, two_page_pdf

HATCH_WORDS = "这面墙不要打填充——留白就行"
COPY_WORDS = "别再写“赋能”这种词,直接说做了什么"
KEEP_WORDS = "module 就保持 1.2 m,别动"


def message(index: int) -> dict:
    return {"sessionId": "chat-2026-09-22", "messageId": f"message-{index:02d}"}


def board_source(revision: str, *element_ids: str) -> dict:
    return {"kind": "board", "revisionSha256": revision, "elementIds": list(element_ids)}


def document_source(document: dict, page_index: int = 0) -> dict:
    return {"kind": "document", "runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": page_index}


class DecisionFixture(CandidateTestCase):
    """One real project, and the three kinds of evidence a decision names."""

    def new_client(self) -> TestClient:
        client = TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off")))
        self.addCleanup(client.close)
        return client

    def upload(self, data: bytes, name: str = "图纸.pdf", mime: str = "application/pdf") -> dict:
        response = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "fileName": name, "mimeType": mime,
            "contentBase64": base64.b64encode(data).decode(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    board_revision: str | None = None

    def save_board(self, *element_ids: str, base: str | None = None,
                   deleted: tuple[str, ...] = ()) -> str:
        elements = [{"id": identifier, "type": "rectangle", "x": index * 10, "y": 0,
                     "width": 100, "height": 40, "isDeleted": identifier in deleted}
                    for index, identifier in enumerate((*element_ids, *deleted))]
        response = self.client.put("/api/board", json={
            "projectId": PROJECT_ID, "baseRevisionSha256": base or self.board_revision,
            "title": "设计讨论", "elements": elements, "seenDocuments": []})
        self.assertEqual(response.status_code, 200, response.text)
        self.board_revision = response.json()["revisionSha256"]
        return self.board_revision

    def design_source(self, run_id: str | None = None, state_digest: str | None = None) -> dict:
        return {"kind": "design", "sourceRunId": run_id or REFERENCE_RUN_ID,
                "stateDigest": state_digest or self.state_digest, "sourceStageRef": None}

    def spec(self, **overrides) -> dict:
        body = {"projectId": PROJECT_ID, "rawLanguage": HATCH_WORDS, "messageSource": message(1),
                "disposition": "avoid", "strength": "strong_preference", "targetRef": "drawing:hatch",
                "scope": {"domain": "drawing", "extent": "project"}, "applicability": "scope",
                "sourceKind": "human"}
        body.update(overrides)
        return body

    def save(self, expect: int = 201, **overrides):
        response = self.client.post("/api/decisions", json=self.spec(**overrides))
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def decisions(self, client: TestClient | None = None) -> list[dict]:
        response = (client or self.client).get("/api/decisions")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["projectId"], PROJECT_ID)
        return payload["decisions"]

    def context(self, client: TestClient | None = None, *, run_id: str = REFERENCE_RUN_ID,
                utterance: str = "接着往下调", expect: int = 200, **body) -> dict:
        target = client or self.client
        state = target.get("/api/state", params={"run": run_id, **(
            {"sourceStageRef": body["sourceStageRef"]} if body.get("sourceStageRef") else {})})
        self.assertEqual(state.status_code, 200, state.text)
        response = target.post("/api/intents/context", json={
            "projectId": PROJECT_ID, "sourceRunId": run_id, "utterance": utterance,
            "stateDigest": state.json()["stateDigest"], **body,
        })
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()


class DecisionPersistenceTests(DecisionFixture):
    def test_drawing_and_copy_decisions_reopen_with_their_exact_words_and_evidence(self) -> None:
        head, branches = self.repository.read_head(), self.repository.read_design_branches()
        revision = self.save_board("wall-outline", "note")
        document = self.upload(two_page_pdf())
        hatch = self.save(source=board_source(revision, "wall-outline"))
        copy = self.save(rawLanguage=COPY_WORDS, targetRef="copy:style", messageSource=message(2),
                         sourceKind="agent", scope={"domain": "copy", "extent": "project"},
                         source=document_source(document, 1))
        self.assertEqual((copy["sourceKind"], copy["messageSource"]), ("agent", message(2)))
        self.assertEqual((hatch["previousRevisionRef"], hatch["status"]), (None, "active"))
        self.assertEqual(hatch["projectId"], PROJECT_ID)
        self.assertEqual(hatch["attribution"],
                         {"actorId": "studio:explicit-user-action", "authenticated": False, "origin": "studio"})
        # A message reference is provenance the caller claims; the boundary's
        # own attribution is separate and is not taken from the request body.
        self.assertEqual((hatch["messageSource"], hatch["revisionMessageSource"]), (message(1), None))
        self.assertNotEqual(hatch["decisionId"], copy["decisionId"])
        self.assertEqual(hatch["source"], board_source(revision, "wall-outline"))
        self.assertEqual(copy["source"], document_source(document, 1))

        # A later board revision does not disturb the evidence already named.
        later = self.save_board("wall-outline", "note", "arrow", base=revision)
        self.assertNotEqual(later, revision)
        reopened = self.decisions(self.new_client())
        self.assertEqual([row["rawLanguage"] for row in reopened], [HATCH_WORDS, COPY_WORDS])
        self.assertEqual(reopened, [hatch, copy])
        again = self.save(source=board_source(revision, "note"), rawLanguage="这个标注也不要填充")
        self.assertEqual(again["source"]["revisionSha256"], revision)

        # Representation preferences are not design: no Stage, no HEAD, no run
        # beyond the decisions run and the evidence's own.
        self.assertEqual(self.repository.read_head(), head)
        self.assertEqual(self.repository.read_design_branches(), branches)
        self.assertEqual(set(self.repository.layout.runs.iterdir()) >= {
            self.repository.layout.run(DECISIONS_RUN_ID).root}, True)
        self.assertNotIn("studio-projection", {path.name for path in self.repository.layout.runs.iterdir()})

    def test_forged_missing_and_mismatched_sources_retain_nothing(self) -> None:
        revision = self.save_board("wall-outline", "note", deleted=("erased",))
        document = self.upload(image_bytes(), "平面.png", "image/png")
        binding = bound_project(self.client.app.state)
        # A second registration of the same bytes, naming a drawing revision
        # that cannot be read: naming that exact revision must reach it rather
        # than be answered from the identical plain upload.
        run = binding.load_run(document["runId"])
        original = next(self.repository.load_json(ref) for ref in binding.record_refs(run.run_id)
                        if ref.record_kind == STUDIO_SOURCE_DOCUMENT)
        drawing_ref = ProjectRecordRef(
            PROJECT_ID, f"runs/drawing-a/records/{record_file_name('drawing-projection-receipt', 'a' * 64)}",
            "a" * 64,
        ).uri
        self.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STUDIO_SOURCE_DOCUMENT,
            payload={**original, "revisionRef": drawing_ref, "drawingId": "plan",
                     "generatedAt": "2026-09-01T00:00:00Z"},
        )
        for status, overrides in (
            (404, {"source": {**document_source(document), "assetSha256": "b" * 64}}),
            (404, {"source": {**document_source(document), "runId": "no-such-run"}}),
            (409, {"source": {**document_source(document), "revisionRef": drawing_ref}}),
            (404, {"source": board_source(revision, "not-on-this-board")}),
            (404, {"source": board_source("c" * 64, "wall-outline")}),
            (404, {"source": board_source(revision, "erased")}),
            (422, {"source": board_source(revision, "wall-outline", "wall-outline")}),
            (422, {"source": self.design_source()}),
            (422, {"targetRef": "copy:style", "scope": {"domain": "copy", "extent": "project"},
                   "source": board_source(revision, "wall-outline")}),
            (422, {"targetRef": "drawing:shading", "source": board_source(revision, "wall-outline")}),
        ):
            with self.subTest(overrides=sorted(overrides)):
                self.save(status, **overrides)
        self.assertEqual(self.decisions(), [])
        self.assertFalse(self.repository.layout.run(DECISIONS_RUN_ID).root.exists())

    def test_keep_and_lock_bind_real_parameters_and_refuse_invented_targets(self) -> None:
        design = {"domain": "design", "extent": "project"}
        keep = self.save(rawLanguage=KEEP_WORDS, disposition="keep", strength="hard",
                         targetRef="parameter:module", scope=design, source=self.design_source(),
                         typedBinding={"kind": "parameter", "parameterKey": "module"})
        self.assertEqual(keep["typedBinding"], {"kind": "parameter", "parameterKey": "module", "value": 1.2,
                                                "unit": "m", "epistemicStatus": "declared", "lockAuthority": None})
        locked = self.save(rawLanguage="plinth 已经锁了,记下来", disposition="lock", strength="hard",
                           targetRef="parameter:plinth", scope=design, source=self.design_source(),
                           typedBinding={"kind": "parameter", "parameterKey": "plinth"})
        self.assertEqual(locked["typedBinding"]["lockAuthority"], "client")
        relation = self.save(rawLanguage="檐部落在基座上这条关系保留", disposition="keep", strength="hard",
                             targetRef="relation:rel-cornice-on-base", scope=design, source=self.design_source())
        self.assertEqual(relation["targetRef"], "relation:rel-cornice-on-base")

        for status, overrides in (
            # A lock decision records an existing lock; it never takes one.
            (409, {"disposition": "lock", "targetRef": "parameter:module", "scope": design,
                   "source": self.design_source(), "typedBinding": {"kind": "parameter", "parameterKey": "module"}}),
            (422, {"disposition": "lock", "targetRef": "parameter:plinth", "scope": design,
                   "source": self.design_source()}),
            (422, {"targetRef": "parameter:plinth", "scope": design, "source": self.design_source(),
                   "typedBinding": {"kind": "parameter", "parameterKey": "module"}}),
            (404, {"targetRef": "relation:invented", "scope": design, "source": self.design_source()}),
            (404, {"targetRef": "entity:ghost", "scope": design, "source": self.design_source()}),
            (404, {"targetRef": "parameter:nowhere", "scope": design, "source": self.design_source(),
                   "typedBinding": {"kind": "parameter", "parameterKey": "nowhere"}}),
            (422, {"targetRef": "entity:portico", "scope": design, "source": board_source(self.save_board("a"), "a")}),
        ):
            with self.subTest(overrides=sorted(overrides)):
                self.save(status, **overrides)
        self.assertEqual(len(self.decisions()), 3)
        binding = bound_project(self.client.app.state)
        _, record = binding.exact_state_record(binding.reference_run(REFERENCE_RUN_ID))
        self.assertEqual(record.parameter("plinth").lock_authority, "client")
        self.assertIsNone(record.parameter("module").lock_authority)

    def test_supersede_and_revoke_keep_the_previous_wording_and_refuse_a_stale_write(self) -> None:
        revision = self.save_board("wall-outline")
        first = self.save(source=board_source(revision, "wall-outline"))
        replacement = self.spec(source=board_source(revision, "wall-outline"),
                                rawLanguage="改主意了:这面墙用细斜线填充", disposition="require",
                                messageSource=message(3))
        second = self.revise(first, action="supersede", replacement=replacement, reason="讨论后改口",
                             revisionMessageSource=message(4))
        self.assertEqual(second["previousRevisionRef"], first["revisionRef"])
        self.assertEqual((second["disposition"], second["status"]), ("require", "active"))
        self.assertEqual((second["messageSource"], second["revisionMessageSource"]),
                         (message(3), message(4)))
        self.revise(first, action="supersede", replacement=replacement, expect=409, code="DECISION_STALE")
        self.revise(second, action="supersede", expect=422)
        self.revise(second, action="revoke", replacement=replacement, expect=422)
        third = self.revise(second, action="revoke", reason="业主不要了", revisionMessageSource=message(5))
        self.assertEqual((third["status"], third["reason"], third["rawLanguage"]),
                         ("revoked", "业主不要了", second["rawLanguage"]))
        # Revoking keeps the wording it revokes and the message that said it,
        # and adds the message that asked for the revocation.
        self.assertEqual((third["messageSource"], third["revisionMessageSource"]),
                         (message(3), message(5)))
        self.revise(third, action="revoke", expect=409, code="DECISION_REVOKED")

        cold = self.new_client()
        self.assertEqual([row["decisionId"] for row in self.decisions(cold)], [third["decisionId"]])
        history = cold.get(f"/api/decisions/{first['decisionId']}")
        self.assertEqual(history.status_code, 200, history.text)
        revisions = history.json()["revisions"]
        # Superseded is derived from standing outside the tip; the retained
        # records still say what they were written with.
        self.assertEqual([row["status"] for row in revisions], ["superseded", "superseded", "revoked"])
        self.assertEqual(revisions, [{**first, "status": "superseded"},
                                     {**second, "status": "superseded"}, third])
        self.assertEqual([row["rawLanguage"] for row in revisions],
                         [HATCH_WORDS, second["rawLanguage"], second["rawLanguage"]])
        self.assertEqual({row["messageSource"]["messageId"] for row in revisions},
                         {"message-01", "message-03"})
        self.assertEqual(cold.get("/api/decisions/not-a-decision").status_code, 404)

    def revise(self, current: dict, *, action: str, replacement: dict | None = None,
               reason: str | None = None, expect: int = 201, code: str | None = None,
               revisionMessageSource: dict | None = None) -> dict:
        response = self.client.post(f"/api/decisions/{current['decisionId']}/revisions", json={
            "projectId": PROJECT_ID, "expectedRevisionRef": current["revisionRef"],
            "action": action, "reason": reason, "replacement": replacement,
            "revisionMessageSource": revisionMessageSource,
        })
        self.assertEqual(response.status_code, expect, response.text)
        if code is not None:
            self.assertEqual(response.json()["code"], code)
        return response.json()

    def test_a_broken_revision_chain_fails_closed_and_retains_every_revision(self) -> None:
        revision = self.save_board("wall-outline")
        first = self.save(source=board_source(revision, "wall-outline"))
        run = self.repository.load_run(DECISIONS_RUN_ID)
        review = PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=DECISIONS_RUN_ID)
        payload = self.repository.load_json(
            next(ref for ref in self.repository.list_json(run=run, destination=review,
                                                          record_kind=STUDIO_SCOPED_DECISION)))
        # A competing second root, then a revision whose parent is not there.
        forged = self.repository.put_json(run=run, destination=review, record_kind=STUDIO_SCOPED_DECISION,
                                          payload={**payload, "createdAt": "2026-09-20T00:00:00+00:00"})
        refused = self.client.get("/api/decisions")
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "DECISION_CONFLICT")
        self.repository.layout.resolve_record(forged).unlink()
        self.assertEqual([row["revisionRef"] for row in self.decisions()], [first["revisionRef"]])
        missing = f"project://{PROJECT_ID}/runs/{DECISIONS_RUN_ID}/reviews/" \
                  f"{record_file_name(STUDIO_SCOPED_DECISION, 'f' * 64)}"
        self.repository.put_json(run=run, destination=review, record_kind=STUDIO_SCOPED_DECISION,
                                 payload={**payload, "previousRevisionRef": missing,
                                          "createdAt": "2026-09-21T00:00:00+00:00"})
        self.assertEqual(self.client.get("/api/decisions").status_code, 409)
        self.assertEqual(self.client.post("/api/decisions", json=self.spec(
            source=board_source(revision, "wall-outline"))).status_code, 409)

    def test_a_retained_decision_names_content_and_never_a_canonical_version(self) -> None:
        """What a format migration would find in these records, through the real writer."""

        revision = self.save_board("wall-outline")
        document = self.upload(two_page_pdf())
        self.save(source=board_source(revision, "wall-outline"))
        self.save(rawLanguage=COPY_WORDS, targetRef="copy:style",
                  scope={"domain": "copy", "extent": "project"}, source=document_source(document, 1))
        keep = self.save(rawLanguage=KEEP_WORDS, disposition="keep", strength="hard",
                         targetRef="parameter:module", source=self.design_source(),
                         scope={"domain": "design", "extent": "project"},
                         typedBinding={"kind": "parameter", "parameterKey": "module"})
        self.revise(keep, action="revoke", reason="不再保留")

        base = self.repository.read_head()
        run = self.repository.load_run(DECISIONS_RUN_ID)
        payloads = [self.repository.load_json(ref) for ref in self.repository.list_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=DECISIONS_RUN_ID),
            record_kind=STUDIO_SCOPED_DECISION)]
        self.assertEqual(len(payloads), 4)
        # Remap both the project's canonical base and the design state one of
        # these decisions names, so a content digest that survives is a content
        # digest nothing mistook for a project version.
        remapping = {base.state_sha256: "f" * 64, self.state_digest: "e" * 64}
        for payload in payloads:
            with self.subTest(target=payload["targetRef"]):
                self.assertEqual(locations(payload), [])
                self.assertEqual(undeclared_version_identities(payload, {base.require_digest(): base.version}), [])
                self.assertEqual(restate(payload, remapping), payload)
        # The base these revisions stand on is P036's own, on the run manifest
        # that owns it - which is why the records themselves carry none.
        manifest = json.loads(self.repository.layout.run(DECISIONS_RUN_ID).manifest.read_text(encoding="utf-8"))
        self.assertEqual([(pointer, digest) for pointer, _, digest in locations(manifest)],
                         [("/base", base.state_sha256)])
        self.assertEqual(restate(manifest, remapping)["base"]["state_sha256"], "f" * 64)

    def test_a_damaged_decisions_run_refuses_instead_of_reading_as_empty(self) -> None:
        revision = self.save_board("wall-outline")
        self.save(source=board_source(revision, "wall-outline"))
        manifest = self.repository.layout.run(DECISIONS_RUN_ID).manifest
        manifest.write_text("{ not a run manifest", encoding="utf-8")
        for response in (self.client.get("/api/decisions"),
                         self.client.post("/api/decisions", json=self.spec(
                             source=board_source(revision, "wall-outline")))):
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["code"], "RUN_NOT_FOUND")

    def test_a_damaged_board_run_refuses_instead_of_reading_as_an_empty_board(self) -> None:
        self.save_board("wall-outline")
        (self.repository.layout.run("studio-board").manifest).write_text("{ broken", encoding="utf-8")
        response = self.client.get("/api/board")
        self.assertEqual(response.status_code, 404, response.text)


class DecisionContextTests(DecisionFixture):
    def design(self, **overrides) -> dict:
        body = {"rawLanguage": KEEP_WORDS, "disposition": "keep", "strength": "hard",
                "targetRef": "parameter:module", "scope": {"domain": "design", "extent": "project"},
                "source": self.design_source(), "messageSource": message(1)}
        body.update(overrides)
        return self.save(**body)

    def test_one_cold_turn_is_handed_the_applicable_words_and_no_transcript(self) -> None:
        keep = self.design(typedBinding={"kind": "parameter", "parameterKey": "module"})
        cornice = self.design(rawLanguage="檐口这条先不要动", targetRef="entity:portico-cornice",
                              scope={"domain": "design", "extent": "targets",
                                     "targetRefs": ["entity:portico-cornice"]})
        drawing = self.save(source=board_source(self.save_board("wall-outline"), "wall-outline"))
        deferred = self.design(rawLanguage="柱距要不要改,先放着", disposition="defer", strength="temporary",
                               targetRef="parameter:bay")
        self.assertEqual(deferred["status"], "deferred")

        cold = self.new_client()
        pack = self.context(cold, elementIds=["portico-base"], utterance="把这块基座再抬高一点")
        self.assertEqual([row["decisionId"] for row in pack["scopedDecisions"]], [keep["decisionId"]])
        self.assertEqual(pack["scopedDecisions"][0]["rawLanguage"], KEEP_WORDS)
        self.assertEqual(pack["scopedDecisions"][0]["messageSource"], message(1))
        self.assertNotIn(HATCH_WORDS, str(pack))
        self.assertNotIn("把这块基座再抬高一点", str(pack["scopedDecisions"]))

        focused = self.context(cold, elementIds=["portico-cornice"], utterance="檐口再讨论一下")
        self.assertEqual({row["decisionId"] for row in focused["scopedDecisions"]},
                         {keep["decisionId"], cornice["decisionId"]})

        # The drawing decision is real, and belongs to a drawing turn.
        drawn = self.context(cold, decisionContext={"domain": "drawing"})
        self.assertEqual([row["decisionId"] for row in drawn["scopedDecisions"]], [drawing["decisionId"]])

        self.revise(keep, action="revoke", reason="不再保留")
        after = self.context(self.new_client(), elementIds=["portico-base"])
        self.assertEqual(after["scopedDecisions"], [])

    def test_all_applicable_project_decisions_reach_the_next_turn(self) -> None:
        saved = [self.design(rawLanguage=f"保留 module 的第 {index + 1} 条判断") for index in range(33)]
        pack = self.context(self.new_client(), elementIds=["portico-base"])
        self.assertEqual({row["decisionId"] for row in pack["scopedDecisions"]},
                         {row["decisionId"] for row in saved})

    def test_relation_scope_applies_when_only_one_end_is_in_focus(self) -> None:
        payload = deepcopy(RECORD_PAYLOAD)
        remote = deepcopy(next(row for row in payload["entities"] if row["entity_id"] == "portico-base"))
        remote["entity_id"] = "unrelated-block"
        remote["fields"]["references"] = {}
        remote["fields"]["params"]["profile"] = [[10, 0], [12, 0], [12, 2], [10, 2]]
        payload["entities"].append(remote)
        run = self.repository.create_run("run-relation-scope")
        digest = runner_state_digest(self.repository, run.run_id, payload)
        retain_runner_receipt(self.repository, run, record_payload=payload, design_state_digest=digest)
        kept = self.design(
            rawLanguage="保留基座与檐口之间的支承关系", targetRef="relation:rel-cornice-on-base",
            source=self.design_source(run.run_id, digest),
            scope={"domain": "design", "extent": "targets",
                   "targetRefs": ["entity:portico-base", "entity:portico-cornice"]},
        )
        cold = self.new_client()
        for target in ("portico-base", "portico-cornice"):
            with self.subTest(target=target):
                pack = self.context(cold, run_id=run.run_id, elementIds=[target])
                self.assertEqual([row["decisionId"] for row in pack["scopedDecisions"]], [kept["decisionId"]])
        unrelated = self.context(cold, run_id=run.run_id, elementIds=["unrelated-block"])
        self.assertEqual(unrelated["scopedDecisions"], [])

    def test_a_target_scope_follows_the_focused_elements_own_bindings(self) -> None:
        # portico-base's height is bound to the parameter this decision keeps,
        # so a turn that focuses that element is the turn it is about.
        bound = self.bind_height_to_module()
        keep = self.design(scope={"domain": "design", "extent": "targets",
                                  "targetRefs": ["parameter:module"]},
                           source=self.design_source(bound, self.bound_digest),
                           typedBinding={"kind": "parameter", "parameterKey": "module"})
        cold = self.new_client()
        focused = self.context(cold, run_id=bound, elementIds=["portico-base"])
        self.assertEqual([row["decisionId"] for row in focused["scopedDecisions"]], [keep["decisionId"]])
        elsewhere = self.context(cold, run_id=bound, elementIds=["portico-cornice"])
        self.assertEqual(elsewhere["scopedDecisions"], [])

    def bind_height_to_module(self, run_id: str = "run-bound") -> str:
        payload = deepcopy(RECORD_PAYLOAD)
        for entity in payload["entities"]:
            if entity["entity_id"] == "portico-base":
                entity["fields"]["params"]["height"] = "@module"
        run = self.repository.create_run(run_id)
        self.bound_digest = runner_state_digest(self.repository, run_id, payload)
        retain_runner_receipt(self.repository, run, record_payload=payload,
                              design_state_digest=self.bound_digest)
        return run_id

    def test_a_decision_about_a_target_the_record_dropped_goes_stale(self) -> None:
        keep = self.design(rawLanguage="檐口这条关系保留", targetRef="relation:rel-cornice-on-base")
        self.assertEqual([row["decisionId"] for row in self.context()["scopedDecisions"]],
                         [keep["decisionId"]])
        stripped = self.without_relation()
        self.assertEqual(self.context(run_id=stripped)["scopedDecisions"], [])
        # Derived, never written: the revision still says what it said.
        self.assertEqual(self.decisions()[0]["status"], "active")

    def without_relation(self, run_id: str = "run-stripped") -> str:
        payload = {key: value for key, value in deepcopy(RECORD_PAYLOAD).items() if key != "relations"}
        run = self.repository.create_run(run_id)
        retain_runner_receipt(self.repository, run, record_payload=payload,
                              design_state_digest=runner_state_digest(self.repository, run_id, payload))
        return run_id

    def test_an_explicit_turn_cannot_name_another_source_or_a_foreign_target(self) -> None:
        self.design()
        moved = self.later_run()
        state = self.client.get("/api/state", params={"run": moved}).json()["stateDigest"]
        mismatched = self.context(expect=409, decisionContext={
            "domain": "design", "source": {"kind": "design", "sourceRunId": moved,
                                           "stateDigest": state, "sourceStageRef": None}})
        self.assertEqual(mismatched["code"], "DECISION_SOURCE_MISMATCH")
        self.assertEqual(self.context(expect=404, decisionContext={
            "domain": "design", "targetRefs": ["entity:nowhere"]})["code"], "DECISION_TARGET_UNKNOWN")
        self.assertEqual(self.context(expect=422, decisionContext={
            "domain": "drawing", "targetRefs": ["entity:portico"]})["code"], "DECISION_INVALID")
        self.assertEqual(self.context(expect=422, decisionContext={
            "domain": "copy", "source": board_source(self.save_board("a"), "a")})["code"], "DECISION_INVALID")
        self.assertEqual(self.context(expect=422, decisionContext={
            "domain": "design", "source": board_source(self.save_board("b"), "b")})["code"], "DECISION_INVALID")

    def test_scope_survives_a_later_source_while_exact_source_goes_stale(self) -> None:
        first = self.save_board("wall-outline")
        scoped = self.save(source=board_source(first, "wall-outline"))
        exact = self.save(source=board_source(first, "wall-outline"), applicability="exact-source",
                          rawLanguage="就这一版图,不要填充")
        second = self.save_board("wall-outline", "arrow", base=first)
        drawing = {"domain": "drawing", "source": board_source(second, "wall-outline")}
        self.assertEqual([row["decisionId"] for row in
                          self.context(decisionContext=drawing)["scopedDecisions"]], [scoped["decisionId"]])
        original = {"domain": "drawing", "source": board_source(first, "wall-outline")}
        self.assertEqual({row["decisionId"] for row in
                          self.context(decisionContext=original)["scopedDecisions"]},
                         {scoped["decisionId"], exact["decisionId"]})
        # Element order is not evidence identity.
        reordered = {"domain": "drawing", "source": board_source(second, "arrow", "wall-outline")}
        both = self.save(source=board_source(second, "wall-outline", "arrow"), applicability="exact-source",
                         rawLanguage="这两个一起,不要填充")
        self.assertIn(both["decisionId"], {row["decisionId"] for row in
                                           self.context(decisionContext=reordered)["scopedDecisions"]})
        self.assertEqual(both["source"]["elementIds"], ["arrow", "wall-outline"])
        # Omitting the representation evidence keeps scope and drops exact-source.
        self.assertEqual([row["decisionId"] for row in
                          self.context(decisionContext={"domain": "drawing"})["scopedDecisions"]],
                         [scoped["decisionId"]])

    def test_a_parameter_binding_goes_stale_when_its_value_or_lock_moves(self) -> None:
        keep = self.design(typedBinding={"kind": "parameter", "parameterKey": "module"})
        locked = self.design(rawLanguage="plinth 锁住了", disposition="lock", targetRef="parameter:plinth",
                             typedBinding={"kind": "parameter", "parameterKey": "plinth"})
        self.assertEqual({row["decisionId"] for row in self.context()["scopedDecisions"]},
                         {keep["decisionId"], locked["decisionId"]})
        changed = self.later_run()
        pack = self.context(run_id=changed)
        self.assertEqual(pack["scopedDecisions"], [])
        # The retained decisions are untouched: staleness is derived, not written.
        self.assertEqual([row["typedBinding"]["value"] for row in self.decisions()], [1.2, 0.6])

    def later_run(self, run_id: str = "run-moved") -> str:
        payload = deepcopy(RECORD_PAYLOAD)
        for parameter in payload["parameters"]:
            if parameter["key"] == "module":
                parameter["value"] = 1.5
            elif parameter["key"] == "bay":
                parameter["value"] = 3.0
            elif parameter["key"] == "span":
                parameter["value"] = 6.0
            elif parameter["key"] == "plinth":
                parameter.pop("lock_authority")
        run = self.repository.create_run(run_id)
        retain_runner_receipt(self.repository, run, record_payload=payload,
                              design_state_digest=runner_state_digest(self.repository, run_id, payload))
        return run_id

    def test_decision_reads_never_enumerate_the_projects_runs(self) -> None:
        design = self.design()
        drawing = self.save(source=board_source(self.save_board("wall-outline"), "wall-outline"))
        binding = bound_project(self.client.app.state)
        design_source = {"kind": "design", "sourceRunId": REFERENCE_RUN_ID,
                         "stateDigest": self.state_digest, "sourceStageRef": None}
        _, record = binding.exact_state_record(binding.reference_run(REFERENCE_RUN_ID))
        board = {"domain": "drawing", "source": board_source(self.board_revision, "wall-outline")}
        # The existing projection is verified above. Decision selection compares
        # explicit design evidence against it instead of repeating its survey;
        # board evidence also reads only the exact Board run.
        with patch.object(ProjectBinding, "run_ids", side_effect=AssertionError("run enumeration")):
            self.assertEqual(len(self.decisions()), 2)
            for requested, expected in ((None, design),
                                        ({"domain": "design", "source": design_source}, design),
                                        (board, drawing)):
                context = decision_context_for(
                    binding, requested=requested, design_source=design_source, stage_ref=None,
                    focus=focus_refs(record, ("portico-base",)), record=record,
                )
                included, _ = compile_scoped_decisions(binding, context, record)
                self.assertEqual([row.decision_id for row in included], [expected["decisionId"]])

    def test_a_referenced_automatic_source_run_survives_collection(self) -> None:
        kept, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        dropped, job = self.run_candidate("set height to 2.4", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        digest = self.client.get("/api/state", params={"run": kept["candidateId"]}).json()["stateDigest"]
        self.design(rawLanguage="这一版的柱距就按这个来", targetRef="entity:portico-base",
                    source=self.design_source(kept["candidateId"], digest))
        value, revision = self.repository.read_working_draft()
        value["current"] = None
        for run_id in (kept["candidateId"], dropped["candidateId"]):
            value["runs"][run_id]["updatedAt"] = "2020-01-01T00:00:00+00:00"
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
        removed = self.repository.prune_working_draft(now="2026-09-20T00:00:00+00:00")
        self.assertEqual(removed, (dropped["candidateId"],))
        self.assertTrue(self.repository.layout.run(kept["candidateId"]).root.is_dir())
        self.assertEqual(len(self.decisions(self.new_client())), 1)

    def revise(self, current: dict, *, action: str, reason: str | None = None) -> dict:
        response = self.client.post(f"/api/decisions/{current['decisionId']}/revisions", json={
            "projectId": PROJECT_ID, "expectedRevisionRef": current["revisionRef"],
            "action": action, "reason": reason,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()


class DecisionStageScopeTests(DesignHistoryFixture):
    def test_a_stage_scope_applies_only_to_its_own_stage(self) -> None:
        stage = self.initialize()
        source = {"kind": "design", "sourceRunId": REFERENCE_RUN_ID,
                  "stateDigest": self.state_digest, "sourceStageRef": None}
        body = {"projectId": PROJECT_ID, "rawLanguage": "这一阶段的柱网保持不变", "disposition": "keep",
                "strength": "strong_preference", "targetRef": "entity:portico", "applicability": "scope",
                "sourceKind": "human", "source": source,
                "scope": {"domain": "design", "extent": "stage", "stageRef": stage["stageRef"]}}
        saved = self.client.post("/api/decisions", json=body)
        self.assertEqual(saved.status_code, 201, saved.text)
        decision_id = saved.json()["decisionId"]

        forged = ProjectRecordRef(PROJECT_ID, f"runs/{REFERENCE_RUN_ID}/reviews/"
                                  f"{record_file_name('design-stage', 'e' * 64)}", "e" * 64).uri
        refused = self.client.post("/api/decisions", json={
            **body, "scope": {"domain": "design", "extent": "stage", "stageRef": forged}})
        self.assertEqual(refused.status_code, 404, refused.text)

        under = self.pack(run_id=stage["candidateId"], sourceStageRef=stage["stageRef"])
        self.assertEqual([row["decisionId"] for row in under["scopedDecisions"]], [decision_id])
        # A run that is no Stage's candidate is under no Stage, and a decision
        # scoped to one does not follow it there.
        outside = self.unstaged_run()
        self.assertEqual(self.pack(run_id=outside)["scopedDecisions"], [])
        injected = self.pack(run_id=outside, expect=409,
                             decisionContext={"domain": "design", "stageRef": stage["stageRef"]})
        self.assertEqual(injected["code"], "DECISION_STAGE_MISMATCH")

    def unstaged_run(self, run_id: str = "run-unstaged") -> str:
        run = self.repository.create_run(run_id)
        retain_runner_receipt(self.repository, run,
                              design_state_digest=runner_state_digest(self.repository, run_id))
        return run_id

    def pack(self, *, run_id: str, expect: int = 200, **body) -> dict:
        state = self.client.get("/api/state", params={"run": run_id, **(
            {"sourceStageRef": body["sourceStageRef"]} if body.get("sourceStageRef") else {})})
        self.assertEqual(state.status_code, 200, state.text)
        response = self.client.post("/api/intents/context", json={
            "projectId": PROJECT_ID, "sourceRunId": run_id, "utterance": "继续这一阶段的工作",
            "stateDigest": state.json()["stateDigest"], **body,
        })
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()


if __name__ == "__main__":
    import unittest

    unittest.main()
