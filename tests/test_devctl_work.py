"""Observable, read-only work lookup over disposable coordination records.

Since #358 the registry holds live GitHub Issue claims only: an Issue claimed
directly, or through named lanes. ``devctl work`` lists them with their Issue,
checkout, base and paths, and knows nothing about work cards.
"""

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
from tools.archcheck import REGISTRY_SCHEMA


ISSUES = "https://github.com/cogco1/MonkeyHub/issues/"
UNRENDERED = "Existing generated map; work lookup must not render it.\n"


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
        (self.root / "docs").mkdir()
        for relative in ("docs/SYSTEM_MAP.md", "docs/SEMANTIC_REGISTRY.md"):
            (self.root / relative).write_text(UNRENDERED, encoding="utf-8")
        self.lanes = [
            self._lane("a-appserver", "apps/monkeyhub/app_server.py", "fixture.appserver"),
            self._lane("b-blender", "archflow/adapters/blender_backend.py", "adapters.cad_execution"),
            self._lane("c-unrelated", "monkeydiagram/drawing.py", "fixture.unrelated"),
        ]
        self.direct = {
            "id": "GH-56", "status": "active", "branch": "codex/56-archives",
            "worktree": str(self.root / "worktrees" / "56-archives"), "base_ref": "simulation-main-base",
            "contributor": "simulation:archives", "reviewer": None, "handoff": None,
            "modules": ["project.archive"], "write_scope": ["tools/archive_fixture.py"], "depends_on": [],
        }
        self.data = {"schema": REGISTRY_SCHEMA, "items": [{"id": "GH-78", "lanes": self.lanes}, self.direct]}
        self._save()
        for field, value in (
            ("ROOT", self.root), ("REGISTRY_PATH", self.registry_path),
            ("SYSTEM_MAP", self.root / "docs/SYSTEM_MAP.md"),
            ("SEMANTIC_REGISTRY", self.root / "docs/SEMANTIC_REGISTRY.md"),
        ):
            patcher = patch.object(devctl, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _lane(self, lane_id, path, module):
        return {
            "id": lane_id, "status": "active",
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

    def test_three_simulated_lanes_and_a_direct_claim_are_listed_with_narrow_claims(self):
        code, result = self._json()
        self.assertEqual(code, 0)
        self.assertIsNone(result["query"])
        self.assertEqual(result["findings"], [])
        self.assertEqual([item["id"] for item in result["items"]], [
            "GH-78/a-appserver", "GH-78/b-blender", "GH-78/c-unrelated", "GH-56",
        ])
        for lane, returned in zip(self.lanes, result["items"]):
            self.assertEqual(returned, {**lane, "id": f"GH-78/{lane['id']}"})
        self.assertEqual(result["items"][3], self.direct)
        code, text = self._run()
        self.assertEqual(code, 0)
        for lane in self.lanes:
            self.assertIn(f"GH-78/{lane['id']} [active] {ISSUES}78", text)
            self.assertIn(lane["branch"], text)
            self.assertNotIn(lane["worktree"], text)
            self.assertNotIn(lane["handoff"], text)
        self.assertIn(f"GH-56 [active] {ISSUES}56", text)
        self.assertIn("codex/56-archives | base simulation-main-base | simulation:archives", text)
        self.assertIn("paths: 1", text)
        self.assertIn("depends on: none", text)
        self.assertNotIn("card", text)

    def test_exact_issue_and_lane_queries_preserve_detail_without_unrelated_rows(self):
        code, result = self._json("GH-78")
        self.assertEqual(code, 0)
        self.assertEqual([row["id"] for row in result["items"]], [f"GH-78/{lane['id']}" for lane in self.lanes])
        code, result = self._json("GH-78/b-blender")
        self.assertEqual(code, 0)
        self.assertEqual(result["query"], "GH-78/b-blender")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["write_scope"], ["archflow/adapters/blender_backend.py"])
        code, text = self._run("GH-78/b-blender")
        self.assertEqual(code, 0)
        for field in ("worktree", "reviewer", "handoff", "modules", "write_scope", "depends_on"):
            self.assertIn(f"  {field}:", text)
        self.assertNotIn("card", text)
        self.assertIn(self.lanes[1]["worktree"], text)
        self.assertIn(self.lanes[1]["handoff"], text)
        for other in ("a-appserver", "c-unrelated", "GH-56"):
            self.assertNotIn(other, text)

    def test_unknown_and_partial_queries_return_one_without_fuzzy_matches(self):
        for query in ("GH-999", "GH-78/missing", "blender", "GH-7", "P115", "P115/b-blender"):
            with self.subTest(query=query):
                code, result = self._json(query)
                self.assertEqual(code, 1)
                self.assertEqual(result, {"query": query, "items": [], "findings": []})
        code, text = self._run("GH-78/missing")
        self.assertEqual(code, 1)
        self.assertIn("No work matches", text)

    def test_overlap_and_dependency_warn_globally_then_explicit_blocking_resolves_claims(self):
        first, second, _ = self.lanes
        first["write_scope"] = ["apps/monkeyhub/"]
        second.update(write_scope=["apps/monkeyhub/app_server.py"], depends_on=["GH-78/a-appserver"])
        self._save()
        code, result = self._json("GH-78/c-unrelated")
        self.assertEqual(code, 1)
        self.assertEqual([item["id"] for item in result["items"]], ["GH-78/c-unrelated"])
        self.assertEqual({finding["code"] for finding in result["findings"]}, {"SCOPE_OVERLAP", "LANE_DEPENDENCY"})
        code, text = self._run("GH-78/c-unrelated")
        self.assertEqual(code, 1)
        self.assertIn("SCOPE_OVERLAP:", text)
        self.assertIn("GH-78/a-appserver", text)
        self.assertIn("GH-78/b-blender", text)
        self.assertIn("depends_on", text)
        self.assertIn("handoff/order", text)

        second.update(status="blocked", blocked_reason="Wait for the simulated appserver contract review",
                      handoff="Simulation A completes review before simulation B begins its shared-path change")
        self._save()
        code, result = self._json("GH-78/b-blender")
        self.assertEqual(code, 0)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["items"][0]["status"], "blocked")
        self.assertEqual(result["items"][0]["depends_on"], ["GH-78/a-appserver"])
        self.assertEqual(result["items"][0]["blocked_reason"], second["blocked_reason"])

        self.lanes.remove(first)
        second.update(status="active", blocked_reason=None)
        self._save()
        code, result = self._json("GH-78/b-blender")
        self.assertEqual(code, 0)
        self.assertEqual(result["findings"], [], "a released predecessor no longer blocks its dependent lane")

    def test_a_blocked_claim_shows_its_own_reason(self):
        self.direct.update(status="blocked", blocked_reason="Waiting for the archive format decision (#54)")
        self._save()
        code, text = self._run("GH-56")
        self.assertEqual(code, 0)
        self.assertIn("  blocked_reason: Waiting for the archive format decision (#54)\n", text)
        code, result = self._json("GH-56")
        self.assertEqual(result["items"], [self.direct], "JSON keeps the registry record unchanged")

    def test_a_registry_with_every_claim_released_is_valid(self):
        self.data["items"] = []
        self._save()
        code, result = self._json()
        self.assertEqual(code, 0)
        self.assertEqual(result, {"query": None, "items": [], "findings": []})
        self.assertEqual(self._run(), (0, "No live claims.\n"))
        self.assertEqual(self._json("GH-56")[0], 1)

    def test_an_unreadable_registry_is_named_instead_of_a_traceback(self):
        for label, text in (("list", "[]"), ("truncated", "{"), ("empty", ""), ("items", '{"schema": "x"}')):
            with self.subTest(case=label):
                self.registry_path.write_text(text, encoding="utf-8")
                with self.assertRaises(SystemExit) as raised:
                    devctl.main(["work"])
                self.assertIsInstance(raised.exception.code, str)
                self.assertIn("work registry", raised.exception.code)

    def test_malformed_claims_report_findings_instead_of_crashing(self):
        original = deepcopy(self.data)
        for label, lanes in (
            ("non-list", "invalid lanes"),
            ("non-object", [None]),
            ("invalid-status", [{**self.lanes[0], "status": ["active"]}]),
            ("invalid-field-types", [{**self.lanes[0], "reviewer": {}, "modules": None, "depends_on": "GH-56"}]),
            ("unsafe-path", [{**self.lanes[0], "write_scope": ["../outside.py"]}]),
            ("blocked-without-reason", [{**self.lanes[0], "status": "blocked"}]),
            ("backlog-status", [{**self.lanes[0], "status": "done"}]),
        ):
            with self.subTest(case=label):
                self.data = deepcopy(original)
                self.data["items"][0]["lanes"] = lanes
                self._save()
                code, result = self._json("GH-78")
                self.assertEqual(code, 1)
                self.assertTrue(result["findings"])
                self.assertEqual({finding["code"] for finding in result["findings"]}, {"LANE_METADATA"})
                code, text = self._run("GH-78")
                self.assertEqual(code, 1)
                self.assertIn("LANE_METADATA:", text)

    def test_card_era_entries_are_reported_as_findings(self):
        self.data["items"].append({
            "id": "P115", "status": "blocked", "goal": "Frozen legacy index",
            "card": "docs/mapping/planning/P115-capability-consolidation.md", "write_scope": [],
        })
        self.direct["goal"] = "Portable project archives"
        self._save()
        code, result = self._json()
        self.assertEqual(code, 1)
        self.assertEqual({finding["code"] for finding in result["findings"]}, {"WORK_ITEM"})
        messages = " ".join(finding["message"] for finding in result["findings"])
        self.assertIn("P115", messages)
        self.assertIn("goal", messages)
        code, text = self._run()
        self.assertIn("WORK_ITEM:", text)

    def test_a_card_era_registry_is_refused_with_the_schema_it_must_become(self):
        self.data["schema"] = "ArchFlowDevelopmentRegistry@2"
        self._save()
        with self.assertRaisesRegex(SystemExit, REGISTRY_SCHEMA):
            devctl.load_registry()

    def test_every_lookup_leaves_the_registry_and_generated_maps_byte_identical(self):
        before = self._bytes()
        for args in ((), ("--json",), ("GH-78",), ("GH-78/b-blender", "--json"), ("GH-56",), ("missing",)):
            self._run(*args)
        self.assertEqual(self._bytes(), before)

    def test_the_card_era_views_are_gone(self):
        for command in ("status", "next"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    devctl.main([command])
                self.assertEqual(raised.exception.code, 2)

    def test_render_map_renders_the_module_and_semantic_maps_without_reading_live_work(self):
        self.registry_path.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(devctl.main(["render-map"]), 0)
        for relative in ("docs/SYSTEM_MAP.md", "docs/SEMANTIC_REGISTRY.md"):
            self.assertNotEqual((self.root / relative).read_text(encoding="utf-8"), UNRENDERED, relative)
        self.assertEqual(sorted(path.name for path in (self.root / "docs").iterdir()),
                         ["SEMANTIC_REGISTRY.md", "SYSTEM_MAP.md"])


if __name__ == "__main__":
    unittest.main()
