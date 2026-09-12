"""Deleting one picked element, and refusing to delete one that is stood on.

Delete is not a new kind of authority: it is the removal the record's own
``edit_components`` already carries, made against one exact base and run
through the candidate route every other change uses. What these tests hold to
is that it removes exactly the object that was picked — never the component it
belongs to, never a neighbour — and that an element something else depends on
is refused with the dependency named instead of being taken away under it.
"""

from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, make_project

SQUARE = [[6.0, 0.0], [9.0, 0.0], [9.0, 2.0], [6.0, 2.0]]


class DeleteElementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.project = self.root / PROJECT_ID
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)

    def digest(self, run: str | None = None) -> str:
        return self.client.get(f"/api/state?run={run}" if run else "/api/state").json()["stateDigest"]

    def finish(self, job_id: str) -> dict:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                return job
            time.sleep(0.05)
        raise AssertionError("the candidate never finished")

    def run_proposal(self, proposal_id: str) -> str:
        started = self.client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        job = self.finish(started.json()["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        return job["candidateId"]

    def drawn_run(self) -> tuple[str, str]:
        """A run with one more object in it: something a delete may remove."""

        element_id = "drawn-annex"
        drawn = self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.digest(), "componentId": "portico", "elementId": element_id,
            "profile": SQUARE, "height": 2.4, "baseLevel": "level-ground"})
        self.assertEqual(drawn.status_code, 201, drawn.text)
        return self.run_proposal(drawn.json()["proposalId"]), element_id

    def names(self, run_id: str) -> set[str]:
        models = sorted((self.project / "runs" / run_id).rglob("*.3dm"))
        self.assertTrue(models, f"the run exported no model under runs/{run_id}")
        found: set[str] = set()
        for path in models:
            found |= {str(row["name"]) for row in inspect_three_dm(path).named_object_bboxes}
        return found

    def runs(self) -> set[str]:
        return {path.name for path in (self.project / "runs").iterdir() if path.is_dir()}

    def test_deleting_a_picked_element_removes_it_and_nothing_else(self) -> None:
        base, element_id = self.drawn_run()
        before = self.names(base)
        self.assertIn(f"obj-{element_id}", before)

        proposal = self.client.post("/api/proposals/delete", json={
            "stateDigest": self.digest(base), "sourceRunId": base, "elementId": element_id})
        self.assertEqual(proposal.status_code, 201, proposal.text)
        body = proposal.json()
        self.assertEqual(body["change"]["kind"], "edit_components")
        self.assertEqual(body["change"]["edits"]["removeEntityIds"], [element_id])
        self.assertEqual(body["target"]["componentId"], "portico",
                         "the element's own component is named, and is not what is removed")
        self.assertEqual([row["action"] for row in body["change"]["changes"]], ["remove"])

        after = self.names(self.run_proposal(body["proposalId"]))
        self.assertNotIn(f"obj-{element_id}", after, "the picked object is gone from the export")
        self.assertEqual(before - after, {f"obj-{element_id}"}, "and it is the only thing gone")
        self.assertIn("obj-portico-base", after)
        self.assertIn("obj-portico-cornice", after)
        # The record kept the component the element belonged to.
        state = self.client.get("/api/state").json()
        self.assertIn("portico", [row["componentId"] for row in state["elements"]],
                      "the component is still there, with its own elements")

    def test_an_element_something_stands_on_is_refused_with_what_stands_on_it(self) -> None:
        before = self.runs()
        refused = self.client.post("/api/proposals/delete", json={
            "stateDigest": self.digest(), "elementId": "portico-base"})
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertEqual(refused.json()["code"], "ELEMENT_HAS_DEPENDENTS")
        self.assertIn("portico-cornice", detail)
        self.assertIn("Nothing was run", detail)
        self.assertEqual(self.runs(), before, "a refused delete leaves no run behind")
        # And the element is still there to be worked with.
        self.assertIn("portico-base", [row["elementId"] for row in self.client.get("/api/state").json()["elements"]])

    def test_deleting_something_that_is_not_an_element_is_refused(self) -> None:
        for target in ("portico", "level-ground", "nothing-here"):
            refused = self.client.post("/api/proposals/delete", json={
                "stateDigest": self.digest(), "elementId": target})
            self.assertEqual(refused.status_code, 404, refused.text)
            self.assertEqual(refused.json()["code"], "ELEMENT_UNKNOWN")
            self.assertIn("portico-base", refused.json()["detail"], "it says what it does declare")

    def test_a_stale_base_deletes_nothing(self) -> None:
        base, element_id = self.drawn_run()
        refused = self.client.post("/api/proposals/delete", json={
            "stateDigest": "f" * 64, "sourceRunId": base, "elementId": element_id})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "STALE_BASE")

    def test_the_run_before_the_delete_still_has_the_object(self) -> None:
        """Undo has something to go back to: the earlier run is untouched."""

        base, element_id = self.drawn_run()
        proposal = self.client.post("/api/proposals/delete", json={
            "stateDigest": self.digest(base), "sourceRunId": base, "elementId": element_id}).json()
        deleted = self.run_proposal(proposal["proposalId"])
        self.assertIn(f"obj-{element_id}", self.names(base))
        self.assertNotIn(f"obj-{element_id}", self.names(deleted))
        # And the earlier run is still a base that can be read and worked from.
        state = self.client.get(f"/api/state?run={base}").json()
        self.assertIn(element_id, [row["elementId"] for row in state["elements"]])

    def test_deleting_is_kept_out_of_what_a_keep_clause_protects(self) -> None:
        base, element_id = self.drawn_run()
        refused = self.client.post("/api/proposals/delete", json={
            "stateDigest": self.digest(base), "sourceRunId": base, "elementId": element_id,
            "keep": [f"entity:{element_id}"]})
        self.assertEqual(refused.status_code, 201, refused.text)
        self.assertEqual(refused.json()["status"], "conflict",
                         "keeping the thing being deleted is a conflict, not a silent removal")
        started = self.client.post(f"/api/proposals/{refused.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 409, started.text)
        self.assertEqual(started.json()["code"], "PROPOSAL_NOT_RUNNABLE")


if __name__ == "__main__":
    unittest.main()
