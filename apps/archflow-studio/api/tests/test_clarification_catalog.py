"""The clarification seam with the project's conventions and the catalog behind it.

A record shaped like the villa's problem: porticos whose columns the model
shows on two sides, roofs with abutments that carry ``height``. Depending on
the case the columns have element rows (west and east) or none at all.

1. "把左侧柱廊的柱子提高 0.1m" with the request's camera (standing south,
   looking north) and the project's compass (PROJECT.md) lands on the west
   columns - never on the abutment, never as a question about the side.
2. A name PROJECT.md declares ("柱子") names the component outright.
3. Columns with no rows end in MISSING_EDITABLE_CONTROL whose draft says
   MODEL_VISIBLE_CATALOG_MISSING and names the objects the model shows; the
   abutment's height is offered nowhere.
"""

from __future__ import annotations

import copy
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.project.record_kinds import SEAT_3DM_INSPECTION

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    EVIDENCE,
    PROJECT_ID,
    RECORD_PAYLOAD,
    make_project,
    retain_runner_receipt,
    run_records,
    runner_state_digest,
    write_runner_record,
)

SHA = "b" * 64
# Standing south of the building, looking north: left is west.
CAMERA = {"position": [0, -50, 10], "target": [0, 0, 0], "up": [0, 0, 1], "fov": 38}
PROJECT_MD = """# Villa-like fixture

## Names
- 柱子, 柱, columns => portico-columns
- 柱廊, portico => porticos
- 甲组 => porticos
- 屋顶, roof => portico-roofs

## Compass
- north: +y
- east: +x
"""


def component(entity_id, parent_id, kind):
    return {"entity_id": entity_id, "schema": "Component@1", "parent_id": parent_id,
            "fields": {"component_id": entity_id, "semantic_kind": kind, "intent": entity_id, "source_refs": [EVIDENCE]},
            "basis_refs": [EVIDENCE]}


def prism(entity_id, component_id, height):
    return {"entity_id": entity_id, "schema": "Element@1", "parent_id": component_id,
            "fields": {"component_id": component_id, "producer": "prism",
                       "references": {"base": {"level": "level-ground"}},
                       "params": {"profile": [[0, 0], [1, 0], [1, 1], [0, 1]], "height": height}},
            "basis_refs": [EVIDENCE]}


def obj(name, component, producer):
    return {
        "bbox": {"name": name, "bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}, "layer_path": f"archflow::{component}", "type": "Brep"},
        "sha": {"name": name, "geometry_sha256": SHA, "layer_path": f"archflow::{component}", "type": "Brep"},
        "strings": {"name": name, "layer_path": f"archflow::{component}", "attributes": [
            {"key": "archflow:component", "value": component},
            {"key": "archflow:producer_op", "value": producer},
            {"key": "archflow:object_ref", "value": f"cad-object:{name}"},
        ]},
    }


COLUMN_OBJECTS = [obj(f"obj-column-{side}-{i}", "portico-columns", f"column-{side}-{i}") for side in ("west", "east") for i in range(2)]
ABUTMENT_OBJECTS = [obj(f"obj-portico-roof-abutment-{side}", "portico-roof-abutments", f"portico-roof-abutment-{side}") for side in ("west", "east")]
ROOF_OBJECTS = [obj(f"obj-portico-roof-{side}-0", "portico-roofs", f"portico-roof-{side}-0") for side in ("west", "east")]


def villa_like_record(*, columns_have_elements: bool) -> dict:
    payload = copy.deepcopy(RECORD_PAYLOAD)
    entities = payload["entities"]
    entities.extend([
        component("porticos", "building", "arrival-and-buttress"),
        component("portico-columns", "porticos", "vertical-support"),
        component("portico-roofs", "porticos", "cover"),
        component("portico-roof-abutments", "portico-roofs", "buttress"),
        prism("portico-roof-abutment-west", "portico-roof-abutments", 1.873),
        prism("portico-roof-abutment-east", "portico-roof-abutments", 1.873),
    ])
    if columns_have_elements:
        entities.extend([
            prism("portico-columns-west", "portico-columns", 9.798),
            prism("portico-columns-east", "portico-columns", 9.798),
        ])
    return payload


class VillaLikeTestCase(unittest.TestCase):
    columns_have_elements = True

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        record_payload = villa_like_record(columns_have_elements=self.columns_have_elements)
        self.record_path = write_runner_record(self.repository, record_payload)
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
        retain_runner_receipt(
            self.repository,
            run,
            design_state_digest=runner_state_digest(
                self.repository, run_id, record_payload
            ),
            record_payload=record_payload,
            seat_results=[
                {
                    "seat_id": "seat-portico",
                    "status": "proposal_accepted",
                    "objects": len(objects),
                    "cad": {
                        "status": "succeeded",
                        "path": "portico.3dm",
                        "inspection_ref": ref.uri,
                    },
                }
            ],
        )
        self.app = create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID, reference_run=run_id))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]
        (self.root / PROJECT_ID / "PROJECT.md").write_text(
            PROJECT_MD + f"\nstate digest: {self.state_digest}\n", encoding="utf-8"
        )

    def ask(self, utterance: str, **body):
        body.setdefault("stateDigest", self.state_digest)
        body["utterance"] = utterance
        response = self.client.post("/api/intents", json=body)
        return response.status_code, response.json()


class CameraAndCompassTests(VillaLikeTestCase):
    def test_a_latin_alias_matches_only_as_a_whole_word(self) -> None:
        embedded_status, embedded = self.ask("把 waterproof 提高 0.1m")
        exact_status, exact = self.ask("把roof提高 0.1m")

        self.assertEqual(embedded_status, 422, embedded)
        self.assertIsNone(embedded["pendingIntent"]["targetComponentId"])
        self.assertEqual(exact_status, 422, exact)
        self.assertEqual(
            exact["pendingIntent"]["targetComponentId"], "portico-roofs"
        )

    def test_a_long_cjk_alias_is_not_swallowed_by_its_short_prefix(self) -> None:
        status, payload = self.ask("把柱廊提高 0.1m")

        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["pendingIntent"]["targetComponentId"], "porticos")

    def test_stale_project_conventions_do_not_enter_intent(self) -> None:
        (self.root / PROJECT_ID / "PROJECT.md").write_text(
            PROJECT_MD + "\nstate digest: " + "0" * 64 + "\n", encoding="utf-8"
        )

        alias_status, alias_payload = self.ask("把甲组提高 0.1m")
        compass_status, compass_payload = self.ask(
            "把左侧柱子提高 0.1m", camera=CAMERA
        )

        self.assertEqual(alias_status, 422, alias_payload)
        self.assertIsNone(alias_payload["pendingIntent"]["targetComponentId"])
        self.assertEqual(compass_status, 422, compass_payload)
        self.assertIsNone(compass_payload["pendingIntent"]["elementId"])
        self.assertEqual(
            sorted(item["elementId"] for item in compass_payload["pendingIntent"]["candidates"]),
            ["portico-columns-east", "portico-columns-west"],
        )

    def test_left_with_a_camera_and_the_compass_is_the_west_columns(self) -> None:
        status, payload = self.ask("把左侧柱廊的柱子提高 0.1m", camera=CAMERA)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-columns-west")
        self.assertEqual(payload["proposal"]["target"]["componentId"], "portico-columns")
        self.assertEqual(payload["proposal"]["change"]["old"], 9.798)
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 9.898)
        self.assertNotIn("abutment", payload["proposal"]["target"]["elementId"])

    def test_left_without_a_camera_is_still_a_question_about_the_side(self) -> None:
        status, payload = self.ask("把左侧柱廊的柱子提高 0.1m")
        self.assertEqual(status, 422, payload)
        pending = payload["pendingIntent"]
        self.assertIn("orientation", pending["missingSlots"])
        self.assertEqual(sorted(c["elementId"] for c in pending["candidates"]), ["portico-columns-east", "portico-columns-west"])

    def test_a_declared_name_names_the_component_outright(self) -> None:
        status, payload = self.ask("把西边的柱子提高 0.1m")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-columns-west")


class CatalogMissingTests(VillaLikeTestCase):
    columns_have_elements = False

    def test_columns_the_model_shows_without_a_row_say_so_and_never_offer_the_abutment(self) -> None:
        status, payload = self.ask("把柱子提高 0.1m", camera=CAMERA)
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["outcome"], "MISSING_EDITABLE_CONTROL")
        draft = payload["authoredControlDraft"]
        self.assertEqual(draft["targetComponentId"], "portico-columns")
        self.assertEqual(draft["catalogStatus"], "MODEL_VISIBLE_CATALOG_MISSING")
        self.assertEqual(sorted(draft["objectNames"]), sorted(o["bbox"]["name"] for o in COLUMN_OBJECTS))
        self.assertIn("MODEL_VISIBLE_CATALOG_MISSING", payload["detail"])
        self.assertIsNone(payload["pendingIntent"]["continuationToken"])
        self.assertEqual(payload["pendingIntent"]["candidates"], [])
        self.assertTrue(all("abutment" not in ref for ref in payload["pendingIntent"]["candidates"]))
        self.assertNotIn("abutment.height", payload["detail"])


if __name__ == "__main__":
    unittest.main()
