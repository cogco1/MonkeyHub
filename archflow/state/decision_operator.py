"""Compile typed design actions into deterministic operational-state closure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import BranchRef
from archflow.state.commitments import (
    Commitment,
    CommitmentAuthorityError,
    CommitmentStatus,
    CommitmentTransitionError,
    transition_commitment,
)
from archflow.state.operational_state import (
    DependencyEffect,
    DependencyEdge,
    DesignObligation,
    FactValue,
    ObligationStatus,
    OperationalMarkovState,
    ParameterBinding,
    StateFact,
    StateLock,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_json
from archflow.contracts.fields import (
    list_of as _list,
    mapping,
    string_tuple as _string_tuple,
    text,
    tuple_of,
    unique as _unique,
)


_MAX_ITEMS = 4096


class DecisionCompilationError(ValueError):
    """Raised before an invalid proposal can become operational state."""


class ConditionComparator(StrEnum):
    EXISTS = "exists"
    ABSENT = "absent"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"


@dataclass(frozen=True, slots=True)
class StateCondition:
    ref: str
    comparator: ConditionComparator
    expected_value: FactValue | object | None = None

    def __post_init__(self) -> None:
        require_logical_ref(self.ref, "condition ref")
        if not isinstance(self.comparator, ConditionComparator):
            raise TypeError("condition comparator is invalid")
        if self.comparator in (
            ConditionComparator.EQUALS,
            ConditionComparator.NOT_EQUALS,
        ):
            object.__setattr__(
                self,
                "expected_value",
                FactValue.from_value(self.expected_value),
            )
        elif self.expected_value is not None:
            raise ValueError(
                "exists/absent conditions cannot carry expected_value"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "comparator": self.comparator.value,
            "expected_value": (
                self.expected_value.to_python()
                if isinstance(self.expected_value, FactValue)
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> StateCondition:
        payload = mapping(value, "state condition")
        if set(payload) != {"ref", "comparator", "expected_value"}:
            raise ValueError("state condition schema drifted")
        try:
            comparator = ConditionComparator(payload["comparator"])
        except (TypeError, ValueError) as exc:
            raise ValueError("condition comparator is invalid") from exc
        return cls(
            ref=payload["ref"],
            comparator=comparator,
            expected_value=payload["expected_value"],
        )


@dataclass(frozen=True, slots=True)
class DecisionOperator:
    """DecisionOperator@2: exact-base, typed intent with no write authority."""

    decision_id: str
    decision_type: str
    base_state_digest: str
    authority_id: str
    intent: str
    preconditions: tuple[StateCondition, ...] = ()
    bindings: tuple[ParameterBinding, ...] = ()
    add_facts: tuple[StateFact, ...] = ()
    delete_fact_refs: tuple[str, ...] = ()
    add_locks: tuple[StateLock, ...] = ()
    release_lock_refs: tuple[str, ...] = ()
    spawn_commitments: tuple[Commitment, ...] = ()
    spawn_obligations: tuple[DesignObligation, ...] = ()
    discharge_obligation_ids: tuple[str, ...] = ()
    add_dependencies: tuple[DependencyEdge, ...] = ()
    delete_dependency_refs: tuple[str, ...] = ()
    invalidates: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    SCHEMA = "DecisionOperator@2"

    def __post_init__(self) -> None:
        for value, field in (
            (self.decision_id, "decision_id"),
            (self.decision_type, "decision_type"),
            (self.authority_id, "authority_id"),
            (self.intent, "intent"),
        ):
            text(value, field)
        require_local_id(self.decision_id, "decision_id")
        if (
            not isinstance(self.base_state_digest, str)
            or len(self.base_state_digest) != 64
            or any(
                char not in "0123456789abcdef"
                for char in self.base_state_digest.lower()
            )
        ):
            raise ValueError("base_state_digest must be a SHA-256 hex digest")
        for field, values in (
            ("preconditions", self.preconditions),
            ("bindings", self.bindings),
            ("add_facts", self.add_facts),
            ("delete_fact_refs", self.delete_fact_refs),
            ("add_locks", self.add_locks),
            ("release_lock_refs", self.release_lock_refs),
            ("spawn_commitments", self.spawn_commitments),
            ("spawn_obligations", self.spawn_obligations),
            ("discharge_obligation_ids", self.discharge_obligation_ids),
            ("add_dependencies", self.add_dependencies),
            ("delete_dependency_refs", self.delete_dependency_refs),
            ("invalidates", self.invalidates),
            ("evidence_refs", self.evidence_refs),
        ):
            tuple_of(values, field)
        for ref in (
            *self.delete_fact_refs,
            *self.release_lock_refs,
            *self.delete_dependency_refs,
            *self.invalidates,
            *self.evidence_refs,
        ):
            require_logical_ref(ref, "operator reference")
        expected_types = (
            ("preconditions", self.preconditions, StateCondition),
            ("bindings", self.bindings, ParameterBinding),
            ("add_facts", self.add_facts, StateFact),
            ("add_locks", self.add_locks, StateLock),
            ("spawn_commitments", self.spawn_commitments, Commitment),
            (
                "spawn_obligations",
                self.spawn_obligations,
                DesignObligation,
            ),
            (
                "add_dependencies",
                self.add_dependencies,
                DependencyEdge,
            ),
        )
        for field, values, expected in expected_types:
            if any(not isinstance(item, expected) for item in values):
                raise TypeError(
                    f"{field} must contain {expected.__name__} values"
                )
        _unique(
            tuple(item.ref for item in self.preconditions),
            "precondition refs",
        )
        _unique(tuple(item.ref for item in self.bindings), "binding refs")
        _unique(tuple(item.ref for item in self.add_facts), "add fact refs")
        _unique(self.delete_fact_refs, "delete_fact_refs")
        _unique(
            tuple(item.target_ref for item in self.add_locks),
            "add lock target refs",
        )
        _unique(self.release_lock_refs, "release_lock_refs")
        _unique(
            tuple(item.commitment_id for item in self.spawn_commitments),
            "spawn commitment ids",
        )
        _unique(
            tuple(item.obligation_id for item in self.spawn_obligations),
            "spawn obligation ids",
        )
        _unique(
            self.discharge_obligation_ids,
            "discharge_obligation_ids",
        )
        _unique(
            tuple(item.ref for item in self.add_dependencies),
            "add dependency refs",
        )
        _unique(self.delete_dependency_refs, "delete_dependency_refs")
        _unique(self.invalidates, "invalidates")
        _unique(self.evidence_refs, "evidence_refs")
        if any(
            ref.startswith(("obligation:", "commitment:"))
            for ref in self.invalidates
        ):
            raise ValueError(
                "normative refs cannot be invalidated deliverables"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "decision_id": self.decision_id,
            "decision_type": self.decision_type,
            "base_state_digest": self.base_state_digest,
            "authority_id": self.authority_id,
            "intent": self.intent,
            "preconditions": [item.to_dict() for item in self.preconditions],
            "bindings": [item.to_dict() for item in self.bindings],
            "add_facts": [item.to_dict() for item in self.add_facts],
            "delete_fact_refs": list(self.delete_fact_refs),
            "add_locks": [item.to_dict() for item in self.add_locks],
            "release_lock_refs": list(self.release_lock_refs),
            "spawn_commitments": [
                item.to_dict() for item in self.spawn_commitments
            ],
            "spawn_obligations": [
                item.to_dict() for item in self.spawn_obligations
            ],
            "discharge_obligation_ids": list(
                self.discharge_obligation_ids
            ),
            "add_dependencies": [
                item.to_dict() for item in self.add_dependencies
            ],
            "delete_dependency_refs": list(self.delete_dependency_refs),
            "invalidates": list(self.invalidates),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DecisionOperator:
        payload = mapping(value, "decision operator")
        expected = {
            "schema",
            "decision_id",
            "decision_type",
            "base_state_digest",
            "authority_id",
            "intent",
            "preconditions",
            "bindings",
            "add_facts",
            "delete_fact_refs",
            "add_locks",
            "release_lock_refs",
            "spawn_commitments",
            "spawn_obligations",
            "discharge_obligation_ids",
            "add_dependencies",
            "delete_dependency_refs",
            "invalidates",
            "evidence_refs",
        }
        if set(payload) != expected:
            raise ValueError("decision operator schema drifted")
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("unsupported current decision-operator schema")
        return cls(
            decision_id=payload["decision_id"],
            decision_type=payload["decision_type"],
            base_state_digest=payload["base_state_digest"],
            authority_id=payload["authority_id"],
            intent=payload["intent"],
            preconditions=tuple(
                StateCondition.from_dict(item)
                for item in _list(
                    payload["preconditions"],
                    "preconditions",
                )
            ),
            bindings=tuple(
                ParameterBinding.from_dict(item)
                for item in _list(payload["bindings"], "bindings")
            ),
            add_facts=tuple(
                StateFact.from_dict(item)
                for item in _list(payload["add_facts"], "add_facts")
            ),
            delete_fact_refs=_string_tuple(
                payload["delete_fact_refs"],
                "delete_fact_refs",
            ),
            add_locks=tuple(
                StateLock.from_dict(item)
                for item in _list(payload["add_locks"], "add_locks")
            ),
            release_lock_refs=_string_tuple(
                payload["release_lock_refs"],
                "release_lock_refs",
            ),
            spawn_commitments=tuple(
                Commitment.from_dict(item)
                for item in _list(
                    payload["spawn_commitments"],
                    "spawn_commitments",
                )
            ),
            spawn_obligations=tuple(
                DesignObligation.from_dict(item)
                for item in _list(
                    payload["spawn_obligations"],
                    "spawn_obligations",
                )
            ),
            discharge_obligation_ids=_string_tuple(
                payload["discharge_obligation_ids"],
                "discharge_obligation_ids",
            ),
            add_dependencies=tuple(
                DependencyEdge.from_dict(item)
                for item in _list(
                    payload["add_dependencies"],
                    "add_dependencies",
                )
            ),
            delete_dependency_refs=_string_tuple(
                payload["delete_dependency_refs"],
                "delete_dependency_refs",
            ),
            invalidates=_string_tuple(
                payload["invalidates"],
                "invalidates",
            ),
            evidence_refs=_string_tuple(
                payload["evidence_refs"],
                "evidence_refs",
            ),
        )


class DecisionOperatorMigrationRequired(ValueError):
    """A legacy operator lacks semantics that must not be invented."""


@dataclass(frozen=True, slots=True)
class LegacyDecisionOperatorV1:
    """Read-only compatibility record; it cannot compile current state."""

    canonical_json: str

    SCHEMA = "DecisionOperator@1"

    def __post_init__(self) -> None:
        # A whole canonical document, not a bounded payload field: the owned
        # `text` rule caps at 2 000 characters and would reject a real record.
        if (
            not isinstance(self.canonical_json, str)
            or not self.canonical_json.strip()
        ):
            raise ValueError(
                "legacy operator canonical_json must be non-empty text"
            )
        payload = mapping(
            json.loads(self.canonical_json),
            "legacy decision operator",
        )
        if payload.get("schema") != self.SCHEMA:
            raise ValueError(
                "legacy decision operator has unsupported schema"
            )

    def to_dict(self) -> dict[str, object]:
        return dict(json.loads(self.canonical_json))

    def migrate(self) -> DecisionOperator:
        raise DecisionOperatorMigrationRequired(
            "DecisionOperator@1 lacks structured conditions and typed "
            "dependency effects; recompile from authoritative project evidence"
        )


def load_decision_operator_record(
    value: object,
) -> DecisionOperator | LegacyDecisionOperatorV1:
    payload = mapping(value, "decision operator record")
    schema = payload.get("schema")
    if schema == DecisionOperator.SCHEMA:
        return DecisionOperator.from_dict(payload)
    if schema == LegacyDecisionOperatorV1.SCHEMA:
        return LegacyDecisionOperatorV1(canonical_json(dict(payload)))
    raise ValueError("unsupported decision-operator schema")


def require_current_decision_operator(value: object) -> DecisionOperator:
    loaded = (
        load_decision_operator_record(value)
        if isinstance(value, Mapping)
        else value
    )
    if isinstance(loaded, LegacyDecisionOperatorV1):
        return loaded.migrate()
    if not isinstance(loaded, DecisionOperator):
        raise TypeError(
            "value must be a DecisionOperator@2 or operator record"
        )
    return loaded


@dataclass(frozen=True, slots=True)
class StateDelta:
    decision_id: str
    base_state_digest: str
    bindings: tuple[ParameterBinding, ...]
    add_facts: tuple[StateFact, ...]
    delete_fact_refs: tuple[str, ...]
    add_locks: tuple[StateLock, ...]
    release_lock_refs: tuple[str, ...]
    spawn_commitments: tuple[Commitment, ...]
    spawn_obligations: tuple[DesignObligation, ...]
    discharge_obligation_ids: tuple[str, ...]
    add_dependencies: tuple[DependencyEdge, ...]
    delete_dependency_refs: tuple[str, ...]
    invalidates: tuple[str, ...]

    SCHEMA = "StateDelta@2"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "decision_id": self.decision_id,
            "base_state_digest": self.base_state_digest,
            "bindings": [item.to_dict() for item in self.bindings],
            "add_facts": [item.to_dict() for item in self.add_facts],
            "delete_fact_refs": list(self.delete_fact_refs),
            "add_locks": [item.to_dict() for item in self.add_locks],
            "release_lock_refs": list(self.release_lock_refs),
            "spawn_commitments": [
                item.to_dict() for item in self.spawn_commitments
            ],
            "spawn_obligations": [
                item.to_dict() for item in self.spawn_obligations
            ],
            "discharge_obligation_ids": list(
                self.discharge_obligation_ids
            ),
            "add_dependencies": [
                item.to_dict() for item in self.add_dependencies
            ],
            "delete_dependency_refs": list(self.delete_dependency_refs),
            "invalidates": list(self.invalidates),
        }

    @property
    def delta_digest(self) -> str:
        return hashlib.sha256(
            canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ClosureReceipt:
    decision_id: str
    base_state_digest: str
    delta_digest: str
    direct_invalidations: tuple[str, ...]
    closure_invalidations: tuple[str, ...]
    generated_obligation_ids: tuple[str, ...]
    result_state_digest: str
    result_sufficient_digest: str

    SCHEMA = "DesignStateClosureReceipt@2"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "decision_id": self.decision_id,
            "base_state_digest": self.base_state_digest,
            "delta_digest": self.delta_digest,
            "direct_invalidations": list(self.direct_invalidations),
            "closure_invalidations": list(self.closure_invalidations),
            "generated_obligation_ids": list(
                self.generated_obligation_ids
            ),
            "result_state_digest": self.result_state_digest,
            "result_sufficient_digest": self.result_sufficient_digest,
        }


@dataclass(frozen=True, slots=True)
class CompiledDecisionTransition:
    operator: DecisionOperator
    delta: StateDelta
    state: OperationalMarkovState
    receipt: ClosureReceipt


def compile_decision_operator(
    state: OperationalMarkovState,
    operator: DecisionOperator,
) -> CompiledDecisionTransition:
    """Compile one typed action through explicit deterministic closure."""

    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be an OperationalMarkovState")
    if not isinstance(operator, DecisionOperator):
        raise TypeError("operator must be a DecisionOperator")
    if operator.base_state_digest != state.state_digest:
        raise DecisionCompilationError("decision operator exact base is stale")

    _check_preconditions(state, operator.preconditions)
    _check_lock_authority(state, operator)
    _check_spawn_authority(operator)
    delta = _delta_from_operator(operator)
    if not _delta_has_effect(delta):
        raise DecisionCompilationError(
            "decision operator has no state effect"
        )

    facts = {item.ref: item for item in state.facts}
    for ref in delta.delete_fact_refs:
        if ref not in facts:
            raise DecisionCompilationError(
                f"cannot delete missing fact: {ref}"
            )
        del facts[ref]
    for item in delta.add_facts:
        facts[item.ref] = item

    bindings = {item.ref: item for item in state.bindings}
    for item in delta.bindings:
        bindings[item.ref] = item

    locks = {item.target_ref: item for item in state.locks}
    for ref in delta.release_lock_refs:
        if ref not in locks:
            raise DecisionCompilationError(
                f"cannot release missing lock: {ref}"
            )
        del locks[ref]
    for item in delta.add_locks:
        existing = locks.get(item.target_ref)
        if existing is not None and existing != item:
            raise DecisionCompilationError(
                f"lock identity collision: {item.target_ref}"
            )
        locks[item.target_ref] = item

    commitments = {
        item.commitment_id: item for item in state.commitments
    }
    for item in delta.spawn_commitments:
        existing = commitments.get(item.commitment_id)
        if existing is not None and existing != item:
            _validate_commitment_successor(
                existing,
                item,
                actor_authority_id=operator.authority_id,
            )
        commitments[item.commitment_id] = item

    obligations = {
        item.obligation_id: item for item in state.obligations
    }
    for obligation_id in delta.discharge_obligation_ids:
        existing = obligations.get(obligation_id)
        if existing is None:
            raise DecisionCompilationError(
                f"cannot discharge unknown obligation: {obligation_id}"
            )
        if existing.status is not ObligationStatus.OPEN:
            raise DecisionCompilationError(
                f"obligation is not open: {obligation_id}"
            )
        obligations[obligation_id] = replace(
            existing,
            status=ObligationStatus.SATISFIED,
        )
    for item in delta.spawn_obligations:
        existing = obligations.get(item.obligation_id)
        if existing is not None and existing != item:
            raise DecisionCompilationError(
                f"obligation identity collision: {item.obligation_id}"
            )
        obligations[item.obligation_id] = item

    dependencies = {item.ref: item for item in state.dependencies}
    for ref in delta.delete_dependency_refs:
        if ref not in dependencies:
            raise DecisionCompilationError(
                f"cannot delete missing dependency: {ref}"
            )
        del dependencies[ref]
    for item in delta.add_dependencies:
        existing = dependencies.get(item.ref)
        if existing is not None and existing != item:
            raise DecisionCompilationError(
                f"dependency identity collision: {item.ref}"
            )
        dependencies[item.ref] = item

    closure_refs = _dependency_closure(
        delta.invalidates,
        tuple(dependencies.values()),
    )
    generated_ids: list[str] = []
    decision_ref = f"decision:{operator.decision_id}"
    for ref in closure_refs:
        obligation_id = _revalidation_obligation_id(
            operator.decision_id,
            ref,
        )
        generated = DesignObligation(
            obligation_id=obligation_id,
            statement=(
                f"Revalidate {ref} after decision "
                f"{operator.decision_id}."
            ),
            source_ref=decision_ref,
            subject_refs=(ref,),
        )
        existing = obligations.get(obligation_id)
        if existing is not None and existing != generated:
            raise DecisionCompilationError(
                f"generated obligation collision: {obligation_id}"
            )
        obligations[obligation_id] = generated
        generated_ids.append(obligation_id)

    refreshed_refs = {
        *(item.ref for item in delta.add_facts),
        *(item.ref for item in delta.bindings),
    }
    invalidated = (
        set(state.invalidated_refs) - refreshed_refs
    ) | set(closure_refs)

    # Readiness must be judged against the successor's invalidation set, or
    # obligations conditioned on invalidated:* refs disagree with the state
    # invariant validator and the compiled state cannot construct.
    refreshed_obligations = _refresh_obligation_readiness(
        facts=facts,
        bindings=bindings,
        locks=locks,
        commitments=commitments,
        obligations=obligations,
        dependencies=dependencies,
        phase=state.phase,
        invalidated=frozenset(invalidated),
    )
    for item in delta.spawn_obligations:
        compiled = refreshed_obligations[item.obligation_id]
        if compiled.status is not item.status:
            raise DecisionCompilationError(
                "spawned obligation readiness mismatch: "
                f"{item.obligation_id} must be {compiled.status.value}"
            )
    obligations = refreshed_obligations
    evidence_refs = tuple(
        sorted(set(state.evidence_refs) | set(operator.evidence_refs))
    )
    next_branch = BranchRef(
        run=state.branch.run,
        branch_id=state.branch.branch_id,
        epoch=state.branch.epoch + 1,
    )
    next_state = OperationalMarkovState(
        branch=next_branch,
        compiler_version=state.compiler_version,
        phase=state.phase,
        facts=tuple(sorted(facts.values(), key=lambda item: item.ref)),
        bindings=tuple(
            sorted(bindings.values(), key=lambda item: item.ref)
        ),
        locks=tuple(
            sorted(locks.values(), key=lambda item: item.target_ref)
        ),
        commitments=tuple(
            sorted(
                commitments.values(),
                key=lambda item: item.commitment_id,
            )
        ),
        obligations=tuple(
            sorted(
                obligations.values(),
                key=lambda item: item.obligation_id,
            )
        ),
        dependencies=tuple(
            sorted(
                dependencies.values(),
                key=lambda item: item.identity,
            )
        ),
        invalidated_refs=tuple(sorted(invalidated)),
        evidence_refs=evidence_refs,
    )
    receipt = ClosureReceipt(
        decision_id=operator.decision_id,
        base_state_digest=state.state_digest,
        delta_digest=delta.delta_digest,
        direct_invalidations=tuple(sorted(delta.invalidates)),
        closure_invalidations=closure_refs,
        generated_obligation_ids=tuple(sorted(generated_ids)),
        result_state_digest=next_state.state_digest,
        result_sufficient_digest=next_state.sufficient_digest,
    )
    return CompiledDecisionTransition(
        operator=operator,
        delta=delta,
        state=next_state,
        receipt=receipt,
    )


def _check_preconditions(
    state: OperationalMarkovState,
    conditions: tuple[StateCondition, ...],
) -> None:
    for condition in conditions:
        actual = state.value_for_ref(condition.ref)
        if condition.comparator is ConditionComparator.EXISTS:
            passed = actual is not None
        elif condition.comparator is ConditionComparator.ABSENT:
            passed = actual is None
        elif condition.comparator is ConditionComparator.EQUALS:
            passed = (
                canonical_json(actual)
                == condition.expected_value.canonical_json
            )
        else:
            passed = (
                canonical_json(actual)
                != condition.expected_value.canonical_json
            )
        if not passed:
            raise DecisionCompilationError(
                "precondition failed: "
                f"{condition.ref} {condition.comparator.value}"
            )


def _check_lock_authority(
    state: OperationalMarkovState,
    operator: DecisionOperator,
) -> None:
    mutated_refs = {
        *operator.delete_fact_refs,
        *(item.ref for item in operator.add_facts),
        *(item.ref for item in operator.bindings),
        *operator.release_lock_refs,
    }
    locks = {item.target_ref: item for item in state.locks}
    for ref in mutated_refs:
        lock = locks.get(ref)
        if lock is not None and lock.authority_id != operator.authority_id:
            raise DecisionCompilationError(
                f"locked state requires authority {lock.authority_id}: {ref}"
            )


def _check_spawn_authority(operator: DecisionOperator) -> None:
    for lock in operator.add_locks:
        if lock.authority_id != operator.authority_id:
            raise DecisionCompilationError(
                "an operator cannot create a lock for another authority"
            )
    for commitment in operator.spawn_commitments:
        if commitment.status is CommitmentStatus.PROPOSED:
            continue
        permitted = {
            commitment.authority_id,
            commitment.authorized_by,
            *commitment.permitted_authority_ids,
        }
        if operator.authority_id not in permitted:
            raise DecisionCompilationError(
                "an operator cannot activate another authority's commitment"
            )
    if any(
        item.status
        not in {
            ObligationStatus.OPEN,
            ObligationStatus.BLOCKED,
        }
        for item in operator.spawn_obligations
    ):
        raise DecisionCompilationError(
            "new obligations must enter the state as open or blocked"
        )


def _validate_commitment_successor(
    current: Commitment,
    successor: Commitment,
    *,
    actor_authority_id: str,
) -> None:
    """Accept only a P015-authorized lifecycle successor with the same id."""

    statuses = [successor.status]
    if (
        current.status is CommitmentStatus.PROPOSED
        and successor.status is CommitmentStatus.ACTIVE
    ):
        statuses = [
            CommitmentStatus.ACCEPTED,
            CommitmentStatus.ACTIVE,
        ]
    compiled = current
    try:
        for index, status in enumerate(statuses):
            successor_id = None
            if status in {
                CommitmentStatus.REVISED,
                CommitmentStatus.SUPERSEDED,
            }:
                added = tuple(
                    item
                    for item in successor.successor_ids
                    if item not in compiled.successor_ids
                )
                if len(added) != 1:
                    raise CommitmentTransitionError(
                        "revision requires exactly one new successor"
                    )
                successor_id = added[0]
            monitor_state_ref = None
            if (
                status is CommitmentStatus.ACTIVE
                and compiled.status is CommitmentStatus.ACCEPTED
            ):
                monitor_state_ref = successor.monitor_state_ref
            elif (
                index == len(statuses) - 1
                and successor.monitor_state_ref != compiled.monitor_state_ref
            ):
                raise CommitmentTransitionError(
                    "lifecycle transition cannot rewrite monitor state"
                )
            compiled = transition_commitment(
                compiled,
                status,
                actor_authority_id=actor_authority_id,
                successor_id=successor_id,
                monitor_state_ref=monitor_state_ref,
            )
    except (
        CommitmentAuthorityError,
        CommitmentTransitionError,
        ValueError,
    ) as exc:
        raise DecisionCompilationError(
            f"invalid commitment lifecycle transition: "
            f"{current.commitment_id}"
        ) from exc
    if compiled != successor:
        raise DecisionCompilationError(
            f"commitment identity collision: {current.commitment_id}"
        )


def _delta_from_operator(operator: DecisionOperator) -> StateDelta:
    return StateDelta(
        decision_id=operator.decision_id,
        base_state_digest=operator.base_state_digest,
        bindings=operator.bindings,
        add_facts=operator.add_facts,
        delete_fact_refs=operator.delete_fact_refs,
        add_locks=operator.add_locks,
        release_lock_refs=operator.release_lock_refs,
        spawn_commitments=operator.spawn_commitments,
        spawn_obligations=operator.spawn_obligations,
        discharge_obligation_ids=operator.discharge_obligation_ids,
        add_dependencies=operator.add_dependencies,
        delete_dependency_refs=operator.delete_dependency_refs,
        invalidates=operator.invalidates,
    )


def _delta_has_effect(delta: StateDelta) -> bool:
    return any(
        (
            delta.bindings,
            delta.add_facts,
            delta.delete_fact_refs,
            delta.add_locks,
            delta.release_lock_refs,
            delta.spawn_commitments,
            delta.spawn_obligations,
            delta.discharge_obligation_ids,
            delta.add_dependencies,
            delta.delete_dependency_refs,
            delta.invalidates,
        )
    )


def _dependency_closure(
    direct_invalidations: tuple[str, ...],
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
    visited = set(direct_invalidations)
    queue = list(sorted(direct_invalidations))
    while queue:
        current = queue.pop(0)
        for downstream in sorted(adjacency.get(current, ())):
            if downstream in visited:
                continue
            visited.add(downstream)
            if len(visited) > _MAX_ITEMS:
                raise DecisionCompilationError(
                    "dependency closure exceeds bounded item count"
                )
            queue.append(downstream)
    return tuple(sorted(visited))


_MISSING_VALUE = object()


def _refresh_obligation_readiness(
    *,
    facts: dict[str, StateFact],
    bindings: dict[str, ParameterBinding],
    locks: dict[str, StateLock],
    commitments: dict[str, Commitment],
    obligations: dict[str, DesignObligation],
    dependencies: dict[str, DependencyEdge],
    phase: str,
    invalidated: frozenset[str],
) -> dict[str, DesignObligation]:
    """Compile current blockers and exact conditions into lifecycle status.

    Reference resolution must mirror ``OperationalMarkovState.value_for_ref``
    (including ``invalidated:*`` fallbacks), and the OPEN/BLOCKED assignment
    is iterated to a fixed point so the successor state always satisfies its
    own readiness invariant validator.
    """

    blocking_edges = {
        (item.upstream_ref, item.downstream_ref)
        for item in dependencies.values()
        if item.effect is DependencyEffect.BLOCKS
    }
    base_values: dict[str, object] = {
        "state:phase": phase,
        **{item.ref: item.python_value for item in facts.values()},
        **{item.ref: item.value for item in bindings.values()},
        **{
            f"lock:{item.target_ref}": item.authority_id
            for item in locks.values()
        },
        **{
            f"commitment:{item.commitment_id}": item.status.value
            for item in commitments.values()
        },
    }

    obligation_refs = {
        f"obligation:{item.obligation_id}": item
        for item in obligations.values()
    }
    for obligation_id, item in obligations.items():
        if item.status not in {
            ObligationStatus.OPEN,
            ObligationStatus.BLOCKED,
        }:
            continue
        downstream_ref = f"obligation:{obligation_id}"
        for blocker_ref in item.blocked_by:
            blocker = obligation_refs.get(blocker_ref)
            if blocker is None:
                raise DecisionCompilationError(
                    f"obligation blocker is missing: {blocker_ref}"
                )
            if (blocker_ref, downstream_ref) not in blocking_edges:
                raise DecisionCompilationError(
                    "obligation blocker lacks a blocking dependency edge"
                )

    current = dict(obligations)
    for _ in range(len(current) + 1):
        refs = {
            f"obligation:{item.obligation_id}": item
            for item in current.values()
        }
        values = {
            **base_values,
            **{ref: item.status.value for ref, item in refs.items()},
        }
        changed = False
        refreshed: dict[str, DesignObligation] = {}
        for obligation_id, item in current.items():
            if item.status not in {
                ObligationStatus.OPEN,
                ObligationStatus.BLOCKED,
            }:
                refreshed[obligation_id] = item
                continue
            if item.condition is None:
                condition_ready = True
            else:
                ref = item.condition.ref
                value = values.get(ref, _MISSING_VALUE)
                if value is _MISSING_VALUE:
                    if ref.startswith("invalidated:"):
                        value = (
                            "true"
                            if ref.removeprefix("invalidated:")
                            in invalidated
                            else None
                        )
                    else:
                        value = None
                condition_ready = (
                    canonical_json(value)
                    == item.condition.expected_value.canonical_json
                )
            blockers_ready = all(
                refs[ref].status
                in {
                    ObligationStatus.SATISFIED,
                    ObligationStatus.WAIVED,
                }
                for ref in item.blocked_by
            )
            status = (
                ObligationStatus.OPEN
                if condition_ready and blockers_ready
                else ObligationStatus.BLOCKED
            )
            if item.status is status:
                refreshed[obligation_id] = item
            else:
                changed = True
                refreshed[obligation_id] = replace(item, status=status)
        current = refreshed
        if not changed:
            return current
    raise DecisionCompilationError(
        "obligation readiness does not converge to a stable assignment"
    )


def _revalidation_obligation_id(
    decision_id: str,
    subject_ref: str,
) -> str:
    digest = hashlib.sha256(
        f"{decision_id}\0{subject_ref}".encode("utf-8")
    ).hexdigest()[:20]
    return f"revalidate-{digest}"
