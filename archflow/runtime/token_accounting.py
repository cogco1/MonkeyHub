"""Comparable token accounting across ArchFlow and direct-MCP lanes.

Payload size is retained as transport telemetry, but it is never converted
into tokens.  A lane comparison is valid only when the task, exact input,
model/provider, phase, and acceptance basis match and both receipts carry
complete provider-reported token telemetry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class TokenAccountingError(ValueError):
    """A receipt or accounting request is malformed."""


class AccountingLane(StrEnum):
    DIRECT_MCP = "direct_mcp"
    ARCHFLOW = "archflow"


class AccountingPhase(StrEnum):
    INITIAL = "initial"
    REVISION = "revision"


class ComparisonStatus(StrEnum):
    COMPARABLE = "COMPARABLE"
    INCOMPARABLE = "INCOMPARABLE"


class IncomparableReason(StrEnum):
    MISSING_DIRECT_TELEMETRY = "missing_direct_telemetry"
    MISSING_ARCHFLOW_TELEMETRY = "missing_archflow_telemetry"
    MISSING_TOKEN_TELEMETRY = "missing_token_telemetry"
    MISMATCHED_LANES = "mismatched_lanes"
    MISMATCHED_PHASE = "mismatched_phase"
    MISMATCHED_TASK = "mismatched_task"
    MISMATCHED_INPUT = "mismatched_input"
    MISMATCHED_MODEL = "mismatched_model"
    MISMATCHED_ACCEPTANCE_BASIS = "mismatched_acceptance_basis"
    MISMATCHED_ACCEPTANCE_OUTCOME = "mismatched_acceptance_outcome"
    MISMATCHED_ACCEPTANCE_IDENTITY = "mismatched_acceptance_identity"
    MISMATCHED_FIDELITY = "mismatched_fidelity"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TokenAccountingError(f"{field} must be non-empty text")
    return value


def _count(value: object, field: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TokenAccountingError(f"{field} must be a non-negative integer")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class TokenAccountingReceipt:
    """One lane's telemetry for one accepted or rejected task attempt.

    ``cached_tokens`` is a subset of ``input_tokens`` and is reported
    separately; it is not added again to ``total_tokens``.  Byte fields may
    be absent, but their presence can never repair missing token telemetry.
    """

    lane: AccountingLane
    phase: AccountingPhase
    task_identity: str
    input_identity: str
    provider_id: str
    model_id: str
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    request_bytes: int | None
    response_bytes: int | None
    tool_calls: int
    retries: int
    rejections: int
    acceptance_basis_identity: str
    acceptance_identity: str
    fidelity_identity: str
    accepted: bool

    SCHEMA = "TokenAccountingReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.lane, AccountingLane):
            raise TypeError("lane must be AccountingLane")
        if not isinstance(self.phase, AccountingPhase):
            raise TypeError("phase must be AccountingPhase")
        for field in (
            "task_identity",
            "input_identity",
            "provider_id",
            "model_id",
            "acceptance_basis_identity",
            "acceptance_identity",
            "fidelity_identity",
        ):
            _text(getattr(self, field), field)
        for field in (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "request_bytes",
            "response_bytes",
        ):
            _count(getattr(self, field), field, optional=True)
        for field in ("tool_calls", "retries", "rejections"):
            _count(getattr(self, field), field)
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be bool")
        if (
            self.cached_tokens is not None
            and self.input_tokens is not None
            and self.cached_tokens > self.input_tokens
        ):
            raise TokenAccountingError(
                "cached_tokens cannot exceed input_tokens"
            )

    @property
    def token_telemetry_complete(self) -> bool:
        return all(
            value is not None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.cached_tokens,
            )
        )

    @property
    def total_tokens(self) -> int | None:
        if not self.token_telemetry_complete:
            return None
        assert self.input_tokens is not None
        assert self.output_tokens is not None
        return self.input_tokens + self.output_tokens

    @property
    def uncached_token_load(self) -> int | None:
        """Uncached input plus output, without claiming a monetary cost."""

        if not self.token_telemetry_complete:
            return None
        assert self.input_tokens is not None
        assert self.output_tokens is not None
        assert self.cached_tokens is not None
        return self.input_tokens - self.cached_tokens + self.output_tokens

    @property
    def payload_bytes(self) -> int | None:
        if self.request_bytes is None or self.response_bytes is None:
            return None
        return self.request_bytes + self.response_bytes

    @property
    def receipt_digest(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "lane": self.lane.value,
            "phase": self.phase.value,
            "task_identity": self.task_identity,
            "input_identity": self.input_identity,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "tool_calls": self.tool_calls,
            "retries": self.retries,
            "rejections": self.rejections,
            "acceptance_basis_identity": self.acceptance_basis_identity,
            "acceptance_identity": self.acceptance_identity,
            "fidelity_identity": self.fidelity_identity,
            "accepted": self.accepted,
            "token_estimation_from_bytes": False,
        }


@dataclass(frozen=True, slots=True)
class LaneTokenComparison:
    status: ComparisonStatus
    reason: IncomparableReason | None
    phase: AccountingPhase | None
    direct_receipt_digest: str | None
    archflow_receipt_digest: str | None
    direct_total_tokens: int | None
    archflow_total_tokens: int | None
    direct_uncached_token_load: int | None
    archflow_uncached_token_load: int | None
    archflow_minus_direct_tokens: int | None
    lower_token_lane: AccountingLane | None

    SCHEMA = "LaneTokenComparison@1"

    @property
    def comparable(self) -> bool:
        return self.status is ComparisonStatus.COMPARABLE


def _incomparable(
    reason: IncomparableReason,
    direct: TokenAccountingReceipt | None,
    archflow: TokenAccountingReceipt | None,
) -> LaneTokenComparison:
    phase = None
    if direct is not None and archflow is not None and direct.phase is archflow.phase:
        phase = direct.phase
    return LaneTokenComparison(
        status=ComparisonStatus.INCOMPARABLE,
        reason=reason,
        phase=phase,
        direct_receipt_digest=(
            None if direct is None else direct.receipt_digest
        ),
        archflow_receipt_digest=(
            None if archflow is None else archflow.receipt_digest
        ),
        direct_total_tokens=None,
        archflow_total_tokens=None,
        direct_uncached_token_load=None,
        archflow_uncached_token_load=None,
        archflow_minus_direct_tokens=None,
        lower_token_lane=None,
    )


def compare_lane_token_usage(
    first: TokenAccountingReceipt,
    second: TokenAccountingReceipt,
) -> LaneTokenComparison:
    """Compare like-for-like lanes using reported tokens only.

    Initial and revision receipts cannot be compared to one another.  Cached
    tokens remain visible, while the lower-token lane is determined from raw
    provider-reported input plus output tokens without applying an invented
    cache price.
    """

    if not isinstance(first, TokenAccountingReceipt) or not isinstance(
        second, TokenAccountingReceipt
    ):
        raise TypeError("both values must be TokenAccountingReceipt")
    by_lane = {first.lane: first, second.lane: second}
    if set(by_lane) != {AccountingLane.DIRECT_MCP, AccountingLane.ARCHFLOW}:
        return _incomparable(IncomparableReason.MISMATCHED_LANES, None, None)
    direct = by_lane[AccountingLane.DIRECT_MCP]
    archflow = by_lane[AccountingLane.ARCHFLOW]
    if direct.phase is not archflow.phase:
        return _incomparable(
            IncomparableReason.MISMATCHED_PHASE, direct, archflow
        )
    if direct.task_identity != archflow.task_identity:
        return _incomparable(
            IncomparableReason.MISMATCHED_TASK, direct, archflow
        )
    if direct.input_identity != archflow.input_identity:
        return _incomparable(
            IncomparableReason.MISMATCHED_INPUT, direct, archflow
        )
    if (
        direct.provider_id,
        direct.model_id,
    ) != (
        archflow.provider_id,
        archflow.model_id,
    ):
        return _incomparable(
            IncomparableReason.MISMATCHED_MODEL, direct, archflow
        )
    if (
        direct.acceptance_basis_identity
        != archflow.acceptance_basis_identity
    ):
        return _incomparable(
            IncomparableReason.MISMATCHED_ACCEPTANCE_BASIS,
            direct,
            archflow,
        )
    if direct.accepted is not archflow.accepted:
        return _incomparable(
            IncomparableReason.MISMATCHED_ACCEPTANCE_OUTCOME,
            direct,
            archflow,
        )
    if direct.acceptance_identity != archflow.acceptance_identity:
        return _incomparable(
            IncomparableReason.MISMATCHED_ACCEPTANCE_IDENTITY,
            direct,
            archflow,
        )
    if direct.fidelity_identity != archflow.fidelity_identity:
        return _incomparable(
            IncomparableReason.MISMATCHED_FIDELITY,
            direct,
            archflow,
        )
    if not direct.token_telemetry_complete:
        return _incomparable(
            IncomparableReason.MISSING_DIRECT_TELEMETRY, direct, archflow
        )
    if not archflow.token_telemetry_complete:
        return _incomparable(
            IncomparableReason.MISSING_ARCHFLOW_TELEMETRY,
            direct,
            archflow,
        )

    direct_total = direct.total_tokens
    archflow_total = archflow.total_tokens
    assert direct_total is not None
    assert archflow_total is not None
    lower_lane = None
    if direct_total < archflow_total:
        lower_lane = AccountingLane.DIRECT_MCP
    elif archflow_total < direct_total:
        lower_lane = AccountingLane.ARCHFLOW
    return LaneTokenComparison(
        status=ComparisonStatus.COMPARABLE,
        reason=None,
        phase=direct.phase,
        direct_receipt_digest=direct.receipt_digest,
        archflow_receipt_digest=archflow.receipt_digest,
        direct_total_tokens=direct_total,
        archflow_total_tokens=archflow_total,
        direct_uncached_token_load=direct.uncached_token_load,
        archflow_uncached_token_load=archflow.uncached_token_load,
        archflow_minus_direct_tokens=archflow_total - direct_total,
        lower_token_lane=lower_lane,
    )


@dataclass(frozen=True, slots=True)
class FailedTokenShare:
    status: ComparisonStatus
    reason: IncomparableReason | None
    receipt_count: int
    failed_receipt_count: int
    total_tokens: int | None
    failed_tokens: int | None
    share: float | None

    SCHEMA = "FailedTokenShare@1"


def compute_failed_token_share(
    receipts: Iterable[TokenAccountingReceipt],
) -> FailedTokenShare:
    """Return exact receipt-level failed-token share.

    A failed receipt is one whose acceptance outcome is false.  Retries inside
    an aggregate accepted receipt are not guessed to be failed tokens; callers
    need one receipt per attributable attempt for that finer accounting.
    """

    values = tuple(receipts)
    if not values:
        raise TokenAccountingError("failed-token share requires receipts")
    if any(not isinstance(item, TokenAccountingReceipt) for item in values):
        raise TypeError("receipts must contain TokenAccountingReceipt values")
    incomplete = tuple(
        item for item in values if not item.token_telemetry_complete
    )
    if incomplete:
        if any(item.lane is AccountingLane.DIRECT_MCP for item in incomplete):
            reason = IncomparableReason.MISSING_DIRECT_TELEMETRY
        elif any(item.lane is AccountingLane.ARCHFLOW for item in incomplete):
            reason = IncomparableReason.MISSING_ARCHFLOW_TELEMETRY
        else:
            reason = IncomparableReason.MISSING_TOKEN_TELEMETRY
        return FailedTokenShare(
            status=ComparisonStatus.INCOMPARABLE,
            reason=reason,
            receipt_count=len(values),
            failed_receipt_count=sum(not item.accepted for item in values),
            total_tokens=None,
            failed_tokens=None,
            share=None,
        )
    totals = tuple(item.total_tokens for item in values)
    assert all(item is not None for item in totals)
    total_tokens = sum(int(item) for item in totals)
    failed_tokens = sum(
        int(item.total_tokens or 0) for item in values if not item.accepted
    )
    return FailedTokenShare(
        status=ComparisonStatus.COMPARABLE,
        reason=None,
        receipt_count=len(values),
        failed_receipt_count=sum(not item.accepted for item in values),
        total_tokens=total_tokens,
        failed_tokens=failed_tokens,
        share=(failed_tokens / total_tokens if total_tokens else 0.0),
    )
