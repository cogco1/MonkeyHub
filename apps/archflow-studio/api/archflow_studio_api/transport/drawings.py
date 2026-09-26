"""Whole-model elevations, top projection, cut plans and section perspectives through the existing drawing owner."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifacts import ModelSourceDto


CLEANUP_REPORT = (
    "The deterministic cleanup the projection owner applied to this revision's lines - per-rule counts and "
    "input/output line counts - exactly as the revision's receipt retains it; null for a revision drawn before "
    "cleanup existed. It is never part of viewRecipe.")


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


Finite = Annotated[float, Field(allow_inf_nan=False)]


class PlanDressingDto(BaseModel):
    """Representation-only SVG from the built-in plan symbols, in source view units."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    asset_id: Literal["person-plan", "tree-plan"] = Field(alias="assetId")
    position_uv: tuple[Finite, Finite] = Field(alias="positionUv",
        description="View coordinates, or an offset from anchorObjectId's projected bounding-box centre.")
    size: float = Field(gt=0, le=100000, allow_inf_nan=False, description="Symbol extent in exact source length units.")
    flipped: bool = False
    anchor_object_id: str | None = Field(alias="anchorObjectId", default=None, min_length=1, max_length=200)


class PlanDressingOperationDto(BaseModel):
    """A bounded edit to an independently identified representation object."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    op: Literal["insert", "move", "scale", "flip", "delete"]
    id: str = Field(min_length=1, max_length=100)
    object: PlanDressingDto | None = None
    position_uv: tuple[Finite, Finite] | None = Field(alias="positionUv", default=None)
    size: float | None = Field(default=None, gt=0, le=100000, allow_inf_nan=False)
    flipped: bool | None = None

    @model_validator(mode="after")
    def operation_fields(self):
        required = {"insert": "object", "move": "position_uv", "scale": "size", "flip": "flipped", "delete": None}[self.op]
        for field in ("object", "position_uv", "size", "flipped"):
            if (getattr(self, field) is not None) != (field == required):
                raise ValueError(f"{self.op} requires only {required or 'id'}")
        if self.object is not None and self.object.id != self.id:
            raise ValueError("insert id must match object id")
        return self


class PlanDressingReadDto(PlanDressingDto):
    status: Literal["resolved", "missing", "outside-view"]
    resolved_uv: tuple[float, float] | None = Field(alias="resolvedUv", default=None)
    detail: str | None = None


class PlanDressingAssetDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    id: Literal["person-plan", "tree-plan"]
    polylines: list[list[tuple[float, float]]]


class PlanDressingAnchorDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    object_id: str = Field(alias="objectId")
    position_uv: tuple[float, float] = Field(alias="positionUv")


class PlanVectorDto(BaseModel):
    svg: str
    assets: list[PlanDressingAssetDto]
    anchors: list[PlanDressingAnchorDto]
    cleanup: dict[str, Any] | None = Field(default=None, description=CLEANUP_REPORT)


class PlanHatchRuleDto(BaseModel):
    """How the cut of one material is drawn, in paper units."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    spacing_mm: float | None = Field(alias="spacingMm", default=None, ge=0.5, le=20, allow_inf_nan=False,
                                     description="Perpendicular hatch spacing on paper, mm. Omitted takes this revision's hatchSpacingMm.")
    angle_deg: float | None = Field(alias="angleDeg", default=None, ge=0, lt=180, allow_inf_nan=False,
                                    description="Hatch direction, degrees anticlockwise from the sheet's x axis. Omitted is 45.")
    poche: bool = Field(default=False, description="Fill this material's cut solid (poché) instead of hatching it.")


class PlanHatchDto(BaseModel):
    """Material-keyed hatch and poché for cut solids; a material without a rule keeps hatchSpacingMm at 45 degrees."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    by_material: dict[Annotated[str, Field(min_length=1, max_length=100)], PlanHatchRuleDto] = Field(
        alias="byMaterial", max_length=100,
        description="Rules by the model's material name. Each is stored complete; an empty map removes every rule.")

    @model_validator(mode="after")
    def printable_names(self):
        if any(ord(char) < 32 or ord(char) == 127 for name in self.by_material for char in name):
            raise ValueError("material names cannot contain control characters")
        return self


class PlanBeyondDto(BaseModel):
    """How lines below the cut plane read against the cut."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    fade: float = Field(ge=0, le=1, allow_inf_nan=False,
                        description="Grey level of the lines below the cut: 0 draws them black like visible lines "
                                    "(and removes the rule), 1 fades them out.")


class DrawingAssetSourceDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[0-9a-f]{64}$")


class DrawingSourceRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    source_asset: DrawingAssetSourceDto | None = Field(alias="sourceAsset", default=None)

    @model_validator(mode="after")
    def separate_imported_source(self):
        if self.source_asset is not None and (self.model_source is not None or self.source_stage_ref is not None):
            raise ValueError("Choose sourceAsset or a design model/Stage, not both")
        return self


class PlanRequestDto(DrawingSourceRequestDto):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    previous_revision_ref: str | None = Field(alias="previousRevisionRef", default=None)
    cut_height: float | None = Field(alias="cutHeight", default=None, allow_inf_nan=False,
                                    description="Horizontal cut elevation in the source model length unit.")
    bottom: float | None = Field(default=None, allow_inf_nan=False)
    scale_denominator: int | None = Field(alias="scaleDenominator", default=None, ge=1, le=10000, strict=True)
    crop_uv: tuple[float, float, float, float] | None = Field(alias="cropUv", default=None)
    cut_line_mm: float | None = Field(alias="cutLineMm", default=None, gt=0, le=2, allow_inf_nan=False)
    visible_line_mm: float | None = Field(alias="visibleLineMm", default=None, gt=0, le=2, allow_inf_nan=False)
    hatch_spacing_mm: float | None = Field(alias="hatchSpacingMm", default=None, ge=0.5, le=20, allow_inf_nan=False)
    hatch: PlanHatchDto | None = Field(default=None, description=(
        "Material hatch and poché rules on paper, beside the pens and hatchSpacingMm. Omitted keeps the previous "
        "revision's rules; an empty byMaterial removes them."))
    beyond: PlanBeyondDto | None = Field(default=None, description=(
        "Fading of the lines below the cut. Omitted keeps the previous revision's; fade 0 removes it."))
    hidden_object_ids: list[str] | None = Field(alias="hiddenObjectIds", default=None, max_length=10000)
    dimensions: list[PlanDimensionDto] | None = Field(default=None, max_length=100)
    dressing: list[PlanDressingDto] | None = Field(default=None, max_length=100)
    dressing_operations: list[PlanDressingOperationDto] | None = Field(alias="dressingOperations", default=None, max_length=100)
    follow: Literal["live", "frozen"] | None = Field(default=None, description=(
        "live follows the project's Working Head; frozen keeps this drawing on its chosen source until it is "
        "rebuilt. Omitted keeps the previous revision's choice; a new drawing is live."))
    reason: str | None = Field(default=None, min_length=1, max_length=200, description=(
        "Why this revision is asked for, in the asker's own words, such as the correction an agent was given; "
        "omit it for a direct edit. Retained with the revision beside who asked, never in its recipe."))
    source_kind: Literal["human", "agent"] | None = Field(alias="sourceKind", default=None, description=(
        "Who this request comes from: human for a person's own edit in the drawing, agent for an agent's reading "
        "of what a person asked. Omitted is unknown; the Hub marks its Agent's requests agent. Retained with the "
        "revision beside who asked, never in its recipe, and an agent's revision is never counted toward a "
        "project recipe suggestion."))

    @model_validator(mode="after")
    def one_dressing_edit(self):
        if self.dressing is not None and self.dressing_operations is not None:
            raise ValueError("Use dressing replacement or dressingOperations, not both")
        if self.dressing_operations is not None and self.previous_revision_ref is None:
            raise ValueError("dressingOperations requires previousRevisionRef")
        return self


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
    dressing: list[PlanDressingReadDto] = Field(default_factory=list)
    cleanup: dict[str, Any] | None = Field(default=None, description=CLEANUP_REPORT)


class PlanDimensionChoicesDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    length_unit: str = Field(alias="lengthUnit")
    dimensions: list[PlanDimensionChoiceDto]


class PlanDimensionProposalRequestDto(PlanStatusRequestDto):
    project_id: str = Field(alias="projectId", min_length=1)
    dimension_id: str = Field(alias="dimensionId", min_length=1)
    value: float = Field(gt=0, allow_inf_nan=False, description="Requested aperture width in the source STEP length unit.")

DrawingStyleId = Literal["arch400-white", "arch364-technical"]

# The five model-axis directions and one isometric axonometric: the view from
# the -X, -Y, +Z side, Z up on the sheet. All are orthographic line projections.
ModelViewName = Literal["front", "back", "left", "right", "top", "axon"]


class ModelViewDto(BaseModel):
    """Transient pixels from a verified model, not a material render or saved drawing."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    source: ModelSourceDto
    view: ModelViewName
    mime_type: Literal["image/png"] = Field(alias="mimeType", default="image/png")
    data: str = Field(description="Base64 PNG bytes from the exact source model's orthographic line projection.")
    width: int = Field(ge=1, le=1024)
    height: int = Field(ge=1, le=1024)
    representation: Literal["orthographic-line-projection"] = "orthographic-line-projection"


class ElevationRequestDto(DrawingSourceRequestDto):
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


class SectionLineDto(BaseModel):
    """A vertical section plane through a plan line."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    line: tuple[tuple[float, float], tuple[float, float]] = Field(
        description="Plan points [[x1, y1], [x2, y2]] in the source model length unit (CAD X/Y, Z up); "
                    "the section is the vertical plane through them.")
    keep: Literal["left", "right"] = Field(
        description="The side kept when walking from the first point to the second. The eye stands on the other side, "
                    "and nothing there is drawn.")


class SectionPlaneDto(BaseModel):
    """Any section plane, as a point on it and its normal."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    origin: tuple[float, float, float] = Field(description="A point on the plane, CAD X/Y/Z in the source model length unit.")
    normal: tuple[float, float, float] = Field(
        description="Points from the kept side to the removed side, where the eye stands; need not be unit length.")


class SectionCameraDto(BaseModel):
    """An explicit camera (eye and target), or the default one-point perspective's eye height and field of view."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    eye: tuple[float, float, float] | None = Field(
        default=None, description="Eye position on the removed side; give it with target, or omit both for the default eye.")
    target: tuple[float, float, float] | None = Field(
        default=None, description="A point the camera looks at, through the cut; it centres the frame.")
    up: tuple[float, float, float] | None = Field(
        default=None, description="Picture up, projected into the section plane. Default +Z; required for a horizontal plane.")
    fov_deg: float | None = Field(
        alias="fovDeg", default=None,
        description="Horizontal field of view in degrees (default 55): the frame's width at the section plane.")
    eye_height: float | None = Field(
        alias="eyeHeight", default=None,
        description="Default eye only: height above the lowest cut point, in the source model unit (default 1.6 m).")


class SectionPerspectiveRequestDto(DrawingSourceRequestDto):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$",
                                   description="Default section-perspective; the same id continues that drawing's revisions.")
    section: SectionLineDto | SectionPlaneDto
    camera: SectionCameraDto | None = Field(
        default=None, description=(
            "Omit for the default one-point perspective: the eye on the removed side 1.6 m above the lowest cut point, "
            "centred on the cut, at the distance that fits the cut's width in a 55 degree field of view, the cut's centre "
            "as target; the frame is the cut with a 5% margin. The picture plane is always the section plane, so the cut "
            "is true to scale and lines along the view axis converge at the eye's foot on it. The target centres the "
            "frame; to move only the vanishing point, move the eye and keep the target at the cut's centre."))
    depth: float | None = Field(default=None, description="Keep only this far behind the section plane, in the source model unit.")
    hidden_object_ids: list[str] = Field(alias="hiddenObjectIds", default_factory=list, max_length=10000,
                                         description="Exact physical object ids left out of the cut and the view.")
    scale_denominator: int = Field(alias="scaleDenominator", default=100, ge=1, le=10000, strict=True,
                                   description="1:N at the section plane; farther geometry is drawn smaller.")
    cut_line_mm: float | None = Field(alias="cutLineMm", default=None, gt=0, le=2)
    visible_line_mm: float | None = Field(alias="visibleLineMm", default=None, gt=0, le=2)
    hatch_spacing_mm: float | None = Field(alias="hatchSpacingMm", default=None, ge=0.2, le=20,
                                           description="Poché hatch spacing on paper; default 0.5 mm.")


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


class SheetRequestDto(DrawingSourceRequestDto):
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
