"""Canonical design State Record (P102).

One record carries a project's design state as data: entities of typed
schemas (level, grid axis, type, element, assembly, space, reading),
parameters (named quantities with basis and lifecycle), relations (the
architectural relation vocabulary with datum roles, propagation rules and
validator bindings), obligations (open duties, separate from relations),
evidence, provenance and stage / authority. Geometry programs, validation
results, receipts and indexes are derived from it and never authoritative.

The record is the canonical abstraction that retires the second design
state model used by geometry production. Until every consumer reads it
directly, ``developed_design_view`` forwards a record to the legacy
``DevelopedDesignState`` the producer and the seats still take; the view is
an adapter with a lineage note, scheduled for retirement with them.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from archflow.contracts.canonical import canonical_json
from archflow.project.refs import RunRef, require_identifier
from archflow.state.operational_state import DependencyEdge, DependencyEffect, DesignObligation

_ENTITY_SCHEMAS = frozenset({"Level@1", "GridAxis@1", "Type@1", "Element@1", "Assembly@1", "Space@1", "Reading@1", "Component@1"})
_EPISTEMIC = frozenset({"observed", "declared", "derived", "hypothesis", "disputed", "unknown"})
_MAX_ITEMS = 50_000


class StateRecordError(ValueError):
    """Typed failure of the state record contracts."""


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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
        if self.check_kind not in {"support_contact", "meets", "aperture_exists", "clearance_interval", "alignment", "engagement_interval", "separation_interval"}:
            raise StateRecordError(f"unknown check kind {self.check_kind!r}")

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
        require_identifier(self.subject, "relation subject")
        require_identifier(self.object, "relation object")
        if self.subject == self.object:
            raise StateRecordError(f"relation {self.relation_id}: subject and object must differ")
        if self.propagation not in {"unchanged", "revalidate", "invalidate"}:
            raise StateRecordError(f"relation {self.relation_id}: invalid propagation")
        if self.epistemic_status not in _EPISTEMIC:
            raise StateRecordError(f"relation {self.relation_id}: invalid epistemic status")
        if self.datum_role is not None:
            require_identifier(self.datum_role, "datum_role")
        _refs(self.basis_refs, f"relation {self.relation_id} basis_refs")
        object.__setattr__(self, "parameters", dict(self.parameters))

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

        if self.propagation == "unchanged":
            return None
        effect = DependencyEffect.INVALIDATES if self.propagation == "invalidate" else DependencyEffect.REQUIRES_REVALIDATION
        return DependencyEdge(upstream_ref=f"entity:{self.subject}", downstream_ref=f"entity:{self.object}", relation=self.kind,
                              source_ref=f"relation:{self.relation_id}", effect=effect)


@dataclass(frozen=True, slots=True)
class StageBinding:
    workflow_ref: str | None = None
    envelope_ref: str | None = None
    stage_id: str | None = None
    predecessor_exit_binding_ref: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"workflow_ref": self.workflow_ref, "envelope_ref": self.envelope_ref, "stage_id": self.stage_id, "predecessor_exit_binding_ref": self.predecessor_exit_binding_ref}

    @classmethod
    def from_dict(cls, value: object) -> "StageBinding":
        value = value or {}
        return cls(value.get("workflow_ref"), value.get("envelope_ref"), value.get("stage_id"), value.get("predecessor_exit_binding_ref"))


@dataclass(frozen=True, slots=True)
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
    stage: StageBinding = field(default_factory=StageBinding)
    decision_ref: str | None = None
    invalidated_refs: tuple[str, ...] = ()

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
        if len(set(keys)) != len(keys):
            raise StateRecordError("parameter keys must be unique")
        for p in self.parameters:
            for item in p.inputs:
                if item not in set(keys):
                    raise StateRecordError(f"parameter {p.key}: unknown input {item!r}")
        rids = [r.relation_id for r in self.relations]
        if len(set(rids)) != len(rids):
            raise StateRecordError("relation ids must be unique")
        for r in self.relations:
            for end in (r.subject, r.object):
                if end not in known:
                    raise StateRecordError(f"relation {r.relation_id}: unknown entity {end!r}")
        _refs(self.evidence_refs, "evidence_refs"); _refs(self.basis_refs, "basis_refs"); _refs(self.invalidated_refs, "invalidated_refs")

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
        """Relations and parameter inputs as kernel edges (closure input)."""

        edges = [e for e in (r.dependency_edge() for r in self.relations) if e is not None]
        for p in self.parameters:
            for item in p.inputs:
                edges.append(DependencyEdge(upstream_ref=f"parameter:{item}", downstream_ref=p.ref, relation="derives",
                                            source_ref=p.source_ref or f"parameter:{p.key}", effect=DependencyEffect.REQUIRES_REVALIDATION))
        for e in self.entities:
            for key in ("base_level", "top_level", "sill_level"):
                target = e.fields.get(key)
                if isinstance(target, str):
                    edges.append(DependencyEdge(upstream_ref=f"entity:{target}", downstream_ref=e.ref, relation=key, source_ref=e.ref, effect=DependencyEffect.REQUIRES_REVALIDATION))
            for key in ("host", "type_ref"):
                target = e.fields.get(key)
                if isinstance(target, str):
                    edges.append(DependencyEdge(upstream_ref=f"entity:{target}", downstream_ref=e.ref, relation=key, source_ref=e.ref, effect=DependencyEffect.INVALIDATES))
        return tuple(edges)

    def closure(self, changed_refs: tuple[str, ...]) -> tuple[str, ...]:
        """Everything downstream of ``changed_refs`` along invalidating edges (P063 semantics)."""

        adjacency: dict[str, set[str]] = {}
        for edge in self.dependency_edges():
            adjacency.setdefault(edge.upstream_ref, set()).add(edge.downstream_ref)
        seen = set(changed_refs)
        queue = sorted(changed_refs)
        while queue:
            current = queue.pop(0)
            for downstream in sorted(adjacency.get(current, ())):
                if downstream not in seen:
                    seen.add(downstream)
                    queue.append(downstream)
        return tuple(sorted(seen))

    # ---- serialization
    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "project_id": self.project_id, "run_id": self.run_id, "entities": [e.to_dict() for e in self.entities],
                "parameters": [p.to_dict() for p in self.parameters], "relations": [r.to_dict() for r in self.relations],
                "obligations": [o.to_dict() for o in self.obligations], "evidence_refs": list(self.evidence_refs), "basis_refs": list(self.basis_refs),
                "predecessor_ref": self.predecessor_ref, "stage": self.stage.to_dict(), "decision_ref": self.decision_ref, "invalidated_refs": list(self.invalidated_refs)}

    @classmethod
    def from_dict(cls, value: object) -> "StateRecord":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise StateRecordError("state record payload malformed")
        return cls(value["project_id"], value["run_id"], tuple(Entity.from_dict(e) for e in value["entities"]),
                   tuple(Parameter.from_dict(p) for p in value.get("parameters", ())), tuple(Relation.from_dict(r) for r in value.get("relations", ())),
                   tuple(DesignObligation.from_dict(o) for o in value.get("obligations", ())), tuple(value.get("evidence_refs", ())), tuple(value.get("basis_refs", ())),
                   value.get("predecessor_ref"), StageBinding.from_dict(value.get("stage")), value.get("decision_ref"), tuple(value.get("invalidated_refs", ())))

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


# ---------------------------------------------------------------- adapter to the legacy model (scheduled for retirement)
def developed_design_view(record: StateRecord, *, run: RunRef, option_id: str, evidence_ref: str, portfolio_id: str = "declared-state-record",
                          branch_id: str = "state-record", selection_decision_ref: str = "decision:state-record-declared"):
    """Forward a State Record to the legacy ``DevelopedDesignState`` the producer still takes.

    Component entities become the semantic tree; the building entity owns
    one massing volume spanning the record's levels. This view exists only
    until ``produce_geometry_program_proposal``, the seats and the
    handovers read the record directly; each call is a lineage event, not
    a second source of truth.
    """

    from archflow.runtime.project_runner import SchematicPack, bootstrap_developed_state
    from archflow.state.spatial import ComponentMaturity, DesignComponent

    components = record.entities_of("Component@1")
    if not components:
        raise StateRecordError("a developed-design view needs Component@1 entities")
    levels = record.entities_of("Level@1")
    elevations = sorted(float(l.fields.get("elevation", 0.0)) for l in levels) or [0.0, 1.0]
    top = max(elevations[-1], elevations[0] + 1.0)
    design_components = []
    for e in components:
        design_components.append(DesignComponent(component_id=e.entity_id, parent_component_id=e.parent_id, semantic_kind=str(e.fields.get("semantic_kind", "component")),
                                                 intent=str(e.fields.get("intent", e.entity_id)), maturity=ComponentMaturity(str(e.fields.get("maturity", "schematic"))), revision=int(e.fields.get("revision", 1)),
                                                 volume_ids=("block",) if e.parent_id is None else (), unresolved_child_roles=(), source_refs=(evidence_ref,)))
    pack = SchematicPack(project_id=record.project_id, option_id=option_id, label=f"{record.project_id} state record {record.digest[:12]}", typology=str(record.entity(components[0].entity_id).fields.get("typology", "declared")),
                         rationale="view of a StateRecord@1; not a second source of truth", evidence_refs=(evidence_ref,),
                         levels=({"level_id": "record", "base_y": int(elevations[0]), "height": max(1, int(top - elevations[0] + 0.999))},),
                         volumes=({"volume_id": "block", "min": [-1, int(elevations[0]), -1], "max": [1, int(top + 0.999), 1], "level_ids": ["record"]},),
                         zones=({"zone_id": "record-zone", "program_node_refs": ["program-node:record"], "level_ids": ["record"], "volume_ids": ["block"]},),
                         connections=(), components=tuple(design_components), footprint_cells=((0, 0),), assumption_refs=("assumption:state-record-view",))
    return bootstrap_developed_state(pack, run=run, portfolio_id=portfolio_id, branch_id=branch_id, selection_decision_ref=selection_decision_ref)
