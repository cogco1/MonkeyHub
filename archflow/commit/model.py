"""External promotion package and commit receipt; neither is canonical state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from archflow.evaluation import EvaluationObservation
from archflow.interaction import (
    CandidateApprovalPolicy,
    CandidateApprovalReceipt,
)
from archflow.state import StateRef
from archflow.validation.commitments import CommitmentMonitorReceipt
from archflow.validation.model import ValidationReceipt
from archflow.contracts.canonical import canonical_digest, canonical_json

if TYPE_CHECKING:
    from archflow.runtime.candidate_assembly import (
        CandidateAssembly,
        CandidateExecutionHandoff,
    )
    from archflow.runtime.player_control import (
        CandidatePromotionReadiness,
    )


class PromotionPackageError(ValueError):
    """The exact production decision package is incomplete or inconsistent."""


def _require_exact_execution(
    assembly: CandidateAssembly,
    execution: CandidateExecutionHandoff,
) -> None:
    source = assembly.submission
    executed = execution.submission
    if (
        execution.assembly_digest != assembly.assembly_digest
        or execution.source_submission_id != source.submission_id
        or execution.plan_digest != assembly.plan.plan_digest
        or executed.submission_id == source.submission_id
        or executed.base != source.base
        or executed.workspace_id != source.workspace_id
    ):
        raise PromotionPackageError(
            "execution handoff does not bind the exact candidate assembly"
        )


@dataclass(frozen=True, slots=True)
class PromotionDecisionPackage:
    """Complete production handoff to the sole canonical writer.

    Approval, hard validation, commitment monitoring, and aesthetics remain
    independent inputs.  This package proves their common identity; it does
    not itself mutate canonical state.
    """

    assembly: CandidateAssembly
    execution: CandidateExecutionHandoff
    approval_policy: CandidateApprovalPolicy
    approval: CandidateApprovalReceipt
    hard_validation: ValidationReceipt
    commitment_monitor: CommitmentMonitorReceipt
    evaluations: tuple[EvaluationObservation, ...]
    readiness: CandidatePromotionReadiness
    compiled_at_utc: str

    SCHEMA = "PromotionDecisionPackage@1"

    def __post_init__(self) -> None:
        from archflow.runtime.candidate_assembly import (
            CandidateAssembly,
            CandidateExecutionHandoff,
        )
        from archflow.runtime.player_control import (
            CandidatePromotionReadiness,
            assess_candidate_promotion_readiness,
        )

        if not isinstance(self.assembly, CandidateAssembly):
            raise TypeError("assembly must be CandidateAssembly")
        if not isinstance(self.execution, CandidateExecutionHandoff):
            raise TypeError("execution must be CandidateExecutionHandoff")
        if not isinstance(
            self.approval_policy,
            CandidateApprovalPolicy,
        ):
            raise TypeError(
                "approval_policy must be CandidateApprovalPolicy"
            )
        if not isinstance(self.approval, CandidateApprovalReceipt):
            raise TypeError("approval must be CandidateApprovalReceipt")
        if not isinstance(self.hard_validation, ValidationReceipt):
            raise TypeError("hard_validation must be ValidationReceipt")
        if not isinstance(
            self.commitment_monitor,
            CommitmentMonitorReceipt,
        ):
            raise TypeError(
                "commitment_monitor must be CommitmentMonitorReceipt"
            )
        if (
            not isinstance(self.evaluations, tuple)
            or any(
                not isinstance(item, EvaluationObservation)
                for item in self.evaluations
            )
        ):
            raise TypeError(
                "evaluations must contain EvaluationObservation values"
            )
        if not isinstance(self.readiness, CandidatePromotionReadiness):
            raise TypeError(
                "readiness must be CandidatePromotionReadiness"
            )
        _require_exact_execution(self.assembly, self.execution)
        expected = assess_candidate_promotion_readiness(
            self.assembly,
            self.approval_policy,
            self.approval,
            self.hard_validation,
            self.commitment_monitor,
            now_utc=self.compiled_at_utc,
            review_submission=self.execution.submission,
        )
        if expected != self.readiness or not expected.ready:
            raise PromotionPackageError(
                "candidate is not ready for single-writer promotion"
            )
        submission = self.execution.submission
        for observation in self.evaluations:
            if (
                observation.submission_id != submission.submission_id
                or observation.checked_state != submission.base
            ):
                raise PromotionPackageError(
                    "aesthetic evaluation targets another submission or base"
                )

    @property
    def submission(self):
        return self.execution.submission

    @property
    def package_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def package_id(self) -> str:
        return f"promotion-package-{self.package_digest[:20]}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_assembly_digest": self.assembly.assembly_digest,
            "execution_handoff_digest": self.execution.handoff_digest,
            "submission_id": self.submission.submission_id,
            "plan_digest": self.assembly.plan.plan_digest,
            "base": {
                "project_id": self.submission.base.project_id,
                "version": self.submission.base.version,
                "state_sha256": self.submission.base.require_digest(),
            },
            "workspace_id": self.submission.workspace_id,
            "approval_policy_ref": self.approval_policy.policy_ref,
            "approval_policy_digest": self.approval_policy.policy_digest,
            "approval_receipt": self.approval.to_dict(),
            "hard_validation_receipt_id": (
                self.hard_validation.receipt_id
            ),
            "commitment_monitor_receipt_id": (
                self.commitment_monitor.receipt_id
            ),
            "evaluation_ids": [
                item.observation_id for item in self.evaluations
            ],
            "readiness": self.readiness.to_dict(),
            "compiled_at_utc": self.compiled_at_utc,
            "hard_gate_waiver_authority": False,
            "commitment_waiver_authority": False,
            "aesthetic_winner_authority": False,
            "canonical_write_authority": False,
        }


def build_promotion_decision_package(
    *,
    assembly: CandidateAssembly,
    execution: CandidateExecutionHandoff,
    approval_policy: CandidateApprovalPolicy,
    approval: CandidateApprovalReceipt | None,
    hard_validation: ValidationReceipt,
    commitment_monitor: CommitmentMonitorReceipt,
    evaluations: tuple[EvaluationObservation, ...] = (),
    now_utc: str,
) -> PromotionDecisionPackage:
    """Compile all independent receipts without granting any one a waiver."""

    from archflow.runtime.player_control import (
        assess_candidate_promotion_readiness,
    )

    if approval is None:
        raise PromotionPackageError(
            "required candidate approval receipt is missing"
        )
    try:
        readiness = assess_candidate_promotion_readiness(
            assembly,
            approval_policy,
            approval,
            hard_validation,
            commitment_monitor,
            now_utc=now_utc,
            review_submission=execution.submission,
        )
        return PromotionDecisionPackage(
            assembly=assembly,
            execution=execution,
            approval_policy=approval_policy,
            approval=approval,
            hard_validation=hard_validation,
            commitment_monitor=commitment_monitor,
            evaluations=evaluations,
            readiness=readiness,
            compiled_at_utc=now_utc,
        )
    except PromotionPackageError:
        raise
    except (TypeError, ValueError) as exc:
        raise PromotionPackageError(
            f"promotion decision package rejected: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    receipt_id: str
    submission_id: str
    submission_digest: str
    validation_receipt_id: str
    from_state: StateRef
    to_state: StateRef
    evaluation_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    approval_receipt_id: str | None = None
    commitment_monitor_receipt_id: str | None = None
    decision_package_id: str | None = None
