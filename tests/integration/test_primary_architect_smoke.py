from __future__ import annotations

import json
import os
import unittest

from archflow.adapters.model_provider import (
    AsyncJsonCommandModelProvider,
    ModelCommandProtocol,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
    ModelProviderSpec,
    create_codex_cli_model_provider,
)


class PrimaryArchitectLiveSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_opt_in_real_model_provider(self) -> None:
        raw_command = os.environ.get(
            "ARCHFLOW_MODEL_SMOKE_COMMAND_JSON"
        )
        if raw_command is None:
            self.skipTest(
                "set ARCHFLOW_MODEL_SMOKE_COMMAND_JSON to an explicit "
                "real-model JSON command array"
            )
        command = json.loads(raw_command)
        if not isinstance(command, list) or not command or any(
            not isinstance(item, str) or not item
            for item in command
        ):
            self.fail(
                "ARCHFLOW_MODEL_SMOKE_COMMAND_JSON must be a JSON "
                "array of non-empty command strings"
            )
        provider_id = os.environ.get(
            "ARCHFLOW_MODEL_SMOKE_PROVIDER_ID",
            "explicit-live-provider",
        )
        model_id = os.environ.get(
            "ARCHFLOW_MODEL_SMOKE_MODEL_ID",
            "explicit-live-model",
        )
        version = os.environ.get(
            "ARCHFLOW_MODEL_SMOKE_VERSION",
            "live-smoke",
        )
        timeout_seconds = float(
            os.environ.get(
                "ARCHFLOW_MODEL_SMOKE_TIMEOUT_SECONDS",
                "60",
            )
        )
        max_output_tokens = int(
            os.environ.get(
                "ARCHFLOW_MODEL_SMOKE_MAX_OUTPUT_TOKENS",
                "2048",
            )
        )
        protocol = ModelCommandProtocol(
            os.environ.get(
                "ARCHFLOW_MODEL_SMOKE_PROTOCOL",
                ModelCommandProtocol.JSON_ENVELOPE.value,
            )
        )
        if protocol is ModelCommandProtocol.CODEX_EXEC_JSONL:
            if len(command) != 1:
                self.fail(
                    "Codex CLI smoke command must contain only the "
                    "executable path"
                )
            provider = create_codex_cli_model_provider(
                executable=command[0],
                provider_id=provider_id,
                model_id=model_id,
                version=version,
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
        else:
            provider = AsyncJsonCommandModelProvider(
                ModelProviderSpec(
                    provider_id=provider_id,
                    model_id=model_id,
                    version=version,
                    command=tuple(command),
                    timeout_seconds=timeout_seconds,
                    max_output_tokens=max_output_tokens,
                )
            )
        request = ModelInvocationRequest.create(
            request_id="primary-architect-live-smoke",
            phase=ModelPhase.CAPABILITY_SELECTION,
            checkpoint_digest="1" * 64,
            context_digest="2" * 64,
            payload={
                "schema": "PrimaryArchitectLiveSmoke@1",
                "instructions": (
                    "Return an empty capability selection using the "
                    "configured ArchFlow model-output envelope."
                ),
                "available_capability_ids": [],
                "required_output_schema": (
                    "PrimaryArchitectCapabilitySelection@1"
                ),
            },
        )

        receipt = await provider.invoke(request)

        self.assertIs(
            receipt.status,
            ModelInvocationStatus.SUCCESS,
            receipt.to_dict(),
        )
        self.assertGreater(receipt.output_tokens or 0, 0)
        self.assertIsNotNone(receipt.output)
