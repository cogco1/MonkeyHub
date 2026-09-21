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

from archflow.project.repository import FilesystemProjectRepository, ProjectAlreadyExists, ProjectHeadLocked, ProjectIntegrityError, PromotionAuthorityError, StaleDesignBranch, StaleProjectHead
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, PROMOTION_DECISION, STATE_RECORD
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
        record_kind=PROMOTION_DECISION,
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
    def test_initial_inputs_retry_after_interruption_and_preserve_existing_project(self) -> None:
        head = self.repository.read_head()
        run = self.repository.create_run("uploaded-documents")
        before = self.repository.layout.head.read_bytes()
        args = dict(expected_head=head, expected_record=None,
                    authored_record={"initial": "model"}, seat_pack={"seats": ["modeler"]})
        with patch("archflow.project.repository._replace_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.repository.initialize_authored_inputs(**args)
        self.assertFalse(self.repository.layout.authored_record.exists())
        self.assertTrue(self.repository.initialize_authored_inputs(**args))
        self.assertFalse(self.repository.initialize_authored_inputs(**args))
        self.assertEqual(self.repository.layout.head.read_bytes(), before)
        self.assertEqual(self.repository.load_run(run.run_id), run)
        self.assertEqual(FilesystemProjectRepository.open(self.root).read_head(), head)

    def test_initial_inputs_refuse_competing_authored_content_and_seats(self) -> None:
        args = dict(expected_head=self.repository.read_head(), expected_record=None,
                    authored_record={"initial": "model"}, seat_pack={"seats": ["modeler"]})
        self.repository.initialize_authored_inputs(**args)
        before = self.repository.layout.authored_record.read_bytes()
        with self.assertRaises(ProjectAlreadyExists):
            self.repository.initialize_authored_inputs(**{**args, "authored_record": {"other": 1}})
        with self.assertRaises(ProjectAlreadyExists):
            self.repository.initialize_authored_inputs(**{**args, "seat_pack": {"other": 1}})
        with self.assertRaises(StaleProjectHead):
            self.repository.initialize_authored_inputs(**{
                **args, "expected_head": ProjectVersionRef("project-a", 0, "0" * 64),
            })
        self.assertEqual(self.repository.layout.authored_record.read_bytes(), before)

    def stage(self, name: str, parent: ProjectRecordRef | None = None) -> ProjectRecordRef:
        run = self.repository.create_run(name)
        return self.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=name),
            record_kind=DESIGN_STAGE,
            payload={"schema": "DesignStage@1", "candidate_id": name,
                     "parent_stage": None if parent is None else parent.to_dict()},
        )

    def branch(self, branch_id: str, fork: ProjectRecordRef, head: ProjectRecordRef,
               parent: str | None = None) -> dict:
        return {"branch_id": branch_id, "parent_branch": parent,
                "fork_stage": fork.to_dict(), "head_stage": head.to_dict()}

    def test_design_history_survives_restart_without_issuing_or_hidden_runs(self) -> None:
        issued = self.repository.read_head()
        self.assertEqual(self.repository.read_design_branches(), {})
        self.assertFalse(self.repository.layout.design_branches.exists())
        s0 = self.stage("initial")
        s1 = self.stage("cabinet-b", s0)
        unaccepted = self.stage("uncommitted", s0)
        self.repository.compare_and_swap_design_branch(
            branch_id="main", expected_head=None, branch=self.branch("main", s0, s0))
        self.repository.compare_and_swap_design_branch(
            branch_id="main", expected_head=s0, branch=self.branch("main", s0, s1))
        self.repository.compare_and_swap_design_branch(
            branch_id="cabinet-alt-a", expected_head=None,
            branch=self.branch("cabinet-alt-a", s0, s0, "main"))
        reopened = FilesystemProjectRepository.open(self.root)
        self.assertEqual(reopened.read_head(), issued)
        branches = reopened.read_design_branches()
        self.assertEqual(branches["main"]["head_stage"], s1.to_dict())
        self.assertEqual(branches["cabinet-alt-a"]["head_stage"], s0.to_dict())
        self.assertEqual({p.name for p in reopened.layout.runs.iterdir()},
                         {"initial", "cabinet-b", "uncommitted"})
        report = reopened.verify()
        self.assertIn(s0.relative_path, report.reachable_paths)
        self.assertIn(s1.relative_path, report.reachable_paths)
        self.assertIn(unaccepted.relative_path, report.orphan_paths)

    def test_design_head_cas_rejects_competing_accept_and_fork_rewrite(self) -> None:
        s0 = self.stage("initial")
        a, b = self.stage("a", s0), self.stage("b", s0)
        self.repository.compare_and_swap_design_branch(
            branch_id="main", expected_head=None, branch=self.branch("main", s0, s0))
        competitor = FilesystemProjectRepository.open(self.root)
        self.repository.compare_and_swap_design_branch(
            branch_id="main", expected_head=s0, branch=self.branch("main", s0, a))
        with self.assertRaises(StaleDesignBranch):
            competitor.compare_and_swap_design_branch(
                branch_id="main", expected_head=s0, branch=self.branch("main", s0, b))
        with self.assertRaises(ProjectIntegrityError):
            competitor.compare_and_swap_design_branch(
                branch_id="main", expected_head=a, branch=self.branch("main", b, b))
        self.assertEqual(competitor.read_design_branches()["main"]["head_stage"], a.to_dict())

    def test_design_ref_failed_write_preserves_position_and_prepared_stage(self) -> None:
        s0, s1 = self.stage("initial"), self.stage("next")
        self.repository.compare_and_swap_design_branch(
            branch_id="main", expected_head=None, branch=self.branch("main", s0, s0))
        with patch("archflow.project.repository._replace_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.repository.compare_and_swap_design_branch(
                    branch_id="main", expected_head=s0, branch=self.branch("main", s0, s1))
        reopened = FilesystemProjectRepository.open(self.root)
        self.assertEqual(reopened.read_design_branches()["main"]["head_stage"], s0.to_dict())
        self.assertIn(s1.relative_path, reopened.verify().orphan_paths)

    def test_design_ref_rejects_non_stage_or_tampered_history(self) -> None:
        run = self.repository.create_run("plain")
        record = self.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STATE_RECORD, payload={"value": "not a stage"})
        with self.assertRaises(ProjectIntegrityError):
            self.repository.compare_and_swap_design_branch(
                branch_id="main", expected_head=None, branch=self.branch("main", record, record))
        s0 = self.stage("initial")
        self.repository.compare_and_swap_design_branch(
            branch_id="main", expected_head=None, branch=self.branch("main", s0, s0))
        self.repository.layout.resolve_record(s0).write_text("{}", encoding="utf-8")
        with self.assertRaises(ProjectIntegrityError):
            FilesystemProjectRepository.open(self.root)

    def test_design_ref_respects_cross_process_lock(self) -> None:
        s0 = self.stage("initial")
        lock_path = self.repository.layout.design_branches.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen([sys.executable, "-c", _FOREIGN_LOCK_HOLDER, str(lock_path)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
            with patch("archflow.project.repository._HEAD_LOCK_TIMEOUT_SECONDS", 0.05):
                with self.assertRaises(ProjectHeadLocked):
                    self.repository.compare_and_swap_design_branch(
                        branch_id="main", expected_head=None, branch=self.branch("main", s0, s0))
            self.assertFalse(self.repository.layout.design_branches.exists())
        finally:
            holder.communicate("\n", timeout=5)

    def test_initial_authored_inputs_refuse_invalid_bytes_and_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "new-project"
            with self.assertRaises(ValueError):
                FilesystemProjectRepository.initialize(root, project_id="new-project", initial_state={}, authored_record={"bad": object()})
            self.assertFalse(root.exists())
            authored = root / "input/runner/state-record.json"
            authored.parent.mkdir(parents=True)
            authored.write_bytes(b"existing authored design")
            with self.assertRaises(ProjectAlreadyExists):
                FilesystemProjectRepository.initialize(root, project_id="new-project", initial_state={}, authored_record={})
            self.assertEqual(authored.read_bytes(), b"existing authored design")
            self.assertFalse((root / "project.json").exists())
            self.assertFalse((root / "HEAD").exists())

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
            record_kind=STATE_RECORD,
            payload={"schema": "StateRecord@1", "value": 1},
        )
        duplicate = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind=STATE_RECORD,
            payload={"schema": "StateRecord@1", "value": 1},
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

    def test_json_load_parses_the_verified_bytes_when_the_file_changes(self) -> None:
        run = self.repository.create_run("changing-record")
        payload = {"schema": "StateRecord@1", "value": 1}
        ref = self.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STATE_RECORD,
            payload=payload,
        )
        record_path = self.repository.layout.resolve_record(ref)
        read_bytes = Path.read_bytes

        def replace_after_read(path: Path) -> bytes:
            data = read_bytes(path)
            if path == record_path:
                path.write_bytes(b'{"schema":"StateRecord@1","value":2}\n')
            return data

        with patch.object(Path, "read_bytes", autospec=True, side_effect=replace_after_read):
            self.assertEqual(self.repository.load_json(ref), payload)
        with self.assertRaisesRegex(ProjectIntegrityError, "record digest mismatch"):
            self.repository.load_json(ref)

    def test_json_kind_selection_avoids_other_payloads_and_verifies_selected_bytes(self) -> None:
        run = self.repository.create_run("kind-selection")
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
        selected = self.repository.put_json(run=run, destination=destination, record_kind=STATE_RECORD,
                                            payload={"schema": "StateRecord@1", "value": 1})
        unrelated = self.repository.put_json(run=run, destination=destination, record_kind=DESIGN_STAGE,
                                             payload={"schema": "DesignStage@1", "value": 2})
        path = self.repository.layout.resolve_record(selected)
        # A historical, longer kind sharing the prefix is not this kind.
        historical = path.with_name(path.name.replace("state-record-", "state-record-archived-", 1))
        historical.write_bytes(path.read_bytes())
        self.repository.layout.resolve_record(unrelated).write_bytes(b"unrelated incomplete payload")
        with patch.object(self.repository, "load_json", wraps=self.repository.load_json) as load:
            self.assertEqual(self.repository.list_json(run=run, destination=destination, record_kind=STATE_RECORD), (selected,))
        self.assertEqual([call.args[0] for call in load.call_args_list], [selected])
        legacy = self.repository.list_json(run=run, destination=destination, record_kind="state-record-archived")
        self.assertEqual(len(legacy), 1, "readable historical kinds need not be registered for new writes")
        with self.assertRaises(ProjectIntegrityError):
            self.repository.list_json(run=run, destination=destination)
        path.write_bytes(b'{"schema":"StateRecord@1","value":3}\n')
        with self.assertRaisesRegex(ProjectIntegrityError, "record digest mismatch"):
            self.repository.list_json(run=run, destination=destination, record_kind=STATE_RECORD)

    def test_workspace_binary_lands_only_in_the_assigned_run_and_leaves_head_unchanged(
        self,
    ) -> None:
        run = self.repository.create_run("workspace-run")
        before = self.repository.read_head()

        capture = self.repository.put_workspace_file(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_WORKSPACE,
                run_id=run.run_id,
            ),
            artifact_id="viewport-capture-demo",
            workspace_relative_path="studio-captures/viewport-demo.png",
            media_type="image/png",
            source=io.BytesIO(b"png-bytes"),
        )

        self.assertIsInstance(capture, ProjectArtifactRef)
        self.assertEqual(
            capture.relative_path,
            "runs/workspace-run/workspaces/studio-captures/viewport-demo.png",
        )
        self.assertEqual(
            self.repository.layout.resolve_record(capture).read_bytes(),
            b"png-bytes",
        )
        self.assertEqual(self.repository.read_head(), before)

        with self.assertRaisesRegex(ValueError, "run-workspace"):
            self.repository.put_workspace_file(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=run.run_id,
                ),
                artifact_id="misplaced-capture",
                workspace_relative_path="studio-captures/misplaced.png",
                media_type="image/png",
                source=io.BytesIO(b"png-bytes"),
            )

    def test_workspace_binary_refuses_wrong_run_escape_and_overwrite(self) -> None:
        run = self.repository.create_run("workspace-run")
        other = self.repository.create_run("other-run")
        workspace_destination = PersistenceDestination(
            PersistenceArea.RUN_WORKSPACE,
            run_id=run.run_id,
        )

        with self.assertRaisesRegex(ValueError, "another run"):
            self.repository.put_workspace_file(
                run=other,
                destination=workspace_destination,
                artifact_id="wrong-run-capture",
                workspace_relative_path="studio-captures/wrong-run.png",
                media_type="image/png",
                source=io.BytesIO(b"png-bytes"),
            )

        with self.assertRaisesRegex(ValueError, "unsafe segment"):
            self.repository.put_workspace_file(
                run=run,
                destination=workspace_destination,
                artifact_id="escape-capture",
                workspace_relative_path="../escape.png",
                media_type="image/png",
                source=io.BytesIO(b"png-bytes"),
            )

        fixed_path = "studio-captures/viewport-fixed.png"
        self.repository.put_workspace_file(
            run=run,
            destination=workspace_destination,
            artifact_id="fixed-capture",
            workspace_relative_path=fixed_path,
            media_type="image/png",
            source=io.BytesIO(b"first-pixels"),
        )
        with self.assertRaisesRegex(ProjectIntegrityError, "different bytes"):
            self.repository.put_workspace_file(
                run=run,
                destination=workspace_destination,
                artifact_id="fixed-capture",
                workspace_relative_path=fixed_path,
                media_type="image/png",
                source=io.BytesIO(b"changed-pixels"),
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
                record_kind=STATE_RECORD,
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
                record_kind=STATE_RECORD,
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
        self.assertEqual(self.repository.load_version_state(base), {"phase": "request", "commitments": []})
        self.assertEqual(self.repository.load_version_state(accepted), {"selected": "a"})
        with self.assertRaises(ProjectIntegrityError):
            self.repository.load_version_state(ProjectVersionRef("project-a", base.version, "0" * 64))

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
