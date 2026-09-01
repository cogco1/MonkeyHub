"""Bounded asynchronous JSON-command model provider.

The adapter sends one detached request to one explicitly configured command.
It has no fallback, project writer, workspace handle, or world-mutation
authority. The configured command is responsible for adapting a real model
surface to ``ArchFlowModelOutput@1``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.ports.model import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.project.refs import require_identifier


class ModelCommandProtocol(StrEnum):
    JSON_ENVELOPE = "json_envelope"
    CODEX_EXEC_JSONL = "codex_exec_jsonl"


@dataclass(frozen=True, slots=True)
class ModelProviderSpec:
    provider_id: str
    model_id: str
    version: str
    command: tuple[str, ...]
    timeout_seconds: float = 45.0
    max_input_bytes: int = 256_000
    max_output_bytes: int = 128_000
    max_output_tokens: int = 8_192
    command_protocol: ModelCommandProtocol = (
        ModelCommandProtocol.JSON_ENVELOPE
    )
    isolated_working_directory: bool = False

    def __post_init__(self) -> None:
        require_identifier(self.provider_id, "provider_id")
        _text(self.model_id, "model_id", maximum=500)
        _text(self.version, "version", maximum=500)
        if not isinstance(self.command, tuple) or not self.command:
            raise ValueError("command must be a non-empty tuple")
        for item in self.command:
            _text(item, "command item", maximum=8_000)
        if not isinstance(self.command_protocol, ModelCommandProtocol):
            raise TypeError(
                "command_protocol must be ModelCommandProtocol"
            )
        if type(self.isolated_working_directory) is not bool:
            raise TypeError(
                "isolated_working_directory must be bool"
            )
        if not isinstance(self.timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be numeric")
        if not 0.01 <= self.timeout_seconds <= 300:
            raise ValueError(
                "timeout_seconds must be between 0.01 and 300"
            )
        for value, field, minimum, maximum in (
            (
                self.max_input_bytes,
                "max_input_bytes",
                1_024,
                2_000_000,
            ),
            (
                self.max_output_bytes,
                "max_output_bytes",
                256,
                2_000_000,
            ),
            (
                self.max_output_tokens,
                "max_output_tokens",
                1,
                200_000,
            ),
        ):
            if (
                type(value) is not int
                or not minimum <= value <= maximum
            ):
                raise ValueError(
                    f"{field} must be between {minimum} and {maximum}"
                )

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "version": self.version,
                "command": list(self.command),
                "command_protocol": self.command_protocol.value,
                "isolated_working_directory": (
                    self.isolated_working_directory
                ),
                "timeout_seconds": float(self.timeout_seconds),
                "max_input_bytes": self.max_input_bytes,
                "max_output_bytes": self.max_output_bytes,
                "max_output_tokens": self.max_output_tokens,
            }
        )


class AsyncJsonCommandModelProvider:
    """Invoke one explicit JSON model command without shell or fallback."""

    def __init__(self, spec: ModelProviderSpec) -> None:
        if not isinstance(spec, ModelProviderSpec):
            raise TypeError("spec must be ModelProviderSpec")
        self.spec = spec

    async def invoke(
        self,
        request: ModelInvocationRequest,
    ) -> ModelInvocationReceipt:
        if not isinstance(request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        started = time.monotonic()
        input_data = (_canonical_json(request.to_dict()) + "\n").encode(
            "utf-8"
        )
        if len(input_data) > self.spec.max_input_bytes:
            return self._failure(
                request,
                ModelInvocationStatus.BUDGET_EXHAUSTED,
                "model.input_budget_exhausted",
                f"input exceeds {self.spec.max_input_bytes} bytes",
                input_bytes=len(input_data),
                duration_ms=_duration_ms(started),
            )
        if self.spec.isolated_working_directory:
            with tempfile.TemporaryDirectory(
                prefix="archflow-model-"
            ) as working_directory:
                return await self._invoke_command(
                    request,
                    input_data,
                    working_directory,
                    started,
                )
        return await self._invoke_command(request, input_data, None, started)

    async def _invoke_command(
        self,
        request: ModelInvocationRequest,
        input_data: bytes,
        working_directory: str | None,
        started: float,
    ) -> ModelInvocationReceipt:
        creation_flags = (
            getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *self.spec.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags,
                cwd=working_directory,
            )
        except FileNotFoundError:
            return self._failure(
                request,
                ModelInvocationStatus.OFFLINE,
                "model.provider_unavailable",
                "configured model provider executable was not found",
                input_bytes=len(input_data),
                duration_ms=_duration_ms(started),
            )
        except OSError as exc:
            return self._failure(
                request,
                ModelInvocationStatus.OFFLINE,
                "model.provider_os_error",
                f"{type(exc).__name__}: {exc}",
                input_bytes=len(input_data),
                duration_ms=_duration_ms(started),
            )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_data),
                timeout=self.spec.timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return self._failure(
                request,
                ModelInvocationStatus.TIMEOUT,
                "model.timeout",
                f"provider exceeded {self.spec.timeout_seconds:g} seconds",
                input_bytes=len(input_data),
                duration_ms=_duration_ms(started),
            )
        output_digest = hashlib.sha256(stdout).hexdigest()
        if len(stdout) > self.spec.max_output_bytes:
            return self._failure(
                request,
                ModelInvocationStatus.OVERSIZED,
                "model.output_oversized",
                f"output exceeds {self.spec.max_output_bytes} bytes",
                input_bytes=len(input_data),
                output_bytes=len(stdout),
                output_sha256=output_digest,
                duration_ms=_duration_ms(started),
            )
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")[:500]
            return self._failure(
                request,
                ModelInvocationStatus.EXIT_ERROR,
                "model.provider_exit",
                f"provider exited {process.returncode}: {error}",
                input_bytes=len(input_data),
                output_bytes=len(stdout),
                output_sha256=output_digest,
                duration_ms=_duration_ms(started),
            )
        try:
            decoded_output = stdout.decode("utf-8")
            if (
                self.spec.command_protocol
                is ModelCommandProtocol.JSON_ENVELOPE
            ):
                envelope = _decode_object(
                    decoded_output,
                    "provider output",
                )
            else:
                envelope = _decode_codex_exec_jsonl(
                    decoded_output,
                    request,
                )
            output, input_tokens, output_tokens = self._parse_output(
                request,
                envelope,
            )
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            return self._failure(
                request,
                ModelInvocationStatus.MALFORMED,
                "model.output_malformed",
                f"{type(exc).__name__}: {exc}",
                input_bytes=len(input_data),
                output_bytes=len(stdout),
                output_sha256=output_digest,
                duration_ms=_duration_ms(started),
            )
        if output_tokens > self.spec.max_output_tokens:
            return self._failure(
                request,
                ModelInvocationStatus.BUDGET_EXHAUSTED,
                "model.output_token_budget_exhausted",
                (
                    f"provider reported {output_tokens} output tokens; "
                    f"limit is {self.spec.max_output_tokens}"
                ),
                input_bytes=len(input_data),
                output_bytes=len(stdout),
                output_sha256=output_digest,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=_duration_ms(started),
            )
        return self._success(
            request,
            output,
            input_bytes=len(input_data),
            output_bytes=len(stdout),
            output_sha256=output_digest,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=_duration_ms(started),
        )

    def _parse_output(
        self,
        request: ModelInvocationRequest,
        envelope: dict[str, object],
    ) -> tuple[dict[str, object], int, int]:
        if set(envelope) != {
            "schema",
            "request_id",
            "phase",
            "output",
            "usage",
        }:
            raise ValueError("provider output fields drifted")
        if envelope["schema"] != "ArchFlowModelOutput@1":
            raise ValueError("provider output schema is unsupported")
        if (
            envelope["request_id"] != request.request_id
            or envelope["phase"] != request.phase.value
        ):
            raise ValueError("provider output is bound to another request")
        output = _mapping(envelope["output"], "provider output body")
        usage = _mapping(envelope["usage"], "provider usage")
        if set(usage) != {"input_tokens", "output_tokens"}:
            raise ValueError("provider usage fields drifted")
        input_tokens = usage["input_tokens"]
        output_tokens = usage["output_tokens"]
        if any(
            type(value) is not int or value < 0
            for value in (input_tokens, output_tokens)
        ):
            raise ValueError("provider token usage must be non-negative")
        return output, input_tokens, output_tokens

    def _success(
        self,
        request: ModelInvocationRequest,
        output: dict[str, object],
        **metrics: Any,
    ) -> ModelInvocationReceipt:
        identity = self._identity(
            request,
            ModelInvocationStatus.SUCCESS,
            metrics["output_sha256"],
            None,
            metrics["duration_ms"],
        )
        return ModelInvocationReceipt(
            receipt_id=f"model-{_digest(identity)[:24]}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id=self.spec.provider_id,
            model_id=self.spec.model_id,
            provider_version=self.spec.version,
            provider_fingerprint=self.spec.fingerprint,
            output_json=_canonical_json(output),
            error_code=None,
            message=None,
            **metrics,
        )

    def _failure(
        self,
        request: ModelInvocationRequest,
        status: ModelInvocationStatus,
        error_code: str,
        message: str,
        *,
        input_bytes: int,
        output_bytes: int = 0,
        output_sha256: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_ms: int = 0,
    ) -> ModelInvocationReceipt:
        identity = self._identity(
            request,
            status,
            output_sha256,
            error_code,
            duration_ms,
        )
        return ModelInvocationReceipt(
            receipt_id=f"model-{_digest(identity)[:24]}",
            status=status,
            request=request,
            provider_id=self.spec.provider_id,
            model_id=self.spec.model_id,
            provider_version=self.spec.version,
            provider_fingerprint=self.spec.fingerprint,
            input_bytes=input_bytes,
            output_bytes=output_bytes,
            output_sha256=output_sha256,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_code=error_code,
            message=message[:1_000],
        )

    def _identity(
        self,
        request: ModelInvocationRequest,
        status: ModelInvocationStatus,
        output_sha256: str | None,
        error_code: str | None,
        duration_ms: int,
    ) -> dict[str, object]:
        return {
            "request": request.to_dict(),
            "provider_fingerprint": self.spec.fingerprint,
            "status": status.value,
            "output_sha256": output_sha256,
            "error_code": error_code,
            "duration_ms": duration_ms,
        }


_CODEX_AGENT_PROMPT = (
    "Act only as a detached ArchFlow proposal model. Read the single "
    "ModelInvocationRequest@1 JSON object supplied on stdin. Do not inspect "
    "files, run commands, call tools, mutate state, or infer authority not "
    "present in that request. Return only one JSON object with exactly these "
    "fields: schema='ArchFlowAgentCliProposal@1', request_id copied from the "
    "request, phase copied from the request, and output containing the "
    "requested proposal schema. Do not add markdown or usage fields. The "
    "bridge binds trusted token usage from Codex CLI events."
)


def create_codex_cli_model_provider(
    *,
    executable: str,
    model_id: str,
    version: str,
    provider_id: str = "codex-agent-cli",
    timeout_seconds: float = 60.0,
    max_input_bytes: int = 256_000,
    max_output_bytes: int = 128_000,
    max_output_tokens: int = 8_192,
    reasoning_effort: str = "medium",
) -> AsyncJsonCommandModelProvider:
    """Create a detached Codex CLI adapter behind AsyncModelProvider."""

    _text(executable, "executable", maximum=8_000)
    if reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
        raise ValueError("reasoning_effort is unsupported")
    return AsyncJsonCommandModelProvider(
        ModelProviderSpec(
            provider_id=provider_id,
            model_id=model_id,
            version=version,
            command=(
                executable,
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--model",
                model_id,
                "-c",
                f'model_reasoning_effort="{reasoning_effort}"',
                _CODEX_AGENT_PROMPT,
            ),
            timeout_seconds=timeout_seconds,
            max_input_bytes=max_input_bytes,
            max_output_bytes=max_output_bytes,
            max_output_tokens=max_output_tokens,
            command_protocol=ModelCommandProtocol.CODEX_EXEC_JSONL,
            isolated_working_directory=True,
        )
    )


def _decode_codex_exec_jsonl(
    value: str,
    request: ModelInvocationRequest,
) -> dict[str, object]:
    final_message: dict[str, object] | None = None
    trusted_usage: dict[str, Any] | None = None
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = _decode_object(
                line,
                f"Codex JSONL event {line_number}",
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Codex JSONL event {line_number} is malformed"
            ) from exc
        event_type = event.get("type")
        if event_type == "item.completed":
            item = _mapping(event.get("item"), "Codex completed item")
            if item.get("type") == "agent_message":
                message = item.get("text")
                if not isinstance(message, str) or not message:
                    raise ValueError(
                        "Codex agent message text is missing"
                    )
                final_message = _decode_object(
                    message,
                    "Codex final agent message",
                )
        elif event_type == "turn.completed":
            trusted_usage = _mapping(
                event.get("usage"),
                "Codex turn usage",
            )
    if final_message is None:
        raise ValueError("Codex JSONL has no final agent message")
    if trusted_usage is None:
        raise ValueError("Codex JSONL has no completed-turn usage")
    if set(final_message) != {
        "schema",
        "request_id",
        "phase",
        "output",
    }:
        raise ValueError("Codex proposal fields drifted")
    if final_message["schema"] != "ArchFlowAgentCliProposal@1":
        raise ValueError("Codex proposal schema is unsupported")
    input_tokens = trusted_usage.get("input_tokens")
    output_tokens = trusted_usage.get("output_tokens")
    if any(
        type(token_count) is not int or token_count < 0
        for token_count in (input_tokens, output_tokens)
    ):
        raise ValueError("Codex token usage is missing or invalid")
    return {
        "schema": "ArchFlowModelOutput@1",
        "request_id": final_message["request_id"],
        "phase": final_message["phase"],
        "output": _mapping(
            final_message["output"],
            "Codex proposal output",
        ),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1_000))


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


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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
    "AsyncJsonCommandModelProvider",
    "AsyncModelProvider",
    "ModelCommandProtocol",
    "ModelInvocationReceipt",
    "ModelInvocationRequest",
    "ModelInvocationStatus",
    "ModelPhase",
    "ModelProviderSpec",
    "create_codex_cli_model_provider",
]
