from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from archflow.commit import InMemoryStateStore
from archflow.runtime.repair_loop import (
    ArchitectDecision,
    ArchitectFailureRecovery,
    CandidateReview,
    ConsultationStatus,
    ExpertSelection,
    RepairAction,
    RepairContext,
    RepairLimits,
    RepairStatus,
    run_repair_loop,
)
from archflow.state import (
    ArtifactRef,
    CanonicalState,
    GoalContract,
    Obligation,
    StateRef,
)
from archflow.submission import (
    CandidateDelta,
    CandidateSubmission,
    RepairFinding,
    RepairObligation,
)
from archflow.validation import Finding, ValidationReceipt
from archflow.workspace import WorkspaceManager, WorkspaceRef


def _initial_state() -> CanonicalState:
    return CanonicalState(
        ref=StateRef("run-repair", 0),
        goal=GoalContract(
            prompt="Build a usable test building",
            must=("usable",),
        ),
        open_obligations=(
            Obligation(
                obligation_id="usable",
                statement="Demonstrate minimal usability",
                source_ref="goal-contract",
            ),
        ),
    )


def _submission(
    workspace: WorkspaceRef,
    *,
    submission_id: str,
    artifact_label: str,
    base: StateRef | None = None,
) -> CandidateSubmission:
    digest = hashlib.sha256(artifact_label.encode()).hexdigest()
    artifact = ArtifactRef(
        artifact_id=f"artifact-{artifact_label}",
        uri=f"artifact://repair/{artifact_label}.json",
        media_type="application/vnd.archflow.voxel+json",
        sha256=digest,
    )
    return CandidateSubmission(
        submission_id=submission_id,
        base=base or workspace.base,
        workspace_id=workspace.workspace_id,
        intent=f"Architect-authored {artifact_label} candidate",
        delta=CandidateDelta(
            obligations_discharge=("usable",),
            artifacts_add=(artifact,),
        ),
        claims=(),
        evidence_refs=(artifact.artifact_id,),
    )


def _validation(
    state: CanonicalState,
    submission: CandidateSubmission,
    *,
    passed: bool = True,
    code: str = "hard.failed",
) -> ValidationReceipt:
    findings = () if passed else (Finding(code=code, message=code),)
    return ValidationReceipt(
        receipt_id=f"validation-{submission.submission_id}",
        submission_id=submission.submission_id,
        submission_digest=submission.content_digest(),
        checked_state=state.ref,
        passed=passed,
        findings=findings,
    )


def _hard_finding(
    code: str = "usability.connectivity.disconnected",
    *,
    source: str = "usability-red",
) -> RepairFinding:
    return RepairFinding(
        code=code,
        message=f"Resolve {code}",
        source_receipt_id=source,
        evidence_refs=("voxel-observation:route",),
    )


def _no_experts(_: RepairContext) -> ExpertSelection:
    return ExpertSelection()


class RepairLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.initial = _initial_state()
        self.store = InMemoryStateStore(self.initial)
        self.workspaces = WorkspaceManager(Path(self.temporary.name))

    def test_red_candidate_is_repaired_to_gold_in_two_iterations(self) -> None:
        seen_workspaces: list[str] = []
        seen_obligation_codes: list[tuple[str, ...]] = []

        def experts(context: RepairContext) -> ExpertSelection:
            codes = {item.finding_code for item in context.obligations}
            if "usability.connectivity.disconnected" not in codes:
                return ExpertSelection(
                    expert_ids=frozenset({"expert.program_use"}),
                    receipt_refs=("expert-program-receipt",),
                )
            return ExpertSelection(
                expert_ids=frozenset({"expert.circulation"}),
                receipt_refs=("expert-circulation-receipt",),
                findings=(
                    _hard_finding(
                        "expert.circulation.route_legibility",
                        source="expert-circulation-receipt",
                    ),
                ),
            )

        def architect(
            state: CanonicalState,
            workspace: WorkspaceRef,
            context: RepairContext,
        ) -> ArchitectDecision:
            self.assertEqual(state, self.initial)
            self.assertFalse(hasattr(context, "committer"))
            self.assertFalse(hasattr(context, "world"))
            seen_workspaces.append(workspace.workspace_id)
            seen_obligation_codes.append(
                tuple(item.finding_code for item in context.obligations)
            )
            if context.iteration == 1:
                return ArchitectDecision(
                    action=RepairAction.REPLACE,
                    rationale="Establish an initial candidate.",
                    submission=_submission(
                        workspace,
                        submission_id="candidate-red",
                        artifact_label="red",
                    ),
                )
            return ArchitectDecision(
                action=RepairAction.REVISE,
                rationale="Open the disconnected route while retaining authorship.",
                submission=_submission(
                    workspace,
                    submission_id="candidate-gold",
                    artifact_label="gold",
                ),
            )

        def reviewer(
            state: CanonicalState,
            submission: CandidateSubmission,
        ) -> CandidateReview:
            if submission.submission_id == "candidate-red":
                return CandidateReview(
                    validation=_validation(state, submission),
                    hard_findings=(_hard_finding(),),
                    evidence_refs=("voxel-observation:route",),
                )
            return CandidateReview(validation=_validation(state, submission))

        outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            reviewer,
            experts,
        )

        self.assertIs(outcome.status, RepairStatus.COMMITTED)
        self.assertTrue(outcome.committed)
        self.assertEqual(outcome.before.version, 0)
        self.assertEqual(outcome.after.version, 1)
        self.assertEqual(len(outcome.iterations), 2)
        self.assertFalse(outcome.iterations[0].accepted)
        self.assertTrue(outcome.iterations[1].accepted)
        self.assertEqual(len(set(seen_workspaces)), 2)
        self.assertIn(
            "usability.connectivity.disconnected",
            seen_obligation_codes[1],
        )
        self.assertIn(
            "expert.circulation.route_legibility",
            seen_obligation_codes[1],
        )
        self.assertEqual(
            outcome.iterations[1].consultation.selected_experts,
            ("expert.circulation",),
        )
        committed = self.store.read()
        self.assertEqual(committed.ref.version, 1)
        self.assertEqual(
            tuple(item.artifact_id for item in committed.artifacts),
            ("artifact-gold",),
        )
        self.assertEqual(committed.open_obligations, ())

    def test_stale_submission_exits_before_review(self) -> None:
        reviewed = 0

        def architect(
            _: CanonicalState,
            workspace: WorkspaceRef,
            __: RepairContext,
        ) -> ArchitectDecision:
            stale = StateRef(workspace.base.run_id, workspace.base.version + 1)
            return ArchitectDecision(
                action=RepairAction.REPLACE,
                rationale="Produce a stale test candidate.",
                submission=_submission(
                    workspace,
                    submission_id="candidate-stale",
                    artifact_label="stale",
                    base=stale,
                ),
            )

        def reviewer(
            state: CanonicalState,
            submission: CandidateSubmission,
        ) -> CandidateReview:
            nonlocal reviewed
            reviewed += 1
            return CandidateReview(validation=_validation(state, submission))

        outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            reviewer,
            _no_experts,
        )

        self.assertIs(outcome.status, RepairStatus.STALE_BASE)
        self.assertEqual(reviewed, 0)
        self.assertEqual(self.store.read(), self.initial)
        self.assertIsNone(outcome.commit)

    def test_repeated_hard_finding_stops_after_targeted_repair(self) -> None:
        def architect(
            _: CanonicalState,
            workspace: WorkspaceRef,
            context: RepairContext,
        ) -> ArchitectDecision:
            return ArchitectDecision(
                action=(
                    RepairAction.REPLACE
                    if context.iteration == 1
                    else RepairAction.REVISE
                ),
                rationale="Try another Architect-authored route.",
                submission=_submission(
                    workspace,
                    submission_id=f"candidate-{context.iteration}",
                    artifact_label=f"changed-{context.iteration}",
                ),
            )

        def reviewer(
            state: CanonicalState,
            submission: CandidateSubmission,
        ) -> CandidateReview:
            return CandidateReview(
                validation=_validation(state, submission),
                hard_findings=(_hard_finding(source=submission.submission_id),),
            )

        outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            reviewer,
            _no_experts,
        )

        self.assertIs(outcome.status, RepairStatus.REPEATED_FINDINGS)
        self.assertEqual(len(outcome.iterations), 2)
        self.assertEqual(self.store.read(), self.initial)

    def test_no_progress_stops_after_two_unchanged_transitions(self) -> None:
        def architect(
            _: CanonicalState,
            workspace: WorkspaceRef,
            context: RepairContext,
        ) -> ArchitectDecision:
            return ArchitectDecision(
                action=RepairAction.REVISE,
                rationale="Retry without changing the candidate.",
                submission=_submission(
                    workspace,
                    submission_id=f"candidate-{context.iteration}",
                    artifact_label="unchanged",
                ),
            )

        def reviewer(
            state: CanonicalState,
            submission: CandidateSubmission,
        ) -> CandidateReview:
            return CandidateReview(
                validation=_validation(state, submission),
                hard_findings=(_hard_finding(source=submission.submission_id),),
            )

        outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            reviewer,
            _no_experts,
            limits=RepairLimits(
                max_iterations=6,
                repeated_finding_limit=99,
                no_progress_limit=2,
            ),
        )

        self.assertIs(outcome.status, RepairStatus.NO_PROGRESS)
        self.assertEqual(len(outcome.iterations), 3)
        self.assertEqual(self.store.read(), self.initial)

    def test_iteration_and_wall_time_limits_preserve_state(self) -> None:
        def architect(
            _: CanonicalState,
            workspace: WorkspaceRef,
            context: RepairContext,
        ) -> ArchitectDecision:
            return ArchitectDecision(
                action=RepairAction.REVISE,
                rationale="Exercise a bounded iteration.",
                submission=_submission(
                    workspace,
                    submission_id=f"candidate-{context.iteration}",
                    artifact_label=f"artifact-{context.iteration}",
                ),
            )

        finding_number = 0

        def rejecting_reviewer(
            state: CanonicalState,
            submission: CandidateSubmission,
        ) -> CandidateReview:
            nonlocal finding_number
            finding_number += 1
            return CandidateReview(
                validation=_validation(state, submission),
                hard_findings=(
                    _hard_finding(f"hard.unique.{finding_number}"),
                ),
            )

        iteration_outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            rejecting_reviewer,
            _no_experts,
            limits=RepairLimits(
                max_iterations=2,
                repeated_finding_limit=99,
                no_progress_limit=99,
            ),
        )
        self.assertIs(iteration_outcome.status, RepairStatus.ITERATION_LIMIT)
        self.assertEqual(len(iteration_outcome.iterations), 2)
        self.assertEqual(self.store.read(), self.initial)

        clock_values = iter((0.0, 0.0, 2.0))
        wall_outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            lambda state, submission: CandidateReview(
                validation=_validation(state, submission)
            ),
            _no_experts,
            limits=RepairLimits(max_wall_seconds=1.0),
            clock=lambda: next(clock_values),
        )
        self.assertIs(wall_outcome.status, RepairStatus.WALL_TIME_LIMIT)
        self.assertEqual(len(wall_outcome.iterations), 1)
        self.assertEqual(self.store.read(), self.initial)

    def test_optional_expert_failure_is_fail_open_with_receipt(self) -> None:
        def broken_expert(_: RepairContext) -> ExpertSelection:
            raise RuntimeError("optional expert offline")

        def architect(
            _: CanonicalState,
            workspace: WorkspaceRef,
            context: RepairContext,
        ) -> ArchitectDecision:
            self.assertIsNotNone(context.consultation)
            self.assertIs(
                context.consultation.status,
                ConsultationStatus.ERROR,
            )
            return ArchitectDecision(
                action=RepairAction.REPLACE,
                rationale="Continue from hard evidence without optional advice.",
                submission=_submission(
                    workspace,
                    submission_id="candidate-no-expert",
                    artifact_label="gold-without-expert",
                ),
            )

        outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            lambda state, submission: CandidateReview(
                validation=_validation(state, submission)
            ),
            broken_expert,
        )

        self.assertIs(outcome.status, RepairStatus.COMMITTED)
        consultation = outcome.iterations[0].consultation
        self.assertIs(consultation.status, ConsultationStatus.ERROR)
        self.assertTrue(consultation.consultation_id)
        self.assertIn("optional expert offline", consultation.error or "")
        self.assertEqual(consultation.obligation_ids, ("usable",))
        self.assertEqual(self.store.read().ref.version, 1)

    def test_architect_can_declare_unresolved_tradeoff(self) -> None:
        reviewed = False

        def reviewer(
            state: CanonicalState,
            submission: CandidateSubmission,
        ) -> CandidateReview:
            nonlocal reviewed
            reviewed = True
            return CandidateReview(validation=_validation(state, submission))

        outcome = run_repair_loop(
            self.store,
            self.workspaces,
            lambda state, workspace, context: ArchitectDecision(
                action=RepairAction.UNRESOLVED,
                rationale="Material and view trade-off needs user authorship.",
            ),
            reviewer,
            _no_experts,
        )

        self.assertIs(outcome.status, RepairStatus.UNRESOLVED)
        self.assertFalse(reviewed)
        self.assertEqual(len(outcome.iterations), 1)
        self.assertEqual(self.store.read(), self.initial)

    def test_exact_environment_recovery_returns_to_architect(self) -> None:
        seen_obligations: list[tuple[str, ...]] = []

        def architect(
            _: CanonicalState,
            __: WorkspaceRef,
            context: RepairContext,
        ) -> ArchitectDecision:
            seen_obligations.append(
                tuple(item.finding_code for item in context.obligations)
            )
            if context.iteration == 1:
                raise RuntimeError("preview failed before write")
            return ArchitectDecision(
                action=RepairAction.UNRESOLVED,
                rationale="Keep the environment response open.",
            )

        def recover(
            state: CanonicalState,
            workspace: WorkspaceRef,
            _: RepairContext,
            __: Exception,
        ) -> ArchitectFailureRecovery:
            return ArchitectFailureRecovery(
                base_state=state.ref,
                workspace_id=workspace.workspace_id,
                obligations=(
                    RepairObligation(
                        obligation_id="repair-environment",
                        statement="Resolve the observed environment relationship.",
                        finding_code="environment.relationship.unresolved",
                        source_receipt_id="receipt-environment",
                        evidence_refs=("observation://environment",),
                    ),
                ),
                evidence_refs=("receipt://environment",),
                observation_ref="observation://environment",
                message="Environment feedback compiled for the next decision.",
            )

        outcome = run_repair_loop(
            self.store,
            self.workspaces,
            architect,
            lambda state, submission: CandidateReview(
                validation=_validation(state, submission)
            ),
            _no_experts,
            architect_error_recovery=recover,
        )

        self.assertIs(outcome.status, RepairStatus.UNRESOLVED)
        self.assertEqual(len(outcome.iterations), 2)
        self.assertIsNone(outcome.iterations[0].action)
        self.assertEqual(
            outcome.iterations[0].environment_observation_ref,
            "observation://environment",
        )
        self.assertIn(
            "environment.relationship.unresolved",
            seen_obligations[1],
        )
        self.assertEqual(self.store.read(), self.initial)


if __name__ == "__main__":
    unittest.main()
