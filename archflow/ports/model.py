"""External model invocation port contracts.

This module owns only provider-independent request, response, status, and
Protocol boundaries. Concrete command execution remains in adapters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.project.refs import require_identifier


class ModelPhase(StrEnum):
    CAPABILITY_SELECTION = "capability_selection"
    ACTION_PROPOSAL = "action_proposal"
    SPATIAL_PROPOSAL = "spatial_proposal"
    RESEARCH = "research"


class ModelInvocationStatus(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    EXIT_ERROR = "exit_error"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class ModelInvocationRequest:
    request_id: str
    phase: ModelPhase
    checkpoint_digest: str
    context_digest: str
    payload_json: str

    SCHEMA = "ModelInvocationRequest@1"

    def __post_init__(self) -> None:
        require_identifier(self.request_id, "request_id")
        if not isinstance(self.phase, ModelPhase):
            raise TypeError("phase must be ModelPhase")
        _sha256(self.checkpoint_digest, "checkpoint_digest")
        _sha256(self.context_digest, "context_digest")
        payload = _decode_object(self.payload_json, "payload_json")
        canonical = _canonical_json(payload)
        if canonical != self.payload_json:
            raise ValueError("payload_json must use canonical JSON")
        forbidden = {
            "raw_history",
            "transcript",
            "canonical_writer",
            "workspace_path",
            "world_handle",
        }
        overlap = forbidden.intersection(_nested_keys(payload))
        if overlap:
            raise ValueError(
                f"model payload contains forbidden fields: {sorted(overlap)}"
            )

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        phase: ModelPhase,
        checkpoint_digest: str,
        context_digest: str,
        payload: Mapping[str, object],
    ) -> ModelInvocationRequest:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        return cls(
            request_id=request_id,
            phase=phase,
            checkpoint_digest=checkpoint_digest,
            context_digest=context_digest,
            payload_json=_canonical_json(dict(payload)),
        )

    @property
    def payload(self) -> dict[str, object]:
        return _decode_object(self.payload_json, "payload_json")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "request_id": self.request_id,
            "phase": self.phase.value,
            "checkpoint_digest": self.checkpoint_digest,
            "context_digest": self.context_digest,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: object) -> ModelInvocationRequest:
        payload = _mapping(value, "model request")
        expected = {
            "schema",
            "request_id",
            "phase",
            "checkpoint_digest",
            "context_digest",
            "payload",
        }
        if set(payload) != expected or payload["schema"] != cls.SCHEMA:
            raise ValueError("model request schema drifted")
        body = _mapping(payload["payload"], "model request payload")
        return cls.create(
            request_id=payload["request_id"],
            phase=ModelPhase(payload["phase"]),
            checkpoint_digest=payload["checkpoint_digest"],
            context_digest=payload["context_digest"],
            payload=body,
        )


@dataclass(frozen=True, slots=True)
class ModelInvocationReceipt:
    receipt_id: str
    status: ModelInvocationStatus
    request: ModelInvocationRequest
    provider_id: str
    model_id: str
    provider_version: str
    provider_fingerprint: str
    input_bytes: int
    output_bytes: int
    output_sha256: str | None
    duration_ms: int = 0
    output_json: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    message: str | None = None

    SCHEMA = "ModelInvocationReceipt@2"
    LEGACY_SCHEMA = "ModelInvocationReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        if not isinstance(self.status, ModelInvocationStatus):
            raise TypeError("status must be ModelInvocationStatus")
        if not isinstance(self.request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        for value, field in (
            (self.provider_id, "provider_id"),
            (self.model_id, "model_id"),
            (self.provider_version, "provider_version"),
            (self.provider_fingerprint, "provider_fingerprint"),
        ):
            _text(value, field, maximum=1_000)
        for value, field in (
            (self.input_bytes, "input_bytes"),
            (self.output_bytes, "output_bytes"),
            (self.duration_ms, "duration_ms"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be non-negative")
        if self.output_sha256 is not None:
            _sha256(self.output_sha256, "output_sha256")
        for value, field in (
            (self.input_tokens, "input_tokens"),
            (self.output_tokens, "output_tokens"),
        ):
            if value is not None and (
                type(value) is not int or value < 0
            ):
                raise ValueError(f"{field} must be non-negative or None")
        if self.status is ModelInvocationStatus.SUCCESS:
            if (
                self.output_json is None
                or self.output_sha256 is None
                or self.error_code is not None
            ):
                raise ValueError(
                    "successful model receipt requires output and no error"
                )
            _decode_object(self.output_json, "output_json")
        elif self.output_json is not None or self.error_code is None:
            raise ValueError(
                "failed model receipt requires an error and no output"
            )
        if self.message is not None:
            _text(self.message, "message", maximum=1_000)

    @property
    def output(self) -> dict[str, object] | None:
        if self.output_json is None:
            return None
        return _decode_object(self.output_json, "output_json")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "request": self.request.to_dict(),
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_version": self.provider_version,
            "provider_fingerprint": self.provider_fingerprint,
            "input_bytes": self.input_bytes,
            "output_bytes": self.output_bytes,
            "output_sha256": self.output_sha256,
            "duration_ms": self.duration_ms,
            "output": self.output,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error_code": self.error_code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: object) -> ModelInvocationReceipt:
        payload = _mapping(value, "model receipt")
        base_expected = {
            "schema",
            "receipt_id",
            "status",
            "request",
            "provider_id",
            "model_id",
            "provider_version",
            "provider_fingerprint",
            "input_bytes",
            "output_bytes",
            "output_sha256",
            "output",
            "input_tokens",
            "output_tokens",
            "error_code",
            "message",
        }
        schema = payload.get("schema")
        if schema == cls.SCHEMA:
            expected = base_expected | {"duration_ms"}
            duration_ms = payload.get("duration_ms")
        elif schema == cls.LEGACY_SCHEMA:
            expected = base_expected
            duration_ms = 0
        else:
            raise ValueError("model receipt schema drifted")
        if set(payload) != expected:
            raise ValueError("model receipt schema drifted")
        output = payload["output"]
        if output is not None:
            output = _mapping(output, "model receipt output")
        return cls(
            receipt_id=payload["receipt_id"],
            status=ModelInvocationStatus(payload["status"]),
            request=ModelInvocationRequest.from_dict(payload["request"]),
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            provider_version=payload["provider_version"],
            provider_fingerprint=payload["provider_fingerprint"],
            input_bytes=payload["input_bytes"],
            output_bytes=payload["output_bytes"],
            output_sha256=payload["output_sha256"],
            duration_ms=duration_ms,
            output_json=(
                None if output is None else _canonical_json(output)
            ),
            input_tokens=payload["input_tokens"],
            output_tokens=payload["output_tokens"],
            error_code=payload["error_code"],
            message=payload["message"],
        )


class AsyncModelProvider(Protocol):
    async def invoke(
        self,
        request: ModelInvocationRequest,
    ) -> ModelInvocationReceipt: ...


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_object(value: str, field: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise TypeError(f"{field} must encode a JSON object")
    return payload


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field} keys must be text")
    return dict(value)


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                keys.add(key)
            keys.update(_nested_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.update(_nested_keys(item))
    return keys


def _text(value: object, field: str, *, maximum: int) -> None:
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


__all__ = [
    "AsyncModelProvider",
    "ModelInvocationReceipt",
    "ModelInvocationRequest",
    "ModelInvocationStatus",
    "ModelPhase",
]

