"""The persisted board scene, excluding viewport state and image bytes."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...application.boards import BoardScene, BoardView


class BoardRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    base_revision_sha256: str | None = Field(alias="baseRevisionSha256", pattern=r"^[0-9a-f]{64}$")
    title: str = Field(default="MonkeyBoard", min_length=1, max_length=200)
    elements: list[dict[str, Any]]
    seen_documents: list[str] = Field(alias="seenDocuments")


class BoardDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    title: str
    elements: list[dict[str, Any]]
    seen_documents: list[str] = Field(alias="seenDocuments")
    revision_sha256: str | None = Field(alias="revisionSha256")


class BoardExportPageDto(BaseModel):
    """One exact registered page, in the order it appears on the board."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[0-9a-f]{64}$")
    revision_ref: str | None = Field(alias="revisionRef", default=None)
    page_index: int = Field(alias="pageIndex", ge=0, strict=True)


class BoardExportRequestDto(BaseModel):
    """A transient clean-source export; the caller supplies no filesystem path."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    pages: list[BoardExportPageDto] = Field(min_length=1, max_length=100)
    format: Literal["merged-pdf", "page-pdfs", "png", "jpeg"]
    zip: bool = False
    max_edge: int | None = Field(default=None, alias="maxEdge", ge=1, le=2048, strict=True,
                                 description="Optional longest pixel edge for transient PNG/JPEG previews; omitted exports retain 144 dpi.")


class BoardRegionDto(BaseModel):
    """A rectangle of the board in scene coordinates."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    width: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    height: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)


class BoardViewRequestDto(BaseModel):
    """One frame (by element id) or region of the board to draw and list; neither is the whole scene."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    frame_id: str | None = Field(default=None, alias="frameId", min_length=1, max_length=256)
    region: BoardRegionDto | None = None
    max_edge: int = Field(default=2048, alias="maxEdge", ge=64, le=2048, strict=True)
    revision_sha256: str | None = Field(default=None, alias="revisionSha256", pattern=r"^[0-9a-f]{64}$")


class BoardViewImageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    mime_type: Literal["image/png"] = Field(alias="mimeType")
    width: int
    height: int
    sha256: str
    data: str = Field(description="The PNG, base64.")


class BoardViewDto(BaseModel):
    """A region of the board as drawn (pages under marks) and every mark in it, with where it lies on each page."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    revision_sha256: str | None = Field(alias="revisionSha256")
    frame: dict[str, Any] | None
    frames: list[dict[str, Any]] = Field(description="Every frame on the board: id, name and box.")
    region: dict[str, float] = Field(description="The drawn region: x, y, width, height in scene coordinates.")
    scale: float = Field(description="Image pixels per scene unit.")
    image: BoardViewImageDto
    pages: list[dict[str, Any]] = Field(description="Registered pages in the region; tracingPaper marks a tracing-paper snapshot.")
    annotations: list[dict[str, Any]] = Field(description=(
        "Marks in the region in scene order: type, colours, box, updatedAt, frameId, text, and onPages "
        "(each page it lies over, the shared part as page fractions x0..x1, y0..y1 from the top left)."))
    annotation_count: int = Field(alias="annotationCount", description="All marks in the region; at most 2,000 are listed.")


def board_view_dto(project_id: str, view: BoardView) -> BoardViewDto:
    import base64
    import hashlib

    x, y, width, height = view.region
    return BoardViewDto(
        project_id=project_id, revision_sha256=view.revision_sha256, frame=None if view.frame is None else dict(view.frame),
        frames=[dict(row) for row in view.frames], region={"x": x, "y": y, "width": width, "height": height},
        scale=view.scale,
        image=BoardViewImageDto(mime_type="image/png", width=view.width, height=view.height,
                                sha256=hashlib.sha256(view.png).hexdigest(), data=base64.b64encode(view.png).decode("ascii")),
        pages=[dict(row) for row in view.pages], annotations=[dict(row) for row in view.annotations],
        annotation_count=view.annotation_count,
    )


def board_dto(scene: BoardScene) -> BoardDto:
    return BoardDto(project_id=scene.project_id, title=scene.title,
                    elements=[dict(row) for row in scene.elements], seen_documents=list(scene.seen_documents),
                    revision_sha256=scene.revision_sha256)
