"""The wire form of the construction contract (#419, spec §3.2).

An agent authors geometry with one construction script and reads the model
back in the same words; meaning arrives later as facets on the same identity.
Every description here is agent-facing text: it keeps the layer rule
(``monkeyarch.authoring.construction.vocabulary.LAYER_RULE_TOKENS``) and names no way
the runtime realises a shape; the Stage C hosted-opening request says ``wall``
only as the facet value it needs. What a script produced is returned as the
ordinary proposal, plus what the script itself reported.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from archflow.state.state_record import _EPISTEMIC
from monkeyarch.authoring.construction.vocabulary import LIMITS

from ...application.projection import StateProjection
from .project import ProjectVersionDto, ReferenceRunDto, project_version_dto, reference_run_dto
from .proposal import STATE_DIGEST_PATTERN, ProposalDto

Bounds = tuple[tuple[float, float, float], tuple[float, float, float]]

_BOUNDS = "((xmin, ymin, zmin), (xmax, ymax, zmax)) in metres, Y up"


class _SourceFields(BaseModel):
    """What every proposal request says about the exact state it was made against."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    state_digest: str = Field(
        alias="stateDigest", pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest GET /api/construction/model answered with; any other base is STALE_BASE",
    )
    summary: str | None = Field(default=None, min_length=1, max_length=240,
                                description="one sentence for the proposal; left out, the change describes itself")
    source_run_id: str | None = Field(alias="sourceRunId", default=None, min_length=1,
                                      description="the run to build on; left out, the project's current state")
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)
    source_proposal_id: str | None = Field(
        alias="sourceProposalId", default=None, min_length=1,
        description="continue this unexecuted proposal; stateDigest stays its original baseStateDigest",
    )
    keep: list[str] = Field(default_factory=list, description=(
        "entity: or parameter: refs this change must not disturb; entity:<geometry id> keeps every part of it"))
    project_id: str | None = Field(alias="projectId", default=None, min_length=1,
                                   description="the project the client believes it is working on")


# The statuses the record accepts for a parameter, read from where it checks them.
EpistemicStatus = Literal[tuple(sorted(_EPISTEMIC))]  # type: ignore[valid-type]


class ConstructionParameterDto(BaseModel):
    """One project parameter to add or change, in the record's own parameter shape.

    A new key needs value and unit. For a key the project already has, a field
    left out keeps what the project says; value, unit, inputs and
    epistemic_status are never null.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, description="the parameter's key; a script reads it with param(key)")
    value: int | float = Field(default=None, description="its number, in unit; required for a new key")
    unit: str = Field(default=None, min_length=1, description="its unit, such as m; required for a new key")
    expr: str | None = Field(
        default=None,
        description="an expression over other parameter keys that derives the value, such as 2 * module; "
                    "null removes an existing one",
    )
    inputs: list[str] = Field(default=None, description="the parameter keys expr reads")
    epistemic_status: EpistemicStatus = Field(
        default=None, description="how the value is known; a new key left without one is derived",
    )
    source_ref: str | None = Field(default=None, description="where the value comes from, such as a brief or a drawing")


class ConstructionRequestDto(_SourceFields):
    """One construction script against one exact state."""

    script: str = Field(
        min_length=1, max_length=LIMITS["characters"],
        description="a construction script in the language GET /api/construction describes: interpreted, never "
                    "executed. The shapes it leaves are proposed under their ids; a refused script proposes nothing "
                    "and answers 422 CONSTRUCTION_INVALID with its line, column and source line",
    )
    parameters: list[ConstructionParameterDto] = Field(
        default_factory=list,
        description="project parameters to add or change with the script; the script reads them with param(key)",
    )


class ConstructionReportRowDto(BaseModel):
    """One shape the script left: its id, what it is, and where the script made or last changed it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    id: str = Field(description="the geometry id: the name the model view and get() use")
    form: str = Field(description="solid, face, path or other")
    status: str = Field(description="created, updated or deleted")
    bounds: Bounds | None = Field(description=_BOUNDS + ", before cuts; null when they cannot be predicted")
    cuts: list[str] = Field(description="the ids this shape removes from itself")
    line: int | None = Field(description="the script line that made or last changed the shape")


class ConstructionOutcomeDto(BaseModel):
    """What the script reported: one row per shape it left or removed, and what it printed."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    report: list[ConstructionReportRowDto]
    log: list[str] = Field(description="what print() wrote, in order")


class ConstructionProposalDto(ProposalDto):
    """A proposal made from one construction script, with what the script reported."""

    construction: ConstructionOutcomeDto


class ConstructionRefusalDto(BaseModel):
    """A refused request: a code and one sentence. A refused script also says where it failed."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str = Field(description="CONSTRUCTION_INVALID for a refused script; another code for any other refusal")
    detail: str
    line: int | None = Field(default=None, description="the script line the refusal names")
    column: int | None = Field(default=None, description="1-based column on that line")
    source_line: str | None = Field(alias="sourceLine", default=None, description="that line of the script")
    message: str | None = Field(default=None, description="the one sentence, also given as detail")


class FacetTargetDto(BaseModel):
    """Facets to set or remove on one geometry id (a component)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="the geometry id the model view lists")
    set: dict[str, str] | None = Field(
        default=None,
        description="facet keys and values to add or change: architectural.role, architectural.enclosure, "
                    "structural.role, material.name, material.color (#RRGGBB, with material.name), fabrication.method",
    )
    remove: list[str] | None = Field(default=None, description="facet keys to take off")


class FacetsRequestDto(_SourceFields):
    """Meaning added to, or taken from, existing geometry. Its form, cuts and objects stay as they are."""

    targets: list[FacetTargetDto] = Field(min_length=1, max_length=50)


# Stage C text (spec §3.5): the hosted-opening contract may say ``wall``, and only as the facet value.
class HostedOpeningRequestDto(_SourceFields):
    """A door or a window on geometry whose meaning is architectural.role = wall, against one exact state."""

    host: str = Field(min_length=1, description="the geometry id to take it; the model view lists hosted-opening "
                                                "among its capabilities")
    kind: Literal["door", "window"]
    along: float = Field(description="metres from the host's alongLine.start toward its alongLine.end (as the model "
                                     "view gives them) to the centre of the opening")
    width: float = Field(gt=0, description="metres across the opening")
    sill: float = Field(description="metres from the host's base up to the bottom of the opening; 0 for a door")
    head: float = Field(description="metres from the host's base up to the top of the opening")
    shape: Literal["rectangular", "semicircular_arch"] | None = Field(
        default=None, description="rectangular when left out; a semicircular_arch needs springHeight")
    spring_height: float | None = Field(
        alias="springHeight", default=None,
        description="semicircular_arch only: metres from the base up to where the arch springs; "
                    "head - springHeight = width / 2")
    family: dict[str, int | float] | None = Field(
        default=None,
        description="the family that fills the opening, in metres: a window takes frame_width, frame_depth, "
                    "frame_projection, glazing_thickness and glazing_offset; a door takes frame_width, frame_depth, "
                    "frame_projection, leaf_thickness, leaf_offset, leaf_count (1 or 2), leaf_gap, clearance_bottom "
                    "and clearance_top. Left out, the opening is an empty passage with no frame or leaf",
    )
    interface_ref: str | None = Field(
        alias="interfaceRef", default=None, min_length=1,
        description="the connection between spaces it serves: one of the relationship refs the project's "
                    "connections declare. Left out, it serves none yet")


class EnrichmentRequiredDto(BaseModel):
    """Refused until the geometry has the meaning a capability needs: the facet to add, and one sentence."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str = Field(description="ENRICHMENT_REQUIRED; STALE_BASE for a request made against another state")
    detail: str
    message: str | None = Field(default=None, description="the one sentence, also given as detail")
    entity: str | None = Field(default=None, description="the geometry id that needs the facet")
    facet: str | None = Field(default=None, description="the facet key to add with POST /api/proposals/facets")
    value: str | None = Field(default=None, description="the value that facet needs")


class ConstructionVerbDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    signature: str
    returns: str
    description: str


class ConstructionLanguageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    summary: str
    allowed: list[str]
    not_allowed: list[str] = Field(alias="notAllowed")
    builtins: list[str]
    math: list[str]


class ConstructionVocabularyDto(BaseModel):
    """The construction contract: conventions, language, limits, verbs and one example."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    vocabulary_schema: str = Field(alias="schema")
    conventions: dict[str, str]
    language: ConstructionLanguageDto
    limits: dict[str, int | float]
    verbs: list[ConstructionVerbDto]
    example: str


class ConstructionLevelDto(BaseModel):
    """A project level a script can stand on with level(id)."""

    model_config = ConfigDict(frozen=True)

    id: str
    elevation: float


class ConstructionParameterValueDto(BaseModel):
    """A project parameter a script can bind with param(key)."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: int | float
    unit: str


class ConstructionCapabilityDto(BaseModel):
    """Something this geometry's facets allow, and the route that does it."""

    model_config = ConfigDict(frozen=True)

    id: str
    route: str
    needs: dict[str, Any]


class ConstructionOpeningDto(BaseModel):
    """A door or a window this geometry hosts, as it was asked for."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="the opening's id, unique within its host")
    kind: str = Field(description="door or window")
    along: float | None = Field(description="metres from the host's alongLine.start to the opening's centre; null "
                                            "when stated another way")
    width: float | None = Field(description="metres across; null when stated another way")
    sill: float | None = Field(description="metres from the host's base up to its bottom; null when stated another way")
    head: float | None = Field(description="metres from the host's base up to its top; null when stated another way")


class ConstructionLineDto(BaseModel):
    """Where a door's or window's along is measured: from start toward end, plan points (x, z) in metres."""

    model_config = ConfigDict(frozen=True)

    start: tuple[float, float]
    end: tuple[float, float]


class ConstructionEntityDto(BaseModel):
    """One geometry id: what it is, where it is, what it cuts and what its meaning allows."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    id: str = Field(description="the geometry id; get(id) reaches it in a script")
    form: str = Field(description="solid, face, path or other")
    bounds: Bounds | None = Field(description=_BOUNDS + "; null when they cannot be predicted")
    cuts: list[str] = Field(description="the ids this geometry removes from itself")
    cut_by: list[str] = Field(alias="cutBy", description="the ids this geometry is removed from")
    hidden: bool = Field(description="true while it cuts something: kept in the model, hidden")
    parts: list[str] | None = Field(description="its part ids when it has several, which get() reaches one by one; "
                                                "null for one")
    facets: dict[str, str] = Field(description="the meaning given to it so far; empty until someone says")
    capabilities: list[ConstructionCapabilityDto] = Field(description="what its facets allow, where its geometry "
                                                                      "can take it")
    openings: list[ConstructionOpeningDto] = Field(description="the doors and windows it hosts, when it is one part")
    along_line: ConstructionLineDto | None = Field(
        alias="alongLine",
        description="where the along of a door or window on it is measured, when its capabilities include "
                    "hosted-opening and its shape can take one; null otherwise")


class ConstructionModelDto(BaseModel):
    """The model in construction terms, at one exact state."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    published: ProjectVersionDto = Field(description="the issued design this model was read against")
    reference_run: ReferenceRunDto = Field(alias="referenceRun", description="the run this model was read from")
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    state_digest: str | None = Field(
        alias="stateDigest",
        description="the state a script is sent against; null when this state cannot base a proposal",
    )
    record_digest: str = Field(alias="recordDigest")
    levels: list[ConstructionLevelDto]
    parameters: list[ConstructionParameterValueDto]
    entities: list[ConstructionEntityDto]


def construction_model_dto(projection: StateProjection, model: Mapping[str, Any]) -> ConstructionModelDto:
    """The model view with the identity ``GET /api/state`` answers with, shaped by the same functions."""

    return ConstructionModelDto.model_validate({
        **model,
        "published": project_version_dto(projection.head),
        "referenceRun": reference_run_dto(projection.reference),
        "sourceStageRef": None if projection.source_stage_ref is None else projection.source_stage_ref.uri,
    })
