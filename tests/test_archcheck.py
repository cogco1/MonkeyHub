from __future__ import annotations

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
