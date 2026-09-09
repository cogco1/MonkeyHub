"""Massing options: make one, read them side by side, select one.

``POST /api/options`` answers 201 and the option, because making one is
arithmetic and a retained pack — no geometry is compiled and no seat runs.
``POST /api/options/{id}/select`` answers 202 and a job id, because selecting
one *is* a geometry run: it goes through the studio's one candidate path, the
runner builds it, and the run it leaves is where the choice becomes a fact.

Nothing here writes the authored record. An option is a variant of it; the
project's own answer to "what does this say" does not move until somebody
promotes a candidate, which this API cannot do at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
import secrets

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.binding import ProjectBinding, bound_project
from ..application.artifacts import require_model_source
from ..application.candidate import execute_option_candidate
from ..application.jobs import JobRegistry
from ..application.monitoring import projection_source_ref
from ..application.options import (
    MassingOption,
    OptionStore,
    OptionsTable,
    make_option,
    record_massing,
)
from ..application.projection import (
    StateProjection,
    project_state,
    require_actionable,
)
from ..settings import StudioSettings
from ..transport.candidate import CandidateAcceptedDto, accepted_dto
from ..transport.errors import StudioError
from ..transport.artifacts import model_source_from
from ..transport.options import (
    MassingOptionDto,
    MassingOptionRequestDto,
    OptionsDto,
    option_dto,
    options_dto,
)

router = APIRouter(tags=["options"])


@router.post(
    "/options",
    response_model=MassingOptionDto,
    response_model_by_alias=True,
    status_code=201,
)
def make_massing_option(
    request: Request, body: MassingOptionRequestDto
) -> MassingOptionDto:
    """Apply one deterministic transform to the record's massing and measure it."""

    state = request.app.state
    binding = bound_project(state)
    projection = project_state(binding, run_id=body.source_run_id, source_stage_ref=body.source_stage_ref)
    require_actionable(projection)
    _require_current_base(
        binding,
        projection,
        state_digest=body.state_digest,
        record_digest=projection.record_digest,
    )
    envelope = (
        None
        if body.envelope is None
        else body.envelope.model_dump(by_alias=False, exclude_none=True)
    )
    return option_dto(
        make_option(
            binding,
            state.options,
            projection.record,
            state_digest=body.state_digest,
            transform=body.transform,
            parameters=body.parameters(),
            label=body.label,
            envelope=envelope,
            program_targets=body.program_targets,
            source_run_id=body.source_run_id,
            source_stage_ref=projection.source_stage_ref,
            model_source=model_source_from(body.model_source) if body.model_source is not None else None,
        )
    )


@router.get(
    "/options",
    response_model=OptionsDto,
    response_model_by_alias=True,
)
def read_options(
    request: Request,
    run: str | None = Query(default=None, min_length=1),
) -> OptionsDto:
    """The current record's massing as the baseline, and every option beside it.

    Options made against an older state stay on the table and keep saying
    which state they were made against: what an architect looked at an hour
    ago is not deleted because the record moved, and ``stateDigest`` per
    option is how a client greys one out rather than the server hiding it.
    """

    state = request.app.state
    binding = bound_project(state)
    projection = project_state(binding, run_id=run)
    if run is not None and not projection.reference_state_exact:
        require_actionable(projection)
    store: OptionStore = state.options
    return options_dto(
        OptionsTable(
            state_digest=projection.state_digest or projection.record_digest,
            baseline=record_massing(projection.record).metrics,
            options=store.all(),
            source_run_id=run,
        )
    )


@router.post(
    "/options/{option_id}/select",
    response_model=CandidateAcceptedDto,
    response_model_by_alias=True,
    status_code=202,
)
def select_option(request: Request, option_id: str) -> CandidateAcceptedDto:
    """Run the selected option as a candidate; it does not become the record.

    The base is checked here, before the job is queued, for the same reason a
    proposal's is: running a massing against a state nobody was shown is the
    one failure that would look like a success.
    """

    state = request.app.state
    option: MassingOption = state.options.get(option_id)
    binding = bound_project(state)
    projection = project_state(binding, run_id=option.source_run_id, source_stage_ref=option.source_stage_ref)
    require_actionable(projection)
    _require_current_base(
        binding,
        projection,
        state_digest=option.base_state_digest,
        record_digest=option.base_record_digest,
    )
    registry: JobRegistry = state.jobs
    if option.model_source is not None:
        require_model_source(binding, option.model_source, projection)
    settings: StudioSettings = state.settings
    run_id = _run_id(option_id)
    pack = option.pack

    def work() -> object:
        return execute_option_candidate(
            binding,
            settings,
            pack,
            run_id,
            base_record_digest=option.base_record_digest,
            base_state_digest=option.base_state_digest,
            source_run_id=option.source_run_id,
            source_stage_ref=option.source_stage_ref,
            model_source=option.model_source,
            monitor=state.monitor,
        )

    return accepted_dto(
        registry.submit(
            candidate_id=run_id,
            # What the job was submitted for. A selection is not a sentence,
            # so this names the option rather than a proposal, and the
            # candidate readout says so instead of inventing an utterance.
            proposal_id=option_id,
            work=work,
            project_id=binding.project_id,
            source_ref=projection_source_ref(projection),
            # A massing option reaches every entity of the record's massing,
            # so its closure is the volumes, levels and zones it declares.
            write_refs=tuple(
                f"entity:{item['volume_id']}" for item in pack.volumes
            )
            + tuple(f"entity:{item['level_id']}" for item in pack.levels)
            + tuple(f"entity:{item['zone_id']}" for item in pack.zones),
            exclusive=settings.rhino_lane,
        )
    )


def _require_current_base(
    binding: ProjectBinding,
    projection: StateProjection,
    *,
    state_digest: str,
    record_digest: str,
) -> None:
    live_state = projection.state_digest
    live_record = projection.record_digest
    if live_state == state_digest and live_record == record_digest:
        return
    raise StudioError(
        409,
        "STALE_BASE",
        f"this option was made against record {record_digest} / state "
        f"{state_digest}, and {binding.project_id} now has record "
        f"{live_record} / state {live_state}. Re-read /api/state and "
        "make the option again: a massing is only meaningful against the "
        "state it was measured on.",
    )


def _run_id(option_id: str) -> str:
    """The candidate run one selection makes: when, which option, and which try."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"studio-opt-{stamp}-{option_id[-8:]}-{secrets.token_hex(2)}"
