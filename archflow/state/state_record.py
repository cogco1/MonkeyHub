"""Canonical design State Record (P102).

One record carries a project's design state as data: entities of typed
schemas (level, grid axis, type, element, assembly, space, reading),
parameters (named quantities with basis and lifecycle), relations (the
architectural relation vocabulary with datum roles, propagation rules and
validator bindings), obligations (open duties, separate from relations),
evidence and provenance. Geometry programs, validation results, receipts and
indexes are derived from it and never authoritative. It carries no stage: a
stage is a property of the run that executes the record, stated by that run's
retained ``StageRunEnvelope`` (ADR-007).

The record is the canonical abstraction that retires the second design
state model used by geometry production. Until every consumer reads it
directly, ``developed_design_view`` forwards a record to the legacy
``DevelopedDesignState`` the producer and the seats still take; the view is
an adapter with a lineage note, scheduled for retirement with them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, cast

from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.project.refs import ProjectVersionRef, RunRef, require_identifier
from archflow.state.derivation import (
    DerivationError,
    DerivationTable,
    DerivedQuantity,
    EvaluatedDerivations,
    evaluate,
    expression_names,
    substitute,
)
from archflow.state.operational_state import DependencyEdge, DependencyEffect, DesignObligation
from archflow.relations.contracts import ArchitecturalRelationKind
from archflow.semantics.conditions import CONDITION_IDS
from archflow.semantics.registry import resolve_semantic_kind, suggest_semantic
from archflow.semantics.roles import ROLE_IDS
from archflow.state.design_portfolio import BranchRevisionRef
from archflow.state.developed_design import DevelopedDesignState, DevelopmentCoordinationStatus, SelectedSchematicInput
from archflow.state.spatial import (
    DesignComponent,
    MassingVolume,
    SchematicOption,
    SiteBounds,
    SpatialConnection,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.project.version_refs import (
    register as _register_version_refs,
    register_derived as _register_derived_fields,
)

# The one phase this module names, and the only thing it is for:
# ``StateRecord.state_digest``. A record states no stage (ADR-007 rule 1),
# so the record's own binding identity - the staleness token an exact-base
# operator cites, and the only value ever compared against it - cannot come
# from an envelope. It is read in one stated phase, named here so that no
# call site quietly supplies one. It is not a default for anybody else: a
# run's state digest comes from ``developed_design_view`` with the phase that
# run's envelope states, which the caller passes and this module never fills
# in.
RECORD_BINDING_PHASE: DesignPhase = DesignPhase.DESIGN_DEVELOPMENT

_RELATION_KINDS = frozenset(kind.value for kind in ArchitecturalRelationKind)   # one vocabulary: the kernel's
# What each schema's readers take off ``fields`` by key; a record that lacks them is refused
# here instead of failing as a KeyError inside a producer or a projection.
_REQUIRED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "Level@1": ("role", "elevation"), "GridAxis@1": ("role", "origin", "direction"), "Element@1": ("producer",),
    "Space@1": ("program_node_refs", "level_ids", "volume_ids"), "Volume@1": ("min", "max", "level_ids"),
    "MassingLevel@1": ("base_y", "height"), "Connection@1": ("source_zone_id", "target_zone_id", "relationship_refs"),
}
_ENTITY_SCHEMAS = frozenset({"Level@1", "GridAxis@1", "Type@1", "Element@1", "Assembly@1", "Space@1", "Reading@1", "Component@1",
                             "MassingLevel@1", "Volume@1", "Connection@1"})   # Space@1 = a zone of the spatial option
_EPISTEMIC = frozenset({"observed", "declared", "derived", "hypothesis", "disputed", "unknown"})
_MAX_ITEMS = 50_000

CHECK_KINDS: Mapping[str, str] = MappingProxyType({
    "support_contact": "the subject supports the object: contact within tolerance",
    "clearance_interval": "the gap between subject and object lies in interval_m",
    "aperture_exists": "the object opening lies within the subject host's extent and has geometry",
    "lintel_minimum_bearing": "the declared axis-aligned lintel bounds meet minimum bearing at both opening span ends, align bottom to opening head and overlap transversely",
    "solid_nonpenetration": "explicit final solid pairs have no positive common volume; contact and separation are allowed",
})
"""The checks the spine can measure: check_kind id -> one line of meaning.

A ``ValidatorBinding`` names one of these and nothing else. A kind the record
accepts but no checker measures reports ``unchecked`` forever, which reads as
verification and is not; ``capabilities.relation_checks.CHECKERS`` is keyed by
exactly these ids and says so at import.
"""

_INTERVAL_KINDS = frozenset({"clearance_interval"})   # these take interval_m; every other kind takes a tolerance


class StateRecordError(ValueError):
    """Typed failure of the state record contracts."""


def _refs(values: object, field_name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(values, tuple) or any(not isinstance(v, str) or not v for v in values):
        raise StateRecordError(f"{field_name} must be a tuple of non-empty text")
    if not values and not allow_empty:
        raise StateRecordError(f"{field_name} must not be empty")
    if tuple(sorted(set(values))) != values:
        raise StateRecordError(f"{field_name} must be sorted and unique")
    return values


@dataclass(frozen=True, slots=True)
class Lineage:
    """Where an item came from and when it changed: stage identity across versions."""

    introduced_at: str | None = None
    inherited_from: str | None = None
    revised_at: str | None = None
    retired_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"introduced_at": self.introduced_at, "inherited_from": self.inherited_from, "revised_at": self.revised_at, "retired_at": self.retired_at}

    @classmethod
    def from_dict(cls, value: object) -> "Lineage":
        value = value or {}
        return cls(value.get("introduced_at"), value.get("inherited_from"), value.get("revised_at"), value.get("retired_at"))


@dataclass(frozen=True, slots=True)
class Entity:
    """One typed entity: a level, a grid axis, a type, an element, an assembly, a space, a reading."""

    entity_id: str
    schema: str
    fields: Mapping[str, Any]
    parent_id: str | None = None
    basis_refs: tuple[str, ...] = ()
    lineage: Lineage = field(default_factory=Lineage)

    def __post_init__(self) -> None:
        require_identifier(self.entity_id, "entity_id")
        if self.schema not in _ENTITY_SCHEMAS:
            raise StateRecordError(f"entity {self.entity_id}: unknown schema {self.schema!r}")
        if not isinstance(self.fields, Mapping):
            raise StateRecordError(f"entity {self.entity_id}: fields must be a mapping")
        missing = [k for k in _REQUIRED_FIELDS.get(self.schema, ()) if k not in self.fields]
        if missing:
            raise StateRecordError(f"entity {self.entity_id} ({self.schema}): missing required fields {missing}")
        if self.parent_id is not None:
            require_identifier(self.parent_id, "parent_id")
        _refs(self.basis_refs, f"entity {self.entity_id} basis_refs")
        object.__setattr__(self, "fields", dict(self.fields))

    @property
    def ref(self) -> str:
        return f"entity:{self.entity_id}"

    def to_dict(self) -> dict[str, object]:
        return {"entity_id": self.entity_id, "schema": self.schema, "parent_id": self.parent_id, "fields": dict(self.fields),
                "basis_refs": list(self.basis_refs), "lineage": self.lineage.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Entity":
        return cls(value["entity_id"], value["schema"], value.get("fields", {}), value.get("parent_id"), tuple(value.get("basis_refs", ())), Lineage.from_dict(value.get("lineage")))


@dataclass(frozen=True, slots=True)
class Parameter:
    """A named quantity with its expression, value, basis and lifecycle."""

    key: str
    value: float
    unit: str
    expr: str | None = None
    inputs: tuple[str, ...] = ()
    epistemic_status: str = "derived"
    source_ref: str | None = None
    lock_authority: str | None = None
    lineage: Lineage = field(default_factory=Lineage)

    def __post_init__(self) -> None:
        require_identifier(self.key, "parameter key")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise StateRecordError(f"parameter {self.key}: value must be a number")
        if self.epistemic_status not in _EPISTEMIC:
            raise StateRecordError(f"parameter {self.key}: invalid epistemic status")
        _refs(self.inputs, f"parameter {self.key} inputs")

    @property
    def ref(self) -> str:
        return f"parameter:{self.key}"

    def reads(self) -> tuple[str, ...]:
        """The parameters this one depends on, as declared: what its expression names.

        The expression is the declaration; ``inputs`` may restate it (and is
        refused where it disagrees) but an empty ``inputs`` does not make an
        expression's dependencies vanish. Without an expression the declared
        ``inputs`` are all there is. A malformed expression is a typed error
        naming the parameter, never an empty answer.
        """

        if self.expr is None:
            return self.inputs
        try:
            return expression_names(self.expr, f"parameter {self.key}")
        except DerivationError as exc:
            raise _located(exc) from exc

    def to_dict(self) -> dict[str, object]:
        return {"key": self.key, "value": self.value, "unit": self.unit, "expr": self.expr, "inputs": list(self.inputs), "epistemic_status": self.epistemic_status,
                "source_ref": self.source_ref, "lock_authority": self.lock_authority, "lineage": self.lineage.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Parameter":
        return cls(value["key"], value["value"], value["unit"], value.get("expr"), tuple(value.get("inputs", ())), value.get("epistemic_status", "derived"),
                   value.get("source_ref"), value.get("lock_authority"), Lineage.from_dict(value.get("lineage")))


@dataclass(frozen=True, slots=True)
class ValidatorBinding:
    """How a relation is verified after materialization."""

    check_kind: str
    tolerance: float | None = None
    interval_m: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if self.check_kind not in CHECK_KINDS:
            raise StateRecordError(f"unknown check kind {self.check_kind!r}: the spine measures {', '.join(sorted(CHECK_KINDS))}")
        if self.check_kind in _INTERVAL_KINDS:
            if self.tolerance is not None:
                raise StateRecordError(f"check {self.check_kind} takes interval_m, not a tolerance")
            if (not isinstance(self.interval_m, tuple) or len(self.interval_m) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in self.interval_m)):
                raise StateRecordError(f"check {self.check_kind} needs an interval_m of two finite numbers in metres")
            low, high = (float(v) for v in self.interval_m)
            if low > high:
                raise StateRecordError(f"check {self.check_kind}: interval_m low {low} is above high {high}")
            object.__setattr__(self, "interval_m", (low, high))
            return
        if self.interval_m is not None:
            raise StateRecordError(f"check {self.check_kind} takes a tolerance, not an interval_m")
        if self.tolerance is None:
            return
        if isinstance(self.tolerance, bool) or not isinstance(self.tolerance, (int, float)) or not math.isfinite(self.tolerance) or self.tolerance < 0.0:
            raise StateRecordError(f"check {self.check_kind}: tolerance must be a non-negative number of metres")
        object.__setattr__(self, "tolerance", float(self.tolerance))
        if self.check_kind == "solid_nonpenetration" and self.tolerance != 0.0:
            raise StateRecordError("solid_nonpenetration takes zero tolerance: a length tolerance cannot excuse positive common volume")

    def to_dict(self) -> dict[str, object]:
        return {"check_kind": self.check_kind, "tolerance": self.tolerance, "interval_m": list(self.interval_m) if self.interval_m else None}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidatorBinding":
        interval = value.get("interval_m")
        return cls(value["check_kind"], value.get("tolerance"), tuple(interval) if interval else None)


@dataclass(frozen=True, slots=True)
class Relation:
    """A present-tense architectural relation with its three bindings.

    ``datum_role`` is what generation binds to; ``propagation`` says what a
    change at the subject does to the object (revalidate / invalidate /
    unchanged); ``validator`` says how the relation is verified. Duties that
    the relation creates are obligations, recorded separately.
    """

    relation_id: str
    kind: str
    subject: str
    object: str
    datum_role: str | None = None
    propagation: str = "revalidate"
    validator: ValidatorBinding | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    epistemic_status: str = "declared"
    basis_refs: tuple[str, ...] = ()
    lineage: Lineage = field(default_factory=Lineage)

    def __post_init__(self) -> None:
        require_identifier(self.relation_id, "relation_id")
        require_identifier(self.kind, "relation kind")
        if self.kind not in _RELATION_KINDS:
            raise StateRecordError(f"relation {self.relation_id}: kind {self.kind!r} is not in the kernel relation vocabulary")
        require_identifier(self.subject, "relation subject")
        require_identifier(self.object, "relation object")
        if self.subject == self.object and not (self.validator is not None and self.validator.check_kind == "solid_nonpenetration"):
            raise StateRecordError(f"relation {self.relation_id}: subject and object must differ")
        if self.propagation not in {"unchanged", "revalidate", "invalidate"}:
            raise StateRecordError(f"relation {self.relation_id}: invalid propagation")
        if self.epistemic_status not in _EPISTEMIC:
            raise StateRecordError(f"relation {self.relation_id}: invalid epistemic status")
        if self.datum_role is not None:
            require_identifier(self.datum_role, "datum_role")
        _refs(self.basis_refs, f"relation {self.relation_id} basis_refs")
        object.__setattr__(self, "parameters", dict(self.parameters))
        if self.validator is not None and self.validator.check_kind == "lintel_minimum_bearing":
            if self.kind != "dependency":
                raise StateRecordError(f"relation {self.relation_id}: lintel_minimum_bearing requires kind dependency")
            required = {"opening_object_id", "lintel_object_id", "span_axis", "minimum_bearing_m"}
            if set(self.parameters) != required:
                raise StateRecordError(f"relation {self.relation_id}: lintel_minimum_bearing requires exactly {', '.join(sorted(required))}")
            for name in ("opening_object_id", "lintel_object_id"):
                require_identifier(self.parameters[name], f"lintel_minimum_bearing {name}")
            if self.parameters["opening_object_id"] == self.parameters["lintel_object_id"]:
                raise StateRecordError(f"relation {self.relation_id}: opening and lintel must name distinct objects")
            if self.parameters["span_axis"] not in ("x", "z"):
                raise StateRecordError(f"relation {self.relation_id}: lintel_minimum_bearing span_axis must be x or z")
            bearing = self.parameters["minimum_bearing_m"]
            try:
                finite_bearing = isinstance(bearing, (int, float)) and not isinstance(bearing, bool) and math.isfinite(bearing)
            except OverflowError:
                finite_bearing = False
            if not finite_bearing or bearing < 0:
                raise StateRecordError(f"relation {self.relation_id}: minimum_bearing_m must be a non-negative finite number of metres")
        if self.validator is not None and self.validator.check_kind == "solid_nonpenetration":
            pairs = self.parameters.get("object_pairs")
            if not isinstance(pairs, (list, tuple)) or not pairs:
                raise StateRecordError(f"relation {self.relation_id}: solid_nonpenetration requires explicit final object_pairs")
            for pair in pairs:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2 or pair[0] == pair[1]:
                    raise StateRecordError(f"relation {self.relation_id}: each object pair must name two distinct final objects")
                for object_id in pair:
                    require_identifier(object_id, "solid_nonpenetration object id")

    def to_dict(self) -> dict[str, object]:
        return {"relation_id": self.relation_id, "kind": self.kind, "subject": self.subject, "object": self.object, "datum_role": self.datum_role,
                "propagation": self.propagation, "validator": self.validator.to_dict() if self.validator else None, "parameters": dict(self.parameters),
                "epistemic_status": self.epistemic_status, "basis_refs": list(self.basis_refs), "lineage": self.lineage.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Relation":
        validator = value.get("validator")
        return cls(value["relation_id"], value["kind"], value["subject"], value["object"], value.get("datum_role"), value.get("propagation", "revalidate"),
                   ValidatorBinding.from_dict(validator) if validator else None, value.get("parameters", {}), value.get("epistemic_status", "declared"),
                   tuple(value.get("basis_refs", ())), Lineage.from_dict(value.get("lineage")))

    def dependency_edge(self) -> DependencyEdge | None:
        """The relation as a kernel dependency edge (subject → object)."""

        if self.propagation == "unchanged" or self.subject == self.object:
            return None
        effect = DependencyEffect.INVALIDATES if self.propagation == "invalidate" else DependencyEffect.REQUIRES_REVALIDATION
        return DependencyEdge(upstream_ref=f"entity:{self.subject}", downstream_ref=f"entity:{self.object}", relation=self.kind,
                              source_ref=f"relation:{self.relation_id}", effect=effect)


@dataclass(frozen=True)
class StateRecord:
    project_id: str
    run_id: str
    entities: tuple[Entity, ...]
    parameters: tuple[Parameter, ...] = ()
    relations: tuple[Relation, ...] = ()
    obligations: tuple[DesignObligation, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    basis_refs: tuple[str, ...] = ()
    predecessor_ref: str | None = None
    decision_ref: str | None = None
    invalidated_refs: tuple[str, ...] = ()
    option: Mapping[str, Any] = field(default_factory=dict)     # the declared selection: option_id, label, typology, rationale, footprint_cells, assumption_refs
    base: ProjectVersionRef | None = None                       # the canonical version this record was authored against

    SCHEMA = "StateRecord@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        for name, items, kind in (("entities", self.entities, Entity), ("parameters", self.parameters, Parameter), ("relations", self.relations, Relation),
                                  ("obligations", self.obligations, DesignObligation)):
            if not isinstance(items, tuple) or any(not isinstance(i, kind) for i in items):
                raise StateRecordError(f"{name} must be {kind.__name__} items")
            if len(items) > _MAX_ITEMS:
                raise StateRecordError(f"{name} exceeds the bounded item count")
        ids = [e.entity_id for e in self.entities]
        if len(set(ids)) != len(ids):
            raise StateRecordError("entity ids must be unique")
        known = set(ids)
        for e in self.entities:
            if e.parent_id is not None and e.parent_id not in known:
                raise StateRecordError(f"entity {e.entity_id}: unknown parent {e.parent_id!r}")
        keys = [p.key for p in self.parameters]
        parameter_keys = set(keys)
        if len(parameter_keys) != len(keys):
            raise StateRecordError("parameter keys must be unique")
        for p in self.parameters:
            for item in p.inputs:
                if item not in parameter_keys:
                    raise StateRecordError(f"parameter {p.key}: unknown input {item!r}")
        for e in self.entities:
            if e.schema == "Element@1" and "type_ref" in e.fields:
                _element_fields(self, e)
            for path, name in parameter_bindings_of(e):
                if name not in parameter_keys:
                    raise StateRecordError(f"entity {e.entity_id}: {path} binds @{name}, which names no parameter (parameters: {', '.join(sorted(keys)) or 'none'})")
        rids = [r.relation_id for r in self.relations]
        if len(set(rids)) != len(rids):
            raise StateRecordError("relation ids must be unique")
        for r in self.relations:
            for end in (r.subject, r.object):
                if end not in known:
                    raise StateRecordError(f"relation {r.relation_id}: unknown entity {end!r}")
        _refs(self.evidence_refs, "evidence_refs"); _refs(self.basis_refs, "basis_refs"); _refs(self.invalidated_refs, "invalidated_refs")
        if self.base is not None:
            if not isinstance(self.base, ProjectVersionRef):
                raise StateRecordError("base must be a ProjectVersionRef")
            if self.base.project_id != self.project_id:
                raise StateRecordError("base belongs to another project")
        if not isinstance(self.option, Mapping):
            raise StateRecordError("option must be a mapping")
        object.__setattr__(self, "option", dict(self.option))
        if self.option and not isinstance(self.option.get("option_id"), str):
            raise StateRecordError("option needs an option_id")
        for zone in self.entities_of("Space@1"):
            for volume_id in zone.fields.get("volume_ids", ()):
                if volume_id not in known:
                    raise StateRecordError(f"zone {zone.entity_id}: unknown volume {volume_id!r}")
        for e in self.entities:
            for name in ("level_ids",):
                for ref in e.fields.get(name, ()):
                    if ref not in known:
                        raise StateRecordError(f"entity {e.entity_id}: {name} names unknown entity {ref!r}")
            grid_roles = {str(g.fields.get("role")) for g in self.entities_of("GridAxis@1")}
            for key, kind, target in _entity_references(e.fields):
                if kind == "entity" and target not in known:
                    raise StateRecordError(f"entity {e.entity_id}: reference {key} names no entity {target!r}")
                if kind == "grid_role" and target not in grid_roles:
                    raise StateRecordError(f"entity {e.entity_id}: reference {key} names no grid axis role {target!r}")
        declared_relations = {f"relation:{r.relation_id}" for r in self.relations}
        for connection in self.entities_of("Connection@1"):
            for end in ("source_zone_id", "target_zone_id"):
                if connection.fields.get(end) not in known:
                    raise StateRecordError(f"connection {connection.entity_id}: unknown zone {connection.fields.get(end)!r}")
            for ref in connection.fields.get("relationship_refs", ()):
                if ref not in declared_relations:
                    raise StateRecordError(f"connection {connection.entity_id}: relationship_ref {ref!r} names no declared relation")
        for component in self.entities_of("Component@1"):
            kind = component.fields.get("semantic_kind")
            roles, conditions = component.fields.get("roles", ()), component.fields.get("conditions", ())
            if kind is None and not (roles or conditions):
                raise StateRecordError(f"component {component.entity_id}: names no semantics (roles/conditions ids, or a semantic_kind that resolves)")
            if kind is not None:
                if not isinstance(kind, str) or not kind or kind != kind.strip() or "+" in kind or "." in kind:
                    raise StateRecordError(f"component {component.entity_id}: semantic_kind must be one registered alias or phrase in local-id form; ids go in roles/conditions")
                if resolve_semantic_kind(kind) is None:
                    near = ", ".join(suggest_semantic(kind)) or "none close"
                    raise StateRecordError(f"component {component.entity_id}: semantic_kind {kind!r} is not a registered role, condition or alias; nearest: {near}")
            for field_name, allowed in (("roles", ROLE_IDS), ("conditions", CONDITION_IDS)):
                for item in component.fields.get(field_name, ()):
                    if item not in allowed:
                        raise StateRecordError(f"component {component.entity_id}: {field_name} names {item!r}, which is not registered; nearest: {', '.join(suggest_semantic(item)) or 'none close'}")

    # ---- views
    def entity(self, entity_id: str) -> Entity:
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        raise StateRecordError(f"unknown entity {entity_id!r}")

    def entities_of(self, schema: str) -> tuple[Entity, ...]:
        return tuple(e for e in self.entities if e.schema == schema)

    def parameter(self, key: str) -> Parameter:
        for p in self.parameters:
            if p.key == key:
                return p
        raise StateRecordError(f"unknown parameter {key!r}")

    def dependency_edges(self) -> tuple[DependencyEdge, ...]:
        """Relations, parameter expressions and entity references as kernel edges (closure input).

        A derived parameter depends on exactly the names its expression reads
        (``Parameter.reads``), so the closure, the protected-ref and lock checks
        and the recomputation after an edit all follow one declaration; a
        parameter whose ``inputs`` array was left empty is not thereby cut off
        from its sources.
        """

        edges = [e for e in (r.dependency_edge() for r in self.relations) if e is not None]
        for p in self.parameters:
            for item in p.reads():
                edges.append(DependencyEdge(upstream_ref=f"parameter:{item}", downstream_ref=p.ref, relation="derives",
                                            source_ref=p.source_ref or f"parameter:{p.key}", effect=DependencyEffect.REQUIRES_REVALIDATION))
        element_ids = {e.entity_id for e in self.entities_of("Element@1")}
        for e in self.entities:
            for key, kind, target in _entity_references(e.fields):
                if kind != "entity":
                    continue
                effect = DependencyEffect.INVALIDATES if key in ("host", "type_ref") or target in element_ids else DependencyEffect.REQUIRES_REVALIDATION
                edges.append(DependencyEdge(upstream_ref=f"entity:{target}", downstream_ref=e.ref, relation=key, source_ref=e.ref, effect=effect))
            for path, name in parameter_bindings_of(e):
                # an explicit "@key" binding: the parameter's value is the row's value, so a change there rebuilds the row
                edges.append(DependencyEdge(upstream_ref=f"parameter:{name}", downstream_ref=e.ref, relation="binds", source_ref=e.ref, effect=DependencyEffect.INVALIDATES))
        return tuple(edges)

    def closure(self, changed_refs: tuple[str, ...]) -> tuple[str, ...]:
        """Everything downstream of ``changed_refs`` along invalidating edges (P063 semantics)."""

        return self.closures((changed_refs,))[0]

    def closures(self, changed_ref_groups: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
        """Independent closures over one dependency graph, in the supplied group order."""

        if not changed_ref_groups:
            return ()
        adjacency: dict[str, set[str]] = {}
        for edge in self.dependency_edges():
            if edge.effect not in (DependencyEffect.INVALIDATES, DependencyEffect.REQUIRES_REVALIDATION):
                continue  # BLOCKS and SUPPORTS_ONLY do not propagate a change downstream
            adjacency.setdefault(edge.upstream_ref, set()).add(edge.downstream_ref)
        results: list[tuple[str, ...]] = []
        for changed_refs in changed_ref_groups:
            seen = set(changed_refs)
            queue = sorted(changed_refs)
            while queue:
                current = queue.pop(0)
                for downstream in sorted(adjacency.get(current, ())):
                    if downstream not in seen:
                        seen.add(downstream)
                        queue.append(downstream)
            results.append(tuple(sorted(seen)))
        return tuple(results)

    # ---- identity the geometry compiler checks (P102)
    def bound_to(self, run: RunRef) -> "StateRecord":
        """The authored (portable) record bound to one run's identity and canonical base.

        An authored record carries no ``base``; whoever projects or executes it
        binds it first. The runner does this before its first write; a
        read-only projection (Studio) binds against the repository HEAD the
        same way. This is the one sanctioned path — never a hand-built base.
        """

        if not isinstance(run, RunRef):
            raise StateRecordError("bound_to needs a RunRef")
        if run.project_id != self.project_id:
            raise StateRecordError("cannot bind a record to a run of another project")
        from dataclasses import replace as _replace

        return _replace(self, run_id=run.run_id, base=run.base)

    @property
    def run_ref(self) -> RunRef:
        """The run this record was authored in; needs ``base``."""

        if self.base is None:
            raise StateRecordError("this record carries no base: it cannot name its own run")
        return RunRef(self.project_id, self.run_id, self.base)

    @property
    def state_digest(self) -> str:
        """The digest of the developed-design projection this record yields.

        The compiler binds a program to a state by this digest. The
        projection is a pure function of the record, so citing it cites the
        record; the value is computed once and kept.

        The phase is ``RECORD_BINDING_PHASE`` and is stated there: this
        property has no run envelope to read one from, and the only values
        ever compared with it - an operator's exact base, a program sheet's
        declared state - are this same property. A run's own digest is the
        projection under that run's envelope phase, and is not taken here.
        """

        cached = getattr(self, "_state_digest_cache", None)
        if cached is None:
            cached = developed_design_view(self, run=self.run_ref, phase=RECORD_BINDING_PHASE).state_digest
            object.__setattr__(self, "_state_digest_cache", cached)
        return cached

    # ---- serialization
    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "project_id": self.project_id, "run_id": self.run_id, "entities": [e.to_dict() for e in self.entities],
                "parameters": [p.to_dict() for p in self.parameters], "relations": [r.to_dict() for r in self.relations],
                "obligations": [o.to_dict() for o in self.obligations], "evidence_refs": list(self.evidence_refs), "basis_refs": list(self.basis_refs),
                "predecessor_ref": self.predecessor_ref, "decision_ref": self.decision_ref, "invalidated_refs": list(self.invalidated_refs),
                "option": dict(self.option),
                "base": None if self.base is None else {"project_id": self.base.project_id, "version": self.base.version, "state_sha256": self.base.state_sha256}}

    @classmethod
    def from_dict(cls, value: object) -> "StateRecord":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise StateRecordError("state record payload malformed")
        if "stage" in value:
            raise StateRecordError("stage is the run's, stated by its envelope (ADR-007); remove the key")
        return cls(value["project_id"], value["run_id"], tuple(Entity.from_dict(e) for e in value["entities"]),
                   tuple(Parameter.from_dict(p) for p in value.get("parameters", ())), tuple(Relation.from_dict(r) for r in value.get("relations", ())),
                   tuple(DesignObligation.from_dict(o) for o in value.get("obligations", ())), tuple(value.get("evidence_refs", ())), tuple(value.get("basis_refs", ())),
                   value.get("predecessor_ref"), value.get("decision_ref"), tuple(value.get("invalidated_refs", ())),
                   dict(value.get("option", {})),
                   None if value.get("base") is None else ProjectVersionRef(value["base"]["project_id"], int(value["base"]["version"]), value["base"].get("state_sha256")))

    @property
    def digest(self) -> str:
        """Content identity: the design content, independent of the run and base
        it is bound to. ``state_digest`` is the binding identity."""

        content = self.to_dict()
        for binding_key in ("run_id", "base"):  # what binds the record, not what it says (ADR-003)
            del content[binding_key]
        return canonical_digest(content)


# ---------------------------------------------------------------- typed views of the record

def _entity_references(fields: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    """(key, kind, id) for every reference that names something: ``level`` and ``offset_from.level``
    name a Level@1, ``host.element`` names an Element@1 (kind ``entity``); grid labels name
    GridAxis@1 roles (kind ``grid_role``); the flat keys base_level/top_level/sill_level/host/type_ref
    name entities. Other strings (a direction, a face) name nothing."""

    out: list[tuple[str, str, str]] = []
    for key in ("base_level", "top_level", "sill_level", "host", "type_ref"):
        target = fields.get(key)
        if isinstance(target, str):
            if key == "type_ref":
                target = target.removeprefix("entity:")
            out.append((key, "entity", target))

    def walk(key: str, value: object) -> None:
        if not isinstance(value, Mapping):
            return
        for kind, payload in value.items():
            if kind == "level" and isinstance(payload, str):
                out.append((key, "entity", payload))
            elif kind == "datum" and isinstance(payload, str):
                # a datum another element published (``<element>-top``) names that element
                out.append((key, "entity", payload[:-4] if payload.endswith("-top") else payload))
            elif kind == "offset_from" and isinstance(payload, Mapping) and isinstance(payload.get("level"), str):
                out.append((key, "entity", payload["level"]))
            elif kind == "host" and isinstance(payload, Mapping) and isinstance(payload.get("element"), str):
                out.append((key, "entity", payload["element"]))
            elif kind == "grid":
                labels = payload if isinstance(payload, (list, tuple)) else [payload]
                out.extend((key, "grid_role", str(label)) for label in labels)
            elif kind == "axis_point" and isinstance(payload, Mapping) and "axis" in payload:
                out.append((key, "grid_role", str(payload["axis"])))
            elif isinstance(payload, Mapping):
                walk(key, payload)

    for key, value in fields.get("references", {}).items():
        walk(key, value)
    return tuple(out)


def design_components_of(record: StateRecord, *, source_ref: str | None = None) -> tuple:
    """The record's ``Component@1`` entities as the semantic component tree.

    The geometry compiler digests this tree per component, so the record must
    answer the question itself. An entity that carries a full
    ``DesignComponent@1`` payload is taken exactly; one that carries only the
    semantics a record needs (kind, intent, volumes) is completed with the
    schematic defaults. This is the only builder: the developed-design
    projection uses it too, so there is one component tree, not two.
    """

    from archflow.state.spatial import ComponentMaturity, DesignComponent

    out = []
    for e in record.entities_of("Component@1"):
        fields = dict(e.fields)
        payload = {**fields, "component_id": e.entity_id, "parent_component_id": e.parent_id}
        if payload.get("schema") == DesignComponent.SCHEMA:
            out.append(DesignComponent.from_dict(payload))
            continue
        refs = tuple(sorted({*(fields.get("source_refs") or ()), *((source_ref,) if source_ref else ()), *record.evidence_refs}))
        if not refs:
            raise StateRecordError(f"component {e.entity_id} has no source: give the entity source_refs, or the record evidence")
        out.append(DesignComponent(
            component_id=e.entity_id, parent_component_id=e.parent_id, semantic_kind=str(fields.get("semantic_kind") or "-".join(i.split(".", 1)[1].replace("_", "-") for i in (*fields.get("roles", ()), *fields.get("conditions", ()))) or "component"),
            intent=str(fields.get("intent", e.entity_id)), maturity=ComponentMaturity(str(fields.get("maturity", "schematic"))),
            revision=int(fields.get("revision", 1)), volume_ids=tuple(fields.get("volume_ids", ())),
            unresolved_child_roles=tuple(fields.get("unresolved_child_roles", ())), source_refs=refs))
    return tuple(out)


def project_levels_of(record: StateRecord, *, published_by: str = "seat-coordination"):
    """The record's Level@1 entities as the published project levels (P098)."""

    from archflow.state.geometry_program import ProjectLevel, ProjectLevels

    levels = tuple(sorted((ProjectLevel(e.entity_id, str(e.fields["role"]), float(e.fields["elevation"]), tuple(e.basis_refs)) for e in record.entities_of("Level@1")),
                          key=lambda l: l.level_id))
    if not levels:
        raise StateRecordError("the record carries no Level@1 entity")
    return ProjectLevels(project_id=record.project_id, published_by=published_by, levels=levels)


def volume_boxes_of(record: StateRecord) -> dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Every ``Volume@1``'s declared box, keyed by volume id: ``(min, max)``.

    The kernel's massing frame, stated once here because more than one reader
    needs it: a box is two coordinate triples in the voxel lattice
    ``state.spatial.SiteBounds`` defines, **x and z are plan and y is up**, and
    both ends are *inclusive* cells — ``SiteBounds.volume`` multiplies
    ``max - min + 1`` per axis, and ``SpatialLevel.top_y`` is
    ``base_y + height - 1`` for the same reason. One plan cell is one square
    metre: ``schematic_proposal`` declares
    ``SpatialGridBasis(horizontal_area_per_cell=1.0, area_unit="square_metres")``.

    Floats, because that is the reading ``project_runner`` hands to the
    relation checker. A measurement that needs whole cells says so itself
    rather than rounding here.
    """

    return {
        entity.entity_id: (
            tuple(float(v) for v in entity.fields["min"]),   # type: ignore[misc]
            tuple(float(v) for v in entity.fields["max"]),   # type: ignore[misc]
        )
        for entity in record.entities_of("Volume@1")
    }


def project_grids_of(record: StateRecord, *, published_by: str = "seat-coordination"):
    """The record's GridAxis@1 entities as the published project grids (P098); None when there are none."""

    from archflow.state.geometry_program import ProjectGridAxis, ProjectGrids

    axes = tuple(sorted((ProjectGridAxis(e.entity_id, str(e.fields["role"]), tuple(float(v) for v in e.fields["origin"]), tuple(float(v) for v in e.fields["direction"]), tuple(e.basis_refs))
                         for e in record.entities_of("GridAxis@1")), key=lambda a: a.axis_id))
    if not axes:
        return None
    return ProjectGrids(project_id=record.project_id, published_by=published_by, axes=axes)


# ---------------------------------------------------------------- parameters: declared expressions, explicit bindings, one evaluator
_BINDING_PREFIX = "@"


def _binding_paths(value: object, path: str) -> list[tuple[str, str]]:
    """(field path, parameter key) for every ``"@key"`` string inside a JSON-like value."""

    if isinstance(value, str):
        return [(path, value[len(_BINDING_PREFIX):])] if value.startswith(_BINDING_PREFIX) else []
    if isinstance(value, Mapping):
        return [b for key, item in value.items() for b in _binding_paths(item, f"{path}.{key}")]
    if isinstance(value, (list, tuple)):
        return [b for index, item in enumerate(value) for b in _binding_paths(item, f"{path}[{index}]")]
    return []


def _element_fields(record: StateRecord, entity: Entity) -> dict[str, Any]:
    """The element's declared fields with its type defaults, before parameter evaluation."""

    fields = dict(entity.fields)
    if "type_ref" not in fields:
        return fields
    type_ref = fields["type_ref"]
    if not isinstance(type_ref, str) or not type_ref:
        raise StateRecordError(f"element {entity.entity_id}: type_ref must name a Type@1")
    type_id = type_ref.removeprefix("entity:")
    declared_type = next((item for item in record.entities if item.entity_id == type_id), None)
    if declared_type is None or declared_type.schema != "Type@1":
        raise StateRecordError(f"element {entity.entity_id}: type_ref names no Type@1 {type_ref!r}")
    if not isinstance(declared_type.fields.get("producer"), str) or declared_type.fields["producer"] != fields["producer"]:
        raise StateRecordError(f"element {entity.entity_id}: producer must match type {type_id}")
    for name in ("params", "references"):
        defaults, overrides = declared_type.fields.get(name, {}), fields.get(name, {})
        if not isinstance(defaults, Mapping) or not isinstance(overrides, Mapping):
            raise StateRecordError(f"element {entity.entity_id}: type and element {name} must be mappings")
        if name in declared_type.fields or name in fields:
            fields[name] = {**defaults, **overrides}
    fields["type_ref"] = type_id
    return fields


def parameter_bindings_of(entity: Entity, record: StateRecord | None = None) -> tuple[tuple[str, str], ...]:
    """Explicit ``"@key"`` bindings on Element@1/Type@1 params and references, as (field path, parameter key).

    A binding is the only way a row reads a parameter. A numeric literal in a
    row is a literal: nothing here guesses that a ``height`` of 2.97 "means"
    the parameter that happens to evaluate to 2.97, and no retained row is
    rebound. Supplying the record includes an element's inherited type bindings;
    other schemas carry no bindings.
    """

    if entity.schema not in {"Element@1", "Type@1"}:
        return ()
    fields = _element_fields(record, entity) if record is not None and entity.schema == "Element@1" else entity.fields
    out: list[tuple[str, str]] = []
    for field_name in ("params", "references"):
        out.extend(_binding_paths(fields.get(field_name, {}), field_name))
    return tuple(out)


def _located(exc: DerivationError) -> StateRecordError:
    text = str(exc).replace("quantities", "parameters").replace("quantity", "parameter")
    return StateRecordError(f"parameters: {text}")


def derivation_table_of(record: StateRecord) -> tuple[DerivationTable, dict[str, float]]:
    """The record's parameters as the derivation engine reads them: an ``expr`` makes a quantity, no ``expr`` makes a reading.

    A parameter that declares ``inputs`` must declare exactly the names its
    expression reads; the two are one dependency stated twice, and a
    disagreement is a conflicting declaration, refused here naming the key.
    An empty ``inputs`` beside an expression is accepted: the expression is
    the declaration, and ``Parameter.reads`` is what every dependency reader
    (edges, closure, locks, recomputation) takes off it.
    """

    quantities: list[DerivedQuantity] = []
    readings: dict[str, float] = {}
    for p in record.parameters:
        if p.expr is None:
            readings[p.key] = float(p.value)
            continue
        try:
            reads = expression_names(p.expr, f"parameter {p.key}")
            if p.inputs and set(p.inputs) != set(reads):
                raise StateRecordError(f"parameter {p.key}: declared inputs {list(p.inputs)} disagree with its expression {p.expr!r}, which reads {list(reads)}")
            quantities.append(DerivedQuantity(p.key, p.expr, p.unit or "-", (p.source_ref,) if p.source_ref else (), p.epistemic_status))
        except DerivationError as exc:
            raise _located(exc) from exc
    try:
        return DerivationTable(record.project_id, tuple(quantities)), readings
    except DerivationError as exc:
        raise _located(exc) from exc


def evaluate_parameters(record: StateRecord) -> EvaluatedDerivations:
    """Every parameter's value by its declaration: readings as stored, derived ones re-evaluated in dependency order.

    The one evaluator is ``state.derivation.evaluate``; a cycle, an unknown
    name, a division by zero or a conflicting ``inputs`` declaration is a
    typed ``StateRecordError`` naming the parameter.
    """

    table, readings = derivation_table_of(record)
    try:
        return evaluate(table, readings)
    except DerivationError as exc:
        raise _located(exc) from exc


def stale_parameters(record: StateRecord, evaluated: EvaluatedDerivations | None = None) -> tuple[str, ...]:
    """The derived parameters whose stored value disagrees with what their expression evaluates to, by key.

    A stored derived value is what the expression last evaluated to; the
    expression is the declaration. Nothing is repaired here - an edit that
    reaches the parameter recomputes it (``apply_state_record_operator``),
    and a producer refuses to read a stale bound value.
    """

    evaluated = evaluated if evaluated is not None else evaluate_parameters(record)
    return tuple(sorted(p.key for p in record.parameters
                        if p.expr is not None and not math.isclose(float(p.value), evaluated[p.key], rel_tol=1e-9, abs_tol=1e-9)))


def resolve_element_bindings(record: StateRecord) -> dict[str, dict[str, Any]]:
    """Every ``Element@1``'s fields with its explicit ``@key`` bindings replaced by the evaluated parameter value, by entity id.

    Type defaults and instance overrides are shallow-merged per params/references
    dictionary; lists such as openings are replaced whole. This is the producers'
    and Studio's one input projection of the record's parameters.
    Bindings only: a literal stays exactly what the row said, and a record
    with no binding is returned as authored without evaluating anything.
    Where a binding exists, the bound parameter and every parameter its
    expression needs must agree with their declarations; a stale stored
    value among them is refused here, located at the binding, rather than
    read as either number.
    """

    elements = record.entities_of("Element@1")
    fields_by_id = {e.entity_id: _element_fields(record, e) for e in elements}
    bindings = {e.entity_id: parameter_bindings_of(replace(e, fields=fields_by_id[e.entity_id])) for e in elements}
    if not any(bindings.values()):
        return fields_by_id
    evaluated = evaluate_parameters(record)
    stale = set(stale_parameters(record, evaluated))
    by_key = {p.key: p for p in record.parameters}
    needed_at: dict[str, str] = {}                  # parameter key -> the first binding that needs it

    def need(name: str, at: str) -> None:
        if name in needed_at:
            return
        needed_at[name] = at
        for item in by_key[name].reads():
            need(item, at)

    for e in elements:
        for path, name in bindings[e.entity_id]:
            need(name, f"element {e.entity_id}: {path} binds @{name}")
    for name in sorted(needed_at):
        if name in stale:
            p = by_key[name]
            raise StateRecordError(f"{needed_at[name]}: stored value {p.value} of derived parameter {name} disagrees with its expression {p.expr!r} = {evaluated[name]}; "
                                   "apply the change through its inputs (which recomputes it) or correct the declaration")
    out: dict[str, dict[str, Any]] = {}
    for e in elements:
        fields = fields_by_id[e.entity_id]
        if bindings[e.entity_id]:
            for field_name in ("params", "references"):
                if field_name in fields:
                    fields[field_name] = substitute(fields[field_name], evaluated)
        out[e.entity_id] = fields
    return out


# ---------------------------------------------------------------- adapter to the legacy model (scheduled for retirement)
# ---------------------------------------------------------------- schematic pack -> developed state
@dataclass(frozen=True, slots=True)
class SchematicPack:
    project_id: str
    option_id: str
    label: str
    typology: str
    rationale: str
    evidence_refs: tuple[str, ...]
    levels: tuple[dict, ...]
    volumes: tuple[dict, ...]
    zones: tuple[dict, ...]
    connections: tuple[dict, ...]
    components: tuple[DesignComponent, ...]
    footprint_cells: tuple[tuple[int, int], ...]
    assumption_refs: tuple[str, ...] = ()

    SCHEMA = "SchematicPack@1"

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SchematicPack":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise StateRecordError("schematic pack payload malformed")
        return cls(
            project_id=value["project_id"], option_id=value["option_id"], label=value["label"], typology=value["typology"],
            rationale=value["rationale"], evidence_refs=tuple(sorted(set(value["evidence_refs"]))),
            levels=tuple(value["levels"]), volumes=tuple(value["volumes"]), zones=tuple(value["zones"]), connections=tuple(value["connections"]),
            components=tuple(DesignComponent.from_dict(c) for c in value["components"]),
            footprint_cells=tuple((int(x), int(z)) for x, z in value["footprint_cells"]),
            assumption_refs=tuple(value.get("assumption_refs", ())),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "project_id": self.project_id, "option_id": self.option_id, "label": self.label, "typology": self.typology,
            "rationale": self.rationale, "evidence_refs": list(self.evidence_refs), "levels": list(self.levels), "volumes": list(self.volumes),
            "zones": list(self.zones), "connections": list(self.connections), "components": [c.to_dict() for c in self.components],
            "footprint_cells": [list(c) for c in self.footprint_cells], "assumption_refs": list(self.assumption_refs),
        }


class StateRecordEditKind(StrEnum):
    """The StateRecord edits that have production consumers today."""

    SET_SCALAR = "set_scalar"
    REPLACE_MASSING = "replace_massing"
    APPLY_PROGRAM = "apply_program"
    REINDEX = "reindex"
    EDIT_COMPONENTS = "edit_components"
    SET_PARAMETER_LOCKS = "set_parameter_locks"


@dataclass(frozen=True, slots=True)
class StateRecordOperator:
    """One exact-base, typed request to derive a successor StateRecord.

    This is deliberately not a general patch language.  Its closed kinds are
    exactly the edits currently compiled by the Studio scalar and massing
    paths, semantic component editing, the program sheet, and element re-indexing.
    """

    kind: StateRecordEditKind
    base_record_digest: str
    base_state_digest: str
    protected: tuple[str, ...] = ()
    target_ref: str | None = None
    key: str | None = None
    value: int | float | None = None
    massing_pack: SchematicPack | None = None
    entities: tuple[Entity, ...] = ()
    relations: tuple[Relation, ...] = ()
    basis_refs: tuple[str, ...] = ()
    parameters: tuple[Parameter, ...] = ()
    remove_entity_ids: tuple[str, ...] = ()
    remove_parameter_keys: tuple[str, ...] = ()
    remove_relation_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Retain the executable change, including its exact original base."""
        return {
            "kind": self.kind.value,
            "base_record_digest": self.base_record_digest,
            "base_state_digest": self.base_state_digest,
            "protected": list(self.protected), "target_ref": self.target_ref,
            "key": self.key, "value": self.value,
            "massing_pack": None if self.massing_pack is None else self.massing_pack.to_dict(),
            "entities": [item.to_dict() for item in self.entities],
            "relations": [item.to_dict() for item in self.relations],
            "basis_refs": list(self.basis_refs),
            "parameters": [item.to_dict() for item in self.parameters],
            "remove_entity_ids": list(self.remove_entity_ids),
            "remove_parameter_keys": list(self.remove_parameter_keys),
            "remove_relation_ids": list(self.remove_relation_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StateRecordOperator":
        if not isinstance(value, Mapping) or set(value) != {
            "kind", "base_record_digest", "base_state_digest", "protected", "target_ref",
            "key", "value", "massing_pack", "entities", "relations", "basis_refs", "parameters",
            "remove_entity_ids", "remove_parameter_keys", "remove_relation_ids",
        }:
            raise StateRecordError("state-record operator fields are invalid")
        return cls(
            kind=StateRecordEditKind(value["kind"]),
            base_record_digest=value["base_record_digest"], base_state_digest=value["base_state_digest"],
            protected=tuple(value["protected"]), target_ref=value["target_ref"], key=value["key"], value=value["value"],
            massing_pack=None if value["massing_pack"] is None else SchematicPack.from_dict(value["massing_pack"]),
            entities=tuple(Entity.from_dict(item) for item in value["entities"]),
            relations=tuple(Relation.from_dict(item) for item in value["relations"]),
            basis_refs=tuple(value["basis_refs"]),
            parameters=tuple(Parameter.from_dict(item) for item in value["parameters"]),
            remove_entity_ids=tuple(value["remove_entity_ids"]),
            remove_parameter_keys=tuple(value["remove_parameter_keys"]),
            remove_relation_ids=tuple(value["remove_relation_ids"]),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StateRecordEditKind):
            raise TypeError("state-record operator kind is invalid")
        if (
            not isinstance(self.base_record_digest, str)
            or len(self.base_record_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.base_record_digest.lower())
        ):
            raise StateRecordError("operator base_record_digest must be a SHA-256 hex digest")
        if (
            not isinstance(self.base_state_digest, str)
            or len(self.base_state_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.base_state_digest.lower())
        ):
            raise StateRecordError("operator base_state_digest must be a SHA-256 hex digest")
        _refs(self.protected, "operator protected refs")
        if any(
            not ref.startswith(("entity:", "parameter:"))
            for ref in self.protected
        ):
            raise StateRecordError("operator protected refs must name an entity or parameter")
        if not isinstance(self.entities, tuple) or any(not isinstance(item, Entity) for item in self.entities):
            raise TypeError("operator entities must be Entity items")
        if not isinstance(self.relations, tuple) or any(not isinstance(item, Relation) for item in self.relations):
            raise TypeError("operator relations must be Relation items")
        if not isinstance(self.parameters, tuple) or any(not isinstance(item, Parameter) for item in self.parameters):
            raise TypeError("operator parameters must be Parameter items")
        _refs(self.basis_refs, "operator basis_refs")
        for name in ("remove_entity_ids", "remove_parameter_keys", "remove_relation_ids"):
            _refs(getattr(self, name), f"operator {name}")
            for identifier in getattr(self, name):
                require_identifier(identifier, name)

        scalar = self.kind is StateRecordEditKind.SET_SCALAR
        massing = self.kind is StateRecordEditKind.REPLACE_MASSING
        graph = self.kind in (StateRecordEditKind.APPLY_PROGRAM, StateRecordEditKind.REINDEX, StateRecordEditKind.EDIT_COMPONENTS)
        if scalar:
            if (
                not isinstance(self.target_ref, str)
                or not self.target_ref.startswith(("entity:", "parameter:"))
                or not isinstance(self.key, str)
                or not self.key
                or isinstance(self.value, bool)
                or not isinstance(self.value, (int, float))
            ):
                raise StateRecordError("set_scalar needs one entity/parameter target, key and numeric value")
        elif self.target_ref is not None or self.key is not None or self.value is not None:
            raise StateRecordError(f"{self.kind.value} cannot carry a scalar target")
        if massing != (self.massing_pack is not None):
            raise StateRecordError("replace_massing alone carries a SchematicPack")
        if not graph and (self.entities or self.relations):
            raise StateRecordError(f"{self.kind.value} cannot carry entity or relation edits")
        if self.kind is not StateRecordEditKind.REINDEX and self.basis_refs:
            raise StateRecordError("only reindex can add record basis refs")
        if self.kind is StateRecordEditKind.SET_PARAMETER_LOCKS:
            if not self.parameters or self.remove_entity_ids or self.remove_parameter_keys or self.remove_relation_ids:
                raise StateRecordError("set_parameter_locks needs existing parameters and cannot remove graph items")
            if len({p.key for p in self.parameters}) != len(self.parameters):
                raise StateRecordError("set_parameter_locks parameter keys must be unique")
            return
        component_edits = self.parameters or self.remove_entity_ids or self.remove_parameter_keys or self.remove_relation_ids
        if self.kind is not StateRecordEditKind.EDIT_COMPONENTS and component_edits:
            raise StateRecordError("only edit_components can edit parameters or remove graph items")
        if self.kind is StateRecordEditKind.EDIT_COMPONENTS and not (self.entities or self.relations or component_edits):
            raise StateRecordError("edit_components needs at least one edit")


def compile_component_edit(
    record: StateRecord,
    *,
    entities: tuple[Entity, ...] = (),
    parameters: tuple[Parameter, ...] = (),
    relations: tuple[Relation, ...] = (),
    remove_entity_ids: tuple[str, ...] = (),
    remove_parameter_keys: tuple[str, ...] = (),
    remove_relation_ids: tuple[str, ...] = (),
    protected: tuple[str, ...] = (),
) -> StateRecordOperator:
    """Compile named semantic edits against this record's exact content and binding."""

    return StateRecordOperator(
        kind=StateRecordEditKind.EDIT_COMPONENTS,
        base_record_digest=record.digest,
        base_state_digest=record.state_digest,
        entities=entities,
        parameters=parameters,
        relations=relations,
        remove_entity_ids=tuple(sorted(remove_entity_ids)),
        remove_parameter_keys=tuple(sorted(remove_parameter_keys)),
        remove_relation_ids=tuple(sorted(remove_relation_ids)),
        protected=tuple(sorted(protected)),
    )


def compile_parameter_locks(
    record: StateRecord, *, parameter_keys: tuple[str, ...], lock_authority: str | None,
) -> StateRecordOperator:
    """Compile an explicit lock/unlock; the application authorizes its caller.

    Only lock metadata may change. This value grants no project-write access;
    the existing candidate runner persists its successor.
    """
    if lock_authority is not None and (not isinstance(lock_authority, str) or not lock_authority.strip()):
        raise StateRecordError("lock authority must be nonempty text")
    return StateRecordOperator(
        kind=StateRecordEditKind.SET_PARAMETER_LOCKS,
        base_record_digest=record.digest, base_state_digest=record.state_digest,
        parameters=tuple(replace(record.parameter(key), lock_authority=lock_authority) for key in parameter_keys),
    )


def combine_component_changes(
    record: StateRecord, candidates: tuple[StateRecord, ...], *, protected: tuple[str, ...] = (),
) -> StateRecordOperator:
    """Normalize independent component changes into one operator on their common base.

    The supplied candidates have already been replayed by the caller. This
    comparison uses their materialized content, including declared dependency
    effects, so neither source operator has its base digest rewritten.
    """
    if len(candidates) < 2:
        raise StateRecordError("combine needs at least two candidate results")
    changed_sets: list[set[str]] = []
    closures: list[set[str]] = []
    write_sets: list[set[str]] = []
    edges = tuple(edge for state in (record, *candidates) for edge in state.dependency_edges())

    def reached(changed: set[str], effects: tuple[DependencyEffect, ...]) -> set[str]:
        result = set(changed)
        while True:
            expanded = result | {edge.downstream_ref for edge in edges
                                 if edge.effect in effects and edge.upstream_ref in result}
            if expanded == result:
                return result
            result = expanded

    edits: dict[str, dict[str, Any]] = {"entities": {}, "parameters": {}, "relations": {}}
    removals: dict[str, set[str]] = {name: set() for name in edits}
    for candidate in candidates:
        for field_name in ("project_id", "base", "obligations", "evidence_refs", "basis_refs",
                           "predecessor_ref", "decision_ref", "invalidated_refs", "option"):
            if getattr(candidate, field_name) != getattr(record, field_name):
                raise StateRecordError(f"combine cannot normalize changes to {field_name} as component edits")
        changed = set(_changed_refs(record, candidate))
        closure = reached(changed, (DependencyEffect.INVALIDATES, DependencyEffect.REQUIRES_REVALIDATION))
        writes = reached(changed, (DependencyEffect.INVALIDATES,))
        for previous, reached, written in zip(changed_sets, closures, write_sets):
            conflicts = (changed & reached) | (previous & closure) | (written & writes)
            if conflicts:
                raise StateRecordError("candidate changes overlap or depend on each other: " + ", ".join(sorted(conflicts)))
        changed_sets.append(changed)
        closures.append(closure)
        write_sets.append(writes)
        for name, identity in (("entities", "entity_id"), ("parameters", "key"), ("relations", "relation_id")):
            before = {getattr(item, identity): item for item in getattr(record, name)}
            after = {getattr(item, identity): item for item in getattr(candidate, name)}
            edits[name].update({key: item for key, item in after.items() if before.get(key) != item})
            removals[name].update(before.keys() - after.keys())
    operator = compile_component_edit(
        record, entities=tuple(edits["entities"].values()), parameters=tuple(edits["parameters"].values()),
        relations=tuple(edits["relations"].values()), remove_entity_ids=tuple(removals["entities"]),
        remove_parameter_keys=tuple(removals["parameters"]), remove_relation_ids=tuple(removals["relations"]),
        protected=protected,
    )
    apply_state_record_operator(record, operator)
    return operator


_MASSING_SCHEMAS = frozenset(
    {"MassingLevel@1", "Volume@1", "Space@1", "Connection@1"}
)


def apply_state_record_operator(
    record: StateRecord, operator: StateRecordOperator
) -> StateRecord:
    """Apply the one canonical StateRecord operator, or refuse it typed.

    Exact-base, protected closure and parameter locks are checked here for all
    edit kinds.  Domain modules only compile an operator; none of them
    constructs a successor record.
    """

    if not isinstance(record, StateRecord):
        raise TypeError("record must be a StateRecord")
    if not isinstance(operator, StateRecordOperator):
        raise TypeError("operator must be a StateRecordOperator")
    if (
        operator.base_record_digest != record.digest
        or operator.base_state_digest != record.state_digest
    ):
        raise StateRecordError("state-record operator exact base is stale")
    _require_declared_protections(record, operator.protected)

    if operator.kind is StateRecordEditKind.SET_PARAMETER_LOCKS:
        updates = {}
        for parameter in operator.parameters:
            previous = record.parameter(parameter.key)
            if parameter.lock_authority is not None and (
                not isinstance(parameter.lock_authority, str) or not parameter.lock_authority.strip()
            ):
                raise StateRecordError("lock authority must be nonempty text")
            if replace(parameter, lock_authority=previous.lock_authority) != previous:
                raise StateRecordError("set_parameter_locks can change only lock metadata")
            if previous.lock_authority == parameter.lock_authority:
                raise StateRecordError(f"parameter {parameter.key}: lock action makes no change")
            if previous.lock_authority and parameter.lock_authority:
                raise StateRecordError(f"parameter {parameter.key}: unlock before assigning another lock")
            if parameter.ref in operator.protected:
                raise StateRecordError(f"state-record operator reaches protected refs: {parameter.ref}")
            updates[parameter.key] = parameter
        return replace(record, parameters=tuple(updates.get(p.key, p) for p in record.parameters))

    if operator.kind is StateRecordEditKind.SET_SCALAR:
        successor = _apply_scalar_operator(record, operator)
    elif operator.kind is StateRecordEditKind.REPLACE_MASSING:
        successor = _apply_massing_operator(
            record, cast(SchematicPack, operator.massing_pack)
        )
    elif operator.kind is StateRecordEditKind.APPLY_PROGRAM:
        successor = _apply_program_operator(record, operator.entities, operator.relations)
    elif operator.kind is StateRecordEditKind.REINDEX:
        successor = _apply_reindex_operator(
            record, operator.entities, operator.relations, operator.basis_refs
        )
    elif operator.kind is StateRecordEditKind.EDIT_COMPONENTS:
        successor = _apply_component_operator(record, operator)

    changed = _changed_refs(record, successor)
    closure = (set(record.closure(changed)) | set(successor.closure(changed))) if changed else set()
    conflicts = tuple(sorted(closure & set(operator.protected)))
    if conflicts:
        raise StateRecordError(
            "state-record operator reaches protected refs: " + ", ".join(conflicts)
        )
    locks = tuple(
        sorted(
            f"{parameter.ref} ({parameter.lock_authority})"
            for parameter in record.parameters
            if parameter.lock_authority and parameter.ref in closure
        )
    )
    if locks:
        raise StateRecordError(
            "state-record operator reaches locked parameters: " + ", ".join(locks)
        )
    for parameter in successor.parameters:
        previous = next((p for p in record.parameters if p.key == parameter.key), None)
        if parameter.lock_authority != (previous.lock_authority if previous else None):
            raise StateRecordError("parameter lock changes require the explicit set_parameter_locks operator")
    _require_locked_bindings(record, successor)
    return _refresh_derived_parameters(successor, changed)


def _require_locked_bindings(record: StateRecord, successor: StateRecord) -> None:
    """Keep existing uses of locked controls without freezing their consumers.

    Compare resolved field paths, including inherited Type defaults: replacing
    an @key with a literal or another key, or deleting its consumer, cannot
    silently evade the lock. Other fields and new consumers remain editable.
    """
    locked = {p.key for p in record.parameters if p.lock_authority}
    if not locked:
        return
    after = {e.entity_id: e for e in successor.entities}
    for entity in record.entities:
        bindings = {(path, key) for path, key in parameter_bindings_of(entity, record) if key in locked}
        if not bindings:
            continue
        replacement = after.get(entity.entity_id)
        remaining = set(parameter_bindings_of(replacement, successor)) if replacement else set()
        lost = bindings - remaining
        if lost:
            raise StateRecordError("state-record operator detaches locked parameter bindings: " + ", ".join(
                f"{entity.ref}.{path} (@{key})" for path, key in sorted(lost)
            ))
    for parameter in record.parameters:
        reads = set(parameter.reads()) & locked
        if reads:
            replacement = next((p for p in successor.parameters if p.key == parameter.key), None)
            if replacement is None or not reads.issubset(replacement.reads()):
                raise StateRecordError(f"state-record operator detaches locked parameter inputs: {parameter.ref}")


def _refresh_derived_parameters(record: StateRecord, changed: tuple[str, ...]) -> StateRecord:
    """The successor with every derived parameter downstream of the edit re-evaluated; nothing else moves.

    The expression is the declaration and the stored value is what it last
    evaluated to, so an edit upstream re-evaluates it through the one
    derivation engine and the successor says one thing. A derived parameter
    the edit does not reach keeps its stored value: an unrelated edit does
    not silently rewrite a locked or otherwise stale declaration elsewhere.
    """

    if not changed:
        return record
    downstream = set(record.closure(changed))
    targets = {p.key for p in record.parameters if p.expr is not None and p.ref in downstream}
    if not targets:
        return record
    evaluated = evaluate_parameters(record)
    return replace(record, parameters=tuple(
        replace(p, value=round(evaluated[p.key], 9)) if p.key in targets else p for p in record.parameters
    ))


def _require_declared_protections(record: StateRecord, protected: tuple[str, ...]) -> None:
    declared = {entity.ref for entity in record.entities} | {
        parameter.ref for parameter in record.parameters
    }
    unknown = tuple(ref for ref in protected if ref not in declared)
    if unknown:
        raise StateRecordError(
            "state-record operator protects unknown refs: " + ", ".join(unknown)
        )


def _apply_scalar_operator(
    record: StateRecord, operator: StateRecordOperator
) -> StateRecord:
    target_ref = cast(str, operator.target_ref)
    key = cast(str, operator.key)
    value = cast(int | float, operator.value)
    if target_ref.startswith("parameter:"):
        parameter_key = target_ref.removeprefix("parameter:")
        if parameter_key != key:
            raise StateRecordError("parameter target and scalar key disagree")
        parameter = record.parameter(parameter_key)
        if parameter.expr is not None:
            # a value stated directly against its own expression is a conflicting declaration
            raise StateRecordError(
                f"parameter {parameter_key} is derived by {parameter.expr!r} from {list(parameter.reads())}: "
                "edit its inputs, or re-declare it without an expression"
            )
        parameters = tuple(
            replace(item, value=value)
            if item.key == parameter_key
            else item
            for item in record.parameters
        )
        return replace(record, parameters=parameters)

    entity_id = target_ref.removeprefix("entity:")
    entity = record.entity(entity_id)
    if entity.schema != "Element@1":
        raise StateRecordError("set_scalar entity target must be an Element@1")
    params = _element_fields(record, entity).get("params")
    if not isinstance(params, Mapping) or key not in params:
        raise StateRecordError(
            f"element {entity_id}: params has no field {key!r}"
        )
    old = params[key]
    if isinstance(old, str) and old.startswith(_BINDING_PREFIX):
        raise StateRecordError(
            f"element {entity_id}: params.{key} is bound to parameter {old[len(_BINDING_PREFIX):]}; edit that parameter"
        )
    if isinstance(old, bool) or not isinstance(old, (int, float)):
        raise StateRecordError(
            f"element {entity_id}: params.{key} is not numeric"
        )
    replacement = replace(
        entity,
        fields={**entity.fields, "params": {**entity.fields.get("params", {}), key: value}},
    )
    return replace(
        record,
        entities=tuple(
            replacement if item.entity_id == entity_id else item
            for item in record.entities
        ),
    )


def _apply_massing_operator(record: StateRecord, pack: SchematicPack) -> StateRecord:
    if pack.project_id != record.project_id:
        raise StateRecordError("schematic pack belongs to another project")
    existing = {entity.entity_id: entity for entity in record.entities}
    evidence = tuple(sorted(set(record.evidence_refs)))

    def entity(entity_id: str, schema: str, fields: Mapping[str, Any]) -> Entity:
        previous = existing.get(entity_id)
        if previous is not None and previous.schema == schema:
            return replace(previous, fields={**previous.fields, **fields})
        return Entity(entity_id, schema, dict(fields), basis_refs=evidence)

    replacements = {
        **{
            level["level_id"]: entity(
                level["level_id"],
                "MassingLevel@1",
                {"base_y": level["base_y"], "height": level["height"]},
            )
            for level in pack.levels
        },
        **{
            volume["volume_id"]: entity(
                volume["volume_id"],
                "Volume@1",
                {
                    "min": list(volume["min"]),
                    "max": list(volume["max"]),
                    "level_ids": list(volume["level_ids"]),
                },
            )
            for volume in pack.volumes
        },
        **{
            zone["zone_id"]: entity(
                zone["zone_id"],
                "Space@1",
                {
                    "program_node_refs": list(zone["program_node_refs"]),
                    "level_ids": list(zone["level_ids"]),
                    "volume_ids": list(zone["volume_ids"]),
                },
            )
            for zone in pack.zones
        },
        **{
            connection["connection_id"]: entity(
                connection["connection_id"],
                "Connection@1",
                {
                    "source_zone_id": connection["source_zone_id"],
                    "target_zone_id": connection["target_zone_id"],
                    "relationship_refs": list(connection["relationship_refs"]),
                    "directed": bool(connection.get("directed", False)),
                },
            )
            for connection in pack.connections
        },
    }
    owned = {
        component.component_id: list(component.volume_ids)
        for component in pack.components
    }
    kept: list[Entity] = []
    seen: set[str] = set()
    for item in record.entities:
        if item.schema == "Component@1" and item.entity_id in owned:
            volume_ids = owned[item.entity_id]
            kept.append(
                item
                if list(item.fields.get("volume_ids", ())) == volume_ids
                else replace(item, fields={**item.fields, "volume_ids": volume_ids})
            )
            continue
        if item.schema not in _MASSING_SCHEMAS:
            kept.append(item)
            continue
        replacement = replacements.get(item.entity_id)
        if replacement is not None:
            kept.append(replacement)
            seen.add(item.entity_id)
    kept.extend(
        item for entity_id, item in replacements.items() if entity_id not in seen
    )
    return replace(
        record,
        entities=tuple(kept),
        option={
            **dict(record.option),
            "option_id": pack.option_id,
            "label": pack.label,
            "typology": pack.typology,
            "rationale": pack.rationale,
            "footprint_cells": [list(cell) for cell in pack.footprint_cells],
            "assumption_refs": list(pack.assumption_refs),
        },
    )


def _apply_program_operator(
    record: StateRecord,
    edits: tuple[Entity, ...],
    additions: tuple[Relation, ...],
) -> StateRecord:
    existing = {entity.entity_id: entity for entity in record.entities}
    for edit in edits:
        previous = existing.get(edit.entity_id)
        if previous is None:
            if edit.schema not in {"Space@1", "Connection@1"}:
                raise StateRecordError("program operator can add only Space@1 or Connection@1")
            continue
        if previous.schema != "Space@1" or edit.schema != "Space@1":
            raise StateRecordError("program operator can replace only an existing Space@1")
        previous_fields = dict(previous.fields)
        next_fields = dict(edit.fields)
        previous_refs = tuple(previous_fields.pop("program_node_refs", ()))
        next_refs = tuple(next_fields.pop("program_node_refs", ()))
        if (
            edit.parent_id != previous.parent_id
            or edit.basis_refs != previous.basis_refs
            or edit.lineage != previous.lineage
            or previous_fields != next_fields
            or next_refs[: len(previous_refs)] != previous_refs
        ):
            raise StateRecordError(
                "program operator may only append an existing Space@1 program_node_refs"
            )
    return _upsert_graph(record, edits, additions, allow_entity_replace=True)


def _apply_reindex_operator(
    record: StateRecord,
    additions: tuple[Entity, ...],
    relations: tuple[Relation, ...],
    basis_refs: tuple[str, ...],
) -> StateRecord:
    allowed_schemas = {"Element@1", "GridAxis@1", "Connection@1"}
    refused = tuple(
        sorted({entity.schema for entity in additions if entity.schema not in allowed_schemas})
    )
    if refused:
        raise StateRecordError(
            "reindex operator cannot add entity schemas: " + ", ".join(refused)
        )
    successor = _upsert_graph(
        record, additions, relations, allow_entity_replace=False
    )
    return replace(
        successor,
        basis_refs=tuple(sorted(set((*record.basis_refs, *basis_refs)))),
        predecessor_ref=f"record:{record.digest}",
    )


def _apply_component_operator(record: StateRecord, operator: StateRecordOperator) -> StateRecord:
    """Apply one atomic semantic graph edit; refuse dangling survivors."""

    allowed_schemas = {"Component@1", "Element@1", "Type@1", "Reading@1", "Level@1"}
    existing_entities = {entity.entity_id: entity for entity in record.entities}
    for entity in operator.entities:
        if entity.schema not in allowed_schemas:
            raise StateRecordError(f"edit_components cannot edit entity schema {entity.schema}")
        previous = existing_entities.get(entity.entity_id)
        if previous is not None and previous.schema != entity.schema:
            raise StateRecordError(f"edit_components cannot change schema of entity {entity.entity_id}")
    for entity_id in operator.remove_entity_ids:
        previous = existing_entities.get(entity_id)
        if previous is not None and previous.schema not in allowed_schemas:
            raise StateRecordError(f"edit_components cannot remove entity schema {previous.schema}")

    # Validate all requested removals and replacements before constructing the
    # final record, so a related parameter and element can be edited together.
    replacements: dict[str, dict[str, Any]] = {}
    for name, items, edits, removals, identity in (
        ("entities", record.entities, operator.entities, operator.remove_entity_ids, "entity_id"),
        ("parameters", record.parameters, operator.parameters, operator.remove_parameter_keys, "key"),
        ("relations", record.relations, operator.relations, operator.remove_relation_ids, "relation_id"),
    ):
        known = {getattr(item, identity) for item in items}
        edited_ids = [getattr(item, identity) for item in edits]
        if len(set(edited_ids)) != len(edited_ids):
            raise StateRecordError(f"edit_components {name} ids must be unique")
        unknown = set(removals) - known
        if unknown:
            raise StateRecordError(f"edit_components removes unknown {name}: " + ", ".join(sorted(unknown)))
        overlap = set(edited_ids) & set(removals)
        if overlap:
            raise StateRecordError(f"edit_components both edits and removes {name}: " + ", ".join(sorted(overlap)))
        replacements[name] = {getattr(item, identity): item for item in edits}

    removed_entities = set(operator.remove_entity_ids)
    removed_relations = set(operator.remove_relation_ids) | {
        relation.relation_id for relation in record.relations
        if relation.subject in removed_entities or relation.object in removed_entities
    }
    # A replacement can deliberately reattach an existing relation to surviving
    # endpoints; only the old incident relation is implicitly removed.
    removed_relations -= set(replacements["relations"])
    final: dict[str, tuple[Any, ...]] = {}
    for name, items, removals, identity in (
        ("entities", record.entities, removed_entities, "entity_id"),
        ("parameters", record.parameters, set(operator.remove_parameter_keys), "key"),
        ("relations", record.relations, removed_relations, "relation_id"),
    ):
        edits = replacements[name]
        known = {getattr(item, identity) for item in items}
        final[name] = tuple(
            edits.get(getattr(item, identity), item) for item in items
            if getattr(item, identity) not in removals
        ) + tuple(item for identifier, item in edits.items() if identifier not in known)

    successor = replace(record, **final)
    if (any(entity.schema == "Level@1" for entity in operator.entities)
            or any(existing_entities[identifier].schema == "Level@1" for identifier in operator.remove_entity_ids)):
        project_levels_of(successor)
    # Component membership and expression inputs must also remain resolvable;
    # they cannot be silently detached when another item is removed.
    surviving_entities = {entity.entity_id: entity for entity in successor.entities}
    for entity in successor.entities:
        component_id = entity.fields.get("component_id")
        if component_id is not None:
            component = surviving_entities.get(component_id)
            if component is None or component.schema != "Component@1":
                raise StateRecordError(f"entity {entity.entity_id}: component_id names no Component@1 {component_id!r}")
    evaluate_parameters(successor)
    return successor


def _upsert_graph(
    record: StateRecord,
    entities: tuple[Entity, ...],
    relations: tuple[Relation, ...],
    *,
    allow_entity_replace: bool,
) -> StateRecord:
    entity_ids = [entity.entity_id for entity in entities]
    relation_ids = [relation.relation_id for relation in relations]
    if len(set(entity_ids)) != len(entity_ids):
        raise StateRecordError("operator entity ids must be unique")
    if len(set(relation_ids)) != len(relation_ids):
        raise StateRecordError("operator relation ids must be unique")
    existing_entities = {entity.entity_id for entity in record.entities}
    if not allow_entity_replace and existing_entities & set(entity_ids):
        raise StateRecordError("operator cannot replace an existing entity")
    existing_relations = {relation.relation_id for relation in record.relations}
    duplicate_relations = tuple(sorted(existing_relations & set(relation_ids)))
    if duplicate_relations:
        raise StateRecordError(
            "operator cannot replace existing relations: "
            + ", ".join(duplicate_relations)
        )
    by_id = {entity.entity_id: entity for entity in entities}
    seen: set[str] = set()
    result: list[Entity] = []
    for entity in record.entities:
        replacement = by_id.get(entity.entity_id)
        result.append(replacement if replacement is not None else entity)
        if replacement is not None:
            seen.add(entity.entity_id)
    result.extend(entity for entity in entities if entity.entity_id not in seen)
    return replace(
        record,
        entities=tuple(result),
        relations=record.relations + relations,
    )


def _changed_refs(
    before: StateRecord, after: StateRecord
) -> tuple[str, ...]:
    changed: set[str] = set()
    before_entities = {entity.entity_id: entity for entity in before.entities}
    after_entities = {entity.entity_id: entity for entity in after.entities}
    for entity_id in before_entities.keys() | after_entities.keys():
        if before_entities.get(entity_id) != after_entities.get(entity_id):
            changed.add(f"entity:{entity_id}")
    before_parameters = {parameter.key: parameter for parameter in before.parameters}
    after_parameters = {parameter.key: parameter for parameter in after.parameters}
    for key in before_parameters.keys() | after_parameters.keys():
        if before_parameters.get(key) != after_parameters.get(key):
            changed.add(f"parameter:{key}")
    before_relations = {relation.relation_id: relation for relation in before.relations}
    after_relations = {relation.relation_id: relation for relation in after.relations}
    for relation_id in before_relations.keys() | after_relations.keys():
        old, new = before_relations.get(relation_id), after_relations.get(relation_id)
        if old != new:
            for relation in (old, new):
                if relation is not None:
                    changed.update((f"entity:{relation.subject}", f"entity:{relation.object}"))
    return tuple(sorted(changed))

def schematic_proposal(pack: SchematicPack) -> SpatialOptionProposal:
    """The pack as a validated spatial option; the dataclasses reject gaps."""

    ev = pack.evidence_refs
    levels = tuple(SpatialLevel(l["level_id"], int(l["base_y"]), int(l["height"]), ev) for l in pack.levels)
    volumes = tuple(MassingVolume(v["volume_id"], SiteBounds(tuple(int(c) for c in v["min"]), tuple(int(c) for c in v["max"])), tuple(v["level_ids"]), ev) for v in pack.volumes)
    zones = tuple(SpatialZone(zone_id=z["zone_id"], program_node_refs=tuple(z["program_node_refs"]), level_ids=tuple(z["level_ids"]), volume_ids=tuple(z["volume_ids"]), source_refs=ev) for z in pack.zones)
    connections = tuple(SpatialConnection(connection_id=c["connection_id"], source_zone_id=c["source_zone_id"], target_zone_id=c["target_zone_id"],
                                          relationship_refs=tuple(c["relationship_refs"]), directed=bool(c.get("directed", False)), source_refs=ev) for c in pack.connections)
    return SpatialOptionProposal(
        option_id=pack.option_id, label=pack.label, program_scenario_ref=None, footprint_range_ref=None,
        grid_basis=SpatialGridBasis(horizontal_area_per_cell=1.0, area_unit="square_metres", source_refs=ev),
        footprint_cells=pack.footprint_cells, levels=levels, volumes=volumes, zones=zones,
        components=tuple(sorted(pack.components, key=lambda c: c.component_id)),
        connections=tuple(sorted(connections, key=lambda c: c.connection_id)), constraint_responses=(),
        typology_hypothesis=pack.typology, palette_refs=(), rationale=pack.rationale, responds_to_refs=ev, expert_advice_refs=(), evidence_refs=ev,
    )

def schematic_pack_of(record: StateRecord, *, option_id: str | None = None, evidence_refs: tuple[str, ...] | None = None) -> SchematicPack | None:
    """The massing the record declares, as a ``SchematicPack@1``; ``None`` if it declares none.

    The one reader of ``MassingLevel@1`` / ``Volume@1`` / ``Space@1`` /
    ``Connection@1`` *as a spatial option*. ``developed_design_view`` builds its
    view from this, and so does anything that measures the massing
    (``monkeyarch.capabilities.massing_metrics``) or offers a variant of it. A record is said to
    declare massing when it carries volumes, zones and massing levels together;
    with any of the three absent the view falls back to one block and this
    answers ``None`` rather than half a pack.

    ``option_id`` and ``evidence_refs`` are the caller's when the caller has
    already resolved them — that is how the view keeps its own sentences for a
    record that names neither — and resolved here the same way otherwise.
    """

    option = dict(record.option)
    option_id = option_id or option.get("option_id") or (record.decision_ref or "").split(":", 1)[-1] or None
    if not option_id:
        raise StateRecordError("a schematic pack needs an option id (record.option, record.decision_ref, or the caller)")
    evidence = tuple(sorted(set(record.evidence_refs if evidence_refs is None else evidence_refs)))
    if not evidence:
        raise StateRecordError("a schematic pack needs at least one evidence ref")
    volumes, zones, massing_levels, connections = (record.entities_of(s) for s in ("Volume@1", "Space@1", "MassingLevel@1", "Connection@1"))
    if not (volumes and zones and massing_levels):
        return None
    return SchematicPack(
        project_id=record.project_id, option_id=option_id, label=str(option.get("label", option_id)), typology=str(option.get("typology", "declared")),
        rationale=str(option.get("rationale", "declared from the state record")), evidence_refs=evidence,
        levels=tuple({"level_id": e.entity_id, "base_y": e.fields["base_y"], "height": e.fields["height"]} for e in massing_levels),
        volumes=tuple({"volume_id": e.entity_id, "min": list(e.fields["min"]), "max": list(e.fields["max"]), "level_ids": list(e.fields["level_ids"])} for e in volumes),
        zones=tuple({"zone_id": e.entity_id, "program_node_refs": list(e.fields["program_node_refs"]), "level_ids": list(e.fields["level_ids"]), "volume_ids": list(e.fields["volume_ids"])} for e in zones),
        connections=tuple({"connection_id": e.entity_id, "source_zone_id": e.fields["source_zone_id"], "target_zone_id": e.fields["target_zone_id"],
                           "relationship_refs": list(e.fields["relationship_refs"]), "directed": bool(e.fields.get("directed", False))} for e in connections),
        components=design_components_of(record, source_ref=evidence[0]), footprint_cells=tuple((int(x), int(z)) for x, z in option.get("footprint_cells", ((0, 0),))),
        assumption_refs=tuple(option.get("assumption_refs", ())),
    )


def bootstrap_developed_state(pack: SchematicPack, *, run: RunRef, portfolio_id: str, branch_id: str, selection_decision_ref: str,
                              phase: DesignPhase) -> DevelopedDesignState:
    """A developed-design state whose selected schematic is the pack.

    The portfolio ceremony (branches, votes, handoff) is replaced by one
    declared selection: the pack *is* the selected option, and the record
    that carries it says so. Everything downstream is the real state.

    ``phase`` is the run's, not the record's (ADR-007 rule 1), and it is
    required: the runner passes its envelope's phase, a harness its own
    stage's, a tool the stage it is opening. It enters the state digest, so
    the same record executed in a schematic stage and in a development stage
    yields two binding identities - that is what a stage envelope binds.
    There is no default; a reader that has no run must say which phase it
    reads in and why (``StateRecord.state_digest`` does, and so does the
    Studio's read-only projection). ``DevelopedDesignState`` admits
    schematic_design and design_development.
    """

    if run.project_id != pack.project_id:
        raise StateRecordError("schematic pack belongs to another project")
    if not isinstance(phase, DesignPhase):
        raise StateRecordError("phase must be a DesignPhase")
    proposal = schematic_proposal(pack)
    option = SchematicOption(proposal=proposal, footprint_area=float(len(proposal.footprint_cells)),
                             topology_signature=canonical_digest({"components": [c.to_dict() for c in proposal.components], "option_id": proposal.option_id}))
    revision_digest = canonical_digest({"branch_id": branch_id, "option_digest": option.option_digest})
    selected = SelectedSchematicInput(
        portfolio_id=portfolio_id, portfolio_digest=canonical_digest({"portfolio_id": portfolio_id, "option_digest": option.option_digest}),
        project_id=run.project_id, run_id=run.run_id, base=run.base, branch_id=branch_id,
        revision=BranchRevisionRef(branch_id=branch_id, revision_id="revision-declared-selection", revision_digest=revision_digest),
        option=option, selection_transition_id="select-by-declared-record", selection_decision_ref=selection_decision_ref,
    )
    return DevelopedDesignState(
        selected_schematic=selected, active_phase=phase, coordination_status=DevelopmentCoordinationStatus.IN_PROGRESS,
        obligations=(), components=(), dependencies=(), advice=(), decisions=(), transitions=(), assumption_refs=tuple(sorted(set(pack.assumption_refs))),
    )


def developed_design_view(record: StateRecord, *, run: RunRef, option_id: str | None = None, evidence_ref: str | None = None, portfolio_id: str = "declared-state-record",
                          branch_id: str = "state-record", selection_decision_ref: str = "decision:state-record-declared",
                          phase: DesignPhase):
    """Forward a State Record to the legacy ``DevelopedDesignState`` the compiler still takes.

    With massing entities (MassingLevel@1, Volume@1, Space@1 zones,
    Connection@1) and a declared ``option``, the spatial option is rebuilt
    exactly from the record; without them the building entity owns one
    block spanning the record's levels. This view exists only until the
    compiler, the seats and the handovers read the record directly; each
    call is a lineage event, not a second source of truth.

    ``phase`` is the executing run's (its stage envelope's) and is required;
    see ``bootstrap_developed_state``. The record itself states no phase, so
    there is nothing here for a default to fall back on: a caller that omits
    it is a caller that has not said which run it is projecting.
    """

    from dataclasses import replace as _replace


    replace_volume_ids = lambda component, volume_ids: _replace(component, volume_ids=volume_ids)

    components = record.entities_of("Component@1")
    if not components:
        raise StateRecordError("a developed-design view needs Component@1 entities")
    evidence = tuple(sorted(set(record.evidence_refs) | ({evidence_ref} if evidence_ref else set())))
    if not evidence:
        raise StateRecordError("a developed-design view needs at least one evidence ref")
    option = dict(record.option)
    option_id = option_id or option.get("option_id") or (record.decision_ref or "").split(":", 1)[-1] or None
    if not option_id:
        raise StateRecordError("a developed-design view needs an option id (record.option, record.decision_ref, or the caller)")
    pack = schematic_pack_of(record, option_id=option_id, evidence_refs=evidence)
    if pack is not None:
        return bootstrap_developed_state(pack, run=run, portfolio_id=portfolio_id, branch_id=branch_id, selection_decision_ref=selection_decision_ref, phase=phase)
    evidence_ref = evidence[0]
    levels = record.entities_of("Level@1")
    elevations = sorted(float(l.fields.get("elevation", 0.0)) for l in levels) or [0.0, 1.0]
    top = max(elevations[-1], elevations[0] + 1.0)
    design_components = tuple(replace_volume_ids(c, ("block",) if c.parent_component_id is None else ()) for c in design_components_of(record, source_ref=evidence_ref))
    pack = SchematicPack(project_id=record.project_id, option_id=option_id, label=f"{record.project_id} state record {record.digest[:12]}", typology=str(record.entity(components[0].entity_id).fields.get("typology", "declared")),
                         rationale="view of a StateRecord@1; not a second source of truth", evidence_refs=evidence,
                         levels=({"level_id": "record", "base_y": int(elevations[0]), "height": max(1, int(top - elevations[0] + 0.999))},),
                         volumes=({"volume_id": "block", "min": [-1, int(elevations[0]), -1], "max": [1, int(top + 0.999), 1], "level_ids": ["record"]},),
                         zones=({"zone_id": "record-zone", "program_node_refs": ["program-node:record"], "level_ids": ["record"], "volume_ids": ["block"]},),
                         connections=(), components=tuple(design_components), footprint_cells=((0, 0),), assumption_refs=("assumption:state-record-view",))
    return bootstrap_developed_state(pack, run=run, portfolio_id=portfolio_id, branch_id=branch_id, selection_decision_ref=selection_decision_ref, phase=phase)


# A ``StateRecord@1`` names the canonical version it is bound to in exactly one
# place. ``digest`` deliberately excludes ``base``, so restating it leaves this
# record's content identity unchanged, and the serialised record carries no
# field derived from its base: ``state_digest`` is computed on demand and is
# never written into the record.
VERSION_REF_POINTERS = {"StateRecord@1": ("/base",)}

_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("StateRecord@1", ())
