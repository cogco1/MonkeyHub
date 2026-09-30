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
from archflow.state.state_record import Entity, Parameter
from archflow_studio_api.application import intent_agent
from archflow_studio_api.application.intent_agent import AnthropicCompiler, CodexCompiler, IntentAgentFailed, Selection
from archflow_studio_api.application.projection import _elements
from monkeyarch.construction import vocabulary

from .test_intent_context import fixture


PROVIDERS = ("codex", "anthropic")
SCALAR_MESSAGE = "set this wall height to 3.5"
COMPONENT_MESSAGE = "set this wall height to 3.5 and thickness to 0.3"
SELECTION = Selection("facade", "wall-07")


def scalar_answer(**changes):
    return {
        "status": "compiled",
        "actions": [{"op": "set_parameter", "field": "height", "value": 3.5, "unit": "m"}],
        "why": "Apply the requested height.", "question": None, "contextRefs": [],
        **changes,
    }


def supplement(*refs):
    return scalar_answer(status="needs_context", actions=[], contextRefs=list(refs), why="Read the named dependency before compiling.")


def construction_answer(**changes):
    """A design-tier answer in the construction contract, every key present as a strict provider writes it."""

    return {"status": "compiled", "script": None, "facets": None, "parameters": None, "keep": None,
            "utterance": None, "targetId": None, "why": "", "question": None, "contextRefs": [], **changes}


# A project made by one construction script: neutral ids, two voids in a loop,
# something standing on a top, and only later a facet saying what one part is.
NEUTRAL_SCRIPT = "\n".join([
    "mass = extrude(rect(0, 0, 12, 8), 3, at=level('ground'))",
    "block = extrude(rect(3, 2, 6, 4), 2.5, at=top(mass))",
    "for i in range(2):",
    "    cutter = extrude(rect(1 + 4 * i, -0.2, 1.2, 0.6), 1.5, at=0.9)",
    "    cut(mass, cutter)",
])


def construction_projection():
    """The neutral project as a projection a compiler reads, with ``mass`` later faceted as architecture says."""

    from archflow.project.refs import ProjectVersionRef
    from archflow.state.state_record import StateRecord, apply_state_record_operator, compile_component_edit
    from monkeyarch.construction import compile_construction_script

    base = StateRecord("neutral-project", "run-neutral", entities=(
        Entity("model", "Component@1", {"intent": "the model"}),
        Entity("ground", "Level@1", {"role": "ground", "elevation": 0.0}),
    ), parameters=(Parameter("module", 1.2, "m"),), base=ProjectVersionRef("neutral-project", 0, "0" * 64),
        basis_refs=("studio:intent",), evidence_refs=("studio:intent",))
    made = compile_construction_script(NEUTRAL_SCRIPT, base, root_component_id="model")
    record = apply_state_record_operator(base, compile_component_edit(
        base, entities=tuple(Entity.from_dict(row) for row in made.entities)))
    mass = record.entity("mass")
    faceted = replace(mass, fields={**mass.fields, "facets": {"architectural.role": "wall"}})
    record = apply_state_record_operator(record, compile_component_edit(record, entities=(faceted,)))
    elements, error = _elements(record)
    assert error is None, error
    return SimpleNamespace(project_id=record.project_id, record=record, components=None, elements=elements,
                           parameters=record.parameters, honesty=(), state_digest="b" * 64,
                           record_digest=record.digest)


def without_facet_values(text):
    """``text`` with every registered facet value taken out: the one place a layer-rule word such as wall may appear."""

    from archflow.semantics.facets import FACETS

    for values in FACETS.values():
        for value in values or ():
            text = text.replace(json.dumps(value), '""')
    return text


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
        return scalar_answer(actions=[
            {"op": "set_parameter", "field": "height", "value": 3.5, "unit": "m"},
            {"op": "set_parameter", "field": "thickness", "value": 300, "unit": "mm"},
        ])

    def test_scalar_adapters_send_small_schema_and_dependency_slice(self):
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [scalar_answer()]) as provider:
                spans = []
                result = self.invoke(provider, observer=spans.append)
                self.assertEqual(result.status, "compiled")
                self.assertEqual(len(provider.calls), 1)
                call = provider.calls[0]
                sheet = self.sheet(call)
                self.assertEqual([c["field"] for c in sheet["targets"][0]["controls"]], ["height"])
                self.assertNotIn("requestContext", sheet)
                self.assertNotIn("producerSignatures", sheet)
                self.assertNotIn("semanticIds", sheet)
                self.assertNotIn("elements", sheet)
                self.assertNotIn("script", call["schema"]["properties"])
                self.assertEqual(result.target_id, "wall-07")
                self.assertEqual(result.utterance, "set params.height to 3.5")
                self.assertEqual(json.loads(result.receipt.output_json), scalar_answer())
                self.assertEqual(json.loads(result.raw), scalar_answer())
                # The narrow schema names actions only: smaller than the whole answer contract, and small.
                self.assertLess(len(json.dumps(call["schema"])), len(json.dumps(intent_agent.response_schema())))
                self.assertLess(len(json.dumps(call["schema"])), 1500)
                self.assertNotIn("facets", call["schema"]["properties"])
                self.assertEqual(spans[0]["details"]["task_type"], "scalar")
                self.assertTrue(spans[0]["details"]["validator_pass"])
                self.assertEqual(spans[0]["usage"]["cached_input_tokens"], 50)
                self.assertEqual(spans[0]["reported_model"], "exact-context-model")
                if name == "anthropic":
                    self.assertEqual(call["max_tokens"], 400)

    def test_component_adapters_accept_selected_wall_multi_field_edit(self):
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [self.component_answer()]) as provider:
                result = self.invoke(provider, COMPONENT_MESSAGE)
                self.assertIsNotNone(result.semantic_edit)
                sheet = self.sheet(provider.calls[0])
                self.assertEqual([c["field"] for c in sheet["targets"][0]["controls"]], ["height", "thickness"])
                self.assertEqual(result.semantic_edit["entities"][0]["fields"]["params"], {"height": 3.5, "thickness": 0.3})
                self.assertEqual(json.loads(result.receipt.output_json), self.component_answer())
                self.assertNotIn("semanticEdit", json.loads(result.receipt.output_json))
                if name == "anthropic":
                    self.assertEqual(provider.calls[0]["max_tokens"], 800)

    def test_broad_architectural_request_keeps_design_context(self):
        answer = construction_answer(status="unsupported", why="This proposal needs an architectural design step.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider:
                result = self.invoke(provider, "reorganize the entire gallery")
                self.assertEqual(result.status, "unsupported")
                sheet = self.sheet(provider.calls[0])
                self.assertNotIn("requestContext", sheet)
                self.assertIn("remote-wall", {row["id"] for row in sheet["controls"]})
                self.assertEqual(sheet["construction"], vocabulary())
                self.assertEqual([row["id"] for row in sheet["model"]], ["facade"])
                for key in ("semanticIds", "producerSignatures", "elements"):
                    self.assertNotIn(key, sheet)
                self.assertEqual(sheet["types"], [])
                if name == "anthropic":
                    self.assertEqual(provider.calls[0]["max_tokens"], 5000)

    def test_only_a_design_request_builds_the_model_view(self):
        # #419 C7 round 1: a numeric request reads its controls; the model view is built for a design request only.
        design = construction_answer(status="unsupported", why="This proposal needs an architectural design step.")
        for name in PROVIDERS:
            for message, answer, reads in ((SCALAR_MESSAGE, scalar_answer(), False),
                                           ("reorganize the entire gallery", design, True)):
                with self.subTest(provider=name, message=message), patch.object(
                        intent_agent, "construction_model", wraps=intent_agent.construction_model) as built, \
                        self.provider(name, [answer]) as provider:
                    self.invoke(provider, message)
                    self.assertEqual(built.call_count, int(reads))
                    self.assertEqual("model" in self.sheet(provider.calls[0]), reads)
                    self.assertEqual("construction" in self.sheet(provider.calls[0]), reads)

    def test_budget_blocks_initial_call_and_does_not_invent_usage_or_receipt(self):
        for name in PROVIDERS:
            for message in (SCALAR_MESSAGE, "reorganize the entire gallery", "replace wall-07 while keeping its base"):
                with self.subTest(provider=name, message=message), self.provider(name, []) as provider:
                    provider.compiler.context_budget_tokens = 1
                    spans = []
                    result = self.invoke(provider, message, observer=spans.append)
                    self.assertEqual(result.status, "unsupported")
                    self.assertIn("configured limit", result.why)
                    self.assertIn("excludes CLI/provider overhead", result.why)
                    self.assertIsNone(result.receipt)
                    self.assertEqual(provider.calls, [])
                    self.assertEqual(provider.requests, [])
                    self.assertEqual(spans, [])

    def test_local_design_provider_receives_slice_and_supplement_preserves_exact_base(self):
        answer = construction_answer(status="unsupported", targetId="wall-07",
                                     why="The available members do not support that detail.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [
                {**answer, "status": "needs_context", "contextRefs": ["entity:remote-wall"]}, answer,
            ]) as provider:
                result = self.invoke(provider, "replace wall-07 while keeping its base")
                self.assertEqual(len(provider.calls), 2)
                first, second = [self.sheet(call) for call in provider.calls]
                self.assertEqual(first["editTargets"], ["wall-07"])
                self.assertEqual(second["editTargets"], first["editTargets"])
                self.assertNotIn("remote-wall", {row["id"] for row in first["controls"]})
                self.assertIn("remote-wall", {row["id"] for row in second["controls"]})
                self.assertEqual(provider.requests[0].checkpoint_digest, provider.requests[1].checkpoint_digest)
                self.assertEqual(result.status, "unsupported")

    def test_budget_rechecked_before_supplement_without_second_provider_call(self):
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [supplement("entity:remote-wall")]) as provider:
                spans = []
                def observer(span):
                    spans.append(span)
                    provider.compiler.context_budget_tokens = 1
                result = self.invoke(provider, observer=observer)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(len(spans), 1)
                self.assertGreater(spans[0]["usage"]["input_tokens"], 0)

    def test_large_project_local_design_compiles_but_global_request_is_budget_blocked(self):
        wall = self.record.entity("wall-07")
        # Unrelated geometry, each with the architect's own long account of what it is for.
        unrelated = tuple(entity for index in range(100) for entity in (
            Entity(f"unrelated-{index}", "Component@1",
                   {"intent": "Unrelated authored design information. " * 60}, "building"),
            replace(wall, entity_id=f"unrelated-{index}-body", parent_id=f"unrelated-{index}",
                    fields={**wall.fields, "component_id": f"unrelated-{index}"}),
        ))
        record = replace(self.record, entities=(*self.record.entities, *unrelated))
        elements, error = _elements(record)
        self.assertIsNone(error)
        self.projection = SimpleNamespace(**{**vars(self.projection), "record": record, "elements": elements,
                                            "record_digest": record.digest})
        answer = construction_answer(script="w = get('wall-07')\nset_height(w, 3.2)", targetId="wall-07",
                                     why="Raise the selected geometry, keeping where it stands.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider:
                result = self.invoke(provider, "reconfigure wall-07 while keeping its base")
                self.assertEqual(result.status, "compiled")
                # The script changes only the request's own target, so the scope check lets it through.
                self.assertEqual(result.script, answer["script"])
                self.assertNotIn("unrelated-99", json.dumps(self.sheet(provider.calls[0])))
                blocked = self.invoke(provider, "reconfigure the entire building")
                self.assertEqual(blocked.status, "unsupported")
                self.assertIn("configured limit", blocked.why)
                self.assertEqual(len(provider.calls), 1)

    def test_local_supplement_cannot_change_a_control_outside_the_request(self):
        request = construction_answer(status="needs_context", targetId="wall-07", why="Read the named wall.",
                                      contextRefs=["entity:remote-wall"])
        # A control the supplement made readable is still not the target's to change.
        answer = construction_answer(targetId="wall-07", why="Move the unrelated control.",
                                     parameters=[{"key": "unrelated", "value": 4.0, "unit": "m", "expr": None,
                                                  "inputs": None, "epistemic_status": None, "source_ref": None}])
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [request, answer]) as provider:
                with self.assertRaises(IntentAgentFailed) as caught:
                    self.invoke(provider, "replace wall-07 while keeping its base")
                self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                self.assertEqual(len(provider.calls), 2)

    def test_supplement_adds_named_context_with_same_target_base_and_individual_usage(self):
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [supplement("entity:remote-wall"), scalar_answer()]) as provider:
                spans = []
                result = self.invoke(provider, observer=spans.append)
                self.assertEqual(result.status, "compiled")
                self.assertEqual(len(provider.calls), 2)
                first, second = map(self.sheet, provider.calls)
                self.assertNotIn("remote-wall", json.dumps(first))
                self.assertIn("remote-wall", json.dumps(second["dependencyFacts"]))
                self.assertEqual(first["targets"], second["targets"])
                self.assertNotIn("requestContext", second)
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
                self.assertEqual([span["details"]["retry_attempt"] for span in spans], [0, 1, 2])

    def test_wrong_target_or_scalar_field_fails_without_retry(self):
        answers = [scalar_answer(elementId="remote-wall"), scalar_answer(actions=[
            {"op": "set_parameter", "field": "thickness", "value": 0.3, "unit": "m"},
        ])]
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

    @contextmanager
    def recorded_projections(self):
        """Every model-facing projection built during one request, in order."""
        builds = []
        real = intent_agent.model_context

        def recording(context):
            built = real(context)
            builds.append({"expansion_count": context.expansion_count,
                           "included": context.included_refs, "sheet": built})
            return built

        with patch.object(intent_agent, "model_context", side_effect=recording):
            yield builds

    def test_one_round_builds_the_sent_projection_once(self):
        """The budget and the request read one projection, not two of their own.

        Building it twice is the same answer bought twice, and it leaves the
        measured text and the sent text agreeing only by construction.
        """

        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [scalar_answer()]) as provider:
                with self.recorded_projections() as builds:
                    result = self.invoke(provider)
                self.assertEqual(result.status, "compiled")
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(len(builds), 1)
                self.assertEqual(self.sheet(provider.calls[0]), builds[0]["sheet"])

    def test_every_supplement_round_sends_the_projection_that_round_built(self):
        """A supplement must not be answered from the round before it."""

        answers = [supplement("entity:remote-wall"), supplement("entity:window-24"), scalar_answer()]
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, answers) as provider:
                with self.recorded_projections() as builds:
                    result = self.invoke(provider)
                self.assertEqual(result.status, "compiled")
                self.assertEqual(len(provider.calls), 3)
                # One projection per provider round, and each from its own
                # expanded context rather than the one the last round used.
                self.assertEqual(len(builds), 3)
                self.assertEqual([build["expansion_count"] for build in builds], [0, 1, 2])
                self.assertEqual(len({build["included"] for build in builds}), 3)
                for call, build in zip(provider.calls, builds):
                    self.assertEqual(self.sheet(call), build["sheet"])
                # The widening is the supplement's, and it only ever moves forward.
                self.assertNotIn("remote-wall", json.dumps(builds[0]["sheet"]))
                self.assertIn("remote-wall", json.dumps(builds[1]["sheet"]))
                self.assertIn("window-24", json.dumps(builds[2]["sheet"]))
                for earlier, later in zip(builds, builds[1:]):
                    self.assertLess(set(earlier["included"]), set(later["included"]))

    def test_budget_blocked_round_builds_no_projection_the_provider_never_sees(self):
        """A refused round still builds exactly one, and sends none."""

        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, []) as provider:
                provider.compiler.context_budget_tokens = 1
                with self.recorded_projections() as builds:
                    result = self.invoke(provider)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(provider.calls, [])
                self.assertEqual(len(builds), 1)

    def test_known_locked_or_shared_control_stops_before_any_provider_call(self):
        for name in PROVIDERS:
            for blocker in ("locked", "shared"):
                with self.subTest(provider=name, blocker=blocker):
                    self.setUp()
                    parameter = Parameter("height-control", 3.0, "m",
                                          lock_authority="design-decision" if blocker == "locked" else None)
                    entities = []
                    for entity in self.record.entities:
                        if entity.entity_id == "wall-07" or (blocker == "shared" and entity.entity_id == "remote-wall"):
                            entity = replace(entity, fields={**entity.fields, "params": {
                                **entity.fields.get("params", {}), "height": "@height-control",
                            }})
                        entities.append(entity)
                    self.record = replace(self.record, entities=tuple(entities),
                                          parameters=(*self.record.parameters, parameter))
                    elements, error = _elements(self.record)
                    self.assertIsNone(error)
                    self.projection = SimpleNamespace(**{**vars(self.projection), "record": self.record,
                                                        "elements": elements, "parameters": self.record.parameters})
                    with self.provider(name, []) as provider:
                        spans = []
                        result = self.invoke(provider, observer=spans.append)
                    self.assertEqual(result.status, "unsupported" if blocker == "locked" else "question")
                    self.assertEqual(result.provider, "deterministic")
                    self.assertIsNone(result.receipt)
                    self.assertIsNone(result.semantic_edit)
                    self.assertIsNone(result.utterance)
                    self.assertEqual(provider.calls, [])
                    self.assertEqual(spans, [])


class ConstructionContractProviderTests(unittest.TestCase):
    """What a model is shown and may answer, on a project made only by a construction script."""

    provider = ContextProviderTests.provider
    sheet = ContextProviderTests.sheet

    def setUp(self):
        self.projection = construction_projection()

    def compile(self, provider, message="reorganize the whole model", selection=Selection("mass", None)):
        return provider.compiler.compile(message=message, selection=selection, projection=self.projection)

    def test_the_schema_prompt_and_sheet_a_model_reads_keep_the_layer_rule(self):
        from monkeyarch.construction.vocabulary import layer_rule_violations

        answer = construction_answer(script="block = get('block')\nset_height(block, 3)", targetId="block",
                                     why="Raise the block on the mass.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider:
                result = self.compile(provider)
                self.assertEqual((result.status, result.script, result.target_id), ("compiled", answer["script"], "block"))
                call = provider.calls[0]
                sheet = self.sheet(call)
                rules = (call["prompt"].split("RECORD SHEET (JSON):\n", 1)[0] if name == "codex"
                         else call["system"].split("\n\nJSON schema of the only acceptable answer:\n", 1)[0])
                schema = json.dumps(call["schema"])
                # The sheet speaks construction: the language, the model by geometry id, meaning as facets.
                self.assertEqual(sheet["construction"], vocabulary())
                self.assertEqual({row["id"] for row in sheet["model"]}, {"mass", "block", "cutter-1", "cutter-2"})
                mass = next(row for row in sheet["model"] if row["id"] == "mass")
                self.assertEqual((mass["cuts"], mass["facets"]), (["cutter-1", "cutter-2"], {"architectural.role": "wall"}))
                self.assertEqual([row["id"] for row in mass["capabilities"]], ["hosted-opening"])
                self.assertEqual(sheet["selection"], {"id": "mass"})
                self.assertIn({"id": "block", "numericFields": {"height": 2.5}, "parameterBindings": {}}, sheet["controls"])
                # "wall" is there only as the facet value it is, in the sheet and in the schema's facet values.
                for text in (json.dumps(sheet), schema):
                    self.assertIn('"wall"', text)
                for label, text in (("rules", rules), ("schema", schema), ("sheet", json.dumps(sheet))):
                    with self.subTest(text=label):
                        self.assertEqual(layer_rule_violations(without_facet_values(text)), ())
                self.assertEqual(layer_rule_violations(rules), ())

    def test_an_answer_in_the_old_component_edit_contract_is_malformed(self):
        old = {"status": "compiled", "targetComponentId": "mass", "elementId": "mass-body", "utterance": None,
               "why": "", "question": None, "contextRefs": [], "semanticEdit": {
                   "summary": "Add a row.", "entities": [], "parameters": [], "relations": [], "removeEntityIds": [],
                   "removeParameterKeys": [], "removeRelationIds": [], "protected": [], "kept": []}}
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [old]) as provider:
                with self.assertRaises(IntentAgentFailed) as caught:
                    self.compile(provider)
                self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)

    def test_facets_and_parameters_arrive_as_the_record_takes_them(self):
        from archflow.semantics.facets import FACETS

        answer = construction_answer(
            why="The block is a canopy; the module grows.",
            facets=[{"id": "block", "set": {key: ("canopy" if key == "architectural.role" else None) for key in FACETS},
                     "remove": None}],
            parameters=[{"key": "module", "value": 1.5, "unit": None, "expr": None, "inputs": None,
                         "epistemic_status": None, "source_ref": None}])
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider:
                result = self.compile(provider)
                # A strict provider's nulls mean "not given": they are left out, never written.
                self.assertEqual(result.facets, ({"id": "block", "set": {"architectural.role": "canopy"}},))
                self.assertEqual(result.parameters, ({"key": "module", "value": 1.5},))
                self.assertIsNone(result.script)
                self.assertEqual(json.loads(result.receipt.output_json)["facets"], [{"id": "block", "set": {"architectural.role": "canopy"}}])

    def test_a_stated_keep_is_read_as_the_records_refs_and_an_unknown_one_is_malformed(self):
        # #419 C7 round 1: validated as the construction route's keep is; an unknown ref is a malformed answer.
        kept = construction_answer(script="b = get('block')\nset_height(b, 3)", keep=["mass", "parameter:module"],
                                   why="Raise the block; the mass and the module stay.")
        unknown = construction_answer(script="b = get('block')\nset_height(b, 3)", keep=["entity:tower"],
                                      why="Raise the block; the tower stays.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [kept]) as provider:
                result = self.compile(provider)
                self.assertEqual(result.keep, ("entity:mass", "parameter:module"))
                self.assertEqual(json.loads(result.receipt.output_json)["keep"], ["entity:mass", "parameter:module"])
            with self.subTest(provider=name, keep="unknown"), self.provider(name, [unknown]) as provider:
                with self.assertRaises(IntentAgentFailed) as caught:
                    self.compile(provider)
                self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                self.assertIn("entity:tower", caught.exception.detail)

    def test_a_kept_geometry_id_keeps_its_own_parts_and_not_the_shapes_placed_under_it(self):
        # A change reaches parts, never a geometry id's own row, so keeping the id keeps its parts. The
        # shapes a script made are placed under the modelling root and are each their own geometry.
        from archflow_studio_api.application.construction import kept_refs

        record = self.projection.record
        self.assertEqual(kept_refs(record, ("entity:block", "parameter:module")),
                         ("entity:block", "parameter:module", "entity:block-body"))
        self.assertEqual(kept_refs(record, ("entity:block-body",)), ("entity:block-body",))
        self.assertEqual(kept_refs(record, ("entity:model",)), ("entity:model",))

    def test_the_design_sheets_grammar_has_no_sentence_keep(self):
        # #419 C7 round 2: a design answer states what it keeps in keep; the parser still reads the
        # sentence form for the web client.
        from archflow_studio_api.application.intent import ACCEPTED_FORMS, parse_utterance

        answer = construction_answer(status="unsupported", why="Nothing to change yet.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider:
                self.compile(provider)
                self.assertEqual(self.sheet(provider.calls[0])["grammar"], {"forms": list(ACCEPTED_FORMS)})
        self.assertIsNotNone(parse_utterance("set height to 3 keep entity:mass"))

    def test_a_local_script_whose_writes_cannot_be_checked_is_malformed(self):
        # #419 C7 round 2: the scope check fails closed, with the compile's reason.
        from archflow_studio_api.application.construction import ConstructionRefused
        from monkeyarch.construction import ConstructionError

        answer = construction_answer(script="b = get('block')\nset_height(b, 3)", targetId="block",
                                     why="Raise the block.")
        refused = ConstructionRefused(ConstructionError("the shapes of this script cannot be read"))
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider, \
                    patch.object(intent_agent, "script_result", side_effect=refused):
                with self.assertRaises(IntentAgentFailed) as caught:
                    self.compile(provider, "rework block, add a plinth", Selection("block", None))
                self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                self.assertIn("the shapes of this script cannot be read", caught.exception.detail)

    def test_a_local_answer_that_changes_other_geometry_is_malformed_naming_its_geometry_id(self):
        # #419 C7 round 1: recorded as the old scope refusal was (a malformed-answer receipt), in the ids the
        # agent saw: mass, never its part mass-body.
        message, selection = "rework block, add a plinth", Selection("block", None)
        outside = (
            construction_answer(script="m = get('mass')\nset_height(m, 4)", targetId="block", why="Raise the mass."),
            construction_answer(facets=[{"id": "mass", "set": {"material.name": "brick"}}], targetId="block",
                                why="The mass is brick."),
        )
        inside = construction_answer(
            script="b = get('block')\nset_height(b, 3)\nplinth = extrude(rect(0, 0, 1, 1), 0.2, at=level('ground'))",
            facets=[{"id": "plinth", "set": {"material.name": "stone"}}], targetId="block",
            why="Raise the block on a stone plinth.")
        for name in PROVIDERS:
            for answer in outside:
                with self.subTest(provider=name, why=answer["why"]), self.provider(name, [answer]) as provider:
                    with self.assertRaises(IntentAgentFailed) as caught:
                        self.compile(provider, message, selection)
                    self.assertEqual(self.sheet(provider.calls[0])["editTargets"], ["block"])
                    self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
                    detail = caught.exception.detail
                    self.assertIn("changes mass, outside what this request may change (block)", detail)
                    self.assertNotIn("mass-body", detail)
            with self.subTest(provider=name, why="inside"), self.provider(name, [inside]) as provider:
                self.assertEqual(self.compile(provider, message, selection).status, "compiled")

    def test_honesty_reaches_the_model_in_construction_words(self):
        # #419 C7 round 1: backend prose is translated; the ids in it stay as they are.
        self.projection = SimpleNamespace(**{**vars(self.projection), "honesty": (
            "bound element values unavailable: the prism producer refuses a boolean aperture on block-body",)})
        answer = construction_answer(status="unsupported", why="Nothing to change yet.")
        for name in PROVIDERS:
            with self.subTest(provider=name), self.provider(name, [answer]) as provider:
                self.compile(provider)
                self.assertEqual(self.sheet(provider.calls[0])["honesty"], [
                    "bound element values unavailable: the solid realisation refuses a cut opening on block-body"])


if __name__ == "__main__":
    unittest.main()
