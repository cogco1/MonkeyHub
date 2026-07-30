from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import devctl


ROOT = Path(__file__).resolve().parents[1]
DEVCTL = ROOT / "tools" / "devctl.py"


class DevelopmentControlTests(unittest.TestCase):
    def run_devctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DEVCTL), *args],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_registry_status_and_completed_scope_pass(self) -> None:
        status = self.run_devctl("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("P001 | archive | done", status.stdout)
        self.assertRegex(
            status.stdout,
            r"(?m)^P002 \| (planning|archive) \| (ready|active|done) \|",
        )
        scope = self.run_devctl("check-scope", "P001", "archflow/state/model.py")
        self.assertEqual(scope.returncode, 0, scope.stderr)

    def test_check_scope_without_paths_is_an_error_not_a_pass(self) -> None:
        result = self.run_devctl("check-scope", "P001")
        self.assertEqual(result.returncode, 2)
        self.assertIn("verifies nothing", result.stderr)
        self.assertNotIn("scope PASS", result.stdout)

    def test_scope_escape_is_rejected(self) -> None:
        result = self.run_devctl(
            "check-scope",
            "P001",
            "..\\ARCHFLOW_V3\\README.md",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("path escapes repository", result.stderr)

    def test_verification_commands_are_structured_with_safe_fallback(self) -> None:
        fallback = devctl.normalized_verification_commands({"id": "P999"})
        self.assertEqual(fallback[0][0], sys.executable)
        self.assertEqual(
            fallback[0][1:],
            ("-m", "unittest", "discover", "-s", "tests"),
        )
        explicit = devctl.normalized_verification_commands(
            {
                "id": "P999",
                "verification_commands": [
                    ["{python}", "-m", "unittest", "tests.test_devctl"]
                ],
            }
        )
        self.assertEqual(explicit[0][0], sys.executable)
        with self.assertRaises(devctl.RegistryError):
            devctl.normalized_verification_commands(
                {"id": "P999", "verification_commands": []}
            )

    def test_architecture_firewall_is_mandatory_and_not_duplicated(self) -> None:
        item = {
            "id": "P999",
            "verification_commands": [
                ["{python}", "-m", "unittest", "tests.test_devctl"]
            ],
        }
        commands = devctl.required_verification_commands(item)
        self.assertEqual(
            commands[0],
            (sys.executable, "tools/archcheck.py"),
        )
        item["verification_commands"].insert(
            0,
            ["{python}", "tools/archcheck.py"],
        )
        commands = devctl.required_verification_commands(item)
        self.assertEqual(
            commands.count((sys.executable, "tools/archcheck.py")),
            1,
        )

    def test_receipt_binds_contract_and_current_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "src" / "module.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            registry = root / "governance" / "work_registry.json"
            registry.parent.mkdir(parents=True)
            registry.write_text("{}\n", encoding="utf-8")
            item = {
                "id": "P999",
                "origin_stream": "planning",
                "stream": "planning",
                "status": "active",
                "actor": "Codex",
                "goal": "test receipt binding",
                "depends_on": [],
                "write_scope": ["src/", "governance/work_registry.json"],
                "acceptance": ["bound"],
                "tests": ["receipt"],
                "verification_commands": [
                    ["{python}", "-m", "unittest", "tests.test_devctl"]
                ],
                "card": "docs/mapping/planning/P999.md",
            }
            with (
                patch.object(devctl, "ROOT", root),
                patch.object(devctl, "REGISTRY_PATH", registry),
                patch.object(
                    devctl,
                    "LOCK_PATH",
                    root / "governance" / ".work_registry.lock",
                ),
            ):
                item["verification_receipt"] = {
                    "schema": devctl.VERIFICATION_SCHEMA,
                    "item_id": "P999",
                    "actor": "Codex",
                    "contract_sha256": devctl.verification_contract_digest(
                        item
                    ),
                    "scope_sha256": devctl.verification_scope_digest(item),
                    "commands": [{"returncode": 0}],
                }
                devctl.require_current_verification(item)
                source.write_text("VALUE = 2\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    devctl.RegistryError,
                    "source state changed",
                ):
                    devctl.require_current_verification(item)

    def test_receipt_rejects_contract_change_and_malformed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "src.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            registry = root / "registry.json"
            item = {
                "id": "P999",
                "origin_stream": "planning",
                "stream": "planning",
                "status": "active",
                "actor": "Codex",
                "goal": "initial",
                "depends_on": [],
                "write_scope": ["src.py"],
                "acceptance": ["bound"],
                "tests": ["receipt"],
                "card": "P999.md",
            }
            with (
                patch.object(devctl, "ROOT", root),
                patch.object(devctl, "REGISTRY_PATH", registry),
                patch.object(devctl, "LOCK_PATH", root / ".lock"),
            ):
                receipt = {
                    "schema": devctl.VERIFICATION_SCHEMA,
                    "item_id": "P999",
                    "actor": "Codex",
                    "contract_sha256": devctl.verification_contract_digest(
                        item
                    ),
                    "scope_sha256": devctl.verification_scope_digest(item),
                    "commands": [{"returncode": 0}],
                }
                item["verification_receipt"] = receipt
                item["goal"] = "changed"
                with self.assertRaisesRegex(
                    devctl.RegistryError,
                    "work contract changed",
                ):
                    devctl.require_current_verification(item)
                item["goal"] = "initial"
                receipt["commands"] = [{"returncode": 1}]
                with self.assertRaisesRegex(
                    devctl.RegistryError,
                    "verification did not pass",
                ):
                    devctl.require_current_verification(item)

    def test_completed_cards_move_from_open_gap_to_layer_evidence(self) -> None:
        data = {
            "items": [
                {"id": "P100", "status": "done"},
                {"id": "P101", "status": "ready"},
            ],
            "architecture_layers": [
                {
                    "id": "L-test",
                    "evidence_cards": ["P099"],
                    "open_cards": ["P100", "P101"],
                }
            ],
        }
        devctl.sync_completed_architecture_evidence(data)
        layer = data["architecture_layers"][0]
        self.assertEqual(layer["evidence_cards"], ["P099", "P100"])
        self.assertEqual(layer["open_cards"], ["P101"])

    def test_rendered_map_contains_all_ledgers(self) -> None:
        result = self.run_devctl("render-map")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (ROOT / "docs" / "DYNAMIC_MAP.md").read_text(encoding="utf-8")
        for heading in ("Retirement", "Modify", "Planning", "Archive"):
            self.assertIn(heading, text)
        self.assertIn("## Architecture coverage", text)
        self.assertIn("## Dependency lanes", text)
        self.assertIn("L9 Instance-answer isolation", text)
        self.assertIn("L12 External world mutation recovery", text)
        self.assertIn("L13 Project document persistence", text)
        self.assertIn("[project](../archflow/project/README.md)", text)
        self.assertIn("M002", text)
        self.assertIn("M004", text)
        self.assertIn("P021", text)
        self.assertIn("P034", text)
        self.assertIn("Brief to spatial candidate", text)
        archive = (
            ROOT / "docs" / "mapping" / "archive" / "INDEX.md"
        ).read_text(encoding="utf-8")
        planning = (
            ROOT / "docs" / "mapping" / "planning" / "INDEX.md"
        ).read_text(encoding="utf-8")
        self.assertIn("P001", archive)
        self.assertNotIn("P001", planning)

    def test_registry_validates_architecture_layer_card_references(self) -> None:
        status = self.run_devctl("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        rendered = self.run_devctl("render-map")
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        dynamic_map = (
            ROOT / "docs" / "DYNAMIC_MAP.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("P009", dynamic_map)
        self.assertIn("P026", dynamic_map)

    def test_context_capsule_is_deterministic_bounded_and_read_only(self) -> None:
        registry_before = devctl.REGISTRY_PATH.read_bytes()
        map_before = devctl.MAP_PATH.read_bytes()

        first = self.run_devctl("context", "P046")
        second = self.run_devctl("context", "P046")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(devctl.REGISTRY_PATH.read_bytes(), registry_before)
        self.assertEqual(devctl.MAP_PATH.read_bytes(), map_before)
        self.assertFalse(devctl.LOCK_PATH.exists())

        capsule = json.loads(first.stdout)
        digest = capsule.pop("capsule_sha256")
        self.assertEqual(digest, devctl.stable_digest(capsule))
        self.assertEqual(capsule["schema"], devctl.CONTEXT_SCHEMA)
        self.assertEqual(capsule["item"]["id"], "P046")
        self.assertEqual(
            capsule["planning_claims"]["label"],
            "unverified_planning_contract",
        )
        self.assertTrue(capsule["planning_claims"]["stop_conditions"])
        self.assertTrue(capsule["authority"]["read_only"])
        for capability in (
            "can_claim",
            "can_modify",
            "can_verify",
            "can_complete",
            "project_state_authority",
            "evidence_authority",
        ):
            self.assertFalse(capsule["authority"][capability])
        self.assertLessEqual(
            len(capsule["sources"]),
            devctl.MAX_CONTEXT_SOURCES,
        )
        expected_card = devctl.find_item(
            devctl.load_registry(),
            "P046",
        )["card"]
        for source in capsule["sources"]:
            self.assertFalse(source["path"].startswith("probes/"))
            if source["path"].startswith("docs/mapping/"):
                self.assertEqual(source["path"], expected_card)
            self.assertLessEqual(
                source["line_count"],
                devctl.MAX_CONTEXT_LINES,
            )
            self.assertEqual(len(source["content_sha256"]), 64)
            self.assertEqual(len(source["excerpt_sha256"]), 64)

    def test_context_capsule_uses_only_explicit_safe_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "AGENTS.md").write_text(
                "# Rules\n\nStay bounded.\n",
                encoding="utf-8",
            )
            card = root / "docs" / "P999.md"
            card.parent.mkdir(parents=True)
            card.write_text(
                "# P999\n\n## Stop conditions\n\n"
                "- Stop on authority drift.\n",
                encoding="utf-8",
            )
            source = root / "src" / "module.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "line one\nline two\nline three\nline four\n",
                encoding="utf-8",
            )
            item = {
                "id": "P999",
                "status": "active",
                "stream": "planning",
                "actor": "Codex",
                "goal": "bounded context",
                "depends_on": [],
                "write_scope": ["src/module.py"],
                "acceptance": ["bounded"],
                "tests": ["safe sources"],
                "card": "docs/P999.md",
                "evidence": [],
                "context_files": [
                    {
                        "path": "src/module.py",
                        "line_start": 2,
                        "line_end": 3,
                        "purpose": "target implementation seam",
                    }
                ],
            }
            data = {"items": [item]}
            with patch.object(devctl, "ROOT", root):
                capsule = devctl.build_context_capsule(data, "P999")
                explicit = capsule["sources"][-1]
                self.assertEqual(explicit["path"], "src/module.py")
                self.assertEqual(explicit["excerpt"], "line two\nline three")
                self.assertEqual(explicit["line_count"], 2)
                self.assertEqual(
                    capsule["repository_evidence"]["verification"]["label"],
                    "no_machine_verification_receipt",
                )

                unsafe_specs = (
                    "../outside.md",
                    "probes/case.json",
                    "secrets/config.json",
                    "src/missing.py",
                )
                probe = root / "probes" / "case.json"
                probe.parent.mkdir(parents=True)
                probe.write_text("{}\n", encoding="utf-8")
                secret = root / "secrets" / "config.json"
                secret.parent.mkdir(parents=True)
                secret.write_text("{}\n", encoding="utf-8")
                for unsafe in unsafe_specs:
                    with self.subTest(unsafe=unsafe):
                        item["context_files"][0]["path"] = unsafe
                        with self.assertRaises(devctl.RegistryError):
                            devctl.build_context_capsule(data, "P999")

    def test_context_capsule_labels_machine_verification_without_logs(self) -> None:
        data = devctl.load_registry()
        capsule = devctl.build_context_capsule(data, "P022")
        verification = capsule["repository_evidence"]["verification"]
        self.assertEqual(
            verification["label"],
            "machine_verification_receipt",
        )
        self.assertTrue(verification["verified"])
        serialized = json.dumps(capsule, ensure_ascii=False)
        self.assertNotIn("stdout_tail", serialized)
        self.assertNotIn("stderr_tail", serialized)

    def test_framework_and_data_only_probe_boundary_are_clean(self) -> None:
        production_files = tuple((ROOT / "archflow").rglob("*.py"))
        production = "\n".join(
            path.read_text(encoding="utf-8") for path in production_files
        )
        forbidden = (
            "probes.",
            "16x12",
            "community library",
            "reading room",
            "service desk",
            "repair workshop",
            "oak_planks",
            "smooth_stone",
            "slab_on_grade",
            "LiveLibrary",
            "live_library",
        )
        for value in forbidden:
            self.assertNotIn(value, production, value)
        self.assertFalse(
            (ROOT / "archflow" / "runtime" / "live_library_archive.py").exists()
        )
        self.assertFalse(
            (
                ROOT
                / "archflow"
                / "runtime"
                / "live_library_checkpoint.py"
            ).exists()
        )
        self.assertFalse((ROOT / "probes" / "p009_library").exists())
        self.assertFalse((ROOT / ".runs" / "live-library").exists())
        case_python = tuple(
            path
            for path in (ROOT / "probes").rglob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(case_python, ())
        probe_rules = (ROOT / "probes" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("data package", probe_rules)
        self.assertIn("must not contain", probe_rules)


if __name__ == "__main__":
    unittest.main()
