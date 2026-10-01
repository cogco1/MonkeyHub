"""A new file lands where the layout places it, whoever wrote it (#553).

archcheck holds four places to lists the policy keeps next to
repository_root_entries: the entries directly under apps/monkeyhub/web/src and
docs/ (directory_entries), test files under the test roots
(test_file_patterns, test_roots), and the first-level directories of each
Python package (subpackages). The rules read Git's index, so most cases build
a small Git repository in a temporary directory and call check_placement with
only the placement keys; the last cases read this repository's own tree and
policy. _write and _git are copied from test_archcheck_paths.py.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))

from tools.governance.archcheck import (  # noqa: E402
    ARCHITECTURE_POLICY, ArchitecturePolicyError, check_docs_layout, check_placement, load_policy, run_checks,
)


WEB = "apps/monkeyhub/web/src"

ENTRIES = {
    "directory_entries": {
        WEB: {
            "files": ["main.tsx", "styles.css"],
            "directories": ["app", "features", "api"],
            "reason": "app/ is the shell, features/ one directory per feature, api/ the clients.",
        },
        "docs": {
            "directories": ["architecture", "decisions"],
            "reason": "docs/README.md says what each category holds.",
        },
    },
}

TESTS = {
    "test_file_patterns": ["test_*.py", "*_test.py", "*.test.ts", "*.test.tsx", "*.browser.mjs"],
    "test_roots": [
        "tests/integration", "tests/packaging", "tools/tests", "packages/alpha/tests", "apps/monkeyhub/web/test", "labs",
    ],
}

PACKAGES = {
    "subpackages": {
        "packages/alpha/src/alpha": ["domain", "data"],
        "packages/beta/src/beta": [],
        "services/runtime/src/runtime_pkg": ["api"],
    },
}


def _write(root: Path, relative: str, text: str = "VALUE = 1\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-c", "user.name=archcheck test", "-c", "user.email=archcheck@example.invalid", *args),
        cwd=root, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return completed.stdout


def _track(root: Path, *relatives: str) -> None:
    for relative in relatives:
        _write(root, relative)
    if relatives:
        _git(root, "add", "--", *relatives)


def _repository(test: unittest.TestCase, *tracked: str) -> Path:
    """A Git repository in a temporary directory whose index holds these files."""

    temporary = tempfile.TemporaryDirectory()
    test.addCleanup(temporary.cleanup)
    root = Path(temporary.name) / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _track(root, *tracked)
    return root


def _findings(root: Path, policy: dict[str, object]) -> list[tuple[str, str, str]]:
    return [(item.path, item.code, item.message) for item in check_placement(root, policy)]


class DirectoryEntryTests(unittest.TestCase):
    """apps/monkeyhub/web/src and docs/ hold only the entries the policy lists."""

    LAYOUT = (
        f"{WEB}/main.tsx", f"{WEB}/styles.css", f"{WEB}/app/App.tsx", f"{WEB}/features/chat/Chat.tsx",
        f"{WEB}/api/client.ts", "docs/README.md", "docs/architecture/overview.md", "docs/decisions/001-one-spine.md",
    )

    def setUp(self) -> None:
        self.root = _repository(self, *self.LAYOUT)

    def add(self, *relatives: str) -> list[tuple[str, str, str]]:
        _track(self.root, *relatives)
        return _findings(self.root, ENTRIES)

    def test_the_layout_passes(self) -> None:
        self.assertEqual([], self.add())

    def test_a_misplaced_file_at_the_web_source_root_names_where_it_belongs(self) -> None:
        findings = self.add(f"{WEB}/Widget.tsx")
        self.assertEqual([(f"{WEB}/Widget.tsx", "PLACEMENT_ENTRY")], [item[:2] for item in findings])
        self.assertIn(f"Widget.tsx is a new file in {WEB}/; put it under app/, features/ or api/, "
                      "or list it in directory_entries in a reviewed policy edit", findings[0][2])
        self.assertIn("app/ is the shell", findings[0][2])

    def test_a_new_directory_at_the_web_source_root_is_reported_once(self) -> None:
        findings = self.add(f"{WEB}/widgets/Dial.tsx", f"{WEB}/widgets/Knob.tsx")
        self.assertEqual([(f"{WEB}/widgets", "PLACEMENT_ENTRY")], [item[:2] for item in findings])
        self.assertIn(f"widgets/ is a new directory in {WEB}/; put its files under app/, features/ or api/",
                      findings[0][2])

    def test_files_below_a_listed_directory_are_free(self) -> None:
        self.assertEqual([], self.add(f"{WEB}/features/board/Board.tsx", f"{WEB}/app/shell/Menu.tsx"))

    def test_a_stray_docs_directory_names_the_categories(self) -> None:
        findings = self.add("docs/notes/meeting.md")
        self.assertEqual([("docs/notes", "PLACEMENT_ENTRY")], [item[:2] for item in findings])
        self.assertIn("notes/ is a new directory in docs/; put its files under architecture/ or decisions/",
                      findings[0][2])
        self.assertIn("docs/README.md says what each category holds", findings[0][2])

    def test_a_stray_docs_root_file_is_one_finding_from_docs_root(self) -> None:
        # docs/ lists no files: README.md alone at the docs root is DOCS_ROOT's rule, not repeated here.
        self.assertEqual([], self.add("docs/notes.md"))
        self.assertEqual([("docs/notes.md", "DOCS_ROOT")], [(item.path, item.code) for item in check_docs_layout(self.root)])

    def test_an_entry_listed_but_gone_is_a_policy_finding(self) -> None:
        _git(self.root, "rm", "-q", "--cached", f"{WEB}/styles.css", f"{WEB}/api/client.ts")
        findings = _findings(self.root, ENTRIES)
        self.assertEqual([(ARCHITECTURE_POLICY, "POLICY_PATH_MISSING")] * 2, [item[:2] for item in findings])
        messages = "\n".join(item[2] for item in findings)
        self.assertIn(f"directory_entries['{WEB}'] lists the directory 'api', which Git does not track there", messages)
        self.assertIn(f"directory_entries['{WEB}'] lists the file 'styles.css', which Git does not track there", messages)

    def test_a_directory_that_holds_nothing_tracked_guards_nothing(self) -> None:
        policy = {"directory_entries": {**ENTRIES["directory_entries"], "apps/retired": {"directories": ["x"], "reason": "gone"}}}
        self.assertEqual(
            [(ARCHITECTURE_POLICY, "POLICY_PATH_MISSING",
              "directory_entries entry 'apps/retired' holds nothing Git tracks; the rule guards nothing")],
            _findings(self.root, policy),
        )

    def test_untracked_files_decide_nothing(self) -> None:
        _write(self.root, f"{WEB}/Scratch.tsx")
        _write(self.root, "docs/private/notes.md")
        self.assertEqual([], _findings(self.root, ENTRIES))


class TestRootTests(unittest.TestCase):
    """A file named like a test lies under a test root."""

    LAYOUT = (
        "tests/integration/test_flow.py", "tests/packaging/test_bundle.py", "tools/tests/test_tool.py",
        "packages/alpha/tests/test_alpha.py", "packages/alpha/src/alpha/core.py",
        "apps/monkeyhub/web/test/view.test.ts", "apps/monkeyhub/web/test/canvas.browser.mjs",
        "labs/probe/test_probe.py", "labs/probe/probe.py",
    )

    def setUp(self) -> None:
        self.root = _repository(self, *self.LAYOUT)

    def add(self, *relatives: str) -> list[tuple[str, str, str]]:
        _track(self.root, *relatives)
        return _findings(self.root, TESTS)

    def test_the_layout_passes(self) -> None:
        self.assertEqual([], self.add())

    def test_a_test_beside_the_code_it_tests_names_its_owners_test_root(self) -> None:
        findings = self.add("packages/alpha/src/alpha/test_core.py")
        self.assertEqual([("packages/alpha/src/alpha/test_core.py", "PLACEMENT_TEST")], [item[:2] for item in findings])
        self.assertIn("test_core.py is a test file outside the test roots; put it in packages/alpha/tests/, "
                      "the test root beside it, or list a new root in test_roots in a reviewed policy edit",
                      findings[0][2])

    def test_every_test_name_pattern_is_held_to_the_roots(self) -> None:
        cases = {
            f"{WEB}/view.test.tsx": "put it in apps/monkeyhub/web/test/, the test root beside it",
            f"{WEB}/panel.test.ts": "put it in apps/monkeyhub/web/test/, the test root beside it",
            f"{WEB}/canvas.browser.mjs": "put it in apps/monkeyhub/web/test/, the test root beside it",
            "tools/governance/archcheck_test.py": "put it in tools/tests/, the test root beside it",
            "tests/test_misc.py": "put it in tests/integration/ or tests/packaging/, the test roots beside it",
        }
        findings = self.add(*cases)
        self.assertEqual(sorted((path, "PLACEMENT_TEST") for path in cases), [item[:2] for item in findings])
        for path, _, message in findings:
            with self.subTest(path=path):
                self.assertIn(cases[path], message)

    def test_a_test_with_no_root_beside_it_gets_the_general_advice(self) -> None:
        findings = self.add("scripts/dev/test_launch.py")
        self.assertEqual([("scripts/dev/test_launch.py", "PLACEMENT_TEST")], [item[:2] for item in findings])
        self.assertIn("put it in the test root of the owner it tests (test_roots lists them; "
                      "docs/architecture/repository-layout.md 6.4)", findings[0][2])

    def test_names_that_only_resemble_tests_are_not_tests(self) -> None:
        self.assertEqual([], self.add(
            "tools/testing.py", "tools/contest.py", "tools/conftest.py", "packages/alpha/src/alpha/test.py",
            "packages/alpha/src/alpha/attest_input.py", f"{WEB}/test.ts", f"{WEB}/latest.ts", f"{WEB}/browser.mjs",
            "packages/alpha/tests/spine_fixture.py",
        ))

    def test_a_vendored_tree_holds_no_test_of_this_repository(self) -> None:
        self.assertEqual([], self.add(
            "apps/monkeyhub/installer/third-party/lib/test_lib.py", "tools/node_modules/pkg/index.test.ts",
        ))

    def test_a_test_root_without_tests_is_a_policy_finding(self) -> None:
        _track(self.root, "apps/monkeyhub/api/tests/support.py")
        policy = {**TESTS, "test_roots": [*TESTS["test_roots"], "apps/monkeyhub/api/tests"]}
        self.assertEqual(
            [(ARCHITECTURE_POLICY, "POLICY_PATH_MISSING",
              "test_roots entry 'apps/monkeyhub/api/tests' holds no test file Git tracks; remove it, "
              "or it lets tests in there unreviewed")],
            _findings(self.root, policy),
        )


class SubpackageTests(unittest.TestCase):
    """The first-level directories of a Python package are the ones the policy lists for it."""

    LAYOUT = (
        "packages/alpha/src/alpha/__init__.py", "packages/alpha/src/alpha/domain/wall.py",
        "packages/alpha/src/alpha/data/rates.json", "packages/alpha/tests/test_wall.py",
        "packages/beta/pyproject.toml", "packages/beta/src/beta/cli.py",
        "services/runtime/src/runtime_pkg/api/routes.py", "services/runtime/tests/test_routes.py",
        "packages/web-shared/src/theme/dark/colors.css",
    )

    def setUp(self) -> None:
        self.root = _repository(self, *self.LAYOUT)

    def add(self, *relatives: str) -> list[tuple[str, str, str]]:
        _track(self.root, *relatives)
        return _findings(self.root, PACKAGES)

    def test_the_layout_passes(self) -> None:
        self.assertEqual([], self.add())

    def test_a_new_subpackage_fails_until_the_policy_lists_it(self) -> None:
        findings = self.add("packages/alpha/src/alpha/helpers/__init__.py", "packages/alpha/src/alpha/helpers/text.py")
        self.assertEqual([("packages/alpha/src/alpha/helpers", "PLACEMENT_SUBPACKAGE")], [item[:2] for item in findings])
        self.assertIn("helpers/ is a new first-level directory of packages/alpha/src/alpha; put its modules in one "
                      "of its subpackages, domain/ or data/, or list it under subpackages in a reviewed policy edit",
                      findings[0][2])
        listed = {"subpackages": {**PACKAGES["subpackages"], "packages/alpha/src/alpha": ["domain", "data", "helpers"]}}
        self.assertEqual([], _findings(self.root, listed))

    def test_a_package_without_subpackages_keeps_its_modules_at_its_root(self) -> None:
        findings = self.add("packages/beta/src/beta/commands/run.py")
        self.assertEqual([("packages/beta/src/beta/commands", "PLACEMENT_SUBPACKAGE")], [item[:2] for item in findings])
        self.assertIn("packages/beta/src/beta has no subpackages; put its modules at its root", findings[0][2])

    def test_a_service_package_is_held_to_its_list(self) -> None:
        findings = self.add("services/runtime/src/runtime_pkg/jobs/queue.py")
        self.assertEqual([("services/runtime/src/runtime_pkg/jobs", "PLACEMENT_SUBPACKAGE")], [item[:2] for item in findings])

    def test_a_new_package_lists_its_subpackages(self) -> None:
        findings = self.add(
            "packages/gamma/src/gamma/__init__.py", "packages/gamma/src/gamma/cli.py", "packages/gamma/src/gamma/core/model.py",
        )
        self.assertEqual([("packages/gamma/src/gamma/core", "PLACEMENT_SUBPACKAGE")], [item[:2] for item in findings])
        self.assertIn("packages/gamma/src/gamma is a package subpackages does not list", findings[0][2])

    def test_deeper_directories_and_modules_at_a_package_root_are_free(self) -> None:
        self.assertEqual([], self.add("packages/alpha/src/alpha/domain/walls/stud.py", "packages/alpha/src/alpha/extra.py"))

    def test_a_tree_without_python_is_no_package(self) -> None:
        self.assertEqual([], self.add("packages/web-shared/src/theme/light/colors.css"))

    def test_a_package_or_subpackage_listed_but_gone_is_a_policy_finding(self) -> None:
        policy = {"subpackages": {
            **PACKAGES["subpackages"], "packages/alpha/src/alpha": ["domain", "data", "retired"], "packages/delta/src/delta": [],
        }}
        findings = _findings(self.root, policy)
        self.assertEqual([(ARCHITECTURE_POLICY, "POLICY_PATH_MISSING")] * 2, [item[:2] for item in findings])
        messages = "\n".join(item[2] for item in findings)
        self.assertIn("subpackages['packages/alpha/src/alpha'] lists 'retired', which holds nothing Git tracks", messages)
        self.assertIn("subpackages entry 'packages/delta/src/delta' holds nothing Git tracks", messages)


class PolicyShapeTests(unittest.TestCase):
    def test_absent_keys_leave_their_rules_off_without_asking_git(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Not a Git repository: reading the index here would fail.
        self.assertEqual([], list(check_placement(Path(temporary.name), {})))

    def test_a_malformed_key_fails_before_anything_is_reported(self) -> None:
        root = _repository(self, "docs/README.md", "docs/architecture/overview.md")
        docs = {"directories": ["architecture"], "reason": "categories"}
        for policy, message in (
            ({"directory_entries": ["docs"]}, "directory_entries and subpackages must map"),
            ({"directory_entries": {"docs": {"directories": ["architecture"]}}}, r"directory_entries\['docs'\] must give"),
            ({"directory_entries": {"../docs": docs}}, r"directory_entries\['\.\./docs'\] must give"),
            ({"directory_entries": {"docs": {**docs, "directories": ["design/notes"]}}}, "a single path segment"),
            ({"directory_entries": {"docs": {**docs, "directories": []}}}, "at least one"),
            ({"directory_entries": {"docs": {**docs, "files": ["README.md", "README.md"]}}}, "distinct names"),
            ({"test_roots": ["tests"]}, "give both or neither"),
            ({"test_file_patterns": ["tests/test_*.py"], "test_roots": ["tests"]}, "file-name patterns"),
            ({"test_file_patterns": ["test_*.py"], "test_roots": ["tests", "tests"]}, "distinct repository directories"),
            ({"subpackages": {"packages/*/src": []}}, "must be a repository directory"),
            ({"subpackages": {"packages/a/src/a": ["core", "core"]}}, "distinct names"),
        ):
            with self.subTest(policy=policy), self.assertRaisesRegex(ArchitecturePolicyError, message):
                list(check_placement(root, policy))


class RunChecksTests(unittest.TestCase):
    """The tree check runs the placement rules beside DOCS_ROOT."""

    def test_archcheck_reports_misplaced_files_and_a_stray_docs_file_once(self) -> None:
        root = _repository(
            self, ".gitignore", "README.md", "tools/check.py", "tools/tests/test_check.py",
            "docs/README.md", "docs/architecture/overview.md",
        )
        # The repository's own policy, so every key the checker requires is present, held to this
        # small tree: its other paths are absent here, so only the findings asked about are read.
        policy = {
            **load_policy(REPOSITORY / ARCHITECTURE_POLICY),
            "source_root": "tools",
            "checked_source_roots": ["tools"],
            "import_only_source_roots": [],
            "python_source_roots": ["."],
            "repository_root_entries": ["docs", "tools", ".gitignore", "README.md"],
            "forbidden_layer_imports": [],
            "allowed_write_sites": [],
            "allowed_authority_symbols": [],
            "directory_entries": {"docs": {"directories": ["architecture"], "reason": "categories"}},
            "test_roots": ["tools/tests"],
            "subpackages": {},
        }
        _track(root, "docs/notes.md", "docs/notes/meeting.md", "tools/test_misplaced.py")
        asked = {"DOCS_ROOT", "PLACEMENT_ENTRY", "PLACEMENT_TEST", "PLACEMENT_SUBPACKAGE"}
        self.assertEqual(
            [("docs/notes", "PLACEMENT_ENTRY"), ("docs/notes.md", "DOCS_ROOT"), ("tools/test_misplaced.py", "PLACEMENT_TEST")],
            [(item.path, item.code) for item in run_checks(root, policy) if item.code in asked],
        )


class RepositoryTests(unittest.TestCase):
    """This repository's tree follows its own placement rules."""

    def setUp(self) -> None:
        self.policy = load_policy(REPOSITORY / ARCHITECTURE_POLICY)

    def test_the_policy_configures_every_placement_rule(self) -> None:
        # An absent key turns its rule off, so removing one must be a visible failure.
        for key in ("directory_entries", "test_file_patterns", "test_roots", "subpackages"):
            with self.subTest(key=key):
                self.assertIn(key, self.policy)
        self.assertEqual({WEB, "docs"}, set(self.policy["directory_entries"]))

    def test_the_tree_passes(self) -> None:
        self.assertEqual([], _findings(REPOSITORY, self.policy))


if __name__ == "__main__":
    unittest.main()
