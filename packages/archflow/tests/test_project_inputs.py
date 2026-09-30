"""ADR-007: one module reads the work-in-progress inputs, at layout-owned paths.

The designer's ``input/runner/state-record.json`` and ``input/runner/seats.json``
are read here and nowhere else. Absent and unreadable are separate refusals
because they are separate problems for whoever has to fix them, and each names
the file it wanted. A record that claims its own stage is refused: a stage is
the run's.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from archflow.project.inputs import (
    AuthoredRecordInvalid,
    AuthoredRecordMissing,
    InputsError,
    ProgramSheetInvalid,
    ProgramSheetMissing,
    SeatPackInvalid,
    SeatPackMissing,
    load_authored_record,
    load_program_sheet_file,
    load_seat_pack_file,
    write_program_sheet_file,
    write_seat_pack_file,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.program_sheet import PROGRAM_SHEET_SCHEMA
from archflow.state.state_record import Entity, StateRecord

RECORD = StateRecord(
    "demo",
    "run-1",
    (
        Entity("shaft", "Component@1", {"semantic_kind": "vertical-support", "intent": "one column"}),
        Entity("shaft-array", "Element@1", {"component_id": "shaft", "producer": "column-array"}),
    ),
)

SEATS = {
    "schema": "RunnerSeats@1",
    "commitment_ref": "commitment:demo",
    "provider_identity": {"provider_id": "demo", "model_id": "demo"},
    "seats": [
        {
            "seat_id": "structure",
            "disciplines": ["structure"],
            "phases": ["design_development"],
            "owned_component_ids": ["shaft"],
        }
    ],
}


SHEET = {
    "schema": PROGRAM_SHEET_SCHEMA,
    "project_id": "demo",
    "state_digest": None,
    "departments": [
        {
            "department_id": "public",
            "name": "对外",
            "spaces": [
                {
                    "space_id": "hall",
                    "name": "Hall",
                    "function": "principal-use",
                    "target_area_m2": 24.0,
                    "count": 1,
                    "clear_height_m": None,
                    "level_ids": [],
                    "zone_id": None,
                    "mapped_area_m2": None,
                }
            ],
        }
    ],
    "adjacencies": [],
    "totals": {"target_area_m2": 24.0, "mapped_area_m2": 0.0, "unmapped_spaces": ["hall"]},
    "honesty": [],
}


class ProjectInputsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "demo"
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id="demo", initial_state={"schema": "TestState@1"}
        )
        self.layout = self.repository.layout

    def write(self, path: Path, payload: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    # ---- the layout names the three files, and nothing else may move them
    def test_the_layout_owns_both_paths(self) -> None:
        self.assertEqual(
            self.layout.authored_record,
            self.layout.root / "input" / "runner" / "state-record.json",
        )
        self.assertEqual(
            self.layout.seat_pack,
            self.layout.root / "input" / "runner" / "seats.json",
        )
        self.assertEqual(
            self.layout.program_sheet,
            self.layout.root / "input" / "runner" / "program-sheet.json",
        )

    # ---- the authored record
    def test_a_valid_record_comes_back_with_its_own_content_identity(self) -> None:
        self.write(self.layout.authored_record, RECORD.to_dict())

        authored = load_authored_record(self.repository)

        self.assertEqual(authored.path, self.layout.authored_record)
        self.assertEqual(authored.record.digest, RECORD.digest)
        # The digest is the record's own; nothing here computes a second one.
        self.assertEqual(authored.digest, authored.record.digest)

    def test_an_absent_record_names_the_file_it_wanted(self) -> None:
        with self.assertRaises(AuthoredRecordMissing) as caught:
            load_authored_record(self.repository)

        self.assertIsInstance(caught.exception, InputsError)
        self.assertEqual(caught.exception.path, self.layout.authored_record)
        self.assertIn("input/runner/state-record.json", str(caught.exception))

    def test_a_record_that_is_not_json_is_invalid_not_absent(self) -> None:
        self.layout.authored_record.parent.mkdir(parents=True, exist_ok=True)
        self.layout.authored_record.write_text("{ not json", encoding="utf-8")

        with self.assertRaises(AuthoredRecordInvalid) as caught:
            load_authored_record(self.repository)

        self.assertEqual(caught.exception.path, self.layout.authored_record)
        self.assertIn("input/runner/state-record.json", str(caught.exception))
        self.assertIn("Expecting", str(caught.exception))

    def test_a_record_in_another_encoding_is_undecodable_not_absent(self) -> None:
        """UTF-16 is read strictly: a file that will not decode is the project's."""

        self.layout.authored_record.parent.mkdir(parents=True, exist_ok=True)
        self.layout.authored_record.write_bytes(
            json.dumps(RECORD.to_dict()).encode("utf-16")
        )

        with self.assertRaises(AuthoredRecordInvalid) as caught:
            load_authored_record(self.repository)

        self.assertEqual(caught.exception.path, self.layout.authored_record)
        self.assertIn("utf-8", str(caught.exception))

    def test_a_record_that_parses_and_is_not_a_state_record_is_invalid(self) -> None:
        self.write(self.layout.authored_record, {"schema": "SomethingElse@1"})

        with self.assertRaises(AuthoredRecordInvalid) as caught:
            load_authored_record(self.repository)

        self.assertIn("state record payload malformed", str(caught.exception))

    def test_a_record_claiming_its_own_stage_is_refused(self) -> None:
        """ADR-007: a stage is the run's, stated by the run's own envelope."""

        payload = RECORD.to_dict()
        payload["stage"] = {"workflow_ref": None, "envelope_ref": None, "stage_id": "stage-0"}
        self.write(self.layout.authored_record, payload)

        with self.assertRaises(AuthoredRecordInvalid) as caught:
            load_authored_record(self.repository)

        self.assertIn("stage is the run's", str(caught.exception))

    # ---- the seat pack
    def test_a_valid_seat_pack_comes_back_whole_with_the_bytes_it_was_read_from(
        self,
    ) -> None:
        path = self.write(self.layout.seat_pack, SEATS)

        pack = load_seat_pack_file(self.repository)

        self.assertEqual(pack.path, self.layout.seat_pack)
        # The raw mapping, unparsed: SeatPack@1 is somebody else's to read.
        self.assertEqual(pack.payload, SEATS)
        self.assertEqual(
            pack.sha256, hashlib.sha256(path.read_bytes()).hexdigest()
        )

    def test_an_absent_seat_pack_names_the_file_it_wanted(self) -> None:
        with self.assertRaises(SeatPackMissing) as caught:
            load_seat_pack_file(self.repository)

        self.assertIsInstance(caught.exception, InputsError)
        self.assertEqual(caught.exception.path, self.layout.seat_pack)
        self.assertIn("input/runner/seats.json", str(caught.exception))

    def test_a_seat_pack_that_is_not_json_is_invalid_not_absent(self) -> None:
        self.layout.seat_pack.parent.mkdir(parents=True, exist_ok=True)
        self.layout.seat_pack.write_text("[", encoding="utf-8")

        with self.assertRaises(SeatPackInvalid) as caught:
            load_seat_pack_file(self.repository)

        self.assertEqual(caught.exception.path, self.layout.seat_pack)
        self.assertIn("input/runner/seats.json", str(caught.exception))

    def test_a_seat_pack_that_is_not_an_object_is_invalid(self) -> None:
        self.write(self.layout.seat_pack, [SEATS])

        with self.assertRaises(SeatPackInvalid) as caught:
            load_seat_pack_file(self.repository)

        self.assertIn("JSON object", str(caught.exception))

    # ---- the seat pack writer: the continuation reads what the run was made under
    def test_the_written_seat_pack_reads_back_as_itself_with_lf_endings(self) -> None:
        written = write_seat_pack_file(self.repository, SEATS)

        raw = self.layout.seat_pack.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(written.path, self.layout.seat_pack)
        self.assertEqual(written.payload, SEATS)
        self.assertEqual(written.sha256, hashlib.sha256(raw).hexdigest())
        read = load_seat_pack_file(self.repository)
        self.assertEqual(read.payload, SEATS)
        self.assertEqual(read.sha256, written.sha256)

    def test_writing_the_same_seat_pack_twice_leaves_the_same_bytes(self) -> None:
        first = write_seat_pack_file(self.repository, SEATS)
        second = write_seat_pack_file(
            self.repository, dict(reversed(list(SEATS.items())))
        )

        self.assertEqual(second.sha256, first.sha256)

    def test_writing_the_seat_pack_replaces_the_authored_file_at_the_same_path(
        self,
    ) -> None:
        """The blocker: a multi-seat pack on disk, a single-seat run to continue."""

        old_path = self.write(self.layout.seat_pack, SEATS)
        old_sha = hashlib.sha256(old_path.read_bytes()).hexdigest()
        single = {
            **SEATS,
            "seats": [
                {
                    "seat_id": "building",
                    "disciplines": ["architecture"],
                    "phases": ["design_development"],
                    "owned_component_ids": ["building"],
                }
            ],
        }

        written = write_seat_pack_file(self.repository, single)

        self.assertEqual(written.path, old_path)
        self.assertNotEqual(written.sha256, old_sha)
        read = load_seat_pack_file(self.repository)
        self.assertEqual(read.payload, single)
        self.assertEqual(read.payload["seats"][0]["owned_component_ids"], ["building"])
        self.assertEqual(
            sorted(p.name for p in self.layout.seat_pack.parent.iterdir()),
            ["seats.json"],
        )

    def test_writing_the_seat_pack_touches_nothing_outside_its_own_file(self) -> None:
        """A work-in-progress write: HEAD, the other inputs and the retained areas stay."""

        self.write(self.layout.authored_record, RECORD.to_dict())
        write_program_sheet_file(self.repository, SHEET)
        head_before = self.layout.head.read_bytes()
        record_before = self.layout.authored_record.read_bytes()
        sheet_before = self.layout.program_sheet.read_bytes()
        before = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and "input" not in p.parts
        )

        write_seat_pack_file(self.repository, SEATS)

        after = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and "input" not in p.parts
        )
        self.assertEqual(after, before)
        self.assertEqual(self.layout.head.read_bytes(), head_before)
        self.assertEqual(self.layout.authored_record.read_bytes(), record_before)
        self.assertEqual(self.layout.program_sheet.read_bytes(), sheet_before)

    def test_writing_a_seat_pack_that_is_not_a_mapping_is_refused_before_the_file(
        self,
    ) -> None:
        with self.assertRaises(SeatPackInvalid) as caught:
            write_seat_pack_file(self.repository, [SEATS])  # type: ignore[arg-type]

        self.assertIsInstance(caught.exception, InputsError)
        self.assertEqual(caught.exception.path, self.layout.seat_pack)
        self.assertIn("JSON object", str(caught.exception))
        self.assertIn("list", str(caught.exception))
        self.assertFalse(self.layout.seat_pack.exists())

    def test_writing_a_seat_pack_that_will_not_serialise_leaves_the_file_as_it_was(
        self,
    ) -> None:
        old_path = self.write(self.layout.seat_pack, SEATS)
        old_bytes = old_path.read_bytes()

        with self.assertRaises(SeatPackInvalid) as caught:
            write_seat_pack_file(self.repository, {**SEATS, "seats": {"a", "b"}})

        self.assertEqual(caught.exception.path, self.layout.seat_pack)
        self.assertIn("input/runner/seats.json", str(caught.exception))
        self.assertIn("not JSON serializable", str(caught.exception))
        self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_a_seat_pack_that_cannot_be_written_is_a_refusal_naming_the_file(
        self,
    ) -> None:
        # A directory where the file goes: write_bytes fails with an OSError.
        self.layout.seat_pack.mkdir(parents=True)

        with self.assertRaises(SeatPackInvalid) as caught:
            write_seat_pack_file(self.repository, SEATS)

        self.assertIsInstance(caught.exception, InputsError)
        self.assertEqual(caught.exception.path, self.layout.seat_pack)
        self.assertIn("input/runner/seats.json", str(caught.exception))

    # ---- the program sheet: the other authored file the studio may write
    def test_an_absent_program_sheet_names_the_file_it_wanted(self) -> None:
        with self.assertRaises(ProgramSheetMissing) as caught:
            load_program_sheet_file(self.repository)

        self.assertIsInstance(caught.exception, InputsError)
        self.assertEqual(caught.exception.path, self.layout.program_sheet)
        self.assertIn("input/runner/program-sheet.json", str(caught.exception))

    def test_a_program_sheet_claiming_another_schema_is_refused(self) -> None:
        self.write(self.layout.program_sheet, {**SHEET, "schema": "SeatPack@1"})

        with self.assertRaises(ProgramSheetInvalid) as caught:
            load_program_sheet_file(self.repository)

        self.assertIn(PROGRAM_SHEET_SCHEMA, str(caught.exception))
        self.assertIn("SeatPack@1", str(caught.exception))

    def test_a_program_sheet_that_is_not_an_object_is_invalid(self) -> None:
        self.write(self.layout.program_sheet, [SHEET])

        with self.assertRaises(ProgramSheetInvalid) as caught:
            load_program_sheet_file(self.repository)

        self.assertIn("JSON object", str(caught.exception))

    def test_writing_a_payload_of_another_schema_is_refused_before_the_file(
        self,
    ) -> None:
        with self.assertRaises(ProgramSheetInvalid):
            write_program_sheet_file(self.repository, {"schema": "SeatPack@1"})

        self.assertFalse(self.layout.program_sheet.exists())

    def test_the_written_sheet_reads_back_as_itself_with_lf_endings(self) -> None:
        written = write_program_sheet_file(self.repository, SHEET)

        raw = self.layout.program_sheet.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(written.sha256, hashlib.sha256(raw).hexdigest())
        read = load_program_sheet_file(self.repository)
        self.assertEqual(read.payload, SHEET)
        self.assertEqual(read.sha256, written.sha256)

    def test_writing_the_same_sheet_twice_leaves_the_same_bytes(self) -> None:
        first = write_program_sheet_file(self.repository, SHEET)
        second = write_program_sheet_file(
            self.repository, dict(reversed(list(SHEET.items())))
        )

        self.assertEqual(second.sha256, first.sha256)

    def test_writing_the_sheet_touches_nothing_outside_input(self) -> None:
        """A work-in-progress write is not a project write."""

        before = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and "input" not in p.parts
        )

        write_program_sheet_file(self.repository, SHEET)

        after = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and "input" not in p.parts
        )
        self.assertEqual(after, before)

    def test_reading_neither_file_writes_anything_into_the_project(self) -> None:
        """Work in progress is not retained: reading it leaves no record."""

        self.write(self.layout.authored_record, RECORD.to_dict())
        self.write(self.layout.seat_pack, SEATS)
        before = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and "input" not in p.parts
        )

        load_authored_record(self.repository)
        load_seat_pack_file(self.repository)

        after = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and "input" not in p.parts
        )
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
