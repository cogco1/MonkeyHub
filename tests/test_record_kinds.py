"""One table of record kinds, and one rule for reading a record's file name.

``put_json`` accepted any identifier as a record kind, and three readers each
had their own way of getting the kind back out of a file name. These tests
hold both halves of the fix: the table decides what may be written, and
``archflow.project.refs`` decides how a name is read.

Reads stay unrestricted on purpose. A retained run from an archived lane
carries kinds the spine never writes, and it has to stay readable (ADR-004).
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    COMPONENT_TEMPLATE,
    RECORD_KINDS,
    RESEARCH_EVIDENCE_LEDGER,
    STAGE_CLOSURE,
    STATE_RECORD,
    geometry_proposal_completion,
    geometry_proposal_round,
    is_registered,
    require_registered,
    stage_geometry_program,
)
from archflow.project.refs import (
    ProjectRecordRef,
    parse_record_file_name,
    record_file_name,
    record_ref_from_uri,
)
from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectRepositoryError,
)


PROJECT_ID = "demo"
SHA = "a" * 64


class RecordKindTableTests(unittest.TestCase):
    def test_every_exact_kind_in_the_table_is_registered(self) -> None:
        exact = [
            entry.kind
            for entry in RECORD_KINDS.values()
            if entry.kind_pattern is None
        ]
        self.assertTrue(exact)
        for kind in exact:
            with self.subTest(kind=kind):
                self.assertTrue(is_registered(kind))
                self.assertIs(require_registered(kind), RECORD_KINDS[kind])

    def test_a_pattern_kind_is_registered_by_what_a_writer_writes(self) -> None:
        # The table key is the shape a person reads; what a writer writes is
        # the formatted kind, and only that is accepted.
        for template, written in (
            ("geometry-proposal-round-NN", geometry_proposal_round(7)),
            ("geometry-proposal-completion-NN", geometry_proposal_completion(0)),
            ("<identifier>-geometry-program", stage_geometry_program("stage-0")),
        ):
            with self.subTest(template=template):
                self.assertIn(template, RECORD_KINDS)
                self.assertFalse(is_registered(template))
                self.assertTrue(is_registered(written))
                self.assertIs(
                    require_registered(written), RECORD_KINDS[template]
                )

    def test_a_pattern_matches_its_own_shape_and_nothing_else(self) -> None:
        self.assertEqual(geometry_proposal_round(3), "geometry-proposal-round-03")
        self.assertFalse(is_registered("geometry-proposal-round-3"))
        self.assertFalse(is_registered("geometry-proposal-round-003"))
        self.assertFalse(is_registered("geometry-proposal-round"))

    def test_an_exact_kind_wins_over_a_pattern_that_would_also_match(self) -> None:
        # "seat-geometry-program" also matches the stage-program pattern; the
        # exact entry is the one that answers, with its own note.
        entry = require_registered("seat-geometry-program")
        self.assertEqual(entry.kind, "seat-geometry-program")
        self.assertIsNone(entry.kind_pattern)

    def test_the_reserved_kinds_are_accepted_and_say_they_are_reserved(self) -> None:
        for kind in (RESEARCH_EVIDENCE_LEDGER, COMPONENT_TEMPLATE):
            with self.subTest(kind=kind):
                self.assertTrue(is_registered(kind))
                note = require_registered(kind).note
                self.assertTrue(
                    "reserved" in note or "no spine module writes one" in note,
                    note,
                )

    def test_the_stage_closure_kind_no_longer_says_it_is_reserved(self) -> None:
        """The runner writes it now (ADR-007 rule 3), so the note says so."""

        note = require_registered(STAGE_CLOSURE).note
        self.assertNotIn("reserved", note)
        self.assertIn("the runner writes", note)

    def test_an_unregistered_kind_is_refused_and_the_table_is_named(self) -> None:
        self.assertFalse(is_registered("architectural-usability-receipt"))
        with self.assertRaises(ValueError) as raised:
            require_registered("architectural-usability-receipt")
        message = str(raised.exception)
        self.assertIn("architectural-usability-receipt", message)
        self.assertIn("record_kinds", message)

    def test_every_entry_declares_where_it_lands(self) -> None:
        areas = {area.value for area in PersistenceArea}
        for entry in RECORD_KINDS.values():
            with self.subTest(kind=entry.kind):
                self.assertIn(entry.area, areas)
                self.assertTrue(entry.note.strip())


class RecordFileNameTests(unittest.TestCase):
    def test_a_name_round_trips_through_the_one_rule(self) -> None:
        name = record_file_name(STATE_RECORD, SHA)
        self.assertEqual(name, f"state-record-{SHA}.json")
        self.assertEqual(parse_record_file_name(name), (STATE_RECORD, SHA))

    def test_the_longest_kind_before_the_digest_wins(self) -> None:
        # The parser cannot know whether "seat-round-receipt-extra" is a kind
        # or a kind plus a word: it returns everything before the digest, and
        # the caller asks the table whether that is a kind the spine writes.
        kind, sha = parse_record_file_name(f"seat-round-receipt-extra-{SHA}.json")
        self.assertEqual((kind, sha), ("seat-round-receipt-extra", SHA))
        self.assertFalse(is_registered(kind))
        self.assertTrue(is_registered("seat-round-receipt"))

    def test_a_name_that_is_not_a_record_is_refused(self) -> None:
        for name in (
            "state-record.json",
            f"state-record-{SHA[:63]}.json",
            f"state-record-{SHA.upper()}.json",
            f"state-record-{SHA}.txt",
            f"{SHA}.json",
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    parse_record_file_name(name)

    def test_a_ref_reports_its_own_kind(self) -> None:
        ref = ProjectRecordRef(
            project_id=PROJECT_ID,
            relative_path=f"runs/r1/records/{record_file_name(STATE_RECORD, SHA)}",
            sha256=SHA,
        )
        self.assertEqual(ref.record_kind, STATE_RECORD)

    def test_a_ref_that_is_not_a_record_refuses_to_name_a_kind(self) -> None:
        ref = ProjectRecordRef(
            project_id=PROJECT_ID,
            relative_path="runs/r1/run.json",
            sha256=SHA,
        )
        with self.assertRaises(ValueError):
            ref.record_kind


class RecordUriTests(unittest.TestCase):
    def test_a_record_uri_reads_back_as_its_ref(self) -> None:
        relative = f"runs/r1/records/{record_file_name(STATE_RECORD, SHA)}"
        ref = ProjectRecordRef(
            project_id=PROJECT_ID, relative_path=relative, sha256=SHA
        )
        self.assertEqual(record_ref_from_uri(ref.uri, PROJECT_ID), ref)

    def test_a_uri_from_another_project_or_scheme_is_refused(self) -> None:
        relative = f"runs/r1/records/{record_file_name(STATE_RECORD, SHA)}"
        for uri in (
            f"project://other/{relative}",
            f"file://{PROJECT_ID}/{relative}",
            f"project://{PROJECT_ID}/runs/r1/run.json",
        ):
            with self.subTest(uri=uri):
                with self.assertRaises(ValueError):
                    record_ref_from_uri(uri, PROJECT_ID)


class PutJsonRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = FilesystemProjectRepository.initialize(
            Path(temporary.name) / PROJECT_ID,
            project_id=PROJECT_ID,
            initial_state={"schema": "TestState@1"},
        )
        self.run = self.repository.create_run("run-001")
        self.destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=self.run.run_id
        )

    def put(self, kind: str) -> ProjectRecordRef:
        return self.repository.put_json(
            run=self.run,
            destination=self.destination,
            record_kind=kind,
            payload={"schema": "TestPayload@1", "kind": kind},
        )

    def test_a_registered_kind_is_written_under_its_own_name(self) -> None:
        for kind in (
            STATE_RECORD,
            STAGE_CLOSURE,
            RESEARCH_EVIDENCE_LEDGER,
            geometry_proposal_round(1),
            geometry_proposal_completion(12),
        ):
            with self.subTest(kind=kind):
                ref = self.put(kind)
                self.assertEqual(ref.record_kind, kind)
                self.assertEqual(self.repository.load_json(ref)["kind"], kind)

    def test_an_unregistered_kind_is_refused_and_nothing_is_written(self) -> None:
        with self.assertRaises(ProjectRepositoryError) as raised:
            self.put("architectural-usability-receipt")
        message = str(raised.exception)
        self.assertIn("architectural-usability-receipt", message)
        self.assertIn("record_kinds", message)
        self.assertEqual(
            list(
                Path(self.repository.layout.run(self.run.run_id).records).glob(
                    "architectural-usability-receipt-*.json"
                )
            ),
            [],
        )

    def test_a_retained_record_of_an_unregistered_kind_stays_readable(self) -> None:
        # An archived lane's run is opened, not rewritten: reads never ask the
        # table, so a kind the spine no longer writes is still loaded and
        # listed (ADR-004).
        records = Path(self.repository.layout.run(self.run.run_id).records)
        payload = b'{\n  "schema": "Legacy@1"\n}\n'
        digest = hashlib.sha256(payload).hexdigest()
        legacy = records / record_file_name(
            "architectural-usability-receipt", digest
        )
        legacy.write_bytes(payload)
        listed = self.repository.list_json(
            run=self.run, destination=self.destination
        )
        kinds = {ref.record_kind for ref in listed}
        self.assertIn("architectural-usability-receipt", kinds)
        loaded = [
            self.repository.load_json(ref)
            for ref in listed
            if ref.record_kind == "architectural-usability-receipt"
        ]
        self.assertEqual(loaded, [{"schema": "Legacy@1"}])


if __name__ == "__main__":
    unittest.main()
