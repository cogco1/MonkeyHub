"""The wire form of one impact closure, and of what the record cannot see.

``unknownCoverage`` is not a warning decorating an otherwise complete answer.
It is half the answer: the components no dependency edge mentions are the ones
this closure was never able to speak about, and a UI that showed only the
closure would render silence as safety.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.impact import Impact


class ImpactLockDto(BaseModel):
    """A locked parameter inside the closure, and whose lock it is."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    ref: str
    authority: str


class UnknownCoverageDto(BaseModel):
    """The components no edge touches: unknown impact, not zero impact."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    count: int
    component_ids: list[str] = Field(alias="componentIds")


class ImpactDto(BaseModel):
    """What the change reaches, in the kernel's own prefixed refs."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    direct: list[str]
    propagated: list[str] = Field(
        description="the kernel's closure of the target, minus the target",
    )
    protected: list[str]
    conflicts: list[str] = Field(
        description="protected refs the change would propagate to",
    )
    locks: list[ImpactLockDto]
    unknown_coverage: UnknownCoverageDto = Field(alias="unknownCoverage")
    honesty: list[str]


def to_dto(answer: Impact) -> ImpactDto:
    """Shape one impact for the wire; every ref in it is the kernel's."""

    return ImpactDto(
        direct=list(answer.direct),
        propagated=list(answer.propagated),
        protected=list(answer.protected),
        conflicts=list(answer.conflicts),
        locks=[
            ImpactLockDto(ref=lock.ref, authority=lock.authority)
            for lock in answer.locks
        ],
        unknown_coverage=UnknownCoverageDto(
            count=len(answer.unknown_coverage),
            component_ids=list(answer.unknown_coverage),
        ),
        honesty=list(answer.honesty),
    )
