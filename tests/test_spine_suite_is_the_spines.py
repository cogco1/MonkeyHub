"""The spine suite tests the spine, and nothing in it reaches into ``archive``.

Retired lane source and its tests are kept in a local archive outside the public
checkout. Keeping tests that depend on that source under ``tests/`` would make a
public clone depend on unavailable code. The guard remains useful if an old
import is accidentally reintroduced (ADR-001).

This walks every module of the spine suite and fails on the first ``archive``
import, function-local ones included. ``tools/archcheck.py`` carries the same
rule for the checked source roots (``tests`` is one of them, and an ``archive``
import from there is a LAYER_AUTHORITY_VIOLATION); this test is the suite's own
copy of it, so a green suite already proves the boundary.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


SUITE_ROOT = Path(__file__).resolve().parent


def _imported_modules(tree: ast.Module) -> tuple[tuple[str, int], ...]:
    """Every module name imported anywhere in one file, with its line."""

    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.append((node.module, node.lineno))
    return tuple(imported)


class SpineSuiteBoundaryTests(unittest.TestCase):
    def test_no_spine_test_imports_the_archive(self) -> None:
        offenders: list[str] = []
        for path in sorted(SUITE_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            relative = path.relative_to(SUITE_ROOT.parent).as_posix()
            offenders.extend(
                f"{relative}:{line}: imports {module}"
                for module, line in _imported_modules(tree)
                if module == "archive" or module.startswith("archive.")
            )
        self.assertEqual(
            [],
            offenders,
            "the public spine suite must not depend on locally archived lane code",
        )

    def test_the_walk_reads_the_suite_it_claims_to_read(self) -> None:
        modules = [
            path
            for path in SUITE_ROOT.rglob("*.py")
            if "__pycache__" not in path.parts and path.name.startswith("test_")
        ]
        self.assertGreater(len(modules), 1)
        self.assertIn(Path(__file__).resolve(), modules)


if __name__ == "__main__":
    unittest.main()
