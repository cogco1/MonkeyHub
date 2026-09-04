"""Clarification with state, on the wire: the interactions the lane must pass.

1. "把左侧柱廊的柱子提高 0.1m" lands on the west portico columns, never on the
   west roof abutment, when the record has column elements and the request
   carries a camera and the project a compass.
2. "把左侧柱廊略微提高" is a question naming candidates and the missing slot;
   "略微" is never turned into a percentage.
3. "不是屋顶，是柱子" continues the same pending intent with the roofs dropped.
4. A component the model shows and the catalog lacks answers
   MODEL_VISIBLE_CATALOG_MISSING, terminal, and never offers abutment.height.
5. "补充 portico-columns 字段" declares a control with provenance; the authored
   record on disk is byte-identical afterwards.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, SEAT_3DM_INSPECTION

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, make_project, run_records, runner_state_digest, write_runner_record
from .test_resolver import ABUTMENT_OBJECTS, CAMERA, COLUMN_OBJECTS, ROOF_OBJECTS, villa_like_record

PROJECT_MD = """# Villa-like fixture

## Names
- 柱子, columns => portico-columns
- 柱廊, portico => porticos
- 屋顶, roof => portico-roofs

## Compass
- north: +y
- east: +x
"""


class PendingTestCase(unittest.TestCase):
    columns_have_elements = True

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.record_path = write_runner_record(self.repository, villa_like_record(columns_have_elements=self.columns_have_elements))
        (self.root / PROJECT_ID / "PROJECT.md").write_text(PROJECT_MD, encoding="utf-8")
        run_id = "inspected-001"
        run = self.repository.create_run(run_id)
        objects = COLUMN_OBJECTS + ABUTMENT_OBJECTS + ROOF_OBJECTS
        ref = self.repository.put_json(run=run, destination=run_records(run_id), record_kind=SEAT_3DM_INSPECTION, payload={
            "schema": "RhinoCadInspection@2",
            "named_object_bboxes": [o["bbox"] for o in objects],
            "object_geometry_sha256": [o["sha"] for o in objects],
            "object_user_strings": [o["strings"] for o in objects],
            "object_count": len(objects),
            "document_user_strings": [{"key": "archflow:length_unit", "value": "meter"}],
        })
        self.repository.put_json(run=run, destination=run_records(run_id), record_kind=RUNNER_RUN_RECEIPT, payload={
            "schema": "RunnerRunReceipt@3", "project_id": PROJECT_ID, "run_id": run_id, "seat_execution_complete": True,
            "seat_results": [{"seat_id": "seat-portico", "status": "proposal_accepted", "objects": len(objects),
                              "cad": {"status": "succeeded", "path": "portico.3dm", "inspection_ref": ref.uri}}],
        })
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=run_id))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]

    def ask(self, utterance: str, **body):
        body.setdefault("stateDigest", self.state_digest)
        body["utterance"] = utterance
        response = self.client.post("/api/intents", json=body)
        return response.status_code, response.json()


class LeftPorticoColumnsTests(PendingTestCase):
    def test_the_left_columns_go_up_by_a_tenth_and_the_abutment_is_untouched(self) -> None:
        status, payload = self.ask("把左侧柱廊的柱子提高 0.1m", camera=CAMERA)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-columns-west")
        self.assertEqual(payload["proposal"]["target"]["componentId"], "portico-columns")
        self.assertEqual(payload["proposal"]["change"]["old"], 9.798)
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 9.898)
        self.assertEqual(payload["resolution"]["targetSource"], "direction")
        self.assertNotIn("abutment", payload["proposal"]["target"]["elementId"])

    def test_a_little_is_candidates_then_an_amount_question(self) -> None:
        status, payload = self.ask("把左侧柱廊略微提高", camera=CAMERA)
        self.assertEqual(status, 422, payload)
        pending = payload["pending"]
        self.assertEqual(pending["reasonCode"], "AMBIGUOUS_TARGET")
        self.assertEqual(pending["missingSlots"], ["target"])
        self.assertEqual(sorted(item["elementId"] for item in pending["candidates"]), ["portico-columns-west", "portico-roof-abutment-west"])
        self.assertNotIn("10", payload["question"])
        token = pending["continuationToken"]
        # Choosing the columns keeps the intent; the amount is still missing.
        status, payload = self.ask("柱子", continuationToken=token, camera=CAMERA)
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["pending"]["continuationToken"], token)
        self.assertEqual(payload["pending"]["reasonCode"], "MISSING_AMOUNT")
        self.assertEqual(payload["pending"]["targetElementId"], "portico-columns-west")
        self.assertEqual(payload["pending"]["originalUtterance"], "把左侧柱廊略微提高")
        self.assertEqual(payload["pending"]["slots"].get("direction"), "increase")
        # The number closes it: the direction carried over, so 0.1 is an increase.
        status, payload = self.ask("0.1m", continuationToken=token)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-columns-west")
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 9.898)
        self.assertEqual(self.client.post("/api/controls", json={"continuationToken": token}).status_code, 404)

    def test_not_the_roof_the_columns_drops_the_roof(self) -> None:
        status, payload = self.ask("把柱廊提高 10 %")
        self.assertEqual(status, 422, payload)
        token = payload["pending"]["continuationToken"]
        self.assertEqual(len(payload["pending"]["candidates"]), 4)
        status, payload = self.ask("不是屋顶，是柱子", continuationToken=token)
        self.assertEqual(status, 422, payload)
        pending = payload["pending"]
        self.assertEqual(pending["continuationToken"], token)
        self.assertIn("portico-roofs", pending["rejectedCandidates"])
        self.assertEqual(sorted(item["elementId"] for item in pending["candidates"]), ["portico-columns-east", "portico-columns-west"])
        self.assertTrue(all("roof" not in item["elementId"] for item in pending["candidates"]))
        status, payload = self.ask("西边的", continuationToken=token)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-columns-west")
        self.assertEqual(payload["proposal"]["utterance"], "increase height by 10 %")


class MissingCatalogTests(PendingTestCase):
    columns_have_elements = False

    def test_columns_without_a_catalog_are_named_missing_not_the_abutment(self) -> None:
        status, payload = self.ask("把柱子提高 0.1m", targetComponentId="portico-columns")
        self.assertEqual(status, 422, payload)
        pending = payload["pending"]
        self.assertEqual(pending["reasonCode"], "MODEL_VISIBLE_CATALOG_MISSING")
        self.assertTrue(pending["terminal"])
        self.assertEqual(pending["targetComponentId"], "portico-columns")
        self.assertNotIn("abutment", payload["question"])
        self.assertNotIn("abutment", payload["detail"])
        return pending["continuationToken"]

    def test_declaring_the_control_has_provenance_and_writes_nothing(self) -> None:
        before = hashlib.sha256(self.record_path.read_bytes()).hexdigest()
        status, payload = self.ask("补充 portico-columns 字段", targetComponentId="portico-columns")
        self.assertEqual(status, 422, payload)
        self.assertTrue(payload["pending"]["terminal"])
        token = payload["pending"]["continuationToken"]
        response = self.client.post("/api/controls", json={"continuationToken": token})
        self.assertEqual(response.status_code, 201, response.text)
        control = response.json()
        self.assertEqual(control["status"], "proposed")
        self.assertEqual(control["componentId"], "portico-columns")
        self.assertEqual(control["reasonCode"], "MODEL_VISIBLE_CATALOG_MISSING")
        self.assertEqual(sorted(control["objectNames"]), sorted(o["bbox"]["name"] for o in COLUMN_OBJECTS))
        self.assertEqual(control["inspectionRun"], "inspected-001")
        self.assertIn("utterance:补充 portico-columns 字段", control["provenance"])
        self.assertIn("inspection-run:inspected-001", control["provenance"])
        self.assertIn("object:obj-column-west-0", control["provenance"])
        self.assertEqual(control["draft"]["component_id"], "portico-columns")
        self.assertEqual(control["confidence"], 0.5)
        self.assertEqual(self.client.get(f"/api/controls/{control['controlId']}").status_code, 200)
        after = hashlib.sha256(self.record_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        # The state the studio serves is unchanged too.
        self.assertEqual(self.client.get("/api/state").json()["stateDigest"], self.state_digest)

    def test_a_non_terminal_pending_intent_declares_nothing(self) -> None:
        status, payload = self.ask("把柱廊提高 10 %")
        self.assertEqual(status, 422, payload)
        response = self.client.post("/api/controls", json={"continuationToken": payload["pending"]["continuationToken"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "CONTROL_NOT_ASKED_FOR")


if __name__ == "__main__":
    unittest.main()
