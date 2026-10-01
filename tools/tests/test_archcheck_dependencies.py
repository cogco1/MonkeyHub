"""A registry entry's depends_on names the registered modules its files import (#537).

Before #537 depends_on mixed module ids with import names: archcheck matched an
import to an owner only through owner_path, so a module held through another
entry's files could be declared only by its import name, and nothing reported a
declaration that no file imported any more or that named nothing at all.

Now an import is held by the entry whose files list the imported module, and
depends_on lists module ids only. An entry that is not one is unknown; one whose
module the files no longer import is stale; an imported module whose id begins
with one of the policy's declared_dependency_namespaces must be declared. The
hand-kept used_by list is retired.

The cases build small trees in a temporary directory; nothing here reads or
writes this repository.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.governance.archcheck import ArchitecturePolicyError, check_registry, validate_policy


REGISTRY_PATH = "governance/module_registry.json"
PYTHON_SOURCE_ROOTS = [".", "packages/archflow/src"]
NAMESPACES = {"packages/archflow/src/archflow": "archflow", "tools": "tools"}

# The fixture tree: three archflow owners, one of them holding two files, a
# package whose __init__ is an owner, a module no entry holds, and two tools.
FILES = {
    "packages/archflow/src/archflow/contracts/canonical.py": "def canonical_json(value):\n    return value\n",
    "packages/archflow/src/archflow/project/__init__.py": "",
    "packages/archflow/src/archflow/project/repository.py": "class Repository:\n    pass\n",
    "packages/archflow/src/archflow/project/digests.py": "def digest(value):\n    return value\n",
    "packages/archflow/src/archflow/state/__init__.py": "KINDS = ()\n",
    "packages/archflow/src/archflow/state/record.py": "class Record:\n    pass\n",
    "packages/archflow/src/archflow/state/loose.py": "LOOSE = 1\n",
    "tools/report.py": "def report(value):\n    return value\n",
}
OWNERS = {
    "archflow.contracts.canonical": ["packages/archflow/src/archflow/contracts/canonical.py"],
    "archflow.project.repository": [
        "packages/archflow/src/archflow/project/repository.py",
        "packages/archflow/src/archflow/project/digests.py",
    ],
    "archflow.state": ["packages/archflow/src/archflow/state/__init__.py"],
    "archflow.state.record": ["packages/archflow/src/archflow/state/record.py"],
    "tools.report": ["tools/report.py"],
}


def _policy(**changes: object) -> dict[str, object]:
    policy: dict[str, object] = {
        "schema": "ArchFlowArchitecturePolicy@1",
        "source_root": "packages/archflow",
        "checked_source_roots": ["packages/archflow", "tools"],
        "import_only_source_roots": [],
        "python_source_roots": PYTHON_SOURCE_ROOTS,
        "repository_root_entries": ["packages", "tools"],
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
        "module_id_namespaces": NAMESPACES,
        "declared_dependency_namespaces": ["archflow"],
    }
    policy.update(changes)
    validate_policy(policy)
    return policy


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _entry(module_id: str, files: list[str], depends_on: list[str], **changes: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "module_id": module_id, "owner_path": files[0], "depends_on": depends_on,
        "untested_reason": "synthetic owner",
    }
    if len(files) > 1:
        entry["files"] = files
    entry.update(changes)
    return entry


class RegistryDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative, text in FILES.items():
            _write(self.root, relative, text)

    def findings(
        self, source: str, depends_on: list[str], *, consumer: str = "tools/consumer.py",
        consumer_id: str = "tools.consumer", policy: dict[str, object] | None = None, **changes: object,
    ) -> list[tuple[str, str]]:
        """The dependency findings for one consumer whose file, its owner_path, is ``source``."""

        _write(self.root, consumer, source)
        modules = [_entry(module_id, files, []) for module_id, files in OWNERS.items() if module_id != consumer_id]
        modules.append(_entry(consumer_id, OWNERS.get(consumer_id, [consumer]), depends_on, **changes))
        _write(self.root, REGISTRY_PATH, json.dumps({"modules": modules}))
        return [
            (item.code, item.message)
            for item in check_registry(self.root, policy or _policy())
            if item.code.startswith(("REGISTRY_DEPENDS_ON", "REGISTRY_RETIRED"))
        ]

    def test_an_unknown_a_stale_and_a_correct_entry(self) -> None:
        source = "from archflow.contracts.canonical import canonical_json\n"
        self.assertEqual([], self.findings(source, ["archflow.contracts.canonical"]))
        findings = self.findings(source, ["archflow.contracts", "archflow.contracts.canonical", "archflow.project.repository"])
        self.assertEqual(
            [
                ("REGISTRY_DEPENDS_ON_UNKNOWN",
                 "tools.consumer depends_on 'archflow.contracts' is not a registered module id; depends_on lists "
                 "module ids, and no entry's files hold a module of that name"),
                ("REGISTRY_DEPENDS_ON_STALE",
                 "tools.consumer declares archflow.project.repository in depends_on, but none of its files imports "
                 "a module archflow.project.repository holds; drop it"),
            ],
            findings,
        )

    def test_an_import_name_is_answered_with_the_module_that_holds_it(self) -> None:
        # digests.py is one of archflow.project.repository's files, so its owner is that id.
        source = "from archflow.project.digests import digest\n"
        findings = self.findings(source, ["archflow.project.digests"])
        self.assertEqual(["REGISTRY_DEPENDS_ON_UNKNOWN", "REGISTRY_DEPENDS_ON_DRIFT"], [code for code, _ in findings])
        self.assertEqual(
            "tools.consumer depends_on 'archflow.project.digests' is not a registered module id but an import "
            "name; archflow.project.repository holds that module, so name archflow.project.repository",
            findings[0][1],
        )
        self.assertEqual([], self.findings(source, ["archflow.project.repository"]))

    def test_an_import_name_of_an_entrys_own_file_is_dropped(self) -> None:
        findings = self.findings(
            "from archflow.project.digests import digest\n", ["archflow.project.digests"],
            consumer="packages/archflow/src/archflow/project/repository.py", consumer_id="archflow.project.repository",
        )
        self.assertEqual(
            [("REGISTRY_DEPENDS_ON_UNKNOWN",
              "archflow.project.repository depends_on 'archflow.project.digests' is not a registered module id "
              "but the import name of one of its own files; drop it")],
            findings,
        )

    def test_an_entry_that_names_itself_is_stale(self) -> None:
        self.assertEqual(
            [("REGISTRY_DEPENDS_ON_STALE", "tools.consumer names itself in depends_on")],
            self.findings("VALUE = 1\n", ["tools.consumer"]),
        )

    def test_an_import_in_a_declared_namespace_must_be_declared(self) -> None:
        source = "from archflow.project.digests import digest\nfrom tools.report import report\n"
        findings = self.findings(source, [])
        self.assertEqual(
            [("REGISTRY_DEPENDS_ON_DRIFT",
              "tools.consumer imports archflow.project.digests (held by archflow.project.repository) "
              "but depends_on does not name archflow.project.repository")],
            findings,
        )
        # tools is not a declared namespace: declaring it is optional, but a declaration must be true.
        self.assertEqual([], self.findings(source, ["archflow.project.repository", "tools.report"]))
        self.assertEqual(
            ["REGISTRY_DEPENDS_ON_STALE"],
            [code for code, _ in self.findings("from archflow.project.repository import Repository\n",
                                               ["archflow.project.repository", "tools.report"])],
        )
        # With no declared namespace nothing has to be declared, and declarations are still checked.
        self.assertEqual([], self.findings(source, [], policy=_policy(declared_dependency_namespaces=[])))

    def test_each_import_form_reaches_the_module_that_holds_what_it_takes(self) -> None:
        for source, holder in (
            ("import archflow.state.record\n", "archflow.state.record"),
            ("from archflow.state import record\n", "archflow.state.record"),
            # A name the package's __init__ defines is taken from the package.
            ("from archflow.state import KINDS\n", "archflow.state"),
            ("from archflow.project import digests\n", "archflow.project.repository"),
            ("def later():\n    from archflow.contracts.canonical import canonical_json\n", "archflow.contracts.canonical"),
        ):
            with self.subTest(source=source):
                findings = self.findings(source, [])
                self.assertEqual(["REGISTRY_DEPENDS_ON_DRIFT"], [code for code, _ in findings])
                self.assertTrue(findings[0][1].endswith(f"depends_on does not name {holder}"), findings[0][1])
                self.assertEqual([], self.findings(source, [holder]))

    def test_a_relative_import_is_resolved_against_the_importing_file(self) -> None:
        consumer = "packages/archflow/src/archflow/state/record.py"
        findings = self.findings("from ..contracts.canonical import canonical_json\nfrom . import KINDS\n", [],
                                 consumer=consumer, consumer_id="archflow.state.record")
        self.assertEqual(
            [("REGISTRY_DEPENDS_ON_DRIFT",
              "archflow.state.record imports archflow.contracts.canonical, archflow.state but depends_on does "
              "not name archflow.contracts.canonical, archflow.state")],
            findings,
        )

    def test_a_module_no_entry_holds_is_matched_to_nothing(self) -> None:
        # It cannot be declared by id, so it is not required, and it keeps no declaration alive:
        # importing a submodule takes that module, not the package above it.
        self.assertEqual([], self.findings("import archflow.state.loose\n", []))
        self.assertEqual(
            ["REGISTRY_DEPENDS_ON_STALE"],
            [code for code, _ in self.findings("import archflow.state.loose\n", ["archflow.state"])],
        )

    def test_files_whose_imports_are_unknown_leave_their_declarations_alone(self) -> None:
        # A file that does not parse, or no Python source at all: no declaration is called stale.
        self.assertEqual([], self.findings("def broken(:\n", ["archflow.state.record"]))
        self.assertEqual([], self.findings("notes\n", ["archflow.state.record"], consumer="tools/notes.md",
                                           consumer_id="tools.notes"))

    def test_an_entry_that_is_not_text_is_unknown(self) -> None:
        self.assertEqual(
            [("REGISTRY_DEPENDS_ON_UNKNOWN",
              "tools.consumer depends_on None is not a registered module id; depends_on lists module ids, and no "
              "entry's files hold a module of that name")],
            self.findings("VALUE = 1\n", [None]),  # type: ignore[list-item]
        )

    def test_used_by_is_retired(self) -> None:
        findings = self.findings("VALUE = 1\n", [], used_by=["tools/report.py"])
        self.assertEqual(
            [("REGISTRY_RETIRED_FIELD",
              "tools.consumer: used_by is retired (#537); the modules that use it are read from their imports")],
            findings,
        )


class DeclaredNamespacePolicyTests(unittest.TestCase):
    def test_the_policy_names_the_declared_namespaces(self) -> None:
        without = _policy()
        del without["declared_dependency_namespaces"]
        with self.assertRaisesRegex(ArchitecturePolicyError, "declared_dependency_namespaces must be a string list"):
            validate_policy(without)
        self.assertEqual([], _policy(declared_dependency_namespaces=[])["declared_dependency_namespaces"])

    def test_a_declared_namespace_is_one_module_id_namespaces_gives(self) -> None:
        with self.assertRaisesRegex(ArchitecturePolicyError, "must not contain duplicates"):
            _policy(declared_dependency_namespaces=["archflow", "archflow"])
        # An import name is not a namespace: the Hub's package monkeyhub_api has the namespace hub.
        for namespace in ("monkeydiagram", "archflow.state", "packages/archflow/src/archflow"):
            with self.subTest(namespace=namespace), self.assertRaisesRegex(
                ArchitecturePolicyError, f"entry '{namespace}' is no namespace that module_id_namespaces gives a unit",
            ):
                _policy(declared_dependency_namespaces=[namespace])


if __name__ == "__main__":
    unittest.main()
