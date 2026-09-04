"""A proposal executed as a detached candidate, by the kernel's own runner.

The whole of this module is arrangement. It takes the authored State Record,
replaces the one value the proposal names, binds the successor to a new run,
retains the harness stage, and calls ``run_project``. Production, propagation,
relation checking, geometry compilation and export are the runner's, and none
of them is re-implemented, approximated or second-guessed here.

Two things about the run are worth stating plainly. It is **detached**: the run
is created under the project's ``runs/``, nothing is issued, ``canonical/``
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

from collections.abc import Callable
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from archflow.capabilities.geometry_proposal import (
    GeometryProposalProviderIdentity,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    INTENT_COMPILATION,
    RUNNER_RUN_RECEIPT,
    SEAT_RELATION_CHECK,
)
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.runtime.project_runner import RunOptions, run_project
from archflow.state.state_record import (
    Entity,
    SchematicPack,
    StateRecord,
    developed_design_view,
)

from ..adapters.harness import STAGE_ID, harness_guard
from ..adapters.seats import load_seat_pack, seats_of
from ..settings import StudioSettings
from ..transport.errors import StudioError
from .artifacts import ArtifactRecord, _text, _whole, list_artifacts
from .binding import ProjectBinding, record_kind
from .jobs import FAILED, QUEUED, RUNNING
from .projection import (
    BRANCH_ID,
    PORTFOLIO_ID,
    SELECTION_DECISION_REF,
    StateProjection,
    # Module-private, deliberately reused rather than re-implemented: a
    # candidate must start from the same authored record, read the same way
    # and refusing for the same reasons, as the projection the user was shown.
    # A second reader of that file would be a second answer to "what does this
    # project say", and the two would drift.
    _load_authored_record,
    project_state,
)
from .proposals import Proposal


class StaleBaseError(ValueError):
    """The authored record moved between the proposal and the run.

    Deliberately not a ``StudioError``: this is raised on the worker thread,
    where there is no request left to answer. The route makes the same check
    at request time and refuses with 409; this one catches the interval the
    route cannot see — between accepting the job and reading the record — and
    the job reports it.
    """


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
    for parameter in record.parameters:
        if parameter.key == proposal.key and parameter.lock_authority:
            # The lock is the record's own statement that this value is not the studio's to change.
            raise StudioError(409, "PARAMETER_LOCKED",
                              f"parameter {proposal.key} is locked by {parameter.lock_authority}; the authored record has to unlock it first")
    parameters = tuple(
        parameter
        if parameter.key != proposal.key
        else replace(parameter, value=proposal.new)
        for parameter in record.parameters
    )
    return replace(record, parameters=parameters)


# The four entity schemas that are a record's massing. A massing successor
# replaces exactly these and the declared ``option``; everything else in the
# record — its levels, axes, elements, parameters and relations — is the
# authored record's and is carried through untouched.
MASSING_SCHEMAS = ("MassingLevel@1", "Volume@1", "Space@1", "Connection@1")


def massing_successor(record: StateRecord, pack: SchematicPack) -> StateRecord:
    """The authored record with its massing replaced by one option's pack.

    The studio's second successor operation, beside ``successor_record``. A
    proposal edits one authored scalar; a selected massing option replaces the
    four massing schemas together, because a volume, the level it stands on
    and the zone that owns it are one statement and half of it is not a
    building.

    Two things are carried rather than rewritten. An entity whose id survives
    keeps its own ``basis_refs`` and any field the pack does not carry, so a
    pack that says exactly what the record already said produces the same
    record — same order, same fields, same digest. And ``Component@1``
    ``volume_ids`` are updated from the pack's components, because the kernel
    requires every massing volume to have exactly one semantic owner
    (``SpatialOptionProposal``) and a transform that adds or drops a volume
    without saying who owns it would be refused by the runner rather than
    here.
    """

    existing = {entity.entity_id: entity for entity in record.entities}
    evidence = tuple(sorted(set(record.evidence_refs)))

    def entity(entity_id: str, schema: str, fields: Mapping[str, Any]) -> Entity:
        previous = existing.get(entity_id)
        if previous is not None and previous.schema == schema:
            return replace(previous, fields={**previous.fields, **fields})
        return Entity(entity_id, schema, dict(fields), basis_refs=evidence)

    replacements = {
        **{
            level["level_id"]: entity(
                level["level_id"],
                "MassingLevel@1",
                {"base_y": level["base_y"], "height": level["height"]},
            )
            for level in pack.levels
        },
        **{
            volume["volume_id"]: entity(
                volume["volume_id"],
                "Volume@1",
                {
                    "min": list(volume["min"]),
                    "max": list(volume["max"]),
                    "level_ids": list(volume["level_ids"]),
                },
            )
            for volume in pack.volumes
        },
        **{
            zone["zone_id"]: entity(
                zone["zone_id"],
                "Space@1",
                {
                    "program_node_refs": list(zone["program_node_refs"]),
                    "level_ids": list(zone["level_ids"]),
                    "volume_ids": list(zone["volume_ids"]),
                },
            )
            for zone in pack.zones
        },
        **{
            connection["connection_id"]: entity(
                connection["connection_id"],
                "Connection@1",
                {
                    "source_zone_id": connection["source_zone_id"],
                    "target_zone_id": connection["target_zone_id"],
                    "relationship_refs": list(connection["relationship_refs"]),
                    "directed": bool(connection.get("directed", False)),
                },
            )
            for connection in pack.connections
        },
    }
    owned = {
        component.component_id: list(component.volume_ids)
        for component in pack.components
    }
    kept: list[Entity] = []
    seen: set[str] = set()
    for item in record.entities:
        if item.schema == "Component@1" and item.entity_id in owned:
            volume_ids = owned[item.entity_id]
            kept.append(
                item
                if list(item.fields.get("volume_ids", ())) == volume_ids
                else replace(item, fields={**item.fields, "volume_ids": volume_ids})
            )
            continue
        if item.schema not in MASSING_SCHEMAS:
            kept.append(item)
            continue
        successor = replacements.get(item.entity_id)
        if successor is not None:
            kept.append(successor)
            seen.add(item.entity_id)
    kept.extend(
        item for entity_id, item in replacements.items() if entity_id not in seen
    )
    return replace(
        record,
        entities=tuple(kept),
        option={
            **dict(record.option),
            "option_id": pack.option_id,
            "label": pack.label,
            "typology": pack.typology,
            "rationale": pack.rationale,
            "footprint_cells": [list(cell) for cell in pack.footprint_cells],
            "assumption_refs": list(pack.assumption_refs),
        },
    )


def execute_candidate(
    binding: ProjectBinding,
    settings: StudioSettings,
    proposal: Proposal,
    run_id: str,
) -> Mapping[str, Any]:
    """Run the proposal as a candidate and return the runner's own receipt.

    Called on a worker thread: ``run_project`` calls ``asyncio.run`` internally
    and would refuse to start on the event loop. Every failure — a refused
    value, an absent seat pack, a stale base, a repository error — propagates
    to the caller, which is the job registry, and becomes a visible failed job.

    The order matters. The seat pack, the base check and the authored record
    are all resolved *before* ``create_run``, so a candidate that cannot run
    leaves no run directory behind for somebody to wonder about later.
    """

    seat_pack = _seat_pack(binding, proposal.base_state_digest)
    successor = successor_record(_load_authored_record(binding), proposal)
    retain: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    if proposal.compilation_receipt is not None:
        # A chat turn is work in progress; a run is shared (ADR-007). The receipt of
        # the model call that compiled the words is retained here, and only here,
        # because this is where those words became a run somebody can cite. The
        # deterministic compiler reads no model and returns no receipt, so a proposal
        # made from a sentence already in the grammar retains nothing.
        retain = (
            (
                INTENT_COMPILATION,
                {
                    "schema": "IntentCompilation@1",
                    "proposal_id": proposal.proposal_id,
                    "utterance": proposal.utterance,
                    "base_state_digest": proposal.base_state_digest,
                    "receipt": dict(proposal.compilation_receipt),
                },
            ),
        )
    return _run_successor(
        binding, settings, seat_pack, successor, run_id, retain=retain
    )


def execute_option_candidate(
    binding: ProjectBinding,
    settings: StudioSettings,
    pack: SchematicPack,
    run_id: str,
    *,
    base_state_digest: str,
) -> Mapping[str, Any]:
    """Run one selected massing option as a candidate, by the same arrangement.

    The only difference from a proposal's candidate is which successor is run:
    the authored record with its massing replaced by this option's pack rather
    than with one scalar replaced. Everything after that — the base check, the
    run, the harness stage, the seats — is the same code, so an option that
    cannot run fails for the reasons a proposal would.

    Nothing is written here about the selection itself. The runner retains the
    option it executed as ``selected-spatial-option``, from the record's own
    massing, and a record the studio wrote a second time would be a second
    statement of the same fact.
    """

    seat_pack = _seat_pack(binding, base_state_digest)
    successor = massing_successor(_load_authored_record(binding), pack)
    return _run_successor(binding, settings, seat_pack, successor, run_id)


def _seat_pack(
    binding: ProjectBinding, base_state_digest: str
) -> Mapping[str, Any]:
    """The seat pack, and the base check made where the record is actually read.

    The route checked the base too, but that was before this job reached the
    front of the queue: in between, the authored record may have been
    rewritten, and running the change against a state nobody was shown is the
    one failure that would look like a success. The seat pack is loaded first,
    so a candidate that cannot run leaves no run directory behind.
    """

    seat_pack = load_seat_pack(binding.repository)
    live = project_state(binding).state_digest
    if live != base_state_digest:
        raise StaleBaseError(
            "STALE_BASE: the authored record changed after the proposal was "
            f"made ({base_state_digest[:8]} -> {(live or 'none')[:8]})"
        )
    return seat_pack


def _run_successor(
    binding: ProjectBinding,
    settings: StudioSettings,
    seat_pack: Mapping[str, Any],
    successor: StateRecord,
    run_id: str,
    *,
    retain: tuple[tuple[str, Mapping[str, Any]], ...] = (),
) -> Mapping[str, Any]:
    """Create the run, retain what belongs to it, and hand the record to the runner.

    The whole of this is arrangement, and it is one function so that every
    kind of candidate runs under the same harness stage, the same seats and
    the same options. ``retain`` is what the studio knows and the run records
    do not — a compilation receipt, so far — written before the run starts.
    """

    repository = binding.repository
    seats = seats_of(seat_pack)
    run = repository.create_run(run_id)
    for record_kind_, payload in retain:
        repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=run_id
            ),
            record_kind=record_kind_,
            payload=payload,
        )

    return run_successor(
        binding,
        settings,
        successor,
        run_id,
        retain_before_run=retain_compilation,
    )


def run_successor(
    binding: ProjectBinding,
    settings: StudioSettings,
    successor: StateRecord,
    run_id: str,
    *,
    base_state_digest: str | None = None,
    retain: tuple[tuple[str, Mapping[str, Any]], ...] = (),
) -> Mapping[str, Any]:
    """Run a successor record that no proposal made (a program sheet applied) by the same
    arrangement every candidate takes: the base check where the record is read, the run, the
    harness stage and the seats (``_run_successor``). ``base_state_digest`` is checked against
    the live state when given; ``retain`` are records put into the run before it starts."""

    seat_pack = _seat_pack(binding, base_state_digest) if base_state_digest is not None else load_seat_pack(binding.repository)
    return _run_successor(binding, settings, seat_pack, successor, run_id, retain=retain)

@dataclass(frozen=True, slots=True)
class SeatOutcome:
    """What one seat did, exactly as its row of the run receipt says."""

    seat_id: str
    status: str
    program_ref: str | None
    program_digest: str | None
    objects: int | None
    relation_check_ref: str | None
    # The seat row's ``cad`` block, carried as the runner wrote it and never
    # interpreted here. ``None`` is the runner saying this seat was not asked
    # to export at all; a mapping is it saying one was attempted, and it is
    # the only evidence anywhere that distinguishes the two. It stays off the
    # wire as a block: what a client needs from it is the verdict, the honesty
    # line, and — since the timings landed — how long the export took and
    # whether it was a full rebuild or a patch.
    cad: Mapping[str, Any] | None = None
    # The seat row's own wall time, as the runner wrote it.
    wall_time_s: float | None = None


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
    # The retained record this readout was made from; never absent.
    receipt_ref: str
    seat_execution_complete: bool
    seat_results: tuple[SeatOutcome, ...]
    relation_checks: RelationTotals
    artifacts: tuple[ArtifactRecord, ...]
    # Runs whose records could not be listed while looking for this
    # candidate's exported models. Normally empty, never hidden.
    skipped_runs: tuple[str, ...]
    wall_time_s: float | None
    # What this candidate cannot tell you, in lines the UI shows verbatim.
    honesty: tuple[str, ...]


def describe(
    binding: ProjectBinding,
    proposal: Proposal | None,
    *,
    candidate_id: str,
    job_id: str,
    status: str,
    proposal_id: str | None = None,
) -> CandidateRun:
    """Read one candidate run back out of the project, or refuse by name.

    Nothing the job remembered is used for the run's own facts: the seats, the
    digests and the relation counts all come from the retained records, so a
    candidate reported here is one the project can still account for. The
    proposal is here for one thing only — the honesty lines, which are about
    what the *change* did not do rather than about what the run produced.

    ``proposal`` is ``None`` for a candidate this process did not make from a
    sentence — a selected massing option, or any candidate whose proposal was
    lost with a restart — and ``proposal_id`` then names what the job was
    submitted for. The run's own facts are unaffected: they are the records'.
    """

    if status in (QUEUED, RUNNING):
        raise StudioError(
            404,
            "CANDIDATE_NOT_FOUND",
            f"{binding.project_id}: candidate {candidate_id} is still "
            f"{status}; job {job_id} has not finished writing its records. "
            f"Poll GET /api/jobs/{job_id} and read the candidate when it "
            "reports succeeded.",
        )
    if status == FAILED:
        # Before any run lookup, because a job that failed at the seat pack,
        # the base check or the authored record never reached ``create_run``:
        # there is no run directory, and asking for one would answer
        # ``RUN_NOT_FOUND`` — a code about the project's runs for a question
        # about this candidate, when the reason it failed is on the job.
        raise StudioError(
            404,
            "CANDIDATE_NOT_FOUND",
            f"{binding.project_id}: candidate {candidate_id} was not produced "
            f"— job {job_id} failed. GET /api/jobs/{job_id} carries the "
            "runner's own sentence; a failed run leaves its reason on its "
            "job, not a candidate.",
        )
    retained, receipt = _receipt(binding, candidate_id)
    seat_rows = _rows(receipt.get("seat_results"))
    record_digest = _text(receipt.get("state_record_digest"))
    projection = project_state(binding)
    # One listing answers both questions: which of this run's exports are
    # servable, and which runs could not be read while finding out.
    listing = list_artifacts(binding)
    return CandidateRun(
        candidate_id=candidate_id,
        proposal_id=(
            proposal.proposal_id
            if proposal is not None
            else (proposal_id or job_id)
        ),
        job_id=job_id,
        status=status,
        base=binding.load_run(candidate_id).base,
        state_digest=_text(receipt.get("design_state_digest")),
        record_digest=record_digest,
        changed_vs_projection=(
            None
            if record_digest is None
            else record_digest != projection.record_digest
        ),
        receipt_ref=_text(receipt.get("receipt_ref")) or retained.uri,
        seat_execution_complete=bool(receipt.get("seat_execution_complete")),
        seat_results=tuple(
            SeatOutcome(
                seat_id=str(row.get("seat_id")),
                status=str(row.get("status")),
                program_ref=_text(row.get("program_ref")),
                program_digest=_text(row.get("program_digest")),
                objects=_whole(row.get("objects")),
                relation_check_ref=_text(row.get("relation_check_ref")),
                cad=_block(row.get("cad")),
                wall_time_s=_number(row.get("wall_time_s")),
            )
            for row in seat_rows
        ),
        relation_checks=_relation_totals(binding, candidate_id),
        # Only this run's own receipts are read: a candidate never claims
        # another run's model. A run that did not export has none, so an
        # unexported candidate answers with an empty list by construction.
        artifacts=tuple(
            record
            for record in listing.artifacts
            if record.run_id == candidate_id
        ),
        skipped_runs=listing.skipped_runs,
        wall_time_s=_number(receipt.get("wall_time_s")),
        honesty=_honesty(proposal, projection),
    )


def _honesty(
    proposal: Proposal | None, projection: StateProjection
) -> tuple[str, ...]:
    """What this candidate did not do, said out loud.

    The studio replaces one authored value and runs; it does not recompute the
    quantities that value feeds, because recomputation is the kernel's rule to
    apply and the kernel has no successor operation yet (K1/P109). A candidate
    whose geometry was built from a record where ``span`` still says what it
    said before ``bay`` changed is not wrong — it is partial — and the
    difference has to be on the wire, not in a design note somebody read once.
    """

    if proposal is None:
        # A selected massing option, or a proposal this process no longer
        # holds. Either way the sentence that would say what the change
        # reached is not here, and guessing one from the run records would be
        # a claim about an intent nothing retained.
        return (
            "this candidate was not made from a proposal this process holds: "
            "what it changed is stated by the records its run retained, not "
            "by this readout",
        )
    if proposal.impact.propagated:
        return (
            f"derived values downstream of {proposal.target_ref} were not "
            "recomputed for this candidate (K1/P109): "
            + ", ".join(proposal.impact.propagated),
        )
    if not projection.edges:
        # Nothing propagated, and nothing could have: the record declares no
        # dependencies at all. Reporting the first without the second would
        # let "nothing downstream" read as "nothing is downstream".
        return (
            "0 dependency edges: nothing downstream could be recomputed or "
            "checked",
        )
    return ()


def _receipt(
    binding: ProjectBinding, run_id: str
) -> tuple[ProjectRecordRef, Mapping[str, Any]]:
    """The run's newest ``runner-run-receipt``, or a 404 that says why not.

    Which receipt that is, is the binding's answer: a second reader here could
    start preferring a different one than the projection reads.
    """

    newest = binding.newest_runner_receipt(run_id)
    if newest is None:
        raise StudioError(
            404,
            "CANDIDATE_NOT_FOUND",
            f"{binding.project_id}: run {run_id} retained no "
            f"{RUNNER_RUN_RECEIPT}, so there is no candidate to read. A run "
            "that failed leaves its reason on its job, not a candidate.",
        )
    return newest


def _relation_totals(
    binding: ProjectBinding, run_id: str
) -> RelationTotals:
    """Every relation report in the run, added up without losing a state."""

    held = violated = unchecked = 0
    reports = 0
    all_held = True
    all_checked = True
    for ref in binding.record_refs(run_id):
        if record_kind(ref) != SEAT_RELATION_CHECK:
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


def _block(value: object) -> Mapping[str, Any] | None:
    """One nested receipt block, frozen and otherwise untouched.

    Nothing here reads what is in it. The runner wrote it, the verdict asks
    it two questions, and a copy that could be edited afterwards would let a
    reader of this candidate change what the run said it did.
    """

    return None if not isinstance(value, Mapping) else MappingProxyType(dict(value))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
