from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.adapters.model_provider import (
    AsyncJsonCommandModelProvider,
    ModelCommandProtocol,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
    ModelProviderSpec,
    create_codex_cli_model_provider,
)
from archflow.production import (
    InvocationEvidenceCollector,
    activate_provider_from_spec,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectAlreadyExists,
)
from archflow.project.production_transition import (
    load_production_transition,
    persist_failed_production_attempt,
    production_intent_digest,
)


class PrimaryArchitectLiveSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_opt_in_real_model_provider(self) -> None:
        if os.environ.get("ARCHFLOW_MODEL_SMOKE_ALLOW_LIVE") != "1":
            self.skipTest(
                "set ARCHFLOW_MODEL_SMOKE_ALLOW_LIVE=1 for one deliberate "
                "persisted live invocation"
            )
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
        reasoning_effort = os.environ.get(
            "ARCHFLOW_MODEL_SMOKE_REASONING_EFFORT",
            "low",
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
                reasoning_effort=reasoning_effort,
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
        repository = None
        run = None
        request_ref = None
        project_root = os.environ.get("ARCHFLOW_MODEL_SMOKE_PROJECT_ROOT")
        project_id = os.environ.get(
            "ARCHFLOW_MODEL_SMOKE_PROJECT_ID",
            "p059-provider-runtime",
        )
        run_id = os.environ.get(
            "ARCHFLOW_MODEL_SMOKE_RUN_ID",
            "live-agent-cli-001",
        )
        if project_root is None:
            self.fail(
                "ARCHFLOW_MODEL_SMOKE_PROJECT_ROOT is required before live invocation"
            )
        else:
            root = Path(project_root)
            if not root.is_absolute():
                self.fail("ARCHFLOW_MODEL_SMOKE_PROJECT_ROOT must be absolute")
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id=project_id,
                initial_state={
                    "schema": "CanonicalProjectState@1",
                    "phase": "provider-conformance",
                    "authoritative_record_refs": [],
                    "derived_record_refs": [],
                },
            )
            run = repository.create_run(run_id)
            request_ref = repository.put_json(
                run=run,
                destination=PersistenceDestination(PersistenceArea.INPUT),
                record_kind="provider-live-request",
                payload={
                    "schema": "ProviderLiveDiagnosticRequest@1",
                    "model_request": request.to_dict(),
                },
            )

        collector = InvocationEvidenceCollector()
        authorized = activate_provider_from_spec(
            provider,
            spec=provider.spec,
            responsibility_id="model.provider-live-diagnostic",
            contract_owner_id="archflow.provider-runtime",
            verification_evidence_refs=(
                "evidence://provider-live-diagnostic"
                if request_ref is None
                else request_ref.uri,
            ),
            envelope_observer=collector.observe,
        )

        receipt = await authorized.invoke(request)
        self.assertEqual(1, len(collector.since(0)))

        if repository is not None and run is not None and request_ref is not None:
            destination = PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            )
            if receipt.status is ModelInvocationStatus.SUCCESS:
                repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind="provider-live-invocation",
                    payload={
                        "schema": "ProviderLiveInvocationEvidence@1",
                        "project_id": run.project_id,
                        "run_id": run.run_id,
                        "request_ref": request_ref.uri,
                        "provider_envelope": collector.since(0)[0].to_dict(),
                        "result_status": receipt.status.value,
                        "duration_ms": receipt.duration_ms,
                        "transition_checkpoint_ref": None,
                        "lifecycle_successor": False,
                        "api_service_execution_claimed": False,
                        "building_execution_claimed": False,
                        "canonical_write_authority": False,
                    },
                )
            else:
                intent_digest = production_intent_digest(
                    run,
                    intent={
                        "schema": "ProviderLiveDiagnosticIntent@1",
                        "step_id": "provider-conformance",
                        "request_ref": request_ref.uri,
                    },
                )
                persist_failed_production_attempt(
                    repository,
                    run=run,
                    intent_digest=intent_digest,
                    step_id="provider-conformance",
                    error_code=(
                        receipt.error_code or "model.provider_failed"
                    ),
                    message=(
                        receipt.message or "live provider did not succeed"
                    ),
                    invocation_envelopes=collector.since(0),
                )

        self.assertIs(
            receipt.status,
            ModelInvocationStatus.SUCCESS,
            receipt.to_dict(),
        )
        self.assertGreater(receipt.output_tokens or 0, 0)
        self.assertIsNotNone(receipt.output)


class PrimaryArchitectSmokeHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_provider_persists_once_before_replay_is_blocked(
        self,
    ) -> None:
        script = (
            "import json,sys;"
            "r=json.load(sys.stdin);"
            "open(sys.argv[1],'a',encoding='utf-8').write('invoked\\n');"
            "print(json.dumps({'schema':'ArchFlowModelOutput@1',"
            "'request_id':r['request_id'],'phase':r['phase'],"
            "'output':{'schema':'PrimaryArchitectCapabilitySelection@1',"
            "'selected_capability_ids':[]},"
            "'usage':{'input_tokens':7,'output_tokens':3}}))"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "p059-provider-runtime"
            marker = Path(temporary) / "invocations.txt"
            environment = {
                "ARCHFLOW_MODEL_SMOKE_ALLOW_LIVE": "1",
                "ARCHFLOW_MODEL_SMOKE_COMMAND_JSON": json.dumps(
                    [sys.executable, "-c", script, str(marker)]
                ),
                "ARCHFLOW_MODEL_SMOKE_PROVIDER_ID": "local-contract-provider",
                "ARCHFLOW_MODEL_SMOKE_MODEL_ID": "local-contract-model",
                "ARCHFLOW_MODEL_SMOKE_VERSION": "local-contract-1",
                "ARCHFLOW_MODEL_SMOKE_TIMEOUT_SECONDS": "5",
                "ARCHFLOW_MODEL_SMOKE_MAX_OUTPUT_TOKENS": "32",
                "ARCHFLOW_MODEL_SMOKE_PROTOCOL": (
                    ModelCommandProtocol.JSON_ENVELOPE.value
                ),
                "ARCHFLOW_MODEL_SMOKE_PROJECT_ROOT": str(root),
            }
            case = PrimaryArchitectLiveSmoke(
                "test_explicit_opt_in_real_model_provider"
            )
            with patch.dict(os.environ, environment):
                await case.test_explicit_opt_in_real_model_provider()

            self.assertEqual("invoked\n", marker.read_text(encoding="utf-8"))
            repository = FilesystemProjectRepository.open(root)
            run = repository.load_run("live-agent-cli-001")
            records = tuple(
                repository.load_json(ref)
                for ref in repository.list_json(
                    run=run,
                    destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=run.run_id,
                    ),
                )
            )
            evidence = next(
                item
                for item in records
                if item.get("schema") == "ProviderLiveInvocationEvidence@1"
            )
            provider_receipt = json.loads(
                evidence["provider_envelope"]["provider_receipt_json"]
            )
            self.assertEqual("ModelInvocationReceipt@2", provider_receipt["schema"])
            self.assertEqual("success", provider_receipt["status"])
            self.assertGreaterEqual(provider_receipt["duration_ms"], 0)
            self.assertEqual(0, repository.read_head().version)

            replay = PrimaryArchitectLiveSmoke(
                "test_explicit_opt_in_real_model_provider"
            )
            with patch.dict(os.environ, environment):
                with self.assertRaises(ProjectAlreadyExists):
                    await replay.test_explicit_opt_in_real_model_provider()
            self.assertEqual("invoked\n", marker.read_text(encoding="utf-8"))

    async def test_local_provider_failure_is_p036_evidence_not_completion(
        self,
    ) -> None:
        script = "import sys;sys.stdout.write('{')"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "p059-provider-runtime"
            environment = {
                "ARCHFLOW_MODEL_SMOKE_ALLOW_LIVE": "1",
                "ARCHFLOW_MODEL_SMOKE_COMMAND_JSON": json.dumps(
                    [sys.executable, "-c", script]
                ),
                "ARCHFLOW_MODEL_SMOKE_PROVIDER_ID": "local-contract-provider",
                "ARCHFLOW_MODEL_SMOKE_MODEL_ID": "local-contract-model",
                "ARCHFLOW_MODEL_SMOKE_VERSION": "local-contract-1",
                "ARCHFLOW_MODEL_SMOKE_TIMEOUT_SECONDS": "5",
                "ARCHFLOW_MODEL_SMOKE_MAX_OUTPUT_TOKENS": "32",
                "ARCHFLOW_MODEL_SMOKE_PROTOCOL": (
                    ModelCommandProtocol.JSON_ENVELOPE.value
                ),
                "ARCHFLOW_MODEL_SMOKE_PROJECT_ROOT": str(root),
            }
            case = PrimaryArchitectLiveSmoke(
                "test_explicit_opt_in_real_model_provider"
            )
            with patch.dict(os.environ, environment):
                with self.assertRaises(AssertionError):
                    await case.test_explicit_opt_in_real_model_provider()

            repository = FilesystemProjectRepository.open(root)
            run = repository.load_run("live-agent-cli-001")
            records = tuple(
                repository.load_json(ref)
                for ref in repository.list_json(
                    run=run,
                    destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=run.run_id,
                    ),
                )
            )
            failure = next(
                item
                for item in records
                if item.get("schema") == "ProductionFailedAttemptReceipt@1"
            )
            provider_receipt = json.loads(
                failure["invocation_envelopes"][0]["provider_receipt_json"]
            )
            self.assertEqual("malformed", provider_receipt["status"])
            self.assertFalse(failure["lifecycle_successor"])
            self.assertIsNone(failure["transition_checkpoint_ref"])
            self.assertIsNone(
                load_production_transition(
                    repository,
                    run=run,
                    intent_digest=failure["intent_digest"],
                )
            )
            self.assertEqual(0, repository.read_head().version)
