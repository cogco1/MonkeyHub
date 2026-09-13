"""Whole-model elevations and top projection through the existing drawing owner."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import ModelSourceDto

DrawingStyleId = Literal["arch400-white", "arch364-technical"]

class ElevationRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    view: Literal["front", "back", "left", "right", "top"] = Field(
        default="front", description=(
            "Whole-model orthographic direction. top looks down CAD -Z with X right and Y up on the sheet; "
            "it is a top projection of visible geometry, not a cut floor plan."
        ),
    )
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    hidden_lines: bool = Field(alias="hiddenLines", default=False)
    scale_denominator: int = Field(alias="scaleDenominator", default=100, ge=1, le=10000)


class DrawingStyleDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    id: DrawingStyleId
    name: str
    name_en: str = Field(alias="nameEn")
    description: str
    description_en: str = Field(alias="descriptionEn")
    paper_size_mm: tuple[float, float] = Field(alias="paperSizeMm")
    preview_kind: Literal["presentation", "technical"] = Field(alias="previewKind")


class DrawingStylesDto(BaseModel):
    styles: list[DrawingStyleDto]


class SheetRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    style_id: DrawingStyleId = Field(alias="styleId")
    scale_denominator: int = Field(alias="scaleDenominator", default=20, strict=True, ge=1, le=10000,
                                   description="Exact drawing scale; oversized layouts are refused, never silently rescaled.")
    hidden_object_ids: list[str] = Field(alias="hiddenObjectIds", default_factory=list,
                                        description="Exact physical object ids excluded before all three visibility solves.")
    outline_object_ids: list[str] = Field(alias="outlineObjectIds", default_factory=list,
                                         description="Visible physical object ids to simplify to outlines in this sheet.")
    notes: list[str] = Field(default_factory=list)
