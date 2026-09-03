"""A proposal executed as a detached candidate, by the kernel's own runner.

The whole of this module is arrangement. It takes the authored State Record,
replaces the one value the proposal names, binds the successor to a new run,
retains the harness stage, and calls ``run_project``. Production, propagation,
relation checking, geometry compilation and export are the runner's, and none
of them is re-implemented, approximated or second-guessed here.

Two things about the run are worth stating plainly. It is **detached**: the run
is created under the project's ``runs/``, HEAD is never moved, ``canonical/``
and ``input/`` are never written, and the harness workflow it runs under is the
same one the reference-run rule refuses to follow. And it is **content
addressed**: the candidate is identified by its run id, and what it *is* comes
from the receipt the runner retained — ``state_record_digest`` for what the
record says, ``design_state_digest`` for that content bound to this run.

``changedVsProjection`` compares the two content identities, so a candidate
that happens to say exactly what the project already says reports that it
changed nothing rather than implying it did.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from archflow.capabilities.geometry_proposal import (
    GeometryProposalProviderIdentity,
)
from archflow.project.refs import ProjectVersionRef
from archflow.runtime.project_runner import RunOptions, run_project
from archflow.state.state_record import StateRecord, developed_design_view

from ..adapters.harness import STAGE_ID, harness_guard
from ..adapters.seats import load_seat_pack, seats_of
from ..settings import StudioSettings
from ..transport.errors import StudioError
from .artifacts import ArtifactRecord, list_artifacts
from .binding import ProjectBinding, record_kind
from .projection import (
    BRANCH_ID,
    PORTFOLIO_ID,
    SELECTION_DECISION_REF,
    # Module-private, deliberately reused rather than re-implemented: a
    # candidate must start from the same authored record, read the same way
    # and refusing for the same reasons, as the projection the user was shown.
    # A second reader of that file would be a second answer to "what does this
    # project say", and the two would drift.
    _load_authored_record,
    project_state,
)
from .proposals import Proposal

RUNNER_RECEIPT_KIND = "runner-run-receipt"
RELATION_CHECK_KIND = "seat-relation-check"


def successor_record(record: StateRecord, proposal: Proposal) -> StateRecord:
    """The authored record with the one value the proposal names replaced.

    This is the K1 candidate-under-card: until the kernel offers a successor
    operation of its own, the studio edits exactly one authored scalar and
    nothing else. An element proposal replaces ``params.<key>`` on its row; a
    parameter proposal replaces that ``Parameter``'s value. No expression is
    re-evaluated and no derived value is touched — recomputing them here would
    be a second propagation rule beside the kernel's.
    """

    if proposal.element_id is not None:
        entities = tuple(
            entity
            if entity.entity_id != proposal.element_id
            else replace(
                entity,
                fields={
                    **entity.fields,
                    "params": {
                        **entity.fields["params"],
                        proposal.key: proposal.new,
                    },
                },
            )
            for entity in record.entities
        )
        return replace(record, entities=entities)
    parameters = tuple(
        parameter
        if parameter.key != proposal.key
        else replace(parameter, value=proposal.new)
        for parameter in record.parameters
    )
    return replace(record, parameters=parameters)


def execute_candidate(
    binding: ProjectBinding,
    settings: StudioSettings,
    proposal: Proposal,
    run_id: str,
) -> Mapping[str, Any]:
    """Run the proposal as a candidate and return the runner's own receipt.

    Called on a worker thread: ``run_project`` calls ``asyncio.run`` internally
    and would refuse to start on the event loop. Every failure — a refused
    value, an absent seat pack, a repository error — propagates to the caller,
    which is the job registry, and becomes a visible failed job.
    """

    repository = binding.repository
    seat_pack = load_seat_pack(repository)
    seats = seats_of(seat_pack)
    successor = successor_record(_load_authored_record(binding), proposal)
    run = repository.create_run(run_id)
    # The one sanctioned binding: the record attaches itself to this run.
    bound = successor.bound_to(run)
    state = developed_design_view(
        bound,
        run=run,
        portfolio_id=PORTFOLIO_ID,
        branch_id=BRANCH_ID,
        selection_decision_ref=SELECTION_DECISION_REF,
    )
    guard = harness_guard(repository, run, state)
    options = RunOptions(
        commitment_ref=seat_pack["commitment_ref"],
        provider_identity=GeometryProposalProviderIdentity(
            **seat_pack["provider_identity"]
        ),
        branch_id=seat_pack.get("branch_id", BRANCH_ID),
        export=settings.rhino_export,
        workspace_root=repository.layout.run(run_id).root / "workspaces",
        powershell=settings.powershell,
    )
    if options.export:
        # The exporter writes into a directory per seat and expects it to be
        # there; production's own entry point creates them the same way.
        for seat in seats:
            if not seat.reviewer:
                (
                    options.workspace_root
                    / f"cad-{STAGE_ID}-{seat.seat_id}"
                ).mkdir(parents=True, exist_ok=True)
    return run_project(
        repository,
        run=run,
        stage_guard=guard,
        record=bound,
        seats=seats,
        options=options,
    )


@dataclass(frozen=True, slots=True)
class SeatOutcome:
    """What one seat did, exactly as its row of the run receipt says."""

    seat_id: str
    status: str
    program_ref: str | None
    program_digest: str | None
    objects: int | None
    relation_check_ref: str | None


@dataclass(frozen=True, slots=True)
class RelationTotals:
    """Three counts and two flags, never collapsed into a verdict.

    ``held``, ``violated`` and ``unchecked`` are separate answers: a relation
    nobody could check has not passed, and ``held_flag`` alone is never a green
    light — it is true whenever nothing was violated, including when nothing
    was checked, which is why ``fully_checked`` travels beside it.
    """

    held: int
    violated: int
    unchecked: int
    held_flag: bool
    fully_checked: bool


@dataclass(frozen=True, slots=True)
class CandidateRun:
    """One finished candidate, read back off the records its run retained."""

    candidate_id: str
    proposal_id: str
    job_id: str
    status: str
    base: ProjectVersionRef
    state_digest: str | None
    record_digest: str | None
    changed_vs_projection: bool | None
    receipt_ref: str | None
    seat_execution_complete: bool
    seat_results: tuple[SeatOutcome, ...]
    relation_checks: RelationTotals
    artifacts: tuple[ArtifactRecord, ...]
    wall_time_s: float | None


def describe(
    binding: ProjectBinding,
    *,
    candidate_id: str,
    proposal_id: str,
    job_id: str,
    status: str,
) -> CandidateRun:
    """Read one candidate run back out of the project, or refuse by name.

    Nothing the job remembered is used for the run's own facts: the seats, the
    digests and the relation counts all come from the retained records, so a
    candidate reported here is one the project can still account for.
    """

    receipt = _receipt(binding, candidate_id)
    seat_rows = _rows(receipt.get("seat_results"))
    record_digest = _text(receipt.get("state_record_digest"))
    return CandidateRun(
        candidate_id=candidate_id,
        proposal_id=proposal_id,
        job_id=job_id,
        status=status,
        base=binding.load_run(candidate_id).base,
        state_digest=_text(receipt.get("design_state_digest")),
        record_digest=record_digest,
        changed_vs_projection=(
            None
            if record_digest is None
            else record_digest != project_state(binding).record_digest
        ),
        receipt_ref=_text(receipt.get("receipt_ref")),
        seat_execution_complete=bool(receipt.get("seat_execution_complete")),
        seat_results=tuple(
            SeatOutcome(
                seat_id=str(row.get("seat_id")),
                status=str(row.get("status")),
                program_ref=_text(row.get("program_ref")),
                program_digest=_text(row.get("program_digest")),
                objects=_whole(row.get("objects")),
                relation_check_ref=_text(row.get("relation_check_ref")),
            )
            for row in seat_rows
        ),
        relation_checks=_relation_totals(binding, candidate_id),
        # Only an exported run has anything to serve, and only that run's own
        # receipts are read: a candidate never claims another run's model.
        artifacts=(
            tuple(
                record
                for record in list_artifacts(binding).artifacts
                if record.run_id == candidate_id
            )
            if any(row.get("cad") for row in seat_rows)
            else ()
        ),
        wall_time_s=_number(receipt.get("wall_time_s")),
    )


def _receipt(binding: ProjectBinding, run_id: str) -> Mapping[str, Any]:
    """The run's newest ``runner-run-receipt``, or a 404 that says why not."""

    newest: tuple[float, Mapping[str, Any]] | None = None
    for ref in binding.record_refs(run_id):
        if record_kind(ref) != RUNNER_RECEIPT_KIND:
            continue
        mtime = binding.repository.layout.resolve_record(ref).stat().st_mtime
        payload = binding.repository.load_json(ref)
        if newest is None or mtime > newest[0]:
            newest = (mtime, payload)
    if newest is None:
        raise StudioError(
            404,
            "CANDIDATE_NOT_FOUND",
            f"{binding.project_id}: run {run_id} retained no "
            f"{RUNNER_RECEIPT_KIND}, so there is no candidate to read. A run "
            "that failed leaves its reason on its job, not a candidate.",
        )
    return newest[1]


def _relation_totals(
    binding: ProjectBinding, run_id: str
) -> RelationTotals:
    """Every relation report in the run, added up without losing a state."""

    held = violated = unchecked = 0
    reports = 0
    all_held = True
    all_checked = True
    for ref in binding.record_refs(run_id):
        if record_kind(ref) != RELATION_CHECK_KIND:
            continue
        payload = binding.repository.load_json(ref)
        counts = payload.get("counts")
        counts = counts if isinstance(counts, Mapping) else {}
        reports += 1
        held += _whole(counts.get("held")) or 0
        violated += _whole(counts.get("violated")) or 0
        unchecked += _whole(counts.get("unchecked")) or 0
        all_held = all_held and bool(payload.get("held"))
        all_checked = all_checked and bool(payload.get("fully_checked"))
    if reports == 0:
        # No report is not "everything held": it is nothing checked, and the
        # flags say so rather than defaulting to a quiet green.
        return RelationTotals(0, 0, 0, False, False)
    return RelationTotals(held, violated, unchecked, all_held, all_checked)


def _rows(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
