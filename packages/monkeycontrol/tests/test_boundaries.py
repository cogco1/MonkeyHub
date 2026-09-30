from __future__ import annotations

import ast
import unittest
from pathlib import Path

import monkeycontrol

PACKAGE = Path(monkeycontrol.__file__).resolve().parent
SPINE_IMPORT = "archflow.contracts.canonical"
FORBIDDEN = (
    "monkeyarch",
    "monkeydiagram",
    "monkeymonitor",
    "archflow_studio_api",
    "monkeyhub_api",
    "tools",
    "tests",
)


def absolute_imports() -> list[tuple[str, str, int]]:
    """Every absolute import in the package, as (file, module, line)."""

    found: list[tuple[str, str, int]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(PACKAGE).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.extend(
                    (relative, alias.name, node.lineno) for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.append((relative, node.module, node.lineno))
    return found


class ImportBoundaryTests(unittest.TestCase):
    def test_the_package_is_the_one_under_test(self) -> None:
        self.assertGreaterEqual(len(list(PACKAGE.rglob("*.py"))), 4)
        self.assertTrue(absolute_imports())

    def test_the_only_spine_import_is_canonical_json(self) -> None:
        spine = [
            entry
            for entry in absolute_imports()
            if entry[1] == "archflow" or entry[1].startswith("archflow.")
        ]
        self.assertTrue(spine, "the package is expected to use canonical digests")
        for file, module, line in spine:
            self.assertEqual(module, SPINE_IMPORT, f"{file}:{line} imports {module}")

    def test_no_workflow_application_or_repository_tooling_is_imported(self) -> None:
        for file, module, line in absolute_imports():
            head = module.split(".")[0]
            self.assertNotIn(head, FORBIDDEN, f"{file}:{line} imports {module}")


if __name__ == "__main__":
    unittest.main()
