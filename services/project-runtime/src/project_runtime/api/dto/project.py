"""What the API says about the project it is bound to, and about the list of
projects it binds at all.

The two references it publishes are different facts and both are needed: the
project's exact **published** version - the design that has been issued - and
the base the reference run was created against. When they differ, a client can
see it rather than infer it.

``ProjectVersionDto`` is one canonical project version and nothing more, which
is why it is not called after the published position: the same shape carries a
run's base and a validation's checked state, neither of which is published.
The repository file is still named ``HEAD`` and ``read_head`` still reads it -
the format owns those names (ADR-004, ADR-007) - but nothing a client reads
says the word.

``ProjectSummaryDto`` is deliberately thinner than ``ProjectBindingDto``: a
listing says which projects exist and which one the unscoped paths mean, and it
carries no filesystem path. Where this server keeps a project on disk is an
operator's question, answered by ``projectDir`` on the binding itself.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from archflow.project.refs import ProjectVersionRef

from ...binding import ProjectBinding, ReferenceRun


class ProjectVersionDto(BaseModel):
    """One canonical project version."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    version: int
    state_sha256: str | None = Field(alias="stateSha256")


class ModelingInitializeRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)


class ProjectRefreshRequestDto(BaseModel):
    """Read the project named here again (``POST /api/project/refresh``)."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)


# The most places a refresh answer names; ``movedCount`` says how many moved.
REFRESH_MOVED_LIMIT = 100


class ProjectRefreshDto(BaseModel):
    """What reading the project again found (ADR-012): whether its folder changed outside MonkeyHub."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    changed_outside: bool = Field(
        alias="changedOutside",
        description="The project folder changed outside this runtime since it was last read: by hand, by a sync, "
                    "by a restore or by an agent's command. What moved has been read again.")
    moved: list[str] = Field(
        description=f"The first {REFRESH_MOVED_LIMIT} places that moved, as project-relative paths ('.' for the "
                    "project folder): directories and the pointer files (project.json, HEAD, design/branches.json, "
                    "design/working.json). For diagnostics: a file other than a pointer file rewritten in place "
                    "moves no place.")
    moved_count: int = Field(alias="movedCount", description="How many places moved in all.")


class ModelingInitializeDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    initialized: bool = Field(description="Initial modeling inputs were installed; no geometry, run or issued version was created.")


class ModelingLevelDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    level_id: str = Field(alias="levelId")
    elevation: float


class ModelingComponentDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    component_id: str = Field(alias="componentId")
    parent_component_id: str | None = Field(alias="parentComponentId")


class ModelingBaseDto(ModelingInitializeDto):
    """``POST /api/project/modeling?base=true``: the prepared action plus the base a first proposal writes against.

    The same default state ``GET /api/state`` would answer right after, cut to
    what a first sketch needs, so preparing and reading are one request.
    """

    state_digest: str | None = Field(
        alias="stateDigest",
        description="The default state's stateDigest; send it as the first proposal's stateDigest. "
        "Null when the kernel refused to view the record (read GET /api/state for why).")
    source_stage_ref: str | None = Field(
        alias="sourceStageRef", description="Send it unchanged on writes when not null.")
    levels: list[ModelingLevelDto] = Field(description="Levels a baseLevel can name, elevations in metres.")
    components: list[ModelingComponentDto] | None = Field(
        description="Existing components; a new component names one as parentComponentId. "
        "Null when the component tree could not be resolved.")
    element_count: int = Field(
        alias="elementCount",
        description="Elements already in the default state; 0 for a project with no model yet. "
        "Before editing existing elements, read GET /api/state?authored=true.")


class ReferenceRunDto(BaseModel):
    """The run a projection answers for, with the base it was created against."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    run_id: str = Field(alias="runId")
    base_version: int = Field(alias="baseVersion")
    base_sha256: str | None = Field(alias="baseSha256")


class ProjectBindingDto(BaseModel):
    """The wire form of ``GET /api/project``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    project_dir: str = Field(alias="projectDir")
    published: ProjectVersionDto = Field(
        description="the issued design: the one version this project publishes",
    )
    reference_run: ReferenceRunDto = Field(alias="referenceRun")
    intent_provider: str = Field(
        alias="intentProvider",
        description="who reads a sentence at POST /api/intents in this process: "
        "deterministic (the grammar alone), codex, anthropic, or unknown when "
        "the configured compiler does not say",
    )
    intent_model: str | None = Field(
        alias="intentModel",
        description="the model that provider runs, when it names one",
    )


class ProjectSummaryDto(BaseModel):
    """One project a server binds, as a listing row."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    name: str = Field(
        description="what a person calls this project. P036 gives a project "
        "no display name, so this server sends the project id; a server that "
        "has one sends that instead",
    )
    is_default: bool = Field(
        alias="isDefault",
        description="whether the unscoped paths (/api/project, /api/state, …) "
        "answer for this project",
    )


class ProjectListDto(BaseModel):
    """The wire form of ``GET /api/projects``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    projects: list[ProjectSummaryDto]


def project_list_dto(binding: ProjectBinding) -> ProjectListDto:
    """Every project this server binds: today exactly one, and it is the default."""

    return ProjectListDto(
        projects=[
            ProjectSummaryDto(
                project_id=binding.project_id,
                name=binding.project_id,
                is_default=True,
            )
        ]
    )


def project_version_dto(version: ProjectVersionRef) -> ProjectVersionDto:
    """Shape one ``ProjectVersionRef`` for the wire."""

    return ProjectVersionDto(
        version=version.version,
        state_sha256=version.state_sha256,
    )


def reference_run_dto(reference: ReferenceRun) -> ReferenceRunDto:
    """Shape the chosen run for the wire."""

    return ReferenceRunDto(
        run_id=reference.run.run_id,
        base_version=reference.run.base.version,
        base_sha256=reference.run.base.state_sha256,
    )


def project_binding_dto(
    binding: ProjectBinding,
    reference: ReferenceRun,
    published: ProjectVersionRef,
    *,
    intent_compiler: object,
) -> ProjectBindingDto:
    """Shape the binding itself: which project, which published design, which
    run - and who reads sentences here, so the screen never claims an agent
    that is not wired."""

    provider = getattr(intent_compiler, "provider", None)
    model = getattr(intent_compiler, "model", None)
    return ProjectBindingDto(
        project_id=binding.project_id,
        project_dir=str(binding.project_dir),
        published=project_version_dto(published),
        reference_run=reference_run_dto(reference),
        intent_provider=provider if isinstance(provider, str) else "unknown",
        intent_model=model if isinstance(model, str) else None,
    )
