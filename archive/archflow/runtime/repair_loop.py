"""Bounded obligation-driven repair around a primary Architect."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from archive.archflow.commit.model import CommitReceipt
from archive.archflow.commit.committer import Committer
from archive.archflow.commit.store import InMemoryStateStore
from archive.archflow.evaluation.model import EvaluationObservation
from archflow.state.model import CanonicalState, StateRef
from archflow.submission.model import CandidateSubmission
from archive.archflow.submission.repair import RepairFinding, RepairObligation, obligations_from_findings
from archflow.validation.model import Severity, ValidationReceipt
from archive.archflow.workspace.manager import WorkspaceManager, WorkspaceRef

_MAX_ERROR_TEXT = 500


class RepairAction(StrEnum):
    REVISE = "revise"
    REPLACE = "replace"
    UNRESOLVED = "unresolved"


class RepairStatus(StrEnum):
    COMMITTED = "committed"
    UNRESOLVED = "unresolved"
    STALE_BASE = "stale_base"
    REPEATED_FINDINGS = "repeated_findings"
    NO_PROGRESS = "no_progress"
    ITERATION_LIMIT = "iteration_limit"
    WALL_TIME_LIMIT = "wall_time_limit"
    ARCHITECT_ERROR = "architect_error"
    REVIEW_ERROR = "review_error"
    ENVIRONMENT_BLOCKED = "environment_blocked"


class ConsultationStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RepairLimits:
    max_iterations: int = 6
    max_wall_seconds: float = 120.0
    repeated_finding_limit: int = 2
    no_progress_limit: int = 2

    def __post_init__(self) -> None:
        for value, field in (
            (self.max_iterations, "max_iterations"),
            (self.repeated_finding_limit, "repeated_finding_limit"),
            (self.no_progress_limit, "no_progress_limit"),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        if not isinstance(self.max_wall_seconds, (int, float)):
            raise TypeError("max_wall_seconds must be numeric")
        if not 0 < self.max_wall_seconds <= 3_600:
            raise ValueError("max_wall_seconds must be inside (0, 3600]")


@dataclass(frozen=True, slots=True)
class ExpertSelection:
    """Set-like expert discovery result plus auditable advice references."""

    expert_ids: frozenset[str] = frozenset()
    receipt_refs: tuple[str, ...] = ()
    findings: tuple[RepairFinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.expert_ids, frozenset):
            raise TypeError("expert_ids must be a frozenset")
        if not isinstance(self.receipt_refs, tuple):
            raise TypeError("receipt_refs must be a tuple")
        if not isinstance(self.findings, tuple):
            raise TypeError("findings must be a tuple")
        if len(self.expert_ids) > 32 or len(self.receipt_refs) > 64:
            raise ValueError("expert selection exceeds bounded output")
        if len(self.findings) > 64:
            raise ValueError("expert findings exceed bounded output")
        for value in (*self.expert_ids, *self.receipt_refs):
            if not isinstance(value, str) or not value.strip() or len(value) > 1_000:
                raise ValueError("expert identifiers and receipts must be bounded text")


@dataclass(frozen=True, slots=True)
class ExpertConsultationReceipt:
    consultation_id: str
    base_state: StateRef
    obligation_ids: tuple[str, ...]
    status: ConsultationStatus
    selected_experts: tuple[str, ...] = ()
    advice_receipt_refs: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RepairContext:
    """Detached decision context; it carries no writer or world handle."""

    base_state: StateRef
    iteration: int
    obligations: tuple[RepairObligation, ...]
    evidence_refs: tuple[str, ...]
    consultation: ExpertConsultationReceipt | None = None


@dataclass(frozen=True, slots=True)
class ArchitectDecision:
    action: RepairAction
    rationale: str
    submission: CandidateSubmission | None = None

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("rationale must be non-empty")
        if self.action is RepairAction.UNRESOLVED and self.submission is not None:
            raise ValueError("unresolved decisions cannot contain a submission")
        if self.action is not RepairAction.UNRESOLVED and self.submission is None:
            raise ValueError("revise/replace decisions require a submission")


@dataclass(frozen=True, slots=True)
class ArchitectFailureRecovery:
    """Exact-bound environment feedback returned instead of a design edit."""

    base_state: StateRef
    workspace_id: str
    obligations: tuple[RepairObligation, ...]
    evidence_refs: tuple[str, ...]
    observation_ref: str
    message: str
    retry_stop_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        if not isinstance(self.obligations, tuple):
            raise TypeError("obligations must be a tuple")
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")
        if not self.observation_ref.strip() or not self.message.strip():
            raise ValueError("observation_ref and message must be non-empty")
        if not self.obligations and self.retry_stop_ref is None:
            raise ValueError(
                "failure recovery requires obligations or a retry stop receipt"
            )


@dataclass(frozen=True, slots=True)
class CandidateReview:
    """Hard decision package produced outside the repair controller."""

    validation: ValidationReceipt
    hard_findings: tuple[RepairFinding, ...] = ()
    evaluations: tuple[EvaluationObservation, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.hard_findings, "hard_findings"),
            (self.evaluations, "evaluations"),
            (self.evidence_refs, "evidence_refs"),
        ):
            if not isinstance(value, tuple):
                raise TypeError(f"{field} must be a tuple")

    @property
    def accepted(self) -> bool:
        return self.validation.passed and not self.hard_findings


@dataclass(frozen=True, slots=True)
class RepairIterationReceipt:
    iteration: int
    workspace_id: str
    action: RepairAction | None
    submission_id: str | None
    validation_receipt_id: str | None
    finding_codes: tuple[str, ...]
    input_obligations: tuple[RepairObligation, ...]
    output_obligations: tuple[RepairObligation, ...]
    consultation: ExpertConsultationReceipt
    artifact_signature: tuple[tuple[str, str], ...]
    accepted: bool
    environment_observation_ref: str | None = None
    recovered_error: str | None = None
    retry_stop_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    status: RepairStatus
    before: StateRef
    after: StateRef
    iterations: tuple[RepairIterationReceipt, ...]
    commit: CommitReceipt | None = None
    message: str | None = None

    @property
    def committed(self) -> bool:
        return self.status is RepairStatus.COMMITTED and self.commit is not None


class PrimaryArchitect(Protocol):
    def __call__(
        self,
        state: CanonicalState,
        workspace: WorkspaceRef,
        context: RepairContext,
    ) -> ArchitectDecision: ...


class CandidateReviewer(Protocol):
    def __call__(
        self,
        state: CanonicalState,
        submission: CandidateSubmission,
    ) -> CandidateReview: ...


ExpertAdvisor = Callable[[RepairContext], ExpertSelection]
ArchitectErrorRecovery = Callable[
    [CanonicalState, WorkspaceRef, RepairContext, Exception],
    ArchitectFailureRecovery | None,
]


def run_repair_loop(
    store: InMemoryStateStore,
    workspace_manager: WorkspaceManager,
    architect: PrimaryArchitect,
    reviewer: CandidateReviewer,
    expert_advisor: ExpertAdvisor,
    *,
    limits: RepairLimits = RepairLimits(),
    clock: Callable[[], float] = time.monotonic,
    architect_error_recovery: ArchitectErrorRecovery | None = None,
) -> RepairOutcome:
    """Run bounded attempts and stop immediately after one accepted commit."""

    baseline = store.read()
    started = clock()
    iterations: list[RepairIterationReceipt] = []
    obligations = _canonical_obligations(baseline)
    evidence_refs = tuple(
        dict.fromkeys(
            (
                *(item.artifact_id for item in baseline.artifacts),
                *baseline.evaluation_refs,
            )
        )
    )
    prior_finding_signature: tuple[tuple[str, str], ...] | None = None
    repeated_findings = 0
    prior_progress_signature: tuple[object, ...] | None = None
    no_progress = 0

    for iteration_number in range(1, limits.max_iterations + 1):
        if _expired(clock, started, limits):
            return _outcome(
                RepairStatus.WALL_TIME_LIMIT,
                baseline,
                store,
                iterations,
                message="wall-time limit reached before the next iteration",
            )
        if store.read().ref != baseline.ref:
            return _outcome(
                RepairStatus.STALE_BASE,
                baseline,
                store,
                iterations,
                message="canonical state changed during repair",
            )

        workspace = workspace_manager.fork(baseline)
        detached_context = RepairContext(
            base_state=baseline.ref,
            iteration=iteration_number,
            obligations=obligations,
            evidence_refs=evidence_refs,
        )
        selection, consultation = _consult(expert_advisor, detached_context)
        expert_obligations = obligations_from_findings(selection.findings)
        decision_obligations = _merge_obligations(obligations, expert_obligations)
        context = replace(
            detached_context,
            obligations=decision_obligations,
            consultation=consultation,
        )

        try:
            decision = architect(baseline, workspace, context)
        except Exception as exc:
            recovery = _recover_architect_error(
                architect_error_recovery,
                baseline,
                workspace,
                context,
                exc,
            )
            if recovery is not None:
                if (
                    recovery.base_state != baseline.ref
                    or recovery.workspace_id != workspace.workspace_id
                ):
                    return _outcome(
                        RepairStatus.STALE_BASE,
                        baseline,
                        store,
                        iterations,
                        message=(
                            "environment recovery is not bound to this "
                            "iteration base/workspace"
                        ),
                    )
                iterations.append(
                    _environment_iteration_receipt(
                        iteration_number,
                        workspace,
                        consultation,
                        context,
                        recovery,
                        exc,
                    )
                )
                if recovery.retry_stop_ref is not None:
                    return _outcome(
                        RepairStatus.ENVIRONMENT_BLOCKED,
                        baseline,
                        store,
                        iterations,
                        message=recovery.message,
                    )
                obligations = recovery.obligations
                evidence_refs = tuple(
                    dict.fromkeys(
                        (
                            *recovery.evidence_refs,
                            recovery.observation_ref,
                        )
                    )
                )
                continue
            return _outcome(
                RepairStatus.ARCHITECT_ERROR,
                baseline,
                store,
                iterations,
                message=_error(exc),
            )
        if decision.action is RepairAction.UNRESOLVED:
            iterations.append(
                _iteration_receipt(
                    iteration_number,
                    workspace,
                    decision,
                    consultation,
                    input_obligations=decision_obligations,
                )
            )
            return _outcome(
                RepairStatus.UNRESOLVED,
                baseline,
                store,
                iterations,
                message=decision.rationale,
            )

        submission = decision.submission
        assert submission is not None
        if (
            submission.base != baseline.ref
            or submission.workspace_id != workspace.workspace_id
        ):
            return _outcome(
                RepairStatus.STALE_BASE,
                baseline,
                store,
                iterations,
                message="submission is not bound to this iteration base/workspace",
            )
        try:
            review = reviewer(baseline, submission)
        except Exception as exc:
            return _outcome(
                RepairStatus.REVIEW_ERROR,
                baseline,
                store,
                iterations,
                message=_error(exc),
            )
        binding_error = _review_binding_error(baseline, submission, review)
        if binding_error is not None:
            return _outcome(
                RepairStatus.STALE_BASE,
                baseline,
                store,
                iterations,
                message=binding_error,
            )

        findings = _all_hard_findings(review)
        next_obligations = obligations_from_findings(findings)
        artifact_signature = _artifact_signature(submission)
        iterations.append(
            _iteration_receipt(
                iteration_number,
                workspace,
                decision,
                consultation,
                submission=submission,
                review=review,
                findings=findings,
                input_obligations=decision_obligations,
                output_obligations=next_obligations,
                artifact_signature=artifact_signature,
            )
        )

        if review.accepted:
            if _expired(clock, started, limits):
                return _outcome(
                    RepairStatus.WALL_TIME_LIMIT,
                    baseline,
                    store,
                    iterations,
                    message="wall-time limit reached before commit",
                )
            if store.read().ref != baseline.ref:
                return _outcome(
                    RepairStatus.STALE_BASE,
                    baseline,
                    store,
                    iterations,
                    message="canonical state changed before commit",
                )
            commit = Committer(store).commit(
                submission,
                review.validation,
                review.evaluations,
            )
            return RepairOutcome(
                status=RepairStatus.COMMITTED,
                before=baseline.ref,
                after=store.read().ref,
                iterations=tuple(iterations),
                commit=commit,
            )

        finding_signature = tuple(
            sorted((item.code, item.message) for item in findings)
        )
        if finding_signature == prior_finding_signature:
            repeated_findings += 1
        else:
            repeated_findings = 1
        prior_finding_signature = finding_signature
        if repeated_findings >= limits.repeated_finding_limit:
            return _outcome(
                RepairStatus.REPEATED_FINDINGS,
                baseline,
                store,
                iterations,
                message="identical hard findings survived a targeted repair",
            )

        progress_signature: tuple[object, ...] = (
            artifact_signature,
            tuple(item.obligation_id for item in next_obligations),
        )
        if progress_signature == prior_progress_signature:
            no_progress += 1
        else:
            no_progress = 0
        prior_progress_signature = progress_signature
        if no_progress >= limits.no_progress_limit:
            return _outcome(
                RepairStatus.NO_PROGRESS,
                baseline,
                store,
                iterations,
                message="artifact and obligation set did not change",
            )

        obligations = next_obligations
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    *submission.evidence_refs,
                    *review.evidence_refs,
                    *(ref for item in findings for ref in item.evidence_refs),
                )
            )
        )

    return _outcome(
        RepairStatus.ITERATION_LIMIT,
        baseline,
        store,
        iterations,
        message=f"iteration limit reached ({limits.max_iterations})",
    )


def _canonical_obligations(
    state: CanonicalState,
) -> tuple[RepairObligation, ...]:
    return tuple(
        RepairObligation(
            obligation_id=item.obligation_id,
            statement=item.statement,
            finding_code="canonical.open_obligation",
            source_receipt_id=item.source_ref,
        )
        for item in state.open_obligations
    )


def _consult(
    advisor: ExpertAdvisor,
    context: RepairContext,
) -> tuple[ExpertSelection, ExpertConsultationReceipt]:
    try:
        selection = advisor(context)
        if not isinstance(selection, ExpertSelection):
            raise TypeError("expert advisor must return ExpertSelection")
        status = ConsultationStatus.OK
        error = None
    except Exception as exc:
        selection = ExpertSelection()
        status = ConsultationStatus.ERROR
        error = _error(exc)
    identity = {
        "base": [context.base_state.run_id, context.base_state.version],
        "iteration": context.iteration,
        "obligations": [item.obligation_id for item in context.obligations],
        "status": status.value,
        "experts": sorted(selection.expert_ids),
        "receipts": selection.receipt_refs,
        "error": error,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt = ExpertConsultationReceipt(
        consultation_id=f"expert-consultation-{digest[:20]}",
        base_state=context.base_state,
        obligation_ids=tuple(item.obligation_id for item in context.obligations),
        status=status,
        selected_experts=tuple(sorted(selection.expert_ids)),
        advice_receipt_refs=selection.receipt_refs,
        error=error,
    )
    return selection, receipt


def _all_hard_findings(review: CandidateReview) -> tuple[RepairFinding, ...]:
    validation_findings = tuple(
        RepairFinding(
            code=item.code,
            message=item.message,
            source_receipt_id=review.validation.receipt_id,
            evidence_refs=item.evidence_refs,
        )
        for item in review.validation.findings
        if item.severity is Severity.ERROR
    )
    return (*validation_findings, *review.hard_findings)


def _review_binding_error(
    baseline: CanonicalState,
    submission: CandidateSubmission,
    review: CandidateReview,
) -> str | None:
    if review.validation.submission_id != submission.submission_id:
        return "validation belongs to another submission"
    if review.validation.checked_state != baseline.ref:
        return "validation checked another base state"
    for evaluation in review.evaluations:
        if evaluation.submission_id != submission.submission_id:
            return "evaluation belongs to another submission"
        if evaluation.checked_state != baseline.ref:
            return "evaluation checked another base state"
    return None


def _merge_obligations(
    first: tuple[RepairObligation, ...],
    second: tuple[RepairObligation, ...],
) -> tuple[RepairObligation, ...]:
    merged = {item.obligation_id: item for item in (*first, *second)}
    return tuple(sorted(merged.values(), key=lambda item: item.obligation_id))


def _artifact_signature(
    submission: CandidateSubmission,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (item.artifact_id, item.sha256)
            for item in submission.delta.artifacts_add
        )
    )


def _iteration_receipt(
    iteration: int,
    workspace: WorkspaceRef,
    decision: ArchitectDecision,
    consultation: ExpertConsultationReceipt,
    *,
    submission: CandidateSubmission | None = None,
    review: CandidateReview | None = None,
    findings: tuple[RepairFinding, ...] = (),
    input_obligations: tuple[RepairObligation, ...] = (),
    output_obligations: tuple[RepairObligation, ...] = (),
    artifact_signature: tuple[tuple[str, str], ...] = (),
) -> RepairIterationReceipt:
    return RepairIterationReceipt(
        iteration=iteration,
        workspace_id=workspace.workspace_id,
        action=decision.action,
        submission_id=submission.submission_id if submission else None,
        validation_receipt_id=(
            review.validation.receipt_id if review is not None else None
        ),
        finding_codes=tuple(item.code for item in findings),
        input_obligations=input_obligations,
        output_obligations=output_obligations,
        consultation=consultation,
        artifact_signature=artifact_signature,
        accepted=review.accepted if review is not None else False,
    )


def _environment_iteration_receipt(
    iteration: int,
    workspace: WorkspaceRef,
    consultation: ExpertConsultationReceipt,
    context: RepairContext,
    recovery: ArchitectFailureRecovery,
    error: Exception,
) -> RepairIterationReceipt:
    return RepairIterationReceipt(
        iteration=iteration,
        workspace_id=workspace.workspace_id,
        action=None,
        submission_id=None,
        validation_receipt_id=None,
        finding_codes=tuple(
            item.finding_code for item in recovery.obligations
        ),
        input_obligations=context.obligations,
        output_obligations=recovery.obligations,
        consultation=consultation,
        artifact_signature=(),
        accepted=False,
        environment_observation_ref=recovery.observation_ref,
        recovered_error=_error(error),
        retry_stop_ref=recovery.retry_stop_ref,
    )


def _recover_architect_error(
    handler: ArchitectErrorRecovery | None,
    state: CanonicalState,
    workspace: WorkspaceRef,
    context: RepairContext,
    error: Exception,
) -> ArchitectFailureRecovery | None:
    if handler is None:
        return None
    try:
        recovery = handler(state, workspace, context, error)
    except Exception:
        return None
    if recovery is not None and not isinstance(
        recovery, ArchitectFailureRecovery
    ):
        return None
    return recovery


def _expired(
    clock: Callable[[], float],
    started: float,
    limits: RepairLimits,
) -> bool:
    return clock() - started >= limits.max_wall_seconds


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_TEXT]


def _outcome(
    status: RepairStatus,
    baseline: CanonicalState,
    store: InMemoryStateStore,
    iterations: list[RepairIterationReceipt],
    *,
    message: str,
) -> RepairOutcome:
    return RepairOutcome(
        status=status,
        before=baseline.ref,
        after=store.read().ref,
        iterations=tuple(iterations),
        message=message[:_MAX_ERROR_TEXT],
    )
