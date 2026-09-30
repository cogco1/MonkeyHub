"""The component catalog: what the tree can be asked, and what it says it lacks.

The record declares two elements under ``portico`` and none under ``building``.
The reference run's inspection names objects for both, some produced by the
elements and some by nothing the record declares. The catalog must bind the
first through the same rule a click uses, report the second as visible in the
model and missing from the catalog, and never offer a neighbouring element's
field in their place.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, SEAT_3DM_INSPECTION
from archflow.state.state_record import StateRecord

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.catalog import (
    BOUND,
    EDITABLE,
    MISSING,
    MODEL_VISIBLE_CATALOG_MISSING,
    UNKNOWN_COMPONENT,
    catalog_of,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    PROJECT_ID,
    make_project,
    retain_runner_receipt,
    run_records,
    runner_state_digest,
)

SHA = "a" * 64


def obj(name, component, producer):
    return {
        "bbox": {"name": name, "bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}, "layer_path": f"archflow::{component}", "type": "Brep"},
        "sha": {"name": name, "geometry_sha256": SHA, "layer_path": f"archflow::{component}", "type": "Brep"},
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


OBJECTS = [
    obj("obj-portico-base", "portico", "portico-base"),
    obj("obj-portico-base-1", "portico", "portico-base-1"),
    obj("obj-portico-cornice", "portico", "portico-cornice"),
    obj("obj-portico-frieze-0", "portico", "portico-frieze-0"),
    obj("obj-building-mass", "building", "building-mass"),
    obj("obj-tower-top", "tower", "tower-top"),
]


class CatalogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.run_id = "inspected-001"
        run = self.repository.create_run(self.run_id)
        self.run = run
        ref = self.repository.put_json(
            run=run,
            destination=run_records(self.run_id),
            record_kind=SEAT_3DM_INSPECTION,
            payload={
                "schema": "RhinoCadInspection@2",
                "named_object_bboxes": [o["bbox"] for o in OBJECTS],
                "object_geometry_sha256": [o["sha"] for o in OBJECTS],
                "object_user_strings": [o["strings"] for o in OBJECTS],
                "object_count": len(OBJECTS),
            },
        )
        self.seat_results = [
            {"seat_id": "seat-portico", "status": "proposal_accepted", "objects": len(OBJECTS),
             "cad": {"status": "succeeded", "path": "portico.3dm", "inspection_ref": ref.uri}}
        ]
        retain_runner_receipt(
            self.repository,
            run,
            design_state_digest=runner_state_digest(
                self.repository, self.run_id
            ),
            seat_results=self.seat_results,
        )
        self.app = create_app(
            StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID, reference_run=self.run_id)
        )
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def catalog(self):
        binding = bound_project(self.app.state)
        return catalog_of(binding, project_state(binding))


class ObjectBindingTests(CatalogTestCase):
    def test_objects_bind_by_the_pick_rule_and_the_rest_are_named_missing(self) -> None:
        catalog = self.catalog()
        by_name = {item.name: item for item in catalog.objects}
        self.assertEqual(by_name["obj-portico-base"].status, BOUND)
        self.assertEqual(by_name["obj-portico-base"].element_id, "portico-base")
        self.assertEqual(by_name["obj-portico-base-1"].element_id, "portico-base")
        self.assertEqual(by_name["obj-portico-cornice"].element_id, "portico-cornice")
        frieze = by_name["obj-portico-frieze-0"]
        self.assertEqual(frieze.status, MODEL_VISIBLE_CATALOG_MISSING)
        self.assertIsNone(frieze.element_id)
        self.assertEqual(frieze.component_id, "portico")
        self.assertIn("no Element@1 row names it", frieze.detail)
        mass = by_name["obj-building-mass"]
        self.assertEqual(mass.status, MODEL_VISIBLE_CATALOG_MISSING)
        self.assertIn("no Element@1 row at all", mass.detail)
        self.assertEqual(by_name["obj-tower-top"].status, UNKNOWN_COMPONENT)
        self.assertEqual(
            (catalog.coverage.objects, catalog.coverage.bound, catalog.coverage.unbound, catalog.coverage.unknown_component),
            (6, 3, 2, 1),
        )
        self.assertEqual(catalog.inspection_run, self.run_id)

    def test_different_state_receipt_cannot_bind_inspection_objects(self) -> None:
        ref = self.repository.put_json(
            run=self.run,
            destination=run_records(self.run_id),
            record_kind=RUNNER_RUN_RECEIPT,
            payload={
                "schema": "RunnerRunReceipt@3",
                "project_id": PROJECT_ID,
                "run_id": self.run_id,
                "design_state_digest": "0" * 64,
                "seat_execution_complete": True,
                "seat_results": self.seat_results,
            },
        )
        path = self.repository.layout.resolve_record(ref)
        later = path.stat().st_mtime + 60.0
        os.utime(path, (later, later))

        binding = bound_project(self.app.state)
        projection = project_state(binding)
        self.assertFalse(projection.matches_reference_receipt)

        catalog = catalog_of(binding, projection)
        self.assertFalse(any(item.status == BOUND for item in catalog.objects))
        self.assertEqual((catalog.coverage.objects, catalog.coverage.bound), (0, 0))
        self.assertIsNone(catalog.inspection_run)
        self.assertTrue(
            any("object coverage unknown" in line for line in catalog.honesty)
        )

    def test_elements_carry_their_capabilities_and_their_objects(self) -> None:
        catalog = self.catalog()
        base = catalog.element("portico-base")
        assert base is not None
        self.assertEqual(base.object_names, ("obj-portico-base", "obj-portico-base-1"))
        keys = {cap.key: cap for cap in base.capabilities}
        self.assertIn("height", keys)
        self.assertEqual(keys["height"].value, 0.6)
        self.assertEqual(keys["height"].status, EDITABLE)
        self.assertEqual(keys["height"].source, "authored")
        self.assertEqual(keys["height"].confidence, 1.0)
        self.assertEqual(keys["height"].capability_id, "entity:portico-base#params.height")
        self.assertIn("relation:rel-cornice-on-base", keys["height"].validator_refs)


class ComponentTests(CatalogTestCase):
    def test_component_closures_share_dependency_reads_within_the_catalog(self) -> None:
        binding = bound_project(self.app.state)
        projection = project_state(binding)
        with patch.object(StateRecord, "dependency_edges", autospec=True,
                          side_effect=StateRecord.dependency_edges) as read_edges:
            catalog = catalog_of(binding, projection)
        for component in catalog.components:
            self.assertEqual(component.closure, ("entity:portico-base", "entity:portico-cornice"))
        # One read for capability references and one for every component's
        # closure together; adding components must not rebuild the full graph.
        self.assertLessEqual(read_edges.call_count, 2)

    def test_a_component_knows_its_descendants_capabilities_and_gaps(self) -> None:
        catalog = self.catalog()
        portico = catalog.component("portico")
        assert portico is not None
        self.assertEqual(portico.parent_id, "building")
        self.assertEqual(portico.element_ids, ("portico-base", "portico-cornice"))
        self.assertEqual(portico.descendant_element_ids, ("portico-base", "portico-cornice"))
        self.assertEqual(portico.capability_count, 2)
        self.assertIn(EDITABLE, portico.states)
        # One of its objects has no element: the component is not fully catalogued.
        self.assertIn(MISSING, portico.states)
        self.assertEqual(portico.object_count, 4)
        self.assertEqual(portico.unbound_object_count, 1)
        self.assertIn("entity:portico-cornice", portico.closure)
        building = catalog.component("building")
        assert building is not None
        self.assertEqual(building.children, ("portico",))
        # The building counts what its subtree holds, and says a subtree object is unbound.
        self.assertEqual(building.descendant_element_ids, ("portico-base", "portico-cornice"))
        self.assertEqual(building.object_count, 5)
        self.assertEqual(building.unbound_object_count, 2)

    def test_editable_descendants_are_the_elements_a_component_can_land_on(self) -> None:
        catalog = self.catalog()
        self.assertEqual(
            [item.element_id for item in catalog.editable_descendants("building")],
            ["portico-base", "portico-cornice"],
        )
        self.assertEqual(catalog.editable_descendants("no-such"), ())

    def test_the_honesty_lines_count_the_gaps(self) -> None:
        catalog = self.catalog()
        self.assertTrue(any("2 objects are visible in the model and missing" in line for line in catalog.honesty))
        # Both components have a realization somewhere below them (building
        # through portico), so no component is reported as unrealized here.
        self.assertFalse(any("have no realization" in line for line in catalog.honesty))


class WireTests(CatalogTestCase):
    def test_get_state_carries_the_catalog(self) -> None:
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.text)
        catalog = response.json()["catalog"]
        self.assertIsNotNone(catalog)
        self.assertEqual(catalog["coverage"], {"objects": 6, "bound": 3, "unbound": 2, "ambiguous": 0, "unknownComponent": 1})
        portico = next(item for item in catalog["components"] if item["componentId"] == "portico")
        self.assertEqual(portico["capabilityCount"], 2)
        self.assertIn("missing", portico["states"])
        frieze = next(item for item in catalog["objects"] if item["name"] == "obj-portico-frieze-0")
        self.assertEqual(frieze["status"], "MODEL_VISIBLE_CATALOG_MISSING")
        base = next(item for item in catalog["elements"] if item["elementId"] == "portico-base")
        self.assertEqual(base["capabilities"][0]["capabilityId"], "entity:portico-base#params.height")


class NoInspectionTests(unittest.TestCase):
    def test_a_reference_run_without_inspection_leaves_coverage_unknown(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        make_project(root)
        app = create_app(StudioSettings(cad_export="off", project_dir=root / PROJECT_ID))
        client = TestClient(app)
        self.addCleanup(client.close)
        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.text)
        catalog = response.json()["catalog"]
        self.assertEqual(catalog["coverage"]["objects"], 0)
        self.assertIsNone(catalog["inspectionRun"])
        self.assertTrue(any("object coverage unknown" in line for line in catalog["honesty"]))
        # The elements and their capabilities stand without any inspection.
        self.assertEqual(len(catalog["elements"]), 2)


if __name__ == "__main__":
    unittest.main()
