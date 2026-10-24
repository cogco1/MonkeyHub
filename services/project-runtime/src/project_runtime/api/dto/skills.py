"""The wire form of a skill: a named procedure and its immutable versions.

A skill grants nothing. Its manifest's ``permissions`` are what the procedure
says it needs, declared for a reader; nothing enforces them, and following the
skill never widens what an agent may do.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

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


class SkillAccessDto(_Frozen):
    """What a runnable version needs beyond its own folders: what a person allows in the Hub before it runs."""

    programs: list[str] = Field(default_factory=list, max_length=64,
                                description="programs it starts, e.g. blender, rhino; * for any")
    network: bool = Field(default=False, description="whether it connects to other hosts")
    ssh: list[str] = Field(default_factory=list, max_length=64, description="ssh/scp hosts, e.g. vast-gpu; * for any")
    read: list[str] = Field(default_factory=list, max_length=64, description="absolute folders it reads; * for any")
    write: list[str] = Field(default_factory=list, max_length=64,
                             description="absolute folders it writes outside its run; * for any")
    packages: list[str] = Field(default_factory=list, max_length=64,
                                description="Python requirements installed into the skill's own folder")


class SkillRequestDto(_Frozen):
    """One new version. A new skill names no version; a successor names the one it supersedes."""

    project_id: str = Field(alias="projectId", min_length=1)
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    body: str = Field(min_length=1)
    manifest: SkillManifestDto | None = None
    supersedes_version: int | None = Field(alias="supersedesVersion", default=None, ge=1, strict=True)
    files: dict[str, str] | None = Field(
        default=None, description="script files by relative path, e.g. scripts/run.py; text only")
    entry: str | None = Field(default=None, description="the file to run; makes the version runnable")
    access: SkillAccessDto | None = Field(default=None, description="what the version needs; part of its codeSha256")


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
    code_sha256: str = Field(alias="codeSha256")
    runnable: bool
    access: dict[str, Any] | None = None


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
    entry: str | None = None
    files: dict[str, str] | None = None
    code_sha256: str = Field(alias="codeSha256")
    approvals: list["SkillApprovalDto"] = Field(description="this version's approvals from before S-1, kept as records")
    access: dict[str, Any] | None = None


class SkillApprovalDto(_Frozen):
    approval_id: str | None = Field(alias="approvalId", default=None)
    skill_id: str = Field(alias="skillId")
    version: int
    code_sha256: str = Field(alias="codeSha256")
    scope: str = "version"
    access: dict[str, Any] | None = None
    method: str
    approved_at: str = Field(alias="approvedAt")
    approved_by: SkillAttributionDto = Field(alias="approvedBy")
    status: str | None = None
    revoked_at: str | None = Field(alias="revokedAt", default=None)


class SkillInputRefDto(_Frozen):
    """A registered document (runId, assetSha256), a 3dm model asset (modelSha256), or a drawing
    (kind "drawing" with runId, assetSha256 and revisionRef: its SVG and registration are given)."""

    kind: Literal["document", "model", "drawing", "file"] | None = None
    path: str | None = Field(default=None, description='kind "file": an absolute path of a file on this machine')
    revision_ref: str | None = Field(alias="revisionRef", default=None)
    run_id: str | None = Field(alias="runId", default=None, min_length=1)
    asset_sha256: str | None = Field(alias="assetSha256", default=None, pattern=r"^[0-9a-f]{64}$")
    model_sha256: str | None = Field(alias="modelSha256", default=None, pattern=r"^[0-9a-f]{64}$")


class SkillRunRequestDto(_Frozen):
    """Run the installed skill: its inputs are registered documents, its parameters one JSON object."""

    project_id: str = Field(alias="projectId", min_length=1)
    version: int | None = Field(default=None, ge=1, strict=True,
                                description="the project's version the caller read; refused when the folder now differs")
    inputs: dict[str, SkillInputRefDto] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    agent_run: bool = Field(alias="agentRun", default=False, description="set by the Hub for a chat's run")
    grant_id: str | None = Field(alias="grantId", default=None,
                                 description="set by the Hub: the one-run grant a person gave for this run")


def skill_index_row(row: SkillVersion) -> SkillIndexRowDto:
    payload = row.payload
    return SkillIndexRowDto(id=row.skill_id, version=row.version, name=payload["name"],
                            description=payload["description"], codeSha256=row.code_sha256,
                            runnable=row.entry is not None, access=row.access)


def approval_dto(payload) -> SkillApprovalDto:
    return SkillApprovalDto(approvalId=payload.get("approvalId"), skillId=payload["skillId"], version=payload["version"],
                            codeSha256=payload["codeSha256"], scope=payload.get("scope") or "version",
                            access=payload.get("access"), method=payload["method"], approvedAt=payload["approvedAt"],
                            approvedBy=payload["approvedBy"], status=payload.get("status"),
                            revokedAt=payload.get("revokedAt"))


def skill_dto(row: SkillVersion, latest_version: int, retained: list) -> SkillDto:
    payload: dict[str, Any] = dict(row.payload)
    mine = [item for item in retained if item.get("version") == row.version]
    return SkillDto(
        projectId=payload["projectId"], id=row.skill_id, version=row.version, latestVersion=latest_version,
        name=payload["name"], description=payload["description"], body=payload["body"],
        manifest=payload.get("manifest"), createdAt=payload["createdAt"], attribution=payload["attribution"],
        entry=row.entry, files=dict(row.files) if row.files else None, codeSha256=row.code_sha256,
        approvals=[approval_dto(item) for item in mine], access=row.access,
    )


SkillDto.model_rebuild()
