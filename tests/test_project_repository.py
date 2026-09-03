from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.project.repository import FilesystemProjectRepository, ProjectAlreadyExists, ProjectHeadLocked, ProjectIntegrityError, PromotionAuthorityError, StaleProjectHead
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.project.digests import project_state_sha256


_FOREIGN_LOCK_HOLDER = """
import os
import sys
from pathlib import Path

handle = Path(sys.argv[1]).open("a+b")
if os.name == "nt":
    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
print("LOCKED", flush=True)
sys.stdin.readline()
if os.name == "nt":
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
handle.close()
print("RELEASED", flush=True)
"""


def _decision(
    repository: FilesystemProjectRepository,
    run: RunRef,
    *,
    status: str,
    candidate_ref: str = "project://project-a/runs/candidate-001",
) -> ProjectRecordRef:
    return repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW,
            run_id=run.run_id,
        ),
        record_kind=f"decision-{status}",
        payload={
            "schema": "PromotionDecision@1",
            "status": status,
            "project_id": run.project_id,
            "run_id": run.run_id,
            "checked_state": {
                "project_id": run.base.project_id,
                "version": run.base.version,
                "state_sha256": run.base.require_digest(),
            },
            "candidate_ref": candidate_ref,
        },
    )


class ProjectRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project-a"
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id="project-a",
            initial_state={"phase": "request", "commitments": []},
        )

    def test_initialization_is_immutable_and_reopens_with_verified_head(self) -> None:
        before = self.repository.read_head()
        reopened = FilesystemProjectRepository.open(self.root)
        state = {"phase": "request", "commitments": []}

        self.assertEqual(reopened.read_head(), before)
        self.assertEqual(reopened.load_current_state(), state)
        self.assertEqual(before.require_digest(), project_state_sha256(state))
        head_document = json.loads(
            self.repository.layout.head.read_text(encoding="utf-8")
        )
        self.assertEqual(head_document["schema"], "ProjectHead@2")
        self.assertNotEqual(
            before.require_digest(),
            head_document["snapshot"]["sha256"],
        )
        self.assertEqual(
            reopened.load_manifest().format_version,
            2,
        )
        self.assertIsNotNone(before.state_sha256)
        with self.assertRaises(ProjectAlreadyExists):
            FilesystemProjectRepository.initialize(
                self.root,
                project_id="project-a",
                initial_state={},
            )

    def test_declared_semantic_digest_and_identity_mismatch_fail(self) -> None:
        bad_digest_root = Path(self.temporary.name) / "bad-digest"
        with self.assertRaisesRegex(
            ProjectIntegrityError,
            "semantic digest",
        ):
            FilesystemProjectRepository.initialize(
                bad_digest_root,
                project_id="bad-digest",
                initial_state={
                    "schema": "CanonicalState@1",
                    "project_id": "bad-digest",
                    "version": 0,
                    "state_sha256": "0" * 64,
                },
            )
        bad_identity_root = Path(self.temporary.name) / "bad-identity"
        with self.assertRaisesRegex(
            ProjectIntegrityError,
            "canonical identity",
        ):
            FilesystemProjectRepository.initialize(
                bad_identity_root,
                project_id="bad-identity",
                initial_state={
                    "schema": "CanonicalState@1",
                    "project_id": "another-project",
                    "version": 0,
                },
            )

    def test_record_and_artifact_ingestion_are_content_addressed(self) -> None:
        run = self.repository.create_run("run-001")
        destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        )
        first = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="observation",
            payload={"schema": "Observation@1", "value": 1},
        )
        duplicate = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="observation",
            payload={"schema": "Observation@1", "value": 1},
        )
        artifact = self.repository.ingest(
            run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id="artifact-001",
            media_type="application/octet-stream",
            source=io.BytesIO(b"voxel-data"),
        )

        self.assertEqual(first, duplicate)
        self.assertTrue(first.uri.startswith("project://project-a/"))
        self.assertNotIn(str(self.root), first.uri)
        self.assertIsInstance(artifact, ProjectArtifactRef)
        self.assertEqual(
            self.repository.layout.resolve_record(artifact).read_bytes(),
            b"voxel-data",
        )

    def test_fixed_run_batch_creates_all_siblings_or_none(self) -> None:
        base = self.repository.read_head()
        run_a, run_b = self.repository.create_run_batch(
            ("run-a", "run-b"),
            base=base,
            require_current_base=True,
        )

        self.assertEqual(run_a, self.repository.load_run("run-a"))
        self.assertEqual(run_b, self.repository.load_run("run-b"))
        self.assertEqual(base, run_a.base)
        self.assertEqual(base, run_b.base)

    def test_fixed_run_batch_precheck_does_not_leave_first_sibling(self) -> None:
        self.repository.create_run("run-existing")

        with self.assertRaises(ProjectAlreadyExists):
            self.repository.create_run_batch(
                ("run-new", "run-existing"),
                base=self.repository.read_head(),
                require_current_base=True,
            )

        self.assertFalse(self.repository.layout.run("run-new").root.exists())

    def test_fixed_run_batch_rolls_back_new_roots_on_second_write_failure(self) -> None:
        from archflow.project import repository as repository_module
        original = repository_module._write_immutable

        def fail_second_manifest(path: Path, data: bytes) -> None:
            if path == self.repository.layout.run("run-b").manifest:
                raise OSError("second manifest probe")
            original(path, data)

        with patch(
            "archflow.project.repository._write_immutable",
            side_effect=fail_second_manifest,
        ):
            with self.assertRaisesRegex(OSError, "second manifest probe"):
                self.repository.create_run_batch(
                    ("run-a", "run-b"),
                    base=self.repository.read_head(),
                    require_current_base=True,
                )

        self.assertFalse(self.repository.layout.run("run-a").root.exists())
        self.assertFalse(self.repository.layout.run("run-b").root.exists())

    def test_cross_project_absolute_and_escape_references_fail(self) -> None:
        run = self.repository.create_run("run-001")
        with self.assertRaises(ValueError):
            self.repository.put_json(
                run=RunRef(
                    "project-b",
                    "run-001",
                    ProjectVersionRef("project-b", 0, "0" * 64),
                ),
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id="run-001",
                ),
                record_kind="record",
                payload={},
            )
        with self.assertRaises(ValueError):
            ProjectRecordRef(
                project_id="project-a",
                relative_path="C:/absolute.json",
                sha256="0" * 64,
            )
        with self.assertRaises(ValueError):
            ProjectRecordRef(
                project_id="project-a",
                relative_path="../escape.json",
                sha256="0" * 64,
            )
        with self.assertRaises(PromotionAuthorityError):
            self.repository.put_json(
                run=run,
                destination=PersistenceDestination(PersistenceArea.EVENT),
                record_kind="fake-event",
                payload={},
            )

    def test_rejected_decision_cannot_prepare_or_advance_head(self) -> None:
        run = self.repository.create_run("run-rejected")
        before = self.repository.read_head()
        rejected = _decision(self.repository, run, status="rejected")

        with self.assertRaises(PromotionAuthorityError):
            self.repository.prepare_transition(
                run=run,
                expected=before,
                replacement_state={"phase": "candidate"},
                decision_receipt=rejected,
            )
        self.assertEqual(self.repository.read_head(), before)

    def test_exact_base_cas_rejects_concurrent_stale_writer(self) -> None:
        base = self.repository.read_head()
        run_a = self.repository.create_run("run-a", base=base)
        run_b = self.repository.create_run("run-b", base=base)
        prepared_a = self.repository.prepare_transition(
            run=run_a,
            expected=base,
            replacement_state={"selected": "a"},
            decision_receipt=_decision(self.repository, run_a, status="accepted"),
        )
        prepared_b = self.repository.prepare_transition(
            run=run_b,
            expected=base,
            replacement_state={"selected": "b"},
            decision_receipt=_decision(self.repository, run_b, status="accepted"),
        )

        accepted = self.repository.compare_and_swap(
            expected=prepared_a.expected,
            event=prepared_a.event,
            replacement=prepared_a.replacement,
        )
        with self.assertRaises(StaleProjectHead):
            self.repository.compare_and_swap(
                expected=prepared_b.expected,
                event=prepared_b.event,
                replacement=prepared_b.replacement,
            )

        self.assertEqual(self.repository.read_head(), accepted)
        self.assertEqual(self.repository.load_current_state(), {"selected": "a"})

    def test_concurrent_head_readers_survive_cas_replacement(self) -> None:
        """HEAD reads racing os.replace must not leak PermissionError or
        misreport a healthy project as corrupt (Windows sharing violation)."""

        errors: list[BaseException] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    self.repository.read_head()
                    self.repository.load_current_state()
                except BaseException as exc:  # noqa: BLE001 - assert below
                    errors.append(exc)
                    return

        threads = [
            threading.Thread(target=reader, name=f"head-reader-{index}")
            for index in range(4)
        ]
        for thread in threads:
            thread.start()
        commits = 12
        try:
            for index in range(commits):
                run = self.repository.create_run(f"concurrent-{index:03d}")
                prepared = self.repository.prepare_transition(
                    run=run,
                    expected=run.base,
                    replacement_state={"step": index},
                    decision_receipt=_decision(
                        self.repository,
                        run,
                        status="accepted",
                    ),
                )
                self.repository.compare_and_swap(
                    expected=prepared.expected,
                    event=prepared.event,
                    replacement=prepared.replacement,
                )
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(self.repository.read_head().version, commits)
        self.repository.verify()

    def test_head_cas_is_exclusive_across_processes(self) -> None:
        base = self.repository.read_head()
        run = self.repository.create_run("run-locked", base=base)
        prepared = self.repository.prepare_transition(
            run=run,
            expected=base,
            replacement_state={"selected": "locked"},
            decision_receipt=_decision(self.repository, run, status="accepted"),
        )

        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _FOREIGN_LOCK_HOLDER,
                str(self.root / "HEAD.lock"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "LOCKED")
            with patch(
                "archflow.project.repository._HEAD_LOCK_TIMEOUT_SECONDS",
                0.3,
            ):
                with self.assertRaises(ProjectHeadLocked):
                    self.repository.compare_and_swap(
                        expected=prepared.expected,
                        event=prepared.event,
                        replacement=prepared.replacement,
                    )
            self.assertEqual(self.repository.read_head(), base)
            child.stdin.write("\n")
            child.stdin.flush()
            self.assertEqual(child.stdout.readline().strip(), "RELEASED")
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=10)
            finally:
                child.stdout.close()

        accepted = self.repository.compare_and_swap(
            expected=prepared.expected,
            event=prepared.event,
            replacement=prepared.replacement,
        )
        self.assertEqual(self.repository.read_head(), accepted)
        self.assertEqual(
            self.repository.load_current_state(),
            {"selected": "locked"},
        )

    def test_uncommitted_crash_artifacts_are_reported_but_not_loaded(self) -> None:
        base = self.repository.read_head()
        run = self.repository.create_run("run-crash", base=base)
        prepared = self.repository.prepare_transition(
            run=run,
            expected=base,
            replacement_state={"uncommitted": True},
            decision_receipt=_decision(self.repository, run, status="accepted"),
        )

        reopened = FilesystemProjectRepository.open(self.root)
        report = reopened.verify()

        self.assertEqual(reopened.read_head(), base)
        self.assertEqual(
            set(report.orphan_paths),
            {
                prepared.event.relative_path,
                prepared.replacement.relative_path,
            },
        )
        self.assertEqual(
            reopened.load_current_state(),
            {"phase": "request", "commitments": []},
        )


if __name__ == "__main__":
    unittest.main()
