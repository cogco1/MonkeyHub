"""Evidence-bound material accounting and pure staged-build control.

This module never writes a project or an external world.  It compiles an exact
plan from already accepted candidate, policy, geometry, and sandbox records;
every control transition remains bound to that immutable plan digest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.realization import HybridScene
from archflow.runtime.candidate_assembly import (
    CandidateAssembly,
    CandidatePolicyKind,
)
from archflow.compilers.geometry import CompiledGeometryProgram
from archflow.state.build_policy import (
    BuildPolicy,
    BuildStagingMode,
    ResourceAvailability,
    ResourceDemand,
)
from archflow.state.operational_state import FactEpistemicStatus
from archflow.contracts.canonical import canonical_digest, canonical_json


class StagedBuildError(ValueError):
    """The staged plan or a control transition violates its exact contract."""


class MaterialStatus(StrEnum):
    AVAILABLE = "available"
    SHORTAGE = "shortage"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"
    UNBOUNDED = "unbounded"


class BuildRunStatus(StrEnum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    lowered = value.lower()
    if len(lowered) != 64 or any(c not in "0123456789abcdef" for c in lowered):
        raise StagedBuildError(f"{field} must be a SHA-256 digest")
    return lowered


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StagedBuildError(f"{field} must be non-empty text")
    return value


def _ids(values: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise StagedBuildError(f"{field} must be a tuple")
    for item in values:
        require_identifier(item, field)
    if values != tuple(sorted(set(values))):
        raise StagedBuildError(f"{field} requires deterministic unique ids")
    return values


def _refs(values: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise StagedBuildError(f"{field} must be a tuple")
    for item in values:
        if not isinstance(item, str) or ":" not in item or not item.strip():
            raise StagedBuildError(f"{field} contains an invalid logical ref")
    if values != tuple(sorted(set(values))):
        raise StagedBuildError(f"{field} requires deterministic unique refs")
    return values


def _number(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise StagedBuildError(f"{field} must be a finite non-negative number")
    return float(value)


def _base_dict(value: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": value.project_id,
        "version": value.version,
        "state_sha256": value.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "version",
        "state_sha256",
    }:
        raise StagedBuildError("base is not an exact project-version ref")
    return ProjectVersionRef(
        project_id=value["project_id"],
        version=value["version"],
        state_sha256=value["state_sha256"],
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return value


def _exact(value: Mapping[str, object], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise StagedBuildError(f"{label} schema drifted")


@dataclass(frozen=True, slots=True)
class StageScope:
    """Authorized assignment of semantic components and policy demands."""

    stage_id: str
    phase_id: str
    layer_index: int
    predecessor_ids: tuple[str, ...]
    component_ids: tuple[str, ...]
    demand_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "StageScope@1"

    def __post_init__(self) -> None:
        require_identifier(self.stage_id, "stage_id")
        require_identifier(self.phase_id, "phase_id")
        if (
            not isinstance(self.layer_index, int)
            or isinstance(self.layer_index, bool)
            or self.layer_index < 0
        ):
            raise StagedBuildError("layer_index must be a non-negative integer")
        _ids(self.predecessor_ids, "predecessor_ids", allow_empty=True)
        _ids(self.component_ids, "component_ids")
        _ids(self.demand_ids, "demand_ids", allow_empty=True)
        _refs(self.evidence_refs, "evidence_refs")
        if self.stage_id in self.predecessor_ids:
            raise StagedBuildError("a stage cannot precede itself")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "stage_id": self.stage_id,
            "phase_id": self.phase_id,
            "layer_index": self.layer_index,
            "predecessor_ids": list(self.predecessor_ids),
            "component_ids": list(self.component_ids),
            "demand_ids": list(self.demand_ids),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> StageScope:
        payload = _mapping(value, "stage scope")
        _exact(
            payload,
            {
                "schema",
                "stage_id",
                "phase_id",
                "layer_index",
                "predecessor_ids",
                "component_ids",
                "demand_ids",
                "evidence_refs",
            },
            "stage scope",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StagedBuildError("unsupported stage scope schema")
        return cls(
            stage_id=payload["stage_id"],
            phase_id=payload["phase_id"],
            layer_index=payload["layer_index"],
            predecessor_ids=tuple(payload["predecessor_ids"]),
            component_ids=tuple(payload["component_ids"]),
            demand_ids=tuple(payload["demand_ids"]),
            evidence_refs=tuple(payload["evidence_refs"]),
        )


def _scope_digest(scopes: tuple[StageScope, ...]) -> str:
    return canonical_digest([item.to_dict() for item in scopes], ascii=False)


@dataclass(frozen=True, slots=True)
class MaterialReconciliation:
    scope_id: str
    resource_ref: str
    demand_ids: tuple[str, ...]
    minimum_required: float
    maximum_required: float
    minimum_available: float | None
    maximum_available: float | None
    minimum_shortage: float | None
    maximum_shortage: float | None
    unit: str
    epistemic_status: FactEpistemicStatus
    status: MaterialStatus

    SCHEMA = "MaterialReconciliation@1"

    def __post_init__(self) -> None:
        require_identifier(self.scope_id, "scope_id")
        if not isinstance(self.resource_ref, str) or ":" not in self.resource_ref:
            raise StagedBuildError("resource_ref must be a logical ref")
        _ids(self.demand_ids, "demand_ids")
        minimum = _number(self.minimum_required, "minimum_required")
        maximum = _number(self.maximum_required, "maximum_required")
        if minimum > maximum:
            raise StagedBuildError("required material range is inverted")
        object.__setattr__(self, "minimum_required", minimum)
        object.__setattr__(self, "maximum_required", maximum)
        _text(self.unit, "unit")
        if not isinstance(self.epistemic_status, FactEpistemicStatus):
            raise TypeError("epistemic_status must be FactEpistemicStatus")
        if not isinstance(self.status, MaterialStatus):
            raise TypeError("status must be MaterialStatus")
        quantities = (
            self.minimum_available,
            self.maximum_available,
            self.minimum_shortage,
            self.maximum_shortage,
        )
        if self.status in {MaterialStatus.UNKNOWN, MaterialStatus.UNBOUNDED}:
            if any(value is not None for value in quantities):
                raise StagedBuildError(
                    "unknown or unbounded material cannot claim finite availability"
                )
            return
        if any(value is None for value in quantities):
            raise StagedBuildError("finite material reconciliation is incomplete")
        available_min = _number(self.minimum_available, "minimum_available")
        available_max = _number(self.maximum_available, "maximum_available")
        shortage_min = _number(self.minimum_shortage, "minimum_shortage")
        shortage_max = _number(self.maximum_shortage, "maximum_shortage")
        if available_min > available_max or shortage_min > shortage_max:
            raise StagedBuildError("material range is inverted")
        expected_min = max(0.0, minimum - available_max)
        expected_max = max(0.0, maximum - available_min)
        if (shortage_min, shortage_max) != (expected_min, expected_max):
            raise StagedBuildError("shortage range does not reconcile")
        expected_status = (
            MaterialStatus.AVAILABLE
            if maximum <= available_min
            else MaterialStatus.SHORTAGE
            if minimum > available_max
            else MaterialStatus.UNCERTAIN
        )
        if self.status is not expected_status:
            raise StagedBuildError("material status does not match its ranges")
        object.__setattr__(self, "minimum_available", available_min)
        object.__setattr__(self, "maximum_available", available_max)
        object.__setattr__(self, "minimum_shortage", shortage_min)
        object.__setattr__(self, "maximum_shortage", shortage_max)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "scope_id": self.scope_id,
            "resource_ref": self.resource_ref,
            "demand_ids": list(self.demand_ids),
            "minimum_required": self.minimum_required,
            "maximum_required": self.maximum_required,
            "minimum_available": self.minimum_available,
            "maximum_available": self.maximum_available,
            "minimum_shortage": self.minimum_shortage,
            "maximum_shortage": self.maximum_shortage,
            "unit": self.unit,
            "epistemic_status": self.epistemic_status.value,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> MaterialReconciliation:
        payload = _mapping(value, "material reconciliation")
        _exact(
            payload,
            {
                "schema",
                "scope_id",
                "resource_ref",
                "demand_ids",
                "minimum_required",
                "maximum_required",
                "minimum_available",
                "maximum_available",
                "minimum_shortage",
                "maximum_shortage",
                "unit",
                "epistemic_status",
                "status",
            },
            "material reconciliation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StagedBuildError("unsupported material reconciliation schema")
        return cls(
            scope_id=payload["scope_id"],
            resource_ref=payload["resource_ref"],
            demand_ids=tuple(payload["demand_ids"]),
            minimum_required=payload["minimum_required"],
            maximum_required=payload["maximum_required"],
            minimum_available=payload["minimum_available"],
            maximum_available=payload["maximum_available"],
            minimum_shortage=payload["minimum_shortage"],
            maximum_shortage=payload["maximum_shortage"],
            unit=payload["unit"],
            epistemic_status=FactEpistemicStatus(payload["epistemic_status"]),
            status=MaterialStatus(payload["status"]),
        )


@dataclass(frozen=True, slots=True)
class MaterialAccountReceipt:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    build_policy_digest: str
    stage_scope_digest: str
    totals: tuple[MaterialReconciliation, ...]
    stage_needs: tuple[MaterialReconciliation, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "MaterialAccountReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef) or self.base.project_id != self.project_id:
            raise StagedBuildError("material account and base disagree")
        object.__setattr__(
            self, "build_policy_digest", _sha(self.build_policy_digest, "build_policy_digest")
        )
        object.__setattr__(
            self, "stage_scope_digest", _sha(self.stage_scope_digest, "stage_scope_digest")
        )
        for values, field in ((self.totals, "totals"), (self.stage_needs, "stage_needs")):
            if not isinstance(values, tuple) or any(
                not isinstance(item, MaterialReconciliation) for item in values
            ):
                raise TypeError(f"{field} contains an invalid item")
        total_keys = tuple(item.resource_ref for item in self.totals)
        if total_keys != tuple(sorted(set(total_keys))) or any(
            item.scope_id != "total" for item in self.totals
        ):
            raise StagedBuildError("material totals require deterministic total scopes")
        stage_keys = tuple((item.scope_id, item.resource_ref) for item in self.stage_needs)
        if stage_keys != tuple(sorted(set(stage_keys))):
            raise StagedBuildError("stage needs require deterministic unique scopes")
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def account_digest(self) -> str:
        return canonical_digest(self.to_dict(), ascii=False)

    def needs_for_stage(self, stage_id: str) -> tuple[MaterialReconciliation, ...]:
        require_identifier(stage_id, "stage_id")
        return tuple(item for item in self.stage_needs if item.scope_id == stage_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_dict(self.base),
            "build_policy_digest": self.build_policy_digest,
            "stage_scope_digest": self.stage_scope_digest,
            "totals": [item.to_dict() for item in self.totals],
            "stage_needs": [item.to_dict() for item in self.stage_needs],
            "evidence_refs": list(self.evidence_refs),
            "resource_substitution_authority": False,
            "execution_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> MaterialAccountReceipt:
        payload = _mapping(value, "material account receipt")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "build_policy_digest",
                "stage_scope_digest",
                "totals",
                "stage_needs",
                "evidence_refs",
                "resource_substitution_authority",
                "execution_authority",
                "canonical_write_authority",
            },
            "material account receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
        ):
            raise StagedBuildError("material account authority drifted")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            build_policy_digest=payload["build_policy_digest"],
            stage_scope_digest=payload["stage_scope_digest"],
            totals=tuple(MaterialReconciliation.from_dict(item) for item in payload["totals"]),
            stage_needs=tuple(
                MaterialReconciliation.from_dict(item) for item in payload["stage_needs"]
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
        )


def _validate_scopes(policy: BuildPolicy, scopes: tuple[StageScope, ...]) -> None:
    if not isinstance(scopes, tuple) or not scopes:
        raise StagedBuildError("stage scopes must be a non-empty tuple")
    stage_ids = tuple(item.stage_id for item in scopes)
    if len(stage_ids) != len(set(stage_ids)):
        raise StagedBuildError("stage scope ids must be unique")
    seen: set[str] = set()
    for item in scopes:
        if not set(item.predecessor_ids) <= seen:
            raise StagedBuildError("stage predecessors must appear earlier in the plan")
        seen.add(item.stage_id)
    if policy.staging_mode is BuildStagingMode.UNKNOWN:
        raise StagedBuildError("unknown staging policy cannot authorize a build plan")
    if policy.staging_mode is BuildStagingMode.SINGLE_PASS:
        if len(scopes) != 1 or scopes[0].predecessor_ids:
            raise StagedBuildError("single-pass policy requires one root stage")
    else:
        assumptions = {
            item.stage_id: item.predecessor_ids for item in policy.staging_assumptions
        }
        actual = {item.stage_id: item.predecessor_ids for item in scopes}
        if assumptions != actual:
            raise StagedBuildError("stage topology disagrees with build policy")


def _reconcile(
    *,
    scope_id: str,
    demands: tuple[ResourceDemand, ...],
    availability: ResourceAvailability | None,
    unbounded: bool,
) -> MaterialReconciliation:
    units = {item.unit for item in demands}
    if len(units) != 1:
        raise StagedBuildError("one resource cannot mix material units")
    resource_ref = demands[0].resource_ref
    minimum_required = sum(item.minimum_required for item in demands)
    maximum_required = sum(item.maximum_required for item in demands)
    demand_ids = tuple(sorted(item.demand_id for item in demands))
    unit = demands[0].unit
    if unbounded:
        return MaterialReconciliation(
            scope_id=scope_id,
            resource_ref=resource_ref,
            demand_ids=demand_ids,
            minimum_required=minimum_required,
            maximum_required=maximum_required,
            minimum_available=None,
            maximum_available=None,
            minimum_shortage=None,
            maximum_shortage=None,
            unit=unit,
            epistemic_status=FactEpistemicStatus.DECLARED,
            status=MaterialStatus.UNBOUNDED,
        )
    if availability is None or availability.epistemic_status is FactEpistemicStatus.UNKNOWN:
        return MaterialReconciliation(
            scope_id=scope_id,
            resource_ref=resource_ref,
            demand_ids=demand_ids,
            minimum_required=minimum_required,
            maximum_required=maximum_required,
            minimum_available=None,
            maximum_available=None,
            minimum_shortage=None,
            maximum_shortage=None,
            unit=unit,
            epistemic_status=FactEpistemicStatus.UNKNOWN,
            status=MaterialStatus.UNKNOWN,
        )
    if availability.unit != unit:
        raise StagedBuildError("availability and demand units disagree")
    assert availability.minimum_available is not None
    assert availability.maximum_available is not None
    minimum_shortage = max(0.0, minimum_required - availability.maximum_available)
    maximum_shortage = max(0.0, maximum_required - availability.minimum_available)
    status = (
        MaterialStatus.AVAILABLE
        if maximum_required <= availability.minimum_available
        else MaterialStatus.SHORTAGE
        if minimum_required > availability.maximum_available
        else MaterialStatus.UNCERTAIN
    )
    return MaterialReconciliation(
        scope_id=scope_id,
        resource_ref=resource_ref,
        demand_ids=demand_ids,
        minimum_required=minimum_required,
        maximum_required=maximum_required,
        minimum_available=availability.minimum_available,
        maximum_available=availability.maximum_available,
        minimum_shortage=minimum_shortage,
        maximum_shortage=maximum_shortage,
        unit=unit,
        epistemic_status=availability.epistemic_status,
        status=status,
    )


def compile_material_account(
    policy: BuildPolicy,
    scopes: tuple[StageScope, ...],
) -> MaterialAccountReceipt:
    """Reconcile every upstream demand once without inventing exact quantities."""

    if not isinstance(policy, BuildPolicy):
        raise TypeError("policy must be BuildPolicy")
    _validate_scopes(policy, scopes)
    demands_by_id = {item.demand_id: item for item in policy.demands}
    assigned = [demand_id for scope in scopes for demand_id in scope.demand_ids]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(demands_by_id):
        raise StagedBuildError("stage scopes must assign every policy demand exactly once")
    availability = {item.resource_ref: item for item in policy.availability}

    def grouped(demands: tuple[ResourceDemand, ...]) -> tuple[tuple[ResourceDemand, ...], ...]:
        by_resource: dict[str, list[ResourceDemand]] = {}
        for demand in demands:
            by_resource.setdefault(demand.resource_ref, []).append(demand)
        return tuple(
            tuple(sorted(items, key=lambda item: item.demand_id))
            for _, items in sorted(by_resource.items())
        )

    totals = tuple(
        _reconcile(
            scope_id="total",
            demands=items,
            availability=availability.get(items[0].resource_ref),
            unbounded=policy.unbounded_resources,
        )
        for items in grouped(policy.demands)
    )
    remaining = dict(availability)
    staged_lines: list[MaterialReconciliation] = []
    for scope in scopes:
        for items in grouped(tuple(demands_by_id[item] for item in scope.demand_ids)):
            resource_ref = items[0].resource_ref
            current = remaining.get(resource_ref)
            staged_lines.append(
                _reconcile(
                    scope_id=scope.stage_id,
                    demands=items,
                    availability=current,
                    unbounded=policy.unbounded_resources,
                )
            )
            if (
                current is not None
                and current.epistemic_status is not FactEpistemicStatus.UNKNOWN
                and not policy.unbounded_resources
            ):
                assert current.minimum_available is not None
                assert current.maximum_available is not None
                minimum_required = sum(item.minimum_required for item in items)
                maximum_required = sum(item.maximum_required for item in items)
                remaining[resource_ref] = replace(
                    current,
                    minimum_available=max(
                        0.0,
                        current.minimum_available - maximum_required,
                    ),
                    maximum_available=max(
                        0.0,
                        current.maximum_available - minimum_required,
                    ),
                )
    stage_needs = tuple(staged_lines)
    stage_needs = tuple(sorted(stage_needs, key=lambda item: (item.scope_id, item.resource_ref)))
    evidence_refs = tuple(
        sorted(set(policy.evidence_refs) | {ref for scope in scopes for ref in scope.evidence_refs})
    )
    return MaterialAccountReceipt(
        project_id=policy.project_id,
        run_id=policy.run_id,
        base=policy.base,
        build_policy_digest=policy.policy_digest,
        stage_scope_digest=_scope_digest(scopes),
        totals=totals,
        stage_needs=stage_needs,
        evidence_refs=evidence_refs,
    )


@dataclass(frozen=True, slots=True)
class BuildStage:
    stage_id: str
    phase_id: str
    layer_index: int
    predecessor_ids: tuple[str, ...]
    component_ids: tuple[str, ...]
    object_ids: tuple[str, ...]
    demand_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "BuildStage@1"

    def __post_init__(self) -> None:
        require_identifier(self.stage_id, "stage_id")
        require_identifier(self.phase_id, "phase_id")
        if (
            not isinstance(self.layer_index, int)
            or isinstance(self.layer_index, bool)
            or self.layer_index < 0
        ):
            raise StagedBuildError("layer_index must be a non-negative integer")
        _ids(self.predecessor_ids, "predecessor_ids", allow_empty=True)
        _ids(self.component_ids, "component_ids")
        _ids(self.object_ids, "object_ids")
        _ids(self.demand_ids, "demand_ids", allow_empty=True)
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def work_units(self) -> int:
        return len(self.object_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "stage_id": self.stage_id,
            "phase_id": self.phase_id,
            "layer_index": self.layer_index,
            "predecessor_ids": list(self.predecessor_ids),
            "component_ids": list(self.component_ids),
            "object_ids": list(self.object_ids),
            "demand_ids": list(self.demand_ids),
            "evidence_refs": list(self.evidence_refs),
            "work_units": self.work_units,
        }

    @classmethod
    def from_dict(cls, value: object) -> BuildStage:
        payload = _mapping(value, "build stage")
        _exact(
            payload,
            {
                "schema",
                "stage_id",
                "phase_id",
                "layer_index",
                "predecessor_ids",
                "component_ids",
                "object_ids",
                "demand_ids",
                "evidence_refs",
                "work_units",
            },
            "build stage",
        )
        stage = cls(
            stage_id=payload["stage_id"],
            phase_id=payload["phase_id"],
            layer_index=payload["layer_index"],
            predecessor_ids=tuple(payload["predecessor_ids"]),
            component_ids=tuple(payload["component_ids"]),
            object_ids=tuple(payload["object_ids"]),
            demand_ids=tuple(payload["demand_ids"]),
            evidence_refs=tuple(payload["evidence_refs"]),
        )
        if payload["schema"] != cls.SCHEMA or payload["work_units"] != stage.work_units:
            raise StagedBuildError("build stage work-unit contract drifted")
        return stage


@dataclass(frozen=True, slots=True)
class StagedBuildPlan:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    candidate_assembly_digest: str
    design_state_digest: str
    executable_plan_digest: str
    build_policy_digest: str
    geometry_program_digest: str
    scene_digest: str
    material_account_digest: str
    stage_scope_digest: str
    stages: tuple[BuildStage, ...]

    SCHEMA = "StagedBuildPlan@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef) or self.base.project_id != self.project_id:
            raise StagedBuildError("staged plan and base disagree")
        for field in (
            "candidate_assembly_digest",
            "design_state_digest",
            "executable_plan_digest",
            "build_policy_digest",
            "geometry_program_digest",
            "scene_digest",
            "material_account_digest",
            "stage_scope_digest",
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), field))
        if not isinstance(self.stages, tuple) or not self.stages or any(
            not isinstance(item, BuildStage) for item in self.stages
        ):
            raise TypeError("stages contains an invalid item")
        stage_ids = tuple(item.stage_id for item in self.stages)
        if len(stage_ids) != len(set(stage_ids)):
            raise StagedBuildError("build stage ids must be unique")
        seen: set[str] = set()
        object_ids: list[str] = []
        component_ids: list[str] = []
        demand_ids: list[str] = []
        for stage in self.stages:
            if not set(stage.predecessor_ids) <= seen:
                raise StagedBuildError("build stages are not topologically ordered")
            seen.add(stage.stage_id)
            object_ids.extend(stage.object_ids)
            component_ids.extend(stage.component_ids)
            demand_ids.extend(stage.demand_ids)
        for values, field in (
            (object_ids, "objects"),
            (component_ids, "components"),
            (demand_ids, "demands"),
        ):
            if len(values) != len(set(values)):
                raise StagedBuildError(f"{field} cannot be assigned to multiple stages")

    @property
    def plan_digest(self) -> str:
        return canonical_digest(self.to_dict(), ascii=False)

    @property
    def total_work_units(self) -> int:
        return sum(item.work_units for item in self.stages)

    def stage(self, stage_id: str) -> BuildStage:
        for item in self.stages:
            if item.stage_id == stage_id:
                return item
        raise StagedBuildError(f"unknown build stage: {stage_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_dict(self.base),
            "candidate_assembly_digest": self.candidate_assembly_digest,
            "design_state_digest": self.design_state_digest,
            "executable_plan_digest": self.executable_plan_digest,
            "build_policy_digest": self.build_policy_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "scene_digest": self.scene_digest,
            "material_account_digest": self.material_account_digest,
            "stage_scope_digest": self.stage_scope_digest,
            "stages": [item.to_dict() for item in self.stages],
            "total_work_units": self.total_work_units,
            "execution_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> StagedBuildPlan:
        payload = _mapping(value, "staged build plan")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "candidate_assembly_digest",
                "design_state_digest",
                "executable_plan_digest",
                "build_policy_digest",
                "geometry_program_digest",
                "scene_digest",
                "material_account_digest",
                "stage_scope_digest",
                "stages",
                "total_work_units",
                "execution_authority",
                "canonical_write_authority",
            },
            "staged build plan",
        )
        plan = cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            candidate_assembly_digest=payload["candidate_assembly_digest"],
            design_state_digest=payload["design_state_digest"],
            executable_plan_digest=payload["executable_plan_digest"],
            build_policy_digest=payload["build_policy_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            scene_digest=payload["scene_digest"],
            material_account_digest=payload["material_account_digest"],
            stage_scope_digest=payload["stage_scope_digest"],
            stages=tuple(BuildStage.from_dict(item) for item in payload["stages"]),
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["total_work_units"] != plan.total_work_units
        ):
            raise StagedBuildError("staged build plan authority or totals drifted")
        return plan


def compile_staged_build_plan(
    *,
    candidate: CandidateAssembly,
    build_policy: BuildPolicy,
    geometry_program: CompiledGeometryProgram,
    scene: HybridScene,
    material_account: MaterialAccountReceipt,
    scopes: tuple[StageScope, ...],
) -> StagedBuildPlan:
    """Bind semantic stage scopes to every materialized scene object exactly once."""

    _validate_scopes(build_policy, scopes)
    state = candidate.design_state
    if (
        build_policy.project_id != state.project_id
        or build_policy.run_id != state.run_id
        or build_policy.base != state.base
    ):
        raise StagedBuildError("candidate and build policy disagree")
    build_bindings = [
        item for item in candidate.policies if item.kind is CandidatePolicyKind.BUILD
    ]
    if len(build_bindings) != 1 or build_bindings[0].policy_digest != build_policy.policy_digest:
        raise StagedBuildError("candidate is not bound to this build policy")
    if (
        geometry_program.proposal.project_id != state.project_id
        or geometry_program.proposal.run_id != state.run_id
        or geometry_program.proposal.base != state.base
        or geometry_program.proposal.design_state_digest != state.state_digest
    ):
        raise StagedBuildError("geometry program is not bound to candidate design state")
    if (
        scene.project_id != state.project_id
        or scene.run_id != state.run_id
        or scene.base != state.base
        or scene.geometry_program_digest != geometry_program.program_digest
    ):
        raise StagedBuildError("scene is not the supplied geometry program")
    if (
        material_account.project_id != state.project_id
        or material_account.run_id != state.run_id
        or material_account.base != state.base
        or material_account.build_policy_digest != build_policy.policy_digest
        or material_account.stage_scope_digest != _scope_digest(scopes)
    ):
        raise StagedBuildError("material account is not bound to these stage scopes")

    binding_components = {
        item.binding_id: item.component_id
        for item in geometry_program.proposal.semantic_bindings
    }
    program_components = set(binding_components.values())
    assigned_components = [component for scope in scopes for component in scope.component_ids]
    if len(assigned_components) != len(set(assigned_components)) or set(assigned_components) != program_components:
        raise StagedBuildError("stage scopes must assign every geometry component exactly once")
    component_stage = {
        component: scope.stage_id for scope in scopes for component in scope.component_ids
    }
    objects_by_stage: dict[str, list[str]] = {item.stage_id: [] for item in scopes}
    for scene_object in scene.objects:
        try:
            stages = {
                component_stage[binding_components[binding_id]]
                for binding_id in scene_object.semantic_binding_ids
            }
        except KeyError as exc:
            raise StagedBuildError("scene contains an unbound semantic identity") from exc
        if len(stages) != 1:
            raise StagedBuildError("one scene object cannot span multiple build stages")
        objects_by_stage[stages.pop()].append(scene_object.object_id)
    stages = tuple(
        BuildStage(
            stage_id=scope.stage_id,
            phase_id=scope.phase_id,
            layer_index=scope.layer_index,
            predecessor_ids=scope.predecessor_ids,
            component_ids=scope.component_ids,
            object_ids=tuple(sorted(objects_by_stage[scope.stage_id])),
            demand_ids=scope.demand_ids,
            evidence_refs=scope.evidence_refs,
        )
        for scope in scopes
    )
    return StagedBuildPlan(
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        candidate_assembly_digest=candidate.assembly_digest,
        design_state_digest=state.state_digest,
        executable_plan_digest=candidate.plan.plan_digest,
        build_policy_digest=build_policy.policy_digest,
        geometry_program_digest=geometry_program.program_digest,
        scene_digest=scene.scene_digest,
        material_account_digest=material_account.account_digest,
        stage_scope_digest=_scope_digest(scopes),
        stages=stages,
    )


@dataclass(frozen=True, slots=True)
class StagedBuildCheckpoint:
    plan_digest: str
    sequence: int
    status: BuildRunStatus
    speed_units_per_tick: int
    current_stage_id: str | None
    current_phase_id: str | None
    current_layer_index: int | None
    current_stage_completed_units: int
    completed_stage_ids: tuple[str, ...]
    total_completed_units: int

    SCHEMA = "StagedBuildCheckpoint@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_digest", _sha(self.plan_digest, "plan_digest"))
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise StagedBuildError("sequence must be a non-negative integer")
        if not isinstance(self.status, BuildRunStatus):
            raise TypeError("status must be BuildRunStatus")
        if (
            not isinstance(self.speed_units_per_tick, int)
            or isinstance(self.speed_units_per_tick, bool)
            or self.speed_units_per_tick <= 0
        ):
            raise StagedBuildError("speed_units_per_tick must be positive")
        if not isinstance(self.completed_stage_ids, tuple):
            raise StagedBuildError("completed_stage_ids must be a tuple")
        for item in self.completed_stage_ids:
            require_identifier(item, "completed_stage_ids")
        if len(self.completed_stage_ids) != len(set(self.completed_stage_ids)):
            raise StagedBuildError("completed_stage_ids contains duplicates")
        for value, field in (
            (self.current_stage_completed_units, "current_stage_completed_units"),
            (self.total_completed_units, "total_completed_units"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise StagedBuildError(f"{field} must be a non-negative integer")
        current = (
            self.current_stage_id,
            self.current_phase_id,
            self.current_layer_index,
        )
        if self.current_stage_id is None:
            if current != (None, None, None) or self.current_stage_completed_units:
                raise StagedBuildError("empty current stage cannot retain phase progress")
        else:
            require_identifier(self.current_stage_id, "current_stage_id")
            require_identifier(self.current_phase_id, "current_phase_id")
            if (
                not isinstance(self.current_layer_index, int)
                or isinstance(self.current_layer_index, bool)
                or self.current_layer_index < 0
            ):
                raise StagedBuildError("current_layer_index must be non-negative")
        if self.status in {BuildRunStatus.RUNNING, BuildRunStatus.PAUSED} and self.current_stage_id is None:
            raise StagedBuildError("active checkpoint requires a current stage")
        if self.status in {BuildRunStatus.READY, BuildRunStatus.COMPLETED} and self.current_stage_id is not None:
            raise StagedBuildError("ready or completed checkpoint cannot have a current stage")

    @property
    def checkpoint_digest(self) -> str:
        return canonical_digest(self.to_dict(), ascii=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "plan_digest": self.plan_digest,
            "sequence": self.sequence,
            "status": self.status.value,
            "speed_units_per_tick": self.speed_units_per_tick,
            "current_stage_id": self.current_stage_id,
            "current_phase_id": self.current_phase_id,
            "current_layer_index": self.current_layer_index,
            "current_stage_completed_units": self.current_stage_completed_units,
            "completed_stage_ids": list(self.completed_stage_ids),
            "total_completed_units": self.total_completed_units,
            "world_write_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> StagedBuildCheckpoint:
        payload = _mapping(value, "staged build checkpoint")
        _exact(
            payload,
            {
                "schema",
                "plan_digest",
                "sequence",
                "status",
                "speed_units_per_tick",
                "current_stage_id",
                "current_phase_id",
                "current_layer_index",
                "current_stage_completed_units",
                "completed_stage_ids",
                "total_completed_units",
                "world_write_authority",
                "canonical_write_authority",
            },
            "staged build checkpoint",
        )
        if (
            payload["schema"] != cls.SCHEMA
        ):
            raise StagedBuildError("checkpoint authority drifted")
        return cls(
            plan_digest=payload["plan_digest"],
            sequence=payload["sequence"],
            status=BuildRunStatus(payload["status"]),
            speed_units_per_tick=payload["speed_units_per_tick"],
            current_stage_id=payload["current_stage_id"],
            current_phase_id=payload["current_phase_id"],
            current_layer_index=payload["current_layer_index"],
            current_stage_completed_units=payload["current_stage_completed_units"],
            completed_stage_ids=tuple(payload["completed_stage_ids"]),
            total_completed_units=payload["total_completed_units"],
        )


def _validate_checkpoint(plan: StagedBuildPlan, checkpoint: StagedBuildCheckpoint) -> None:
    if checkpoint.plan_digest != plan.plan_digest:
        raise StagedBuildError("checkpoint belongs to a different plan")
    stage_ids = tuple(item.stage_id for item in plan.stages)
    expected_completed = stage_ids[: len(checkpoint.completed_stage_ids)]
    if checkpoint.completed_stage_ids != expected_completed:
        raise StagedBuildError("completed stages are not an exact plan prefix")
    expected_total = sum(plan.stage(item).work_units for item in checkpoint.completed_stage_ids)
    if checkpoint.current_stage_id is not None:
        next_index = len(checkpoint.completed_stage_ids)
        if next_index >= len(plan.stages):
            raise StagedBuildError("checkpoint retains a stage after plan completion")
        stage = plan.stages[next_index]
        if (
            checkpoint.current_stage_id != stage.stage_id
            or checkpoint.current_phase_id != stage.phase_id
            or checkpoint.current_layer_index != stage.layer_index
            or checkpoint.current_stage_completed_units >= stage.work_units
        ):
            raise StagedBuildError("checkpoint phase or layer disagrees with plan")
        expected_total += checkpoint.current_stage_completed_units
    if checkpoint.total_completed_units != expected_total:
        raise StagedBuildError("checkpoint total progress disagrees with plan")
    if checkpoint.status is BuildRunStatus.COMPLETED and len(checkpoint.completed_stage_ids) != len(plan.stages):
        raise StagedBuildError("completed checkpoint has unfinished stages")
    if checkpoint.status is not BuildRunStatus.COMPLETED and len(checkpoint.completed_stage_ids) == len(plan.stages):
        raise StagedBuildError("finished plan must have completed status")


def validate_build_checkpoint(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
) -> None:
    """Public boundary validator used by save/reload packages."""

    _validate_checkpoint(plan, checkpoint)


def _expect(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    expected_checkpoint_digest: str,
) -> None:
    _validate_checkpoint(plan, checkpoint)
    if checkpoint.checkpoint_digest != _sha(expected_checkpoint_digest, "expected_checkpoint_digest"):
        raise StagedBuildError("stale staged-build control")


def create_build_checkpoint(
    plan: StagedBuildPlan,
    *,
    speed_units_per_tick: int = 1,
) -> StagedBuildCheckpoint:
    checkpoint = StagedBuildCheckpoint(
        plan_digest=plan.plan_digest,
        sequence=0,
        status=BuildRunStatus.READY,
        speed_units_per_tick=speed_units_per_tick,
        current_stage_id=None,
        current_phase_id=None,
        current_layer_index=None,
        current_stage_completed_units=0,
        completed_stage_ids=(),
        total_completed_units=0,
    )
    _validate_checkpoint(plan, checkpoint)
    return checkpoint


def _with_current(
    checkpoint: StagedBuildCheckpoint,
    stage: BuildStage | None,
    *,
    status: BuildRunStatus,
    current_units: int = 0,
    completed: tuple[str, ...] | None = None,
    total: int | None = None,
) -> StagedBuildCheckpoint:
    return replace(
        checkpoint,
        sequence=checkpoint.sequence + 1,
        status=status,
        current_stage_id=None if stage is None else stage.stage_id,
        current_phase_id=None if stage is None else stage.phase_id,
        current_layer_index=None if stage is None else stage.layer_index,
        current_stage_completed_units=current_units,
        completed_stage_ids=checkpoint.completed_stage_ids if completed is None else completed,
        total_completed_units=checkpoint.total_completed_units if total is None else total,
    )


def start_build(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    *,
    expected_checkpoint_digest: str,
) -> StagedBuildCheckpoint:
    _expect(plan, checkpoint, expected_checkpoint_digest)
    if checkpoint.status is not BuildRunStatus.READY:
        raise StagedBuildError("only a ready plan can start")
    result = _with_current(checkpoint, plan.stages[0], status=BuildRunStatus.RUNNING)
    _validate_checkpoint(plan, result)
    return result


def pause_build(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    *,
    expected_checkpoint_digest: str,
) -> StagedBuildCheckpoint:
    _expect(plan, checkpoint, expected_checkpoint_digest)
    if checkpoint.status is not BuildRunStatus.RUNNING:
        raise StagedBuildError("only a running plan can pause")
    result = replace(checkpoint, sequence=checkpoint.sequence + 1, status=BuildRunStatus.PAUSED)
    _validate_checkpoint(plan, result)
    return result


def resume_build(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    *,
    expected_checkpoint_digest: str,
) -> StagedBuildCheckpoint:
    _expect(plan, checkpoint, expected_checkpoint_digest)
    if checkpoint.status is not BuildRunStatus.PAUSED:
        raise StagedBuildError("only a paused plan can resume")
    result = replace(checkpoint, sequence=checkpoint.sequence + 1, status=BuildRunStatus.RUNNING)
    _validate_checkpoint(plan, result)
    return result


def set_build_speed(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    *,
    expected_checkpoint_digest: str,
    speed_units_per_tick: int,
) -> StagedBuildCheckpoint:
    _expect(plan, checkpoint, expected_checkpoint_digest)
    if checkpoint.status in {BuildRunStatus.CANCELLED, BuildRunStatus.COMPLETED}:
        raise StagedBuildError("terminal plan speed cannot change")
    result = replace(
        checkpoint,
        sequence=checkpoint.sequence + 1,
        speed_units_per_tick=speed_units_per_tick,
    )
    _validate_checkpoint(plan, result)
    return result


def advance_build(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    *,
    expected_checkpoint_digest: str,
    ticks: int = 1,
) -> StagedBuildCheckpoint:
    _expect(plan, checkpoint, expected_checkpoint_digest)
    if checkpoint.status is not BuildRunStatus.RUNNING:
        raise StagedBuildError("only a running plan can advance")
    if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks <= 0:
        raise StagedBuildError("ticks must be a positive integer")
    budget = ticks * checkpoint.speed_units_per_tick
    completed = list(checkpoint.completed_stage_ids)
    current_index = len(completed)
    current_units = checkpoint.current_stage_completed_units
    total = checkpoint.total_completed_units
    while budget and current_index < len(plan.stages):
        stage = plan.stages[current_index]
        if not set(stage.predecessor_ids) <= set(completed):
            raise StagedBuildError("stage predecessors are incomplete")
        remaining = stage.work_units - current_units
        consumed = min(budget, remaining)
        current_units += consumed
        total += consumed
        budget -= consumed
        if current_units == stage.work_units:
            completed.append(stage.stage_id)
            current_index += 1
            current_units = 0
    if current_index == len(plan.stages):
        result = _with_current(
            checkpoint,
            None,
            status=BuildRunStatus.COMPLETED,
            completed=tuple(completed),
            total=total,
        )
    else:
        result = _with_current(
            checkpoint,
            plan.stages[current_index],
            status=BuildRunStatus.RUNNING,
            current_units=current_units,
            completed=tuple(completed),
            total=total,
        )
    _validate_checkpoint(plan, result)
    return result


def cancel_build(
    plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    *,
    expected_checkpoint_digest: str,
) -> StagedBuildCheckpoint:
    _expect(plan, checkpoint, expected_checkpoint_digest)
    if checkpoint.status in {BuildRunStatus.CANCELLED, BuildRunStatus.COMPLETED}:
        raise StagedBuildError("terminal plan cannot be cancelled")
    result = replace(checkpoint, sequence=checkpoint.sequence + 1, status=BuildRunStatus.CANCELLED)
    _validate_checkpoint(plan, result)
    return result
