"""The Hub's two archive routes, driven over HTTP against a real project.

Export and restore are watched from outside: what the summary says, which
refusal comes back, and what the filesystem holds afterwards. Every byte is
written by ``archflow.project.archive``; these tests prove the Hub adds none.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from test_monkeyhub_lifecycle import LocalHubCase, ROOT, free_ports, project_fixture
from archflow.project.archive import ARCHIVE_OMISSIONS, ArchiveError
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_CANDIDATE_DELTA
from archflow.project.refs import RunRef
from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectHeadLocked,
    ProjectIntegrityError,
)
from archflow_studio_api.settings import save_application_settings
from archflow.state.state_record import StateRecord
from archflow_studio_api.transport.settings import ApplicationSettingsDto
from monkeyhub_api import project_archive
from monkeyhub_api.main import HubSettings, create_app


def fingerprint(root: Path) -> list[tuple[str, str]]:
    """Sorted relative paths and content digests, as the spine reads a tree.

    Advisory ``.lock`` files are left out exactly as ``tests/test_create_project.py``
    leaves them out: reading a project takes its locks, and an empty lock file
    beside HEAD is not project content that an export may change.
    """

    return sorted(
        (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*") if path.is_file() and path.suffix != ".lock"
    )


class ProjectArchiveRoutes(LocalHubCase):
    def setUp(self):
        super().setUp()
        self.fixture = project_fixture()
        self.fixture.make_project(self.root / "projects")
        self.project_id = self.fixture.PROJECT_ID
        self.project = self.root / "projects" / self.project_id
        self.archive = self.root / "out" / "p.monkeyhub.zip"

    @contextmanager
    def second_hub(self):
        """A second Hub with its own runtime root, empty workspace and ports."""

        runtime = self.root / "hub-b" / "runtime"
        hub_port, studio_port, monitor_port = free_ports(3)
        save_application_settings(runtime, ApplicationSettingsDto(
            **self.configuration(studioPort=studio_port, monitorPort=monitor_port)))
        app = create_app(HubSettings(runtime_root=runtime, port=hub_port), source_root=ROOT)
        with TestClient(app, base_url=f"http://127.0.0.1:{hub_port}") as client:
            yield client

    def export(self, client, **changes):
        body = {"projectDir": str(self.project), "archivePath": str(self.archive), **changes}
        return client.post("/api/project/archive/export", json=body)

    def restore(self, client, **changes):
        body = {"archivePath": str(self.archive), "targetParent": None, **changes}
        return client.post("/api/project/archive/restore", json=body)

    def exported(self):
        with self.hub() as client:
            response = self.export(client)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def restore_failing_verification(self, client, parent):
        """Restore an archive whose project fails to verify once it is written.

        The archive layer bootstraps the folder and only then finds the
        restored project unreadable, which is the one case that leaves bytes
        behind. It cannot be provoked from outside, so it is substituted here.
        """

        def verification_failed(target, archive):
            target.mkdir(parents=True, exist_ok=True)
            (target / "project.json").write_text("{}", encoding="utf-8")
            raise ArchiveError("ARCHIVE_INVALID", "restored project did not verify")

        with patch.object(project_archive, "restore_project_archive", verification_failed):
            return self.restore(client, targetParent=str(parent))

    def assert_names_the_folder_to_remove(self, response, target):
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_INVALID")
        self.assertTrue(response.json()["detail"].endswith(f"Remove {target} before retrying."),
                        response.json()["detail"])
        self.assertTrue((target / "project.json").is_file())

    def test_export_writes_archive_and_reports_summary(self):
        repository = FilesystemProjectRepository.open(self.project)
        run = repository.create_run("candidate")
        source = RunRef(self.project_id, "studio-projection", run.base)
        repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STUDIO_CANDIDATE_DELTA,
            payload={
                "schema": "StudioCandidateDelta@1", "project_id": self.project_id, "run_id": run.run_id,
                "source_run_ref": source.to_dict(), "source_stage_ref": None,
                "source_record_ref": None, "source_runner_ref": None, "source_model": None,
                "source_record": StateRecord(project_id=self.project_id, run_id=source.run_id,
                                             entities=(), base=source.base).to_dict(),
            },
        )
        before = fingerprint(self.project)
        head = repository.read_head()

        with self.hub() as client:
            response = self.export(client)

        self.assertEqual(response.status_code, 201, response.text)
        summary = response.json()
        self.assertEqual(summary["projectId"], self.project_id)
        self.assertEqual(summary["version"], head.version)
        self.assertEqual(summary["stateSha256"], head.state_sha256)
        self.assertGreater(summary["fileCount"], 0)
        self.assertEqual(sum(summary["categories"].values()), summary["fileCount"])
        self.assertEqual(summary["archivePath"], str(self.archive.resolve()))
        self.assertEqual(summary["archiveBytes"], os.path.getsize(self.archive))
        self.assertEqual(
            summary["archiveSha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertIs(summary["verified"], True)
        self.assertEqual(summary["runCount"], 2)  # fixture reference plus candidate; no synthetic run
        self.assertEqual(summary["omissions"], list(ARCHIVE_OMISSIONS))
        self.assertEqual(summary["externalDependencies"], [])
        self.assertEqual(summary["projectDir"], str(self.project.resolve()))
        self.assertEqual(fingerprint(self.project), before)

    def test_export_refuses_path_inside_project(self):
        before = fingerprint(self.project)
        inside = self.project / "backup.monkeyhub.zip"

        with self.hub() as client:
            response = self.export(client, archivePath=str(inside))

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_PATH_INVALID")
        self.assertFalse(inside.exists())
        self.assertEqual(fingerprint(self.project), before)

    def test_export_refuses_existing_archive(self):
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b"an earlier archive")

        with self.hub() as client:
            response = self.export(client)

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_PATH_INVALID")
        self.assertEqual(self.archive.read_bytes(), b"an earlier archive")

    def test_export_unknown_project_is_404(self):
        empty = self.root / "projects" / "not-a-project"
        empty.mkdir()

        with self.hub() as client:
            response = self.export(client, projectDir=str(empty))

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "PROJECT_NOT_FOUND")
        self.assertFalse(self.archive.exists())

    def test_export_refuses_a_project_that_changed_under_the_read(self):
        changed = ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: input/runner/state-record.json")

        with self.hub() as client, patch.object(
                project_archive, "write_project_archive", side_effect=changed):
            response = self.export(client)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_SOURCE_CHANGED")
        self.assertFalse(self.archive.exists())

    def test_export_refuses_a_project_whose_head_another_process_is_holding(self):
        """Every export reads HEAD; a head kept busy is the project moving."""

        busy = ProjectHeadLocked("concurrent HEAD access kept the project head busy: HEAD")

        with self.hub() as client, patch.object(
                project_archive, "write_project_archive", side_effect=busy):
            response = self.export(client)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_SOURCE_CHANGED")
        self.assertFalse(self.archive.exists())

    def test_export_refuses_a_project_that_cannot_be_exported_at_all(self):
        """A defect of the project is not the conflict a retry would clear."""

        broken = ProjectIntegrityError("TRANSFER_DEPENDENCY_MISSING: runs/r-1/records/a.json")

        with self.hub() as client, patch.object(
                project_archive, "write_project_archive", side_effect=broken):
            response = self.export(client)

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_SOURCE_INVALID")
        self.assertEqual(response.json()["detail"],
                         "TRANSFER_DEPENDENCY_MISSING: runs/r-1/records/a.json")
        self.assertFalse(self.archive.exists())

    @unittest.skipUnless(os.name == "nt", "a bare drive letter is a Windows path shape")
    def test_export_refuses_an_archive_path_on_a_drive_that_is_not_there(self):
        if Path("Q:/").exists():
            self.skipTest("Q: is a real drive on this machine")

        with self.hub() as client:
            response = self.export(client, archivePath="Q:\\nowhere\\p.monkeyhub.zip")

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_PATH_INVALID")

    def test_restore_into_fresh_workspace_lists_project(self):
        summary = self.exported()

        with self.second_hub() as other:
            workspace = Path(other.get("/api/chat/workspace").json()["workspaceDir"])
            self.assertEqual(other.get("/api/chat/projects").json(), [])

            response = self.restore(other)

            self.assertEqual(response.status_code, 201, response.text)
            result = response.json()
            restored = str((workspace / self.project_id).resolve())
            self.assertEqual(result["summary"]["projectDir"], restored)
            self.assertEqual(result["summary"]["archiveSha256"], summary["archiveSha256"])
            self.assertEqual(result["summary"]["version"], summary["version"])
            self.assertEqual(result["project"]["projectId"], self.project_id)
            self.assertEqual(result["project"]["projectDir"], restored)
            self.assertEqual(result["project"]["version"], summary["version"])
            self.assertEqual(
                [row["projectId"] for row in other.get("/api/chat/projects").json()],
                [self.project_id])
            opened = other.post("/api/runtime/projects/open",
                                json={"projectId": self.project_id, "projectDir": restored})
            self.assertEqual(opened.status_code, 200, opened.text)
            self.assertEqual(opened.json()["projectId"], self.project_id)

    def test_restore_refuses_occupied_target(self):
        self.exported()
        parent = self.root / "restored"
        occupied = parent / self.project_id
        occupied.mkdir(parents=True)
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")

        with self.hub() as client:
            response = self.restore(client, targetParent=str(parent))

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_TARGET_OCCUPIED")
        self.assertEqual(
            sorted(path.relative_to(parent).as_posix() for path in parent.rglob("*")),
            [self.project_id, f"{self.project_id}/keep.txt"])
        self.assertEqual((occupied / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_restore_refuses_an_archive_file_that_is_not_there(self):
        parent = self.root / "restored"

        with self.hub() as client:
            response = self.restore(client, targetParent=str(parent))

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_PATH_INVALID")
        self.assertEqual(response.json()["detail"],
                         "Give the full path of an existing .zip archive.")
        self.assertFalse(parent.exists())

    def test_restore_refuses_a_target_parent_that_is_a_file(self):
        self.exported()
        occupied = self.root / "not-a-folder.txt"
        occupied.write_text("a file, not a workspace", encoding="utf-8")

        with self.hub() as client:
            response = self.restore(client, targetParent=str(occupied))

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_PATH_INVALID")
        self.assertEqual(occupied.read_text(encoding="utf-8"), "a file, not a workspace")

    def test_restore_names_the_folder_a_failed_verification_left_behind(self):
        self.exported()
        parent = self.root / "restored"

        with self.hub() as client:
            response = self.restore_failing_verification(client, parent)

        self.assert_names_the_folder_to_remove(response, parent.resolve() / self.project_id)

    def test_restore_names_a_pre_existing_empty_folder_it_filled(self):
        self.exported()
        parent = self.root / "restored"
        target = parent / self.project_id
        target.mkdir(parents=True)

        with self.hub() as client:
            response = self.restore_failing_verification(client, parent)

        self.assert_names_the_folder_to_remove(response, target.resolve())

    def test_restore_refuses_a_target_another_restore_is_already_writing(self):
        """A second restore of the same project id holds that folder's head."""

        self.exported()
        parent = self.root / "restored"
        locked = ProjectHeadLocked(
            f"another process holds the project head lock: {parent / self.project_id}")

        with self.hub() as client, patch.object(
                project_archive, "restore_project_archive", side_effect=locked):
            response = self.restore(client, targetParent=str(parent))

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_TARGET_OCCUPIED")
        self.assertEqual(response.json()["detail"], str(locked))

    def test_restore_refuses_corrupt_archive_before_writing(self):
        self.exported()
        corrupt = self.root / "out" / "corrupt.monkeyhub.zip"
        flipped = False
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(corrupt, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if not flipped and info.filename.startswith("project/") and data:
                    data, flipped = bytes([data[0] ^ 0xFF]) + data[1:], True
                target.writestr(info, data)
        self.assertTrue(flipped)
        parent = self.root / "restored"

        with self.hub() as client:
            response = self.restore(client, archivePath=str(corrupt), targetParent=str(parent))

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "ARCHIVE_INVALID")
        self.assertFalse((parent / self.project_id).exists())
