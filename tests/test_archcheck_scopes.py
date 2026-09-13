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


def _lane(lane_id: str, status: str, *scope: str, **changes: object) -> dict[str, object]:
    return {
        "id": lane_id, "status": status, "issue": "#13",
        "branch": f"codex/{lane_id}", "worktree": f"C:/fixture-worktrees/{lane_id}",
        "base_ref": "main", "contributor": "fixture-contributor",
        "reviewer": None, "handoff": None, "modules": ["tools.archcheck"],
        "write_scope": list(scope), "depends_on": [], **changes,
    }


def _laned_card(*lanes: dict[str, object], scope: tuple[str, ...] = ("archflow/", "apps/", "monkeyarch/")) -> dict[str, object]:
    return {**_card("P115", "active", *scope), "lanes": list(lanes)}


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


class LaneOverlapTests(unittest.TestCase):
    def findings(self, *lanes: dict[str, object], cards: tuple[dict[str, object], ...] = ()) -> tuple[object, ...]:
        return tuple(check_scopes(Path("."), POLICY, _registry(_laned_card(*lanes), *cards)))

    def test_narrow_lanes_replace_the_broad_parent_claim(self) -> None:
        findings = self.findings(
            _lane("hub-runtime", "active", "apps/monkeyhub/api/", *SHARED),
            _lane("cad-contract", "review", "archflow/adapters/cad_backend.py", *SHARED),
            _lane("modeling", "active", "monkeyarch/capabilities/", *SHARED),
            cards=(_card("P201", "active", "archflow/state/"),),
        )
        self.assertEqual((), findings)

    def test_overlapping_active_and_review_lanes_name_the_paths_and_handoff(self) -> None:
        findings = self.findings(
            _lane("hub-runtime", "active", "apps/monkeyhub/api/"),
            _lane("hub-recovery", "review", "apps/monkeyhub/api/server.py"),
        )
        self.assertEqual(["SCOPE_OVERLAP"], [item.code for item in findings])
        for text in ("P115/hub-runtime", "P115/hub-recovery", "apps/monkeyhub/api/server.py", "handoff/order"):
            self.assertIn(text, findings[0].message)

    def test_a_lane_still_collides_with_another_live_legacy_card(self) -> None:
        findings = self.findings(
            _lane("cad-contract", "active", "archflow/adapters/cad_backend.py"),
            cards=(_card("P201", "ready", "archflow/adapters/"),),
        )
        self.assertEqual(["SCOPE_OVERLAP"], [item.code for item in findings])
        self.assertIn("P115/cad-contract", findings[0].message)
        self.assertIn("P201", findings[0].message)

    def test_shared_tests_and_generated_maps_do_not_require_handoff(self) -> None:
        self.assertEqual((), self.findings(
            _lane("cad-contract", "active", "archflow/adapters/", *SHARED),
            _lane("modeling", "review", "monkeyarch/capabilities/", *SHARED),
        ))

    def test_planned_blocked_and_done_lanes_do_not_occupy_paths_or_checkouts(self) -> None:
        for status in ("planned", "blocked", "done"):
            with self.subTest(status=status):
                first = _lane("cad-contract", "active", "archflow/adapters/")
                second = _lane("blender", status, "archflow/adapters/",
                               branch=first["branch"], worktree=first["worktree"],
                               blocked_reason="Waiting for CAD contract merge")
                self.assertEqual((), self.findings(first, second))

    def test_live_lanes_cannot_start_while_a_declared_lane_dependency_remains(self) -> None:
        for status in ("active", "review"):
            for prerequisite in ("planned", "active", "review", "blocked"):
                with self.subTest(status=status, prerequisite=prerequisite):
                    findings = self.findings(
                        _lane("cad-contract", prerequisite, "archflow/adapters/cad_backend.py",
                              blocked_reason="Waiting for review"),
                        _lane("blender", status, "archflow/adapters/blender_backend.py",
                              depends_on=["P115/cad-contract"]),
                    )
                    self.assertEqual(["LANE_DEPENDENCY"], [item.code for item in findings])
                    self.assertIn("P115/blender", findings[0].message)
                    self.assertIn("P115/cad-contract", findings[0].message)

    def test_live_legacy_dependencies_block_a_lane_but_finished_dependencies_do_not(self) -> None:
        for status in ("active", "ready", "blocked"):
            with self.subTest(status=status):
                findings = self.findings(
                    _lane("blender", "active", "archflow/adapters/blender_backend.py", depends_on=["P201"]),
                    cards=(_card("P201", status, "apps/monkeyhub/api/"),),
                )
                self.assertEqual(["LANE_DEPENDENCY"], [item.code for item in findings])
        self.assertEqual((), self.findings(
            _lane("cad-contract", "done", "archflow/adapters/cad_backend.py"),
            _lane("blender", "active", "archflow/adapters/blender_backend.py",
                  depends_on=["P115/cad-contract", "P999/removed-lane"]),
        ))

    def test_live_lanes_cannot_share_a_branch_or_worktree(self) -> None:
        for field in ("branch", "worktree"):
            with self.subTest(field=field):
                first = _lane("cad-contract", "active", "archflow/adapters/")
                findings = self.findings(first, _lane("hub-runtime", "review", "apps/monkeyhub/api/", **{field: first[field]}))
                self.assertEqual(["LANE_CHECKOUT"], [item.code for item in findings])
                for text in ("P115/cad-contract", "P115/hub-runtime", field):
                    self.assertIn(text, findings[0].message)
        for path in (r"c:\users\asus\dev\repo", "C:/Users/asus/dev/other/../repo/"):
            with self.subTest(windows_path=path):
                findings = self.findings(
                    _lane("cad-contract", "active", "archflow/adapters/", worktree="C:/Users/asus/dev/repo"),
                    _lane("hub-runtime", "review", "apps/monkeyhub/api/", worktree=path),
                )
                self.assertEqual(["LANE_CHECKOUT"], [item.code for item in findings])

    def test_non_string_status_produces_diagnostics_instead_of_crashing(self) -> None:
        for status in ([], {}, None, 1):
            with self.subTest(status=status):
                findings = self.findings(_lane("cad-contract", status, "archflow/adapters/"))
                self.assertTrue(findings)
                self.assertTrue(all(item.code == "LANE_METADATA" for item in findings))
                self.assertTrue(any("status" in item.message for item in findings))

    def test_live_work_requires_a_discoverable_checkout_base_and_contributor(self) -> None:
        for status in ("active", "review"):
            for field in ("branch", "worktree", "base_ref", "contributor"):
                with self.subTest(status=status, field=field):
                    lane = _lane("cad-contract", status, "archflow/adapters/")
                    lane.pop(field)
                    findings = self.findings(lane)
                    self.assertTrue(any(item.code == "LANE_METADATA" and field in item.message for item in findings))

    def test_blocked_work_requires_a_readable_reason(self) -> None:
        for reason in (None, "", "   ", []):
            with self.subTest(reason=reason):
                findings = self.findings(_lane("blender", "blocked", "archflow/adapters/", blocked_reason=reason))
                self.assertTrue(any(item.code == "LANE_METADATA" and "blocked_reason" in item.message for item in findings))

    def test_invalid_lane_paths_report_metadata_errors_without_crashing(self) -> None:
        for scope in (
            "archflow/", [], [None], [1], [""], ["../outside/"], ["/absolute/"],
            ["C:/outside/"], ["archflow\\state\\"], ["archflow/**"], ["archflow//state/"], ["./archflow/"],
        ):
            with self.subTest(scope=scope):
                findings = self.findings(_lane("cad-contract", "active", write_scope=scope))
                self.assertTrue(findings)
                self.assertTrue(all(item.code == "LANE_METADATA" for item in findings))


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

    def test_legacy_card_subject_suffixes_keep_the_registered_scope(self) -> None:
        for subject in ("P301-owner: valid change", "P301/occt: before lane metadata existed"):
            with self.subTest(subject=subject):
                self.commit(subject, {"archflow/state/value.py": subject})
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

    def start_lanes(self, *lanes: dict[str, object], scope: tuple[str, ...] = ("archflow/state/",)) -> dict[str, object]:
        registry = _registry(_laned_card(*lanes, scope=scope))
        self.base = self.commit("P000-governance: declare contributor lanes", {REGISTRY_PATH: registry})
        return registry

    def test_lane_commits_must_fit_both_the_narrow_lane_and_parent_card(self) -> None:
        self.start_lanes(_lane("foo", "active", "archflow/state/value.py", "apps/outside.py"))
        self.commit("P115/foo: one allowed change", {
            "archflow/state/value.py": "VALUE = 1\n",
            "tests/test_value.py": "assert True\n",
            "docs/mapping/lanes.md": "handoff\n",
        })
        self.assertEqual((), self.findings())
        self.commit("P115/foo: exceed the lane and its parent", {
            "archflow/state/other.py": "VALUE = 2\n",
            "apps/outside.py": "VALUE = 3\n",
        })
        findings = self.findings()
        self.assertEqual({"archflow/state/other.py", "apps/outside.py"}, {item.path for item in findings})
        self.assertTrue(all(item.code == "SCOPE_VIOLATION" and "P115/foo" in item.message for item in findings))

    def test_parent_only_and_unknown_lane_subjects_may_touch_only_shared_paths(self) -> None:
        self.start_lanes(_lane("foo", "active", "archflow/state/"))
        for subject in ("P115: no lane selected", "P115/missing: no registered lane"):
            with self.subTest(subject=subject):
                self.base = _git(self.root, "rev-parse", "HEAD").strip()
                self.commit(subject, {
                    "archflow/state/value.py": subject,
                    "tests/test_value.py": subject,
                    "docs/mapping/lanes.md": subject,
                })
                findings = self.findings()
                self.assertEqual(["archflow/state/value.py"], [item.path for item in findings])
                self.assertIn("P115/<lane>", findings[0].message)

    def test_a_lane_subject_does_not_acquire_the_other_lane_named_in_its_body(self) -> None:
        self.start_lanes(
            _lane("foo", "active", "archflow/state/first.py"),
            _lane("bar", "review", "archflow/state/second.py"),
        )
        self.commit("P115/foo: update the first owner\n\nUnblocks P115/bar after merge.", {
            "archflow/state/first.py": "VALUE = 1\n",
            "archflow/state/second.py": "VALUE = 2\n",
        })
        self.assertEqual(["archflow/state/second.py"], [item.path for item in self.findings()])

    def test_only_a_lane_closing_commit_can_reuse_its_first_parent_scope(self) -> None:
        for closure in ("removed", "done", "parent-removed"):
            with self.subTest(closure=closure):
                lane = _lane("foo", "active", "archflow/state/")
                self.start_lanes(lane)
                if closure == "parent-removed":
                    registry = _registry()
                else:
                    remaining = () if closure == "removed" else ({**lane, "status": "done"},)
                    registry = _registry(_laned_card(*remaining, scope=("archflow/state/",)))
                closing = self.commit("P115/foo: deliver and close", {
                    REGISTRY_PATH: registry, "archflow/state/value.py": closure,
                })
                self.assertEqual((), self.findings())
                later = self.commit("P115/foo: stale lane cannot keep writing", {"archflow/state/late.py": closure})
                findings = self.findings()
                self.assertEqual(["archflow/state/late.py"], [item.path for item in findings])
                self.assertIn(later[:8], findings[0].message)
                self.assertNotIn(closing[:8], findings[0].message)

    def test_pausing_or_planning_a_lane_does_not_borrow_its_previous_write_scope(self) -> None:
        for status in ("blocked", "planned"):
            with self.subTest(status=status):
                lane = _lane("foo", "active", "archflow/state/")
                self.start_lanes(lane)
                self.commit("P115/foo: pause work and still change source", {
                    REGISTRY_PATH: _registry(_laned_card(
                        {**lane, "status": status, "blocked_reason": "Waiting for contract merge"},
                        scope=("archflow/state/",),
                    )),
                    "archflow/state/value.py": status,
                })
                self.assertEqual(["archflow/state/value.py"], [item.path for item in self.findings()])

    def test_closing_lane_cannot_widen_the_scope_it_borrows_from_its_parent(self) -> None:
        lane = _lane("foo", "active", "archflow/state/value.py")
        self.start_lanes(lane)
        self.commit("P115/foo: close with a wider retired declaration", {
            REGISTRY_PATH: _registry(_laned_card(
                {**lane, "status": "done", "write_scope": ["archflow/state/"]},
                scope=("archflow/state/",),
            )),
            "archflow/state/value.py": "VALUE = 1\n",
            "archflow/state/unclaimed.py": "VALUE = 2\n",
        })
        self.assertEqual(["archflow/state/unclaimed.py"], [item.path for item in self.findings()])

    def test_later_lane_scope_changes_do_not_reclassify_earlier_commits(self) -> None:
        lane = _lane("foo", "active", "archflow/state/")
        self.start_lanes(lane)
        earlier = self.commit("P115/foo: original lane scope", {"archflow/state/value.py": "VALUE = 1\n"})
        self.commit("P115/foo: narrow future lane work", {
            REGISTRY_PATH: _registry(_laned_card(
                {**lane, "write_scope": ["archflow/state/other.py"]}, scope=("archflow/state/",),
            )),
        })
        later = self.commit("P115/foo: outside the new lane scope", {"archflow/state/value.py": "VALUE = 2\n"})
        findings = self.findings()
        self.assertEqual(["archflow/state/value.py"], [item.path for item in findings])
        self.assertIn(later[:8], findings[0].message)
        self.assertNotIn(earlier[:8], findings[0].message)


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
