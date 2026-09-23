"""A real room cut plan remains a representation of one exact design source."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.adapters.occt_backend import occt_available
from archflow.project.refs import record_ref_from_uri
from monkeydiagram.drawing_elevation import read_model_axis_elevation
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase
from .test_intents import semantic_wall_edit


def room_edit():
    edit = semantic_wall_edit()
    wall = edit["entities"][0]
    fields = wall["fields"]
    fields["references"]["line"] = {"from": {"point": [0, "@front_shift"]}, "to": {"point": [4, "@front_shift"]}, "inward": [0, 1]}
    door = fields["params"]["openings"][0]
    door.update(shape="rectangular", head=2.1)
    door.pop("spring_height")
    edit["parameters"] = [row for row in edit["parameters"] if row["key"] != "passage_head"]
    edit["parameters"].append({"key": "front_shift", "value": 0, "unit": "m", "source_ref": "studio:intent"})
    for name, start, end, inward in (
        ("room-right", [4, 0], [4, 4], [-1, 0]),
        ("room-back", [4, 4], [0, 4], [0, -1]),
        ("room-left", [0, 4], [0, 0], [1, 0]),
    ):
        other = deepcopy(wall)
        other["entity_id"] = name
        other["fields"]["references"]["line"] = {"from": {"point": start}, "to": {"point": end}, "inward": inward}
        other["fields"]["params"]["openings"] = []
        edit["entities"].append(other)
    return edit


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class CutPlanTests(CandidateTestCase):
    def setUp(self):
        super().setUp()
        self.client.close()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        request = self.client.post("/api/proposals", json={"projectId": PROJECT_ID,
            "stateDigest": self.state_digest, "sourceRunId": REFERENCE_RUN_ID, "semanticEdit": room_edit()})
        self.assertEqual(request.status_code, 201, request.text)
        result = self.start(request.json()["proposalId"])
        self.assertEqual(self.finished(result["jobId"])["status"], "succeeded")
        candidate = self.client.get(f"/api/candidates/{result['candidateId']}").json()
        self.model = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
        accepted = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": self.model})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.stage = accepted.json()
        self.head = self.repository.read_head()
        self.branches = self.repository.read_design_branches()

    def generate(self, **changes):
        payload = {"projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"],
                   "drawingId": "room-plan", "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50,
                   "dimensions": [{"id": "door-width", "entityRef": "entity:passage-wall", "openingId": "passage-arch",
                                   "placement": {"offsetMm": 8}}], **changes}
        result = self.client.post("/api/drawings/plans", json=payload)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def status(self, doc, **changes):
        result = self.client.post("/api/drawings/plans/status", json={
            "runId": doc["runId"], "assetSha256": doc["assetSha256"], "revisionRef": doc["revisionRef"], **changes})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def commit_edit(self, edit):
        result = self.client.post("/api/proposals", json={"projectId": PROJECT_ID,
            "stateDigest": self.model["stateDigest"], "sourceRunId": self.model["runId"],
            "sourceStageRef": self.stage["stageRef"], "semanticEdit": edit})
        self.assertEqual(result.status_code, 201, result.text)
        job = self.start(result.json()["proposalId"])
        self.assertEqual(self.finished(job["jobId"])["status"], "succeeded")
        accepted = self.client.post(f"/api/candidates/{job['candidateId']}/accept", json={
            "projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": self.stage["stageRef"]})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        stage = accepted.json()
        model = next(row["modelSource"] for row in self.client.get(f"/api/candidates/{job['candidateId']}").json()["artifacts"] if row["format"] == "3dm")
        return stage, model

    def test_real_plan_representation_changes_reopen_with_source_and_old_revisions(self):
        first = self.generate()
        reading = self.status(first)
        self.assertEqual(reading["status"], "current", reading)
        self.assertTrue(reading["dimensions"][0]["canDrive"], reading)
        self.assertEqual(reading["dimensions"][0]["label"], "2000 mm")
        drawing = read_model_axis_elevation(self.repository, record_ref_from_uri(first["revisionRef"], PROJECT_ID))
        self.assertNotIn(b'data-object="obj-passage-wall-aperture-passage-arch"', drawing.svg)
        self.assertIn(b'data-object="obj-passage-wall-cut"', drawing.svg)
        second = self.generate(previousRevisionRef=first["revisionRef"], scaleDenominator=100, cutLineMm=.5,
            dimensions=[{"id": "door-width", "entityRef": "entity:passage-wall", "openingId": "passage-arch", "placement": {"offsetMm": 4}}])
        self.assertNotEqual(first["revisionRef"], second["revisionRef"])
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(self.repository.read_design_branches(), self.branches)
        retained = read_model_axis_elevation(self.repository, record_ref_from_uri(second["revisionRef"], PROJECT_ID))
        self.assertEqual(retained.receipt["previousRevisionRef"], first["revisionRef"])
        self.assertIn(b"2000 mm", retained.svg)
        with TestClient(create_app(self.settings)) as client:
            for doc in (first, second):
                result = client.get(f"/api/documents/{doc['assetSha256']}/bytes", params={"runId": doc["runId"], "revisionRef": doc["revisionRef"]})
                self.assertEqual(result.status_code, 200, result.text[:200] if result.status_code != 200 else "")
        with patch("archflow_studio_api.application.drawing_plans.freeze_cut_plan", side_effect=AssertionError("cache must not project")):
            again = self.generate()
        self.assertEqual(again, first)

    def test_move_rebuild_delete_and_stale_dimension_preserve_valid_intent(self):
        first = self.generate()
        newer, model = self.commit_edit({"summary": "Move the front wall", "parameters": [{"key": "front_shift", "value": .4}]})
        changed = self.status(first)
        self.assertEqual(changed["status"], "outdated", changed)
        self.assertEqual(changed["targetModelSource"], model)
        historical = self.status(first, targetModelSource=self.model, targetStageRef=self.stage["stageRef"])
        self.assertEqual(historical["status"], "current")
        self.assertFalse(historical["dimensions"][0]["canDrive"])
        refused = self.client.post("/api/drawings/plans/dimension-proposal", json={
            "projectId": PROJECT_ID, "runId": first["runId"], "assetSha256": first["assetSha256"],
            "revisionRef": first["revisionRef"], "dimensionId": "door-width", "value": 1.2,
            "targetModelSource": self.model, "targetStageRef": self.stage["stageRef"]})
        self.assertEqual(refused.status_code, 409, refused.text)
        second = self.generate(sourceStageRef=newer["stageRef"], previousRevisionRef=first["revisionRef"], dimensions=None)
        self.assertEqual(second["viewRecipe"], first["viewRecipe"])
        self.assertEqual(self.status(second)["status"], "current")
        self.stage, self.model = newer, model
        deleted, _ = self.commit_edit({"summary": "Remove front wall", "removeEntityIds": ["passage-wall"]})
        broken = self.status(second)
        self.assertEqual(broken["status"], "partially-broken", broken)
        third = self.generate(sourceStageRef=deleted["stageRef"], previousRevisionRef=second["revisionRef"], dimensions=None)
        self.assertEqual(third["viewRecipe"]["dimensions"], first["viewRecipe"]["dimensions"])
        self.assertFalse(self.status(third)["dimensions"][0]["canDrive"])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_real_dimension_change_is_an_existing_candidate_not_a_label_edit(self):
        document = self.generate()
        proposal = self.client.post("/api/drawings/plans/dimension-proposal", json={
            "projectId": PROJECT_ID, "runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "dimensionId": "door-width", "value": 1.2,
            "targetModelSource": self.model, "targetStageRef": self.stage["stageRef"]})
        self.assertEqual(proposal.status_code, 201, proposal.text)
        self.assertEqual(proposal.json()["baseStateDigest"], self.model["stateDigest"])
        job = self.start(proposal.json()["proposalId"])
        self.assertEqual(self.finished(job["jobId"])["status"], "succeeded")
        record = self.client.get("/api/state", params={"run": job["candidateId"]}).json()
        self.assertEqual(next(row["value"] for row in record["parameters"] if row["key"] == "passage_width"), 1.2)
        self.assertEqual(self.repository.read_design_branches(), self.branches)
        self.assertEqual(self.status(document)["dimensions"][0]["label"], "2000 mm")

    def test_invalid_inputs_fail_before_drawing_and_choices_are_semantic(self):
        choices = self.client.get("/api/drawings/plans/dimensions", params={
            "sourceRunId": self.model["runId"], "stateDigest": self.model["stateDigest"],
            "assetSha256": self.model["assetSha256"], "sourceStageRef": self.stage["stageRef"]})
        self.assertEqual(choices.status_code, 200, choices.text)
        self.assertEqual(len(choices.json()["dimensions"]), 1)
        self.assertEqual(choices.json()["dimensions"][0]["openingId"], "passage-arch")
        for changes in ({"cutHeight": 0, "bottom": 1}, {"cropUv": [0, 0, 0, 1]}, {"projectId": "other"},
                        {"previousRevisionRef": "not-a-record-ref"}):
            answer = self.client.post("/api/drawings/plans", json={"projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"], **changes})
            self.assertIn(answer.status_code, (403, 422), answer.text)
        self.assertEqual(self.client.get("/api/documents").json()["documents"], [])

    def test_omitting_stage_does_not_bypass_accepted_branch_freshness(self):
        document = self.generate(sourceStageRef=None, modelSource=self.model)
        self.assertEqual(document["sourceStageRef"], self.stage["stageRef"])
        self.commit_edit({"summary": "Move front wall", "parameters": [{"key": "front_shift", "value": .4}]})
        result = self.client.post("/api/drawings/plans/dimension-proposal", json={
            "projectId": PROJECT_ID, "runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "dimensionId": "door-width", "value": 1.2,
            "targetModelSource": self.model})
        self.assertEqual(result.status_code, 409, result.text)

    def test_read_set_detects_newly_entering_objects_and_uncertain_target(self):
        document = self.generate()
        wall = deepcopy(room_edit()["entities"][1])
        wall["entity_id"] = "entering-wall"
        wall["fields"]["references"]["line"] = {
            "from": {"point": [0, 20]}, "to": {"point": [4, 20]}, "inward": [0, 1]}
        self.stage, self.model = self.commit_edit({"summary": "Add wall outside view", "entities": [wall]})
        unchanged = self.status(document)
        self.assertEqual(unchanged["status"], "current", unchanged)
        self.assertTrue(unchanged["bindingChanged"])
        self.assertFalse(unchanged["dimensions"][0]["canDrive"])
        wall["fields"]["references"]["line"] = {
            "from": {"point": [0, 2]}, "to": {"point": [4, 2]}, "inward": [0, 1]}
        self.stage, self.model = self.commit_edit({"summary": "Move wall into view", "entities": [wall]})
        self.assertEqual(self.status(document)["status"], "outdated")
        unknown = self.status(document, targetStageRef="not-a-retained-stage")
        self.assertEqual(unknown["status"], "unknown", unknown)
