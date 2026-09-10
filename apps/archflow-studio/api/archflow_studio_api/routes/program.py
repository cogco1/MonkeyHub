"""``GET /api/program`` and ``POST /api/program``: the brief, both ways.

The read answers with the architect's own sheet where the project holds one
and with the record's own reading of its zones where it does not, and says
which. The write applies a sheet to the record **as a candidate** — the same
detached run a proposal's candidate takes, through the same code — and answers
202 with the run it will become and what the sheet totals. It never rewrites
the authored record; the one authored file it can write is the sheet itself,
on request, on a local studio.

``GET /api/semantics`` sits here because this is the panel that needs it: a
function on a program row is a registered role, and a client that guessed at
the vocabulary would offer the architect choices the record refuses.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from archflow.state.program_sheet import totals_of

from ..application.binding import bound_project
from ..application.artifacts import require_model_source
from ..application.candidate import run_operator
from ..application.jobs import JobRegistry
from ..application.program import (
    ProgramCandidate,
    candidate_honesty,
    candidate_run_id,
    closure_of_sheet,
    read_program,
    save_input_sheet,
    semantic_terms,
    operator_for,
)
from ..application.projection import project_state, require_actionable
from ..application.monitoring import projection_source_ref
from ..settings import StudioSettings
from ..transport.errors import StudioError
from ..transport.artifacts import model_source_from
from ..transport.program import (
    ProgramApplyRequestDto,
    ProgramCandidateDto,
    ProgramDto,
    program_candidate_dto,
    program_dto,
    sheet_payload,
)
from ..transport.semantics import SemanticsDto, semantics_dto

router = APIRouter(tags=["program"])


@router.get(
    "/program",
    response_model=ProgramDto,
    response_model_by_alias=True,
)
def read_sheet(
    request: Request,
    run: str | None = Query(default=None, min_length=1),
) -> ProgramDto:
    """The project's program sheet: the authored one, or the derived one."""

    binding = bound_project(request.app.state)
    projection = project_state(binding, run_id=run)
    if run is not None and not projection.reference_state_exact:
        require_actionable(projection)
    return program_dto(read_program(binding, projection, source_run_id=run))


@router.post(
    "/program",
    response_model=ProgramCandidateDto,
    response_model_by_alias=True,
    status_code=202,
)
def apply_program(
    request: Request, body: ProgramApplyRequestDto
) -> ProgramCandidateDto:
    """Queue this sheet as a candidate run, and name the run it will make.

    The order is deliberate. The state is checked first, because a sheet
    applied to a record the architect was not looking at is the one failure
    that looks like a success. The successor is built next, so a sheet the
    kernel refuses is a 422 now rather than a failed job later. Only then is
    the run queued — and only after that is the authored file written.

    That last step is the one that can refuse on its own. Running a sheet is a
    read of the record and needs no owner; *keeping* one does, and a remote
    server has none yet. So a remote request that asked to save gets the run it
    asked for and a ``409 WIP_WRITE_REMOTE`` naming that run, rather than a
    silent save that overwrote somebody else's brief.
    """

    state = request.app.state
    settings: StudioSettings = state.settings
    binding = bound_project(state)
    projection = project_state(binding, run_id=body.source_run_id, source_stage_ref=body.source_stage_ref)
    require_actionable(projection)
    if (
        body.state_digest != projection.state_digest
        or body.sheet.state_digest != projection.record.state_digest
        or body.sheet.record_digest != projection.record_digest
    ):
        raise StudioError(
            409,
            "STALE_BASE",
            f"the request names state {body.state_digest}; its sheet names "
            f"record {body.sheet.record_digest} and state "
            f"{body.sheet.state_digest}; {binding.project_id} now answers "
            f"record {projection.record_digest} and state "
            f"{projection.record.state_digest} (request state "
            f"{projection.state_digest}). Read "
            "/api/program again: a sheet maps to zones of one state, and the "
            "zones it names may have moved.",
        )
    sheet = sheet_payload(body.sheet)
    model_source = model_source_from(body.model_source) if body.model_source is not None else None
    if model_source is not None:
        require_model_source(binding, model_source, projection)
    operator = operator_for(sheet, projection)
    registry: JobRegistry = state.jobs
    run_id = candidate_run_id()

    def work() -> object:
        return run_operator(
            binding, settings, operator, run_id, source_run_id=body.source_run_id,
            model_source=model_source,
            source_stage_ref=projection.source_stage_ref,
            monitor=state.monitor,
        )

    job = registry.submit(
        candidate_id=run_id,
        # A sheet is not a proposal, and this names what it is instead of
        # borrowing a proposal id that would resolve to nothing.
        proposal_id=f"program-sheet:{projection.record_digest}",
        work=work,
        project_id=binding.project_id,
        source_ref=projection_source_ref(projection),
        write_refs=closure_of_sheet(sheet),
        exclusive=settings.rhino_lane,
    )
    saved = False
    if body.save_input:
        save_input_sheet(binding, settings, sheet, candidate_id=run_id, job_id=job.job_id)
        saved = True
    return program_candidate_dto(ProgramCandidate(
        candidate_id=job.candidate_id,
        job_id=job.job_id,
        status=job.status,
        # The server's own sum of the rows it just applied, never the client's:
        # a total is the one number on that screen nobody could otherwise check.
        totals=totals_of(sheet),
        saved_input=saved,
        honesty=candidate_honesty(saved, settings),
    ))


@router.get(
    "/semantics",
    response_model=SemanticsDto,
    response_model_by_alias=True,
)
def read_semantics(request: Request) -> SemanticsDto:
    """Every role and condition canonical state may name.

    It reads no project: the vocabulary is the framework's, the same for every
    project this server binds, and asking for it must not fail because a
    project root is wrong.
    """

    return semantics_dto(semantic_terms())
