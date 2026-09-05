"""The wire form of a candidate run, its job, and the 202 that starts it.

Three things this DTO is careful about. ``relationChecks`` carries three counts
and two flags rather than a verdict, because held / violated / unchecked are
distinct and a client is never handed a single boolean it could paint green.
``harness`` says in one sentence what kind of run produced these numbers, so
nobody reads a candidate as a stage the project advanced through. And
``artifacts`` reuses the artifact row the rest of the API already serves — a
candidate's exported model is an artifact like any other, and is absent, with
its reason, exactly as often.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..adapters.harness import HARNESS_STATEMENT
from ..application.artifacts import _text, _whole
from ..application.candidate import CandidateRun, RelationTotals, SeatOutcome
from ..application.jobs import PERSISTENCE, Job
from .artifacts import ProjectArtifactDto, artifact_dto
from .project import ProjectVersionDto


class CandidateAcceptedDto(BaseModel):
    """The wire form of ``POST /api/proposals/{id}/candidate``: 202, not a run.

    The candidate id is the run id, and it is answered before the run starts:
    the run exists as soon as the job is queued, and naming it now is what
    lets a client watch for it on the event stream.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    job_id: str = Field(alias="jobId")
    candidate_id: str = Field(
        alias="candidateId",
        description="the run id this candidate will be retained under",
    )
    status: str


class JobDto(BaseModel):
    """The wire form of ``GET /api/jobs/{id}``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    job_id: str = Field(alias="jobId")
    status: str = Field(
        description="queued | running | succeeded | failed",
    )
    candidate_id: str = Field(alias="candidateId")
    proposal_id: str = Field(alias="proposalId")
    created_at: str = Field(alias="createdAt")
    started_at: str | None = Field(alias="startedAt")
    finished_at: str | None = Field(alias="finishedAt")
    error: str | None = Field(
        description="the runner's own message when the run failed; never a "
        "summary of it and never hidden",
    )
    wall_time_s: float | None = Field(alias="wallTimeS")
    lane: str = Field(
        description="parallel, or exclusive when the run exports through "
        "Rhino: one Rhino export at a time on this machine. The ordinary "
        "in-process (OCCT) export runs in the parallel lane",
    )
    waiting_for: str | None = Field(
        alias="waitingFor",
        description="while queued, the candidate this one waits for; null "
        "when nothing ahead of it conflicts",
    )
    waiting_reason: str | None = Field(
        alias="waitingReason",
        description="why it waits, in the queue's words: the refs the two "
        "closures share, the export lane, or every worker busy",
    )
    persistence: str = Field(
        description="where this job lives; it is not version history",
    )


class CandidateSeatResultDto(BaseModel):
    """One seat's row of the run receipt."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    seat_id: str = Field(alias="seatId")
    status: str
    program_ref: str | None = Field(alias="programRef")
    program_digest: str | None = Field(alias="programDigest")
    objects: int | None
    relation_check_ref: str | None = Field(alias="relationCheckRef")


class ExportTimingDto(BaseModel):
    """One seat's export, as its ``cad`` block times it.

    ``path`` is the runner's own word — ``rebuild`` (the whole seat rebuilt) or
    ``patch`` (kept objects carried, only the changed ones rebuilt) — and the
    two object counts are present only on a patch. ``rebuildRatio`` is the
    one derived number: rebuilt over rebuilt + kept, the share of the seat
    that was actually recomputed; null when the receipt does not carry the
    counts, never assumed to be 1.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    seat_id: str = Field(alias="seatId")
    path: str | None = Field(
        description="as the runner wrote it: occt (an in-process export), "
        "reused (a prior export of exactly this program, re-verified by "
        "digest), unsupported (an operation the executor does not realize; "
        "no file), or the Rhino path's rebuild | patch",
    )
    seconds: float | None
    status: str | None
    rebuilt_objects: int | None = Field(alias="rebuiltObjects")
    kept_objects: int | None = Field(alias="keptObjects")
    rebuild_ratio: float | None = Field(alias="rebuildRatio")


class SeatTimingDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    seat_id: str = Field(alias="seatId")
    wall_time_s: float | None = Field(alias="wallTimeS")


class TimingsDto(BaseModel):
    """Where the seconds of a candidate went, read off the run receipt.

    Slow because the run was validating is one thing; slow because it rebuilt
    what did not change is another, and this block is what tells them apart:
    ``runS`` is the whole harness run, ``seats`` the runner's per-seat wall
    times, ``exports`` each Rhino export with its path and seconds. A run that
    exported nothing has an empty ``exports`` — a real answer.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    run_s: float | None = Field(alias="runS")
    seats: list[SeatTimingDto]
    exports: list[ExportTimingDto]


class RelationChecksDto(BaseModel):
    """Three states, and the two flags that keep them from becoming one."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    held: int
    violated: int
    unchecked: int
    held_flag: bool = Field(
        alias="heldFlag",
        description="nothing was violated; true also when nothing was "
        "checked, so it is never a green light on its own",
    )
    fully_checked: bool = Field(
        alias="fullyChecked",
        description="every declared relation was actually checked",
    )


class CandidateDto(BaseModel):
    """The wire form of ``GET /api/candidates/{id}``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    candidate_id: str = Field(alias="candidateId")
    proposal_id: str | None = Field(
        alias="proposalId",
        description="the process-local proposal id; null after restart",
    )
    job_id: str | None = Field(
        alias="jobId",
        description="the process-local job id; null after restart",
    )
    status: str
    base: ProjectVersionDto
    state_digest: str | None = Field(
        alias="stateDigest",
        description="binding identity: this content bound to this run",
    )
    record_digest: str | None = Field(
        alias="recordDigest",
        description="content identity: what the candidate record says",
    )
    changed_vs_projection: bool | None = Field(
        alias="changedVsProjection",
        description="whether the candidate's content differs from the "
        "project's current projection; null when the receipt names no "
        "record digest",
    )
    receipt_ref: str = Field(
        alias="receiptRef",
        description="the retained runner receipt this readout was made from",
    )
    seat_execution_complete: bool = Field(alias="seatExecutionComplete")
    seat_results: list[CandidateSeatResultDto] = Field(alias="seatResults")
    relation_checks: RelationChecksDto = Field(alias="relationChecks")
    artifacts: list[ProjectArtifactDto]
    skipped_runs: list[str] = Field(
        alias="skippedRuns",
        description="runs whose records could not be listed while resolving "
        "this candidate's exported models; normally empty, never hidden",
    )
    wall_time_s: float | None = Field(alias="wallTimeS")
    timings: TimingsDto
    harness: str = Field(
        description="what kind of run produced this; a candidate is never a "
        "stage the project advanced through",
    )
    honesty: list[str] = Field(
        description="what this candidate cannot tell you, in lines the UI "
        "shows verbatim; empty is a real answer, not a missing one",
    )


def timings_dto(candidate: CandidateRun) -> TimingsDto:
    """The run's seconds, seat by seat, from the receipt's own numbers."""

    exports: list[ExportTimingDto] = []
    for seat in candidate.seat_results:
        if seat.cad is None:
            continue
        rebuilt = _whole(seat.cad.get("rebuilt_objects"))
        kept = _whole(seat.cad.get("kept_objects"))
        ratio = (
            rebuilt / (rebuilt + kept)
            if rebuilt is not None and kept is not None and rebuilt + kept > 0
            else None
        )
        exports.append(
            ExportTimingDto(
                seat_id=seat.seat_id,
                path=_text(seat.cad.get("path")),
                seconds=_float(seat.cad.get("seconds")),
                status=_text(seat.cad.get("status")),
                rebuilt_objects=rebuilt,
                kept_objects=kept,
                rebuild_ratio=ratio,
            )
        )
    return TimingsDto(
        run_s=candidate.wall_time_s,
        seats=[
            SeatTimingDto(seat_id=seat.seat_id, wall_time_s=seat.wall_time_s)
            for seat in candidate.seat_results
        ],
        exports=exports,
    )


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def accepted_dto(job: Job) -> CandidateAcceptedDto:
    return CandidateAcceptedDto(
        job_id=job.job_id,
        candidate_id=job.candidate_id,
        status=job.status,
    )


def job_dto(job: Job) -> JobDto:
    return JobDto(
        job_id=job.job_id,
        status=job.status,
        candidate_id=job.candidate_id,
        proposal_id=job.proposal_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        wall_time_s=job.wall_time_s,
        lane=job.lane,
        waiting_for=job.waiting_for,
        waiting_reason=job.waiting_reason,
        persistence=PERSISTENCE,
    )


def to_dto(candidate: CandidateRun) -> CandidateDto:
    """Shape one candidate for the wire; every value came off its records."""

    return CandidateDto(
        candidate_id=candidate.candidate_id,
        proposal_id=candidate.proposal_id,
        job_id=candidate.job_id,
        status=candidate.status,
        base=ProjectVersionDto(
            version=candidate.base.version,
            state_sha256=candidate.base.state_sha256,
        ),
        state_digest=candidate.state_digest,
        record_digest=candidate.record_digest,
        changed_vs_projection=candidate.changed_vs_projection,
        receipt_ref=candidate.receipt_ref,
        seat_execution_complete=candidate.seat_execution_complete,
        seat_results=[_seat_dto(seat) for seat in candidate.seat_results],
        relation_checks=relations_dto(candidate.relation_checks),
        artifacts=[
            artifact_dto(record) for record in candidate.artifacts
        ],
        skipped_runs=list(candidate.skipped_runs),
        wall_time_s=candidate.wall_time_s,
        timings=timings_dto(candidate),
        harness=HARNESS_STATEMENT,
        honesty=list(candidate.honesty),
    )


def _seat_dto(seat: SeatOutcome) -> CandidateSeatResultDto:
    return CandidateSeatResultDto(
        seat_id=seat.seat_id,
        status=seat.status,
        program_ref=seat.program_ref,
        program_digest=seat.program_digest,
        objects=seat.objects,
        relation_check_ref=seat.relation_check_ref,
    )


def relations_dto(totals: RelationTotals) -> RelationChecksDto:
    """Shape the three-state block for the wire.

    Public because the validation readout carries the *same* block: a candidate
    and its verdict must not be able to disagree about what held.
    """

    return RelationChecksDto(
        held=totals.held,
        violated=totals.violated,
        unchecked=totals.unchecked,
        held_flag=totals.held_flag,
        fully_checked=totals.fully_checked,
    )
