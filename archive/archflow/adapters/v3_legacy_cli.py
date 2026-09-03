"""Explicit bounded subprocess bridge to one quarantined V3 capability."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from archflow.project.refs import ProjectVersionRef
from archflow.project.refs import require_identifier
from archflow.contracts.canonical import canonical_digest, canonical_json, require_sha256


class V3LegacyStatus(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    EXIT_ERROR = "exit_error"
    MALFORMED = "malformed"
    INPUT_OVERSIZED = "input_oversized"
    OUTPUT_OVERSIZED = "output_oversized"
    CAPABILITY_MISMATCH = "capability_mismatch"
    MISSING_PROVIDER = "missing_provider"


class V3LegacyBoundaryError(ValueError):
    """Detached input is unsafe or outside the bounded JSON contract."""


_FORBIDDEN_HANDLE_KEYS = {
    "canonical_repository",
    "canonical_store",
    "canonical_writer",
    "committer",
    "credentials",
    "mcp_client",
    "password",
    "project_repository",
    "secret",
    "state_store",
    "token",
    "world_handle",
    "world_writer",
}
_FORBIDDEN_V3_ENTRY_TOKENS = {
    "archflow.cli",
    "archflow/cli.py",
    "archflow.examples",
    "compose_building",
    "decision.v2_stages",
    "decision/v2_stages.py",
    "example_planner",
    "legacy_pack_adapter",
    "register_builtin_packs",
}


@dataclass(frozen=True, slots=True)
class V3LegacyProviderSpec:
    provider_id: str
    provider_version: str
    capability_id: str
    v3_fingerprint: str
    command: tuple[str, ...]
    timeout_seconds: float = 15.0
    max_input_bytes: int = 64_000
    max_output_bytes: int = 64_000

    def __post_init__(self) -> None:
        require_identifier(self.provider_id, "provider_id")
        require_identifier(self.capability_id, "capability_id")
        _text(self.provider_version, "provider_version")
        require_sha256(self.v3_fingerprint, "v3_fingerprint")
        if not isinstance(self.command, tuple) or not self.command:
            raise ValueError("command must be an explicit non-empty tuple")
        for item in self.command:
            _text(item, "command item", maximum=8_000)
        normalized_command = " ".join(self.command).lower().replace("\\", "/")
        matched = sorted(
            token
            for token in _FORBIDDEN_V3_ENTRY_TOKENS
            if token in normalized_command
        )
        if matched:
            raise V3LegacyBoundaryError(
                "command targets a forbidden V2, Pack, composer, or generic "
                f"V3 entrypoint: {matched}"
            )
        if not isinstance(self.timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be numeric")
        if not 0 < self.timeout_seconds <= 120:
            raise ValueError(
                "timeout_seconds must be greater than 0 and at most 120"
            )
        for value, field in (
            (self.max_input_bytes, "max_input_bytes"),
            (self.max_output_bytes, "max_output_bytes"),
        ):
            if type(value) is not int or not 256 <= value <= 2_000_000:
                raise ValueError(
                    f"{field} must be between 256 and 2000000"
                )

    @property
    def provider_fingerprint(self) -> str:
        return canonical_digest(
            {
                "provider_id": self.provider_id,
                "provider_version": self.provider_version,
                "capability_id": self.capability_id,
                "v3_fingerprint": self.v3_fingerprint,
                "command": list(self.command),
            }
        , ascii=False)


@dataclass(frozen=True, slots=True)
class V3LegacyCapabilityRequest:
    request_id: str
    project_id: str
    run_id: str
    workspace_id: str
    base: ProjectVersionRef
    capability_id: str
    detached_snapshot_json: str
    obligation_json: str
    evidence_refs: tuple[str, ...] = ()

    SCHEMA = "V3LegacyCapabilityRequest@1"

    def __post_init__(self) -> None:
        require_identifier(self.request_id, "request_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.workspace_id, "workspace_id")
        require_identifier(self.capability_id, "capability_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise V3LegacyBoundaryError(
                "request and base belong to different projects"
            )
        self.base.require_digest()
        _require_canonical_object_json(
            self.detached_snapshot_json,
            "detached_snapshot_json",
        )
        _require_canonical_object_json(
            self.obligation_json,
            "obligation_json",
        )
        _refs(self.evidence_refs, "evidence_refs")

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        project_id: str,
        run_id: str,
        workspace_id: str,
        base: ProjectVersionRef,
        capability_id: str,
        detached_snapshot: dict[str, Any],
        obligation: dict[str, Any],
        evidence_refs: tuple[str, ...] = (),
    ) -> V3LegacyCapabilityRequest:
        return cls(
            request_id=request_id,
            project_id=project_id,
            run_id=run_id,
            workspace_id=workspace_id,
            base=base,
            capability_id=capability_id,
            detached_snapshot_json=_detached_object_json(
                detached_snapshot,
                "detached_snapshot",
            ),
            obligation_json=_detached_object_json(
                obligation,
                "obligation",
            ),
            evidence_refs=evidence_refs,
        )

    @property
    def detached_snapshot(self) -> dict[str, Any]:
        return json.loads(self.detached_snapshot_json)

    @property
    def obligation(self) -> dict[str, Any]:
        return json.loads(self.obligation_json)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "request_id": self.request_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "base": _base_payload(self.base),
            "capability_id": self.capability_id,
            "detached_snapshot": self.detached_snapshot,
            "obligation": self.obligation,
            "evidence_refs": list(self.evidence_refs),
            "canonical_write_authority": False,
            "live_world_authority": False,
        }


@dataclass(frozen=True, slots=True)
class V3LegacyCapabilityOutput:
    kind: str
    payload_json: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in {"observation", "proposal"}:
            raise ValueError("kind must be observation or proposal")
        _require_canonical_object_json(self.payload_json, "payload_json")
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "evidence_refs": list(self.evidence_refs),
            "canonical_write_authority": False,
            "live_world_authority": False,
        }


@dataclass(frozen=True, slots=True)
class V3LegacyCapabilityReceipt:
    receipt_id: str
    status: V3LegacyStatus
    request: V3LegacyCapabilityRequest
    provider_id: str
    provider_version: str
    provider_fingerprint: str
    capability_id: str
    v3_fingerprint: str
    command: tuple[str, ...]
    duration_ms: int
    output_sha256: str | None
    output: V3LegacyCapabilityOutput | None = None
    error_code: str | None = None
    message: str | None = None

    SCHEMA = "V3LegacyCapabilityReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        if not isinstance(self.status, V3LegacyStatus):
            raise TypeError("status must be V3LegacyStatus")
        if not isinstance(self.request, V3LegacyCapabilityRequest):
            raise TypeError("request must be V3LegacyCapabilityRequest")
        _text(self.provider_id, "provider_id")
        _text(self.provider_version, "provider_version")
        _text(self.provider_fingerprint, "provider_fingerprint")
        _text(self.capability_id, "capability_id")
        _text(self.v3_fingerprint, "v3_fingerprint")
        if not isinstance(self.command, tuple):
            raise TypeError("command must be tuple")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")
        if self.output_sha256 is not None:
            require_sha256(self.output_sha256, "output_sha256")
        if self.status is V3LegacyStatus.SUCCESS:
            if (
                self.output is None
                or self.output_sha256 is None
                or self.error_code is not None
            ):
                raise ValueError(
                    "successful receipt requires output and no error"
                )
        elif self.output is not None or self.error_code is None:
            raise ValueError(
                "failed receipt requires an error and no output"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "request": self.request.to_payload(),
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "provider_fingerprint": self.provider_fingerprint,
            "capability_id": self.capability_id,
            "v3_fingerprint": self.v3_fingerprint,
            "command": list(self.command),
            "duration_ms": self.duration_ms,
            "output_sha256": self.output_sha256,
            "output": self.output.to_dict() if self.output else None,
            "error_code": self.error_code,
            "message": self.message,
            "fallback_attempted": False,
            "canonical_write_authority": False,
            "live_world_authority": False,
            "persistence_authority": False,
        }


class V3LegacyCliBridge:
    """Invoke exactly one configured capability with no import or fallback."""

    def __init__(self, spec: V3LegacyProviderSpec) -> None:
        if not isinstance(spec, V3LegacyProviderSpec):
            raise TypeError("spec must be V3LegacyProviderSpec")
        self.spec = spec

    def invoke(
        self,
        request: V3LegacyCapabilityRequest,
    ) -> V3LegacyCapabilityReceipt:
        if not isinstance(request, V3LegacyCapabilityRequest):
            raise TypeError("request must be V3LegacyCapabilityRequest")
        if request.capability_id != self.spec.capability_id:
            return self._failure(
                request,
                V3LegacyStatus.CAPABILITY_MISMATCH,
                "v3_legacy.capability_mismatch",
                "configured provider does not expose the requested capability",
                duration_ms=0,
            )
        input_bytes = canonical_json(request.to_payload(), ascii=False).encode("utf-8")
        if len(input_bytes) > self.spec.max_input_bytes:
            return self._failure(
                request,
                V3LegacyStatus.INPUT_OVERSIZED,
                "v3_legacy.input_oversized",
                f"request exceeds {self.spec.max_input_bytes} bytes",
                duration_ms=0,
            )

        started = time.monotonic()
        creation_flags = (
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        try:
            completed = subprocess.run(
                self.spec.command,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.spec.timeout_seconds,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except FileNotFoundError:
            return self._failure(
                request,
                V3LegacyStatus.OFFLINE,
                "v3_legacy.provider_unavailable",
                "configured provider executable was not found",
                duration_ms=_duration_ms(started),
            )
        except subprocess.TimeoutExpired:
            return self._failure(
                request,
                V3LegacyStatus.TIMEOUT,
                "v3_legacy.timeout",
                f"provider exceeded {self.spec.timeout_seconds:g} seconds",
                duration_ms=_duration_ms(started),
            )
        except OSError as exc:
            return self._failure(
                request,
                V3LegacyStatus.OFFLINE,
                "v3_legacy.provider_os_error",
                f"{type(exc).__name__}: {exc}",
                duration_ms=_duration_ms(started),
            )

        duration_ms = _duration_ms(started)
        output_digest = hashlib.sha256(completed.stdout).hexdigest()
        if len(completed.stdout) > self.spec.max_output_bytes:
            return self._failure(
                request,
                V3LegacyStatus.OUTPUT_OVERSIZED,
                "v3_legacy.output_oversized",
                f"output exceeds {self.spec.max_output_bytes} bytes",
                duration_ms=duration_ms,
                output_sha256=output_digest,
            )
        if completed.returncode != 0:
            stderr = completed.stderr.decode(
                "utf-8",
                errors="replace",
            )[:500]
            return self._failure(
                request,
                V3LegacyStatus.EXIT_ERROR,
                "v3_legacy.provider_exit",
                f"provider exited {completed.returncode}: {stderr}",
                duration_ms=duration_ms,
                output_sha256=output_digest,
            )
        try:
            raw = json.loads(completed.stdout.decode("utf-8"))
            output = self._parse_output(request, raw)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            return self._failure(
                request,
                V3LegacyStatus.MALFORMED,
                "v3_legacy.output_malformed",
                f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
                output_sha256=output_digest,
            )

        identity = self._identity(
            request,
            V3LegacyStatus.SUCCESS,
            output_digest,
            None,
        )
        return V3LegacyCapabilityReceipt(
            receipt_id=f"v3-legacy-{canonical_digest(identity, ascii=False)[:20]}",
            status=V3LegacyStatus.SUCCESS,
            request=request,
            provider_id=self.spec.provider_id,
            provider_version=self.spec.provider_version,
            provider_fingerprint=self.spec.provider_fingerprint,
            capability_id=self.spec.capability_id,
            v3_fingerprint=self.spec.v3_fingerprint,
            command=self.spec.command,
            duration_ms=duration_ms,
            output_sha256=output_digest,
            output=output,
        )

    def _parse_output(
        self,
        request: V3LegacyCapabilityRequest,
        raw: object,
    ) -> V3LegacyCapabilityOutput:
        expected_fields = {
            "schema",
            "request_id",
            "provider_id",
            "provider_version",
            "capability_id",
            "v3_fingerprint",
            "workspace_id",
            "base",
            "kind",
            "evidence_refs",
            "payload",
        }
        if not isinstance(raw, dict) or set(raw) != expected_fields:
            raise V3LegacyBoundaryError("provider output fields drifted")
        if raw["schema"] != "V3LegacyCapabilityOutput@1":
            raise V3LegacyBoundaryError("provider output schema is unsupported")
        for actual, expected, field in (
            (raw["request_id"], request.request_id, "request_id"),
            (raw["provider_id"], self.spec.provider_id, "provider_id"),
            (
                raw["provider_version"],
                self.spec.provider_version,
                "provider_version",
            ),
            (
                raw["capability_id"],
                self.spec.capability_id,
                "capability_id",
            ),
            (
                raw["v3_fingerprint"],
                self.spec.v3_fingerprint,
                "v3_fingerprint",
            ),
            (
                raw["workspace_id"],
                request.workspace_id,
                "workspace_id",
            ),
            (raw["base"], _base_payload(request.base), "base"),
        ):
            if actual != expected:
                raise V3LegacyBoundaryError(
                    f"provider output {field} does not match request"
                )
        evidence_refs = raw["evidence_refs"]
        if not isinstance(evidence_refs, list):
            raise TypeError("evidence_refs must be a JSON array")
        evidence_tuple = tuple(evidence_refs)
        _refs(evidence_tuple, "evidence_refs")
        if not set(request.evidence_refs).issubset(evidence_tuple):
            raise V3LegacyBoundaryError(
                "provider output dropped request evidence"
            )
        return V3LegacyCapabilityOutput(
            kind=raw["kind"],
            payload_json=_detached_object_json(raw["payload"], "payload"),
            evidence_refs=evidence_tuple,
        )

    def _failure(
        self,
        request: V3LegacyCapabilityRequest,
        status: V3LegacyStatus,
        error_code: str,
        message: str,
        *,
        duration_ms: int,
        output_sha256: str | None = None,
    ) -> V3LegacyCapabilityReceipt:
        identity = self._identity(
            request,
            status,
            output_sha256,
            error_code,
        )
        return V3LegacyCapabilityReceipt(
            receipt_id=f"v3-legacy-{canonical_digest(identity, ascii=False)[:20]}",
            status=status,
            request=request,
            provider_id=self.spec.provider_id,
            provider_version=self.spec.provider_version,
            provider_fingerprint=self.spec.provider_fingerprint,
            capability_id=self.spec.capability_id,
            v3_fingerprint=self.spec.v3_fingerprint,
            command=self.spec.command,
            duration_ms=duration_ms,
            output_sha256=output_sha256,
            error_code=error_code,
            message=message[:500],
        )

    def _identity(
        self,
        request: V3LegacyCapabilityRequest,
        status: V3LegacyStatus,
        output_sha256: str | None,
        error_code: str | None,
    ) -> dict[str, Any]:
        return {
            "request": request.to_payload(),
            "provider_fingerprint": self.spec.provider_fingerprint,
            "status": status.value,
            "output_sha256": output_sha256,
            "error_code": error_code,
        }


def missing_v3_provider_receipt(
    request: V3LegacyCapabilityRequest,
    *,
    provider_id: str,
) -> V3LegacyCapabilityReceipt:
    require_identifier(provider_id, "provider_id")
    identity = {
        "request": request.to_payload(),
        "provider_id": provider_id,
        "status": V3LegacyStatus.MISSING_PROVIDER.value,
    }
    return V3LegacyCapabilityReceipt(
        receipt_id=f"v3-legacy-{canonical_digest(identity, ascii=False)[:20]}",
        status=V3LegacyStatus.MISSING_PROVIDER,
        request=request,
        provider_id=provider_id,
        provider_version="unavailable",
        provider_fingerprint="unavailable",
        capability_id=request.capability_id,
        v3_fingerprint="unavailable",
        command=(),
        duration_ms=0,
        output_sha256=None,
        error_code="v3_legacy.provider_missing",
        message="requested provider is not registered; fallback is forbidden",
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1_000))


def _base_payload(base: ProjectVersionRef) -> dict[str, Any]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _text(value: object, field: str, *, maximum: int = 1_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")


def _refs(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > 64 or len(value) != len(set(value)):
        raise V3LegacyBoundaryError(
            f"{field} must contain at most 64 unique references"
        )
    for item in value:
        _text(item, f"{field} item", maximum=4_000)


def _detached_object_json(value: object, field: str) -> str:
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a JSON object")
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 4_096 or depth > 12:
            raise V3LegacyBoundaryError(f"{field} exceeds JSON bounds")
        if isinstance(current, dict):
            for key, child in current.items():
                _text(key, f"{field} key", maximum=256)
                normalized = key.strip().lower().replace("-", "_")
                if normalized in _FORBIDDEN_HANDLE_KEYS:
                    raise V3LegacyBoundaryError(
                        f"{field} contains forbidden handle key {key!r}"
                    )
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif current is None or isinstance(current, (bool, int, str)):
            if isinstance(current, str) and len(current) > 32_000:
                raise V3LegacyBoundaryError(
                    f"{field} contains oversized text"
                )
        elif isinstance(current, float) and math.isfinite(current):
            continue
        else:
            raise V3LegacyBoundaryError(
                f"{field} contains a non-JSON value"
            )
    encoded = canonical_json(value, ascii=False)
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise V3LegacyBoundaryError(f"{field} exceeds detached JSON limit")
    return encoded


def _require_canonical_object_json(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be canonical JSON text")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise V3LegacyBoundaryError(f"{field} is malformed") from exc
    if _detached_object_json(decoded, field) != value:
        raise V3LegacyBoundaryError(f"{field} must be canonical JSON")
