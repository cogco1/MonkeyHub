from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project import (
    BranchRef,
    PersistenceArea,
    PersistenceDestination,
    PersistenceDestinationRequired,
    ProjectArtifactRef,
    ProjectLayout,
    ProjectManifest,
    ProjectManifestError,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_destination,
)


ZERO_DIGEST = "0" * 64
ONE_DIGEST = "1" * 64


class ProjectBoundaryTests(unittest.TestCase):
    def test_manifest_contains_no_mutable_head_or_building_answer(self) -> None:
        manifest = ProjectManifest(project_id="test-project")
        payload = manifest.to_dict()
        self.assertEqual(
            payload,
            {
                "schema": "ArchFlowProject@1",
                "project_id": "test-project",
                "format_version": 1,
            },
        )
        self.assertEqual(ProjectManifest.from_dict(payload), manifest)
        with self.assertRaises(ProjectManifestError):
            ProjectManifest.from_dict({**payload, "head": 3})

    def test_project_run_and_branch_identities_are_distinct(self) -> None:
        base = ProjectVersionRef("project-a", 4, ZERO_DIGEST)
        run = RunRef("project-a", "run-001", base)
        branch = BranchRef(run, "option-a", 2)
        self.assertEqual(branch.run.base.version, 4)
        self.assertEqual(branch.epoch, 2)
        with self.assertRaises(ValueError):
            RunRef(
                "project-b",
                "run-001",
                base,
            )

    def test_layout_is_side_effect_free_and_names_known_areas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-a"
            layout = ProjectLayout(root, "project-a")
            self.assertFalse(root.exists())
            self.assertEqual(layout.manifest, root / "project.json")
            self.assertEqual(layout.head, root / "HEAD")
            self.assertEqual(layout.objects, root / "objects" / "sha256")
            self.assertEqual(
                layout.run("run-001").reviews,
                root / "runs" / "run-001" / "reviews",
            )
            self.assertFalse(root.exists())

    def test_project_refs_resolve_inside_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = ProjectLayout(
                Path(temp_dir) / "project-a",
                "project-a",
            )
            record = ProjectRecordRef(
                project_id="project-a",
                relative_path="runs/run-001/records/query.json",
                sha256=ZERO_DIGEST,
            )
            artifact = ProjectArtifactRef(
                project_id="project-a",
                artifact_id="artifact-001",
                relative_path="objects/sha256/11/artifact.bin",
                sha256=ONE_DIGEST,
                media_type="application/octet-stream",
            )
            self.assertEqual(
                layout.resolve_record(record),
                layout.root
                / "runs"
                / "run-001"
                / "records"
                / "query.json",
            )
            self.assertTrue(record.uri.startswith("project://project-a/"))
            self.assertEqual(
                layout.resolve_record(artifact),
                layout.root / "objects" / "sha256" / "11" / "artifact.bin",
            )
            with self.assertRaises(ValueError):
                ProjectRecordRef(
                    project_id="project-a",
                    relative_path="../outside.json",
                    sha256=ZERO_DIGEST,
                )
            with self.assertRaises(ValueError):
                layout.resolve_record(
                    ProjectRecordRef(
                        project_id="project-b",
                        relative_path="events/0001.json",
                        sha256=ZERO_DIGEST,
                    )
                )

    def test_unassigned_destination_stops_before_write(self) -> None:
        with self.assertRaisesRegex(
            PersistenceDestinationRequired,
            "ask the project owner",
        ):
            require_destination(None, producer="program_compiler")
        destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id="run-001",
        )
        self.assertIs(
            require_destination(destination, producer="program_compiler"),
            destination,
        )
        with self.assertRaises(TypeError):
            PersistenceDestination("misc")  # type: ignore[arg-type]

    def test_project_skeleton_has_no_filesystem_writer(self) -> None:
        project_root = Path(__file__).resolve().parents[1] / "archflow" / "project"
        source = "\n".join(
            (project_root / name).read_text(encoding="utf-8")
            for name in ("layout.py", "manifest.py", "ports.py", "refs.py")
        )
        for forbidden in (
            ".write_text(",
            ".write_bytes(",
            ".mkdir(",
            "open(",
        ):
            self.assertNotIn(forbidden, source)

        rules = (
            Path(__file__).resolve().parents[1] / "AGENTS.md"
        ).read_text(encoding="utf-8")
        self.assertIn("ask Kevin to decide its ownership", rules)


if __name__ == "__main__":
    unittest.main()
