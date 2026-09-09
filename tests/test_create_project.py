from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from archflow.project.inputs import load_authored_record, load_seat_pack_file
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


if __name__ == "__main__":
    unittest.main()
