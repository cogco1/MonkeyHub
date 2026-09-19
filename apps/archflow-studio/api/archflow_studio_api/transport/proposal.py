"""The wire form of one proposal: a selection and a sentence in, a typed
operator out.

The selection travels in the request and never in the utterance. That is the
whole reason this DTO has ``targetComponentId`` and ``elementId`` at all: what
the user clicked is a fact the client already knows, and parsing it back out of
prose would be the one place a wrong thing could get changed confidently.

``decisionOperator`` goes out as an opaque ``dict``. It is the kernel's own
``DecisionOperator@2`` payload and this transport does not mirror it field by
field: the client displays it, and anything that needs to read it parses it
with the kernel.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..application.episodes import DeliberationEpisode, EpisodeProposal, WorkingCopy, WorkingCopyOption
from ..application.proposals import PERSISTENCE, Proposal
from .impact import ImpactDto
from .impact import to_dto as impact_dto
from .artifacts import ModelSourceDto, model_source_dto, model_source_from

# 64 lowercase hex, the form the kernel writes. A wrongly shaped digest is a
# malformed request, not a stale base, and the two must not arrive alike.
STATE_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class WorkingCopyOptionDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=240)
    model_source: ModelSourceDto = Field(alias="modelSource")


class WorkingCopyCreateRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    group_id: str = Field(alias="groupId", min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    label: str = Field(min_length=1, max_length=240)
    stage_id: str = Field(alias="stageId", min_length=1, max_length=128)
    common_base: ModelSourceDto = Field(alias="commonBase", description="The explicitly chosen comparison base; does not rewrite retained kernel lineage.")
    scope: list[str] = Field(min_length=1, max_length=2000)
    options: list[WorkingCopyOptionDto] = Field(min_length=2, max_length=32)


class WorkingCopySelectionRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    base_revision_sha256: str = Field(alias="baseRevisionSha256", pattern=STATE_DIGEST_PATTERN)
    option_id: str = Field(alias="optionId", min_length=1)


class WorkingCopyOptionRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    base_revision_sha256: str = Field(alias="baseRevisionSha256", pattern=STATE_DIGEST_PATTERN)
    option: WorkingCopyOptionDto


class WorkingCopyDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    group_id: str = Field(alias="groupId")
    label: str
    stage_id: str = Field(alias="stageId")
    base_stage_ref: str | None = Field(default=None, alias="baseStageRef")
    common_base: ModelSourceDto = Field(alias="commonBase")
    scope: list[str]
    options: list[WorkingCopyOptionDto]
    selected_option_id: str | None = Field(alias="selectedOptionId")
    revision_sha256: str = Field(alias="revisionSha256")


class WorkingCopyListDto(BaseModel):
    working_copies: list[WorkingCopyDto] = Field(alias="workingCopies")


def working_option_from(dto: WorkingCopyOptionDto) -> WorkingCopyOption:
    return WorkingCopyOption(dto.id, dto.label, model_source_from(dto.model_source))


def working_copy_dto(item: WorkingCopy) -> WorkingCopyDto:
    return WorkingCopyDto(project_id=item.project_id, group_id=item.group_id, label=item.label, stage_id=item.stage_id,
                          base_stage_ref=item.base_stage_ref,
                          common_base=model_source_dto(item.common_base), scope=list(item.scope),
                          options=[WorkingCopyOptionDto(**option.to_dict()) for option in item.options],
                          selected_option_id=item.selected_option_id, revision_sha256=item.revision_sha256)


class SketchPlaneDto(BaseModel):
    """An explicit orthonormal drawing plane in building-local Y-up coordinates."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    origin: tuple[float, float, float]
    x_axis: tuple[float, float, float] = Field(alias="xAxis")
    y_axis: tuple[float, float, float] = Field(alias="yAxis")
    normal: tuple[float, float, float]

    @model_validator(mode="after")
    def orthonormal_frame(self) -> "SketchPlaneDto":
        vectors = (self.x_axis, self.y_axis, self.normal)
        if any(not isfinite(c) for vector in (self.origin, *vectors) for c in vector):
            raise ValueError("drawing plane coordinates must be finite")
        if any(abs(sum(c * c for c in vector) - 1.0) > 1e-6 for vector in vectors):
            raise ValueError("drawing plane axes and normal must be unit vectors")
        if any(abs(sum(a * b for a, b in zip(vectors[i], vectors[j]))) > 1e-6
               for i, j in ((0, 1), (0, 2), (1, 2))):
            raise ValueError("drawing plane axes and normal must be perpendicular")
        return self


class SketchActionDto(BaseModel):
    """A profile drawn on a work plane and the height it is pulled to.

    This is the same design edit ``edit_components`` already carries — one
    ``Element@1`` row whose producer is ``prism`` — stated in the terms the
    person drew it in. Sending the same ``elementId`` again is how an outline
    or a height is changed afterwards: the record keeps both as its own
    parameters, so nothing here is a one-way conversion into geometry.

    Without a plane, points are the record's own ``(x, z)`` pairs and height
    follows +Y. An explicit plane keeps its origin and orthonormal axes;
    zero height is a real face and negative height reverses the pull. The
    pointer preview stays in the browser; only a finished action arrives here.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    component_id: str = Field(alias="componentId", min_length=1)
    parent_component_id: str | None = Field(
        alias="parentComponentId",
        default=None,
        min_length=1,
        description="required when componentId is new here: the existing component it belongs under, "
                    "which is what decides the seat that builds it",
    )
    semantic_kind: str | None = Field(
        alias="semanticKind",
        default=None,
        min_length=1,
        description="what a new component is, in the record's own vocabulary; required only when "
                    "componentId is new here",
    )
    element_id: str = Field(
        alias="elementId",
        min_length=1,
        description="the Element@1 this action authors; an existing id edits that element",
    )
    profile: list[tuple[float, float]] = Field(
        min_length=2,
        max_length=512,
        description="Ordered local-plane point pairs. Closed profiles omit the repeated first point; open paths need at least two points.",
    )
    closed: bool = Field(default=True, description="True keeps the existing face/prism action; false saves an open polyline model curve and requires zero height.")
    height: float = Field(description="Signed pull distance in project length units; zero creates a planar face when closed, or an unfilled curve when open.")
    plane: SketchPlaneDto | None = Field(default=None, description=
        "Optional drawing frame. origin is relative to the resolved base datum; profile pairs "
        "are distances along xAxis/yAxis and positive height follows normal. Omit for the retained XZ plane.")
    base_level: str | None = Field(alias="baseLevel", default=None, min_length=1)
    base_datum: str | None = Field(alias="baseDatum", default=None, min_length=1)
    summary: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("profile")
    @classmethod
    def real_plan_points(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for x, z in value:
            if not isfinite(x) or not isfinite(z):
                raise ValueError("a profile point must be a finite number")
        rounded = [(round(x, 9), round(z, 9)) for x, z in value]
        if any(a == b for a, b in zip(rounded, rounded[1:] + rounded[:1])):
            raise ValueError("a profile cannot repeat a point; it closes on its own")
        if len(set(rounded)) != len(rounded):
            raise ValueError("a profile cannot visit the same point twice")
        return value

    @field_validator("height")
    @classmethod
    def real_height(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("height must be a finite number")
        return value

    @model_validator(mode="after")
    def one_base(self) -> "SketchActionDto":
        # What it stands on is one fact: a published level, or another
        # element's top. Both, or neither, is a request nobody can execute.
        if (self.base_level is None) == (self.base_datum is None):
            raise ValueError("state exactly one of baseLevel or baseDatum")
        if self.closed and len(self.profile) < 3:
            raise ValueError("a closed profile needs at least three points")
        if not self.closed and self.height != 0:
            raise ValueError("an open curve has no pull height")
        return self

    def base_reference(self) -> dict[str, str]:
        """The element row's ``references.base``, as the producers read it."""

        return {"level": self.base_level} if self.base_level is not None else {"datum": self.base_datum}

class SketchPrismRequestDto(SketchActionDto):
    """One drawing action against an exact retained or proposed source."""

    source_proposal_id: str | None = Field(
        alias="sourceProposalId", default=None, min_length=1,
        description="Continue this in-memory proposal; stateDigest stays its original baseStateDigest. "
        "Only executing the final proposal creates a candidate checkpoint.",
    )
    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; any other base is STALE_BASE",
    )
    keep: list[str] = Field(default_factory=list, description="refs this action must not change")
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)


class SketchBatchRequestDto(BaseModel):
    """Several planned forms become one proposal, with no intermediate writes."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    source_proposal_id: str | None = Field(
        alias="sourceProposalId", default=None, min_length=1,
        description="Continue this in-memory proposal; stateDigest stays its original baseStateDigest. "
        "Only executing the final proposal creates a candidate checkpoint.",
    )
    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; any other base is STALE_BASE",
    )
    keep: list[str] = Field(default_factory=list, description="refs this action must not change")
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)

    summary: str | None = Field(default=None, min_length=1, max_length=240)
    sketches: list[SketchActionDto] = Field(min_length=1, description="Ordered drawing actions; later items may reference an earlier item in this batch.")


class DocumentTracingSourceDto(BaseModel):
    """An exact saved page revision and the paths the user explicitly chose."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=STATE_DIGEST_PATTERN)
    page_index: int = Field(alias="pageIndex", ge=0)
    revision_sha256: str = Field(alias="revisionSha256", pattern=STATE_DIGEST_PATTERN)
    drawing_revision_ref: str | None = Field(alias="drawingRevisionRef", default=None)
    annotation_ids: list[str] = Field(alias="annotationIds", min_length=1, max_length=128)


class DocumentTracingRequestDto(BaseModel):
    """Read calibrated paths from saved annotations through the existing sketch action."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    state_digest: str = Field(alias="stateDigest", pattern=STATE_DIGEST_PATTERN)
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)
    source_proposal_id: str | None = Field(alias="sourceProposalId", default=None, min_length=1)
    keep: list[str] = Field(default_factory=list)
    tracing: DocumentTracingSourceDto
    component_id: str = Field(alias="componentId", min_length=1)
    parent_component_id: str | None = Field(alias="parentComponentId", default=None, min_length=1)
    semantic_kind: str | None = Field(alias="semanticKind", default=None, min_length=1)
    base_level: str | None = Field(alias="baseLevel", default=None, min_length=1)
    base_datum: str | None = Field(alias="baseDatum", default=None, min_length=1)
    height: float = Field(ge=0, allow_inf_nan=False,
        description="Positive pull height in project length units when any closed contour is selected; open paths always become zero-height curves.")
    summary: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def one_base(self) -> DocumentTracingRequestDto:
        if (self.base_level is None) == (self.base_datum is None):
            raise ValueError("state exactly one of baseLevel or baseDatum")
        return self


class TransformElementRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    source_proposal_id: str | None = Field(
        alias="sourceProposalId", default=None, min_length=1,
        description="Continue this in-memory proposal; stateDigest stays its original baseStateDigest. "
        "Only executing the final proposal creates a candidate checkpoint.",
    )
    state_digest: str = Field(alias="stateDigest", pattern=STATE_DIGEST_PATTERN)
    element_id: str = Field(alias="elementId", min_length=1)
    kind: Literal["move", "rotate", "scale", "copy"]
    translation: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    angle_degrees: float = Field(alias="angleDegrees", default=0)
    scale: tuple[float, float, float] | None = None
    origin: tuple[float, float, float] | None = None
    copy_element_id: str | None = Field(alias="copyElementId", default=None, min_length=1)
    keep: list[str] = Field(default_factory=list)
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)

    @model_validator(mode="after")
    def action_parameters(self) -> "TransformElementRequestDto":
        if self.kind in {"move", "copy"} and self.translation is None:
            raise ValueError("move/copy requires translation")
        if self.kind == "scale" and self.scale is None:
            raise ValueError("scale requires three factors")
        if self.copy_element_id is not None and self.kind != "copy":
            raise ValueError("copyElementId is only valid for copy")
        numbers = [self.angle_degrees, *(c for v in (self.translation, self.axis, self.scale, self.origin) if v for c in v)]
        if any(not isfinite(value) for value in numbers):
            raise ValueError("transform values must be finite")
        return self


class PushPullRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    source_proposal_id: str | None = Field(
        alias="sourceProposalId", default=None, min_length=1,
        description="Continue this in-memory proposal; stateDigest stays its original baseStateDigest. "
        "Only executing the final proposal creates a candidate checkpoint.",
    )
    state_digest: str = Field(alias="stateDigest", pattern=STATE_DIGEST_PATTERN)
    element_id: str = Field(alias="elementId", min_length=1)
    distance: float
    normal: tuple[float, float, float] | None = None
    keep: list[str] = Field(default_factory=list)
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)

    @model_validator(mode="after")
    def finite_pull(self) -> "PushPullRequestDto":
        if not isfinite(self.distance) or self.distance == 0 or any(not isfinite(c) for c in self.normal or ()):
            raise ValueError("push/pull requires a finite nonzero distance and finite normal")
        return self


class DeleteElementRequestDto(BaseModel):
    """Remove one element the architect picked, and nothing else.

    The same typed removal ``edit_components`` already carries, stated as the
    one thing a Delete key means: this element, at this exact base. It names no
    component to delete — pressing Delete on an object the architect picked has
    never meant "and the rest of what it belongs to" — and it cascades into
    nothing: an element another element stands on is refused, naming what
    stands on it, rather than quietly taking the neighbours with it.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    source_proposal_id: str | None = Field(
        alias="sourceProposalId", default=None, min_length=1,
        description="Continue this in-memory proposal; stateDigest stays its original baseStateDigest. "
        "Only executing the final proposal creates a candidate checkpoint.",
    )
    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; any other base is STALE_BASE",
    )
    element_id: str = Field(
        alias="elementId",
        min_length=1,
        description="the Element@1 the pick resolved to; the only thing removed",
    )
    summary: str | None = Field(default=None, min_length=1, max_length=240)
    keep: list[str] = Field(default_factory=list, description="refs this action must not change")
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)


def _semantic_edit_schema(schema: dict[str, Any]) -> None:
    """Reuse the compiler's producer contracts for direct, partial upserts."""

    from ..application.intent_agent import response_schema

    edit = response_schema(strict=False)["properties"]["semanticEdit"]["anyOf"][1]
    edit["required"] = ["summary"]
    for name, identity in (("entities", "entity_id"), ("parameters", "key"), ("relations", "relation_id")):
        items = edit["properties"][name]["items"]
        for variant in items.get("anyOf", [items]):
            variant["required"] = [identity]
            variant["description"] = "Upsert: omitted fields retain the existing value; new items need their complete declared fields."
            fields = variant.get("properties", {}).get("fields")
            if fields is not None:
                for field_variant in fields.get("anyOf", [fields]):
                    field_variant["required"] = []
    schema.update(edit)


class SemanticEditRequestDto(BaseModel):
    """Named design edits, validated by the existing component compiler."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid", json_schema_extra=_semantic_edit_schema)

    summary: str = Field(min_length=1)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    remove_entity_ids: list[str] = Field(alias="removeEntityIds", default_factory=list)
    remove_parameter_keys: list[str] = Field(alias="removeParameterKeys", default_factory=list)
    remove_relation_ids: list[str] = Field(alias="removeRelationIds", default_factory=list)
    protected: list[str] = Field(default_factory=list)
    kept: list[str] = Field(default_factory=list)


class ProposalRequestDto(BaseModel):
    """One scalar utterance or a typed semantic edit, at one exact base."""

    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)

    model_config = ConfigDict(populate_by_name=True, frozen=True, json_schema_extra={
        "oneOf": [
            {"required": ["utterance", "targetComponentId"],
             "properties": {"utterance": {"type": "string"}, "targetComponentId": {"type": "string"}, "semanticEdit": {"type": "null"}}},
            {"required": ["semanticEdit"],
             "properties": {"semanticEdit": {"type": "object"}, "utterance": {"type": "null"}}},
        ],
    })

    source_proposal_id: str | None = Field(
        alias="sourceProposalId", default=None, min_length=1,
        description="Continue this in-memory proposal; stateDigest stays its original baseStateDigest. "
        "Only executing the final proposal creates a candidate checkpoint.",
    )
    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; a proposal "
        "against any other state is refused as STALE_BASE",
    )
    target_component_id: str | None = Field(
        alias="targetComponentId",
        default=None,
        min_length=1,
        description="the selected Component@1; selection comes from the "
        "request, never from the utterance",
    )
    element_id: str | None = Field(
        alias="elementId",
        default=None,
        min_length=1,
        description="the selected Element@1, when one was picked; it must "
        "belong to targetComponentId",
    )
    utterance: str | None = Field(
        default=None,
        min_length=1,
        description="one sentence in the intent grammar; anything else comes "
        "back as BLOCKED_NEEDS_HUMAN with the accepted forms",
    )
    semantic_edit: SemanticEditRequestDto | None = Field(
        alias="semanticEdit", default=None,
        description="Submit the current Agent's typed component edit directly, without another model call. "
        "Entity producer inputs use @parameter_key bindings; expressions belong to Parameter.expr and inputs. "
        "Exactly one of semanticEdit and utterance is required.",
    )
    keep: list[str] = Field(default_factory=list, description="Additional entity:/parameter: refs this edit must preserve.")
    project_id: str | None = Field(
        alias="projectId",
        default=None,
        min_length=1,
        description="the project the client believes it is proposing against; "
        "a different one is refused as PROJECT_MISMATCH",
    )
    source_run_id: str | None = Field(
        alias="sourceRunId",
        default=None,
        min_length=1,
        description="the retained run selected as the editing base; omitted "
        "uses the project's default state projection",
    )

    @model_validator(mode="after")
    def one_edit(self) -> "ProposalRequestDto":
        if (self.utterance is None) == (self.semantic_edit is None):
            raise ValueError("provide exactly one of utterance or semanticEdit")
        if self.utterance is not None and self.target_component_id is None:
            raise ValueError("a scalar utterance requires targetComponentId")
        return self

    def context_refs(self) -> list[str]:
        """The selection as the ``IntentProvider`` port takes it."""

        refs = [
            f"state:{self.state_digest}",
            f"component:{self.target_component_id}",
        ]
        if self.element_id is not None:
            refs.append(f"element:{self.element_id}")
        return refs


class ProposalTargetDto(BaseModel):
    """What the proposal is about, in the record's own identifiers."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    component_id: str = Field(alias="componentId")
    element_id: str | None = Field(alias="elementId")
    ref: str = Field(
        description="the kernel's prefixed ref: entity:<id> or parameter:<key>",
    )
    key: str | None


class ProposalChangeDto(BaseModel):
    """The number as the record has it, and the number proposed for it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    kind: Literal["set_scalar"] = "set_scalar"

    # Authored quantities keep the type they were written with: a value
    # authored as 3 must not come back as 3.0.
    old: int | float
    new: int | float
    unit: str | None = Field(
        description="the unit the record declares; element params are "
        "unit-less numbers and answer null",
    )


class ComponentChangeDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    action: Literal["add", "update", "remove"]
    entity_id: str = Field(alias="entityId")
    label: str
    description: str


class ComponentEditsDto(BaseModel):
    """The typed design edits shown only in the proposal's details."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    entities: list[dict[str, Any]]
    parameters: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    remove_entity_ids: list[str] = Field(alias="removeEntityIds")
    remove_parameter_keys: list[str] = Field(alias="removeParameterKeys")
    remove_relation_ids: list[str] = Field(alias="removeRelationIds")


class ComponentEditChangeDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    kind: Literal["edit_components"] = "edit_components"
    summary: str
    changes: list[ComponentChangeDto]
    kept: list[str]
    edits: ComponentEditsDto


class ProposalScopeDto(BaseModel):
    """How far the request said the change reaches, and what that covers.

    A *coverage*, not a mutation. The operator below still moves one scalar on
    one element; ``elementIds`` is what the architect agreed the change is
    about, so a client can show the whole stack as revalidated instead of
    discovering it in the closure afterwards.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    scope: Literal["element", "stack", "datum"]
    element_ids: list[str] = Field(
        alias="elementIds",
        description="every element the settled scope covers, the target first",
    )


class ProposalDto(BaseModel):
    """The wire form of ``POST /api/proposals`` and ``GET /api/proposals/{id}``."""

    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)

    proposal_id: str = Field(alias="proposalId")
    status: Literal["proposed", "conflict"] = Field(
        description="conflict means the change reaches something the "
        "utterance asked to keep; it is still a proposal, never an execution",
    )
    base_state_digest: str = Field(
        alias="baseStateDigest",
        description="the exact base the operator refuses to run without",
    )
    record_digest: str = Field(alias="recordDigest")
    source_run_id: str | None = Field(
        alias="sourceRunId",
        default=None,
        description="the explicitly selected editing-base run, or null for "
        "the project's default state projection",
    )
    target: ProposalTargetDto
    change: ProposalChangeDto | ComponentEditChangeDto = Field(discriminator="kind")
    protected: list[str]
    decision_operator: dict[str, Any] | None = Field(
        alias="decisionOperator",
        description="the kernel's DecisionOperator@2 payload, opaque here",
    )
    impact: ImpactDto
    utterance: str
    persistence: str = Field(
        description="where this proposal lives; it is not version history",
    )
    created_at: str = Field(alias="createdAt")
    scope: ProposalScopeDto | None = Field(
        default=None,
        description="the scope the intent exchange settled, when it settled "
        "one; null for a proposal made straight from a selection, which asked "
        "nobody how far",
    )


def to_dto(proposal: Proposal, *, scope: ProposalScopeDto | None = None) -> ProposalDto:
    """Shape one proposal for the wire; nothing here is recomputed.

    ``scope`` belongs to the intent exchange, not to the proposal store: a
    proposal is the same typed operator whether an exchange settled a coverage
    or a click did. ``POST /api/proposals`` asks nobody how far and passes none.
    """

    return ProposalDto(
        scope=scope,
        proposal_id=proposal.proposal_id,
        # The application's two statuses are the DTO's two literals; a third
        # would fail here rather than reach a client.
        status=proposal.status,
        base_state_digest=proposal.base_state_digest,
        record_digest=proposal.record_digest,
        source_run_id=proposal.source_run_id,
        source_stage_ref=None if proposal.source_stage_ref is None else proposal.source_stage_ref.uri,
        model_source=model_source_dto(proposal.model_source),
        target=ProposalTargetDto(
            component_id=proposal.component_id,
            element_id=proposal.element_id,
            ref=proposal.target_ref,
            key=proposal.key,
        ),
        change=(
            ComponentEditChangeDto(**proposal.semantic_edit)
            if proposal.semantic_edit is not None
            else ProposalChangeDto(old=proposal.old, new=proposal.new, unit=proposal.unit)
        ),
        protected=list(proposal.protected),
        decision_operator=None if proposal.operator is None else proposal.operator.to_dict(),
        impact=impact_dto(proposal.impact),
        utterance=proposal.utterance,
        persistence=PERSISTENCE,
        created_at=proposal.created_at,
    )


# ---- the judgement -----------------------------------------------------------
#
# The wire form of one ``DeliberationEpisode``. It lives beside the proposal it
# is a decision about, because a decision that travelled in its own vocabulary
# would be a second story about the same option.


class ModifiedToDto(BaseModel):
    """What the architect said instead, when the decision was ``modified``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    utterance: str = Field(
        min_length=1,
        description="the sentence the architect replaced the proposal with; "
        "it is re-proposed through the same deterministic path",
    )


class ProposalDecisionRequestDto(BaseModel):
    """``POST /api/proposals/{id}/decision``: what was decided, and why.

    Running a candidate is not accepting it. ``accepted`` is the architect's
    explicit choice of one candidate this process ran from the proposal and
    finished: ``candidateId`` names that run, and the acceptance is retained
    into it. An acceptance that named no run would be a judgement about a
    building nobody built, so the id is required and never defaulted to the
    latest run.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    decision: Literal["accepted", "rejected", "modified"] = Field(
        description="accepted chooses the finished candidate named by "
        "candidateId and closes the other options still open against the "
        "same base; rejected closes the option; modified closes it and "
        "re-proposes modifiedTo in its place",
    )
    reason: str | None = Field(
        default=None,
        min_length=1,
        description="the architect's own sentence for the decision, kept "
        "verbatim; null when none was given",
    )
    modified_to: ModifiedToDto | None = Field(
        alias="modifiedTo",
        default=None,
        description="required when decision is modified, refused otherwise",
    )
    candidate_id: str | None = Field(
        alias="candidateId",
        default=None,
        min_length=1,
        description="required when decision is accepted, refused otherwise: "
        "a candidate this process ran from this proposal, and which "
        "succeeded; the acceptance is written into that run",
    )


class EpisodeChangeDto(BaseModel):
    """The number the record had, and the number the option proposed."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    kind: Literal["set_scalar"] = "set_scalar"

    key: str
    old: int | float
    new: int | float


class EpisodeProposalDto(BaseModel):
    """One option that was on the table, and what became of it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    proposal_id: str = Field(alias="proposalId")
    target: str
    change: EpisodeChangeDto | ComponentEditChangeDto = Field(discriminator="kind")
    closure: list[str] = Field(
        description="every ref this option would invalidate, as the "
        "proposal's own closure had it",
    )
    decision: Literal["accepted", "rejected", "modified"]
    reason: str | None
    modified_to: dict[str, Any] | None = Field(alias="modifiedTo")


class EpisodeIntentDto(BaseModel):
    """What was asked, as the studio resolved it — not the chat log."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    utterance: str
    target_component_id: str = Field(alias="targetComponentId")
    element_id: str | None = Field(alias="elementId")
    requested_property: str | None = Field(alias="requestedProperty")
    known_slots: dict[str, str] = Field(alias="knownSlots")
    request_id: str | None = Field(
        alias="requestId",
        description="the pending intent this request belonged to, when it "
        "came through a clarification chain",
    )


class EpisodeDto(BaseModel):
    """One retained judgement: the intent, the options, and the run."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    episode_id: str = Field(alias="episodeId")
    project_id: str = Field(alias="projectId")
    state_digest: str = Field(alias="stateDigest")
    intent: EpisodeIntentDto
    proposals: list[EpisodeProposalDto]
    protected: list[str]
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    validation_refs: list[str] = Field(alias="validationRefs")
    produced_run: str | None = Field(
        alias="producedRun",
        description="the candidate run the accepted proposal made; null "
        "while the judgement has met no run",
    )
    chosen_scope: str | None = Field(alias="chosenScope")
    created_at: str = Field(alias="createdAt")
    persistence: str = Field(
        description="run:<id> once the judgement is in a run's records, and "
        "the in-memory sentence while it is only this process's",
    )


def episode_proposal_dto(item: EpisodeProposal) -> EpisodeProposalDto:
    return EpisodeProposalDto(
        proposal_id=item.proposal_id,
        target=item.target,
        change=(
            ComponentEditChangeDto(**item.change.semantic_edit)
            if item.change.semantic_edit is not None
            else EpisodeChangeDto(key=item.change.key, old=item.change.old, new=item.change.new)
        ),
        closure=list(item.closure),
        # The application's three decisions are the DTO's three literals; a
        # fourth would fail here rather than reach a client.
        decision=item.decision,
        reason=item.reason,
        modified_to=(
            None if item.modified_to is None else dict(item.modified_to)
        ),
    )


def episode_dto(episode: DeliberationEpisode) -> EpisodeDto:
    """Shape one judgement for the wire; nothing here is recomputed."""

    return EpisodeDto(
        episode_id=episode.episode_id,
        project_id=episode.project_id,
        state_digest=episode.state_digest,
        intent=EpisodeIntentDto(
            utterance=episode.intent.utterance,
            target_component_id=episode.intent.target_component_id,
            element_id=episode.intent.element_id,
            requested_property=episode.intent.requested_property,
            known_slots=dict(episode.intent.known_slots),
            request_id=episode.intent.request_id,
        ),
        proposals=[episode_proposal_dto(item) for item in episode.proposals],
        protected=list(episode.protected),
        evidence_refs=list(episode.evidence_refs),
        validation_refs=list(episode.validation_refs),
        produced_run=episode.produced_run,
        chosen_scope=episode.chosen_scope,
        created_at=episode.created_at,
        persistence=episode.persistence,
    )
