"""Before / After / Why from the inspection records two runs retained.

The runs here are written the way the runner leaves them: a runner receipt
whose seat rows name a ``seat-3dm-inspection`` record, and that record with
each object's name, box, geometry digest and ``archflow:*`` strings. Nothing
is read from a 3dm; the comparison is a join on the records, and these tests
check the join, the counts, the ordering and what is said when a run has
nothing to compare.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, SEAT_3DM_INSPECTION

from .support import (
    PROJECT_ID,
    REFERENCE_RUN_ID,
    make_project,
    run_records,
    runner_state_digest,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def box(x0, y0, z0, x1, y1, z1):
    return {"min": [x0, y0, z0], "max": [x1, y1, z1]}


def obj(name, component, producer, sha, bbox):
    return {
        "bbox": {"name": name, "bbox": bbox, "layer_path": f"archflow::{component}", "type": "Brep"},
        "sha": {"name": name, "geometry_sha256": sha, "layer_path": f"archflow::{component}", "type": "Brep"},
        "strings": {
            "name": name,
            "layer_path": f"archflow::{component}",
            "attributes": [
                {"key": "archflow:component", "value": component},
                {"key": "archflow:producer_op", "value": producer},
                {"key": "archflow:object_ref", "value": f"cad-object:{name}"},
            ],
        },
    }


def inspection_payload(objects):
    return {
        "schema": "RhinoCadInspection@2",
        "named_object_bboxes": [o["bbox"] for o in objects],
        "object_geometry_sha256": [o["sha"] for o in objects],
        "object_user_strings": [o["strings"] for o in objects],
        "object_count": len(objects),
    }


class CompareTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)

    def write_run(self, run_id: str, seats: dict[str, list[dict]], *, inspected: bool = True) -> None:
        run = self.repository.create_run(run_id)
        rows = []
        for seat_id, objects in seats.items():
            cad: dict = {"status": "succeeded", "path": f"{seat_id}.3dm"}
            if inspected:
                ref = self.repository.put_json(
                    run=run,
                    destination=run_records(run_id),
                    record_kind=SEAT_3DM_INSPECTION,
                    payload=inspection_payload(objects),
                )
                cad["inspection_ref"] = ref.uri
            rows.append({"seat_id": seat_id, "status": "proposal_accepted", "objects": len(objects), "cad": cad})
        self.repository.put_json(
            run=run,
            destination=run_records(run_id),
            record_kind=RUNNER_RUN_RECEIPT,
            payload={
                "schema": "RunnerRunReceipt@3",
                "project_id": PROJECT_ID,
                "run_id": run_id,
                "seat_execution_complete": True,
                "seat_results": rows,
            },
        )

    def compare(self, candidate: str, against: str) -> tuple[int, dict]:
        response = self.client.get(f"/api/candidates/{candidate}/compare", params={"against": against})
        return response.status_code, response.json()


class JoinTests(CompareTestCase):
    def test_changed_unchanged_added_and_removed_are_told_apart(self) -> None:
        base = box(0, 0, 0, 4, 2, 0.6)
        self.write_run(
            "run-before",
            {
                "seat-structure": [
                    obj("obj-portico-base", "portico", "portico-base", SHA_A, base),
                    obj("obj-portico-cornice", "portico", "portico-cornice", SHA_B, box(0, 0, 0.6, 4, 2, 0.9)),
                    obj("obj-old-step", "portico", "old-step", SHA_C, box(0, -1, 0, 4, 0, 0.2)),
                ],
                "seat-envelope": [
                    obj("obj-door-leaf", "door", "door-leaf", SHA_C, box(1, 0, 0, 2, 0.1, 2)),
                ],
            },
        )
        self.write_run(
            "run-after",
            {
                "seat-structure": [
                    # Taller: new geometry digest and a taller box.
                    obj("obj-portico-base", "portico", "portico-base", SHA_B, box(0, 0, 0, 4, 2, 0.8)),
                    # Same digest, moved up with the base: changed.
                    obj("obj-portico-cornice", "portico", "portico-cornice", SHA_B, box(0, 0, 0.8, 4, 2, 1.1)),
                    obj("obj-new-step", "portico", "new-step", SHA_A, box(0, -1, 0, 4, 0, 0.2)),
                ],
                "seat-envelope": [
                    obj("obj-door-leaf", "door", "door-leaf", SHA_C, box(1, 0, 0, 2, 0.1, 2)),
                ],
            },
        )
        status, payload = self.compare("run-after", "run-before")
        self.assertEqual(status, 200, payload)
        self.assertEqual(
            {k: payload[k] for k in ("changed", "unchanged", "added", "removed")},
            {"changed": 2, "unchanged": 1, "added": 1, "removed": 1},
        )
        by_name = {row["name"]: row for row in payload["objects"]}
        self.assertEqual(by_name["obj-portico-base"]["status"], "changed")
        self.assertEqual(by_name["obj-portico-cornice"]["status"], "changed")
        self.assertEqual(by_name["obj-door-leaf"]["status"], "unchanged")
        self.assertEqual(by_name["obj-new-step"]["status"], "added")
        self.assertIsNone(by_name["obj-new-step"]["before"])
        self.assertEqual(by_name["obj-old-step"]["status"], "removed")
        self.assertIsNone(by_name["obj-old-step"]["after"])
        self.assertEqual(by_name["obj-portico-base"]["before"]["max"], [4, 2, 0.6])
        self.assertEqual(by_name["obj-portico-base"]["after"]["max"], [4, 2, 0.8])
        self.assertEqual(by_name["obj-portico-base"]["componentId"], "portico")
        self.assertEqual(by_name["obj-portico-base"]["producerOp"], "portico-base")
        self.assertEqual(by_name["obj-portico-base"]["seatId"], "seat-structure")
        # Components with something to say first.
        self.assertEqual(
            payload["components"],
            [
                {"componentId": "portico", "changed": 2, "unchanged": 0, "added": 1, "removed": 1},
                {"componentId": "door", "changed": 0, "unchanged": 1, "added": 0, "removed": 0},
            ],
        )
        self.assertIsNone(payload["why"])
        self.assertEqual(payload["whySource"], "unavailable")
        self.assertEqual(payload["against"], "run-before")

    def test_a_box_within_tolerance_is_the_same_box(self) -> None:
        self.write_run("r0", {"s": [obj("obj-a", "c", "a", SHA_A, box(0, 0, 0, 1, 1, 1))]})
        self.write_run("r1", {"s": [obj("obj-a", "c", "a", SHA_A, box(0, 0, 0, 1, 1, 1.00001))]})
        status, payload = self.compare("r1", "r0")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["unchanged"], 1)
        self.assertEqual(payload["changed"], 0)

    def test_the_same_run_against_itself_changes_nothing(self) -> None:
        self.write_run("r0", {"s": [obj("obj-a", "c", "a", SHA_A, box(0, 0, 0, 1, 1, 1))]})
        status, payload = self.compare("r0", "r0")
        self.assertEqual(status, 200, payload)
        self.assertEqual((payload["changed"], payload["unchanged"]), (0, 1))


class WhyTests(CompareTestCase):
    def test_the_sentence_travels_when_this_process_made_the_candidate(self) -> None:
        response = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": self.state_digest,
                "targetComponentId": "portico",
                "elementId": "portico-base",
                "utterance": "set height to 0.8",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        proposal_id = response.json()["proposalId"]
        self.write_run("run-before", {"s": [obj("obj-a", "portico", "a", SHA_A, box(0, 0, 0, 1, 1, 1))]})
        self.write_run("cand-1", {"s": [obj("obj-a", "portico", "a", SHA_B, box(0, 0, 0, 1, 1, 1.2))]})
        job = self.app.state.jobs.submit(candidate_id="cand-1", proposal_id=proposal_id, work=lambda: None)
        self.assertEqual(job.candidate_id, "cand-1")
        status, payload = self.compare("cand-1", "run-before")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["why"], "set height to 0.8")
        self.assertEqual(payload["whySource"], "proposal")
        self.assertEqual(payload["changed"], 1)


class NothingToCompareTests(CompareTestCase):
    def test_a_run_that_does_not_exist_is_named(self) -> None:
        self.write_run("r0", {"s": [obj("obj-a", "c", "a", SHA_A, box(0, 0, 0, 1, 1, 1))]})
        status, payload = self.compare("r0", "no-such-run")
        self.assertEqual(status, 404, payload)
        self.assertIn(payload["code"], {"RUN_NOT_FOUND", "CANDIDATE_NOT_FOUND"})
        self.assertIn("no-such-run", payload["detail"])

    def test_a_run_without_inspection_records_says_so(self) -> None:
        self.write_run("r0", {"s": [obj("obj-a", "c", "a", SHA_A, box(0, 0, 0, 1, 1, 1))]})
        self.write_run("r-uninspected", {"s": [obj("obj-a", "c", "a", SHA_A, box(0, 0, 0, 1, 1, 1))]}, inspected=False)
        status, payload = self.compare("r-uninspected", "r0")
        self.assertEqual(status, 404, payload)
        self.assertEqual(payload["code"], "INSPECTION_NOT_FOUND")
        self.assertIn("r-uninspected", payload["detail"])

    def test_against_is_required(self) -> None:
        self.write_run("r0", {"s": [obj("obj-a", "c", "a", SHA_A, box(0, 0, 0, 1, 1, 1))]})
        response = self.client.get("/api/candidates/r0/compare")
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
