"""Independent hand-computable geometry expectations, never model inputs."""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from archflow.adapters.occt_backend import occt_available
from archflow.semantics.roles import ROLE_IDS
from labs.spatial_observation.fixture import Fixture, SourceMismatch, VIEWS, cad_to_hub, hub_to_cad
from monkeydiagram.drawing_elevation import DrawingElevationError


@unittest.skipUnless(occt_available(), "real cadquery-ocp runtime required")
class PublicFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="spatial-observation-fixture-")
        cls.base = Fixture.create(Path(cls.temp.name) / "base")
        cls.heldout = Fixture.create(Path(cls.temp.name) / "heldout", variant="heldout")
        cls.scaled = Fixture.create(Path(cls.temp.name) / "scaled", scale=2)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def query(self, action, args, fixture=None):
        fixture = fixture or self.base
        return fixture.exact_query(action, args, source=fixture.source)["result"]

    def test_retained_exact_solids_agree_with_independent_analytic_expectations(self):
        for fixture, gap, shell_gap, common in ((self.base, 3, .5, .25),
                                               (self.heldout, 4, .25, .5),
                                               (self.scaled, 6, 1, 2)):
            with self.subTest(revision=fixture.revision):
                twins = self.query("pair", {"first": "twin-a", "second": "twin-b"}, fixture)
                self.assertAlmostEqual(twins["distance_m"], gap, places=7)
                shell = self.query("pair", {"first": "u-shell", "second": "insert"}, fixture)
                self.assertAlmostEqual(shell["distance_m"], shell_gap, places=7)
                self.assertAlmostEqual(shell["common_volume_m3"], 0, places=7)
                clash = self.query("pair", {"first": "clash-a", "second": "clash-b"}, fixture)
                self.assertAlmostEqual(clash["common_volume_m3"], common, places=7)
                self.assertAlmostEqual(clash["distance_m"], 0, places=7)

    def test_bbox_overlap_does_not_imply_solid_overlap_or_zero_clearance(self):
        shell = self.query("shape", {"id": "u-shell"})["bbox"]
        insert = self.query("shape", {"id": "insert"})["bbox"]
        self.assertTrue(all(a <= b and c <= d for a, b, c, d in
                            zip(shell["min"], insert["max"], insert["min"], shell["max"])))
        pair = self.query("pair", {"first": "u-shell", "second": "insert"})
        self.assertGreater(pair["distance_m"], 0)
        self.assertEqual(pair["common_volume_m3"], 0)

    def test_full_scene_hlr_preserves_identity_behind_occluder(self):
        for fixture in (self.base, self.heldout, self.scaled):
            front = {r["id"]: r for r in self.query("visibility", {"view": "front", "ids": ["twin-a", "twin-b"]}, fixture)["objects"]}
            self.assertEqual(front["twin-a"]["visible_edges"], 0)
            self.assertGreater(front["twin-a"]["hidden_edges"], 0)
            self.assertGreater(front["twin-b"]["visible_edges"], 0)
            back = self.query("visibility", {"view": "back", "ids": ["twin-a"]}, fixture)["objects"][0]
            self.assertGreater(back["visible_edges"], 0)

    def test_all_five_pngs_match_current_production_model_view_byte_for_byte(self):
        views = self.base.render_views()
        # _drawing_recipe has loaded the actual Studio package.
        from archflow_studio_api.application import drawings
        verified = self.base._verified()
        binding = SimpleNamespace(repository=self.base.repository)
        with patch.object(drawings, "_complete_source", return_value=(self.base.elevation_source, verified.receipt)):
            for view in VIEWS:
                png, width, height = drawings.model_view(binding, model_source=None, view=view)
                self.assertEqual(png, views[view]["png"])
                self.assertEqual((width, height), (views[view]["width"], views[view]["height"]))
                self.assertLessEqual(max(width, height), 1024)
                with Image.open(BytesIO(png)) as image:
                    self.assertEqual(image.mode, "L", "current baseline is grayscale lines, not RGB")

    def test_state_dependencies_unknown_role_and_coordinates(self):
        snapshot = self.base.snapshot()
        ids = {row["id"] for row in snapshot["entities"]}
        self.assertEqual(len(ids), 13)
        self.assertTrue(all(edge["source"] in ids and edge["target"] in ids for edge in snapshot["dependencies"]))
        self.assertEqual(set(self.query("dependencies", {"ids": ["screen"]})["closure"]),
                         {"screen", "lintel", "seal", "fixing", "drain"})
        self.assertEqual(set(self.query("dependencies", {"ids": ["twin-b"]})["closure"]), {"twin-b", "drain"})
        self.assertEqual(self.query("dependencies", {"ids": ["twin-a"]})["closure"], ["twin-a"])
        self.assertIsNone(next(e for e in snapshot["entities"] if e["id"] == "mystery")["metadata"]["role"])
        self.assertEqual(cad_to_hub((10, 2.5, .5)), (10, .5, 2.5))
        self.assertEqual(hub_to_cad(cad_to_hub((10, 2.5, .5))), (10, 2.5, .5))
        for fixture in (self.base, self.heldout):
            self.assertEqual(self.query("point", {"id": "insert", "point": [10, 2.5, .5]}, fixture)["classification"], "inside")
            self.assertEqual(self.query("point", {"id": "u-shell", "point": [10, 2.5, .5]}, fixture)["classification"], "outside")

    def test_stale_binding_and_tampered_retained_step_are_refused(self):
        with self.assertRaises(SourceMismatch):
            self.heldout.exact_query("state", {}, source=self.base.source)
        with self.assertRaises(SourceMismatch):
            self.base.exact_query("state", {}, source={**self.base.source, "step_sha256": "0" * 64})
        mixed = replace(self.base, record=self.heldout.record)
        with self.assertRaises(SourceMismatch):
            mixed.exact_query("state", {}, source=mixed.source)
        invalid = replace(self.base, elevation_source=replace(self.base.elevation_source, step_sha256="0" * 64))
        with self.assertRaises(DrawingElevationError):
            invalid.exact_query("pair", {"first": "u-shell", "second": "insert"}, source=invalid.source)

    def test_queries_are_read_only_and_snapshot_mutations_do_not_change_source(self):
        before = self.base.repository.read_head()
        original_digest = self.base.record.digest
        snapshot = self.base.snapshot()
        snapshot["entities"][0]["params"]["height"] = 9000
        self.query("pair", {"first": "u-shell", "second": "insert"})
        self.assertEqual(self.base.record.digest, original_digest)
        self.assertEqual(self.base.repository.read_head(), before)

    def test_role_drift_changes_bound_declaration_without_changing_geometry(self):
        drift = Fixture.create(Path(self.temp.name) / "role-drift", variant="role-drift")
        self.assertNotEqual(drift.record.digest, self.base.record.digest)
        self.assertNotEqual(drift.record.state_digest, self.base.record.state_digest)
        base_rows = {e["id"]: e for e in self.base.snapshot()["entities"]}
        drift_rows = {e["id"]: e for e in drift.snapshot()["entities"]}
        self.assertEqual(base_rows.keys(), drift_rows.keys())
        for identifier in base_rows:
            self.assertEqual(base_rows[identifier]["params"], drift_rows[identifier]["params"])
            if identifier != "mystery":
                self.assertEqual(base_rows[identifier], drift_rows[identifier])
            if identifier != "ground":
                self.assertEqual(self.query("shape", {"id": identifier}),
                                 self.query("shape", {"id": identifier}, drift))
        declared = self.query("state", {"ids": ["mystery"]}, drift)["entities"][0]["metadata"]
        self.assertEqual(declared["label"], "unclassified object")
        self.assertIn(declared["role"], ROLE_IDS)
        self.assertEqual(declared["role"], "role.structural_support")
        self.assertIsNone(base_rows["mystery"]["metadata"]["role"])
        self.assertEqual(self.base.snapshot()["dependencies"], drift.snapshot()["dependencies"])
        with self.assertRaises(SourceMismatch):
            drift.exact_query("state", {"ids": ["mystery"]}, source=self.base.source)


if __name__ == "__main__":
    unittest.main()
