from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

from archflow.adapters.model_provider import (
    AsyncJsonCommandModelProvider,
    ModelCommandProtocol,
    ModelProviderSpec,
    create_codex_cli_model_provider,
)
from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.production import ProviderIdentity, activate_model_provider
from archflow.runtime.design_controller import prepare_design_turn
from archflow.runtime.primary_architect import (
    PrimaryArchitectReceipt,
    PrimaryArchitectStatus,
    run_primary_architect_turn,
)
from archflow.state.decision_operator import DecisionOperator
from archflow.state.operational_state import (
    StateDomain,
    StateFact,
)
from tests.test_design_controller import (
    _checkpoint,
    _experts,
    _turn_subject_inventory,
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _authorize(provider):  # type: ignore[no-untyped-def]
    return activate_model_provider(
        provider,
        identity=ProviderIdentity(
            provider_id="fake-model-provider",
            version="test-1",
            fingerprint=_hash("fake-provider"),
        ),
        responsibility_id="model.primary-architect-test",
        contract_owner_id="tests.primary-architect",
        verification_evidence_refs=("evidence://primary-architect-test",),
    )


def _request(
    *,
    phase: ModelPhase = ModelPhase.CAPABILITY_SELECTION,
    payload: dict[str, object] | None = None,
) -> ModelInvocationRequest:
    return ModelInvocationRequest.create(
        request_id=f"request-{phase.value}",
        phase=phase,
        checkpoint_digest=_hash("checkpoint"),
        context_digest=_hash("context"),
        payload=payload or {"schema": "TestPrompt@1"},
    )


def _success(
    request: ModelInvocationRequest,
    output: dict[str, object],
) -> ModelInvocationReceipt:
    output_json = json.dumps(
        output,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ModelInvocationReceipt(
        receipt_id=f"model-{request.request_id}",
        status=ModelInvocationStatus.SUCCESS,
        request=request,
        provider_id="fake-model-provider",
        model_id="fake-reasoner",
        provider_version="test-1",
        provider_fingerprint=_hash("fake-provider"),
        input_bytes=len(
            json.dumps(request.to_dict()).encode("utf-8")
        ),
        output_bytes=len(output_json.encode("utf-8")),
        output_sha256=hashlib.sha256(
            output_json.encode("utf-8")
        ).hexdigest(),
        output_json=output_json,
        input_tokens=100,
        output_tokens=40,
    )


def _failure(
    request: ModelInvocationRequest,
    status: ModelInvocationStatus,
    error_code: str,
) -> ModelInvocationReceipt:
    return ModelInvocationReceipt(
        receipt_id=f"model-{request.request_id}-failure",
        status=status,
        request=request,
        provider_id="fake-model-provider",
        model_id="fake-reasoner",
        provider_version="test-1",
        provider_fingerprint=_hash("fake-provider"),
        input_bytes=100,
        output_bytes=0,
        output_sha256=None,
        error_code=error_code,
        message="bounded fake provider failure",
    )


def _command(script: str) -> tuple[str, ...]:
    return (sys.executable, "-c", script)


class ModelProviderAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_command_success_is_bounded_and_reloadable(
        self,
    ) -> None:
        script = (
            "import json,sys;"
            "r=json.load(sys.stdin);"
            "print(json.dumps({'schema':'ArchFlowModelOutput@1',"
            "'request_id':r['request_id'],'phase':r['phase'],"
            "'output':{'schema':'TestSelection@1'},"
            "'usage':{'input_tokens':11,'output_tokens':7}}))"
        )
        provider = AsyncJsonCommandModelProvider(
            ModelProviderSpec(
                provider_id="json-command-test",
                model_id="test-model",
                version="1",
                command=_command(script),
            )
        )

        receipt = await provider.invoke(_request())

        self.assertIs(
            receipt.status,
            ModelInvocationStatus.SUCCESS,
        )
        self.assertEqual(
            receipt.output,
            {"schema": "TestSelection@1"},
        )
        self.assertEqual(
            ModelInvocationReceipt.from_dict(receipt.to_dict()),
            receipt,
        )
        self.assertEqual("ModelInvocationReceipt@2", receipt.to_dict()["schema"])
        self.assertGreaterEqual(receipt.duration_ms, 0)

        legacy = receipt.to_dict()
        legacy["schema"] = ModelInvocationReceipt.LEGACY_SCHEMA
        legacy.pop("duration_ms")
        reloaded_legacy = ModelInvocationReceipt.from_dict(legacy)
        self.assertEqual(0, reloaded_legacy.duration_ms)
        self.assertEqual(receipt.request, reloaded_legacy.request)

    async def test_codex_jsonl_bridge_uses_trusted_usage_and_empty_cwd(
        self,
    ) -> None:
        script = (
            "import json,os,sys;"
            "r=json.load(sys.stdin);"
            "p={'schema':'ArchFlowAgentCliProposal@1',"
            "'request_id':r['request_id'],'phase':r['phase'],"
            "'output':{'schema':'TestSelection@1',"
            "'working_directory':os.getcwd()}};"
            "print(json.dumps({'type':'item.completed','item':"
            "{'type':'agent_message','text':json.dumps(p)}}));"
            "print(json.dumps({'type':'turn.completed','usage':"
            "{'input_tokens':19,'cached_input_tokens':3,"
            "'output_tokens':5,'reasoning_output_tokens':1}}))"
        )
        provider = AsyncJsonCommandModelProvider(
            ModelProviderSpec(
                provider_id="codex-jsonl-test",
                model_id="test-model",
                version="1",
                command=_command(script),
                command_protocol=(
                    ModelCommandProtocol.CODEX_EXEC_JSONL
                ),
                isolated_working_directory=True,
            )
        )

        receipt = await provider.invoke(_request())

        self.assertIs(receipt.status, ModelInvocationStatus.SUCCESS)
        self.assertEqual(receipt.input_tokens, 19)
        self.assertEqual(receipt.output_tokens, 5)
        working_directory = Path(receipt.output["working_directory"])
        self.assertNotEqual(working_directory, Path.cwd())
        self.assertFalse(working_directory.exists())

    def test_codex_factory_is_read_only_ephemeral_and_api_neutral(
        self,
    ) -> None:
        provider = create_codex_cli_model_provider(
            executable="codex-test",
            model_id="test-model",
            version="test-version",
            reasoning_effort="low",
        )

        self.assertIsInstance(provider, AsyncJsonCommandModelProvider)
        self.assertIs(
            provider.spec.command_protocol,
            ModelCommandProtocol.CODEX_EXEC_JSONL,
        )
        self.assertTrue(provider.spec.isolated_working_directory)
        command = provider.spec.command
        self.assertEqual(command[:3], ("codex-test", "exec", "--json"))
        self.assertIn("--ephemeral", command)
        self.assertIn("read-only", command)
        self.assertNotIn("workspace-write", command)
        self.assertIn('model_reasoning_effort="low"', command)

        with self.assertRaisesRegex(ValueError, "reasoning_effort"):
            create_codex_cli_model_provider(
                executable="codex-test",
                model_id="test-model",
                version="test-version",
                reasoning_effort="unbounded",
            )

    def test_provider_fingerprint_binds_execution_budgets(self) -> None:
        baseline = ModelProviderSpec(
            provider_id="fingerprint-test",
            model_id="test-model",
            version="1",
            command=_command("print('{}')"),
        )
        variants = (
            ModelProviderSpec(
                provider_id=baseline.provider_id,
                model_id=baseline.model_id,
                version=baseline.version,
                command=baseline.command,
                timeout_seconds=baseline.timeout_seconds + 1,
            ),
            ModelProviderSpec(
                provider_id=baseline.provider_id,
                model_id=baseline.model_id,
                version=baseline.version,
                command=baseline.command,
                max_output_tokens=baseline.max_output_tokens + 1,
            ),
        )
        for variant in variants:
            self.assertNotEqual(baseline.fingerprint, variant.fingerprint)

    async def test_timeout_malformed_exit_and_token_budget_are_typed(
        self,
    ) -> None:
        cases = (
            (
                "timeout",
                _command("import time;time.sleep(1)"),
                0.01,
                100,
                ModelInvocationStatus.TIMEOUT,
            ),
            (
                "malformed",
                _command("print('{')"),
                1,
                100,
                ModelInvocationStatus.MALFORMED,
            ),
            (
                "exit",
                _command("import sys;sys.exit(7)"),
                1,
                100,
                ModelInvocationStatus.EXIT_ERROR,
            ),
            (
                "tokens",
                _command(
                    "import json,sys;r=json.load(sys.stdin);"
                    "print(json.dumps({'schema':'ArchFlowModelOutput@1',"
                    "'request_id':r['request_id'],'phase':r['phase'],"
                    "'output':{'schema':'Test@1'},"
                    "'usage':{'input_tokens':1,'output_tokens':101}}))"
                ),
                1,
                100,
                ModelInvocationStatus.BUDGET_EXHAUSTED,
            ),
        )
        for name, command, timeout, token_limit, expected in cases:
            with self.subTest(name=name):
                provider = AsyncJsonCommandModelProvider(
                    ModelProviderSpec(
                        provider_id=f"provider-{name}",
                        model_id="test-model",
                        version="1",
                        command=command,
                        timeout_seconds=timeout,
                        max_output_tokens=token_limit,
                    )
                )
                receipt = await provider.invoke(_request())
                self.assertIs(receipt.status, expected)
                self.assertIsNotNone(receipt.error_code)
                self.assertIsNone(receipt.output)
                self.assertGreaterEqual(receipt.duration_ms, 0)
                if name == "timeout":
                    self.assertGreater(receipt.duration_ms, 0)

    async def test_input_budget_stops_before_missing_command(
        self,
    ) -> None:
        provider = AsyncJsonCommandModelProvider(
            ModelProviderSpec(
                provider_id="input-budget-test",
                model_id="test-model",
                version="1",
                command=("definitely-missing-model-command",),
                max_input_bytes=1_024,
            )
        )
        request = _request(payload={"content": "x" * 2_000})

        receipt = await provider.invoke(request)

        self.assertIs(
            receipt.status,
            ModelInvocationStatus.BUDGET_EXHAUSTED,
        )
        self.assertEqual(
            receipt.error_code,
            "model.input_budget_exhausted",
        )

    def test_request_rejects_writer_history_and_world_handles(
        self,
    ) -> None:
        for field in (
            "raw_history",
            "transcript",
            "canonical_writer",
            "workspace_path",
            "world_handle",
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    ValueError,
                    "forbidden fields",
                ):
                    _request(payload={field: "must-not-cross"})


class ScriptedArchitectProvider:
    def __init__(
        self,
        operator: DecisionOperator,
        *,
        fail_selection: bool = False,
        select_unknown: bool = False,
    ) -> None:
        self.operator = operator
        self.fail_selection = fail_selection
        self.select_unknown = select_unknown
        self.requests: list[ModelInvocationRequest] = []

    async def invoke(
        self,
        request: ModelInvocationRequest,
    ) -> ModelInvocationReceipt:
        self.requests.append(request)
        if (
            request.phase is ModelPhase.CAPABILITY_SELECTION
            and self.fail_selection
        ):
            return _failure(
                request,
                ModelInvocationStatus.TIMEOUT,
                "model.timeout",
            )
        if request.phase is ModelPhase.CAPABILITY_SELECTION:
            selected = (
                ["invented-expert"]
                if self.select_unknown
                else ["expert-structure-a"]
            )
            return _success(
                request,
                {
                    "schema": (
                        "PrimaryArchitectCapabilitySelection@1"
                    ),
                    "selected_capability_ids": selected,
                },
            )
        receipts = request.payload["expert_receipts"]
        advice_refs = [
            item["receipt_ref"]
            for item in receipts
            if item["status"] == "advice"
        ]
        return _success(
            request,
            {
                "schema": "PrimaryArchitectActionProposal@1",
                "action_id": "model-action-001",
                "operator": self.operator.to_dict(),
                "responds_to_refs": [
                    "commitment:preserve-public-purpose",
                    "obligation:resolve-grid",
                ],
                "adopted_advice_refs": advice_refs,
                "rejected_advice_refs": [],
                "tradeoff_rationale": (
                    "Adopt the bounded structural advice while "
                    "preserving the governing public purpose."
                ),
            },
        )


class PrimaryArchitectRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.checkpoint, _ = _checkpoint()
        self.registry, self.metadata = _experts()
        self.prepared = prepare_design_turn(
            self.checkpoint,
            self.registry,
            phase_metadata=self.metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(
                self.checkpoint
            ),
        )
        target = self.checkpoint.tree.node(
            self.checkpoint.target_node_ref
        )
        self.operator = DecisionOperator(
            decision_id="model-grid-proposal",
            decision_type="parameter-derivation",
            base_state_digest=target.operational_state.state_digest,
            authority_id="structure-agent",
            intent="Propose an evidence-backed grid relation.",
            add_facts=(
                StateFact(
                    domain=StateDomain.PARAMETER,
                    key="model-grid-relation",
                    value={"status": "proposed"},
                    source_ref="evidence://model/grid-relation",
                ),
            ),
            evidence_refs=("evidence://model/grid-relation",),
        )

    async def test_dynamic_selection_and_exact_base_proposal_transition(
        self,
    ) -> None:
        provider = ScriptedArchitectProvider(self.operator)

        result = await run_primary_architect_turn(
            self.checkpoint,
            self.prepared,
            self.registry,
            _authorize(provider),
            history_event_ref="design-event:model-turn",
        )

        self.assertIs(
            result.receipt.status,
            PrimaryArchitectStatus.TRANSITIONED,
        )
        self.assertIsNotNone(result.controller_result)
        self.assertEqual(len(provider.requests), 2)
        selection_payload = provider.requests[0].payload
        self.assertEqual(
            set(selection_payload["available_capability_ids"]),
            set(self.prepared.discovered_expert_ids),
        )
        self.assertNotIn("raw_history", selection_payload)
        self.assertNotIn("canonical_writer", selection_payload)
        action_payload = provider.requests[1].payload
        self.assertEqual(
            action_payload["exact_base_state_digest"],
            self.operator.base_state_digest,
        )
        self.assertEqual(
            PrimaryArchitectReceipt.from_dict(
                result.receipt.to_dict()
            ),
            result.receipt,
        )

    async def test_provider_failure_preserves_checkpoint_and_reloads(
        self,
    ) -> None:
        provider = ScriptedArchitectProvider(
            self.operator,
            fail_selection=True,
        )

        result = await run_primary_architect_turn(
            self.checkpoint,
            self.prepared,
            self.registry,
            _authorize(provider),
            history_event_ref="design-event:model-timeout",
        )

        self.assertIs(
            result.receipt.status,
            PrimaryArchitectStatus.PROVIDER_FAILED,
        )
        self.assertIsNone(result.controller_result)
        self.assertIsNone(result.receipt.next_checkpoint_digest)
        self.assertEqual(
            PrimaryArchitectReceipt.from_dict(
                result.receipt.to_dict()
            ),
            result.receipt,
        )

    async def test_unknown_capability_is_rejected_without_action_call(
        self,
    ) -> None:
        provider = ScriptedArchitectProvider(
            self.operator,
            select_unknown=True,
        )

        result = await run_primary_architect_turn(
            self.checkpoint,
            self.prepared,
            self.registry,
            _authorize(provider),
            history_event_ref="design-event:model-unknown-capability",
        )

        self.assertIs(
            result.receipt.status,
            PrimaryArchitectStatus.PROPOSAL_REJECTED,
        )
        self.assertEqual(len(provider.requests), 1)
        self.assertIsNone(result.controller_result)

    async def test_repeated_model_plan_returns_reloadable_stop(
        self,
    ) -> None:
        first = await run_primary_architect_turn(
            self.checkpoint,
            self.prepared,
            self.registry,
            _authorize(ScriptedArchitectProvider(self.operator)),
            history_event_ref="design-event:model-first-plan",
        )
        self.assertIsNotNone(first.controller_result)
        next_checkpoint = first.controller_result.checkpoint
        prepared_again = prepare_design_turn(
            next_checkpoint,
            self.registry,
            phase_metadata=self.metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(
                next_checkpoint
            ),
        )
        next_target = next_checkpoint.tree.node(
            next_checkpoint.target_node_ref
        )
        repeated_operator = DecisionOperator(
            decision_id="model-grid-proposal-repeated",
            decision_type=self.operator.decision_type,
            base_state_digest=(
                next_target.operational_state.state_digest
            ),
            authority_id=self.operator.authority_id,
            intent=self.operator.intent,
            add_facts=self.operator.add_facts,
            evidence_refs=self.operator.evidence_refs,
        )

        repeated = await run_primary_architect_turn(
            next_checkpoint,
            prepared_again,
            self.registry,
            _authorize(ScriptedArchitectProvider(repeated_operator)),
            history_event_ref="design-event:model-repeated-plan",
        )

        self.assertIs(
            repeated.receipt.status,
            PrimaryArchitectStatus.CONTROLLER_STOPPED,
        )
        self.assertEqual(
            repeated.receipt.controller_outcome,
            "stopped_repeated_action",
        )
        self.assertEqual(
            PrimaryArchitectReceipt.from_dict(
                repeated.receipt.to_dict()
            ),
            repeated.receipt,
        )

    def test_prompts_contain_no_building_instance_defaults(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = "\n".join(
            (
                (root / "archflow/adapters/model_provider.py").read_text(
                    encoding="utf-8"
                ),
                (root / "archflow/runtime/primary_architect.py").read_text(
                    encoding="utf-8"
                ),
            )
        ).lower()
        for forbidden in (
            "pantheon",
            "rotunda",
            "room_width",
            "block_palette",
            "fixed_footprint",
        ):
            self.assertNotIn(forbidden, source)
