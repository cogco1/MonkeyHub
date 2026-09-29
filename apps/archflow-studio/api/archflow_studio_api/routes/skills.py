"""Add and read the bound project's skills: named procedures in immutable versions.

The Hub reads these routes on the library project's Runtime and hands the
current versions to the agents through their native skill mechanism. A skill
is only a procedure: it grants no permission, and the ``permissions`` its
manifest declares are not enforced by anything.
"""

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application import skills
from ..application.authentication import request_attribution
from ..application.binding import bound_project
from ..transport.errors import StudioError
from ..transport.skills import (
    SkillDto,
    SkillIndexDto,
    SkillRequestDto,
    skill_dto,
    skill_index_row,
)

router = APIRouter(tags=["skills"])


@router.get("/skills", response_model=SkillIndexDto, response_model_by_alias=True)
def read_skill_index(request: Request) -> SkillIndexDto:
    """Every skill's current version: id, version, name and description, and no body."""

    binding = bound_project(request.app.state)
    return SkillIndexDto(projectId=binding.project_id,
                         skills=[skill_index_row(row) for row in skills.list_skills(binding)])


@router.get("/skills/{skill_id}", response_model=SkillDto, response_model_by_alias=True)
def read_skill(request: Request, skill_id: str,
               version: int | None = Query(default=None, ge=1)) -> SkillDto:
    """One skill with its body: the latest version, or the exact ``version`` named."""

    row, latest = skills.read_skill(bound_project(request.app.state), skill_id, version)
    return skill_dto(row, latest)


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
    )
    return skill_dto(row, row.version)
