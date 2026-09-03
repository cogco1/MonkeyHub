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

from dataclasses import dataclass, field
from typing import Any, Mapping

from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.project.refs import ProjectVersionRef, RunRef, require_identifier
from archflow.state.operational_state import DependencyEdge, DependencyEffect, DesignObligation
from archflow.relations.contracts import ArchitecturalRelationKind

_RELATION_KINDS = frozenset(kind.value for kind in ArchitecturalRelationKind)   # one vocabulary: the kernel's
_ENTITY_SCHEMAS = frozenset({"Level@1", "GridAxis@1", "Type@1", "Element@1", "Assembly@1", "Space@1", "Reading@1", "Component@1",
                             "MassingLevel@1", "Volume@1", "Connection@1"})   # Space@1 = a zone of the spatial option
_EPISTEMIC = frozenset({"observed", "declared", "derived", "hypothesis", "disputed", "unknown"})
_MAX_ITEMS = 50_000


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
        if self.kind not in _RELATION_KINDS:
            raise StateRecordError(f"relation {self.relation_id}: kind {self.kind!r} is not in the kernel relation vocabulary")
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
    stage: StageBinding = field(default_factory=StageBinding)
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
        for connection in self.entities_of("Connection@1"):
            for end in ("source_zone_id", "target_zone_id"):
                if connection.fields.get(end) not in known:
                    raise StateRecordError(f"connection {connection.entity_id}: unknown zone {connection.fields.get(end)!r}")

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
        """

        cached = getattr(self, "_state_digest_cache", None)
        if cached is None:
            cached = developed_design_view(self, run=self.run_ref).state_digest
            object.__setattr__(self, "_state_digest_cache", cached)
        return cached

    # ---- serialization
    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "project_id": self.project_id, "run_id": self.run_id, "entities": [e.to_dict() for e in self.entities],
                "parameters": [p.to_dict() for p in self.parameters], "relations": [r.to_dict() for r in self.relations],
                "obligations": [o.to_dict() for o in self.obligations], "evidence_refs": list(self.evidence_refs), "basis_refs": list(self.basis_refs),
                "predecessor_ref": self.predecessor_ref, "stage": self.stage.to_dict(), "decision_ref": self.decision_ref, "invalidated_refs": list(self.invalidated_refs),
                "option": dict(self.option),
                "base": None if self.base is None else {"project_id": self.base.project_id, "version": self.base.version, "state_sha256": self.base.state_sha256}}

    @classmethod
    def from_dict(cls, value: object) -> "StateRecord":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise StateRecordError("state record payload malformed")
        return cls(value["project_id"], value["run_id"], tuple(Entity.from_dict(e) for e in value["entities"]),
                   tuple(Parameter.from_dict(p) for p in value.get("parameters", ())), tuple(Relation.from_dict(r) for r in value.get("relations", ())),
                   tuple(DesignObligation.from_dict(o) for o in value.get("obligations", ())), tuple(value.get("evidence_refs", ())), tuple(value.get("basis_refs", ())),
                   value.get("predecessor_ref"), StageBinding.from_dict(value.get("stage")), value.get("decision_ref"), tuple(value.get("invalidated_refs", ())),
                   dict(value.get("option", {})),
                   None if value.get("base") is None else ProjectVersionRef(value["base"]["project_id"], int(value["base"]["version"]), value["base"].get("state_sha256")))

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


# ---------------------------------------------------------------- typed views of the record
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
            component_id=e.entity_id, parent_component_id=e.parent_id, semantic_kind=str(fields.get("semantic_kind", "component")),
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


def project_grids_of(record: StateRecord, *, published_by: str = "seat-coordination"):
    """The record's GridAxis@1 entities as the published project grids (P098); None when there are none."""

    from archflow.state.geometry_program import ProjectGridAxis, ProjectGrids

    axes = tuple(sorted((ProjectGridAxis(e.entity_id, str(e.fields["role"]), tuple(float(v) for v in e.fields["origin"]), tuple(float(v) for v in e.fields["direction"]), tuple(e.basis_refs))
                         for e in record.entities_of("GridAxis@1")), key=lambda a: a.axis_id))
    if not axes:
        return None
    return ProjectGrids(project_id=record.project_id, published_by=published_by, axes=axes)


# ---------------------------------------------------------------- adapter to the legacy model (scheduled for retirement)
def developed_design_view(record: StateRecord, *, run: RunRef, option_id: str | None = None, evidence_ref: str | None = None, portfolio_id: str = "declared-state-record",
                          branch_id: str = "state-record", selection_decision_ref: str = "decision:state-record-declared"):
    """Forward a State Record to the legacy ``DevelopedDesignState`` the compiler still takes.

    With massing entities (MassingLevel@1, Volume@1, Space@1 zones,
    Connection@1) and a declared ``option``, the spatial option is rebuilt
    exactly from the record; without them the building entity owns one
    block spanning the record's levels. This view exists only until the
    compiler, the seats and the handovers read the record directly; each
    call is a lineage event, not a second source of truth.
    """

    from dataclasses import replace as _replace

    from archflow.runtime.project_runner import SchematicPack, bootstrap_developed_state

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
    volumes, zones, massing_levels, connections = (record.entities_of(s) for s in ("Volume@1", "Space@1", "MassingLevel@1", "Connection@1"))
    if volumes and zones and massing_levels:
        design_components = design_components_of(record, source_ref=evidence[0])
        pack = SchematicPack(
            project_id=record.project_id, option_id=option_id, label=str(option.get("label", option_id)), typology=str(option.get("typology", "declared")),
            rationale=str(option.get("rationale", "declared from the state record")), evidence_refs=evidence,
            levels=tuple({"level_id": e.entity_id, "base_y": e.fields["base_y"], "height": e.fields["height"]} for e in massing_levels),
            volumes=tuple({"volume_id": e.entity_id, "min": list(e.fields["min"]), "max": list(e.fields["max"]), "level_ids": list(e.fields["level_ids"])} for e in volumes),
            zones=tuple({"zone_id": e.entity_id, "program_node_refs": list(e.fields["program_node_refs"]), "level_ids": list(e.fields["level_ids"]), "volume_ids": list(e.fields["volume_ids"])} for e in zones),
            connections=tuple({"connection_id": e.entity_id, "source_zone_id": e.fields["source_zone_id"], "target_zone_id": e.fields["target_zone_id"],
                               "relationship_refs": list(e.fields["relationship_refs"]), "directed": bool(e.fields.get("directed", False))} for e in connections),
            components=design_components, footprint_cells=tuple((int(x), int(z)) for x, z in option.get("footprint_cells", ((0, 0),))),
            assumption_refs=tuple(option.get("assumption_refs", ())),
        )
        return bootstrap_developed_state(pack, run=run, portfolio_id=portfolio_id, branch_id=branch_id, selection_decision_ref=selection_decision_ref)
    evidence_ref = evidence[0]
    levels = record.entities_of("Level@1")
    elevations = sorted(float(l.fields.get("elevation", 0.0)) for l in levels) or [0.0, 1.0]
    top = max(elevations[-1], elevations[0] + 1.0)
    design_components = tuple(replace_volume_ids(c, ("block",) if c.parent_component_id is None else ()) for c in design_components_of(record, source_ref=evidence_ref))
    pack = SchematicPack(project_id=record.project_id, option_id=option_id, label=f"{record.project_id} state record {record.digest[:12]}", typology=str(record.entity(components[0].entity_id).fields.get("typology", "declared")),
                         rationale="view of a StateRecord@1; not a second source of truth", evidence_refs=(evidence_ref,),
                         levels=({"level_id": "record", "base_y": int(elevations[0]), "height": max(1, int(top - elevations[0] + 0.999))},),
                         volumes=({"volume_id": "block", "min": [-1, int(elevations[0]), -1], "max": [1, int(top + 0.999), 1], "level_ids": ["record"]},),
                         zones=({"zone_id": "record-zone", "program_node_refs": ["program-node:record"], "level_ids": ["record"], "volume_ids": ["block"]},),
                         connections=(), components=tuple(design_components), footprint_cells=((0, 0),), assumption_refs=("assumption:state-record-view",))
    return bootstrap_developed_state(pack, run=run, portfolio_id=portfolio_id, branch_id=branch_id, selection_decision_ref=selection_decision_ref)
