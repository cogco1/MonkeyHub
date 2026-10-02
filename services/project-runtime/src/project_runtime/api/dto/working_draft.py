"""Current working position, explicit saves and unexecuted local recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...application.working_draft import LocalDraftInputDto, LocalDraftSourceDto
from .artifacts import ModelSourceDto, model_source_dto
from .decisions import MessageSourceDto

if TYPE_CHECKING:
    from ...application.working_draft import WorkingSource


class WorkingDraftSelectionDto(BaseModel):
    """Continue on a run (the Working Head follows it at once), or return to the default with none.

    Who moved the head is read from the request boundary. The two optional fields
    mark the Hub Agent continuing on the user's own words, as the Hub binds them.
    """

    model_config = ConfigDict(extra="forbid")
    projectId: str
    baseRevisionSha256: str | None
    runId: str | None
    branchId: str | None = None
    messageSource: MessageSourceDto | None = Field(
        default=None,
        description="The chat message the Hub Agent continued on, as the Hub binds it; provenance, never a "
        "credential. Only a Hub-managed Runtime takes it, and the architect's own Continue has none.")
    rawLanguage: str | None = Field(
        default=None, min_length=1, max_length=2000,
        description="The user's own words in that message that ask for this Continue; required with "
        "messageSource. The retained design.continued event names the message by id, not these words.")


class WorkingDraftSaveDto(BaseModel):
    """Name a run as a version. Every name is listed and shown; only one the person saved keeps its run (#575)."""

    model_config = ConfigDict(extra="forbid")
    projectId: str
    baseRevisionSha256: str | None
    runId: str = Field(min_length=1)
    label: str | None = Field(default=None, max_length=200)
    savedBy: Literal["person"] | None = Field(
        default=None,
        description="\"person\" only when the person saves this name in the Hub (the history panel's save); the "
        "run's working row then records that the person saved it, and only such a name keeps a superseded draft out of "
        "the project trash. A name saved without it, by an agent or any other caller, is listed and shown the same "
        "but keeps nothing. The caller's own statement, never a credential: agents leave it out.")


class LocalDraftRequestDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str
    baseRevisionSha256: str | None
    draft: LocalDraftInputDto | None
    expectedSource: LocalDraftSourceDto | None = None


class WorkingHeadDto(BaseModel):
    """The project's current valid working state; ordinary work follows it."""

    runId: str
    stateDigest: str
    recordDigest: str
    sourceStageRef: str | None = Field(default=None, description="The accepted Stage this state is, or continues from.")
    branchId: str | None = None
    accepted: bool = Field(description="True only when the head run is exactly the accepted Stage's model run.")
    origin: Literal["working-position", "branch-head", "reference"] = Field(
        description="Which retained fact answered: the saved working position, the main line's accepted head, or the reference run.")
    label: str | None = None
    modelSource: ModelSourceDto | None = None
    lineage: list[str] = Field(description="The head run first, then each exact retained source it continued.")


class WorkingRevisionDto(BaseModel):
    """Only the working position's revision: a cheap check for whether the head may have moved."""

    projectId: str
    revisionSha256: str | None = None


class WorkingSourceDto(BaseModel):
    """One workspace's current source under a LIVE or FROZEN policy."""

    projectId: str
    workspace: Literal["modeling", "drawing", "render", "board"]
    policy: Literal["live", "frozen"]
    revisionSha256: str | None = Field(default=None, description="The working position revision; it changes whenever the head moves.")
    head: WorkingHeadDto | None = None
    compatible: bool
    source: ModelSourceDto | None = Field(default=None, description="The exact model this workspace should use.")
    stageRef: str | None = Field(default=None, description="Set only when source is exactly an accepted Stage's pinned model.")
    reason: str | None = Field(default=None, description="Why the workspace cannot follow the head, or why a frozen pin is no longer current.")
    warnings: list[str] = Field(default_factory=list)


class RepresentationStatusDto(BaseModel):
    """One exact registered page's representation status (#223), in the projection's own words.

    Derived on every read by asking the page's owner, as the Worktree Graph and
    Publish read it; nothing is stored.
    """

    projectId: str
    state: Literal["current", "outdated", "frozen", "unavailable"] = Field(
        description="current: the page shows what its inputs say now; outdated: a newer registered page "
        "replaces it, or an input it read changed; frozen: a person keeps it on a chosen version; "
        "unavailable: the page, or an exact input it was made from, can no longer be read or verified.")
    reason: str | None = Field(default=None, description="The answering owner's own detail, for display on request.")


def working_head_dto(head) -> WorkingHeadDto | None:
    return None if head is None else WorkingHeadDto(
        runId=head.run_id, stateDigest=head.state_digest, recordDigest=head.record_digest,
        sourceStageRef=head.source_stage_ref, branchId=head.branch_id, accepted=head.accepted, origin=head.origin,
        label=head.label, modelSource=model_source_dto(head.model_source), lineage=list(head.lineage))


def working_source_dto(value: WorkingSource) -> WorkingSourceDto:
    return WorkingSourceDto(
        projectId=value.project_id, workspace=value.workspace, policy=value.policy, revisionSha256=value.revision_sha256,
        head=working_head_dto(value.head), compatible=value.compatible, source=model_source_dto(value.source),
        stageRef=value.stage_ref, reason=value.reason, warnings=list(value.warnings))
