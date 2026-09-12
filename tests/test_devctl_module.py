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


class CapabilityLookupTests(unittest.TestCase):
    """Finding a capability by the goal it serves, in the same registry file."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        registry_path = Path(self.temporary.name) / "modules.json"
        entry = {
            "capability_id": "candidate.modify_existing",
            "owner": "studio.intent",
            "execution_owner": "studio.candidate",
            "kind": "authoring",
            "status": "PARTIAL",
            "purpose": "Change one number on a built element.",
            "purpose_zh": "修改已建构件的一个数值。",
            "goals": ["change a height", "改高度"],
            "aliases": ["调整高度", "体块高度", "adjust the height", "keep another object unchanged"],
            "reads": ["StateRecord"], "writes": ["candidate_run_via_P036"],
            "effects": ["change_one_existing_value"],
            "entrypoints": ["POST /api/capabilities/{capability_id}/run"],
            "works": [f"supported case {i}" for i in range(11)],
            "missing": ["creating an element"],
            "inputs_ref": "OpenAPI:#/components/schemas/CapabilityRunRequestDto",
            "estimated_cost": "one deterministic parse and one candidate run",
        }
        registry_path.write_text(json.dumps({
            "schema": "ArchFlowModuleRegistry@1", "modules": [], "capabilities": [entry]}), encoding="utf-8")
        registry_patch = patch.object(devctl, "MODULE_REGISTRY", registry_path)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)

    def run_cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = devctl.main(["capability", *args])
        return code, output.getvalue()

    def test_what_a_person_types_finds_the_entry_through_the_served_rule(self) -> None:
        """The CLI and the running service share one matcher, so both hit these."""

        from archflow_studio_api.application.capability import match_capabilities

        self.assertIs(devctl._matcher(), match_capabilities)
        for said in ("把这个体块高度改成4.2米，雨棚不动",
                     "change the main body height to 4.2 m and keep the canopy"):
            code, output = self.run_cli(said, "--json")
            self.assertEqual(code, 0, said)
            result = json.loads(output)
            self.assertEqual(result["capability"]["capability_id"], "candidate.modify_existing", said)
            self.assertTrue(result["matched"], said)

    def test_a_goal_in_either_language_finds_the_entry_and_its_owner(self) -> None:
        for goal in ("change a height", "改高度", "candidate.modify_existing"):
            code, output = self.run_cli(goal, "--json")
            self.assertEqual(code, 0, goal)
            entry = json.loads(output)["capability"]
            self.assertEqual(entry["capability_id"], "candidate.modify_existing")
            self.assertEqual(entry["owner"], "studio.intent")
            self.assertEqual(entry["execution_owner"], "studio.candidate")
            self.assertEqual(entry["status"], "PARTIAL")
            self.assertEqual(entry["inputs_ref"], "OpenAPI:#/components/schemas/CapabilityRunRequestDto")

    def test_long_sections_page_like_a_module_contract(self) -> None:
        _, output = self.run_cli("candidate.modify_existing", "--json")
        result = json.loads(output)
        self.assertEqual(len(result["capability"]["works"]), 8)
        self.assertEqual(result["omitted"]["works"], {"before": 0, "after": 3, "total": 11})
        _, page = self.run_cli("candidate.modify_existing", "--section", "works", "--offset", "8", "--json")
        self.assertEqual(json.loads(page)["capability"]["works"], [f"supported case {i}" for i in range(8, 11)])

    def test_no_hit_says_what_the_index_is_rather_than_that_it_is_missing(self) -> None:
        code, output = self.run_cli("produce a working drawing set")
        self.assertEqual(code, 1)
        self.assertIn("is not the list of everything the system does", output)
        self.assertIn("devctl module", output)
        self.assertNotIn("MISSING", output)

    def test_invalid_page_size_is_rejected_before_reading_anything(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_cli("change a height", "--limit", "0")
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
