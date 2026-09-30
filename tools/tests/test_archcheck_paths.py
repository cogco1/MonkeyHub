"""Every path archcheck is configured with must exist, and the root holds only the layout.

A check whose path has gone passes forever: a source root that no longer
exists is walked as an empty set, a layer rule whose source matches nothing
never fires, a layer rule's target that names a module nobody can import
forbids nothing, and a registry that names a moved file keeps describing the
old tree. Moving the repository's packages (#484 and the topology lanes after
it) would otherwise leave each of these guards silently switched off.

The cases build small trees in a temporary directory; the root-entry and docs
cases make it a Git repository, because both are read from the index, and so
do the target cases that ask Git what it ignores. Nothing here reads or writes
this repository.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.governance.archcheck import (
    ARCHITECTURE_POLICY, ArchitecturePolicyError, _checked_python_files, _module_name,
    check_docs_layout, check_layer_targets, check_policy_paths, check_registry, check_repository_root,
    run_checks, validate_policy,
)


REGISTRY_PATH = "governance/module_registry.json"

# A body long enough for the duplicate-owner check, which ignores short helpers.
OWNED_FUNCTION = """
def {name}(values):
    total = 0
    for value in values:
        total += value * value
    return total / max(len(values), 1)
"""


def _policy(**changes: object) -> dict[str, object]:
    """A complete policy for a small synthetic tree."""

    policy: dict[str, object] = {
        "schema": "ArchFlowArchitecturePolicy@1",
        "source_root": "archflow",
        "checked_source_roots": ["archflow", "tools"],
        "import_only_source_roots": [],
        "python_source_roots": ["."],
        "repository_root_entries": ["docs", "tools", ".gitignore", "README.md"],
        "shared_write_scope": ["tests/"],
        "unclaimed_write_scope": ["docs/"],
        "probe_root": "probes",
        "forbidden_instance_literals": [],
        "forbidden_framework_identifiers": [],
        "probe_executable_suffixes": [".py"],
        "allowed_write_sites": [],
        "forbidden_layer_imports": [
            {"source": "archflow", "targets": ["tools"], "reason": "The core never imports repository tooling."},
        ],
        "authority_symbol_patterns": [],
        "allowed_authority_symbols": [],
    }
    policy.update(changes)
    validate_policy(policy)
    return policy


def _write(root: Path, relative: str, value: object = "VALUE = 1\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value)
    path.write_text(text, encoding="utf-8", newline="\n")


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-c", "user.name=archcheck test", "-c", "user.email=archcheck@example.invalid", *args),
        cwd=root, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return completed.stdout


def _temporary_root(test: unittest.TestCase, *parts: str) -> Path:
    temporary = tempfile.TemporaryDirectory()
    test.addCleanup(temporary.cleanup)
    root = Path(temporary.name).joinpath(*parts)
    root.mkdir(parents=True, exist_ok=True)
    return root


class PolicyPathTests(unittest.TestCase):
    """A rule on a path that has gone is reported, not skipped."""

    def setUp(self) -> None:
        self.root = _temporary_root(self)
        _write(self.root, "archflow/state/value.py")
        _write(self.root, "tools/check.py")

    def findings(self, policy: dict[str, object]) -> list[tuple[str, str, str]]:
        return [(item.path, item.code, item.message) for item in check_policy_paths(self.root, policy)]

    def test_configured_paths_that_exist_are_not_findings(self) -> None:
        self.assertEqual([], self.findings(_policy()))

    def test_a_checked_root_that_moved_away_is_a_finding(self) -> None:
        findings = self.findings(_policy(checked_source_roots=["archflow", "tools", "monkeydiagram"]))
        self.assertEqual([(ARCHITECTURE_POLICY, "POLICY_PATH_MISSING")], [item[:2] for item in findings])
        self.assertIn("checked_source_roots entry 'monkeydiagram' does not exist", findings[0][2])

    def test_a_checked_root_left_holding_only_caches_is_a_finding(self) -> None:
        # A move leaves the old directory behind with its ignored bytecode.
        _write(self.root, "monkeydiagram/__pycache__/drawing_svg.cpython-312.pyc", "")
        findings = self.findings(_policy(checked_source_roots=["archflow", "tools", "monkeydiagram"]))
        self.assertEqual(["POLICY_PATH_MISSING"], [code for _, code, _ in findings])
        self.assertIn("'monkeydiagram' holds no Python source", findings[0][2])

    def test_a_layer_rule_on_a_source_that_is_gone_is_a_finding(self) -> None:
        rule = {"source": "archflow/evidence", "targets": ["archflow.adapters"], "reason": "Evidence has no adapter."}
        findings = self.findings(_policy(forbidden_layer_imports=[rule]))
        self.assertEqual(["POLICY_PATH_MISSING"], [code for _, code, _ in findings])
        self.assertIn("forbidden_layer_imports[0] source 'archflow/evidence' does not exist", findings[0][2])
        self.assertIn("guards nothing", findings[0][2])

    def test_a_layer_rule_outside_the_checked_roots_guards_nothing(self) -> None:
        _write(self.root, "scripts/dev/start.py")
        rule = {"source": "scripts", "targets": ["archflow"], "reason": "Launchers stay thin."}
        findings = self.findings(_policy(forbidden_layer_imports=[rule]))
        self.assertEqual(["POLICY_PATH_MISSING"], [code for _, code, _ in findings])
        self.assertIn("'scripts' matches no checked Python file", findings[0][2])

    def test_a_layer_rule_may_name_one_module(self) -> None:
        rule = {"source": "tools/check", "targets": ["archflow"], "reason": "The check stays independent."}
        self.assertEqual([], self.findings(_policy(forbidden_layer_imports=[rule])))

    def test_a_python_source_root_that_is_gone_is_a_finding(self) -> None:
        findings = self.findings(_policy(python_source_roots=[".", "packages/archflow/src"]))
        self.assertEqual(["POLICY_PATH_MISSING"], [code for _, code, _ in findings])
        self.assertIn("python_source_roots entry 'packages/archflow/src' does not exist", findings[0][2])

    def test_a_moved_package_needs_its_src_directory_as_a_python_source_root(self) -> None:
        for relative in (
            "packages/monkeydiagram/src/monkeydiagram/drawing_svg.py",
            "packages/monkeydiagram/src/monkeydiagram/documentation/styles.py",
            "services/project-runtime/src/project_runtime/main.py",
        ):
            _write(self.root, relative)
        roots = ["archflow", "tools", "packages", "services"]
        findings = self.findings(_policy(checked_source_roots=roots))
        self.assertEqual(["POLICY_PATH_MISSING"] * 2, [code for _, code, _ in findings])
        self.assertIn("python_source_roots has no entry 'packages/monkeydiagram/src'", findings[0][2])
        self.assertIn("python_source_roots has no entry 'services/project-runtime/src'", findings[1][2])
        listed = [".", "packages/monkeydiagram/src", "services/project-runtime/src"]
        self.assertEqual([], self.findings(_policy(checked_source_roots=roots, python_source_roots=listed)))

    def test_a_write_site_that_is_gone_still_stops_the_check(self) -> None:
        site = {
            "path": "archflow/project/repository.py", "function": "*", "kind": "project_repository",
            "owner": "project.repository", "operations": ["*"], "reason": "The one project writer.",
        }
        with self.assertRaisesRegex(ArchitecturePolicyError, "does not exist: archflow/project/repository.py"):
            validate_policy(_policy(allowed_write_sites=[site]), self.root)


class LayerTargetTests(unittest.TestCase):
    """A layer rule's target names a module that exists (#511).

    Targets are import names, not paths, so a module that moves or goes leaves
    its target naming nothing: archflow.runtime moved into monkeyarch, the
    archflow.commit lane was archived, and the rules naming them kept passing
    while they forbade nothing.
    """

    PYTHON_SOURCE_ROOTS = [".", "packages/archflow/src", "packages/monkeyarch/src"]

    def setUp(self) -> None:
        self.root = _temporary_root(self, "repo")
        _git(self.root, "init", "-q")
        _write(self.root, ".gitignore", "archive/\n")
        for relative in (
            "packages/archflow/src/archflow/contracts/canonical.py",
            "packages/archflow/src/archflow/project/repository.py",
            "packages/monkeyarch/src/monkeyarch/application/project_runner.py",
            "tools/check.py",
        ):
            _write(self.root, relative)
        _write(self.root, "docs/architecture/overview.md", "text\n")
        _write(self.root, "probes/fixture/README.md", "text\n")

    def findings(self, *targets: str, imported: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
        rule = {
            "source": "packages/archflow/src/archflow/contracts", "targets": list(targets),
            "reason": "Contract primitives depend on project identity only.",
        }
        policy = _policy(python_source_roots=self.PYTHON_SOURCE_ROOTS, forbidden_layer_imports=[rule])
        return [(item.path, item.code, item.message) for item in check_layer_targets(self.root, policy, imported)]

    def test_a_module_and_every_package_above_it_resolve(self) -> None:
        self.assertEqual([], self.findings(
            "archflow", "archflow.project", "archflow.project.repository",
            "monkeyarch.application", "monkeyarch.application.project_runner", "tools", "tools.check",
        ))

    def test_a_target_whose_module_is_gone_is_a_finding(self) -> None:
        findings = self.findings("archflow.runtime", "archflow.project.repositories", "archflow.commit")
        self.assertEqual([(ARCHITECTURE_POLICY, "POLICY_TARGET_MISSING")] * 3, [item[:2] for item in findings])
        messages = "\n".join(message for _, _, message in findings)
        for text in (
            "forbidden_layer_imports[0] target 'archflow.runtime' names no module: archflow has no module runtime",
            "target 'archflow.project.repositories' names no module: archflow.project has no module repositories",
            "target 'archflow.commit' names no module: archflow has no module commit",
        ):
            self.assertIn(text, messages)
        self.assertTrue(all(message.endswith("the rule guards nothing against it") for _, _, message in findings))

    def test_each_rule_that_names_a_dead_target_is_reported(self) -> None:
        rules = [
            {"source": "tools", "targets": ["archflow.evaluation"], "reason": "Tools stay independent."},
            {"source": "packages/archflow/src/archflow/contracts", "targets": ["tools", "archflow.evaluation"],
             "reason": "Contract primitives depend on project identity only."},
        ]
        policy = _policy(python_source_roots=self.PYTHON_SOURCE_ROOTS, forbidden_layer_imports=rules)
        messages = sorted(item.message for item in check_layer_targets(self.root, policy, ()))
        self.assertEqual(2, len(messages))
        self.assertTrue(messages[0].startswith("forbidden_layer_imports[0] target 'archflow.evaluation'"))
        self.assertTrue(messages[1].startswith("forbidden_layer_imports[1] target 'archflow.evaluation'"))

    def test_a_module_is_named_as_it_is_imported_not_by_its_path(self) -> None:
        # A src layout's modules are named from their Python source root (#488).
        self.assertEqual([], self.findings("monkeyarch.application"))
        findings = self.findings("packages.monkeyarch.src.monkeyarch.application", "src.monkeyarch")
        self.assertEqual(["POLICY_TARGET_MISSING"] * 2, [code for _, code, _ in findings])

    def test_a_directory_without_python_names_no_module(self) -> None:
        # docs/ holds documents and probes/ project data: nothing there can be imported.
        findings = self.findings("docs", "docs.architecture", "probes")
        self.assertEqual(["POLICY_TARGET_MISSING"] * 3, [code for _, code, _ in findings])

    def test_a_third_party_target_resolves_while_checked_code_imports_it(self) -> None:
        imported = ("numpy", "OCP.gp", "shapely.geometry", "archflow.project.repository")
        self.assertEqual([], self.findings("numpy", "OCP", "shapely", "shapely.geometry", imported=imported))
        findings = self.findings("networkx", "scipy", "OCP.BRepAlgoAPI", imported=imported)
        self.assertEqual(["POLICY_TARGET_MISSING"] * 3, [code for _, code, _ in findings])
        self.assertIn(
            "target 'networkx' names no module under the Python source roots, no checked file imports it",
            findings[0][2],
        )

    def test_an_import_does_not_revive_a_module_the_repository_no_longer_has(self) -> None:
        # A leftover import of a moved module is broken code, not a module to forbid.
        findings = self.findings("archflow.runtime", imported=("archflow.runtime.project_runner",))
        self.assertEqual(["POLICY_TARGET_MISSING"], [code for _, code, _ in findings])

    def test_a_directory_git_ignores_holds_code_outside_the_public_tree(self) -> None:
        # archive/ keeps retired lanes in some checkouts only: an import of it works
        # on one machine and fails on every other, which is why the rules name it.
        self.assertEqual([], self.findings("archive", "archive.lanes.example"))
        findings = self.findings("archives", "private")
        self.assertEqual(["POLICY_TARGET_MISSING"] * 2, [code for _, code, _ in findings])
        self.assertIn("Git ignores no directory of that name", findings[0][2])
        _write(self.root, "archive/lanes/example.py")  # a checkout that keeps the retired lanes
        self.assertEqual([], self.findings("archive", "archive.lanes.example"))


class LayerTargetRunTests(unittest.TestCase):
    """The tree check fails on a dead target and learns third-party use from the checked files."""

    def test_archcheck_reports_a_target_that_names_no_module(self) -> None:
        root = _temporary_root(self, "repo")
        _git(root, "init", "-q")
        _write(root, ".gitignore", "archive/\n")
        _write(root, "README.md", "text\n")
        _write(root, "archflow/state/value.py")
        _write(root, "tools/check.py", "import numpy\n\n\ndef later():\n    from OCP.gp import gp_Pnt\n")
        _git(root, "add", "-A")

        def findings(*targets: str) -> list[tuple[str, str]]:
            rule = {"source": "archflow", "targets": list(targets), "reason": "The core never imports tooling."}
            policy = _policy(
                repository_root_entries=["archflow", "tools", ".gitignore", "README.md"],
                forbidden_layer_imports=[rule],
            )
            return [(item.code, item.message) for item in run_checks(root, policy)]

        self.assertEqual([], findings("tools", "numpy", "OCP", "archive"))
        dead = findings("tools", "scipy", "archflow.runtime")
        self.assertEqual(["POLICY_TARGET_MISSING"] * 2, [code for code, _ in dead])
        self.assertIn("target 'archflow.runtime' names no module", dead[0][1] + dead[1][1])
        self.assertIn("target 'scipy' names no module", dead[0][1] + dead[1][1])


class PolicyShapeTests(unittest.TestCase):
    def test_python_source_roots_are_required_repository_directories(self) -> None:
        without = _policy()
        del without["python_source_roots"]
        with self.assertRaisesRegex(ArchitecturePolicyError, "python_source_roots"):
            validate_policy(without)
        for roots in (["/src"], ["C:/repo/src"], ["apps\\api"], ["../outside"], ["packages/*/src"], [".", "."]):
            with self.subTest(roots=roots), self.assertRaisesRegex(ArchitecturePolicyError, "python_source_roots"):
                _policy(python_source_roots=roots)

    def test_root_entries_are_required_top_level_names(self) -> None:
        without = _policy()
        del without["repository_root_entries"]
        with self.assertRaisesRegex(ArchitecturePolicyError, "repository_root_entries"):
            validate_policy(without)
        for names in (["apps/monkeyhub"], [".."], ["C:"], ["docs", "docs"]):
            with self.subTest(names=names), self.assertRaisesRegex(ArchitecturePolicyError, "repository_root_entries"):
                _policy(repository_root_entries=names)


class RepositoryRootTests(unittest.TestCase):
    """The root is read from Git's index and holds only the allowlisted entries."""

    def setUp(self) -> None:
        self.root = _temporary_root(self, "repo")
        _git(self.root, "init", "-q")
        for relative in ("README.md", ".gitignore", "docs/index.md", "tools/check.py"):
            _write(self.root, relative, "*.egg-info/\n.pytest_cache/\n*.log\n" if relative == ".gitignore" else "text\n")
        self.track()

    def track(self) -> None:
        _git(self.root, "add", "-A")

    def findings(self, policy: dict[str, object] | None = None) -> list[tuple[str, str, str]]:
        return [
            (item.path, item.code, item.message)
            for item in check_repository_root(self.root, policy or _policy())
        ]

    def test_the_layout_passes(self) -> None:
        self.assertEqual([], self.findings())

    def test_a_tracked_entry_outside_the_layout_is_a_finding(self) -> None:
        _write(self.root, "scratch/notes.md")
        _write(self.root, "setup.py")
        self.track()
        findings = self.findings()
        self.assertEqual([("scratch", "ROOT_ENTRY"), ("setup.py", "ROOT_ENTRY")], [item[:2] for item in findings])
        self.assertIn("docs/architecture/repository-layout.md", findings[0][2])

    def test_ignored_and_untracked_files_are_not_the_layout(self) -> None:
        for relative in ("archflow_v4.egg-info/PKG-INFO", ".pytest_cache/README.md", "error.log", "notes.txt"):
            _write(self.root, relative)
        self.track()  # "git add -A" stages notes.txt only
        _git(self.root, "rm", "-q", "--cached", "notes.txt")
        self.assertEqual([], self.findings())

    def test_a_package_moved_back_to_the_root_is_a_finding(self) -> None:
        # The packages left the root for packages/ in round 1; nothing lets one return.
        _write(self.root, "archflow/__init__.py")
        self.track()
        self.assertEqual([("archflow", "ROOT_ENTRY")], [item[:2] for item in self.findings()])


class DocsLayoutTests(unittest.TestCase):
    """docs/ keeps README.md at its root and categorised, kebab-case documents.

    Read from Git's index like the root entries, so ignored private notes under
    docs/ in a checkout decide nothing.
    """

    LAYOUT = (
        "docs/README.md",
        "docs/architecture/overview.md",
        "docs/decisions/README.md",
        "docs/decisions/001-one-spine.md",
        "docs/prototypes/candidate-graph/README.md",
        "docs/prototypes/candidate-graph/index.html",
        "docs/prototypes/candidate-graph/data/issue-fixture.js",
        "docs/prototypes/candidate-graph/screenshots/01-closed.png",
    )

    def setUp(self) -> None:
        self.root = _temporary_root(self, "repo")
        _git(self.root, "init", "-q")
        for relative in self.LAYOUT:
            _write(self.root, relative, "text\n")
        self.track()

    def track(self) -> None:
        _git(self.root, "add", "-A")

    def add(self, *relatives: str) -> list[tuple[str, str, str]]:
        for relative in relatives:
            _write(self.root, relative, "text\n")
        self.track()
        return [(item.path, item.code, item.message) for item in check_docs_layout(self.root)]

    def test_the_layout_passes(self) -> None:
        self.assertEqual([], self.add())

    def test_only_the_readme_stays_at_the_docs_root(self) -> None:
        findings = self.add("docs/ARCHITECTURE.md", "docs/index.md")
        self.assertEqual(
            [("docs/ARCHITECTURE.md", "DOCS_ROOT"), ("docs/index.md", "DOCS_ROOT")],
            [item[:2] for item in findings],
        )
        self.assertIn("only README.md stays at the docs root", findings[0][2])

    def test_a_document_name_is_lowercase_kebab_case_markdown(self) -> None:
        bad = (
            "docs/architecture/PROJECT_RUNTIME.md",
            "docs/research/research_plan.md",
            "docs/design/drawing-standard-v0.1.md",
            "docs/design/Overview.md",
            "docs/product/roadmap.markdown",
        )
        findings = self.add(*bad, "docs/design/drawing-standard.md")
        self.assertEqual(sorted((path, "DOC_NAME") for path in bad), [item[:2] for item in findings])
        self.assertTrue(all("not a lowercase kebab-case Markdown name" in item[2] for item in findings))

    def test_a_dated_name_is_refused_although_it_is_kebab_case(self) -> None:
        name = "2026-09-28-construction-api.md"
        # The kebab pattern alone accepts it; the date ban is a rule of its own.
        self.assertIsNotNone(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.md", name))
        findings = self.add(f"docs/design/{name}", f"docs/audits/{name}")
        self.assertEqual(
            [(f"docs/audits/{name}", "DOC_NAME"), (f"docs/design/{name}", "DOC_NAME")],
            [item[:2] for item in findings],
        )
        self.assertIn("begins with a date", findings[0][2])
        self.assertIn("created: YYYY-MM-DD", findings[0][2])

    def test_a_decision_keeps_its_three_digit_number(self) -> None:
        bad = (
            "docs/decisions/ADR-011-interface-information-hierarchy.md",
            "docs/decisions/11-interface.md",
            "docs/decisions/0011-interface.md",
            "docs/decisions/interface.md",
        )
        findings = self.add(*bad, "docs/decisions/011-interface-information-hierarchy.md")
        self.assertEqual(sorted((path, "DOC_NAME") for path in bad), [item[:2] for item in findings])
        self.assertTrue(all("not a decision name" in item[2] for item in findings))

    def test_a_directory_name_is_kebab_case_and_reported_once(self) -> None:
        findings = self.add("docs/Research/notes.md", "docs/design_notes/a.md", "docs/design_notes/b.md")
        self.assertEqual(
            [("docs/Research", "DOC_NAME"), ("docs/design_notes", "DOC_NAME")],
            [item[:2] for item in findings],
        )

    def test_only_prototypes_keep_files_that_are_not_markdown(self) -> None:
        findings = self.add(
            "docs/prototypes/candidate-graph/prototype.css",
            "docs/prototypes/candidate-graph/NOTES.md",
            "docs/design/diagram.png",
        )
        self.assertEqual(
            [("docs/design/diagram.png", "DOC_NAME"), ("docs/prototypes/candidate-graph/NOTES.md", "DOC_NAME")],
            [item[:2] for item in findings],
        )
        self.assertIn("only docs/prototypes/ keeps other files", findings[0][2])

    def test_ignored_and_untracked_files_are_not_the_layout(self) -> None:
        _write(self.root, ".gitignore", "docs/TEAM_MEETING_AGENDA.md\ndocs/claude-worktree/\n")
        for relative in ("docs/TEAM_MEETING_AGENDA.md", "docs/claude-worktree/Notes.md", "docs/RESEARCH_POSITIONING.md"):
            _write(self.root, relative)
        self.track()  # "git add -A" stages the untracked positioning note; unstage it
        _git(self.root, "rm", "-q", "--cached", "docs/RESEARCH_POSITIONING.md")
        self.assertEqual([], [item for item in check_docs_layout(self.root)])


class RegistryPathTests(unittest.TestCase):
    """Every path the module registry names exists; a module id names a module."""

    def setUp(self) -> None:
        self.root = _temporary_root(self)
        for relative in ("tools/check.py", "tools/helpers/value.py", "tests/test_check.py", "docs/index.md"):
            _write(self.root, relative)

    def findings(self, registry: dict[str, object]) -> list[tuple[str, str]]:
        _write(self.root, REGISTRY_PATH, registry)
        return [(item.code, item.message) for item in check_registry(self.root, _policy())]

    def test_paths_directories_and_module_ids_that_exist_pass(self) -> None:
        self.assertEqual([], self.findings({
            "modules": [{
                "module_id": "tools.check", "owner_path": "tools/check.py", "tests": ["tests/test_check.py"],
                "files": ["tools/check.py", "tools/helpers/"], "used_by": ["tools.check", "docs/index.md"],
                "depends_on": [],
            }],
            "spine": {"spec": "docs/index.md", "entry_points": ["tools/check.py"]},
            "interfaces": [{"interface_id": "check", "implementations": [{"implementation_file": "tools/check.py"}]}],
            "capabilities": [{"capability_id": "project.check", "tests": ["tests/test_check.py"]}],
        }))

    def test_every_path_field_that_names_a_missing_path_is_a_finding(self) -> None:
        findings = self.findings({
            "modules": [{
                "module_id": "tools.check", "owner_path": "tools/check.py", "tests": ["tests/test_check.py"],
                "files": ["tools/check.py", "tools/moved.py"],
                "used_by": ["docs/moved.md", "tools/*.py", "tools.retired"],
                "depends_on": [],
            }],
            "spine": {"spec": "docs/SPINE.md", "entry_points": ["tools/run_moved.py"]},
            "interfaces": [{"interface_id": "check", "implementations": [{"implementation_file": "tools/backend.py"}]}],
            "capabilities": [{"capability_id": "project.check", "tests": ["tests/test_moved.py"]}],
        })
        self.assertEqual(["REGISTRY_PATH_MISSING"] * 8, [code for code, _ in findings])
        messages = "\n".join(message for _, message in findings)
        for text in (
            "tools.check files: tools/moved.py does not exist",
            "tools.check used_by: docs/moved.md does not exist",
            "tools.check used_by: 'tools/*.py' is not a literal repository-relative path",
            "tools.check used_by: 'tools.retired' is neither a repository path nor a module id",
            "spine.spec: docs/SPINE.md does not exist",
            "spine.entry_points: tools/run_moved.py does not exist",
            "interface check implementation_file: tools/backend.py does not exist",
            "capability project.check tests: tests/test_moved.py does not exist",
        ):
            self.assertIn(text, messages)

    def test_a_named_file_must_be_a_file(self) -> None:
        findings = self.findings({
            "modules": [{"module_id": "tools.check", "owner_path": "tools/check.py", "tests": ["tests/test_check.py"]}],
            "spine": {"spec": "docs/", "entry_points": []},
        })
        self.assertEqual([("REGISTRY_PATH_MISSING", "spine.spec: docs/ is not a file")], findings)


class ImportNameTests(unittest.TestCase):
    """A registered owner is matched by the name it is imported with."""

    def test_an_import_name_begins_at_the_longest_python_source_root(self) -> None:
        roots = [".", "services/project-runtime/src", "packages/archflow/src"]
        for path, name in (
            ("archflow/state/state_record.py", "archflow.state.state_record"),
            ("packages/archflow/src/archflow/state/state_record.py", "archflow.state.state_record"),
            ("services/project-runtime/src/project_runtime/main.py", "project_runtime.main"),
            ("monkeyarch/authoring/construction/__init__.py", "monkeyarch.authoring.construction"),
            ("tools/governance/archcheck.py", "tools.governance.archcheck"),
            ("apps/monkeyhub/web/scripts/dump-openapi.py", None),
            ("apps/monkeyhub/desktop/", None),
        ):
            with self.subTest(path=path):
                self.assertEqual(name, _module_name(path, roots))

    def registry_findings(self, python_source_roots: list[str], owner: str, owner_id: str, imported: str) -> list[str]:
        root = _temporary_root(self)
        _write(root, owner)
        _write(root, "tools/consumer.py", f"import {imported}\n")
        _write(root, REGISTRY_PATH, {"modules": [
            {"module_id": owner_id, "owner_path": owner, "depends_on": [], "untested_reason": "synthetic owner"},
            {"module_id": "tools.consumer", "owner_path": "tools/consumer.py", "depends_on": [owner_id],
             "untested_reason": "synthetic consumer"},
        ]})
        return [item.code for item in check_registry(root, _policy(python_source_roots=python_source_roots))]

    def test_an_owner_under_a_src_layout_is_matched_through_its_source_root(self) -> None:
        owner = "packages/archflow/src/archflow/state/record.py"
        self.assertEqual([], self.registry_findings([".", "packages/archflow/src"], owner, "state.ledger", "archflow.state.record"))
        self.assertEqual(
            ["REGISTRY_DEPENDS_ON_DRIFT"],
            self.registry_findings(["."], owner, "state.ledger", "archflow.state.record"),
        )

    def test_a_package_owner_is_the_package(self) -> None:
        self.assertEqual([], self.registry_findings(
            ["."], "monkeyarch/authoring/construction/__init__.py", "construction.script", "monkeyarch.authoring.construction",
        ))


class SourceWalkTests(unittest.TestCase):
    def test_vendored_trees_and_environments_are_not_source(self) -> None:
        root = _temporary_root(self)
        _write(root, "tools/check.py")
        for relative in (
            "tools/installer/third-party/lib/vendored.py",
            "tools/web/node_modules/katex/build.py",
            "tools/.venv/Lib/site-packages/package/module.py",
            "tools/venv/Lib/os.py",
            "tools/embedded/Lib/site-packages/package/module.py",
            "tools/archflow_v4.egg-info/hooks.py",
            "tools/__pycache__/check.py",
        ):
            _write(root, relative, "open('output.txt', 'w')\n")
        self.assertEqual(
            ["tools/check.py"],
            [path.relative_to(root).as_posix() for path in _checked_python_files(root, _policy())],
        )

    def test_a_checkout_inside_a_tests_directory_still_finds_copied_owners(self) -> None:
        root = _temporary_root(self, "tests", "checkout")
        _write(root, "tools/owner.py", OWNED_FUNCTION.format(name="measure"))
        _write(root, "tools/copy.py", OWNED_FUNCTION.format(name="copied_measure"))
        _write(root, "tests/test_owner.py", OWNED_FUNCTION.format(name="expected_measure"))
        _write(root, REGISTRY_PATH, {"modules": [{
            "module_id": "tools.owner", "owner_path": "tools/owner.py", "depends_on": [],
            "untested_reason": "synthetic owner",
        }]})
        findings = check_registry(root, _policy(checked_source_roots=["archflow", "tools", "tests"]))
        self.assertEqual([("tools/copy.py", "DUPLICATE_OWNED_FUNCTION")], [(item.path, item.code) for item in findings])


if __name__ == "__main__":
    unittest.main()
