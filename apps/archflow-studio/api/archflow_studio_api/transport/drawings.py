"""Whole-model elevations and axonometrics, top projection, cut plans and vertical sections, section perspectives and
sheets of caller-placed views, through the existing drawing owner."""

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


LengthUnit = Literal["meter", "millimeter", "inch", "foot"]
LENGTH_UNIT = (
    "The length unit this request's coordinates and distances are written in. It must be the source model's own unit: "
    "another one is refused (DRAWING_UNIT_MISMATCH), never converted. Omitted reads them in the source unit.")


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


class PlanDrawingDto(BaseModel):
    """What POST /api/drawings/plans draws, without its project and source: a horizontal cut plan, or with
    ``section`` a vertical section, composed the same way. A sheet view takes these fields as they are."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    file_name: str | None = Field(alias="fileName", default=None, max_length=240, description=(
        "Optional human-readable name for a new cut plan. A missing extension is completed as .png; only .png is accepted. "
        "Existing drawings keep their name: omitted or blank inherits it, and a different name is refused. "
        "The name is display metadata and never supplies drawingId or a storage path."))
    previous_revision_ref: str | None = Field(alias="previousRevisionRef", default=None)
    cut_height: float | None = Field(alias="cutHeight", default=None, allow_inf_nan=False,
                                    description="Horizontal cut elevation in the source model length unit.")
    bottom: float | None = Field(default=None, allow_inf_nan=False)
    section: SectionLineDto | SectionPlaneDto | None = Field(default=None, description=(
        "A vertical section instead of the horizontal cut: the plane through a plan line ({line, keep}) or through a "
        "point with a horizontal normal ({origin, normal}, pointing from the kept side to the removed side, where the "
        "viewer stands). It must be perpendicular to CAD X or Y; any other plane is refused "
        "(SECTION_PLANE_NOT_MODEL_AXIS), and one that cuts no drawn object too (SECTION_PLANE_MISSES_MODEL). The "
        "drawing looks from the removed side into the kept side with CAD +Z up: u runs along the sheet's right (X when "
        "looking +Y, -X looking -Y, -Y looking +X, Y looking -X) and v is Z, both in model coordinates. Omitted, a "
        "rebuild keeps its own plane and a new drawing is a horizontal cut plan. A drawing never changes between the "
        "two (DRAWING_ORIENTATION_CHANGED)."))
    depth: float | None = Field(default=None, gt=0, allow_inf_nan=False, description=(
        "A vertical section only: how far beyond its plane the kept side is drawn, in the source length unit. Omitted, a "
        "rebuild keeps its own and a new section draws to the far side of the model's bounds."))
    length_unit: LengthUnit | None = Field(alias="lengthUnit", default=None, description=LENGTH_UNIT)
    scale_denominator: int | None = Field(alias="scaleDenominator", default=None, ge=1, le=10000, strict=True)
    crop_uv: tuple[float, float, float, float] | None = Field(alias="cropUv", default=None, description=(
        "The drawn window (u_min, v_min, u_max, v_max) in the source length unit: X/Y for a plan, the section's u/v for "
        "a vertical section. Omitted, a rebuild keeps its own; a new drawing frames the model."))
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
        if self.section is not None and (self.cut_height is not None or self.bottom is not None):
            raise ValueError("Give section for a vertical section, or cutHeight and bottom for a horizontal plan, not both")
        return self


class PlanRequestDto(PlanDrawingDto, DrawingSourceRequestDto):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)


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


CorrectionClass = Literal["compiler_defect", "semantic_rule", "recipe", "local_override"]


class DrawingCorrectionPairDto(BaseModel):
    """Two adjacent revisions of one cut plan: why the page was replaced, who asked, and what changed."""
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    drawing_id: str = Field(alias="drawingId")
    before_revision_ref: str = Field(alias="beforeRevisionRef")
    after_revision_ref: str = Field(alias="afterRevisionRef",
                                    description="The revision whose receipt names beforeRevisionRef as the one it continued.")
    cause: Literal["source", "representation"] | None = Field(description=(
        "representation: the same exact source (model, Stage, imported asset) drawn another way; source: the "
        "drawing followed another source, which corrects nothing."))
    origin: str | None = Field(description="The after revision's attribution.origin; null when it was not recorded.")
    source_kind: Literal["human", "agent"] | None = Field(alias="sourceKind", description=(
        "The after revision's sourceKind: whether a person or an agent asked for it; null when its request did not say."))
    reason: str | None = Field(description="The after revision's reason; null when none was given.")
    correction_class: CorrectionClass = Field(alias="class", description=(
        "The first that holds: compiler_defect - the page followed its source, or it only hid objects the cleanup "
        "flagged (V0 cleanup reports name no object, so no hide is one); semantic_rule - a material's hatch or poché "
        "rule changed; recipe - a pen, the hatch spacing, the fade beyond the cut or the number of entourage objects "
        "changed; local_override - anything else: an object hidden or shown, entourage moved, flipped, scaled or "
        "swapped, the crop, scale or cut, a dimension. Derived on every read, never stored."))
    diff: dict[str, tuple[Any, Any]] = Field(description=(
        "What changed, {dotted viewRecipe path: [old, new]}, null where absent. hiddenObjectIds.<id> is [was hidden, "
        "is hidden]; dimensions.<id> and dressing.<id> are the whole object where it was added or removed, else one "
        "entry per changed field; graphics.hatch.byMaterial.<material> is that material's whole rule."))


class DrawingCorrectionEvidenceDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    drawing_id: str = Field(alias="drawingId")
    before_revision_ref: str = Field(alias="beforeRevisionRef")
    after_revision_ref: str = Field(alias="afterRevisionRef")


class DrawingCorrectionPageDto(BaseModel):
    """One exact registered drawing page, as a document decision source names it."""
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    run_id: str = Field(alias="runId")
    asset_sha256: str = Field(alias="assetSha256")
    revision_ref: str = Field(alias="revisionRef")
    page_index: int = Field(alias="pageIndex", ge=0)


class RecipeSuggestionDto(BaseModel):
    """A recipe correction repeated the same way on several drawings, offered and never retained."""
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    suggestion_id: str = Field(alias="suggestionId", description=(
        "Derived from field, direction, value and drawingIds, so the same offer keeps its id across reads."))
    field: Literal["cutLineMm", "visibleLineMm", "hatchSpacingMm"] = Field(
        description="A paper value a project recipe can hold.")
    direction: Literal["increase", "decrease"]
    value: float = Field(description="The value, in paper mm, of the group's most recent after revision.")
    drawing_ids: list[str] = Field(alias="drawingIds", description="The distinct drawings that repeat it, at least two.")
    evidence: list[DrawingCorrectionEvidenceDto] = Field(description="Every revision pair that counts, by drawing.")
    page: DrawingCorrectionPageDto = Field(description=(
        "The most recent after revision's page: the exact document source a recipe decision saving this cites."))


class DrawingCorrectionsDto(BaseModel):
    """A read of the retained revisions: one drawing's pairs and the project's suggestions. Nothing is written."""
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    drawing_id: str | None = Field(alias="drawingId", default=None)
    pairs: list[DrawingCorrectionPairDto] = Field(description=(
        "Each revision of drawingId paired with the revision it continued, in the order drawn; empty without drawingId."))
    suggestions: list[RecipeSuggestionDto] = Field(description=(
        "Project-wide: a pen or the hatch spacing changed the same way, in representation-only revisions no agent "
        "asked for, on at least two drawings to which no active recipe already gives that key."))

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


class ElevationDrawingDto(BaseModel):
    """What POST /api/drawings/elevations draws, without its project and source. A sheet view takes these fields."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    view: Literal["front", "back", "left", "right", "top", "axon"] = Field(
        default="front", description=(
            "Whole-model orthographic direction. top looks down CAD -Z with X right and Y up on the sheet; "
            "it is a top projection of visible geometry, not a cut floor plan. axon is a parallel view of the whole "
            "model from direction, CAD +Z up on the sheet: foreshortened, so its scale is a display size, not a "
            "measurable one."
        ),
    )
    direction: tuple[Finite, Finite, Finite] | None = Field(default=None, description=(
        "axon only: the direction from the model toward the viewer in CAD X/Y/Z, any length; [1, -1, 1] is the isometric "
        "from +X, -Y, +Z. It may not be vertical. Omitted is [-1, -1, 1], the model view's axon."))
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    hidden_lines: bool = Field(alias="hiddenLines", default=False)
    scale_denominator: int = Field(alias="scaleDenominator", default=100, ge=1, le=10000)
    length_unit: LengthUnit | None = Field(alias="lengthUnit", default=None, description=LENGTH_UNIT)

    @model_validator(mode="after")
    def direction_of_an_axonometric(self):
        if self.direction is not None and self.view != "axon":
            raise ValueError("direction belongs to the axon view")
        return self


class ElevationRequestDto(ElevationDrawingDto, DrawingSourceRequestDto):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)


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


class SectionPerspectiveDrawingDto(BaseModel):
    """What POST /api/drawings/section-perspectives draws, without its project and source. A sheet view takes these."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
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
    hatch: PlanHatchDto | None = Field(default=None, description=(
        "Material hatch and poché rules for the cut on paper, as a cut plan takes them; each is stored complete and "
        "an empty byMaterial draws none."))
    beyond: PlanBeyondDto | None = Field(default=None, description=(
        "Fading of what lies beyond the cut, as in a cut plan; fade 0 draws it black."))
    length_unit: LengthUnit | None = Field(alias="lengthUnit", default=None, description=LENGTH_UNIT)


class SectionPerspectiveRequestDto(SectionPerspectiveDrawingDto, DrawingSourceRequestDto):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)


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


SheetViewId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]


class SheetViewDto(BaseModel):
    """One drawing on a view sheet: exactly one of plan, elevation or sectionPerspective, and where it goes.

    The drawing is the one its own route draws from the same fields and the sheet's one source, retained and
    registered in the documents list as that route registers it: an identical request reads the registered revision
    back. It is placed at its own scale and paper size, never rescaled or cropped to fit.
    """
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    id: SheetViewId = Field(description=(
        "The view's drawing identity (its drawingId): the same id continues that drawing's revisions, here or on its "
        "own route."))
    place_mm: tuple[Finite, Finite] = Field(alias="placeMm", description=(
        "Where the drawing's top-left corner goes: paper mm from the sheet's top-left. Its title and scale sit just "
        "above it; a drawing that leaves the frame or overlaps another or the title strip is refused "
        "(DRAWING_SHEET_LAYOUT_INVALID), never moved."))
    title: str | None = Field(default=None, min_length=1, max_length=80, description=(
        "The view's title. Omitted names its kind: PLAN, SECTION A-A, FRONT ELEVATION, ISOMETRIC, ..."))
    subtitle: str | None = Field(default=None, max_length=160, description=(
        "The line under the title. Omitted states the cut or direction from the drawing's own frame, such as "
        "'Vertical cut at Y -3.048 m, looking +Y'; an empty string draws none."))
    mark_on: SheetViewId | None = Field(alias="markOn", default=None, description=(
        "A vertical section or a section perspective with a vertical plane: the id of a horizontal plan on this sheet "
        "on which its cut line is drawn, with arrows toward the kept side, labelled markLabel."))
    mark_label: str | None = Field(alias="markLabel", default=None, pattern=r"^[A-Z0-9]{1,3}$", description=(
        "The cut's name, such as A: it labels the cut line on the plan and titles the view SECTION A-A."))
    plan: PlanDrawingDto | None = Field(default=None, description=(
        "A horizontal cut plan, or with section a vertical section, as POST /api/drawings/plans draws it. Dimensions, "
        "dressingOperations, reason and sourceKind belong to that route."))
    elevation: ElevationDrawingDto | None = Field(default=None, description=(
        "An elevation, top projection or axonometric (view axon), as POST /api/drawings/elevations draws it."))
    section_perspective: SectionPerspectiveDrawingDto | None = Field(alias="sectionPerspective", default=None, description=(
        "A section perspective, as POST /api/drawings/section-perspectives draws it."))

    @model_validator(mode="after")
    def one_drawing(self):
        drawings = [name for name in ("plan", "elevation", "section_perspective") if getattr(self, name) is not None]
        if len(drawings) != 1:
            raise ValueError("A sheet view draws exactly one of plan, elevation or sectionPerspective")
        drawing = getattr(self, drawings[0])
        if "scale_denominator" not in drawing.model_fields_set:
            raise ValueError("A sheet view states its own scaleDenominator")
        if drawing.drawing_id not in (None, self.id):
            raise ValueError("A sheet view's drawing identity is its id")
        if self.plan is not None and (self.plan.dimensions or self.plan.dressing_operations is not None
                                      or self.plan.reason is not None or self.plan.source_kind is not None):
            raise ValueError("Dimensions, dressingOperations, reason and sourceKind belong to POST /api/drawings/plans")
        if (self.mark_on is None) != (self.mark_label is None):
            raise ValueError("markOn and markLabel are given together")
        if self.mark_on is not None and self.elevation is not None:
            raise ValueError("Only a section is marked on a plan")
        return self


class SheetRequestDto(DrawingSourceRequestDto):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    style_id: DrawingStyleId = Field(alias="styleId", description=(
        "The sheet style. Without views it lays out front, right and top at one scale in the style's own paper; with "
        "views it gives the frame margin and type sizes."))
    scale_denominator: int | None = Field(alias="scaleDenominator", default=None, strict=True, ge=1, le=10000,
                                          description=(
        "Without views: the one exact scale of front, right and top (default 20); oversized layouts are refused, never "
        "silently rescaled. With views each view states its own, and this is refused."))
    hidden_object_ids: list[str] = Field(alias="hiddenObjectIds", default_factory=list,
                                        description="Without views: exact physical object ids excluded before all three visibility solves.")
    outline_object_ids: list[str] = Field(alias="outlineObjectIds", default_factory=list,
                                         description="Without views: visible physical object ids to simplify to outlines in this sheet.")
    notes: list[str] = Field(default_factory=list)
    views: list[SheetViewDto] | None = Field(default=None, min_length=1, max_length=12, description=(
        "Same-source drawings placed on one sheet, each at its own scale: plans, vertical sections, elevations, "
        "axonometrics and section perspectives, each drawn or read back through its own route's owner from the sheet's "
        "one source. Omitted is the front, right and top review sheet."))
    paper_size_mm: tuple[Finite, Finite] | None = Field(alias="paperSizeMm", default=None, description=(
        "With views: the paper, width and height in mm (A3 landscape is [420, 297]). Omitted is the style's paper."))
    title: str | None = Field(default=None, min_length=1, max_length=120, description=(
        "With views: the title strip's title. Omitted is the project id."))
    subtitle: str | None = Field(default=None, max_length=160, description=(
        "With views: the line under the title. Omitted lists the views' titles; an empty string draws none."))
    sheet_number: str | None = Field(alias="sheetNumber", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$",
                                     description="With views: the sheet's number in its title strip and file (default 01).")
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$",
                                   description=(
        "With views: the sheet's drawing identity (default sheet-<sheetNumber>). Without views it is the style id."))
    length_unit: LengthUnit | None = Field(alias="lengthUnit", default=None, description=LENGTH_UNIT)

    @model_validator(mode="after")
    def views_or_review(self):
        if self.views is None:
            given = [name for name, value in (("paperSizeMm", self.paper_size_mm), ("title", self.title),
                                              ("subtitle", self.subtitle), ("sheetNumber", self.sheet_number),
                                              ("drawingId", self.drawing_id)) if value is not None]
            if given:
                raise ValueError(f"{', '.join(given)} place views; a front/right/top review sheet takes none")
            return self
        if self.scale_denominator is not None:
            raise ValueError("Each view states its own scaleDenominator; the sheet takes none")
        if self.hidden_object_ids or self.outline_object_ids:
            raise ValueError("Each view states its own hidden objects; the sheet takes none")
        ids = [view.id for view in self.views]
        if len(set(ids)) != len(ids):
            raise ValueError("Each view on a sheet has its own id")
        for view in self.views:
            if view.mark_on is not None and (view.mark_on == view.id or view.mark_on not in ids):
                raise ValueError(f"{view.id} is marked on {view.mark_on}, which is no other view of this sheet")
        return self


DrawingFileFormat = Literal["pdf", "dxf", "svg", "png"]
