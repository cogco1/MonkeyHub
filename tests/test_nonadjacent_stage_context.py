"""A retained opening interface crosses an unrelated Stage into real lintel CAD.

The 150 mm bearing is this fixture's condition, not structural design advice.
All projects are disposable P036 fixtures; no provider or user CAD app is used.
"""
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/archflow-studio/api"))

from fastapi.testclient import TestClient

from archflow.adapters import occt_backend
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import (
    Entity, StateRecordEditKind, StateRecordOperator, compile_parameter_locks,
)
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.candidate import run_operator
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from tests import test_window_relational_update as window
from tests.test_project_runner import _no_rhino

# Load API fixtures by their full package so they do not shadow kernel tests.
support = import_module("apps.archflow-studio.api.tests.support")
CandidateTestCase = import_module("apps.archflow-studio.api.tests.test_candidate").CandidateTestCase
register_model = import_module("apps.archflow-studio.api.tests.test_working_copies").register_model
PROJECT_ID, SEATS_PAYLOAD = support.PROJECT_ID, support.SEATS_PAYLOAD
retain_runner_receipt = support.retain_runner_receipt
runner_state_digest = support.runner_state_digest
write_runner_seats = support.write_runner_seats


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class NonAdjacentStageContextTests(CandidateTestCase):
    def setUp(self):
        super().setUp()
        for patcher in _no_rhino():
            patcher.start()
            self.addCleanup(patcher.stop)
        record = replace(window._record(checked=True), project_id=PROJECT_ID)
        # Use the construction slice: retain the measured bearing relation.
        # This is a bare opening without frame/glass or a room/outside claim;
        # the filled-window fixture and its unchecked spatial relation remain
        # covered separately by test_window_relational_update.
        wall = record.entity(window.HOST)
        params = deepcopy(dict(wall.fields["params"]))
        params["openings"][0].pop("interface_ref")
        params["openings"][0].pop("type_id")
        params.pop("types")
        record = replace(record,
            entities=tuple(replace(e, fields={**e.fields, "params": params}) if e == wall else e
                           for e in record.entities if e.entity_id != "window-interface"),
            relations=tuple(r for r in record.relations if r.relation_id == window.BEARING_RELATION))
        record = replace(record, parameters=tuple(
            replace(p, lock_authority="fixture:confirmed-opening", source_ref="entity:opening-condition")
            if p.key in {"window_left", "window_width"} else p for p in record.parameters),
            entities=record.entities + (
                Entity("opening-condition", "Reading@1", {
                    "subject_refs": ["parameter:window_width"],
                    "note": "Confirmed 1.2 m opening; the lintel reads this width with 0.15 m end bearing.",
                }),
                Entity("lintel-review", "Reading@1", {
                    "subject_refs": [f"entity:{window.LINTEL}"], "note": "Check the retained bearing interface.",
                }),
                Entity("fixed-review", "Reading@1", {
                    "subject_refs": [f"entity:{window.FIXED}"], "note": "Independent retained height.",
                }),
            ))
        seats = deepcopy(SEATS_PAYLOAD)
        seats["seats"][0]["owned_component_ids"] = ["building"]
        write_runner_seats(self.repository, seats)
        run = self.repository.create_run("opening-input")
        digest = runner_state_digest(self.repository, run.run_id, record.to_dict())
        retain_runner_receipt(self.repository, run, record_payload=record.to_dict(), design_state_digest=digest)
        self.client.close()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.restart()
        self.initial_head = self.repository.read_head()

    def restart(self):
        self.client.close()
        self.repository = FilesystemProjectRepository.open(self.root / PROJECT_ID)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def projection(self, run_id, stage=None):
        return project_state(bound_project(self.app.state), run_id=run_id,
                             source_stage_ref=None if stage is None else record_ref_from_uri(stage["stageRef"], PROJECT_ID))

    def execute(self, source, run_id, *, stage=None, element=None, height=None, unlock=False, width=None):
        projection = self.projection(source, stage)
        record = projection.record
        if unlock:
            operator = compile_parameter_locks(record, parameter_keys=("window_width",), lock_authority=None)
        elif width is not None:
            operator = window._width_edit(record, width)
        else:
            operator = StateRecordOperator(
                kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest,
                base_state_digest=record.state_digest, target_ref=f"entity:{element}", key="height", value=height)
        receipt = run_operator(bound_project(self.app.state), self.settings, operator, run_id,
                               source_run_id=source,
                               source_stage_ref=None if stage is None else record_ref_from_uri(stage["stageRef"], PROJECT_ID))
        self.assertTrue(receipt["seat_execution_complete"], receipt)
        return receipt

    def initialize(self, run_id):
        result = self.client.get(f"/api/candidates/{run_id}")
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        native = next(row for row in payload["artifacts"] if row["format"] == "3dm")
        data = self.client.get(f"/api/artifacts/{native['sha256']}/bytes").content
        # Same real-model registration used by design-history integration tests.
        model = register_model(self.client, run_id, payload["stateDigest"], data + b"\n")["modelSource"]
        response = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": model})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def accept(self, run_id, previous):
        response = self.client.post(f"/api/candidates/{run_id}/accept", json={
            "projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": previous["stageRef"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save_decision(self, stage, key, *, extent="targets", applicability="scope"):
        response = self.client.post("/api/decisions", json={
            "projectId": PROJECT_ID, "rawLanguage": f"Keep {key} at its confirmed value for this interface.",
            "messageSource": {"sessionId": "discarded-fixture-chat", "messageId": f"{key}-{extent}-{applicability}"},
            "sourceKind": "human", "disposition": "lock", "strength": "hard", "targetRef": f"parameter:{key}",
            "scope": {"domain": "design", "extent": extent,
                      **({"stageRef": stage["stageRef"]} if extent == "stage" else {"targetRefs": [f"parameter:{key}"]})},
            "applicability": applicability, "typedBinding": {"kind": "parameter", "parameterKey": key},
            "source": {"kind": "design", "sourceRunId": stage["candidateId"],
                       "sourceStageRef": stage["stageRef"], "stateDigest": stage["modelSource"]["stateDigest"]},
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def pack(self, run_id, *, element=window.LINTEL, **extra):
        projection = self.projection(run_id)
        response = self.client.post("/api/intents/context", json={
            "projectId": PROJECT_ID, "sourceRunId": run_id, "stateDigest": projection.state_digest,
            "elementIds": [element], "utterance": "Review this element and its retained interfaces", **extra,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_cold_nonadjacent_interface_and_local_reopening(self):
        self.execute("opening-input", "confirmed-opening", element=window.FIXED, height=3.1)
        opening = self.initialize("confirmed-opening")
        width_decision = self.save_decision(opening, "window_width")
        left_decision = self.save_decision(opening, "window_left")
        stage_only = self.save_decision(opening, "window_width", extent="stage")
        exact_only = self.save_decision(opening, "window_width", applicability="exact-source")
        # S1 changes an independent element. S2 consumes S0's opening interface.
        self.execute("confirmed-opening", "independent-stage", stage=opening, element=window.FIXED, height=3.3)
        intermediate = self.accept("independent-stage", opening)
        original = self.execute("independent-stage", "lintel-stage", stage=intermediate, element=window.LINTEL, height=0.3)
        lintel = self.accept("lintel-stage", intermediate)
        self.assertEqual(lintel["parentStageRef"], intermediate["stageRef"])
        self.assertEqual(intermediate["parentStageRef"], opening["stageRef"])
        self.restart()
        before_read = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        pack = self.pack("lintel-stage")
        self.assertEqual({d["decisionId"] for d in pack["scopedDecisions"]},
                         {width_decision["decisionId"], left_decision["decisionId"]})
        self.assertNotIn(stage_only["decisionId"], {d["decisionId"] for d in pack["scopedDecisions"]})
        self.assertNotIn(exact_only["decisionId"], {d["decisionId"] for d in pack["scopedDecisions"]})
        self.assertEqual(pack["scopedDecisions"][0]["source"], width_decision["source"])
        facts = pack["context"]
        self.assertIn("parameter:window_width", facts["readOnlyRefs"])
        self.assertIn("entity:opening-condition", facts["readOnlyRefs"])
        self.assertEqual(next(p for p in facts["parameters"] if p["key"] == "window_width")["lockAuthority"],
                         "fixture:confirmed-opening")
        self.assertEqual(pack["confirmedStage"]["stageRef"], lintel["stageRef"])
        self.assertTrue(pack["confirmedStage"]["isSource"])
        self.assertEqual(self.pack("lintel-stage", element=window.FIXED)["scopedDecisions"], [])
        self.assertEqual(self.pack("lintel-stage", decisionContext={"domain": "copy"})["scopedDecisions"], [])
        self.assertEqual(before_read, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

        # An explicit unlock reopens only width. Reading an interface never unlocks it.
        self.execute("lintel-stage", "unlocked-opening", stage=lintel, unlock=True)
        updated = self.execute("unlocked-opening", "wider-opening", stage=lintel, width=1.6)
        self.restart()
        reopened = self.pack("wider-opening")
        self.assertEqual([d["decisionId"] for d in reopened["scopedDecisions"]], [left_decision["decisionId"]])
        review = reopened["confirmedStage"]["changes"]["needsReviewRefs"]
        self.assertTrue({"parameter:window_width", f"entity:{window.HOST}", f"entity:{window.LINTEL}",
                         "entity:opening-condition", "entity:lintel-review"} <= set(review))
        self.assertNotIn(f"entity:{window.FIXED}", review)
        self.assertNotIn("entity:fixed-review", review)
        self.assertFalse(reopened["confirmedStage"]["isSource"])
        old, new = window._measured(original), window._measured(updated)
        self.assertEqual(old[window.FIXED_OBJECT], new[window.FIXED_OBJECT])
        self.assertAlmostEqual(new[window.APERTURE].bbox_max[0] - old[window.APERTURE].bbox_max[0], 0.4)
        self.assertAlmostEqual(new[window.APERTURE].bbox_min[0] - new[window.LINTEL_OBJECT].bbox_min[0], 0.15)
        self.assertAlmostEqual(new[window.LINTEL_OBJECT].bbox_max[0] - new[window.APERTURE].bbox_max[0], 0.15)
        seat = updated["seat_results"][0]
        retained = self.repository.load_json(record_ref_from_uri(seat["receipt_ref"], PROJECT_ID))
        self.assertIn(window.FIXED, retained["reused_element_ids"])
        checks = self.repository.load_json(record_ref_from_uri(seat["relation_check_ref"], PROJECT_ID))
        bearing = next(row for row in checks["checks"] if row["relation_id"] == window.BEARING_RELATION)
        self.assertEqual(bearing["status"], "held", bearing)
        current = self.projection("wider-opening").record
        self.assertEqual(current.entity(window.FIXED), self.projection("lintel-stage").record.entity(window.FIXED))
        self.assertEqual(self.repository.read_head(), self.initial_head)
        history = self.client.get("/api/design-history", params={"branchId": "main"}).json()
        self.assertEqual([s["stageRef"] for s in history["stages"]],
                         [opening["stageRef"], intermediate["stageRef"], lintel["stageRef"]])
        # All original decision revisions remain readable; derived stale is not deletion.
        decisions = self.client.get("/api/decisions").json()["decisions"]
        self.assertIn(width_decision, decisions)
