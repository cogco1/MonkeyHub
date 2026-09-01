from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_pantheon_reconstruction.py"
PROJECT_SUPPORT = (
    ROOT / "tools" / "projects" / "pantheon" / "monument_support.py"
)
FIXTURE_SUPPORT = (
    ROOT / "tools" / "projects" / "pantheon" / "fixture_support.py"
)


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
    return tuple(sorted(names))


def _support_snapshot(module) -> tuple[tuple[str, int, str], ...]:
    names = (
        "PROJECT_ID",
        "RUN_ID",
        "PROMPT",
        "CENTER_X",
        "CENTER_Z",
        "DRUM_OUTER",
        "DRUM_INNER",
        "_STAGE_COMPONENTS",
        "_STAGE_REVISED",
        "_stage_symmetry_findings",
        "_monument_context",
        "_monument_proposal",
        "_monument_geometry",
        "_MonumentScriptedProvider",
        "_persist_stage",
    )
    return tuple(
        (name, id(getattr(module, name)), repr(getattr(module, name)))
        for name in names
    )


class PantheonRunnerBoundaryTests(unittest.TestCase):
    def test_production_runner_and_support_do_not_import_tests(self):
        for path in (RUNNER, PROJECT_SUPPORT, FIXTURE_SUPPORT):
            imported = _imported_modules(path)
            forbidden = tuple(
                name
                for name in imported
                if name == "tests" or name.startswith("tests.")
            )
            self.assertEqual((), forbidden, f"{path}: {forbidden}")

    def test_runner_does_not_inject_tests_into_sys_path(self):
        source = RUNNER.read_text(encoding="utf-8")
        self.assertNotIn('ROOT / "tests"', source)
        self.assertNotIn("ROOT / 'tests'", source)
        self.assertIn(
            "from tools.projects.pantheon import monument_support as M",
            source,
        )

    def test_runner_import_succeeds_when_tests_imports_are_blocked(self):
        script = f"""
import importlib.abc
import sys

sys.path.insert(0, {str(ROOT)!r})

class BlockTests(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tests' or fullname.startswith('tests.'):
            raise ImportError('production import attempted to load tests: ' + fullname)
        return None

sys.meta_path.insert(0, BlockTests())
import tools.run_pantheon_reconstruction
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0,
            result.returncode,
            f"stdout={result.stdout}\nstderr={result.stderr}",
        )

    def test_moved_monument_context_preserves_the_retained_contract(self):
        from tools.projects.pantheon import monument_support

        payload = monument_support._monument_context().to_dict()
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.assertEqual(
            "66475cce737e6c660ebf09d36c464552fd3eb337dff1e5420b1cfed00a0cef40",
            hashlib.sha256(encoded).hexdigest(),
        )

    def test_runner_context_and_stage_hooks_are_profile_injected(self):
        from tools import run_pantheon_reconstruction as runner
        from tools.projects.pantheon import monument_support

        before = _support_snapshot(monument_support)
        first_profile = replace(
            runner.DEFAULT_RUNNER_PROFILE,
            run_id="profile-one",
            center_x=21.0,
            center_z=33.0,
        )
        second_profile = replace(
            runner.DEFAULT_RUNNER_PROFILE,
            run_id="profile-two",
            center_x=29.0,
            center_z=44.0,
        )

        first = runner.create_runner_context(profile=first_profile)
        first_digest = first.support_context_digest
        second = runner.create_runner_context(profile=second_profile)

        self.assertEqual("PantheonRunnerProfile@1", first.profile.to_dict()["schema"])
        self.assertNotEqual(first.profile.profile_digest, second.profile.profile_digest)
        self.assertNotEqual(first_digest, second.support_context_digest)
        self.assertEqual(first_digest, first.support_context_digest)
        self.assertEqual(before, _support_snapshot(monument_support))
        with self.assertRaises(FrozenInstanceError):
            first.profile.run_id = "mutated"

    def test_install_failure_leaves_support_module_unchanged(self):
        from tools import run_pantheon_reconstruction as runner
        from tools.projects.pantheon import monument_support

        before = _support_snapshot(monument_support)
        with self.assertRaisesRegex(TypeError, "stage_closure_resolver"):
            runner.install(
                run_id="profile-that-must-fail",
                stage_closure_resolver=object(),
            )
        self.assertEqual(before, _support_snapshot(monument_support))

    def test_runner_has_no_support_module_assignment_or_legacy_state(self):
        source = RUNNER.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(RUNNER))
        support_assignments: list[str] = []
        for node in ast.walk(tree):
            targets = ()
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (
                    tuple(node.targets)
                    if isinstance(node, ast.Assign)
                    else (node.target,)
                )
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "M"
                ):
                    support_assignments.append(target.attr)

        self.assertEqual([], support_assignments)
        self.assertNotIn("setattr(M,", source)
        for name in (
            "_ORIGINAL_CONTEXT",
            "_ORIGINAL_PERSIST",
            "_CONTRACTS",
            "_DECLARATION_LOG",
            "_STAGE_CLOSURE_RESOLVER",
        ):
            self.assertNotIn(name, source)

    def test_default_runner_support_context_digest_is_frozen(self):
        from tools import run_pantheon_reconstruction as runner

        self.assertEqual(
            "e4322a15cf588cb3f8e2bda01dd07f60efc1b48d1c6c1a197295250bbf6c8e08",
            runner.create_runner_context().support_context_digest,
        )


if __name__ == "__main__":
    unittest.main()
