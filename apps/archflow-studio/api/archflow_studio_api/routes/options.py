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

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import ProjectBinding, bound_project
from ..application.candidate import execute_option_candidate
from ..application.jobs import JobRegistry
from ..application.options import (
    MassingOption,
    OptionStore,
    OptionsTable,
    make_option,
    record_massing,
)
from ..application.projection import StateProjection, project_state
from ..settings import StudioSettings
from ..transport.candidate import CandidateAcceptedDto, accepted_dto
from ..transport.errors import StudioError
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
    projection = project_state(binding)
    _require_current_base(binding, projection, body.state_digest)
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
        )
    )


@router.get(
    "/options",
    response_model=OptionsDto,
    response_model_by_alias=True,
)
def read_options(request: Request) -> OptionsDto:
    """The current record's massing as the baseline, and every option beside it.

    Options made against an older state stay on the table and keep saying
    which state they were made against: what an architect looked at an hour
    ago is not deleted because the record moved, and ``stateDigest`` per
    option is how a client greys one out rather than the server hiding it.
    """

    state = request.app.state
    binding = bound_project(state)
    projection = project_state(binding)
    store: OptionStore = state.options
    return options_dto(
        OptionsTable(
            state_digest=projection.state_digest or projection.record_digest,
            baseline=record_massing(projection.record).metrics,
            options=store.all(),
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
    projection = project_state(binding)
    _require_current_base(binding, projection, option.base_state_digest)
    registry: JobRegistry = state.jobs
    settings: StudioSettings = state.settings
    run_id = _run_id(option_id)
    pack = option.pack

    def work() -> object:
        return execute_option_candidate(
            binding,
            settings,
            pack,
            run_id,
            base_state_digest=option.base_state_digest,
        )

    return accepted_dto(
        registry.submit(
            candidate_id=run_id,
            # What the job was submitted for. A selection is not a sentence,
            # so this names the option rather than a proposal, and the
            # candidate readout says so instead of inventing an utterance.
            proposal_id=option_id,
            work=work,
            # A massing option reaches every entity of the record's massing,
            # so its closure is the volumes, levels and zones it declares.
            closure=tuple(
                f"entity:{item['volume_id']}" for item in pack.volumes
            )
            + tuple(f"entity:{item['level_id']}" for item in pack.levels)
            + tuple(f"entity:{item['zone_id']}" for item in pack.zones),
            exclusive=settings.rhino_export,
        )
    )


def _require_current_base(
    binding: ProjectBinding,
    projection: StateProjection,
    state_digest: str,
) -> None:
    live = projection.state_digest
    if live == state_digest:
        return
    raise StudioError(
        409,
        "STALE_BASE",
        f"this option was made against state {state_digest}, and "
        f"{binding.project_id} now projects {live}. Re-read /api/state and "
        "make the option again: a massing is only meaningful against the "
        "state it was measured on.",
    )


def _run_id(option_id: str) -> str:
    """The candidate run one selection makes: when, which option, and which try."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"studio-opt-{stamp}-{option_id[-8:]}-{secrets.token_hex(2)}"
