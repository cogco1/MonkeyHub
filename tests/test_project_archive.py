from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from archflow.project.archive import (
    ARCHIVE_OMISSIONS,
    ArchiveError,
    archive_target,
    read_project_archive,
    restore_project_archive,
    write_project_archive,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord


class ProjectArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project-new"
        record = StateRecord(project_id=self.root.name, run_id="authored", entities=())
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id=self.root.name,
            initial_state={"project_id": self.root.name, "version": 0},
            authored_record=record.to_dict(),
        )

    def export(self, name: str = "project-new.monkeyhub.zip") -> Path:
        archive_path = Path(self.temp.name) / "backups" / name
        write_project_archive(self.repository, archive_path)
        return archive_path

    def test_write_project_archive_returns_summary_with_counts(self) -> None:
        archive_path = Path(self.temp.name) / "backups" / "project-new.monkeyhub.zip"
        summary = write_project_archive(self.repository, archive_path)
        manifest, _ = read_project_archive(archive_path)
        rows = manifest["transfer"]["files"]
        head = self.repository.read_head()

        self.assertEqual(summary.project_id, "project-new")
        self.assertEqual(summary.version, head.version)
        self.assertEqual(summary.state_sha256, head.state_sha256)
        self.assertEqual(summary.format_version, manifest["transfer"]["format_version"])
        self.assertEqual(summary.run_count, len(manifest["transfer"]["run_ids"]))
        self.assertEqual(summary.file_count, len(rows))
        self.assertEqual(summary.retained_bytes, sum(row["size"] for row in rows))
        self.assertEqual(list(summary.categories), sorted(summary.categories))
        self.assertEqual(sum(summary.categories.values()), summary.file_count)
        self.assertEqual(summary.omissions, ARCHIVE_OMISSIONS)
        self.assertEqual(summary.external_dependencies, ())
        self.assertEqual(summary.archive_path, str(archive_path))
        self.assertEqual(summary.archive_bytes, archive_path.stat().st_size)
        self.assertEqual(
            summary.archive_sha256,
            hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        )
        self.assertIs(summary.verified, True)
        self.assertEqual(summary.project_dir, str(self.root))

    def test_archive_target_uses_manifest_project_id(self) -> None:
        archive_path = self.export()
        parent = Path(self.temp.name) / "restored"
        self.assertEqual(archive_target(parent, archive_path), parent / "project-new")

    def test_archive_target_refuses_unreadable_archive(self) -> None:
        broken = Path(self.temp.name) / "not-really.zip"
        broken.write_text("this is text, not an archive", encoding="utf-8")
        with self.assertRaises(ArchiveError) as refusal:
            archive_target(Path(self.temp.name) / "restored", broken)
        self.assertEqual(refusal.exception.code, "ARCHIVE_INVALID")

    def test_write_refuses_archive_inside_project_and_existing_file(self) -> None:
        inside = self.root / "backup.monkeyhub.zip"
        with self.assertRaises(ArchiveError) as refusal:
            write_project_archive(self.repository, inside)
        self.assertEqual(refusal.exception.code, "ARCHIVE_PATH_INVALID")
        self.assertFalse(inside.exists())

        taken = Path(self.temp.name) / "taken.monkeyhub.zip"
        taken.write_text("keep me", encoding="utf-8")
        with self.assertRaises(ArchiveError) as refusal:
            write_project_archive(self.repository, taken)
        self.assertEqual(refusal.exception.code, "ARCHIVE_PATH_INVALID")
        self.assertEqual(taken.read_text(encoding="utf-8"), "keep me")

    def test_write_refuses_relative_archive_path(self) -> None:
        with self.assertRaises(ArchiveError) as refusal:
            write_project_archive(self.repository, Path("project-new.monkeyhub.zip"))
        self.assertEqual(refusal.exception.code, "ARCHIVE_PATH_INVALID")
        self.assertFalse(Path("project-new.monkeyhub.zip").exists())

    def test_restore_refuses_occupied_target(self) -> None:
        archive_path = self.export()
        occupied = Path(self.temp.name) / "occupied" / "project-new"
        occupied.mkdir(parents=True)
        marker = occupied / "keep.txt"
        marker.write_text("do not overwrite", encoding="utf-8")

        with self.assertRaises(ArchiveError) as refusal:
            restore_project_archive(occupied, archive_path)
        self.assertEqual(refusal.exception.code, "ARCHIVE_TARGET_OCCUPIED")
        self.assertEqual([path.name for path in occupied.iterdir()], ["keep.txt"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not overwrite")

    def test_restore_returns_summary_pointing_at_restored_dir(self) -> None:
        archive_path = self.export()
        root = Path(self.temp.name) / "restored" / "project-new"
        repository, summary = restore_project_archive(root, archive_path)

        self.assertEqual(summary.project_dir, str(root))
        self.assertEqual(Path(summary.project_dir), repository.layout.root)
        self.assertIs(summary.verified, True)
        self.assertEqual(repository.read_head(), self.repository.read_head())
        self.assertEqual(summary.archive_path, str(archive_path))
        self.assertEqual(summary.archive_bytes, archive_path.stat().st_size)
        self.assertEqual(
            summary.archive_sha256,
            hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
