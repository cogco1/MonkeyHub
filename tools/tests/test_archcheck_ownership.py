"""Every checked source file has exactly one owner in the module registry (#559).

A Python file that no module's files hold is a place where new code lands
unreviewed. archcheck therefore holds every Python file Git tracks under the
policy's checked_source_roots to exactly one registry entry, the way #537
resolves imports: an entry's files list the file, or a directory above it, or
the entry has no files and the file is its owner_path. The test suites and the
import-only roots (labs) are outside the rule, and so is a package's
__init__.py while no entry holds it. A file no entry holds is
REGISTRY_FILE_UNOWNED, whose message names the nearest plausible owner; a file
two module ids hold is REGISTRY_FILE_OWNED_TWICE.

The rule reads Git's index, so the cases build a small Git repository in a
temporary directory; the last case reads this repository's own tree and
policy. _write and _git are copied from test_archcheck_paths.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))

from tools.governance.archcheck import (  # noqa: E402
    ARCHITECTURE_POLICY, check_file_owners, load_policy, run_checks, validate_policy,
)


REGISTRY_PATH = "governance/module_registry.json"
ALPHA = "packages/alpha/src/alpha"

# The fixture tree. alpha.intent holds two files among the tools' benchmarks the
# way project_runtime.intent holds two in tools/benchmarks, and tools.bench.kit
# holds a directory.
TRACKED = (
    f"{ALPHA}/__init__.py",
    f"{ALPHA}/application/__init__.py",
    f"{ALPHA}/application/boards.py",
    f"{ALPHA}/application/intent.py",
    f"{ALPHA}/api/routes/notes.py",
    f"{ALPHA}/api/routes/views.py",
    f"{ALPHA}/api/routes/panels.py",
    "packages/alpha/tests/test_boards.py",
    "tools/bench/__init__.py",
    "tools/bench/daily.py",
    "tools/bench/data.py",
    "tools/bench/intent_context.py",
    "tools/bench/visual.py",
    "tools/bench/kit/support.py",
    "tools/bench/kit/deep/more.py",
    "tools/tests/test_daily.py",
    "labs/probe/probe.py",
    "tests/integration/test_flow.py",
)

OWNERS = (
    ("alpha.board", [f"{ALPHA}/application/boards.py"]),
    ("alpha.intent", [f"{ALPHA}/application/intent.py", "tools/bench/intent_context.py", "tools/bench/visual.py"]),
    ("alpha.notes", [f"{ALPHA}/api/routes/notes.py"]),
    ("alpha.views", [f"{ALPHA}/api/routes/views.py", f"{ALPHA}/api/routes/panels.py"]),
    ("tools.bench.daily", ["tools/bench/daily.py", "tools/bench/data.py"]),
    ("tools.bench.kit", ["tools/bench/kit/"]),
)


def _policy(**changes: object) -> dict[str, object]:
    """A complete policy for the fixture tree."""

    policy: dict[str, object] = {
        "schema": "ArchFlowArchitecturePolicy@1",
        "source_root": "packages/alpha",
        "checked_source_roots": ["packages/alpha", "tools", "labs", "tests"],
        "import_only_source_roots": ["labs"],
        "python_source_roots": [".", "packages/alpha/src"],
        "repository_root_entries": ["governance", "labs", "packages", "tests", "tools"],
        "shared_write_scope": [],
        "unclaimed_write_scope": [],
        "probe_root": "probes",
        "forbidden_instance_literals": [],
        "forbidden_framework_identifiers": [],
        "probe_executable_suffixes": [".py"],
        "allowed_write_sites": [],
        "forbidden_layer_imports": [],
        "authority_symbol_patterns": [],
        "allowed_authority_symbols": [],
        "module_id_namespaces": {ALPHA: "alpha", "tools": "tools"},
        "declared_dependency_namespaces": [],
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


def _entry(module_id: str, files: list[str]) -> dict[str, object]:
    entry: dict[str, object] = {"module_id": module_id, "owner_path": files[0], "untested_reason": "synthetic owner"}
    if len(files) > 1 or files[0].endswith("/"):
        entry["files"] = files
    return entry


class OwnershipCase(unittest.TestCase):
    """The fixture tree as a Git repository, every file tracked and held."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q")
        self.track(*TRACKED)
        self.register(*OWNERS)

    def track(self, *relatives: str) -> None:
        for relative in relatives:
            if not (self.root / relative).exists():
                _write(self.root, relative)
        if relatives:
            _git(self.root, "add", "--", *relatives)

    def register(self, *owners: tuple[str, list[str]]) -> None:
        _write(self.root, REGISTRY_PATH, {"modules": [_entry(module_id, files) for module_id, files in owners]})

    def findings(self, policy: dict[str, object] | None = None) -> list[tuple[str, str, str]]:
        return [(item.path, item.code, item.message) for item in check_file_owners(self.root, policy or _policy())]


class UnownedTests(OwnershipCase):
    """A file no entry holds is reported, with the module most likely to own it."""

    def test_the_tree_passes(self) -> None:
        self.assertEqual([], self.findings())

    def test_a_file_no_entry_holds_is_reported_with_its_nearest_owner(self) -> None:
        self.track("tools/bench/driver.py")
        self.assertEqual(
            [("tools/bench/driver.py", "REGISTRY_FILE_UNOWNED",
              "no module in governance/module_registry.json holds tools/bench/driver.py; list it in the files of "
              "the module that owns it, most likely tools.bench.daily, which holds tools/bench/daily.py")],
            self.findings(),
        )

    def test_a_namesake_in_another_layer_comes_before_the_neighbours(self) -> None:
        # One feature keeps its name across layers: the route of the boards is the boards' owner's,
        # not that of whoever holds most routes.
        self.track(f"{ALPHA}/api/routes/boards.py", f"{ALPHA}/api/dto/boards.py")
        found = self.findings()
        self.assertEqual(
            [(f"{ALPHA}/api/dto/boards.py", "REGISTRY_FILE_UNOWNED"), (f"{ALPHA}/api/routes/boards.py", "REGISTRY_FILE_UNOWNED")],
            [item[:2] for item in found],
        )
        for _, _, message in found:
            self.assertTrue(message.endswith(f"most likely alpha.board, which holds {ALPHA}/application/boards.py"), message)

    def test_without_a_namesake_the_nearest_directory_decides(self) -> None:
        self.track("tools/bench/kit/extra.py", f"{ALPHA}/api/routes/other.py")
        self.register(*[(module_id, files) for module_id, files in OWNERS if module_id != "tools.bench.kit"],
                      ("tools.bench.kit", ["tools/bench/kit/support.py"]))
        nearest = {path: message.rsplit("most likely ", 1)[1] for path, _, message in self.findings()}
        self.assertEqual(
            {
                # tools/bench/kit/ is nearer than tools/bench/; deep/more.py is held by nobody now.
                "tools/bench/kit/extra.py": "tools.bench.kit, which holds tools/bench/kit/support.py",
                "tools/bench/kit/deep/more.py": "tools.bench.kit, which holds tools/bench/kit/support.py",
                # Two modules hold files beside it; the one holding more of them comes first.
                f"{ALPHA}/api/routes/other.py": f"alpha.views, which holds {ALPHA}/api/routes/panels.py",
            },
            nearest,
        )

    def test_a_module_of_the_files_own_namespace_comes_first(self) -> None:
        # alpha.intent holds as many files in tools/bench as tools.bench.daily and sorts first, but a
        # file under tools/ is a tools module's.
        self.track("tools/bench/driver.py")
        self.assertIn("most likely tools.bench.daily", self.findings()[0][2])
        self.register(*[(module_id, files) for module_id, files in OWNERS if module_id != "tools.bench.daily"])
        found = {path: message for path, _, message in self.findings()}
        self.assertIn("most likely alpha.intent, which holds tools/bench/intent_context.py", found["tools/bench/driver.py"])

    def test_a_root_where_nothing_is_held_names_no_owner(self) -> None:
        self.track("packages/beta/src/beta/value.py")
        policy = _policy(
            checked_source_roots=["packages/alpha", "packages/beta", "tools", "labs", "tests"],
            python_source_roots=[".", "packages/alpha/src", "packages/beta/src"],
        )
        self.assertEqual(
            [("packages/beta/src/beta/value.py", "REGISTRY_FILE_UNOWNED",
              "no module in governance/module_registry.json holds packages/beta/src/beta/value.py, nor anything "
              "under packages/beta/; register the module that owns it there")],
            self.findings(policy),
        )


class DirectoryTests(OwnershipCase):
    """A directory an entry lists holds every Python file below it, at any depth."""

    def test_a_listed_directory_holds_the_files_below_it(self) -> None:
        self.track("tools/bench/kit/deep/deeper/most.py")
        self.assertEqual([], self.findings())
        self.register(*[(module_id, files) for module_id, files in OWNERS if module_id != "tools.bench.kit"])
        self.assertEqual(
            ["tools/bench/kit/deep/deeper/most.py", "tools/bench/kit/deep/more.py", "tools/bench/kit/support.py"],
            [path for path, code, _ in self.findings() if code == "REGISTRY_FILE_UNOWNED"],
        )

    def test_a_file_listed_by_one_module_and_held_through_anothers_directory_is_held_twice(self) -> None:
        self.track("tools/bench/extra.py")
        self.register(*OWNERS, ("tools.bench.extra", ["tools/bench/extra.py", "tools/bench/kit/support.py"]))
        self.assertEqual(
            [("tools/bench/kit/support.py", "REGISTRY_FILE_OWNED_TWICE",
              "tools/bench/kit/support.py is held by tools.bench.kit (through tools/bench/kit/) and tools.bench.extra "
              "(lists it); a file has one owner, so keep it in the files of one module")],
            self.findings(),
        )

    def test_one_module_holding_a_file_directly_and_through_its_directory_is_one_owner(self) -> None:
        self.register(*[(module_id, files) for module_id, files in OWNERS if module_id != "tools.bench.kit"],
                      ("tools.bench.kit", ["tools/bench/kit/", "tools/bench/kit/support.py"]))
        self.assertEqual([], self.findings())


class OwnedTwiceTests(OwnershipCase):
    """A file has one owner: no two module ids hold it, whatever the file."""

    def test_two_modules_listing_one_file(self) -> None:
        self.register(*OWNERS, ("alpha.extra", [f"{ALPHA}/application/intent.py", "tools/bench/daily.py"]))
        self.assertEqual(
            [
                (f"{ALPHA}/application/intent.py", "REGISTRY_FILE_OWNED_TWICE",
                 f"{ALPHA}/application/intent.py is held by alpha.intent (lists it) and alpha.extra (lists it); a file "
                 "has one owner, so keep it in the files of one module"),
                ("tools/bench/daily.py", "REGISTRY_FILE_OWNED_TWICE",
                 "tools/bench/daily.py is held by tools.bench.daily (lists it) and alpha.extra (lists it); a file has "
                 "one owner, so keep it in the files of one module"),
            ],
            self.findings(),
        )

    def test_an_owner_path_alone_holds_its_file(self) -> None:
        # alpha.board and alpha.second list no files; each holds its owner_path.
        self.register(*OWNERS, ("alpha.second", [f"{ALPHA}/application/boards.py"]))
        self.assertEqual(
            [(f"{ALPHA}/application/boards.py", "REGISTRY_FILE_OWNED_TWICE",
              f"{ALPHA}/application/boards.py is held by alpha.board (its owner_path) and alpha.second (its "
              "owner_path); a file has one owner, so keep it in the files of one module")],
            self.findings(),
        )

    def test_a_package_init_may_go_unheld_but_not_held_twice(self) -> None:
        init = f"{ALPHA}/application/__init__.py"
        self.assertEqual([], self.findings())
        self.register(*OWNERS, ("alpha.package", [init]), ("alpha.application", [f"{ALPHA}/application/"]))
        found = self.findings()
        self.assertEqual(
            [(path, "REGISTRY_FILE_OWNED_TWICE") for path in (
                init, f"{ALPHA}/application/boards.py", f"{ALPHA}/application/intent.py")],
            [item[:2] for item in found],
        )
        self.assertIn(f"held by alpha.package (its owner_path) and alpha.application (through {ALPHA}/application/)",
                      found[0][2])


class ScopeTests(OwnershipCase):
    """Only the source the policy checks, as Git tracks it, needs an owner."""

    def test_tests_labs_and_files_outside_the_checked_roots_need_none(self) -> None:
        self.track("tools/tests/support_fixture.py", "packages/alpha/tests/alpha_fixture.py",
                   "tests/integration/support.py", "labs/probe/notes.py", "conftest.py", "scripts/run.py")
        self.assertEqual([], self.findings())

    def test_untracked_vendored_and_web_files_decide_nothing(self) -> None:
        _write(self.root, "tools/bench/scratch.py")
        self.track("tools/bench/third-party/vendored.py", "tools/bench/node_modules/lib/index.py",
                   "tools/bench/view.ts", "tools/bench/panel.tsx")
        self.assertEqual([], self.findings())

    def test_a_tree_without_a_registry_is_not_checked(self) -> None:
        (self.root / REGISTRY_PATH).unlink()
        self.track("tools/bench/driver.py")
        self.assertEqual([], self.findings())


class RunChecksTests(OwnershipCase):
    def test_archcheck_reports_an_unowned_and_a_twice_owned_file(self) -> None:
        self.track("tools/bench/driver.py")
        self.register(*OWNERS, ("tools.bench.extra", ["tools/bench/daily.py"]))
        self.assertEqual(
            [("tools/bench/daily.py", "REGISTRY_FILE_OWNED_TWICE"), ("tools/bench/driver.py", "REGISTRY_FILE_UNOWNED")],
            [(item.path, item.code) for item in run_checks(self.root, _policy()) if item.code.startswith("REGISTRY_FILE_")],
        )


class RepositoryTests(unittest.TestCase):
    """This repository's checked source files each have one owner."""

    def test_the_tree_passes(self) -> None:
        policy = load_policy(REPOSITORY / ARCHITECTURE_POLICY)
        self.assertEqual([], [(item.path, item.code, item.message) for item in check_file_owners(REPOSITORY, policy)])


if __name__ == "__main__":
    unittest.main()
