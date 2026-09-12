"""The wire shapes of the capability index, its description and its run.

The run request carries the same fields ``ProposalRequestDto`` does, with one
addition: ``keep`` as a list of refs rather than a clause inside the sentence.
It is not a second parameter schema — the route merges the list into the
utterance through the grammar's own ``merge_keep`` and hands the result to the
existing proposal route, which validates it exactly as it validates a typed
one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..application.capability import Description, EditableField, KeepScope, summary
from .proposal import ProposalRequestDto


class CapabilitySummaryDto(BaseModel):
    """One line of the index: enough to choose, not the whole entry."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    capability_id: str = Field(alias="capabilityId")
    owner: str | None = None
    kind: str | None = None
    status: str | None = Field(
        default=None,
        description="how far the capability is written down and implemented; "
        "it never states whether the bound project can run it now",
    )
    purpose: str | None = None
    purpose_zh: str | None = Field(default=None, alias="purposeZh")
    goals: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    matched: list[str] = Field(
        default_factory=list,
        description="the written-down words of this entry that the goal "
        "contained; empty when the whole index was read without a goal",
    )


class CapabilityIndexDto(BaseModel):
    """Every registered capability, or the ones a goal matched."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    capabilities: list[CapabilitySummaryDto]
    match_count: int = Field(alias="matchCount")
    registered: int = Field(
        description="how many capabilities are written down in total, so that "
        "an empty match reads as the size of the index and not as a verdict",
    )
    note: str | None = Field(
        default=None,
        description="present only when nothing matched: what this index is, "
        "and why no match is not a claim that the system cannot do it",
    )


class EditableFieldDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    element_id: str = Field(alias="elementId")
    component_id: str = Field(
        alias="componentId",
        description="the component this element belongs to; send this as "
        "targetComponentId, which is not always the component that was asked "
        "about when a parent was described",
    )
    field: str
    value: int | float
    unit: str | None
    status: str = Field(description="editable, derived or locked, as the catalog decided")
    source: str
    capability_id: str = Field(alias="capabilityId")
    utterance: str = Field(
        description="this field written in the intent grammar, holding the "
        "value it already has: a shape to edit, not a proposed change",
    )


class CapabilityTargetDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    component_id: str = Field(alias="componentId")
    element_id: str | None = Field(alias="elementId")
    editable: list[EditableFieldDto]
    not_editable: list[EditableFieldDto] = Field(alias="notEditable")


class KeepScopeDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    accepted: list[str]
    remaining: int
    note: str


class CapabilitySourceDto(BaseModel):
    """The exact base a description was read from."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    state_digest: str | None = Field(alias="stateDigest")
    exact_source: bool = Field(alias="exactSource")
    actionable: bool = Field(
        description="whether this state can be proposed against now; distinct "
        "from the capability's written-down status",
    )
    source_stage_ref: str | None = Field(
        alias="sourceStageRef",
        default=None,
        description="the Stage this description was read against, when one was "
        "selected; it travels into the request below so the run is made from "
        "the source that was read",
    )
    read_with: str = Field(
        alias="readWith",
        description="how to read this same base again: a read selects a "
        "retained run with the ?run= query parameter",
    )
    write_with: str = Field(
        alias="writeWith",
        description="how to write against this same base: a write names the "
        "run as sourceRunId in the body, never as a query parameter",
    )


class CapabilityDetailDto(BaseModel):
    """One capability, and what it means for the project that is bound."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    capability: dict[str, Any] = Field(
        description="the registered entry, verbatim: goals, requires, "
        "entrypoints, composes, produces, validators, works and missing",
    )
    source: CapabilitySourceDto
    target: CapabilityTargetDto | None = None
    keep: KeepScopeDto | None = None
    request: dict[str, Any] | None = Field(
        default=None,
        description="the next request with this project's real base already "
        "in it; null when the target has no number this capability can move",
    )
    honesty: list[str] = Field(default_factory=list)


class CapabilityRunRequestDto(ProposalRequestDto):
    """A proposal request, plus the keep list written as a list.

    Every other field *is* ``ProposalRequestDto``'s — inherited, not copied, so
    there is one definition of ``stateDigest``, ``targetComponentId``,
    ``elementId``, ``utterance``, ``projectId``, ``sourceRunId`` and
    ``sourceStageRef`` and no second schema to keep in step. ``keep`` is the
    one addition: the same protected refs the grammar takes as a ``keep``
    clause, as a list a client can build without writing a sentence.
    """

    keep: list[str] = Field(
        default_factory=list,
        description="refs this change must not disturb, as entity:<id> or "
        "parameter:<key>; a change that reaches one comes back as a conflict "
        "and is not run",
    )

    def proposal_request(self, utterance: str) -> ProposalRequestDto:
        """This request as the existing proposal route takes it.

        The keep list has already been merged into ``utterance`` by the caller
        through the grammar's own ``merge_keep``; what leaves here is exactly
        the existing DTO, validated by its own rules.
        """

        return ProposalRequestDto.model_validate(
            {**self.model_dump(by_alias=True, exclude={"keep"}), "utterance": utterance}
        )


class CapabilityRunDto(BaseModel):
    """What one capability run started, and where to read the result."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    capability_id: str = Field(alias="capabilityId")
    proposal_id: str = Field(alias="proposalId")
    job_id: str = Field(alias="jobId")
    candidate_id: str = Field(alias="candidateId")
    status: Literal["queued"] = Field(
        default="queued",
        description="the run was accepted onto the existing candidate queue; "
        "it is not accepted as the project's design and issues nothing",
    )
    target: dict[str, Any]
    change: dict[str, Any]
    kept: list[str]
    next: list[str] = Field(
        description="the existing reads that finish this: poll the job, then "
        "read the candidate and compare it against the run it came from",
    )


def index_dto(entries, evidence=(), *, registered: int, note: str | None) -> CapabilityIndexDto:
    rows = summary(entries)
    matched = [list(item[1]) for item in evidence] + [[]] * max(0, len(rows) - len(evidence))
    return CapabilityIndexDto(
        capabilities=[CapabilitySummaryDto(**{
            "capabilityId": row["capability_id"],
            "owner": row["owner"],
            "kind": row["kind"],
            "status": row["status"],
            "purpose": row["purpose"],
            "purposeZh": row["purpose_zh"],
            "goals": list(row["goals"]),
            "entrypoints": list(row["entrypoints"]),
            "matched": matched[index],
        }) for index, row in enumerate(rows)],
        match_count=len(rows),
        registered=registered,
        note=note,
    )


def _field_dto(item: EditableField) -> EditableFieldDto:
    return EditableFieldDto(
        element_id=item.element_id,
        component_id=item.component_id,
        field=item.field,
        value=item.value,
        unit=item.unit,
        status=item.status,
        source=item.source,
        capability_id=item.capability_id,
        utterance=item.utterance,
    )


def _keep_dto(keep: KeepScope) -> KeepScopeDto:
    return KeepScopeDto(accepted=list(keep.accepted), remaining=keep.remaining, note=keep.note)


def detail_dto(description: Description) -> CapabilityDetailDto:
    target = description.target
    return CapabilityDetailDto(
        capability=dict(description.entry),
        source=CapabilitySourceDto(
            project_id=description.project_id,
            run_id=description.run_id,
            state_digest=description.state_digest,
            exact_source=description.exact_source,
            actionable=description.actionable,
            source_stage_ref=description.source_stage_ref,
            read_with=description.read_with,
            write_with=description.write_with,
        ),
        target=None if target is None else CapabilityTargetDto(
            component_id=target.component_id,
            element_id=target.element_id,
            editable=[_field_dto(item) for item in target.editable],
            not_editable=[_field_dto(item) for item in target.not_editable],
        ),
        keep=None if description.keep is None else _keep_dto(description.keep),
        request=None if description.request is None else dict(description.request),
        honesty=list(description.honesty),
    )
