"""The wire form of one scoped project decision.

Everything a decision claims is stated, never inferred: what was said, what
kind of judgement it is, how strongly it is held, what it is about, how far it
reaches, and the exact evidence it was said against. The server adds only what
it can vouch for itself — the decision's identity, its revision chain, who
asked for it through which surface, and the parameter value it read. A
project recipe's paper-space values are the one binding the caller states:
they are what the person chose.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..application.decisions import DecisionRevision
from .drawings import PlanRequestDto

SHA256 = r"^[0-9a-f]{64}$"

Disposition = Literal["keep", "reject", "avoid", "require", "lock", "defer"]
Strength = Literal["hard", "strong_preference", "soft_preference", "temporary"]
Domain = Literal["drawing", "copy", "design"]
Extent = Literal["project", "stage", "targets"]
Applicability = Literal["scope", "exact-source"]
SourceKind = Literal["human", "agent", "evaluator", "deterministic-rule"]


class _Frozen(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")


class BoardDecisionSourceDto(_Frozen):
    """An exact historic board revision and the elements named on it."""

    kind: Literal["board"]
    revision_sha256: str = Field(alias="revisionSha256", pattern=SHA256)
    element_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        alias="elementIds", min_length=1, max_length=64,
    )


class DocumentDecisionSourceDto(_Frozen):
    """One registered page of one document at the exact revision it was read at."""

    kind: Literal["document"]
    run_id: str = Field(alias="runId", min_length=1, max_length=128)
    asset_sha256: str = Field(alias="assetSha256", pattern=SHA256)
    revision_ref: str | None = Field(alias="revisionRef", default=None, min_length=1)
    page_index: int = Field(alias="pageIndex", ge=0, strict=True)


class DesignDecisionSourceDto(_Frozen):
    """A real retained design run, its state digest and its Stage when named."""

    kind: Literal["design"]
    source_run_id: str = Field(alias="sourceRunId", min_length=1, max_length=128)
    state_digest: str = Field(alias="stateDigest", pattern=SHA256)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None, min_length=1)


class RecipeExportDecisionSourceDto(_Frozen):
    """The recipe export an imported project recipe came from, named by its own sha256.

    Retained, never requested: a decision request names a board, a page or a
    design run. tools/export_drawing_recipe.py writes this source when a person
    confirms an import, after reading that export and checking its content
    against the digest. The export file itself stays outside the project.
    """

    kind: Literal["recipe-export"]
    export_sha256: str = Field(alias="exportSha256", pattern=SHA256)


DecisionSourceDto = Annotated[
    Union[BoardDecisionSourceDto, DocumentDecisionSourceDto, DesignDecisionSourceDto],
    Field(discriminator="kind"),
]

# What a retained decision can cite: what a request names, and the export an
# imported recipe came from.
RetainedDecisionSourceDto = Annotated[
    Union[BoardDecisionSourceDto, DocumentDecisionSourceDto, DesignDecisionSourceDto,
          RecipeExportDecisionSourceDto],
    Field(discriminator="kind"),
]


class MessageSourceDto(_Frozen):
    """Which chat message a decision's words came from, as the caller states it.

    A claim of provenance, never a credential and never an authorization: the
    boundary's own ``attribution`` is resolved from configured actors and this
    process's surface, and no request body takes part in it. The Hub fills
    this in from the message the user actually sent; nothing here verifies it.
    """

    session_id: str = Field(alias="sessionId", min_length=1, max_length=256)
    message_id: str = Field(alias="messageId", min_length=1, max_length=256)


class DecisionScopeDto(_Frozen):
    """How far one decision reaches, said in a field rather than guessed."""

    domain: Domain
    extent: Extent
    stage_ref: str | None = Field(alias="stageRef", default=None, min_length=1)
    target_refs: list[Annotated[str, Field(min_length=1, max_length=192)]] | None = Field(
        alias="targetRefs", default=None, max_length=32,
    )


class ParameterBindingRequestDto(_Frozen):
    """The parameter a decision is bound to; the server reads its value itself."""

    kind: Literal["parameter"]
    parameter_key: str = Field(alias="parameterKey", min_length=1, max_length=128)


# A drawing request's own bounds on the same paper-space values, so a recipe
# never holds a value a drawing request would refuse.
_PLAN = PlanRequestDto.model_fields
_CutLineMm = Annotated[float, *_PLAN["cut_line_mm"].metadata]
_VisibleLineMm = Annotated[float, *_PLAN["visible_line_mm"].metadata]
_HatchSpacingMm = Annotated[float, *_PLAN["hatch_spacing_mm"].metadata]


class RecipeGraphicsDto(_Frozen):
    """The paper-space values one project recipe sets; a key it leaves out is null.

    Closed to the drawing's own graphics keys, each bounded exactly as a cut-plan
    request bounds it. No object, material or model is looked up: a recipe is
    what a new drawing starts from, not a claim about one model.
    """

    cut_line_mm: _CutLineMm | None = Field(
        alias="cutLineMm", default=None, description="cut line weight on paper; set under drawing:lineweight")
    visible_line_mm: _VisibleLineMm | None = Field(
        alias="visibleLineMm", default=None, description="visible line weight on paper; set under drawing:lineweight")
    hatch_spacing_mm: _HatchSpacingMm | None = Field(
        alias="hatchSpacingMm", default=None, description="section hatch spacing on paper; set under drawing:hatch")


class RecipeBindingRequestDto(_Frozen):
    """The project recipe: paper-space values a new drawing starts from.

    Retained only for a person's explicit confirmation (sourceKind 'human') of a
    'require' decision in the drawing domain, held hard, strong_preference or
    soft_preference, applying by 'scope' to the project or one Stage, and
    evidenced by the exact document page it was confirmed on. Every value sits
    under its own targetRef. One key has one active value per strength and reach;
    supersede the decision to change it.
    """

    kind: Literal["recipe"]
    graphics: RecipeGraphicsDto


TypedBindingRequestDto = Annotated[
    Union[ParameterBindingRequestDto, RecipeBindingRequestDto],
    Field(discriminator="kind"),
]


class DecisionParameterBindingDto(_Frozen):
    """What the record said about that parameter when the decision was made."""

    kind: Literal["parameter"]
    parameter_key: str = Field(alias="parameterKey")
    value: float
    unit: str
    epistemic_status: str = Field(alias="epistemicStatus")
    lock_authority: str | None = Field(
        alias="lockAuthority",
        description="the existing lock this decision records; a decision never takes or releases one",
    )


class DecisionRecipeBindingDto(_Frozen):
    """The paper-space values this project recipe sets, as the person confirmed them."""

    kind: Literal["recipe"]
    graphics: RecipeGraphicsDto


DecisionTypedBindingDto = Annotated[
    Union[DecisionParameterBindingDto, DecisionRecipeBindingDto],
    Field(discriminator="kind"),
]


class DecisionRequestDto(_Frozen):
    """One decision as the caller states it."""

    project_id: str = Field(alias="projectId", min_length=1)
    raw_language: str = Field(
        alias="rawLanguage", min_length=1, max_length=2000,
        description="the architect's own words, retained unedited; no rule is manufactured from them",
    )
    message_source: MessageSourceDto | None = Field(
        alias="messageSource", default=None,
        description="which chat message these words came from, as the caller claims it; it is "
        "provenance, not a credential, and it grants nothing",
    )
    disposition: Disposition
    strength: Strength
    target_ref: str = Field(alias="targetRef", min_length=1, max_length=192)
    scope: DecisionScopeDto
    source: DecisionSourceDto
    applicability: Applicability = Field(
        description="'scope' survives later revisions inside the scope; 'exact-source' applies "
        "only while the caller reads the very source it was said against",
    )
    source_kind: SourceKind = Field(
        alias="sourceKind",
        description="who this decision's disposition, scope and target were settled by, as the "
        "caller claims it, kept apart from the boundary's own attribution. Use 'agent' whenever "
        "an agent interpreted any of those fields from the words: 'human' does not mean a person "
        "typed the sentence, it means a person settled every field the decision now claims",
    )
    typed_binding: TypedBindingRequestDto | None = Field(
        alias="typedBinding", default=None,
        description="'parameter' names a design parameter whose value, unit and lock the server reads "
        "itself; 'recipe' carries a project recipe's paper-space values, retained only for a person's "
        "confirmed 'require' decision in the drawing domain",
    )


class DecisionAttributionDto(_Frozen):
    actor_id: str = Field(alias="actorId")
    authenticated: bool
    origin: str


class DecisionDto(_Frozen):
    """One decision at one revision, as this project retains it."""

    project_id: str = Field(alias="projectId")
    decision_id: str = Field(alias="decisionId")
    revision_ref: str = Field(alias="revisionRef")
    previous_revision_ref: str | None = Field(alias="previousRevisionRef")
    status: Literal["active", "deferred", "revoked", "superseded"] = Field(
        description="what this revision is now. 'superseded' is derived, not retained: it is what "
        "a revision that is no longer its chain's tip reads as, and the record it was written "
        "into is unchanged",
    )
    raw_language: str = Field(alias="rawLanguage")
    message_source: MessageSourceDto | None = Field(alias="messageSource")
    disposition: Disposition
    strength: Strength
    target_ref: str = Field(alias="targetRef")
    scope: DecisionScopeDto
    source: RetainedDecisionSourceDto
    applicability: Applicability
    source_kind: SourceKind = Field(alias="sourceKind")
    typed_binding: DecisionTypedBindingDto | None = Field(alias="typedBinding")
    attribution: DecisionAttributionDto
    created_at: str = Field(alias="createdAt")
    reason: str | None = None
    revision_message_source: MessageSourceDto | None = Field(
        alias="revisionMessageSource", default=None,
        description="the message that asked for this revision, when one was named. Null on a "
        "first revision; a revocation carries this beside the original message it revokes",
    )


class DecisionListDto(_Frozen):
    project_id: str = Field(alias="projectId")
    decisions: list[DecisionDto]


class DecisionHistoryDto(_Frozen):
    project_id: str = Field(alias="projectId")
    decision_id: str = Field(alias="decisionId")
    revisions: list[DecisionDto]


class DecisionRevisionRequestDto(_Frozen):
    """Revoke or supersede one decision, against the revision the caller read."""

    project_id: str = Field(alias="projectId", min_length=1)
    expected_revision_ref: str = Field(alias="expectedRevisionRef", min_length=1)
    action: Literal["revoke", "supersede"]
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    revision_message_source: MessageSourceDto | None = Field(
        alias="revisionMessageSource", default=None,
        description="which chat message asked for this revocation or supersession; the wording "
        "being revised keeps its own messageSource",
    )
    replacement: DecisionRequestDto | None = None

    @model_validator(mode="after")
    def coherent_action(self) -> DecisionRevisionRequestDto:
        if (self.action == "supersede") != (self.replacement is not None):
            raise ValueError("supersede carries its replacement decision; revoke carries none")
        if self.replacement is not None and self.replacement.project_id != self.project_id:
            raise ValueError("the replacement names another project")
        return self


class DecisionContextDto(_Frozen):
    """What a next turn is about, for the decisions it should be handed.

    Absent, the design and drawing domains (the project recipe among the drawing
    decisions), the projection's own Stage and the focus the request already
    names answer for it. Present, it names the one domain the turn reads and is
    checked exactly like a decision's own evidence; it never invents a Stage or a
    design source.
    """

    domain: Domain
    stage_ref: str | None = Field(alias="stageRef", default=None, min_length=1)
    target_refs: list[Annotated[str, Field(min_length=1, max_length=192)]] | None = Field(
        alias="targetRefs", default=None, max_length=32,
    )
    source: DecisionSourceDto | None = None


def decision_dto(revision: DecisionRevision, *, status: str | None = None) -> DecisionDto:
    """One retained revision on the wire; the payload already is the contract.

    ``status`` states what a reader of the whole chain knows and one record
    cannot: a revision that is not the tip is superseded. It overrides nothing
    retained - the payload is immutable and keeps the status it was written
    with.
    """

    payload: Mapping[str, Any] = revision.payload
    return DecisionDto(
        projectId=payload["projectId"],
        decisionId=payload["decisionId"],
        revisionRef=revision.ref,
        previousRevisionRef=payload["previousRevisionRef"],
        status=status or payload["status"],
        rawLanguage=payload["rawLanguage"],
        messageSource=payload.get("messageSource"),
        disposition=payload["disposition"],
        strength=payload["strength"],
        targetRef=payload["targetRef"],
        scope=DecisionScopeDto(**payload["scope"]),
        source=payload["source"],
        applicability=payload["applicability"],
        sourceKind=payload["sourceKind"],
        typedBinding=payload["typedBinding"],
        attribution=DecisionAttributionDto(**payload["attribution"]),
        createdAt=payload["createdAt"],
        reason=payload["reason"],
        revisionMessageSource=payload.get("revisionMessageSource"),
    )
