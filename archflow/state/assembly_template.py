"""Building assembly templates: the typology layer (P096).

Between generic building semantics (relation predicates, obligations)
and component templates sits the organisation of one *kind* of
building: which roles exist, how they nest, how many of each, what
function each serves, which relations bind them and which datum each
relation carries, when each appears, and which checks must run. A
`BuildingAssemblyTemplate@1` stores exactly that and never an absolute
coordinate — a project re-derives geometry; it does not copy a model.

A project consumes a template through an `AssemblyTemplateBinding@1`:
roles to project components (or a typed declination), datum roles to
project datums, project-derived cardinalities with evidence. Templates
are immutable records; a binding never mutates one, and a new edition
is a new record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum

from archflow.contracts.authority import no_authority
from archflow.project.refs import require_identifier
from archflow.relations.contracts import ArchitecturalRelationKind
from archflow.state.component_template import (
    CaseVote,
    ParameterForm,
    TemplateParameter,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.geometry_program import InterfaceDatumKind
from archflow.state.spatial import DesignComponent

LIBRARY_PROMOTION_MIN_VOTES = 2
_RECORD_AUTHORITY = ("canonical_write_authority", "design_authority")


class AssemblyTemplateError(ValueError):
    """Typed failure of the assembly template contracts."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssemblyTemplateError(f"{field_name} must be non-empty text")
    return value


def _refs(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise AssemblyTemplateError(f"{field_name} must be a tuple of non-empty text")
    if tuple(sorted(set(values))) != values:
        raise AssemblyTemplateError(f"{field_name} must be sorted and unique")
    return values


@dataclass(frozen=True, slots=True)
class Cardinality:
    """How many instances of a role a project carries.

    ``value`` is a fixed count; ``parameter`` names a COUNT parameter of
    the template whose value the project supplies with evidence
    (project-derived). Exactly one of the two is set.
    """

    value: int | None = None
    parameter: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.parameter is None):
            raise AssemblyTemplateError("cardinality needs exactly one of value or parameter")
        if self.value is not None and (isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1):
            raise AssemblyTemplateError("cardinality value must be a positive integer")
        if self.parameter is not None:
            require_identifier(self.parameter, "cardinality parameter")

    @property
    def project_derived(self) -> bool:
        return self.parameter is not None

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "parameter": self.parameter}

    @classmethod
    def from_dict(cls, value: dict) -> "Cardinality":
        return cls(value=value.get("value"), parameter=value.get("parameter"))


@dataclass(frozen=True, slots=True)
class AssemblyRole:
    role_id: str
    parent_role: str | None
    function: str
    cardinality: Cardinality
    phase: DesignPhase
    indexing: tuple[str, ...] = ()
    component_template_family: str | None = None
    required_datum_roles: tuple[str, ...] = ()

    SCHEMA = "AssemblyRole@1"

    def __post_init__(self) -> None:
        require_identifier(self.role_id, "role_id")
        if self.parent_role is not None:
            require_identifier(self.parent_role, "parent_role")
            if self.parent_role == self.role_id:
                raise AssemblyTemplateError(f"role {self.role_id!r} cannot be its own parent")
        _text(self.function, "role function")
        if not isinstance(self.cardinality, Cardinality):
            raise AssemblyTemplateError("cardinality must be Cardinality")
        if not isinstance(self.phase, DesignPhase):
            raise AssemblyTemplateError("phase must be DesignPhase")
        if not isinstance(self.indexing, tuple) or any(not isinstance(i, str) or not i for i in self.indexing):
            raise AssemblyTemplateError("indexing must be a tuple of names")
        if len(set(self.indexing)) != len(self.indexing):
            raise AssemblyTemplateError("indexing names must be unique")
        if self.indexing and self.cardinality.value is not None and len(self.indexing) != self.cardinality.value:
            raise AssemblyTemplateError(f"role {self.role_id!r} indexing does not match its cardinality")
        if self.component_template_family is not None:
            require_identifier(self.component_template_family, "component_template_family")
        _refs(self.required_datum_roles, f"role {self.role_id} required_datum_roles")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "role_id": self.role_id, "parent_role": self.parent_role,
            "function": self.function, "cardinality": self.cardinality.to_dict(), "phase": self.phase.value,
            "indexing": list(self.indexing), "component_template_family": self.component_template_family,
            "required_datum_roles": list(self.required_datum_roles),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "AssemblyRole":
        return cls(
            role_id=value["role_id"], parent_role=value.get("parent_role"), function=value["function"],
            cardinality=Cardinality.from_dict(value["cardinality"]), phase=DesignPhase(value["phase"]),
            indexing=tuple(value.get("indexing", ())), component_template_family=value.get("component_template_family"),
            required_datum_roles=tuple(value.get("required_datum_roles", ())),
        )


@dataclass(frozen=True, slots=True)
class AssemblyRelation:
    """A directed relation between roles and the datum it carries."""

    relation_id: str
    subject_role: str
    predicate: ArchitecturalRelationKind
    object_role: str
    datum_role: str | None
    basis_refs: tuple[str, ...]

    SCHEMA = "AssemblyRelation@1"

    def __post_init__(self) -> None:
        require_identifier(self.relation_id, "relation_id")
        require_identifier(self.subject_role, "subject_role")
        require_identifier(self.object_role, "object_role")
        if not isinstance(self.predicate, ArchitecturalRelationKind):
            raise AssemblyTemplateError("predicate must be ArchitecturalRelationKind")
        if self.datum_role is not None:
            require_identifier(self.datum_role, "datum_role")
        _refs(self.basis_refs, f"relation {self.relation_id} basis_refs")
        if not self.basis_refs:
            raise AssemblyTemplateError(f"relation {self.relation_id!r} needs at least one basis ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "relation_id": self.relation_id, "subject_role": self.subject_role,
            "predicate": self.predicate.value, "object_role": self.object_role, "datum_role": self.datum_role,
            "basis_refs": list(self.basis_refs),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "AssemblyRelation":
        return cls(
            relation_id=value["relation_id"], subject_role=value["subject_role"],
            predicate=ArchitecturalRelationKind(value["predicate"]), object_role=value["object_role"],
            datum_role=value.get("datum_role"), basis_refs=tuple(value["basis_refs"]),
        )


@dataclass(frozen=True, slots=True)
class RequiredDatum:
    datum_role: str
    kind: InterfaceDatumKind
    published_by_role: str

    SCHEMA = "RequiredDatum@1"

    def __post_init__(self) -> None:
        require_identifier(self.datum_role, "datum_role")
        if not isinstance(self.kind, InterfaceDatumKind):
            raise AssemblyTemplateError("datum kind must be InterfaceDatumKind")
        require_identifier(self.published_by_role, "published_by_role")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "datum_role": self.datum_role, "kind": self.kind.value, "published_by_role": self.published_by_role}

    @classmethod
    def from_dict(cls, value: dict) -> "RequiredDatum":
        return cls(datum_role=value["datum_role"], kind=InterfaceDatumKind(value["kind"]), published_by_role=value["published_by_role"])


class CheckKind(StrEnum):
    SUPPORT_CONTACT = "support_contact"
    CLEARANCE = "clearance"
    HOST_CUT = "host_cut"
    PASSAGE = "passage"
    INTERPENETRATION = "interpenetration"
    ALIGNMENT = "alignment"


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    check_id: str
    kind: CheckKind
    roles: tuple[str, ...]

    SCHEMA = "RequiredCheck@1"

    def __post_init__(self) -> None:
        require_identifier(self.check_id, "check_id")
        if not isinstance(self.kind, CheckKind):
            raise AssemblyTemplateError("check kind must be CheckKind")
        _refs(self.roles, f"check {self.check_id} roles")
        if not self.roles:
            raise AssemblyTemplateError(f"check {self.check_id!r} names no roles")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "check_id": self.check_id, "kind": self.kind.value, "roles": list(self.roles)}

    @classmethod
    def from_dict(cls, value: dict) -> "RequiredCheck":
        return cls(check_id=value["check_id"], kind=CheckKind(value["kind"]), roles=tuple(value["roles"]))


def _reject_coordinates(payload: object, path: str, *, allow_float: bool) -> None:
    """No float and no numeric vector may appear outside a MODULE_RATIO value."""

    if isinstance(payload, bool):
        return
    if isinstance(payload, float):
        if not allow_float:
            raise AssemblyTemplateError(f"assembly template carries a coordinate-like number at {path}")
        return
    if isinstance(payload, list):
        if (
            not allow_float
            and payload
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in payload)
        ):
            raise AssemblyTemplateError(f"assembly template carries a numeric vector at {path}")
        for i, item in enumerate(payload):
            _reject_coordinates(item, f"{path}[{i}]", allow_float=allow_float)
    elif isinstance(payload, dict):
        for key, item in payload.items():
            _reject_coordinates(item, f"{path}.{key}", allow_float=allow_float)


@dataclass(frozen=True, slots=True)
class BuildingAssemblyTemplate:
    """The organisation of one kind of building, without its coordinates."""

    template_id: str
    typology: str
    edition: int
    roles: tuple[AssemblyRole, ...]
    relations: tuple[AssemblyRelation, ...]
    datums: tuple[RequiredDatum, ...]
    checks: tuple[RequiredCheck, ...]
    parameters: tuple[TemplateParameter, ...]
    applicability: tuple[str, ...]
    basis_refs: tuple[str, ...]
    case_votes: tuple[CaseVote, ...]
    harvested_from_project: str
    harvested_from_run: str
    open_boundaries: tuple[str, ...] = ()
    predecessor_ref: str | None = None

    SCHEMA = "BuildingAssemblyTemplate@1"

    def __post_init__(self) -> None:
        require_identifier(self.template_id, "template_id")
        require_identifier(self.typology, "typology")
        if isinstance(self.edition, bool) or not isinstance(self.edition, int) or self.edition < 1:
            raise AssemblyTemplateError("edition must be a positive integer")
        for value, kind, name in (
            (self.roles, AssemblyRole, "roles"), (self.relations, AssemblyRelation, "relations"),
            (self.datums, RequiredDatum, "datums"), (self.checks, RequiredCheck, "checks"),
            (self.parameters, TemplateParameter, "parameters"), (self.case_votes, CaseVote, "case_votes"),
        ):
            if not isinstance(value, tuple) or any(not isinstance(item, kind) for item in value):
                raise AssemblyTemplateError(f"{name} has an invalid item")
        if not self.roles:
            raise AssemblyTemplateError("an assembly template needs at least one role")
        role_ids = [r.role_id for r in self.roles]
        if len(set(role_ids)) != len(role_ids):
            raise AssemblyTemplateError("role ids must be unique")
        if role_ids != sorted(role_ids):
            raise AssemblyTemplateError("roles must be sorted by role_id")
        roles = {r.role_id: r for r in self.roles}
        roots = [r for r in self.roles if r.parent_role is None]
        if len(roots) != 1:
            raise AssemblyTemplateError("an assembly template needs exactly one root role")
        for r in self.roles:
            if r.parent_role is not None and r.parent_role not in roles:
                raise AssemblyTemplateError(f"role {r.role_id!r} names unknown parent {r.parent_role!r}")
        # acyclic: walk up from every role
        for r in self.roles:
            seen, cur = set(), r
            while cur.parent_role is not None:
                if cur.role_id in seen:
                    raise AssemblyTemplateError("role tree has a cycle")
                seen.add(cur.role_id)
                cur = roles[cur.parent_role]
        params = {p.name: p for p in self.parameters}
        for r in self.roles:
            if r.cardinality.project_derived:
                p = params.get(r.cardinality.parameter)
                if p is None or p.form is not ParameterForm.COUNT:
                    raise AssemblyTemplateError(
                        f"role {r.role_id!r} is project-derived but names no COUNT parameter with a basis"
                    )
            if r.component_template_family is None and r.required_datum_roles:
                pass
        datum_roles = {d.datum_role for d in self.datums}
        if len(datum_roles) != len(self.datums):
            raise AssemblyTemplateError("datum roles must be unique")
        for d in self.datums:
            if d.published_by_role not in roles:
                raise AssemblyTemplateError(f"datum {d.datum_role!r} is published by unknown role {d.published_by_role!r}")
        for r in self.roles:
            for dr in r.required_datum_roles:
                if dr not in datum_roles:
                    raise AssemblyTemplateError(f"role {r.role_id!r} requires unknown datum role {dr!r}")
        rel_ids = [x.relation_id for x in self.relations]
        if len(set(rel_ids)) != len(rel_ids):
            raise AssemblyTemplateError("relation ids must be unique")
        for x in self.relations:
            if x.subject_role not in roles or x.object_role not in roles:
                raise AssemblyTemplateError(f"relation {x.relation_id!r} names an unknown role")
            if x.datum_role is not None and x.datum_role not in datum_roles:
                raise AssemblyTemplateError(f"relation {x.relation_id!r} carries unknown datum role {x.datum_role!r}")
        for c in self.checks:
            for role in c.roles:
                if role not in roles:
                    raise AssemblyTemplateError(f"check {c.check_id!r} names unknown role {role!r}")
        _refs(self.applicability, "applicability")
        _refs(self.basis_refs, "basis_refs")
        _refs(self.open_boundaries, "open_boundaries")
        if not self.basis_refs:
            raise AssemblyTemplateError("an assembly template needs at least one basis ref")
        require_identifier(self.harvested_from_project, "harvested_from_project")
        require_identifier(self.harvested_from_run, "harvested_from_run")
        if self.predecessor_ref is not None:
            _text(self.predecessor_ref, "predecessor_ref")
        # the rule that makes this a template and not a model
        payload = self.to_dict()
        for p in self.parameters:
            _reject_coordinates(json.loads(p.value_json), f"parameters.{p.name}", allow_float=p.form is ParameterForm.MODULE_RATIO)
        stripped = dict(payload)
        stripped["parameters"] = []
        _reject_coordinates(stripped, "template", allow_float=False)

    def distinct_vote_projects(self) -> tuple[str, ...]:
        return tuple(sorted({v.project_id for v in self.case_votes}))

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "template_id": self.template_id, "typology": self.typology, "edition": self.edition,
            "roles": [r.to_dict() for r in self.roles], "relations": [x.to_dict() for x in self.relations],
            "datums": [d.to_dict() for d in self.datums], "checks": [c.to_dict() for c in self.checks],
            "parameters": [p.to_dict() for p in self.parameters], "applicability": list(self.applicability),
            "basis_refs": list(self.basis_refs), "case_votes": [v.to_dict() for v in self.case_votes],
            "harvested_from_project": self.harvested_from_project, "harvested_from_run": self.harvested_from_run,
            "open_boundaries": list(self.open_boundaries), "predecessor_ref": self.predecessor_ref,
            **no_authority(_RECORD_AUTHORITY),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "BuildingAssemblyTemplate":
        if value.get("schema") != cls.SCHEMA:
            raise AssemblyTemplateError("unsupported assembly template schema")
        return cls(
            template_id=value["template_id"], typology=value["typology"], edition=value["edition"],
            roles=tuple(AssemblyRole.from_dict(r) for r in value["roles"]),
            relations=tuple(AssemblyRelation.from_dict(x) for x in value["relations"]),
            datums=tuple(RequiredDatum.from_dict(d) for d in value["datums"]),
            checks=tuple(RequiredCheck.from_dict(c) for c in value["checks"]),
            parameters=tuple(TemplateParameter.from_dict(p) for p in value["parameters"]),
            applicability=tuple(value["applicability"]), basis_refs=tuple(value["basis_refs"]),
            case_votes=tuple(CaseVote.from_dict(v) for v in value["case_votes"]),
            harvested_from_project=value["harvested_from_project"], harvested_from_run=value["harvested_from_run"],
            open_boundaries=tuple(value.get("open_boundaries", ())), predecessor_ref=value.get("predecessor_ref"),
        )


def require_assembly_votes(template: BuildingAssemblyTemplate, *, waiver_ref: str | None = None) -> None:
    """Two distinct projects before promotion, or a recorded waiver."""

    if not isinstance(template, BuildingAssemblyTemplate):
        raise AssemblyTemplateError("template must be a BuildingAssemblyTemplate")
    if waiver_ref is not None:
        _text(waiver_ref, "two-vote waiver_ref")
        return
    votes = template.distinct_vote_projects()
    if len(votes) < LIBRARY_PROMOTION_MIN_VOTES:
        raise AssemblyTemplateError(
            f"assembly promotion requires votes from at least {LIBRARY_PROMOTION_MIN_VOTES} distinct projects, found {list(votes)}"
        )


# ------------------------------------------------------------------ binding
@dataclass(frozen=True, slots=True)
class RoleBinding:
    """One role bound to project components, or declined with a reason."""

    role_id: str
    component_ids: tuple[str, ...] = ()
    indices: tuple[str, ...] = ()
    declined_reason: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.role_id, "role_id")
        _refs(self.component_ids, f"role {self.role_id} component_ids")
        if not isinstance(self.indices, tuple) or any(not isinstance(i, str) or not i for i in self.indices):
            raise AssemblyTemplateError("indices must be names")
        if self.declined_reason is not None:
            _text(self.declined_reason, "declined_reason")
            if self.component_ids:
                raise AssemblyTemplateError(f"role {self.role_id!r} is both bound and declined")
        _refs(self.evidence_refs, f"role {self.role_id} evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {"role_id": self.role_id, "component_ids": list(self.component_ids), "indices": list(self.indices),
                "declined_reason": self.declined_reason, "evidence_refs": list(self.evidence_refs)}


@dataclass(frozen=True, slots=True)
class AssemblyTemplateBinding:
    """A project's consumption of a template: roles, datums, counts, evidence."""

    template_ref: str
    template_digest: str
    project_id: str
    run_id: str
    role_bindings: tuple[RoleBinding, ...]
    datum_bindings: tuple[tuple[str, str], ...]        # (datum_role, project datum id)
    parameters: tuple[TemplateParameter, ...]         # project-derived counts with basis
    evidence_rebinding: tuple[tuple[str, str], ...] = field(default=())

    SCHEMA = "AssemblyTemplateBinding@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "template_ref": self.template_ref, "template_digest": self.template_digest,
            "project_id": self.project_id, "run_id": self.run_id,
            "role_bindings": [r.to_dict() for r in self.role_bindings],
            "datum_bindings": [{"datum_role": a, "datum_id": b} for a, b in self.datum_bindings],
            "parameters": [p.to_dict() for p in self.parameters],
            "evidence_rebinding": [{"template_ref": a, "local_ref": b} for a, b in self.evidence_rebinding],
            **no_authority(_RECORD_AUTHORITY),
        }


def bind_assembly_template(
    template: BuildingAssemblyTemplate,
    *,
    template_ref: str,
    project_id: str,
    run_id: str,
    components: tuple[DesignComponent, ...],
    role_bindings: tuple[RoleBinding, ...],
    datum_bindings: tuple[tuple[str, str], ...],
    parameters: tuple[TemplateParameter, ...] = (),
    evidence_rebinding: tuple[tuple[str, str], ...] = (),
) -> AssemblyTemplateBinding:
    """Bind a template to one project; every gap fails typed."""

    if not isinstance(template, BuildingAssemblyTemplate):
        raise AssemblyTemplateError("template must be a BuildingAssemblyTemplate")
    _text(template_ref, "template_ref")
    require_identifier(project_id, "project_id")
    require_identifier(run_id, "run_id")
    component_ids = {c.component_id for c in components}
    bound = {}
    for rb in role_bindings:
        if not isinstance(rb, RoleBinding):
            raise AssemblyTemplateError("role_bindings must be RoleBinding items")
        if rb.role_id in bound:
            raise AssemblyTemplateError(f"role {rb.role_id!r} is bound twice")
        bound[rb.role_id] = rb
    roles = {r.role_id: r for r in template.roles}
    unknown = sorted(set(bound) - set(roles))
    if unknown:
        raise AssemblyTemplateError(f"binding names roles absent from the template: {unknown}")
    missing = sorted(set(roles) - set(bound))
    if missing:
        raise AssemblyTemplateError(f"roles neither bound nor declined: {missing}")
    params = {p.name: p for p in parameters}
    for role in template.roles:
        rb = bound[role.role_id]
        if rb.declined_reason is not None:
            continue
        stray = sorted(set(rb.component_ids) - component_ids)
        if stray:
            raise AssemblyTemplateError(f"role {role.role_id!r} binds components absent from the project: {stray}")
        if role.cardinality.project_derived:
            p = params.get(role.cardinality.parameter)
            if p is None:
                raise AssemblyTemplateError(f"role {role.role_id!r} needs project parameter {role.cardinality.parameter!r}")
            band = json.loads(p.value_json)
            expected = band.get("adopted") if isinstance(band, dict) else None
            if expected is None:
                raise AssemblyTemplateError(
                    f"role {role.role_id!r} count parameter {p.name!r} has no adopted value"
                )
        else:
            expected = role.cardinality.value
        actual = len(rb.indices) if rb.indices else len(rb.component_ids)
        if actual != expected:
            raise AssemblyTemplateError(
                f"role {role.role_id!r} cardinality {expected} not met: bound {actual}"
            )
        if role.indexing and rb.indices:
            if role.cardinality.project_derived:
                stray = sorted(set(rb.indices) - set(role.indexing))
                if stray:
                    raise AssemblyTemplateError(f"role {role.role_id!r} indices outside the template indexing: {stray}")
            elif tuple(sorted(rb.indices)) != tuple(sorted(role.indexing)):
                raise AssemblyTemplateError(f"role {role.role_id!r} indices differ from the template indexing")
    datum_roles = {d.datum_role for d in template.datums}
    seen = set()
    for datum_role, datum_id in datum_bindings:
        if datum_role not in datum_roles:
            raise AssemblyTemplateError(f"binding names unknown datum role {datum_role!r}")
        require_identifier(datum_id, "datum_id")
        seen.add(datum_role)
    # a datum role is required only where its publishing role is bound (not declined)
    for d in template.datums:
        publisher = bound[d.published_by_role]
        if publisher.declined_reason is None and d.datum_role not in seen:
            raise AssemblyTemplateError(f"datum role {d.datum_role!r} is unbound although its publisher is bound")
    return AssemblyTemplateBinding(
        template_ref=template_ref, template_digest=template.digest, project_id=project_id, run_id=run_id,
        role_bindings=tuple(sorted(role_bindings, key=lambda r: r.role_id)),
        datum_bindings=tuple(sorted(datum_bindings)), parameters=tuple(sorted(parameters, key=lambda p: p.name)),
        evidence_rebinding=tuple(sorted(evidence_rebinding)),
    )


__all__ = [
    "AssemblyRelation", "AssemblyRole", "AssemblyTemplateBinding", "AssemblyTemplateError",
    "BuildingAssemblyTemplate", "Cardinality", "CheckKind", "LIBRARY_PROMOTION_MIN_VOTES",
    "RequiredCheck", "RequiredDatum", "RoleBinding", "bind_assembly_template", "require_assembly_votes",
]
