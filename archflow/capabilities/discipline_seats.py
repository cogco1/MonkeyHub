"""Discipline seats: handover, projection, write scope, schedule (P095).

Modelled on how design offices divide labour. One provider protocol,
several *seats*: a seat names the component subtrees it may write, the
phases and quadrants it may act in, and the seats whose handovers it
consumes. A handover (提资) is compiled deterministically from another
seat's committed state into constraints — published interface datums,
exclusion bounds, open obligations — never raw context. A seat context
keeps only the owned subtree plus its ancestors, the inherited
principles, and the consumed handovers. Reviewer seats own nothing and
carry no authority. All records are authority-free derived views.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from archflow.capabilities.declaration import DeclarationQuadrant
from archflow.contracts.authority import no_authority
from archflow.project.refs import require_identifier
from archflow.state.design_maturity import DesignPhase
from archflow.state.developed_design import (
    DevelopedDesignState,
    DevelopmentDiscipline,
    DevelopmentObligationStatus,
)
from archflow.state.geometry_program import InterfaceDatum, ProjectGrids, ProjectLevels, verify_project_datums
from archflow.state.spatial import DesignComponent, SpatialOptionProposal

_RECORD_AUTHORITY = (
    "canonical_write_authority",
    "design_authority",
    "stage_acceptance_authority",
)


class SeatError(ValueError):
    """Typed failure of the seat contracts."""


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sorted_unique(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise SeatError(f"{field} must be a tuple")
    for item in values:
        require_identifier(item, field)
    if tuple(sorted(set(values))) != values:
        raise SeatError(f"{field} must be sorted and unique")
    return values


@dataclass(frozen=True, slots=True)
class SeatSpec:
    """One role-scoped provider seat.

    ``owned_component_ids`` are subtree roots; the seat may bind any
    descendant. ``disciplines`` name which development obligations the
    seat is expected to discharge. ``consumes`` names the seats whose
    handovers it reads. A reviewer seat owns nothing and writes nothing.
    """

    seat_id: str
    disciplines: tuple[DevelopmentDiscipline, ...]
    owned_component_ids: tuple[str, ...]
    phases: tuple[DesignPhase, ...]
    quadrants: tuple[DeclarationQuadrant, ...]
    consumes: tuple[str, ...] = ()
    reviewer: bool = False

    SCHEMA = "SeatSpec@1"

    def __post_init__(self) -> None:
        require_identifier(self.seat_id, "seat_id")
        for value, kind, field in (
            (self.disciplines, DevelopmentDiscipline, "disciplines"),
            (self.phases, DesignPhase, "phases"),
            (self.quadrants, DeclarationQuadrant, "quadrants"),
        ):
            if not isinstance(value, tuple) or any(
                not isinstance(item, kind) for item in value
            ):
                raise SeatError(f"{field} has an invalid item")
            if len(set(value)) != len(value):
                raise SeatError(f"{field} repeats an item")
        _sorted_unique(self.owned_component_ids, "owned_component_ids")
        _sorted_unique(self.consumes, "consumes")
        if self.seat_id in self.consumes:
            raise SeatError("a seat cannot consume its own handover")
        if not self.phases:
            raise SeatError("a seat must name at least one phase")
        if self.reviewer:
            if self.owned_component_ids:
                raise SeatError("a reviewer seat owns no component subtree")
        elif not self.owned_component_ids:
            raise SeatError("an authoring seat must own at least one subtree")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "seat_id": self.seat_id,
            "disciplines": [item.value for item in self.disciplines],
            "owned_component_ids": list(self.owned_component_ids),
            "phases": [item.value for item in self.phases],
            "quadrants": [item.value for item in self.quadrants],
            "consumes": list(self.consumes),
            "reviewer": self.reviewer,
        }


def _components(proposal: SpatialOptionProposal) -> dict[str, DesignComponent]:
    if not isinstance(proposal, SpatialOptionProposal):
        raise SeatError("proposal must be a SpatialOptionProposal")
    return {item.component_id: item for item in proposal.components}


def owned_subtree(
    proposal: SpatialOptionProposal, roots: tuple[str, ...]
) -> tuple[str, ...]:
    """Every component under the roots, roots included, sorted."""

    components = _components(proposal)
    missing = sorted(set(roots) - set(components))
    if missing:
        raise SeatError(f"owned roots are absent from the component tree: {missing}")
    result = set(roots)
    changed = True
    while changed:
        changed = False
        for item in components.values():
            if (
                item.parent_component_id in result
                and item.component_id not in result
            ):
                result.add(item.component_id)
                changed = True
    return tuple(sorted(result))


def ancestors(
    proposal: SpatialOptionProposal, ids: tuple[str, ...]
) -> tuple[str, ...]:
    """Every strict ancestor of the given components, sorted."""

    components = _components(proposal)
    result: set[str] = set()
    for component_id in ids:
        current = components.get(component_id)
        while current is not None and current.parent_component_id is not None:
            parent_id = current.parent_component_id
            if parent_id in result:
                break
            result.add(parent_id)
            current = components.get(parent_id)
    return tuple(sorted(result - set(ids)))


class HandoverKind(StrEnum):
    PUBLISHED_DATUM = "published_datum"
    EXCLUSION_BOUNDS = "exclusion_bounds"
    OPEN_OBLIGATION = "open_obligation"


@dataclass(frozen=True, slots=True)
class HandoverConstraint:
    kind: HandoverKind
    subject_id: str
    payload_json: str
    basis_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HandoverKind):
            raise SeatError("constraint kind must be HandoverKind")
        require_identifier(self.subject_id, "constraint subject_id")
        if not isinstance(self.payload_json, str):
            raise SeatError("payload_json must be text")
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise SeatError("payload_json is invalid JSON") from exc
        if _canonical(payload) != self.payload_json:
            raise SeatError("payload_json must be canonical JSON")
        if not isinstance(self.basis_refs, tuple) or any(
            not isinstance(item, str) or not item for item in self.basis_refs
        ):
            raise SeatError("basis_refs must be non-empty text")
        if tuple(sorted(set(self.basis_refs))) != self.basis_refs:
            raise SeatError("basis_refs must be sorted and unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "subject_id": self.subject_id,
            "payload_json": self.payload_json,
            "basis_refs": list(self.basis_refs),
        }


@dataclass(frozen=True, slots=True)
class SeatHandover:
    """Compiled constraints one seat hands to another (提资单)."""

    from_seat: str
    to_seat: str
    base_state_digest: str
    datums: tuple[InterfaceDatum, ...]
    constraints: tuple[HandoverConstraint, ...]

    SCHEMA = "SeatHandover@1"

    def __post_init__(self) -> None:
        require_identifier(self.from_seat, "from_seat")
        require_identifier(self.to_seat, "to_seat")
        if self.from_seat == self.to_seat:
            raise SeatError("a handover needs two distinct seats")
        if not isinstance(self.base_state_digest, str) or not self.base_state_digest:
            raise SeatError("base_state_digest must be text")
        if not isinstance(self.datums, tuple) or any(
            not isinstance(item, InterfaceDatum) for item in self.datums
        ):
            raise SeatError("datums must be InterfaceDatum items")
        if not isinstance(self.constraints, tuple) or any(
            not isinstance(item, HandoverConstraint) for item in self.constraints
        ):
            raise SeatError("constraints must be HandoverConstraint items")
        datum_ids = [item.datum_id for item in self.datums]
        if datum_ids != sorted(datum_ids) or len(set(datum_ids)) != len(datum_ids):
            raise SeatError("datums must be sorted by datum_id and unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "from_seat": self.from_seat,
            "to_seat": self.to_seat,
            "base_state_digest": self.base_state_digest,
            "datums": [item.to_dict() for item in self.datums],
            "constraints": [item.to_dict() for item in self.constraints],
            **no_authority(_RECORD_AUTHORITY),
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


Bounds = tuple[tuple[float, float, float], tuple[float, float, float]]


def _owned_objects(
    program_bindings, owned_components: tuple[str, ...]
) -> dict[str, str]:
    """object_id -> owning component for objects bound under the seat."""

    owned = set(owned_components)
    result: dict[str, str] = {}
    for binding in program_bindings:
        if binding.component_id in owned:
            for object_id in binding.object_ids:
                result[object_id] = binding.component_id
    return result


def compile_handover(
    *,
    from_seat: SeatSpec,
    to_seat: SeatSpec,
    design_state: DevelopedDesignState,
    published_datums: tuple[InterfaceDatum, ...] = (),
    program_bindings=(),
    realized_bounds: Mapping[str, Bounds] | None = None,
    object_digests: Mapping[str, str] | None = None,
) -> SeatHandover:
    """Compile what ``from_seat`` has committed into constraints for ``to_seat``.

    Included: interface datums published by a component or object the
    from-seat owns; exclusion bounds for owned objects the caller has
    realized bounds for; every OPEN obligation in the to-seat's
    disciplines. Nothing else crosses — in particular no provider text.
    """

    if not isinstance(from_seat, SeatSpec) or not isinstance(to_seat, SeatSpec):
        raise SeatError("seats must be SeatSpec")
    if from_seat.reviewer:
        raise SeatError("a reviewer seat hands nothing over")
    if not isinstance(design_state, DevelopedDesignState):
        raise SeatError("design_state must be DevelopedDesignState")
    proposal = design_state.selected_schematic.option.proposal
    owned_components = owned_subtree(proposal, from_seat.owned_component_ids)
    owned_objects = _owned_objects(program_bindings, owned_components)
    publishers = set(owned_components) | set(owned_objects)
    digests = dict(object_digests or {})

    datums = tuple(
        sorted(
            (
                item for item in published_datums
                if isinstance(item, InterfaceDatum) and item.published_by in publishers
            ),
            key=lambda item: item.datum_id,
        )
    )
    constraints: list[HandoverConstraint] = []
    for item in datums:
        basis = f"seat:{from_seat.seat_id}/datum/{item.datum_id}"
        constraints.append(
            HandoverConstraint(
                kind=HandoverKind.PUBLISHED_DATUM,
                subject_id=item.published_by,
                payload_json=_canonical(item.to_dict()),
                basis_refs=(basis,),
            )
        )
    for object_id in sorted(realized_bounds or {}):
        if object_id not in owned_objects:
            continue
        low, high = (realized_bounds or {})[object_id]
        payload = {
            "component_id": owned_objects[object_id],
            "min": [float(v) for v in low],
            "max": [float(v) for v in high],
        }
        basis_refs = tuple(
            sorted(
                {f"seat:{from_seat.seat_id}/object/{object_id}"}
                | ({f"object-digest:{digests[object_id]}"} if object_id in digests else set())
            )
        )
        constraints.append(
            HandoverConstraint(
                kind=HandoverKind.EXCLUSION_BOUNDS,
                subject_id=object_id,
                payload_json=_canonical(payload),
                basis_refs=basis_refs,
            )
        )
    for obligation in sorted(design_state.obligations, key=lambda o: o.obligation_id):
        if (
            obligation.status is DevelopmentObligationStatus.OPEN
            and obligation.discipline in to_seat.disciplines
        ):
            payload = {
                "obligation_id": obligation.obligation_id,
                "discipline": obligation.discipline.value,
                "statement": obligation.statement,
                "priority": obligation.priority.value,
            }
            basis_refs = tuple(sorted(set(obligation.source_refs) | set(obligation.dependency_refs)))
            constraints.append(
                HandoverConstraint(
                    kind=HandoverKind.OPEN_OBLIGATION,
                    subject_id=obligation.obligation_id,
                    payload_json=_canonical(payload),
                    basis_refs=basis_refs or (f"obligation:{obligation.obligation_id}",),
                )
            )
    return SeatHandover(
        from_seat=from_seat.seat_id,
        to_seat=to_seat.seat_id,
        base_state_digest=design_state.state_digest,
        datums=datums,
        constraints=tuple(constraints),
    )


@dataclass(frozen=True, slots=True)
class DeclaredEngagement:
    """A consuming object allowed to enter one host's exclusion bound, on record."""

    subject_id: str
    host_id: str
    reason: str
    basis_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_identifier(self.subject_id, "engagement subject_id")
        require_identifier(self.host_id, "engagement host_id")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise SeatError("an engagement needs a reason")
        if not isinstance(self.basis_refs, tuple) or not self.basis_refs or any(
            not isinstance(item, str) or not item for item in self.basis_refs
        ):
            raise SeatError("an engagement needs at least one basis ref")

    def to_dict(self) -> dict[str, object]:
        return {"subject_id": self.subject_id, "host_id": self.host_id, "reason": self.reason, "basis_refs": list(self.basis_refs)}


@dataclass(frozen=True, slots=True)
class ExclusionViolation:
    subject_id: str
    host_id: str
    depth: tuple[float, float, float]

    def to_dict(self) -> dict[str, object]:
        return {"subject_id": self.subject_id, "host_id": self.host_id, "depth": list(self.depth)}


def check_handover_exclusions(
    handover: SeatHandover,
    object_bounds: Mapping[str, Bounds],
    *,
    tolerance: float = 0.001,
    engagements: tuple[DeclaredEngagement, ...] = (),
) -> tuple[ExclusionViolation, ...]:
    """Report consuming objects that enter a handover's exclusion bounds (M097).

    A shared face is contact, not a violation: every axis must overlap by
    more than ``tolerance``. A declared engagement silences exactly its
    (subject, host) pair and nothing else.
    """

    if not isinstance(handover, SeatHandover):
        raise SeatError("handover must be a SeatHandover")
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or tolerance < 0:
        raise SeatError("tolerance must be a non-negative number")
    allowed = {(e.subject_id, e.host_id) for e in engagements}
    violations: list[ExclusionViolation] = []
    for constraint in handover.constraints:
        if constraint.kind is not HandoverKind.EXCLUSION_BOUNDS:
            continue
        payload = json.loads(constraint.payload_json)
        host_lo, host_hi = payload["min"], payload["max"]
        for subject_id in sorted(object_bounds):
            if subject_id == constraint.subject_id or (subject_id, constraint.subject_id) in allowed:
                continue
            lo, hi = object_bounds[subject_id]
            depth = tuple(min(hi[k], host_hi[k]) - max(lo[k], host_lo[k]) for k in range(3))
            if all(d > tolerance for d in depth):
                violations.append(ExclusionViolation(subject_id=subject_id, host_id=constraint.subject_id, depth=tuple(round(d, 6) for d in depth)))
    return tuple(sorted(violations, key=lambda v: (v.subject_id, v.host_id)))


@dataclass(frozen=True, slots=True)
class SeatAuthoringContext:
    """What one seat may read: its subtree, ancestors, principles, handovers."""

    seat_id: str
    base_state_digest: str
    phase: DesignPhase
    owned_component_ids: tuple[str, ...]
    visible_component_ids: tuple[str, ...]
    components: tuple[DesignComponent, ...]
    inherited_commitment_refs: tuple[str, ...]
    handovers: tuple[SeatHandover, ...]
    project_datums: tuple[InterfaceDatum, ...] = ()

    SCHEMA = "SeatAuthoringContext@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "seat_id": self.seat_id,
            "base_state_digest": self.base_state_digest,
            "phase": self.phase.value,
            "owned_component_ids": list(self.owned_component_ids),
            "visible_component_ids": list(self.visible_component_ids),
            "components": [item.to_dict() for item in self.components],
            "inherited_commitment_refs": list(self.inherited_commitment_refs),
            "handovers": [item.to_dict() for item in self.handovers],
            "project_datums": [item.to_dict() for item in self.project_datums],
            **no_authority(_RECORD_AUTHORITY),
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


def project_seat_context(
    *,
    seat: SeatSpec,
    design_state: DevelopedDesignState,
    inherited_commitment_refs: tuple[str, ...],
    handovers: tuple[SeatHandover, ...] = (),
    project_levels: ProjectLevels | None = None,
    project_grids: ProjectGrids | None = None,
) -> SeatAuthoringContext:
    """Project the design state onto one seat; sibling subtrees never leak.

    Project levels and grids (P098) enter every seat's context as
    datums: a seat binds to them, it never restates them.
    """

    if project_levels is not None and not isinstance(project_levels, ProjectLevels):
        raise SeatError("project_levels must be ProjectLevels")
    if project_grids is not None and not isinstance(project_grids, ProjectGrids):
        raise SeatError("project_grids must be ProjectGrids")

    if not isinstance(seat, SeatSpec):
        raise SeatError("seat must be SeatSpec")
    if seat.reviewer:
        raise SeatError("a reviewer seat has no authoring context")
    if not isinstance(design_state, DevelopedDesignState):
        raise SeatError("design_state must be DevelopedDesignState")
    phase = design_state.active_phase
    if phase not in seat.phases:
        raise SeatError(
            f"seat {seat.seat_id!r} may not act in phase {phase.value!r}"
        )
    for handover in handovers:
        if not isinstance(handover, SeatHandover):
            raise SeatError("handovers must be SeatHandover items")
        if handover.to_seat != seat.seat_id:
            raise SeatError("handover is addressed to another seat")
        if handover.from_seat not in seat.consumes:
            raise SeatError(
                f"seat {seat.seat_id!r} does not consume handovers from "
                f"{handover.from_seat!r}"
            )
        if handover.base_state_digest != design_state.state_digest:
            raise SeatError("handover was compiled against another design state")
    proposal = design_state.selected_schematic.option.proposal
    owned = owned_subtree(proposal, seat.owned_component_ids)
    visible = tuple(sorted(set(owned) | set(ancestors(proposal, owned))))
    components = tuple(
        item for item in proposal.components if item.component_id in set(visible)
    )
    refs = tuple(inherited_commitment_refs)
    if tuple(sorted(set(refs))) != refs:
        raise SeatError("inherited_commitment_refs must be sorted and unique")
    return SeatAuthoringContext(
        seat_id=seat.seat_id,
        base_state_digest=design_state.state_digest,
        phase=phase,
        owned_component_ids=owned,
        visible_component_ids=visible,
        components=components,
        inherited_commitment_refs=refs,
        handovers=tuple(sorted(handovers, key=lambda item: item.from_seat)),
        project_datums=tuple(
            sorted(
                (project_levels.datums() if project_levels is not None else ())
                + (project_grids.datums() if project_grids is not None else ()),
                key=lambda item: item.datum_id,
            )
        ),
    )


def check_seat_datums(
    *,
    seat: SeatSpec,
    published_datums: tuple[InterfaceDatum, ...],
    project_levels: ProjectLevels | None = None,
    project_grids: ProjectGrids | None = None,
) -> None:
    """Fail typed when a seat publishes over a project level or axis (P098).

    The seat may bind to a project datum and may publish its own
    component datums; it may not publish a datum carrying a project
    level or axis id with another kind, value, unit or publisher.
    """

    if not isinstance(seat, SeatSpec):
        raise SeatError("seat must be SeatSpec")
    if seat.reviewer:
        raise SeatError("a reviewer seat publishes nothing")
    violations = verify_project_datums(
        tuple(published_datums), project_levels, project_grids
    )
    if violations:
        raise SeatError(
            f"seat {seat.seat_id!r} overwrites project datums: " + "; ".join(violations)
        )


def schedule_seats(seats: tuple[SeatSpec, ...]) -> tuple[tuple[str, ...], ...]:
    """Order authoring seats by handover dependency into parallel rounds.

    Seats whose consumed seats are all scheduled share a round; reviewer
    seats form the final round. A consumed seat that is absent, or a
    cycle, fails typed.
    """

    by_id = {}
    for seat in seats:
        if not isinstance(seat, SeatSpec):
            raise SeatError("seats must be SeatSpec items")
        if seat.seat_id in by_id:
            raise SeatError(f"seat {seat.seat_id!r} is declared twice")
        by_id[seat.seat_id] = seat
    authors = {sid: s for sid, s in by_id.items() if not s.reviewer}
    for seat in authors.values():
        missing = sorted(set(seat.consumes) - set(authors))
        if missing:
            raise SeatError(
                f"seat {seat.seat_id!r} consumes unknown or reviewer seats {missing}"
            )
    rounds: list[tuple[str, ...]] = []
    scheduled: set[str] = set()
    while len(scheduled) < len(authors):
        ready = tuple(
            sorted(
                sid for sid, s in authors.items()
                if sid not in scheduled and set(s.consumes) <= scheduled
            )
        )
        if not ready:
            raise SeatError("seat handovers form a cycle")
        rounds.append(ready)
        scheduled.update(ready)
    reviewers = tuple(sorted(sid for sid, s in by_id.items() if s.reviewer))
    if reviewers:
        rounds.append(reviewers)
    return tuple(rounds)


__all__ = [
    "Bounds",
    "DeclaredEngagement",
    "ExclusionViolation",
    "check_handover_exclusions",
    "HandoverConstraint",
    "HandoverKind",
    "SeatAuthoringContext",
    "SeatError",
    "SeatHandover",
    "SeatSpec",
    "ancestors",
    "compile_handover",
    "owned_subtree",
    "project_seat_context",
    "schedule_seats",
]
