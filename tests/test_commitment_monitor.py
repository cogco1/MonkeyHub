from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.state.commitments import Commitment, CommitmentKind, CommitmentStatus, CommitmentStrength, CriterionRef, RevisionPolicy
from archflow.state.operational_state import DependencyEffect, DependencyEdge, ObligationStatus, OperationalMarkovState
from archive.archflow.validation.commitments import (
    CommitmentFindingSeverity,
    CommitmentMonitorError,
    CommitmentProgressOutcome,
    CriterionObservation,
    CriterionOutcome,
    DependencyImpactKind,
    TemporalMonitorSpec,
    TemporalMonitorStage,
    TemporalMonitorState,
    TemporalSemantics,
    monitor_commitments,
)


def _branch() -> BranchRef:
    base = ProjectVersionRef(
        project_id="commitment-monitor-test",
        version=0,
        state_sha256="a" * 64,
    )
    return BranchRef(
        run=RunRef(
            project_id="commitment-monitor-test",
            run_id="run-001",
            base=base,
        ),
        branch_id="option-a",
        epoch=0,
    )


def _commitment(
    commitment_id: str,
    *,
    kind: CommitmentKind = CommitmentKind.MAINTENANCE,
    strength: CommitmentStrength = CommitmentStrength.HARD,
    activation: bool = False,
    revision_policy: RevisionPolicy = RevisionPolicy.OWNER_ONLY,
    permitted_authority_ids: tuple[str, ...] = (),
    dependency_ids: tuple[str, ...] = (),
) -> Commitment:
    return Commitment(
        commitment_id=commitment_id,
        kind=kind,
        strength=strength,
        status=CommitmentStatus.ACTIVE,
        authority_id="authority-user",
        authorized_by="authority-user",
        source_event_ref=f"event://request/{commitment_id}",
        satisfaction_criterion=CriterionRef(
            criterion_id=f"criterion-{commitment_id}",
            provider_id=f"validator-{commitment_id}",
            subject_refs=(f"semantic://{commitment_id}",),
        ),
        activation_criterion=(
            CriterionRef(
                criterion_id=f"activation-{commitment_id}",
                provider_id=f"validator-{commitment_id}",
                subject_refs=(f"semantic://{commitment_id}",),
            )
            if activation
            else None
        ),
        evidence_refs=(f"evidence://request/{commitment_id}",),
        scope_refs=(f"semantic://{commitment_id}",),
        revision_policy=revision_policy,
        permitted_authority_ids=permitted_authority_ids,
        dependency_ids=dependency_ids,
        monitor_state_ref=f"monitor://{commitment_id}",
    )


def _state(
    *commitments: Commitment,
    dependencies: tuple[DependencyEdge, ...] = (),
) -> OperationalMarkovState:
    return OperationalMarkovState(
        branch=_branch(),
        compiler_version="commitment-test-1",
        phase="schematic",
        commitments=tuple(commitments),
        dependencies=dependencies,
        evidence_refs=("evidence://request/root",),
    )


def _observation(
    state: OperationalMarkovState,
    commitment: Commitment,
    outcome: CriterionOutcome,
    *,
    candidate_id: str = "candidate-001",
    activation: bool = False,
) -> CriterionObservation:
    criterion = (
        commitment.activation_criterion
        if activation
        else commitment.satisfaction_criterion
    )
    if criterion is None:
        raise AssertionError("requested criterion is absent")
    return CriterionObservation(
        observation_id=(
            f"observation-{commitment.commitment_id}-"
            f"{'activation' if activation else 'satisfaction'}"
        ),
        candidate_id=candidate_id,
        branch=state.branch,
        base_state_digest=state.state_digest,
        provider_id=criterion.provider_id,
        criterion_id=criterion.criterion_id,
        outcome=outcome,
        measurement={"outcome": outcome.value},
        threshold={"required": CriterionOutcome.SATISFIED.value},
        evidence_refs=(
            f"evidence://criterion/{criterion.criterion_id}",
        ),
    )


class CommitmentMonitorTests(unittest.TestCase):
    def test_achievement_waits_until_boundary_but_maintenance_fails_now(
        self,
    ) -> None:
        achievement = _commitment(
            "capacity",
            kind=CommitmentKind.ACHIEVEMENT,
        )
        maintenance = _commitment(
            "clear-egress",
            kind=CommitmentKind.MAINTENANCE,
        )
        state = _state(achievement, maintenance)
        observations = (
            _observation(
                state,
                achievement,
                CriterionOutcome.UNSATISFIED,
            ),
            _observation(
                state,
                maintenance,
                CriterionOutcome.UNSATISFIED,
            ),
        )

        receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=observations,
            completion_boundary=False,
        )
        progress = {
            item.commitment_id: item.outcome for item in receipt.progress
        }

        self.assertEqual(
            progress["capacity"],
            CommitmentProgressOutcome.PENDING,
        )
        self.assertEqual(
            progress["clear-egress"],
            CommitmentProgressOutcome.VIOLATED,
        )
        self.assertEqual(
            [item.commitment_id for item in receipt.findings],
            ["clear-egress"],
        )
        self.assertFalse(receipt.passed)

        boundary_receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=observations,
            completion_boundary=True,
        )
        self.assertEqual(
            {
                item.commitment_id
                for item in boundary_receipt.findings
            },
            {"capacity", "clear-egress"},
        )

    def test_missing_evidence_creates_typed_blocked_obligation_only(
        self,
    ) -> None:
        commitment = _commitment("daylight")
        state = _state(commitment)
        original_digest = state.state_digest

        receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=(),
            completion_boundary=False,
        )

        self.assertEqual(state.state_digest, original_digest)
        self.assertEqual(
            receipt.progress[0].outcome,
            CommitmentProgressOutcome.EVIDENCE_BLOCKED,
        )
        self.assertEqual(len(receipt.obligations), 1)
        obligation = receipt.obligations[0]
        self.assertEqual(obligation.status, ObligationStatus.BLOCKED)
        self.assertIsNotNone(obligation.condition)
        assert obligation.condition is not None
        self.assertTrue(
            obligation.condition.ref.startswith(
                "criterion-observation:"
            )
        )
        self.assertEqual(
            obligation.condition.expected_value.to_python(),
            CriterionOutcome.SATISFIED.value,
        )

    def test_unknown_activation_evidence_blocks_conditional_commitment(
        self,
    ) -> None:
        commitment = _commitment("assembly-load", activation=True)
        state = _state(commitment)
        satisfaction = _observation(
            state,
            commitment,
            CriterionOutcome.SATISFIED,
        )

        receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=(satisfaction,),
            completion_boundary=False,
        )

        self.assertEqual(
            receipt.progress[0].outcome,
            CommitmentProgressOutcome.EVIDENCE_BLOCKED,
        )
        self.assertEqual(
            receipt.findings[0].code,
            "commitment.activation_evidence_missing",
        )
        self.assertEqual(
            receipt.obligations[0].status,
            ObligationStatus.BLOCKED,
        )

    def test_candidate_cannot_replace_an_active_commitment(self) -> None:
        commitment = _commitment("occupancy")
        state = _state(commitment)
        replacement = replace(
            commitment,
            status=CommitmentStatus.PROPOSED,
            authorized_by=None,
            scope_refs=("semantic://different-scope",),
            monitor_state_ref=None,
        )

        receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=(
                _observation(
                    state,
                    commitment,
                    CriterionOutcome.SATISFIED,
                ),
            ),
            completion_boundary=False,
            proposed_commitments=(replacement,),
        )

        self.assertFalse(receipt.passed)
        self.assertIn(
            "commitment.unauthorized_mutation",
            {item.code for item in receipt.findings},
        )
        self.assertEqual(state.commitments, (commitment,))
        self.assertTrue(
            any(
                item.status is ObligationStatus.OPEN
                for item in receipt.obligations
            )
        )

    def test_dependency_propagation_is_named_and_local(self) -> None:
        source = _commitment("structural-grid")
        dependent = _commitment(
            "partition-layout",
            dependency_ids=("structural-grid",),
        )
        dependencies = (
            DependencyEdge(
                upstream_ref="commitment:structural-grid",
                downstream_ref="semantic://column-grid",
                relation="governs",
                source_ref="evidence://dependency/grid",
                effect=DependencyEffect.INVALIDATES,
            ),
            DependencyEdge(
                upstream_ref="semantic://column-grid",
                downstream_ref="semantic://partition-layout",
                relation="coordinates",
                source_ref="evidence://dependency/partition",
                effect=DependencyEffect.REQUIRES_REVALIDATION,
            ),
            DependencyEdge(
                upstream_ref="commitment:structural-grid",
                downstream_ref="semantic://material-palette",
                relation="context-only",
                source_ref="evidence://dependency/material",
                effect=DependencyEffect.SUPPORTS_ONLY,
            ),
            DependencyEdge(
                upstream_ref="commitment:unrelated",
                downstream_ref="semantic://unrelated-output",
                relation="unrelated",
                source_ref="evidence://dependency/unrelated",
                effect=DependencyEffect.INVALIDATES,
            ),
        )
        state = _state(
            source,
            dependent,
            dependencies=dependencies,
        )

        receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=(
                _observation(
                    state,
                    source,
                    CriterionOutcome.UNSATISFIED,
                ),
                _observation(
                    state,
                    dependent,
                    CriterionOutcome.SATISFIED,
                ),
            ),
            completion_boundary=False,
        )
        impacts = {
            item.target_ref: item for item in receipt.dependency_impacts
        }

        self.assertEqual(
            impacts["semantic://column-grid"].impact,
            DependencyImpactKind.INVALIDATED,
        )
        self.assertEqual(
            impacts["semantic://partition-layout"].impact,
            DependencyImpactKind.REVALIDATION_REQUIRED,
        )
        self.assertEqual(
            impacts["commitment:partition-layout"].impact,
            DependencyImpactKind.REVALIDATION_REQUIRED,
        )
        self.assertNotIn("semantic://material-palette", impacts)
        self.assertNotIn("semantic://unrelated-output", impacts)
        self.assertTrue(
            all(len(item.dependency_path) >= 2 for item in impacts.values())
        )
        impact_subjects = {
            item.subject_refs[0]
            for item in receipt.obligations
            if item.obligation_id.startswith("commitment-impact-")
        }
        self.assertEqual(impact_subjects, set(impacts))

    def test_negotiable_failure_proposes_revision_without_authorizing_it(
        self,
    ) -> None:
        commitment = _commitment(
            "target-area",
            strength=CommitmentStrength.NEGOTIABLE,
            revision_policy=RevisionPolicy.NAMED_AUTHORITIES,
            permitted_authority_ids=("authority-reviewer",),
        )
        state = _state(commitment)

        receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=(
                _observation(
                    state,
                    commitment,
                    CriterionOutcome.UNSATISFIED,
                ),
            ),
            completion_boundary=False,
        )

        self.assertFalse(receipt.passed)
        self.assertEqual(len(receipt.revision_proposals), 1)
        proposal = receipt.revision_proposals[0]
        self.assertEqual(
            proposal.required_authority_ids,
            ("authority-reviewer",),
        )
        self.assertEqual(proposal.base_state_digest, state.state_digest)
        self.assertEqual(
            state.commitments[0].status,
            CommitmentStatus.ACTIVE,
        )

    def test_once_activated_temporal_state_latches_and_round_trips(
        self,
    ) -> None:
        commitment = _commitment("fire-separation", activation=True)
        state = _state(commitment)
        spec = TemporalMonitorSpec(
            commitment_id=commitment.commitment_id,
            semantics=TemporalSemantics.ONCE_ACTIVATED,
        )
        first = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=(
                _observation(
                    state,
                    commitment,
                    CriterionOutcome.SATISFIED,
                    activation=True,
                ),
                _observation(
                    state,
                    commitment,
                    CriterionOutcome.SATISFIED,
                ),
            ),
            completion_boundary=False,
            temporal_specs=(spec,),
        )
        temporal = first.temporal_states[0]

        self.assertTrue(temporal.activation_latched)
        self.assertEqual(temporal.stage, TemporalMonitorStage.ACTIVE)
        self.assertEqual(
            TemporalMonitorState.from_dict(temporal.to_dict()),
            temporal,
        )

        second = monitor_commitments(
            state,
            candidate_id="candidate-002",
            observations=(
                _observation(
                    state,
                    commitment,
                    CriterionOutcome.UNSATISFIED,
                    candidate_id="candidate-002",
                ),
            ),
            completion_boundary=False,
            temporal_specs=(spec,),
            prior_temporal_states=(temporal,),
        )

        self.assertEqual(
            second.progress[0].outcome,
            CommitmentProgressOutcome.VIOLATED,
        )
        self.assertEqual(
            second.temporal_states[0].stage,
            TemporalMonitorStage.VIOLATED,
        )
        self.assertTrue(second.temporal_states[0].activation_latched)

    def test_advisory_aesthetic_observation_cannot_waive_hard_failure(
        self,
    ) -> None:
        hard = _commitment("egress-clearance")
        aesthetic = _commitment(
            "facade-rhythm",
            strength=CommitmentStrength.PREFERENCE,
        )
        state = _state(hard, aesthetic)

        receipt = monitor_commitments(
            state,
            candidate_id="candidate-001",
            observations=(
                _observation(
                    state,
                    hard,
                    CriterionOutcome.UNSATISFIED,
                ),
                _observation(
                    state,
                    aesthetic,
                    CriterionOutcome.UNSATISFIED,
                ),
            ),
            completion_boundary=False,
        )
        severities = {
            item.commitment_id: item.severity
            for item in receipt.findings
        }

        self.assertEqual(
            severities["egress-clearance"],
            CommitmentFindingSeverity.HARD_FAILURE,
        )
        self.assertEqual(
            severities["facade-rhythm"],
            CommitmentFindingSeverity.ADVISORY,
        )
        self.assertFalse(receipt.passed)

    def test_stale_observation_is_rejected(self) -> None:
        commitment = _commitment("structure")
        state = _state(commitment)
        stale = replace(
            _observation(
                state,
                commitment,
                CriterionOutcome.SATISFIED,
            ),
            base_state_digest="b" * 64,
        )

        with self.assertRaisesRegex(
            CommitmentMonitorError,
            "stale or cross-branch",
        ):
            monitor_commitments(
                state,
                candidate_id="candidate-001",
                observations=(stale,),
                completion_boundary=False,
            )


if __name__ == "__main__":
    unittest.main()
