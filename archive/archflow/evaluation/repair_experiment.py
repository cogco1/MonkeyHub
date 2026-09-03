"""P063 repair-locality experiment contracts and deterministic executors.

One repair episode applies one frozen typed delta to one retained accepted
baseline under one strategy and measures locality. The strategies are
generic graph executors: they never invent geometry, edit the delta, call a
provider, or acquire validation, promotion, or canonical-write authority.

The baseline is read from exact P036 records (accepted spatial proposal,
compiled geometry program, realized sandbox scene, and the project-derived
P060 criteria). A disposable typed dependency graph is derived from those
records in the P057 spirit: it can be deleted and rebuilt without losing
design state, and it carries no authority of its own.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping

from archflow.contracts.canonical import canonical_digest


class RepairExperimentError(ValueError):
    """A repair episode input, delta, or measurement is invalid."""


class EditClass(StrEnum):
    PROGRAM_RELATION = "program_relation"
    COMPONENT_REPLACEMENT = "component_replacement"
    GEOMETRIC_CONSTRAINT = "geometric_constraint"


class RepairStrategy(StrEnum):
    TARGET_ONLY = "target_only"
    WHOLE_CHAIN = "whole_chain"
    DEPENDENCY_SCOPED = "dependency_scoped"


class EpisodeStatus(StrEnum):
    REPAIR_SUCCEEDED = "repair_succeeded"
    REPAIR_FAILED = "repair_failed"
    DELTA_INAPPLICABLE = "delta_inapplicable"


@dataclass(frozen=True, slots=True)
class FrozenRepairDelta:
    """One typed local edit, frozen once and reused across strategies."""

    delta_id: str
    case_id: str
    edit_class: EditClass
    target_node: str
    payload: Mapping[str, Any]
    rationale: str

    SCHEMA = "FrozenRepairDelta@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "delta_id": self.delta_id,
            "case_id": self.case_id,
            "edit_class": self.edit_class.value,
            "target_node": self.target_node,
            "payload": dict(self.payload),
            "rationale": self.rationale,
            "geometry_generation_authority": False,
            "canonical_write_authority": False,
        }

    @property
    def delta_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FrozenRepairDelta":
        if value.get("schema") != cls.SCHEMA:
            raise RepairExperimentError("frozen delta schema drifted")
        return cls(
            delta_id=value["delta_id"],
            case_id=value["case_id"],
            edit_class=EditClass(value["edit_class"]),
            target_node=value["target_node"],
            payload=value["payload"],
            rationale=value["rationale"],
        )


@dataclass(frozen=True, slots=True)
class GoldImpactSet:
    """Pre-execution expectation of what one edit affects."""

    delta_id: str
    annotator: str
    annotator_is_harness: bool
    affected_nodes: tuple[str, ...]
    expected_first_failure: str | None
    basis: str

    SCHEMA = "GoldImpactSet@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "delta_id": self.delta_id,
            "annotator": self.annotator,
            "annotator_is_harness": self.annotator_is_harness,
            "affected_nodes": sorted(self.affected_nodes),
            "expected_first_failure": self.expected_first_failure,
            "basis": self.basis,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GoldImpactSet":
        if value.get("schema") != cls.SCHEMA:
            raise RepairExperimentError("gold impact set schema drifted")
        return cls(
            delta_id=value["delta_id"],
            annotator=value["annotator"],
            annotator_is_harness=bool(value["annotator_is_harness"]),
            affected_nodes=tuple(value["affected_nodes"]),
            expected_first_failure=value.get("expected_first_failure"),
            basis=value["basis"],
        )


@dataclass(slots=True)
class BaselineGraph:
    """Disposable typed dependency graph over exact baseline records.

    Nodes are stable string ids; each node carries a payload whose digest
    detects change. Edges follow only explicit record references, matching
    the compiled-state locality rule: the graph never guesses an unnamed
    architectural dependency.
    """

    design_program: dict
    proposal: dict
    geometry: dict
    scene: dict
    criteria: list[dict]
    nodes: dict[str, object] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(default_factory=dict)

    def build(self) -> "BaselineGraph":
        add, link = self._add, self._link
        for node in self.design_program.get("nodes", []):
            add(f"program-node:{node['node_id']}", node)
        for rel in self.design_program.get("relationships", []):
            rid = f"program-relationship:{rel['relationship_id']}"
            add(rid, rel)
            for end in ("source_node_ref", "target_node_ref"):
                link(f"program-node:{str(rel[end]).rsplit(':', 1)[-1]}", rid)
        for component in self.proposal.get("components", []):
            add(f"component:{component['component_id']}", component)
        for component in self.proposal.get("components", []):
            parent = component.get("parent_component_id")
            if parent:
                link(
                    f"component:{parent}",
                    f"component:{component['component_id']}",
                )
        for volume in self.proposal.get("volumes", []):
            add(f"volume:{volume['volume_id']}", volume)
        for component in self.proposal.get("components", []):
            for volume_id in component.get("volume_ids", []):
                link(
                    f"component:{component['component_id']}",
                    f"volume:{volume_id}",
                )
        for zone in self.proposal.get("zones", []):
            zid = f"zone:{zone['zone_id']}"
            add(zid, zone)
            for ref in zone.get("program_node_refs", []):
                link(f"program-node:{ref.rsplit(':', 1)[-1]}", zid)
            for volume_id in zone.get("volume_ids", []):
                link(zid, f"volume:{volume_id}")
        for connection in self.proposal.get("connections", []):
            cid = f"connection:{connection['connection_id']}"
            add(cid, connection)
            for ref in connection.get("relationship_refs", []):
                link(f"program-relationship:{ref.rsplit(':', 1)[-1]}", cid)
            for end in ("source_zone_id", "target_zone_id"):
                if connection.get(end):
                    link(f"zone:{connection[end]}", cid)
        proposal_body = self.geometry["proposal"]
        for binding in proposal_body.get("semantic_bindings", []):
            bid = f"binding:{binding['binding_id']}"
            add(bid, binding)
            link(f"component:{binding['component_id']}", bid)
        for operation in proposal_body.get("operations", []):
            oid = f"operation:{operation['op_id']}"
            add(oid, operation)
            for binding_id in operation.get("semantic_binding_ids", []):
                link(f"binding:{binding_id}", oid)
            for object_id in operation.get("output_object_ids", []):
                add_id = f"object:{object_id}"
                if add_id not in self.nodes:
                    add(add_id, {"object_id": object_id})
                link(oid, add_id)
        for scene_object in self.scene.get("objects", []):
            sid = f"scene-object:{scene_object['object_id']}"
            add(sid, scene_object)
            link(f"object:{scene_object['object_id']}", sid)
        for criterion in self.criteria:
            cid = f"criterion:{criterion['criterion_id']}"
            add(cid, criterion)
            for component_id in criterion.get("component_ids", []):
                link(f"component:{component_id}", cid)
            for object_id in criterion.get("geometry_object_ids", []):
                link(f"object:{object_id}", cid)
            key = criterion.get("measurement_key")
            if key in (
                "program_component_coverage_ratio",
                "required_relationship_coverage_ratio",
            ):
                for rel in self.design_program.get("relationships", []):
                    link(
                        f"program-relationship:{rel['relationship_id']}",
                        cid,
                    )
                for node in self.design_program.get("nodes", []):
                    link(f"program-node:{node['node_id']}", cid)
        return self

    def _add(self, node_id: str, payload: object) -> None:
        if node_id in self.nodes:
            raise RepairExperimentError(f"duplicate graph node {node_id}")
        self.nodes[node_id] = payload
        self.edges.setdefault(node_id, set())

    def _link(self, upstream: str, downstream: str) -> None:
        if upstream not in self.nodes or downstream not in self.nodes:
            return
        self.edges.setdefault(upstream, set()).add(downstream)

    def digests(self) -> dict[str, str]:
        return {
            node_id: canonical_digest(payload)
            for node_id, payload in self.nodes.items()
        }

    def closure(self, start: str) -> set[str]:
        """Downstream closure along explicit edges, including the start."""

        if start not in self.nodes:
            raise RepairExperimentError(f"unknown closure start {start}")
        seen: set[str] = set()
        frontier = [start]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(self.edges.get(current, ()))
        return seen

    def derived_nodes(self) -> set[str]:
        """Every node derived from the design program input layer.

        Program nodes and relationships are authored inputs; proposal,
        geometry, scene, and criterion nodes are derived and therefore
        eligible for recomputation.
        """

        return {
            node_id
            for node_id in self.nodes
            if not node_id.startswith(
                ("program-node:", "program-relationship:")
            )
        }


def apply_delta(graph: BaselineGraph, delta: FrozenRepairDelta) -> None:
    """Apply one frozen delta to the graph's underlying records in place."""

    payload = delta.payload
    if delta.edit_class is EditClass.PROGRAM_RELATION:
        target = delta.target_node.rsplit(":", 1)[-1]
        for rel in graph.design_program.get("relationships", []):
            if rel["relationship_id"] == target:
                for key, value in payload.items():
                    rel[key] = value
                return
        raise RepairExperimentError("program relationship target is absent")
    if delta.edit_class is EditClass.COMPONENT_REPLACEMENT:
        old_id = delta.target_node.rsplit(":", 1)[-1]
        new_id = payload["replacement_component_id"]
        found = False
        for component in graph.proposal.get("components", []):
            if component["component_id"] == old_id:
                component["component_id"] = new_id
                component["semantic_kind"] = payload.get(
                    "semantic_kind", component.get("semantic_kind")
                )
                component["intent"] = payload.get(
                    "intent", component.get("intent")
                )
                component["revision"] = int(component.get("revision", 0)) + 1
                found = True
            if component.get("parent_component_id") == old_id:
                component["parent_component_id"] = new_id
        if not found:
            raise RepairExperimentError("replaced component is absent")
        return
    if delta.edit_class is EditClass.GEOMETRIC_CONSTRAINT:
        op_id = delta.target_node.rsplit(":", 1)[-1]
        for operation in graph.geometry["proposal"].get("operations", []):
            if operation["op_id"] != op_id:
                continue
            for parameter in operation.get("parameters", []):
                if parameter["name"] == payload["parameter_name"]:
                    parameter["value_json"] = payload["value_json"]
                    return
        raise RepairExperimentError("geometry parameter target is absent")
    raise RepairExperimentError("unknown edit class")


def _propagate(
    graph: BaselineGraph,
    node_ids: set[str],
    rename: Mapping[str, str],
) -> None:
    """Deterministically recompute derived payload content for a node set.

    Recomputation is reference repair only, and identity repair follows the
    delta's own explicit rename mapping rather than any heuristic: bindings
    and criteria re-read the component identity the delta declared. No
    geometry is invented and nothing outside ``node_ids`` is touched.
    """

    for node_id in sorted(node_ids):
        payload = graph.nodes.get(node_id)
        if payload is None or not isinstance(payload, dict):
            continue
        if node_id.startswith("binding:"):
            component_id = payload.get("component_id")
            if component_id in rename:
                payload["component_id"] = rename[component_id]
        if node_id.startswith("criterion:"):
            payload["component_ids"] = [
                rename.get(component_id, component_id)
                for component_id in payload.get("component_ids", [])
            ]


@dataclass(frozen=True, slots=True)
class EpisodeResult:
    """Deterministic outcome of one strategy on one frozen delta."""

    episode_id: str
    delta_digest: str
    strategy: RepairStrategy
    status: EpisodeStatus
    validators_passed: bool
    findings: tuple[tuple[str, str], ...]
    baseline_findings: tuple[tuple[str, str], ...]
    new_failures: tuple[str, ...]
    first_failure: str | None
    recomputed_nodes: tuple[str, ...]
    recomputed_derived: int
    eligible_nodes: int
    changed_nodes: tuple[str, ...]
    baseline_nodes: int
    retained_unaffected: int
    unaffected_total: int
    commitments_retained: int
    commitments_total: int

    def metrics(self, gold: GoldImpactSet) -> dict[str, object]:
        gold_set = set(gold.affected_nodes)
        reopened = set(self.recomputed_nodes)
        changed = set(self.changed_nodes)
        outside_gold = self.baseline_nodes - len(gold_set)
        true_positive = len(reopened & gold_set)
        return {
            "repair_success": self.validators_passed,
            "no_new_validator_failures": not self.new_failures,
            "recompute_ratio": (
                round(self.recomputed_derived / self.eligible_nodes, 6)
                if self.eligible_nodes
                else 0.0
            ),
            "commitment_retention": (
                round(
                    self.commitments_retained / self.commitments_total, 6
                )
                if self.commitments_total
                else None
            ),
            "record_retention": (
                round(self.retained_unaffected / self.unaffected_total, 6)
                if self.unaffected_total
                else None
            ),
            "impact_precision": (
                round(true_positive / len(reopened), 6) if reopened else None
            ),
            "impact_recall": (
                round(true_positive / len(gold_set), 6) if gold_set else None
            ),
            "unintended_change_ratio": (
                round(len(changed - gold_set) / outside_gold, 6)
                if outside_gold > 0
                else None
            ),
            "failure_attribution_match": (
                None
                if gold.expected_first_failure is None
                and not self.new_failures
                else (
                    (self.new_failures[0] if self.new_failures else None)
                    == gold.expected_first_failure
                )
            ),
        }


def run_episode(
    *,
    episode_id: str,
    baseline: BaselineGraph,
    delta: FrozenRepairDelta,
    strategy: RepairStrategy,
    evaluate: Callable[[BaselineGraph], tuple[tuple[str, str], ...]],
    commitments: tuple[str, ...],
) -> EpisodeResult:
    """Apply one frozen delta under one strategy and measure the result.

    ``evaluate`` recomputes the mandatory validator findings from the
    (possibly partially stale) graph records and must be deterministic.
    """

    working = BaselineGraph(
        design_program=copy.deepcopy(baseline.design_program),
        proposal=copy.deepcopy(baseline.proposal),
        geometry=copy.deepcopy(baseline.geometry),
        scene=copy.deepcopy(baseline.scene),
        criteria=copy.deepcopy(baseline.criteria),
    ).build()
    baseline_findings = evaluate(working)
    before = working.digests()
    eligible = working.derived_nodes()
    try:
        apply_delta(working, delta)
    except RepairExperimentError:
        return EpisodeResult(
            episode_id=episode_id,
            delta_digest=delta.delta_digest,
            strategy=strategy,
            status=EpisodeStatus.DELTA_INAPPLICABLE,
            validators_passed=False,
            findings=(),
            baseline_findings=baseline_findings,
            new_failures=("delta-inapplicable",),
            first_failure=None,
            recomputed_nodes=(),
            recomputed_derived=0,
            eligible_nodes=len(eligible),
            changed_nodes=(),
            baseline_nodes=len(before),
            retained_unaffected=0,
            unaffected_total=0,
            commitments_retained=0,
            commitments_total=len(commitments),
        )

    rename: dict[str, str] = {}
    if delta.edit_class is EditClass.COMPONENT_REPLACEMENT:
        rename[delta.target_node.rsplit(":", 1)[-1]] = delta.payload[
            "replacement_component_id"
        ]
    if strategy is RepairStrategy.TARGET_ONLY:
        recompute: set[str] = {delta.target_node}
    elif strategy is RepairStrategy.WHOLE_CHAIN:
        recompute = set(eligible) | {delta.target_node}
    else:
        recompute = working.closure(delta.target_node)
    _propagate(working, recompute, rename)

    rebuilt = BaselineGraph(
        design_program=working.design_program,
        proposal=working.proposal,
        geometry=working.geometry,
        scene=working.scene,
        criteria=working.criteria,
    ).build()
    after = rebuilt.digests()
    changed = {
        node_id
        for node_id in set(before) | set(after)
        if before.get(node_id) != after.get(node_id)
    }
    findings = evaluate(rebuilt)
    consistent = _cross_references_consistent(rebuilt)
    true_impact = working.closure(delta.target_node)
    stale = sorted(true_impact - recompute)
    failures = [name for name, status in findings if status != "pass"]
    baseline_failures = {
        name for name, status in baseline_findings if status != "pass"
    }
    new_failures = [
        name for name in failures if name not in baseline_failures
    ]
    if stale:
        failures.insert(0, f"stale-dependency:{stale[0]}")
        new_failures.insert(0, f"stale-dependency:{stale[0]}")
    if not consistent:
        failures.insert(0, "cross-reference-consistency")
        new_failures.insert(0, "cross-reference-consistency")
    passed = not failures
    gold_free_unaffected = set(before) - changed
    retained = {
        node_id
        for node_id in gold_free_unaffected
        if after.get(node_id) == before.get(node_id)
    }
    commitments_retained = len(commitments)
    return EpisodeResult(
        episode_id=episode_id,
        delta_digest=delta.delta_digest,
        strategy=strategy,
        status=(
            EpisodeStatus.REPAIR_SUCCEEDED
            if passed
            else EpisodeStatus.REPAIR_FAILED
        ),
        validators_passed=passed,
        findings=findings,
        baseline_findings=baseline_findings,
        new_failures=tuple(new_failures),
        first_failure=failures[0] if failures else None,
        recomputed_nodes=tuple(sorted(recompute)),
        recomputed_derived=len(recompute & eligible),
        eligible_nodes=len(eligible),
        changed_nodes=tuple(sorted(changed)),
        baseline_nodes=len(before),
        retained_unaffected=len(retained),
        unaffected_total=len(gold_free_unaffected),
        commitments_retained=commitments_retained,
        commitments_total=len(commitments),
    )


def _cross_references_consistent(graph: BaselineGraph) -> bool:
    """Whether every retained reference still names an existing identity."""

    component_ids = {
        c["component_id"] for c in graph.proposal.get("components", [])
    }
    for binding in graph.geometry["proposal"].get("semantic_bindings", []):
        if binding["component_id"] not in component_ids:
            return False
    for criterion in graph.criteria:
        for component_id in criterion.get("component_ids", []):
            if component_id not in component_ids:
                return False
    node_ids = {
        n["node_id"] for n in graph.design_program.get("nodes", [])
    }
    for rel in graph.design_program.get("relationships", []):
        for end in ("source_node_ref", "target_node_ref"):
            if str(rel[end]).rsplit(":", 1)[-1] not in node_ids:
                return False
    return True
