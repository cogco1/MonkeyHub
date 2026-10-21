"""Quantities of one exact model: volume, area, closure, role and material per member, and their sums (G4)."""

from __future__ import annotations

import base64
import csv
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from project_runtime.application import artifacts, quantities
from project_runtime.application.artifacts import ModelSource
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

PROJECT_ID = "quantities"
FIXTURE = Path(__file__).parent / "fixtures" / "model-source-a.3dm"


class QuantitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="quantities ")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / PROJECT_ID
        FilesystemProjectRepository.initialize(self.project, project_id=PROJECT_ID,
                                               initial_state={"project_id": PROJECT_ID, "version": 0})
        self.client = TestClient(create_app(StudioSettings(project_dir=self.project, cad_export="off")))
        self.addCleanup(self.client.close)

    def files(self) -> dict[str, bytes]:
        return {path.relative_to(self.project).as_posix(): path.read_bytes()
                for path in self.project.rglob("*") if path.is_file()}

    def test_an_imported_model_is_measured_member_by_member_and_summed(self) -> None:
        model = artifacts.register_model_asset(bound_project(self.client.app.state), None, None, "site.3dm",
                                               base64.b64encode(FIXTURE.read_bytes()).decode("ascii"))
        body = {"projectId": PROJECT_ID, "sourceAsset": {"runId": model.run_id, "assetSha256": model.sha256}}
        before = self.files()
        answer = self.client.post("/api/quantities", json=body)
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(self.files(), before, "a quantities read writes nothing")
        result = answer.json()
        self.assertEqual(result["lengthUnit"], "meter")
        members = sorted(result["members"], key=lambda row: row["area"])
        # The fixture's two boxes: 1 x 1 x 2 and 4 x 2 x 0.6.
        self.assertEqual([(row["closed"], row["solidCount"], row["measuredFrom"]) for row in members],
                         [(True, 1, "shape"), (True, 1, "shape")])
        self.assertAlmostEqual(members[0]["volume"], 2.0, places=5)
        self.assertAlmostEqual(members[0]["area"], 10.0, places=5)
        self.assertAlmostEqual(members[1]["volume"], 4.8, places=4)
        self.assertAlmostEqual(members[1]["area"], 23.2, places=4)
        self.assertEqual((result["totals"]["count"], result["totals"]["closedCount"]), (2, 2))
        self.assertAlmostEqual(result["totals"]["volume"], 6.8, places=4)
        [group] = result["summary"]
        self.assertEqual((group["material"], group["role"], group["count"], group["closedCount"]), (None, None, 2, 2))
        self.assertAlmostEqual(group["volumeM3"], 6.8, places=4)
        exported = self.client.post("/api/quantities", json={**body, "format": "csv"})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.headers["content-type"].startswith("text/csv"))
        rows = list(csv.reader(StringIO(exported.text)))
        self.assertEqual(rows[0], [*quantities.CSV_COLUMNS, "lengthUnit"])
        self.assertEqual(len([row for row in rows[1:3] if row]), 2)
        self.assertIn(["summary", "role", "material", "count", "closedCount", "openCount", "volume", "volumeM3",
                       "area", "areaM2"], rows)

    def test_a_member_takes_its_role_and_material_from_its_geometry_by_the_naming_rule(self) -> None:
        # obj-<id>-body is the member a construction script's geometry <id> made;
        # its facets come from the state, and an open member adds only area.
        selected = ModelSource("cand-run", "a" * 64, "b" * 64)
        receipt = {"readback": {
            "obj-roof.cap-body": {"closed": True, "solid_count": 1, "volume": 0.25, "layers": ["Roof"],
                                  "declared_delivery": "closed_solid", "bbox": None},
            "obj-roof.cap-2-body": {"closed": True, "solid_count": 1, "volume": 0.5, "layers": ["Roof"],
                                    "declared_delivery": "closed_solid", "bbox": None},
            "obj-wrap-body": {"closed": False, "solid_count": 0, "volume": None, "layers": ["Membrane"],
                              "declared_delivery": "open_surface", "bbox": None}}}
        verified = SimpleNamespace(length_unit="foot", physical_object_ids=tuple(receipt["readback"]), entries=())
        facets = {"roof.cap": {"material.name": "Steel", "architectural.role": "roof"},
                  "roof.cap-2": {"material.name": "Steel", "architectural.role": "roof"},
                  "wrap": {"material.name": "Membrane"}}
        with mock.patch.object(quantities, "load_drawing_source", return_value=(selected, None, receipt, verified)), \
             mock.patch.object(quantities, "_member_facets", return_value=facets), \
             mock.patch.object(quantities, "object_groups", return_value={}):
            result = quantities.model_quantities(bound_project(self.client.app.state), model_source=selected)
        rows = {row["objectId"]: row for row in result["members"]}
        self.assertEqual((rows["obj-roof.cap-2-body"]["material"], rows["obj-roof.cap-2-body"]["role"],
                          rows["obj-roof.cap-2-body"]["materialSource"]), ("Steel", "roof", "facet"))
        self.assertAlmostEqual(rows["obj-roof.cap-body"]["volumeM3"], 0.25 * 0.3048 ** 3)
        groups = {(group["material"], group["role"]): group for group in result["summary"]}
        self.assertEqual((groups[("Steel", "roof")]["count"], groups[("Steel", "roof")]["volume"]), (2, 0.75))
        self.assertEqual((groups[("Membrane", None)]["openCount"], groups[("Membrane", None)]["volume"]), (1, 0.0))
        self.assertEqual(result["totals"]["closedCount"], 2)

    def test_a_request_without_a_model_or_for_another_project_is_refused(self) -> None:
        missing = self.client.post("/api/quantities", json={"projectId": PROJECT_ID})
        self.assertEqual((missing.status_code, missing.json()["code"]), (422, "DRAWING_SOURCE_REQUIRED"))
        other = self.client.post("/api/quantities", json={"projectId": "another"})
        self.assertEqual((other.status_code, other.json()["code"]), (403, "PROJECT_MISMATCH"))


if __name__ == "__main__":
    unittest.main()
