from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archive.archflow.adapters.minecraft_mcp import MinecraftMcpFailure
from archive.archflow.adapters.mcp_stdio import McpClientError
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectVersionRef
from archive.archflow.runtime.walking_skeleton import initial_state
from archive.archflow.runtime.world_recovery import (
    CanonicalCommitEvidence,
    RecoveryDisposition,
    WorldMutationStatus,
    WorldMutationTrace,
    WorldRecoveryArchive,
    WorldRecoveryError,
    load_world_recovery,
    persist_world_recovery,
    reconcile_world_after_restart,
)
from archive.archflow.workspace.manager import WorkspaceManager
from archive.tests.integration.test_minecraft_mcp_adapter import (
    PLAN,
    _ScriptedClient,
    _exact_state,
    adapter,
)


class WorldRecoveryIntegrationTests(unittest.TestCase):
    def _project_and_trace(self, temporary: str):
        repository = FilesystemProjectRepository.initialize(
            Path(temporary) / "world-recovery-project",
            project_id="world-recovery-project",
            initial_state={
                "schema": "CanonicalProjectState@1",
                "phase": "candidate",
                "authoritative_record_refs": [],
                "derived_record_refs": [],
            },
        )
        run = repository.create_run("run-001")
        state = replace(
            initial_state(
                "A project-authored test candidate",
                run_id=run.project_id,
            ),
            ref=run.base,
        )
        workspace = WorkspaceManager(
            Path(temporary) / "workspaces"
        ).fork(state)
        adapter(allow_world_write=True).build(state, workspace, PLAN)
        trace = WorldMutationTrace.from_dict(
            json.loads(
                (
                    workspace.root
                    / "minecraft-mutation-receipt.json"
                ).read_text()
            )
        )
        return repository, run, trace

    def test_restart_reloads_pending_candidate_without_head_advance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, run, trace = self._project_and_trace(temporary)
            candidate = repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_CANDIDATE,
                    run_id=run.run_id,
                ),
                record_kind="candidate",
                payload={
                    "schema": "CandidateRecord@1",
                    "submission_id": "candidate-recovery-001",
                },
            )
            before = repository.read_head()
            outcome = reconcile_world_after_restart(
                trace,
                current_head=before,
                candidate_record_ref=candidate.uri,
                candidate_submission_id="candidate-recovery-001",
                candidate_plan_sha256=trace.plan_sha256,
            )
            archive = WorldRecoveryArchive(trace=trace, outcome=outcome)
            record = persist_world_recovery(
                repository,
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECOVERY,
                    run_id=run.run_id,
                ),
                archive=archive,
            )
            reopened = FilesystemProjectRepository.open(
                repository.layout.root
            )
            loaded = load_world_recovery(reopened, record)

            self.assertEqual(
                loaded.outcome.disposition,
                RecoveryDisposition.CANDIDATE_PENDING_REVIEW,
            )
            self.assertEqual(loaded, archive)
            self.assertEqual(reopened.read_head(), before)
            self.assertFalse(
                loaded.outcome.to_dict()["canonical_advance_performed"]
            )
            reopened.verify()

    def test_missing_durable_candidate_is_orphaned_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, _, trace = self._project_and_trace(temporary)
            before = repository.read_head()
            outcome = reconcile_world_after_restart(
                trace,
                current_head=before,
            )

            self.assertEqual(
                outcome.disposition,
                RecoveryDisposition.ORPHANED_CANDIDATE,
            )
            self.assertEqual(repository.read_head(), before)

    def test_tampered_phase_chain_fails_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, _, trace = self._project_and_trace(temporary)
            payload = trace.to_dict()
            payload["phases"][1]["outcome"] = "fabricated-acknowledgement"

            with self.assertRaisesRegex(
                WorldRecoveryError,
                "digest",
            ):
                WorldMutationTrace.from_dict(payload)

    def test_unknown_write_forbids_canonical_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, _, trace = self._project_and_trace(temporary)
            execute_started_index = next(
                index
                for index, item in enumerate(trace.phases)
                if item.phase == "execute" and item.outcome == "started"
            )
            uncertain_phases = trace.phases[: execute_started_index + 1]
            uncertain = replace(
                trace,
                status=WorldMutationStatus.WRITE_UNKNOWN,
                phases=uncertain_phases,
                phase_head_sha256=uncertain_phases[-1].phase_sha256,
            )
            before = repository.read_head()
            outcome = reconcile_world_after_restart(
                uncertain,
                current_head=before,
                candidate_record_ref=(
                    "project://world-recovery-project/runs/run-001/"
                    "candidates/uncertain.json"
                ),
                candidate_submission_id="candidate-uncertain",
                candidate_plan_sha256=uncertain.plan_sha256,
            )

            self.assertEqual(
                outcome.disposition,
                RecoveryDisposition.MANUAL_RECONCILIATION,
            )
            self.assertEqual(repository.read_head(), before)
            self.assertIn("forbidden", " ".join(outcome.reasons))

    def test_exact_commit_receipt_reconciles_without_claiming_atomicity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, run, trace = self._project_and_trace(temporary)
            advanced = ProjectVersionRef(
                project_id=run.project_id,
                version=run.base.version + 1,
                state_sha256="f" * 64,
            )
            commit = CanonicalCommitEvidence(
                submission_id="candidate-recovery-001",
                from_state=run.base,
                to_state=advanced,
                commit_receipt_ref=(
                    "project://world-recovery-project/events/"
                    "commit-recovery-001.json"
                ),
            )
            outcome = reconcile_world_after_restart(
                trace,
                current_head=advanced,
                candidate_record_ref=(
                    "project://world-recovery-project/runs/run-001/"
                    "candidates/candidate-recovery-001.json"
                ),
                candidate_submission_id="candidate-recovery-001",
                candidate_plan_sha256=trace.plan_sha256,
                commit_evidence=commit,
            )
            payload = outcome.to_dict()

            self.assertEqual(
                outcome.disposition,
                RecoveryDisposition.RECONCILED_COMMITTED,
            )
            self.assertFalse(payload["canonical_advance_performed"])
            self.assertFalse(payload["cross_system_atomicity_claimed"])

    def test_manual_resolution_is_separate_explicit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, _, trace = self._project_and_trace(temporary)
            outcome = reconcile_world_after_restart(
                trace,
                current_head=repository.read_head(),
                manual_resolution_ref=(
                    "project://world-recovery-project/runs/run-001/"
                    "recovery/manual-resolution.json"
                ),
            )

            self.assertEqual(
                outcome.disposition,
                RecoveryDisposition.MANUALLY_RECONCILED,
            )
            self.assertFalse(
                outcome.to_dict()["external_world_mutation_performed"]
            )

    def test_compensation_outcomes_are_distinct_and_reloadable(self) -> None:
        cases = (
            (
                None,
                WorldMutationStatus.COMPENSATED,
                RecoveryDisposition.COMPENSATED,
            ),
            (
                McpClientError("undo failed"),
                WorldMutationStatus.COMPENSATION_FAILED,
                RecoveryDisposition.COMPENSATION_FAILED,
            ),
        )
        for index, (undo_error, trace_status, disposition) in enumerate(cases):
            with self.subTest(disposition=disposition):
                state = _exact_state(f"compensation-reload-{index}")
                boundary = adapter(
                    allow_world_write=True,
                    allow_compensation=True,
                )
                with tempfile.TemporaryDirectory() as temporary:
                    workspace = WorkspaceManager(Path(temporary)).fork(state)
                    clients = (
                        _ScriptedClient(
                            capture_error=McpClientError("capture failed")
                        ),
                        _ScriptedClient(undo_error=undo_error),
                    )
                    with patch.object(
                        boundary,
                        "_client",
                        side_effect=clients,
                    ):
                        with self.assertRaises(MinecraftMcpFailure):
                            boundary.build(state, workspace, PLAN)
                    trace = WorldMutationTrace.from_dict(
                        json.loads(
                            (
                                workspace.root
                                / "minecraft-mutation-receipt.json"
                            ).read_text()
                        )
                    )
                outcome = reconcile_world_after_restart(
                    trace,
                    current_head=state.ref,
                )
                archive = WorldRecoveryArchive(
                    trace=trace,
                    outcome=outcome,
                )

                self.assertEqual(trace.status, trace_status)
                self.assertEqual(outcome.disposition, disposition)
                self.assertEqual(
                    WorldRecoveryArchive.from_dict(archive.to_dict()),
                    archive,
                )


if __name__ == "__main__":
    unittest.main()
