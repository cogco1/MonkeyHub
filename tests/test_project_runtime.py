from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archive.archflow.project.runtime import RuntimeConfigError, RuntimePaths, bootstrap_external_project, initialize_runtime, load_runtime_config


class ExternalProjectRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_root = Path(__file__).resolve().parents[1]

    def _paths(self, root: Path) -> RuntimePaths:
        return RuntimePaths(
            workspace_root=(root / "workspace").resolve(),
            cache_root=(root / "cache").resolve(),
            temp_root=(root / "temp").resolve(),
        )

    def test_config_loads_three_explicit_external_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schema": "ArchFlowRuntimeConfig@1",
                "workspace_root": str((root / "workspace").resolve()),
                "cache_root": str((root / "cache").resolve()),
                "temp_root": str((root / "temp").resolve()),
            }
            config = root / "runtime.json"
            config.write_text(json.dumps(payload), encoding="utf-8")
            paths = load_runtime_config(
                config,
                repository_root=self.repository_root,
            )
            self.assertEqual(paths.to_dict()["schema"], payload["schema"])
            self.assertEqual(
                paths.to_dict()["workspace_root"],
                (root / "workspace").resolve().as_posix(),
            )
            self.assertEqual(
                paths.to_dict()["cache_root"],
                (root / "cache").resolve().as_posix(),
            )
            self.assertEqual(
                paths.to_dict()["temp_root"],
                (root / "temp").resolve().as_posix(),
            )
            self.assertFalse(paths.workspace_root.exists())

    def test_relative_overlapping_and_checkout_roots_fail_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeConfigError, "absolute"):
            RuntimePaths.from_dict(
                {
                    "schema": "ArchFlowRuntimeConfig@1",
                    "workspace_root": "relative/workspace",
                    "cache_root": str((self.repository_root.parent / "cache").resolve()),
                    "temp_root": str((self.repository_root.parent / "temp").resolve()),
                }
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(RuntimeConfigError, "non-overlapping"):
                RuntimePaths(
                    workspace_root=root,
                    cache_root=root / "cache",
                    temp_root=root.parent / "temp",
                )
        with self.assertRaisesRegex(RuntimeConfigError, "source checkout"):
            RuntimePaths(
                workspace_root=(self.repository_root / "runtime").resolve(),
                cache_root=(self.repository_root.parent / "cache").resolve(),
                temp_root=(self.repository_root.parent / "temp").resolve(),
            ).require_external_to(self.repository_root)

    def test_runtime_initialization_creates_no_project_or_canonical_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            receipt = initialize_runtime(paths)
            self.assertTrue(paths.projects_root.is_dir())
            self.assertTrue(paths.cache_root.is_dir())
            self.assertTrue(paths.temp_root.is_dir())
            self.assertEqual(tuple(paths.projects_root.iterdir()), ())
            self.assertEqual(receipt.canonical_authority, "FilesystemProjectRepository")
            self.assertFalse(receipt.durable_cloud_storage_claimed)

    def test_external_project_uses_p036_and_reopens_with_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            bootstrap = bootstrap_external_project(
                paths,
                project_id="external-building",
                prompt="Design a building from the supplied brief.",
            )
            root = paths.project("external-building")
            self.assertTrue(root.is_dir())
            self.assertFalse((self.repository_root / "external-building").exists())
            reopened = FilesystemProjectRepository.open(root)
            self.assertEqual(reopened.read_head(), bootstrap.head)
            self.assertEqual(reopened.verify().orphan_paths, ())
            self.assertTrue(bootstrap.request.uri.startswith("project://external-building/"))

    def test_cache_and_temp_are_project_scoped_but_noncanonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(Path(temporary))
            self.assertEqual(
                paths.cache("building-a", "run-001"),
                paths.cache_root / "projects" / "building-a" / "runs" / "run-001",
            )
            self.assertEqual(
                paths.temporary("building-a"),
                paths.temp_root / "projects" / "building-a",
            )
            with self.assertRaises(ValueError):
                paths.project("../escape")

    def test_existing_probe_stays_reloadable(self) -> None:
        probe = self.repository_root / "probes" / "test_pantheon"
        repository = FilesystemProjectRepository.open(probe)
        self.assertEqual(repository.layout.root, probe.resolve())
        self.assertEqual(repository.verify().orphan_paths, ())


if __name__ == "__main__":
    unittest.main()
