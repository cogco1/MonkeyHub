"""Persistence-neutral contracts for pluggable stage search policies.

The policy sees an exact architectural state, bounded candidate evaluations,
and a finite budget.  It may only propose a search directive.  Commit,
rollback, persistence, and canonical-write authority remain outside this
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from archive.archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_identifiers,
    deterministic_refs,
    exact_mapping,
    finite_number,
    identifier,
    logical_ref,
    text,
)
from archflow.project.refs import BranchRef


MAX_SEARCH_ITEMS = 4_096
RESERVED_OCBA_POLICY_FAMILY = "ocba"


class SearchPolicyError(ValueError):
    """A search-policy contract is stale, malformed, or exceeds authority."""


class DecisionSpaceKind(StrEnum):
    CATEGORICAL = "categorical"
    GRAPH = "graph"
    CONTINUOUS = "continuous"
    MIXED = "mixed"


class ObjectiveDirection(StrEnum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class SearchAction(StrEnum):
    RESEARCH = "research"
    EXPAND = "expand"
    DEEPEN = "deepen"
    RESAMPLE = "resample"
    PRUNE = "prune"
    HOLD = "hold"
    REQUEST_COMMIT = "request_commit"
    REQUEST_REOPEN = "request_reopen"
    STOP = "stop"


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


def _non_negative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SearchPolicyError(f"{field} must be a non-negative integer")
    return value


def _sorted_enum_tuple(
    values: object,
    enum_type: type[StrEnum],
    field: str,
) -> tuple[StrEnum, ...]:
    if (
        not isinstance(values, tuple)
        or not values
        or len(values) > MAX_SEARCH_ITEMS
        or any(not isinstance(item, enum_type) for item in values)
    ):
        raise TypeError(f"{field} must contain {enum_type.__name__} values")
    normalized = tuple(sorted(values, key=lambda item: item.value))
    if len(normalized) != len(set(normalized)):
        raise SearchPolicyError(f"{field} contains duplicates")
    return normalized


@dataclass(frozen=True, slots=True)
class SearchObjectiveEstimate:
    objective_ref: str
    direction: ObjectiveDirection
    mean: float
    variance: float
    sample_count: int
    source_ref: str

    SCHEMA = "SearchObjectiveEstimate@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "objective_ref",
            logical_ref(self.objective_ref, "objective_ref"),
        )
        if not isinstance(self.direction, ObjectiveDirection):
            raise TypeError("direction must be ObjectiveDirection")
        mean = float(finite_number(self.mean, "objective mean"))
        variance = float(finite_number(self.variance, "objective variance"))
        if variance < 0:
            raise SearchPolicyError("objective variance must be non-negative")
        object.__setattr__(self, "mean", 0.0 if mean == 0 else mean)
        object.__setattr__(
            self,
            "variance",
            0.0 if variance == 0 else variance,
        )
        if (
            not isinstance(self.sample_count, int)
            or isinstance(self.sample_count, bool)
            or self.sample_count < 1
        ):
            raise SearchPolicyError("sample_count must be a positive integer")
        object.__setattr__(
            self,
            "source_ref",
            logical_ref(self.source_ref, "objective source_ref"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "objective_ref": self.objective_ref,
            "direction": self.direction.value,
            "mean": self.mean,
            "variance": self.variance,
            "sample_count": self.sample_count,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SearchObjectiveEstimate":
        payload = exact_mapping(
            value,
            {
                "schema",
                "objective_ref",
                "direction",
                "mean",
                "variance",
                "sample_count",
                "source_ref",
            },
            "search objective estimate",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported objective estimate schema")
        return cls(
            objective_ref=payload["objective_ref"],
            direction=ObjectiveDirection(payload["direction"]),
            mean=payload["mean"],
            variance=payload["variance"],
            sample_count=payload["sample_count"],
            source_ref=payload["source_ref"],
        )


@dataclass(frozen=True, slots=True)
class SearchCandidateEvaluation:
    candidate_ref: str
    candidate_digest: str
    evaluation_ref: str
    hard_failure_refs: tuple[str, ...]
    objectives: tuple[SearchObjectiveEstimate, ...]

    SCHEMA = "SearchCandidateEvaluation@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_ref",
            logical_ref(self.candidate_ref, "candidate_ref"),
        )
        object.__setattr__(
            self,
            "candidate_digest",
            require_sha256(self.candidate_digest, "candidate_digest"),
        )
        object.__setattr__(
            self,
            "evaluation_ref",
            logical_ref(self.evaluation_ref, "evaluation_ref"),
        )
        object.__setattr__(
            self,
            "hard_failure_refs",
            deterministic_refs(
                self.hard_failure_refs,
                "hard_failure_refs",
                allow_empty=True,
            ),
        )
        if (
            not isinstance(self.objectives, tuple)
            or not self.objectives
            or len(self.objectives) > MAX_SEARCH_ITEMS
            or any(
                not isinstance(item, SearchObjectiveEstimate)
                for item in self.objectives
            )
        ):
            raise TypeError(
                "objectives must contain SearchObjectiveEstimate values"
            )
        objectives = tuple(
            sorted(self.objectives, key=lambda item: item.objective_ref)
        )
        refs = tuple(item.objective_ref for item in objectives)
        if len(refs) != len(set(refs)):
            raise SearchPolicyError("candidate objectives contain duplicates")
        object.__setattr__(self, "objectives", objectives)

    @property
    def evaluation_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_ref": self.candidate_ref,
            "candidate_digest": self.candidate_digest,
            "evaluation_ref": self.evaluation_ref,
            "hard_failure_refs": list(self.hard_failure_refs),
            "objectives": [item.to_dict() for item in self.objectives],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "evaluation_digest": self.evaluation_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SearchCandidateEvaluation":
        payload = exact_mapping(
            value,
            {
                "schema",
                "candidate_ref",
                "candidate_digest",
                "evaluation_ref",
                "hard_failure_refs",
                "objectives",
                "evaluation_digest",
            },
            "search candidate evaluation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported candidate evaluation schema")
        if not isinstance(payload["hard_failure_refs"], list):
            raise TypeError("hard_failure_refs must be a list")
        if not isinstance(payload["objectives"], list):
            raise TypeError("objectives must be a list")
        result = cls(
            candidate_ref=payload["candidate_ref"],
            candidate_digest=payload["candidate_digest"],
            evaluation_ref=payload["evaluation_ref"],
            hard_failure_refs=tuple(payload["hard_failure_refs"]),
            objectives=tuple(
                SearchObjectiveEstimate.from_dict(item)
                for item in payload["objectives"]
            ),
        )
        if result.to_dict() != dict(payload):
            raise SearchPolicyError("candidate evaluation digest changed")
        return result


@dataclass(frozen=True, slots=True)
class DecisionSpaceDescriptor:
    descriptor_id: str
    stage_id: str
    branch: BranchRef
    state_digest: str
    kind: DecisionSpaceKind
    decision_refs: tuple[str, ...]
    reopenable_decision_refs: tuple[str, ...]
    candidate_refs: tuple[str, ...]
    objective_refs: tuple[str, ...]
    hard_constraint_refs: tuple[str, ...]

    SCHEMA = "DecisionSpaceDescriptor@1"

    def __post_init__(self) -> None:
        identifier(self.descriptor_id, "descriptor_id")
        identifier(self.stage_id, "stage_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "state_digest"),
        )
        if not isinstance(self.kind, DecisionSpaceKind):
            raise TypeError("kind must be DecisionSpaceKind")
        object.__setattr__(
            self,
            "decision_refs",
            deterministic_refs(self.decision_refs, "decision_refs"),
        )
        for field in (
            "reopenable_decision_refs",
            "candidate_refs",
            "objective_refs",
            "hard_constraint_refs",
        ):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    field,
                    allow_empty=True,
                ),
            )
        if not set(self.reopenable_decision_refs).issubset(
            self.decision_refs
        ):
            raise SearchPolicyError(
                "reopenable decisions must belong to the decision space"
            )

    @property
    def descriptor_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "descriptor_id": self.descriptor_id,
            "stage_id": self.stage_id,
            "branch": branch_ref_to_dict(self.branch),
            "state_digest": self.state_digest,
            "kind": self.kind.value,
            "decision_refs": list(self.decision_refs),
            "reopenable_decision_refs": list(
                self.reopenable_decision_refs
            ),
            "candidate_refs": list(self.candidate_refs),
            "objective_refs": list(self.objective_refs),
            "hard_constraint_refs": list(self.hard_constraint_refs),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "descriptor_digest": self.descriptor_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "DecisionSpaceDescriptor":
        payload = exact_mapping(
            value,
            {
                "schema",
                "descriptor_id",
                "stage_id",
                "branch",
                "state_digest",
                "kind",
                "decision_refs",
                "reopenable_decision_refs",
                "candidate_refs",
                "objective_refs",
                "hard_constraint_refs",
                "descriptor_digest",
                *_AUTHORITY_FIELDS,
            },
            "decision space descriptor",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported decision-space schema")
        for field in (
            "decision_refs",
            "reopenable_decision_refs",
            "candidate_refs",
            "objective_refs",
            "hard_constraint_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            descriptor_id=payload["descriptor_id"],
            stage_id=payload["stage_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            state_digest=payload["state_digest"],
            kind=DecisionSpaceKind(payload["kind"]),
            decision_refs=tuple(payload["decision_refs"]),
            reopenable_decision_refs=tuple(
                payload["reopenable_decision_refs"]
            ),
            candidate_refs=tuple(payload["candidate_refs"]),
            objective_refs=tuple(payload["objective_refs"]),
            hard_constraint_refs=tuple(payload["hard_constraint_refs"]),
        )
        if result.to_dict() != dict(payload):
            raise SearchPolicyError("decision-space descriptor digest changed")
        return result


@dataclass(frozen=True, slots=True)
class SearchBudget:
    evaluation_units: int
    compute_millis: int
    model_tokens: int

    SCHEMA = "SearchBudget@1"

    def __post_init__(self) -> None:
        for field in ("evaluation_units", "compute_millis", "model_tokens"):
            object.__setattr__(
                self,
                field,
                _non_negative_int(getattr(self, field), field),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "evaluation_units": self.evaluation_units,
            "compute_millis": self.compute_millis,
            "model_tokens": self.model_tokens,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SearchBudget":
        payload = exact_mapping(
            value,
            {
                "schema",
                "evaluation_units",
                "compute_millis",
                "model_tokens",
            },
            "search budget",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported search budget schema")
        return cls(
            evaluation_units=payload["evaluation_units"],
            compute_millis=payload["compute_millis"],
            model_tokens=payload["model_tokens"],
        )


@dataclass(frozen=True, slots=True)
class SearchPolicyDescriptor:
    policy_id: str
    policy_family: str
    policy_version: str
    implementation_digest: str
    supported_space_kinds: tuple[DecisionSpaceKind, ...]
    supported_actions: tuple[SearchAction, ...]
    supports_multiobjective: bool

    SCHEMA = "SearchPolicyDescriptor@1"

    def __post_init__(self) -> None:
        identifier(self.policy_id, "policy_id")
        identifier(self.policy_family, "policy_family")
        text(self.policy_version, "policy_version", maximum=200)
        object.__setattr__(
            self,
            "implementation_digest",
            require_sha256(
                self.implementation_digest,
                "implementation_digest",
            ),
        )
        object.__setattr__(
            self,
            "supported_space_kinds",
            _sorted_enum_tuple(
                self.supported_space_kinds,
                DecisionSpaceKind,
                "supported_space_kinds",
            ),
        )
        object.__setattr__(
            self,
            "supported_actions",
            _sorted_enum_tuple(
                self.supported_actions,
                SearchAction,
                "supported_actions",
            ),
        )
        if type(self.supports_multiobjective) is not bool:
            raise TypeError("supports_multiobjective must be bool")

    @property
    def descriptor_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "policy_family": self.policy_family,
            "policy_version": self.policy_version,
            "implementation_digest": self.implementation_digest,
            "supported_space_kinds": [
                item.value for item in self.supported_space_kinds
            ],
            "supported_actions": [
                item.value for item in self.supported_actions
            ],
            "supports_multiobjective": self.supports_multiobjective,
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "descriptor_digest": self.descriptor_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SearchPolicyDescriptor":
        payload = exact_mapping(
            value,
            {
                "schema",
                "policy_id",
                "policy_family",
                "policy_version",
                "implementation_digest",
                "supported_space_kinds",
                "supported_actions",
                "supports_multiobjective",
                "descriptor_digest",
                *_AUTHORITY_FIELDS,
            },
            "search policy descriptor",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported policy descriptor schema")
        if not isinstance(payload["supported_space_kinds"], list):
            raise TypeError("supported_space_kinds must be a list")
        if not isinstance(payload["supported_actions"], list):
            raise TypeError("supported_actions must be a list")
        result = cls(
            policy_id=payload["policy_id"],
            policy_family=payload["policy_family"],
            policy_version=payload["policy_version"],
            implementation_digest=payload["implementation_digest"],
            supported_space_kinds=tuple(
                DecisionSpaceKind(item)
                for item in payload["supported_space_kinds"]
            ),
            supported_actions=tuple(
                SearchAction(item) for item in payload["supported_actions"]
            ),
            supports_multiobjective=payload["supports_multiobjective"],
        )
        if result.to_dict() != dict(payload):
            raise SearchPolicyError("policy descriptor digest changed")
        return result


@dataclass(frozen=True, slots=True)
class SearchPolicyRequest:
    request_id: str
    policy: SearchPolicyDescriptor
    branch: BranchRef
    stage_id: str
    state_digest: str
    decision_space: DecisionSpaceDescriptor
    portfolio_ref: str
    portfolio_digest: str
    evaluations: tuple[SearchCandidateEvaluation, ...]
    budget: SearchBudget
    allowed_actions: tuple[SearchAction, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "SearchPolicyRequest@1"

    def __post_init__(self) -> None:
        identifier(self.request_id, "request_id")
        if not isinstance(self.policy, SearchPolicyDescriptor):
            raise TypeError("policy must be SearchPolicyDescriptor")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "state_digest"),
        )
        if not isinstance(self.decision_space, DecisionSpaceDescriptor):
            raise TypeError("decision_space must be DecisionSpaceDescriptor")
        if (
            self.decision_space.branch != self.branch
            or self.decision_space.stage_id != self.stage_id
            or self.decision_space.state_digest != self.state_digest
        ):
            raise SearchPolicyError(
                "decision space is stale or belongs to another stage branch"
            )
        if self.decision_space.kind not in self.policy.supported_space_kinds:
            raise SearchPolicyError(
                "policy does not support the requested decision-space kind"
            )
        object.__setattr__(
            self,
            "portfolio_ref",
            logical_ref(self.portfolio_ref, "portfolio_ref"),
        )
        object.__setattr__(
            self,
            "portfolio_digest",
            require_sha256(self.portfolio_digest, "portfolio_digest"),
        )
        if (
            not isinstance(self.evaluations, tuple)
            or len(self.evaluations) > MAX_SEARCH_ITEMS
            or any(
                not isinstance(item, SearchCandidateEvaluation)
                for item in self.evaluations
            )
        ):
            raise TypeError(
                "evaluations must contain SearchCandidateEvaluation values"
            )
        evaluations = tuple(
            sorted(self.evaluations, key=lambda item: item.candidate_ref)
        )
        candidate_refs = tuple(item.candidate_ref for item in evaluations)
        if len(candidate_refs) != len(set(candidate_refs)):
            raise SearchPolicyError("evaluations repeat a candidate")
        if not set(candidate_refs).issubset(
            self.decision_space.candidate_refs
        ):
            raise SearchPolicyError(
                "evaluation names a candidate outside the decision space"
            )
        expected_objectives = self.decision_space.objective_refs
        if any(
            tuple(item.objective_ref for item in evaluation.objectives)
            != expected_objectives
            for evaluation in evaluations
        ):
            raise SearchPolicyError(
                "candidate evaluations do not exactly cover stage objectives"
            )
        for objective_index, objective_ref in enumerate(expected_objectives):
            directions = {
                evaluation.objectives[objective_index].direction
                for evaluation in evaluations
            }
            if len(directions) > 1:
                raise SearchPolicyError(
                    f"objective direction changed across candidates: "
                    f"{objective_ref}"
                )
        if (
            len(expected_objectives) > 1
            and not self.policy.supports_multiobjective
        ):
            raise SearchPolicyError(
                "policy does not support the requested objective count"
            )
        object.__setattr__(self, "evaluations", evaluations)
        if not isinstance(self.budget, SearchBudget):
            raise TypeError("budget must be SearchBudget")
        object.__setattr__(
            self,
            "allowed_actions",
            _sorted_enum_tuple(
                self.allowed_actions,
                SearchAction,
                "allowed_actions",
            ),
        )
        if not set(self.allowed_actions).issubset(
            self.policy.supported_actions
        ):
            raise SearchPolicyError(
                "request allows an action unsupported by the exact policy"
            )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "evidence_refs"),
        )

    @property
    def request_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "request_id": self.request_id,
            "policy": self.policy.to_dict(),
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "state_digest": self.state_digest,
            "decision_space": self.decision_space.to_dict(),
            "portfolio_ref": self.portfolio_ref,
            "portfolio_digest": self.portfolio_digest,
            "evaluations": [item.to_dict() for item in self.evaluations],
            "budget": self.budget.to_dict(),
            "allowed_actions": [item.value for item in self.allowed_actions],
            "evidence_refs": list(self.evidence_refs),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "request_digest": self.request_digest}

    @classmethod
    def from_dict(cls, value: object) -> "SearchPolicyRequest":
        payload = exact_mapping(
            value,
            {
                "schema",
                "request_id",
                "policy",
                "branch",
                "stage_id",
                "state_digest",
                "decision_space",
                "portfolio_ref",
                "portfolio_digest",
                "evaluations",
                "budget",
                "allowed_actions",
                "evidence_refs",
                "request_digest",
                *_AUTHORITY_FIELDS,
            },
            "search policy request",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported search request schema")
        for field in ("evaluations", "allowed_actions", "evidence_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            request_id=payload["request_id"],
            policy=SearchPolicyDescriptor.from_dict(payload["policy"]),
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            state_digest=payload["state_digest"],
            decision_space=DecisionSpaceDescriptor.from_dict(
                payload["decision_space"]
            ),
            portfolio_ref=payload["portfolio_ref"],
            portfolio_digest=payload["portfolio_digest"],
            evaluations=tuple(
                SearchCandidateEvaluation.from_dict(item)
                for item in payload["evaluations"]
            ),
            budget=SearchBudget.from_dict(payload["budget"]),
            allowed_actions=tuple(
                SearchAction(item) for item in payload["allowed_actions"]
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
        )
        if result.to_dict() != dict(payload):
            raise SearchPolicyError("search policy request digest changed")
        return result


@dataclass(frozen=True, slots=True)
class SearchBudgetAllocation:
    target_ref: str
    evaluation_units: int
    compute_millis: int
    model_tokens: int

    SCHEMA = "SearchBudgetAllocation@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_ref",
            logical_ref(self.target_ref, "allocation target_ref"),
        )
        values = []
        for field in ("evaluation_units", "compute_millis", "model_tokens"):
            value = _non_negative_int(getattr(self, field), field)
            object.__setattr__(self, field, value)
            values.append(value)
        if not any(values):
            raise SearchPolicyError("budget allocation must allocate work")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "target_ref": self.target_ref,
            "evaluation_units": self.evaluation_units,
            "compute_millis": self.compute_millis,
            "model_tokens": self.model_tokens,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SearchBudgetAllocation":
        payload = exact_mapping(
            value,
            {
                "schema",
                "target_ref",
                "evaluation_units",
                "compute_millis",
                "model_tokens",
            },
            "search budget allocation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported allocation schema")
        return cls(
            target_ref=payload["target_ref"],
            evaluation_units=payload["evaluation_units"],
            compute_millis=payload["compute_millis"],
            model_tokens=payload["model_tokens"],
        )


@dataclass(frozen=True, slots=True)
class SearchDirective:
    directive_id: str
    request_digest: str
    policy_descriptor_digest: str
    branch: BranchRef
    stage_id: str
    state_digest: str
    action: SearchAction
    target_refs: tuple[str, ...]
    allocations: tuple[SearchBudgetAllocation, ...]
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]

    SCHEMA = "SearchDirective@1"

    def __post_init__(self) -> None:
        identifier(self.directive_id, "directive_id")
        object.__setattr__(
            self,
            "request_digest",
            require_sha256(self.request_digest, "request_digest"),
        )
        object.__setattr__(
            self,
            "policy_descriptor_digest",
            require_sha256(
                self.policy_descriptor_digest,
                "policy_descriptor_digest",
            ),
        )
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "state_digest"),
        )
        if not isinstance(self.action, SearchAction):
            raise TypeError("action must be SearchAction")
        object.__setattr__(
            self,
            "target_refs",
            deterministic_refs(
                self.target_refs,
                "target_refs",
                allow_empty=True,
            ),
        )
        if (
            not isinstance(self.allocations, tuple)
            or len(self.allocations) > MAX_SEARCH_ITEMS
            or any(
                not isinstance(item, SearchBudgetAllocation)
                for item in self.allocations
            )
        ):
            raise TypeError(
                "allocations must contain SearchBudgetAllocation values"
            )
        allocations = tuple(
            sorted(self.allocations, key=lambda item: item.target_ref)
        )
        allocation_refs = tuple(item.target_ref for item in allocations)
        if len(allocation_refs) != len(set(allocation_refs)):
            raise SearchPolicyError("allocations repeat a target")
        object.__setattr__(self, "allocations", allocations)
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "evidence_refs"),
        )
        object.__setattr__(
            self,
            "reason_codes",
            deterministic_identifiers(self.reason_codes, "reason_codes"),
        )

    @property
    def directive_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "directive_id": self.directive_id,
            "request_digest": self.request_digest,
            "policy_descriptor_digest": self.policy_descriptor_digest,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "state_digest": self.state_digest,
            "action": self.action.value,
            "target_refs": list(self.target_refs),
            "allocations": [item.to_dict() for item in self.allocations],
            "evidence_refs": list(self.evidence_refs),
            "reason_codes": list(self.reason_codes),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "directive_digest": self.directive_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SearchDirective":
        payload = exact_mapping(
            value,
            {
                "schema",
                "directive_id",
                "request_digest",
                "policy_descriptor_digest",
                "branch",
                "stage_id",
                "state_digest",
                "action",
                "target_refs",
                "allocations",
                "evidence_refs",
                "reason_codes",
                "directive_digest",
                *_AUTHORITY_FIELDS,
            },
            "search directive",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SearchPolicyError("unsupported search directive schema")
        for field in (
            "target_refs",
            "allocations",
            "evidence_refs",
            "reason_codes",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            directive_id=payload["directive_id"],
            request_digest=payload["request_digest"],
            policy_descriptor_digest=payload[
                "policy_descriptor_digest"
            ],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            state_digest=payload["state_digest"],
            action=SearchAction(payload["action"]),
            target_refs=tuple(payload["target_refs"]),
            allocations=tuple(
                SearchBudgetAllocation.from_dict(item)
                for item in payload["allocations"]
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
            reason_codes=tuple(payload["reason_codes"]),
        )
        if result.to_dict() != dict(payload):
            raise SearchPolicyError("search directive digest changed")
        return result


@runtime_checkable
class AsyncSearchPolicy(Protocol):
    """Team-supplied policy boundary; implementations own no authority."""

    @property
    def descriptor(self) -> SearchPolicyDescriptor:
        """Return the immutable identity and declared capability envelope."""

    async def decide(self, request: SearchPolicyRequest) -> SearchDirective:
        """Return one bounded directive for the exact supplied request."""


def validate_search_directive(
    request: SearchPolicyRequest,
    directive: SearchDirective,
) -> SearchDirective:
    """Validate an untrusted policy output against one exact request."""

    if not isinstance(request, SearchPolicyRequest):
        raise TypeError("request must be SearchPolicyRequest")
    if not isinstance(directive, SearchDirective):
        raise TypeError("directive must be SearchDirective")
    if (
        directive.request_digest != request.request_digest
        or directive.policy_descriptor_digest
        != request.policy.descriptor_digest
        or directive.branch != request.branch
        or directive.stage_id != request.stage_id
        or directive.state_digest != request.state_digest
    ):
        raise SearchPolicyError("search directive is stale or cross-policy")
    if directive.action not in request.allowed_actions:
        raise SearchPolicyError("search directive action is not allowed")

    eligible = {
        *request.decision_space.decision_refs,
        *request.decision_space.candidate_refs,
    }
    if not set(directive.target_refs).issubset(eligible):
        raise SearchPolicyError("search directive names an unknown target")
    available_evidence = {
        *request.evidence_refs,
        *(item.evaluation_ref for item in request.evaluations),
    }
    if not set(directive.evidence_refs).issubset(available_evidence):
        raise SearchPolicyError("search directive cites unavailable evidence")

    allocation_actions = {
        SearchAction.RESEARCH,
        SearchAction.EXPAND,
        SearchAction.DEEPEN,
        SearchAction.RESAMPLE,
    }
    no_target_actions = {SearchAction.HOLD, SearchAction.STOP}
    candidate_actions = {
        SearchAction.RESAMPLE,
        SearchAction.PRUNE,
        SearchAction.REQUEST_COMMIT,
    }
    if directive.action in no_target_actions:
        if directive.target_refs or directive.allocations:
            raise SearchPolicyError(
                "hold/stop directive cannot target or allocate work"
            )
    else:
        if not directive.target_refs:
            raise SearchPolicyError("search directive requires targets")
        if directive.action in allocation_actions:
            allocation_refs = tuple(
                item.target_ref for item in directive.allocations
            )
            if allocation_refs != directive.target_refs:
                raise SearchPolicyError(
                    "work directive must allocate every exact target once"
                )
        elif directive.allocations:
            raise SearchPolicyError(
                "prune/commit/reopen request cannot allocate execution budget"
            )

    if directive.action in candidate_actions and not set(
        directive.target_refs
    ).issubset(request.decision_space.candidate_refs):
        raise SearchPolicyError(
            "candidate action targets a non-candidate decision"
        )
    if (
        directive.action is SearchAction.REQUEST_REOPEN
        and not set(directive.target_refs).issubset(
            request.decision_space.reopenable_decision_refs
        )
    ):
        raise SearchPolicyError(
            "reopen request targets a decision outside the reopen envelope"
        )
    if directive.action is SearchAction.REQUEST_COMMIT:
        evaluations = {
            item.candidate_ref: item for item in request.evaluations
        }
        if any(
            target_ref not in evaluations
            or evaluations[target_ref].hard_failure_refs
            for target_ref in directive.target_refs
        ):
            raise SearchPolicyError(
                "commit request requires evaluated hard-feasible candidates"
            )

    totals = SearchBudget(
        evaluation_units=sum(
            item.evaluation_units for item in directive.allocations
        ),
        compute_millis=sum(
            item.compute_millis for item in directive.allocations
        ),
        model_tokens=sum(item.model_tokens for item in directive.allocations),
    )
    if (
        totals.evaluation_units > request.budget.evaluation_units
        or totals.compute_millis > request.budget.compute_millis
        or totals.model_tokens > request.budget.model_tokens
    ):
        raise SearchPolicyError("search directive exceeds the exact budget")
    return directive


__all__ = [
    "AsyncSearchPolicy",
    "DecisionSpaceDescriptor",
    "DecisionSpaceKind",
    "MAX_SEARCH_ITEMS",
    "ObjectiveDirection",
    "RESERVED_OCBA_POLICY_FAMILY",
    "SearchAction",
    "SearchBudget",
    "SearchBudgetAllocation",
    "SearchCandidateEvaluation",
    "SearchDirective",
    "SearchObjectiveEstimate",
    "SearchPolicyDescriptor",
    "SearchPolicyError",
    "SearchPolicyRequest",
    "validate_search_directive",
]
