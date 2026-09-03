"""Deterministic projections and bounded traversal over architectural relations.

The canonical relation graph stores role-bearing relation instances.  A
``RelationView`` is a read-only, question-specific projection: support flow,
host lookup, access, lineage, realization, composition, provenance, or change
impact.  The projection owns no design, stage-acceptance, persistence, or
canonical-write authority.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from heapq import heapify, heappop, heappush
from typing import Iterable

from archive.archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
    text,
)
from archflow.relations.contracts import (
    ArchitecturalRelation,
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
    ImpactEffect,
    RelationEpistemicStatus,
    RelationProjection,
)
from archflow.project.refs import BranchRef
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus


MAX_TRAVERSAL_ITEMS = 4_096
UNIVERSAL_SCENARIO_REF = "scenario:universal"
_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


class RelationTraversalError(ValueError):
    """A relation projection or traversal request is malformed or stale."""


class RelationCheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class TraversalStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class TraversalStopReason(StrEnum):
    TARGETS_REACHED = "targets_reached"
    TARGETS_UNPROVEN = "targets_unproven"
    TARGETS_DISCONNECTED = "targets_disconnected"
    IMPACT_CLOSURE_COMPLETE = "impact_closure_complete"


@dataclass(frozen=True, slots=True)
class RelationCheckerRequirement:
    """Exact checker family and check identity required for one relation."""

    relation_ref: str
    check_id: str
    checker_id: str
    checker_version: str

    SCHEMA = "RelationCheckerRequirement@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relation_ref",
            logical_ref(self.relation_ref, "checker requirement relation_ref"),
        )
        identifier(self.check_id, "checker requirement check_id")
        identifier(self.checker_id, "checker requirement checker_id")
        text(
            self.checker_version,
            "checker requirement checker_version",
            maximum=100,
        )

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.relation_ref,
            self.check_id,
            self.checker_id,
            self.checker_version,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_ref": self.relation_ref,
            "check_id": self.check_id,
            "checker_id": self.checker_id,
            "checker_version": self.checker_version,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationCheckerRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_ref",
                "check_id",
                "checker_id",
                "checker_version",
                *_AUTHORITY_FIELDS,
            },
            "relation checker requirement",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError(
                "unsupported relation checker requirement schema"
            )
        result = cls(
            relation_ref=payload["relation_ref"],
            check_id=payload["check_id"],
            checker_id=payload["checker_id"],
            checker_version=payload["checker_version"],
        )
        if result.to_dict() != dict(payload):
            raise RelationTraversalError(
                "relation checker requirement roundtrip changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationTraversalPolicy:
    """Evidence-bound checker eligibility for one relation projection."""

    policy_id: str
    projection: RelationProjection
    start_refs: tuple[str, ...]
    target_refs: tuple[str, ...]
    minimum_hops: int
    allowed_checker_ids: tuple[str, ...]
    check_requirements: tuple[RelationCheckerRequirement, ...]
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA = "RelationTraversalPolicy@1"

    def __post_init__(self) -> None:
        identifier(self.policy_id, "relation traversal policy_id")
        if not isinstance(self.projection, RelationProjection):
            raise TypeError("projection must be RelationProjection")
        if (
            not isinstance(self.start_refs, tuple)
            or not self.start_refs
            or len(self.start_refs) > MAX_TRAVERSAL_ITEMS
        ):
            raise RelationTraversalError(
                "relation traversal policy requires bounded start_refs"
            )
        object.__setattr__(
            self,
            "start_refs",
            deterministic_refs(
                self.start_refs,
                "relation traversal policy start_refs",
            ),
        )
        if (
            not isinstance(self.target_refs, tuple)
            or len(self.target_refs) > MAX_TRAVERSAL_ITEMS
        ):
            raise RelationTraversalError(
                "relation traversal policy target_refs must be bounded"
            )
        object.__setattr__(
            self,
            "target_refs",
            deterministic_refs(
                self.target_refs,
                "relation traversal policy target_refs",
                allow_empty=True,
            ),
        )
        if (
            not isinstance(self.minimum_hops, int)
            or isinstance(self.minimum_hops, bool)
            or self.minimum_hops not in {0, 1}
        ):
            raise RelationTraversalError(
                "minimum_hops must explicitly allow zero-hop or require an edge"
            )
        if not isinstance(self.allowed_checker_ids, tuple):
            raise TypeError("allowed_checker_ids must be a tuple")
        checker_ids = tuple(
            sorted(
                identifier(item, "allowed_checker_id")
                for item in self.allowed_checker_ids
            )
        )
        if len(checker_ids) != len(set(checker_ids)):
            raise RelationTraversalError(
                "allowed_checker_ids contain duplicates"
            )
        if (
            not isinstance(self.check_requirements, tuple)
            or len(self.check_requirements) > MAX_TRAVERSAL_ITEMS
            or any(
                not isinstance(item, RelationCheckerRequirement)
                for item in self.check_requirements
            )
        ):
            raise TypeError(
                "check_requirements must contain RelationCheckerRequirement values"
            )
        requirements = tuple(
            sorted(self.check_requirements, key=lambda item: item.identity)
        )
        if len({item.relation_ref for item in requirements}) != len(
            requirements
        ):
            raise RelationTraversalError(
                "check_requirements repeat a relation_ref"
            )
        if any(
            item.checker_id not in checker_ids
            for item in requirements
        ):
            raise RelationTraversalError(
                "check requirement names a checker outside policy"
            )
        object.__setattr__(self, "check_requirements", requirements)
        if self.projection is RelationProjection.IMPACT:
            if (
                checker_ids
                or requirements
                or self.target_refs
                or self.minimum_hops != 0
            ):
                raise RelationTraversalError(
                    "impact policy cannot consume checks, targets, or hops"
                )
        else:
            if not checker_ids:
                raise RelationTraversalError(
                    "reachability policy requires allowed_checker_ids"
                )
            if not self.target_refs:
                raise RelationTraversalError(
                    "reachability policy requires target_refs"
                )
        object.__setattr__(self, "allowed_checker_ids", checker_ids)
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(
                self.evidence_refs,
                "relation traversal policy evidence_refs",
            ),
        )
        object.__setattr__(
            self,
            "authority_refs",
            deterministic_refs(
                self.authority_refs,
                "relation traversal policy authority_refs",
            ),
        )

    @property
    def policy_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"relation-traversal-policy:{self.policy_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "projection": self.projection.value,
            "start_refs": list(self.start_refs),
            "target_refs": list(self.target_refs),
            "minimum_hops": self.minimum_hops,
            "allowed_checker_ids": list(self.allowed_checker_ids),
            "check_requirements": [
                item.to_dict() for item in self.check_requirements
            ],
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationTraversalPolicy":
        payload = exact_mapping(
            value,
            {
                "schema",
                "policy_id",
                "projection",
                "start_refs",
                "target_refs",
                "minimum_hops",
                "allowed_checker_ids",
                "check_requirements",
                "evidence_refs",
                "authority_refs",
                *_AUTHORITY_FIELDS,
            },
            "relation traversal policy",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError(
                "unsupported relation traversal policy schema"
            )
        for field in (
            "start_refs",
            "target_refs",
            "allowed_checker_ids",
            "check_requirements",
            "evidence_refs",
            "authority_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            policy_id=payload["policy_id"],
            projection=RelationProjection(payload["projection"]),
            start_refs=tuple(payload["start_refs"]),
            target_refs=tuple(payload["target_refs"]),
            minimum_hops=payload["minimum_hops"],
            allowed_checker_ids=tuple(payload["allowed_checker_ids"]),
            check_requirements=tuple(
                RelationCheckerRequirement.from_dict(item)
                for item in payload["check_requirements"]
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )
        if result.to_dict() != dict(payload):
            raise RelationTraversalError(
                "relation traversal policy roundtrip changed"
            )
        return result


_IMPACT_RANK = {
    ImpactEffect.REVALIDATE: 1,
    ImpactEffect.INVALIDATE: 2,
}
_OPEN_EPISTEMIC_STATUSES = frozenset(
    {
        RelationEpistemicStatus.HYPOTHESIS,
        RelationEpistemicStatus.DISPUTED,
        RelationEpistemicStatus.UNKNOWN,
    }
)
_CYCLE_REJECTING_PROJECTIONS = frozenset(
    {
        RelationProjection.COMPOSITION,
        RelationProjection.SUPPORT,
        RelationProjection.HOST,
        RelationProjection.REALIZATION,
        RelationProjection.LINEAGE,
        RelationProjection.PROVENANCE,
    }
)


def projection_rejects_cycles(projection: RelationProjection) -> bool:
    if not isinstance(projection, RelationProjection):
        raise TypeError("projection must be RelationProjection")
    return projection in _CYCLE_REJECTING_PROJECTIONS


def _bounded_refs(values: tuple[str, ...], field: str, *, empty: bool = False) -> tuple[str, ...]:
    if len(values) > MAX_TRAVERSAL_ITEMS:
        raise RelationTraversalError(f"{field} exceeds bounded item count")
    return deterministic_refs(values, field, allow_empty=empty)


@dataclass(frozen=True, slots=True)
class RelationArc:
    relation_ref: str
    source_ref: str
    target_ref: str
    projection: RelationProjection
    epistemic_status: RelationEpistemicStatus
    impact_effect: ImpactEffect | None = None

    SCHEMA = "RelationArc@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "relation_ref", logical_ref(self.relation_ref, "relation_ref"))
        object.__setattr__(self, "source_ref", logical_ref(self.source_ref, "source_ref"))
        object.__setattr__(self, "target_ref", logical_ref(self.target_ref, "target_ref"))
        if self.source_ref == self.target_ref:
            raise RelationTraversalError("relation arc cannot be a self edge")
        if not isinstance(self.projection, RelationProjection):
            raise TypeError("projection must be RelationProjection")
        if not isinstance(self.epistemic_status, RelationEpistemicStatus):
            raise TypeError("epistemic_status must be RelationEpistemicStatus")
        if self.projection is RelationProjection.IMPACT:
            if not isinstance(self.impact_effect, ImpactEffect):
                raise RelationTraversalError("impact arc requires an ImpactEffect")
        elif self.impact_effect is not None:
            raise RelationTraversalError("impact_effect only applies to impact arcs")

    @property
    def identity(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.projection.value,
            self.source_ref,
            self.target_ref,
            self.relation_ref,
            self.epistemic_status.value,
            "" if self.impact_effect is None else self.impact_effect.value,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_ref": self.relation_ref,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "projection": self.projection.value,
            "epistemic_status": self.epistemic_status.value,
            "impact_effect": None if self.impact_effect is None else self.impact_effect.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationArc":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_ref",
                "source_ref",
                "target_ref",
                "projection",
                "epistemic_status",
                "impact_effect",
            },
            "relation arc",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError("unsupported relation arc schema")
        impact = payload["impact_effect"]
        if impact is not None and not isinstance(impact, str):
            raise TypeError("impact_effect must be text or None")
        return cls(
            relation_ref=payload["relation_ref"],
            source_ref=payload["source_ref"],
            target_ref=payload["target_ref"],
            projection=RelationProjection(payload["projection"]),
            epistemic_status=RelationEpistemicStatus(
                payload["epistemic_status"]
            ),
            impact_effect=None if impact is None else ImpactEffect(impact),
        )


@dataclass(frozen=True, slots=True)
class RelationView:
    graph_digest: str
    branch: BranchRef
    scope_digest: str
    stage_subject_digest: str
    projection: RelationProjection
    scenario_ref: str
    node_refs: tuple[str, ...]
    arcs: tuple[RelationArc, ...]

    SCHEMA = "RelationView@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "graph_digest",
            require_sha256(self.graph_digest, "graph_digest"),
        )
        require_exact_branch(self.branch, "relation view branch")
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        object.__setattr__(
            self,
            "stage_subject_digest",
            require_sha256(
                self.stage_subject_digest,
                "stage_subject_digest",
            ),
        )
        if not isinstance(self.projection, RelationProjection):
            raise TypeError("projection must be RelationProjection")
        object.__setattr__(self, "scenario_ref", logical_ref(self.scenario_ref, "scenario_ref"))
        object.__setattr__(self, "node_refs", _bounded_refs(self.node_refs, "node_refs"))
        if (
            not isinstance(self.arcs, tuple)
            or len(self.arcs) > MAX_TRAVERSAL_ITEMS
            or any(not isinstance(item, RelationArc) for item in self.arcs)
        ):
            raise TypeError("arcs must contain bounded RelationArc values")
        arcs = tuple(sorted(self.arcs, key=lambda item: item.identity))
        identities = tuple(item.identity for item in arcs)
        if len(identities) != len(set(identities)):
            raise RelationTraversalError("relation view repeats an arc")
        if any(item.projection is not self.projection for item in arcs):
            raise RelationTraversalError("relation arc belongs to another projection")
        known = set(self.node_refs)
        if any(item.source_ref not in known or item.target_ref not in known for item in arcs):
            raise RelationTraversalError("relation arc endpoint is absent from the graph")
        object.__setattr__(self, "arcs", arcs)

    @property
    def relation_refs(self) -> tuple[str, ...]:
        return tuple(sorted({item.relation_ref for item in self.arcs}))

    @property
    def view_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "graph_digest": self.graph_digest,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_subject_digest": self.stage_subject_digest,
            "projection": self.projection.value,
            "scenario_ref": self.scenario_ref,
            "node_refs": list(self.node_refs),
            "arcs": [item.to_dict() for item in self.arcs],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "view_digest": self.view_digest}

    @classmethod
    def from_dict(cls, value: object) -> "RelationView":
        payload = exact_mapping(
            value,
            {
                "schema", "graph_digest", "branch", "scope_digest",
                "stage_subject_digest", "projection", "scenario_ref", "node_refs", "arcs",
                "view_digest", *_AUTHORITY_FIELDS,
            },
            "relation view",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError("unsupported relation view schema")
        if not isinstance(payload["node_refs"], list) or not isinstance(payload["arcs"], list):
            raise TypeError("relation view tuple fields must be lists")
        result = cls(
            graph_digest=payload["graph_digest"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_subject_digest=payload["stage_subject_digest"],
            projection=RelationProjection(payload["projection"]),
            scenario_ref=payload["scenario_ref"],
            node_refs=tuple(payload["node_refs"]),
            arcs=tuple(RelationArc.from_dict(item) for item in payload["arcs"]),
        )
        if result.to_dict() != dict(payload):
            raise RelationTraversalError("relation view digest changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationStatusBinding:
    relation_ref: str
    status: RelationCheckStatus
    check_receipt_id: str
    check_receipt_digest: str
    checker_id: str

    SCHEMA = "RelationStatusBinding@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "relation_ref", logical_ref(self.relation_ref, "relation_ref"))
        if not isinstance(self.status, RelationCheckStatus):
            raise TypeError("status must be RelationCheckStatus")
        identifier(self.check_receipt_id, "check_receipt_id")
        object.__setattr__(
            self,
            "check_receipt_digest",
            require_sha256(
                self.check_receipt_digest,
                "check_receipt_digest",
            ),
        )
        identifier(self.checker_id, "checker_id")

    @classmethod
    def bind(
        cls,
        relation_ref: str,
        receipt: CheckReceiptEnvelope,
    ) -> "RelationStatusBinding":
        if not isinstance(receipt, CheckReceiptEnvelope):
            raise TypeError("receipt must be CheckReceiptEnvelope")
        normalized_ref = logical_ref(relation_ref, "relation_ref")
        if (
            normalized_ref not in receipt.subject_refs
            or normalized_ref not in receipt.coverage_denominator
        ):
            raise RelationTraversalError(
                "relation check receipt omitted the bound relation"
            )
        status = {
            CheckStatus.PASS: RelationCheckStatus.PASS,
            CheckStatus.FAIL: RelationCheckStatus.FAIL,
            CheckStatus.UNKNOWN: RelationCheckStatus.UNKNOWN,
            CheckStatus.NOT_APPLICABLE: RelationCheckStatus.UNKNOWN,
        }[receipt.status]
        if (
            status is RelationCheckStatus.PASS
            and receipt.revalidation_refs
        ):
            status = RelationCheckStatus.UNKNOWN
        if status is RelationCheckStatus.PASS and (
            normalized_ref not in receipt.covered_refs
        ):
            raise RelationTraversalError(
                "passing relation check did not cover the bound relation"
            )
        return cls(
            relation_ref=normalized_ref,
            status=status,
            check_receipt_id=receipt.receipt_id,
            check_receipt_digest=receipt.receipt_digest,
            checker_id=receipt.checker_id,
        )

    def require_receipt(self, receipt: CheckReceiptEnvelope) -> None:
        expected = RelationStatusBinding.bind(self.relation_ref, receipt)
        if self != expected:
            raise RelationTraversalError(
                "relation status binding changed its check receipt"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_ref": self.relation_ref,
            "status": self.status.value,
            "check_receipt_id": self.check_receipt_id,
            "check_receipt_digest": self.check_receipt_digest,
            "checker_id": self.checker_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationStatusBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_ref",
                "status",
                "check_receipt_id",
                "check_receipt_digest",
                "checker_id",
            },
            "relation status",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError("unsupported relation status schema")
        return cls(
            relation_ref=payload["relation_ref"],
            status=RelationCheckStatus(payload["status"]),
            check_receipt_id=payload["check_receipt_id"],
            check_receipt_digest=payload["check_receipt_digest"],
            checker_id=payload["checker_id"],
        )


@dataclass(frozen=True, slots=True)
class TraversalWitness:
    start_ref: str
    target_ref: str
    node_refs: tuple[str, ...]
    relation_refs: tuple[str, ...]

    SCHEMA = "TraversalWitness@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_ref", logical_ref(self.start_ref, "start_ref"))
        object.__setattr__(self, "target_ref", logical_ref(self.target_ref, "target_ref"))
        if (
            not isinstance(self.node_refs, tuple)
            or not self.node_refs
            or len(self.node_refs) > MAX_TRAVERSAL_ITEMS
        ):
            raise RelationTraversalError("witness node_refs must be non-empty and bounded")
        for ref in self.node_refs:
            logical_ref(ref, "witness node_ref")
        if self.node_refs[0] != self.start_ref or self.node_refs[-1] != self.target_ref:
            raise RelationTraversalError("witness endpoints disagree with its node path")
        if len(self.relation_refs) != len(self.node_refs) - 1:
            raise RelationTraversalError("witness relation path length is invalid")
        for ref in self.relation_refs:
            logical_ref(ref, "witness relation_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "start_ref": self.start_ref,
            "target_ref": self.target_ref,
            "node_refs": list(self.node_refs),
            "relation_refs": list(self.relation_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "TraversalWitness":
        payload = exact_mapping(
            value,
            {"schema", "start_ref", "target_ref", "node_refs", "relation_refs"},
            "traversal witness",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError("unsupported traversal witness schema")
        if not isinstance(payload["node_refs"], list) or not isinstance(payload["relation_refs"], list):
            raise TypeError("witness paths must be lists")
        return cls(
            start_ref=payload["start_ref"],
            target_ref=payload["target_ref"],
            node_refs=tuple(payload["node_refs"]),
            relation_refs=tuple(payload["relation_refs"]),
        )


@dataclass(frozen=True, slots=True)
class ImpactAssignment:
    node_ref: str
    effect: ImpactEffect

    SCHEMA = "ImpactAssignment@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_ref", logical_ref(self.node_ref, "impact node_ref"))
        if not isinstance(self.effect, ImpactEffect):
            raise TypeError("effect must be ImpactEffect")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "node_ref": self.node_ref, "effect": self.effect.value}

    @classmethod
    def from_dict(cls, value: object) -> "ImpactAssignment":
        payload = exact_mapping(value, {"schema", "node_ref", "effect"}, "impact assignment")
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError("unsupported impact assignment schema")
        return cls(node_ref=payload["node_ref"], effect=ImpactEffect(payload["effect"]))


@dataclass(frozen=True, slots=True)
class GraphTraversalReceipt:
    traversal_id: str
    graph_digest: str
    view_digest: str
    policy_ref: str
    policy_digest: str
    projection: RelationProjection
    scenario_ref: str
    start_refs: tuple[str, ...]
    target_refs: tuple[str, ...]
    status: TraversalStatus
    stop_reason: TraversalStopReason
    reached_refs: tuple[str, ...]
    traversed_relation_refs: tuple[str, ...]
    unresolved_relation_refs: tuple[str, ...]
    blocker_relation_refs: tuple[str, ...]
    boundary_refs: tuple[str, ...]
    cycle_node_groups: tuple[tuple[str, ...], ...]
    witnesses: tuple[TraversalWitness, ...]
    status_bindings: tuple[RelationStatusBinding, ...]
    reject_cycles: bool
    impacts: tuple[ImpactAssignment, ...] = ()

    SCHEMA = "GraphTraversalReceipt@1"

    def __post_init__(self) -> None:
        identifier(self.traversal_id, "traversal_id")
        for field in ("graph_digest", "view_digest", "policy_digest"):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        object.__setattr__(
            self,
            "policy_ref",
            logical_ref(self.policy_ref, "policy_ref"),
        )
        if self.policy_ref != f"relation-traversal-policy:{self.policy_digest}":
            raise RelationTraversalError(
                "traversal policy ref does not bind its policy digest"
            )
        if not isinstance(self.projection, RelationProjection):
            raise TypeError("projection must be RelationProjection")
        object.__setattr__(self, "scenario_ref", logical_ref(self.scenario_ref, "scenario_ref"))
        for field in (
            "start_refs", "target_refs", "reached_refs", "traversed_relation_refs",
            "unresolved_relation_refs", "blocker_relation_refs", "boundary_refs",
        ):
            object.__setattr__(self, field, _bounded_refs(getattr(self, field), field, empty=field not in {"start_refs"}))
        if not isinstance(self.status, TraversalStatus):
            raise TypeError("status must be TraversalStatus")
        if not isinstance(self.stop_reason, TraversalStopReason):
            raise TypeError("stop_reason must be TraversalStopReason")
        if (
            not isinstance(self.cycle_node_groups, tuple)
            or len(self.cycle_node_groups) > MAX_TRAVERSAL_ITEMS
        ):
            raise TypeError("cycle_node_groups must be a bounded tuple")
        normalized_cycles = []
        for group in self.cycle_node_groups:
            normalized_cycles.append(_bounded_refs(group, "cycle node group"))
        normalized_cycles.sort()
        object.__setattr__(self, "cycle_node_groups", tuple(normalized_cycles))
        if (
            not isinstance(self.witnesses, tuple)
            or len(self.witnesses) > MAX_TRAVERSAL_ITEMS
            or any(not isinstance(item, TraversalWitness) for item in self.witnesses)
        ):
            raise TypeError("witnesses must contain TraversalWitness values")
        object.__setattr__(
            self,
            "witnesses",
            tuple(sorted(self.witnesses, key=lambda item: (item.start_ref, item.target_ref, item.node_refs))),
        )
        if (
            not isinstance(self.status_bindings, tuple)
            or len(self.status_bindings) > MAX_TRAVERSAL_ITEMS
            or any(
                not isinstance(item, RelationStatusBinding)
                for item in self.status_bindings
            )
        ):
            raise TypeError(
                "status_bindings must contain RelationStatusBinding values"
            )
        bindings = tuple(
            sorted(self.status_bindings, key=lambda item: item.relation_ref)
        )
        if len({item.relation_ref for item in bindings}) != len(bindings):
            raise RelationTraversalError("status bindings repeat a relation")
        object.__setattr__(self, "status_bindings", bindings)
        if type(self.reject_cycles) is not bool:
            raise TypeError("reject_cycles must be bool")
        if self.reject_cycles is not projection_rejects_cycles(self.projection):
            raise RelationTraversalError(
                "cycle policy was not derived from the relation projection"
            )
        if (
            not isinstance(self.impacts, tuple)
            or len(self.impacts) > MAX_TRAVERSAL_ITEMS
            or any(not isinstance(item, ImpactAssignment) for item in self.impacts)
        ):
            raise TypeError("impacts must contain ImpactAssignment values")
        impacts = tuple(sorted(self.impacts, key=lambda item: item.node_ref))
        if len({item.node_ref for item in impacts}) != len(impacts):
            raise RelationTraversalError("impacts repeat a node")
        object.__setattr__(self, "impacts", impacts)
        if self.projection is RelationProjection.IMPACT and not self.impacts:
            raise RelationTraversalError("impact traversal must retain impact assignments")
        if self.projection is not RelationProjection.IMPACT and self.impacts:
            raise RelationTraversalError("impacts only apply to the impact projection")
        binding_statuses = {
            item.relation_ref: item.status for item in self.status_bindings
        }
        used_relations = {
            *self.traversed_relation_refs,
            *self.unresolved_relation_refs,
            *self.blocker_relation_refs,
            *(
                relation_ref
                for witness in self.witnesses
                for relation_ref in witness.relation_refs
            ),
        }
        if self.projection is RelationProjection.IMPACT:
            if (
                self.status is not TraversalStatus.PASS
                or self.stop_reason
                is not TraversalStopReason.IMPACT_CLOSURE_COMPLETE
                or self.target_refs
                or self.status_bindings
                or self.reject_cycles
                or self.unresolved_relation_refs
                or self.blocker_relation_refs
                or self.boundary_refs
                or {item.node_ref for item in self.impacts}
                != set(self.reached_refs)
                or not set(self.start_refs) <= set(self.reached_refs)
            ):
                raise RelationTraversalError(
                    "impact traversal receipt is internally inconsistent"
                )
        else:
            if not self.target_refs:
                raise RelationTraversalError(
                    "reachability traversal requires target_refs"
                )
            if not used_relations <= set(binding_statuses):
                raise RelationTraversalError(
                    "traversal result omitted relation status evidence"
                )
            witness_starts = {item.start_ref for item in self.witnesses}
            if not witness_starts <= set(self.start_refs) or any(
                item.target_ref not in self.target_refs
                for item in self.witnesses
            ):
                raise RelationTraversalError(
                    "traversal witness crossed declared endpoints"
                )
            if self.status is TraversalStatus.PASS:
                valid = (
                    self.stop_reason is TraversalStopReason.TARGETS_REACHED
                    and witness_starts == set(self.start_refs)
                    and not self.unresolved_relation_refs
                    and not self.blocker_relation_refs
                )
            elif self.status is TraversalStatus.UNKNOWN:
                valid = (
                    self.stop_reason is TraversalStopReason.TARGETS_UNPROVEN
                    and witness_starts == set(self.start_refs)
                    and bool(self.unresolved_relation_refs)
                    and not self.blocker_relation_refs
                )
            else:
                valid = (
                    self.stop_reason
                    is TraversalStopReason.TARGETS_DISCONNECTED
                    and witness_starts != set(self.start_refs)
                )
            if not valid or (
                self.reject_cycles
                and self.cycle_node_groups
                and self.status is not TraversalStatus.FAIL
            ):
                raise RelationTraversalError(
                    "reachability traversal receipt is internally inconsistent"
                )

    def require_view(self, view: RelationView) -> None:
        """Verify that retained traversal evidence binds this exact view."""

        if not isinstance(view, RelationView):
            raise TypeError("view must be RelationView")
        if (
            self.graph_digest != view.graph_digest
            or self.view_digest != view.view_digest
            or self.projection is not view.projection
            or self.scenario_ref != view.scenario_ref
            or not set(self.start_refs) <= set(view.node_refs)
            or not set(self.target_refs) <= set(view.node_refs)
            or not set(self.traversed_relation_refs) <= set(view.relation_refs)
        ):
            raise RelationTraversalError(
                "traversal receipt does not bind the supplied relation view"
            )
        if self.projection is not RelationProjection.IMPACT and (
            tuple(item.relation_ref for item in self.status_bindings)
            != view.relation_refs
        ):
            raise RelationTraversalError(
                "traversal status denominator does not cover the exact view"
            )
        if self.projection is not RelationProjection.IMPACT:
            statuses = _effective_status_map(view, self.status_bindings)
            if any(
                statuses[ref] is not RelationCheckStatus.UNKNOWN
                for ref in self.unresolved_relation_refs
            ):
                raise RelationTraversalError(
                    "unresolved relation is not epistemically UNKNOWN"
                )
            if any(
                statuses[ref] is not RelationCheckStatus.FAIL
                for ref in self.blocker_relation_refs
            ):
                raise RelationTraversalError(
                    "blocker relation lacks a failed check receipt"
                )
        arc_identities = {
            (arc.source_ref, arc.target_ref, arc.relation_ref)
            for arc in view.arcs
        }
        for witness in self.witnesses:
            for source, target, relation_ref in zip(
                witness.node_refs[:-1],
                witness.node_refs[1:],
                witness.relation_refs,
                strict=True,
            ):
                if (source, target, relation_ref) not in arc_identities:
                    raise RelationTraversalError(
                        "traversal witness is absent from the supplied view"
                    )
        if (
            self.projection is not RelationProjection.IMPACT
            and self.status is TraversalStatus.PASS
            and any(
            statuses[relation_ref] is not RelationCheckStatus.PASS
            for witness in self.witnesses
            for relation_ref in witness.relation_refs
            )
        ):
            raise RelationTraversalError(
                "passing traversal used a non-passing relation"
            )

    def require_policy(self, policy: RelationTraversalPolicy) -> None:
        """Verify the exact checker-eligibility policy behind traversal."""

        if not isinstance(policy, RelationTraversalPolicy):
            raise TypeError("policy must be a RelationTraversalPolicy")
        if (
            self.policy_ref != policy.ref
            or self.policy_digest != policy.policy_digest
            or self.projection is not policy.projection
            or self.start_refs != policy.start_refs
            or self.target_refs != policy.target_refs
            or any(
                len(witness.relation_refs) < policy.minimum_hops
                for witness in self.witnesses
            )
        ):
            raise RelationTraversalError(
                "traversal receipt does not bind the supplied traversal policy"
            )

    def require_check_receipts(
        self,
        view: RelationView,
        policy: RelationTraversalPolicy,
        receipts: tuple[CheckReceiptEnvelope, ...],
    ) -> None:
        """Verify the exact typed check evidence behind relation statuses."""

        self.require_view(view)
        self.require_policy(policy)
        _require_binding_receipts(
            view,
            self.status_bindings,
            receipts,
            policy,
        )

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "traversal_id": self.traversal_id,
            "graph_digest": self.graph_digest,
            "view_digest": self.view_digest,
            "policy_ref": self.policy_ref,
            "policy_digest": self.policy_digest,
            "projection": self.projection.value,
            "scenario_ref": self.scenario_ref,
            "start_refs": list(self.start_refs),
            "target_refs": list(self.target_refs),
            "status": self.status.value,
            "stop_reason": self.stop_reason.value,
            "reached_refs": list(self.reached_refs),
            "traversed_relation_refs": list(self.traversed_relation_refs),
            "unresolved_relation_refs": list(self.unresolved_relation_refs),
            "blocker_relation_refs": list(self.blocker_relation_refs),
            "boundary_refs": list(self.boundary_refs),
            "cycle_node_groups": [list(item) for item in self.cycle_node_groups],
            "witnesses": [item.to_dict() for item in self.witnesses],
            "status_bindings": [
                item.to_dict() for item in self.status_bindings
            ],
            "reject_cycles": self.reject_cycles,
            "impacts": [item.to_dict() for item in self.impacts],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "receipt_digest": self.receipt_digest}

    @classmethod
    def from_dict(cls, value: object) -> "GraphTraversalReceipt":
        payload = exact_mapping(
            value,
            {
                "schema", "traversal_id", "graph_digest", "view_digest",
                "policy_ref", "policy_digest", "projection",
                "scenario_ref", "start_refs", "target_refs", "status", "stop_reason",
                "reached_refs", "traversed_relation_refs", "unresolved_relation_refs",
                "blocker_relation_refs", "boundary_refs", "cycle_node_groups", "witnesses",
                "status_bindings", "reject_cycles", "impacts", "receipt_digest",
                *_AUTHORITY_FIELDS,
            },
            "graph traversal receipt",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationTraversalError("unsupported traversal receipt schema")
        for field in (
            "start_refs", "target_refs", "reached_refs", "traversed_relation_refs",
            "unresolved_relation_refs", "blocker_relation_refs", "boundary_refs",
            "cycle_node_groups", "witnesses", "status_bindings", "impacts",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            traversal_id=payload["traversal_id"],
            graph_digest=payload["graph_digest"],
            view_digest=payload["view_digest"],
            policy_ref=payload["policy_ref"],
            policy_digest=payload["policy_digest"],
            projection=RelationProjection(payload["projection"]),
            scenario_ref=payload["scenario_ref"],
            start_refs=tuple(payload["start_refs"]),
            target_refs=tuple(payload["target_refs"]),
            status=TraversalStatus(payload["status"]),
            stop_reason=TraversalStopReason(payload["stop_reason"]),
            reached_refs=tuple(payload["reached_refs"]),
            traversed_relation_refs=tuple(payload["traversed_relation_refs"]),
            unresolved_relation_refs=tuple(payload["unresolved_relation_refs"]),
            blocker_relation_refs=tuple(payload["blocker_relation_refs"]),
            boundary_refs=tuple(payload["boundary_refs"]),
            cycle_node_groups=tuple(tuple(item) for item in payload["cycle_node_groups"]),
            witnesses=tuple(TraversalWitness.from_dict(item) for item in payload["witnesses"]),
            status_bindings=tuple(
                RelationStatusBinding.from_dict(item)
                for item in payload["status_bindings"]
            ),
            reject_cycles=payload["reject_cycles"],
            impacts=tuple(ImpactAssignment.from_dict(item) for item in payload["impacts"]),
        )
        if result.to_dict() != dict(payload):
            raise RelationTraversalError("graph traversal receipt digest changed")
        return result


def _participants(relation: ArchitecturalRelation, role: str) -> tuple[str, ...]:
    return tuple(item.node_ref for item in relation.participants if item.role == role)


def _required_participants(relation: ArchitecturalRelation, role: str) -> tuple[str, ...]:
    values = _participants(relation, role)
    if not values:
        raise RelationTraversalError(
            f"{relation.kind.value} relation {relation.relation_id} lacks role {role}"
        )
    return values


def _pair_arcs(
    relation: ArchitecturalRelation,
    projection: RelationProjection,
    sources: Iterable[str],
    targets: Iterable[str],
    *,
    effect: ImpactEffect | None = None,
) -> list[RelationArc]:
    arcs: list[RelationArc] = []
    for source in sources:
        for target in targets:
            if source == target:
                continue
            if len(arcs) >= MAX_TRAVERSAL_ITEMS:
                raise RelationTraversalError(
                    "relation projection exceeds bounded arc expansion"
                )
            arcs.append(
                RelationArc(
                    relation_ref=relation.ref,
                    source_ref=source,
                    target_ref=target,
                    projection=projection,
                    epistemic_status=relation.epistemic_status,
                    impact_effect=effect,
                )
            )
    return arcs


def _project_relation(
    relation: ArchitecturalRelation,
    projection: RelationProjection,
) -> tuple[RelationArc, ...]:
    arcs: list[RelationArc] = []
    if projection is RelationProjection.IMPACT:
        for rule in relation.propagation_rules:
            arcs.extend(
                _pair_arcs(
                    relation,
                    projection,
                    _required_participants(relation, rule.trigger_role),
                    _required_participants(relation, rule.affected_role),
                    effect=rule.effect,
                )
            )
        return tuple(arcs)

    kind = relation.kind
    if projection is RelationProjection.COMPOSITION:
        if kind in {
            ArchitecturalRelationKind.COMPOSITION,
            ArchitecturalRelationKind.AGGREGATES,
        }:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "whole"), _required_participants(relation, "part")))
        elif kind is ArchitecturalRelationKind.PRIMARY_CONTAINS:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "container"), _required_participants(relation, "contained")))
    elif projection is RelationProjection.HOST:
        if kind is ArchitecturalRelationKind.HOST:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "hosted"), _required_participants(relation, "host")))
        elif kind is ArchitecturalRelationKind.HOSTS_VOID:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "void"), _required_participants(relation, "host")))
        elif kind is ArchitecturalRelationKind.FILLS_VOID:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "fill"), _required_participants(relation, "void")))
    elif projection is RelationProjection.SUPPORT:
        if kind is ArchitecturalRelationKind.SUPPORT:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "supported"), _required_participants(relation, "supporter")))
        elif kind is ArchitecturalRelationKind.LOAD_TRANSFER:
            senders = _required_participants(relation, "sender")
            receivers = _required_participants(relation, "receiver")
            via = _participants(relation, "via")
            if via:
                arcs.extend(_pair_arcs(relation, projection, senders, via))
                arcs.extend(_pair_arcs(relation, projection, via, receivers))
            else:
                arcs.extend(_pair_arcs(relation, projection, senders, receivers))
    elif projection is RelationProjection.ACCESS:
        if kind in {
            ArchitecturalRelationKind.ACCESS,
            ArchitecturalRelationKind.ALLOWS_PASSAGE,
        }:
            sources = _required_participants(relation, "from")
            targets = _required_participants(relation, "to")
            via = _participants(relation, "via")
            if via:
                arcs.extend(_pair_arcs(relation, projection, sources, via))
                arcs.extend(_pair_arcs(relation, projection, via, targets))
            else:
                arcs.extend(_pair_arcs(relation, projection, sources, targets))
    elif projection is RelationProjection.REALIZATION:
        if kind in {
            ArchitecturalRelationKind.REALIZATION,
            ArchitecturalRelationKind.REALIZES,
        }:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "semantic"), _required_participants(relation, "realization")))
    elif projection is RelationProjection.LINEAGE:
        if kind in {
            ArchitecturalRelationKind.LINEAGE,
            ArchitecturalRelationKind.REFINES,
            ArchitecturalRelationKind.REPLACES,
        }:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "current"), _required_participants(relation, "predecessor")))
    elif projection is RelationProjection.PROVENANCE:
        if kind is ArchitecturalRelationKind.EVIDENCES:
            arcs.extend(_pair_arcs(relation, projection, _required_participants(relation, "assertion"), _required_participants(relation, "evidence")))
    return tuple(arcs)


def compile_relation_view(
    graph: ArchitecturalRelationGraph,
    *,
    projection: RelationProjection,
    scenario_ref: str,
) -> RelationView:
    """Compile one exact question-specific relation projection."""

    if not isinstance(graph, ArchitecturalRelationGraph):
        raise TypeError("graph must be ArchitecturalRelationGraph")
    if not isinstance(projection, RelationProjection):
        raise TypeError("projection must be RelationProjection")
    scenario_ref = logical_ref(scenario_ref, "scenario_ref")
    arcs: list[RelationArc] = []
    for relation in graph.relations:
        if relation.scenario_ref not in {UNIVERSAL_SCENARIO_REF, scenario_ref}:
            continue
        projected = _project_relation(relation, projection)
        if len(arcs) + len(projected) > MAX_TRAVERSAL_ITEMS:
            raise RelationTraversalError(
                "relation view exceeds bounded arc expansion"
            )
        arcs.extend(projected)
    return RelationView(
        graph_digest=graph.graph_digest,
        branch=graph.branch,
        scope_digest=graph.scope_digest,
        stage_subject_digest=graph.stage_subject_digest,
        projection=projection,
        scenario_ref=scenario_ref,
        node_refs=tuple(item.node_ref for item in graph.nodes),
        arcs=tuple(arcs),
    )


def _adjacency(view: RelationView) -> dict[str, tuple[RelationArc, ...]]:
    values: dict[str, list[RelationArc]] = {}
    for arc in view.arcs:
        values.setdefault(arc.source_ref, []).append(arc)
    return {key: tuple(sorted(items, key=lambda item: item.identity)) for key, items in values.items()}


def find_cycle_node_groups(view: RelationView) -> tuple[tuple[str, ...], ...]:
    """Return deterministic SCC cycle groups without recursive graph walking."""

    if not isinstance(view, RelationView):
        raise TypeError("view must be RelationView")
    adjacency_sets: dict[str, set[str]] = {
        ref: set() for ref in view.node_refs
    }
    reverse_sets: dict[str, set[str]] = {
        ref: set() for ref in view.node_refs
    }
    for arc in view.arcs:
        adjacency_sets[arc.source_ref].add(arc.target_ref)
        reverse_sets[arc.target_ref].add(arc.source_ref)
    adjacency = {
        ref: tuple(sorted(targets))
        for ref, targets in adjacency_sets.items()
    }
    reverse_adjacency = {
        ref: tuple(sorted(sources))
        for ref, sources in reverse_sets.items()
    }

    visited: set[str] = set()
    finishing_order: list[str] = []
    for root in view.node_refs:
        if root in visited:
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node_ref, expanded = stack.pop()
            if expanded:
                finishing_order.append(node_ref)
                continue
            if node_ref in visited:
                continue
            visited.add(node_ref)
            stack.append((node_ref, True))
            for target in reversed(adjacency[node_ref]):
                if target not in visited:
                    stack.append((target, False))

    assigned: set[str] = set()
    groups: list[tuple[str, ...]] = []
    for root in reversed(finishing_order):
        if root in assigned:
            continue
        component: set[str] = set()
        stack = [(root, False)]
        while stack:
            node_ref, _ = stack.pop()
            if node_ref in assigned:
                continue
            assigned.add(node_ref)
            component.add(node_ref)
            for source in reversed(reverse_adjacency[node_ref]):
                if source not in assigned:
                    stack.append((source, False))
        if len(component) > 1:
            groups.append(tuple(sorted(component)))
    return tuple(sorted(groups))


def topological_order(view: RelationView) -> tuple[str, ...]:
    """Return a deterministic order or fail with the cyclic node groups."""

    cycles = find_cycle_node_groups(view)
    if cycles:
        raise RelationTraversalError(f"relation view contains cycles: {cycles!r}")
    adjacency = _adjacency(view)
    indegree = {node_ref: 0 for node_ref in view.node_refs}
    for arc in view.arcs:
        indegree[arc.target_ref] += 1
    queue = [ref for ref, value in indegree.items() if value == 0]
    heapify(queue)
    result: list[str] = []
    while queue:
        node_ref = heappop(queue)
        result.append(node_ref)
        for arc in adjacency.get(node_ref, ()):
            indegree[arc.target_ref] -= 1
            if indegree[arc.target_ref] == 0:
                heappush(queue, arc.target_ref)
    if len(result) != len(view.node_refs):
        raise RelationTraversalError("topological traversal did not cover the view")
    return tuple(result)


def _require_binding_receipts(
    view: RelationView,
    bindings: tuple[RelationStatusBinding, ...],
    receipts: tuple[CheckReceiptEnvelope, ...],
    policy: RelationTraversalPolicy,
) -> None:
    if not isinstance(view, RelationView):
        raise TypeError("view must be RelationView")
    if not isinstance(policy, RelationTraversalPolicy):
        raise TypeError("policy must be a RelationTraversalPolicy")
    if policy.projection is not view.projection:
        raise RelationTraversalError(
            "relation traversal policy crossed the relation projection"
        )
    requirements = {
        item.relation_ref: item for item in policy.check_requirements
    }
    if set(requirements) != set(view.relation_refs):
        raise RelationTraversalError(
            "traversal policy checks do not exactly cover the relation view"
        )
    if (
        not isinstance(receipts, tuple)
        or any(not isinstance(item, CheckReceiptEnvelope) for item in receipts)
    ):
        raise TypeError("check_receipts must contain CheckReceiptEnvelope values")
    receipt_by_id = {item.receipt_id: item for item in receipts}
    if len(receipt_by_id) != len(receipts):
        raise RelationTraversalError("check receipts repeat a receipt_id")
    expected_ids = {item.check_receipt_id for item in bindings}
    if set(receipt_by_id) != expected_ids:
        raise RelationTraversalError(
            "check receipts do not exactly cover relation status bindings"
        )
    for binding in bindings:
        receipt = receipt_by_id[binding.check_receipt_id]
        binding.require_receipt(receipt)
        requirement = requirements[binding.relation_ref]
        if (
            receipt.check_id != requirement.check_id
            or receipt.checker_id != requirement.checker_id
            or receipt.checker_version != requirement.checker_version
        ):
            raise RelationTraversalError(
                "relation check receipt crossed its exact checker requirement"
            )
    if any(
        item.checker_id not in policy.allowed_checker_ids
        for item in receipts
    ):
        raise RelationTraversalError(
            "relation check receipt used a checker outside traversal policy"
        )
    if any(
        item.branch != view.branch
        or item.scope_digest != view.scope_digest
        or item.subject_digest != view.stage_subject_digest
        for item in receipts
    ):
        raise RelationTraversalError(
            "relation check receipt crossed the exact relation view"
        )


def _effective_status_map(
    view: RelationView,
    bindings: tuple[RelationStatusBinding, ...],
) -> dict[str, RelationCheckStatus]:
    by_ref = {item.relation_ref: item.status for item in bindings}
    open_refs = {
        arc.relation_ref
        for arc in view.arcs
        if arc.epistemic_status in _OPEN_EPISTEMIC_STATUSES
    }
    return {
        relation_ref: (
            RelationCheckStatus.UNKNOWN
            if relation_ref in open_refs
            and status is not RelationCheckStatus.FAIL
            else status
        )
        for relation_ref, status in by_ref.items()
    }


def _status_map(
    view: RelationView,
    bindings: tuple[RelationStatusBinding, ...],
    receipts: tuple[CheckReceiptEnvelope, ...],
    policy: RelationTraversalPolicy,
) -> dict[str, RelationCheckStatus]:
    if (
        not isinstance(bindings, tuple)
        or len(bindings) > MAX_TRAVERSAL_ITEMS
        or any(not isinstance(item, RelationStatusBinding) for item in bindings)
    ):
        raise TypeError("status_bindings must contain RelationStatusBinding values")
    by_ref = {item.relation_ref: item.status for item in bindings}
    if len(by_ref) != len(bindings):
        raise RelationTraversalError("status bindings repeat a relation")
    expected = set(view.relation_refs)
    if set(by_ref) != expected:
        raise RelationTraversalError("status bindings do not exactly cover the relation view")
    _require_binding_receipts(view, bindings, receipts, policy)
    return _effective_status_map(view, bindings)


def _search_path(
    adjacency: dict[str, tuple[RelationArc, ...]],
    start_ref: str,
    targets: set[str],
    statuses: dict[str, RelationCheckStatus],
    allowed: frozenset[RelationCheckStatus],
    minimum_hops: int,
) -> tuple[TraversalWitness | None, set[str], set[str]]:
    queue = deque((start_ref,))
    visited = {start_ref}
    depth = {start_ref: 0}
    parent: dict[str, tuple[str, str]] = {}
    examined: set[str] = set()
    found: str | None = (
        start_ref
        if start_ref in targets and minimum_hops == 0
        else None
    )
    while queue and found is None:
        current = queue.popleft()
        for arc in adjacency.get(current, ()):
            examined.add(arc.relation_ref)
            if statuses[arc.relation_ref] not in allowed or arc.target_ref in visited:
                continue
            visited.add(arc.target_ref)
            parent[arc.target_ref] = (current, arc.relation_ref)
            depth[arc.target_ref] = depth[current] + 1
            if (
                arc.target_ref in targets
                and depth[arc.target_ref] >= minimum_hops
            ):
                found = arc.target_ref
                break
            queue.append(arc.target_ref)
    if found is None:
        return None, visited, examined
    nodes = [found]
    relations: list[str] = []
    while nodes[-1] != start_ref:
        previous, relation_ref = parent[nodes[-1]]
        relations.append(relation_ref)
        nodes.append(previous)
    nodes.reverse()
    relations.reverse()
    return (
        TraversalWitness(
            start_ref=start_ref,
            target_ref=found,
            node_refs=tuple(nodes),
            relation_refs=tuple(relations),
        ),
        visited,
        examined,
    )


def check_reachability(
    view: RelationView,
    *,
    policy: RelationTraversalPolicy,
    traversal_id: str,
    start_refs: tuple[str, ...],
    target_refs: tuple[str, ...],
    status_bindings: tuple[RelationStatusBinding, ...],
    check_receipts: tuple[CheckReceiptEnvelope, ...],
) -> GraphTraversalReceipt:
    """Require every start to reach at least one target through passing arcs."""

    if not isinstance(view, RelationView):
        raise TypeError("view must be RelationView")
    if not isinstance(policy, RelationTraversalPolicy):
        raise TypeError("policy must be a RelationTraversalPolicy")
    if policy.projection is not view.projection:
        raise RelationTraversalError(
            "relation traversal policy crossed the relation projection"
        )
    if view.projection is RelationProjection.IMPACT:
        raise RelationTraversalError(
            "reachability does not accept an IMPACT traversal policy"
        )
    identifier(traversal_id, "traversal_id")
    starts = _bounded_refs(start_refs, "start_refs")
    targets = _bounded_refs(target_refs, "target_refs")
    if starts != policy.start_refs or targets != policy.target_refs:
        raise RelationTraversalError(
            "reachability endpoints crossed the exact traversal policy"
        )
    known = set(view.node_refs)
    if not (set(starts) | set(targets)) <= known:
        raise RelationTraversalError("traversal endpoint is absent from the relation view")
    reject_cycles = projection_rejects_cycles(view.projection)
    statuses = _status_map(
        view,
        status_bindings,
        check_receipts,
        policy,
    )
    adjacency = _adjacency(view)
    cycles = find_cycle_node_groups(view)
    reached: set[str] = set()
    traversed: set[str] = set()
    unresolved: set[str] = set()
    blockers: set[str] = set()
    boundary: set[str] = set()
    witnesses: list[TraversalWitness] = []
    start_statuses: list[TraversalStatus] = []
    target_set = set(targets)

    if reject_cycles and cycles:
        receipt = GraphTraversalReceipt(
            traversal_id=traversal_id,
            graph_digest=view.graph_digest,
            view_digest=view.view_digest,
            policy_ref=policy.ref,
            policy_digest=policy.policy_digest,
            projection=view.projection,
            scenario_ref=view.scenario_ref,
            start_refs=starts,
            target_refs=targets,
            status=TraversalStatus.FAIL,
            stop_reason=TraversalStopReason.TARGETS_DISCONNECTED,
            reached_refs=starts,
            traversed_relation_refs=(),
            unresolved_relation_refs=(),
            blocker_relation_refs=(),
            boundary_refs=(),
            cycle_node_groups=cycles,
            witnesses=(),
            status_bindings=status_bindings,
            reject_cycles=reject_cycles,
        )
        receipt.require_check_receipts(view, policy, check_receipts)
        return receipt

    for start in starts:
        passed, pass_reached, pass_examined = _search_path(
            adjacency,
            start,
            target_set,
            statuses,
            frozenset({RelationCheckStatus.PASS}),
            policy.minimum_hops,
        )
        reached.update(pass_reached)
        traversed.update(pass_examined)
        if passed is not None:
            start_statuses.append(TraversalStatus.PASS)
            witnesses.append(passed)
            continue
        possible, possible_reached, possible_examined = _search_path(
            adjacency,
            start,
            target_set,
            statuses,
            frozenset({RelationCheckStatus.PASS, RelationCheckStatus.UNKNOWN}),
            policy.minimum_hops,
        )
        reached.update(possible_reached)
        traversed.update(possible_examined)
        unresolved.update(ref for ref in possible_examined if statuses[ref] is RelationCheckStatus.UNKNOWN)
        if possible is not None:
            start_statuses.append(TraversalStatus.UNKNOWN)
            witnesses.append(possible)
            unresolved.update(ref for ref in possible.relation_refs if statuses[ref] is RelationCheckStatus.UNKNOWN)
            continue
        start_statuses.append(TraversalStatus.FAIL)
        blockers.update(ref for ref in possible_examined if statuses[ref] is RelationCheckStatus.FAIL)
        boundary.update(possible_reached)

    if TraversalStatus.FAIL in start_statuses:
        status = TraversalStatus.FAIL
        reason = TraversalStopReason.TARGETS_DISCONNECTED
    elif TraversalStatus.UNKNOWN in start_statuses:
        status = TraversalStatus.UNKNOWN
        reason = TraversalStopReason.TARGETS_UNPROVEN
    else:
        status = TraversalStatus.PASS
        reason = TraversalStopReason.TARGETS_REACHED
    receipt = GraphTraversalReceipt(
        traversal_id=traversal_id,
        graph_digest=view.graph_digest,
        view_digest=view.view_digest,
        policy_ref=policy.ref,
        policy_digest=policy.policy_digest,
        projection=view.projection,
        scenario_ref=view.scenario_ref,
        start_refs=starts,
        target_refs=targets,
        status=status,
        stop_reason=reason,
        reached_refs=tuple(sorted(reached)),
        traversed_relation_refs=tuple(sorted(traversed)),
        unresolved_relation_refs=tuple(sorted(unresolved)),
        blocker_relation_refs=tuple(sorted(blockers)),
        boundary_refs=tuple(sorted(boundary)),
        cycle_node_groups=cycles,
        witnesses=tuple(witnesses),
        status_bindings=status_bindings,
        reject_cycles=reject_cycles,
    )
    receipt.require_check_receipts(view, policy, check_receipts)
    return receipt


def propagate_impacts(
    view: RelationView,
    *,
    policy: RelationTraversalPolicy,
    traversal_id: str,
    start_refs: tuple[str, ...],
) -> GraphTraversalReceipt:
    """Compute a monotone revalidate/invalidate closure with witness paths."""

    if not isinstance(view, RelationView):
        raise TypeError("view must be RelationView")
    if not isinstance(policy, RelationTraversalPolicy):
        raise TypeError("policy must be a RelationTraversalPolicy")
    if view.projection is not RelationProjection.IMPACT:
        raise RelationTraversalError("impact propagation requires an IMPACT relation view")
    if policy.projection is not RelationProjection.IMPACT:
        raise RelationTraversalError(
            "impact propagation policy crossed the relation projection"
        )
    starts = _bounded_refs(start_refs, "start_refs")
    if starts != policy.start_refs:
        raise RelationTraversalError(
            "impact origins crossed the exact traversal policy"
        )
    if not set(starts) <= set(view.node_refs):
        raise RelationTraversalError("impact start is absent from the relation view")
    adjacency = _adjacency(view)
    best = {ref: ImpactEffect.INVALIDATE for ref in starts}
    parent: dict[str, tuple[str, str]] = {}
    queue = deque(starts)
    traversed: set[str] = set()
    while queue:
        source = queue.popleft()
        for arc in adjacency.get(source, ()):
            traversed.add(arc.relation_ref)
            assert arc.impact_effect is not None
            candidate = arc.impact_effect
            current = best.get(arc.target_ref)
            if current is not None and _IMPACT_RANK[current] >= _IMPACT_RANK[candidate]:
                continue
            best[arc.target_ref] = candidate
            parent[arc.target_ref] = (source, arc.relation_ref)
            queue.append(arc.target_ref)
            if len(best) > MAX_TRAVERSAL_ITEMS:
                raise RelationTraversalError("impact closure exceeds bounded item count")
    witnesses: list[TraversalWitness] = []
    for target in sorted(set(best) - set(starts)):
        nodes = [target]
        relations: list[str] = []
        seen = {target}
        while nodes[-1] not in starts:
            previous, relation_ref = parent[nodes[-1]]
            if previous in seen:
                break
            seen.add(previous)
            relations.append(relation_ref)
            nodes.append(previous)
        if nodes[-1] not in starts:
            continue
        nodes.reverse()
        relations.reverse()
        witnesses.append(
            TraversalWitness(
                start_ref=nodes[0],
                target_ref=target,
                node_refs=tuple(nodes),
                relation_refs=tuple(relations),
            )
        )
    receipt = GraphTraversalReceipt(
        traversal_id=traversal_id,
        graph_digest=view.graph_digest,
        view_digest=view.view_digest,
        policy_ref=policy.ref,
        policy_digest=policy.policy_digest,
        projection=view.projection,
        scenario_ref=view.scenario_ref,
        start_refs=starts,
        target_refs=(),
        status=TraversalStatus.PASS,
        stop_reason=TraversalStopReason.IMPACT_CLOSURE_COMPLETE,
        reached_refs=tuple(sorted(best)),
        traversed_relation_refs=tuple(sorted(traversed)),
        unresolved_relation_refs=(),
        blocker_relation_refs=(),
        boundary_refs=(),
        cycle_node_groups=find_cycle_node_groups(view),
        witnesses=tuple(witnesses),
        status_bindings=(),
        reject_cycles=False,
        impacts=tuple(ImpactAssignment(node_ref=ref, effect=effect) for ref, effect in sorted(best.items())),
    )
    receipt.require_view(view)
    receipt.require_policy(policy)
    return receipt


__all__ = [
    "GraphTraversalReceipt",
    "ImpactAssignment",
    "RelationArc",
    "RelationCheckerRequirement",
    "RelationCheckStatus",
    "RelationStatusBinding",
    "RelationTraversalError",
    "RelationTraversalPolicy",
    "RelationView",
    "TraversalStatus",
    "TraversalStopReason",
    "TraversalWitness",
    "UNIVERSAL_SCENARIO_REF",
    "check_reachability",
    "compile_relation_view",
    "find_cycle_node_groups",
    "propagate_impacts",
    "projection_rejects_cycles",
    "topological_order",
]
