"""An architect's sentence, compiled by an agent, typed by the grammar.

The agent is a seam: ``app.state.intent_compiler``. These tests put scripted
compilers there — real objects implementing the port, answering what a model
would have answered — and everything after the seam is real: the projection,
the deterministic grammar, the kernel's closure, the proposal store. What is
proved is the contract around the agent, not the agent.
"""

from __future__ import annotations

from pathlib import Path
import json
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application.intent_agent import (
    CODEX,
    AGENT_FAILED,
    Compilation,
    DeterministicCompiler,
    Selection,
    _parse_answer,
    record_sheet,
    response_schema,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import (
    PROJECT_ID,
    REFERENCE_RUN_ID,
    make_project,
    runner_state_digest,
    write_codex_shim,
)


def semantic_wall_edit() -> dict:
    """A small domain edit with shared dimensions and a real arch opening."""

    return {
        "summary": "Add a supporting wall with an open arched passage, keeping the existing base and level.",
        "entities": [{
            "entity_id": "passage-wall", "schema": "Element@1", "parent_id": "portico",
            "basis_refs": ["studio:intent"],
            "fields": {
                "component_id": "portico", "producer": "wall",
                "references": {
                    "base": {"level": "level-ground"},
                    "line": {
                        "from": {"axis_point": {"axis": "front", "along": 0}},
                        "to": {"axis_point": {"axis": "front", "along": 4}},
                        "inward": [1, 0],
                    },
                },
                "params": {
                    "thickness": "@passage_thickness", "height": 3,
                    "openings": [{
                        "opening_id": "passage-arch", "kind": "door", "shape": "semicircular_arch",
                        "along": 2, "width": "@passage_width", "sill": 0,
                        "head": "@passage_head", "spring_height": 1.5,
                    }],
                },
            },
        }],
        "parameters": [
            {"key": "passage_thickness", "value": 0.3, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
            {"key": "passage_width", "value": 2, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
            {"key": "passage_head", "value": 2.5, "unit": "m", "expr": "1.5 + passage_width / 2", "inputs": ["passage_width"], "source_ref": "studio:intent"},
        ],
        "relations": [], "removeEntityIds": [], "removeParameterKeys": [], "removeRelationIds": [],
        "protected": ["entity:level-ground", "entity:portico-base"],
        "kept": ["The existing base geometry and ground level remain unchanged."],
    }


def scripted(**fields: object):
    """A compiler that answers one fixed compilation, whatever it is asked."""

    class Scripted:
        calls: list[dict] = []

        def compile(self, *, message, selection, projection):
            Scripted.calls.append(
                {"message": message, "selection": selection, "projection": projection}
            )
            base = dict(
                status="compiled",
                provider=CODEX,
                model="scripted",
                utterance=None,
                component_id=None,
                element_id=None,
                why="",
                question=None,
                latency_ms=7,
                prompt_sha256="ab" * 32,
                raw="{}",
            )
            base.update(fields)
            return Compilation(**base)

    return Scripted()


class Failing:
    def compile(self, *, message, selection, projection):
        raise StudioError(502, AGENT_FAILED, "codex exited with 1: no auth")


class IntentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.app = create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)

    def ask(self, utterance: str, **body: object) -> tuple[int, dict]:
        body.setdefault("stateDigest", self.state_digest)
        body.setdefault("targetComponentId", "portico")
        body["utterance"] = utterance
        response = self.client.post("/api/intents", json=body)
        return response.status_code, response.json()


class DeterministicPassThroughTests(IntentTestCase):
    def test_a_grammatical_sentence_is_a_proposal_with_no_agent_words(self) -> None:
        status, payload = self.ask("set height to 0.8", elementId="portico-base")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["agent"]["provider"], "deterministic")
        self.assertEqual(payload["agent"]["compiledUtterance"], "set height to 0.8")
        self.assertEqual(payload["agent"]["why"], "")
        self.assertIsNone(payload["agent"]["promptSha256"])
        self.assertEqual(payload["proposal"]["change"]["new"], 0.8)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-base")

    def test_an_abstract_sentence_with_no_agent_is_the_grammars_question(self) -> None:
        status, payload = self.ask("make the portico a little taller", elementId="portico-base")
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertIn("acceptedForms", payload)

    def test_the_proposal_is_kept_for_a_later_get(self) -> None:
        _, payload = self.ask("set height to 0.8", elementId="portico-base")
        proposal_id = payload["proposal"]["proposalId"]
        self.assertEqual(self.client.get(f"/api/proposals/{proposal_id}").status_code, 200)


class ScriptedAgentTests(IntentTestCase):
    def test_an_unfamiliar_natural_name_can_be_resolved_by_one_agent_call(self) -> None:
        compiler = scripted(
            utterance="increase height by 10 %", component_id="portico", element_id="portico-base",
            why="The arrival plinth is the portico base; a little = +10 %.",
        )
        self.app.state.intent_compiler = compiler
        status, payload = self.ask("Make the arrival plinth a little taller.", targetComponentId=None)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-base")
        self.assertEqual(payload["proposal"]["change"]["old"], 0.6)
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 0.66)
        self.assertEqual(len(compiler.calls), 1)

    def test_the_agents_compiled_sentence_becomes_the_records_proposal(self) -> None:
        compiler = scripted(
            utterance="increase height by 10 %",
            component_id="portico",
            element_id="portico-base",
            why="a little = +10 %",
        )
        self.app.state.intent_compiler = compiler
        status, payload = self.ask("make the portico base a little taller")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["agent"]["provider"], "codex")
        self.assertEqual(payload["agent"]["why"], "a little = +10 %")
        self.assertEqual(payload["agent"]["compiledUtterance"], "increase height by 10 %")
        self.assertEqual(payload["agent"]["latencyMs"], 7)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-base")
        # 0.6 in the record, +10 % as the grammar computes it — the number is the record's
        self.assertEqual(payload["proposal"]["change"]["old"], 0.6)
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 0.66)
        self.assertEqual(payload["proposal"]["utterance"], "increase height by 10 %")
        self.assertEqual(compiler.calls[0]["message"], "make the portico base a little taller")

    def test_the_agent_may_move_the_selection_to_an_element_the_record_declares(self) -> None:
        self.app.state.intent_compiler = scripted(
            utterance="set height to 0.5", component_id="portico", element_id="portico-cornice"
        )
        status, payload = self.ask("raise the cornice", elementId="portico-base")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-cornice")
        self.assertEqual(payload["proposal"]["change"]["old"], 0.3)

    def test_an_agent_naming_an_element_the_record_lacks_is_a_technical_failure(self) -> None:
        self.app.state.intent_compiler = scripted(
            utterance="set height to 0.5", component_id="portico", element_id="portico-attic"
        )
        status, payload = self.ask("raise the attic")
        self.assertEqual(status, 502, payload)
        self.assertEqual(payload["code"], AGENT_FAILED)
        self.assertNotIn("question", payload)

    def test_the_agents_question_is_asked_as_the_agents(self) -> None:
        self.app.state.intent_compiler = scripted(
            status="question",
            question="Which element: portico-base (height 0.6) or portico-cornice (height 0.3)?",
            why="the request names the portico, not an element",
        )
        status, payload = self.ask("make the portico taller")
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertTrue(payload["question"].startswith("Which element"))
        self.assertIn("the codex agent asked instead of compiling", payload["detail"])
        self.assertIn("the request names the portico", payload["detail"])
        self.assertNotIn("acceptedForms", payload)

    def test_an_agent_that_claims_to_compile_but_does_not_is_a_technical_failure(self) -> None:
        self.app.state.intent_compiler = scripted(
            utterance="lift it a bit", component_id="portico", element_id="portico-base"
        )
        status, payload = self.ask("lift it")
        self.assertEqual(status, 502, payload)
        self.assertEqual(payload["code"], AGENT_FAILED)
        self.assertIn("is not in the grammar", payload["detail"])
        self.assertNotIn("question", payload)
        self.assertNotIn("acceptedForms", payload)

    def test_an_agent_that_fails_is_a_502_with_its_own_sentence(self) -> None:
        self.app.state.intent_compiler = Failing()
        status, payload = self.ask("anything")
        self.assertEqual(status, 502, payload)
        self.assertEqual(payload["code"], AGENT_FAILED)
        self.assertIn("no auth", payload["detail"])

    def test_a_sentence_already_in_the_grammar_never_reaches_the_agent(self) -> None:
        compiler = scripted(utterance="set height to 9", element_id="portico-cornice")
        self.app.state.intent_compiler = compiler
        status, payload = self.ask("set height to 0.8", elementId="portico-base")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["agent"]["provider"], "deterministic")
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-base")
        self.assertEqual(payload["proposal"]["change"]["new"], 0.8)
        self.assertEqual(compiler.calls, [])

    def test_a_stale_base_is_refused_before_the_agent_is_asked(self) -> None:
        compiler = scripted(utterance="set height to 0.8", element_id="portico-base")
        self.app.state.intent_compiler = compiler
        status, payload = self.ask("set height to 0.8", stateDigest="0" * 64)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "STALE_BASE")
        self.assertEqual(compiler.calls, [])

    def test_an_agent_can_terminally_name_a_missing_tool_capability(self) -> None:
        self.app.state.intent_compiler = scripted(
            status="unsupported",
            why="This compiler cannot create a passage component.",
        )
        status, payload = self.ask("make a passage beneath the landing")
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "UNSUPPORTED_REQUEST")
        self.assertEqual(
            payload["detail"], "This compiler cannot create a passage component."
        )
        self.assertIsNone(payload["pendingIntent"]["continuationToken"])
        self.assertNotIn("question", payload)

    def test_unsupported_scalar_reply_preserves_current_action_kind(self) -> None:
        self.app.state.intent_compiler = scripted(
            status="unsupported",
            why="This compiler cannot make that scalar change.",
        )
        status, payload = self.ask(
            "make the portico base taller", elementId="portico-base"
        )
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "UNSUPPORTED_REQUEST")
        self.assertEqual(
            payload["pendingIntent"]["actionKind"], "clarify"
        )
        self.assertIsNone(payload["pendingIntent"]["continuationToken"])
        self.assertNotIn("question", payload)


class RecordSheetTests(IntentTestCase):
    def test_the_sheet_carries_only_what_the_projection_declares(self) -> None:
        with self.client:
            self.client.get("/api/state")
            projection = project_state(bound_project(self.app.state))
        sheet = record_sheet(projection, Selection("portico", "portico-base"))
        ids = {component["componentId"] for component in sheet["components"]}
        self.assertIn("portico", ids)
        base = next(e for e in sheet["elements"] if e["elementId"] == "portico-base")
        self.assertEqual(base["numericFields"]["height"], 0.6)
        self.assertEqual(sheet["selection"], {"componentId": "portico", "elementId": "portico-base"})
        self.assertEqual(len(sheet["grammar"]["forms"]), 4)
        self.assertEqual(list(sheet["honesty"]), list(projection.honesty))


class SemanticIntentTests(IntentTestCase):
    def test_unordered_repeated_reference_sets_compile_without_changing_the_design(self) -> None:
        from archflow.state.state_record import apply_state_record_operator

        edit = semantic_wall_edit()
        basis = ["studio:intent", "evidence:demo", "studio:intent"]
        edit["entities"][0]["basis_refs"] = basis
        edit["parameters"][2].update(
            expr="passage_width / 2 + passage_thickness + 1.2",
            inputs=["passage_width", "passage_thickness", "passage_width"],
        )
        edit["relations"] = [{"relation_id": "rel-cornice-on-base", "basis_refs": basis}]
        edit["protected"] = ["entity:level-ground", "entity:level-ground"]
        compiler = scripted(semantic_edit=edit, component_id="portico")
        self.app.state.intent_compiler = compiler

        status, body = self.ask("Add the passage wall and retain the ground level.", targetComponentId=None)

        self.assertEqual(status, 201, body)
        self.assertEqual(body["proposal"]["status"], "proposed")
        proposal = self.app.state.proposals.get(body["proposal"]["proposalId"])
        operator = proposal.state_record_operator
        self.assertEqual(operator.entities[0].basis_refs, ("evidence:demo", "studio:intent"))
        self.assertEqual(operator.relations[0].basis_refs, ("evidence:demo", "studio:intent"))
        self.assertEqual(operator.parameters[2].inputs, ("passage_thickness", "passage_width"))
        self.assertEqual(operator.protected, ("entity:level-ground",))
        base = compiler.calls[0]["projection"].record
        successor = apply_state_record_operator(base, operator)
        self.assertEqual(successor.entity("passage-wall").fields, edit["entities"][0]["fields"])
        self.assertEqual(successor.entity("portico-base"), base.entity("portico-base"))
        self.assertEqual(next(p.value for p in successor.parameters if p.key == "passage_head"), 2.5)
        self.assertEqual(edit["entities"][0]["basis_refs"], basis)

    def test_repeated_removal_ids_are_one_removal_of_each_named_item(self) -> None:
        from archflow.state.state_record import apply_state_record_operator

        edit = semantic_wall_edit()
        edit.update(
            removeEntityIds=["portico-cornice", "portico-base", "portico-cornice"],
            removeParameterKeys=["span", "bay", "span"],
            removeRelationIds=["rel-cornice-on-base", "rel-cornice-on-base"],
            protected=["entity:level-ground"], kept=["The ground level remains unchanged."],
        )
        compiler = scripted(semantic_edit=edit, component_id="portico")
        self.app.state.intent_compiler = compiler
        status, body = self.ask("Replace the old base and cornice with the passage wall.")
        self.assertEqual(status, 201, body)
        operator = self.app.state.proposals.get(body["proposal"]["proposalId"]).state_record_operator
        self.assertEqual(operator.remove_entity_ids, ("portico-base", "portico-cornice"))
        self.assertEqual(operator.remove_parameter_keys, ("bay", "span"))
        self.assertEqual(operator.remove_relation_ids, ("rel-cornice-on-base",))
        successor = apply_state_record_operator(compiler.calls[0]["projection"].record, operator)
        self.assertNotIn("portico-base", {entity.entity_id for entity in successor.entities})
        self.assertEqual(successor.relations, ())

    def test_reference_set_normalisation_retains_content_validation(self) -> None:
        for field, value in (("basis_refs", ["studio:intent", 7]), ("basis_refs", "studio:intent")):
            edit = semantic_wall_edit()
            edit["entities"][0][field] = value
            self.app.state.intent_compiler = scripted(semantic_edit=edit)
            status, body = self.ask("Add the passage wall.")
            self.assertEqual(status, 422, body)
            self.assertEqual(body["code"], "SEMANTIC_EDIT_INVALID")
        edit = semantic_wall_edit()
        edit["parameters"][2]["inputs"] = ["passage_thickness", "passage_thickness"]
        self.app.state.intent_compiler = scripted(semantic_edit=edit)
        status, body = self.ask("Add the passage wall.")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "SEMANTIC_EDIT_INVALID")
        self.assertIn("disagree with its expression", body["detail"])

    def test_missing_members_reach_the_agent_without_a_scalar_target(self) -> None:
        from archflow.state.state_record import StateRecordEditKind, apply_state_record_operator

        compiler = scripted(semantic_edit=semantic_wall_edit(), component_id="portico")
        self.app.state.intent_compiler = compiler
        before = self.repository.read_head()
        status, body = self.ask("平台下面缺了一条通道和支承，帮我补齐，现有宽度高度都保持不变。", elementId="portico-base")
        self.assertEqual(status, 201, body)
        self.assertEqual(len(compiler.calls), 1)
        proposal = body["proposal"]
        self.assertEqual(proposal["change"]["kind"], "edit_components")
        self.assertNotIn("old", proposal["change"])
        self.assertIsNone(proposal["target"]["key"])
        self.assertIsNone(proposal["decisionOperator"])
        self.assertEqual(body["agent"]["compiledUtterance"], proposal["change"]["summary"])
        self.assertEqual(body["pendingIntent"]["candidates"], [])
        stored = self.app.state.proposals.get(proposal["proposalId"])
        self.assertEqual(stored.state_record_operator.kind, StateRecordEditKind.EDIT_COMPONENTS)
        base = compiler.calls[0]["projection"].record
        successor = apply_state_record_operator(base, stored.state_record_operator)
        self.assertEqual(successor.entity("portico-base"), base.entity("portico-base"))
        self.assertEqual(successor.entity("passage-wall").fields["producer"], "wall")
        self.assertIn("entity:passage-wall", successor.closure(("parameter:passage_width",)))
        self.assertEqual(self.repository.read_head(), before)

    def test_a_scalar_is_not_an_answer_to_missing_members(self) -> None:
        self.app.state.intent_compiler = scripted(utterance="set height to 22", component_id="portico", element_id="portico-base")
        status, body = self.ask("补齐平台下面缺少的侧向通道，平台高度保持不变。", elementId="portico-base")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "UNSUPPORTED_REQUEST")
        self.assertEqual(body["pendingIntent"]["candidates"], [])

    def test_a_design_question_has_no_unrelated_numeric_choices(self) -> None:
        self.app.state.intent_compiler = scripted(status="question", question="Should the passage lead to the hall or the garden?", why="The destination changes the route.")
        status, body = self.ask("Add a passage beneath the landing.", elementId="portico-base")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["question"], "Should the passage lead to the hall or the garden?")
        self.assertEqual(body["pendingIntent"]["candidates"], [])

    def test_a_missing_agent_never_offers_a_numeric_replacement(self) -> None:
        status, body = self.ask("补齐缺少的通道和支承，楼梯宽度保持不变。", elementId="portico-base")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "UNSUPPORTED_REQUEST")
        self.assertEqual(body["pendingIntent"]["candidates"], [])

    def test_undeclared_references_and_producers_are_refused_before_a_proposal_exists(self) -> None:
        for mutate in (
            lambda edit: edit["entities"][0]["fields"]["references"].update(base={"level": "unknown-level"}),
            lambda edit: edit["entities"][0]["fields"]["references"]["line"]["from"]["axis_point"].update(axis="axis-a"),
            lambda edit: edit["entities"][0]["fields"].update(producer="loft"),
            lambda edit: edit["entities"][0].update(schema="GeometryProgram@1"),
        ):
            edit = semantic_wall_edit()
            mutate(edit)
            self.app.state.intent_compiler = scripted(semantic_edit=edit)
            status, body = self.ask("Add a passage wall.")
            self.assertEqual(status, 422, body)
            self.assertEqual(body["code"], "SEMANTIC_EDIT_INVALID")
        self.assertEqual(self.app.state.proposals.for_state(self.state_digest), ())

    def test_semantic_edits_bind_the_requested_exact_base_before_the_agent(self) -> None:
        compiler = scripted(semantic_edit=semantic_wall_edit())
        self.app.state.intent_compiler = compiler
        status, body = self.ask("Add the passage wall.", stateDigest="0" * 64)
        self.assertEqual(status, 409, body)
        self.assertEqual(compiler.calls, [])

    def test_semantic_diff_is_readable_and_retained_in_an_episode(self) -> None:
        self.app.state.intent_compiler = scripted(semantic_edit=semantic_wall_edit())
        status, body = self.ask("Add a wall with an arched passage.")
        self.assertEqual(status, 201, body)
        proposal = body["proposal"]
        reread = self.client.get(f"/api/proposals/{proposal['proposalId']}")
        self.assertEqual(reread.json(), proposal)
        decided = self.client.post(f"/api/proposals/{proposal['proposalId']}/decision", json={"decision": "rejected", "reason": "Move the passage first."})
        self.assertEqual(decided.status_code, 201, decided.text)
        change = decided.json()["proposals"][0]["change"]
        self.assertEqual(change["kind"], "edit_components")
        self.assertEqual(change["edits"]["entities"][0]["entity_id"], "passage-wall")
        self.assertNotIn("old", change)

    def test_the_model_schema_uses_the_producer_signature(self) -> None:
        from archflow.capabilities.element_producers import producer_signatures

        schema = response_schema(strict=False)
        edits = schema["properties"]["semanticEdit"]["anyOf"][1]
        element = edits["properties"]["entities"]["items"]["anyOf"][0]
        wall = element["properties"]["fields"]["anyOf"][0]
        self.assertEqual(wall["properties"]["params"], producer_signatures()["wall"]["parameters"])
        self.assertFalse(wall["additionalProperties"])
        projection = project_state(bound_project(self.app.state))
        sheet = record_sheet(projection, Selection("portico", "portico-base"))
        self.assertEqual(sheet["producerSignatures"], producer_signatures())
        self.assertIn("relationships", sheet)
        self.assertIn("frame", sheet)
        self.assertIn("types", sheet)
        self.assertNotIn("profile", next(row for row in sheet["elements"] if row["elementId"] == "portico-base")["params"])


class AnswerParsingTests(unittest.TestCase):
    def test_a_fenced_json_answer_is_read(self) -> None:
        raw = '```json\n{"status":"compiled","targetComponentId":"portico","elementId":"portico-base","utterance":"set height to 0.8","why":"","question":null}\n```'
        compilation = _parse_answer(raw, provider=CODEX, model=None, latency_ms=1, prompt_sha="00" * 32)
        self.assertEqual(compilation.utterance, "set height to 0.8")
        self.assertEqual(compilation.element_id, "portico-base")

    def test_optional_nulls_from_strict_semantic_output_mean_absent_domain_fields(self) -> None:
        edit = semantic_wall_edit()
        fields = edit["entities"][0]["fields"]
        fields["type_ref"] = None
        fields["params"]["openings"][0].update(at=None, count=None, step=None)
        raw = json.dumps({
            "status": "compiled", "targetComponentId": "portico", "elementId": None,
            "utterance": None, "semanticEdit": edit, "why": "The named wall and opening meet the request.", "question": None,
        })
        compilation = _parse_answer(raw, provider=CODEX, model=None, latency_ms=1, prompt_sha="00" * 32)
        parsed = compilation.semantic_edit["entities"][0]["fields"]
        self.assertNotIn("type_ref", parsed)
        self.assertNotIn("at", parsed["params"]["openings"][0])

    def test_not_json_is_the_agents_failure(self) -> None:
        with self.assertRaises(StudioError) as caught:
            _parse_answer("I would raise it", provider=CODEX, model=None, latency_ms=1, prompt_sha="00" * 32)
        self.assertEqual(caught.exception.code, AGENT_FAILED)

    def test_a_status_outside_the_two_is_a_failure(self) -> None:
        with self.assertRaises(StudioError):
            _parse_answer('{"status":"done"}', provider=CODEX, model=None, latency_ms=1, prompt_sha="00" * 32)

    def test_compiled_without_a_sentence_is_a_failure(self) -> None:
        with self.assertRaises(StudioError):
            _parse_answer(
                '{"status":"compiled","targetComponentId":null,"elementId":null,"utterance":null,"why":"","question":null}',
                provider=CODEX, model=None, latency_ms=1, prompt_sha="00" * 32,
            )

    def test_the_deterministic_compiler_passes_the_sentence_through(self) -> None:
        compilation = DeterministicCompiler().compile(
            message="set height to 0.8", selection=Selection("portico", None), projection=None  # type: ignore[arg-type]
        )
        self.assertEqual(compilation.status, "compiled")
        self.assertEqual(compilation.utterance, "set height to 0.8")
        self.assertEqual(compilation.component_id, "portico")

    def test_unsupported_answer_is_read_without_a_question(self) -> None:
        compilation = _parse_answer(
            '{"status":"unsupported","why":"This tool cannot model that component."}',
            provider=CODEX,
            model=None,
            latency_ms=1,
            prompt_sha="00" * 32,
        )
        self.assertEqual(compilation.status, "unsupported")
        self.assertEqual(compilation.why, "This tool cannot model that component.")
        self.assertIsNone(compilation.utterance)
        self.assertIsNone(compilation.question)


class ProviderOnTheWireTests(IntentTestCase):
    """The screen may not claim an agent the process does not have."""

    def test_the_default_process_says_deterministic(self) -> None:
        response = self.client.get("/api/project")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["intentProvider"], "deterministic")
        self.assertIsNone(response.json()["intentModel"])

    def test_a_compiler_that_does_not_say_is_unknown(self) -> None:
        self.app.state.intent_compiler = scripted(utterance="set height to 0.8")
        response = self.client.get("/api/project")
        self.assertEqual(response.json()["intentProvider"], "unknown")

    def test_the_codex_compiler_names_itself_and_its_model(self) -> None:
        from archflow_studio_api.application.intent_agent import CodexCompiler

        # A real executable, because building the compiler reads its version:
        # the screen names the agent this process actually has.
        shim, _ = write_codex_shim(self.root, answer="{}")
        self.app.state.intent_compiler = CodexCompiler(
            executable=str(shim), model="gpt-5"
        )
        response = self.client.get("/api/project")
        self.assertEqual(response.json()["intentProvider"], "codex")
        self.assertEqual(response.json()["intentModel"], "gpt-5")
