"""Typed branch-local state for the next architectural design move.

This module stores only future-relevant compiled consequences. Prompt history,
tool transcripts, rejected drafts, and canonical write authority remain
outside the operational state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.state.commitments import Commitment


_MAX_ITEMS = 4096
_MAX_FACT_JSON_BYTES = 64_000
_MAX_QUALIFICATION_CHARS = 1_000
_PORTABLE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s\\]+$")
_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class StateDomain(StrEnum):
    """Factorized namespaces without embedding a building answer."""

    BRIEF = "brief"
    SEMANTIC = "semantic"
    GEOMETRY = "geometry"
    PARAMETER = "parameter"
    DECISION = "decision"
    EVALUATION = "evaluation"
    UNKNOWN = "unknown"
    DELIVERABLE = "deliverable"
    BUDGET = "budget"
    CAPABILITY = "capability"


class ObligationStatus(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    SATISFIED = "satisfied"
    WAIVED = "waived"


class FactEpistemicStatus(StrEnum):
    """Decision-relevant status, separate from a fact's value."""

    OBSERVED = "observed"
    DECLARED = "declared"
    DERIVED = "derived"
    HYPOTHESIS = "hypothesis"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


class DependencyEffect(StrEnum):
    """What may propagate along one named dependency."""

    INVALIDATES = "invalidates"
    REQUIRES_REVALIDATION = "requires_revalidation"
    BLOCKS = "blocks"
    SUPPORTS_ONLY = "supports_only"


class OperationalStateMigrationRequired(ValueError):
    """A legacy state lacks semantics that must not be invented."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def require_logical_ref(value: object, field: str) -> str:
    text = _text(value, field)
    if text.startswith("file:") or re.match(r"^[A-Za-z]:[\\/]", text):
        raise ValueError(f"{field} cannot be an absolute machine path")
    if _PORTABLE_REF.fullmatch(text) is None:
        raise ValueError(f"{field} must be a portable logical reference")
    return text


def _tuple(value: object, field: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def require_local_id(value: object, field: str) -> str:
    text = _text(value, field)
    if _LOCAL_ID.fullmatch(text) is None:
        raise ValueError(f"{field} must be a portable local id")
    return text


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be bounded JSON data") from exc


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def _enum(enum_type: type[StrEnum], value: object, field: str) -> Any:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field} has an unsupported value") from exc


@dataclass(frozen=True, slots=True)
class FactValue:
    """Immutable canonical JSON value used by operational facts."""

    canonical_json: str

    def __post_init__(self) -> None:
        _text(self.canonical_json, "fact canonical_json")
        if len(self.canonical_json.encode("utf-8")) > _MAX_FACT_JSON_BYTES:
            raise ValueError("fact value exceeds bounded JSON size")
        try:
            decoded = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("fact canonical_json is invalid") from exc
        if decoded is None:
            raise ValueError("fact value cannot be null")
        if _canonical_json(decoded) != self.canonical_json:
            raise ValueError("fact value must already be canonical JSON")

    @classmethod
    def from_value(cls, value: object) -> FactValue:
        if isinstance(value, cls):
            return value
        encoded = _canonical_json(value)
        if len(encoded.encode("utf-8")) > _MAX_FACT_JSON_BYTES:
            raise ValueError("fact value exceeds bounded JSON size")
        return cls(encoded)

    def to_python(self) -> object:
        return json.loads(self.canonical_json)


@dataclass(frozen=True, slots=True)
class ObligationCondition:
    """One exact activation condition without executable predicates."""

    ref: str
    expected_value: FactValue | object

    def __post_init__(self) -> None:
        require_logical_ref(self.ref, "obligation condition ref")
        object.__setattr__(
            self,
            "expected_value",
            FactValue.from_value(self.expected_value),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "expected_value": self.expected_value.to_python(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ObligationCondition:
        payload = _mapping(value, "obligation condition")
        if set(payload) != {"ref", "expected_value"}:
            raise ValueError("obligation condition schema drifted")
        return cls(
            ref=payload["ref"],
            expected_value=payload["expected_value"],
        )


@dataclass(frozen=True, slots=True)
class StateFact:
    domain: StateDomain
    key: str
    value: FactValue | object
    source_ref: str
    epistemic_status: FactEpistemicStatus = FactEpistemicStatus.DERIVED
    confidence: float | None = None
    qualification: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.domain, StateDomain):
            raise TypeError("fact domain must be a StateDomain")
        require_local_id(self.key, "fact key")
        object.__setattr__(self, "value", FactValue.from_value(self.value))
        require_logical_ref(self.source_ref, "fact source_ref")
        if not isinstance(self.epistemic_status, FactEpistemicStatus):
            raise TypeError(
                "fact epistemic_status must be a FactEpistemicStatus"
            )
        if self.confidence is not None:
            if (
                isinstance(self.confidence, bool)
                or not isinstance(self.confidence, (int, float))
                or not 0.0 <= float(self.confidence) <= 1.0
            ):
                raise ValueError("fact confidence must be between 0 and 1")
            object.__setattr__(self, "confidence", float(self.confidence))
        if self.qualification is not None:
            _text(self.qualification, "fact qualification")
            if len(self.qualification) > _MAX_QUALIFICATION_CHARS:
                raise ValueError("fact qualification exceeds bounded text")

    @property
    def ref(self) -> str:
        return f"fact:{self.domain.value}:{self.key}"

    @property
    def python_value(self) -> object:
        return self.value.to_python()

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain.value,
            "key": self.key,
            "value": self.python_value,
            "source_ref": self.source_ref,
            "epistemic_status": self.epistemic_status.value,
            "confidence": self.confidence,
            "qualification": self.qualification,
        }

    @classmethod
    def from_dict(cls, value: object) -> StateFact:
        payload = _mapping(value, "state fact")
        expected = {
            "domain",
            "key",
            "value",
            "source_ref",
            "epistemic_status",
            "confidence",
            "qualification",
        }
        if set(payload) != expected:
            raise ValueError("state fact schema drifted")
        return cls(
            domain=_enum(
                StateDomain,
                payload["domain"],
                "fact domain",
            ),
            key=payload["key"],
            value=payload["value"],
            source_ref=payload["source_ref"],
            epistemic_status=_enum(
                FactEpistemicStatus,
                payload["epistemic_status"],
                "fact epistemic_status",
            ),
            confidence=payload["confidence"],
            qualification=payload["qualification"],
        )


@dataclass(frozen=True, slots=True)
class ParameterBinding:
    key: str
    value: str
    source_ref: str

    def __post_init__(self) -> None:
        require_local_id(self.key, "binding key")
        _text(self.value, "binding value")
        require_logical_ref(self.source_ref, "binding source_ref")

    @property
    def ref(self) -> str:
        return f"binding:{self.key}"

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "value": self.value,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> ParameterBinding:
        payload = _mapping(value, "parameter binding")
        if set(payload) != {"key", "value", "source_ref"}:
            raise ValueError("parameter binding schema drifted")
        return cls(
            key=payload["key"],
            value=payload["value"],
            source_ref=payload["source_ref"],
        )


@dataclass(frozen=True, slots=True)
class StateLock:
    target_ref: str
    authority_id: str
    source_ref: str

    def __post_init__(self) -> None:
        require_logical_ref(self.target_ref, "lock target_ref")
        _text(self.authority_id, "lock authority_id")
        require_logical_ref(self.source_ref, "lock source_ref")

    def to_dict(self) -> dict[str, str]:
        return {
            "target_ref": self.target_ref,
            "authority_id": self.authority_id,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> StateLock:
        payload = _mapping(value, "state lock")
        if set(payload) != {
            "target_ref",
            "authority_id",
            "source_ref",
        }:
            raise ValueError("state lock schema drifted")
        return cls(
            target_ref=payload["target_ref"],
            authority_id=payload["authority_id"],
            source_ref=payload["source_ref"],
        )


@dataclass(frozen=True, slots=True)
class DesignObligation:
    obligation_id: str
    statement: str
    source_ref: str
    status: ObligationStatus = ObligationStatus.OPEN
    subject_refs: tuple[str, ...] = ()
    validator_ref: str | None = None
    condition: ObligationCondition | None = None
    blocked_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_local_id(self.obligation_id, "obligation_id")
        _text(self.statement, "obligation statement")
        require_logical_ref(self.source_ref, "obligation source_ref")
        if not isinstance(self.status, ObligationStatus):
            raise TypeError("obligation status must be an ObligationStatus")
        _tuple(self.subject_refs, "obligation subject_refs")
        for ref in self.subject_refs:
            require_logical_ref(ref, "obligation subject_ref")
        _unique(self.subject_refs, "obligation subject_refs")
        if self.validator_ref is not None:
            require_logical_ref(self.validator_ref, "obligation validator_ref")
        if self.condition is not None and not isinstance(
            self.condition,
            ObligationCondition,
        ):
            raise TypeError(
                "obligation condition must be an ObligationCondition"
            )
        _tuple(self.blocked_by, "obligation blocked_by")
        for ref in self.blocked_by:
            require_logical_ref(ref, "obligation blocker ref")
            if not ref.startswith("obligation:"):
                raise ValueError(
                    "obligation blockers must name obligation refs"
                )
        _unique(self.blocked_by, "obligation blocked_by")
        if (
            self.status is ObligationStatus.BLOCKED
            and self.condition is None
            and not self.blocked_by
        ):
            raise ValueError(
                "blocked obligation requires a condition or blocker"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "obligation_id": self.obligation_id,
            "statement": self.statement,
            "source_ref": self.source_ref,
            "status": self.status.value,
            "subject_refs": list(self.subject_refs),
            "validator_ref": self.validator_ref,
            "condition": (
                self.condition.to_dict()
                if self.condition is not None
                else None
            ),
            "blocked_by": list(self.blocked_by),
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignObligation:
        payload = _mapping(value, "design obligation")
        expected = {
            "obligation_id",
            "statement",
            "source_ref",
            "status",
            "subject_refs",
            "validator_ref",
            "condition",
            "blocked_by",
        }
        if set(payload) != expected:
            raise ValueError("design obligation schema drifted")
        condition = payload["condition"]
        return cls(
            obligation_id=payload["obligation_id"],
            statement=payload["statement"],
            source_ref=payload["source_ref"],
            status=_enum(
                ObligationStatus,
                payload["status"],
                "obligation status",
            ),
            subject_refs=_string_tuple(
                payload["subject_refs"],
                "obligation subject_refs",
            ),
            validator_ref=payload["validator_ref"],
            condition=(
                None
                if condition is None
                else ObligationCondition.from_dict(condition)
            ),
            blocked_by=_string_tuple(
                payload["blocked_by"],
                "obligation blocked_by",
            ),
        )


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    upstream_ref: str
    downstream_ref: str
    relation: str
    source_ref: str
    effect: DependencyEffect = DependencyEffect.SUPPORTS_ONLY

    def __post_init__(self) -> None:
        require_logical_ref(self.upstream_ref, "dependency upstream_ref")
        require_logical_ref(self.downstream_ref, "dependency downstream_ref")
        if self.upstream_ref == self.downstream_ref:
            raise ValueError("dependency cannot be a self edge")
        _text(self.relation, "dependency relation")
        require_logical_ref(self.source_ref, "dependency source_ref")
        if not isinstance(self.effect, DependencyEffect):
            raise TypeError(
                "dependency effect must be a DependencyEffect"
            )
        if (
            self.effect
            in {
                DependencyEffect.INVALIDATES,
                DependencyEffect.REQUIRES_REVALIDATION,
            }
            and self.downstream_ref.startswith(
                ("obligation:", "commitment:")
            )
        ):
            raise ValueError(
                "normative refs require blocking or support-only edges"
            )
        if (
            self.effect is DependencyEffect.BLOCKS
            and (
                not self.upstream_ref.startswith("obligation:")
                or not self.downstream_ref.startswith("obligation:")
            )
        ):
            raise ValueError(
                "blocking dependency must connect obligation refs"
            )

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.upstream_ref,
            self.downstream_ref,
            self.relation,
            self.effect.value,
        )

    @property
    def ref(self) -> str:
        digest = hashlib.sha256(
            _canonical_json(self.identity).encode("utf-8")
        ).hexdigest()
        return f"dependency:{digest}"

    def to_dict(self) -> dict[str, str]:
        return {
            "upstream_ref": self.upstream_ref,
            "downstream_ref": self.downstream_ref,
            "relation": self.relation,
            "source_ref": self.source_ref,
            "effect": self.effect.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> DependencyEdge:
        payload = _mapping(value, "dependency edge")
        expected = {
            "upstream_ref",
            "downstream_ref",
            "relation",
            "source_ref",
            "effect",
        }
        if set(payload) != expected:
            raise ValueError("dependency edge schema drifted")
        return cls(
            upstream_ref=payload["upstream_ref"],
            downstream_ref=payload["downstream_ref"],
            relation=payload["relation"],
            source_ref=payload["source_ref"],
            effect=_enum(
                DependencyEffect,
                payload["effect"],
                "dependency effect",
            ),
        )


@dataclass(frozen=True, slots=True)
class OperationalMarkovState:
    """OperationalMarkovState@3: a constructed sufficient working summary.

    ``state_digest`` includes branch epoch for exact-base concurrency checks.
    ``sufficient_digest`` excludes branch history so two compiled states with
    the same future-relevant content can be compared operationally.
    """

    branch: BranchRef
    compiler_version: str
    phase: str
    facts: tuple[StateFact, ...] = ()
    bindings: tuple[ParameterBinding, ...] = ()
    locks: tuple[StateLock, ...] = ()
    commitments: tuple[Commitment, ...] = ()
    obligations: tuple[DesignObligation, ...] = ()
    dependencies: tuple[DependencyEdge, ...] = ()
    invalidated_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    SCHEMA = "OperationalMarkovState@3"

    def __post_init__(self) -> None:
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        self.canonical_base.require_digest()
        _text(self.compiler_version, "compiler_version")
        _text(self.phase, "phase")
        for field, values in (
            ("facts", self.facts),
            ("bindings", self.bindings),
            ("locks", self.locks),
            ("commitments", self.commitments),
            ("obligations", self.obligations),
            ("dependencies", self.dependencies),
            ("invalidated_refs", self.invalidated_refs),
            ("evidence_refs", self.evidence_refs),
        ):
            _tuple(values, field)
        if any(not isinstance(item, StateFact) for item in self.facts):
            raise TypeError("facts must contain StateFact values")
        if any(
            not isinstance(item, ParameterBinding) for item in self.bindings
        ):
            raise TypeError("bindings must contain ParameterBinding values")
        if any(not isinstance(item, StateLock) for item in self.locks):
            raise TypeError("locks must contain StateLock values")
        if any(
            not isinstance(item, Commitment) for item in self.commitments
        ):
            raise TypeError("commitments must contain Commitment values")
        if any(
            not isinstance(item, DesignObligation)
            for item in self.obligations
        ):
            raise TypeError("obligations must contain DesignObligation values")
        if any(
            not isinstance(item, DependencyEdge)
            for item in self.dependencies
        ):
            raise TypeError("dependencies must contain DependencyEdge values")
        _unique(tuple(item.ref for item in self.facts), "fact refs")
        _unique(tuple(item.ref for item in self.bindings), "binding refs")
        _unique(
            tuple(item.target_ref for item in self.locks),
            "lock target refs",
        )
        _unique(
            tuple(item.commitment_id for item in self.commitments),
            "commitment ids",
        )
        _unique(
            tuple(item.obligation_id for item in self.obligations),
            "obligation ids",
        )
        dependency_ids = tuple(
            "|".join(item.identity) for item in self.dependencies
        )
        _unique(dependency_ids, "dependency identities")
        obligation_refs = {
            f"obligation:{item.obligation_id}"
            for item in self.obligations
        }
        blocking_edges = {
            (item.upstream_ref, item.downstream_ref)
            for item in self.dependencies
            if item.effect is DependencyEffect.BLOCKS
        }
        declared_blockers = {
            (
                blocker_ref,
                f"obligation:{item.obligation_id}",
            )
            for item in self.obligations
            for blocker_ref in item.blocked_by
        }
        if blocking_edges != declared_blockers:
            raise ValueError(
                "blocking dependencies must exactly match blocked_by refs"
            )
        for item in self.obligations:
            downstream_ref = f"obligation:{item.obligation_id}"
            for blocker_ref in item.blocked_by:
                if blocker_ref not in obligation_refs:
                    raise ValueError(
                        f"obligation blocker is missing: {blocker_ref}"
                    )
                if (blocker_ref, downstream_ref) not in blocking_edges:
                    raise ValueError(
                        "obligation blocker lacks a blocking dependency edge"
                    )
        for ref in (*self.invalidated_refs, *self.evidence_refs):
            require_logical_ref(ref, "state reference")
        if any(
            ref.startswith(("obligation:", "commitment:"))
            for ref in self.invalidated_refs
        ):
            raise ValueError(
                "normative refs cannot be invalidated deliverables"
            )
        _unique(self.invalidated_refs, "invalidated_refs")
        _unique(self.evidence_refs, "evidence_refs")
        _validate_blocking_acyclic(self.obligations)
        _validate_obligation_readiness(self)

    @property
    def canonical_base(self) -> ProjectVersionRef:
        return self.branch.run.base

    @property
    def epoch(self) -> int:
        return self.branch.epoch

    def value_for_ref(self, ref: str) -> object | None:
        if ref == "state:phase":
            return self.phase
        facts = {item.ref: item.python_value for item in self.facts}
        if ref in facts:
            return facts[ref]
        bindings = {item.ref: item.value for item in self.bindings}
        if ref in bindings:
            return bindings[ref]
        locks = {f"lock:{item.target_ref}": item.authority_id for item in self.locks}
        if ref in locks:
            return locks[ref]
        commitments = {
            f"commitment:{item.commitment_id}": item.status.value
            for item in self.commitments
        }
        if ref in commitments:
            return commitments[ref]
        obligations = {
            f"obligation:{item.obligation_id}": item.status.value
            for item in self.obligations
        }
        if ref in obligations:
            return obligations[ref]
        if ref.startswith("invalidated:"):
            target = ref.removeprefix("invalidated:")
            return "true" if target in self.invalidated_refs else None
        return None

    @property
    def sufficient_digest(self) -> str:
        return hashlib.sha256(
            _canonical_json(self._future_identity()).encode("utf-8")
        ).hexdigest()

    @property
    def state_digest(self) -> str:
        identity = {
            "branch": _branch_dict(self.branch),
            "future": self._future_identity(),
        }
        return hashlib.sha256(
            _canonical_json(identity).encode("utf-8")
        ).hexdigest()

    def _future_identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "canonical_base": _version_dict(self.canonical_base),
            "compiler_version": self.compiler_version,
            "phase": self.phase,
            "facts": [
                item.to_dict()
                for item in sorted(self.facts, key=lambda item: item.ref)
            ],
            "bindings": [
                item.to_dict()
                for item in sorted(self.bindings, key=lambda item: item.ref)
            ],
            "locks": [
                item.to_dict()
                for item in sorted(
                    self.locks,
                    key=lambda item: item.target_ref,
                )
            ],
            "commitments": [
                item.to_dict()
                for item in sorted(
                    self.commitments,
                    key=lambda item: item.commitment_id,
                )
            ],
            "obligations": [
                item.to_dict()
                for item in sorted(
                    self.obligations,
                    key=lambda item: item.obligation_id,
                )
            ],
            "dependencies": [
                item.to_dict()
                for item in sorted(
                    self.dependencies,
                    key=lambda item: item.identity,
                )
            ],
            "invalidated_refs": sorted(self.invalidated_refs),
            "evidence_refs": sorted(self.evidence_refs),
        }

    def to_dict(self) -> dict[str, object]:
        payload = {
            **self._future_identity(),
            "branch": _branch_dict(self.branch),
            "state_digest": self.state_digest,
            "sufficient_digest": self.sufficient_digest,
        }
        return payload

    @classmethod
    def from_dict(cls, value: object) -> OperationalMarkovState:
        payload = _mapping(value, "operational state")
        expected = {
            "schema",
            "canonical_base",
            "compiler_version",
            "phase",
            "facts",
            "bindings",
            "locks",
            "commitments",
            "obligations",
            "dependencies",
            "invalidated_refs",
            "evidence_refs",
            "branch",
            "state_digest",
            "sufficient_digest",
        }
        if set(payload) != expected:
            raise ValueError("operational state schema drifted")
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("unsupported current operational-state schema")
        branch = _branch_from_dict(payload["branch"])
        canonical_base = _version_from_dict(
            payload["canonical_base"],
            "canonical_base",
        )
        if branch.run.base != canonical_base:
            raise ValueError(
                "operational state canonical base disagrees with branch"
            )
        state = cls(
            branch=branch,
            compiler_version=payload["compiler_version"],
            phase=payload["phase"],
            facts=tuple(
                StateFact.from_dict(item)
                for item in _list(payload["facts"], "facts")
            ),
            bindings=tuple(
                ParameterBinding.from_dict(item)
                for item in _list(payload["bindings"], "bindings")
            ),
            locks=tuple(
                StateLock.from_dict(item)
                for item in _list(payload["locks"], "locks")
            ),
            commitments=tuple(
                Commitment.from_dict(item)
                for item in _list(
                    payload["commitments"],
                    "commitments",
                )
            ),
            obligations=tuple(
                DesignObligation.from_dict(item)
                for item in _list(
                    payload["obligations"],
                    "obligations",
                )
            ),
            dependencies=tuple(
                DependencyEdge.from_dict(item)
                for item in _list(
                    payload["dependencies"],
                    "dependencies",
                )
            ),
            invalidated_refs=_string_tuple(
                payload["invalidated_refs"],
                "invalidated_refs",
            ),
            evidence_refs=_string_tuple(
                payload["evidence_refs"],
                "evidence_refs",
            ),
        )
        if (
            payload["state_digest"] != state.state_digest
            or payload["sufficient_digest"]
            != state.sufficient_digest
        ):
            raise ValueError("operational state digest mismatch")
        return state


@dataclass(frozen=True, slots=True)
class LegacyOperationalMarkovStateV2:
    """Read-only compatibility record; it has no current-state authority."""

    canonical_json: str

    SCHEMA = "OperationalMarkovState@2"

    def __post_init__(self) -> None:
        _text(self.canonical_json, "legacy state canonical_json")
        payload = _mapping(
            json.loads(self.canonical_json),
            "legacy operational state",
        )
        if payload.get("schema") != self.SCHEMA:
            raise ValueError(
                "legacy state record has unsupported schema"
            )

    @property
    def state_digest(self) -> str:
        payload = self.to_dict()
        digest = payload.get("state_digest")
        if not isinstance(digest, str):
            raise ValueError("legacy state lacks state_digest")
        return digest

    def to_dict(self) -> dict[str, object]:
        return dict(json.loads(self.canonical_json))

    def migrate(self) -> OperationalMarkovState:
        raise OperationalStateMigrationRequired(
            "OperationalMarkovState@2 lacks first-class epistemic, "
            "conditional-obligation, and dependency-effect semantics; "
            "recompile from authoritative project evidence"
        )


def load_operational_state_record(
    value: object,
) -> OperationalMarkovState | LegacyOperationalMarkovStateV2:
    payload = _mapping(value, "operational state record")
    schema = payload.get("schema")
    if schema == OperationalMarkovState.SCHEMA:
        return OperationalMarkovState.from_dict(payload)
    if schema == LegacyOperationalMarkovStateV2.SCHEMA:
        encoded = _canonical_json(dict(payload))
        return LegacyOperationalMarkovStateV2(encoded)
    raise ValueError("unsupported operational-state schema")


def require_current_operational_state(
    value: object,
) -> OperationalMarkovState:
    loaded = (
        load_operational_state_record(value)
        if isinstance(value, Mapping)
        else value
    )
    if isinstance(loaded, LegacyOperationalMarkovStateV2):
        return loaded.migrate()
    if not isinstance(loaded, OperationalMarkovState):
        raise TypeError(
            "value must be an OperationalMarkovState@3 or state record"
        )
    return loaded


def _validate_obligation_readiness(
    state: OperationalMarkovState,
) -> None:
    obligations = {
        f"obligation:{item.obligation_id}": item
        for item in state.obligations
    }
    for item in state.obligations:
        if item.status not in {
            ObligationStatus.OPEN,
            ObligationStatus.BLOCKED,
        }:
            continue
        condition_ready = (
            item.condition is None
            or _canonical_json(
                state.value_for_ref(item.condition.ref)
            )
            == item.condition.expected_value.canonical_json
        )
        blockers_ready = all(
            obligations[ref].status
            in {
                ObligationStatus.SATISFIED,
                ObligationStatus.WAIVED,
            }
            for ref in item.blocked_by
        )
        expected = (
            ObligationStatus.OPEN
            if condition_ready and blockers_ready
            else ObligationStatus.BLOCKED
        )
        if item.status is not expected:
            raise ValueError(
                f"obligation readiness mismatch: {item.obligation_id} "
                f"must be {expected.value}"
            )


def _validate_blocking_acyclic(
    obligations: tuple[DesignObligation, ...],
) -> None:
    adjacency = {
        f"obligation:{item.obligation_id}": set(item.blocked_by)
        for item in obligations
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(ref: str) -> None:
        if ref in visiting:
            raise ValueError("obligation blocking graph contains a cycle")
        if ref in visited:
            return
        visiting.add(ref)
        for blocker_ref in sorted(adjacency[ref]):
            visit(blocker_ref)
        visiting.remove(ref)
        visited.add(ref)

    for ref in sorted(adjacency):
        visit(ref)


def _version_dict(value: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": value.project_id,
        "version": value.version,
        "state_sha256": value.state_sha256,
    }


def _version_from_dict(
    value: object,
    field: str,
) -> ProjectVersionRef:
    payload = _mapping(value, field)
    if set(payload) != {"project_id", "version", "state_sha256"}:
        raise ValueError(f"{field} schema drifted")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


def _branch_dict(value: BranchRef) -> dict[str, object]:
    return {
        "project_id": value.run.project_id,
        "run_id": value.run.run_id,
        "branch_id": value.branch_id,
        "epoch": value.epoch,
        "base": _version_dict(value.run.base),
    }


def _branch_from_dict(value: object) -> BranchRef:
    payload = _mapping(value, "branch")
    expected = {
        "project_id",
        "run_id",
        "branch_id",
        "epoch",
        "base",
    }
    if set(payload) != expected:
        raise ValueError("branch schema drifted")
    base = _version_from_dict(payload["base"], "branch base")
    return BranchRef(
        run=RunRef(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=base,
        ),
        branch_id=payload["branch_id"],
        epoch=payload["epoch"],
    )
