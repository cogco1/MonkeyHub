"""Nested, branch-local operational design state.

The tree keeps a global concept root while allowing each stage, discipline,
component, and detail node to retain only its own future-relevant operational
state. Context compilation projects one root-to-node path plus explicit
cross-node interfaces; unrelated node state and full event history are absent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import BranchRef
from archflow.state.commitments import Commitment, CommitmentStatus
from archflow.state.decision_operator import (
    CompiledDecisionTransition,
    DecisionOperator,
    compile_decision_operator,
)
from archflow.state.operational_state import (
    DependencyEffect,
    DesignObligation,
    ObligationStatus,
    OperationalMarkovState,
    StateFact,
    require_local_id,
    require_logical_ref,
)


_MAX_ITEMS = 4096
_HEX = frozenset("0123456789abcdef")


class DesignStateError(ValueError):
    """A nested state tree or context projection is invalid."""


class DesignStateLayer(StrEnum):
    GLOBAL_CONCEPT = "global_concept"
    PHASE = "phase"
    DISCIPLINE = "discipline"
    COMPONENT = "component"
    DETAIL = "detail"


DESIGN_STATE_LAYERS: tuple[DesignStateLayer, ...] = tuple(
    DesignStateLayer
)
_LAYER_INDEX = {
    layer: index for index, layer in enumerate(DESIGN_STATE_LAYERS)
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignStateError(f"{field} must be non-empty text")
    return value


def _tuple(value: object, field: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise DesignStateError(f"{field} exceeds bounded item count")
    return value


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise DesignStateError(f"{field} contains duplicates")


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    digest = value.lower()
    if len(digest) != 64 or any(char not in _HEX for char in digest):
        raise DesignStateError(f"{field} must be a SHA-256 digest")
    return digest


def _branch_identity(branch: BranchRef) -> tuple[object, ...]:
    return (
        branch.run.project_id,
        branch.run.run_id,
        branch.run.base,
        branch.branch_id,
    )


def _branch_to_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.require_digest(),
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _branch_from_dict(value: object) -> BranchRef:
    from archflow.project.refs import ProjectVersionRef, RunRef

    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "run_id",
        "base",
        "branch_id",
        "epoch",
    }:
        raise DesignStateError("branch schema drifted")
    base = value["base"]
    if not isinstance(base, Mapping) or set(base) != {
        "version",
        "state_sha256",
    }:
        raise DesignStateError("branch base schema drifted")
    project_id = value["project_id"]
    try:
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
    except (TypeError, ValueError) as exc:
        raise DesignStateError("branch is invalid") from exc


@dataclass(frozen=True, slots=True)
class StatePathSegment:
    layer: DesignStateLayer
    node_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.layer, DesignStateLayer):
            raise TypeError("layer must be a DesignStateLayer")
        require_local_id(self.node_id, "node_id")

    def to_dict(self) -> dict[str, str]:
        return {
            "layer": self.layer.value,
            "node_id": self.node_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> StatePathSegment:
        if not isinstance(value, Mapping) or set(value) != {
            "layer",
            "node_id",
        }:
            raise DesignStateError("state path segment schema drifted")
        return cls(
            layer=DesignStateLayer(value["layer"]),
            node_id=value["node_id"],
        )


@dataclass(frozen=True, slots=True)
class StatePath:
    segments: tuple[StatePathSegment, ...]

    def __post_init__(self) -> None:
        _tuple(self.segments, "segments")
        if not self.segments:
            raise DesignStateError("state path cannot be empty")
        if len(self.segments) > len(DESIGN_STATE_LAYERS):
            raise DesignStateError("state path exceeds design hierarchy")
        if any(
            not isinstance(item, StatePathSegment)
            for item in self.segments
        ):
            raise TypeError("segments must contain StatePathSegment")
        if self.segments[0].layer is not DesignStateLayer.GLOBAL_CONCEPT:
            raise DesignStateError(
                "state path must start at global concept"
            )
        for expected_index, segment in enumerate(self.segments):
            if _LAYER_INDEX[segment.layer] != expected_index:
                raise DesignStateError(
                    "state path must follow the hierarchy without skipping"
                )
        _unique(
            tuple(item.node_id for item in self.segments),
            "state path node ids",
        )

    @property
    def layer(self) -> DesignStateLayer:
        return self.segments[-1].layer

    @property
    def node_id(self) -> str:
        return self.segments[-1].node_id

    @property
    def ref(self) -> str:
        return f"design-state:{_digest(self.to_dict())}"

    @property
    def parent_ref(self) -> str | None:
        if len(self.segments) == 1:
            return None
        return StatePath(self.segments[:-1]).ref

    def is_ancestor_of(self, other: StatePath) -> bool:
        if not isinstance(other, StatePath):
            raise TypeError("other must be a StatePath")
        return (
            len(self.segments) < len(other.segments)
            and other.segments[: len(self.segments)] == self.segments
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "segments": [item.to_dict() for item in self.segments],
        }

    @classmethod
    def from_dict(cls, value: object) -> StatePath:
        if not isinstance(value, Mapping) or set(value) != {"segments"}:
            raise DesignStateError("state path schema drifted")
        segments = value["segments"]
        if not isinstance(segments, list):
            raise TypeError("state path segments must be a list")
        return cls(
            tuple(StatePathSegment.from_dict(item) for item in segments)
        )


@dataclass(frozen=True, slots=True)
class DesignStateNode:
    path: StatePath
    operational_state: OperationalMarkovState
    allowed_authority_ids: tuple[str, ...]
    child_refs: tuple[str, ...] = ()
    phase_deliverable_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.path, StatePath):
            raise TypeError("path must be a StatePath")
        if not isinstance(self.operational_state, OperationalMarkovState):
            raise TypeError(
                "operational_state must be an OperationalMarkovState"
            )
        _tuple(self.allowed_authority_ids, "allowed_authority_ids")
        if not self.allowed_authority_ids:
            raise DesignStateError(
                "node requires at least one mutation authority"
            )
        for authority_id in self.allowed_authority_ids:
            require_local_id(authority_id, "allowed authority id")
        _unique(
            self.allowed_authority_ids,
            "allowed_authority_ids",
        )
        _tuple(self.child_refs, "child_refs")
        for ref in self.child_refs:
            require_logical_ref(ref, "child_ref")
        _unique(self.child_refs, "child_refs")
        _tuple(
            self.phase_deliverable_refs,
            "phase_deliverable_refs",
        )
        for ref in self.phase_deliverable_refs:
            require_logical_ref(ref, "phase_deliverable_ref")
        _unique(
            self.phase_deliverable_refs,
            "phase_deliverable_refs",
        )
        phase_segments = tuple(
            item
            for item in self.path.segments
            if item.layer is DesignStateLayer.PHASE
        )
        if (
            phase_segments
            and self.operational_state.phase
            != phase_segments[0].node_id
        ):
            raise DesignStateError(
                "node operational phase disagrees with state path"
            )

    @property
    def ref(self) -> str:
        return self.path.ref

    @property
    def parent_ref(self) -> str | None:
        return self.path.parent_ref

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path.to_dict(),
            "operational_state": self.operational_state.to_dict(),
            "allowed_authority_ids": list(
                self.allowed_authority_ids
            ),
            "child_refs": list(self.child_refs),
            "phase_deliverable_refs": list(
                self.phase_deliverable_refs
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignStateNode:
        if not isinstance(value, Mapping) or set(value) != {
            "path",
            "operational_state",
            "allowed_authority_ids",
            "child_refs",
            "phase_deliverable_refs",
        }:
            raise DesignStateError("design state node schema drifted")
        authorities = value["allowed_authority_ids"]
        child_refs = value["child_refs"]
        deliverable_refs = value["phase_deliverable_refs"]
        if not isinstance(authorities, list) or not isinstance(
            child_refs,
            list,
        ) or not isinstance(deliverable_refs, list):
            raise TypeError("node tuple fields must be lists")
        return cls(
            path=StatePath.from_dict(value["path"]),
            operational_state=OperationalMarkovState.from_dict(
                value["operational_state"]
            ),
            allowed_authority_ids=tuple(authorities),
            child_refs=tuple(child_refs),
            phase_deliverable_refs=tuple(deliverable_refs),
        )


@dataclass(frozen=True, slots=True)
class InterfaceConstraint:
    interface_id: str
    source_node_ref: str
    target_node_ref: str
    statement: str
    source_refs: tuple[str, ...]
    target_refs: tuple[str, ...]
    effect: DependencyEffect
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.interface_id, "interface_id")
        require_logical_ref(self.source_node_ref, "source_node_ref")
        require_logical_ref(self.target_node_ref, "target_node_ref")
        if self.source_node_ref == self.target_node_ref:
            raise DesignStateError(
                "interface cannot connect a node to itself"
            )
        _text(self.statement, "interface statement")
        for field, values in (
            ("source_refs", self.source_refs),
            ("target_refs", self.target_refs),
            ("evidence_refs", self.evidence_refs),
        ):
            _tuple(values, field)
            if not values:
                raise DesignStateError(f"{field} cannot be empty")
            for ref in values:
                require_logical_ref(ref, field)
            _unique(values, field)
        if self.effect not in {
            DependencyEffect.INVALIDATES,
            DependencyEffect.REQUIRES_REVALIDATION,
            DependencyEffect.SUPPORTS_ONLY,
        }:
            raise DesignStateError(
                "interface effect must be invalidation, revalidation, "
                "or support-only"
            )

    @property
    def ref(self) -> str:
        return f"design-interface:{self.interface_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "interface_id": self.interface_id,
            "source_node_ref": self.source_node_ref,
            "target_node_ref": self.target_node_ref,
            "statement": self.statement,
            "source_refs": list(self.source_refs),
            "target_refs": list(self.target_refs),
            "effect": self.effect.value,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> InterfaceConstraint:
        if not isinstance(value, Mapping) or set(value) != {
            "interface_id",
            "source_node_ref",
            "target_node_ref",
            "statement",
            "source_refs",
            "target_refs",
            "effect",
            "evidence_refs",
        }:
            raise DesignStateError("interface constraint schema drifted")
        tuple_fields = {}
        for field in ("source_refs", "target_refs", "evidence_refs"):
            raw = value[field]
            if not isinstance(raw, list):
                raise TypeError(f"{field} must be a list")
            tuple_fields[field] = tuple(raw)
        return cls(
            interface_id=value["interface_id"],
            source_node_ref=value["source_node_ref"],
            target_node_ref=value["target_node_ref"],
            statement=value["statement"],
            source_refs=tuple_fields["source_refs"],
            target_refs=tuple_fields["target_refs"],
            effect=DependencyEffect(value["effect"]),
            evidence_refs=tuple_fields["evidence_refs"],
        )


@dataclass(frozen=True, slots=True)
class DesignStateTree:
    branch: BranchRef
    nodes: tuple[DesignStateNode, ...]
    interfaces: tuple[InterfaceConstraint, ...] = ()

    SCHEMA = "DesignStateTree@1"

    def __post_init__(self) -> None:
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        _tuple(self.nodes, "nodes")
        if not self.nodes:
            raise DesignStateError("design state tree cannot be empty")
        if any(
            not isinstance(item, DesignStateNode)
            for item in self.nodes
        ):
            raise TypeError("nodes must contain DesignStateNode")
        _tuple(self.interfaces, "interfaces")
        if any(
            not isinstance(item, InterfaceConstraint)
            for item in self.interfaces
        ):
            raise TypeError(
                "interfaces must contain InterfaceConstraint"
            )
        object.__setattr__(
            self,
            "nodes",
            tuple(sorted(self.nodes, key=lambda item: item.ref)),
        )
        object.__setattr__(
            self,
            "interfaces",
            tuple(
                sorted(
                    self.interfaces,
                    key=lambda item: item.interface_id,
                )
            ),
        )
        refs = tuple(item.ref for item in self.nodes)
        _unique(refs, "node refs")
        by_ref = {item.ref: item for item in self.nodes}
        roots = [
            item
            for item in self.nodes
            if item.path.layer is DesignStateLayer.GLOBAL_CONCEPT
        ]
        if len(roots) != 1:
            raise DesignStateError(
                "tree requires exactly one global concept root"
            )
        for node in self.nodes:
            if _branch_identity(node.operational_state.branch) != (
                _branch_identity(self.branch)
            ):
                raise DesignStateError(
                    "node belongs to another run or branch"
                )
            if node.operational_state.branch.epoch > self.branch.epoch:
                raise DesignStateError(
                    "node operational state originates in the future"
                )
            if node.parent_ref is not None:
                parent = by_ref.get(node.parent_ref)
                if parent is None:
                    raise DesignStateError("node parent is missing")
                if node.ref not in parent.child_refs:
                    raise DesignStateError(
                        "parent child_refs omit the child"
                    )
                if not set(node.allowed_authority_ids) <= set(
                    parent.allowed_authority_ids
                ):
                    raise DesignStateError(
                        "child mutation authority cannot widen"
                    )
            for child_ref in node.child_refs:
                child = by_ref.get(child_ref)
                if child is None or child.parent_ref != node.ref:
                    raise DesignStateError(
                        "child_refs must match exact path parent"
                    )
        _unique(
            tuple(item.interface_id for item in self.interfaces),
            "interface ids",
        )
        for interface in self.interfaces:
            if (
                interface.source_node_ref not in by_ref
                or interface.target_node_ref not in by_ref
            ):
                raise DesignStateError(
                    "interface endpoint is not a known node"
                )
        owned_fact_refs: dict[str, str] = {}
        owned_commitments: dict[str, str] = {}
        owned_obligations: dict[str, str] = {}
        for node in self.nodes:
            for fact in node.operational_state.facts:
                _claim_owner(owned_fact_refs, fact.ref, node.ref, "fact")
            for commitment in node.operational_state.commitments:
                _claim_owner(
                    owned_commitments,
                    commitment.commitment_id,
                    node.ref,
                    "commitment",
                )
            for obligation in node.operational_state.obligations:
                _claim_owner(
                    owned_obligations,
                    obligation.obligation_id,
                    node.ref,
                    "obligation",
                )

    @property
    def root(self) -> DesignStateNode:
        return next(
            item
            for item in self.nodes
            if item.path.layer is DesignStateLayer.GLOBAL_CONCEPT
        )

    @property
    def tree_digest(self) -> str:
        return _digest(self.to_dict())

    def node(self, node_ref: str) -> DesignStateNode:
        for item in self.nodes:
            if item.ref == node_ref:
                return item
        raise DesignStateError(f"unknown design-state node: {node_ref}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch": _branch_to_dict(self.branch),
            "nodes": [
                item.to_dict()
                for item in sorted(
                    self.nodes,
                    key=lambda node: node.ref,
                )
            ],
            "interfaces": [
                item.to_dict()
                for item in sorted(
                    self.interfaces,
                    key=lambda interface: interface.interface_id,
                )
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignStateTree:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "branch",
            "nodes",
            "interfaces",
        }:
            raise DesignStateError("design state tree schema drifted")
        if value["schema"] != cls.SCHEMA:
            raise DesignStateError("unsupported design state tree schema")
        nodes = value["nodes"]
        interfaces = value["interfaces"]
        if not isinstance(nodes, list) or not isinstance(
            interfaces,
            list,
        ):
            raise TypeError("tree collection fields must be lists")
        return cls(
            branch=_branch_from_dict(value["branch"]),
            nodes=tuple(
                DesignStateNode.from_dict(item) for item in nodes
            ),
            interfaces=tuple(
                InterfaceConstraint.from_dict(item)
                for item in interfaces
            ),
        )


@dataclass(frozen=True, slots=True)
class PhaseTreeTransition:
    """A deterministic whole-tree phase projection and ref remap."""

    tree: DesignStateTree
    previous_phase: str
    next_phase: str
    node_ref_map: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.tree, DesignStateTree):
            raise TypeError("tree must be a DesignStateTree")
        require_local_id(self.previous_phase, "previous_phase")
        require_local_id(self.next_phase, "next_phase")
        _tuple(self.node_ref_map, "node_ref_map")
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or any(not isinstance(ref, str) for ref in item)
            for item in self.node_ref_map
        ):
            raise TypeError(
                "node_ref_map must contain reference pairs"
            )
        for old_ref, new_ref in self.node_ref_map:
            require_logical_ref(old_ref, "old node ref")
            require_logical_ref(new_ref, "new node ref")
        _unique(
            tuple(item[0] for item in self.node_ref_map),
            "old node refs",
        )
        _unique(
            tuple(item[1] for item in self.node_ref_map),
            "new node refs",
        )

    def remap(self, node_ref: str) -> str:
        for old_ref, new_ref in self.node_ref_map:
            if old_ref == node_ref:
                return new_ref
        raise DesignStateError(
            f"phase transition omitted node ref: {node_ref}"
        )


def compile_tree_phase_change(
    tree: DesignStateTree,
    *,
    next_phase: str,
    obligation_target_ref: str | None = None,
    add_obligations: tuple[DesignObligation, ...] = (),
) -> PhaseTreeTransition:
    """Compile a new phase projection without retaining stale path labels."""

    if not isinstance(tree, DesignStateTree):
        raise TypeError("tree must be a DesignStateTree")
    require_local_id(next_phase, "next_phase")
    _tuple(add_obligations, "add_obligations")
    if any(
        not isinstance(item, DesignObligation)
        for item in add_obligations
    ):
        raise TypeError(
            "add_obligations must contain DesignObligation"
        )
    current_phases = {
        item.operational_state.phase for item in tree.nodes
    }
    path_phases = {
        segment.node_id
        for node in tree.nodes
        for segment in node.path.segments
        if segment.layer is DesignStateLayer.PHASE
    }
    if len(current_phases) != 1 or len(path_phases) != 1:
        raise DesignStateError(
            "phase change requires one current operational phase"
        )
    previous_phase = next(iter(current_phases))
    if path_phases != {previous_phase}:
        raise DesignStateError(
            "operational phase disagrees with phase path"
        )
    if next_phase == previous_phase:
        raise DesignStateError(
            "phase change must name a different phase"
        )
    if add_obligations and obligation_target_ref is None:
        raise DesignStateError(
            "phase obligations require an explicit target node"
        )
    if obligation_target_ref is not None:
        tree.node(obligation_target_ref)

    next_branch = replace(
        tree.branch,
        epoch=tree.branch.epoch + 1,
    )

    def rephase_path(path: StatePath) -> StatePath:
        return StatePath(
            tuple(
                replace(segment, node_id=next_phase)
                if segment.layer is DesignStateLayer.PHASE
                else segment
                for segment in path.segments
            )
        )

    path_by_old_ref = {
        node.ref: rephase_path(node.path) for node in tree.nodes
    }
    ref_map = {
        old_ref: path.ref
        for old_ref, path in path_by_old_ref.items()
    }
    obligation_ids = tuple(
        item.obligation_id for item in add_obligations
    )
    _unique(obligation_ids, "added obligation ids")
    next_nodes = []
    for node in tree.nodes:
        obligations = node.operational_state.obligations
        if node.ref == obligation_target_ref:
            existing_ids = {
                item.obligation_id for item in obligations
            }
            overlap = existing_ids.intersection(obligation_ids)
            if overlap:
                raise DesignStateError(
                    "phase obligations already exist: "
                    f"{sorted(overlap)}"
                )
            obligations = tuple(
                sorted(
                    (*obligations, *add_obligations),
                    key=lambda item: item.obligation_id,
                )
            )
        next_nodes.append(
            DesignStateNode(
                path=path_by_old_ref[node.ref],
                operational_state=replace(
                    node.operational_state,
                    branch=next_branch,
                    phase=next_phase,
                    obligations=obligations,
                ),
                allowed_authority_ids=node.allowed_authority_ids,
                child_refs=tuple(
                    ref_map[item] for item in node.child_refs
                ),
                phase_deliverable_refs=(),
            )
        )
    next_interfaces = tuple(
        replace(
            item,
            source_node_ref=ref_map[item.source_node_ref],
            target_node_ref=ref_map[item.target_node_ref],
        )
        for item in tree.interfaces
    )
    next_tree = DesignStateTree(
        branch=next_branch,
        nodes=tuple(next_nodes),
        interfaces=next_interfaces,
    )
    return PhaseTreeTransition(
        tree=next_tree,
        previous_phase=previous_phase,
        next_phase=next_phase,
        node_ref_map=tuple(sorted(ref_map.items())),
    )


def _claim_owner(
    owners: dict[str, str],
    item_id: str,
    node_ref: str,
    item_kind: str,
) -> None:
    previous = owners.get(item_id)
    if previous is not None:
        raise DesignStateError(
            f"{item_kind} is owned by multiple nodes: {item_id}"
        )
    owners[item_id] = node_ref


@dataclass(frozen=True, slots=True)
class ContextSlice:
    tree_digest: str
    target_node_ref: str
    target_state_digest: str
    path: StatePath
    ancestor_node_refs: tuple[str, ...]
    concept_facts: tuple[StateFact, ...]
    ancestor_facts: tuple[StateFact, ...]
    local_facts: tuple[StateFact, ...]
    commitments: tuple[Commitment, ...]
    obligations: tuple[DesignObligation, ...]
    phase_deliverable_refs: tuple[str, ...]
    interfaces: tuple[InterfaceConstraint, ...]
    allowed_authority_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    omitted_node_refs: tuple[str, ...]

    SCHEMA = "ContextSlice@1"

    def __post_init__(self) -> None:
        _sha256(self.tree_digest, "tree_digest")
        require_logical_ref(self.target_node_ref, "target_node_ref")
        _sha256(self.target_state_digest, "target_state_digest")
        if not isinstance(self.path, StatePath):
            raise TypeError("path must be a StatePath")
        for field, values, item_type in (
            ("concept_facts", self.concept_facts, StateFact),
            ("ancestor_facts", self.ancestor_facts, StateFact),
            ("local_facts", self.local_facts, StateFact),
            ("commitments", self.commitments, Commitment),
            ("obligations", self.obligations, DesignObligation),
            ("interfaces", self.interfaces, InterfaceConstraint),
        ):
            _tuple(values, field)
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"{field} contains invalid values")
        for field, values in (
            ("ancestor_node_refs", self.ancestor_node_refs),
            ("allowed_authority_ids", self.allowed_authority_ids),
            ("evidence_refs", self.evidence_refs),
            ("omitted_node_refs", self.omitted_node_refs),
            (
                "phase_deliverable_refs",
                self.phase_deliverable_refs,
            ),
        ):
            _tuple(values, field)
            _unique(values, field)
        for ref in self.phase_deliverable_refs:
            require_logical_ref(ref, "phase_deliverable_ref")

    @property
    def context_digest(self) -> str:
        return _digest(
            {
                "schema": self.SCHEMA,
                "tree_digest": self.tree_digest,
                "target_node_ref": self.target_node_ref,
                "target_state_digest": self.target_state_digest,
                "path": self.path.to_dict(),
                "ancestor_node_refs": self.ancestor_node_refs,
                "concept_fact_refs": tuple(
                    item.ref for item in self.concept_facts
                ),
                "ancestor_fact_refs": tuple(
                    item.ref for item in self.ancestor_facts
                ),
                "local_fact_refs": tuple(
                    item.ref for item in self.local_facts
                ),
                "commitment_ids": tuple(
                    item.commitment_id for item in self.commitments
                ),
                "obligation_ids": tuple(
                    item.obligation_id for item in self.obligations
                ),
                "phase_deliverable_refs": (
                    self.phase_deliverable_refs
                ),
                "interface_ids": tuple(
                    item.interface_id for item in self.interfaces
                ),
                "allowed_authority_ids": self.allowed_authority_ids,
                "evidence_refs": self.evidence_refs,
                "omitted_node_refs": self.omitted_node_refs,
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "tree_digest": self.tree_digest,
            "target_node_ref": self.target_node_ref,
            "target_state_digest": self.target_state_digest,
            "path": self.path.to_dict(),
            "ancestor_node_refs": list(self.ancestor_node_refs),
            "concept_facts": [
                item.to_dict() for item in self.concept_facts
            ],
            "ancestor_facts": [
                item.to_dict() for item in self.ancestor_facts
            ],
            "local_facts": [
                item.to_dict() for item in self.local_facts
            ],
            "commitments": [
                item.to_dict() for item in self.commitments
            ],
            "obligations": [
                item.to_dict() for item in self.obligations
            ],
            "phase_deliverable_refs": list(
                self.phase_deliverable_refs
            ),
            "interfaces": [
                item.to_dict() for item in self.interfaces
            ],
            "allowed_authority_ids": list(
                self.allowed_authority_ids
            ),
            "evidence_refs": list(self.evidence_refs),
            "omitted_node_refs": list(self.omitted_node_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> ContextSlice:
        if not isinstance(value, Mapping):
            raise DesignStateError("context slice must be an object")
        expected = {
            "schema",
            "tree_digest",
            "target_node_ref",
            "target_state_digest",
            "path",
            "ancestor_node_refs",
            "concept_facts",
            "ancestor_facts",
            "local_facts",
            "commitments",
            "obligations",
            "phase_deliverable_refs",
            "interfaces",
            "allowed_authority_ids",
            "evidence_refs",
            "omitted_node_refs",
        }
        if set(value) != expected or value["schema"] != cls.SCHEMA:
            raise DesignStateError("context slice schema drifted")

        def items(field: str) -> list[object]:
            raw = value[field]
            if not isinstance(raw, list):
                raise TypeError(f"{field} must be a list")
            return raw

        def refs(field: str) -> tuple[str, ...]:
            raw = items(field)
            if any(not isinstance(item, str) for item in raw):
                raise TypeError(f"{field} must contain text")
            return tuple(raw)

        return cls(
            tree_digest=value["tree_digest"],
            target_node_ref=value["target_node_ref"],
            target_state_digest=value["target_state_digest"],
            path=StatePath.from_dict(value["path"]),
            ancestor_node_refs=refs("ancestor_node_refs"),
            concept_facts=tuple(
                StateFact.from_dict(item)
                for item in items("concept_facts")
            ),
            ancestor_facts=tuple(
                StateFact.from_dict(item)
                for item in items("ancestor_facts")
            ),
            local_facts=tuple(
                StateFact.from_dict(item)
                for item in items("local_facts")
            ),
            commitments=tuple(
                Commitment.from_dict(item)
                for item in items("commitments")
            ),
            obligations=tuple(
                DesignObligation.from_dict(item)
                for item in items("obligations")
            ),
            phase_deliverable_refs=refs(
                "phase_deliverable_refs"
            ),
            interfaces=tuple(
                InterfaceConstraint.from_dict(item)
                for item in items("interfaces")
            ),
            allowed_authority_ids=refs(
                "allowed_authority_ids"
            ),
            evidence_refs=refs("evidence_refs"),
            omitted_node_refs=refs("omitted_node_refs"),
        )


class ContextSliceCompiler:
    """Compile one decision-sufficient path without unrelated branch state."""

    def compile(
        self,
        tree: DesignStateTree,
        *,
        target_node_ref: str,
    ) -> ContextSlice:
        if not isinstance(tree, DesignStateTree):
            raise TypeError("tree must be a DesignStateTree")
        target = tree.node(target_node_ref)
        path_nodes = tuple(
            sorted(
                (
                    item
                    for item in tree.nodes
                    if item.ref == target.ref
                    or item.path.is_ancestor_of(target.path)
                ),
                key=lambda item: len(item.path.segments),
            )
        )
        path_refs = tuple(item.ref for item in path_nodes)
        if not path_nodes or path_nodes[-1].ref != target.ref:
            raise DesignStateError("target path cannot be reconstructed")
        interface_anchor_refs = {
            item.ref
            for item in path_nodes
            if _LAYER_INDEX[item.path.layer]
            >= _LAYER_INDEX[DesignStateLayer.DISCIPLINE]
        }
        interfaces = tuple(
            sorted(
                (
                    item
                    for item in tree.interfaces
                    if item.source_node_ref in interface_anchor_refs
                    or item.target_node_ref in interface_anchor_refs
                ),
                key=lambda item: item.interface_id,
            )
        )
        commitments = _current_commitments(path_nodes)
        obligations = tuple(
            sorted(
                (
                    item
                    for node in path_nodes
                    for item in node.operational_state.obligations
                    if item.status
                    in {ObligationStatus.OPEN, ObligationStatus.BLOCKED}
                ),
                key=lambda item: item.obligation_id,
            )
        )
        concept_facts = tree.root.operational_state.facts
        ancestor_facts = tuple(
            item
            for node in path_nodes[1:-1]
            for item in node.operational_state.facts
        )
        evidence = {
            ref
            for node in path_nodes
            for ref in node.operational_state.evidence_refs
        }
        evidence.update(
            item.source_ref
            for node in path_nodes
            for item in node.operational_state.facts
        )
        evidence.update(
            ref
            for item in commitments
            for ref in item.evidence_refs
        )
        evidence.update(
            ref
            for item in interfaces
            for ref in item.evidence_refs
        )
        return ContextSlice(
            tree_digest=tree.tree_digest,
            target_node_ref=target.ref,
            target_state_digest=target.operational_state.state_digest,
            path=target.path,
            ancestor_node_refs=path_refs[:-1],
            concept_facts=concept_facts,
            ancestor_facts=ancestor_facts,
            local_facts=target.operational_state.facts,
            commitments=commitments,
            obligations=obligations,
            phase_deliverable_refs=tuple(
                sorted(
                    {
                        ref
                        for node in path_nodes
                        for ref in node.phase_deliverable_refs
                    }
                )
            ),
            interfaces=interfaces,
            allowed_authority_ids=target.allowed_authority_ids,
            evidence_refs=tuple(sorted(evidence)),
            omitted_node_refs=tuple(
                sorted(
                    item.ref
                    for item in tree.nodes
                    if item.ref not in path_refs
                )
            ),
        )


def _current_commitments(
    nodes: tuple[DesignStateNode, ...],
) -> tuple[Commitment, ...]:
    closed = {
        CommitmentStatus.RELEASED,
        CommitmentStatus.REVISED,
        CommitmentStatus.SUPERSEDED,
    }
    return tuple(
        sorted(
            (
                item
                for node in nodes
                for item in node.operational_state.commitments
                if item.status not in closed
            ),
            key=lambda item: item.commitment_id,
        )
    )


def _changed_logical_refs(
    before: OperationalMarkovState,
    after: OperationalMarkovState,
) -> set[str]:
    """Return refs whose future-relevant meaning changed."""

    changed: set[str] = set()

    def collect(
        before_items: tuple[Any, ...],
        after_items: tuple[Any, ...],
        *,
        identity: Any,
        refs: Any,
    ) -> None:
        before_by_id = {identity(item): item for item in before_items}
        after_by_id = {identity(item): item for item in after_items}
        for item_id in set(before_by_id) | set(after_by_id):
            old = before_by_id.get(item_id)
            new = after_by_id.get(item_id)
            if old == new:
                continue
            for item in (old, new):
                if item is not None:
                    changed.update(refs(item))

    collect(
        before.facts,
        after.facts,
        identity=lambda item: item.ref,
        refs=lambda item: (item.ref,),
    )
    collect(
        before.bindings,
        after.bindings,
        identity=lambda item: item.ref,
        refs=lambda item: (item.ref,),
    )
    collect(
        before.locks,
        after.locks,
        identity=lambda item: item.target_ref,
        refs=lambda item: (f"lock:{item.target_ref}",),
    )
    collect(
        before.commitments,
        after.commitments,
        identity=lambda item: item.commitment_id,
        refs=lambda item: (
            f"commitment:{item.commitment_id}",
            *item.scope_refs,
        ),
    )
    collect(
        before.obligations,
        after.obligations,
        identity=lambda item: item.obligation_id,
        refs=lambda item: (
            f"obligation:{item.obligation_id}",
            *item.subject_refs,
        ),
    )
    collect(
        before.dependencies,
        after.dependencies,
        identity=lambda item: item.ref,
        refs=lambda item: (item.ref,),
    )
    changed_invalidations = set(before.invalidated_refs) ^ set(
        after.invalidated_refs
    )
    changed.update(changed_invalidations)
    changed.update(
        f"invalidated:{ref}" for ref in changed_invalidations
    )
    changed.update(
        set(before.evidence_refs) ^ set(after.evidence_refs)
    )
    return changed


@dataclass(frozen=True, slots=True)
class NestedStateTransition:
    compiled: CompiledDecisionTransition
    tree: DesignStateTree
    changed_node_ref: str
    invalidated_node_refs: tuple[str, ...]
    revalidation_node_refs: tuple[str, ...]


def compile_nested_decision(
    tree: DesignStateTree,
    *,
    target_node_ref: str,
    operator: DecisionOperator,
) -> NestedStateTransition:
    """Apply one local operator and propagate only named interfaces."""

    if not isinstance(tree, DesignStateTree):
        raise TypeError("tree must be a DesignStateTree")
    target = tree.node(target_node_ref)
    if operator.authority_id not in target.allowed_authority_ids:
        raise DesignStateError(
            "operator authority is outside target-node permission"
        )
    compiled = compile_decision_operator(
        target.operational_state,
        operator,
    )
    next_node = replace(
        target,
        operational_state=compiled.state,
    )
    changed_refs = _changed_logical_refs(
        target.operational_state,
        compiled.state,
    )
    invalidated: set[str] = set()
    revalidation: set[str] = set()
    for interface in tree.interfaces:
        if (
            interface.source_node_ref != target.ref
            or not changed_refs.intersection(interface.source_refs)
        ):
            continue
        if interface.effect is DependencyEffect.INVALIDATES:
            invalidated.add(interface.target_node_ref)
        elif (
            interface.effect
            is DependencyEffect.REQUIRES_REVALIDATION
        ):
            revalidation.add(interface.target_node_ref)
    revalidation -= invalidated
    next_epoch = max(tree.branch.epoch, compiled.state.branch.epoch)
    next_tree = DesignStateTree(
        branch=replace(tree.branch, epoch=next_epoch),
        nodes=tuple(
            next_node if item.ref == target.ref else item
            for item in tree.nodes
        ),
        interfaces=tree.interfaces,
    )
    return NestedStateTransition(
        compiled=compiled,
        tree=next_tree,
        changed_node_ref=target.ref,
        invalidated_node_refs=tuple(sorted(invalidated)),
        revalidation_node_refs=tuple(sorted(revalidation)),
    )
