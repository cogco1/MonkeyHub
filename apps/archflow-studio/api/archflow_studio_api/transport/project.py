"""What the API says about the project it is bound to.

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
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from archflow.project.refs import ProjectVersionRef

from ..application.binding import ProjectBinding, ReferenceRun


class ProjectVersionDto(BaseModel):
    """One canonical project version."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    version: int
    state_sha256: str | None = Field(alias="stateSha256")


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
