"""Pause and exact-base resume around a named human clarification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from archflow.interaction.clarification import (
    AuthorityDecisionReceipt,
    ClarificationAlternative,
    ClarificationDisposition,
    ClarificationEffect,
    ClarificationRequest,
    CommitmentClarificationAction,
    parse_utc,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
    RunRef,
)
from archflow.state import (
    Commitment,
    CommitmentStatus,
    ConditionComparator,
    DecisionCompilationError,
    DecisionOperator,
    DesignObligation,
    FactEpistemicStatus,
    ObligationStatus,
    OperationalMarkovState,
    StateCondition,
    StateFact,
    compile_decision_operator,
    transition_commitment,
)
from archflow.state.commitments import (
    CommitmentAuthorityError,
    CommitmentTransitionError,
)
from archflow.state.decision_operator import CompiledDecisionTransition


class ClarificationError(ValueError):
    """A clarification cannot become an authority-bound state proposal."""


class ClarificationStaleError(ClarificationError):
    pass


class ClarificationDuplicateError(ClarificationError):
    pass


class ClarificationExpiredError(ClarificationError):
    pass


class ClarificationUnauthorizedError(ClarificationError):
    pass


class ClarificationResumeStatus(StrEnum):
    BLOCKED = "blocked"
    RESUMED = "resumed"


@dataclass(frozen=True, slots=True)
class ClarificationResumeResult:
    status: ClarificationResumeStatus
    state: OperationalMarkovState
    reason: str
    operator: DecisionOperator | None = None
    transition: CompiledDecisionTransition | None = None
    receipt_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ClarificationResumeStatus):
            raise TypeError("status must be ClarificationResumeStatus")
        if not isinstance(self.state, OperationalMarkovState):
            raise TypeError("state must be OperationalMarkovState")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be non-empty text")
        if self.status is ClarificationResumeStatus.RESUMED:
            if (
                not isinstance(self.operator, DecisionOperator)
                or not isinstance(
                    self.transition,
                    CompiledDecisionTransition,
                )
                or self.receipt_id is None
            ):
                raise ValueError(
                    "resumed result requires operator transition and receipt"
                )
        elif self.operator is not None or self.transition is not None:
            raise ValueError("blocked result cannot contain a transition")


@dataclass(frozen=True, slots=True)
class PersistedClarification:
    request: ProjectRecordRef
    receipt: ProjectRecordRef | None = None


def create_clarification_request(
    state: OperationalMarkovState,
    *,
    obligation_id: str,
    requesting_agent_id: str,
    authority_ids: tuple[str, ...],
    question: str,
    blocked_reason: str,
    alternatives: tuple[ClarificationAlternative, ...] = (),
    target_refs: tuple[str, ...] = (),
    allow_open_response: bool = True,
    created_at_utc: str,
    expires_at_utc: str,
) -> ClarificationRequest:
    """Create a pause record only for an actual open state obligation."""

    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be OperationalMarkovState")
    obligation = _obligation(state, obligation_id)
    if obligation.status is not ObligationStatus.OPEN:
        raise ClarificationError(
            "clarification requires an open authority obligation"
        )
    effect_refs = {
        ref
        for alternative in alternatives
        for ref in alternative.effect.target_refs
    }
    targets = tuple(
        sorted(
            {
                f"obligation:{obligation_id}",
                *obligation.subject_refs,
                *target_refs,
                *effect_refs,
            }
        )
    )
    return ClarificationRequest(
        branch=state.branch,
        operational_state_digest=state.state_digest,
        requesting_agent_id=requesting_agent_id,
        authority_ids=authority_ids,
        obligation_id=obligation_id,
        target_refs=targets,
        question=question,
        blocked_reason=blocked_reason,
        alternatives=alternatives,
        allow_open_response=allow_open_response,
        created_at_utc=created_at_utc,
        expires_at_utc=expires_at_utc,
    )


def issue_authority_decision(
    request: ClarificationRequest,
    *,
    authority_id: str,
    disposition: ClarificationDisposition,
    authority_event_ref: str,
    issued_at_utc: str,
    valid_until_utc: str,
    selected_alternative_id: str | None = None,
    revised_effect: ClarificationEffect | None = None,
) -> AuthorityDecisionReceipt:
    """Bind one typed response; chat text alone never enters this boundary."""

    if not isinstance(request, ClarificationRequest):
        raise TypeError("request must be ClarificationRequest")
    if authority_id == request.requesting_agent_id:
        raise ClarificationUnauthorizedError(
            "requesting Agent cannot answer its own request"
        )
    if authority_id not in request.authority_ids:
        raise ClarificationUnauthorizedError(
            "authority is not permitted by this request"
        )
    issued = parse_utc(issued_at_utc)
    if issued < parse_utc(request.created_at_utc):
        raise ClarificationError(
            "authority decision predates its clarification request"
        )
    if parse_utc(valid_until_utc) > parse_utc(request.expires_at_utc):
        raise ClarificationError(
            "authority decision outlives its clarification request"
        )
    if disposition is ClarificationDisposition.SELECTED:
        alternatives = {
            item.alternative_id for item in request.alternatives
        }
        if selected_alternative_id not in alternatives:
            raise ClarificationError(
                "selected alternative is not in the exact request"
            )
    if (
        disposition is ClarificationDisposition.REVISED
        and not request.allow_open_response
    ):
        raise ClarificationError(
            "this clarification does not allow an open revision"
        )
    receipt = AuthorityDecisionReceipt(
        request_id=request.request_id,
        request_digest=request.request_digest,
        branch=request.branch,
        operational_state_digest=request.operational_state_digest,
        authority_id=authority_id,
        disposition=disposition,
        selected_alternative_id=selected_alternative_id,
        revised_effect=revised_effect,
        authority_event_ref=authority_event_ref,
        issued_at_utc=issued_at_utc,
        valid_until_utc=valid_until_utc,
    )
    _effect_within_targets(request, _receipt_effect(request, receipt))
    return receipt


def validate_authority_decision(
    request: ClarificationRequest,
    receipt: AuthorityDecisionReceipt,
    state: OperationalMarkovState,
    *,
    now_utc: str,
    consumed_request_ids: tuple[str, ...] = (),
) -> None:
    """Fail closed on identity, time, authority, exact-base, and replay."""

    if not isinstance(request, ClarificationRequest):
        raise TypeError("request must be ClarificationRequest")
    if not isinstance(receipt, AuthorityDecisionReceipt):
        raise TypeError("receipt must be AuthorityDecisionReceipt")
    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be OperationalMarkovState")
    if request.request_id in consumed_request_ids:
        raise ClarificationDuplicateError(
            "clarification request was already consumed"
        )
    if (
        receipt.request_id != request.request_id
        or receipt.request_digest != request.request_digest
    ):
        raise ClarificationStaleError(
            "authority receipt targets another request"
        )
    if receipt.authority_id == request.requesting_agent_id:
        raise ClarificationUnauthorizedError(
            "requesting Agent cannot answer its own request"
        )
    if receipt.authority_id not in request.authority_ids:
        raise ClarificationUnauthorizedError(
            "authority is not permitted by this request"
        )
    if receipt.branch != request.branch:
        raise ClarificationStaleError(
            "authority receipt project run branch or base mismatched"
        )
    if (
        state.branch != request.branch
        or state.state_digest != request.operational_state_digest
        or receipt.operational_state_digest
        != request.operational_state_digest
    ):
        raise ClarificationStaleError(
            "clarification operational state is stale"
        )
    now = parse_utc(now_utc)
    if (
        now > parse_utc(request.expires_at_utc)
        or now > parse_utc(receipt.valid_until_utc)
        or now < parse_utc(receipt.issued_at_utc)
    ):
        raise ClarificationExpiredError(
            "clarification request or receipt is outside validity"
        )
    obligation = _obligation(state, request.obligation_id)
    if obligation.status is not ObligationStatus.OPEN:
        raise ClarificationStaleError(
            "clarification obligation is no longer open"
        )
    _effect_within_targets(request, _receipt_effect(request, receipt))


def compile_clarification_operator(
    request: ClarificationRequest,
    receipt: AuthorityDecisionReceipt,
    state: OperationalMarkovState,
    *,
    now_utc: str,
    commitment_catalog: tuple[Commitment, ...] = (),
    consumed_request_ids: tuple[str, ...] = (),
) -> DecisionOperator | None:
    """Compile authority evidence into a proposal, never a direct state write."""

    validate_authority_decision(
        request,
        receipt,
        state,
        now_utc=now_utc,
        consumed_request_ids=consumed_request_ids,
    )
    effect = _receipt_effect(request, receipt)
    if effect is None:
        return None
    source_ref = receipt.ref
    facts = tuple(
        StateFact(
            domain=item.domain,
            key=item.key,
            value=item.value,
            source_ref=source_ref,
            epistemic_status=FactEpistemicStatus.DECLARED,
            qualification=item.qualification,
        )
        for item in effect.fact_updates
    )
    commitments = _compile_commitment_effect(
        effect,
        receipt=receipt,
        request=request,
        state=state,
        commitment_catalog=commitment_catalog,
    )
    return DecisionOperator(
        decision_id=f"decision.{receipt.receipt_id}",
        decision_type="authority-clarification",
        base_state_digest=state.state_digest,
        authority_id=receipt.authority_id,
        intent="Apply one exact-base typed authority clarification.",
        preconditions=(
            StateCondition(
                ref=f"obligation:{request.obligation_id}",
                comparator=ConditionComparator.EQUALS,
                expected_value=ObligationStatus.OPEN.value,
            ),
        ),
        add_facts=facts,
        spawn_commitments=commitments,
        discharge_obligation_ids=(request.obligation_id,),
        evidence_refs=(
            request.ref,
            receipt.ref,
            receipt.authority_event_ref,
        ),
    )


def resume_from_clarification(
    request: ClarificationRequest,
    state: OperationalMarkovState,
    *,
    now_utc: str,
    receipt: AuthorityDecisionReceipt | None = None,
    commitment_catalog: tuple[Commitment, ...] = (),
    consumed_request_ids: tuple[str, ...] = (),
) -> ClarificationResumeResult:
    """Return the unchanged blocked state or a normally compiled transition."""

    if receipt is None:
        reason = (
            "clarification request timed out"
            if parse_utc(now_utc) > parse_utc(request.expires_at_utc)
            else "clarification request is unanswered"
        )
        return ClarificationResumeResult(
            status=ClarificationResumeStatus.BLOCKED,
            state=state,
            reason=reason,
        )
    try:
        operator = compile_clarification_operator(
            request,
            receipt,
            state,
            now_utc=now_utc,
            commitment_catalog=commitment_catalog,
            consumed_request_ids=consumed_request_ids,
        )
    except ClarificationExpiredError:
        return ClarificationResumeResult(
            status=ClarificationResumeStatus.BLOCKED,
            state=state,
            reason="clarification request or receipt expired",
            receipt_id=receipt.receipt_id,
        )
    if operator is None:
        return ClarificationResumeResult(
            status=ClarificationResumeStatus.BLOCKED,
            state=state,
            reason=f"authority response is {receipt.disposition.value}",
            receipt_id=receipt.receipt_id,
        )
    try:
        transition = compile_decision_operator(state, operator)
    except DecisionCompilationError as exc:
        raise ClarificationStaleError(
            "clarification proposal failed normal decision compilation"
        ) from exc
    return ClarificationResumeResult(
        status=ClarificationResumeStatus.RESUMED,
        state=transition.state,
        reason="authority clarification compiled and resumed",
        operator=operator,
        transition=transition,
        receipt_id=receipt.receipt_id,
    )


def persist_clarification_request(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    request: ClarificationRequest,
) -> PersistedClarification:
    _validate_repository_identity(repository, run, request.branch.run)
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind="clarification-request",
        payload=request.to_dict(),
    )
    return PersistedClarification(request=ref)


def persist_authority_decision(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    request_ref: ProjectRecordRef,
    request: ClarificationRequest,
    receipt: AuthorityDecisionReceipt,
) -> PersistedClarification:
    _validate_repository_identity(repository, run, request.branch.run)
    if (
        request_ref.project_id != run.project_id
        or repository.load_json(request_ref) != request.to_dict()
    ):
        raise ClarificationError(
            "persisted clarification request does not match"
        )
    if (
        receipt.request_id != request.request_id
        or receipt.request_digest != request.request_digest
    ):
        raise ClarificationError("receipt does not bind the exact request")
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    for existing_ref in repository.list_json(
        run=run,
        destination=destination,
    ):
        payload = repository.load_json(existing_ref)
        if (
            payload.get("schema")
            == AuthorityDecisionReceipt.SCHEMA
            and payload.get("request_id") == request.request_id
        ):
            raise ClarificationDuplicateError(
                "clarification request already has a persisted reply"
            )
    receipt_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="authority-decision-receipt",
        payload=receipt.to_dict(),
    )
    return PersistedClarification(
        request=request_ref,
        receipt=receipt_ref,
    )


def _compile_commitment_effect(
    effect: ClarificationEffect,
    *,
    receipt: AuthorityDecisionReceipt,
    request: ClarificationRequest,
    state: OperationalMarkovState,
    commitment_catalog: tuple[Commitment, ...],
) -> tuple[Commitment, ...]:
    action = effect.commitment_action
    if action is CommitmentClarificationAction.NONE:
        return ()
    catalog: dict[str, Commitment] = {
        item.commitment_id: item for item in state.commitments
    }
    for item in commitment_catalog:
        existing = catalog.get(item.commitment_id)
        if existing is not None and existing != item:
            raise ClarificationError(
                "commitment catalog conflicts with operational state"
            )
        catalog[item.commitment_id] = item
    target = catalog.get(effect.commitment_id)
    if target is None:
        raise ClarificationError("clarification target commitment is missing")
    try:
        if action is CommitmentClarificationAction.AUTHORIZE:
            if target.status is not CommitmentStatus.PROPOSED:
                raise CommitmentTransitionError(
                    "only a proposed commitment can be authorized"
                )
            accepted = transition_commitment(
                target,
                CommitmentStatus.ACCEPTED,
                actor_authority_id=receipt.authority_id,
            )
            active = transition_commitment(
                accepted,
                CommitmentStatus.ACTIVE,
                actor_authority_id=receipt.authority_id,
                monitor_state_ref=(
                    f"monitor:clarification/{request.request_id}"
                ),
            )
            return (active,)
        if action is CommitmentClarificationAction.RELEASE:
            released = transition_commitment(
                target,
                CommitmentStatus.RELEASED,
                actor_authority_id=receipt.authority_id,
            )
            return (released,)
        replacement = catalog.get(effect.replacement_commitment_id)
        if replacement is None:
            raise ClarificationError(
                "replacement commitment is missing"
            )
        if (
            replacement.status is not CommitmentStatus.PROPOSED
            or replacement.predecessor_id != target.commitment_id
        ):
            raise ClarificationError(
                "replacement commitment lacks proposed predecessor lineage"
            )
        terminal = (
            CommitmentStatus.SUPERSEDED
            if target.status is CommitmentStatus.PROPOSED
            else CommitmentStatus.REVISED
        )
        previous = transition_commitment(
            target,
            terminal,
            actor_authority_id=receipt.authority_id,
            successor_id=replacement.commitment_id,
        )
        active_replacement = transition_commitment(
            transition_commitment(
                replacement,
                CommitmentStatus.ACCEPTED,
                actor_authority_id=receipt.authority_id,
            ),
            CommitmentStatus.ACTIVE,
            actor_authority_id=receipt.authority_id,
            monitor_state_ref=(
                f"monitor:clarification/{request.request_id}"
            ),
        )
        return (previous, active_replacement)
    except (
        CommitmentAuthorityError,
        CommitmentTransitionError,
    ) as exc:
        raise ClarificationUnauthorizedError(
            "commitment clarification violates lifecycle authority"
        ) from exc


def _receipt_effect(
    request: ClarificationRequest,
    receipt: AuthorityDecisionReceipt,
) -> ClarificationEffect | None:
    if receipt.disposition is ClarificationDisposition.SELECTED:
        alternatives = {
            item.alternative_id: item for item in request.alternatives
        }
        selected = alternatives.get(receipt.selected_alternative_id)
        if selected is None:
            raise ClarificationError(
                "selected alternative is absent from request"
            )
        return selected.effect
    if receipt.disposition is ClarificationDisposition.REVISED:
        if not request.allow_open_response:
            raise ClarificationError(
                "open revision is not allowed by request"
            )
        return receipt.revised_effect
    return None


def _effect_within_targets(
    request: ClarificationRequest,
    effect: ClarificationEffect | None,
) -> None:
    if effect is None:
        return
    if not set(effect.target_refs) <= set(request.target_refs):
        raise ClarificationError(
            "authority response changes an untargeted state reference"
        )


def _obligation(
    state: OperationalMarkovState,
    obligation_id: str,
) -> DesignObligation:
    matches = tuple(
        item
        for item in state.obligations
        if item.obligation_id == obligation_id
    )
    if len(matches) != 1:
        raise ClarificationError(
            "clarification obligation is missing from operational state"
        )
    return matches[0]


def _validate_repository_identity(
    repository: FilesystemProjectRepository,
    run: RunRef,
    request_run: RunRef,
) -> None:
    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    if run != request_run:
        raise ClarificationError(
            "clarification project run or canonical base mismatched"
        )
    if repository.load_run(run.run_id) != run:
        raise ClarificationError(
            "clarification run exact base mismatches repository"
        )
