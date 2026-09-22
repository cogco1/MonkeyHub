"""The wire form of one scoped project decision.

Everything a decision claims is stated, never inferred: what was said, what
kind of judgement it is, how strongly it is held, what it is about, how far it
reaches, and the exact evidence it was said against. The server adds only what
it can vouch for itself — the decision's identity, its revision chain, who
asked for it through which surface, and the parameter value it read.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..application.decisions import DecisionRevision

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


DecisionSourceDto = Annotated[
    Union[BoardDecisionSourceDto, DocumentDecisionSourceDto, DesignDecisionSourceDto],
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


class TypedBindingRequestDto(_Frozen):
    """The parameter a decision is bound to; the server reads its value itself."""

    kind: Literal["parameter"]
    parameter_key: str = Field(alias="parameterKey", min_length=1, max_length=128)


class DecisionTypedBindingDto(_Frozen):
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
    typed_binding: TypedBindingRequestDto | None = Field(alias="typedBinding", default=None)


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
    source: DecisionSourceDto
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

    Absent, the design domain, the projection's own Stage and the focus the
    request already names answer for it. Present, it is checked exactly like a
    decision's own evidence; it never invents a Stage or a design source.
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
        typedBinding=(None if payload["typedBinding"] is None
                      else DecisionTypedBindingDto(**payload["typedBinding"])),
        attribution=DecisionAttributionDto(**payload["attribution"]),
        createdAt=payload["createdAt"],
        reason=payload["reason"],
        revisionMessageSource=payload.get("revisionMessageSource"),
    )
