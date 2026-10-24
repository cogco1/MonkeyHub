"""The wire form of ``POST /api/markup/resolve``: marks on a page or strokes on the model in, what each lies on out.

The marks are the ink Board and the model view already draw (``DocumentGestureDto``,
``ModelGestureDto``); a reading is ``MarkReading.to_dict()`` key for key, its
hits in the order the reader gives them, targets first.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...application.markup import BORDERLINE_HIT, NOTHING, PARTIAL, UNBOUND, PageMarks
from .intent import DocumentGestureDto, ModelGestureDto
from .pick import STATE_DIGEST_PATTERN

MAX_MARKS = 2000


class MarkupPageDto(BaseModel):
    """The registered page the marks were drawn on, addressed as the documents list names it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=STATE_DIGEST_PATTERN)
    page_index: int = Field(alias="pageIndex", ge=0)
    drawing_revision_ref: str | None = Field(alias="drawingRevisionRef", default=None, description=(
        "A view drawing's revisionRef, which reading its marks needs; omitted for any other page."))


class MarkupResolveRequestDto(BaseModel):
    """Marks on one page (``page`` and ``marks``) or strokes on the model (``stateDigest`` and ``strokes``)."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    page: MarkupPageDto | None = None
    marks: list[DocumentGestureDto] = Field(default_factory=list, max_length=MAX_MARKS)
    state_digest: str | None = Field(alias="stateDigest", default=None, pattern=STATE_DIGEST_PATTERN, description=(
        "For strokes: the stateDigest the model view was loaded with; another state is refused as STALE_BASE."))
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1, description=(
        "For strokes: the run the model view shows; omitted uses the project's working design."))
    strokes: list[ModelGestureDto] = Field(default_factory=list, max_length=MAX_MARKS)

    @model_validator(mode="after")
    def one_surface(self) -> MarkupResolveRequestDto:
        if (self.page is None) == (self.state_digest is None):
            raise ValueError("send a page and its marks, or a stateDigest and the strokes drawn on that model")
        if self.page is not None and (self.strokes or self.source_run_id is not None):
            raise ValueError("strokes on the model are sent without a page")
        if self.page is None and self.marks:
            raise ValueError("marks on a page are sent with that page")
        return self


class MarkHitDto(BaseModel):
    """One component, or one element of it, a mark lies on."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    component_id: str = Field(alias="componentId")
    element_id: str | None = Field(alias="elementId", description="Null when no one row of the component produced it.")
    object_ids: list[str] = Field(alias="objectIds")
    coverage: float = Field(description="How much of all of it on the page lies inside the mark (circled whole is 1).")
    share: float = Field(description="How much of what is inside the mark is its own; for an arrow or a model stroke, "
                                     "its share of the weight.")
    central: bool = Field(description="It reaches the middle of the mark, not only its rim.")
    target: bool = Field(description="The mark clearly means it.")
    borderline: bool = Field(description="It lies in the mark's rim and may be meant.")


class MarkReadingDto(BaseModel):
    """One mark's subjects, targets first, and why it goes back to the architect when it does."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    mark_id: str = Field(alias="markId")
    kind: str
    zone: str = Field(description="area, stroke or point on a page; model for a stroke on the model.")
    hits: list[MarkHitDto]
    ambiguous: bool
    reason: Literal["nothing", "unbound", "partial", "borderline"] | None = Field(description=(
        f"{NOTHING}: nothing is drawn under it; {UNBOUND}: what is drawn names no component of this record; "
        f"{PARTIAL}: it only touches several things; {BORDERLINE_HIT}: something else lies at its rim. Null: clear."))


class MarkupReadingsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    source: Literal["drawing", "sheet", "id-image", "page", "model"] = Field(description=(
        "What the marks were read against: a view drawing's lines, a view sheet's placed views, a camera pass's "
        "object-ID image, a page that names no object (every mark reads nothing), or the model's own objects."))
    state_digest: str | None = Field(alias="stateDigest", description=(
        "The record state that answered for what the marks lie on; null when none could."))
    readings: list[MarkReadingDto]


def markup_readings_dto(marks: PageMarks) -> MarkupReadingsDto:
    return MarkupReadingsDto(source=marks.source, state_digest=marks.state_digest,
                             readings=[MarkReadingDto.model_validate(reading.to_dict()) for reading in marks.readings])
