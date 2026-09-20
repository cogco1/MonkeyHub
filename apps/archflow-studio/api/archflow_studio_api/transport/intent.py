"""The wire form of ``POST /api/intents``: an architect's sentence in, the
agent's compiled sentence and the typed proposal it became out.

The proposal half is the same ``ProposalDto`` that ``POST /api/proposals``
answers with — it *is* a proposal made by the deterministic seam. The agent
half is kept apart from it on purpose: ``agent`` says who read the request,
what it compiled and why, so nothing the model said can be mistaken for
something the record answered.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from ..application.clarification import (
    AuthoredControlDraft,
    CandidateOption,
    PendingIntent,
    ScopeOption,
)
from ..application.gestures import (
    DocumentAnnotationPage, DocumentAnnotationRef, DocumentGesture, DocumentTracingCalibration, Gesture, GestureHit,
)
from ..application.intent_agent import Compilation
from .capability import CapabilitySourceDto, CapabilityTargetDto, KeepScopeDto, detail_dto
from .proposal import STATE_DIGEST_PATTERN, ProposalDto
from .artifacts import ModelSourceDto, model_source_dto

Vector3 = tuple[float, float, float]


class GestureHitDto(BaseModel):
    """One object a stroke sample fell on, exactly as the viewer read it.

    The client derives nothing: the user strings and the object name are the
    file's, the world point is where the ray met the mesh. The server resolves
    them through the same pick resolver a click goes through.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    object_name: str | None = Field(alias="objectName", default=None)
    user_strings: dict[str, str] = Field(alias="userStrings", default_factory=dict)
    world: Vector3


class CameraDto(BaseModel):
    """Where the architect stood when they drew: the viewpoint is part of the intent."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    position: Vector3
    target: Vector3
    up: Vector3
    fov: float
    # Missing fields stay absent for historical annotation records; no migration
    # invents a projection/zoom for ink that never recorded them.
    projection: Literal["perspective", "orthographic"] | None = Field(default=None, exclude_if=lambda value: value is None)
    zoom: float | None = Field(default=None, gt=0, allow_inf_nan=False, exclude_if=lambda value: value is None)


class GestureDto(BaseModel):
    """One non-destructive annotation on the model.

    ``screen`` is the stroke in canvas pixels, ``camera`` the view it was
    drawn in, ``hits`` the objects under its samples. For an arrow the world
    start/end/direction and its length in model units are the client's
    geometry of the stroke on the model; the server names what it points at.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    kind: Literal["circle", "arrow", "keep", "remove", "freehand", "line", "ruler", "arc"]
    screen: list[tuple[float, float]] = Field(min_length=1)
    camera: CameraDto
    hits: list[GestureHitDto] = Field(default_factory=list)
    world_start: Vector3 | None = Field(alias="worldStart", default=None)
    world_end: Vector3 | None = Field(alias="worldEnd", default=None)
    world_direction: Vector3 | None = Field(alias="worldDirection", default=None)
    length_model_units: float | None = Field(alias="lengthModelUnits", default=None)
    label: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    line_width: int | None = Field(alias="lineWidth", default=None, ge=1, le=8)
    screen_size: tuple[int, int] | None = Field(alias="screenSize", default=None)


def gesture_from(dto: GestureDto) -> Gesture:
    return Gesture(
        kind=dto.kind,
        hits=tuple(
            GestureHit(
                object_name=hit.object_name,
                user_strings=dict(hit.user_strings),
                world=hit.world,
            )
            for hit in dto.hits
        ),
        world_direction=dto.world_direction,
        length_model_units=dto.length_model_units,
        label=dto.label,
        screen=tuple(dto.screen),
        color=dto.color,
        line_width=dto.line_width,
        camera=dto.camera.model_dump(),
        screen_size=dto.screen_size,
    )


class ModelGestureDto(GestureDto):
    """The existing 3D gesture, with a stable id for erasing and restoring saved ink."""

    id: str = Field(min_length=1, max_length=128)


class ModelAnnotationsRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    model_source: ModelSourceDto = Field(alias="modelSource")
    base_revision_sha256: str | None = Field(alias="baseRevisionSha256", pattern=STATE_DIGEST_PATTERN)
    annotations: list[ModelGestureDto] = Field(max_length=2000)
    comment: str = Field(default="", max_length=8000)


class ModelAnnotationsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    model_source: ModelSourceDto = Field(alias="modelSource")
    revision_sha256: str | None = Field(alias="revisionSha256")
    annotations: list[ModelGestureDto]
    comment: str


def model_annotations_dto(snapshot) -> ModelAnnotationsDto:
    return ModelAnnotationsDto(project_id=snapshot.project_id, model_source=model_source_dto(snapshot.model_source),
                               revision_sha256=snapshot.revision_sha256,
                               annotations=[ModelGestureDto(**row) for row in snapshot.annotations], comment=snapshot.comment)


PageCoordinate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class DocumentGestureDto(BaseModel):
    """Page-local ink; this DTO cannot carry model hits, world coordinates or a camera."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    kind: Literal["circle", "arrow", "keep", "remove", "freehand", "line", "ruler", "arc", "text", "polyline"]
    points: list[tuple[PageCoordinate, PageCoordinate]] = Field(
        min_length=1, max_length=20000,
        description="Coordinates in [0,1], origin at the visible page's top left, x right/y down. PDF uses CropBox after rotation; images use EXIF orientation. Zoom and DPI do not change them. Text has exactly one point anchoring the text block's top-left corner.",
    )
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    line_width: float = Field(
        alias="lineWidth", gt=0, le=1, allow_inf_nan=False,
        description="Stroke width as a fraction of the visible page's shorter side. Render at lineWidth * min(displayedPageWidth, displayedPageHeight) CSS px, independently of zoom and DPI. Text does not render this width; its independent fontSize sets the font.",
    )
    label: str | None = Field(
        default=None, max_length=2000,
        description="For text, the non-empty plain-text content, with explicit newlines preserved (at most 2000 characters). Other tools keep their optional label limit of 120 characters.",
    )
    font_size: float | None = Field(
        alias="fontSize", default=None, gt=0, le=1, allow_inf_nan=False,
        exclude_if=lambda value: value is None,
        description="Required only for text: font size as a fraction of the visible page's shorter side. Render at fontSize * min(displayedPageWidth, displayedPageHeight) CSS px with 1.25em line height. Absent on existing strokes; never derived from lineWidth.",
    )
    closed: bool | None = Field(default=None, exclude_if=lambda value: value is None,
        description="Required only for polyline: whether the ordered editable vertices close into a contour. The first point is not repeated.")

    @model_validator(mode="after")
    def valid_document_gesture(self) -> DocumentGestureDto:
        document_gesture_from(self)
        return self


class DocumentAnnotationRefDto(BaseModel):
    """One exact saved page revision to accompany a written design request."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=STATE_DIGEST_PATTERN)
    page_index: int = Field(alias="pageIndex", ge=0)
    revision_sha256: str = Field(alias="revisionSha256", pattern=STATE_DIGEST_PATTERN)
    drawing_revision_ref: str | None = Field(alias="drawingRevisionRef", default=None,
        description="The exact generated drawing receipt URI; omitted for legacy source documents.")

    @model_serializer(mode="wrap")
    def serialize_ref(self, handler):
        value = handler(self)
        if self.drawing_revision_ref is None:
            value.pop("drawingRevisionRef", None)
            value.pop("drawing_revision_ref", None)
        return value


class DocumentTracingCalibrationDto(BaseModel):
    """Explicit page origin/+X direction and the known distance in project length units."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    origin: tuple[PageCoordinate, PageCoordinate]
    axis_point: tuple[PageCoordinate, PageCoordinate] = Field(alias="axisPoint")
    distance: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def distinct_points(self) -> DocumentTracingCalibrationDto:
        self.to_domain()
        return self

    def to_domain(self) -> DocumentTracingCalibration:
        return DocumentTracingCalibration(self.origin, self.axis_point, self.distance)


class DocumentAnnotationsRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=STATE_DIGEST_PATTERN)
    page_index: int = Field(alias="pageIndex", ge=0)
    drawing_revision_ref: str | None = Field(alias="drawingRevisionRef", default=None)
    base_revision_sha256: str | None = Field(
        alias="baseRevisionSha256", pattern=STATE_DIGEST_PATTERN,
        description="The last revision read for this file/page; null only for an unsaved page. A stale revision is refused with 409.",
    )
    annotations: list[DocumentGestureDto] = Field(max_length=2000, description="Complete remaining ink on this page. Erasing a stroke removes its id from this list; prior saved revisions remain readable.")
    comment: str = Field(default="", max_length=8000)
    tracing_calibration: DocumentTracingCalibrationDto | None = Field(alias="tracingCalibration", default=None,
        description="Explicitly calibrated origin, direction and distance for this saved page. Omit/null to save without a model scale.")


class DocumentAnnotationsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    asset_sha256: str = Field(alias="assetSha256")
    page_index: int = Field(alias="pageIndex")
    revision_sha256: str | None = Field(alias="revisionSha256")
    annotations: list[DocumentGestureDto]
    comment: str
    drawing_revision_ref: str | None = Field(alias="drawingRevisionRef", default=None)
    tracing_calibration: DocumentTracingCalibrationDto | None = Field(alias="tracingCalibration", default=None,
        exclude_if=lambda value: value is None)


class DocumentVisualInputDto(BaseModel):
    """Transient visible-page PNGs, rendered by the client from a registered source."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    role: Literal["edit", "reference"]
    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=STATE_DIGEST_PATTERN)
    page_index: int = Field(alias="pageIndex", ge=0)
    revision_sha256: str | None = Field(alias="revisionSha256", default=None, pattern=STATE_DIGEST_PATTERN)
    drawing_revision_ref: str | None = Field(alias="drawingRevisionRef", default=None)
    page_png_base64: str = Field(alias="pagePngBase64", max_length=5592408,
        description="Pure base64 PNG, at most 4 MiB decoded and 2048 px on its longer side; same visible-page aspect ratio as the registered PDF/image.")
    annotated_png_base64: str | None = Field(alias="annotatedPngBase64", default=None, max_length=5592408,
        description="Same-size page with the exact saved revision's complete ink; required when that selected revision has annotations, otherwise null.")
    reference_note: str | None = Field(alias="referenceNote", default=None, max_length=2000)


class DocumentCommentDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    comment_ref: str = Field(alias="commentRef", description="Retained P036 record URI, for citation; never a server filesystem path.")
    project_id: str = Field(alias="projectId")
    source_run_id: str | None = Field(alias="sourceRunId")
    state_digest: str = Field(alias="stateDigest")
    utterance: str
    document_annotations: list[DocumentAnnotationRefDto] = Field(alias="documentAnnotations")
    submitted_at: str = Field(alias="submittedAt")


class DocumentCommentsDto(BaseModel):
    comments: list[DocumentCommentDto]


def document_gesture_from(dto: DocumentGestureDto) -> DocumentGesture:
    return DocumentGesture(dto.id, dto.kind, tuple(dto.points), dto.color, dto.line_width, dto.label, dto.font_size, dto.closed)


def document_annotation_ref_from(dto: DocumentAnnotationRefDto) -> DocumentAnnotationRef:
    return DocumentAnnotationRef(dto.run_id, dto.asset_sha256, dto.page_index, dto.revision_sha256, dto.drawing_revision_ref)


def document_annotations_dto(page: DocumentAnnotationPage) -> DocumentAnnotationsDto:
    return DocumentAnnotationsDto(
        project_id=page.project_id, run_id=page.run_id, asset_sha256=page.asset_sha256,
        page_index=page.page_index, revision_sha256=page.revision_sha256,
        annotations=[DocumentGestureDto(**annotation.to_dict()) for annotation in page.annotations],
        comment=page.comment, drawing_revision_ref=page.drawing_revision_ref,
        tracing_calibration=DocumentTracingCalibrationDto(**page.tracing_calibration.to_dict()) if page.tracing_calibration is not None else None,
    )


class IntentRequestDto(BaseModel):
    """One request in the architect's words, against the current selection."""

    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)

    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; a request "
        "against any other state is refused as STALE_BASE",
    )
    source_run_id: str | None = Field(
        alias="sourceRunId",
        default=None,
        min_length=1,
        description="the retained run selected as the editing base; omitted "
        "uses the project's default state projection",
    )
    utterance: str = Field(
        min_length=1,
        description="what the architect said, in any words; the agent compiles "
        "it into the grammar or asks",
    )
    target_component_id: str | None = Field(
        alias="targetComponentId",
        default=None,
        min_length=1,
        description="the current selection's component, when there is one; the "
        "agent may keep it or name another the record declares",
    )
    element_id: str | None = Field(
        alias="elementId",
        default=None,
        min_length=1,
        description="the current selection's element, when one was picked",
    )
    project_id: str | None = Field(
        alias="projectId",
        default=None,
        min_length=1,
        description="the project the client believes it is proposing against; "
        "a different one is refused as PROJECT_MISMATCH",
    )
    gestures: list[GestureDto] = Field(
        default_factory=list,
        description="what the architect drew on the model with the words: "
        "circles, arrows, keep and remove marks, with the objects under them; "
        "the server resolves them and reads them beside the sentence",
    )
    document_annotations: list[DocumentAnnotationRefDto] = Field(
        alias="documentAnnotations", default_factory=list, max_length=100,
        description="Exact saved document page revisions submitted with the words. The server verifies and retains their source context without inferring a model hit or camera.",
    )
    document_visuals: list[DocumentVisualInputDto] = Field(
        alias="documentVisuals", default_factory=list, max_length=4,
        description="One edit page matching the sole documentAnnotations reference, plus at most three explicitly selected reference pages. At most 16 MiB total decoded PNGs. Reference pages never change the editing base or import old comments. Omit on clarification to reuse the exact submitted images.",
    )
    camera: CameraDto | None = Field(
        default=None,
        description="where the viewer stands: reads 'left' and 'right' against the project's compass (PROJECT.md)",
    )
    scope: Literal["element", "stack", "datum"] | None = Field(
        default=None,
        description="how far the change reaches, when the client settles it in "
        "a field rather than in words: this element, the stack that seats on "
        "it, or everything on its datum. The architect may say it instead "
        "(整个叠层 / the whole stack / 整条标高 / 只这个); either way the "
        "answer is the same slot",
    )
    continuation_token: str | None = Field(
        alias="continuationToken",
        default=None,
        min_length=1,
        description="the token the server's last clarification answered with, "
        "when this request continues that exchange. It is the whole of the "
        "continuity: the pending intent it names carries the original "
        "utterance, the target resolved so far and what has been rejected, so "
        "no transcript is sent and none is read",
    )


class ContextPackRequestDto(BaseModel):
    """One turn's words, plus the exact source and focus they were said about.

    An absent focus asks about the whole design. Named objects and sources are
    checked exactly; no missing selection is replaced by a recent candidate.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    utterance: str = Field(
        min_length=1,
        description="the complete message the architect sent, unedited; it is "
        "read by the same context compiler an intent uses, so a request that "
        "names several objects keeps the wider context that reading needs",
    )
    project_id: str = Field(
        alias="projectId", min_length=1,
        description="the project the caller believes it is reading; a "
        "different one is refused as PROJECT_MISMATCH",
    )
    source_run_id: str | None = Field(
        alias="sourceRunId", default=None, min_length=1,
        description="the exact retained run; omit only for authored initial state or an explicit sourceStageRef",
    )
    state_digest: str = Field(
        alias="stateDigest", pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest that run projects to; any other is "
        "refused as STALE_BASE",
    )
    target_component_id: str | None = Field(
        alias="targetComponentId", default=None, min_length=1,
        description="optional exact Component@1 focus; when elements are named they must belong to it",
    )
    element_id: str | None = Field(
        alias="elementId", default=None, min_length=1,
        description="the Element@1 in focus; it must declare "
        "targetComponentId as its own component",
    )
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)
    element_ids: list[Annotated[str, Field(min_length=1)]] = Field(alias="elementIds", default_factory=list, max_length=64)
    context_refs: list[Annotated[str, Field(min_length=1)]] = Field(alias="contextRefs", default_factory=list, max_length=16)
    context_offset: int = Field(alias="contextOffset", default=0, ge=0, strict=True,
                              description="offset into the bounded reference index; it changes no focus or edit scope")

    @model_validator(mode="after")
    def coherent_focus(self) -> ContextPackRequestDto:
        if len(set(self.element_ids)) != len(self.element_ids):
            raise ValueError("elementIds must not repeat an element")
        if self.element_id is not None and self.element_ids and self.element_ids != [self.element_id]:
            raise ValueError("elementId and elementIds must name the same single focus, or use elementIds alone")
        if len(set(self.context_refs)) != len(self.context_refs):
            raise ValueError("contextRefs must not repeat a reference")
        return self


class StageContextChangesDto(BaseModel):
    """Content differences and declared review scope, never a validation receipt."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    changed_refs: list[str] = Field(alias="changedRefs")
    affected_refs: list[str] = Field(alias="affectedRefs")
    needs_review_refs: list[str] = Field(alias="needsReviewRefs")
    unresolved_impact_refs: list[str] = Field(alias="unresolvedImpactRefs")


class ConfirmedStageContextDto(BaseModel):
    """A read-only view of committed Stage identity and its retained conditions."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    stage_ref: str = Field(alias="stageRef")
    label: str
    branch_id: str = Field(alias="branchId")
    run_id: str = Field(alias="runId")
    state_digest: str = Field(alias="stateDigest")
    is_source: bool = Field(alias="isSource", description="True only when the selected run, retained record and state digest are the accepted Stage itself. Inheriting a Stage base does not accept a candidate.")
    locked_parameter_keys: list[str] = Field(alias="lockedParameterKeys", description="Parameters locked in the accepted Stage. This is not a whole-geometry lock; current values and lock state remain in context.")
    retained_condition_refs: list[str] = Field(alias="retainedConditionRefs", description="Reading and obligation references retained in the accepted Stage; their current details and coverage remain in context.")
    changes: StageContextChangesDto
    omitted_counts: dict[str, int] = Field(alias="omittedCounts", description="Omitted entries by summary list name; each list is bounded to 64 entries.")
    limitations: list[str]


class ContextPackDto(BaseModel):
    """What a caller would otherwise discover by reading before it can act.

    It is the existing capability description, the existing compiled read
    context and the existing preflight, composed for one named source and
    focus. It proposes nothing: ``request`` is the template the capability
    already reports, holding the values the record holds now.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    context_pack: Literal["ContextPack@1"] = Field(alias="contextPack", default="ContextPack@1")
    source: CapabilitySourceDto
    target: CapabilityTargetDto | None = Field(
        default=None,
        description="the focus element's numbers, as the catalog decided them; "
        "null when the capability reads no target for it",
    )
    keep: KeepScopeDto | None = None
    request: dict[str, Any] | None = Field(
        default=None,
        description="the capability's own next request with this project's "
        "base and the focus element's current values already in it; a "
        "template to edit, never an approved change. Null when the target has "
        "no number this capability can move, or when the preflight below "
        "already answers the request without one",
    )
    context_tier: str = Field(
        alias="contextTier",
        description="which read context the existing compiler chose for these "
        "words: scalar, component or design",
    )
    escalation: list[str] = Field(
        default_factory=list,
        description="why the compiler widened the context, in its own reasons; "
        "empty when a narrow numeric reading was reached",
    )
    context: dict[str, Any] = Field(
        description="the facts that reading makes available, as the existing "
        "model projection of it; it grants no edit and changes no reference",
    )
    preflight: dict[str, Any] | None = Field(
        default=None,
        description="the existing known obstacle to a numeric action — a lock, "
        "a derived value, a shared control, a top reference — as the record "
        "already answers it, needing no model call. Null when there is none "
        "and for the design tier, which this preflight does not judge",
    )
    confirmed_stage: ConfirmedStageContextDto | None = Field(
        alias="confirmedStage", default=None,
        description="Derived from verified committed design history, with differences from this exact source. Null when no committed Stage is bound; never inferred from an unaccepted candidate.",
    )
    honesty: list[str] = Field(default_factory=list)


class AgentReadingDto(BaseModel):
    """What the agent said and how it was obtained — the agent's, not the record's."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    provider: str = Field(description="deterministic, codex or anthropic")
    model: str | None = Field(description="the model the provider ran, when it names one")
    compiled_utterance: str = Field(
        alias="compiledUtterance",
        description="the sentence in the grammar the agent produced; for the "
        "deterministic provider it is the request itself",
    )
    why: str = Field(
        description="the agent's own reading of the request, verbatim; empty "
        "for the deterministic provider",
    )
    latency_ms: int = Field(alias="latencyMs")
    prompt_sha256: str | None = Field(
        alias="promptSha256",
        description="digest of the exact prompt the agent was shown; null for "
        "the deterministic provider",
    )
    receipt_id: str | None = Field(
        alias="receiptId",
        default=None,
        description="the ModelInvocationReceipt that signs the call to the "
        "model; null for the deterministic provider, which calls none",
    )
    status: str | None = Field(
        default=None,
        description="how that call ended on the shared contract (success); "
        "null when no model was called",
    )


class IntentTimingsDto(BaseModel):
    """How long the two halves of an intent took, in this process.

    ``compileMs`` is the agent (zero for the deterministic pass-through);
    ``typeMs`` is the grammar, the record and the closure. The fast stage of
    a change is the second number; the first is what an agent costs.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    compile_ms: int = Field(alias="compileMs")
    type_ms: int = Field(alias="typeMs")


Outcome = Literal[
    "COMPILED", "NEEDS_CLARIFICATION", "MISSING_EDITABLE_CONTROL", "UNSUPPORTED"
]
ActionKind = Literal[
    "change_existing_value", "declare_missing_control", "edit_components", "clarify", "unsupported"
]


class CandidateOptionDto(BaseModel):
    """One thing the architect could have meant, as the record has it now.

    A choice a person can make: what it is, what its number is, and where it
    sits. A list of bare identifiers would be the studio asking somebody else to
    do its resolution.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    ref: str = Field(description="how this candidate is written in rejectedCandidates")
    component_id: str = Field(alias="componentId")
    element_id: str | None = Field(alias="elementId")
    key: str | None = Field(description="the numeric field this option would change")
    current_value: float | int | None = Field(
        alias="currentValue", description="what the record holds for it now"
    )
    unit: str | None
    orientation: str | None = Field(
        description="the compass identity the record's own name carries, or null",
    )
    label: str = Field(description="the option as a person reads it")


class ScopeOptionDto(BaseModel):
    """One reading of how far a change reaches, and exactly what it covers.

    A choice about coverage, not about identity: ``elementIds`` is what the
    reading names, so the question can say "these three" rather than asking the
    architect to imagine which. Nothing here promises a multi-element edit —
    the successor record still moves one scalar.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    scope: Literal["element", "stack", "datum"]
    element_ids: list[str] = Field(
        alias="elementIds", description="the elements this reading covers"
    )
    label: str = Field(description="the reading as a person reads it")


class PendingIntentDto(BaseModel):
    """The one short-term structure a clarification chain is carried in.

    It is server-side state keyed by ``continuationToken`` and bound to one
    ``stateDigest``. A client sends the token back and nothing else: no
    transcript, no re-derived selection. A ``continuationToken`` of ``null``
    means the exchange is over — the answer is terminal and there is nothing
    left to ask.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    request_id: str = Field(alias="requestId")
    state_digest: str = Field(alias="stateDigest")
    original_utterance: str = Field(
        alias="originalUtterance",
        description="what the architect said when the exchange began; every "
        "later reply is read against it",
    )
    action_kind: ActionKind = Field(alias="actionKind")
    target_component_id: str | None = Field(alias="targetComponentId")
    element_id: str | None = Field(alias="elementId")
    requested_semantic_property: str | None = Field(
        alias="requestedSemanticProperty",
        description="the quality the request is about — height, thickness — "
        "read from the words, not from a field name",
    )
    known_slots: dict[str, str] = Field(alias="knownSlots")
    missing_slots: list[str] = Field(
        alias="missingSlots",
        description="what is still open: target, property, value, orientation, scope",
    )
    candidates: list[CandidateOptionDto]
    scope_options: list[ScopeOptionDto] = Field(
        alias="scopeOptions",
        default_factory=list,
        description="the readings of how far a change to the resolved element "
        "reaches, with the ids each covers; empty until one element resolves, "
        "and a single 'element' entry for one that carries nothing and shares "
        "no datum — where there is one reading there is no question",
    )
    rejected_candidates: list[str] = Field(
        alias="rejectedCandidates",
        description="what the architect has refused; authoritative for the "
        "life of this pending intent and never offered again inside it",
    )
    reason_code: str = Field(alias="reasonCode")
    continuation_token: str | None = Field(
        alias="continuationToken",
        description="send this back to continue; null means terminal",
    )
    turn: int = Field(description="which round of this exchange this answer is")


class AuthoredControlDraftDto(BaseModel):
    """A control somebody would have to author, and where it was read from.

    Never written. It is returned so a person can see what the system is
    missing and decide; a later confirm step would turn it into an edit of the
    authored record, and nothing in this round does.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    target_component_id: str = Field(alias="targetComponentId")
    suggested_element_id: str = Field(alias="suggestedElementId")
    semantic_property: str | None = Field(alias="semanticProperty")
    producer: str | None = Field(
        description="the producer the elements around it use, when they agree",
    )
    binding: str | None = Field(
        description="where its base reference would come from",
    )
    unit: str | None = Field(
        description="element params are bare numbers in the record; this seam "
        "converts nothing and states no unit the record does not",
    )
    provenance: list[str] = Field(
        description="exactly which elements and producers this was read from",
    )
    confidence: str = Field(description="how much those readings agreed: high, medium, low")
    dependency_requirements: list[str] = Field(alias="dependencyRequirements")
    suggested_action: str = Field(alias="suggestedAction")
    catalog_status: str | None = Field(
        alias="catalogStatus",
        default=None,
        description="MODEL_VISIBLE_CATALOG_MISSING: the model shows objects of the component that no Element@1 row produced; DECLARED_ONLY: no objects either",
    )
    object_names: list[str] = Field(
        alias="objectNames", default_factory=list, description="the model's objects of the component, as the catalog binds them"
    )


class IntentBlockedDto(BaseModel):
    """The body every refusing outcome of ``POST /api/intents`` answers with.

    The same ``{code, detail}`` every failure has, plus the pending intent it
    belongs to. It is declared here so a client reads the shape from the
    server's own schema rather than hand-writing a mirror of it.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str
    detail: str
    outcome: Outcome
    pending_intent: PendingIntentDto | None = Field(alias="pendingIntent", default=None)
    authored_control_draft: AuthoredControlDraftDto | None = Field(
        alias="authoredControlDraft", default=None
    )
    question: str | None = None
    accepted_forms: list[str] | None = Field(alias="acceptedForms", default=None)


class IntentDto(BaseModel):
    """The wire form of ``POST /api/intents``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    outcome: Outcome = Field(
        description="which of the four closed answers this is; a 201 is always "
        "COMPILED, and the other three arrive as the refusal body",
    )
    agent: AgentReadingDto
    proposal: ProposalDto
    timings: IntentTimingsDto
    document_comment_ref: str | None = Field(alias="documentCommentRef", default=None)
    gestures: list[str] = Field(
        default_factory=list,
        description="the server's own reading of each gesture, in the record's "
        "names, as it was put on the sheet; empty when nothing was drawn",
    )
    pending_intent: PendingIntentDto = Field(
        alias="pendingIntent",
        description="how the request was resolved: the target the proposal was "
        "made against, what was rejected on the way, and a null "
        "continuationToken, because a compiled request has nothing left to ask",
    )


def candidate_dto(option: CandidateOption) -> CandidateOptionDto:
    return CandidateOptionDto(
        ref=option.ref,
        component_id=option.component_id,
        element_id=option.element_id,
        key=option.key,
        current_value=option.current_value,
        unit=option.unit,
        orientation=option.orientation,
        label=option.label,
    )


def scope_option_dto(option: ScopeOption) -> ScopeOptionDto:
    return ScopeOptionDto(
        scope=option.scope,
        element_ids=list(option.element_ids),
        label=option.label,
    )


def pending_dto(pending: PendingIntent) -> PendingIntentDto:
    """One pending intent for the wire; nothing here is recomputed."""

    return PendingIntentDto(
        request_id=pending.request_id,
        state_digest=pending.state_digest,
        original_utterance=pending.original_utterance,
        action_kind=pending.action_kind,
        target_component_id=pending.target_component_id,
        element_id=pending.element_id,
        requested_semantic_property=pending.requested_semantic_property,
        known_slots=dict(pending.known_slots),
        missing_slots=list(pending.missing_slots),
        candidates=[candidate_dto(option) for option in pending.candidates],
        scope_options=[scope_option_dto(option) for option in pending.scope_options],
        rejected_candidates=list(pending.rejected_candidates),
        reason_code=pending.reason_code,
        continuation_token=pending.continuation_token,
        turn=pending.turn,
    )


def draft_dto(draft: AuthoredControlDraft) -> AuthoredControlDraftDto:
    return AuthoredControlDraftDto(
        catalog_status=draft.catalog_status,
        object_names=list(draft.object_names),
        target_component_id=draft.target_component_id,
        suggested_element_id=draft.suggested_element_id,
        semantic_property=draft.semantic_property,
        producer=draft.producer,
        binding=draft.binding,
        unit=draft.unit,
        provenance=list(draft.provenance),
        confidence=draft.confidence,
        dependency_requirements=list(draft.dependency_requirements),
        suggested_action=draft.suggested_action,
    )


def pending_body(pending: PendingIntent) -> dict[str, object]:
    """The pending intent as the error body carries it, camelCase and all."""

    return pending_dto(pending).model_dump(by_alias=True)


def draft_body(draft: AuthoredControlDraft) -> dict[str, object]:
    return draft_dto(draft).model_dump(by_alias=True)


TEMPLATE_NOTE = (
    "request holds the values this element has now. It is the shape of a change, not one that was "
    "asked for or approved: sending it unchanged would set each number to what it already is."
)
BLOCKED_NOTE = (
    "the record already answers this numeric request without a model: see preflight. No executable "
    "request is offered, because running one would go past the obstacle it names."
)


def context_pack_dto(
    description, context, preflight: Mapping[str, Any] | None, model_facts: Mapping[str, Any],
    *, confirmed_stage: Mapping[str, Any] | None = None,
) -> ContextPackDto:
    """One capability description and one compiled read context, as the pack.

    The capability's own halves are taken from the description this API already
    answers ``GET /api/capabilities/{id}`` with, so the two cannot drift. The
    registered entry itself is left out: the caller asked what its project is,
    not what the registry says about the capability.
    """

    detail = detail_dto(description)
    blocked = preflight is not None
    return ContextPackDto(
        source=detail.source,
        target=detail.target,
        keep=detail.keep,
        request=None if blocked else detail.request,
        context_tier=context.tier,
        escalation=list(context.escalation),
        context=dict(model_facts),
        preflight=None if preflight is None else dict(preflight),
        confirmed_stage=None if confirmed_stage is None else ConfirmedStageContextDto(**confirmed_stage),
        honesty=[
            *detail.honesty,
            *([BLOCKED_NOTE] if blocked else []),
            *([TEMPLATE_NOTE] if not blocked and detail.request is not None else []),
        ],
    )


def agent_dto(compilation: Compilation) -> AgentReadingDto:
    # Where there is a receipt it is the authority on how long the call took;
    # ``promptSha256`` stays the digest of the exact bytes that receipt
    # counted as its input.
    receipt = compilation.receipt
    return AgentReadingDto(
        provider=compilation.provider,
        model=compilation.model,
        compiled_utterance=(
            str(compilation.semantic_edit.get("summary", ""))
            if compilation.semantic_edit is not None else compilation.utterance or ""
        ),
        why=compilation.why,
        latency_ms=(
            compilation.latency_ms if receipt is None else receipt.duration_ms
        ),
        prompt_sha256=compilation.prompt_sha256,
        receipt_id=None if receipt is None else receipt.receipt_id,
        status=None if receipt is None else receipt.status.value,
    )
