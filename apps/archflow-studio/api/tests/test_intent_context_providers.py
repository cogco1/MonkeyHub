"""Real provider adapters send bounded context and account for every call.

The external process/SDK boundaries are fakes; request preparation, response
validation, supplements and receipts all use the production implementation.
"""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import archflow_studio_api  # noqa: F401
from archflow.ports.model import ModelInvocationStatus
from archflow_studio_api.application import intent_agent
from archflow_studio_api.application.intent_agent import AnthropicCompiler, CodexCompiler, IntentAgentFailed, Selection
from archflow_studio_api.application.projection import _elements

from .test_intent_context import fixture


PROVIDERS = ("codex", "anthropic")
SCALAR_MESSAGE = "set this wall height to 3.5"
COMPONENT_MESSAGE = "set this wall height to 3.5 and thickness to 0.3"
SELECTION = Selection("facade", "wall-07")


def scalar_answer(**changes):
    return {
        "status": "compiled", "targetComponentId": "facade", "elementId": "wall-07",
        "utterance": "set height to 3.5", "semanticEdit": None,
        "why": "Apply the requested height.", "question": None, "contextRefs": [],
        **changes,
    }


def supplement(*refs):
    return scalar_answer(status="needs_context", utterance=None, contextRefs=list(refs), why="Read the named dependency before compiling.")


class ContextProviderTests(unittest.TestCase):
    def setUp(self):
        record, _ = fixture()
        wall = record.entity("wall-07")
        wall = replace(wall, fields={**wall.fields, "producer": "wall", "params": {"height": 3.0, "thickness": 0.2}})
        self.record = replace(record, entities=tuple(wall if item.entity_id == wall.entity_id else item for item in record.entities))
        elements, error = _elements(self.record)
        self.assertIsNone(error)
        self.projection = SimpleNamespace(
            project_id=self.record.project_id, record=self.record, components=None,
            elements=elements, parameters=self.record.parameters, honesty=(),
            state_digest="a" * 64, record_digest=self.record.digest,
        )

    @contextmanager
    def provider(self, name, answers):
        calls, requests = [], []
        pending = iter(answers)
        original_request = intent_agent._invocation_request

        def request(**kwargs):
            value = original_request(**kwargs)
            requests.append(value)
            return value

        def usage():
            # Separate counters make each supplemented call observable.
            return {"input_tokens": 100 + len(calls), "output_tokens": 20 + len(calls)}

        with ExitStack() as stack:
            stack.enter_context(patch.object(intent_agent, "_invocation_request", side_effect=request))
            if name == "codex":
                stack.enter_context(patch.object(intent_agent, "_codex_version", return_value="context-test-1"))
                compiler = CodexCompiler(model="context-model")

                def run(command, prompt, timeout_s):
                    schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))
                    calls.append({"prompt": prompt, "schema": schema, "command": list(command)})
                    Path(command[command.index("-o") + 1]).write_text(json.dumps(next(pending)), encoding="utf-8")
                    counters = {**usage(), "cached_input_tokens": 50}
                    event = json.dumps({"type": "turn.completed", "usage": counters, "model": "exact-context-model"})
                    return subprocess.CompletedProcess(command, 0, event, "")

                stack.enter_context(patch.object(intent_agent, "_run_bounded", side_effect=run))
            else:
                def create(**kwargs):
                    schema = json.loads(kwargs["system"].split("JSON schema of the only acceptable answer:\n", 1)[1])
                    calls.append({**deepcopy(kwargs), "schema": schema, "prompt": kwargs["messages"][0]["content"]})
                    return SimpleNamespace(
                        content=[SimpleNamespace(type="text", text=json.dumps(next(pending)))],
                        usage={**usage(), "cache_read_input_tokens": 50}, model="exact-context-model",
                    )

                sdk = SimpleNamespace(Anthropic=lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=create)))
                stack.enter_context(patch.object(intent_agent, "_anthropic_sdk", return_value=(sdk, "context-sdk-1")))
                compiler = AnthropicCompiler(model="context-model")
            yield SimpleNamespace(compiler=compiler, calls=calls, requests=requests)

    def invoke(self, provider, message=SCALAR_MESSAGE, observer=None):
        return provider.compiler.compile(message=message, selection=SELECTION, projection=self.projection, operation_observer=observer)

    def sheet(self, call):
        return json.loads(call["prompt"].split("RECORD SHEET (JSON):\n", 1)[1].split("\n\nREQUEST:\n", 1)[0])

    def component_answer(self):
        wall = self.record.entity("wall-07").to_dict()
        wall.pop("lineage")
        wall["fields"]["params"] = {"height": 3.5, "thickness": 0.3}
        return scalar_answer(utterance=None, semanticEdit={
            "summary": "Raise and thicken the selected wall.", "entities": [wall],
            "parameters": [], "relations": [], "removeEntityIds": [],
            "removeParameterKeys": [], "removeRelationIds": [], "protected": [], "kept": [],
        })

    def test_scalar_adapters_send_small_schema_and_dependency_slice(self):
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [scalar_answer()]) as provider:
                spans = []
                result = self.invoke(provider, observer=spans.append)
                self.assertEqual(result.status, "compiled")
                self.assertEqual(len(provider.calls), 1)
                call = provider.calls[0]
                sheet = self.sheet(call)
                self.assertEqual(sheet["requestContext"]["tier"], "scalar")
                self.assertEqual(sheet["requestContext"]["targetIds"], ["wall-07"])
                self.assertEqual(sheet["requestContext"]["editableFields"], ["height"])
                self.assertNotIn("producerSignatures", sheet)
                self.assertNotIn("semanticIds", sheet)
                self.assertNotIn("remote-wall", {row["elementId"] for row in sheet["elements"]})
                self.assertEqual(call["schema"]["properties"]["semanticEdit"], {"type": "null"})
                self.assertLess(len(json.dumps(call["schema"])), len(json.dumps(intent_agent.response_schema())) / 4)
                self.assertEqual(spans[0]["details"]["task_type"], "scalar")
                self.assertTrue(spans[0]["details"]["validator_pass"])
                self.assertEqual(spans[0]["usage"]["cached_input_tokens"], 50)
                self.assertEqual(spans[0]["reported_model"], "exact-context-model")
                if name == "anthropic":
                    self.assertEqual(call["max_tokens"], 800)

    def test_component_adapters_accept_selected_wall_multi_field_edit(self):
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [self.component_answer()]) as provider:
                result = self.invoke(provider, COMPONENT_MESSAGE)
                self.assertIsNotNone(result.semantic_edit)
                sheet = self.sheet(provider.calls[0])
                self.assertEqual(sheet["requestContext"]["tier"], "component")
                self.assertEqual(sheet["requestContext"]["editableFields"], ["height", "thickness"])
                if name == "anthropic":
                    self.assertEqual(provider.calls[0]["max_tokens"], 2400)

    def test_broad_architectural_request_keeps_design_context(self):
        answer = scalar_answer(status="unsupported", utterance=None, why="This proposal needs an architectural design step.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider:
                result = self.invoke(provider, "reorganize the entire gallery")
                self.assertEqual(result.status, "unsupported")
                sheet = self.sheet(provider.calls[0])
                self.assertEqual(sheet["requestContext"]["tier"], "design")
                self.assertIn("remote-wall", {row["elementId"] for row in sheet["elements"]})
                self.assertIn("semanticIds", sheet)
                if name == "anthropic":
                    self.assertEqual(provider.calls[0]["max_tokens"], 5000)

    def test_supplement_adds_named_context_with_same_target_base_and_individual_usage(self):
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [supplement("entity:remote-wall"), scalar_answer()]) as provider:
                spans = []
                result = self.invoke(provider, observer=spans.append)
                self.assertEqual(result.status, "compiled")
                self.assertEqual(len(provider.calls), 2)
                first, second = map(self.sheet, provider.calls)
                self.assertNotIn("remote-wall", {row["elementId"] for row in first["elements"]})
                self.assertIn("remote-wall", {row["elementId"] for row in second["elements"]})
                self.assertEqual(first["requestContext"]["targetIds"], second["requestContext"]["targetIds"])
                self.assertEqual(second["requestContext"]["expansionCount"], 1)
                self.assertEqual([request.checkpoint_digest for request in provider.requests], ["a" * 64] * 2)
                self.assertNotEqual(provider.requests[0].context_digest, provider.requests[1].context_digest)
                self.assertEqual([request.payload["selection"] for request in provider.requests], [{"component_id": "facade", "element_id": "wall-07", "gestures": []}] * 2)
                self.assertEqual(len(spans), 2)
                offset = 50 if name == "anthropic" else 0
                self.assertEqual([span["usage"]["input_tokens"] for span in spans], [101 + offset, 102 + offset])
                self.assertEqual([span["usage"]["output_tokens"] for span in spans], [21, 22])
                self.assertEqual([span["details"]["retry_attempt"] for span in spans], [0, 1])

    def test_unknown_or_already_included_ref_fails_without_a_second_call(self):
        for name in PROVIDERS:
            for ref in ("entity:invented", "entity:wall-07"):
                with self.subTest(provider=name, ref=ref), self.provider(name, [supplement(ref)]) as provider:
                    spans = []
                    with self.assertRaises(IntentAgentFailed) as caught:
                        self.invoke(provider, observer=spans.append)
                    self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                    self.assertEqual(len(provider.calls), 1)
                    self.assertEqual(len(spans), 1)
                    self.assertFalse(spans[0]["details"]["validator_pass"])
                    self.assertGreater(spans[0]["usage"]["input_tokens"], 0)

    def test_repeated_supplement_stops_after_its_failed_second_call(self):
        for name in PROVIDERS:
            answers = [supplement("entity:remote-wall")] * 2
            with self.subTest(provider=name), self.provider(name, answers) as provider:
                with self.assertRaises(IntentAgentFailed) as caught:
                    self.invoke(provider)
                self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                self.assertEqual(len(provider.calls), 2)

    def test_third_supplement_is_refused_after_three_total_calls(self):
        for name in PROVIDERS:
            answers = [supplement("entity:remote-wall"), supplement("entity:window-24"), supplement("parameter:unrelated")]
            with self.subTest(provider=name), self.provider(name, answers) as provider:
                spans = []
                with self.assertRaises(IntentAgentFailed) as caught:
                    self.invoke(provider, observer=spans.append)
                self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                self.assertEqual(len(provider.calls), 3)
                self.assertEqual(len(spans), 3)
                self.assertEqual([self.sheet(call)["requestContext"]["expansionCount"] for call in provider.calls], [0, 1, 2])

    def test_wrong_target_or_scalar_field_fails_without_retry(self):
        answers = [scalar_answer(elementId="remote-wall"), scalar_answer(utterance="set thickness to 0.3")]
        for name in PROVIDERS:
            for answer in answers:
                with self.subTest(provider=name, answer=answer), self.provider(name, [answer]) as provider:
                    with self.assertRaises(IntentAgentFailed) as caught:
                        self.invoke(provider)
                    self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                    self.assertEqual(len(provider.calls), 1)

    def test_supplement_does_not_authorize_editing_added_context(self):
        for name in PROVIDERS:
            answers = [supplement("entity:remote-wall"), scalar_answer(elementId="remote-wall")]
            with self.subTest(provider=name), self.provider(name, answers) as provider:
                with self.assertRaises(IntentAgentFailed) as caught:
                    self.invoke(provider)
                self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                self.assertEqual(len(provider.calls), 2)

    def test_diagnostic_failure_cannot_repeat_a_successful_external_call(self):
        observed = []

        def diagnostic(span):
            observed.append(span)
            raise RuntimeError("diagnostic sink unavailable")

        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [scalar_answer()]) as provider:
                result = self.invoke(provider, observer=diagnostic)
                self.assertEqual(result.status, "compiled")
                self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(observed), 2)


if __name__ == "__main__":
    unittest.main()
