"""Read-only deterministic monitors for active normative commitments.

Criterion providers measure a proposed candidate and return exact-base
observations.  This module compiles those observations into findings,
future-facing temporal progress, and typed repair obligations.  It has no
candidate, geometry, repository, aesthetic, expert, or commitment writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.state.commitments import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    RevisionPolicy,
)
from archflow.state.operational_state import (
    DependencyEdge,
    DependencyEffect,
    DesignObligation,
    FactValue,
    ObligationCondition,
    ObligationStatus,
    OperationalMarkovState,
    require_local_id,
    require_logical_ref,
)
from archflow.submission.commitment_revision import (
    CommitmentRevisionProposal,
)
from archflow.contracts.canonical import canonical_digest, canonical_json, require_sha256


_MAX_ITEMS = 4096
_HEX = frozenset("0123456789abcdef")


class CommitmentMonitorError(ValueError):
    """The monitor input is stale, cross-branch, or structurally invalid."""


class CriterionOutcome(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


class TemporalSemantics(StrEnum):
    EVENTUALLY = "eventually"
    ONCE_ACTIVATED = "once_activated"


class TemporalMonitorStage(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    ACHIEVED = "achieved"
    VIOLATED = "violated"


class CommitmentProgressOutcome(StrEnum):
    INACTIVE = "inactive"
    PENDING = "pending"
    ACHIEVED = "achieved"
    PRESERVED = "preserved"
    VIOLATED = "violated"
    EVIDENCE_BLOCKED = "evidence_blocked"


class CommitmentFindingSeverity(StrEnum):
    HARD_FAILURE = "hard_failure"
    REVISION_REQUIRED = "revision_required"
    ADVISORY = "advisory"


class CommitmentResolutionAction(StrEnum):
    SUPPLY_EVIDENCE = "supply_evidence"
    REPAIR_CANDIDATE = "repair_candidate"
    REQUEST_AUTHORIZED_REVISION = "request_authorized_revision"
    RECORD_TRADEOFF = "record_tradeoff"


class DependencyImpactKind(StrEnum):
    INVALIDATED = "invalidated"
    REVALIDATION_REQUIRED = "revalidation_required"


# Commitment statuses the monitor evaluates; promotion coverage checks must
# use this same set so the two definitions can never drift apart.
MONITORED_COMMITMENT_STATUSES = frozenset(
    {
        CommitmentStatus.ACCEPTED,
        CommitmentStatus.ACTIVE,
        CommitmentStatus.VIOLATED,
    }
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _tuple(value: object, field: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


def _branch_to_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.state_sha256,
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _branch_from_dict(value: object) -> BranchRef:
    if not isinstance(value, Mapping):
        raise TypeError("branch must be an object")
    if set(value) != {
        "project_id",
        "run_id",
        "base",
        "branch_id",
        "epoch",
    }:
        raise ValueError("branch schema drifted")
    base = value["base"]
    if not isinstance(base, Mapping) or set(base) != {
        "version",
        "state_sha256",
    }:
        raise ValueError("branch base schema drifted")
    project_id = value["project_id"]
    return BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=value["run_id"],
            base=ProjectVersionRef(
                project_id=project_id,
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
        ),
        branch_id=value["branch_id"],
        epoch=value["epoch"],
    )


def _same_branch(left: BranchRef, right: BranchRef) -> bool:
    return (
        left.run == right.run
        and left.branch_id == right.branch_id
    )


def _commitment_ref(commitment_id: str) -> str:
    candidate = f"commitment:{commitment_id}"
    try:
        return require_logical_ref(candidate, "commitment ref")
    except ValueError:
        return f"commitment-id:{canonical_digest(commitment_id)}"


def _criterion_observation_ref(
    provider_id: str,
    criterion_id: str,
) -> str:
    return (
        "criterion-observation:"
        f"{canonical_digest((provider_id, criterion_id))}"
    )


@dataclass(frozen=True, slots=True)
class CriterionObservation:
    """A provider result bound to one candidate and operational base."""

    observation_id: str
    candidate_id: str
    branch: BranchRef
    base_state_digest: str
    provider_id: str
    criterion_id: str
    outcome: CriterionOutcome
    measurement: FactValue | object
    threshold: FactValue | object
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.observation_id, "observation_id")
        require_local_id(self.candidate_id, "candidate_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            require_sha256(self.base_state_digest, "base_state_digest"),
        )
        _text(self.provider_id, "provider_id")
        _text(self.criterion_id, "criterion_id")
        if not isinstance(self.outcome, CriterionOutcome):
            raise TypeError("outcome must be a CriterionOutcome")
        object.__setattr__(
            self,
            "measurement",
            FactValue.from_value(self.measurement),
        )
        object.__setattr__(
            self,
            "threshold",
            FactValue.from_value(self.threshold),
        )
        _tuple(self.evidence_refs, "evidence_refs")
        if not self.evidence_refs:
            raise ValueError("criterion observation requires evidence")
        for ref in self.evidence_refs:
            require_logical_ref(ref, "evidence_ref")
        _unique(self.evidence_refs, "evidence_refs")

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider_id, self.criterion_id)


@dataclass(frozen=True, slots=True)
class TemporalMonitorSpec:
    commitment_id: str
    semantics: TemporalSemantics

    def __post_init__(self) -> None:
        _text(self.commitment_id, "commitment_id")
        if not isinstance(self.semantics, TemporalSemantics):
            raise TypeError("semantics must be TemporalSemantics")


@dataclass(frozen=True, slots=True)
class TemporalMonitorState:
    """Minimal temporal memory; direct predicates do not create this object."""

    commitment_id: str
    semantics: TemporalSemantics
    stage: TemporalMonitorStage
    activation_latched: bool
    satisfaction_seen: bool
    observed_branch: BranchRef
    observed_base_state_digest: str
    last_candidate_id: str

    def __post_init__(self) -> None:
        _text(self.commitment_id, "commitment_id")
        if not isinstance(self.semantics, TemporalSemantics):
            raise TypeError("semantics must be TemporalSemantics")
        if not isinstance(self.stage, TemporalMonitorStage):
            raise TypeError("stage must be TemporalMonitorStage")
        if type(self.activation_latched) is not bool:
            raise TypeError("activation_latched must be bool")
        if type(self.satisfaction_seen) is not bool:
            raise TypeError("satisfaction_seen must be bool")
        if not isinstance(self.observed_branch, BranchRef):
            raise TypeError("observed_branch must be BranchRef")
        object.__setattr__(
            self,
            "observed_base_state_digest",
            require_sha256(
                self.observed_base_state_digest,
                "observed_base_state_digest",
            ),
        )
        require_local_id(self.last_candidate_id, "last_candidate_id")
        if (
            self.stage is TemporalMonitorStage.ACHIEVED
            and not self.satisfaction_seen
        ):
            raise ValueError("achieved monitor must remember satisfaction")
        if (
            self.stage is not TemporalMonitorStage.INACTIVE
            and self.semantics is TemporalSemantics.ONCE_ACTIVATED
            and not self.activation_latched
        ):
            raise ValueError(
                "once-activated monitor cannot progress before activation"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "TemporalMonitorState@1",
            "commitment_id": self.commitment_id,
            "semantics": self.semantics.value,
            "stage": self.stage.value,
            "activation_latched": self.activation_latched,
            "satisfaction_seen": self.satisfaction_seen,
            "observed_branch": _branch_to_dict(self.observed_branch),
            "observed_base_state_digest": (
                self.observed_base_state_digest
            ),
            "last_candidate_id": self.last_candidate_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> TemporalMonitorState:
        if not isinstance(value, Mapping):
            raise TypeError("temporal monitor state must be an object")
        if set(value) != {
            "schema",
            "commitment_id",
            "semantics",
            "stage",
            "activation_latched",
            "satisfaction_seen",
            "observed_branch",
            "observed_base_state_digest",
            "last_candidate_id",
        } or value["schema"] != "TemporalMonitorState@1":
            raise ValueError("temporal monitor state schema drifted")
        return cls(
            commitment_id=value["commitment_id"],
            semantics=TemporalSemantics(value["semantics"]),
            stage=TemporalMonitorStage(value["stage"]),
            activation_latched=value["activation_latched"],
            satisfaction_seen=value["satisfaction_seen"],
            observed_branch=_branch_from_dict(value["observed_branch"]),
            observed_base_state_digest=value[
                "observed_base_state_digest"
            ],
            last_candidate_id=value["last_candidate_id"],
        )


def commitment_content_digest(commitment: Commitment) -> str:
    """Digest of the exact commitment content a monitor evaluated.

    Promotion compares this against the canonical commitment so a same-id
    lookalike with weakened strength or a different criterion can never
    satisfy the completion boundary on the canonical commitment's behalf.
    """

    if not isinstance(commitment, Commitment):
        raise TypeError("commitment must be Commitment")
    return canonical_digest(commitment.to_dict())


@dataclass(frozen=True, slots=True)
class CommitmentProgress:
    commitment_id: str
    kind: CommitmentKind
    commitment_digest: str
    outcome: CommitmentProgressOutcome
    recommended_status: CommitmentStatus | None
    temporal_state_ref: str | None = None

    def __post_init__(self) -> None:
        _text(self.commitment_id, "commitment_id")
        if not isinstance(self.kind, CommitmentKind):
            raise TypeError("kind must be CommitmentKind")
        object.__setattr__(
            self,
            "commitment_digest",
            require_sha256(self.commitment_digest, "commitment_digest"),
        )
        if not isinstance(self.outcome, CommitmentProgressOutcome):
            raise TypeError("outcome must be CommitmentProgressOutcome")
        if self.recommended_status is not None and not isinstance(
            self.recommended_status,
            CommitmentStatus,
        ):
            raise TypeError(
                "recommended_status must be CommitmentStatus or None"
            )
        if self.temporal_state_ref is not None:
            require_logical_ref(
                self.temporal_state_ref,
                "temporal_state_ref",
            )


@dataclass(frozen=True, slots=True)
class CommitmentFinding:
    finding_id: str
    code: str
    message: str
    commitment_id: str
    kind: CommitmentKind
    strength: CommitmentStrength
    criterion_id: str
    measurement_json: str
    threshold_json: str
    evidence_refs: tuple[str, ...]
    dependency_path: tuple[str, ...]
    permitted_actions: tuple[CommitmentResolutionAction, ...]
    severity: CommitmentFindingSeverity

    def __post_init__(self) -> None:
        require_local_id(self.finding_id, "finding_id")
        for value, field in (
            (self.code, "code"),
            (self.message, "message"),
            (self.commitment_id, "commitment_id"),
            (self.criterion_id, "criterion_id"),
        ):
            _text(value, field)
        if not isinstance(self.kind, CommitmentKind):
            raise TypeError("kind must be CommitmentKind")
        if not isinstance(self.strength, CommitmentStrength):
            raise TypeError("strength must be CommitmentStrength")
        FactValue(self.measurement_json)
        FactValue(self.threshold_json)
        _tuple(self.evidence_refs, "evidence_refs")
        for ref in self.evidence_refs:
            require_logical_ref(ref, "evidence_ref")
        _unique(self.evidence_refs, "evidence_refs")
        _tuple(self.dependency_path, "dependency_path")
        if not self.dependency_path:
            raise ValueError("dependency_path cannot be empty")
        for ref in self.dependency_path:
            require_logical_ref(ref, "dependency path ref")
        _tuple(self.permitted_actions, "permitted_actions")
        if any(
            not isinstance(item, CommitmentResolutionAction)
            for item in self.permitted_actions
        ):
            raise TypeError(
                "permitted_actions must contain CommitmentResolutionAction"
            )
        if not isinstance(self.severity, CommitmentFindingSeverity):
            raise TypeError("severity must be CommitmentFindingSeverity")


@dataclass(frozen=True, slots=True)
class CommitmentDependencyImpact:
    target_ref: str
    impact: DependencyImpactKind
    dependency_path: tuple[str, ...]

    def __post_init__(self) -> None:
        require_logical_ref(self.target_ref, "target_ref")
        if not isinstance(self.impact, DependencyImpactKind):
            raise TypeError("impact must be DependencyImpactKind")
        _tuple(self.dependency_path, "dependency_path")
        if not self.dependency_path:
            raise ValueError("dependency_path cannot be empty")
        for ref in self.dependency_path:
            require_logical_ref(ref, "dependency path ref")


@dataclass(frozen=True, slots=True)
class CommitmentMonitorReceipt:
    receipt_id: str
    candidate_id: str
    branch: BranchRef
    base_state_digest: str
    completion_boundary: bool
    passed: bool
    progress: tuple[CommitmentProgress, ...]
    findings: tuple[CommitmentFinding, ...]
    obligations: tuple[DesignObligation, ...]
    dependency_impacts: tuple[CommitmentDependencyImpact, ...]
    temporal_states: tuple[TemporalMonitorState, ...]
    revision_proposals: tuple[CommitmentRevisionProposal, ...]

    def __post_init__(self) -> None:
        require_local_id(self.receipt_id, "receipt_id")
        require_local_id(self.candidate_id, "candidate_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            require_sha256(self.base_state_digest, "base_state_digest"),
        )
        if type(self.completion_boundary) is not bool:
            raise TypeError("completion_boundary must be bool")
        if type(self.passed) is not bool:
            raise TypeError("passed must be bool")
        for field, values, item_type in (
            ("progress", self.progress, CommitmentProgress),
            ("findings", self.findings, CommitmentFinding),
            ("obligations", self.obligations, DesignObligation),
            (
                "dependency_impacts",
                self.dependency_impacts,
                CommitmentDependencyImpact,
            ),
            (
                "temporal_states",
                self.temporal_states,
                TemporalMonitorState,
            ),
            (
                "revision_proposals",
                self.revision_proposals,
                CommitmentRevisionProposal,
            ),
        ):
            _tuple(values, field)
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"{field} contains the wrong value type")
        has_blocking_failure = any(
            item.severity
            in {
                CommitmentFindingSeverity.HARD_FAILURE,
                CommitmentFindingSeverity.REVISION_REQUIRED,
            }
            for item in self.findings
        )
        if self.passed == has_blocking_failure:
            raise ValueError(
                "passed must be true exactly when no blocking commitment "
                "finding exists"
            )

    @property
    def hard_failure_ids(self) -> tuple[str, ...]:
        return tuple(
            item.finding_id
            for item in self.findings
            if item.severity
            in {
                CommitmentFindingSeverity.HARD_FAILURE,
                CommitmentFindingSeverity.REVISION_REQUIRED,
            }
        )


def _observation_for(
    observations: Mapping[tuple[str, str], CriterionObservation],
    commitment: Commitment,
    *,
    activation: bool = False,
) -> CriterionObservation | None:
    criterion = (
        commitment.activation_criterion
        if activation
        else commitment.satisfaction_criterion
    )
    if criterion is None:
        return None
    return observations.get((criterion.provider_id, criterion.criterion_id))


def _finding_severity(
    commitment: Commitment,
) -> CommitmentFindingSeverity:
    if commitment.strength is CommitmentStrength.HARD:
        return CommitmentFindingSeverity.HARD_FAILURE
    if commitment.strength is CommitmentStrength.NEGOTIABLE:
        return CommitmentFindingSeverity.REVISION_REQUIRED
    return CommitmentFindingSeverity.ADVISORY


def _resolution_actions(
    commitment: Commitment,
    *,
    missing_evidence: bool = False,
) -> tuple[CommitmentResolutionAction, ...]:
    actions: list[CommitmentResolutionAction] = []
    if missing_evidence:
        actions.append(CommitmentResolutionAction.SUPPLY_EVIDENCE)
    else:
        actions.append(CommitmentResolutionAction.REPAIR_CANDIDATE)
    if (
        commitment.strength is CommitmentStrength.NEGOTIABLE
        and commitment.revision_policy is not RevisionPolicy.IMMUTABLE
    ):
        actions.append(
            CommitmentResolutionAction.REQUEST_AUTHORIZED_REVISION
        )
    if commitment.strength in {
        CommitmentStrength.PREFERENCE,
        CommitmentStrength.HYPOTHESIS,
    }:
        actions.append(CommitmentResolutionAction.RECORD_TRADEOFF)
    return tuple(actions)


def _make_finding(
    commitment: Commitment,
    *,
    code: str,
    message: str,
    observation: CriterionObservation | None,
    criterion_id: str,
    dependency_path: tuple[str, ...],
    candidate_ref: str,
    missing_evidence: bool | None = None,
) -> CommitmentFinding:
    missing = (
        observation is None
        or observation.outcome is CriterionOutcome.UNKNOWN
        if missing_evidence is None
        else missing_evidence
    )
    measurement = (
        FactValue.from_value("unknown")
        if observation is None
        else observation.measurement
    )
    threshold = (
        FactValue.from_value("criterion evidence required")
        if observation is None
        else observation.threshold
    )
    evidence_refs = (
        (candidate_ref,)
        if observation is None
        else observation.evidence_refs
    )
    payload = {
        "code": code,
        "commitment_id": commitment.commitment_id,
        "criterion_id": criterion_id,
        "measurement": measurement.canonical_json,
        "threshold": threshold.canonical_json,
        "evidence_refs": evidence_refs,
        "dependency_path": dependency_path,
    }
    return CommitmentFinding(
        finding_id=f"cmf-{canonical_digest(payload)[:24]}",
        code=code,
        message=message,
        commitment_id=commitment.commitment_id,
        kind=commitment.kind,
        strength=commitment.strength,
        criterion_id=criterion_id,
        measurement_json=measurement.canonical_json,
        threshold_json=threshold.canonical_json,
        evidence_refs=evidence_refs,
        dependency_path=dependency_path,
        permitted_actions=_resolution_actions(
            commitment,
            missing_evidence=missing,
        ),
        severity=_finding_severity(commitment),
    )


def _blocked_evidence_obligation(
    finding: CommitmentFinding,
    *,
    provider_id: str,
) -> DesignObligation:
    return DesignObligation(
        obligation_id=f"commitment-evidence-{finding.finding_id[4:]}",
        statement=(
            f"Supply criterion evidence before resolving "
            f"{finding.commitment_id}."
        ),
        source_ref=f"commitment-finding:{finding.finding_id}",
        status=ObligationStatus.BLOCKED,
        subject_refs=(_commitment_ref(finding.commitment_id),),
        validator_ref=(
            f"criterion-provider:{canonical_digest(provider_id)[:24]}"
        ),
        condition=ObligationCondition(
            ref=_criterion_observation_ref(
                provider_id,
                finding.criterion_id,
            ),
            expected_value=CriterionOutcome.SATISFIED.value,
        ),
    )


def _repair_obligation(finding: CommitmentFinding) -> DesignObligation:
    return DesignObligation(
        obligation_id=f"commitment-repair-{finding.finding_id[4:]}",
        statement=(
            f"Resolve commitment finding {finding.finding_id} without "
            "changing the commitment unless authorized."
        ),
        source_ref=f"commitment-finding:{finding.finding_id}",
        status=ObligationStatus.OPEN,
        subject_refs=(_commitment_ref(finding.commitment_id),),
    )


def _dependency_impacts(
    commitments: tuple[Commitment, ...],
    dependencies: tuple[DependencyEdge, ...],
    violated_commitment_ids: tuple[str, ...],
) -> tuple[CommitmentDependencyImpact, ...]:
    impacts: dict[
        tuple[str, DependencyImpactKind],
        CommitmentDependencyImpact,
    ] = {}
    commitment_by_id = {
        item.commitment_id: item for item in commitments
    }
    dependent_ids: dict[str, set[str]] = {}
    for item in commitments:
        for dependency_id in item.dependency_ids:
            dependent_ids.setdefault(dependency_id, set()).add(
                item.commitment_id
            )

    queue: list[tuple[str, tuple[str, ...]]] = [
        (
            commitment_id,
            (_commitment_ref(commitment_id),),
        )
        for commitment_id in sorted(violated_commitment_ids)
    ]
    seen_commitments = set(violated_commitment_ids)
    while queue:
        current_id, path = queue.pop(0)
        for dependent_id in sorted(dependent_ids.get(current_id, ())):
            if dependent_id not in commitment_by_id:
                continue
            target = _commitment_ref(dependent_id)
            next_path = (*path, target)
            impacts[(target, DependencyImpactKind.REVALIDATION_REQUIRED)] = (
                CommitmentDependencyImpact(
                    target_ref=target,
                    impact=DependencyImpactKind.REVALIDATION_REQUIRED,
                    dependency_path=next_path,
                )
            )
            if dependent_id not in seen_commitments:
                seen_commitments.add(dependent_id)
                queue.append((dependent_id, next_path))

    adjacency: dict[str, list[DependencyEdge]] = {}
    for edge in dependencies:
        if edge.effect not in {
            DependencyEffect.INVALIDATES,
            DependencyEffect.REQUIRES_REVALIDATION,
        }:
            continue
        adjacency.setdefault(edge.upstream_ref, []).append(edge)
    for commitment_id in violated_commitment_ids:
        start = _commitment_ref(commitment_id)
        path_by_ref: dict[str, tuple[str, ...]] = {start: (start,)}
        impact_by_ref: dict[str, DependencyImpactKind] = {
            start: DependencyImpactKind.INVALIDATED
        }
        walk = [start]
        while walk:
            upstream = walk.pop(0)
            for edge in sorted(
                adjacency.get(upstream, ()),
                key=lambda item: item.identity,
            ):
                path = (*path_by_ref[upstream], edge.downstream_ref)
                kind = (
                    DependencyImpactKind.INVALIDATED
                    if (
                        impact_by_ref[upstream]
                        is DependencyImpactKind.INVALIDATED
                        and edge.effect is DependencyEffect.INVALIDATES
                    )
                    else DependencyImpactKind.REVALIDATION_REQUIRED
                )
                previous = impact_by_ref.get(edge.downstream_ref)
                merged = (
                    DependencyImpactKind.INVALIDATED
                    if DependencyImpactKind.INVALIDATED
                    in {previous, kind}
                    else DependencyImpactKind.REVALIDATION_REQUIRED
                )
                key = (edge.downstream_ref, kind)
                if previous is merged:
                    continue
                if previous is not None:
                    impacts.pop((edge.downstream_ref, previous), None)
                impact_by_ref[edge.downstream_ref] = merged
                path_by_ref[edge.downstream_ref] = path
                impacts[(edge.downstream_ref, merged)] = (
                    CommitmentDependencyImpact(
                        target_ref=edge.downstream_ref,
                        impact=merged,
                        dependency_path=path,
                    )
                )
                if len(path_by_ref) > _MAX_ITEMS:
                    raise CommitmentMonitorError(
                        "dependency closure exceeds bounded item count"
                    )
                walk.append(edge.downstream_ref)
    return tuple(
        sorted(
            impacts.values(),
            key=lambda item: (
                item.target_ref,
                item.impact.value,
                item.dependency_path,
            ),
        )
    )


def _temporal_state(
    commitment: Commitment,
    spec: TemporalMonitorSpec,
    prior: TemporalMonitorState | None,
    *,
    active: bool,
    observation: CriterionObservation | None,
    state: OperationalMarkovState,
    candidate_id: str,
    completion_boundary: bool,
) -> TemporalMonitorState:
    if (
        spec.semantics is TemporalSemantics.EVENTUALLY
        and commitment.kind is not CommitmentKind.ACHIEVEMENT
    ):
        raise CommitmentMonitorError(
            "eventually semantics requires an achievement commitment"
        )
    if (
        spec.semantics is TemporalSemantics.ONCE_ACTIVATED
        and commitment.kind is not CommitmentKind.MAINTENANCE
    ):
        raise CommitmentMonitorError(
            "once_activated semantics requires a maintenance commitment"
        )
    if prior is not None:
        if (
            prior.commitment_id != commitment.commitment_id
            or prior.semantics is not spec.semantics
            or not _same_branch(prior.observed_branch, state.branch)
            or prior.observed_branch.epoch > state.branch.epoch
        ):
            raise CommitmentMonitorError(
                "temporal monitor state is stale or cross-branch"
            )
    latched = (
        active
        or (
            prior.activation_latched
            if prior is not None
            else False
        )
    )
    satisfied = (
        observation is not None
        and observation.outcome is CriterionOutcome.SATISFIED
    ) or (prior.satisfaction_seen if prior is not None else False)
    if spec.semantics is TemporalSemantics.ONCE_ACTIVATED and not latched:
        stage = TemporalMonitorStage.INACTIVE
    elif satisfied and commitment.kind is CommitmentKind.ACHIEVEMENT:
        stage = TemporalMonitorStage.ACHIEVED
    elif (
        observation is not None
        and observation.outcome is CriterionOutcome.UNSATISFIED
        and (
            commitment.kind is CommitmentKind.MAINTENANCE
            or completion_boundary
        )
    ):
        stage = TemporalMonitorStage.VIOLATED
    else:
        stage = TemporalMonitorStage.ACTIVE
    return TemporalMonitorState(
        commitment_id=commitment.commitment_id,
        semantics=spec.semantics,
        stage=stage,
        activation_latched=latched,
        satisfaction_seen=satisfied,
        observed_branch=state.branch,
        observed_base_state_digest=state.state_digest,
        last_candidate_id=candidate_id,
    )


def monitor_commitments(
    state: OperationalMarkovState,
    *,
    candidate_id: str,
    observations: tuple[CriterionObservation, ...],
    completion_boundary: bool,
    proposed_commitments: tuple[Commitment, ...] = (),
    temporal_specs: tuple[TemporalMonitorSpec, ...] = (),
    prior_temporal_states: tuple[TemporalMonitorState, ...] = (),
) -> CommitmentMonitorReceipt:
    """Evaluate commitments without editing the candidate or normative state."""

    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be an OperationalMarkovState")
    require_local_id(candidate_id, "candidate_id")
    if type(completion_boundary) is not bool:
        raise TypeError("completion_boundary must be bool")
    for field, values, item_type in (
        ("observations", observations, CriterionObservation),
        ("proposed_commitments", proposed_commitments, Commitment),
        ("temporal_specs", temporal_specs, TemporalMonitorSpec),
        (
            "prior_temporal_states",
            prior_temporal_states,
            TemporalMonitorState,
        ),
    ):
        _tuple(values, field)
        if any(not isinstance(item, item_type) for item in values):
            raise TypeError(f"{field} contains the wrong value type")

    observation_by_key: dict[
        tuple[str, str],
        CriterionObservation,
    ] = {}
    for item in observations:
        if (
            item.candidate_id != candidate_id
            or item.branch != state.branch
            or item.base_state_digest != state.state_digest
        ):
            raise CommitmentMonitorError(
                "criterion observation is stale or cross-branch"
            )
        if item.key in observation_by_key:
            raise CommitmentMonitorError(
                f"duplicate criterion observation: {item.key}"
            )
        observation_by_key[item.key] = item

    spec_by_id = {item.commitment_id: item for item in temporal_specs}
    if len(spec_by_id) != len(temporal_specs):
        raise CommitmentMonitorError("duplicate temporal monitor spec")
    prior_by_id = {
        item.commitment_id: item for item in prior_temporal_states
    }
    if len(prior_by_id) != len(prior_temporal_states):
        raise CommitmentMonitorError("duplicate temporal monitor state")
    unknown_specs = set(spec_by_id) - {
        item.commitment_id for item in state.commitments
    }
    if unknown_specs:
        raise CommitmentMonitorError(
            f"temporal specs name unknown commitments: {sorted(unknown_specs)}"
        )

    progress: list[CommitmentProgress] = []
    findings: list[CommitmentFinding] = []
    obligations: list[DesignObligation] = []
    temporal_states: list[TemporalMonitorState] = []
    violated_ids: list[str] = []
    candidate_ref = f"candidate:{candidate_id}"

    existing_by_id = {
        item.commitment_id: item for item in state.commitments
    }
    for proposed in proposed_commitments:
        existing = existing_by_id.get(proposed.commitment_id)
        if existing is None:
            if proposed.status is not CommitmentStatus.PROPOSED:
                raise CommitmentMonitorError(
                    "new candidate commitments must remain proposed"
                )
            continue
        finding = _make_finding(
            existing,
            code="commitment.unauthorized_mutation",
            message=(
                f"Candidate {candidate_id} attempted to replace active "
                f"commitment {existing.commitment_id}."
            ),
            observation=None,
            criterion_id=(
                existing.satisfaction_criterion.criterion_id
            ),
            dependency_path=(_commitment_ref(existing.commitment_id),),
            candidate_ref=candidate_ref,
            missing_evidence=False,
        )
        findings.append(finding)
        obligations.append(_repair_obligation(finding))
        violated_ids.append(existing.commitment_id)

    for commitment in sorted(
        state.commitments,
        key=lambda item: item.commitment_id,
    ):
        if commitment.status not in MONITORED_COMMITMENT_STATUSES:
            continue
        activation = _observation_for(
            observation_by_key,
            commitment,
            activation=True,
        )
        if commitment.activation_criterion is None:
            active = True
            activation_unknown = False
        elif activation is None or activation.outcome is CriterionOutcome.UNKNOWN:
            active = False
            activation_unknown = True
        else:
            active = activation.outcome is CriterionOutcome.SATISFIED
            activation_unknown = False

        prior = prior_by_id.get(commitment.commitment_id)
        spec = spec_by_id.get(commitment.commitment_id)
        if (
            spec is not None
            and spec.semantics is TemporalSemantics.ONCE_ACTIVATED
            and prior is not None
            and prior.activation_latched
        ):
            active = True
            activation_unknown = False

        criterion = commitment.satisfaction_criterion
        observation = _observation_for(
            observation_by_key,
            commitment,
        )
        commitment_ref = _commitment_ref(commitment.commitment_id)

        if activation_unknown:
            finding = _make_finding(
                commitment,
                code="commitment.activation_evidence_missing",
                message=(
                    f"Activation of commitment "
                    f"{commitment.commitment_id} cannot be determined."
                ),
                observation=activation,
                criterion_id=(
                    commitment.activation_criterion.criterion_id
                ),
                dependency_path=(commitment_ref,),
                candidate_ref=candidate_ref,
            )
            findings.append(finding)
            obligations.append(
                _blocked_evidence_obligation(
                    finding,
                    provider_id=(
                        commitment.activation_criterion.provider_id
                    ),
                )
            )
            progress.append(
                CommitmentProgress(
                    commitment_id=commitment.commitment_id,
                    kind=commitment.kind,
                    commitment_digest=commitment_content_digest(commitment),
                    outcome=CommitmentProgressOutcome.EVIDENCE_BLOCKED,
                    recommended_status=None,
                )
            )
            if spec is not None:
                temporal_states.append(
                    _temporal_state(
                        commitment,
                        spec,
                        prior,
                        active=False,
                        observation=None,
                        state=state,
                        candidate_id=candidate_id,
                        completion_boundary=completion_boundary,
                    )
                )
            continue
        if not active:
            progress.append(
                CommitmentProgress(
                    commitment_id=commitment.commitment_id,
                    kind=commitment.kind,
                    commitment_digest=commitment_content_digest(commitment),
                    outcome=CommitmentProgressOutcome.INACTIVE,
                    recommended_status=None,
                )
            )
            if spec is not None:
                temporal = _temporal_state(
                    commitment,
                    spec,
                    prior,
                    active=False,
                    observation=observation,
                    state=state,
                    candidate_id=candidate_id,
                    completion_boundary=completion_boundary,
                )
                temporal_states.append(temporal)
            continue

        if observation is None or observation.outcome is CriterionOutcome.UNKNOWN:
            finding = _make_finding(
                commitment,
                code="commitment.satisfaction_evidence_missing",
                message=(
                    f"Satisfaction of commitment "
                    f"{commitment.commitment_id} cannot be determined."
                ),
                observation=observation,
                criterion_id=criterion.criterion_id,
                dependency_path=(commitment_ref,),
                candidate_ref=candidate_ref,
            )
            findings.append(finding)
            obligations.append(
                _blocked_evidence_obligation(
                    finding,
                    provider_id=criterion.provider_id,
                )
            )
            progress.append(
                CommitmentProgress(
                    commitment_id=commitment.commitment_id,
                    kind=commitment.kind,
                    commitment_digest=commitment_content_digest(commitment),
                    outcome=CommitmentProgressOutcome.EVIDENCE_BLOCKED,
                    recommended_status=None,
                )
            )
            if spec is not None:
                temporal_states.append(
                    _temporal_state(
                        commitment,
                        spec,
                        prior,
                        active=True,
                        observation=observation,
                        state=state,
                        candidate_id=candidate_id,
                        completion_boundary=completion_boundary,
                    )
                )
            continue

        satisfied = observation.outcome is CriterionOutcome.SATISFIED
        if commitment.kind is CommitmentKind.ACHIEVEMENT:
            if satisfied:
                outcome = CommitmentProgressOutcome.ACHIEVED
                recommended = CommitmentStatus.SATISFIED
            elif completion_boundary:
                outcome = CommitmentProgressOutcome.VIOLATED
                recommended = CommitmentStatus.VIOLATED
            else:
                outcome = CommitmentProgressOutcome.PENDING
                recommended = None
        else:
            if satisfied:
                outcome = CommitmentProgressOutcome.PRESERVED
                recommended = CommitmentStatus.ACTIVE
            else:
                outcome = CommitmentProgressOutcome.VIOLATED
                recommended = CommitmentStatus.VIOLATED

        temporal_ref = None
        if spec is not None:
            temporal = _temporal_state(
                commitment,
                spec,
                prior,
                active=active,
                observation=observation,
                state=state,
                candidate_id=candidate_id,
                completion_boundary=completion_boundary,
            )
            temporal_states.append(temporal)
            temporal_ref = (
                "temporal-monitor:"
                f"{canonical_digest(temporal.to_dict())}"
            )
        progress.append(
            CommitmentProgress(
                commitment_id=commitment.commitment_id,
                kind=commitment.kind,
                commitment_digest=commitment_content_digest(commitment),
                outcome=outcome,
                recommended_status=recommended,
                temporal_state_ref=temporal_ref,
            )
        )
        if outcome is CommitmentProgressOutcome.VIOLATED:
            code = (
                "commitment.achievement_unmet"
                if commitment.kind is CommitmentKind.ACHIEVEMENT
                else "commitment.maintenance_violated"
            )
            finding = _make_finding(
                commitment,
                code=code,
                message=(
                    f"Candidate {candidate_id} violates commitment "
                    f"{commitment.commitment_id}."
                ),
                observation=observation,
                criterion_id=criterion.criterion_id,
                dependency_path=(commitment_ref,),
                candidate_ref=candidate_ref,
            )
            findings.append(finding)
            obligations.append(_repair_obligation(finding))
            violated_ids.append(commitment.commitment_id)

    impacts = _dependency_impacts(
        state.commitments,
        state.dependencies,
        tuple(sorted(set(violated_ids))),
    )
    for impact in impacts:
        identity = canonical_digest(
            (
                candidate_id,
                impact.target_ref,
                impact.impact.value,
                impact.dependency_path,
            )
        )
        obligations.append(
            DesignObligation(
                obligation_id=f"commitment-impact-{identity[:20]}",
                statement=(
                    f"Recheck {impact.target_ref} after a named "
                    "commitment dependency changed."
                ),
                source_ref=f"commitment-impact:{identity}",
                status=ObligationStatus.OPEN,
                subject_refs=(impact.target_ref,),
            )
        )

    revision_proposals: list[CommitmentRevisionProposal] = []
    finding_by_commitment = {
        item.commitment_id: item
        for item in findings
        if item.severity is CommitmentFindingSeverity.REVISION_REQUIRED
    }
    for commitment_id, finding in sorted(finding_by_commitment.items()):
        commitment = existing_by_id[commitment_id]
        if commitment.revision_policy is RevisionPolicy.IMMUTABLE:
            continue
        authorities = (
            commitment.permitted_authority_ids
            if commitment.revision_policy
            is RevisionPolicy.NAMED_AUTHORITIES
            else (commitment.authority_id,)
        )
        revision_proposals.append(
            CommitmentRevisionProposal(
                proposal_id=(
                    f"crp-{canonical_digest((candidate_id, finding.finding_id))[:24]}"
                ),
                commitment_id=commitment_id,
                candidate_id=candidate_id,
                branch=state.branch,
                base_state_digest=state.state_digest,
                reason_finding_id=finding.finding_id,
                required_authority_ids=authorities,
                evidence_refs=finding.evidence_refs,
            )
        )

    unique_obligations = {
        item.obligation_id: item for item in obligations
    }
    frozen_findings = tuple(
        sorted(findings, key=lambda item: item.finding_id)
    )
    frozen_progress = tuple(
        sorted(progress, key=lambda item: item.commitment_id)
    )
    frozen_obligations = tuple(
        sorted(
            unique_obligations.values(),
            key=lambda item: item.obligation_id,
        )
    )
    frozen_temporal = tuple(
        sorted(temporal_states, key=lambda item: item.commitment_id)
    )
    frozen_revisions = tuple(
        sorted(revision_proposals, key=lambda item: item.proposal_id)
    )
    passed = not any(
        item.severity
        in {
            CommitmentFindingSeverity.HARD_FAILURE,
            CommitmentFindingSeverity.REVISION_REQUIRED,
        }
        for item in frozen_findings
    )
    receipt_payload = {
        "candidate_id": candidate_id,
        "branch": _branch_to_dict(state.branch),
        "base_state_digest": state.state_digest,
        "completion_boundary": completion_boundary,
        "progress": [
            (
                item.commitment_id,
                item.outcome.value,
                (
                    item.recommended_status.value
                    if item.recommended_status is not None
                    else None
                ),
            )
            for item in frozen_progress
        ],
        "finding_ids": [item.finding_id for item in frozen_findings],
        "obligation_ids": [
            item.obligation_id for item in frozen_obligations
        ],
        "impacts": [
            (
                item.target_ref,
                item.impact.value,
                item.dependency_path,
            )
            for item in impacts
        ],
        "temporal": [item.to_dict() for item in frozen_temporal],
        "revision_ids": [
            item.proposal_id for item in frozen_revisions
        ],
    }
    return CommitmentMonitorReceipt(
        receipt_id=f"cmr-{canonical_digest(receipt_payload)[:24]}",
        candidate_id=candidate_id,
        branch=state.branch,
        base_state_digest=state.state_digest,
        completion_boundary=completion_boundary,
        passed=passed,
        progress=frozen_progress,
        findings=frozen_findings,
        obligations=frozen_obligations,
        dependency_impacts=impacts,
        temporal_states=frozen_temporal,
        revision_proposals=frozen_revisions,
    )
