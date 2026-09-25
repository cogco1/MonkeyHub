"""Studio acceptance, candidate admission and historical views over P036.

There is no history store here. Committed nodes are the records reachable from
P036 design branches; candidate contents and model bytes remain in their runs.
Admission verdicts (#294) are the one retained addition: each closed loop's
``CandidateAdmission@1`` in the fixed ``studio-admissions`` run, written only
through the preflight Stage acceptance also uses (see "Candidate admission").

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

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import os
import re
import threading
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4
import weakref

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    AUDIT_EVENT,
    CANDIDATE_ADMISSION,
    DELIBERATION_EPISODE,
    DESIGN_STAGE,
    RUNNER_RUN_RECEIPT,
    STUDIO_WORKING_COPY,
)
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
from .authentication import ActorAttribution, LOCAL_ACTOR_ID, ORIGIN_HUB, ORIGIN_HUB_AGENT, ORIGIN_STUDIO
from .binding import retained_sources
from .binding import ProjectBinding, ReferenceRun, record_kind
from .candidate import CandidateRun, describe, read_candidate_delta, replay_candidate
from .compare import shapes_of
from .episodes import (
    ACCEPTED as EPISODE_ACCEPTED,
    SCHEMA as EPISODE_SCHEMA,
    _working_copy_from,
    evidence_generation,
)
from .jobs import SUCCEEDED
from .projection import project_state
from .validation import validate_candidate, validate_design_candidate
from .working_draft import lineage_of, resolve_working_source
from ..ports import StudioEventSink
from ..transport.errors import StudioError, error_sentence


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
    # The project's admitted Candidates and their Studies (#294), derived on read.
    pool: CandidatePool = field(default_factory=lambda: CandidatePool())


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
    *,
    include_rejected: bool = False,
) -> DesignHistory:
    branches = _branches(binding)
    stages: tuple[StageView, ...] = ()
    if branches:
        history = binding.design_history(branch_id)
        stages = tuple(read_stage(binding, ref) for ref, _ in history)
    # An unstaged project still has a pool: its Candidates sit under no Stage.
    return DesignHistory(
        binding.project_id,
        branches,
        branch_id,
        stages,
        candidate_pool(binding, branch_id=branch_id, include_rejected=include_rejected),
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


# ---- The closed-loop gate ---------------------------------------------------
#
# Stage acceptance and candidate admission read one preflight, so there is one
# gate and not two (#294 section 4.3): C2 the result finished as a Studio
# harness run, C1 its retained changes replay to it, C3 review readiness against
# its exact base, and C5 exactly one complete model. Which clause refused is
# kept with the refusal; its code, status and wording are the refusal's own.


class ClauseRefused(StudioError):
    """One completion-contract clause refused, answered in the refusal's own words."""

    def __init__(self, clause: str, refusal: StudioError) -> None:
        super().__init__(refusal.status, refusal.code, refusal.detail)
        self.clause = clause
        self.refusal = refusal

    def body(self) -> dict[str, object]:
        return self.refusal.body()


@contextmanager
def _clause(name: str) -> Iterator[None]:
    try:
        yield
    except ClauseRefused:
        raise
    except StudioError as exc:
        raise ClauseRefused(name, exc) from exc


@dataclass(frozen=True, slots=True)
class CompletedResult:
    """What the gate read off one retained result run.

    ``review_ready`` and ``blocked_by`` are the review-readiness verdict as it
    was read. Whether a failing verdict refuses is the caller's rule: Stage
    acceptance and an Agent's admission refuse, and a human admission keeps it
    as the Candidate's violation marker (owner decision Q2).
    """

    candidate: CandidateRun
    runner_ref: ProjectRecordRef
    record_digest: str
    review_ready: bool
    blocked_by: tuple[str, ...]
    artifact: ArtifactRecord
    model_source: ModelSource


def _finished(binding: ProjectBinding, candidate_id: str) -> tuple[CandidateRun, ProjectRecordRef, StateRecord]:
    """C2: the retained harness result, its exact runner receipt and finished seats."""

    with _clause("C2"):
        candidate = describe(
            binding,
            None,
            candidate_id=candidate_id,
            job_id=None,
            status=SUCCEEDED,
        )
        runner_ref = record_ref_from_uri(candidate.receipt_ref, binding.project_id)
        _, record, _ = _exact_runner(binding, candidate_id, runner_ref)
    return candidate, runner_ref, record


def completed_result(
    binding: ProjectBinding,
    candidate_id: str,
    *,
    stage_ref: ProjectRecordRef | None,
    events: StudioEventSink,
    require_ready: bool,
    purpose: str = "acceptance",
) -> CompletedResult:
    """Read C2, C1, C3 and C5 off one result, in that order, or refuse by clause.

    A Stage-based result is validated against its exact base Stage; an
    unstaged one against the published version, the rule its validation
    readout already uses.
    """

    candidate, runner_ref, record = _finished(binding, candidate_id)
    with _clause("C1"):
        replayed = replay_candidate(binding, candidate_id)
        if replayed.digest != record.digest:
            raise StudioError(
                409,
                "CANDIDATE_REPLAY_MISMATCH",
                "The saved candidate changes do not reproduce its retained result.",
            )
    with _clause("C3"):
        validation = (
            validate_design_candidate(stage_ref, candidate, binding=binding, events=events)
            if stage_ref is not None
            else validate_candidate(binding.head(), candidate, binding=binding, events=events)
        )
        if require_ready and not validation.review_ready:
            raise StudioError(
                409,
                "CANDIDATE_NOT_READY",
                f"The candidate is not ready for {purpose}: "
                + ", ".join(validation.blocked_by),
            )
    with _clause("C5"):
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
            source_stage_ref=stage_ref,
        )
        resolved = require_model_source(binding, source, projection)
        if resolved.receipt_ref != artifact.receipt_ref:
            raise StudioError(
                409,
                "DESIGN_STAGE_SOURCE_MISMATCH",
                "The candidate model has competing source records.",
            )
    return CompletedResult(
        candidate=candidate,
        runner_ref=runner_ref,
        record_digest=record.digest,
        review_ready=validation.review_ready,
        blocked_by=tuple(validation.blocked_by),
        artifact=artifact,
        model_source=source,
    )


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
    rejection = rejected_by(binding, candidate_id)
    if rejection is not None:
        raise StudioError(
            409,
            "CANDIDATE_REJECTED",
            f"Candidate {candidate_id} was rejected ({rejection}); a rejected result is not accepted as a Stage.",
        )
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
    completed = completed_result(
        binding,
        candidate_id,
        stage_ref=expected_head,
        events=events,
        require_ready=True,
    )
    artifact, source, runner_ref = completed.artifact, completed.model_source, completed.runner_ref

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
        record_digest=completed.record_digest,
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


# ---- Candidate admission (#294) ---------------------------------------------
#
# A Candidate is a closed-loop result plus admission, never an execution
# artifact: a run that succeeded is a Run, and becomes a Candidate only through
# one retained, attributable verdict. Each closed loop - a task, one worktree of
# a task, or one human act - retains one ``CandidateAdmission@1`` in the fixed
# ``studio-admissions`` run (owner decision Q1, 2026-09-25). Like
# ``studio-decisions`` that run is asked for by name and never found by
# scanning every run, so reading the pool costs the number of admissions, not
# the number of runs. Lineage is not recorded again; it is derived from each
# run's StudioCandidateDelta@1. An admission advances no branch, moves no
# Working Head and changes no design state.

ADMISSIONS_RUN_ID = "studio-admissions"
ADMISSION_SCHEMA = "CandidateAdmission@1"
ADMITTED = "admitted"
REJECTED = "rejected"
SUPERSEDED = "superseded"
TASK_UI = "ui"
TASK_HUB_CHAT = "hub-chat"
TASK_RETROACTIVE = "retroactive"
TASK_KINDS = (TASK_UI, TASK_HUB_CHAT, TASK_RETROACTIVE)
ORIGIN_RETROACTIVE = "retroactive"
LEGACY_STAGE = "stage"
LEGACY_WORKING_COPY = "working-copy"
LEGACY_EPISODE = "episode"
STUDY_DECLARED = "declared"
STUDY_ADMISSION = "admission"
STUDY_WORKING_COPY = "working-copy"
_WORKING_COPY_SCHEMA = "StudioWorkingCopy@1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UNREADABLE = (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError)


def _detail(exc: BaseException) -> str:
    return exc.detail if isinstance(exc, StudioError) else error_sentence(exc)


def _admission_invalid(message: str) -> StudioError:
    return StudioError(422, "ADMISSION_INVALID", message)


def _admissions_destination() -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=ADMISSIONS_RUN_ID)


@dataclass(frozen=True, slots=True)
class AdmissionRecord:
    """One retained ``CandidateAdmission@1``: the exact ref it is at, and what it says."""

    ref: str
    payload: Mapping[str, Any]

    @property
    def admission_id(self) -> str:
        return str(self.payload["admissionId"])

    @property
    def results(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.payload["results"])

    @property
    def study_id(self) -> str:
        """The Study its results group into: the declared one, else this loop itself."""

        study = self.payload.get("study")
        return self.admission_id if study is None else str(study["id"])


@dataclass(frozen=True, slots=True)
class AdmissionClaim:
    """One live record naming one run, as a result or as an attempt a result superseded."""

    record: AdmissionRecord
    result_run_id: str
    superseded: bool
    outcome: str


@dataclass(frozen=True, slots=True)
class AdmissionStore:
    """Every live admission of a project and the runs those admissions claim.

    ``problems`` names every record that could not be read: readers leave it
    out and say so, and the writer refuses to add a claim beside it.
    """

    records: tuple[AdmissionRecord, ...] = ()
    claims: Mapping[str, tuple[AdmissionClaim, ...]] = field(default_factory=dict)
    problems: tuple[str, ...] = ()

    @property
    def competing(self) -> frozenset[str]:
        return frozenset(run_id for run_id, found in self.claims.items() if len(found) > 1)

    def warnings(self) -> tuple[str, ...]:
        """Unreadable records, then every run that more than one live record claims."""

        return self.problems + tuple(
            f"Run {run_id} is claimed by admissions "
            f"{', '.join(sorted({claim.record.admission_id for claim in found}))}; "
            "it is left out until the claims are reconciled."
            for run_id, found in sorted(self.claims.items())
            if len(found) > 1
        )


def _admissions_run(binding: ProjectBinding, *, create: bool):
    """The admissions run, asked for by name; created only by the first admission."""

    if not binding.repository.layout.run(ADMISSIONS_RUN_ID).root.is_dir():
        if not create:
            return None
        try:
            return binding.repository.create_run(ADMISSIONS_RUN_ID)
        except (ProjectRepositoryError, OSError) as failure:
            raise StudioError(409, "ADMISSION_WRITE_FAILED",
                              "The admission run could not be created in this project.") from failure
    return binding.load_run(ADMISSIONS_RUN_ID)


def _texts(value: Any, *keys: str, required: tuple[str, ...] = ()) -> bool:
    """A mapping whose named fields are text, and null only where that is allowed."""

    return isinstance(value, Mapping) and all(
        isinstance(value.get(key), str) or (key not in required and value.get(key) is None) for key in keys
    )


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _record_problem(binding: ProjectBinding, payload: Mapping[str, Any]) -> str | None:
    """Why one retained payload is not a readable admission of this project, or None.

    Readers leave such a record out and name it; nothing it says is shown.
    """

    if payload.get("schema") != ADMISSION_SCHEMA:
        return "is not a CandidateAdmission@1"
    if payload.get("projectId") != binding.project_id:
        return "belongs to another project"
    if (not _texts(payload, "admissionId", "occurredAt", "previousRevisionRef", "rawLanguage",
                   required=("admissionId", "occurredAt"))
            or not payload["admissionId"].startswith("adm-")):
        return "has no admission id or time"
    study, task, message = payload.get("study"), payload.get("task"), payload.get("messageSource")
    if study is not None and not _texts(study, "id", "label", "baseRunId", "baseStageRef", required=("id",)):
        return "declares an invalid Study"
    actor = payload.get("actor")
    if (not _texts(actor, "actorId", "origin", required=("actorId",))
            or not isinstance(actor.get("authenticated"), (bool, type(None)))
            or not _texts(task, "kind", required=("kind",)) or task["kind"] not in TASK_KINDS
            or not _strings(task.get("ids"))
            or (message is not None and not _texts(message, "sessionId", "messageId",
                                                   required=("sessionId", "messageId")))):
        return "names no actor, task or message it can be read with"
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return "retains no result"
    for row in results:
        if (not _texts(row, "runId", "receiptRef", "recordDigest", "baseStageRef", "label", "summary", "reason",
                       required=("runId", "receiptRef", "recordDigest"))
                or row.get("outcome") not in (ADMITTED, REJECTED) or not _strings(row.get("supersedes"))
                or not (row.get("blockedBy") is None or _strings(row["blockedBy"]))
                or not (row.get("modelSource") is None or (
                    _texts(row["modelSource"], "runId", "stateDigest", "assetSha256",
                           required=("runId", "stateDigest", "assetSha256"))
                    and all(_SHA256.fullmatch(row["modelSource"][key]) for key in ("stateDigest", "assetSha256"))))):
            return "retains a malformed result"
    return None


def _read_store(binding: ProjectBinding) -> AdmissionStore:
    try:
        run = _admissions_run(binding, create=False)
        refs = () if run is None else binding.repository.list_json(
            run=run, destination=_admissions_destination(), record_kind=CANDIDATE_ADMISSION,
        )
        loaded = [(ref, binding.repository.load_json(ref)) for ref in refs]
    except _UNREADABLE as exc:
        return AdmissionStore(problems=(f"The admission records could not be read: {_detail(exc)}",))
    problems: list[str] = []
    records: list[AdmissionRecord] = []
    for ref, payload in loaded:
        problem = _record_problem(binding, payload)
        if problem is None:
            records.append(AdmissionRecord(ref.uri, payload))
        else:
            problems.append(f"Admission record {ref.uri} {problem}; it is left out.")
    # A later revision (#289/#290) retires the record it names; V0 writes none.
    named = {record.payload.get("previousRevisionRef") for record in records} - {None}
    problems.extend(f"An admission revision names {ref}, which this project does not retain."
                    for ref in sorted(named - {record.ref for record in records}))
    live = sorted((record for record in records if record.ref not in named),
                  key=lambda record: (record.payload["occurredAt"], record.admission_id))
    claims: dict[str, list[AdmissionClaim]] = {}
    for record in live:
        for row in record.results:
            claims.setdefault(row["runId"], []).append(AdmissionClaim(record, row["runId"], False, row["outcome"]))
            for attempt in row["supersedes"]:
                claims.setdefault(attempt, []).append(AdmissionClaim(record, row["runId"], True, row["outcome"]))
    return AdmissionStore(tuple(live), {run_id: tuple(found) for run_id, found in claims.items()}, tuple(problems))


_STORES: "weakref.WeakKeyDictionary[ProjectBinding, tuple[frozenset[str], AdmissionStore]]" = (
    weakref.WeakKeyDictionary()
)
_STORES_LOCK = threading.Lock()


def admission_store(binding: ProjectBinding) -> AdmissionStore:
    """Every live admission of this project, read from its one fixed run.

    Retained records are content-addressed and never rewritten, so the record
    names in the run's review area identify the whole store: it is read again
    only when a record was added. It never asks for ``binding.run_ids()``.
    """

    reviews = binding.repository.layout.run(ADMISSIONS_RUN_ID).reviews
    try:
        with os.scandir(reviews) as entries:
            names = frozenset(entry.name for entry in entries if entry.name.startswith(f"{CANDIDATE_ADMISSION}-"))
    except FileNotFoundError:
        names = frozenset()
    except OSError as exc:
        return AdmissionStore(problems=(f"The admission records could not be listed: {error_sentence(exc)}",))
    with _STORES_LOCK:
        cached = _STORES.get(binding)
    if cached is not None and cached[0] == names:
        return cached[1]
    store = _read_store(binding) if names else AdmissionStore()
    if not store.problems:
        with _STORES_LOCK:
            _STORES[binding] = (names, store)
    return store


def rejected_by(binding: ProjectBinding, run_id: str) -> str | None:
    """The live admission that rejected this run, if one did."""

    for claim in admission_store(binding).claims.get(run_id, ()):
        if not claim.superseded and claim.outcome == REJECTED:
            return claim.record.admission_id
    return None


def list_admissions(
    binding: ProjectBinding, *, include_rejected: bool = False,
) -> tuple[tuple[AdmissionRecord, ...], tuple[str, ...]]:
    """Every live admission, oldest first, and what could not be read.

    A rejection stays retained and readable as "already tried"; it is not a
    Candidate, so it is listed only when asked for.
    """

    store = admission_store(binding)
    shown: list[AdmissionRecord] = []
    for record in store.records:
        results = [row for row in record.results if include_rejected or row["outcome"] == ADMITTED]
        if len(results) == len(record.results):
            shown.append(record)
        elif results:
            shown.append(AdmissionRecord(record.ref, {**record.payload, "results": results}))
    return tuple(shown), store.warnings()


def _committed_stages(
    binding: ProjectBinding, *, preferred: str = "main", strict: bool = False,
) -> tuple[tuple[tuple[ProjectRecordRef, DesignStage], ...], tuple[str, ...]]:
    """Every committed Stage once: the preferred line first, each line oldest first."""

    branches = binding.repository.read_design_branches()
    seen: set[ProjectRecordRef] = set()
    stages: list[tuple[ProjectRecordRef, DesignStage]] = []
    warnings: list[str] = []
    for branch_id in sorted(branches, key=lambda name: (name != preferred, name)):
        try:
            history = binding.design_history(branch_id)
        except _UNREADABLE as exc:
            if strict:
                raise
            warnings.append(f"Branch {branch_id} could not be read: {_detail(exc)}")
            continue
        for ref, stage in history:
            if ref not in seen:
                seen.add(ref)
                stages.append((ref, stage))
    return tuple(stages), tuple(warnings)


# ---- writing one admission ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateFailure:
    """One clause one result failed, named the way the refusal named it."""

    run_id: str
    clause: str
    code: str
    detail: str


class AdmissionRefused(StudioError):
    """The gate refused: nothing was retained, and every failing clause is named."""

    def __init__(self, failures: Sequence[GateFailure]) -> None:
        super().__init__(
            409,
            "ADMISSION_GATE_REFUSED",
            "No admission was retained: " + " ".join(
                f"{failure.run_id} fails {failure.clause} ({failure.code}): {failure.detail}"
                for failure in failures
            ),
        )
        self.failures = tuple(failures)

    def body(self) -> dict[str, object]:
        body = super().body()
        body["failures"] = [
            {"runId": failure.run_id, "clause": failure.clause, "code": failure.code, "detail": failure.detail}
            for failure in self.failures
        ]
        return body


def _requested(spec: Mapping[str, Any]) -> dict[str, Any]:
    """One admission request in the order it was given, or a refusal naming what is malformed."""

    results: list[dict[str, Any]] = []
    named: set[str] = set()
    attempts_seen: set[str] = set()
    for row in spec["results"]:
        run_id, attempts = row["runId"], list(row.get("supersedes") or ())
        if run_id in named:
            raise _admission_invalid(f"Run {run_id} is named twice among the results.")
        if len(set(attempts)) != len(attempts) or run_id in attempts:
            raise _admission_invalid(f"Result {run_id} names each superseded attempt once, and never itself.")
        if attempts_seen & set(attempts):
            raise _admission_invalid("An attempt is superseded by one result only.")
        named.add(run_id)
        attempts_seen.update(attempts)
        results.append({"runId": run_id, "outcome": row["outcome"], "supersedes": sorted(attempts),
                        "label": row.get("label"), "summary": row.get("summary"), "reason": row.get("reason")})
    if named & attempts_seen:
        raise _admission_invalid("A run is either a result of this loop or an attempt it superseded, not both.")
    task, study = spec["task"], spec.get("study")
    message, words = spec.get("messageSource"), spec.get("rawLanguage")
    if task["kind"] not in TASK_KINDS:
        raise _admission_invalid(f"A closed loop is one of {', '.join(TASK_KINDS)}.")
    if any(row["outcome"] not in (ADMITTED, REJECTED) for row in results):
        raise _admission_invalid("Each result is admitted or rejected.")
    if study is not None and study["baseRunId"] in named | attempts_seen:
        raise _admission_invalid("A Study's base is where its results start, not one of them.")
    if task["kind"] == TASK_HUB_CHAT:
        if message is None:
            raise _admission_invalid("An Agent admission names the chat message whose task it closes.")
        if words is None and any(row["outcome"] == REJECTED for row in results):
            raise _admission_invalid("The Agent rejects only on the user's own words: bind them as rawLanguage.")
    return {
        "task": {"kind": task["kind"], "ids": list(task.get("ids") or ())},
        "study": None if study is None else {"id": study["id"], "label": study["label"],
                                             "baseRunId": study["baseRunId"]},
        "messageSource": None if message is None else {"sessionId": message["sessionId"],
                                                       "messageId": message["messageId"]},
        "rawLanguage": words,
        "results": results,
    }


def _request_of(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A retained record in the shape its request had."""

    study = payload.get("study")
    return {
        "task": payload.get("task"),
        "study": None if study is None else {"id": study.get("id"), "label": study.get("label"),
                                             "baseRunId": study.get("baseRunId")},
        "messageSource": payload.get("messageSource"),
        "rawLanguage": payload.get("rawLanguage"),
        "results": [{"runId": row["runId"], "outcome": row["outcome"], "supersedes": sorted(row["supersedes"]),
                     "label": row.get("label"), "summary": row.get("summary"), "reason": row.get("reason")}
                    for row in payload["results"]],
    }


def _repeat_key(request: Mapping[str, Any]) -> dict[str, Any]:
    """What an identical retry repeats, in whatever order its results came."""

    return {**request, "results": sorted(request["results"], key=lambda row: row["runId"])}


def _admission_origin(kind: str, attribution: ActorAttribution) -> str:
    """The surface that closed the loop, as far as this boundary can vouch for it."""

    if kind == TASK_HUB_CHAT:
        if attribution.origin != ORIGIN_HUB:
            raise _admission_invalid("An Agent admission comes through the Hub that bound its chat; "
                                     "this Runtime is not managed by a Hub.")
        return ORIGIN_HUB_AGENT
    if kind == TASK_RETROACTIVE:
        return ORIGIN_RETROACTIVE
    return attribution.origin


def _claim_words(claim: AdmissionClaim) -> str:
    return f"superseded by {claim.result_run_id}" if claim.superseded else claim.outcome


def _require_unclaimed(store: AdmissionStore, request: Mapping[str, Any], stage_runs: Mapping[str, str]) -> None:
    """A run is in at most one live record, and a committed Stage's run stays admitted."""

    conflicts: list[str] = []
    for row in request["results"]:
        for run_id in (row["runId"], *row["supersedes"]):
            conflicts.extend(f"{run_id} is already {_claim_words(claim)} in {claim.record.admission_id}"
                             for claim in store.claims.get(run_id, ()))
        if row["outcome"] == REJECTED and row["runId"] in stage_runs:
            conflicts.append(f"{row['runId']} is accepted as Stage {stage_runs[row['runId']]} and is not rejected")
        conflicts.extend(f"{attempt} is accepted as Stage {stage_runs[attempt]} and is not superseded"
                         for attempt in row["supersedes"] if attempt in stage_runs)
    study = request["study"]
    if study is not None:
        for record in store.records:
            declared = record.payload.get("study")
            if (declared is not None and declared.get("id") == study["id"]
                    and (declared.get("label"), declared.get("baseRunId")) != (study["label"], study["baseRunId"])):
                conflicts.append(f"Study {study['id']} is declared by {record.admission_id} with another label or base")
                break
    if conflicts:
        raise StudioError(409, "ADMISSION_CONFLICT", "; ".join(dict.fromkeys(conflicts)) + ". A retained "
                          "disposition changes only through a later revision (#289), never through a second record.")


@dataclass(frozen=True, slots=True)
class _Closed:
    """What the gate read off one result, as the record retains it."""

    receipt_ref: str
    record_digest: str
    base_stage_ref: str | None
    model_source: ModelSource | None
    blocked_by: tuple[str, ...] | None


def _readback_problem(binding: ProjectBinding, candidate: CandidateRun) -> str | None:
    """C4: every seat that exported left a retained object inspection that reads back."""

    exporting = [seat for seat in candidate.seat_results if seat.cad is not None]
    if not exporting:
        return None
    missing = [seat.seat_id for seat in exporting if not seat.cad.get("inspection_ref")]
    if missing:
        return "No retained object inspection for exporting seats: " + ", ".join(missing) + "."
    try:
        shapes_of(binding, candidate.candidate_id)
    except (StudioError, ProjectRepositoryError) as exc:
        return _detail(exc)
    return None


def _delta_stage_ref(binding: ProjectBinding, run_id: str) -> str | None:
    """A run's base Stage as its retained change names it, or None."""

    try:
        delta = binding.candidate_delta(run_id)
        value = None if delta is None else delta.get("source_stage_ref")
        return None if value is None else ProjectRecordRef.from_dict(value).uri
    except _UNREADABLE:
        return None


def _supersession_problem(
    binding: ProjectBinding, result_run: str, attempt: str, base_run: str | None,
    known: dict[str, tuple[str, ...]],
) -> str | None:
    """C7: an attempt is in its result's lineage, or was built from the same base."""

    try:
        if binding.candidate_delta(attempt) is None:
            return f"{attempt} is not a retained candidate result."
    except _UNREADABLE as exc:
        return f"{attempt} could not be read: {_detail(exc)}"
    lineage = lineage_of(binding, result_run, known=known)
    if attempt in lineage[1:]:
        return None
    base = base_run if base_run is not None else (lineage[1] if len(lineage) > 1 else None)
    if base is not None and base in lineage_of(binding, attempt, known=known)[1:]:
        return None
    return f"{attempt} is neither in {result_run}'s lineage nor built from the same base ({base})."


def _close(
    binding: ProjectBinding, request: Mapping[str, Any], *, human: bool, events: StudioEventSink,
) -> tuple[dict[str, _Closed], str | None]:
    """Read the completion contract off every result, or refuse the whole record.

    An admitted result passes C1-C5 and C7 (a person's may fail C3, which then
    travels as its marker); a rejection needs only C2, a completed result. C6
    and C8 are the actor's retained claim, not something the server checks.
    """

    failures: list[GateFailure] = []
    closed: dict[str, _Closed] = {}
    active = binding.repository.read_working_draft()[0]["active"]
    known: dict[str, tuple[str, ...]] = {}
    study = request["study"]
    base_run = None if study is None else study["baseRunId"]
    for row in request["results"]:
        run_id = row["runId"]
        if run_id in active:
            failures.append(GateFailure(run_id, "C2", "CANDIDATE_RUNNING", "The run is still executing or was "
                                        "interrupted; only a completed result is judged."))
            continue
        try:
            with _clause("C2"):
                binding.load_run(run_id)
            if row["outcome"] == ADMITTED:
                with _clause("C1"):
                    value = read_candidate_delta(binding, run_id).get("source_stage_ref")
                stage_ref = None if value is None else ProjectRecordRef.from_dict(value)
                completed = completed_result(binding, run_id, stage_ref=stage_ref, events=events,
                                             require_ready=not human, purpose="an Agent's admission")
                readback = _readback_problem(binding, completed.candidate)
                if readback is not None:
                    failures.append(GateFailure(run_id, "C4", "OBJECT_READBACK_MISSING", readback))
                result = _Closed(completed.candidate.receipt_ref, completed.record_digest,
                                 None if stage_ref is None else stage_ref.uri, completed.model_source,
                                 completed.blocked_by)
            else:
                candidate, _, record = _finished(binding, run_id)
                result = _Closed(candidate.receipt_ref, record.digest, _delta_stage_ref(binding, run_id), None, None)
        except ClauseRefused as exc:
            failures.append(GateFailure(run_id, exc.clause, exc.code, exc.detail))
            continue
        for attempt in row["supersedes"]:
            problem = _supersession_problem(binding, run_id, attempt, base_run, known)
            if problem is not None:
                failures.append(GateFailure(run_id, "C7", "SUPERSESSION_INVALID", problem))
        if base_run is not None and base_run not in lineage_of(binding, run_id, known=known)[1:]:
            failures.append(GateFailure(run_id, "C1", "STUDY_BASE_MISMATCH",
                                        f"{run_id} is not built from the Study base {base_run}."))
        closed[run_id] = result
    stage_refs = {result.base_stage_ref for result in closed.values()}
    if study is not None and len(stage_refs) > 1:
        failures.extend(GateFailure(run_id, "C1", "STUDY_STAGE_MISMATCH", "The results of one Study continue "
                                    "one base Stage; these continue several.") for run_id in closed)
    if failures:
        raise AdmissionRefused(failures)
    return closed, (next(iter(stage_refs)) if study is not None and stage_refs else None)


def _retain_admission(binding: ProjectBinding, payload: Mapping[str, Any]) -> AdmissionRecord:
    run = _admissions_run(binding, create=True)
    try:
        ref = binding.repository.put_json(
            run=run, destination=_admissions_destination(), record_kind=CANDIDATE_ADMISSION, payload=payload,
        )
    except (ProjectRepositoryError, OSError, ValueError) as exc:
        raise StudioError(409, "ADMISSION_WRITE_FAILED", "The admission could not be retained in its project.") from exc
    return AdmissionRecord(ref.uri, payload)


@retained_sources
def admit_results(
    binding: ProjectBinding, spec: Mapping[str, Any], attribution: ActorAttribution, *, events: StudioEventSink,
) -> tuple[AdmissionRecord, bool]:
    """Retain one closed loop's verdict through the completion gate.

    Returns the record and whether it is new: an identical retry returns the
    record it repeats and writes nothing. A run is in at most one live record,
    so any other claim on one is 409 ADMISSION_CONFLICT; changing a retained
    disposition is a later revision (#289). A person (``ui``, ``retroactive``)
    may admit a result that is not review-ready as a comparison option, and
    the failing clauses travel as its marker; an Agent's admission
    (``hub-chat``) is review-ready or refused (owner decision Q2).
    """

    request = _requested(spec)
    origin = _admission_origin(request["task"]["kind"], attribution)
    store = admission_store(binding)
    if store.problems:
        raise StudioError(409, "ADMISSION_RECORD_INVALID", "No claim is added beside admissions that cannot all be "
                          "read: " + " ".join(store.problems))
    key = _repeat_key(request)
    for record in store.records:
        if _repeat_key(_request_of(record.payload)) == key:
            return record, False
    stages, _ = _committed_stages(binding, strict=True)
    _require_unclaimed(store, request, {stage.candidate_id: stage.label for _, stage in stages})
    closed, study_stage = _close(binding, request, human=request["task"]["kind"] != TASK_HUB_CHAT, events=events)
    study = request["study"]
    results = []
    for row in request["results"]:
        read = closed[row["runId"]]
        results.append({
            "runId": row["runId"],
            "outcome": row["outcome"],
            "modelSource": None if read.model_source is None else read.model_source.to_dict(),
            "receiptRef": read.receipt_ref,
            "recordDigest": read.record_digest,
            "baseStageRef": read.base_stage_ref,
            "supersedes": row["supersedes"],
            "label": row["label"],
            "summary": row["summary"],
            "reason": row["reason"],
            "blockedBy": None if read.blocked_by is None else list(read.blocked_by),
        })
    return _retain_admission(binding, {
        "schema": ADMISSION_SCHEMA,
        "admissionId": f"adm-{uuid4().hex[:12]}",
        "projectId": binding.project_id,
        "previousRevisionRef": None,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "actor": {"actorId": attribution.actor_id, "authenticated": attribution.authenticated, "origin": origin},
        "messageSource": request["messageSource"],
        "rawLanguage": request["rawLanguage"],
        "task": request["task"],
        "study": None if study is None else {**study, "baseStageRef": study_stage},
        "results": results,
    }), True


# ---- reading the Candidate Pool -----------------------------------------------


@dataclass(frozen=True, slots=True)
class AdmittedBy:
    """Who admitted a Candidate and through which surface, as far as its fact says."""

    actor_id: str
    authenticated: bool | None
    origin: str | None


@dataclass(frozen=True, slots=True)
class PoolCandidate:
    """One Candidate, with what its retained facts and its lineage say about it."""

    candidate_id: str
    outcome: str
    label: str | None
    summary: str | None
    base_stage_ref: str | None
    study_id: str | None
    model_source: ModelSource | None
    admitted_by: AdmittedBy | None
    admitted_at: str | None
    admission_ref: str | None
    legacy: str | None
    blocked_by: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    accepted_stage_ref: str | None = None
    continued_from: str | None = None
    in_working_head_lineage: bool = False


@dataclass(frozen=True, slots=True)
class PoolStudy:
    """One Study: a declared one, one loop's own group, or a retained Exploration."""

    study_id: str
    label: str | None
    base_run_id: str | None
    base_stage_ref: str | None
    candidate_ids: tuple[str, ...]
    source: str


@dataclass(frozen=True, slots=True)
class CandidatePool:
    """The admitted Candidates and their Studies; rejected results only when asked for."""

    candidates: tuple[PoolCandidate, ...] = ()
    studies: tuple[PoolStudy, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _LegacyStudy:
    """A retained Exploration's non-base options, read as a legacy Study."""

    group_id: str
    label: str
    base_run_id: str
    base_stage_ref: str | None
    record_ref: str
    options: tuple[tuple[str, ModelSource], ...]


@dataclass(frozen=True, slots=True)
class _LegacyEpisode:
    """The run an accepted DeliberationEpisode@1 produced."""

    run_id: str
    created_at: str | None
    record_ref: str


class _LegacyScan:
    """What each run holds of the two legacy kinds, looked at once per bound project."""

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.signature: int | None = None
        self.runs: dict[str, tuple[tuple, tuple, str | None]] = {}


_LEGACY: "weakref.WeakKeyDictionary[ProjectBinding, _LegacyScan]" = weakref.WeakKeyDictionary()
_LEGACY_LOCK = threading.Lock()


def _accepted_episode(binding: ProjectBinding, payload: Mapping[str, Any], run_id: str) -> bool:
    return (payload.get("schema") == EPISODE_SCHEMA and payload.get("projectId") == binding.project_id
            and payload.get("producedRun") == run_id
            and any(isinstance(row, Mapping) and row.get("decision") == EPISODE_ACCEPTED
                    for row in payload.get("proposals") or ()))


def _legacy_run(binding: ProjectBinding, run_id: str) -> tuple[tuple, tuple, str | None]:
    """One run's retained Exploration records and the accepted episodes it produced."""

    try:
        run = binding.load_run(run_id)
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)
        copies = tuple((ref, binding.repository.load_json(ref)) for ref in binding.repository.list_json(
            run=run, destination=destination, record_kind=STUDIO_WORKING_COPY))
        episodes = tuple((ref, payload) for ref in binding.repository.list_json(
            run=run, destination=destination, record_kind=DELIBERATION_EPISODE)
            for payload in (binding.repository.load_json(ref),) if _accepted_episode(binding, payload, run_id))
    except _UNREADABLE as exc:
        return (), (), f"Run {run_id} could not be read for earlier admissions: {_detail(exc)}"
    return copies, episodes, None


def _legacy_from(
    binding: ProjectBinding, found: Mapping[str, tuple[tuple, tuple, str | None]],
) -> tuple[tuple[_LegacyStudy, ...], tuple[_LegacyEpisode, ...], tuple[str, ...]]:
    warnings = [problem for _copies, _episodes, problem in found.values() if problem]
    groups: dict[str, dict[str, Mapping[str, Any]]] = {}
    uris: dict[str, str] = {}
    for run_id, (copies, _episodes, _problem) in sorted(found.items()):
        for ref, payload in copies:
            base = payload.get("commonBase")
            group_id = payload.get("groupId")
            if (payload.get("schema") != _WORKING_COPY_SCHEMA or payload.get("projectId") != binding.project_id
                    or not isinstance(group_id, str) or not isinstance(base, Mapping) or base.get("runId") != run_id):
                warnings.append(f"Exploration record {ref.uri} has an inconsistent binding; it is left out.")
                continue
            groups.setdefault(group_id, {})[ref.sha256] = payload
            uris[ref.sha256] = ref.uri
    studies: list[_LegacyStudy] = []
    for group_id, revisions in sorted(groups.items()):
        try:
            item = _working_copy_from(revisions)
        except _UNREADABLE as exc:
            warnings.append(f"Exploration {group_id} could not be read: {_detail(exc)}")
            continue
        studies.append(_LegacyStudy(
            group_id, item.label, item.common_base.run_id, item.base_stage_ref, uris[item.revision_sha256],
            tuple((option.label, option.model_source) for option in item.options
                  if option.model_source.run_id != item.common_base.run_id),
        ))
    episodes = tuple(_LegacyEpisode(run_id, payload.get("createdAt"), ref.uri)
                     for run_id, (_copies, accepted, _problem) in sorted(found.items())
                     for ref, payload in accepted)
    return tuple(studies), episodes, tuple(warnings)


def _legacy_evidence(
    binding: ProjectBinding,
) -> tuple[tuple[_LegacyStudy, ...], tuple[_LegacyEpisode, ...], tuple[str, ...]]:
    """Retained Explorations and accepted episodes, the legacy admission evidence.

    Both live in ordinary run records, with no index. Each run is looked at
    once per bound project, and a run that appears later when it appears; this
    process's own writers of either kind make every run be looked at again. A
    record another process adds to an older run is read after a restart.
    """

    generation = evidence_generation()
    try:
        signature: int | None = binding.repository.layout.runs.stat().st_mtime_ns
    except OSError:
        signature = None
    with _LEGACY_LOCK:
        scan = _LEGACY.get(binding)
        if scan is None or scan.generation != generation:
            scan = _LegacyScan(generation)
            _LEGACY[binding] = scan
        if signature is None or signature != scan.signature:
            for run_id in binding.run_ids():
                if run_id not in scan.runs:
                    scan.runs[run_id] = _legacy_run(binding, run_id)
            scan.signature = signature
        found = dict(scan.runs)
    return _legacy_from(binding, found)


def _stage_candidate(
    binding: ProjectBinding, ref: ProjectRecordRef, stage: DesignStage, warnings: list[str], *, detailed: bool,
) -> PoolCandidate:
    """A committed Stage's run, admitted by its acceptance (legacy rule 1)."""

    source = admitted_by = admitted_at = None
    if detailed:
        try:
            digest = binding.repository.load_json(stage.runner_ref).get("design_state_digest")
            source = ModelSource(stage.candidate_id, digest, stage.model_sha256) if isinstance(digest, str) else None
        except _UNREADABLE as exc:
            warnings.append(f"Stage {stage.label} model could not be read: {_detail(exc)}")
        try:
            attribution = _retained_acceptance_attribution(binding, ref, stage)
        except _UNREADABLE as exc:
            attribution = None
            warnings.append(f"Stage {stage.label} acceptance could not be read: {_detail(exc)}")
        admitted_by = (AdmittedBy(stage.accepted_by, None, None) if attribution is None
                       else AdmittedBy(attribution.actor_id, attribution.authenticated, attribution.origin))
        admitted_at = None if attribution is None else attribution.occurred_at
    return PoolCandidate(
        candidate_id=stage.candidate_id, outcome=ADMITTED, label=stage.label, summary=None,
        base_stage_ref=None if stage.parent_stage is None else stage.parent_stage.uri, study_id=None,
        model_source=source, admitted_by=admitted_by, admitted_at=admitted_at, admission_ref=ref.uri,
        legacy=LEGACY_STAGE,
    )


def _pool_entries(
    binding: ProjectBinding, store: AdmissionStore, stages: Sequence[tuple[ProjectRecordRef, DesignStage]],
    *, include_rejected: bool, detailed: bool,
) -> tuple[dict[str, PoolCandidate], dict[str, dict[str, Any]], list[str]]:
    """Each Candidate once, from its strongest retained fact, before lineage.

    Evidence: an admission record; else a committed Stage, a retained
    Exploration's non-base option, or an accepted episode. Saved and recovery
    rows, chat ids, pins and newest files prove nothing. Grouping: the record's
    declared Study, else the record itself, else a retained Exploration; a
    Stage or an episode alone is ungrouped. A run that live records compete
    for is left out, and the warnings say so.
    """

    warnings = list(store.warnings())
    competing = store.competing
    entries: dict[str, PoolCandidate] = {}
    studies: dict[str, dict[str, Any]] = {}
    for record in store.records:
        declared = record.payload.get("study")
        loop = frozenset(run_id for row in record.results for run_id in (row["runId"], *row["supersedes"]))
        meta = ({"label": None, "baseRunId": None, "baseStageRef": None, "source": STUDY_ADMISSION, "loop": loop}
                if declared is None else
                {"label": declared.get("label"), "baseRunId": declared.get("baseRunId"),
                 "baseStageRef": declared.get("baseStageRef"), "source": STUDY_DECLARED})
        if studies.setdefault(record.study_id, meta) != meta:
            warnings.append(f"Study {record.study_id} is declared differently by {record.admission_id}; "
                            "its first declaration is shown.")
        actor = record.payload.get("actor")
        admitted_by = (AdmittedBy(actor["actorId"], actor.get("authenticated"), actor.get("origin"))
                       if isinstance(actor, Mapping) and isinstance(actor.get("actorId"), str) else None)
        for row in record.results:
            run_id = row["runId"]
            if run_id in competing or run_id in entries or (row["outcome"] != ADMITTED and not include_rejected):
                continue
            source = row.get("modelSource")
            entries[run_id] = PoolCandidate(
                candidate_id=run_id, outcome=row["outcome"], label=row.get("label"), summary=row.get("summary"),
                base_stage_ref=row.get("baseStageRef"), study_id=record.study_id,
                model_source=ModelSource.from_dict(source) if isinstance(source, Mapping) else None,
                admitted_by=admitted_by, admitted_at=record.payload["occurredAt"], admission_ref=record.ref,
                legacy=None, blocked_by=tuple(row.get("blockedBy") or ()), supersedes=tuple(row["supersedes"]),
            )
    claimed = set(store.claims)
    for ref, stage in stages:
        if stage.candidate_id not in claimed and stage.candidate_id not in entries:
            entries[stage.candidate_id] = _stage_candidate(binding, ref, stage, warnings, detailed=detailed)
    legacy_studies, episodes, legacy_warnings = _legacy_evidence(binding)
    warnings.extend(legacy_warnings)
    for group in legacy_studies:
        study_id = f"{STUDY_WORKING_COPY}:{group.group_id}"
        studies.setdefault(study_id, {"label": group.label, "baseRunId": group.base_run_id,
                                      "baseStageRef": group.base_stage_ref, "source": STUDY_WORKING_COPY})
        for label, source in group.options:
            run_id = source.run_id
            existing = entries.get(run_id)
            if run_id in claimed or (existing is not None and (existing.legacy is None or existing.study_id)):
                continue
            if existing is not None:
                entries[run_id] = replace(existing, study_id=study_id)
                continue
            entries[run_id] = PoolCandidate(
                candidate_id=run_id, outcome=ADMITTED, label=label, summary=None,
                base_stage_ref=(_delta_stage_ref(binding, run_id) if detailed else None) or group.base_stage_ref,
                study_id=study_id, model_source=source, admitted_by=None, admitted_at=None,
                admission_ref=group.record_ref, legacy=LEGACY_WORKING_COPY,
            )
    for episode in episodes:
        if episode.run_id in claimed or episode.run_id in entries:
            continue
        entries[episode.run_id] = PoolCandidate(
            candidate_id=episode.run_id, outcome=ADMITTED, label=None, summary=None,
            base_stage_ref=_delta_stage_ref(binding, episode.run_id) if detailed else None, study_id=None,
            model_source=None, admitted_by=None, admitted_at=episode.created_at, admission_ref=episode.record_ref,
            legacy=LEGACY_EPISODE,
        )
    return entries, studies, warnings


def candidate_pool(
    binding: ProjectBinding, *, branch_id: str = "main", include_rejected: bool = False,
) -> CandidatePool:
    """The admitted Candidates and their Studies, from retained facts only.

    ``continuedFrom`` is a Candidate's nearest admitted ancestor. A Stage's own
    run carries that Stage as ``acceptedStageRef``, and so does the nearest
    Candidate the Stage's accepted run was developed from ("S3 from B"), found
    between that run and its parent Stage. A Candidate without a Stage has no
    ``baseStageRef``: it sits under the Unstaged root.
    """

    store = admission_store(binding)
    stages, stage_warnings = _committed_stages(binding, preferred=branch_id)
    entries, studies, found = _pool_entries(binding, store, stages, include_rejected=include_rejected, detailed=True)
    warnings = [*stage_warnings, *found]
    known: dict[str, tuple[str, ...]] = {}
    admitted = {run_id for run_id, entry in entries.items() if entry.outcome == ADMITTED}
    stage_runs = {ref: stage.candidate_id for ref, stage in stages}
    accepted: dict[str, str] = {}
    for ref, stage in stages:
        if stage.candidate_id in admitted:
            accepted.setdefault(stage.candidate_id, ref.uri)
        parent_run = None if stage.parent_stage is None else stage_runs.get(stage.parent_stage)
        for ancestor in lineage_of(binding, stage.candidate_id, known=known)[1:]:
            if ancestor == parent_run:
                break
            if ancestor in admitted and entries[ancestor].legacy != LEGACY_STAGE:
                accepted.setdefault(ancestor, ref.uri)
                break
    try:
        head = resolve_working_source(binding).head
    except _UNREADABLE as exc:
        head = None
        warnings.append(f"The Working Head could not be read: {_detail(exc)}")
    head_lineage = frozenset(() if head is None else head.lineage)
    candidates = tuple(
        replace(
            entry,
            accepted_stage_ref=accepted.get(run_id),
            continued_from=next((ancestor for ancestor in lineage_of(binding, run_id, known=known)[1:]
                                 if ancestor in admitted), None),
            in_working_head_lineage=run_id in head_lineage,
        )
        for run_id, entry in entries.items()
    )
    members: dict[str, list[str]] = {}
    for candidate in candidates:
        if candidate.study_id is not None:
            members.setdefault(candidate.study_id, []).append(candidate.candidate_id)
    listed: list[PoolStudy] = []
    for study_id, meta in studies.items():
        ids = members.get(study_id)
        if not ids:
            continue
        base_run, base_stage = meta["baseRunId"], meta["baseStageRef"]
        if meta["source"] == STUDY_ADMISSION:
            # A loop declared no base: it starts at the nearest run outside the loop.
            starts = {next((run for run in lineage_of(binding, run_id, known=known)[1:] if run not in meta["loop"]),
                           None) for run_id in ids}
            stage_refs = {entries[run_id].base_stage_ref for run_id in ids}
            base_run = next(iter(starts)) if len(starts) == 1 else None
            base_stage = next(iter(stage_refs)) if len(stage_refs) == 1 else None
        listed.append(PoolStudy(study_id, meta["label"], base_run, base_stage, tuple(ids), meta["source"]))
    return CandidatePool(candidates, tuple(listed), tuple(dict.fromkeys(warnings)))


def admission_index(binding: ProjectBinding) -> tuple[dict[str, tuple[str, str | None]], tuple[str, ...]]:
    """Each run's admission and Study, for views that list runs; nothing is written."""

    store = admission_store(binding)
    stages, warnings = _committed_stages(binding)
    entries, _studies, found = _pool_entries(binding, store, stages, include_rejected=True, detailed=False)
    index = {run_id: (entry.outcome, entry.study_id) for run_id, entry in entries.items()}
    for run_id, claims in store.claims.items():
        if len(claims) == 1 and claims[0].superseded:
            index[run_id] = (SUPERSEDED, claims[0].record.study_id)
    return index, (*warnings, *found)
