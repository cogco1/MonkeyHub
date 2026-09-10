"""Studio acceptance and historical views over portfolio values and P036.

There is no history store here. Committed nodes are the records reachable from
P036 design branches; candidate contents and model bytes remain in their runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, RUNNER_RUN_RECEIPT
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri, require_identifier
from archflow.project.repository import StaleDesignBranch
from archflow.state.design_portfolio import (
    DesignBranch, DesignStage, advance_branch, fork_branch, initialize_branch,
)
from archflow.state.state_record import StateRecord

from .artifacts import ArtifactRecord, ModelSource, require_complete_model, require_model_source
from .binding import ProjectBinding, ReferenceRun, record_kind
from .candidate import describe, read_candidate_delta, replay_candidate
from .jobs import SUCCEEDED
from .projection import project_state
from .validation import validate_design_candidate
from ..ports import StudioEventSink
from ..transport.errors import StudioError


@dataclass(frozen=True, slots=True)
class StageView:
    ref: ProjectRecordRef
    stage: DesignStage
    model_source: ModelSource
    record_digest: str


@dataclass(frozen=True, slots=True)
class DesignHistory:
    project_id: str
    branches: tuple[DesignBranch, ...]
    branch_id: str
    stages: tuple[StageView, ...]


def stage_ref_from(binding: ProjectBinding, uri: str) -> ProjectRecordRef:
    try:
        ref = record_ref_from_uri(uri, binding.project_id)
        if ref.record_kind != DESIGN_STAGE:
            raise ValueError("the reference does not name a design Stage")
    except (TypeError, ValueError) as exc:
        raise StudioError(422, "DESIGN_STAGE_REF_INVALID", "Provide a retained design Stage reference in this project.") from exc
    return ref


def _branches(binding: ProjectBinding) -> tuple[DesignBranch, ...]:
    return tuple(DesignBranch.from_dict(row) for _, row in sorted(binding.repository.read_design_branches().items()))


def _branch(binding: ProjectBinding, branch_id: str) -> DesignBranch:
    rows = binding.repository.read_design_branches()
    if branch_id not in rows:
        raise StudioError(404, "DESIGN_BRANCH_NOT_FOUND", f"Design branch {branch_id!r} does not exist.")
    return DesignBranch.from_dict(rows[branch_id])


def _exact_runner(
    binding: ProjectBinding, run_id: str, runner_ref: ProjectRecordRef,
) -> tuple[ProjectRecordRef, StateRecord, dict]:
    if record_kind(runner_ref) != RUNNER_RUN_RECEIPT:
        raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The Stage source is not a retained runner receipt.")
    receipt = binding.repository.load_json(runner_ref)
    ref, record = binding.exact_state_record(ReferenceRun(
        run=binding.load_run(run_id), source="design-history", receipt=receipt,
    ))
    if not receipt.get("seat_execution_complete"):
        raise StudioError(409, "CANDIDATE_NOT_FINISHED", "The source run did not finish its seats.")
    return ref, record, receipt


def read_stage(binding: ProjectBinding, ref: ProjectRecordRef) -> StageView:
    stage = binding.design_stage(ref)
    record_ref, record, receipt = _exact_runner(binding, stage.candidate_id, stage.runner_ref)
    if record_ref != stage.record_ref:
        raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The Stage record differs from its pinned runner source.")
    source = ModelSource(stage.candidate_id, receipt.get("design_state_digest"), stage.model_sha256)
    projection = project_state(binding, source_stage_ref=ref)
    artifact = require_model_source(binding, source, projection)
    if artifact.receipt_ref != stage.model_ref.uri:
        raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The Stage model differs from its pinned model source.")
    return StageView(ref, stage, source, record.digest)


def read_design_history(binding: ProjectBinding, branch_id: str = "main") -> DesignHistory:
    branches = _branches(binding)
    if not branches:
        return DesignHistory(binding.project_id, (), branch_id, ())
    history = binding.design_history(branch_id)
    return DesignHistory(binding.project_id, branches, branch_id, tuple(read_stage(binding, ref) for ref, _ in history))


def _retain_stage(binding: ProjectBinding, stage: DesignStage) -> ProjectRecordRef:
    run = binding.load_run(stage.candidate_id)
    return binding.repository.put_json(
        run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run.run_id),
        record_kind=DESIGN_STAGE, payload={"schema": "DesignStage@1", **stage.to_dict()},
    )


def _stage_from_model(
    binding: ProjectBinding, *, source: ModelSource, artifact: ArtifactRecord,
    runner_ref: ProjectRecordRef, parent: ProjectRecordRef | None,
    branch_id: str, label: str, accepted_by: str,
) -> DesignStage:
    record_ref, _, receipt = _exact_runner(binding, source.run_id, runner_ref)
    if receipt.get("design_state_digest") != source.state_digest or (
        artifact.run_id, artifact.design_state_digest, artifact.sha256, artifact.format
    ) != (source.run_id, source.state_digest, source.asset_sha256, "3dm"):
        raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The exact model and runner state do not agree.")
    require_complete_model(artifact, receipt)
    return DesignStage(
        parent_stage=parent, record_ref=record_ref,
        model_ref=record_ref_from_uri(artifact.receipt_ref, binding.project_id),
        model_sha256=source.asset_sha256, runner_ref=runner_ref,
        candidate_id=source.run_id, branch_id=branch_id, label=label,
        accepted_by=accepted_by,
    )


def initialize_design_stage(
    binding: ProjectBinding, *, model_source: ModelSource, branch_id: str = "main", label: str = "S0",
    accepted_by: str = "studio:explicit-user-action",
) -> StageView:
    require_identifier(branch_id, "branch_id")
    branches = binding.repository.read_design_branches()
    if branch_id in branches:
        branch = DesignBranch.from_dict(branches[branch_id])
        first = read_stage(binding, branch.fork_stage)
        if first.stage.parent_stage is None and first.stage.branch_id == branch_id and first.model_source == model_source:
            return first
        raise StudioError(409, "DESIGN_BRANCH_EXISTS", "This design branch already has a committed starting point.")
    projection = project_state(binding, model_source.run_id)
    artifact = require_model_source(binding, model_source, projection)
    refs = [ref for ref in binding.record_refs(model_source.run_id)
            if record_kind(ref) == RUNNER_RUN_RECEIPT and binding.repository.load_json(ref) == projection.reference.receipt]
    if len(refs) != 1:
        raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The initial model does not have one exact retained runner source.")
    stage = _stage_from_model(binding, source=model_source, artifact=artifact, runner_ref=refs[0],
                              parent=None, branch_id=branch_id, label=label, accepted_by=accepted_by)
    ref = _retain_stage(binding, stage)
    branch = initialize_branch(branch_id, ref)
    try:
        binding.repository.compare_and_swap_design_branch(branch_id=branch_id, expected_head=None, branch=branch.to_dict())
    except StaleDesignBranch as exc:
        # A duplicate request may have won the same creation concurrently.
        existing = _branch(binding, branch_id)
        first = read_stage(binding, existing.fork_stage)
        if first.stage.parent_stage is None and first.stage.branch_id == branch_id and first.model_source == model_source:
            return first
        raise StudioError(409, "DESIGN_BRANCH_EXISTS", "Another initial Stage established this design branch.") from exc
    return read_stage(binding, ref)


def _accepted_retry(
    binding: ProjectBinding, branch_id: str, candidate_id: str, expected_head: ProjectRecordRef,
) -> StageView | None:
    for ref, stage in binding.design_history(branch_id):
        if (stage.branch_id, stage.candidate_id, stage.parent_stage) == (branch_id, candidate_id, expected_head):
            return read_stage(binding, ref)
    return None


def accept_design_candidate(
    binding: ProjectBinding, *, candidate_id: str, branch_id: str,
    expected_head: ProjectRecordRef, events: StudioEventSink, label: str | None = None,
    accepted_by: str = "studio:explicit-user-action",
) -> StageView:
    require_identifier(candidate_id, "candidate_id")
    branch = _branch(binding, branch_id)
    repeated = _accepted_retry(binding, branch_id, candidate_id, expected_head)
    if repeated is not None:
        return repeated
    if branch.head_stage != expected_head:
        raise StudioError(409, "DESIGN_BRANCH_STALE", "The design branch changed. Review the candidate against its new head.")
    delta = read_candidate_delta(binding, candidate_id)
    if delta.get("source_stage_ref") != expected_head.to_dict():
        raise StudioError(409, "CANDIDATE_STAGE_MISMATCH", "The candidate was not produced from this exact committed Stage.")
    candidate = describe(binding, None, candidate_id=candidate_id, job_id=None, status=SUCCEEDED)
    runner_ref = record_ref_from_uri(candidate.receipt_ref, binding.project_id)
    _, record, _ = _exact_runner(binding, candidate_id, runner_ref)
    replayed = replay_candidate(binding, candidate_id)
    if replayed.digest != record.digest:
        raise StudioError(409, "CANDIDATE_REPLAY_MISMATCH", "The saved candidate changes do not reproduce its retained result.")
    validation = validate_design_candidate(expected_head, candidate, binding=binding, events=events)
    if not validation.review_ready:
        raise StudioError(409, "CANDIDATE_NOT_READY", "The candidate is not ready for acceptance: " + ", ".join(validation.blocked_by))
    models = [row for row in candidate.artifacts if row.available and row.format == "3dm"
              and row.design_state_digest == candidate.state_digest and row.sha256]
    complete = [row for row in models if row.representation == "composed"]
    choices = complete or models
    if len(choices) != 1:
        raise StudioError(409, "CANDIDATE_MODEL_AMBIGUOUS", "Accept a candidate with one exact complete model, rather than a set of separate exports.")
    artifact = choices[0]
    source = ModelSource(candidate_id, candidate.state_digest, artifact.sha256)
    projection = project_state(binding, candidate_id, source_stage_ref=expected_head)
    resolved = require_model_source(binding, source, projection)
    if resolved.receipt_ref != artifact.receipt_ref:
        raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The candidate model has competing source records.")
    stage = _stage_from_model(binding, source=source, artifact=artifact, runner_ref=runner_ref,
                              parent=expected_head, branch_id=branch_id,
                              label=label or f"S{len(binding.design_history(branch_id))}", accepted_by=accepted_by)
    ref = _retain_stage(binding, stage)
    advanced = advance_branch(branch, expected_head=expected_head, candidate_base=expected_head, stage_ref=ref, stage=stage)
    try:
        binding.repository.compare_and_swap_design_branch(branch_id=branch_id, expected_head=expected_head, branch=advanced.to_dict())
    except StaleDesignBranch as exc:
        repeated = _accepted_retry(binding, branch_id, candidate_id, expected_head)
        if repeated is not None:
            return repeated
        raise StudioError(409, "DESIGN_BRANCH_STALE", "Another candidate advanced the design branch; this candidate remains available.") from exc
    return read_stage(binding, ref)


def fork_design_branch(
    binding: ProjectBinding, *, branch_id: str, parent_branch: str, stage_ref: ProjectRecordRef,
) -> DesignBranch:
    source = _branch(binding, parent_branch)
    if stage_ref not in {ref for ref, _ in binding.design_history(parent_branch)}:
        raise StudioError(409, "DESIGN_FORK_SOURCE_MISMATCH", "The fork Stage does not belong to the selected history line.")
    read_stage(binding, stage_ref)
    branch = fork_branch(source, new_branch_id=branch_id, stage_ref=stage_ref)
    try:
        saved = binding.repository.compare_and_swap_design_branch(branch_id=branch_id, expected_head=None, branch=branch.to_dict())
        return DesignBranch.from_dict(saved)
    except StaleDesignBranch as exc:
        existing = _branch(binding, branch_id)
        if (existing.parent_branch, existing.fork_stage) == (parent_branch, stage_ref):
            return existing
        raise StudioError(409, "DESIGN_BRANCH_EXISTS", "The branch name already belongs to a different history line.") from exc
