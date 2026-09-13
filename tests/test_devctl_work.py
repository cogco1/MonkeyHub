"""Observable, read-only work lookup over disposable collaboration records."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tools import devctl


class WorkLookupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry_path = self.root / "governance" / "work_registry.json"
        self.registry_path.parent.mkdir()
        source_root = Path(devctl.__file__).resolve().parents[1]
        (self.registry_path.parent / "architecture_policy.json").write_bytes(
            (source_root / "governance" / "architecture_policy.json").read_bytes()
        )
        card_path = "docs/mapping/planning/P115-fixture.md"
        legacy_path = "docs/mapping/planning/P114-fixture.md"
        for relative in (card_path, legacy_path):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Disposable CLI simulation\n", encoding="utf-8")
        for relative in ("docs/DYNAMIC_MAP.md", "docs/SYSTEM_MAP.md", "docs/SEMANTIC_REGISTRY.md", "docs/mapping/planning/INDEX.md"):
            (self.root / relative).write_text("Existing generated map; work lookup must not render it.\n", encoding="utf-8")
        self.lanes = [
            self._lane("a-appserver", "apps/monkeyhub/app_server.py", "fixture.appserver"),
            self._lane("b-blender", "archflow/adapters/blender_backend.py", "adapters.cad_execution"),
            self._lane("c-unrelated", "monkeydiagram/drawing.py", "fixture.unrelated"),
        ]
        self.legacy = {
            "id": "P114", "status": "ready", "goal": "Legacy card fixture without lanes",
            "card": legacy_path, "depends_on": [], "write_scope": ["tools/legacy_fixture.py"],
        }
        self.data = {
            "schema": devctl.SCHEMA,
            "items": [{
                "id": "P115", "status": "active", "goal": "Three simulated independent contributors",
                "card": card_path, "depends_on": [],
                "write_scope": [lane["write_scope"][0] for lane in self.lanes],
                "lanes": self.lanes,
            }, self.legacy],
        }
        self._save()
        for field, value in (
            ("ROOT", self.root), ("REGISTRY_PATH", self.registry_path),
            ("MAP_PATH", self.root / "docs/DYNAMIC_MAP.md"),
            ("SYSTEM_MAP", self.root / "docs/SYSTEM_MAP.md"),
            ("SEMANTIC_REGISTRY", self.root / "docs/SEMANTIC_REGISTRY.md"),
            ("PLANNING_INDEX", self.root / "docs/mapping/planning/INDEX.md"),
        ):
            patcher = patch.object(devctl, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _lane(self, lane_id, path, module):
        return {
            "id": lane_id, "status": "active", "issue": f"simulation:{lane_id}",
            "branch": f"codex/simulation-{lane_id}", "worktree": str(self.root / "worktrees" / lane_id),
            "base_ref": "simulation-main-base", "contributor": f"simulation:{lane_id}",
            "reviewer": "simulation:reviewer", "handoff": f"Read {lane_id} results before integration",
            "modules": [module], "write_scope": [path], "depends_on": [],
        }

    def _save(self):
        self.registry_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def _run(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = devctl.main(["work", *args])
        self.assertEqual(stderr.getvalue(), "")
        return code, stdout.getvalue()

    def _json(self, *args):
        code, output = self._run(*args, "--json")
        return code, json.loads(output)

    def _bytes(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_three_independent_simulated_lanes_are_listed_with_narrow_claims(self):
        code, result = self._json()
        self.assertEqual(code, 0)
        self.assertIsNone(result["query"])
        self.assertEqual(result["findings"], [])
        self.assertEqual([item["id"] for item in result["items"]], [
            "P115/a-appserver", "P115/b-blender", "P115/c-unrelated", "P114",
        ])
        for lane, returned in zip(self.lanes, result["items"]):
            self.assertEqual(returned, {**lane, "id": f"P115/{lane['id']}", "card": self.data["items"][0]["card"]})
        code, text = self._run()
        self.assertEqual(code, 0)
        for lane in self.lanes:
            self.assertIn(f"P115/{lane['id']} [active]", text)
            self.assertIn(lane["branch"], text)
            self.assertNotIn(lane["worktree"], text)
            self.assertNotIn(lane["handoff"], text)
        self.assertIn("paths: 1", text)
        self.assertIn("depends on: none", text)

    def test_exact_card_and_lane_queries_preserve_detail_without_unrelated_rows(self):
        code, result = self._json("P115")
        self.assertEqual(code, 0)
        self.assertEqual([row["id"] for row in result["items"]], [f"P115/{lane['id']}" for lane in self.lanes])
        code, result = self._json("P115/b-blender")
        self.assertEqual(code, 0)
        self.assertEqual(result["query"], "P115/b-blender")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["write_scope"], ["archflow/adapters/blender_backend.py"])
        code, text = self._run("P115/b-blender")
        self.assertEqual(code, 0)
        for field in ("worktree", "reviewer", "handoff", "modules", "write_scope", "depends_on", "card"):
            self.assertIn(f"  {field}:", text)
        self.assertIn(self.lanes[1]["worktree"], text)
        self.assertIn(self.lanes[1]["handoff"], text)
        self.assertNotIn("a-appserver", text)
        self.assertNotIn("c-unrelated", text)
        self.assertNotIn("P114", text)

    def test_unknown_and_partial_queries_return_one_without_fuzzy_matches(self):
        for query in ("P999", "P115/missing", "blender", "P11"):
            with self.subTest(query=query):
                code, result = self._json(query)
                self.assertEqual(code, 1)
                self.assertEqual(result, {"query": query, "items": [], "findings": []})
        code, text = self._run("P115/missing")
        self.assertEqual(code, 1)
        self.assertIn("No work matches", text)

    def test_overlap_and_dependency_warn_globally_then_explicit_blocking_resolves_claims(self):
        first, second, _ = self.lanes
        first["write_scope"] = ["apps/monkeyhub/"]
        second.update(write_scope=["apps/monkeyhub/app_server.py"], depends_on=["P115/a-appserver"])
        self._save()
        code, result = self._json("P115/c-unrelated")
        self.assertEqual(code, 1)
        self.assertEqual([item["id"] for item in result["items"]], ["P115/c-unrelated"])
        self.assertEqual({finding["code"] for finding in result["findings"]}, {"SCOPE_OVERLAP", "LANE_DEPENDENCY"})
        code, text = self._run("P115/c-unrelated")
        self.assertEqual(code, 1)
        self.assertIn("SCOPE_OVERLAP:", text)
        self.assertIn("P115/a-appserver", text)
        self.assertIn("P115/b-blender", text)
        self.assertIn("depends_on", text)
        self.assertIn("handoff/order", text)

        second.update(status="blocked", blocked_reason="Wait for the simulated appserver contract review",
                      handoff="Simulation A completes review before simulation B begins its shared-path change")
        self._save()
        code, result = self._json("P115/b-blender")
        self.assertEqual(code, 0)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["items"][0]["status"], "blocked")
        self.assertEqual(result["items"][0]["depends_on"], ["P115/a-appserver"])
        self.assertEqual(result["items"][0]["blocked_reason"], second["blocked_reason"])

        first["status"] = "done"
        second.update(status="active", blocked_reason=None)
        self._save()
        code, result = self._json("P115/b-blender")
        self.assertEqual(code, 0)
        self.assertEqual(result["findings"], [], "a done predecessor no longer blocks its dependent lane")

    def test_legacy_card_lookup_and_empty_registry_remain_valid(self):
        code, result = self._json("P114")
        self.assertEqual(code, 0)
        self.assertEqual(result["items"], [self.legacy])
        self.assertEqual(result["findings"], [])
        self.data["items"] = []
        self._save()
        code, result = self._json()
        self.assertEqual(code, 0)
        self.assertEqual(result, {"query": None, "items": [], "findings": []})
        self.assertEqual(self._json("P114")[0], 1)

    def test_malformed_lane_metadata_reports_findings_instead_of_crashing(self):
        original = deepcopy(self.data)
        for label, lanes in (
            ("non-list", "invalid lanes"),
            ("non-object", [None]),
            ("invalid-status", [{**self.lanes[0], "status": ["active"]}]),
            ("invalid-field-types", [{**self.lanes[0], "reviewer": {}, "modules": None, "depends_on": "P114"}]),
            ("unsafe-path", [{**self.lanes[0], "write_scope": ["../outside.py"]}]),
            ("blocked-without-reason", [{**self.lanes[0], "status": "blocked"}]),
        ):
            with self.subTest(case=label):
                self.data = deepcopy(original)
                self.data["items"][0]["lanes"] = lanes
                self._save()
                code, result = self._json("P115")
                self.assertEqual(code, 1)
                self.assertTrue(result["findings"])
                self.assertEqual({finding["code"] for finding in result["findings"]}, {"LANE_METADATA"})
                code, text = self._run("P115")
                self.assertEqual(code, 1)
                self.assertIn("LANE_METADATA:", text)

    def test_every_lookup_leaves_registry_cards_and_generated_maps_byte_identical(self):
        before = self._bytes()
        for args in ((), ("--json",), ("P115",), ("P115/b-blender", "--json"), ("P114",), ("missing",)):
            self._run(*args)
        self.assertEqual(self._bytes(), before)


if __name__ == "__main__":
    unittest.main()
