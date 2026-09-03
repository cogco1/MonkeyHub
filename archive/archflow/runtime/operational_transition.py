"""Compatibility-only P008/P009 operational transition trace.

This module owns neither an MCP client nor a canonical-state writer. It turns
an exact-bound proposal plus a verified observation into the next detached
working trace state. Pending side effects are never treated as facts.

``OperationalMarkovState@1`` is intentionally retained for archived traces and
the early repair-loop fixtures. It is not a complete design-state schema and
must not be extended by new controllers. New design moves use
``archflow.state.OperationalMarkovState`` (V2), ``DecisionOperator@1``, and
deterministic closure.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from archive.archflow.capabilities.experts import (
    ExpertEvidence,
    ExpertObligation,
    ExpertRegistry,
    ExpertSnapshot,
)
from archflow.state.model import StateRef
from archflow.state.operational_state import (
    OperationalMarkovState as CompiledOperationalMarkovState,
)
from archflow.contracts.canonical import canonical_json_bytes, require_sha256

_MAX_ITEMS = 128
_MAX_TEXT = 2_000
_MAX_JSON_BYTES = 2_000_000
LEGACY_OPERATIONAL_STATE_SCHEMA = "OperationalMarkovState@1"
LEGACY_TRANSITION_PROPOSAL_SCHEMA = "TransitionProposal@1"


class OperationalTransitionError(ValueError):
    """A transition cannot safely become decision state."""


class OperationalStateMigrationRequired(OperationalTransitionError):
    """A V1 trace state cannot be promoted by inventing missing V2 factors."""


class FactBasis(StrEnum):
    """Evidence quality allowed inside an actionable operational state."""

    DETERMINISTIC_COMPILE = "deterministic_compile"
    OBSERVED = "observed"


class TransitionObservationStatus(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED_PREWRITE = "rejected_prewrite"


class TransitionReceiptStatus(StrEnum):
    ADVANCED = "advanced"
    ENVIRONMENT_OBSERVED = "environment_observed"
    NO_PROGRESS = "no_progress"


@dataclass(frozen=True, slots=True)
class OperationalFact:
    key: str
    value: str
    basis: FactBasis
    source_ref: str

    def __post_init__(self) -> None:
        _text(self.key, "fact.key")
        _text(self.value, "fact.value")
        _text(self.source_ref, "fact.source_ref")
        if not isinstance(self.basis, FactBasis):
            raise TypeError("fact.basis must be a FactBasis")


@dataclass(frozen=True, slots=True)
class OperationalObligation:
    obligation_id: str
    topic: str
    statement: str
    source_ref: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.obligation_id, "obligation.obligation_id"),
            (self.topic, "obligation.topic"),
            (self.statement, "obligation.statement"),
            (self.source_ref, "obligation.source_ref"),
        ):
            _text(value, field)
        _bounded_text_tuple(self.evidence_refs, "obligation.evidence_refs")


@dataclass(frozen=True, slots=True)
class LegacyOperationalMarkovState:
    """Minimal V1 trace state; not a sufficient architectural design state."""

    canonical_base: StateRef
    epoch: int
    material_revision: int
    facts: tuple[OperationalFact, ...] = ()
    obligations: tuple[OperationalObligation, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    parent_state_digest: str | None = None
    last_transition_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_base, StateRef):
            raise TypeError("canonical_base must be a StateRef")
        for value, field in (
            (self.epoch, "epoch"),
            (self.material_revision, "material_revision"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.material_revision > self.epoch:
            raise ValueError("material_revision cannot exceed epoch")
        if not isinstance(self.facts, tuple):
            raise TypeError("facts must be a tuple")
        if not isinstance(self.obligations, tuple):
            raise TypeError("obligations must be a tuple")
        if len(self.facts) > _MAX_ITEMS or len(self.obligations) > _MAX_ITEMS:
            raise ValueError("operational state exceeds bounded item count")
        if len({item.key for item in self.facts}) != len(self.facts):
            raise ValueError("operational fact keys must be unique")
        if len({item.obligation_id for item in self.obligations}) != len(
            self.obligations
        ):
            raise ValueError("operational obligation ids must be unique")
        _bounded_text_tuple(self.evidence_refs, "evidence_refs")
        if self.epoch == 0:
            if self.parent_state_digest is not None:
                raise ValueError("initial state cannot have a parent digest")
            if self.last_transition_id is not None:
                raise ValueError("initial state cannot have a transition id")
        else:
            require_sha256(self.parent_state_digest, "parent_state_digest")
            _text(self.last_transition_id, "last_transition_id")

    @property
    def state_digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(_state_identity(self), ascii=False)).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = _state_identity(self)
        payload["schema"] = LEGACY_OPERATIONAL_STATE_SCHEMA
        payload["state_digest"] = self.state_digest
        return payload


OperationalMarkovState = LegacyOperationalMarkovState


@dataclass(frozen=True, slots=True)
class LegacyTransitionProposal:
    """Minimal exact-plan proposal retained only for V1 trace compatibility."""

    proposal_id: str
    base_state_digest: str
    capability_id: str
    intent: str
    plan_sha256: str
    resolves_obligation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.proposal_id, "proposal_id"),
            (self.capability_id, "capability_id"),
            (self.intent, "intent"),
        ):
            _text(value, field)
        require_sha256(self.base_state_digest, "base_state_digest")
        require_sha256(self.plan_sha256, "plan_sha256")
        _bounded_text_tuple(
            self.resolves_obligation_ids,
            "resolves_obligation_ids",
        )
        if len(set(self.resolves_obligation_ids)) != len(
            self.resolves_obligation_ids
        ):
            raise ValueError("resolves_obligation_ids contains duplicates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LEGACY_TRANSITION_PROPOSAL_SCHEMA,
            "proposal_id": self.proposal_id,
            "base_state_digest": self.base_state_digest,
            "capability_id": self.capability_id,
            "intent": self.intent,
            "plan_sha256": self.plan_sha256,
            "resolves_obligation_ids": list(
                self.resolves_obligation_ids
            ),
        }


TransitionProposal = LegacyTransitionProposal


@dataclass(frozen=True, slots=True)
class TransitionObservation:
    observation_id: str
    proposal_id: str
    base_state_digest: str
    plan_sha256: str
    status: TransitionObservationStatus
    summary: str
    fact_updates: tuple[OperationalFact, ...] = ()
    new_obligations: tuple[OperationalObligation, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    exact_result_verified: bool = False
    world_may_have_changed: bool = False

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id")
        _text(self.proposal_id, "observation.proposal_id")
        _text(self.summary, "observation.summary")
        require_sha256(self.base_state_digest, "observation.base_state_digest")
        require_sha256(self.plan_sha256, "observation.plan_sha256")
        if not isinstance(self.status, TransitionObservationStatus):
            raise TypeError("observation.status is invalid")
        if not isinstance(self.fact_updates, tuple):
            raise TypeError("fact_updates must be a tuple")
        if not isinstance(self.new_obligations, tuple):
            raise TypeError("new_obligations must be a tuple")
        if len(self.fact_updates) > _MAX_ITEMS:
            raise ValueError("fact_updates exceeds bounded item count")
        if len(self.new_obligations) > _MAX_ITEMS:
            raise ValueError("new_obligations exceeds bounded item count")
        if len({item.key for item in self.fact_updates}) != len(
            self.fact_updates
        ):
            raise ValueError("fact update keys must be unique")
        if len({item.obligation_id for item in self.new_obligations}) != len(
            self.new_obligations
        ):
            raise ValueError("new obligation ids must be unique")
        _bounded_text_tuple(self.evidence_refs, "observation.evidence_refs")
        if type(self.exact_result_verified) is not bool:
            raise TypeError("exact_result_verified must be boolean")
        if type(self.world_may_have_changed) is not bool:
            raise TypeError("world_may_have_changed must be boolean")
        if self.status is TransitionObservationStatus.REJECTED_PREWRITE:
            if self.fact_updates:
                raise ValueError(
                    "a pre-write rejection cannot claim material fact updates"
                )
            if self.world_may_have_changed:
                raise ValueError(
                    "a pre-write rejection cannot report possible world mutation"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "TransitionObservation@1",
            "observation_id": self.observation_id,
            "proposal_id": self.proposal_id,
            "base_state_digest": self.base_state_digest,
            "plan_sha256": self.plan_sha256,
            "status": self.status.value,
            "summary": self.summary,
            "fact_updates": [
                {
                    "key": item.key,
                    "value": item.value,
                    "basis": item.basis.value,
                    "source_ref": item.source_ref,
                }
                for item in self.fact_updates
            ],
            "new_obligations": [
                {
                    "obligation_id": item.obligation_id,
                    "topic": item.topic,
                    "statement": item.statement,
                    "source_ref": item.source_ref,
                    "evidence_refs": list(item.evidence_refs),
                }
                for item in self.new_obligations
            ],
            "evidence_refs": list(self.evidence_refs),
            "exact_result_verified": self.exact_result_verified,
            "world_may_have_changed": self.world_may_have_changed,
        }


@dataclass(frozen=True, slots=True)
class OperationalTransitionReceipt:
    receipt_id: str
    status: TransitionReceiptStatus
    canonical_base: StateRef
    proposal_id: str
    observation_id: str
    before_state_digest: str
    after_state_digest: str
    operational_advanced: bool
    material_advanced: bool
    resolved_obligation_ids: tuple[str, ...]
    active_obligation_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "OperationalTransitionReceipt@1",
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "canonical_base": _state_ref_json(self.canonical_base),
            "proposal_id": self.proposal_id,
            "observation_id": self.observation_id,
            "before_state_digest": self.before_state_digest,
            "after_state_digest": self.after_state_digest,
            "operational_advanced": self.operational_advanced,
            "material_advanced": self.material_advanced,
            "resolved_obligation_ids": list(self.resolved_obligation_ids),
            "active_obligation_ids": list(self.active_obligation_ids),
            "evidence_refs": list(self.evidence_refs),
            "hard_usability_verdict": None,
            "aesthetic_winner": None,
            "canonical_state_transition": None,
        }


@dataclass(frozen=True, slots=True)
class OperationalTransitionResult:
    state: OperationalMarkovState
    receipt: OperationalTransitionReceipt
    proposal: TransitionProposal
    observation: TransitionObservation


@dataclass(frozen=True, slots=True)
class OperationalExpertDiscovery:
    state_digest: str
    canonical_base: StateRef
    obligation_ids: tuple[str, ...]
    expert_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "OperationalExpertDiscovery@1",
            "state_digest": self.state_digest,
            "canonical_base": _state_ref_json(self.canonical_base),
            "obligation_ids": list(self.obligation_ids),
            "expert_ids": list(self.expert_ids),
            "read_only": True,
        }


@dataclass(frozen=True, slots=True)
class OperationalTransitionTrace:
    initial_state: OperationalMarkovState
    final_state: OperationalMarkovState
    receipts: tuple[OperationalTransitionReceipt, ...]
    proposals: tuple[TransitionProposal, ...]
    observations: tuple[TransitionObservation, ...]
    discoveries: tuple[OperationalExpertDiscovery, ...]


def initial_operational_state(
    *,
    canonical_base: StateRef,
    facts: Sequence[OperationalFact] = (),
    obligations: Sequence[OperationalObligation] = (),
    evidence_refs: Sequence[str] = (),
) -> OperationalMarkovState:
    """Create candidate-working state without copying canonical write authority."""

    return OperationalMarkovState(
        canonical_base=canonical_base,
        epoch=0,
        material_revision=0,
        facts=tuple(sorted(facts, key=lambda item: item.key)),
        obligations=tuple(
            sorted(obligations, key=lambda item: item.obligation_id)
        ),
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
    )


def apply_operational_transition(
    state: OperationalMarkovState,
    proposal: TransitionProposal,
    observation: TransitionObservation,
) -> OperationalTransitionResult:
    """Compile one exact observed transition into the next working state."""

    before = state.state_digest
    if proposal.base_state_digest != before:
        raise OperationalTransitionError("proposal exact base is stale")
    if observation.base_state_digest != before:
        raise OperationalTransitionError("observation exact base is stale")
    if observation.proposal_id != proposal.proposal_id:
        raise OperationalTransitionError("observation proposal binding drifted")
    if observation.plan_sha256 != proposal.plan_sha256:
        raise OperationalTransitionError("observation plan digest drifted")
    if not observation.exact_result_verified:
        raise OperationalTransitionError(
            "pending or unverified side effects cannot become state facts"
        )

    facts = {item.key: item for item in state.facts}
    obligations = {item.obligation_id: item for item in state.obligations}
    unknown_resolutions = set(proposal.resolves_obligation_ids) - set(obligations)
    if unknown_resolutions:
        raise OperationalTransitionError(
            f"proposal resolves unknown obligations: {sorted(unknown_resolutions)}"
        )
    if (
        observation.status
        is TransitionObservationStatus.REJECTED_PREWRITE
        and proposal.resolves_obligation_ids
    ):
        raise OperationalTransitionError(
            "a rejected pre-write action cannot resolve obligations"
        )

    facts_changed = False
    material_advanced = False
    if observation.status is TransitionObservationStatus.CONFIRMED:
        for update in observation.fact_updates:
            prior = facts.get(update.key)
            if prior != update:
                facts_changed = True
            if prior is None or prior.value != update.value:
                material_advanced = True
            facts[update.key] = update
        for obligation_id in proposal.resolves_obligation_ids:
            obligations.pop(obligation_id)

    for obligation in observation.new_obligations:
        existing = obligations.get(obligation.obligation_id)
        if existing is not None and existing != obligation:
            raise OperationalTransitionError(
                f"obligation identity collision: {obligation.obligation_id}"
            )
        obligations[obligation.obligation_id] = obligation

    next_evidence = tuple(
        dict.fromkeys((*state.evidence_refs, *observation.evidence_refs))
    )
    obligations_changed = tuple(
        sorted(obligations.values(), key=lambda item: item.obligation_id)
    ) != tuple(
        sorted(state.obligations, key=lambda item: item.obligation_id)
    )
    evidence_changed = next_evidence != state.evidence_refs
    operational_advanced = (
        facts_changed or obligations_changed or evidence_changed
    )

    if operational_advanced:
        next_state = OperationalMarkovState(
            canonical_base=state.canonical_base,
            epoch=state.epoch + 1,
            material_revision=(
                state.material_revision + 1
                if material_advanced
                else state.material_revision
            ),
            facts=tuple(sorted(facts.values(), key=lambda item: item.key)),
            obligations=tuple(
                sorted(obligations.values(), key=lambda item: item.obligation_id)
            ),
            evidence_refs=next_evidence,
            parent_state_digest=before,
            last_transition_id=proposal.proposal_id,
        )
        status = (
            TransitionReceiptStatus.ADVANCED
            if observation.status is TransitionObservationStatus.CONFIRMED
            else TransitionReceiptStatus.ENVIRONMENT_OBSERVED
        )
    else:
        next_state = state
        status = TransitionReceiptStatus.NO_PROGRESS

    after = next_state.state_digest
    identity = {
        "status": status.value,
        "base": _state_ref_json(state.canonical_base),
        "proposal": proposal.proposal_id,
        "observation": observation.observation_id,
        "before": before,
        "after": after,
    }
    receipt_id = (
        "operational-transition-"
        + hashlib.sha256(canonical_json_bytes(identity, ascii=False)).hexdigest()[:20]
    )
    receipt = OperationalTransitionReceipt(
        receipt_id=receipt_id,
        status=status,
        canonical_base=state.canonical_base,
        proposal_id=proposal.proposal_id,
        observation_id=observation.observation_id,
        before_state_digest=before,
        after_state_digest=after,
        operational_advanced=operational_advanced,
        material_advanced=material_advanced,
        resolved_obligation_ids=(
            proposal.resolves_obligation_ids
            if observation.status is TransitionObservationStatus.CONFIRMED
            else ()
        ),
        active_obligation_ids=tuple(
            item.obligation_id for item in next_state.obligations
        ),
        evidence_refs=observation.evidence_refs,
    )
    return OperationalTransitionResult(
        state=next_state,
        receipt=receipt,
        proposal=proposal,
        observation=observation,
    )


def require_compiled_operational_state(
    value: object,
) -> CompiledOperationalMarkovState:
    """Accept only P041 state at new controller boundaries.

    There is deliberately no automatic V1-to-V2 migration: V1 lacks bindings,
    locks, commitments, dependencies, phase deliverables, evaluations, and
    uncertainty. Those factors must be recompiled from authoritative project
    records rather than fabricated from a legacy trace.
    """

    if isinstance(value, CompiledOperationalMarkovState):
        return value
    if isinstance(value, LegacyOperationalMarkovState):
        raise OperationalStateMigrationRequired(
            "OperationalMarkovState@1 is compatibility-only; recompile "
            "authoritative project records into OperationalMarkovState@2"
        )
    raise TypeError("value must be an OperationalMarkovState@2")


def discover_operational_experts(
    state: OperationalMarkovState,
    registry: ExpertRegistry,
    *,
    program_json: str | None = None,
) -> OperationalExpertDiscovery:
    """Discover read-only experts from this state, not from a fixed pipeline."""

    snapshot = ExpertSnapshot(
        base_state=state.canonical_base,
        program_json=program_json,
        obligations=tuple(
            ExpertObligation(
                obligation_id=item.obligation_id,
                topic=item.topic,
                statement=item.statement,
                source_ref=item.source_ref,
            )
            for item in state.obligations
        ),
        evidence=tuple(
            ExpertEvidence(
                kind="operational_state",
                evidence_ref=reference,
                summary="Evidence compiled into the operational Markov state.",
            )
            for reference in state.evidence_refs
        ),
    )
    discovered = registry.discover(snapshot)
    return OperationalExpertDiscovery(
        state_digest=state.state_digest,
        canonical_base=state.canonical_base,
        obligation_ids=tuple(
            item.obligation_id for item in state.obligations
        ),
        expert_ids=tuple(item.expert_id for item in discovered),
    )


def operational_plan_sha256(plan: Mapping[str, Any]) -> str:
    if not isinstance(plan, Mapping) or not plan:
        raise OperationalTransitionError("plan must be a non-empty mapping")
    try:
        frozen = json.loads(
            json.dumps(plan, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise OperationalTransitionError("plan must be finite JSON") from exc
    return hashlib.sha256(canonical_json_bytes(frozen, ascii=False)).hexdigest()


def compile_operational_transition_trace(
    *,
    initial_state: OperationalMarkovState,
    initial_discovery: OperationalExpertDiscovery,
    transitions: Sequence[
        tuple[OperationalTransitionResult, OperationalExpertDiscovery]
    ],
) -> dict[str, Any]:
    """Compile a repository-ready candidate trace without writing storage."""

    _validate_discovery(initial_state, initial_discovery)
    current = initial_state
    serialized_steps: list[dict[str, Any]] = []
    for result, discovery in transitions:
        if result.receipt.before_state_digest != current.state_digest:
            raise OperationalTransitionError("transition trace chain is broken")
        replayed = apply_operational_transition(
            current,
            result.proposal,
            result.observation,
        )
        if (
            replayed.state.state_digest != result.state.state_digest
            or replayed.receipt.to_dict() != result.receipt.to_dict()
        ):
            raise OperationalTransitionError(
                "transition result does not match deterministic reducer replay"
            )
        _validate_discovery(result.state, discovery)
        serialized_steps.append(
            {
                "proposal": result.proposal.to_dict(),
                "observation": result.observation.to_dict(),
                "receipt": result.receipt.to_dict(),
                "resulting_state": result.state.to_dict(),
                "expert_discovery": discovery.to_dict(),
            }
        )
        current = result.state
    payload: dict[str, Any] = {
        "schema": "OperationalTransitionTrace@1",
        "initial_state": initial_state.to_dict(),
        "initial_expert_discovery": initial_discovery.to_dict(),
        "transitions": serialized_steps,
        "final_state": current.to_dict(),
        "candidate_working_state_only": True,
        "tool_execution_authority": False,
        "hard_usability_verdict": None,
        "aesthetic_winner": None,
        "canonical_commit_receipt": None,
    }
    if len(canonical_json_bytes(payload, ascii=False)) > _MAX_JSON_BYTES:
        raise OperationalTransitionError("transition trace is too large")
    return payload


def load_operational_transition_trace(
    payload: Mapping[str, Any],
) -> OperationalTransitionTrace:
    """Verify a repository-loaded trace and every authority boundary."""

    try:
        frozen = json.loads(
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise OperationalTransitionError(
            "transition trace must be finite JSON"
        ) from exc
    if (
        not isinstance(frozen, dict)
        or len(canonical_json_bytes(frozen, ascii=False)) > _MAX_JSON_BYTES
    ):
        raise OperationalTransitionError("transition trace is not loadable")
    if frozen.get("schema") != "OperationalTransitionTrace@1":
        raise OperationalTransitionError("transition trace schema is invalid")
    if frozen.get("candidate_working_state_only") is not True:
        raise OperationalTransitionError("trace claimed non-candidate authority")
    for field in (
        "hard_usability_verdict",
        "aesthetic_winner",
        "canonical_commit_receipt",
    ):
        if frozen.get(field) is not None:
            raise OperationalTransitionError(
                f"transition trace acquired forbidden authority: {field}"
            )

    initial = _state_from_dict(frozen.get("initial_state"), "initial_state")
    initial_discovery = _discovery_from_dict(
        frozen.get("initial_expert_discovery"),
        "initial_expert_discovery",
    )
    _validate_discovery(initial, initial_discovery)
    current = initial
    receipts: list[OperationalTransitionReceipt] = []
    proposals: list[TransitionProposal] = []
    observations: list[TransitionObservation] = []
    discoveries: list[OperationalExpertDiscovery] = [initial_discovery]
    steps = frozen.get("transitions")
    if not isinstance(steps, list):
        raise OperationalTransitionError("trace transitions must be a list")
    for index, item in enumerate(steps):
        if not isinstance(item, dict):
            raise OperationalTransitionError(
                f"transitions[{index}] must be an object"
            )
        proposal = _proposal_from_dict(item.get("proposal"))
        observation = _observation_from_dict(item.get("observation"))
        receipt = _receipt_from_dict(item.get("receipt"))
        state = _state_from_dict(
            item.get("resulting_state"),
            f"transitions[{index}].resulting_state",
        )
        discovery = _discovery_from_dict(
            item.get("expert_discovery"),
            f"transitions[{index}].expert_discovery",
        )
        replayed = apply_operational_transition(
            current,
            proposal,
            observation,
        )
        if replayed.state.state_digest != state.state_digest:
            raise OperationalTransitionError(
                "deterministic state reducer replay drifted"
            )
        if replayed.receipt.to_dict() != receipt.to_dict():
            raise OperationalTransitionError(
                "deterministic transition receipt replay drifted"
            )
        if receipt.canonical_base != initial.canonical_base:
            raise OperationalTransitionError(
                "transition receipt canonical base drifted"
            )
        if receipt.before_state_digest != current.state_digest:
            raise OperationalTransitionError("transition before digest drifted")
        if receipt.after_state_digest != state.state_digest:
            raise OperationalTransitionError("transition after digest drifted")
        if receipt.operational_advanced:
            if state.parent_state_digest != current.state_digest:
                raise OperationalTransitionError("state parent digest drifted")
            if state.epoch != current.epoch + 1:
                raise OperationalTransitionError("state epoch is not sequential")
            expected_material = current.material_revision + int(
                receipt.material_advanced
            )
            if state.material_revision != expected_material:
                raise OperationalTransitionError(
                    "state material revision is not sequential"
                )
        elif state.state_digest != current.state_digest:
            raise OperationalTransitionError(
                "non-advancing receipt changed operational state"
            )
        if receipt.active_obligation_ids != tuple(
            obligation.obligation_id for obligation in state.obligations
        ):
            raise OperationalTransitionError(
                "receipt active obligations drifted"
            )
        _validate_discovery(state, discovery)
        receipts.append(receipt)
        proposals.append(proposal)
        observations.append(observation)
        discoveries.append(discovery)
        current = state

    final_state = _state_from_dict(frozen.get("final_state"), "final_state")
    if final_state.state_digest != current.state_digest:
        raise OperationalTransitionError("trace final state drifted")
    if final_state.canonical_base != initial.canonical_base:
        raise OperationalTransitionError("trace advanced canonical base")
    return OperationalTransitionTrace(
        initial_state=initial,
        final_state=final_state,
        receipts=tuple(receipts),
        proposals=tuple(proposals),
        observations=tuple(observations),
        discoveries=tuple(discoveries),
    )


def _state_identity(state: OperationalMarkovState) -> dict[str, Any]:
    return {
        "canonical_base": _state_ref_json(state.canonical_base),
        "epoch": state.epoch,
        "material_revision": state.material_revision,
        "parent_state_digest": state.parent_state_digest,
        "last_transition_id": state.last_transition_id,
        "facts": [
            {
                "key": item.key,
                "value": item.value,
                "basis": item.basis.value,
                "source_ref": item.source_ref,
            }
            for item in sorted(state.facts, key=lambda value: value.key)
        ],
        "obligations": [
            {
                "obligation_id": item.obligation_id,
                "topic": item.topic,
                "statement": item.statement,
                "source_ref": item.source_ref,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in sorted(
                state.obligations,
                key=lambda value: value.obligation_id,
            )
        ],
        "evidence_refs": list(state.evidence_refs),
    }


def _state_from_dict(value: object, field: str) -> OperationalMarkovState:
    if not isinstance(value, dict):
        raise OperationalTransitionError(f"{field} must be an object")
    if value.get("schema") != LEGACY_OPERATIONAL_STATE_SCHEMA:
        raise OperationalTransitionError(f"{field} schema is invalid")
    facts_value = value.get("facts")
    obligations_value = value.get("obligations")
    if not isinstance(facts_value, list) or not isinstance(
        obligations_value, list
    ):
        raise OperationalTransitionError(f"{field} items are invalid")
    facts = tuple(_fact_from_dict(item, field) for item in facts_value)
    obligations = tuple(
        _obligation_from_dict(item, field) for item in obligations_value
    )
    evidence_refs = _text_tuple_from_json(
        value.get("evidence_refs"),
        f"{field}.evidence_refs",
    )
    state = OperationalMarkovState(
        canonical_base=_state_ref_from_json(
            value.get("canonical_base"),
            f"{field}.canonical_base",
        ),
        epoch=_integer(value.get("epoch"), f"{field}.epoch"),
        material_revision=_integer(
            value.get("material_revision"),
            f"{field}.material_revision",
        ),
        facts=facts,
        obligations=obligations,
        evidence_refs=evidence_refs,
        parent_state_digest=value.get("parent_state_digest"),
        last_transition_id=value.get("last_transition_id"),
    )
    if value.get("state_digest") != state.state_digest:
        raise OperationalTransitionError(f"{field} digest drifted")
    return state


def _receipt_from_dict(value: object) -> OperationalTransitionReceipt:
    if not isinstance(value, dict):
        raise OperationalTransitionError("transition receipt must be an object")
    if value.get("schema") != "OperationalTransitionReceipt@1":
        raise OperationalTransitionError("transition receipt schema is invalid")
    for field in (
        "hard_usability_verdict",
        "aesthetic_winner",
        "canonical_state_transition",
    ):
        if value.get(field) is not None:
            raise OperationalTransitionError(
                f"transition receipt acquired forbidden authority: {field}"
            )
    try:
        status = TransitionReceiptStatus(value.get("status"))
    except ValueError as exc:
        raise OperationalTransitionError(
            "transition receipt status is invalid"
        ) from exc
    receipt = OperationalTransitionReceipt(
        receipt_id=_json_text(value.get("receipt_id"), "receipt_id"),
        status=status,
        canonical_base=_state_ref_from_json(
            value.get("canonical_base"),
            "receipt.canonical_base",
        ),
        proposal_id=_json_text(value.get("proposal_id"), "proposal_id"),
        observation_id=_json_text(
            value.get("observation_id"),
            "observation_id",
        ),
        before_state_digest=_json_digest(
            value.get("before_state_digest"),
            "before_state_digest",
        ),
        after_state_digest=_json_digest(
            value.get("after_state_digest"),
            "after_state_digest",
        ),
        operational_advanced=_json_bool(
            value.get("operational_advanced"),
            "operational_advanced",
        ),
        material_advanced=_json_bool(
            value.get("material_advanced"),
            "material_advanced",
        ),
        resolved_obligation_ids=_text_tuple_from_json(
            value.get("resolved_obligation_ids"),
            "resolved_obligation_ids",
        ),
        active_obligation_ids=_text_tuple_from_json(
            value.get("active_obligation_ids"),
            "active_obligation_ids",
        ),
        evidence_refs=_text_tuple_from_json(
            value.get("evidence_refs"),
            "receipt.evidence_refs",
        ),
    )
    if receipt.material_advanced and not receipt.operational_advanced:
        raise OperationalTransitionError(
            "material advance requires operational advance"
        )
    return receipt


def _proposal_from_dict(value: object) -> TransitionProposal:
    if not isinstance(value, dict):
        raise OperationalTransitionError("transition proposal must be an object")
    if value.get("schema") != LEGACY_TRANSITION_PROPOSAL_SCHEMA:
        raise OperationalTransitionError("transition proposal schema is invalid")
    try:
        return TransitionProposal(
            proposal_id=_json_text(
                value.get("proposal_id"),
                "proposal.proposal_id",
            ),
            base_state_digest=_json_digest(
                value.get("base_state_digest"),
                "proposal.base_state_digest",
            ),
            capability_id=_json_text(
                value.get("capability_id"),
                "proposal.capability_id",
            ),
            intent=_json_text(value.get("intent"), "proposal.intent"),
            plan_sha256=_json_digest(
                value.get("plan_sha256"),
                "proposal.plan_sha256",
            ),
            resolves_obligation_ids=_text_tuple_from_json(
                value.get("resolves_obligation_ids"),
                "proposal.resolves_obligation_ids",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise OperationalTransitionError(
            f"transition proposal is invalid: {exc}"
        ) from exc


def _observation_from_dict(value: object) -> TransitionObservation:
    if not isinstance(value, dict):
        raise OperationalTransitionError(
            "transition observation must be an object"
        )
    if value.get("schema") != "TransitionObservation@1":
        raise OperationalTransitionError(
            "transition observation schema is invalid"
        )
    fact_values = value.get("fact_updates")
    obligation_values = value.get("new_obligations")
    if not isinstance(fact_values, list) or not isinstance(
        obligation_values, list
    ):
        raise OperationalTransitionError(
            "transition observation items are invalid"
        )
    try:
        status = TransitionObservationStatus(value.get("status"))
        return TransitionObservation(
            observation_id=_json_text(
                value.get("observation_id"),
                "observation.observation_id",
            ),
            proposal_id=_json_text(
                value.get("proposal_id"),
                "observation.proposal_id",
            ),
            base_state_digest=_json_digest(
                value.get("base_state_digest"),
                "observation.base_state_digest",
            ),
            plan_sha256=_json_digest(
                value.get("plan_sha256"),
                "observation.plan_sha256",
            ),
            status=status,
            summary=_json_text(
                value.get("summary"),
                "observation.summary",
            ),
            fact_updates=tuple(
                _fact_from_dict(item, "observation.fact_updates")
                for item in fact_values
            ),
            new_obligations=tuple(
                _obligation_from_dict(
                    item,
                    "observation.new_obligations",
                )
                for item in obligation_values
            ),
            evidence_refs=_text_tuple_from_json(
                value.get("evidence_refs"),
                "observation.evidence_refs",
            ),
            exact_result_verified=_json_bool(
                value.get("exact_result_verified"),
                "observation.exact_result_verified",
            ),
            world_may_have_changed=_json_bool(
                value.get("world_may_have_changed"),
                "observation.world_may_have_changed",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise OperationalTransitionError(
            f"transition observation is invalid: {exc}"
        ) from exc


def _discovery_from_dict(
    value: object,
    field: str,
) -> OperationalExpertDiscovery:
    if not isinstance(value, dict):
        raise OperationalTransitionError(f"{field} must be an object")
    if value.get("schema") != "OperationalExpertDiscovery@1":
        raise OperationalTransitionError(f"{field} schema is invalid")
    if value.get("read_only") is not True:
        raise OperationalTransitionError(f"{field} lost read-only boundary")
    return OperationalExpertDiscovery(
        state_digest=_json_digest(
            value.get("state_digest"),
            f"{field}.state_digest",
        ),
        canonical_base=_state_ref_from_json(
            value.get("canonical_base"),
            f"{field}.canonical_base",
        ),
        obligation_ids=_text_tuple_from_json(
            value.get("obligation_ids"),
            f"{field}.obligation_ids",
        ),
        expert_ids=_text_tuple_from_json(
            value.get("expert_ids"),
            f"{field}.expert_ids",
        ),
    )


def _validate_discovery(
    state: OperationalMarkovState,
    discovery: OperationalExpertDiscovery,
) -> None:
    if discovery.state_digest != state.state_digest:
        raise OperationalTransitionError("expert discovery state drifted")
    if discovery.canonical_base != state.canonical_base:
        raise OperationalTransitionError(
            "expert discovery canonical base drifted"
        )
    if discovery.obligation_ids != tuple(
        item.obligation_id for item in state.obligations
    ):
        raise OperationalTransitionError(
            "expert discovery obligations drifted"
        )


def _fact_from_dict(value: object, field: str) -> OperationalFact:
    if not isinstance(value, dict):
        raise OperationalTransitionError(f"{field} contains invalid fact")
    try:
        basis = FactBasis(value.get("basis"))
    except ValueError as exc:
        raise OperationalTransitionError(f"{field} fact basis is invalid") from exc
    return OperationalFact(
        key=_json_text(value.get("key"), f"{field}.fact.key"),
        value=_json_text(value.get("value"), f"{field}.fact.value"),
        basis=basis,
        source_ref=_json_text(
            value.get("source_ref"),
            f"{field}.fact.source_ref",
        ),
    )


def _obligation_from_dict(
    value: object,
    field: str,
) -> OperationalObligation:
    if not isinstance(value, dict):
        raise OperationalTransitionError(
            f"{field} contains invalid obligation"
        )
    return OperationalObligation(
        obligation_id=_json_text(
            value.get("obligation_id"),
            f"{field}.obligation_id",
        ),
        topic=_json_text(value.get("topic"), f"{field}.topic"),
        statement=_json_text(
            value.get("statement"),
            f"{field}.statement",
        ),
        source_ref=_json_text(
            value.get("source_ref"),
            f"{field}.source_ref",
        ),
        evidence_refs=_text_tuple_from_json(
            value.get("evidence_refs"),
            f"{field}.evidence_refs",
        ),
    )


def _state_ref_json(value: StateRef) -> dict[str, Any]:
    return {"run_id": value.run_id, "version": value.version}


def _state_ref_from_json(value: object, field: str) -> StateRef:
    if not isinstance(value, dict):
        raise OperationalTransitionError(f"{field} must be an object")
    return StateRef(
        run_id=_json_text(value.get("run_id"), f"{field}.run_id"),
        version=_integer(value.get("version"), f"{field}.version"),
    )


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if len(value) > _MAX_TEXT:
        raise ValueError(f"{field} exceeds {_MAX_TEXT} characters")


def _bounded_text_tuple(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    for item in value:
        _text(item, f"{field} item")


def _json_text(value: object, field: str) -> str:
    try:
        _text(value, field)
    except (TypeError, ValueError) as exc:
        raise OperationalTransitionError(str(exc)) from exc
    assert isinstance(value, str)
    return value


def _json_digest(value: object, field: str) -> str:
    try:
        require_sha256(value, field)
    except ValueError as exc:
        raise OperationalTransitionError(str(exc)) from exc
    assert isinstance(value, str)
    return value


def _json_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise OperationalTransitionError(f"{field} must be boolean")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise OperationalTransitionError(
            f"{field} must be a non-negative integer"
        )
    return value


def _text_tuple_from_json(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise OperationalTransitionError(f"{field} must be a list")
    result = tuple(_json_text(item, f"{field} item") for item in value)
    if len(result) > _MAX_ITEMS:
        raise OperationalTransitionError(
            f"{field} exceeds bounded item count"
        )
    return result
