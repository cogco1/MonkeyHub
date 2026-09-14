from __future__ import annotations

import contextlib
import base64
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from archflow.project.inputs import load_authored_record, load_program_sheet_file, load_seat_pack_file, write_program_sheet_file
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import Entity, StateRecord
from tools.create_project import main


class CreateProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project-new"

    def create(self, *args: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return main(["--project", str(self.root), *args])

    def invoke(self, root: Path, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--project", str(root), *args])
        return result, output.getvalue()

    def test_cli_creates_and_reopens_empty_external_project(self) -> None:
        self.root = Path(self.temp.name) / "工作 space" / "project-new"
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(Path(__file__).resolve().parents[1] / "tools/create_project.py"),
             "--project", str(self.root)],
            cwd=self.temp.name, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        repository = FilesystemProjectRepository.open(self.root)
        self.assertEqual(repository.read_head().version, 0)
        self.assertEqual(load_authored_record(repository).record.entities, ())
        self.assertEqual(list(repository.layout.runs.iterdir()), [])
        self.assertIn("project is empty", result.stdout)

    def test_supplied_inputs_survive_reopen_without_a_run_or_issue(self) -> None:
        record_path = Path(self.temp.name) / "record.json"
        seats_path = Path(self.temp.name) / "seats.json"
        record = StateRecord(project_id=self.root.name, run_id="authored", entities=(Entity("frame", "Component@1", {"semantic_kind": "building"}),),
                             option={"option_id": "starting-option", "label": "设计输入"})
        seats = {"commitment_ref": "commitment:project-new", "seats": [
            {"seat_id": "author", "disciplines": ["structure_support"], "phases": ["design_development"], "owned_component_ids": ["frame"]}
        ]}
        record_path.write_text(json.dumps(record.to_dict()), encoding="utf-8-sig")
        seats_path.write_text(json.dumps(seats), encoding="utf-8-sig")
        self.assertEqual(self.create("--state-record", str(record_path), "--seats-file", str(seats_path)), 0)
        repository = FilesystemProjectRepository.open(self.root)
        self.assertEqual(load_authored_record(repository).record.to_dict(), record.to_dict())
        self.assertEqual(load_seat_pack_file(repository).payload, seats)
        self.assertEqual(repository.read_head().version, 0)
        self.assertEqual(list(repository.layout.runs.iterdir()), [])

    def test_existing_project_and_unrelated_files_are_preserved(self) -> None:
        self.create()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.create()
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.root = Path(self.temp.name) / "other-project"
        self.root.mkdir()
        (self.root / "model.3dm").write_bytes(b"existing source")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.create()
        self.assertEqual([p.name for p in self.root.iterdir()], ["model.3dm"])

    def test_invalid_foreign_or_bound_inputs_leave_no_project(self) -> None:
        path = Path(self.temp.name) / "record.json"
        for payload in ({"schema": "wrong"}, StateRecord(project_id="another-project", run_id="authored", entities=()).to_dict()):
            with self.subTest(payload=payload):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.create("--state-record", str(path))
                self.assertFalse(self.root.exists())
        source = FilesystemProjectRepository.initialize(Path(self.temp.name) / "source", project_id=self.root.name, initial_state={})
        record = StateRecord(project_id=self.root.name, run_id="retained", entities=(), base=source.read_head())
        path.write_text(json.dumps(record.to_dict()), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.create("--state-record", str(path))
        self.assertFalse(self.root.exists())

    def test_invalid_seats_are_refused_before_any_project_write(self) -> None:
        record_path = Path(self.temp.name) / "record.json"
        seats_path = Path(self.temp.name) / "seats.json"
        record_path.write_text(json.dumps(StateRecord(project_id=self.root.name, run_id="authored", entities=()).to_dict()), encoding="utf-8")
        seats_path.write_text(json.dumps({"commitment_ref": "commitment:test", "seats": [{"seat_id": "bad"}]}), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.create("--state-record", str(record_path), "--seats-file", str(seats_path))
        self.assertFalse(self.root.exists())

    def test_project_archive_restores_same_verified_project_without_runtime_noise(self) -> None:
        self.create()
        source = FilesystemProjectRepository.open(self.root)
        runtime_noise = self.root / "runtime-local.log"
        runtime_noise.write_text("not portable project truth", encoding="utf-8")
        archive_path = Path(self.temp.name) / "backups" / "project-new.monkeyhub.zip"

        result, output = self.invoke(self.root, "--export-archive", str(archive_path))
        self.assertEqual(result, 0)
        self.assertIn("Exported project project-new at version 0", output)
        self.assertTrue(archive_path.is_file())

        with zipfile.ZipFile(archive_path, "r") as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["schema"], "ProjectArchiveManifest@1")
        self.assertEqual(manifest["transfer"]["project_id"], "project-new")
        self.assertEqual(manifest["transfer"]["head"], source.read_head().to_dict())
        self.assertNotIn("project/runtime-local.log", names)
        self.assertIn("credentials/tokens", manifest["omissions"])

        restored_root = Path(self.temp.name) / "restored" / "project-new"
        result, output = self.invoke(restored_root, "--restore-archive", str(archive_path))
        self.assertEqual(result, 0)
        self.assertIn("verified it through normal project readers", output)
        restored = FilesystemProjectRepository.open(restored_root)
        self.assertEqual(restored.load_manifest(), source.load_manifest())
        self.assertEqual(restored.read_head(), source.read_head())
        self.assertEqual(restored.read_design_branches(), source.read_design_branches())
        self.assertEqual(load_authored_record(restored).record.to_dict(), load_authored_record(source).record.to_dict())
        self.assertEqual(restored.verify(), source.verify())
        self.assertFalse((restored_root / "runtime-local.log").exists())

    def test_project_archive_corruption_fails_before_restore_writes(self) -> None:
        self.create()
        archive_path = Path(self.temp.name) / "project-new.monkeyhub.zip"
        self.invoke(self.root, "--export-archive", str(archive_path))
        corrupted = Path(self.temp.name) / "project-new-corrupted.monkeyhub.zip"
        with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(corrupted, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "project/HEAD":
                    data += b"corrupt"
                target.writestr(info, data)

        restored_root = Path(self.temp.name) / "corrupt-restore" / "project-new"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke(restored_root, "--restore-archive", str(corrupted))
        self.assertFalse(restored_root.exists())

    def test_project_archive_preserves_board_documents_and_original_source_bytes(self) -> None:
        studio_api = Path(__file__).resolve().parents[1] / "apps/archflow-studio/api"
        with patch.object(sys, "path", [str(studio_api), *sys.path]):
            from PIL import Image
            from archflow_studio_api.application.artifacts import document_bytes, list_documents, save_document
            from archflow_studio_api.application.binding import ProjectBinding
            from archflow_studio_api.application.boards import read_board, save_board
            from archflow_studio_api.settings import StudioSettings

        self.create()
        binding = ProjectBinding.open(StudioSettings(project_dir=self.root, cad_export="off"))
        original = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(original, format="PNG")
        document = save_document(binding, None, "original.png", "image/png",
                                 base64.b64encode(original.getvalue()).decode("ascii"))
        elements = [
            {"id": "note", "type": "text", "text": "Retain this project note"},
            {"id": "source-image", "type": "image", "fileId": "registered-source", "x": 20, "y": 40, "width": 100, "height": 100,
             "customData": {"sourceDocument": {"runId": document.run_id, "assetSha256": document.asset_sha256,
                                               "pageIndex": 0}}},
        ]
        first = save_board(binding, None, "Review board", elements, [document.asset_sha256])
        latest = save_board(binding, first.revision_sha256, "Saved review board", elements, [document.asset_sha256])
        program_sheet = {"schema": "ProgramSheet@1", "project_id": self.root.name, "spaces": []}
        program_bytes = write_program_sheet_file(binding.repository, program_sheet).path.read_bytes()
        scratch = binding.repository.layout.run(document.run_id).workspaces / "scratch.txt"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("unregistered workspace data", encoding="utf-8")
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*")
                  if path.is_file() and path.suffix != ".lock"}
        source_head = binding.repository.read_head()
        archive_path = Path(self.temp.name) / "project-with-board.zip"
        self.invoke(self.root, "--export-archive", str(archive_path))
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["transfer"]["run_ids"], ["studio-board", "studio-documents"])
            self.assertNotIn("project/" + scratch.relative_to(self.root).as_posix(), archive.namelist())
        restored_root = Path(self.temp.name) / "board-restored" / self.root.name
        self.invoke(restored_root, "--restore-archive", str(archive_path))
        restored = ProjectBinding.open(StudioSettings(project_dir=restored_root, cad_export="off"))
        self.assertEqual(read_board(restored), latest)
        self.assertEqual(list_documents(restored), list_documents(binding))
        registered, restored_bytes = document_bytes(restored, document.run_id, document.asset_sha256)
        self.assertEqual(registered, document)
        self.assertEqual(restored_bytes, original.getvalue())
        restored_program = load_program_sheet_file(restored.repository)
        self.assertEqual(restored_program.payload, program_sheet)
        self.assertEqual(restored_program.path.read_bytes(), program_bytes)
        self.assertEqual(restored.repository.read_head(), source_head)
        self.assertEqual(restored.repository.read_design_branches(), {})
        self.assertEqual(restored.record_refs("studio-board"), binding.record_refs("studio-board"))
        restored.repository.verify()
        self.assertEqual(before, {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*")
                                  if path.is_file() and path.suffix != ".lock"})

        original_path = binding.repository.layout.resolve_relative(
            f"objects/sha256/{document.asset_sha256[:2]}/{document.asset_sha256}"
        )
        original_path.unlink()
        missing_source_archive = Path(self.temp.name) / "missing-source.zip"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke(self.root, "--export-archive", str(missing_source_archive))
        self.assertFalse(missing_source_archive.exists())

    def test_project_archive_refuses_unlisted_member_and_nonempty_restore_target(self) -> None:
        self.create()
        archive_path = Path(self.temp.name) / "project-new.monkeyhub.zip"
        self.invoke(self.root, "--export-archive", str(archive_path))
        injected = Path(self.temp.name) / "project-new-injected.monkeyhub.zip"
        with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(injected, "w") as target:
            for info in source.infolist():
                target.writestr(info, source.read(info.filename))
            target.writestr("project/credentials.txt", b"must not be accepted")

        injected_root = Path(self.temp.name) / "injected" / "project-new"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke(injected_root, "--restore-archive", str(injected))
        self.assertFalse(injected_root.exists())

        occupied_root = Path(self.temp.name) / "occupied" / "project-new"
        occupied_root.mkdir(parents=True)
        marker = occupied_root / "keep.txt"
        marker.write_text("do not overwrite", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke(occupied_root, "--restore-archive", str(archive_path))
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not overwrite")
        self.assertEqual([path.name for path in occupied_root.iterdir()], ["keep.txt"])

    def test_project_archive_refuses_matching_unfinished_restore_target(self) -> None:
        self.create()
        archive_path = Path(self.temp.name) / "project-new.zip"
        self.invoke(self.root, "--export-archive", str(archive_path))
        occupied_root = Path(self.temp.name) / "unfinished" / self.root.name
        occupied_root.mkdir(parents=True)
        manifest_bytes = (self.root / "project.json").read_bytes()
        (occupied_root / "project.json").write_bytes(manifest_bytes)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke(occupied_root, "--restore-archive", str(archive_path))
        self.assertEqual({path.name: path.read_bytes() for path in occupied_root.iterdir()},
                         {"project.json": manifest_bytes})


if __name__ == "__main__":
    unittest.main()
