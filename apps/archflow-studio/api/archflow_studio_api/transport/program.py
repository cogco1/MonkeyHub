"""The wire form of the program sheet, and of applying one.

The kernel speaks the sheet in ``snake_case`` because it is a payload a person
authors into a file; the wire is ``camelCase`` like every other field of this
protocol. The two mappings live here and nowhere else, so a route never
reshapes a sheet and the client never sees the file's own spelling.

``recordDigest`` and ``stateDigest`` on a sheet are the complete content and
run/base identities it was read from. They travel on the document rather than
only on the request because a sheet is a thing a person keeps — in a file, in
a browser tab, in an email — and a sheet that could not say which exact record
it describes would be a table of areas about nothing.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from archflow.state.program_sheet import PROGRAM_SHEET_SCHEMA, REQUIREMENTS

from ..application.program import ProgramCandidate, ProgramView
from .artifacts import ModelSourceDto


class ProgramSpaceDto(BaseModel):
    """One room of the brief, and the zone of the record it maps to."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    space_id: str = Field(alias="spaceId", min_length=1)
    name: str
    function: str | None = Field(
        description="a registered role/condition id or alias (GET /api/semantics); "
        "null where the brief has not said what this space is for",
    )
    target_area_m2: float | None = Field(
        alias="targetAreaM2",
        description="what the brief asks for, per one of them; null where it "
        "asks for no number",
    )
    count: int = Field(ge=1, description="how many of this space the brief asks for")
    clear_height_m: float | None = Field(
        alias="clearHeightM",
        description="null from a derived sheet: a MassingLevel@1 height is the "
        "level's full height, not a clear height",
    )
    level_ids: list[str] = Field(alias="levelIds")
    zone_id: str | None = Field(
        alias="zoneId",
        description="the Space@1 this row maps to; null means applying the "
        "sheet would add one",
    )
    mapped_area_m2: float | None = Field(
        alias="mappedAreaM2",
        description="the footprint the record actually draws for the mapped "
        "zone; null when it draws none. Never authored: it is recomputed from "
        "the record on every read",
    )


class ProgramDepartmentDto(BaseModel):
    """One department of the brief and the spaces under it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    department_id: str = Field(alias="departmentId", min_length=1)
    name: str
    spaces: list[ProgramSpaceDto]


class ProgramAdjacencyDto(BaseModel):
    """One requirement between two spaces of the brief."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    from_space_id: str = Field(alias="fromSpaceId", min_length=1)
    to_space_id: str = Field(alias="toSpaceId", min_length=1)
    requirement: str = Field(
        description="one of " + ", ".join(REQUIREMENTS),
    )
    relation_id: str | None = Field(
        alias="relationId",
        description="the record relation this requirement is already carried "
        "by; null means applying the sheet would declare one",
    )


class ProgramTotalsDto(BaseModel):
    """Target against mapped, and what maps to nothing."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    target_area_m2: float = Field(
        alias="targetAreaM2",
        description="the sum of the rows that state a target, times their "
        "count; rows that state none are not counted as zero",
    )
    mapped_area_m2: float = Field(alias="mappedAreaM2")
    unmapped_spaces: list[str] = Field(
        alias="unmappedSpaces",
        description="the spaces of the brief that no zone of the record carries",
    )


class ProgramSheetDto(BaseModel):
    """One ``ProgramSheet@1``, as authored or as derived."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    schema_: str = Field(
        alias="schema",
        default=PROGRAM_SHEET_SCHEMA,
        description=f"always {PROGRAM_SHEET_SCHEMA}",
    )
    project_id: str = Field(alias="projectId")
    record_digest: str | None = Field(
        alias="recordDigest",
        default=None,
        description="the complete content identity of the record this sheet was read from",
    )
    state_digest: str | None = Field(
        alias="stateDigest",
        description="the record this sheet was read from, or written against",
    )
    departments: list[ProgramDepartmentDto]
    adjacencies: list[ProgramAdjacencyDto]
    totals: ProgramTotalsDto
    honesty: list[str]


class ProgramDto(BaseModel):
    """The wire form of ``GET /api/program``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    source_run_id: str | None = Field(default=None, alias="sourceRunId")
    source: str = Field(
        description="input — the architect's own input/runner/program-sheet.json; "
        "derived — what the record's own zones say",
    )
    sheet: ProgramSheetDto
    state_digest: str | None = Field(
        alias="stateDigest",
        description="the state that answers now, whatever the sheet claims",
    )


class ProgramApplyRequestDto(BaseModel):
    """Apply a sheet to the record as a candidate; optionally keep the sheet."""

    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    source_run_id: str | None = Field(
        default=None, alias="sourceRunId", min_length=1,
        description="the selected retained run; omitted uses the project's default source",
    )
    state_digest: str = Field(alias="stateDigest", min_length=1)
    model_source: ModelSourceDto | None = Field(default=None, alias="modelSource")
    sheet: ProgramSheetDto
    save_input: bool = Field(
        alias="saveInput",
        default=False,
        description="also write the sheet to input/runner/program-sheet.json. "
        "Local mode only; a remote server refuses with WIP_WRITE_REMOTE and "
        "still makes the candidate",
    )


class ProgramCandidateDto(BaseModel):
    """The wire form of ``POST /api/program``: 202, and what it will total."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    job_id: str = Field(alias="jobId")
    candidate_id: str = Field(
        alias="candidateId",
        description="the run id this application will be retained under",
    )
    status: str
    totals: ProgramTotalsDto
    saved_input: bool = Field(
        alias="savedInput",
        description="whether the architect's own sheet file was written",
    )
    honesty: list[str]


def sheet_dto(sheet: Mapping[str, Any]) -> ProgramSheetDto:
    """One kernel sheet on the wire, key for key."""

    return ProgramSheetDto(
        schema_=str(sheet.get("schema", PROGRAM_SHEET_SCHEMA)),
        project_id=str(sheet.get("project_id", "")),
        record_digest=sheet.get("record_digest"),
        state_digest=sheet.get("state_digest"),
        departments=[
            ProgramDepartmentDto(
                department_id=str(department["department_id"]),
                name=str(department.get("name", department["department_id"])),
                spaces=[
                    ProgramSpaceDto(
                        space_id=str(space["space_id"]),
                        name=str(space.get("name", space["space_id"])),
                        function=space.get("function"),
                        target_area_m2=space.get("target_area_m2"),
                        count=int(space.get("count", 1)),
                        clear_height_m=space.get("clear_height_m"),
                        level_ids=[str(item) for item in space.get("level_ids", ())],
                        zone_id=space.get("zone_id"),
                        mapped_area_m2=space.get("mapped_area_m2"),
                    )
                    for space in department.get("spaces", ())
                ],
            )
            for department in sheet.get("departments", ())
        ],
        adjacencies=[
            ProgramAdjacencyDto(
                from_space_id=str(row["from_space_id"]),
                to_space_id=str(row["to_space_id"]),
                requirement=str(row["requirement"]),
                relation_id=row.get("relation_id"),
            )
            for row in sheet.get("adjacencies", ())
        ],
        totals=_totals_dto(sheet.get("totals") or {}),
        honesty=[str(line) for line in sheet.get("honesty", ())],
    )


def sheet_payload(dto: ProgramSheetDto) -> dict[str, Any]:
    """One wire sheet back in the kernel's own spelling.

    The schema literal is written from the constant rather than copied off the
    request: what a client sends is checked by the kernel, and what this hands
    it is a sheet that says what this server thinks a sheet is.
    """

    return {
        "schema": PROGRAM_SHEET_SCHEMA,
        "project_id": dto.project_id,
        "record_digest": dto.record_digest,
        "state_digest": dto.state_digest,
        "departments": [
            {
                "department_id": department.department_id,
                "name": department.name,
                "spaces": [
                    {
                        "space_id": space.space_id,
                        "name": space.name,
                        "function": space.function,
                        "target_area_m2": space.target_area_m2,
                        "count": space.count,
                        "clear_height_m": space.clear_height_m,
                        "level_ids": list(space.level_ids),
                        "zone_id": space.zone_id,
                        "mapped_area_m2": space.mapped_area_m2,
                    }
                    for space in department.spaces
                ],
            }
            for department in dto.departments
        ],
        "adjacencies": [
            {
                "from_space_id": row.from_space_id,
                "to_space_id": row.to_space_id,
                "requirement": row.requirement,
                "relation_id": row.relation_id,
            }
            for row in dto.adjacencies
        ],
        "totals": {
            "target_area_m2": dto.totals.target_area_m2,
            "mapped_area_m2": dto.totals.mapped_area_m2,
            "unmapped_spaces": list(dto.totals.unmapped_spaces),
        },
        "honesty": list(dto.honesty),
    }


def program_dto(view: ProgramView) -> ProgramDto:
    return ProgramDto(
        source=view.source,
        sheet=sheet_dto(view.sheet),
        state_digest=view.state_digest,
        source_run_id=view.source_run_id,
    )


def program_candidate_dto(candidate: ProgramCandidate) -> ProgramCandidateDto:
    return ProgramCandidateDto(
        job_id=candidate.job_id,
        candidate_id=candidate.candidate_id,
        status=candidate.status,
        totals=_totals_dto(candidate.totals),
        saved_input=candidate.saved_input,
        honesty=list(candidate.honesty),
    )


def _totals_dto(totals: Mapping[str, Any]) -> ProgramTotalsDto:
    return ProgramTotalsDto(
        target_area_m2=float(totals.get("target_area_m2", 0.0)),
        mapped_area_m2=float(totals.get("mapped_area_m2", 0.0)),
        unmapped_spaces=[str(item) for item in totals.get("unmapped_spaces", ())],
    )
