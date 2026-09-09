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

import base64
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from monkeyarch.capabilities.geometry_proposal import (
    GeometryProposalProviderIdentity,
    load_compiled_geometry_program,
)
from archflow.adapters.cad_execution import patch_composed_three_dm
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    INTENT_COMPILATION,
    RUNNER_RUN_RECEIPT,
    SEAT_RELATION_CHECK,
    STUDIO_DOCUMENT_COMMENT,
    STUDIO_MODEL_ASSET,
    STUDIO_CANDIDATE_DELTA,
)
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef, record_ref_from_uri
from monkeyarch.runtime.project_runner import CAD_BACKEND_OCCT, RunOptions, run_project
from archflow.state.state_record import (
    SchematicPack,
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    apply_state_record_operator,
    combine_component_changes,
    developed_design_view,
)

from ..adapters.harness import STAGE_ID, harness_guard
from ..adapters.seats import load_seat_pack, seats_of
from ..settings import StudioSettings
from ..transport.errors import StudioError
from .artifacts import (
    ArtifactRecord, ModelSource, _text, _whole, artifact_bytes, list_artifacts,
    register_model_asset, require_model_source,
)
from .monitoring import StudioMonitor, candidate_event_id
from .binding import ProjectBinding, ReferenceRun, record_kind
from .jobs import FAILED, QUEUED, RUNNING
from .projection import (
    BRANCH_ID,
    PORTFOLIO_ID,
    SELECTION_DECISION_REF,
    StateProjection,
    project_state,
    require_actionable,
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


def execute_candidate(
    binding: ProjectBinding,
    settings: StudioSettings,
    proposal: Proposal,
    run_id: str,
    *,
    monitor: StudioMonitor | None = None,
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

    base_record = _operator_base(
        binding,
        expected_record_digest=proposal.record_digest,
        expected_state_digest=proposal.base_state_digest,
        source_run_id=proposal.source_run_id,
        source_stage_ref=proposal.source_stage_ref,
    )
    operator = proposal.state_record_operator or StateRecordOperator(
        kind=StateRecordEditKind.SET_SCALAR,
        base_record_digest=proposal.record_digest,
        base_state_digest=base_record.state_digest,
        protected=tuple(sorted(set(proposal.protected))),
        target_ref=proposal.target_ref,
        key=proposal.key,
        value=proposal.new,
    )
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
    if proposal.document_comment_ref is not None:
        from .gestures import require_document_comment_source

        comment = binding.repository.load_json(proposal.document_comment_ref)
        require_document_comment_source(binding, comment, project_state(binding, proposal.source_run_id))
        retain += ((STUDIO_DOCUMENT_COMMENT, comment),)
    return run_operator(
        binding, settings, operator, run_id,
        source_run_id=proposal.source_run_id, retain=retain,
        model_source=proposal.model_source,
        source_stage_ref=proposal.source_stage_ref,
        monitor=monitor,
    )


def _retain_composed_candidate(
    binding: ProjectBinding, source: ArtifactRecord, run_id: str, receipt: Mapping[str, Any],
    *, source_receipt: Mapping[str, Any] | None,
) -> None:
    """Compose the runner's native exports into the exact input model and retain it."""

    before = {row["seat_id"]: row for row in _rows((source_receipt or {}).get("seat_results"))}
    after = {row["seat_id"]: row for row in _rows(receipt.get("seat_results"))}
    if not before or before.keys() != after.keys():
        raise ValueError("The composed candidate needs the same retained geometry seats as its source model.")
    _, composed = artifact_bytes(binding, source.sha256)
    listing = list_artifacts(binding)
    for seat_id, seat in after.items():
        prior_ref = before[seat_id].get("program_ref")
        next_ref = seat.get("program_ref")
        if not prior_ref or not next_ref:
            raise ValueError(f"The composed candidate has no retained source or replacement program for seat {seat_id}.")
        execution_ref = (seat.get("cad") or {}).get("execution_ref")
        donors = [row for row in listing.artifacts if row.run_id == run_id
                  and row.receipt_ref == execution_ref and row.format == "3dm"]
        if len(donors) != 1 or not donors[0].available or not donors[0].sha256:
            raise ValueError(f"The composed candidate needs an available native 3DM export for seat {seat_id}.")
        prior_program = load_compiled_geometry_program(binding.repository.load_json(record_ref_from_uri(prior_ref, binding.project_id)))
        program = load_compiled_geometry_program(binding.repository.load_json(record_ref_from_uri(next_ref, binding.project_id)))
        _, donor = artifact_bytes(binding, donors[0].sha256)
        composed = patch_composed_three_dm(composed, prior_program=prior_program, program=program, replacement_3dm=donor)
    projection = project_state(binding, run_id)
    register_model_asset(binding, run_id, projection.state_digest, f"{run_id}-composed.3dm", base64.b64encode(composed).decode())


def execute_option_candidate(
    binding: ProjectBinding,
    settings: StudioSettings,
    pack: SchematicPack,
    run_id: str,
    *,
    base_record_digest: str,
    base_state_digest: str,
    source_run_id: str | None = None,
    model_source: ModelSource | None = None,
    source_stage_ref: ProjectRecordRef | None = None,
    monitor: StudioMonitor | None = None,
) -> Mapping[str, Any]:
    """Run one selected massing option as a candidate, by the same arrangement.

    The only difference from a proposal's candidate is which successor is run:
    the selected source record with its massing replaced by this option's pack rather
    than with one scalar replaced. Everything after that — the base check, the
    run, the harness stage, the seats — is the same code, so an option that
    cannot run fails for the reasons a proposal would.

    Nothing is written here about the selection itself. The runner retains the
    option it executed as ``selected-spatial-option``, from the record's own
    massing, and a record the studio wrote a second time would be a second
    statement of the same fact.
    """

    base_record = _operator_base(
        binding,
        expected_record_digest=base_record_digest,
        expected_state_digest=base_state_digest,
        source_run_id=source_run_id,
        source_stage_ref=source_stage_ref,
    )
    operator = StateRecordOperator(
        kind=StateRecordEditKind.REPLACE_MASSING,
        base_record_digest=base_record_digest,
        base_state_digest=base_record.state_digest,
        massing_pack=pack,
    )
    return run_operator(binding, settings, operator, run_id, source_run_id=source_run_id, model_source=model_source,
                        source_stage_ref=source_stage_ref, monitor=monitor)


def _operator_base(
    binding: ProjectBinding,
    *,
    expected_record_digest: str,
    expected_state_digest: str,
    source_run_id: str | None = None,
    source_stage_ref: ProjectRecordRef | None = None,
) -> StateRecord:
    """Bind a Studio request to the StateRecord exact base on the worker.

    The route checked the base too, but that was before this job reached the
    front of the queue. Both identities captured by the proposal or option
    are checked rather than replacing either with a fresh value from here.
    """

    projection = project_state(binding, run_id=source_run_id, source_stage_ref=source_stage_ref)
    require_actionable(projection)
    if (
        projection.record_digest != expected_record_digest
        or projection.state_digest != expected_state_digest
    ):
        raise StaleBaseError(
            "STALE_BASE: the source record changed after the candidate base "
            f"was captured (record {expected_record_digest[:8]} -> "
            f"{projection.record_digest[:8]}; state "
            f"{expected_state_digest[:8]} -> "
            f"{(projection.state_digest or 'none')[:8]})"
        )
    return projection.record


def _run_successor(
    binding: ProjectBinding,
    settings: StudioSettings,
    seat_pack: Mapping[str, Any],
    successor: StateRecord,
    run_id: str,
    *,
    retain: tuple[tuple[str, Mapping[str, Any]], ...] = (),
    model_source_ref: str | None = None,
    source_run_receipt_ref: ProjectRecordRef | None = None,
    monitor: StudioMonitor | None = None,
) -> Mapping[str, Any]:
    """Create the run, retain what belongs to it, and hand the record to the runner.

    The whole of this is arrangement, and it is one function so that every
    kind of candidate runs under the same harness stage, the same seats and
    the same options. ``retain`` is what the studio knows and the run records
    do not — a compilation receipt, so far — written before the run starts.
    """

    repository = binding.repository
    seats = seats_of(seat_pack)
    if successor.base is None:
        raise StateRecordError("candidate successor carries no project base")
    run = repository.create_run(run_id, base=successor.base)
    for record_kind_, payload in retain:
        repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=run_id
            ),
            record_kind=record_kind_,
            payload=payload,
        )
    # The one sanctioned binding: the record attaches itself to this run.
    bound = successor.bound_to(run)
    state = developed_design_view(
        bound,
        run=run,
        portfolio_id=PORTFOLIO_ID,
        branch_id=BRANCH_ID,
        selection_decision_ref=SELECTION_DECISION_REF,
    )
    guard = harness_guard(repository, run, state, model_source_ref=model_source_ref)
    # What a live provider would have to present. The runner records its own
    # proposals, so a pack that declares no provider identity still runs.
    declared_provider = seat_pack.get("provider_identity")
    options = RunOptions(
        commitment_ref=seat_pack["commitment_ref"],
        live_provider_identity=(
            None
            if declared_provider is None
            else GeometryProposalProviderIdentity(**declared_provider)
        ),
        branch_id=seat_pack.get("branch_id", BRANCH_ID),
        # The one CAD setting decides both whether a seat exports and which
        # executor does it: OCCT in process by default, Rhino only when the
        # process was configured to say so. Nothing here falls back.
        export=settings.exports,
        cad_backend=settings.cad_export if settings.exports else CAD_BACKEND_OCCT,
        workspace_root=repository.layout.run(run_id).root / "workspaces",
        powershell=settings.powershell,
        source_run_receipt_ref=source_run_receipt_ref,
    )
    if options.export:
        # Either exporter writes into a directory per seat and expects it to
        # be there; production's own entry point creates them the same way.
        for seat in seats:
            if not seat.reviewer:
                (
                    options.workspace_root
                    / f"cad-{STAGE_ID}-{seat.seat_id}"
                ).mkdir(parents=True, exist_ok=True)
    observations = {}
    if monitor is not None and monitor.store is not None:
        observed = monitor.observer(project_id=binding.project_id, run_id=run_id)
        def observe_export(timing):
            # The runner also exposes nested operations. Backend/path are its
            # export compatibility fields; the observation already names phase.
            observed({"related_event_id": candidate_event_id(binding.project_id, run_id),
                      **{key: value for key, value in timing.items() if key not in {"backend", "path"}}})
        observations["operation_observer"] = observe_export
    return run_project(
        repository,
        run=run,
        stage_guard=guard,
        record=bound,
        seats=seats,
        options=options,
        **observations,
    )


def run_operator(
    binding: ProjectBinding,
    settings: StudioSettings,
    operator: StateRecordOperator,
    run_id: str,
    *,
    source_run_id: str | None = None,
    retain: tuple[tuple[str, Mapping[str, Any]], ...] = (),
    model_source: ModelSource | None = None,
    source_stage_ref: ProjectRecordRef | None = None,
    combined_candidate_ids: tuple[str, ...] = (),
    monitor: StudioMonitor | None = None,
) -> Mapping[str, Any]:
    """Replay a typed operator against its selected or default exact base and run it."""

    seat_pack = load_seat_pack(binding.repository)
    projection = project_state(binding, run_id=source_run_id, source_stage_ref=source_stage_ref)
    require_actionable(projection)
    if model_source is None and projection.source_stage_ref is not None:
        stage = binding.design_stage(projection.source_stage_ref)
        if stage.candidate_id == projection.run.run_id:
            model_source = ModelSource(stage.candidate_id, projection.state_digest, stage.model_sha256)
        else:
            complete = [row.model_source for row in list_artifacts(binding).artifacts
                        if row.run_id == projection.run.run_id and row.design_state_digest == projection.state_digest
                        and row.representation == "composed" and row.model_source is not None]
            if len(complete) == 1:
                model_source = complete[0]
            elif len(complete) > 1:
                raise StudioError(409, "MODEL_SOURCE_REQUIRED", "Select the complete candidate model to continue.")
    source_model = require_model_source(binding, model_source, projection) if model_source is not None else None
    if source_model is not None and source_model.representation != "composed":
        source_model = None
    successor = apply_state_record_operator(projection.record, operator)
    source_record_ref = (
        record_ref_from_uri(projection.record_source, binding.project_id)
        if projection.reference_state_exact else None
    )
    runner_ref = None
    if projection.reference.receipt is not None:
        runner_ref = next((ref for ref in binding.record_refs(projection.run.run_id)
                           if record_kind(ref) == RUNNER_RUN_RECEIPT
                           and binding.repository.load_json(ref) == projection.reference.receipt), None)
        if runner_ref is None:
            raise StudioError(409, "CANDIDATE_SOURCE_INVALID", "The selected source runner receipt could not be retained exactly.")
    delta = {
        "schema": "StudioCandidateDelta@1", "project_id": binding.project_id, "run_id": run_id,
        "source_stage_ref": None if projection.source_stage_ref is None else projection.source_stage_ref.to_dict(),
        "source_run_ref": projection.run.to_dict(),
        "source_record_ref": None if source_record_ref is None else source_record_ref.to_dict(),
        "source_record": projection.record.to_dict() if source_record_ref is None else None,
        "source_runner_ref": None if runner_ref is None else runner_ref.to_dict(),
        "source_model": None if model_source is None else model_source.to_dict(),
        "operator": operator.to_dict(), "result_record_digest": successor.digest,
        "combined_candidate_ids": list(combined_candidate_ids),
    }
    receipt = _run_successor(binding, settings, seat_pack, successor, run_id,
                             retain=(*retain, (STUDIO_CANDIDATE_DELTA, delta)),
                             model_source_ref=source_model.receipt_ref if source_model is not None else None,
                             source_run_receipt_ref=runner_ref, monitor=monitor)
    if source_model is not None and settings.exports:
        _retain_composed_candidate(binding, source_model, run_id, receipt, source_receipt=projection.reference.receipt)
    return receipt


def read_candidate_delta(binding: ProjectBinding, run_id: str) -> dict[str, Any]:
    delta = binding.candidate_delta(run_id)
    if delta is None:
        raise StudioError(409, "CANDIDATE_DELTA_MISSING", "This retained model has no replayable candidate change; keep it as a legacy reference.")
    return delta


def prepare_combined_candidate(
    binding: ProjectBinding, candidate_ids: tuple[str, ...],
) -> tuple[StateProjection, StateRecordOperator]:
    """Read saved candidates from one Stage and compile their independent net edits."""
    if len(candidate_ids) < 2 or len(set(candidate_ids)) != len(candidate_ids):
        raise StudioError(422, "CANDIDATE_COMBINE_INVALID", "Choose at least two distinct candidates.")
    deltas = [read_candidate_delta(binding, run_id) for run_id in candidate_ids]
    stage_value = deltas[0].get("source_stage_ref")
    if stage_value is None or any(delta.get("source_stage_ref") != stage_value for delta in deltas):
        raise StudioError(409, "CANDIDATE_COMBINE_BASE_MISMATCH", "Combined candidates must share one exact committed Stage.")
    stage_ref = ProjectRecordRef.from_dict(stage_value)
    projection = project_state(binding, source_stage_ref=stage_ref)
    require_actionable(projection)
    results = tuple(replay_candidate(binding, run_id) for run_id in candidate_ids)
    protected: set[str] = set()
    for run_id in candidate_ids:
        current = run_id
        while current != projection.run.run_id:
            delta = read_candidate_delta(binding, current)
            protected.update(delta["operator"]["protected"])
            current = delta["source_run_ref"]["run_id"]
    try:
        operator = combine_component_changes(projection.record, results, protected=tuple(sorted(protected)))
    except StateRecordError as exc:
        raise StudioError(409, "CANDIDATE_COMBINE_CONFLICT", str(exc)) from exc
    return projection, operator


def replay_candidate(binding: ProjectBinding, run_id: str) -> StateRecord:
    """Verify each exact parent before replaying the candidate's retained operator."""
    visiting: set[str] = set()

    def replay(current_id: str) -> StateRecord:
        if current_id in visiting:
            raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The candidate source chain contains a cycle.")
        visiting.add(current_id)
        delta = read_candidate_delta(binding, current_id)
        source_run = RunRef.from_dict(delta["source_run_ref"])
        if source_run.project_id != binding.project_id:
            raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The candidate source belongs to another project.")
        source_ref_value = delta["source_record_ref"]
        source_is_stage = False
        if source_ref_value is None:
            if delta.get("source_stage_ref") is not None:
                raise StudioError(409, "CANDIDATE_DELTA_INVALID", "A Stage-based candidate must retain its exact source record reference.")
            source = StateRecord.from_dict(delta["source_record"])
            if source.run_ref != source_run:
                raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The authored source binding does not match the retained operator.")
        else:
            source_receipt = binding.repository.load_json(ProjectRecordRef.from_dict(delta["source_runner_ref"]))
            source_ref, source = binding.exact_state_record(ReferenceRun(source_run, "query", source_receipt))
            if source_ref != ProjectRecordRef.from_dict(source_ref_value):
                raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The candidate source record and runner disagree.")
            stage_value = delta.get("source_stage_ref")
            if stage_value is not None:
                stage = binding.design_stage(ProjectRecordRef.from_dict(stage_value))
                source_is_stage = stage.record_ref == source_ref and stage.runner_ref == ProjectRecordRef.from_dict(delta["source_runner_ref"])
                if not source_is_stage:
                    parent_delta = binding.candidate_delta(source_run.run_id)
                    if parent_delta is None or parent_delta.get("source_stage_ref") != stage_value:
                        raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The candidate's parent does not descend from its declared Stage.")
            if not source_is_stage and binding.candidate_delta(source_run.run_id) is not None:
                parent_result = replay(source_run.run_id)
                if parent_result.digest != source.digest:
                    raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The preceding candidate does not replay to its retained source.")
        operator = StateRecordOperator.from_dict(delta["operator"])
        combined = delta.get("combined_candidate_ids", [])
        if combined:
            if len(combined) < 2 or len(set(combined)) != len(combined) or not source_is_stage:
                raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The combined candidate must use one exact Stage base and distinct sources.")
            results = []
            for candidate_id in combined:
                if read_candidate_delta(binding, candidate_id).get("source_stage_ref") != delta.get("source_stage_ref"):
                    raise StudioError(409, "CANDIDATE_DELTA_INVALID", "Combined candidate sources do not share the retained Stage.")
                results.append(replay(candidate_id))
            expected = combine_component_changes(source, tuple(results), protected=operator.protected)
            if expected != operator:
                raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The combined operator disagrees with its source candidates.")
        result = apply_state_record_operator(source, operator)
        if result.digest != delta["result_record_digest"]:
            raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The retained change does not reproduce the candidate content.")
        actual = project_state(binding, current_id, source_stage_ref=None if delta.get("source_stage_ref") is None else ProjectRecordRef.from_dict(delta["source_stage_ref"]))
        if not actual.reference_state_exact or actual.record.digest != result.digest:
            raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The replayed content does not match the actual candidate result.")
        visiting.remove(current_id)
        return actual.record

    return replay(run_id)

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
    proposal_id: str | None
    job_id: str | None
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
    job_id: str | None,
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
    lost with a restart. The process-local proposal and job ids may then both
    be absent; they are never reconstructed from the run id. The run's own
    facts are unaffected: they are the records'.
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
    executed = _executed_record(binding, receipt)
    # One listing answers both questions: which of this run's exports are
    # servable, and which runs could not be read while finding out.
    listing = list_artifacts(binding)
    workflow_ref = receipt.get("workflow_ref")
    if workflow_ref:
        workflow = binding.repository.load_json(record_ref_from_uri(workflow_ref, binding.project_id))
        composed_source = any(
            record_kind(record_ref_from_uri(ref, binding.project_id)) == STUDIO_MODEL_ASSET
            for ref in workflow.get("basis_refs", ()) if ref.startswith("project://")
        )
        if composed_source and not any(row.run_id == candidate_id and row.representation == "composed" and row.available for row in listing.artifacts):
            raise StudioError(404, "CANDIDATE_NOT_FOUND", f"Candidate {candidate_id} has native results but no completed composed model for its retained source.")
    return CandidateRun(
        candidate_id=candidate_id,
        proposal_id=(
            proposal.proposal_id if proposal is not None else proposal_id
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
        honesty=_honesty(proposal, projection, executed),
    )


def _executed_record(
    binding: ProjectBinding, receipt: Mapping[str, Any]
) -> StateRecord | None:
    """The State Record the run executed, read off the record the receipt names; None when it cannot be.

    The honesty lines quote the values this record carries for the derived
    parameters the edit reached, so what they say is what the run built from,
    not what the current projection or the proposal remembers.
    """

    uri = receipt.get("state_record_ref")
    if not isinstance(uri, str):
        return None
    try:
        return StateRecord.from_dict(
            binding.repository.load_json(
                record_ref_from_uri(uri, binding.project_id)
            )
        )
    except Exception:  # a readout never fails on a line it can leave out
        return None


def _honesty(
    proposal: Proposal | None,
    projection: StateProjection,
    executed: StateRecord | None = None,
) -> tuple[str, ...]:
    """What this candidate did and did not do, said out loud.

    The kernel's ``StateRecordOperator`` applies the explicit edit and then
    re-evaluates every derived parameter its closure reaches through the
    declared expressions (``_refresh_derived_parameters``); the run then
    rebuilds the rows and re-measures the relations the edit reaches through
    declared references and relations. That is the whole of what is
    propagated: a dependency nobody declared is not followed, and the
    components no edge mentions are named as unknown rather than left to
    read as unaffected.
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
    if not projection.edges and not proposal.impact.propagated:
        # Nothing propagated, and nothing could have: the record declares no
        # dependencies at all. Reporting the first without the second would
        # let "nothing downstream" read as "nothing is downstream".
        return (
            "0 dependency edges: nothing downstream could be recomputed or "
            "checked",
        )
    lines: list[str] = []
    if proposal.impact.propagated:
        parameters = {
            parameter.key: parameter
            for parameter in (
                executed.parameters if executed is not None else projection.parameters
            )
        }
        derived = [
            ref
            for ref in proposal.impact.propagated
            if ref.startswith("parameter:")
            and ref[len("parameter:"):] in parameters
            and parameters[ref[len("parameter:"):]].expr is not None
        ]
        entities = [
            ref for ref in proposal.impact.propagated if ref.startswith("entity:")
        ]
        other = [
            ref
            for ref in proposal.impact.propagated
            if ref not in derived and ref not in entities
        ]
        if derived:
            shown = ", ".join(
                f"{ref} = {parameters[ref[len('parameter:'):]].value}"
                if executed is not None
                else ref
                for ref in derived
            )
            lines.append(
                f"this candidate applied the explicit edit at "
                f"{proposal.target_ref} and the kernel re-evaluated the "
                f"declared expressions it reaches: {shown}"
            )
        if entities:
            # Membership in the edit's closure says these were handed to the
            # run, not that every one of them was measured: a relation can stay
            # unchecked and a seat can fail, and the seat rows and relation
            # counts above are what say so. The line claims only the handover.
            lines.append(
                "elements the edit reaches through declared references and "
                "relations are included in this run's rebuild and check "
                "results, not recomputed as stored values; the seat rows and "
                "relation counts say what was actually rebuilt and measured: "
                + ", ".join(entities)
            )
        if other:
            lines.append(
                "reached by the edit, with no declared expression to "
                "re-evaluate: " + ", ".join(other)
            )
    unknown = proposal.impact.unknown_coverage
    if unknown:
        count = len(unknown)
        noun = "component appears" if count == 1 else "components appear"
        lines.append(
            f"{count} {noun} in no dependency edge ({', '.join(unknown)}): "
            "what this edit does to them is unknown, not nothing"
        )
    return tuple(lines)


def _receipt(
    binding: ProjectBinding, run_id: str
) -> tuple[ProjectRecordRef, Mapping[str, Any]]:
    """The run's newest ``runner-run-receipt``, or a 404 that says why not.

    Which receipt that is, is the binding's answer: a second reader here could
    start preferring a different one than the projection reads.
    """

    try:
        newest = binding.newest_runner_receipt(run_id)
    except StudioError as exc:
        if exc.code != "RUN_NOT_FOUND":
            raise
        raise StudioError(
            404,
            "CANDIDATE_NOT_FOUND",
            f"{binding.project_id}: no completed candidate {run_id} is "
            "retained in this project.",
        ) from exc
    if newest is None:
        raise StudioError(
            404,
            "CANDIDATE_NOT_FOUND",
            f"{binding.project_id}: run {run_id} retained no "
            f"{RUNNER_RUN_RECEIPT}, so there is no candidate to read. A run "
            "that failed leaves its reason on its job, not a candidate.",
        )
    retained, receipt = newest
    stage = receipt.get("stage")
    if not (
        receipt.get("project_id") == binding.project_id
        and receipt.get("run_id") == run_id
        and receipt.get("workflow_is_harness") is True
        and isinstance(stage, Mapping)
        and stage.get("stage_id") == STAGE_ID
    ):
        raise StudioError(
            404,
            "CANDIDATE_NOT_FOUND",
            f"{binding.project_id}: run {run_id} retained a runner receipt, "
            f"but it is not the exact {STAGE_ID} harness receipt for this "
            "project and run.",
        )
    return retained, receipt


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
