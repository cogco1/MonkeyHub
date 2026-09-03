"""Pure stage-convergence evaluation over exact operational states.

The evaluator has no persistence or design authority.  A project-supplied
policy fixes the protected refs, mandatory obligation universe, dependency
edges, and bounded scope-expansion authorities.  One exact parent/child pair
is then classified as progress, repair, explicit scope expansion, or a
fail-closed rejection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from archflow.contracts.canonical import CanonicalValueError, canonical_digest, canonical_json, require_sha256
from archflow.project.refs import BranchRef
from archflow.state.operational_state import (
    DependencyEdge,
    DependencyEffect,
    ObligationStatus,
    OperationalMarkovState,
    StateLock,
    require_local_id,
    require_logical_ref,
)


_MAX_ITEMS = 4096


class StageConvergenceError(ValueError):
    """A convergence contract or request is structurally invalid."""


class StageTransitionKind(StrEnum):
    REFINE = "refine"
    RESOLVE = "resolve"
    REPAIR = "repair"
    SCOPE_EXPANSION = "scope_expansion"


class StageConvergenceOutcome(StrEnum):
    PROGRESS = "progress"
    REPAIR = "repair"
    SCOPE_EXPANSION = "scope_expansion"
    REJECTED = "rejected"


def _canonical_json(value: object) -> str:
    try:
        return canonical_json(value)
    except CanonicalValueError as exc:
        raise StageConvergenceError(
            "stage convergence value must be canonical JSON data"
        ) from exc


def _digest(value: object) -> str:
    try:
        return canonical_digest(value)
    except CanonicalValueError as exc:
        raise StageConvergenceError(
            "stage convergence value must be canonical JSON data"
        ) from exc


def _sha256(value: object, field: str) -> str:
    try:
        return require_sha256(value, field)
    except ValueError as exc:
        raise StageConvergenceError(
            f"{field} must be a SHA-256 digest"
        ) from exc


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageConvergenceError(f"{field} must be non-empty text")
    return value


def _tuple(value: object, field: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise StageConvergenceError(f"{field} exceeds bounded item count")
    return value


def _sorted_refs(value: object, field: str) -> tuple[str, ...]:
    values = _tuple(value, field)
    for ref in values:
        require_logical_ref(ref, field)
    if len(values) != len(set(values)):
        raise StageConvergenceError(f"{field} contains duplicates")
    return tuple(sorted(values))


def _sorted_ids(value: object, field: str) -> tuple[str, ...]:
    values = _tuple(value, field)
    for item in values:
        require_local_id(item, field)
    if len(values) != len(set(values)):
        raise StageConvergenceError(f"{field} contains duplicates")
    return tuple(sorted(values))


def _branch_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "project_id": branch.run.base.project_id,
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.state_sha256,
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _same_lineage(left: BranchRef, right: BranchRef) -> bool:
    return left.run == right.run and left.branch_id == right.branch_id


@dataclass(frozen=True, slots=True)
class StageConvergencePolicy:
    """Project-supplied denominator for one bounded design stage."""

    policy_id: str
    stage: str
    protected_refs: tuple[str, ...] = ()
    mandatory_obligation_ids: tuple[str, ...] = ()
    dependencies: tuple[DependencyEdge, ...] = ()
    scope_expansion_authority_ids: tuple[str, ...] = ()
    max_scope_expansion_refs: int = 32

    SCHEMA = "StageConvergencePolicy@1"

    def __post_init__(self) -> None:
        require_local_id(self.policy_id, "policy_id")
        require_local_id(self.stage, "stage")
        object.__setattr__(
            self,
            "protected_refs",
            _sorted_refs(self.protected_refs, "protected_refs"),
        )
        object.__setattr__(
            self,
            "mandatory_obligation_ids",
            _sorted_ids(
                self.mandatory_obligation_ids,
                "mandatory_obligation_ids",
            ),
        )
        _tuple(self.dependencies, "dependencies")
        if any(
            not isinstance(item, DependencyEdge) for item in self.dependencies
        ):
            raise TypeError("dependencies must contain DependencyEdge")
        dependency_refs = tuple(item.ref for item in self.dependencies)
        if len(dependency_refs) != len(set(dependency_refs)):
            raise StageConvergenceError("dependencies contains duplicates")
        object.__setattr__(
            self,
            "dependencies",
            tuple(sorted(self.dependencies, key=lambda item: item.identity)),
        )
        object.__setattr__(
            self,
            "scope_expansion_authority_ids",
            _sorted_ids(
                self.scope_expansion_authority_ids,
                "scope_expansion_authority_ids",
            ),
        )
        if (
            not isinstance(self.max_scope_expansion_refs, int)
            or isinstance(self.max_scope_expansion_refs, bool)
            or self.max_scope_expansion_refs < 1
            or self.max_scope_expansion_refs > _MAX_ITEMS
        ):
            raise StageConvergenceError(
                "max_scope_expansion_refs must be inside 1..4096"
            )

    @property
    def policy_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "stage": self.stage,
            "protected_refs": list(self.protected_refs),
            "mandatory_obligation_ids": list(
                self.mandatory_obligation_ids
            ),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "scope_expansion_authority_ids": list(
                self.scope_expansion_authority_ids
            ),
            "max_scope_expansion_refs": self.max_scope_expansion_refs,
        }


@dataclass(frozen=True, slots=True)
class StageConvergenceEvidence:
    """Exact-state external deficits not represented in operational state."""

    state_digest: str
    hard_gate_failure_refs: tuple[str, ...] = ()
    conflict_refs: tuple[str, ...] = ()
    tolerance_failure_refs: tuple[str, ...] = ()
    revalidation_refs: tuple[str, ...] = ()

    SCHEMA = "StageConvergenceEvidence@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "state_digest",
            _sha256(self.state_digest, "evidence state_digest"),
        )
        for field in (
            "hard_gate_failure_refs",
            "conflict_refs",
            "tolerance_failure_refs",
            "revalidation_refs",
        ):
            object.__setattr__(
                self,
                field,
                _sorted_refs(getattr(self, field), field),
            )

    @property
    def evidence_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "state_digest": self.state_digest,
            "hard_gate_failure_refs": list(self.hard_gate_failure_refs),
            "conflict_refs": list(self.conflict_refs),
            "tolerance_failure_refs": list(self.tolerance_failure_refs),
            "revalidation_refs": list(self.revalidation_refs),
        }


@dataclass(frozen=True, slots=True)
class StageTransitionRequest:
    """Claimed intent and exact base/result binding for one transition."""

    request_id: str
    stage: str
    kind: StageTransitionKind
    authority_id: str
    parent_state_digest: str
    child_state_digest: str
    trigger_refs: tuple[str, ...] = ()
    reopened_refs: tuple[str, ...] = ()
    added_mandatory_obligation_ids: tuple[str, ...] = ()
    authorization_ref: str | None = None

    SCHEMA = "StageTransitionRequest@1"

    def __post_init__(self) -> None:
        require_local_id(self.request_id, "request_id")
        require_local_id(self.stage, "stage")
        if not isinstance(self.kind, StageTransitionKind):
            raise TypeError("kind must be StageTransitionKind")
        require_local_id(self.authority_id, "authority_id")
        object.__setattr__(
            self,
            "parent_state_digest",
            _sha256(self.parent_state_digest, "parent_state_digest"),
        )
        object.__setattr__(
            self,
            "child_state_digest",
            _sha256(self.child_state_digest, "child_state_digest"),
        )
        object.__setattr__(
            self,
            "trigger_refs",
            _sorted_refs(self.trigger_refs, "trigger_refs"),
        )
        object.__setattr__(
            self,
            "reopened_refs",
            _sorted_refs(self.reopened_refs, "reopened_refs"),
        )
        object.__setattr__(
            self,
            "added_mandatory_obligation_ids",
            _sorted_ids(
                self.added_mandatory_obligation_ids,
                "added_mandatory_obligation_ids",
            ),
        )
        if self.authorization_ref is not None:
            require_logical_ref(self.authorization_ref, "authorization_ref")


@dataclass(frozen=True, slots=True)
class StageConvergencePotential:
    """Ordered deficit sets; ``vector`` is compared lexicographically."""

    hard_gate_failure_refs: tuple[str, ...]
    conflict_refs: tuple[str, ...]
    tolerance_failure_refs: tuple[str, ...]
    missing_mandatory_obligation_refs: tuple[str, ...]
    blocked_mandatory_obligation_refs: tuple[str, ...]
    open_mandatory_obligation_refs: tuple[str, ...]
    invalidated_refs: tuple[str, ...]
    revalidation_refs: tuple[str, ...]

    SCHEMA = "StageConvergencePotential@1"

    def __post_init__(self) -> None:
        for field in (
            "hard_gate_failure_refs",
            "conflict_refs",
            "tolerance_failure_refs",
            "missing_mandatory_obligation_refs",
            "blocked_mandatory_obligation_refs",
            "open_mandatory_obligation_refs",
            "invalidated_refs",
            "revalidation_refs",
        ):
            object.__setattr__(
                self,
                field,
                _sorted_refs(getattr(self, field), field),
            )

    @property
    def vector(self) -> tuple[int, ...]:
        return (
            len(self.hard_gate_failure_refs),
            len(self.conflict_refs),
            len(self.tolerance_failure_refs),
            len(self.missing_mandatory_obligation_refs),
            len(self.blocked_mandatory_obligation_refs),
            len(self.open_mandatory_obligation_refs),
            len(self.invalidated_refs),
            len(self.revalidation_refs),
        )

    @property
    def is_zero(self) -> bool:
        return not any(self.vector)

    @property
    def deficit_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *self.hard_gate_failure_refs,
                    *self.conflict_refs,
                    *self.tolerance_failure_refs,
                    *self.missing_mandatory_obligation_refs,
                    *self.blocked_mandatory_obligation_refs,
                    *self.open_mandatory_obligation_refs,
                    *self.invalidated_refs,
                    *self.revalidation_refs,
                }
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "hard_gate_failure_refs": list(self.hard_gate_failure_refs),
            "conflict_refs": list(self.conflict_refs),
            "tolerance_failure_refs": list(self.tolerance_failure_refs),
            "missing_mandatory_obligation_refs": list(
                self.missing_mandatory_obligation_refs
            ),
            "blocked_mandatory_obligation_refs": list(
                self.blocked_mandatory_obligation_refs
            ),
            "open_mandatory_obligation_refs": list(
                self.open_mandatory_obligation_refs
            ),
            "invalidated_refs": list(self.invalidated_refs),
            "revalidation_refs": list(self.revalidation_refs),
            "vector": list(self.vector),
        }


@dataclass(frozen=True, slots=True)
class StageConvergenceReceipt:
    receipt_id: str
    outcome: StageConvergenceOutcome
    request_id: str
    stage: str
    transition_kind: StageTransitionKind
    branch: BranchRef
    policy_digest: str
    parent_state_digest: str
    child_state_digest: str
    parent_sufficient_digest: str
    child_sufficient_digest: str
    parent_evidence_digest: str
    child_evidence_digest: str
    potential_before: StageConvergencePotential
    potential_after: StageConvergencePotential
    protected_refs: tuple[str, ...]
    changed_protected_refs: tuple[str, ...]
    mandatory_obligation_ids: tuple[str, ...]
    added_mandatory_obligation_ids: tuple[str, ...]
    dependency_closure: tuple[str, ...]
    authorization_ref: str | None
    reason_codes: tuple[str, ...]

    SCHEMA = "StageConvergenceReceipt@1"

    def __post_init__(self) -> None:
        require_local_id(self.receipt_id, "receipt_id")
        require_local_id(self.request_id, "request_id")
        require_local_id(self.stage, "stage")
        if not isinstance(self.outcome, StageConvergenceOutcome):
            raise TypeError("outcome must be StageConvergenceOutcome")
        if not isinstance(self.transition_kind, StageTransitionKind):
            raise TypeError("transition_kind must be StageTransitionKind")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        for field in (
            "policy_digest",
            "parent_state_digest",
            "child_state_digest",
            "parent_sufficient_digest",
            "child_sufficient_digest",
            "parent_evidence_digest",
            "child_evidence_digest",
        ):
            object.__setattr__(
                self,
                field,
                _sha256(getattr(self, field), field),
            )
        if not isinstance(
            self.potential_before, StageConvergencePotential
        ) or not isinstance(self.potential_after, StageConvergencePotential):
            raise TypeError("receipt potentials have the wrong type")
        for field in (
            "protected_refs",
            "changed_protected_refs",
            "dependency_closure",
        ):
            object.__setattr__(
                self,
                field,
                _sorted_refs(getattr(self, field), field),
            )
        for field in (
            "mandatory_obligation_ids",
            "added_mandatory_obligation_ids",
        ):
            object.__setattr__(
                self,
                field,
                _sorted_ids(getattr(self, field), field),
            )
        if self.authorization_ref is not None:
            require_logical_ref(self.authorization_ref, "authorization_ref")
        _tuple(self.reason_codes, "reason_codes")
        for code in self.reason_codes:
            _text(code, "reason_code")
        if tuple(sorted(set(self.reason_codes))) != self.reason_codes:
            raise StageConvergenceError(
                "reason_codes must be sorted and unique"
            )
        if (
            self.outcome is StageConvergenceOutcome.REJECTED
        ) != bool(self.reason_codes):
            raise StageConvergenceError(
                "rejected outcome must carry reasons and accepted outcomes "
                "must not"
            )

    @property
    def passed(self) -> bool:
        return self.outcome is not StageConvergenceOutcome.REJECTED

    @property
    def stage_ready(self) -> bool:
        """Only a closed non-expansion transition can support stage exit."""

        return (
            self.passed
            and self.outcome
            in {
                StageConvergenceOutcome.PROGRESS,
                StageConvergenceOutcome.REPAIR,
            }
            and self.potential_after.is_zero
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "outcome": self.outcome.value,
            "request_id": self.request_id,
            "stage": self.stage,
            "transition_kind": self.transition_kind.value,
            "branch": _branch_dict(self.branch),
            "policy_digest": self.policy_digest,
            "parent_state_digest": self.parent_state_digest,
            "child_state_digest": self.child_state_digest,
            "parent_sufficient_digest": self.parent_sufficient_digest,
            "child_sufficient_digest": self.child_sufficient_digest,
            "parent_evidence_digest": self.parent_evidence_digest,
            "child_evidence_digest": self.child_evidence_digest,
            "potential_before": self.potential_before.to_dict(),
            "potential_after": self.potential_after.to_dict(),
            "protected_refs": list(self.protected_refs),
            "changed_protected_refs": list(self.changed_protected_refs),
            "mandatory_obligation_ids": list(
                self.mandatory_obligation_ids
            ),
            "added_mandatory_obligation_ids": list(
                self.added_mandatory_obligation_ids
            ),
            "dependency_closure": list(self.dependency_closure),
            "authorization_ref": self.authorization_ref,
            "reason_codes": list(self.reason_codes),
            "stage_ready": self.stage_ready,
            "canonical_write_authority": False,
        }


def _potential(
    state: OperationalMarkovState,
    evidence: StageConvergenceEvidence,
    mandatory_obligation_ids: tuple[str, ...],
) -> StageConvergencePotential:
    obligations = {item.obligation_id: item for item in state.obligations}
    missing: list[str] = []
    blocked: list[str] = []
    opened: list[str] = []
    for obligation_id in mandatory_obligation_ids:
        ref = f"obligation:{obligation_id}"
        obligation = obligations.get(obligation_id)
        if obligation is None:
            missing.append(ref)
        elif obligation.status is ObligationStatus.BLOCKED:
            blocked.append(ref)
        elif obligation.status is ObligationStatus.OPEN:
            opened.append(ref)
    return StageConvergencePotential(
        hard_gate_failure_refs=evidence.hard_gate_failure_refs,
        conflict_refs=evidence.conflict_refs,
        tolerance_failure_refs=evidence.tolerance_failure_refs,
        missing_mandatory_obligation_refs=tuple(missing),
        blocked_mandatory_obligation_refs=tuple(blocked),
        open_mandatory_obligation_refs=tuple(opened),
        invalidated_refs=state.invalidated_refs,
        revalidation_refs=evidence.revalidation_refs,
    )


def _dependency_closure(
    trigger_refs: tuple[str, ...],
    dependencies: tuple[DependencyEdge, ...],
) -> tuple[str, ...]:
    adjacency: dict[str, set[str]] = {}
    for edge in dependencies:
        if edge.effect not in {
            DependencyEffect.INVALIDATES,
            DependencyEffect.REQUIRES_REVALIDATION,
        }:
            continue
        adjacency.setdefault(edge.upstream_ref, set()).add(
            edge.downstream_ref
        )
    visited = set(trigger_refs)
    queue = list(sorted(trigger_refs))
    while queue:
        upstream = queue.pop(0)
        for downstream in sorted(adjacency.get(upstream, ())):
            if downstream in visited:
                continue
            visited.add(downstream)
            if len(visited) > _MAX_ITEMS:
                raise StageConvergenceError(
                    "dependency closure exceeds bounded item count"
                )
            queue.append(downstream)
    return tuple(sorted(visited))


def _protected_changes(
    policy: StageConvergencePolicy,
    parent: OperationalMarkovState,
    child: OperationalMarkovState,
    reasons: set[str],
) -> tuple[str, ...]:
    parent_locks = {item.target_ref: item for item in parent.locks}
    child_locks = {item.target_ref: item for item in child.locks}
    changed: list[str] = []
    for ref in policy.protected_refs:
        parent_lock = parent_locks.get(ref)
        child_lock = child_locks.get(ref)
        if parent_lock is None:
            reasons.add("stage_convergence.protected_lock_missing_parent")
            changed.append(ref)
            continue
        if child_lock != parent_lock or (
            parent.value_for_ref(ref) != child.value_for_ref(ref)
        ):
            changed.append(ref)
    return tuple(sorted(changed))


def _active_obligation_ids(
    state: OperationalMarkovState,
) -> set[str]:
    return {
        item.obligation_id
        for item in state.obligations
        if item.status in {ObligationStatus.OPEN, ObligationStatus.BLOCKED}
    }


def evaluate_stage_convergence(
    policy: StageConvergencePolicy,
    request: StageTransitionRequest,
    parent: OperationalMarkovState,
    child: OperationalMarkovState,
    *,
    parent_evidence: StageConvergenceEvidence,
    child_evidence: StageConvergenceEvidence,
) -> StageConvergenceReceipt:
    """Classify one exact transition without mutating or persisting state."""

    if not isinstance(policy, StageConvergencePolicy):
        raise TypeError("policy must be StageConvergencePolicy")
    if not isinstance(request, StageTransitionRequest):
        raise TypeError("request must be StageTransitionRequest")
    if not isinstance(parent, OperationalMarkovState) or not isinstance(
        child, OperationalMarkovState
    ):
        raise TypeError("parent and child must be OperationalMarkovState")
    if not isinstance(
        parent_evidence, StageConvergenceEvidence
    ) or not isinstance(child_evidence, StageConvergenceEvidence):
        raise TypeError("evidence must be StageConvergenceEvidence")

    reasons: set[str] = set()
    if request.stage != policy.stage:
        reasons.add("stage_convergence.policy_stage_mismatch")
    if request.parent_state_digest != parent.state_digest:
        reasons.add("stage_convergence.stale_parent_digest")
    if request.child_state_digest != child.state_digest:
        reasons.add("stage_convergence.stale_child_digest")
    if parent_evidence.state_digest != parent.state_digest:
        reasons.add("stage_convergence.stale_parent_evidence")
    if child_evidence.state_digest != child.state_digest:
        reasons.add("stage_convergence.stale_child_evidence")
    if not _same_lineage(parent.branch, child.branch):
        reasons.add("stage_convergence.cross_branch_transition")
    elif child.branch.epoch != parent.branch.epoch + 1:
        reasons.add("stage_convergence.child_is_not_exact_successor")
    if parent.phase != child.phase:
        reasons.add("stage_convergence.phase_changed_inside_stage")
    if parent.compiler_version != child.compiler_version:
        reasons.add("stage_convergence.compiler_changed_inside_stage")
    if parent.sufficient_digest == child.sufficient_digest:
        reasons.add("stage_convergence.no_op")

    mandatory_before = policy.mandatory_obligation_ids
    mandatory_after = tuple(
        sorted(
            {
                *mandatory_before,
                *request.added_mandatory_obligation_ids,
            }
        )
    )
    potential_before = _potential(
        parent,
        parent_evidence,
        mandatory_before,
    )
    potential_after = _potential(
        child,
        child_evidence,
        mandatory_after,
    )
    changed_protected = _protected_changes(
        policy,
        parent,
        child,
        reasons,
    )
    parent_active = _active_obligation_ids(parent)
    child_active = _active_obligation_ids(child)
    new_active = child_active - parent_active
    new_invalidations = set(child.invalidated_refs) - set(
        parent.invalidated_refs
    )
    new_revalidations = set(child_evidence.revalidation_refs) - set(
        parent_evidence.revalidation_refs
    )

    dependency_closure: tuple[str, ...] = ()
    if request.kind is StageTransitionKind.SCOPE_EXPANSION:
        dependency_closure = _dependency_closure(
            request.trigger_refs,
            policy.dependencies,
        )
        if request.authority_id not in (
            policy.scope_expansion_authority_ids
        ):
            reasons.add(
                "stage_convergence.scope_expansion_authority_missing"
            )
        if request.authorization_ref is None:
            reasons.add(
                "stage_convergence.scope_expansion_receipt_missing"
            )
        if request.reopened_refs != dependency_closure:
            reasons.add("stage_convergence.dependency_closure_mismatch")
        expanded_size = len(
            {
                *request.reopened_refs,
                *(
                    f"obligation:{item}"
                    for item in request.added_mandatory_obligation_ids
                ),
            }
        )
        if expanded_size > policy.max_scope_expansion_refs:
            reasons.add("stage_convergence.scope_expansion_too_wide")
        if not request.reopened_refs and not (
            request.added_mandatory_obligation_ids
        ):
            reasons.add("stage_convergence.scope_expansion_is_empty")
        if new_active != set(request.added_mandatory_obligation_ids):
            reasons.add(
                "stage_convergence.scope_obligation_set_mismatch"
            )
        if not new_invalidations <= set(dependency_closure):
            reasons.add("stage_convergence.unexplained_invalidation")
        if not new_revalidations <= set(dependency_closure):
            reasons.add("stage_convergence.unexplained_revalidation")
        parent_locks = {item.target_ref: item for item in parent.locks}
        for ref in changed_protected:
            lock: StateLock | None = parent_locks.get(ref)
            if (
                lock is None
                or lock.authority_id != request.authority_id
                or ref not in request.trigger_refs
            ):
                reasons.add(
                    "stage_convergence.protected_change_not_authorized"
                )
    else:
        if request.reopened_refs or request.added_mandatory_obligation_ids:
            reasons.add(
                "stage_convergence.scope_change_misclassified_as_progress"
            )
        if request.authorization_ref is not None:
            reasons.add(
                "stage_convergence.unexpected_expansion_authorization"
            )
        if changed_protected:
            reasons.add("stage_convergence.protected_ref_changed")
        if new_active:
            reasons.add(
                "stage_convergence.ordinary_transition_added_obligations"
            )
        if new_invalidations:
            reasons.add("stage_convergence.unexplained_invalidation")
        if new_revalidations:
            reasons.add("stage_convergence.unexplained_revalidation")
        if request.kind is StageTransitionKind.REPAIR:
            if not request.trigger_refs:
                reasons.add("stage_convergence.repair_trigger_missing")
            elif not set(request.trigger_refs) <= set(
                potential_before.deficit_refs
            ):
                reasons.add(
                    "stage_convergence.repair_outside_parent_deficit"
                )
        if not potential_after.vector < potential_before.vector:
            reasons.add(
                "stage_convergence.potential_not_strictly_decreased"
            )

    if reasons:
        outcome = StageConvergenceOutcome.REJECTED
    elif request.kind is StageTransitionKind.REPAIR:
        outcome = StageConvergenceOutcome.REPAIR
    elif request.kind is StageTransitionKind.SCOPE_EXPANSION:
        outcome = StageConvergenceOutcome.SCOPE_EXPANSION
    else:
        outcome = StageConvergenceOutcome.PROGRESS

    reason_codes = tuple(sorted(reasons))
    identity = {
        "request_id": request.request_id,
        "stage": request.stage,
        "kind": request.kind.value,
        "outcome": outcome.value,
        "policy_digest": policy.policy_digest,
        "parent_state_digest": parent.state_digest,
        "child_state_digest": child.state_digest,
        "parent_evidence_digest": parent_evidence.evidence_digest,
        "child_evidence_digest": child_evidence.evidence_digest,
        "potential_before": potential_before.to_dict(),
        "potential_after": potential_after.to_dict(),
        "changed_protected_refs": changed_protected,
        "mandatory_obligation_ids": mandatory_after,
        "dependency_closure": dependency_closure,
        "authorization_ref": request.authorization_ref,
        "reason_codes": reason_codes,
    }
    return StageConvergenceReceipt(
        receipt_id=f"scr-{_digest(identity)[:24]}",
        outcome=outcome,
        request_id=request.request_id,
        stage=request.stage,
        transition_kind=request.kind,
        branch=child.branch,
        policy_digest=policy.policy_digest,
        parent_state_digest=parent.state_digest,
        child_state_digest=child.state_digest,
        parent_sufficient_digest=parent.sufficient_digest,
        child_sufficient_digest=child.sufficient_digest,
        parent_evidence_digest=parent_evidence.evidence_digest,
        child_evidence_digest=child_evidence.evidence_digest,
        potential_before=potential_before,
        potential_after=potential_after,
        protected_refs=policy.protected_refs,
        changed_protected_refs=changed_protected,
        mandatory_obligation_ids=mandatory_after,
        added_mandatory_obligation_ids=(
            request.added_mandatory_obligation_ids
        ),
        dependency_closure=dependency_closure,
        authorization_ref=request.authorization_ref,
        reason_codes=reason_codes,
    )


__all__ = [
    "StageConvergenceError",
    "StageConvergenceEvidence",
    "StageConvergenceOutcome",
    "StageConvergencePolicy",
    "StageConvergencePotential",
    "StageConvergenceReceipt",
    "StageTransitionKind",
    "StageTransitionRequest",
    "evaluate_stage_convergence",
]
