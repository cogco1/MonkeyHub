from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from archflow.project.location import ProjectLocationError, ProjectLocationKind, locate_project, open_located_project
from archive.archflow.project.bootstrap import bootstrap_raw_request_project


class ProjectLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self.local = self.root / "local-projects"
        self.workspace = self.root / "workspace-projects"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _bootstrap(self, root: Path, project_id: str) -> Path:
        project = root / project_id
        bootstrap_raw_request_project(
            project,
            project_id=project_id,
            prompt=f"Bootstrap {project_id}.",
            run_id="run-001",
            synthetic_test=True,
        )
        return project

    def _anchor(
        self,
        project_id: str,
        target: Path,
        *,
        manifest_hash: str | None = None,
    ) -> Path:
        self.local.mkdir(parents=True, exist_ok=True)
        path = self.local / f"{project_id}.anchor.json"
        payload = {
            "schema": "ProbeRelocationAnchor@1",
            "project_id": project_id,
            "moved_to": str(target),
        }
        if manifest_hash is not None:
            payload["project_json_sha256"] = manifest_hash
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_local_project_wins_and_opens_single_p036_repository(self):
        project = self._bootstrap(self.local, "local-case")
        self._bootstrap(self.workspace, "local-case")

        location, repository = open_located_project(
            "local-case",
            local_projects_root=self.local,
            workspace_projects_root=self.workspace,
        )

        self.assertIs(location.kind, ProjectLocationKind.LOCAL)
        self.assertEqual(project.resolve(), location.root)
        self.assertFalse(location.to_dict()["canonical_write_authority"])
        repository.verify()

    def test_relocation_anchor_validates_identity_and_manifest_hash(self):
        target = self._bootstrap(self.workspace, "relocated-case")
        manifest = target / "project.json"
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        anchor = self._anchor(
            "relocated-case",
            target,
            manifest_hash=digest,
        )

        location = locate_project(
            "relocated-case",
            local_projects_root=self.local,
            workspace_projects_root=self.workspace,
        )

        self.assertIs(location.kind, ProjectLocationKind.RELOCATED)
        self.assertEqual(target.resolve(), location.root)
        self.assertEqual(anchor.resolve(), location.anchor_path)

    def test_workspace_fallback_supports_active_unpromoted_project(self):
        target = self._bootstrap(self.workspace, "active-case")

        location = locate_project(
            "active-case",
            local_projects_root=self.local,
            workspace_projects_root=self.workspace,
        )

        self.assertIs(location.kind, ProjectLocationKind.WORKSPACE)
        self.assertEqual(target.resolve(), location.root)

    def test_corrupt_anchor_fails_closed_before_workspace_fallback(self):
        target = self._bootstrap(self.workspace, "anchored-case")
        self._anchor(
            "anchored-case",
            target,
            manifest_hash="0" * 64,
        )

        with self.assertRaisesRegex(
            ProjectLocationError,
            "manifest hash changed",
        ):
            locate_project(
                "anchored-case",
                local_projects_root=self.local,
                workspace_projects_root=self.workspace,
            )

    def test_cross_project_anchor_and_missing_project_do_not_write(self):
        target = self._bootstrap(self.workspace, "other-case")
        self._anchor("requested-case", target)

        with self.assertRaisesRegex(
            ProjectLocationError,
            "manifest belongs to other-case",
        ):
            locate_project(
                "requested-case",
                local_projects_root=self.local,
                workspace_projects_root=self.workspace,
            )

        absent_root = self.root / "absent-local"
        with self.assertRaisesRegex(ProjectLocationError, "no local project"):
            locate_project(
                "absent-case",
                local_projects_root=absent_root,
            )
        self.assertFalse(absent_root.exists())


if __name__ == "__main__":
    unittest.main()
