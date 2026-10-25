"""An imported model made into a design that can be changed (#300 item 22 B).

Two ways a model from outside reaches the project, both run here through the
real candidate route with OCCT exports:

* a snapshot attached on its own (0925's Live Rhino snapshots): one click
  proposes the design, a component per part and an element per object; its
  run's composed model is the file's objects placed by the record, and an
  object can then be removed, moved and given a material like any other;
* a complete model uploaded for a design whose record already has an element
  per object, named as the element but carrying no identity (housing): a
  click names the element by that name (its patch in place is
  ``monkeycad``'s ``test_three_dm_imported``).

Models are read through the CAD owner's inspector, never opened here.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from monkeycad.formats.three_dm_inspector import inspect_three_dm_index
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

PROJECT_ID = "imported-design"
FIXTURE = Path(__file__).parent / "fixtures" / "snapshot-parts.3dm"
EVIDENCE = "input:monkeyarch-modeling-setup"
RECORD = {
    "schema": "StateRecord@1", "project_id": PROJECT_ID, "run_id": "authored", "base": None,
    "basis_refs": [], "decision_ref": None, "evidence_refs": [EVIDENCE], "invalidated_refs": [], "obligations": [],
    "option": {"option_id": "modeling"}, "parameters": [], "predecessor_ref": None, "relations": [],
    "entities": [
        {"entity_id": "model", "schema": "Component@1", "parent_id": None, "basis_refs": [],
         "fields": {"intent": "Root for candidate modeling", "semantic_kind": "building", "source_refs": [EVIDENCE]}},
        {"entity_id": "ground", "schema": "Level@1", "parent_id": None, "basis_refs": [EVIDENCE],
         "fields": {"elevation": 0.0, "role": "ground"}},
    ],
}
SEATS = {"schema": "RunnerSeats@1", "branch_id": "runner-v1", "commitment_ref": "commitment:monkeyarch-candidate-modeling",
         "seats": [{"seat_id": "modeler", "disciplines": ["structure_support"], "owned_component_ids": ["model"],
                    "phases": ["design_development"], "reviewer": False, "consumes": []}]}


def _occt_available() -> bool:
    from monkeycad.backends.occt.kernel import occt_available

    return occt_available()


@unittest.skipUnless(_occt_available(), "cadquery-ocp is not installed")
class ImportedDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        repository = FilesystemProjectRepository.initialize(
            self.root / PROJECT_ID, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
            authored_record=json.loads(json.dumps(RECORD)), seat_pack=SEATS)
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=repository.layout.root)))
        self.addCleanup(self.client.close)

    def run_proposal(self, response) -> str:
        self.assertEqual(response.status_code, 201, response.text)
        started = self.client.post(f"/api/proposals/{response.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{started.json()['jobId']}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "succeeded", job)
        return started.json()["candidateId"]

    def composed(self, run_id: str) -> dict[str, dict]:
        """The run's composed model, each top-level object by name: its id, bounds and user strings."""

        rows = [row for row in self.client.get(f"/api/candidates/{run_id}").json()["artifacts"]
                if row["representation"] == "composed"]
        self.assertEqual(len(rows), 1, rows)
        return _objects(self.client.get(f"/api/artifacts/{rows[0]['sha256']}/bytes").content)

    def state(self, run_id: str) -> str:
        return self.client.get("/api/state", params={"run": run_id}).json()["stateDigest"]

    def test_a_snapshot_becomes_a_design_whose_objects_are_removed_moved_and_dressed(self) -> None:
        data = FIXTURE.read_bytes()
        attached = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "fileName": "0928 snapshot.3dm", "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(attached.status_code, 201, attached.text)
        sha = attached.json()["sha256"]
        source = {row["object_id"] for row in inspect_three_dm_index(data, bounds_for=lambda ids: ids)["objects"]
                  if not row["is_instance_definition_object"]}

        # Named, or by default the model attached last: here the same one.
        named = self.client.post("/api/model-assets/design", json={"assetSha256": sha})
        self.assertEqual(named.status_code, 201, named.text)
        converted = self.run_proposal(self.client.post("/api/model-assets/design", json={}))
        objects = self.composed(converted)
        self.assertEqual({row["id"] for row in objects.values()}, set(source))
        self.assertTrue(all(name.startswith("obj-imported-") for name in objects))
        # The catalog the Hub edits through names this run and its placed objects, so a pick can be deleted or moved.
        catalog = self.client.get("/api/state", params={"run": converted}).json()["catalog"]
        self.assertEqual(catalog["inspectionRun"], converted)
        self.assertEqual({row["name"] for row in catalog["objects"]}, set(objects))

        name, picked_object = sorted(objects.items())[0]
        pick = self.client.post("/api/pick/resolve", json={
            "stateDigest": self.state(converted), "sourceRunId": converted, "objectName": name,
            "userStrings": {"archflow:component": picked_object["strings"]["archflow:component"]}}).json()
        self.assertEqual(pick["status"], "resolved", pick)
        component = pick["componentId"]

        others = sorted(set(objects) - {name})
        removed_element = others[0][len("obj-"):]
        removed = self.run_proposal(self.client.post("/api/proposals/delete", json={
            "stateDigest": self.state(converted), "sourceRunId": converted, "elementId": removed_element}))
        self.assertNotIn(others[0], self.composed(removed))

        moved = self.run_proposal(self.client.post("/api/proposals/transform", json={
            "stateDigest": self.state(removed), "sourceRunId": removed, "elementId": pick["elementId"],
            "kind": "move", "translation": [0.3, 0.0, 0.0]}))
        before = picked_object["bounds"]
        after = self.composed(moved)[name]["bounds"]
        self.assertAlmostEqual(after["min"][0] - before["min"][0], 0.3, places=5)  # the snapshot is in metres
        self.assertAlmostEqual(after["min"][1], before["min"][1], places=5)

        dressed = self.run_proposal(self.client.post("/api/proposals/facets", json={
            "stateDigest": self.state(moved), "sourceRunId": moved,
            "targets": [{"id": component, "set": {"material.name": "橡木", "material.color": "#B08050"}}]}))
        worn = {name: row["strings"].get("archflow:material") for name, row in self.composed(dressed).items()
                if row["strings"].get("archflow:component") == component}
        self.assertTrue(worn)
        self.assertEqual(set(worn.values()), {"橡木"})

        # What the direct tools see of it: the box it stands in, which turns about its middle when no point is named.
        element = next(row for row in self.client.get("/api/state", params={"run": dressed}).json()["elements"]
                       if row["elementId"] == pick["elementId"])
        self.assertEqual(element["producer"], "imported")
        self.assertEqual(len(element["drawnShape"]["profile"]), 4)
        turned = self.run_proposal(self.client.post("/api/proposals/transform", json={
            "stateDigest": self.state(dressed), "sourceRunId": dressed, "elementId": pick["elementId"],
            "kind": "rotate", "axis": [0, 1, 0], "angleDegrees": 90}))
        box = self.composed(turned)[name]["bounds"]
        self.assertAlmostEqual(box["max"][0] - box["min"][0], after["max"][1] - after["min"][1], places=5)
        dressed = turned

        refused = self.client.post("/api/proposals/transform", json={
            "stateDigest": self.state(dressed), "sourceRunId": dressed, "elementId": pick["elementId"],
            "kind": "scale", "scale": [2, 2, 2]})
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("Rhino", refused.text)

    def test_an_object_named_as_an_element_with_no_identity_is_picked_as_that_element(self) -> None:
        state = self.client.get("/api/state").json()["stateDigest"]
        built = self.run_proposal(self.client.post("/api/proposals", json={"stateDigest": state, "semanticEdit": {
            "summary": "one block", "entities": [
                {"entity_id": "blocks", "schema": "Component@1", "parent_id": "model",
                 "fields": {"intent": "blocks", "source_refs": [EVIDENCE]}},
                {"entity_id": "block-1", "schema": "Element@1", "parent_id": "blocks",
                 "fields": {"component_id": "blocks", "producer": "prism", "references": {"base": {"level": "ground"}},
                            "params": {"profile": [[0, 0], [3, 0], [3, 3], [0, 3]], "height": 3.0}}}],
            "parameters": [], "relations": [], "removeEntityIds": [], "removeParameterKeys": [], "removeRelationIds": [],
            "protected": [], "kept": []}}))
        pick = lambda name: self.client.post("/api/pick/resolve", json={  # noqa: E731
            "stateDigest": self.state(built), "sourceRunId": built, "objectName": name, "userStrings": {}}).json()
        # What housing's uploaded model is: the record's objects named as their elements, with no archflow strings.
        self.assertEqual((pick("block-1")["status"], pick("block-1")["componentId"], pick("block-1")["elementId"]),
                         ("resolved", "blocks", "block-1"))
        self.assertEqual(pick("a neighbour's shed")["status"], "unbound")


def _objects(data: bytes) -> dict[str, dict]:
    index = inspect_three_dm_index(data, bounds_for=lambda ids: ids)
    return {row["name"]: {"id": row["object_id"], "bounds": row["bounds"],
                          "strings": {pair["key"]: pair["value"] for pair in row["attributes"]}}
            for row in index["objects"] if not row["is_instance_definition_object"]}


if __name__ == "__main__":
    unittest.main()
