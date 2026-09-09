"""Write scopes are the boundary between people developing in parallel.

Two checks live here. ``check_scopes`` reads the work registry and refuses two
live cards that claim the same path, so nobody starts a day owning the same
directory as somebody else. ``check_changed_scopes`` reads a branch and refuses
a commit that wrote outside the scope of the card it names, so the boundary is
enforced where it is actually crossed rather than only declared.

The second one needs real commits, so it builds a throwaway Git repository in a
temporary directory; nothing here reads or writes this repository.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.archcheck import (
    ArchitecturePolicyError, _index_tree, check_changed_scopes, check_imports,
    check_registry, check_scopes, load_policy,
)


SHARED = (
    "docs/mapping/",
    "governance/module_registry.json",
    "governance/work_registry.json",
    "tests/",
)
POLICY = {"shared_write_scope": list(SHARED)}
POLICY_PATH = "governance/architecture_policy.json"
REGISTRY_PATH = "governance/work_registry.json"


def _registry(*items: dict[str, object]) -> dict[str, object]:
    return {"schema": "ArchFlowDevelopmentRegistry@2", "items": list(items)}


def _card(card_id: str, status: str, *scope: str) -> dict[str, object]:
    return {"id": card_id, "status": status, "write_scope": list(scope)}


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        (
            "git",
            "-c",
            "user.name=archcheck test",
            "-c",
            "user.email=archcheck@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class WorkflowBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(Path(__file__).resolve().parents[1] / POLICY_PATH)

    def test_core_and_peer_workflow_reverse_imports_are_refused(self) -> None:
        for source, target in (
            ("archflow/state/example.py", "monkeyarch.capabilities.element_producers"),
            ("archflow/adapters/example.py", "monkeydiagram.drawing_svg"),
            ("monkeyarch/example.py", "monkeydiagram.drawing_svg"),
            ("monkeydiagram/example.py", "monkeyarch.compilers.geometry"),
        ):
            with self.subTest(source=source, target=target):
                findings = tuple(check_imports(source, _index_tree(ast.parse(f"import {target}")), self.policy))
                self.assertTrue(any(f.code == "LAYER_AUTHORITY_VIOLATION" for f in findings))

    def test_workflows_may_consume_shared_contracts(self) -> None:
        for source in ("monkeyarch/example.py", "monkeydiagram/example.py"):
            with self.subTest(source=source):
                findings = tuple(check_imports(source, _index_tree(ast.parse(
                    "from archflow.state.geometry_program import CompiledGeometryProgram"
                )), self.policy))
                self.assertEqual((), findings)

    def test_registry_checks_dependencies_from_each_workflow_package(self) -> None:
        for package in ("monkeyarch", "monkeydiagram"):
            with self.subTest(package=package), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write(root, "tools/consumer.py", f"import {package}.example\n")
                _write(root, "governance/module_registry.json", json.dumps({"modules": [{
                    "module_id": "tools.consumer", "owner_path": "tools/consumer.py",
                    "depends_on": [], "untested_reason": "synthetic checker fixture",
                }]}))
                findings = tuple(check_registry(root, self.policy))
                self.assertTrue(any(f.code == "REGISTRY_DEPENDS_ON_DRIFT" for f in findings))


class ScopeOverlapTests(unittest.TestCase):
    def test_two_live_cards_claiming_the_same_directory_are_refused(self) -> None:
        registry = _registry(
            _card("P201", "active", "archflow/state/", "tests/"),
            _card("P202", "ready", "archflow/state/", "docs/mapping/"),
        )
        findings = tuple(check_scopes(Path("."), POLICY, registry))
        self.assertEqual(1, len(findings), findings)
        finding = findings[0]
        self.assertEqual("SCOPE_OVERLAP", finding.code)
        self.assertEqual("governance/work_registry.json", finding.path)
        self.assertIn("P201", finding.message)
        self.assertIn("P202", finding.message)
        self.assertIn("archflow/state/", finding.message)

    def test_a_directory_containing_another_cards_file_is_an_overlap(self) -> None:
        registry = _registry(
            _card("P203", "ready", "apps/archflow-studio/"),
            _card("P204", "ready", "apps/archflow-studio/api/settings.py"),
        )
        findings = tuple(check_scopes(Path("."), POLICY, registry))
        self.assertEqual(["SCOPE_OVERLAP"], [item.code for item in findings])
        self.assertIn("apps/archflow-studio/api/settings.py", findings[0].message)

    def test_the_shared_ledgers_are_not_an_overlap(self) -> None:
        registry = _registry(
            _card("P205", "active", *SHARED, "archflow/state/"),
            _card("P206", "ready", *SHARED, "archflow/runtime/"),
        )
        self.assertEqual((), tuple(check_scopes(Path("."), POLICY, registry)))

    def test_a_card_nobody_is_working_on_does_not_collide(self) -> None:
        registry = _registry(
            _card("P207", "active", "archflow/state/"),
            _card("P208", "blocked", "archflow/state/"),
        )
        self.assertEqual((), tuple(check_scopes(Path("."), POLICY, registry)))

    def test_no_registry_is_not_a_finding(self) -> None:
        self.assertEqual((), tuple(check_scopes(Path("."), POLICY, None)))


class ChangedScopeTests(unittest.TestCase):
    """One temporary repository, four commits, read back by ``--changed``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        root = Path(cls._temporary.name) / "repo"
        root.mkdir()
        cls.root = root
        _git(root, "init", "-q")
        cls.registry = _registry(
            _card("P301", "active", "archflow/state/", "tests/"),
            _card("P302", "ready", "docs/mapping/"),
        )
        _write(root, "README.md", "base\n")
        _write(root, POLICY_PATH, json.dumps(POLICY))
        _write(root, REGISTRY_PATH, json.dumps(cls.registry))
        _git(root, "add", "README.md", POLICY_PATH, REGISTRY_PATH)
        _git(root, "commit", "-q", "-m", "base")
        cls.base = _git(root, "rev-parse", "HEAD").strip()

        _write(root, "archflow/state/evidence.py", "VALUE = 1\n")
        _git(root, "add", "archflow/state/evidence.py")
        _git(root, "commit", "-q", "-m", "P301 evidence ledger owner\n\nInside scope.")

        _write(root, "apps/archflow-studio/api/app.py", "VALUE = 2\n")
        _git(root, "add", "apps/archflow-studio/api/app.py")
        _git(root, "commit", "-q", "-m", "P301 reach into the Studio as well")

        _write(root, "tests/test_ledger.py", "assert True\n")
        _git(root, "add", "tests/test_ledger.py")
        _git(root, "commit", "-q", "-m", "Tidy the suite while passing through")

        _write(root, "tools/oneoff.py", "VALUE = 3\n")
        _git(root, "add", "tools/oneoff.py")
        _git(root, "commit", "-q", "-m", "P000-governance drive-by tool")

        _write(root, "archflow/state/ledger.py", "VALUE = 4\n")
        _git(root, "add", "archflow/state/ledger.py")
        _git(
            root,
            "commit",
            "-q",
            "-m",
            "P301 the ledger owner\n\nUnblocks P302 and supersedes P303.",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _findings(self) -> tuple[object, ...]:
        return tuple(check_changed_scopes(self.root, self.base))

    def test_a_commit_inside_its_cards_scope_passes(self) -> None:
        self.assertNotIn(
            "archflow/state/evidence.py",
            [finding.path for finding in self._findings()],
        )

    def test_a_commit_outside_its_cards_scope_is_a_violation(self) -> None:
        violations = [
            finding
            for finding in self._findings()
            if finding.code == "SCOPE_VIOLATION"
        ]
        self.assertEqual(
            ["apps/archflow-studio/api/app.py", "tools/oneoff.py"],
            [finding.path for finding in violations],
        )
        self.assertIn("P301", violations[0].message)

    def test_a_commit_without_a_card_may_still_touch_a_shared_ledger(self) -> None:
        self.assertNotIn(
            "tests/test_ledger.py",
            [finding.path for finding in self._findings()],
        )

    def test_explicit_governance_cannot_write_an_unowned_tool(self) -> None:
        findings = [
            finding
            for finding in self._findings()
            if finding.path == "tools/oneoff.py"
        ]
        self.assertEqual(["SCOPE_VIOLATION"], [finding.code for finding in findings])
        self.assertIn("P000-governance", findings[0].message)

    def test_the_subject_decides_when_the_body_names_other_cards(self) -> None:
        """A body says what a change unblocks; that is not its own card."""

        self.assertNotIn(
            "archflow/state/ledger.py",
            [finding.path for finding in self._findings()],
        )

    def test_a_deleted_file_counts_as_changed(self) -> None:
        root = Path(self._temporary.name) / "deletion"
        root.mkdir()
        _git(root, "init", "-q")
        _write(root, "tools/gone.py", "VALUE = 1\n")
        _write(root, POLICY_PATH, json.dumps(POLICY))
        _write(root, REGISTRY_PATH, json.dumps(self.registry))
        _git(root, "add", "tools/gone.py", POLICY_PATH, REGISTRY_PATH)
        _git(root, "commit", "-q", "-m", "base")
        base = _git(root, "rev-parse", "HEAD").strip()
        _git(root, "rm", "-q", "tools/gone.py")
        _git(root, "commit", "-q", "-m", "P301 remove the tool")
        findings = tuple(check_changed_scopes(root, base))
        self.assertEqual(
            [("tools/gone.py", "SCOPE_VIOLATION")],
            [(finding.path, finding.code) for finding in findings],
        )


class HistoricalScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q")
        self.base = self.commit(
            "base",
            {
                "README.md": "base\n",
                POLICY_PATH: POLICY,
                REGISTRY_PATH: _registry(_card("P301", "active", "archflow/state/")),
            },
        )

    def commit(self, message: str, files: dict[str, object]) -> str:
        for relative, value in files.items():
            if value is None:
                (self.root / relative).unlink()
            else:
                _write(
                    self.root, relative,
                    value if isinstance(value, str) else json.dumps(value),
                )
        _git(self.root, "add", "--", *files)
        _git(self.root, "commit", "-q", "-m", message)
        return _git(self.root, "rev-parse", "HEAD").strip()

    def findings(self) -> tuple[object, ...]:
        return tuple(check_changed_scopes(self.root, self.base))

    def test_governance_may_maintain_only_its_named_files_and_readmes(self) -> None:
        self.commit(
            "P000-governance: maintain repository guidance",
            {
                "README.md": "updated\n",
                "archflow/project/README.md": "project package\n",
                "tools/archcheck.py": "# checker\n",
                POLICY_PATH: {**POLICY, "notes": "scope guidance"},
                ".github/workflows/verify.yml": "# CI\n",
                ".github/pull_request_template.md": "PR guidance\n",
                "CONTRIBUTING.md": "contributor guidance\n",
                "AGENTS.md": "agent guidance\n",
                "docs/SYSTEM_MAP.md": "system map\n",
            },
        )
        self.assertEqual((), self.findings())
        forbidden = {
            "archflow/project/NOTES.md": "other document\n",
            "archflow/project/README.md.py": "VALUE = 1\n",
            "archflow/state/value.py": "VALUE = 1\n",
            "skills/example/SKILL.md": "skill behavior\n",
            "tools/oneoff.py": "VALUE = 1\n",
        }
        self.commit("P000-governance: unrelated changes", forbidden)
        self.assertEqual(
            [(path, "SCOPE_VIOLATION") for path in sorted(forbidden)],
            [(item.path, item.code) for item in self.findings()],
        )

    def test_unknown_card_and_uncarded_commits_do_not_gain_governance_scope(self) -> None:
        self.commit(
            "P999: no registered owner",
            {"archflow/state/value.py": "VALUE = 1\n", "README.md": "unknown card\n"},
        )
        self.commit("Uncarded documentation", {"archflow/project/README.md": "package\n"})
        self.assertEqual(
            {"README.md", "archflow/state/value.py", "archflow/project/README.md"},
            {item.path for item in self.findings()},
        )
        self.assertTrue(all(item.code == "SCOPE_UNDECLARED" for item in self.findings()))

    def test_a_hyphenated_card_subject_keeps_its_registered_scope(self) -> None:
        self.commit("P301-owner: valid change", {"archflow/state/value.py": "VALUE = 1\n"})
        self.assertEqual((), self.findings())

    def test_a_partial_governance_marker_does_not_gain_readme_scope(self) -> None:
        self.commit("P000-governance-extra: not the marker", {"README.md": "updated\n"})
        self.commit("P3010: invalid four-digit card", {"archflow/state/value.py": "VALUE = 1\n"})
        self.assertEqual(
            [("README.md", "SCOPE_UNDECLARED"), ("archflow/state/value.py", "SCOPE_UNDECLARED")],
            [(item.path, item.code) for item in self.findings()],
        )

    def test_a_closing_commit_keeps_its_scope_but_the_next_commit_does_not(self) -> None:
        self.commit("P301: delivered change", {"archflow/state/value.py": "VALUE = 1\n"})
        self.commit(
            "P301: finish the card",
            {REGISTRY_PATH: _registry(), "archflow/state/value.py": "VALUE = 2\n"},
        )
        self.assertEqual((), self.findings())
        self.commit("P301: work after retirement", {"archflow/state/late.py": "VALUE = 3\n"})
        self.assertEqual(
            [("archflow/state/late.py", "SCOPE_UNDECLARED")],
            [(item.path, item.code) for item in self.findings()],
        )

    def test_later_card_scope_changes_do_not_reclassify_earlier_commits(self) -> None:
        earlier = self.commit("P301: original scope", {"archflow/state/value.py": "VALUE = 1\n"})
        self.commit(
            "P301: narrow future work",
            {REGISTRY_PATH: _registry(_card("P301", "active", "archflow/state/other.py"))},
        )
        later = self.commit("P301: outside new scope", {"archflow/state/value.py": "VALUE = 2\n"})
        findings = self.findings()
        self.assertEqual(["SCOPE_VIOLATION"], [item.code for item in findings])
        self.assertIn(later[:8], findings[0].message)
        self.assertNotIn(earlier[:8], findings[0].message)

    def test_a_retained_nonlive_row_grants_scope_only_to_its_closing_commit(self) -> None:
        self.commit(
            "P301: complete the card",
            {
                REGISTRY_PATH: _registry(_card("P301", "done", "archflow/state/")),
                "archflow/state/value.py": "VALUE = 1\n",
            },
        )
        self.assertEqual((), self.findings())
        self.commit("P301: work after completion", {"archflow/state/late.py": "VALUE = 2\n"})
        self.assertEqual(
            [("archflow/state/late.py", "SCOPE_UNDECLARED")],
            [(item.path, item.code) for item in self.findings()],
        )

    def test_later_shared_scope_does_not_erase_an_earlier_violation(self) -> None:
        earlier = self.commit("P301: outside scope", {"apps/other.py": "VALUE = 1\n"})
        self.commit(
            "P000-governance: change shared scope",
            {POLICY_PATH: {"shared_write_scope": [*SHARED, "apps/"]}},
        )
        self.commit("P301: now shared", {"apps/other.py": "VALUE = 2\n"})
        findings = self.findings()
        self.assertEqual(["SCOPE_VIOLATION"], [item.code for item in findings])
        self.assertIn(earlier[:8], findings[0].message)

    def test_policy_field_removal_is_an_error(self) -> None:
        self.commit("P000-governance: remove field", {POLICY_PATH: {}})
        with self.assertRaisesRegex(ArchitecturePolicyError, "missing shared_write_scope"):
            self.findings()

    def test_policy_file_removal_is_an_error(self) -> None:
        self.commit("P000-governance: remove policy", {POLICY_PATH: None})
        with self.assertRaisesRegex(ArchitecturePolicyError, "missing policy"):
            self.findings()

    def test_a_missing_selected_policy_cannot_disable_the_check(self) -> None:
        self.commit("Uncarded source", {"apps/other.py": "VALUE = 1\n"})
        with self.assertRaisesRegex(ArchitecturePolicyError, "missing policy"):
            tuple(check_changed_scopes(self.root, self.base, "missing-policy.json"))

    def test_malformed_policy_is_an_error(self) -> None:
        self.commit("P000-governance: corrupt policy", {POLICY_PATH: "{"})
        with self.assertRaisesRegex(ArchitecturePolicyError, "invalid governance/architecture_policy.json"):
            self.findings()

    def test_invalid_scope_field_is_an_error(self) -> None:
        self.commit("P000-governance: corrupt scope", {POLICY_PATH: {"shared_write_scope": "apps/"}})
        with self.assertRaisesRegex(ArchitecturePolicyError, "shared_write_scope must be a string list"):
            self.findings()

    def test_a_base_after_policy_removal_cannot_restart_the_rule(self) -> None:
        self.base = self.commit("P000-governance: remove field", {POLICY_PATH: {}})
        self.commit("Uncarded source", {"apps/other.py": "VALUE = 1\n"})
        with self.assertRaisesRegex(ArchitecturePolicyError, "missing shared_write_scope"):
            self.findings()

    def test_missing_registry_is_an_error_even_for_governance(self) -> None:
        self.commit("P000-governance: remove registry", {REGISTRY_PATH: None})
        with self.assertRaisesRegex(ArchitecturePolicyError, "invalid work registry"):
            self.findings()


    def test_the_rule_does_not_reach_its_predecessors_or_bootstrap(self) -> None:
        self.root = Path(self.temporary.name) / "before_rule"
        self.root.mkdir()
        _git(self.root, "init", "-q")
        self.base = self.commit(
            "Before the scope rule",
            {POLICY_PATH: {}, REGISTRY_PATH: _registry(_card("P301", "active", "archflow/state/"))},
        )

        self.commit("Old uncarded change", {"apps/old.py": "VALUE = 1\n"})
        self.commit(
            "Introduce scope checks",
            {POLICY_PATH: POLICY, "tools/checker_bootstrap.py": "# bootstrap\n"},
        )
        self.commit("P301: first scoped change", {"archflow/state/value.py": "VALUE = 1\n"})
        self.assertEqual((), self.findings())
        self.commit("P301: new violation", {"apps/new.py": "VALUE = 2\n"})
        self.assertEqual(
            [("apps/new.py", "SCOPE_VIOLATION")],
            [(item.path, item.code) for item in self.findings()],
        )


if __name__ == "__main__":
    unittest.main()
