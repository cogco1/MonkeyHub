"""The wire form of a skill: a named procedure and its immutable versions.

A skill grants nothing. Its manifest's ``permissions`` are what the procedure
says it needs, declared for a reader; nothing enforces them, and following the
skill never widens what an agent may do.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from ...application.skills import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, SkillVersion

_Item = Annotated[str, Field(min_length=1, max_length=200)]


class _Frozen(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")


class SkillManifestDto(_Frozen):
    """What a procedure reads, produces and says it needs; declared, never enforced."""

    inputs: list[_Item] = Field(default_factory=list, max_length=32)
    outputs: list[_Item] = Field(default_factory=list, max_length=32)
    permissions: list[_Item] = Field(default_factory=list, max_length=32)


class SkillRequestDto(_Frozen):
    """One new version. A new skill names no version; a successor names the one it supersedes."""

    project_id: str = Field(alias="projectId", min_length=1)
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    body: str = Field(min_length=1)
    manifest: SkillManifestDto | None = None
    supersedes_version: int | None = Field(alias="supersedesVersion", default=None, ge=1, strict=True)


class SkillAttributionDto(_Frozen):
    actor_id: str = Field(alias="actorId")
    authenticated: bool
    origin: str


class SkillIndexRowDto(_Frozen):
    """What an agent sees before it uses a skill: no body."""

    id: str
    version: int
    name: str
    description: str


class SkillIndexDto(_Frozen):
    project_id: str = Field(alias="projectId")
    skills: list[SkillIndexRowDto]


class SkillDto(_Frozen):
    """One exact version, with its body, and the version that is current now."""

    project_id: str = Field(alias="projectId")
    id: str
    version: int
    latest_version: int = Field(alias="latestVersion")
    name: str
    description: str
    body: str
    manifest: SkillManifestDto | None
    created_at: str = Field(alias="createdAt")
    attribution: SkillAttributionDto


def skill_index_row(row: SkillVersion) -> SkillIndexRowDto:
    payload = row.payload
    return SkillIndexRowDto(id=row.skill_id, version=row.version, name=payload["name"],
                            description=payload["description"])


def skill_dto(row: SkillVersion, latest_version: int) -> SkillDto:
    payload: dict[str, Any] = dict(row.payload)
    return SkillDto(
        projectId=payload["projectId"], id=row.skill_id, version=row.version, latestVersion=latest_version,
        name=payload["name"], description=payload["description"], body=payload["body"],
        manifest=payload.get("manifest"), createdAt=payload["createdAt"], attribution=payload["attribution"],
    )
