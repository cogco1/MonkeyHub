"""Studio acceptance and historical views over portfolio values and P036.

There is no history store here. Committed nodes are the records reachable from
P036 design branches; candidate contents and model bytes remain in their runs.

**Who accepted it.** ``DesignStage.accepted_by`` keeps its original contract:
it is the actor id. New accepted Stage records may additionally retain a small
``acceptance_attribution`` extension beside the structural Stage fields. That
extension binds the winning acceptance attempt to its event id, time,
authentication state and surface without changing portfolio semantics. One
``AuditEvent@1`` can therefore be reconstructed idempotently from the committed
winner after a crash, even when the retry arrives through another surface.

A Stage accepted before this evidence existed reads back with no acceptance
evidence, which is what it is, rather than an actor invented for it. An event
that does not agree with the committed Stage in every fact, or that no retained
attribution vouches for, is refused on the read rather than displayed as an
acceptance the Stage cannot confirm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import AUDIT_EVENT, DESIGN_STAGE, RUNNER_RUN_RECEIPT
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri, require_identifier
from archflow.project.repository import ProjectRepositoryError, StaleDesignBranch
from archflow.state.design_portfolio import (
    DesignBranch,
    DesignStage,
    advance_branch,
    fork_branch,
    initialize_branch,
)
from archflow.state.state_record import StateRecord

from .artifacts import (
    ArtifactRecord,
    ModelSource,
    require_complete_model,
    require_model_source,
)
from .authentication import ActorAttribution, LOCAL_ACTOR_ID, ORIGIN_STUDIO
from .binding import retained_sources
from .binding import ProjectBinding, ReferenceRun, record_kind
from .candidate import describe, read_candidate_delta, replay_candidate
from .jobs import SUCCEEDED
from .projection import project_state
from .validation import validate_design_candidate
from ..ports import StudioEventSink
from ..transport.errors import StudioError


AUDIT_EVENT_SCHEMA = "AuditEvent@1"
ACCEPTANCE_ATTRIBUTION_SCHEMA = "AcceptanceAttribution@1"
DESIGN_ACCEPTED = "design.accepted"
EVENT_SUCCEEDED = "succeeded"
_STAGE_EXTENSION_KEY = "acceptance_attribution"


@dataclass(frozen=True, slots=True)
class RetainedAcceptanceAttribution:
    """Audit-safe facts that make one committed acceptance attempt exact."""

    event_id: str
    occurred_at: str
    actor_id: str
    authenticated: bool
    origin: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ACCEPTANCE_ATTRIBUTION_SCHEMA,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "actor_id": self.actor_id,
            "authenticated": self.authenticated,
            "origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceEvidence:
    """The retained actor/origin binding for one accepted Stage."""

    event_id: str
    occurred_at: str
    action: str
    status: str
    actor_id: str
    authenticated: bool
    origin: str
    audit_ref: ProjectRecordRef


@dataclass(frozen=True, slots=True)
class StageView:
    ref: ProjectRecordRef
    stage: DesignStage
    model_source: ModelSource
    record_digest: str
    acceptance: AcceptanceEvidence | None = None
    acceptance_attribution: RetainedAcceptanceAttribution | None = None


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
        raise StudioError(
            422,
            "DESIGN_STAGE_REF_INVALID",
            "Provide a retained design Stage reference in this project.",
        ) from exc
    return ref


def _branches(binding: ProjectBinding) -> tuple[DesignBranch, ...]:
    return tuple(
        DesignBranch.from_dict(row)
        for _, row in sorted(binding.repository.read_design_branches().items())
    )


def _branch(binding: ProjectBinding, branch_id: str) -> DesignBranch:
    rows = binding.repository.read_design_branches()
    if branch_id not in rows:
        raise StudioError(
            404,
            "DESIGN_BRANCH_NOT_FOUND",
            f"Design branch {branch_id!r} does not exist.",
        )
    return DesignBranch.from_dict(rows[branch_id])


def _exact_runner(
    binding: ProjectBinding,
    run_id: str,
    runner_ref: ProjectRecordRef,
) -> tuple[ProjectRecordRef, StateRecord, dict]:
    if record_kind(runner_ref) != RUNNER_RUN_RECEIPT:
        raise StudioError(
            409,
            "DESIGN_STAGE_SOURCE_MISMATCH",
            "The Stage source is not a retained runner receipt.",
        )
    receipt = binding.repository.load_json(runner_ref)
    ref, record = binding.exact_state_record(
        ReferenceRun(
            run=binding.load_run(run_id),
            source="design-history",
            receipt=receipt,
        )
    )
    if not receipt.get("seat_execution_complete"):
        raise StudioError(
            409,
            "CANDIDATE_NOT_FINISHED",
            "The source run did not finish its seats.",
        )
    return ref, record, receipt


def _new_acceptance_attribution(
    attribution: ActorAttribution,
) -> RetainedAcceptanceAttribution:
    return RetainedAcceptanceAttribution(
        event_id=f"aud-{uuid4().hex[:12]}",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        actor_id=attribution.actor_id,
        authenticated=attribution.authenticated,
        origin=attribution.origin,
    )


def _retained_acceptance_attribution(
    binding: ProjectBinding,
    stage_ref: ProjectRecordRef,
    stage: DesignStage,
) -> RetainedAcceptanceAttribution | None:
    """Read the optional winner extension without changing DesignStage itself."""

    payload = binding.repository.load_json(stage_ref)
    value = payload.get(_STAGE_EXTENSION_KEY)
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "event_id",
        "occurred_at",
        "actor_id",
        "authenticated",
        "origin",
    }:
        raise StudioError(
            409,
            "ACCEPTANCE_ATTRIBUTION_INVALID",
            "The committed Stage carries malformed acceptance attribution facts.",
        )
    event_id = value.get("event_id")
    occurred_at = value.get("occurred_at")
    actor_id = value.get("actor_id")
    authenticated = value.get("authenticated")
    origin = value.get("origin")
    if value.get("schema") != ACCEPTANCE_ATTRIBUTION_SCHEMA:
        raise StudioError(
            409,
            "ACCEPTANCE_ATTRIBUTION_INVALID",
            "The committed Stage carries an unsupported acceptance attribution schema.",
        )
    try:
        moment = datetime.fromisoformat(occurred_at)
    except (TypeError, ValueError) as exc:
        raise StudioError(
            409,
            "ACCEPTANCE_ATTRIBUTION_INVALID",
            "The committed Stage carries an invalid acceptance time.",
        ) from exc
    if (
        not isinstance(event_id, str)
        or not event_id.startswith("aud-")
        or len(event_id) <= 4
        or moment.tzinfo is None
        or not isinstance(actor_id, str)
        or not actor_id
        or actor_id != stage.accepted_by
        or type(authenticated) is not bool
        or not isinstance(origin, str)
        or not origin
    ):
        raise StudioError(
            409,
            "ACCEPTANCE_ATTRIBUTION_INVALID",
            "The committed Stage carries invalid acceptance attribution facts.",
        )
    return RetainedAcceptanceAttribution(
        event_id=event_id,
        occurred_at=occurred_at,
        actor_id=actor_id,
        authenticated=authenticated,
        origin=origin,
    )


def read_stage(binding: ProjectBinding, ref: ProjectRecordRef) -> StageView:
    stage = binding.design_stage(ref)
    attribution = _retained_acceptance_attribution(binding, ref, stage)
    record_ref, record, receipt = _exact_runner(
        binding,
        stage.candidate_id,
        stage.runner_ref,
    )
    if record_ref != stage.record_ref:
        raise StudioError(
            409,
            "DESIGN_STAGE_SOURCE_MISMATCH",
            "The Stage record differs from its pinned runner source.",
        )
    source = ModelSource(
        stage.candidate_id,
        receipt.get("design_state_digest"),
        stage.model_sha256,
    )
    projection = project_state(binding, source_stage_ref=ref)
    artifact = require_model_source(binding, source, projection)
    if artifact.receipt_ref != stage.model_ref.uri:
        raise StudioError(
            409,
            "DESIGN_STAGE_SOURCE_MISMATCH",
            "The Stage model differs from its pinned model source.",
        )
    evidence = read_acceptance(
        binding,
        ref,
        stage,
        record_digest=record.digest,
        attribution=attribution,
    )
    return StageView(
        ref,
        stage,
        source,
        record.digest,
        evidence,
        attribution,
    )


def read_design_history(
    binding: ProjectBinding,
    branch_id: str = "main",
) -> DesignHistory:
    branches = _branches(binding)
    if not branches:
        return DesignHistory(binding.project_id, (), branch_id, ())
    history = binding.design_history(branch_id)
    return DesignHistory(
        binding.project_id,
        branches,
        branch_id,
        tuple(read_stage(binding, ref) for ref, _ in history),
    )


def _retain_stage(
    binding: ProjectBinding,
    stage: DesignStage,
    *,
    attribution: RetainedAcceptanceAttribution | None = None,
) -> ProjectRecordRef:
    run = binding.load_run(stage.candidate_id)
    payload: dict[str, Any] = {
        "schema": "DesignStage@1",
        **stage.to_dict(),
    }
    if attribution is not None:
        if attribution.actor_id != stage.accepted_by:
            raise StudioError(
                409,
                "ACCEPTANCE_ATTRIBUTION_INVALID",
                "The Stage actor and acceptance attribution disagree.",
            )
        payload[_STAGE_EXTENSION_KEY] = attribution.to_dict()
    return binding.repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW,
            run_id=run.run_id,
        ),
        record_kind=DESIGN_STAGE,
        payload=payload,
    )


def _acceptance_payload(
    binding: ProjectBinding,
    *,
    stage: DesignStage,
    stage_ref: ProjectRecordRef,
    attribution: RetainedAcceptanceAttribution,
    record_digest: str,
) -> dict[str, Any]:
    return {
        "schema": AUDIT_EVENT_SCHEMA,
        "eventId": attribution.event_id,
        "occurredAt": attribution.occurred_at,
        "action": DESIGN_ACCEPTED,
        "status": EVENT_SUCCEEDED,
        "projectId": binding.project_id,
        "actorId": attribution.actor_id,
        "authenticatedActor": attribution.authenticated,
        "origin": attribution.origin,
        "branchId": stage.branch_id,
        "candidateId": stage.candidate_id,
        "baseStageRef": (
            None if stage.parent_stage is None else stage.parent_stage.uri
        ),
        "resultStageRef": stage_ref.uri,
        "resultRecordDigest": record_digest,
    }


def _evidence_from(
    payload: Mapping[str, Any],
    ref: ProjectRecordRef,
) -> AcceptanceEvidence:
    """Read one payload already proven to be this Stage's own event.

    Every field is taken as it was retained. ``authenticatedActor`` in
    particular is not coerced: a value that is not the retained bool is a
    refusal in ``read_acceptance``, never an authentication this reader
    invents by calling ``bool()`` on it.
    """

    return AcceptanceEvidence(
        event_id=payload["eventId"],
        occurred_at=payload["occurredAt"],
        action=payload["action"],
        status=payload["status"],
        actor_id=payload["actorId"],
        authenticated=payload["authenticatedActor"],
        origin=payload["origin"],
        audit_ref=ref,
    )


def _retain_acceptance(
    binding: ProjectBinding,
    *,
    stage: DesignStage,
    stage_ref: ProjectRecordRef,
    attribution: RetainedAcceptanceAttribution,
    record_digest: str,
) -> AcceptanceEvidence:
    """Idempotently retain the event selected by the committed Stage winner."""

    run = binding.load_run(stage.candidate_id)
    payload = _acceptance_payload(
        binding,
        stage=stage,
        stage_ref=stage_ref,
        attribution=attribution,
        record_digest=record_digest,
    )
    try:
        ref = binding.repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_REVIEW,
                run_id=run.run_id,
            ),
            record_kind=AUDIT_EVENT,
            payload=payload,
        )
    except (ProjectRepositoryError, OSError, ValueError) as exc:
        raise StudioError(
            500,
            "ACCEPTANCE_EVIDENCE_NOT_RETAINED",
            f"Stage {stage_ref.uri} is committed on branch {stage.branch_id}, and the "
            "acceptance evidence naming the actor that authorized it could not be "
            "retained. Retry the same acceptance to recover the winner's evidence.",
        ) from exc
    return _evidence_from(payload, ref)


def _disagreements(
    payload: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> tuple[str, ...]:
    """The field names two acceptance payloads differ on, without their values."""

    return tuple(sorted(
        set(payload).symmetric_difference(expected)
        | {
            key
            for key in set(payload) & set(expected)
            if payload[key] != expected[key]
        }
    ))


def read_acceptance(
    binding: ProjectBinding,
    stage_ref: ProjectRecordRef,
    stage: DesignStage,
    *,
    record_digest: str,
    attribution: RetainedAcceptanceAttribution | None,
) -> AcceptanceEvidence | None:
    """The retained acceptance evidence for one committed Stage, if it has any.

    An event found beside the Stage is only that Stage's evidence when it is
    exactly the event this Stage's own acceptance would have written: the same
    project, candidate, branch, base Stage, result Stage and result record
    digest, the same ``design.accepted``/``succeeded`` outcome, and the same
    actor, time, event id, origin and authentication state the committed Stage
    retained. That is one comparison against ``_acceptance_payload``, the
    writer's own shape, so the reader cannot drift away from what is written.

    A Stage that retained no attribution vouches for no event: it reads back
    with none, and an event that turns up beside it is refused rather than
    displayed as the acceptance nothing on the Stage can confirm.
    """

    expected = (
        None
        if attribution is None
        else _acceptance_payload(
            binding,
            stage=stage,
            stage_ref=stage_ref,
            attribution=attribution,
            record_digest=record_digest,
        )
    )
    run = binding.load_run(stage.candidate_id)
    found: list[AcceptanceEvidence] = []
    for ref in binding.repository.list_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW,
            run_id=run.run_id,
        ),
    ):
        if record_kind(ref) != AUDIT_EVENT:
            continue
        payload = binding.repository.load_json(ref)
        if (
            payload.get("schema") != AUDIT_EVENT_SCHEMA
            or payload.get("resultStageRef") != stage_ref.uri
        ):
            continue
        if expected is None:
            raise StudioError(
                409,
                "ACCEPTANCE_EVIDENCE_MISMATCH",
                "This committed Stage retained no acceptance attribution, and the "
                "acceptance evidence beside it cannot be confirmed as its own.",
            )
        # ``1 == True`` in Python, so equality alone would let an integer pass
        # as the retained authentication state and read back as authenticated.
        if type(payload.get("authenticatedActor")) is not bool:
            raise StudioError(
                409,
                "ACCEPTANCE_EVIDENCE_MISMATCH",
                "The acceptance evidence does not state its authentication as a "
                "retained true or false.",
            )
        differing = _disagreements(payload, expected)
        if differing:
            raise StudioError(
                409,
                "ACCEPTANCE_EVIDENCE_MISMATCH",
                "The acceptance evidence disagrees with the committed Stage on: "
                + ", ".join(differing)
                + ".",
            )
        found.append(_evidence_from(payload, ref))
    if len(found) > 1:
        raise StudioError(
            409,
            "ACCEPTANCE_EVIDENCE_CONFLICT",
            "This committed Stage has competing acceptance evidence.",
        )
    return found[0] if found else None


def _ensure_acceptance(
    binding: ProjectBinding,
    view: StageView,
) -> StageView:
    if view.acceptance is not None or view.acceptance_attribution is None:
        return view
    _retain_acceptance(
        binding,
        stage=view.stage,
        stage_ref=view.ref,
        attribution=view.acceptance_attribution,
        record_digest=view.record_digest,
    )
    return read_stage(binding, view.ref)


def _stage_from_model(
    binding: ProjectBinding,
    *,
    source: ModelSource,
    artifact: ArtifactRecord,
    runner_ref: ProjectRecordRef,
    parent: ProjectRecordRef | None,
    branch_id: str,
    label: str,
    accepted_by: str,
) -> DesignStage:
    record_ref, _, receipt = _exact_runner(binding, source.run_id, runner_ref)
    if receipt.get("design_state_digest") != source.state_digest or (
        artifact.run_id,
        artifact.design_state_digest,
        artifact.sha256,
        artifact.format,
    ) != (
        source.run_id,
        source.state_digest,
        source.asset_sha256,
        "3dm",
    ):
        raise StudioError(
            409,
            "DESIGN_STAGE_SOURCE_MISMATCH",
            "The exact model and runner state do not agree.",
        )
    require_complete_model(artifact, receipt)
    return DesignStage(
        parent_stage=parent,
        record_ref=record_ref,
        model_ref=record_ref_from_uri(
            artifact.receipt_ref,
            binding.project_id,
        ),
        model_sha256=source.asset_sha256,
        runner_ref=runner_ref,
        candidate_id=source.run_id,
        branch_id=branch_id,
        label=label,
        accepted_by=accepted_by,
    )


@retained_sources
def initialize_design_stage(
    binding: ProjectBinding,
    *,
    model_source: ModelSource,
    branch_id: str = "main",
    label: str = "S0",
    accepted_by: str = LOCAL_ACTOR_ID,
) -> StageView:
    require_identifier(branch_id, "branch_id")
    branches = binding.repository.read_design_branches()
    if branch_id in branches:
        branch = DesignBranch.from_dict(branches[branch_id])
        first = read_stage(binding, branch.fork_stage)
        if (
            first.stage.parent_stage is None
            and first.stage.branch_id == branch_id
            and first.model_source == model_source
        ):
            return first
        raise StudioError(
            409,
            "DESIGN_BRANCH_EXISTS",
            "This design branch already has a committed starting point.",
        )
    projection = project_state(binding, model_source.run_id)
    artifact = require_model_source(binding, model_source, projection)
    refs = [
        ref
        for ref in binding.record_refs(model_source.run_id)
        if record_kind(ref) == RUNNER_RUN_RECEIPT
        and binding.repository.load_json(ref) == projection.reference.receipt
    ]
    if len(refs) != 1:
        raise StudioError(
            409,
            "DESIGN_STAGE_SOURCE_MISMATCH",
            "The initial model does not have one exact retained runner source.",
        )
    stage = _stage_from_model(
        binding,
        source=model_source,
        artifact=artifact,
        runner_ref=refs[0],
        parent=None,
        branch_id=branch_id,
        label=label,
        accepted_by=accepted_by,
    )
    ref = _retain_stage(binding, stage)
    branch = initialize_branch(branch_id, ref)
    try:
        binding.repository.compare_and_swap_design_branch(
            branch_id=branch_id,
            expected_head=None,
            branch=branch.to_dict(),
        )
    except StaleDesignBranch as exc:
        existing = _branch(binding, branch_id)
        first = read_stage(binding, existing.fork_stage)
        if (
            first.stage.parent_stage is None
            and first.stage.branch_id == branch_id
            and first.model_source == model_source
        ):
            return first
        raise StudioError(
            409,
            "DESIGN_BRANCH_EXISTS",
            "Another initial Stage established this design branch.",
        ) from exc
    return read_stage(binding, ref)


def _accepted_retry(
    binding: ProjectBinding,
    branch_id: str,
    candidate_id: str,
    expected_head: ProjectRecordRef,
) -> StageView | None:
    for ref, stage in binding.design_history(branch_id):
        if (
            stage.branch_id,
            stage.candidate_id,
            stage.parent_stage,
        ) == (
            branch_id,
            candidate_id,
            expected_head,
        ):
            return read_stage(binding, ref)
    return None


@retained_sources
def accept_design_candidate(
    binding: ProjectBinding,
    *,
    candidate_id: str,
    branch_id: str,
    expected_head: ProjectRecordRef,
    events: StudioEventSink,
    label: str | None = None,
    attribution: ActorAttribution = ActorAttribution(
        LOCAL_ACTOR_ID,
        False,
        ORIGIN_STUDIO,
    ),
) -> StageView:
    require_identifier(candidate_id, "candidate_id")
    branch = _branch(binding, branch_id)
    repeated = _accepted_retry(
        binding,
        branch_id,
        candidate_id,
        expected_head,
    )
    if repeated is not None:
        return _ensure_acceptance(binding, repeated)
    if branch.head_stage != expected_head:
        raise StudioError(
            409,
            "DESIGN_BRANCH_STALE",
            "The design branch changed. Review the candidate against its new head.",
        )
    delta = read_candidate_delta(binding, candidate_id)
    if delta.get("source_stage_ref") != expected_head.to_dict():
        raise StudioError(
            409,
            "CANDIDATE_STAGE_MISMATCH",
            "The candidate was not produced from this exact committed Stage.",
        )
    candidate = describe(
        binding,
        None,
        candidate_id=candidate_id,
        job_id=None,
        status=SUCCEEDED,
    )
    runner_ref = record_ref_from_uri(candidate.receipt_ref, binding.project_id)
    _, record, _ = _exact_runner(binding, candidate_id, runner_ref)
    replayed = replay_candidate(binding, candidate_id)
    if replayed.digest != record.digest:
        raise StudioError(
            409,
            "CANDIDATE_REPLAY_MISMATCH",
            "The saved candidate changes do not reproduce its retained result.",
        )
    validation = validate_design_candidate(
        expected_head,
        candidate,
        binding=binding,
        events=events,
    )
    if not validation.review_ready:
        raise StudioError(
            409,
            "CANDIDATE_NOT_READY",
            "The candidate is not ready for acceptance: "
            + ", ".join(validation.blocked_by),
        )
    models = [
        row
        for row in candidate.artifacts
        if row.available
        and row.format == "3dm"
        and row.design_state_digest == candidate.state_digest
        and row.sha256
    ]
    complete = [row for row in models if row.representation == "composed"]
    choices = complete or models
    if len(choices) != 1:
        raise StudioError(
            409,
            "CANDIDATE_MODEL_AMBIGUOUS",
            "Accept a candidate with one exact complete model, rather than a set of separate exports.",
        )
    artifact = choices[0]
    source = ModelSource(
        candidate_id,
        candidate.state_digest,
        artifact.sha256,
    )
    projection = project_state(
        binding,
        candidate_id,
        source_stage_ref=expected_head,
    )
    resolved = require_model_source(binding, source, projection)
    if resolved.receipt_ref != artifact.receipt_ref:
        raise StudioError(
            409,
            "DESIGN_STAGE_SOURCE_MISMATCH",
            "The candidate model has competing source records.",
        )

    retained_attribution = _new_acceptance_attribution(attribution)
    stage = _stage_from_model(
        binding,
        source=source,
        artifact=artifact,
        runner_ref=runner_ref,
        parent=expected_head,
        branch_id=branch_id,
        label=label or f"S{len(binding.design_history(branch_id))}",
        accepted_by=retained_attribution.actor_id,
    )
    ref = _retain_stage(
        binding,
        stage,
        attribution=retained_attribution,
    )
    advanced = advance_branch(
        branch,
        expected_head=expected_head,
        candidate_base=expected_head,
        stage_ref=ref,
        stage=stage,
    )
    try:
        binding.repository.compare_and_swap_design_branch(
            branch_id=branch_id,
            expected_head=expected_head,
            branch=advanced.to_dict(),
        )
    except StaleDesignBranch as exc:
        repeated = _accepted_retry(
            binding,
            branch_id,
            candidate_id,
            expected_head,
        )
        if repeated is not None:
            return _ensure_acceptance(binding, repeated)
        raise StudioError(
            409,
            "DESIGN_BRANCH_STALE",
            "Another candidate advanced the design branch; this candidate remains available.",
        ) from exc
    except (ProjectRepositoryError, OSError) as exc:
        repeated = _accepted_retry(
            binding,
            branch_id,
            candidate_id,
            expected_head,
        )
        if repeated is not None:
            return _ensure_acceptance(binding, repeated)
        raise StudioError(
            500,
            "DESIGN_BRANCH_COMMIT_FAILED",
            "The design branch could not be advanced; no accepted Stage was recovered.",
        ) from exc

    _retain_acceptance(
        binding,
        stage=stage,
        stage_ref=ref,
        attribution=retained_attribution,
        record_digest=record.digest,
    )
    return read_stage(binding, ref)


@retained_sources
def fork_design_branch(
    binding: ProjectBinding,
    *,
    branch_id: str,
    parent_branch: str,
    stage_ref: ProjectRecordRef,
) -> DesignBranch:
    source = _branch(binding, parent_branch)
    if stage_ref not in {
        ref for ref, _ in binding.design_history(parent_branch)
    }:
        raise StudioError(
            409,
            "DESIGN_FORK_SOURCE_MISMATCH",
            "The fork Stage does not belong to the selected history line.",
        )
    read_stage(binding, stage_ref)
    branch = fork_branch(
        source,
        new_branch_id=branch_id,
        stage_ref=stage_ref,
    )
    try:
        saved = binding.repository.compare_and_swap_design_branch(
            branch_id=branch_id,
            expected_head=None,
            branch=branch.to_dict(),
        )
        return DesignBranch.from_dict(saved)
    except StaleDesignBranch as exc:
        existing = _branch(binding, branch_id)
        if (
            existing.parent_branch,
            existing.fork_stage,
        ) == (
            parent_branch,
            stage_ref,
        ):
            return existing
        raise StudioError(
            409,
            "DESIGN_BRANCH_EXISTS",
            "The branch name already belongs to a different history line.",
        ) from exc
