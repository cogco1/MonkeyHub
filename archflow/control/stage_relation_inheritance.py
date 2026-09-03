"""Fail-closed architectural-relation inheritance between exact stages.

The predecessor graph is the denominator.  This module does not derive
relations from geometry, invent successor relations, or grant stage or
canonical-write authority.  It only joins caller-supplied relation lineage to
the exact current relation-realization manifest and receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archflow.contracts.branch import (
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
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archflow.relations.contracts import (
    ArchitecturalRelation,
    ArchitecturalRelationGraph,
)
from archflow.relations.realization import (
    RELATION_REALIZATION_CHECKER_ID,
    RelationRealizationManifest,
    relation_realization_denominator,
)
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "geometry_mutation_authority": False,
    "stage_acceptance_authority": False,
    "promotion_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}

STAGE_RELATION_INHERITANCE_CHECKER_ID = (
    "stage-relation-inheritance-validator"
)
STAGE_RELATION_INHERITANCE_CHECK_ID = "stage-relation-inheritance"


class StageRelationInheritanceError(ValueError):
    """A staged relation inheritance record is malformed or has drifted."""


class RelationInheritanceDisposition(StrEnum):
    """Mechanical disposition of one predecessor relation."""

    RETAINED = "retained"
    REFINED = "refined"
    MISSING = "missing"
    DUPLICATE = "duplicate"


def _record_ref_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_ref_from_dict(value: object, field: str) -> ProjectRecordRef:
    payload = exact_mapping(
        value,
        {"project_id", "relative_path", "sha256", "media_type"},
        field,
    )
    try:
        return ProjectRecordRef(
            project_id=payload["project_id"],
            relative_path=payload["relative_path"],
            sha256=payload["sha256"],
            media_type=payload["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise StageRelationInheritanceError(f"{field} is invalid") from exc


@dataclass(frozen=True, slots=True)
class AcceptedRelationTopologyIdentity:
    """One credited topology source and its exact promoted graph digest."""

    topology_source_digest: str
    graph_digest: str

    SCHEMA: ClassVar[str] = "AcceptedRelationTopologyIdentity@1"

    def __post_init__(self) -> None:
        for field in ("topology_source_digest", "graph_digest"):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "topology_source_digest": self.topology_source_digest,
            "graph_digest": self.graph_digest,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> "AcceptedRelationTopologyIdentity":
        payload = exact_mapping(
            value,
            {
                "schema",
                "topology_source_digest",
                "graph_digest",
                *_AUTHORITY_FIELDS,
            },
            "accepted relation topology identity",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageRelationInheritanceError(
                "unsupported accepted relation topology identity schema"
            )
        result = cls(
            topology_source_digest=payload["topology_source_digest"],
            graph_digest=payload["graph_digest"],
        )
        if result.to_dict() != payload:
            raise StageRelationInheritanceError(
                "accepted relation topology identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class AcceptedStageRelationPredecessor:
    """Typed P036 binding to the accepted graph of the immediately prior stage.

    This value has no acceptance authority by itself.  The durable controller
    replays the named checkpoint anchor and baseline-source record before it
    may credit the embedded graph as the inheritance denominator.
    """

    predecessor_checkpoint_ref: ProjectRecordRef
    predecessor_checkpoint_digest: str
    stage_exit_anchor_ref: ProjectRecordRef
    stage_exit_proof_digest: str
    baseline_sources_ref: ProjectRecordRef
    baseline_sources_digest: str
    baseline_coverage_ref: ProjectRecordRef
    baseline_coverage_digest: str
    accepted_topologies: tuple[AcceptedRelationTopologyIdentity, ...]
    topology_source_digest: str
    graph: ArchitecturalRelationGraph

    SCHEMA: ClassVar[str] = "AcceptedStageRelationPredecessor@1"

    def __post_init__(self) -> None:
        for field in (
            "predecessor_checkpoint_ref",
            "stage_exit_anchor_ref",
            "baseline_sources_ref",
            "baseline_coverage_ref",
        ):
            if not isinstance(getattr(self, field), ProjectRecordRef):
                raise TypeError(f"{field} must be ProjectRecordRef")
        if not isinstance(self.graph, ArchitecturalRelationGraph):
            raise TypeError("graph must be ArchitecturalRelationGraph")
        for field in (
            "predecessor_checkpoint_digest",
            "stage_exit_proof_digest",
            "baseline_sources_digest",
            "baseline_coverage_digest",
            "topology_source_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        run = self.graph.branch.run
        prefix = (
            f"runs/{run.run_id}/branches/"
            f"{self.graph.branch.branch_id}/records/"
        )
        for field in (
            "predecessor_checkpoint_ref",
            "stage_exit_anchor_ref",
            "baseline_sources_ref",
            "baseline_coverage_ref",
        ):
            ref = getattr(self, field)
            if (
                ref.project_id != run.project_id
                or not ref.relative_path.startswith(prefix)
            ):
                raise StageRelationInheritanceError(
                    f"{field} crossed predecessor project, run, or branch"
                )
        if (
            not isinstance(self.accepted_topologies, tuple)
            or not self.accepted_topologies
            or any(
                not isinstance(item, AcceptedRelationTopologyIdentity)
                for item in self.accepted_topologies
            )
        ):
            raise TypeError(
                "accepted_topologies must contain credited topology identities"
            )
        ordered = tuple(
            sorted(
                self.accepted_topologies,
                key=lambda item: (
                    item.topology_source_digest,
                    item.graph_digest,
                ),
            )
        )
        pairs = tuple(
            (item.topology_source_digest, item.graph_digest)
            for item in ordered
        )
        if (
            len(pairs) != len(set(pairs))
            or len({item[0] for item in pairs}) != len(pairs)
            or len({item[1] for item in pairs}) != len(pairs)
        ):
            raise StageRelationInheritanceError(
                "accepted topology identities must be one-to-one and unique"
            )
        selected = (self.topology_source_digest, self.graph.graph_digest)
        if selected not in pairs:
            raise StageRelationInheritanceError(
                "selected predecessor graph is outside the accepted topology set"
            )
        object.__setattr__(self, "accepted_topologies", ordered)

    @property
    def acceptance_set_digest(self) -> str:
        return canonical_digest(
            {
                "predecessor_checkpoint_ref": _record_ref_dict(
                    self.predecessor_checkpoint_ref
                ),
                "predecessor_checkpoint_digest": (
                    self.predecessor_checkpoint_digest
                ),
                "stage_exit_anchor_ref": _record_ref_dict(
                    self.stage_exit_anchor_ref
                ),
                "stage_exit_proof_digest": self.stage_exit_proof_digest,
                "baseline_sources_ref": _record_ref_dict(
                    self.baseline_sources_ref
                ),
                "baseline_sources_digest": self.baseline_sources_digest,
                "baseline_coverage_ref": _record_ref_dict(
                    self.baseline_coverage_ref
                ),
                "baseline_coverage_digest": self.baseline_coverage_digest,
                "accepted_topologies": [
                    item.to_dict() for item in self.accepted_topologies
                ],
            }
        )

    @property
    def binding_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"accepted-stage-relation-predecessor:{self.binding_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_checkpoint_ref": _record_ref_dict(
                self.predecessor_checkpoint_ref
            ),
            "predecessor_checkpoint_digest": (
                self.predecessor_checkpoint_digest
            ),
            "stage_exit_anchor_ref": _record_ref_dict(
                self.stage_exit_anchor_ref
            ),
            "stage_exit_proof_digest": self.stage_exit_proof_digest,
            "baseline_sources_ref": _record_ref_dict(
                self.baseline_sources_ref
            ),
            "baseline_sources_digest": self.baseline_sources_digest,
            "baseline_coverage_ref": _record_ref_dict(
                self.baseline_coverage_ref
            ),
            "baseline_coverage_digest": self.baseline_coverage_digest,
            "accepted_topologies": [
                item.to_dict() for item in self.accepted_topologies
            ],
            "topology_source_digest": self.topology_source_digest,
            "graph": self.graph.to_dict(),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> "AcceptedStageRelationPredecessor":
        payload = exact_mapping(
            value,
            {
                "schema",
                "predecessor_checkpoint_ref",
                "predecessor_checkpoint_digest",
                "stage_exit_anchor_ref",
                "stage_exit_proof_digest",
                "baseline_sources_ref",
                "baseline_sources_digest",
                "baseline_coverage_ref",
                "baseline_coverage_digest",
                "accepted_topologies",
                "topology_source_digest",
                "graph",
                *_AUTHORITY_FIELDS,
            },
            "accepted stage relation predecessor",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageRelationInheritanceError(
                "unsupported accepted stage relation predecessor schema"
            )
        if not isinstance(payload["accepted_topologies"], list):
            raise TypeError("accepted_topologies must be a list")
        result = cls(
            predecessor_checkpoint_ref=_record_ref_from_dict(
                payload["predecessor_checkpoint_ref"],
                "predecessor_checkpoint_ref",
            ),
            predecessor_checkpoint_digest=payload[
                "predecessor_checkpoint_digest"
            ],
            stage_exit_anchor_ref=_record_ref_from_dict(
                payload["stage_exit_anchor_ref"],
                "stage_exit_anchor_ref",
            ),
            stage_exit_proof_digest=payload["stage_exit_proof_digest"],
            baseline_sources_ref=_record_ref_from_dict(
                payload["baseline_sources_ref"],
                "baseline_sources_ref",
            ),
            baseline_sources_digest=payload["baseline_sources_digest"],
            baseline_coverage_ref=_record_ref_from_dict(
                payload["baseline_coverage_ref"],
                "baseline_coverage_ref",
            ),
            baseline_coverage_digest=payload["baseline_coverage_digest"],
            accepted_topologies=tuple(
                AcceptedRelationTopologyIdentity.from_dict(item)
                for item in payload["accepted_topologies"]
            ),
            topology_source_digest=payload["topology_source_digest"],
            graph=ArchitecturalRelationGraph.from_dict(payload["graph"]),
        )
        if result.to_dict() != payload:
            raise StageRelationInheritanceError(
                "accepted stage relation predecessor identity changed"
            )
        return result


def _typed_tuple(
    values: object,
    item_type: type,
    field: str,
) -> tuple[object, ...]:
    if not isinstance(values, tuple) or len(values) > 4_096:
        raise TypeError(f"{field} must be a bounded tuple")
    if any(not isinstance(item, item_type) for item in values):
        raise TypeError(f"{field} contains an invalid item")
    return values


@dataclass(frozen=True, slots=True)
class StageRelationInheritanceCoverage:
    """Exact successor claims for one predecessor relation denominator item."""

    predecessor_relation_ref: str
    predecessor_relation_digest: str
    current_relation_refs: tuple[str, ...]
    current_relation_digests: tuple[str, ...]
    disposition: RelationInheritanceDisposition

    SCHEMA: ClassVar[str] = "StageRelationInheritanceCoverage@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "predecessor_relation_ref",
            logical_ref(
                self.predecessor_relation_ref,
                "predecessor_relation_ref",
            ),
        )
        object.__setattr__(
            self,
            "predecessor_relation_digest",
            require_sha256(
                self.predecessor_relation_digest,
                "predecessor_relation_digest",
            ),
        )
        refs = deterministic_refs(
            self.current_relation_refs,
            "current_relation_refs",
            allow_empty=True,
        )
        if (
            not isinstance(self.current_relation_digests, tuple)
            or len(self.current_relation_digests) != len(refs)
        ):
            raise StageRelationInheritanceError(
                "current relation refs and digests differ in cardinality"
            )
        digests = tuple(
            require_sha256(item, "current_relation_digest")
            for item in self.current_relation_digests
        )
        object.__setattr__(self, "current_relation_refs", refs)
        object.__setattr__(self, "current_relation_digests", digests)
        if tuple(zip(refs, digests)) != tuple(sorted(zip(refs, digests))):
            raise StageRelationInheritanceError(
                "current relation refs and digests must be deterministically paired"
            )
        if not isinstance(self.disposition, RelationInheritanceDisposition):
            raise TypeError("disposition must be RelationInheritanceDisposition")
        cardinality = len(refs)
        expected = (
            RelationInheritanceDisposition.MISSING
            if cardinality == 0
            else RelationInheritanceDisposition.DUPLICATE
            if cardinality > 1
            else self.disposition
        )
        if self.disposition is not expected:
            raise StageRelationInheritanceError(
                "inheritance disposition disagrees with successor cardinality"
            )
        if cardinality == 1 and self.disposition not in {
            RelationInheritanceDisposition.RETAINED,
            RelationInheritanceDisposition.REFINED,
        }:
            raise StageRelationInheritanceError(
                "one successor relation must be retained or refined"
            )

    @property
    def ref(self) -> str:
        return f"stage-relation-inheritance-coverage:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_relation_ref": self.predecessor_relation_ref,
            "predecessor_relation_digest": self.predecessor_relation_digest,
            "current_relation_refs": list(self.current_relation_refs),
            "current_relation_digests": list(self.current_relation_digests),
            "disposition": self.disposition.value,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> "StageRelationInheritanceCoverage":
        payload = exact_mapping(
            value,
            {
                "schema",
                "predecessor_relation_ref",
                "predecessor_relation_digest",
                "current_relation_refs",
                "current_relation_digests",
                "disposition",
                *_AUTHORITY_FIELDS,
            },
            "stage relation inheritance coverage",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageRelationInheritanceError(
                "unsupported stage relation inheritance coverage schema"
            )
        for field in ("current_relation_refs", "current_relation_digests"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            predecessor_relation_ref=payload["predecessor_relation_ref"],
            predecessor_relation_digest=payload[
                "predecessor_relation_digest"
            ],
            current_relation_refs=tuple(payload["current_relation_refs"]),
            current_relation_digests=tuple(
                payload["current_relation_digests"]
            ),
            disposition=RelationInheritanceDisposition(
                payload["disposition"]
            ),
        )
        if result.to_dict() != payload:
            raise StageRelationInheritanceError(
                "stage relation inheritance coverage is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class StageRelationInheritanceReceipt:
    """No-authority result for an exact predecessor/current graph join."""

    predecessor_branch: BranchRef
    current_branch: BranchRef
    predecessor_stage_id: str
    current_stage_id: str
    predecessor_graph_digest: str
    current_graph_digest: str
    predecessor_stage_subject_digest: str
    current_stage_subject_digest: str
    realization_manifest_digest: str
    realization_receipt_digest: str
    realization_status: CheckStatus
    coverage: tuple[StageRelationInheritanceCoverage, ...]
    new_current_relation_refs: tuple[str, ...]
    findings: tuple[CheckFinding, ...]
    status: CheckStatus

    SCHEMA: ClassVar[str] = "StageRelationInheritanceReceipt@1"

    def __post_init__(self) -> None:
        require_exact_branch(self.predecessor_branch, "predecessor branch")
        require_exact_branch(self.current_branch, "current branch")
        identifier(self.predecessor_stage_id, "predecessor_stage_id")
        identifier(self.current_stage_id, "current_stage_id")
        for field in (
            "predecessor_graph_digest",
            "current_graph_digest",
            "predecessor_stage_subject_digest",
            "current_stage_subject_digest",
            "realization_manifest_digest",
            "realization_receipt_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        if not isinstance(self.realization_status, CheckStatus):
            raise TypeError("realization_status must be CheckStatus")
        coverage = _typed_tuple(
            self.coverage,
            StageRelationInheritanceCoverage,
            "coverage",
        )
        expected_coverage = tuple(
            sorted(coverage, key=lambda item: item.predecessor_relation_ref)
        )
        if coverage != expected_coverage:
            raise StageRelationInheritanceError(
                "relation inheritance coverage must be deterministically ordered"
            )
        predecessor_refs = tuple(
            item.predecessor_relation_ref for item in coverage
        )
        if len(predecessor_refs) != len(set(predecessor_refs)):
            raise StageRelationInheritanceError(
                "relation inheritance coverage repeats a predecessor"
            )
        deterministic_refs(
            self.new_current_relation_refs,
            "new_current_relation_refs",
            allow_empty=True,
        )
        findings = _typed_tuple(self.findings, CheckFinding, "findings")
        expected_findings = tuple(
            sorted(
                findings,
                key=lambda item: (item.code, item.subject_refs, item.message),
            )
        )
        if findings != expected_findings:
            raise StageRelationInheritanceError(
                "stage relation inheritance findings must be deterministic"
            )
        finding_keys = tuple(
            (item.code, item.subject_refs, item.message) for item in findings
        )
        if len(finding_keys) != len(set(finding_keys)):
            raise StageRelationInheritanceError(
                "stage relation inheritance findings contain duplicates"
            )
        if not isinstance(self.status, CheckStatus):
            raise TypeError("status must be CheckStatus")
        has_error = any(
            item.severity is FindingSeverity.ERROR for item in findings
        )
        has_unknown = any(
            item.severity is FindingSeverity.UNKNOWN for item in findings
        )
        expected_status = (
            CheckStatus.FAIL
            if has_error
            else CheckStatus.UNKNOWN
            if has_unknown
            else CheckStatus.PASS
        )
        if self.status is not expected_status:
            raise StageRelationInheritanceError(
                "stage relation inheritance status disagrees with findings"
            )
        if self.status is CheckStatus.PASS and (
            self.realization_status is not CheckStatus.PASS
            or any(
                item.disposition
                not in {
                    RelationInheritanceDisposition.RETAINED,
                    RelationInheritanceDisposition.REFINED,
                }
                for item in coverage
            )
        ):
            raise StageRelationInheritanceError(
                "passing inheritance lacks exact coverage or fresh realization"
            )

    @property
    def predecessor_relation_refs(self) -> tuple[str, ...]:
        return tuple(
            item.predecessor_relation_ref for item in self.coverage
        )

    @property
    def covered_predecessor_relation_refs(self) -> tuple[str, ...]:
        if self.status is not CheckStatus.PASS:
            return ()
        return self.predecessor_relation_refs

    @property
    def closure_ready(self) -> bool:
        return self.status is CheckStatus.PASS

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"stage-relation-inheritance-receipt:{self.receipt_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_branch": branch_ref_to_dict(
                self.predecessor_branch
            ),
            "current_branch": branch_ref_to_dict(self.current_branch),
            "predecessor_stage_id": self.predecessor_stage_id,
            "current_stage_id": self.current_stage_id,
            "predecessor_graph_digest": self.predecessor_graph_digest,
            "current_graph_digest": self.current_graph_digest,
            "predecessor_stage_subject_digest": (
                self.predecessor_stage_subject_digest
            ),
            "current_stage_subject_digest": (
                self.current_stage_subject_digest
            ),
            "realization_manifest_digest": self.realization_manifest_digest,
            "realization_receipt_digest": self.realization_receipt_digest,
            "realization_status": self.realization_status.value,
            "coverage": [item.to_dict() for item in self.coverage],
            "predecessor_relation_refs": list(
                self.predecessor_relation_refs
            ),
            "covered_predecessor_relation_refs": list(
                self.covered_predecessor_relation_refs
            ),
            "new_current_relation_refs": list(
                self.new_current_relation_refs
            ),
            "findings": [item.to_dict() for item in self.findings],
            "status": self.status.value,
            "closure_ready": self.closure_ready,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageRelationInheritanceReceipt":
        payload = exact_mapping(
            value,
            {
                "schema",
                "predecessor_branch",
                "current_branch",
                "predecessor_stage_id",
                "current_stage_id",
                "predecessor_graph_digest",
                "current_graph_digest",
                "predecessor_stage_subject_digest",
                "current_stage_subject_digest",
                "realization_manifest_digest",
                "realization_receipt_digest",
                "realization_status",
                "coverage",
                "predecessor_relation_refs",
                "covered_predecessor_relation_refs",
                "new_current_relation_refs",
                "findings",
                "status",
                "closure_ready",
                *_AUTHORITY_FIELDS,
            },
            "stage relation inheritance receipt",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageRelationInheritanceError(
                "unsupported stage relation inheritance receipt schema"
            )
        for field in (
            "coverage",
            "predecessor_relation_refs",
            "covered_predecessor_relation_refs",
            "new_current_relation_refs",
            "findings",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            predecessor_branch=branch_ref_from_dict(
                payload["predecessor_branch"]
            ),
            current_branch=branch_ref_from_dict(payload["current_branch"]),
            predecessor_stage_id=payload["predecessor_stage_id"],
            current_stage_id=payload["current_stage_id"],
            predecessor_graph_digest=payload["predecessor_graph_digest"],
            current_graph_digest=payload["current_graph_digest"],
            predecessor_stage_subject_digest=payload[
                "predecessor_stage_subject_digest"
            ],
            current_stage_subject_digest=payload[
                "current_stage_subject_digest"
            ],
            realization_manifest_digest=payload[
                "realization_manifest_digest"
            ],
            realization_receipt_digest=payload[
                "realization_receipt_digest"
            ],
            realization_status=CheckStatus(payload["realization_status"]),
            coverage=tuple(
                StageRelationInheritanceCoverage.from_dict(item)
                for item in payload["coverage"]
            ),
            new_current_relation_refs=tuple(
                payload["new_current_relation_refs"]
            ),
            findings=tuple(
                CheckFinding.from_dict(item) for item in payload["findings"]
            ),
            status=CheckStatus(payload["status"]),
        )
        if result.to_dict() != payload:
            raise StageRelationInheritanceError(
                "stage relation inheritance receipt is not canonical"
            )
        return result


def _same_branch_lineage(left: BranchRef, right: BranchRef) -> bool:
    return left.run == right.run and left.branch_id == right.branch_id


def _relation_signature(
    relation: ArchitecturalRelation,
    graph: ArchitecturalRelationGraph,
    *,
    resolve_predecessor_nodes: bool,
) -> tuple[object, ...]:
    nodes = {item.node_ref: item for item in graph.nodes}
    participants = []
    for participant in relation.participants:
        node = nodes[participant.node_ref]
        resolved_ref = (
            node.predecessor_ref
            if resolve_predecessor_nodes and node.predecessor_ref is not None
            else node.node_ref
        )
        participants.append(
            (
                participant.role,
                participant.ordinal,
                resolved_ref,
                node.node_kind.value,
                node.semantic_kind,
            )
        )
    return (
        relation.kind.value,
        relation.scenario_ref,
        tuple(sorted(participants)),
        tuple(item.identity for item in relation.propagation_rules),
    )


def stage_relation_inheritance_denominator(
    predecessor: AcceptedStageRelationPredecessor,
    current_graph: ArchitecturalRelationGraph,
    current_manifest: RelationRealizationManifest,
    current_realization_receipt: CheckReceiptEnvelope,
) -> tuple[str, ...]:
    """Return the exact graph/manifest/receipt denominator for one join."""

    if not isinstance(predecessor, AcceptedStageRelationPredecessor):
        raise TypeError(
            "predecessor must be AcceptedStageRelationPredecessor"
        )
    predecessor_graph = predecessor.graph
    if not isinstance(current_graph, ArchitecturalRelationGraph):
        raise TypeError("current_graph must be ArchitecturalRelationGraph")
    if not isinstance(current_manifest, RelationRealizationManifest):
        raise TypeError("current_manifest must be RelationRealizationManifest")
    if not isinstance(current_realization_receipt, CheckReceiptEnvelope):
        raise TypeError(
            "current_realization_receipt must be CheckReceiptEnvelope"
        )
    return tuple(
        sorted(
            {
                predecessor.ref,
                predecessor_graph.ref,
                current_graph.ref,
                current_manifest.ref,
                current_manifest.graph_ref,
                current_manifest.stage_subject_ref,
                (
                    "stage-relation-predecessor-graph-record:"
                    f"{predecessor_graph.graph_digest}"
                ),
                (
                    "stage-relation-current-graph-record:"
                    f"{current_graph.graph_digest}"
                ),
                (
                    "stage-relation-realization-manifest-record:"
                    f"{current_manifest.manifest_digest}"
                ),
                (
                    "stage-relation-realization-receipt-record:"
                    f"{current_realization_receipt.receipt_digest}"
                ),
                f"check-receipt:{current_realization_receipt.receipt_digest}",
                *(item.ref for item in predecessor_graph.relations),
                *(item.ref for item in current_graph.relations),
                *(
                    f"architectural-relation-record:{item.relation_digest}"
                    for item in predecessor_graph.relations
                ),
                *(
                    f"architectural-relation-record:{item.relation_digest}"
                    for item in current_graph.relations
                ),
                *(
                    item.predecessor_relation_ref
                    for item in current_graph.relations
                    if item.predecessor_relation_ref is not None
                ),
            }
        )
    )


def bridge_stage_relation_inheritance_receipt(
    predecessor: AcceptedStageRelationPredecessor,
    current_graph: ArchitecturalRelationGraph,
    current_manifest: RelationRealizationManifest,
    current_realization_receipt: CheckReceiptEnvelope,
    receipt: StageRelationInheritanceReceipt,
) -> CheckReceiptEnvelope:
    """Bridge the typed join into composite stage-closure vocabulary."""

    if not isinstance(receipt, StageRelationInheritanceReceipt):
        raise TypeError("receipt must be StageRelationInheritanceReceipt")
    if not isinstance(predecessor, AcceptedStageRelationPredecessor):
        raise TypeError(
            "predecessor must be AcceptedStageRelationPredecessor"
        )
    predecessor_graph = predecessor.graph
    denominator = stage_relation_inheritance_denominator(
        predecessor,
        current_graph,
        current_manifest,
        current_realization_receipt,
    )
    expected = compile_stage_relation_inheritance(
        predecessor_graph,
        current_graph,
        current_manifest,
        current_realization_receipt,
    )
    if receipt != expected:
        raise StageRelationInheritanceError(
            "stage relation inheritance receipt changed before bridging"
        )
    return CheckReceiptEnvelope(
        check_id=STAGE_RELATION_INHERITANCE_CHECK_ID,
        checker_id=STAGE_RELATION_INHERITANCE_CHECKER_ID,
        checker_version="1.0.0",
        branch=current_graph.branch,
        scope_digest=current_graph.scope_digest,
        subject_refs=denominator,
        subject_digest=current_graph.stage_subject_digest,
        status=receipt.status,
        source_refs=tuple(
            sorted(
                {
                    predecessor.ref,
                    predecessor_graph.ref,
                    current_graph.ref,
                    current_manifest.ref,
                    (
                        "check-receipt:"
                        f"{current_realization_receipt.receipt_digest}"
                    ),
                }
            )
        ),
        findings=receipt.findings,
        coverage_denominator=denominator,
        covered_refs=(denominator if receipt.status is CheckStatus.PASS else ()),
    )


def compile_stage_relation_inheritance(
    predecessor_graph: ArchitecturalRelationGraph,
    current_graph: ArchitecturalRelationGraph,
    current_manifest: RelationRealizationManifest,
    current_realization_receipt: CheckReceiptEnvelope,
) -> StageRelationInheritanceReceipt:
    """Compile exact predecessor coverage and fresh current-stage revalidation.

    Current relations with no predecessor are permitted only when they carry
    both evidence and authority.  Geometry-operation inputs are deliberately
    ignored; only explicit ``predecessor_relation_ref`` values participate.
    """

    if not isinstance(predecessor_graph, ArchitecturalRelationGraph):
        raise TypeError("predecessor_graph must be ArchitecturalRelationGraph")
    if not isinstance(current_graph, ArchitecturalRelationGraph):
        raise TypeError("current_graph must be ArchitecturalRelationGraph")
    if not isinstance(current_manifest, RelationRealizationManifest):
        raise TypeError("current_manifest must be RelationRealizationManifest")
    if not isinstance(current_realization_receipt, CheckReceiptEnvelope):
        raise TypeError(
            "current_realization_receipt must be CheckReceiptEnvelope"
        )

    findings: list[CheckFinding] = []

    def add(
        code: str,
        message: str,
        *subject_refs: str,
        severity: FindingSeverity = FindingSeverity.ERROR,
    ) -> None:
        findings.append(
            CheckFinding(
                code=code,
                severity=severity,
                message=message,
                subject_refs=tuple(sorted(set(subject_refs))),
            )
        )

    if not _same_branch_lineage(
        predecessor_graph.branch,
        current_graph.branch,
    ):
        add(
            "stage-relation-cross-branch-lineage",
            "predecessor and current relation graphs crossed run or branch lineage",
            predecessor_graph.ref,
            current_graph.ref,
        )
    elif current_graph.branch.epoch < predecessor_graph.branch.epoch:
        add(
            "stage-relation-epoch-regression",
            "current relation graph precedes the predecessor branch epoch",
            predecessor_graph.ref,
            current_graph.ref,
        )
    if predecessor_graph.stage_id == current_graph.stage_id:
        add(
            "stage-relation-stage-not-advanced",
            "relation inheritance requires distinct predecessor and current stages",
            predecessor_graph.ref,
            current_graph.ref,
        )

    for relation in predecessor_graph.relations:
        if relation.source_stage_id != predecessor_graph.stage_id:
            add(
                "stage-relation-predecessor-source-stage-mismatch",
                "predecessor relation source stage differs from its graph stage",
                predecessor_graph.ref,
                relation.ref,
            )
    for relation in current_graph.relations:
        if relation.source_stage_id != current_graph.stage_id:
            add(
                "stage-relation-current-source-stage-mismatch",
                "current relation source stage differs from its graph stage",
                current_graph.ref,
                relation.ref,
            )

    predecessor_by_ref = {
        item.ref: item for item in predecessor_graph.relations
    }
    claims: dict[str, list[ArchitecturalRelation]] = {
        ref: [] for ref in predecessor_by_ref
    }
    new_current: list[ArchitecturalRelation] = []
    for relation in current_graph.relations:
        predecessor_ref = relation.predecessor_relation_ref
        if predecessor_ref is None:
            new_current.append(relation)
            if not relation.evidence_refs or not relation.authority_refs:
                add(
                    "stage-relation-new-basis-missing",
                    "a new current relation requires both evidence and authority",
                    relation.ref,
                )
            continue
        if predecessor_ref not in predecessor_by_ref:
            add(
                "stage-relation-predecessor-unknown",
                "current relation names a predecessor outside the exact predecessor graph",
                relation.ref,
                predecessor_ref,
            )
            continue
        claims[predecessor_ref].append(relation)

    coverage: list[StageRelationInheritanceCoverage] = []
    for predecessor in predecessor_graph.relations:
        successors = tuple(
            sorted(claims[predecessor.ref], key=lambda item: item.ref)
        )
        if not successors:
            disposition = RelationInheritanceDisposition.MISSING
            add(
                "stage-relation-predecessor-missing",
                "predecessor relation has no exact current successor",
                predecessor.ref,
            )
        elif len(successors) > 1:
            disposition = RelationInheritanceDisposition.DUPLICATE
            add(
                "stage-relation-predecessor-duplicate",
                "predecessor relation is claimed by more than one current relation",
                predecessor.ref,
                *(item.ref for item in successors),
            )
        else:
            successor = successors[0]
            retained = _relation_signature(
                predecessor,
                predecessor_graph,
                resolve_predecessor_nodes=False,
            ) == _relation_signature(
                successor,
                current_graph,
                resolve_predecessor_nodes=True,
            )
            disposition = (
                RelationInheritanceDisposition.RETAINED
                if retained
                else RelationInheritanceDisposition.REFINED
            )
            if not retained and (
                not successor.evidence_refs
                or not successor.authority_refs
            ):
                add(
                    "stage-relation-refinement-basis-missing",
                    "a refined current relation requires both evidence and authority",
                    predecessor.ref,
                    successor.ref,
                )
        coverage.append(
            StageRelationInheritanceCoverage(
                predecessor_relation_ref=predecessor.ref,
                predecessor_relation_digest=predecessor.relation_digest,
                current_relation_refs=tuple(item.ref for item in successors),
                current_relation_digests=tuple(
                    item.relation_digest for item in successors
                ),
                disposition=disposition,
            )
        )

    if current_manifest.branch != current_graph.branch:
        add(
            "stage-relation-manifest-branch-mismatch",
            "current realization manifest crossed the exact current branch",
            current_graph.ref,
            current_manifest.ref,
        )
    if current_manifest.stage_id != current_graph.stage_id:
        add(
            "stage-relation-manifest-stage-mismatch",
            "current realization manifest crossed the exact current stage",
            current_graph.ref,
            current_manifest.ref,
        )
    if current_manifest.scope_digest != current_graph.scope_digest:
        add(
            "stage-relation-manifest-scope-mismatch",
            "current realization manifest crossed the exact current scope",
            current_graph.ref,
            current_manifest.ref,
        )
    if current_manifest.relation_graph_digest != current_graph.graph_digest:
        add(
            "stage-relation-manifest-graph-digest-mismatch",
            "current realization manifest does not bind the exact current graph",
            current_graph.ref,
            current_manifest.graph_ref,
        )
    if (
        current_manifest.stage_subject_digest
        != current_graph.stage_subject_digest
    ):
        add(
            "stage-relation-manifest-subject-digest-mismatch",
            "current realization manifest crossed the exact current stage subject",
            current_graph.ref,
            current_manifest.stage_subject_ref,
        )

    expected_denominator = relation_realization_denominator(
        current_graph,
        current_manifest,
    )
    receipt_ref = (
        f"check-receipt:{current_realization_receipt.receipt_digest}"
    )
    receipt_identity_matches = (
        current_realization_receipt.check_id == current_manifest.check_id
        and current_realization_receipt.checker_id
        == RELATION_REALIZATION_CHECKER_ID
        and current_realization_receipt.branch == current_graph.branch
        and current_realization_receipt.scope_digest
        == current_graph.scope_digest
        and current_realization_receipt.subject_digest
        == current_graph.stage_subject_digest
        and current_realization_receipt.subject_refs == expected_denominator
        and current_realization_receipt.coverage_denominator
        == expected_denominator
        and not current_realization_receipt.revalidation_refs
    )
    if not receipt_identity_matches:
        add(
            "stage-relation-current-receipt-stale",
            "realization receipt is not bound to the exact current graph, manifest, branch, scope, and subject",
            current_graph.ref,
            current_manifest.ref,
            receipt_ref,
        )
    elif current_realization_receipt.status is CheckStatus.FAIL:
        add(
            "stage-relation-current-realization-failed",
            "current relation realization failed and cannot close inheritance",
            current_graph.ref,
            receipt_ref,
        )
    elif current_realization_receipt.status is not CheckStatus.PASS:
        add(
            "stage-relation-current-realization-open",
            "current relation realization is not PASS and cannot close inheritance",
            current_graph.ref,
            receipt_ref,
            severity=FindingSeverity.UNKNOWN,
        )

    finding_map = {
        (item.code, item.subject_refs, item.message): item for item in findings
    }
    ordered_findings = tuple(
        sorted(
            finding_map.values(),
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )
    has_error = any(
        item.severity is FindingSeverity.ERROR for item in ordered_findings
    )
    has_unknown = any(
        item.severity is FindingSeverity.UNKNOWN for item in ordered_findings
    )
    status = (
        CheckStatus.FAIL
        if has_error
        else CheckStatus.UNKNOWN
        if has_unknown
        else CheckStatus.PASS
    )
    return StageRelationInheritanceReceipt(
        predecessor_branch=predecessor_graph.branch,
        current_branch=current_graph.branch,
        predecessor_stage_id=predecessor_graph.stage_id,
        current_stage_id=current_graph.stage_id,
        predecessor_graph_digest=predecessor_graph.graph_digest,
        current_graph_digest=current_graph.graph_digest,
        predecessor_stage_subject_digest=(
            predecessor_graph.stage_subject_digest
        ),
        current_stage_subject_digest=current_graph.stage_subject_digest,
        realization_manifest_digest=current_manifest.manifest_digest,
        realization_receipt_digest=(
            current_realization_receipt.receipt_digest
        ),
        realization_status=current_realization_receipt.status,
        coverage=tuple(
            sorted(
                coverage,
                key=lambda item: item.predecessor_relation_ref,
            )
        ),
        new_current_relation_refs=tuple(
            sorted(item.ref for item in new_current)
        ),
        findings=ordered_findings,
        status=status,
    )


__all__ = [
    "AcceptedRelationTopologyIdentity",
    "AcceptedStageRelationPredecessor",
    "RelationInheritanceDisposition",
    "STAGE_RELATION_INHERITANCE_CHECKER_ID",
    "STAGE_RELATION_INHERITANCE_CHECK_ID",
    "StageRelationInheritanceCoverage",
    "StageRelationInheritanceError",
    "StageRelationInheritanceReceipt",
    "bridge_stage_relation_inheritance_receipt",
    "compile_stage_relation_inheritance",
    "stage_relation_inheritance_denominator",
]
