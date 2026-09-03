"""What the API says about the project it is bound to.

The two references it publishes are different facts and both are needed: the
project's exact canonical ``head``, and the base the reference run was created
against. When they differ, a client can see it rather than infer it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from archflow.project.refs import ProjectVersionRef

from ..application.binding import ProjectBinding, ReferenceRun

PROJECT_SCHEMA = "ProjectBinding@1"


class HeadDto(BaseModel):
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

    schema_id: str = Field(default=PROJECT_SCHEMA, alias="schema")
    project_id: str = Field(alias="projectId")
    project_dir: str = Field(alias="projectDir")
    head: HeadDto
    reference_run: ReferenceRunDto = Field(alias="referenceRun")
    canonical_write_authority: bool = Field(
        default=False,
        alias="canonicalWriteAuthority",
    )


def head_dto(head: ProjectVersionRef) -> HeadDto:
    """Shape one ``ProjectVersionRef`` for the wire."""

    return HeadDto(version=head.version, state_sha256=head.state_sha256)


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
    head: ProjectVersionRef,
) -> ProjectBindingDto:
    """Shape the binding itself: which project, which HEAD, which run."""

    return ProjectBindingDto(
        project_id=binding.project_id,
        project_dir=str(binding.project_dir),
        head=head_dto(head),
        reference_run=reference_run_dto(reference),
    )
