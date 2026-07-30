"""Asynchronous Primary Architect proposal orchestration.

The model selects from dynamically discovered read-only capabilities, receives
their detached receipts, and proposes an exact-base action. Only the existing
deterministic design controller may compile that proposal into next state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.adapters.model_provider import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertReceipt,
    ExpertRegistry,
)
from archflow.runtime.design_controller import (
    ControllerStatus,
    ControllerTurnResult,
    DesignControllerCheckpoint,
    DesignControllerError,
    GroundedArchitectAction,
    PreparedDesignTurn,
    apply_architect_action,
    consult_selected_experts,
)
from archflow.state.decision_operator import DecisionOperator
from archflow.state.operational_state import require_logical_ref


SELECTION_INSTRUCTIONS = (
    "Select zero or more available read-only capabilities in the order needed "
    "for this exact design state. Do not invent capabilities or design facts."
)
ACTION_INSTRUCTIONS = (
    "Propose one evidence-grounded exact-base design operator. Account for "
    "every advice receipt and cite current commitments, obligations, "
    "interfaces, or phase work. The proposal has no commit or world authority."
)


class PrimaryArchitectStatus(StrEnum):
    TRANSITIONED = "transitioned"
    CONTROLLER_STOPPED = "controller_stopped"
    PROVIDER_FAILED = "provider_failed"
    PROPOSAL_REJECTED = "proposal_rejected"


@dataclass(frozen=True, slots=True)
class PrimaryArchitectReceipt:
    receipt_id: str
    status: PrimaryArchitectStatus
    checkpoint_digest: str
    context_digest: str
    selection_receipt: ModelInvocationReceipt
    action_receipt: ModelInvocationReceipt | None
    controller_outcome: str | None
    next_checkpoint_digest: str | None
    error_code: str | None = None
    message: str | None = None

    SCHEMA = "PrimaryArchitectReceipt@1"

    def __post_init__(self) -> None:
        _text(self.receipt_id, "receipt_id")
        if not isinstance(self.status, PrimaryArchitectStatus):
            raise TypeError("status must be PrimaryArchitectStatus")
        _sha256(self.checkpoint_digest, "checkpoint_digest")
        _sha256(self.context_digest, "context_digest")
        if not isinstance(
            self.selection_receipt,
            ModelInvocationReceipt,
        ):
            raise TypeError(
                "selection_receipt must be ModelInvocationReceipt"
            )
        if self.action_receipt is not None and not isinstance(
            self.action_receipt,
            ModelInvocationReceipt,
        ):
            raise TypeError(
                "action_receipt must be ModelInvocationReceipt or None"
            )
        if self.next_checkpoint_digest is not None:
            _sha256(
                self.next_checkpoint_digest,
                "next_checkpoint_digest",
            )
        if self.status in {
            PrimaryArchitectStatus.TRANSITIONED,
            PrimaryArchitectStatus.CONTROLLER_STOPPED,
        }:
            if (
                self.action_receipt is None
                or self.controller_outcome is None
                or self.next_checkpoint_digest is None
                or self.error_code is not None
            ):
                raise ValueError(
                    "controller result receipt is incomplete"
                )
        elif self.error_code is None:
            raise ValueError("failed attempt requires error_code")
        if self.message is not None:
            _text(self.message, "message", maximum=1_000)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "checkpoint_digest": self.checkpoint_digest,
            "context_digest": self.context_digest,
            "selection_receipt": self.selection_receipt.to_dict(),
            "action_receipt": (
                None
                if self.action_receipt is None
                else self.action_receipt.to_dict()
            ),
            "controller_outcome": self.controller_outcome,
            "next_checkpoint_digest": self.next_checkpoint_digest,
            "error_code": self.error_code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: object) -> PrimaryArchitectReceipt:
        payload = _mapping(value, "primary Architect receipt")
        expected = {
            "schema",
            "receipt_id",
            "status",
            "checkpoint_digest",
            "context_digest",
            "selection_receipt",
            "action_receipt",
            "controller_outcome",
            "next_checkpoint_digest",
            "error_code",
            "message",
        }
        if set(payload) != expected or payload["schema"] != cls.SCHEMA:
            raise ValueError("primary Architect receipt schema drifted")
        action_receipt = payload["action_receipt"]
        return cls(
            receipt_id=payload["receipt_id"],
            status=PrimaryArchitectStatus(payload["status"]),
            checkpoint_digest=payload["checkpoint_digest"],
            context_digest=payload["context_digest"],
            selection_receipt=ModelInvocationReceipt.from_dict(
                payload["selection_receipt"]
            ),
            action_receipt=(
                None
                if action_receipt is None
                else ModelInvocationReceipt.from_dict(action_receipt)
            ),
            controller_outcome=payload["controller_outcome"],
            next_checkpoint_digest=payload["next_checkpoint_digest"],
            error_code=payload["error_code"],
            message=payload["message"],
        )


@dataclass(frozen=True, slots=True)
class PrimaryArchitectTurn:
    receipt: PrimaryArchitectReceipt
    controller_result: ControllerTurnResult | None = None


async def run_primary_architect_turn(
    checkpoint: DesignControllerCheckpoint,
    prepared: PreparedDesignTurn,
    registry: ExpertRegistry,
    provider: AsyncModelProvider,
    *,
    history_event_ref: str,
) -> PrimaryArchitectTurn:
    """Run at most two model calls and one deterministic controller action."""

    if not isinstance(checkpoint, DesignControllerCheckpoint):
        raise TypeError("checkpoint must be DesignControllerCheckpoint")
    if not isinstance(prepared, PreparedDesignTurn):
        raise TypeError("prepared must be PreparedDesignTurn")
    if prepared.checkpoint_digest != checkpoint.checkpoint_digest:
        raise DesignControllerError("prepared turn is stale")
    if not isinstance(registry, ExpertRegistry):
        raise TypeError("registry must be ExpertRegistry")
    require_logical_ref(history_event_ref, "history_event_ref")

    selection_request = ModelInvocationRequest.create(
        request_id=f"select-{checkpoint.checkpoint_digest[:24]}",
        phase=ModelPhase.CAPABILITY_SELECTION,
        checkpoint_digest=checkpoint.checkpoint_digest,
        context_digest=prepared.context.context_digest,
        payload={
            "schema": "PrimaryArchitectSelectionPrompt@1",
            "instructions": SELECTION_INSTRUCTIONS,
            "context": prepared.context.to_dict(),
            "available_capability_ids": list(
                prepared.discovered_expert_ids
            ),
            "required_output_schema": (
                "PrimaryArchitectCapabilitySelection@1"
            ),
        },
    )
    selection_receipt = await provider.invoke(selection_request)
    if selection_receipt.status is not ModelInvocationStatus.SUCCESS:
        return _failed(
            checkpoint,
            prepared,
            selection_receipt,
            None,
            PrimaryArchitectStatus.PROVIDER_FAILED,
            selection_receipt.error_code or "model.selection_failed",
            selection_receipt.message,
        )
    try:
        selected_expert_ids = _parse_selection(
            selection_receipt.output
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            selected_expert_ids,
        )
    except (TypeError, ValueError, DesignControllerError) as exc:
        return _failed(
            checkpoint,
            prepared,
            selection_receipt,
            None,
            PrimaryArchitectStatus.PROPOSAL_REJECTED,
            "architect.capability_selection_rejected",
            f"{type(exc).__name__}: {exc}",
        )

    target_state = checkpoint.tree.node(
        checkpoint.target_node_ref
    ).operational_state
    action_request = ModelInvocationRequest.create(
        request_id=f"action-{_digest({
            'checkpoint': checkpoint.checkpoint_digest,
            'selection': selection_receipt.receipt_id,
        })[:24]}",
        phase=ModelPhase.ACTION_PROPOSAL,
        checkpoint_digest=checkpoint.checkpoint_digest,
        context_digest=prepared.context.context_digest,
        payload={
            "schema": "PrimaryArchitectActionPrompt@1",
            "instructions": ACTION_INSTRUCTIONS,
            "context": prepared.context.to_dict(),
            "selected_capability_ids": list(selected_expert_ids),
            "expert_receipts": [
                _expert_receipt_payload(item)
                for item in consultation.receipts
            ],
            "exact_base_state_digest": target_state.state_digest,
            "operator_schema": DecisionOperator.SCHEMA,
            "required_output_schema": (
                "PrimaryArchitectActionProposal@1"
            ),
        },
    )
    action_receipt = await provider.invoke(action_request)
    if action_receipt.status is not ModelInvocationStatus.SUCCESS:
        return _failed(
            checkpoint,
            prepared,
            selection_receipt,
            action_receipt,
            PrimaryArchitectStatus.PROVIDER_FAILED,
            action_receipt.error_code or "model.action_failed",
            action_receipt.message,
        )
    try:
        action = _parse_action(
            action_receipt.output,
            checkpoint=checkpoint,
            prepared=prepared,
            selected_expert_ids=selected_expert_ids,
        )
        controller_result = apply_architect_action(
            checkpoint,
            prepared,
            consultation,
            action,
            history_event_ref=history_event_ref,
        )
    except (TypeError, ValueError, DesignControllerError) as exc:
        return _failed(
            checkpoint,
            prepared,
            selection_receipt,
            action_receipt,
            PrimaryArchitectStatus.PROPOSAL_REJECTED,
            "architect.action_proposal_rejected",
            f"{type(exc).__name__}: {exc}",
        )
    status = (
        PrimaryArchitectStatus.CONTROLLER_STOPPED
        if controller_result.checkpoint.status is ControllerStatus.STOPPED
        else PrimaryArchitectStatus.TRANSITIONED
    )
    receipt = _receipt(
        status=status,
        checkpoint=checkpoint,
        prepared=prepared,
        selection_receipt=selection_receipt,
        action_receipt=action_receipt,
        controller_outcome=controller_result.receipt.outcome.value,
        next_checkpoint_digest=(
            controller_result.checkpoint.checkpoint_digest
        ),
    )
    return PrimaryArchitectTurn(
        receipt=receipt,
        controller_result=controller_result,
    )


def _parse_selection(
    value: object,
) -> tuple[str, ...]:
    payload = _mapping(value, "capability selection")
    if set(payload) != {"schema", "selected_capability_ids"}:
        raise ValueError("capability selection fields drifted")
    if payload["schema"] != "PrimaryArchitectCapabilitySelection@1":
        raise ValueError("capability selection schema is unsupported")
    return _strings(
        payload["selected_capability_ids"],
        "selected_capability_ids",
    )


def _parse_action(
    value: object,
    *,
    checkpoint: DesignControllerCheckpoint,
    prepared: PreparedDesignTurn,
    selected_expert_ids: tuple[str, ...],
) -> GroundedArchitectAction:
    payload = _mapping(value, "action proposal")
    expected = {
        "schema",
        "action_id",
        "operator",
        "responds_to_refs",
        "adopted_advice_refs",
        "rejected_advice_refs",
        "tradeoff_rationale",
    }
    if set(payload) != expected:
        raise ValueError("action proposal fields drifted")
    if payload["schema"] != "PrimaryArchitectActionProposal@1":
        raise ValueError("action proposal schema is unsupported")
    return GroundedArchitectAction(
        action_id=payload["action_id"],
        checkpoint_digest=checkpoint.checkpoint_digest,
        context_digest=prepared.context.context_digest,
        operator=DecisionOperator.from_dict(payload["operator"]),
        responds_to_refs=_strings(
            payload["responds_to_refs"],
            "responds_to_refs",
        ),
        selected_expert_ids=selected_expert_ids,
        adopted_advice_refs=_strings(
            payload["adopted_advice_refs"],
            "adopted_advice_refs",
        ),
        rejected_advice_refs=_strings(
            payload["rejected_advice_refs"],
            "rejected_advice_refs",
        ),
        tradeoff_rationale=payload["tradeoff_rationale"],
    )


def _expert_receipt_payload(
    receipt: ExpertReceipt,
) -> dict[str, object]:
    advice: ExpertAdvice | None = receipt.advice
    return {
        "receipt_ref": f"expert-receipt:{receipt.receipt_id}",
        "expert_id": receipt.expert_id,
        "status": receipt.status.value,
        "attempts": receipt.attempts,
        "advice": (
            None
            if advice is None
            else {
                "summary": advice.summary,
                "findings": list(advice.findings),
                "suggested_obligations": list(
                    advice.suggested_obligations
                ),
                "evidence_refs": list(advice.evidence_refs),
            }
        ),
        "error_code": receipt.error_code,
        "message": receipt.message,
    }


def _failed(
    checkpoint: DesignControllerCheckpoint,
    prepared: PreparedDesignTurn,
    selection_receipt: ModelInvocationReceipt,
    action_receipt: ModelInvocationReceipt | None,
    status: PrimaryArchitectStatus,
    error_code: str,
    message: str | None,
) -> PrimaryArchitectTurn:
    return PrimaryArchitectTurn(
        receipt=_receipt(
            status=status,
            checkpoint=checkpoint,
            prepared=prepared,
            selection_receipt=selection_receipt,
            action_receipt=action_receipt,
            controller_outcome=None,
            next_checkpoint_digest=None,
            error_code=error_code,
            message=message,
        )
    )


def _receipt(
    *,
    status: PrimaryArchitectStatus,
    checkpoint: DesignControllerCheckpoint,
    prepared: PreparedDesignTurn,
    selection_receipt: ModelInvocationReceipt,
    action_receipt: ModelInvocationReceipt | None,
    controller_outcome: str | None,
    next_checkpoint_digest: str | None,
    error_code: str | None = None,
    message: str | None = None,
) -> PrimaryArchitectReceipt:
    identity = {
        "status": status.value,
        "checkpoint": checkpoint.checkpoint_digest,
        "context": prepared.context.context_digest,
        "selection": selection_receipt.receipt_id,
        "action": (
            None if action_receipt is None else action_receipt.receipt_id
        ),
        "controller_outcome": controller_outcome,
        "next_checkpoint": next_checkpoint_digest,
        "error_code": error_code,
    }
    return PrimaryArchitectReceipt(
        receipt_id=f"primary-architect-{_digest(identity)[:24]}",
        status=status,
        checkpoint_digest=checkpoint.checkpoint_digest,
        context_digest=prepared.context.context_digest,
        selection_receipt=selection_receipt,
        action_receipt=action_receipt,
        controller_outcome=controller_outcome,
        next_checkpoint_digest=next_checkpoint_digest,
        error_code=error_code,
        message=(None if message is None else message[:1_000]),
    )


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    result = tuple(value)
    if len(result) > 128 or len(result) != len(set(result)):
        raise ValueError(f"{field} must be bounded and unique")
    return result


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return dict(value)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _text(
    value: object,
    field: str,
    *,
    maximum: int = 1_000,
) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be bounded non-empty text")


def _sha256(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value.lower())
    ):
        raise ValueError(f"{field} must be a SHA-256 digest")
