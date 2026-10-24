"""The skills installed on this machine, run on the bound project, and the versions it retains.

A skill is a folder in the skill folders; every project can run it, and the
project retains the exact version each run used. What a runnable skill
declares is allowed by a person in the Hub, for every project (S-1); these
routes read those grants and never write one. ``GET /skills/approvals`` lists
the project's own approvals from before S-1, which the Hub reads once to carry
them over.
"""

from fastapi import APIRouter, Query
from fastapi.responses import Response
from starlette.requests import Request

from ...application import skill_library, skill_runs, skills
from ...authentication import request_attribution
from ...binding import bound_project
from ...errors import StudioError
from ..dto.skills import (
    SkillDto,
    SkillIndexDto,
    SkillRequestDto,
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


@router.get("/skills/approvals")
def read_skill_grants(request: Request) -> dict:
    """The project's approvals from before S-1 with their status: active, used (a once grant a run took) or revoked."""

    binding = bound_project(request.app.state)
    rows = skills.grant_rows(binding, skill_runs.used_once_grants(binding))
    return {"projectId": binding.project_id, "approvals": [approval_dto(row).model_dump(by_alias=True) for row in rows]}


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
    return SkillIndexDto(projectId=binding.project_id, skills=[skill_index_row(row) for row in skills.list_skills(binding)])


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

    The old version stays readable. Retaining a skill allows nothing; what a
    run may do is a person's to allow in the Hub.
    """

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The skill names another project.")
    row = skills.add_skill(
        binding, name=payload.name, description=payload.description, body=payload.body,
        manifest=None if payload.manifest is None else payload.manifest.model_dump(by_alias=True),
        supersedes_version=payload.supersedes_version, attribution=request_attribution(request),
        files=payload.files, entry=payload.entry,
        access=None if payload.access is None else payload.access.model_dump(),
    )
    return skill_dto(row, row.version, [])


@router.post("/skills/{skill_id}/run", status_code=202)
def run_skill(request: Request, skill_id: str, payload: SkillRunRequestDto) -> dict:
    """Start the installed skill on project inputs as a background job; read it at statusPath.

    Refused before anything runs with SKILL_NOT_INSTALLED, SKILL_NOT_RUNNABLE,
    SKILL_CHANGED, SKILL_ACCESS_NEEDED (what a person still has to allow) or
    SKILL_REPEATED_FAILURE (an Agent's exact request that just failed twice) or
    SKILL_INPUT_MISSING (an input the skill's manifest requires, ``requiredInputs``,
    that the request did not give; nothing is asked of the person first). A
    run that tries what it did not declare fails with SKILL_PERMISSION_NEEDED
    and names what it tried; one that writes no file fails with
    SKILL_OUTPUT_MISSING; one that lacks an input its manifest names fails with
    SKILL_INPUT_MISSING.

    Every refusal and every failed run says what to do next (``Next (fix_input |
    ask_person | stop)`` in the refusal, ``failureNext`` and ``failureNextWords``
    in the run): change the request, tell the person, or stop and say what
    happened; never run the same request again. A run that succeeded carries
    ``verification``: ``passed`` or ``failed`` against the files its manifest says
    it writes (``missing`` names the ones that are not there; the outputs it did
    write are kept) or ``inconclusive`` when the manifest names none.
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


@router.get("/skills/{skill_id}/needs")
def read_skill_needs(request: Request, skill_id: str) -> dict:
    """What a run of the installed skill would need a person to allow now; nothing runs and nothing is retained."""

    return skill_runs.run_needs(bound_project(request.app.state), skill_id)


@router.get("/skills/{skill_id}/runs")
def read_skill_runs(request: Request, skill_id: str) -> dict:
    """Every retained run of one skill, oldest first, with its inputs, outputs and digests."""

    binding = bound_project(request.app.state)
    skills.skill_versions(binding, skill_id)
    return {"projectId": binding.project_id, "runs": list(skill_runs.list_skill_runs(binding, skill_id))}
