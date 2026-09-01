from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path

from tools import archcheck


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "governance" / "architecture_policy.json"
ARCHCHECK = ROOT / "tools" / "archcheck.py"


class ArchitectureFirewallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = archcheck.load_policy(POLICY_PATH)

    def test_clean_repository_passes_inside_fast_budget(self) -> None:
        started = time.perf_counter()
        findings = archcheck.run_checks(ROOT, self.policy)
        elapsed = time.perf_counter() - started
        self.assertEqual(findings, ())
        self.assertLess(elapsed, 3.0)

    def test_source_index_covers_nodes_and_exact_parents(self) -> None:
        tree = ast.parse(
            "def outer():\n"
            "    value = 1\n"
            "    return value\n"
        )

        index = archcheck._index_tree(tree)

        self.assertEqual(set(index.nodes), set(ast.walk(tree)))
        self.assertEqual(len(index.nodes), len(set(index.nodes)))
        for parent in index.nodes:
            for child in ast.iter_child_nodes(parent):
                self.assertIs(index.parents[child], parent)

    def test_reverse_import_instance_answer_writer_and_authority_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "archflow" / "bad.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "\n".join(
                    (
                        "from pathlib import Path",
                        "from probes.case import DATA",
                        "BUILDING = 'Pantheon'",
                        "class CanonicalStateWriter: pass",
                        "def save():",
                        "    Path('bad.json').write_text('x')",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            findings = archcheck.run_checks(root, self.policy)
            codes = {item.code for item in findings}
            self.assertIn("PROBE_REVERSE_IMPORT", codes)
            self.assertIn("INSTANCE_ANSWER_LITERAL", codes)
            self.assertIn("UNOWNED_FILESYSTEM_WRITE", codes)
            self.assertIn("DUPLICATE_STATE_AUTHORITY", codes)

    def test_framework_imports_cannot_reach_repository_support_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "archflow" / "bad_imports.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "\n".join(
                    (
                        "from docs.guide import NOTES",
                        "from probes.case import DATA",
                        "from tests.helpers import FIXTURE",
                        "from tools.builder import build",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            findings = archcheck.run_checks(root, self.policy)

            violations = {
                (item.code, item.line)
                for item in findings
                if item.path == "archflow/bad_imports.py"
            }
            self.assertIn(("LAYER_AUTHORITY_VIOLATION", 1), violations)
            self.assertIn(("PROBE_REVERSE_IMPORT", 2), violations)
            self.assertIn(("LAYER_AUTHORITY_VIOLATION", 3), violations)
            self.assertIn(("LAYER_AUTHORITY_VIOLATION", 4), violations)

    def test_tool_imports_cannot_reach_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "tools" / "bad.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "from tests.helpers import FIXTURE\n",
                encoding="utf-8",
            )

            findings = archcheck.run_checks(root, self.policy)

            self.assertEqual(
                tuple(
                    (item.path, item.line, item.code)
                    for item in findings
                ),
                (("tools/bad.py", 1, "LAYER_AUTHORITY_VIOLATION"),),
            )

    def test_canonical_low_layers_cannot_import_runtime_or_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = {
                "contracts": "from archflow.runtime import Coordinator\n",
                "ports": "from archflow.adapters import cli_retrieval\n",
                "evidence": "from archflow.adapters.web_evidence import fetch\n",
                "research": "from archflow.validation import validate\n",
                "materials": "from archflow.runtime import Runner\n",
                "control": "from archflow.adapters import cad\n",
            }
            for package, body in sources.items():
                source = root / "archflow" / package / "bad.py"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(body, encoding="utf-8")

            findings = archcheck.run_checks(root, self.policy)

            violations = {
                item.path
                for item in findings
                if item.code == "LAYER_AUTHORITY_VIOLATION"
            }
            self.assertEqual(
                violations,
                {
                    f"archflow/{package}/bad.py"
                    for package in sources
                },
            )

    def test_framework_rejects_only_explicit_project_literals(self) -> None:
        forbidden = ("Pantheon", "Parthenon", "P087", "Pentelic")
        for literal in forbidden:
            with (
                self.subTest(literal=literal),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                source = root / "archflow" / "bad_literal.py"
                source.parent.mkdir(parents=True)
                source.write_text(
                    f"PROJECT_ANSWER = {literal!r}\n",
                    encoding="utf-8",
                )

                findings = archcheck.run_checks(root, self.policy)

                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].code, "INSTANCE_ANSWER_LITERAL")

    def test_generic_vocabulary_and_project_tools_are_not_instance_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            framework = root / "archflow" / "generic.py"
            framework.parent.mkdir(parents=True)
            framework.write_text(
                "RELATIONS = ('column', 'wall', 'door', 'marble', 'support')\n",
                encoding="utf-8",
            )
            project_tool = root / "tools" / "project_runner.py"
            project_tool.parent.mkdir(parents=True)
            project_tool.write_text(
                "PROJECT = 'Parthenon Pentelic P087'\n",
                encoding="utf-8",
            )

            findings = archcheck.run_checks(root, self.policy)

            self.assertEqual(findings, ())

    def test_probe_executable_and_root_run_store_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "archflow").mkdir()
            probe = root / "probes" / "case" / "build.py"
            probe.parent.mkdir(parents=True)
            probe.write_text("print('building')\n", encoding="utf-8")
            (root / ".runs").mkdir()
            findings = archcheck.run_checks(root, self.policy)
            codes = {item.code for item in findings}
            self.assertIn("PROBE_EXECUTABLE", codes)
            self.assertIn("ROOT_RUN_STORE", codes)

    def test_hard_soft_and_commit_ranking_leaks_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evaluation = root / "archflow" / "evaluation" / "bad.py"
            evaluation.parent.mkdir(parents=True)
            evaluation.write_text(
                "from archflow.commit import Committer\n",
                encoding="utf-8",
            )
            commit = root / "archflow" / "commit" / "bad.py"
            commit.parent.mkdir(parents=True)
            commit.write_text(
                "def choose(observation):\n"
                "    return observation.score > 0.8\n",
                encoding="utf-8",
            )
            findings = archcheck.run_checks(root, self.policy)
            codes = {item.code for item in findings}
            self.assertIn("LAYER_AUTHORITY_VIOLATION", codes)
            self.assertIn("SOFT_GATE_PROMOTION_LEAK", codes)

    def test_policy_rejects_broad_non_repository_write_exemption(self) -> None:
        policy = deepcopy(self.policy)
        policy["allowed_write_sites"].append(
            {
                "path": "archflow/runtime/bad.py",
                "function": "*",
                "operations": ["*"],
                "kind": "runtime",
                "owner": "P999",
                "reason": "too broad",
            }
        )
        with self.assertRaises(archcheck.ArchitecturePolicyError):
            archcheck.validate_policy(policy)

    def test_cli_json_result_is_machine_readable(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ARCHCHECK), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "ArchFlowArchitectureCheck@1")
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["findings"], [])


if __name__ == "__main__":
    unittest.main()
