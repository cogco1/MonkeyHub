from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import devctl


class ModuleLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        registry_path = Path(self.temporary.name) / "modules.json"
        wall = {
            "module_id": "capabilities.wall_solver",
            "owner_path": "monkeyarch/capabilities/wall_solver.py",
            "purpose": "Solve a wall from its authored line and height.",
            "owns": ["wall geometry"], "does_not_own": ["opening geometry"],
            "inputs": ["wall"], "outputs": ["geometry"],
            "public_api": ["solve_wall"], "depends_on": ["state.design"],
            "files": ["monkeyarch/capabilities/wall_solver.py", "monkeyarch/capabilities/wall_helpers.py"],
            "tests": ["tests/test_wall_solver.py"],
            "invariants": [f"wall condition {i}" for i in range(12)],
            "status": "canonical",
        }
        related = [
            {"module_id": f"drawing.panel_{i:02d}", "owner_path": f"drawing/panel_{i:02d}.py",
             "purpose": "Draw wall panels.", "owns": ["private drawing contract"]}
            for i in range(30)
        ]
        registry_path.write_text(json.dumps({"schema": "ArchFlowModuleRegistry@1", "modules": [*related, wall]}), encoding="utf-8")
        registry_patch = patch.object(devctl, "MODULE_REGISTRY", registry_path)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        work_patch = patch.object(devctl, "load_registry", side_effect=AssertionError("module lookup must not load work registry"))
        work_patch.start()
        self.addCleanup(work_patch.stop)

    def run_cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = devctl.main(["module", *args])
        return code, output.getvalue()

    def test_exact_id_reports_only_the_owned_contract_and_source_paths(self) -> None:
        code, output = self.run_cli("capabilities.wall_solver", "--json")
        self.assertEqual(code, 0)
        result = json.loads(output)
        module = result["module"]
        self.assertEqual(module["public_api"], ["solve_wall"])
        self.assertEqual(module["owns"], ["wall geometry"])
        self.assertEqual(module["does_not_own"], ["opening geometry"])
        self.assertEqual(module["depends_on"], ["state.design"])
        self.assertEqual(module["source_paths"], ["monkeyarch/capabilities/wall_solver.py", "monkeyarch/capabilities/wall_helpers.py"])
        self.assertEqual(module["tests"], ["tests/test_wall_solver.py"])
        self.assertNotIn("private drawing contract", output)
        self.assertNotIn("candidates", result)

    def test_keyword_ambiguity_returns_a_bounded_page_ranked_by_module_id(self) -> None:
        code, output = self.run_cli("wall", "--json")
        result = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(result["match_count"], 31)
        self.assertEqual(len(result["candidates"]), 8)
        self.assertEqual(result["candidates"][0]["module_id"], "capabilities.wall_solver")
        self.assertEqual(result["remaining"], 23)
        self.assertNotIn("private drawing contract", output)
        self.assertNotIn("invariants", output)
        _, second_output = self.run_cli("wall", "--offset", "8", "--json")
        second = json.loads(second_output)
        self.assertTrue(set(m["module_id"] for m in result["candidates"]).isdisjoint(m["module_id"] for m in second["candidates"]))

    def test_truncated_contract_exposes_remaining_rules_through_section_paging(self) -> None:
        _, output = self.run_cli("capabilities.wall_solver")
        self.assertIn("--section invariants --offset 8", output)
        _, page = self.run_cli("capabilities.wall_solver", "--section", "invariants", "--offset", "8", "--json")
        result = json.loads(page)
        self.assertEqual(result["module"]["invariants"], [f"wall condition {i}" for i in range(8, 12)])
        self.assertNotIn("owns", result["module"])
        self.assertEqual(result["omitted"]["invariants"], {"before": 8, "after": 0, "total": 12})

    def test_unique_keyword_and_unknown_query_have_clear_results(self) -> None:
        code, output = self.run_cli("authored line")
        self.assertEqual(code, 0)
        self.assertIn("capabilities.wall_solver", output)
        code, output = self.run_cli("unknown module")
        self.assertEqual(code, 1)
        self.assertEqual(output, "No modules match 'unknown module'.\n")

    def test_invalid_page_size_is_rejected_before_loading_the_registry(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_cli("wall", "--limit", "1000")
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
