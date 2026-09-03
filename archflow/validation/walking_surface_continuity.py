"""Deterministic exterior-to-interior walking-surface continuity checks.

The checker is intentionally project-neutral.  It consumes explicit surface
datums, directed route edges, complete route declarations, and numeric limits
supplied by the caller.  It never infers a building-specific level or embeds a
default accessibility threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
)
from archflow.project.refs import BranchRef
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "promotion_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "geometry_mutation_authority": False,
    "canonical_write_authority": False,
}


class WalkingSurfaceContinuityError(ValueError):
    """A walking-surface profile is malformed or changed identity."""


class WalkingSurfaceNodeRole(StrEnum):
    EXTERIOR = "exterior"
    TRANSITION = "transition"
    INTERIOR = "interior"


class WalkingSurfaceEdgeKind(StrEnum):
    CONTINUOUS = "continuous"
    STEP = "step"
    RAMP = "ramp"
    THRESHOLD = "threshold"


def _optional_non_negative(value: object, field: str) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise WalkingSurfaceContinuityError(
            f"{field} must be a finite non-negative number or None"
        )
    return float(value)


def _optional_finite(value: object, field: str) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise WalkingSurfaceContinuityError(
            f"{field} must be a finite number or None"
        )
    return float(value)


@dataclass(frozen=True, slots=True)
class WalkingSurfaceNode:
    node_ref: str
    role: WalkingSurfaceNodeRole
    datum: float | None
    evidence_refs: tuple[str, ...] = ()

    SCHEMA: ClassVar[str] = "WalkingSurfaceNode@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_ref", logical_ref(self.node_ref, "node_ref"))
        if not isinstance(self.role, WalkingSurfaceNodeRole):
            raise TypeError("role must be WalkingSurfaceNodeRole")
        object.__setattr__(self, "datum", _optional_finite(self.datum, "datum"))
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "node evidence_refs", allow_empty=True),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "node_ref": self.node_ref,
            "role": self.role.value,
            "datum": self.datum,
            "evidence_refs": list(self.evidence_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfaceNode":
        payload = exact_mapping(
            value,
            {"schema", "node_ref", "role", "datum", "evidence_refs", *_AUTHORITY_FIELDS},
            "walking surface node",
        )
        if payload["schema"] != cls.SCHEMA or not isinstance(payload["evidence_refs"], list):
            raise WalkingSurfaceContinuityError("unsupported walking surface node schema")
        result = cls(
            node_ref=payload["node_ref"],
            role=WalkingSurfaceNodeRole(payload["role"]),
            datum=payload["datum"],
            evidence_refs=tuple(payload["evidence_refs"]),
        )
        if result.to_dict() != payload:
            raise WalkingSurfaceContinuityError("walking surface node identity changed")
        return result


@dataclass(frozen=True, slots=True)
class WalkingSurfaceEdge:
    edge_ref: str
    from_node_ref: str
    to_node_ref: str
    kind: WalkingSurfaceEdgeKind
    horizontal_run: float | None = None
    evidence_refs: tuple[str, ...] = ()

    SCHEMA: ClassVar[str] = "WalkingSurfaceEdge@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "edge_ref", logical_ref(self.edge_ref, "edge_ref"))
        object.__setattr__(
            self, "from_node_ref", logical_ref(self.from_node_ref, "from_node_ref")
        )
        object.__setattr__(self, "to_node_ref", logical_ref(self.to_node_ref, "to_node_ref"))
        if self.from_node_ref == self.to_node_ref:
            raise WalkingSurfaceContinuityError("walking surface edge cannot be a self-loop")
        if not isinstance(self.kind, WalkingSurfaceEdgeKind):
            raise TypeError("kind must be WalkingSurfaceEdgeKind")
        run = _optional_non_negative(self.horizontal_run, "horizontal_run")
        if run == 0.0:
            raise WalkingSurfaceContinuityError("horizontal_run must be positive when supplied")
        object.__setattr__(self, "horizontal_run", run)
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "edge evidence_refs", allow_empty=True),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "edge_ref": self.edge_ref,
            "from_node_ref": self.from_node_ref,
            "to_node_ref": self.to_node_ref,
            "kind": self.kind.value,
            "horizontal_run": self.horizontal_run,
            "evidence_refs": list(self.evidence_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfaceEdge":
        payload = exact_mapping(
            value,
            {
                "schema", "edge_ref", "from_node_ref", "to_node_ref", "kind",
                "horizontal_run", "evidence_refs", *_AUTHORITY_FIELDS,
            },
            "walking surface edge",
        )
        if payload["schema"] != cls.SCHEMA or not isinstance(payload["evidence_refs"], list):
            raise WalkingSurfaceContinuityError("unsupported walking surface edge schema")
        result = cls(
            edge_ref=payload["edge_ref"],
            from_node_ref=payload["from_node_ref"],
            to_node_ref=payload["to_node_ref"],
            kind=WalkingSurfaceEdgeKind(payload["kind"]),
            horizontal_run=payload["horizontal_run"],
            evidence_refs=tuple(payload["evidence_refs"]),
        )
        if result.to_dict() != payload:
            raise WalkingSurfaceContinuityError("walking surface edge identity changed")
        return result


@dataclass(frozen=True, slots=True)
class WalkingSurfacePathRequirement:
    path_id: str
    node_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    SCHEMA: ClassVar[str] = "WalkingSurfacePathRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.path_id, "path_id")
        if not isinstance(self.node_refs, tuple) or len(self.node_refs) < 2:
            raise WalkingSurfaceContinuityError("path must contain at least two nodes")
        normalized = tuple(logical_ref(item, "path node_ref") for item in self.node_refs)
        if len(normalized) != len(set(normalized)):
            raise WalkingSurfaceContinuityError("path cannot repeat a node")
        object.__setattr__(self, "node_refs", normalized)
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "path evidence_refs", allow_empty=True),
        )

    @property
    def ref(self) -> str:
        return f"walking-surface-path:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "path_id": self.path_id,
            "node_refs": list(self.node_refs),
            "evidence_refs": list(self.evidence_refs),
            "complete_exterior_to_interior_required": True,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfacePathRequirement":
        payload = exact_mapping(
            value,
            {
                "schema", "path_id", "node_refs", "evidence_refs",
                "complete_exterior_to_interior_required", *_AUTHORITY_FIELDS,
            },
            "walking surface path",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["complete_exterior_to_interior_required"] is not True
            or not isinstance(payload["node_refs"], list)
            or not isinstance(payload["evidence_refs"], list)
        ):
            raise WalkingSurfaceContinuityError("unsupported walking surface path schema")
        result = cls(
            path_id=payload["path_id"],
            node_refs=tuple(payload["node_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]),
        )
        if result.to_dict() != payload:
            raise WalkingSurfaceContinuityError("walking surface path identity changed")
        return result


@dataclass(frozen=True, slots=True)
class WalkingSurfaceCriteria:
    max_continuous_delta: float | None
    max_step_rise: float | None
    max_ramp_slope: float | None
    max_threshold_rise: float | None

    SCHEMA: ClassVar[str] = "WalkingSurfaceCriteria@1"

    def __post_init__(self) -> None:
        for field in (
            "max_continuous_delta", "max_step_rise", "max_ramp_slope",
            "max_threshold_rise",
        ):
            object.__setattr__(self, field, _optional_non_negative(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "max_continuous_delta": self.max_continuous_delta,
            "max_step_rise": self.max_step_rise,
            "max_ramp_slope": self.max_ramp_slope,
            "max_threshold_rise": self.max_threshold_rise,
            "project_supplied_values": True,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfaceCriteria":
        payload = exact_mapping(
            value,
            {
                "schema", "max_continuous_delta", "max_step_rise",
                "max_ramp_slope", "max_threshold_rise", "project_supplied_values",
                *_AUTHORITY_FIELDS,
            },
            "walking surface criteria",
        )
        if payload["schema"] != cls.SCHEMA or payload["project_supplied_values"] is not True:
            raise WalkingSurfaceContinuityError("unsupported walking surface criteria schema")
        result = cls(
            max_continuous_delta=payload["max_continuous_delta"],
            max_step_rise=payload["max_step_rise"],
            max_ramp_slope=payload["max_ramp_slope"],
            max_threshold_rise=payload["max_threshold_rise"],
        )
        if result.to_dict() != payload:
            raise WalkingSurfaceContinuityError("walking surface criteria identity changed")
        return result


@dataclass(frozen=True, slots=True)
class WalkingSurfaceContinuityProfile:
    profile_id: str
    nodes: tuple[WalkingSurfaceNode, ...]
    edges: tuple[WalkingSurfaceEdge, ...]
    paths: tuple[WalkingSurfacePathRequirement, ...]
    criteria: WalkingSurfaceCriteria
    length_unit_ref: str

    SCHEMA: ClassVar[str] = "WalkingSurfaceContinuityProfile@1"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        if not isinstance(self.nodes, tuple) or any(not isinstance(item, WalkingSurfaceNode) for item in self.nodes):
            raise TypeError("nodes must be a WalkingSurfaceNode tuple")
        if not isinstance(self.edges, tuple) or any(not isinstance(item, WalkingSurfaceEdge) for item in self.edges):
            raise TypeError("edges must be a WalkingSurfaceEdge tuple")
        if not isinstance(self.paths, tuple) or not self.paths or any(
            not isinstance(item, WalkingSurfacePathRequirement) for item in self.paths
        ):
            raise TypeError("paths must contain WalkingSurfacePathRequirement values")
        if not isinstance(self.criteria, WalkingSurfaceCriteria):
            raise TypeError("criteria must be WalkingSurfaceCriteria")
        nodes = tuple(sorted(self.nodes, key=lambda item: item.node_ref))
        edges = tuple(sorted(self.edges, key=lambda item: item.edge_ref))
        paths = tuple(sorted(self.paths, key=lambda item: item.path_id))
        if len({item.node_ref for item in nodes}) != len(nodes):
            raise WalkingSurfaceContinuityError("node_ref values must be unique")
        if len({item.edge_ref for item in edges}) != len(edges):
            raise WalkingSurfaceContinuityError("edge_ref values must be unique")
        if len({item.path_id for item in paths}) != len(paths):
            raise WalkingSurfaceContinuityError("path_id values must be unique")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "paths", paths)
        object.__setattr__(self, "length_unit_ref", logical_ref(self.length_unit_ref, "length_unit_ref"))

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"walking-surface-profile:{self.profile_digest}"

    @property
    def path_refs(self) -> tuple[str, ...]:
        return tuple(sorted(item.ref for item in self.paths))

    @property
    def checker_requirement_refs(self) -> tuple[str, ...]:
        """Exact refs controllers bind into relation-verification profiles."""

        return self.path_refs

    @property
    def subject_refs(self) -> tuple[str, ...]:
        return tuple(sorted({self.ref, *self.path_refs, *(item.node_ref for item in self.nodes), *(item.edge_ref for item in self.edges), *(ref for path in self.paths for ref in path.node_refs)}))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            "paths": [item.to_dict() for item in self.paths],
            "criteria": self.criteria.to_dict(),
            "length_unit_ref": self.length_unit_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfaceContinuityProfile":
        payload = exact_mapping(
            value,
            {"schema", "profile_id", "nodes", "edges", "paths", "criteria", "length_unit_ref", *_AUTHORITY_FIELDS},
            "walking surface continuity profile",
        )
        if payload["schema"] != cls.SCHEMA or any(not isinstance(payload[field], list) for field in ("nodes", "edges", "paths")):
            raise WalkingSurfaceContinuityError("unsupported walking surface profile schema")
        result = cls(
            profile_id=payload["profile_id"],
            nodes=tuple(WalkingSurfaceNode.from_dict(item) for item in payload["nodes"]),
            edges=tuple(WalkingSurfaceEdge.from_dict(item) for item in payload["edges"]),
            paths=tuple(WalkingSurfacePathRequirement.from_dict(item) for item in payload["paths"]),
            criteria=WalkingSurfaceCriteria.from_dict(payload["criteria"]),
            length_unit_ref=payload["length_unit_ref"],
        )
        if result.to_dict() != payload:
            raise WalkingSurfaceContinuityError("walking surface profile identity changed")
        return result


@dataclass(frozen=True, slots=True)
class _PathOutcome:
    status: CheckStatus
    findings: tuple[CheckFinding, ...]
    measurements: tuple[CheckMeasurement, ...]


def _finding(path: WalkingSurfacePathRequirement, code: str, severity: FindingSeverity, message: str, subjects: tuple[str, ...], evidence_refs: tuple[str, ...] = ()) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=severity,
        message=f"{path.path_id}: {message}",
        subject_refs=tuple(sorted({path.ref, *subjects})),
        evidence_refs=tuple(sorted(set(path.evidence_refs) | set(evidence_refs))),
    )


def _measurement(path: WalkingSurfacePathRequirement, edge: WalkingSurfaceEdge, name: str, value: float, unit_ref: str | None) -> CheckMeasurement:
    digest = canonical_digest({"path_ref": path.ref, "edge_ref": edge.edge_ref, "name": name})
    return CheckMeasurement(
        measurement_id=f"walking-surface-{digest[:24]}",
        subject_ref=edge.edge_ref,
        name=name,
        value=value,
        unit_ref=unit_ref,
        evidence_refs=tuple(sorted(set(path.evidence_refs) | set(edge.evidence_refs))),
    )


def _evaluate_path(profile: WalkingSurfaceContinuityProfile, path: WalkingSurfacePathRequirement) -> _PathOutcome:
    nodes = {item.node_ref: item for item in profile.nodes}
    edges: dict[tuple[str, str], list[WalkingSurfaceEdge]] = {}
    for edge in profile.edges:
        edges.setdefault((edge.from_node_ref, edge.to_node_ref), []).append(edge)
    findings: list[CheckFinding] = []
    measurements: list[CheckMeasurement] = []
    endpoint_specs = ((path.node_refs[0], WalkingSurfaceNodeRole.EXTERIOR, "start"), (path.node_refs[-1], WalkingSurfaceNodeRole.INTERIOR, "end"))
    for node_ref, role, label in endpoint_specs:
        node = nodes.get(node_ref)
        if node is None:
            findings.append(_finding(path, "walking-surface-endpoint-missing", FindingSeverity.UNKNOWN, f"{label} endpoint is absent from the supplied node inventory", (node_ref,)))
        elif node.role is not role:
            findings.append(_finding(path, "walking-surface-endpoint-role-mismatch", FindingSeverity.ERROR, f"{label} endpoint must be {role.value}, observed {node.role.value}", (node_ref,), node.evidence_refs))

    limits = {
        WalkingSurfaceEdgeKind.CONTINUOUS: (profile.criteria.max_continuous_delta, "max_continuous_delta"),
        WalkingSurfaceEdgeKind.STEP: (profile.criteria.max_step_rise, "max_step_rise"),
        WalkingSurfaceEdgeKind.RAMP: (profile.criteria.max_ramp_slope, "max_ramp_slope"),
        WalkingSurfaceEdgeKind.THRESHOLD: (profile.criteria.max_threshold_rise, "max_threshold_rise"),
    }
    for ordinal, (from_ref, to_ref) in enumerate(zip(path.node_refs, path.node_refs[1:])):
        candidates = edges.get((from_ref, to_ref), [])
        if not candidates:
            findings.append(_finding(path, "walking-surface-segment-missing", FindingSeverity.UNKNOWN, f"segment {ordinal} has no declared directed edge", (from_ref, to_ref)))
            continue
        if len(candidates) != 1:
            findings.append(_finding(path, "walking-surface-segment-ambiguous", FindingSeverity.UNKNOWN, f"segment {ordinal} has multiple declared directed edges", tuple(item.edge_ref for item in candidates)))
            continue
        edge = candidates[0]
        from_node = nodes.get(from_ref)
        to_node = nodes.get(to_ref)
        evidence_refs = tuple(sorted(set(edge.evidence_refs) | set(from_node.evidence_refs if from_node else ()) | set(to_node.evidence_refs if to_node else ())))
        if from_node is None or to_node is None:
            findings.append(_finding(path, "walking-surface-segment-node-missing", FindingSeverity.UNKNOWN, f"segment {ordinal} references a node absent from the supplied inventory", (edge.edge_ref, from_ref, to_ref), evidence_refs))
            continue
        if from_node.datum is None or to_node.datum is None:
            findings.append(_finding(path, "walking-surface-datum-unknown", FindingSeverity.UNKNOWN, f"segment {ordinal} cannot be checked without both node datums", (edge.edge_ref, from_ref, to_ref), evidence_refs))
            continue
        delta = abs(to_node.datum - from_node.datum)
        limit, limit_name = limits[edge.kind]
        measurements.append(_measurement(path, edge, "vertical_delta", delta, profile.length_unit_ref))
        if edge.kind is WalkingSurfaceEdgeKind.RAMP:
            if edge.horizontal_run is None:
                findings.append(_finding(path, "walking-surface-ramp-run-unknown", FindingSeverity.UNKNOWN, f"segment {ordinal} ramp has no horizontal run", (edge.edge_ref,), evidence_refs))
                continue
            observed = delta / edge.horizontal_run
            measurements.append(_measurement(path, edge, "ramp_slope", observed, None))
        else:
            observed = delta
        if limit is None:
            findings.append(_finding(path, "walking-surface-criterion-unknown", FindingSeverity.UNKNOWN, f"segment {ordinal} requires project-supplied {limit_name}", (edge.edge_ref,), evidence_refs))
        elif observed > limit:
            findings.append(_finding(path, "walking-surface-limit-exceeded", FindingSeverity.ERROR, f"segment {ordinal} {edge.kind.value} observed {observed} exceeds {limit_name} {limit}", (edge.edge_ref, from_ref, to_ref), evidence_refs))

    if any(item.severity is FindingSeverity.ERROR for item in findings):
        status = CheckStatus.FAIL
    elif any(item.severity is FindingSeverity.UNKNOWN for item in findings):
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    return _PathOutcome(
        status=status,
        findings=tuple(sorted(findings, key=lambda item: (item.code, item.subject_refs, item.message))),
        measurements=tuple(sorted(measurements, key=lambda item: item.measurement_id)),
    )


def check_walking_surface_continuity(
    profile: WalkingSurfaceContinuityProfile,
    *,
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Check every declared complete exterior-to-interior path, fail-closed."""

    if not isinstance(profile, WalkingSurfaceContinuityProfile):
        raise TypeError("profile must be WalkingSurfaceContinuityProfile")
    stage_subject_digest = require_sha256(stage_subject_digest, "stage_subject_digest")
    outcomes = {path.ref: _evaluate_path(profile, path) for path in profile.paths}
    if any(item.status is CheckStatus.FAIL for item in outcomes.values()):
        status = CheckStatus.FAIL
    elif any(item.status is CheckStatus.UNKNOWN for item in outcomes.values()):
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    findings = tuple(sorted((item for outcome in outcomes.values() for item in outcome.findings), key=lambda item: (item.code, item.subject_refs, item.message)))
    measurements = tuple(sorted((item for outcome in outcomes.values() for item in outcome.measurements), key=lambda item: item.measurement_id))
    source_refs = tuple(sorted({ref for node in profile.nodes for ref in node.evidence_refs} | {ref for edge in profile.edges for ref in edge.evidence_refs} | {ref for path in profile.paths for ref in path.evidence_refs}))
    covered_refs = tuple(sorted(path_ref for path_ref, outcome in outcomes.items() if outcome.status is not CheckStatus.UNKNOWN))
    return CheckReceiptEnvelope(
        check_id=f"walking-surface-{profile.profile_digest[:24]}",
        checker_id="walking-surface-continuity-checker",
        checker_version="1.0.0",
        branch=branch,
        scope_digest=scope_digest,
        subject_refs=profile.subject_refs,
        subject_digest=stage_subject_digest,
        status=status,
        source_refs=source_refs,
        findings=findings,
        measurements=measurements,
        coverage_denominator=profile.path_refs,
        covered_refs=covered_refs,
    )


validate_walking_surface_continuity = check_walking_surface_continuity


__all__ = [
    "WalkingSurfaceContinuityError",
    "WalkingSurfaceContinuityProfile",
    "WalkingSurfaceCriteria",
    "WalkingSurfaceEdge",
    "WalkingSurfaceEdgeKind",
    "WalkingSurfaceNode",
    "WalkingSurfaceNodeRole",
    "WalkingSurfacePathRequirement",
    "check_walking_surface_continuity",
    "validate_walking_surface_continuity",
]
