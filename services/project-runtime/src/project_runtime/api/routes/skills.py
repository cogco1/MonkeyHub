"""Add, grant and run the bound project's skills: named procedures in immutable versions.

The Hub reads these routes on the library project's Runtime and hands the
current versions to the agents through their native skill mechanism. A
runnable version declares the access it needs; it runs only under a grant a
person gives on the Hub page (once, or for this version until revoked), and
the chat's tools reach neither the grant nor the revocation.
"""

from fastapi import APIRouter, Query
from fastapi.responses import Response
from starlette.requests import Request

from ...application import skill_library, skill_runs, skills
from ...authentication import request_attribution
from ...binding import bound_project
from ...errors import StudioError
from ..dto.skills import (
    SkillApprovalDto,
    SkillApprovalRequestDto,
    SkillDto,
    SkillImportRequestDto,
    SkillIndexDto,
    SkillRequestDto,
    SkillRevokeRequestDto,
    SkillRunRequestDto,
    approval_dto,
    skill_dto,
    skill_index_row,
)

router = APIRouter(tags=["skills"])


# Declared before /skills/{skill_id}: these paths are not skill ids.
@router.get("/skills/library")
def read_skill_library(request: Request) -> dict:
    """The packages shipped with MonkeyHub (bundled) and the user's own (user), and what this project holds of each."""

    return skill_library.library(bound_project(request.app.state))


@router.post("/skills/import")
def import_skill(request: Request, payload: SkillImportRequestDto) -> dict:
    """Register one package as this project's skill; an unchanged package adds no version. It grants nothing."""

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The import names another project.")
    row, imported = skill_library.import_package(binding, payload.source, payload.name,
                                                 attribution=request_attribution(request))
    latest = skills.latest_version(binding, row.skill_id)
    return {"imported": imported, "skill": skill_dto(row, latest.version, list(skills.approvals(binding, row.skill_id)))
            .model_dump(by_alias=True)}


@router.get("/skills/approvals")
def read_skill_grants(request: Request) -> dict:
    """Every grant of the project with its status: active, used (a once grant a run took) or revoked."""

    binding = bound_project(request.app.state)
    rows = skills.grant_rows(binding, skill_runs.used_once_grants(binding))
    return {"projectId": binding.project_id, "approvals": [approval_dto(row).model_dump(by_alias=True) for row in rows]}


@router.post("/skills/approvals/{approval_id}/revoke")
def revoke_skill_grant(request: Request, approval_id: str, payload: SkillRevokeRequestDto) -> dict:
    """A person withdraws a grant on the Hub page; later runs cannot use it. The chat's tools cannot reach this."""

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The revocation names another project.")
    row = skills.revoke_approval(binding, approval_id, attribution=request_attribution(request))
    return {"approvalId": row["approvalId"], "revokedAt": row["revokedAt"], "revokedBy": row["revokedBy"]}


@router.get("/skills/runs/{run_id}")
def read_skill_run(request: Request, run_id: str) -> dict:
    """One run's latest record: queued, running, succeeded or failed, with what it used and did."""

    return skill_runs.run_report(bound_project(request.app.state), run_id)


@router.get("/skills/runs/{run_id}/outputs/{name:path}", response_class=Response)
def read_skill_run_output(request: Request, run_id: str, name: str) -> Response:
    """One retained output's bytes, verified against the digest its run recorded."""

    data, media_type = skill_runs.output_bytes(bound_project(request.app.state), run_id, name)
    return Response(data, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.get("/skills", response_model=SkillIndexDto, response_model_by_alias=True)
def read_skill_index(request: Request) -> SkillIndexDto:
    """Every skill's current version: id, version, name and description, and no body."""

    binding = bound_project(request.app.state)
    retained = list(skills.approvals(binding))
    return SkillIndexDto(projectId=binding.project_id,
                         skills=[skill_index_row(row, retained) for row in skills.list_skills(binding)])


@router.get("/skills/{skill_id}", response_model=SkillDto, response_model_by_alias=True)
def read_skill(request: Request, skill_id: str,
               version: int | None = Query(default=None, ge=1)) -> SkillDto:
    """One skill with its body: the latest version, or the exact ``version`` named."""

    binding = bound_project(request.app.state)
    row, latest = skills.read_skill(binding, skill_id, version)
    return skill_dto(row, latest, list(skills.approvals(binding, skill_id)))


@router.post("/skills", response_model=SkillDto, response_model_by_alias=True, status_code=201)
def add_skill(request: Request, payload: SkillRequestDto) -> SkillDto:
    """Retain a new skill, or a version superseding the one the caller read.

    The old version stays readable. Retaining a skill grants no permission;
    its declared ``permissions`` are for a reader and are never enforced.
    """

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The skill names another project.")
    row = skills.add_skill(
        binding, name=payload.name, description=payload.description, body=payload.body,
        manifest=None if payload.manifest is None else payload.manifest.model_dump(),
        supersedes_version=payload.supersedes_version, attribution=request_attribution(request),
        files=payload.files, entry=payload.entry,
        access=None if payload.access is None else payload.access.model_dump(),
    )
    return skill_dto(row, row.version, [])


@router.post("/skills/{skill_id}/approvals", response_model=SkillApprovalDto, response_model_by_alias=True,
             status_code=201)
def approve_skill(request: Request, skill_id: str, payload: SkillApprovalRequestDto) -> SkillApprovalDto:
    """A person approves one version's exact code (codeSha256) so it may run.

    The Hub page sends this when the architect presses Approve; the chat's
    tools cannot reach it. Code that differs from the hash shown is refused.
    """

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The approval names another project.")
    return approval_dto(skills.approve_skill(binding, skill_id, version=payload.version, code=payload.code_sha256,
                                             method=payload.method, scope=payload.scope,
                                             attribution=request_attribution(request)))


@router.post("/skills/{skill_id}/run", status_code=202)
def run_skill(request: Request, skill_id: str, payload: SkillRunRequestDto) -> dict:
    """Start a granted version on project inputs as a background job; read it at statusPath.

    Refused before anything runs with SKILL_NOT_APPROVED, SKILL_CODE_CHANGED or
    SKILL_NOT_RUNNABLE. A run the code makes beyond its grant fails with
    SKILL_PERMISSION_NEEDED and names what it needed.
    """

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The run names another project.")
    return skill_runs.submit_skill_run(
        binding, request.app.state.jobs, skill_id, version=payload.version,
        inputs={name: ref.model_dump(by_alias=True, exclude_none=True) for name, ref in payload.inputs.items()},
        parameters=payload.parameters, attribution=request_attribution(request), agent=payload.agent_run,
        grant_id=payload.grant_id,
    )


@router.get("/skills/{skill_id}/runs")
def read_skill_runs(request: Request, skill_id: str) -> dict:
    """Every retained run of one skill, oldest first, with its inputs, outputs and digests."""

    binding = bound_project(request.app.state)
    skills.skill_versions(binding, skill_id)
    return {"projectId": binding.project_id, "runs": list(skill_runs.list_skill_runs(binding, skill_id))}
