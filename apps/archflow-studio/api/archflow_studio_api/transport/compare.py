"""The wire form of ``GET /api/candidates/{id}/compare``: Before / After / Why.

Counts and boxes are the inspection records' own numbers; ``why`` is the
sentence the candidate was made from, when this process still holds the
proposal, and says where it came from either way.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.compare import Comparison, ComponentChange, ObjectChange

Vector3 = tuple[float, float, float]


class BoxDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    min: Vector3
    max: Vector3


class CompareObjectDto(BaseModel):
    """One exported object, before and after, by the export's own name."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    name: str
    seat_id: str = Field(alias="seatId")
    component_id: str | None = Field(alias="componentId")
    producer_op: str | None = Field(alias="producerOp")
    status: str = Field(description="unchanged | changed | added | removed")
    before: BoxDto | None
    after: BoxDto | None


class CompareComponentDto(BaseModel):
    """A component's objects, counted by what happened to them."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    component_id: str = Field(alias="componentId")
    changed: int
    unchanged: int
    added: int
    removed: int


class CompareDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    candidate_id: str = Field(alias="candidateId")
    against: str = Field(description="the run whose exports are the 'before'")
    why: str | None = Field(
        description="the sentence the candidate was made from, verbatim, when "
        "this process still holds its proposal; null otherwise",
    )
    why_source: str = Field(
        alias="whySource",
        description="proposal (held in this process) or unavailable (the "
        "proposal store does not survive a restart)",
    )
    tolerance: float = Field(
        description="boxes closer than this on every coordinate are the same box"
    )
    changed: int
    unchanged: int
    added: int
    removed: int
    components: list[CompareComponentDto] = Field(
        description="components with something to say first, most first"
    )
    objects: list[CompareObjectDto]


def _box(lo: Vector3 | None, hi: Vector3 | None) -> BoxDto | None:
    return BoxDto(min=lo, max=hi) if lo is not None and hi is not None else None


def _object(change: ObjectChange) -> CompareObjectDto:
    return CompareObjectDto(
        name=change.name,
        seat_id=change.seat_id,
        component_id=change.component_id,
        producer_op=change.producer_op,
        status=change.status,
        before=_box(change.before_min, change.before_max),
        after=_box(change.after_min, change.after_max),
    )


def _component(change: ComponentChange) -> CompareComponentDto:
    return CompareComponentDto(
        component_id=change.component_id,
        changed=change.changed,
        unchanged=change.unchanged,
        added=change.added,
        removed=change.removed,
    )


def compare_dto(comparison: Comparison) -> CompareDto:
    return CompareDto(
        candidate_id=comparison.candidate_id,
        against=comparison.against,
        why=comparison.why,
        why_source=comparison.why_source,
        tolerance=comparison.tolerance,
        changed=comparison.changed,
        unchanged=comparison.unchanged,
        added=comparison.added,
        removed=comparison.removed,
        components=[_component(item) for item in comparison.components],
        objects=[_object(item) for item in comparison.objects],
    )
