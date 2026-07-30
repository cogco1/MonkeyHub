from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace

from archflow.runtime.player_control import (
    CandidateControlStatus,
    PlayerControlError,
    VoxelBounds,
    WorldTarget,
    approve_candidate_control,
    cancel_candidate_control,
    create_candidate_preview,
    open_candidate_control,
    pause_candidate_control,
    record_exact_undo,
)
from archflow.runtime.world_recovery import (
    MutationPhaseReceipt,
    WorldMutationStatus,
    WorldMutationTrace,
)
from tests.test_candidate_assembly import EVIDENCE, _assembly


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _trace(
    *,
    plan_sha256: str,
    status: WorldMutationStatus,
    exact_token_proof: bool = True,
) -> WorldMutationTrace:
    world = {
        "world_id": "disposable-world",
        "dimension_id": "minecraft:overworld",
    }
    session = "7" * 64
    outcomes = (
        (
            ("execute", "acknowledged"),
            ("compensate", "acknowledged"),
            ("finalize", "failed"),
        )
        if status is WorldMutationStatus.COMPENSATED
        else (
            ("execute", "acknowledged"),
            ("compensate", "failed"),
            ("finalize", "failed"),
        )
    )
    phases = []
    prior = None
    for sequence, (phase, outcome) in enumerate(outcomes):
        evidence = {
            "fixture": True,
            "atomic_rollback_claimed": False,
        }
        if phase == "execute" and outcome == "acknowledged":
            evidence["undo_token_available"] = exact_token_proof
        if (
            phase == "compensate"
            and outcome == "acknowledged"
            and exact_token_proof
        ):
            evidence.update(
                {
                    "undo_token_sha256": "8" * 64,
                    "compensation_session_sha256": "6" * 64,
                }
            )
        body = {
            "sequence": sequence,
            "phase": phase,
            "outcome": outcome,
            "plan_sha256": plan_sha256,
            "world_identity": world,
            "session_sha256": session,
            "prior_phase_sha256": prior,
            "evidence": evidence,
        }
        receipt = MutationPhaseReceipt(
            **body,
            phase_sha256=_digest(body),
        )
        phases.append(receipt)
        prior = receipt.phase_sha256
    assembly = _assembly()
    return WorldMutationTrace(
        mutation_id="mutation-player-undo",
        base=assembly.plan.base,
        workspace_id=assembly.submission.workspace_id,
        plan_sha256=plan_sha256,
        server={"transport": "fixture", "endpoint": "localhost"},
        world_identity=world,
        session_sha256=session,
        preview_plan_id="preview-player-undo",
        status=status,
        phases=tuple(phases),
        phase_head_sha256=phases[-1].phase_sha256,
    )


def _control(trace: WorldMutationTrace):
    assembly = _assembly()
    preview = create_candidate_preview(
        assembly,
        world=WorldTarget.from_trace(trace),
        bounds=VoxelBounds((0, 0, 0), (1, 1, 1)),
        additions=1,
        removals=0,
        evidence_refs=(EVIDENCE,),
    )
    return replace(
        open_candidate_control(preview),
        status=CandidateControlStatus.UNDO_REQUIRED,
        sequence=1,
        approval_ref="candidate-approval:fixture",
        mutation_trace_ref=f"world-mutation:{trace.trace_digest}",
    )


class PreviewUndoContractTests(unittest.TestCase):
    def test_exact_compensation_restores_named_candidate_world_boundary(
        self,
    ) -> None:
        assembly = _assembly()
        trace = _trace(
            plan_sha256=assembly.plan.plan_digest,
            status=WorldMutationStatus.COMPENSATED,
        )
        before = _control(trace)
        restored = record_exact_undo(before, trace)

        self.assertIs(restored.status, CandidateControlStatus.RESTORED)
        self.assertEqual(
            restored.candidate_assembly_digest,
            before.candidate_assembly_digest,
        )
        self.assertEqual(restored.base, before.base)
        self.assertEqual(restored.world, before.world)
        self.assertIsNotNone(restored.restore_evidence_ref)
        self.assertIn("not claimed", restored.reason)
        self.assertFalse(
            restored.to_dict()["world_mutation_performed"]
        )
        self.assertFalse(
            restored.to_dict()["canonical_write_authority"]
        )

    def test_failed_compensation_never_claims_restore(self) -> None:
        assembly = _assembly()
        trace = _trace(
            plan_sha256=assembly.plan.plan_digest,
            status=WorldMutationStatus.COMPENSATION_FAILED,
        )
        outcome = record_exact_undo(_control(trace), trace)

        self.assertIs(
            outcome.status,
            CandidateControlStatus.MANUAL_RECONCILIATION_REQUIRED,
        )
        self.assertIsNone(outcome.restore_evidence_ref)

    def test_compensated_label_without_exact_token_proof_never_restores(
        self,
    ) -> None:
        assembly = _assembly()
        trace = _trace(
            plan_sha256=assembly.plan.plan_digest,
            status=WorldMutationStatus.COMPENSATED,
            exact_token_proof=False,
        )
        outcome = record_exact_undo(_control(trace), trace)

        self.assertIs(
            outcome.status,
            CandidateControlStatus.MANUAL_RECONCILIATION_REQUIRED,
        )
        self.assertIsNone(outcome.restore_evidence_ref)

    def test_pause_cannot_launder_written_candidate(self) -> None:
        assembly = _assembly()
        trace = _trace(
            plan_sha256=assembly.plan.plan_digest,
            status=WorldMutationStatus.COMPENSATED,
        )
        written = _control(trace)

        paused = pause_candidate_control(written, reason="player break")
        self.assertIs(paused.status, CandidateControlStatus.PAUSED)
        self.assertIs(
            paused.effective_status,
            CandidateControlStatus.UNDO_REQUIRED,
        )

        with self.assertRaisesRegex(
            PlayerControlError,
            "exact undo or reconciliation",
        ):
            cancel_candidate_control(paused, reason="give up")

        with self.assertRaisesRegex(
            PlayerControlError,
            "paused from undo_required",
        ):
            approve_candidate_control(
                paused,
                assembly,
                None,
                None,
                now_utc="2026-07-25T10:20:00Z",
            )

        repaused = pause_candidate_control(paused, reason="still away")
        self.assertIs(
            repaused.effective_status,
            CandidateControlStatus.UNDO_REQUIRED,
        )

        restored = record_exact_undo(paused, trace)
        self.assertIs(restored.status, CandidateControlStatus.RESTORED)
        self.assertIsNone(restored.paused_from)

    def test_mismatched_plan_cannot_prove_undo_boundary(self) -> None:
        assembly = _assembly()
        exact = _trace(
            plan_sha256=assembly.plan.plan_digest,
            status=WorldMutationStatus.COMPENSATED,
        )
        another = _trace(
            plan_sha256="f" * 64,
            status=WorldMutationStatus.COMPENSATED,
        )

        with self.assertRaisesRegex(
            PlayerControlError,
            "exact candidate restore boundary",
        ):
            record_exact_undo(_control(exact), another)


if __name__ == "__main__":
    unittest.main()
