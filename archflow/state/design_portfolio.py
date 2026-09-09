"""Committed design stages and continuing branches, without persistence.

The application checks candidate contents and explicit acceptance. P036 retains
stages and atomically advances branch references. The values and rules here do
neither, and never turn ordinary A/B exploration into separate branches.

The schematic portfolio records below remain readers for retained data and the
compiler compatibility types. Their superseded lifecycle writers are removed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from archflow.project.refs import (
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.state.operational_state import require_logical_ref
from archflow.state.spatial import SchematicOption
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    mapping as _mapping,
    string_tuple as _strings,
)
from archflow.contracts.fields import (
    exact_mapping as _exact,
    ids,
    refs as _refs,
    text,
)


class DesignPortfolioError(ValueError):
    """A portfolio transition is stale, malformed, or exceeds its authority."""


def _record_ref(value: object, field: str) -> ProjectRecordRef:
    if not isinstance(value, ProjectRecordRef):
        raise TypeError(f"{field} must be a ProjectRecordRef")
    return value


@dataclass(frozen=True, slots=True)
class DesignStage:
    """A committed snapshot's contents; its identity is the P036 record ref.

    ``model_ref`` names the retained composed-model registration or native CAD
    receipt; ``model_sha256`` selects the exact model bytes from that source.
    The application verifies those bytes, the StateRecord and runner receipt.
    Constructing this value does not accept a candidate or advance a branch.
    """

    parent_stage: ProjectRecordRef | None
    record_ref: ProjectRecordRef
    model_ref: ProjectRecordRef
    model_sha256: str
    runner_ref: ProjectRecordRef
    candidate_id: str
    branch_id: str
    label: str
    accepted_by: str

    def __post_init__(self) -> None:
        project_id = _record_ref(self.record_ref, "record_ref").project_id
        for field in ("model_ref", "runner_ref", "parent_stage"):
            value = getattr(self, field)
            if value is None and field == "parent_stage":
                continue
            if _record_ref(value, field).project_id != project_id:
                raise DesignPortfolioError(f"{field} belongs to another project")
        require_sha256(self.model_sha256, "model_sha256")
        require_identifier(self.candidate_id, "candidate_id")
        require_identifier(self.branch_id, "branch_id")
        text(self.label, "stage label")
        text(self.accepted_by, "accepted_by")

    @property
    def project_id(self) -> str:
        return self.record_ref.project_id

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_stage": None if self.parent_stage is None else self.parent_stage.to_dict(),
            "record_ref": self.record_ref.to_dict(),
            "model_ref": self.model_ref.to_dict(),
            "model_sha256": self.model_sha256,
            "runner_ref": self.runner_ref.to_dict(),
            "candidate_id": self.candidate_id,
            "branch_id": self.branch_id,
            "label": self.label,
            "accepted_by": self.accepted_by,
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignStage:
        payload = _mapping(value, "design stage")
        _exact(payload, {
            "parent_stage", "record_ref", "model_ref", "model_sha256", "runner_ref",
            "candidate_id", "branch_id", "label", "accepted_by",
        }, "design stage")
        parent = payload["parent_stage"]
        return cls(
            parent_stage=None if parent is None else ProjectRecordRef.from_dict(parent),
            record_ref=ProjectRecordRef.from_dict(payload["record_ref"]),
            model_ref=ProjectRecordRef.from_dict(payload["model_ref"]),
            model_sha256=payload["model_sha256"],
            runner_ref=ProjectRecordRef.from_dict(payload["runner_ref"]),
            candidate_id=payload["candidate_id"], branch_id=payload["branch_id"],
            label=payload["label"], accepted_by=payload["accepted_by"],
        )


@dataclass(frozen=True, slots=True)
class DesignBranch:
    """A continuing history line, separate from one exploration's candidates."""

    branch_id: str
    parent_branch: str | None
    fork_stage: ProjectRecordRef
    head_stage: ProjectRecordRef

    def __post_init__(self) -> None:
        require_identifier(self.branch_id, "branch_id")
        if self.parent_branch is not None:
            require_identifier(self.parent_branch, "parent_branch")
            if self.parent_branch == self.branch_id:
                raise DesignPortfolioError("a branch cannot fork from itself")
        fork = _record_ref(self.fork_stage, "fork_stage")
        head = _record_ref(self.head_stage, "head_stage")
        if fork.project_id != head.project_id:
            raise DesignPortfolioError("branch stages belong to different projects")

    @property
    def project_id(self) -> str:
        return self.head_stage.project_id

    def to_dict(self) -> dict[str, object]:
        return {
            "branch_id": self.branch_id,
            "parent_branch": self.parent_branch,
            "fork_stage": self.fork_stage.to_dict(),
            "head_stage": self.head_stage.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignBranch:
        payload = _mapping(value, "design branch")
        _exact(payload, {"branch_id", "parent_branch", "fork_stage", "head_stage"}, "design branch")
        return cls(
            branch_id=payload["branch_id"], parent_branch=payload["parent_branch"],
            fork_stage=ProjectRecordRef.from_dict(payload["fork_stage"]),
            head_stage=ProjectRecordRef.from_dict(payload["head_stage"]),
        )


def initialize_branch(branch_id: str, stage_ref: ProjectRecordRef) -> DesignBranch:
    """Start a history line at a snapshot the caller has verified and retained."""
    return DesignBranch(branch_id, None, stage_ref, stage_ref)


def fork_branch(
    source_branch: DesignBranch, *, new_branch_id: str, stage_ref: ProjectRecordRef,
) -> DesignBranch:
    """Fork at a resolved committed stage, including a non-head historical one.

    The repository/application resolves committed ancestry and refuses a branch
    id that already exists. This pure rule preserves the original history line.
    """
    if not isinstance(source_branch, DesignBranch):
        raise TypeError("source_branch must be a DesignBranch")
    if _record_ref(stage_ref, "stage_ref").project_id != source_branch.project_id:
        raise DesignPortfolioError("fork stage belongs to another project")
    return DesignBranch(new_branch_id, source_branch.branch_id, stage_ref, stage_ref)


def advance_branch(
    branch: DesignBranch, *, expected_head: ProjectRecordRef,
    candidate_base: ProjectRecordRef, stage_ref: ProjectRecordRef, stage: DesignStage,
) -> DesignBranch:
    """Advance one line using an accepted candidate on its exact design head.

    ``stage_ref`` is the retained identity of ``stage``. Its content and the
    explicit acceptance are verified by the caller; P036 performs the atomic
    compare-and-swap. Canonical issue base and design Stage base are distinct.
    """
    if not isinstance(branch, DesignBranch):
        raise TypeError("branch must be a DesignBranch")
    if not isinstance(stage, DesignStage):
        raise TypeError("stage must be a DesignStage")
    _record_ref(expected_head, "expected_head")
    _record_ref(candidate_base, "candidate_base")
    _record_ref(stage_ref, "stage_ref")
    if branch.head_stage != expected_head:
        raise DesignPortfolioError("branch head changed")
    if candidate_base != expected_head:
        raise DesignPortfolioError("candidate base is not the expected Stage")
    if stage.parent_stage != expected_head:
        raise DesignPortfolioError("Stage parent is not the expected head")
    if stage.branch_id != branch.branch_id:
        raise DesignPortfolioError("Stage belongs to another branch")
    if stage.project_id != branch.project_id or stage_ref.project_id != branch.project_id:
        raise DesignPortfolioError("Stage belongs to another project")
    if stage_ref == expected_head:
        raise DesignPortfolioError("advancing a branch requires a new Stage")
    return replace(branch, head_stage=stage_ref)


# Retained schematic portfolio readers and compiler compatibility values.
# These schemas and their digests keep their historical serialized identity.


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


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    """Named authorities allowed to make or release a branch selection."""

    authority_ids: tuple[str, ...]
    source_refs: tuple[str, ...]

    SCHEMA = "SelectionPolicy@1"

    def __post_init__(self) -> None:
        ids(
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
        text(self.rationale, "expert advice rationale")
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
        require_sha256(self.revision_digest, "revision_digest")

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
        text(self.tradeoff_rationale, "tradeoff_rationale")
        require_identifier(self.author_id, "author_id")

    @property
    def revision_digest(self) -> str:
        return canonical_digest(self.to_dict())

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
class LegacyDesignBranch:
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
    def from_dict(cls, value: object) -> LegacyDesignBranch:
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
        text(self.summary, "Pareto summary")

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
        require_sha256(
            self.predecessor_portfolio_digest,
            "predecessor_portfolio_digest",
        )
        ids(
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
        text(self.rationale, "transition rationale")
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
    branches: tuple[LegacyDesignBranch, ...]
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
        require_sha256(
            self.source_option_set_digest,
            "source_option_set_digest",
        )
        require_sha256(
            self.operational_state_digest,
            "operational_state_digest",
        )
        if not isinstance(self.selection_policy, SelectionPolicy):
            raise TypeError("selection_policy must be SelectionPolicy")
        if (
            not isinstance(self.branches, tuple)
            or len(self.branches) < 2
            or any(not isinstance(item, LegacyDesignBranch) for item in self.branches)
        ):
            raise DesignPortfolioError(
                "portfolio requires at least two branches"
            )
        branch_ids = tuple(item.branch_id for item in self.branches)
        ids(
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
        return canonical_digest(self.to_dict())

    @property
    def selected_branch(self) -> LegacyDesignBranch | None:
        return next(
            (
                branch
                for branch in self.branches
                if branch.lifecycle is BranchLifecycle.SELECTED
            ),
            None,
        )

    def branch(self, branch_id: str) -> LegacyDesignBranch:
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
            "base": self.base.to_dict(),
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
            base=ProjectVersionRef.from_dict(payload["base"]),
            source_option_set_digest=payload[
                "source_option_set_digest"
            ],
            operational_state_digest=payload[
                "operational_state_digest"
            ],
            selection_policy=SelectionPolicy.from_dict(
                payload["selection_policy"]
            ),
            branches=tuple(LegacyDesignBranch.from_dict(item) for item in branches),
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
        require_sha256(self.portfolio_digest, "portfolio_digest")
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
            "base": self.base.to_dict(),
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
