"""A real room cut plan remains a representation of one exact design source."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.adapters.occt_backend import occt_available
from archflow.project.refs import record_ref_from_uri
from monkeydiagram.drawing_elevation import read_model_axis_elevation
from archflow_studio_api.application.artifacts import _chain_head, _page_replacements, list_documents, replacement_cause
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase
from .test_intents import semantic_wall_edit
from .test_working_source import adopt


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


def page_of(document):
    """A drawing revision's one page, as every replacement names it."""
    return document["runId"], document["assetSha256"], document["revisionRef"], 0


def replacing(document):
    """The replacesPages entry naming this revision's page as the replaced one."""
    return {"runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": 0, "newPageIndex": 0}


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

    def links(self):
        """Every registered page replacement, and the documents they were read from."""
        documents = list_documents(bound_project(self.app.state))
        return _page_replacements(documents), {row.revision_ref: row for row in documents}

    def commit_edit(self, edit, branch_id="main"):
        result = self.client.post("/api/proposals", json={"projectId": PROJECT_ID,
            "stateDigest": self.model["stateDigest"], "sourceRunId": self.model["runId"],
            "sourceStageRef": self.stage["stageRef"], "semanticEdit": edit})
        self.assertEqual(result.status_code, 201, result.text)
        job = self.start(result.json()["proposalId"])
        self.assertEqual(self.finished(job["jobId"])["status"], "succeeded")
        accepted = self.client.post(f"/api/candidates/{job['candidateId']}/accept", json={
            "projectId": PROJECT_ID, "branchId": branch_id, "expectedHeadStageRef": self.stage["stageRef"]})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        stage = accepted.json()
        model = next(row["modelSource"] for row in self.client.get(f"/api/candidates/{job['candidateId']}").json()["artifacts"] if row["format"] == "3dm")
        return stage, model

    def test_dressing_typed_edits_reopen_and_preserve_design_and_old_vectors(self):
        person = {"id": "person-a", "assetId": "person-plan", "positionUv": [2, 2], "size": .65,
                  "flipped": False, "anchorObjectId": None}
        tree = {"id": "tree-a", "assetId": "tree-plan", "positionUv": [1, 1], "size": 1,
                "flipped": False, "anchorObjectId": None}
        first = self.generate(dressing=[person, tree])
        vector = self.client.get("/api/drawings/plans/vector", params={
            "runId": first["runId"], "assetSha256": first["assetSha256"], "revisionRef": first["revisionRef"]})
        self.assertEqual(vector.status_code, 200, vector.text)
        self.assertIn('data-dressing="person-a"', vector.json()["svg"])
        self.assertNotIn('data-object="person-a"', vector.json()["svg"])
        self.assertEqual({row["id"] for row in vector.json()["assets"]}, {"person-plan", "tree-plan"})
        self.assertIn("obj-passage-wall-cut", {row["objectId"] for row in vector.json()["anchors"]})
        second = self.generate(previousRevisionRef=first["revisionRef"], dressingOperations=[
            {"op": "move", "id": "person-a", "positionUv": [3, 2]},
            {"op": "scale", "id": "person-a", "size": .9},
            {"op": "flip", "id": "person-a", "flipped": True},
            {"op": "delete", "id": "tree-a"},
            {"op": "insert", "id": "person-b", "object": {**person, "id": "person-b"}},
        ])
        self.assertEqual(second["viewRecipe"]["dressing"], [{**person, "positionUv": [3, 2], "size": .9, "flipped": True}, {**person, "id": "person-b"}])
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(self.repository.read_design_branches(), self.branches)
        old = read_model_axis_elevation(self.repository, record_ref_from_uri(first["revisionRef"], PROJECT_ID))
        new = read_model_axis_elevation(self.repository, record_ref_from_uri(second["revisionRef"], PROJECT_ID))
        self.assertNotEqual(old.png, new.png)
        self.assertIn(b'data-dressing="tree-a"', old.svg)
        self.assertNotIn(b'data-dressing="tree-a"', new.svg)
        self.assertEqual(old.receipt["projection"]["selected_object_ids"], new.receipt["projection"]["selected_object_ids"])
        with TestClient(create_app(self.settings)) as client:
            cold = client.get("/api/documents").json()["documents"]
            reopened = next(row for row in cold if row["revisionRef"] == second["revisionRef"])
            self.assertEqual(reopened["viewRecipe"]["dressing"], second["viewRecipe"]["dressing"])
            again = client.get("/api/drawings/plans/vector", params={
                "runId": first["runId"], "assetSha256": first["assetSha256"], "revisionRef": first["revisionRef"]})
            self.assertEqual(again.json(), vector.json())
        self.assertEqual(self.status(second)["status"], "current")

    def test_dressing_anchor_follows_exact_object_and_survives_deleted_anchor_without_rebinding(self):
        fixed = {"id": "fixed", "assetId": "tree-plan", "positionUv": [1, 2], "size": .5}
        anchored = {"id": "anchored", "assetId": "person-plan", "positionUv": [0, 1], "size": .5,
                    "anchorObjectId": "obj-passage-wall-cut"}
        first = self.generate(dimensions=[], dressing=[fixed, anchored])
        first_read = self.status(first)["dressing"]
        newer, model = self.commit_edit({"summary": "Move wall", "parameters": [{"key": "front_shift", "value": .4}]})
        changed = self.status(first)
        self.assertEqual(changed["status"], "outdated", changed)
        self.assertAlmostEqual(changed["dressing"][1]["resolvedUv"][1] - first_read[1]["resolvedUv"][1], .4)
        second = self.generate(sourceStageRef=newer["stageRef"], previousRevisionRef=first["revisionRef"], dimensions=[])
        self.assertEqual(second["viewRecipe"]["dressing"], first["viewRecipe"]["dressing"])
        self.assertEqual(self.status(second)["dressing"][0]["resolvedUv"], first_read[0]["resolvedUv"])
        self.stage, self.model = newer, model
        removed, _ = self.commit_edit({"summary": "Remove the anchor wall", "removeEntityIds": ["passage-wall"]})
        missing = self.status(second)
        self.assertEqual(missing["status"], "partially-broken", missing)
        self.assertEqual(missing["dressing"][1]["status"], "missing")
        self.assertIsNone(missing["dressing"][1]["resolvedUv"])
        third = self.generate(sourceStageRef=removed["stageRef"], previousRevisionRef=second["revisionRef"], dimensions=[])
        self.assertEqual(third["viewRecipe"]["dressing"], second["viewRecipe"]["dressing"])
        drawing = read_model_axis_elevation(self.repository, record_ref_from_uri(third["revisionRef"], PROJECT_ID))
        self.assertIn(b'data-dressing="fixed"', drawing.svg)
        self.assertNotIn(b'data-dressing="anchored"', drawing.svg)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_dressing_invalid_edits_are_atomic_and_outside_crop_is_explicit(self):
        first = self.generate(dimensions=[], dressing=[{"id": "person", "assetId": "person-plan", "positionUv": [100, 100], "size": .5}])
        self.assertEqual(self.status(first)["dressing"][0]["status"], "outside-view")
        before = self.client.get("/api/documents").json()
        for changes in (
            {"dressing": [{"id": "bad", "assetId": "tree-plan", "positionUv": [0, 0], "size": 1, "anchorObjectId": "not-a-wall"}]},
            {"dressingOperations": [{"op": "move", "id": "person", "positionUv": [2, 2]}, {"op": "delete", "id": "missing"}]},
            {"dressingOperations": [{"op": "scale", "id": "person", "size": 0}]},
            {"dressingOperations": [{"op": "flip", "id": "person"}]},
            {"dressingOperations": [{"op": "move", "id": "person", "positionUv": [1, 1], "size": 1}]},
            {"dressing": [], "dressingOperations": []},
        ):
            response = self.client.post("/api/drawings/plans", json={"projectId": PROJECT_ID,
                "sourceStageRef": self.stage["stageRef"], "previousRevisionRef": first["revisionRef"], **changes})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/documents").json(), before)
        self.assertEqual(self.repository.read_design_branches(), self.branches)

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
        own = self.status(document, targetModelSource=self.model, targetStageRef=self.stage["stageRef"])
        self.assertEqual(own["dimensions"][0]["label"], "2000 mm")
        # Shown, not adopted: the drawing still reads the editing base.
        self.assertEqual(self.status(document)["status"], "current")
        adopt(self.client, job["candidateId"])
        # Continued: the retained drawing is stale on the new base, not relabelled.
        live = self.status(document)
        self.assertEqual((live["status"], live["dimensions"][0]["label"]), ("outdated", "1200 mm"))

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

    def test_multiple_accepted_stages_require_explicit_source_even_after_both_heads_advance(self):
        original_stage, original_model = self.stage, self.model
        alternate = self.client.post("/api/design-stages/initialize", json={
            "projectId": PROJECT_ID, "modelSource": self.model, "branchId": "alternate"})
        self.assertEqual(alternate.status_code, 201, alternate.text)
        self.assertNotEqual(alternate.json()["stageRef"], self.stage["stageRef"])
        for advanced in (False, True):
            if advanced:
                self.commit_edit({"summary": "Advance main", "parameters": [{"key": "front_shift", "value": .2}]})
                self.stage = alternate.json()
                self.stage, self.model = self.commit_edit({"summary": "Advance alternate",
                    "parameters": [{"key": "front_shift", "value": .4}]}, branch_id="alternate")
            with self.subTest(heads_advanced=advanced):
                refused = self.client.post("/api/drawings/plans", json={
                    "projectId": PROJECT_ID, "modelSource": original_model})
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertEqual(refused.json()["code"], "DRAWING_SOURCE_AMBIGUOUS")
                current = self.generate()
                proposal = self.client.post("/api/drawings/plans/dimension-proposal", json={
                    "projectId": PROJECT_ID, "runId": current["runId"], "assetSha256": current["assetSha256"],
                    "revisionRef": current["revisionRef"], "dimensionId": "door-width", "value": 1.2,
                    "targetModelSource": self.model, "targetStageRef": self.stage["stageRef"]})
                self.assertEqual(proposal.status_code, 201, proposal.text)
                self.assertEqual(proposal.json()["sourceStageRef"], self.stage["stageRef"])
        old = self.generate(sourceStageRef=original_stage["stageRef"], modelSource=original_model)
        self.assertEqual(old["sourceStageRef"], original_stage["stageRef"])
        self.assertFalse(self.status(old, targetStageRef=original_stage["stageRef"])["dimensions"][0]["canDrive"])

    def test_unaccepted_drawing_remains_valid_but_later_ambiguous_acceptance_cannot_drive(self):
        proposed = self.client.post("/api/proposals", json={"projectId": PROJECT_ID,
            "stateDigest": self.model["stateDigest"], "sourceRunId": self.model["runId"],
            "sourceStageRef": self.stage["stageRef"], "semanticEdit": {
                "summary": "Unaccepted width candidate", "parameters": [{"key": "passage_width", "value": 1.8}]}})
        self.assertEqual(proposed.status_code, 201, proposed.text)
        job = self.start(proposed.json()["proposalId"])
        self.assertEqual(self.finished(job["jobId"])["status"], "succeeded")
        candidate = self.client.get(f"/api/candidates/{job['candidateId']}").json()
        model = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
        # The architect continues from the unaccepted candidate; only its editing base may drive a change.
        adopt(self.client, job["candidateId"])
        document = self.generate(sourceStageRef=None, modelSource=model)
        self.assertIsNone(document["sourceStageRef"])
        payload = {"projectId": PROJECT_ID, "runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "dimensionId": "door-width", "value": 1.2, "targetModelSource": model}
        allowed = self.client.post("/api/drawings/plans/dimension-proposal", json=payload)
        self.assertEqual(allowed.status_code, 201, allowed.text)
        self.assertEqual(allowed.json()["sourceStageRef"], self.stage["stageRef"])
        for branch in ("first", "second"):
            initial = self.client.post("/api/design-stages/initialize", json={
                "projectId": PROJECT_ID, "modelSource": model, "branchId": branch})
            self.assertEqual(initial.status_code, 201, initial.text)
            self.stage, self.model = initial.json(), model
            self.commit_edit({"summary": f"Advance {branch}", "parameters": [{"key": "front_shift", "value": .2}]}, branch_id=branch)
        unknown = self.status(document, targetModelSource=model)
        self.assertEqual(unknown["status"], "unknown", unknown)
        self.assertEqual(unknown["dimensions"], [])
        refused = self.client.post("/api/drawings/plans/dimension-proposal", json=payload)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "DRAWING_SOURCE_AMBIGUOUS")
        retained = next(row for row in self.client.get("/api/documents").json()["documents"]
                        if row["revisionRef"] == document["revisionRef"])
        self.assertEqual(retained, document, "Ambiguity must not rewrite the retained drawing's source.")

    def propose_move(self, shift):
        """An unaccepted continuation of the current Stage model, returned with its exact model."""
        result = self.client.post("/api/proposals", json={"projectId": PROJECT_ID,
            "stateDigest": self.model["stateDigest"], "sourceRunId": self.model["runId"],
            "sourceStageRef": self.stage["stageRef"], "semanticEdit": {
                "summary": "Move the front wall", "parameters": [{"key": "front_shift", "value": shift}]}})
        self.assertEqual(result.status_code, 201, result.text)
        job = self.start(result.json()["proposalId"])
        self.assertEqual(self.finished(job["jobId"])["status"], "succeeded")
        candidate = self.client.get(f"/api/candidates/{job['candidateId']}").json()
        return job["candidateId"], next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")

    def test_status_follows_the_working_head_before_anything_is_accepted(self):
        first = self.generate()
        candidate, model = self.propose_move(.4)
        adopt(self.client, candidate)
        self.assertEqual(self.client.get("/api/working-source").json()["head"]["runId"], candidate)
        stale = self.status(first)
        self.assertEqual(stale["status"], "outdated", stale)
        self.assertEqual((stale["targetModelSource"], stale["targetStageRef"]), (model, None))
        drive = {"projectId": PROJECT_ID, "dimensionId": "door-width", "value": 1.2}
        refused = self.client.post("/api/drawings/plans/dimension-proposal", json={**drive,
            "runId": first["runId"], "assetSha256": first["assetSha256"], "revisionRef": first["revisionRef"]})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "STALE_BASE")

        live = self.generate(sourceStageRef=None, modelSource=model, previousRevisionRef=first["revisionRef"])
        self.assertIsNone(live["sourceStageRef"])
        current = self.status(live)
        self.assertEqual((current["status"], current["bindingChanged"]), ("current", False), current)
        self.assertTrue(current["dimensions"][0]["canDrive"], current)
        proposal = self.client.post("/api/drawings/plans/dimension-proposal", json={**drive,
            "runId": live["runId"], "assetSha256": live["assetSha256"], "revisionRef": live["revisionRef"]})
        self.assertEqual(proposal.status_code, 201, proposal.text)
        self.assertEqual(proposal.json()["sourceRunId"], candidate)
        self.assertEqual(self.repository.read_design_branches(), self.branches)

    def test_a_head_without_an_exact_step_is_reported_not_rebound(self):
        first = self.generate()
        position = self.client.get("/api/working-draft").json()
        moved = self.client.put("/api/working-draft", json={"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID,
                                "baseRevisionSha256": position["revisionSha256"]})
        self.assertEqual(moved.status_code, 200, moved.text)
        blocked = self.status(first)
        self.assertEqual(blocked["status"], "outdated", blocked)
        self.assertIsNone(blocked["targetModelSource"])
        self.assertIn("cannot be drawn yet", blocked["detail"])
        retained = next(row for row in self.client.get("/api/documents").json()["documents"]
                        if row["revisionRef"] == first["revisionRef"])
        self.assertEqual(retained, first)

    def test_each_new_cut_plan_is_its_own_drawing(self):
        first = self.generate(drawingId=None)
        second = self.generate(drawingId=None, cutHeight=1.0)
        self.assertEqual((first["drawingId"], second["drawingId"]), ("floor-plan", "floor-plan-2"))
        revised = self.generate(drawingId=None, previousRevisionRef=first["revisionRef"], scaleDenominator=100)
        self.assertEqual(revised["drawingId"], "floor-plan")

    def test_a_drawing_kept_on_a_chosen_version_stays_there_until_rebuilt(self):
        kept = self.generate(follow="frozen")
        self.assertEqual(kept["viewRecipe"]["follow"], "frozen")
        candidate, model = self.propose_move(.4)
        adopt(self.client, candidate)
        self.assertEqual(self.client.get("/api/working-source").json()["head"]["runId"], candidate)
        status = self.status(kept)
        self.assertEqual((status["status"], status["bindingChanged"]), ("current", False), status)
        self.assertEqual(status["targetStageRef"], self.stage["stageRef"])
        self.assertIn("stays on the version", status["detail"])
        self.assertFalse(status["dimensions"][0]["canDrive"], status)
        [drawing] = [row for row in self.client.get("/api/worktrees").json()["representations"] if row["kind"] == "drawing"]
        self.assertEqual((drawing["itemId"], drawing["state"]), ("room-plan", "frozen"))

        # Later revisions keep the choice; only an explicit live rebuild follows the head again.
        styled = self.generate(previousRevisionRef=kept["revisionRef"], scaleDenominator=100)
        self.assertEqual(styled["viewRecipe"]["follow"], "frozen")
        live = self.generate(sourceStageRef=None, modelSource=model, previousRevisionRef=styled["revisionRef"], follow="live")
        self.assertNotIn("follow", live["viewRecipe"])
        self.assertEqual(self.status(live)["status"], "current")
        [drawing] = [row for row in self.client.get("/api/worktrees").json()["representations"] if row["kind"] == "drawing"]
        self.assertEqual(drawing["state"], "current")

    def test_a_rebuild_registers_the_replacement_of_its_previous_revision(self):
        first = self.generate()
        styled = self.generate(previousRevisionRef=first["revisionRef"], cutLineMm=.5)
        self.assertEqual((first["replacesPages"], styled["replacesPages"]), ([], [replacing(first)]))
        newer, _ = self.commit_edit({"summary": "Move the front wall", "parameters": [{"key": "front_shift", "value": .4}]})
        rebuilt = self.generate(sourceStageRef=newer["stageRef"], previousRevisionRef=styled["revisionRef"])
        self.assertEqual(rebuilt["replacesPages"], [replacing(styled)])
        links, documents = self.links()
        # The one existing chain reader walks from the first page to the newest.
        self.assertEqual(_chain_head(links, page_of(first))[0], page_of(rebuilt))
        causes = [replacement_cause(documents[old["revisionRef"]], documents[new["revisionRef"]])
                  for old, new in ((first, styled), (styled, rebuilt))]
        self.assertEqual(causes, ["representation", "source"])
        self.assertEqual(self.repository.read_head(), self.head)
        with TestClient(create_app(self.settings)) as client:
            cold = {row["revisionRef"]: row for row in client.get("/api/documents").json()["documents"]}
        self.assertEqual([cold[row["revisionRef"]]["replacesPages"] for row in (first, styled, rebuilt)],
                         [[], [replacing(first)], [replacing(styled)]])

    def test_a_rebuild_from_a_replaced_revision_forks_without_a_relation(self):
        first = self.generate()
        second = self.generate(previousRevisionRef=first["revisionRef"], cutLineMm=.5)
        fork = self.generate(previousRevisionRef=first["revisionRef"], cutLineMm=.6)
        self.assertEqual(fork["drawingId"], first["drawingId"])
        self.assertEqual(fork["replacesPages"], [])
        links, _ = self.links()
        self.assertEqual(links, {page_of(first): page_of(second)})
        self.assertEqual(self.repository.read_design_branches(), self.branches)

    def test_a_crop_change_that_alters_the_aspect_registers_no_relation(self):
        first = self.generate()
        x0, y0, x1, y1 = first["viewRecipe"]["frame"]["crop_uv"]
        wider = self.generate(previousRevisionRef=first["revisionRef"], cropUv=[x0 - 2, y0, x1 + 2, y1])
        old, new = first["pages"][0], wider["pages"][0]
        self.assertGreater(abs(old["width"] / old["height"] - new["width"] / new["height"]), .1)
        self.assertEqual(wider["replacesPages"], [])
        # Moving the same window keeps its page shape, and so its place on a board.
        moved = self.generate(previousRevisionRef=wider["revisionRef"], cropUv=[x0 - 1, y0 + 1, x1 + 3, y1 + 1])
        self.assertEqual(moved["replacesPages"], [replacing(wider)])
        links, _ = self.links()
        self.assertEqual(links, {page_of(wider): page_of(moved)})

    def test_the_cache_hit_registers_nothing(self):
        first = self.generate()
        second = self.generate(previousRevisionRef=first["revisionRef"], cutLineMm=.5)
        before = self.client.get("/api/documents").json()
        with patch("archflow_studio_api.application.drawing_plans.freeze_cut_plan", side_effect=AssertionError("cache must not project")):
            again = self.generate(previousRevisionRef=second["revisionRef"],
                                  cutLineMm=first["viewRecipe"]["graphics"]["cutLineMm"])
        self.assertEqual(again, first)
        self.assertEqual(self.client.get("/api/documents").json(), before)
        links, _ = self.links()
        self.assertEqual(links, {page_of(first): page_of(second)})
        self.assertEqual(_chain_head(links, page_of(first))[0], page_of(second))
