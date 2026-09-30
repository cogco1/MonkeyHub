"""The project-memory wire contract (#252, ADR-009).

A memory item is how the project works - where its content is, where to look
first, which library skill a task follows - not a decision it settled. The caller states the item's kind, its
value and the user's words; the server adds what it can vouch for itself: the
item's identity and key, when it applies, the revision chain, who asked
through which surface, and whether a locator's target still resolves.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..application.memory import MemoryMatch, MemoryRevision
from .decisions import DecisionAttributionDto, MessageSourceDto

SHA256 = r"^[0-9a-f]{64}$"

MemoryKind = Literal["locator", "source_policy", "recipe"]
MemoryScope = Literal["project"]
MemoryAuthority = Literal["explicit"]
MemorySourceKind = Literal["human", "agent"]
TaskDomain = Literal["design", "drawing", "copy", "research"]
ResearchKey = Literal["materials", "regulations", "products", "precedents"]


class _Frozen(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")


class DocumentContentRefDto(_Frozen):
    """One registered document at its exact revision, and one page of it when named."""

    kind: Literal["document"]
    run_id: str = Field(alias="runId", min_length=1, max_length=128)
    asset_sha256: str = Field(alias="assetSha256", pattern=SHA256)
    revision_ref: str | None = Field(alias="revisionRef", default=None, min_length=1)
    page_index: int | None = Field(alias="pageIndex", default=None, ge=0, strict=True)


class ArtifactContentRefDto(_Frozen):
    """One retained artifact, by the sha256 its receipt certifies."""

    kind: Literal["artifact"]
    sha256: str = Field(pattern=SHA256)


class BoardContentRefDto(_Frozen):
    """One element on one exact retained board revision."""

    kind: Literal["board"]
    revision_sha256: str = Field(alias="revisionSha256", pattern=SHA256)
    element_id: str = Field(alias="elementId", min_length=1, max_length=256)


ContentRefDto = Annotated[
    Union[DocumentContentRefDto, ArtifactContentRefDto, BoardContentRefDto],
    Field(discriminator="kind"),
]


class LocatorValueRequestDto(_Frozen):
    """Where one piece of retained project content is, under the name the user calls it.

    The target must resolve now. A string target (an absolute machine path or a
    URL) is refused with its reason: register the file as a project document first.
    """

    label: str = Field(min_length=1, max_length=80, description="the user's short name for it, e.g. 项目图框")
    target: Union[ContentRefDto, Annotated[str, Field(max_length=4096)]] = Field(
        description="the retained content itself; a string is accepted only to be refused with its reason")


class LocatorValueDto(_Frozen):
    label: str
    target: ContentRefDto


class SourcePolicyValueDto(_Frozen):
    """Where to look first for a research topic, and what to avoid.

    A soft default the current request can override unless the user's words
    say it must be followed; say which sources were used.
    """

    topic: str = Field(min_length=1, max_length=200, description="the topic in the user's words")
    keys: list[ResearchKey] = Field(min_length=1, max_length=4)
    prefer: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=16, description="sources to look in first, in order: site domains or names")
    avoid: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=16, description="sources not to use: site domains or names")
    note: str | None = Field(default=None, max_length=500)


class RecipeValueDto(_Frozen):
    """Which library skill a task follows, by one exact version; the steps stay in the skill.

    A chat names the skill as the agent sees it and the Hub fills the exact
    version from the configured library.
    """

    task: str = Field(min_length=1, max_length=200,
                      description="the task in the user's words, e.g. 出平面图前检查填充")
    skill: str = Field(min_length=1, max_length=90, pattern=r"^skill:[a-z0-9]+(-[a-z0-9]+)*@[1-9][0-9]{0,8}$",
                       description="skill:<name>@<version>, one exact version of a library skill")
    note: str | None = Field(default=None, max_length=500)


class AppliesWhenRequestDto(_Frozen):
    domains: list[TaskDomain] | None = Field(
        default=None, min_length=1, max_length=4,
        description="a recipe only: the task domains the user's words indicate; the other kinds' follow from their kind")
    stage_ref: str | None = Field(
        alias="stageRef", default=None, min_length=1,
        description="an exact Stage of this project the item applies under; omitted, it applies project-wide")


class MemoryRequestDto(_Frozen):
    """One memory item, saved from the user's own words or a person's explicit action.

    'agent' names the user's message in messageSource: an agent never saves one
    as the user's on its own, and nothing is inferred from behaviour.
    """

    project_id: str = Field(alias="projectId", min_length=1)
    kind: MemoryKind = Field(description="'locator' (value {label, target}), 'source_policy' "
                             "(value {topic, keys, prefer, avoid, note}) or 'recipe' (value {task, skill, note}, "
                             "with appliesWhen.domains); preference, habit and standard are reserved")
    value: Union[LocatorValueRequestDto, SourcePolicyValueDto, RecipeValueDto]
    scope: MemoryScope = Field(default="project", description="organization, team and user are reserved for the "
                               "library project")
    applies_when: AppliesWhenRequestDto | None = Field(alias="appliesWhen", default=None)
    authority: MemoryAuthority = Field(default="explicit", description="observed and inferred are reserved")
    raw_language: str = Field(alias="rawLanguage", min_length=1, max_length=2000,
                              description="the user's own words, unedited")
    message_source: MessageSourceDto | None = Field(alias="messageSource", default=None)
    source_kind: MemorySourceKind = Field(alias="sourceKind")
    evidence_refs: list[ContentRefDto] = Field(
        alias="evidenceRefs", default_factory=list, max_length=8,
        description="retained content the words were said about, if any; the user's message alone is enough")

    @model_validator(mode="after")
    def value_of_its_kind(self) -> MemoryRequestDto:
        expected = {"locator": LocatorValueRequestDto, "source_policy": SourcePolicyValueDto,
                    "recipe": RecipeValueDto}[self.kind]
        if not isinstance(self.value, expected):
            raise ValueError("a locator's value is {label, target}; a source_policy's is "
                             "{topic, keys, prefer, avoid, note}; a recipe's is {task, skill, note}")
        return self


class AppliesWhenDto(_Frozen):
    domains: list[TaskDomain] = Field(description="the task domains it applies in; empty means every domain")
    topics: list[str]
    keys: list[ResearchKey]
    stage_ref: str | None = Field(alias="stageRef")


class MemoryProvenanceDto(_Frozen):
    raw_language: str = Field(alias="rawLanguage")
    message_source: MessageSourceDto | None = Field(alias="messageSource")
    source_kind: MemorySourceKind = Field(alias="sourceKind")
    evidence_refs: list[ContentRefDto] = Field(alias="evidenceRefs")


class MemoryDto(_Frozen):
    """One memory item at one revision, as this project retains it."""

    project_id: str = Field(alias="projectId")
    memory_id: str = Field(alias="memoryId")
    revision_ref: str = Field(alias="revisionRef")
    previous_revision_ref: str | None = Field(alias="previousRevisionRef")
    version: int
    status: Literal["active", "superseded", "revoked"] = Field(
        description="'superseded' is derived: a revision that is no longer its chain's tip")
    key: str
    kind: MemoryKind
    scope: MemoryScope
    applies_when: AppliesWhenDto = Field(alias="appliesWhen")
    value: Union[LocatorValueDto, SourcePolicyValueDto, RecipeValueDto]
    authority: MemoryAuthority
    provenance: MemoryProvenanceDto
    attribution: DecisionAttributionDto
    created_at: str = Field(alias="createdAt")
    reason: str | None = None
    revision_message_source: MessageSourceDto | None = Field(alias="revisionMessageSource", default=None)


class MemoryListDto(_Frozen):
    project_id: str = Field(alias="projectId")
    memory: list[MemoryDto]


class MemoryHistoryDto(_Frozen):
    project_id: str = Field(alias="projectId")
    memory_id: str = Field(alias="memoryId")
    revisions: list[MemoryDto]


class MemoryRevisionRequestDto(_Frozen):
    """Revoke or supersede one memory item, against the revision the caller read."""

    project_id: str = Field(alias="projectId", min_length=1)
    expected_revision_ref: str = Field(alias="expectedRevisionRef", min_length=1)
    action: Literal["revoke", "supersede"]
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    revision_message_source: MessageSourceDto | None = Field(alias="revisionMessageSource", default=None)
    replacement: MemoryRequestDto | None = None

    @model_validator(mode="after")
    def coherent_action(self) -> MemoryRevisionRequestDto:
        if (self.action == "supersede") != (self.replacement is not None):
            raise ValueError("supersede carries its replacement item; revoke carries none")
        if self.replacement is not None and self.replacement.project_id != self.project_id:
            raise ValueError("the replacement names another project")
        return self


class MemoryMatchDto(_Frozen):
    """One item a turn's words are about.

    A locator's 'stale' is never dropped: the content moved or went, and
    staleReason says how. Nothing is guessed in its place. A source policy or a
    recipe is always 'current' here; whether a recipe's skill version is still
    the library's is added by the Hub, which reads the library.
    """

    memory: MemoryDto
    status: Literal["current", "stale"]
    stale_reason: str | None = Field(alias="staleReason")
    matched_terms: list[str] = Field(alias="matchedTerms",
                                     description="the normalized terms the words shared with this item")


class MemoryLocateDto(_Frozen):
    project_id: str = Field(alias="projectId")
    query: str
    locators: list[MemoryMatchDto]


class MemoryAboutRequestDto(_Frozen):
    """A turn's words, to read the memory they are about without any design source."""

    project_id: str = Field(alias="projectId", min_length=1)
    utterance: str = Field(min_length=1, description="the user's whole message, unedited")
    stage_ref: str | None = Field(alias="stageRef", default=None, min_length=1,
                                  description="the exact Stage the turn is under, if any")
    domain: TaskDomain | None = Field(default=None, description="the one task domain the turn named, if any")


class MemoryAboutDto(_Frozen):
    project_id: str = Field(alias="projectId")
    memory: list[MemoryMatchDto] = Field(
        description="what ContextPack.memory would hand for the same words: locators first, each re-read now, "
                    "then source policies, then recipes")


def memory_dto(revision: MemoryRevision, *, status: str | None = None) -> MemoryDto:
    payload = revision.payload
    return MemoryDto(
        projectId=payload["projectId"], memoryId=payload["memoryId"], revisionRef=revision.ref,
        previousRevisionRef=payload["previousRevisionRef"], version=payload["version"],
        status=status or payload["status"], key=payload["key"], kind=payload["kind"], scope=payload["scope"],
        appliesWhen=payload["appliesWhen"], value=payload["value"], authority=payload["authority"],
        provenance=payload["provenance"], attribution=payload["attribution"], createdAt=payload["createdAt"],
        reason=payload["reason"], revisionMessageSource=payload["revisionMessageSource"],
    )


def memory_match_dto(match: MemoryMatch) -> MemoryMatchDto:
    return MemoryMatchDto(memory=memory_dto(match.revision),
                          status="current" if match.stale_reason is None else "stale",
                          staleReason=match.stale_reason, matchedTerms=list(match.matched_terms))
