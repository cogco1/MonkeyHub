"""Reloadable schematic branch portfolios with explicit selection authority.

The portfolio is a framework-owned ledger for project-authored answers.  It
preserves alternatives and their lineage, but it does not rank them, evaluate
hard usability, or promote canonical state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import (
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.state.operational_state import require_logical_ref
from archflow.state.spatial import SchematicOption, SchematicOptionSet


_HEX = frozenset("0123456789abcdef")
_MAX_ITEMS = 4_096
_MAX_TEXT = 4_000


class DesignPortfolioError(ValueError):
    """A portfolio transition is stale, malformed, or exceeds its authority."""


class BranchLifecycle(StrEnum):
    ACTIVE = "active"
    PARKED = "parked"
    REJECTED = "rejected"
    SELECTED = "selected"


class LineageKind(StrEnum):
    ORIGIN = "origin"
    FORK = "fork"
    REVISION = "revision"
    COMBINE = "combine"


class AdviceDisposition(StrEnum):
    ADOPTED = "adopted"
    REJECTED = "rejected"
    PARTIAL = "partial"
    DEFERRED = "deferred"


class PortfolioTransitionKind(StrEnum):
    FORK = "fork"
    REVISE = "revise"
    COMBINE = "combine"
    PARK = "park"
    REJECT = "reject"
    SELECT = "select"
    ATTACH_PARETO_OBSERVATION = "attach_pareto_observation"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignPortfolioError(f"{field} must be non-empty text")
    if len(value) > _MAX_TEXT:
        raise DesignPortfolioError(f"{field} exceeds bounded text")
    return value


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise DesignPortfolioError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _exact(
    value: Mapping[str, Any],
    fields: set[str],
    label: str,
) -> None:
    if set(value) != fields:
        raise DesignPortfolioError(f"{label} schema drifted")


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def _ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
    sorted_required: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise DesignPortfolioError(f"{field} has invalid item count")
    for value in values:
        require_identifier(value, field)
    if len(values) != len(set(values)):
        raise DesignPortfolioError(f"{field} contains duplicates")
    if sorted_required and values != tuple(sorted(values)):
        raise DesignPortfolioError(f"{field} must use stable id order")
    return values


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise DesignPortfolioError(f"{field} has invalid item count")
    for value in values:
        require_logical_ref(value, field)
    if len(values) != len(set(values)):
        raise DesignPortfolioError(f"{field} contains duplicates")
    return values


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    """Named authorities allowed to make or release a branch selection."""

    authority_ids: tuple[str, ...]
    source_refs: tuple[str, ...]

    SCHEMA = "SelectionPolicy@1"

    def __post_init__(self) -> None:
        _ids(
            self.authority_ids,
            "selection authority_ids",
            sorted_required=True,
        )
        _refs(self.source_refs, "selection policy source_refs")

    def permits(self, authority_id: str) -> bool:
        require_identifier(authority_id, "authority_id")
        return authority_id in self.authority_ids

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "authority_ids": list(self.authority_ids),
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SelectionPolicy:
        payload = _mapping(value, "selection policy")
        _exact(
            payload,
            {"schema", "authority_ids", "source_refs"},
            "selection policy",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DesignPortfolioError("selection policy schema changed")
        return cls(
            authority_ids=_strings(
                payload["authority_ids"],
                "selection authority_ids",
            ),
            source_refs=_strings(
                payload["source_refs"],
                "selection policy source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExpertAdviceResolution:
    advice_ref: str
    expert_id: str
    disposition: AdviceDisposition
    rationale: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "ExpertAdviceResolution@1"

    def __post_init__(self) -> None:
        require_logical_ref(self.advice_ref, "advice_ref")
        require_identifier(self.expert_id, "expert_id")
        if not isinstance(self.disposition, AdviceDisposition):
            raise TypeError("disposition must be AdviceDisposition")
        _text(self.rationale, "expert advice rationale")
        _refs(self.evidence_refs, "expert advice evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "advice_ref": self.advice_ref,
            "expert_id": self.expert_id,
            "disposition": self.disposition.value,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> ExpertAdviceResolution:
        payload = _mapping(value, "expert advice resolution")
        _exact(
            payload,
            {
                "schema",
                "advice_ref",
                "expert_id",
                "disposition",
                "rationale",
                "evidence_refs",
            },
            "expert advice resolution",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DesignPortfolioError("expert advice schema changed")
        return cls(
            advice_ref=payload["advice_ref"],
            expert_id=payload["expert_id"],
            disposition=AdviceDisposition(payload["disposition"]),
            rationale=payload["rationale"],
            evidence_refs=_strings(
                payload["evidence_refs"],
                "expert advice evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class BranchRevisionRef:
    branch_id: str
    revision_id: str
    revision_digest: str

    def __post_init__(self) -> None:
        require_identifier(self.branch_id, "branch_id")
        require_identifier(self.revision_id, "revision_id")
        _sha256(self.revision_digest, "revision_digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "branch_id": self.branch_id,
            "revision_id": self.revision_id,
            "revision_digest": self.revision_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> BranchRevisionRef:
        payload = _mapping(value, "branch revision ref")
        _exact(
            payload,
            {"branch_id", "revision_id", "revision_digest"},
            "branch revision ref",
        )
        return cls(
            branch_id=payload["branch_id"],
            revision_id=payload["revision_id"],
            revision_digest=payload["revision_digest"],
        )


@dataclass(frozen=True, slots=True)
class BranchRevision:
    revision_id: str
    branch_id: str
    index: int
    kind: LineageKind
    option: SchematicOption
    parent_revisions: tuple[BranchRevisionRef, ...]
    requirement_refs: tuple[str, ...]
    derivation_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    expert_resolutions: tuple[ExpertAdviceResolution, ...]
    tradeoff_rationale: str
    author_id: str

    SCHEMA = "DesignBranchRevision@1"

    def __post_init__(self) -> None:
        require_identifier(self.revision_id, "revision_id")
        require_identifier(self.branch_id, "branch_id")
        if (
            not isinstance(self.index, int)
            or isinstance(self.index, bool)
            or self.index < 0
        ):
            raise DesignPortfolioError("revision index must be non-negative")
        if not isinstance(self.kind, LineageKind):
            raise TypeError("kind must be LineageKind")
        if not isinstance(self.option, SchematicOption):
            raise TypeError("option must be SchematicOption")
        if not isinstance(self.parent_revisions, tuple) or any(
            not isinstance(item, BranchRevisionRef)
            for item in self.parent_revisions
        ):
            raise TypeError("parent_revisions contains an invalid item")
        if len(self.parent_revisions) != len(set(self.parent_revisions)):
            raise DesignPortfolioError("parent_revisions contains duplicates")
        expected_parents = {
            LineageKind.ORIGIN: 0,
            LineageKind.FORK: 1,
            LineageKind.REVISION: 1,
        }
        if self.kind in expected_parents and (
            len(self.parent_revisions) != expected_parents[self.kind]
        ):
            raise DesignPortfolioError(
                f"{self.kind.value} has invalid parent count"
            )
        if self.kind is LineageKind.COMBINE and len(self.parent_revisions) < 2:
            raise DesignPortfolioError("combine requires at least two parents")
        _refs(self.requirement_refs, "revision requirement_refs")
        _refs(self.derivation_refs, "revision derivation_refs")
        _refs(self.evidence_refs, "revision evidence_refs")
        if not isinstance(self.expert_resolutions, tuple) or any(
            not isinstance(item, ExpertAdviceResolution)
            for item in self.expert_resolutions
        ):
            raise TypeError("expert_resolutions contains an invalid item")
        advice_refs = tuple(
            item.advice_ref for item in self.expert_resolutions
        )
        if len(advice_refs) != len(set(advice_refs)):
            raise DesignPortfolioError(
                "expert advice is resolved more than once"
            )
        _text(self.tradeoff_rationale, "tradeoff_rationale")
        require_identifier(self.author_id, "author_id")

    @property
    def revision_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def ref(self) -> BranchRevisionRef:
        return BranchRevisionRef(
            branch_id=self.branch_id,
            revision_id=self.revision_id,
            revision_digest=self.revision_digest,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "revision_id": self.revision_id,
            "branch_id": self.branch_id,
            "index": self.index,
            "kind": self.kind.value,
            "option": self.option.to_dict(),
            "parent_revisions": [
                item.to_dict() for item in self.parent_revisions
            ],
            "requirement_refs": list(self.requirement_refs),
            "derivation_refs": list(self.derivation_refs),
            "evidence_refs": list(self.evidence_refs),
            "expert_resolutions": [
                item.to_dict() for item in self.expert_resolutions
            ],
            "tradeoff_rationale": self.tradeoff_rationale,
            "author_id": self.author_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> BranchRevision:
        payload = _mapping(value, "branch revision")
        _exact(
            payload,
            {
                "schema",
                "revision_id",
                "branch_id",
                "index",
                "kind",
                "option",
                "parent_revisions",
                "requirement_refs",
                "derivation_refs",
                "evidence_refs",
                "expert_resolutions",
                "tradeoff_rationale",
                "author_id",
            },
            "branch revision",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DesignPortfolioError("branch revision schema changed")
        parents = payload["parent_revisions"]
        resolutions = payload["expert_resolutions"]
        if not isinstance(parents, list) or not isinstance(resolutions, list):
            raise TypeError("revision nested values must be lists")
        return cls(
            revision_id=payload["revision_id"],
            branch_id=payload["branch_id"],
            index=payload["index"],
            kind=LineageKind(payload["kind"]),
            option=SchematicOption.from_dict(payload["option"]),
            parent_revisions=tuple(
                BranchRevisionRef.from_dict(item) for item in parents
            ),
            requirement_refs=_strings(
                payload["requirement_refs"],
                "revision requirement_refs",
            ),
            derivation_refs=_strings(
                payload["derivation_refs"],
                "revision derivation_refs",
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "revision evidence_refs",
            ),
            expert_resolutions=tuple(
                ExpertAdviceResolution.from_dict(item)
                for item in resolutions
            ),
            tradeoff_rationale=payload["tradeoff_rationale"],
            author_id=payload["author_id"],
        )


@dataclass(frozen=True, slots=True)
class DesignBranch:
    branch_id: str
    lifecycle: BranchLifecycle
    revisions: tuple[BranchRevision, ...]
    lifecycle_evidence_refs: tuple[str, ...]

    SCHEMA = "DesignBranch@1"

    def __post_init__(self) -> None:
        require_identifier(self.branch_id, "branch_id")
        if not isinstance(self.lifecycle, BranchLifecycle):
            raise TypeError("lifecycle must be BranchLifecycle")
        if not isinstance(self.revisions, tuple) or not self.revisions:
            raise DesignPortfolioError("branch requires revisions")
        if any(
            not isinstance(item, BranchRevision) for item in self.revisions
        ):
            raise TypeError("revisions contains an invalid item")
        if any(item.branch_id != self.branch_id for item in self.revisions):
            raise DesignPortfolioError("revision belongs to another branch")
        if tuple(item.index for item in self.revisions) != tuple(
            range(len(self.revisions))
        ):
            raise DesignPortfolioError("revision indices are not contiguous")
        revision_ids = tuple(item.revision_id for item in self.revisions)
        if len(revision_ids) != len(set(revision_ids)):
            raise DesignPortfolioError("revision ids contain duplicates")
        if self.revisions[0].kind not in {
            LineageKind.ORIGIN,
            LineageKind.FORK,
            LineageKind.COMBINE,
        }:
            raise DesignPortfolioError(
                "first branch revision has invalid lineage kind"
            )
        if any(
            item.kind is not LineageKind.REVISION
            for item in self.revisions[1:]
        ):
            raise DesignPortfolioError(
                "later branch revisions must be revisions"
            )
        _refs(
            self.lifecycle_evidence_refs,
            "lifecycle_evidence_refs",
            allow_empty=True,
        )

    @property
    def head(self) -> BranchRevision:
        return self.revisions[-1]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch_id": self.branch_id,
            "lifecycle": self.lifecycle.value,
            "revisions": [item.to_dict() for item in self.revisions],
            "lifecycle_evidence_refs": list(
                self.lifecycle_evidence_refs
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignBranch:
        payload = _mapping(value, "design branch")
        _exact(
            payload,
            {
                "schema",
                "branch_id",
                "lifecycle",
                "revisions",
                "lifecycle_evidence_refs",
            },
            "design branch",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DesignPortfolioError("design branch schema changed")
        revisions = payload["revisions"]
        if not isinstance(revisions, list):
            raise TypeError("revisions must be a list")
        return cls(
            branch_id=payload["branch_id"],
            lifecycle=BranchLifecycle(payload["lifecycle"]),
            revisions=tuple(
                BranchRevision.from_dict(item) for item in revisions
            ),
            lifecycle_evidence_refs=_strings(
                payload["lifecycle_evidence_refs"],
                "lifecycle_evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class ParetoBranchObservation:
    """Detached comparison evidence with deliberately no selection authority."""

    observation_ref: str
    branch_revisions: tuple[BranchRevisionRef, ...]
    objective_names: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    summary: str

    SCHEMA = "ParetoBranchObservation@1"

    def __post_init__(self) -> None:
        require_logical_ref(self.observation_ref, "observation_ref")
        if (
            not isinstance(self.branch_revisions, tuple)
            or len(self.branch_revisions) < 2
            or any(
                not isinstance(item, BranchRevisionRef)
                for item in self.branch_revisions
            )
        ):
            raise DesignPortfolioError(
                "Pareto observation requires at least two branch revisions"
            )
        if len(self.branch_revisions) != len(set(self.branch_revisions)):
            raise DesignPortfolioError(
                "Pareto observation repeats a branch revision"
            )
        if not isinstance(self.objective_names, tuple):
            raise TypeError("objective_names must be a tuple")
        if not self.objective_names or any(
            not isinstance(item, str) or not item.strip()
            for item in self.objective_names
        ):
            raise DesignPortfolioError("objective_names are invalid")
        if len(self.objective_names) != len(set(self.objective_names)):
            raise DesignPortfolioError("objective_names contain duplicates")
        _refs(self.evidence_refs, "Pareto evidence_refs")
        _text(self.summary, "Pareto summary")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "observation_ref": self.observation_ref,
            "branch_revisions": [
                item.to_dict() for item in self.branch_revisions
            ],
            "objective_names": list(self.objective_names),
            "evidence_refs": list(self.evidence_refs),
            "summary": self.summary,
            "read_only": True,
            "selection_authority": False,
            "deletion_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ParetoBranchObservation:
        payload = _mapping(value, "Pareto branch observation")
        _exact(
            payload,
            {
                "schema",
                "observation_ref",
                "branch_revisions",
                "objective_names",
                "evidence_refs",
                "summary",
                "read_only",
                "selection_authority",
                "deletion_authority",
            },
            "Pareto branch observation",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["read_only"] is not True
            or payload["selection_authority"] is not False
            or payload["deletion_authority"] is not False
        ):
            raise DesignPortfolioError(
                "Pareto observation acquired forbidden authority"
            )
        revisions = payload["branch_revisions"]
        if not isinstance(revisions, list):
            raise TypeError("branch_revisions must be a list")
        return cls(
            observation_ref=payload["observation_ref"],
            branch_revisions=tuple(
                BranchRevisionRef.from_dict(item) for item in revisions
            ),
            objective_names=_strings(
                payload["objective_names"],
                "objective_names",
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "Pareto evidence_refs",
            ),
            summary=payload["summary"],
        )


@dataclass(frozen=True, slots=True)
class PortfolioTransition:
    sequence: int
    transition_id: str
    kind: PortfolioTransitionKind
    predecessor_portfolio_digest: str
    affected_branch_ids: tuple[str, ...]
    result_revision_ref: BranchRevisionRef | None
    authority_id: str
    decision_ref: str
    rationale: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "PortfolioTransition@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise DesignPortfolioError("transition sequence must be positive")
        require_identifier(self.transition_id, "transition_id")
        if not isinstance(self.kind, PortfolioTransitionKind):
            raise TypeError("kind must be PortfolioTransitionKind")
        _sha256(
            self.predecessor_portfolio_digest,
            "predecessor_portfolio_digest",
        )
        _ids(
            self.affected_branch_ids,
            "affected_branch_ids",
            sorted_required=True,
        )
        if self.result_revision_ref is not None and not isinstance(
            self.result_revision_ref,
            BranchRevisionRef,
        ):
            raise TypeError(
                "result_revision_ref must be BranchRevisionRef or None"
            )
        require_identifier(self.authority_id, "authority_id")
        require_logical_ref(self.decision_ref, "decision_ref")
        _text(self.rationale, "transition rationale")
        _refs(self.evidence_refs, "transition evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "sequence": self.sequence,
            "transition_id": self.transition_id,
            "kind": self.kind.value,
            "predecessor_portfolio_digest": (
                self.predecessor_portfolio_digest
            ),
            "affected_branch_ids": list(self.affected_branch_ids),
            "result_revision_ref": (
                self.result_revision_ref.to_dict()
                if self.result_revision_ref is not None
                else None
            ),
            "authority_id": self.authority_id,
            "decision_ref": self.decision_ref,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> PortfolioTransition:
        payload = _mapping(value, "portfolio transition")
        _exact(
            payload,
            {
                "schema",
                "sequence",
                "transition_id",
                "kind",
                "predecessor_portfolio_digest",
                "affected_branch_ids",
                "result_revision_ref",
                "authority_id",
                "decision_ref",
                "rationale",
                "evidence_refs",
            },
            "portfolio transition",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DesignPortfolioError("portfolio transition schema changed")
        result_ref = payload["result_revision_ref"]
        return cls(
            sequence=payload["sequence"],
            transition_id=payload["transition_id"],
            kind=PortfolioTransitionKind(payload["kind"]),
            predecessor_portfolio_digest=payload[
                "predecessor_portfolio_digest"
            ],
            affected_branch_ids=_strings(
                payload["affected_branch_ids"],
                "affected_branch_ids",
            ),
            result_revision_ref=(
                BranchRevisionRef.from_dict(result_ref)
                if result_ref is not None
                else None
            ),
            authority_id=payload["authority_id"],
            decision_ref=payload["decision_ref"],
            rationale=payload["rationale"],
            evidence_refs=_strings(
                payload["evidence_refs"],
                "transition evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DesignOptionPortfolio:
    portfolio_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    source_option_set_digest: str
    operational_state_digest: str
    selection_policy: SelectionPolicy
    branches: tuple[DesignBranch, ...]
    observations: tuple[ParetoBranchObservation, ...]
    transitions: tuple[PortfolioTransition, ...]

    SCHEMA = "DesignOptionPortfolio@1"

    def __post_init__(self) -> None:
        require_identifier(self.portfolio_id, "portfolio_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise DesignPortfolioError(
                "portfolio and base belong to different projects"
            )
        _sha256(
            self.source_option_set_digest,
            "source_option_set_digest",
        )
        _sha256(
            self.operational_state_digest,
            "operational_state_digest",
        )
        if not isinstance(self.selection_policy, SelectionPolicy):
            raise TypeError("selection_policy must be SelectionPolicy")
        if (
            not isinstance(self.branches, tuple)
            or len(self.branches) < 2
            or any(not isinstance(item, DesignBranch) for item in self.branches)
        ):
            raise DesignPortfolioError(
                "portfolio requires at least two branches"
            )
        branch_ids = tuple(item.branch_id for item in self.branches)
        _ids(
            branch_ids,
            "portfolio branch ids",
            sorted_required=True,
        )
        selected = tuple(
            item.branch_id
            for item in self.branches
            if item.lifecycle is BranchLifecycle.SELECTED
        )
        if len(selected) > 1:
            raise DesignPortfolioError(
                "portfolio cannot have multiple selected branches"
            )
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, ParetoBranchObservation)
            for item in self.observations
        ):
            raise TypeError("observations contains an invalid item")
        observation_refs = tuple(
            item.observation_ref for item in self.observations
        )
        if len(observation_refs) != len(set(observation_refs)):
            raise DesignPortfolioError("observation refs contain duplicates")
        if not isinstance(self.transitions, tuple) or any(
            not isinstance(item, PortfolioTransition)
            for item in self.transitions
        ):
            raise TypeError("transitions contains an invalid item")
        if tuple(item.sequence for item in self.transitions) != tuple(
            range(1, len(self.transitions) + 1)
        ):
            raise DesignPortfolioError(
                "portfolio transition sequence is not contiguous"
            )
        transition_ids = tuple(
            item.transition_id for item in self.transitions
        )
        if len(transition_ids) != len(set(transition_ids)):
            raise DesignPortfolioError("transition ids contain duplicates")
        revisions = {
            (revision.branch_id, revision.revision_id): (
                revision.revision_digest
            )
            for branch in self.branches
            for revision in branch.revisions
        }
        for branch in self.branches:
            for revision in branch.revisions:
                for parent in revision.parent_revisions:
                    if revisions.get(
                        (parent.branch_id, parent.revision_id)
                    ) != parent.revision_digest:
                        raise DesignPortfolioError(
                            "branch lineage parent is missing or changed"
                        )
        for observation in self.observations:
            for item in observation.branch_revisions:
                if revisions.get((item.branch_id, item.revision_id)) != (
                    item.revision_digest
                ):
                    raise DesignPortfolioError(
                        "Pareto observation revision is missing or changed"
                    )
        known_branch_ids = set(branch_ids)
        for transition in self.transitions:
            if not set(transition.affected_branch_ids) <= known_branch_ids:
                raise DesignPortfolioError(
                    "transition names an unknown branch"
                )
            if transition.result_revision_ref is not None:
                result_ref = transition.result_revision_ref
                if revisions.get(
                    (result_ref.branch_id, result_ref.revision_id)
                ) != result_ref.revision_digest:
                    raise DesignPortfolioError(
                        "transition revision is missing or changed"
                    )
        if selected:
            selected_id = selected[0]
            selection = next(
                (
                    item
                    for item in reversed(self.transitions)
                    if selected_id in item.affected_branch_ids
                    and item.kind
                    in {
                        PortfolioTransitionKind.SELECT,
                        PortfolioTransitionKind.PARK,
                        PortfolioTransitionKind.REJECT,
                    }
                ),
                None,
            )
            if (
                selection is None
                or selection.kind is not PortfolioTransitionKind.SELECT
                or not self.selection_policy.permits(
                    selection.authority_id
                )
            ):
                raise DesignPortfolioError(
                    "selected branch lacks its latest authorized decision"
                )

    @property
    def run(self) -> RunRef:
        return RunRef(
            project_id=self.project_id,
            run_id=self.run_id,
            base=self.base,
        )

    @property
    def portfolio_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def selected_branch(self) -> DesignBranch | None:
        return next(
            (
                branch
                for branch in self.branches
                if branch.lifecycle is BranchLifecycle.SELECTED
            ),
            None,
        )

    def branch(self, branch_id: str) -> DesignBranch:
        require_identifier(branch_id, "branch_id")
        for branch in self.branches:
            if branch.branch_id == branch_id:
                return branch
        raise DesignPortfolioError(f"unknown branch: {branch_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "portfolio_id": self.portfolio_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "source_option_set_digest": self.source_option_set_digest,
            "operational_state_digest": self.operational_state_digest,
            "selection_policy": self.selection_policy.to_dict(),
            "branches": [item.to_dict() for item in self.branches],
            "observations": [
                item.to_dict() for item in self.observations
            ],
            "transitions": [
                item.to_dict() for item in self.transitions
            ],
            "ranked": False,
            "automatic_winner": False,
            "hard_usability_verdict": None,
            "canonical_write_authority": False,
            "candidate_assembly_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignOptionPortfolio:
        payload = _mapping(value, "design option portfolio")
        _exact(
            payload,
            {
                "schema",
                "portfolio_id",
                "project_id",
                "run_id",
                "base",
                "source_option_set_digest",
                "operational_state_digest",
                "selection_policy",
                "branches",
                "observations",
                "transitions",
                "ranked",
                "automatic_winner",
                "hard_usability_verdict",
                "canonical_write_authority",
                "candidate_assembly_authority",
            },
            "design option portfolio",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["ranked"] is not False
            or payload["automatic_winner"] is not False
            or payload["hard_usability_verdict"] is not None
            or payload["canonical_write_authority"] is not False
            or payload["candidate_assembly_authority"] is not False
        ):
            raise DesignPortfolioError(
                "portfolio acquired forbidden authority"
            )
        branches = payload["branches"]
        observations = payload["observations"]
        transitions = payload["transitions"]
        if not all(
            isinstance(item, list)
            for item in (branches, observations, transitions)
        ):
            raise TypeError("portfolio collections must be lists")
        return cls(
            portfolio_id=payload["portfolio_id"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            source_option_set_digest=payload[
                "source_option_set_digest"
            ],
            operational_state_digest=payload[
                "operational_state_digest"
            ],
            selection_policy=SelectionPolicy.from_dict(
                payload["selection_policy"]
            ),
            branches=tuple(DesignBranch.from_dict(item) for item in branches),
            observations=tuple(
                ParetoBranchObservation.from_dict(item)
                for item in observations
            ),
            transitions=tuple(
                PortfolioTransition.from_dict(item)
                for item in transitions
            ),
        )


@dataclass(frozen=True, slots=True)
class SelectedBranchHandoff:
    portfolio_id: str
    portfolio_digest: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    branch_id: str
    revision: BranchRevisionRef
    option: SchematicOption
    selection_transition_id: str
    selection_decision_ref: str

    SCHEMA = "SelectedSchematicBranch@1"

    def __post_init__(self) -> None:
        require_identifier(self.portfolio_id, "portfolio_id")
        _sha256(self.portfolio_digest, "portfolio_digest")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise DesignPortfolioError(
                "selected handoff belongs to another base"
            )
        require_identifier(self.branch_id, "branch_id")
        if (
            not isinstance(self.revision, BranchRevisionRef)
            or self.revision.branch_id != self.branch_id
        ):
            raise DesignPortfolioError(
                "selected handoff revision does not match branch"
            )
        if not isinstance(self.option, SchematicOption):
            raise TypeError("option must be SchematicOption")
        require_identifier(
            self.selection_transition_id,
            "selection_transition_id",
        )
        require_logical_ref(
            self.selection_decision_ref,
            "selection_decision_ref",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "portfolio_id": self.portfolio_id,
            "portfolio_digest": self.portfolio_digest,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "branch_id": self.branch_id,
            "revision": self.revision.to_dict(),
            "option": self.option.to_dict(),
            "selection_transition_id": self.selection_transition_id,
            "selection_decision_ref": self.selection_decision_ref,
            "design_development_complete": False,
            "hard_usability_verdict": None,
            "candidate_created": False,
            "canonical_write_authority": False,
        }


def _require_expected(
    portfolio: DesignOptionPortfolio,
    expected_portfolio_digest: str,
) -> str:
    if not isinstance(portfolio, DesignOptionPortfolio):
        raise TypeError("portfolio must be DesignOptionPortfolio")
    expected = _sha256(
        expected_portfolio_digest,
        "expected_portfolio_digest",
    )
    if portfolio.portfolio_digest != expected:
        raise DesignPortfolioError("portfolio transition has a stale base")
    return expected


def _require_decision(
    *,
    authority_id: str,
    decision_ref: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
) -> None:
    require_identifier(authority_id, "authority_id")
    require_logical_ref(decision_ref, "decision_ref")
    _text(rationale, "transition rationale")
    _refs(evidence_refs, "transition evidence_refs")


def _replace_branch(
    portfolio: DesignOptionPortfolio,
    branch: DesignBranch,
) -> tuple[DesignBranch, ...]:
    branches = tuple(
        branch if item.branch_id == branch.branch_id else item
        for item in portfolio.branches
    )
    return tuple(sorted(branches, key=lambda item: item.branch_id))


def _append_transition(
    portfolio: DesignOptionPortfolio,
    *,
    expected_digest: str,
    transition_id: str,
    kind: PortfolioTransitionKind,
    affected_branch_ids: tuple[str, ...],
    result_revision_ref: BranchRevisionRef | None,
    authority_id: str,
    decision_ref: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
    branches: tuple[DesignBranch, ...] | None = None,
    observations: tuple[ParetoBranchObservation, ...] | None = None,
) -> DesignOptionPortfolio:
    transition = PortfolioTransition(
        sequence=len(portfolio.transitions) + 1,
        transition_id=transition_id,
        kind=kind,
        predecessor_portfolio_digest=expected_digest,
        affected_branch_ids=tuple(sorted(affected_branch_ids)),
        result_revision_ref=result_revision_ref,
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )
    return replace(
        portfolio,
        branches=branches if branches is not None else portfolio.branches,
        observations=(
            observations
            if observations is not None
            else portfolio.observations
        ),
        transitions=portfolio.transitions + (transition,),
    )


def initialize_design_portfolio(
    option_set: SchematicOptionSet,
    *,
    portfolio_id: str,
    selection_policy: SelectionPolicy,
    architect_id: str,
) -> DesignOptionPortfolio:
    if not isinstance(option_set, SchematicOptionSet):
        raise TypeError("option_set must be SchematicOptionSet")
    require_identifier(portfolio_id, "portfolio_id")
    if not isinstance(selection_policy, SelectionPolicy):
        raise TypeError("selection_policy must be SelectionPolicy")
    require_identifier(architect_id, "architect_id")
    branches = []
    for option in option_set.options:
        proposal = option.proposal
        revision = BranchRevision(
            revision_id=f"origin-{option.option_id}",
            branch_id=option.option_id,
            index=0,
            kind=LineageKind.ORIGIN,
            option=option,
            parent_revisions=(),
            requirement_refs=proposal.responds_to_refs,
            derivation_refs=(option_set.ref, proposal.ref),
            evidence_refs=proposal.evidence_refs,
            expert_resolutions=(),
            tradeoff_rationale=proposal.rationale,
            author_id=architect_id,
        )
        branches.append(
            DesignBranch(
                branch_id=option.option_id,
                lifecycle=BranchLifecycle.ACTIVE,
                revisions=(revision,),
                lifecycle_evidence_refs=(),
            )
        )
    return DesignOptionPortfolio(
        portfolio_id=portfolio_id,
        project_id=option_set.project_id,
        run_id=option_set.run_id,
        base=option_set.base,
        source_option_set_digest=option_set.option_set_digest,
        operational_state_digest=option_set.operational_state_digest,
        selection_policy=selection_policy,
        branches=tuple(sorted(branches, key=lambda item: item.branch_id)),
        observations=(),
        transitions=(),
    )


def fork_branch(
    portfolio: DesignOptionPortfolio,
    *,
    expected_portfolio_digest: str,
    parent_branch_id: str,
    new_branch_id: str,
    revision_id: str,
    option: SchematicOption,
    authority_id: str,
    decision_ref: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
    expert_resolutions: tuple[ExpertAdviceResolution, ...] = (),
    transition_id: str,
) -> DesignOptionPortfolio:
    expected = _require_expected(portfolio, expected_portfolio_digest)
    _require_decision(
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )
    require_identifier(new_branch_id, "new_branch_id")
    if any(item.branch_id == new_branch_id for item in portfolio.branches):
        raise DesignPortfolioError("new branch id already exists")
    parent = portfolio.branch(parent_branch_id)
    if parent.lifecycle is BranchLifecycle.REJECTED:
        raise DesignPortfolioError("cannot fork a rejected branch")
    requirements = tuple(sorted(set(parent.head.requirement_refs)))
    if not set(requirements).issubset(option.proposal.responds_to_refs):
        raise DesignPortfolioError(
            "fork lost parent requirement or commitment references"
        )
    revision = BranchRevision(
        revision_id=revision_id,
        branch_id=new_branch_id,
        index=0,
        kind=LineageKind.FORK,
        option=option,
        parent_revisions=(parent.head.ref,),
        requirement_refs=requirements,
        derivation_refs=tuple(
            sorted(
                set(parent.head.derivation_refs)
                | {parent.head.option.ref, option.proposal.ref}
            )
        ),
        evidence_refs=evidence_refs,
        expert_resolutions=expert_resolutions,
        tradeoff_rationale=rationale,
        author_id=authority_id,
    )
    branch = DesignBranch(
        branch_id=new_branch_id,
        lifecycle=BranchLifecycle.ACTIVE,
        revisions=(revision,),
        lifecycle_evidence_refs=(),
    )
    branches = tuple(
        sorted(
            portfolio.branches + (branch,),
            key=lambda item: item.branch_id,
        )
    )
    return _append_transition(
        portfolio,
        expected_digest=expected,
        transition_id=transition_id,
        kind=PortfolioTransitionKind.FORK,
        affected_branch_ids=(parent_branch_id, new_branch_id),
        result_revision_ref=revision.ref,
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
        branches=branches,
    )


def revise_branch(
    portfolio: DesignOptionPortfolio,
    *,
    expected_portfolio_digest: str,
    branch_id: str,
    revision_id: str,
    option: SchematicOption,
    authority_id: str,
    decision_ref: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
    expert_resolutions: tuple[ExpertAdviceResolution, ...] = (),
    transition_id: str,
) -> DesignOptionPortfolio:
    expected = _require_expected(portfolio, expected_portfolio_digest)
    _require_decision(
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )
    branch = portfolio.branch(branch_id)
    if branch.lifecycle in {
        BranchLifecycle.REJECTED,
        BranchLifecycle.SELECTED,
    }:
        raise DesignPortfolioError(
            "rejected or selected branch must not be revised in place"
        )
    requirements = branch.head.requirement_refs
    if not set(requirements).issubset(option.proposal.responds_to_refs):
        raise DesignPortfolioError(
            "revision lost requirement or commitment references"
        )
    revision = BranchRevision(
        revision_id=revision_id,
        branch_id=branch_id,
        index=len(branch.revisions),
        kind=LineageKind.REVISION,
        option=option,
        parent_revisions=(branch.head.ref,),
        requirement_refs=requirements,
        derivation_refs=tuple(
            sorted(
                set(branch.head.derivation_refs)
                | {branch.head.option.ref, option.proposal.ref}
            )
        ),
        evidence_refs=evidence_refs,
        expert_resolutions=expert_resolutions,
        tradeoff_rationale=rationale,
        author_id=authority_id,
    )
    revised = replace(branch, revisions=branch.revisions + (revision,))
    return _append_transition(
        portfolio,
        expected_digest=expected,
        transition_id=transition_id,
        kind=PortfolioTransitionKind.REVISE,
        affected_branch_ids=(branch_id,),
        result_revision_ref=revision.ref,
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
        branches=_replace_branch(portfolio, revised),
    )


def combine_branches(
    portfolio: DesignOptionPortfolio,
    *,
    expected_portfolio_digest: str,
    parent_branch_ids: tuple[str, ...],
    new_branch_id: str,
    revision_id: str,
    option: SchematicOption,
    authority_id: str,
    decision_ref: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
    expert_resolutions: tuple[ExpertAdviceResolution, ...] = (),
    transition_id: str,
) -> DesignOptionPortfolio:
    expected = _require_expected(portfolio, expected_portfolio_digest)
    _require_decision(
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )
    _ids(
        parent_branch_ids,
        "parent_branch_ids",
        sorted_required=True,
    )
    if len(parent_branch_ids) < 2:
        raise DesignPortfolioError("combine requires at least two branches")
    require_identifier(new_branch_id, "new_branch_id")
    if any(item.branch_id == new_branch_id for item in portfolio.branches):
        raise DesignPortfolioError("combined branch id already exists")
    parents = tuple(portfolio.branch(item) for item in parent_branch_ids)
    if any(
        item.lifecycle is BranchLifecycle.REJECTED for item in parents
    ):
        raise DesignPortfolioError("cannot combine a rejected branch")
    requirements = tuple(
        sorted(
            {
                ref
                for parent in parents
                for ref in parent.head.requirement_refs
            }
        )
    )
    if not set(requirements).issubset(option.proposal.responds_to_refs):
        raise DesignPortfolioError(
            "combine lost a parent requirement or commitment reference"
        )
    derivation_refs = tuple(
        sorted(
            {
                ref
                for parent in parents
                for ref in (
                    *parent.head.derivation_refs,
                    parent.head.option.ref,
                )
            }
            | {option.proposal.ref}
        )
    )
    revision = BranchRevision(
        revision_id=revision_id,
        branch_id=new_branch_id,
        index=0,
        kind=LineageKind.COMBINE,
        option=option,
        parent_revisions=tuple(parent.head.ref for parent in parents),
        requirement_refs=requirements,
        derivation_refs=derivation_refs,
        evidence_refs=evidence_refs,
        expert_resolutions=expert_resolutions,
        tradeoff_rationale=rationale,
        author_id=authority_id,
    )
    branch = DesignBranch(
        branch_id=new_branch_id,
        lifecycle=BranchLifecycle.ACTIVE,
        revisions=(revision,),
        lifecycle_evidence_refs=(),
    )
    branches = tuple(
        sorted(
            portfolio.branches + (branch,),
            key=lambda item: item.branch_id,
        )
    )
    return _append_transition(
        portfolio,
        expected_digest=expected,
        transition_id=transition_id,
        kind=PortfolioTransitionKind.COMBINE,
        affected_branch_ids=parent_branch_ids + (new_branch_id,),
        result_revision_ref=revision.ref,
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
        branches=branches,
    )


def _change_lifecycle(
    portfolio: DesignOptionPortfolio,
    *,
    expected_portfolio_digest: str,
    branch_id: str,
    target: BranchLifecycle,
    kind: PortfolioTransitionKind,
    authority_id: str,
    decision_ref: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
    transition_id: str,
) -> DesignOptionPortfolio:
    expected = _require_expected(portfolio, expected_portfolio_digest)
    _require_decision(
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )
    branch = portfolio.branch(branch_id)
    if branch.lifecycle is target:
        raise DesignPortfolioError("lifecycle transition is a no-op")
    if branch.lifecycle is BranchLifecycle.REJECTED:
        raise DesignPortfolioError("rejected branch is terminal")
    if target is BranchLifecycle.SELECTED:
        if not portfolio.selection_policy.permits(authority_id):
            raise DesignPortfolioError(
                "authority is not allowed to select a branch"
            )
        if portfolio.selected_branch is not None:
            raise DesignPortfolioError(
                "release the existing selection before selecting another"
            )
    elif branch.lifecycle is BranchLifecycle.SELECTED:
        if (
            target is not BranchLifecycle.PARKED
            or not portfolio.selection_policy.permits(authority_id)
        ):
            raise DesignPortfolioError(
                "selected branch requires authorized release to parked"
            )
    elif target not in {
        BranchLifecycle.PARKED,
        BranchLifecycle.REJECTED,
    }:
        raise DesignPortfolioError("invalid lifecycle transition")
    changed = replace(
        branch,
        lifecycle=target,
        lifecycle_evidence_refs=tuple(
            sorted(set(branch.lifecycle_evidence_refs) | set(evidence_refs))
        ),
    )
    return _append_transition(
        portfolio,
        expected_digest=expected,
        transition_id=transition_id,
        kind=kind,
        affected_branch_ids=(branch_id,),
        result_revision_ref=branch.head.ref,
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
        branches=_replace_branch(portfolio, changed),
    )


def park_branch(
    portfolio: DesignOptionPortfolio,
    **kwargs: object,
) -> DesignOptionPortfolio:
    return _change_lifecycle(
        portfolio,
        target=BranchLifecycle.PARKED,
        kind=PortfolioTransitionKind.PARK,
        **kwargs,
    )


def reject_branch(
    portfolio: DesignOptionPortfolio,
    **kwargs: object,
) -> DesignOptionPortfolio:
    return _change_lifecycle(
        portfolio,
        target=BranchLifecycle.REJECTED,
        kind=PortfolioTransitionKind.REJECT,
        **kwargs,
    )


def select_branch(
    portfolio: DesignOptionPortfolio,
    **kwargs: object,
) -> DesignOptionPortfolio:
    return _change_lifecycle(
        portfolio,
        target=BranchLifecycle.SELECTED,
        kind=PortfolioTransitionKind.SELECT,
        **kwargs,
    )


def attach_pareto_observation(
    portfolio: DesignOptionPortfolio,
    *,
    expected_portfolio_digest: str,
    observation: ParetoBranchObservation,
    authority_id: str,
    decision_ref: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
    transition_id: str,
) -> DesignOptionPortfolio:
    expected = _require_expected(portfolio, expected_portfolio_digest)
    _require_decision(
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )
    if not isinstance(observation, ParetoBranchObservation):
        raise TypeError("observation must be ParetoBranchObservation")
    if any(
        item.observation_ref == observation.observation_ref
        for item in portfolio.observations
    ):
        raise DesignPortfolioError("Pareto observation already attached")
    for revision in observation.branch_revisions:
        if portfolio.branch(revision.branch_id).head.ref != revision:
            raise DesignPortfolioError(
                "Pareto observation is stale for a branch"
            )
    statuses = tuple(
        (item.branch_id, item.lifecycle) for item in portfolio.branches
    )
    result = _append_transition(
        portfolio,
        expected_digest=expected,
        transition_id=transition_id,
        kind=PortfolioTransitionKind.ATTACH_PARETO_OBSERVATION,
        affected_branch_ids=tuple(
            item.branch_id for item in observation.branch_revisions
        ),
        result_revision_ref=None,
        authority_id=authority_id,
        decision_ref=decision_ref,
        rationale=rationale,
        evidence_refs=evidence_refs,
        observations=portfolio.observations + (observation,),
    )
    if tuple(
        (item.branch_id, item.lifecycle) for item in result.branches
    ) != statuses:
        raise DesignPortfolioError(
            "read-only observation changed branch lifecycle"
        )
    return result


def compile_selected_branch_handoff(
    portfolio: DesignOptionPortfolio,
    *,
    expected_portfolio_digest: str,
    expected_revision_digest: str,
) -> SelectedBranchHandoff:
    _require_expected(portfolio, expected_portfolio_digest)
    selected = portfolio.selected_branch
    if selected is None:
        raise DesignPortfolioError(
            "candidate assembly requires an explicit selected branch"
        )
    expected_revision = _sha256(
        expected_revision_digest,
        "expected_revision_digest",
    )
    if selected.head.revision_digest != expected_revision:
        raise DesignPortfolioError(
            "selected branch handoff is stale"
        )
    selection = next(
        (
            item
            for item in reversed(portfolio.transitions)
            if item.kind is PortfolioTransitionKind.SELECT
            and selected.branch_id in item.affected_branch_ids
        ),
        None,
    )
    if selection is None or not portfolio.selection_policy.permits(
        selection.authority_id
    ):
        raise DesignPortfolioError(
            "selected branch lacks an authorized selection receipt"
        )
    return SelectedBranchHandoff(
        portfolio_id=portfolio.portfolio_id,
        portfolio_digest=portfolio.portfolio_digest,
        project_id=portfolio.project_id,
        run_id=portfolio.run_id,
        base=portfolio.base,
        branch_id=selected.branch_id,
        revision=selected.head.ref,
        option=selected.head.option,
        selection_transition_id=selection.transition_id,
        selection_decision_ref=selection.decision_ref,
    )
