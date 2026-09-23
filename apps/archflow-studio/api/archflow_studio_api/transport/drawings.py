"""Whole-model elevations and top projection through the existing drawing owner."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import ModelSourceDto


class PlanDimensionPlacementDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    offset_mm: float = Field(alias="offsetMm", default=8, ge=-100, le=100, allow_inf_nan=False)


class PlanDimensionDto(BaseModel):
    """One semantic aperture, not a client-authored measurement or driving grant."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    entity_ref: str = Field(alias="entityRef", pattern=r"^entity:[A-Za-z0-9][A-Za-z0-9._-]*$")
    opening_id: str = Field(alias="openingId", min_length=1, max_length=100)
    placement: PlanDimensionPlacementDto = Field(default_factory=PlanDimensionPlacementDto)


class PlanRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    previous_revision_ref: str | None = Field(alias="previousRevisionRef", default=None)
    cut_height: float | None = Field(alias="cutHeight", default=None, allow_inf_nan=False,
                                    description="Horizontal cut elevation in the exact STEP length unit.")
    bottom: float | None = Field(default=None, allow_inf_nan=False)
    scale_denominator: int | None = Field(alias="scaleDenominator", default=None, ge=1, le=10000, strict=True)
    crop_uv: tuple[float, float, float, float] | None = Field(alias="cropUv", default=None)
    cut_line_mm: float | None = Field(alias="cutLineMm", default=None, gt=0, le=2, allow_inf_nan=False)
    visible_line_mm: float | None = Field(alias="visibleLineMm", default=None, gt=0, le=2, allow_inf_nan=False)
    hatch_spacing_mm: float | None = Field(alias="hatchSpacingMm", default=None, ge=0.5, le=20, allow_inf_nan=False)
    hidden_object_ids: list[str] | None = Field(alias="hiddenObjectIds", default=None, max_length=10000)
    dimensions: list[PlanDimensionDto] | None = Field(default=None, max_length=100)


class PlanStatusRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[0-9a-f]{64}$")
    revision_ref: str = Field(alias="revisionRef", min_length=1)
    target_model_source: ModelSourceDto | None = Field(alias="targetModelSource", default=None)
    target_stage_ref: str | None = Field(alias="targetStageRef", default=None)


class PlanDimensionChoiceDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    entity_ref: str = Field(alias="entityRef")
    opening_id: str = Field(alias="openingId")
    label: str
    parameter_key: str | None = Field(alias="parameterKey", default=None)
    parameter_unit: str | None = Field(alias="parameterUnit", default=None)
    can_drive: bool = Field(alias="canDrive")
    drive_reason: str | None = Field(alias="driveReason", default=None)


class PlanDimensionReadDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    id: str
    entity_ref: str = Field(alias="entityRef")
    opening_id: str = Field(alias="openingId")
    status: Literal["resolved", "missing", "ambiguous", "outside-view", "unverified"]
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    value: float | None = None
    label: str | None = None
    offset_mm: float = Field(alias="offsetMm")
    detail: str | None = None
    parameter_key: str | None = Field(alias="parameterKey", default=None)
    parameter_unit: str | None = Field(alias="parameterUnit", default=None)
    can_drive: bool = Field(alias="canDrive")
    drive_reason: str | None = Field(alias="driveReason", default=None)


class PlanStatusDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    status: Literal["current", "outdated", "partially-broken", "unknown"]
    detail: str
    target_model_source: ModelSourceDto | None = Field(alias="targetModelSource", default=None)
    target_stage_ref: str | None = Field(alias="targetStageRef", default=None)
    length_unit: str | None = Field(alias="lengthUnit", default=None)
    binding_changed: bool = Field(alias="bindingChanged", default=False)
    dimensions: list[PlanDimensionReadDto] = Field(default_factory=list)
    unresolved_object_ids: list[str] = Field(alias="unresolvedObjectIds", default_factory=list)


class PlanDimensionChoicesDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    length_unit: str = Field(alias="lengthUnit")
    dimensions: list[PlanDimensionChoiceDto]


class PlanDimensionProposalRequestDto(PlanStatusRequestDto):
    project_id: str = Field(alias="projectId", min_length=1)
    dimension_id: str = Field(alias="dimensionId", min_length=1)
    value: float = Field(gt=0, allow_inf_nan=False, description="Requested aperture width in the source STEP length unit.")

DrawingStyleId = Literal["arch400-white", "arch364-technical"]


class ModelViewDto(BaseModel):
    """Transient pixels from a verified model, not a material render or saved drawing."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    source: ModelSourceDto
    view: Literal["front", "back", "left", "right", "top"]
    mime_type: Literal["image/png"] = Field(alias="mimeType", default="image/png")
    data: str = Field(description="Base64 PNG bytes from the exact source model's orthographic line projection.")
    width: int = Field(ge=1, le=1024)
    height: int = Field(ge=1, le=1024)
    representation: Literal["orthographic-line-projection"] = "orthographic-line-projection"


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
