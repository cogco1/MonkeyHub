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
import math
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.adapters import occt_backend

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
    RUNNER_RECORD_PATH,
    RUNNER_SEATS_PATH,
    make_portico_project,
    make_project,
    runner_state_digest,
    write_codex_shim,
)
from .test_candidate import _load_kind


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


def clearance_chain_edit() -> dict:
    """The wall edit with one more authored input the passage width follows.

    ``maintenance_clearance`` is a fixture parameter and nothing more: its
    number and the ``+ 0.4`` are test inputs chosen so the arch stays inside
    the fixture wall, not a clearance rule. What the fixture exercises is the
    chain: a declared parameter, a width derived from it by expression, a
    head derived from the width by the semicircular relationship the wall
    edit already states, and an opening that reads both through @bindings.
    """

    edit = semantic_wall_edit()
    thickness, _, head = edit["parameters"]
    edit["parameters"] = [
        thickness,
        {"key": "maintenance_clearance", "value": 1.6, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "passage_width", "value": 2, "unit": "m", "expr": "maintenance_clearance + 0.4", "inputs": ["maintenance_clearance"], "source_ref": "studio:intent"},
        head,
    ]
    return edit


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
        from monkeyarch.capabilities.element_producers import producer_signatures

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
        # A prism's outline is advertised authoring data now, so the sheet
        # shows it: the agent and the person change the same element.
        self.assertEqual(
            next(row for row in sheet["elements"] if row["elementId"] == "portico-base")["params"]["profile"],
            [[0, 0], [4, 0], [4, 2], [0, 2]],
        )


JOB_DEADLINE_S = 120.0

# The wall fixture's own numbers, named once: the arch springs at 1.5 m along a
# 3 m wall at along 2, and the solver overshoots every cut by its 5 cm margin.
SPRING = 1.5
WALL_HEIGHT = 3.0
ARCH_ALONG = 2.0
CUT_MARGIN = 0.05


def _finished(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + JOB_DEADLINE_S
    while time.monotonic() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in ("succeeded", "failed"):
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished")


def _program_ops(repository, run_id: str) -> dict[str, dict]:
    """The operations of the seat program the runner retained, by op id."""

    program = _load_kind(repository, run_id, "seat-geometry-program")
    return {op["op_id"]: op for op in program["proposal"]["operations"]}


def _op_value(op: dict, name: str):
    return next(json.loads(p["value_json"]) for p in op["parameters"] if p["name"] == name)


def _arch_measures(ops: dict[str, dict]) -> tuple[float, float, tuple[float, float]]:
    """What the retained arch tool says: its radius, the box above the springing, and the jamb range along the wall."""

    cylinder = ops["passage-wall-void-passage-arch-cylinder"]
    self_check = (_op_value(cylinder, "start_radius"), _op_value(cylinder, "end_radius"))
    assert self_check[0] == self_check[1], self_check
    upper = _op_value(ops["passage-wall-void-passage-arch-upper"], "vector")[1]
    legs = [point[2] for point in _op_value(ops["passage-wall-void-passage-arch-legs"], "profile")]
    return self_check[0], upper, (min(legs), max(legs))


class SemanticCandidateChainTests(IntentTestCase):
    """A parameter the architect adds, driving a real opening through two runner candidates.

    Everything after the scripted agent is real: the typed operator, the
    kernel's derivation, ``run_project`` producing the wall and its arch, the
    records the run retained, and a second process reading them back. The
    numbers asserted are the ones the runner wrote into the retained program
    and record, never the ones the request carried.
    """

    def compile(self, app, client: TestClient, edit: dict, *, source_run_id: str, state_digest: str, element_id: str, utterance: str) -> tuple[int, dict]:
        app.state.intent_compiler = scripted(semantic_edit=edit, component_id="portico")
        response = client.post("/api/intents", json={
            "stateDigest": state_digest, "sourceRunId": source_run_id,
            "targetComponentId": "portico", "elementId": element_id, "utterance": utterance,
        })
        return response.status_code, response.json()

    def run_candidate(self, client: TestClient, proposal_id: str) -> tuple[str, dict]:
        accepted = client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(accepted.status_code, 202, accepted.text)
        return accepted.json()["candidateId"], _finished(client, accepted.json()["jobId"])

    def candidate_a(self, app, client: TestClient) -> tuple[str, dict]:
        """Candidate A: the chain authored against the reference run; its id and its retained state."""

        status, body = self.compile(
            app, client, clearance_chain_edit(), source_run_id=REFERENCE_RUN_ID,
            state_digest=self.state_digest, element_id="portico-base",
            utterance="Add a supporting wall with an arched passage that follows the maintenance clearance.",
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["proposal"]["status"], "proposed")
        self.assertEqual(body["proposal"]["protected"], ["entity:level-ground", "entity:portico-base"])
        self.assertEqual(body["proposal"]["change"]["kept"], ["The existing base geometry and ground level remain unchanged."])
        self.assertIn("parameter:maintenance_clearance", body["proposal"]["impact"]["direct"])
        run_id, job = self.run_candidate(client,body["proposal"]["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        state = client.get("/api/state", params={"run": run_id})
        self.assertEqual(state.status_code, 200, state.text)
        return run_id, state.json()

    def candidate_b(self, app, client: TestClient, source_run_id: str, source_state: dict) -> tuple[str, dict]:
        """Candidate B: only the clearance moves, from A's exact retained state."""

        edit = semantic_wall_edit()
        edit.update(
            summary="Widen the passage by moving the maintenance clearance it follows.",
            entities=[], parameters=[{"key": "maintenance_clearance", "value": 1.2, "unit": "m"}],
            protected=["entity:portico-base"], kept=["The base stays as it is."],
        )
        status, body = self.compile(
            app, client, edit, source_run_id=source_run_id, state_digest=source_state["stateDigest"],
            element_id="passage-wall", utterance="Change the maintenance clearance so the passage follows it.",
        )
        self.assertEqual(status, 201, body)
        proposal = body["proposal"]
        self.assertEqual(proposal["sourceRunId"], source_run_id)
        self.assertEqual(proposal["baseStateDigest"], source_state["stateDigest"])
        # The kernel already re-evaluated the chain at proposal time: three
        # parameters move, and the bound wall is what the change propagates to.
        self.assertEqual(
            {(row["action"], row["entityId"]) for row in proposal["change"]["changes"]},
            {("update", "parameter:maintenance_clearance"), ("update", "parameter:passage_width"), ("update", "parameter:passage_head")},
        )
        self.assertEqual(proposal["impact"]["propagated"], ["entity:passage-wall"])
        self.assertEqual(proposal["impact"]["conflicts"], [])
        run_id, job = self.run_candidate(client,proposal["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        return run_id, body

    def test_a_new_parameter_drives_the_opening_through_two_candidates_and_a_restart(self) -> None:
        before_head = self.repository.read_head()
        authored = {
            path: self.repository.layout.resolve_relative(path).read_bytes()
            for path in (RUNNER_RECORD_PATH, RUNNER_SEATS_PATH)
        }
        run_a, state_a = self.candidate_a(self.app, self.client)

        # A's retained record: the declarations stand, the values are the kernel's, the bindings are not inlined.
        record_a = _load_kind(self.repository, run_a, "state-record")
        parameters = {p["key"]: p for p in record_a["parameters"]}
        self.assertEqual((parameters["maintenance_clearance"]["value"], parameters["maintenance_clearance"]["expr"]), (1.6, None))
        self.assertEqual((parameters["passage_width"]["value"], parameters["passage_width"]["expr"]), (2, "maintenance_clearance + 0.4"))
        self.assertEqual((parameters["passage_head"]["value"], parameters["passage_head"]["expr"]), (2.5, "1.5 + passage_width / 2"))
        self.assertEqual(parameters["plinth"]["lock_authority"], "client")
        wall = next(e for e in record_a["entities"] if e["entity_id"] == "passage-wall")["fields"]["params"]
        self.assertEqual((wall["openings"][0]["width"], wall["openings"][0]["head"], wall["thickness"]), ("@passage_width", "@passage_head", "@passage_thickness"))
        by_key = {p["key"]: p for p in state_a["parameters"]}
        self.assertEqual((by_key["passage_width"]["value"], by_key["passage_width"]["expr"]), (2.0, "maintenance_clearance + 0.4"))

        # A's retained program: the runner produced the arch from the evaluated chain.
        ops_a = _program_ops(self.repository, run_a)
        radius, upper, jambs = _arch_measures(ops_a)
        self.assertAlmostEqual(radius, 2.0 / 2)
        self.assertAlmostEqual(upper, 2.5 + CUT_MARGIN - SPRING)
        self.assertEqual(jambs, (ARCH_ALONG - 1.0, ARCH_ALONG + 1.0))
        self.assertEqual(_op_value(ops_a["passage-wall"], "vector"), [0.0, WALL_HEIGHT, 0.0])
        self.assertEqual(max(p[0] for p in _op_value(ops_a["passage-wall"], "profile")), 0.3)   # thickness, through @passage_thickness
        self.assertEqual(_op_value(ops_a["portico-base"], "vector"), [0.0, 0.6, 0.0])
        self.assertEqual(_op_value(ops_a["portico-cornice"], "base_level"), 0.6)
        candidate_a = self.client.get(f"/api/candidates/{run_a}").json()
        self.assertEqual((candidate_a["relationChecks"]["held"], candidate_a["relationChecks"]["violated"]), (2, 0))
        self.assertTrue(candidate_a["relationChecks"]["fullyChecked"])

        run_b, _ = self.candidate_b(self.app, self.client, run_a, state_a)

        # B's record: the one input moved, the declared chain was re-evaluated, the declarations themselves did not.
        record_b = _load_kind(self.repository, run_b, "state-record")
        values_b = {p["key"]: (p["value"], p["expr"]) for p in record_b["parameters"]}
        self.assertEqual(values_b["maintenance_clearance"], (1.2, None))
        self.assertEqual(values_b["passage_width"], (1.6, "maintenance_clearance + 0.4"))
        self.assertEqual(values_b["passage_head"], (2.3, "1.5 + passage_width / 2"))
        self.assertEqual(values_b["plinth"], (0.6, None))
        self.assertEqual(values_b["bay"], (2.4, "2 * module"))
        # B's program: the arch tool follows; every other operation is exactly A's.
        ops_b = _program_ops(self.repository, run_b)
        radius, upper, jambs = _arch_measures(ops_b)
        self.assertAlmostEqual(radius, 1.6 / 2)
        self.assertAlmostEqual(upper, 2.3 + CUT_MARGIN - SPRING)
        self.assertEqual(jambs, (ARCH_ALONG - 0.8, ARCH_ALONG + 0.8))
        moved = {"passage-wall-void-passage-arch-cylinder", "passage-wall-void-passage-arch-upper", "passage-wall-void-passage-arch-legs"}
        self.assertEqual(set(ops_a), set(ops_b))
        self.assertEqual({op_id for op_id in ops_a if ops_a[op_id] != ops_b[op_id]}, moved)
        self.assertEqual(ops_b["passage-wall"], ops_a["passage-wall"])
        self.assertEqual(ops_b["portico-base"], ops_a["portico-base"])
        self.assertEqual(ops_b["portico-cornice"], ops_a["portico-cornice"])
        candidate_b = self.client.get(f"/api/candidates/{run_b}").json()
        self.assertEqual((candidate_b["relationChecks"]["held"], candidate_b["relationChecks"]["violated"]), (2, 0))
        self.assertIn("entity:passage-wall", candidate_b["honesty"][0])

        # A is still A, the project moved nothing, and both stay candidates.
        self.assertEqual(_load_kind(self.repository, run_a, "state-record"), record_a)
        self.assertEqual(self.repository.read_head(), before_head)
        for path, content in authored.items():
            self.assertEqual(self.repository.layout.resolve_relative(path).read_bytes(), content)
        self.assertEqual(self.client.get("/api/state").json()["referenceRun"]["runId"], REFERENCE_RUN_ID)

        # A new process holds no proposal or job and reads both candidates off their retained records.
        restarted = TestClient(create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)))
        self.addCleanup(restarted.close)
        for run_id, width, head in ((run_a, 2.0, 2.5), (run_b, 1.6, 2.3)):
            with self.subTest(run=run_id):
                reopened = restarted.get(f"/api/candidates/{run_id}")
                self.assertEqual(reopened.status_code, 200, reopened.text)
                self.assertEqual((reopened.json()["status"], reopened.json()["jobId"], reopened.json()["proposalId"]), ("succeeded", None, None))
                state = restarted.get("/api/state", params={"run": run_id}).json()
                self.assertEqual(state["referenceRun"]["runId"], run_id)
                self.assertTrue(state["matchesReferenceReceipt"])
                reread = {p["key"]: p for p in state["parameters"]}
                self.assertEqual((reread["passage_width"]["value"], reread["passage_head"]["value"]), (width, head))
                self.assertEqual(reread["passage_width"]["expr"], "maintenance_clearance + 0.4")
                self.assertEqual(reread["plinth"]["lockAuthority"], "client")
                wall_row = next(e for e in state["elements"] if e["elementId"] == "passage-wall")
                self.assertEqual(wall_row["numericFields"], {"height": 3, "thickness": 0.3})
        self.assertEqual(restarted.get("/api/state").json()["referenceRun"]["runId"], REFERENCE_RUN_ID)

    def test_the_chain_is_refused_where_it_must_be_before_any_candidate_runs(self) -> None:
        run_a, state_a = self.candidate_a(self.app, self.client)
        source = dict(source_run_id=run_a, state_digest=state_a["stateDigest"], element_id="passage-wall", utterance="Change the maintenance clearance.")

        def clearance_edit(**changes: object) -> dict:
            edit = semantic_wall_edit()
            edit.update(summary="Move the clearance.", entities=[], parameters=[{"key": "maintenance_clearance", "value": 1.0, "unit": "m"}], protected=[], kept=[])
            edit.update(changes)
            return edit

        unsafe = "__import__(" + "'os').system('x')"
        for label, edit, fragment in (
            ("an expression outside the closed grammar", clearance_edit(parameters=[{"key": "passage_width", "value": 2, "unit": "m", "expr": unsafe, "inputs": []}]), "unexpected character"),
            ("a function the evaluator does not have", clearance_edit(parameters=[{"key": "passage_width", "value": 2, "unit": "m", "expr": "pow(maintenance_clearance, 2)", "inputs": []}]), "unknown function 'pow'"),
            ("a cycle through the chain", clearance_edit(parameters=[{"key": "maintenance_clearance", "value": 1, "unit": "m", "expr": "passage_head - 1", "inputs": []}]), "cycle among parameters"),
            ("a locked parameter", clearance_edit(parameters=[{"key": "plinth", "value": 0.7, "unit": "m"}]), "reaches locked parameters: parameter:plinth (client)"),
        ):
            with self.subTest(refused=label):
                status, body = self.compile(self.app, self.client, edit, **source)
                self.assertEqual(status, 422, body)
                self.assertEqual(body["code"], "SEMANTIC_EDIT_INVALID")
                self.assertIn(fragment, body["detail"])

        # A protection the chain reaches is kept for review, and never run.
        status, body = self.compile(self.app, self.client, clearance_edit(protected=["parameter:passage_head"]), **source)
        self.assertEqual(status, 201, body)
        self.assertEqual(body["proposal"]["status"], "conflict")
        self.assertEqual(body["proposal"]["impact"]["conflicts"], ["parameter:passage_head"])
        refused = self.client.post(f"/api/proposals/{body['proposal']['proposalId']}/candidate")
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "PROPOSAL_NOT_RUNNABLE")

        # A's exact base: the reference run's digest is not A's, and the agent is never asked.
        compiler = scripted(semantic_edit=clearance_edit(), component_id="portico")
        self.app.state.intent_compiler = compiler
        stale = self.client.post("/api/intents", json={"stateDigest": self.state_digest, "sourceRunId": run_a, "targetComponentId": "portico", "elementId": "passage-wall", "utterance": "Change the maintenance clearance."})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "STALE_BASE")
        self.assertEqual(compiler.calls, [])

        # The scalar grammar names the new source instead of writing over what follows it.
        derived = self.client.post("/api/proposals", json={"stateDigest": state_a["stateDigest"], "sourceRunId": run_a, "targetComponentId": "portico", "utterance": "set passage_width to 1.5 m"})
        self.assertEqual(derived.status_code, 422, derived.text)
        self.assertEqual(derived.json()["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertIn("set maintenance_clearance (= 1.6 m) instead", derived.json()["question"])
        bound = self.client.post("/api/proposals", json={"stateDigest": state_a["stateDigest"], "sourceRunId": run_a, "targetComponentId": "portico", "elementId": "passage-wall", "utterance": "set thickness to 0.5"})
        self.assertEqual(bound.status_code, 422, bound.text)
        self.assertIn("bound to parameter passage_thickness", bound.json()["question"])

        # Nothing above left a run behind.
        self.assertEqual(sorted(p.name for p in self.repository.layout.runs.iterdir()), sorted([REFERENCE_RUN_ID, run_a]))

    @unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
    def test_the_exported_solids_follow_the_chain(self) -> None:
        """The same two candidates with the process's default export: the STEP aperture is the opening the chain says."""

        app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        self.assertTrue(app.state.settings.exports)
        client = TestClient(app)
        self.addCleanup(client.close)
        run_a, state_a = self.candidate_a(app, client)
        run_b, _ = self.candidate_b(app, client, run_a, state_a)

        def solids(run_id: str) -> dict[str, occt_backend.ShapeMeasure]:
            candidate = client.get(f"/api/candidates/{run_id}").json()
            exact = next(row for row in candidate["artifacts"] if row["representation"] == "exact")
            self.assertEqual((exact["format"], exact["status"], exact["readbackVerified"]), ("step", "succeeded", True))
            fetched = client.get(f"/api/artifacts/{exact['sha256']}/bytes")
            self.assertEqual(fetched.status_code, 200, fetched.text)
            path = self.root / f"{run_id}.step"
            path.write_bytes(fetched.content)
            return {entry.name: occt_backend.measure_shape(entry.shape) for entry in occt_backend.read_step(path, length_unit="meter")}

        measured = {run_a: solids(run_a), run_b: solids(run_b)}
        for run_id, width, head in ((run_a, 2.0, 2.5), (run_b, 1.6, 2.3)):
            with self.subTest(run=run_id):
                shapes = measured[run_id]
                self.assertEqual(set(shapes), {"obj-passage-wall-cut", "obj-passage-wall-aperture-passage-arch", "obj-portico-base", "obj-portico-cornice"})
                aperture = shapes["obj-passage-wall-aperture-passage-arch"]
                self.assertTrue(aperture.valid and aperture.closed and aperture.solid_count == 1)
                # CAD frame is (thickness x, along y, up z): the jambs, the crown and the volume of a semicircular arch of that width.
                self.assertAlmostEqual(aperture.bbox_max[1] - aperture.bbox_min[1], width, places=6)
                self.assertAlmostEqual(aperture.bbox_min[1], ARCH_ALONG - width / 2, places=6)
                self.assertAlmostEqual(aperture.bbox_max[2], head, places=6)
                self.assertAlmostEqual(aperture.volume, 0.3 * (width * SPRING + math.pi * (width / 2) ** 2 / 2), places=6)
                cut = shapes["obj-passage-wall-cut"]
                self.assertAlmostEqual(cut.volume, 4.0 * 0.3 * WALL_HEIGHT - aperture.volume, places=6)
        for name in ("obj-portico-base", "obj-portico-cornice"):
            self.assertEqual(measured[run_a][name], measured[run_b][name])
        self.assertAlmostEqual(measured[run_a]["obj-portico-base"].volume, 4.0 * 2.0 * 0.6, places=6)


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

    def test_the_deterministic_compiler_passes_the_sentence_through(self) -> None:
        compilation = DeterministicCompiler().compile(
            message="set height to 0.8", selection=Selection("portico", None), projection=None  # type: ignore[arg-type]
        )
        self.assertEqual(compilation.status, "compiled")
        self.assertEqual(compilation.utterance, "set height to 0.8")
        self.assertEqual(compilation.component_id, "portico")


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


def benchmark_prompt(scenario: str) -> str:
    """The real manual benchmark's words, read from the config it is run with.

    Read rather than copied: what these assertions are about is how *that* text
    compiles, and a second copy here would keep passing after the benchmark it
    claims to be about had changed.
    """

    root = Path(__file__).resolve().parents[4]
    config = json.loads((root / "tests/monkeymonitor/benchmarks.json").read_text(encoding="utf-8"))
    return next(row["prompt"] for row in config["scenarios"] if row["id"] == scenario)


class ContextPackTests(IntentTestCase):
    """``POST /api/intents/context``: one named source and focus, read once.

    The deterministic context compiler does run; no model provider does. The
    agent seat holds ``Failing``, which raises the moment it is asked, so a pack
    that came back at all is a pack no model was called for.
    """

    def setUp(self) -> None:
        super().setUp()
        self.app.state.intent_compiler = Failing()
        self.head = self.repository.layout.head.read_bytes()

    def pack(self, utterance: str, **body: object) -> tuple[int, dict]:
        body.setdefault("projectId", PROJECT_ID)
        body.setdefault("sourceRunId", REFERENCE_RUN_ID)
        body.setdefault("stateDigest", self.state_digest)
        body.setdefault("targetComponentId", "portico")
        body.setdefault("elementId", "portico-cornice")
        body["utterance"] = utterance
        response = self.client.post("/api/intents/context", json=body)
        return response.status_code, response.json()

    def test_advertised_reading_edit_survives_candidate_and_cold_task_context(self) -> None:
        from jsonschema import Draft202012Validator
        from archflow_studio_api.transport.proposal import SemanticEditRequestDto

        reading = {
            "entity_id": "entry-condition", "schema": "Reading@1", "parent_id": "portico",
            "basis_refs": ["studio:intent"],
            "fields": {"note": "Keep the south entry and courtyard route open; this is a design condition, not Stage approval.",
                       "subject_refs": ["entity:portico-base", "entity:portico-cornice"],
                       "source_ref": "studio:intent"},
        }
        edit = {"summary": "Retain the entry condition for later wall work.", "entities": [reading]}
        # The agent must be able to discover the write through the very schema
        # exposed by studio_schema; an accepted undocumented payload is not enough.
        Draft202012Validator(SemanticEditRequestDto.model_json_schema()).validate(edit)
        proposed = self.client.post("/api/proposals", json={
            "projectId": PROJECT_ID, "sourceRunId": REFERENCE_RUN_ID,
            "stateDigest": self.state_digest, "semanticEdit": edit,
        })
        self.assertEqual(proposed.status_code, 201, proposed.text)
        started = self.client.post(f"/api/proposals/{proposed.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(_finished(self.client, started.json()["jobId"])["status"], "succeeded")
        candidate_id = started.json()["candidateId"]
        record = _load_kind(self.repository, candidate_id, "state-record")
        saved = next(row for row in record["entities"] if row["entity_id"] == "entry-condition")
        self.assertEqual(saved["fields"], reading["fields"])
        self.assertEqual(saved["basis_refs"], reading["basis_refs"])

        self.client.close()
        cold_app = create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        cold_app.state.intent_compiler = Failing()
        with TestClient(cold_app) as cold:
            state = cold.get("/api/state", params={"run": candidate_id}).json()
            response = cold.post("/api/intents/context", json={
                "projectId": PROJECT_ID, "sourceRunId": candidate_id,
                "stateDigest": state["stateDigest"], "utterance": "Add the courtyard walls.",
                "elementIds": ["portico-base", "portico-cornice"],
            })
            self.assertEqual(response.status_code, 200, response.text)
            facts = response.json()["context"]
            self.assertEqual(next(row for row in facts["readings"] if row["entity_id"] == "entry-condition"), saved)
        self.assertEqual(self.repository.layout.head.read_bytes(), self.head)

    def test_the_named_source_and_focus_answer_a_pack_with_no_compiler_and_no_write(self) -> None:
        status, payload = self.pack("Raise portico-cornice height to 0.5 m.")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["contextPack"], "ContextPack@1")
        # The exact base it was read against, not a nearby one.
        self.assertEqual(payload["source"]["projectId"], PROJECT_ID)
        self.assertEqual(payload["source"]["runId"], REFERENCE_RUN_ID)
        self.assertEqual(payload["source"]["stateDigest"], self.state_digest)
        self.assertTrue(payload["source"]["exactSource"])
        self.assertTrue(payload["source"]["actionable"])
        self.assertIn(f'sourceRunId="{REFERENCE_RUN_ID}"', payload["source"]["writeWith"])
        self.assertEqual(payload["target"]["componentId"], "portico")
        self.assertEqual(payload["target"]["elementId"], "portico-cornice")
        # The registered entry is about the capability, not about this project,
        # and is not what was asked for here.
        self.assertNotIn("capability", payload)
        # Reading changed nothing the project stands on.
        self.assertEqual(self.repository.layout.head.read_bytes(), self.head)

    def test_whole_and_multi_element_tasks_do_not_invent_a_single_target(self) -> None:
        for focuses in ({}, {"elementIds": ["portico-base", "portico-cornice"]}):
            with self.subTest(focuses=focuses):
                response = self.client.post("/api/intents/context", json={
                    "projectId": PROJECT_ID, "sourceRunId": REFERENCE_RUN_ID,
                    "stateDigest": self.state_digest, "utterance": "Explore their spatial relation", **focuses,
                })
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["contextTier"], "design")
                self.assertEqual(payload["context"]["focusElementIds"], focuses.get("elementIds", []))
                self.assertIsNone(payload["target"])
                self.assertIsNone(payload["request"])
                self.assertIn("portico-base", {row["elementId"] for row in payload["context"]["elements"]})
        self.assertEqual(self.repository.layout.head.read_bytes(), self.head)

    def test_exact_element_alone_resolves_only_its_recorded_component(self) -> None:
        status, payload = self.pack("set portico-cornice height to 0.5", targetComponentId=None)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["target"]["componentId"], "portico")
        self.assertEqual(payload["contextTier"], "scalar")

    def test_context_request_refuses_conflicting_focuses_and_unknown_supplements(self) -> None:
        for body in (
            {"elementIds": ["portico-base"]},
            {"elementId": None, "elementIds": ["portico-base", "portico-base"]},
            {"contextRefs": ["entity:unknown"]},
            {"contextOffset": -1},
        ):
            with self.subTest(body=body):
                status, payload = self.pack("Reorganize the design", **body)
                self.assertEqual(status, 422, payload)
        status, payload = self.pack("Reorganize the design", elementId=None,
                                    elementIds=["portico-base", "unknown"])
        self.assertEqual(status, 404, payload)
        self.assertEqual(payload["code"], "ELEMENT_UNKNOWN")

    def test_omitted_run_never_selects_an_existing_candidate(self) -> None:
        status, payload = self.pack("Review the whole design", sourceRunId=None,
                                    targetComponentId=None, elementId=None)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "SOURCE_RUN_REQUIRED")

    def test_initialized_empty_modeling_state_needs_no_run_or_fake_focus(self) -> None:
        from archflow.project.repository import FilesystemProjectRepository
        from archflow.state.state_record import StateRecord

        root = self.root / "empty-context"
        repository = FilesystemProjectRepository.initialize(
            root, project_id="empty-context", initial_state={"project_id": "empty-context", "version": 0},
            authored_record=StateRecord(project_id="empty-context", run_id="authored", entities=()).to_dict(),
        )
        with TestClient(create_app(StudioSettings(project_dir=root, cad_export="off"))) as client:
            initialized = client.post("/api/project/modeling", json={"projectId": "empty-context"})
            self.assertEqual(initialized.status_code, 200, initialized.text)
            state = client.get("/api/state").json()
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            response = client.post("/api/intents/context", json={
                "projectId": "empty-context", "stateDigest": state["stateDigest"],
                "utterance": "Explore three masses around a courtyard",
            })
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["context"]["elements"], [])
            self.assertEqual(payload["context"]["focusElementIds"], [])
            self.assertIsNone(payload["target"])
            self.assertIsNone(payload["request"])
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
            self.assertEqual(list(repository.layout.runs.iterdir()), [])

    def test_explicit_lock_candidate_cold_context_refuses_bypass_and_explicit_unlock_restores_editing(self) -> None:
        from archflow.state.state_record import StateRecord
        from archflow_studio_api.application.candidate import replay_candidate

        head = self.repository.read_head()

        def run_lock(client, source, keys, action):
            proposed = client.post("/api/proposals/parameter-locks", json={
                "projectId": PROJECT_ID, "sourceRunId": source["referenceRun"]["runId"],
                "stateDigest": source["stateDigest"], "parameterKeys": keys, "action": action,
            })
            self.assertEqual(proposed.status_code, 201, proposed.text)
            started = client.post(f"/api/proposals/{proposed.json()['proposalId']}/candidate")
            self.assertEqual(started.status_code, 202, started.text)
            job = _finished(client, started.json()["jobId"])
            self.assertEqual(job["status"], "succeeded", job)
            return started.json()["candidateId"]

        source = self.client.get(f"/api/state?run={REFERENCE_RUN_ID}").json()
        locked_run = run_lock(self.client, source, ["module"], "lock")
        original = StateRecord.from_dict(_load_kind(self.repository, REFERENCE_RUN_ID, "state-record"))
        saved = StateRecord.from_dict(_load_kind(self.repository, locked_run, "state-record"))
        self.assertEqual(saved.entities, original.entities)
        self.assertEqual(saved.parameter("module").lock_authority, "studio:explicit-user-action")
        self.assertEqual(self.repository.read_head(), head)

        self.client.close()
        app = create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        with TestClient(app) as cold:
            self.assertEqual(replay_candidate(bound_project(app.state), locked_run).digest, saved.digest)
            state = cold.get(f"/api/state?run={locked_run}").json()
            request = {"projectId": PROJECT_ID, "sourceRunId": locked_run, "stateDigest": state["stateDigest"]}
            context = cold.post("/api/intents/context", json={**request, "utterance": "Continue the design while preserving locked dimensions"})
            self.assertEqual(context.status_code, 200, context.text)
            parameters = {row["key"]: row for row in context.json()["context"]["parameters"]}
            self.assertEqual(parameters["module"]["lockAuthority"], "studio:explicit-user-action")
            scalar = cold.post("/api/proposals", json={**request, "targetComponentId": "portico", "utterance": "set module to 1.5"})
            self.assertEqual(scalar.status_code, 422, scalar.text)
            # No chat or proposal store is needed to read and unlock this exact candidate.
            unlocked_run = run_lock(cold, state, ["module"], "unlock")
            unlocked = cold.get(f"/api/state?run={unlocked_run}").json()
            edited = cold.post("/api/proposals", json={
                "projectId": PROJECT_ID, "sourceRunId": unlocked_run, "stateDigest": unlocked["stateDigest"],
                "targetComponentId": "portico", "utterance": "set module to 1.5",
            })
            self.assertEqual(edited.status_code, 201, edited.text)
            self.assertIsNone(StateRecord.from_dict(_load_kind(self.repository, unlocked_run, "state-record")).parameter("module").lock_authority)
            self.assertEqual(self.repository.read_head(), head)

    def test_cold_context_reopens_exact_design_facts_and_continues_without_chat_history(self) -> None:
        from .support import add_later_run

        request = {
            "projectId": PROJECT_ID, "sourceRunId": REFERENCE_RUN_ID, "stateDigest": self.state_digest,
            "utterance": "Adjust the cornice while preserving the existing base and locked project dimensions",
        }
        original = self.client.post("/api/intents/context", json=request)
        self.assertEqual(original.status_code, 200, original.text)
        original_facts = original.json()["context"]
        source_record = _load_kind(self.repository, REFERENCE_RUN_ID, "state-record")
        authored = {path: self.repository.layout.resolve_relative(path).read_bytes()
                    for path in (RUNNER_RECORD_PATH, RUNNER_SEATS_PATH)}
        # A newer eligible run makes an accidental implicit-latest read visible.
        newer = add_later_run(self.repository, run_id="newer-context-design")
        self.client.close()
        cold_app = create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        cold_app.state.intent_compiler = Failing()
        with TestClient(cold_app) as cold:
            self.assertEqual(cold.get("/api/state").json()["referenceRun"]["runId"], newer.run_id)
            reopened = cold.post("/api/intents/context", json=request)
            self.assertEqual(reopened.status_code, 200, reopened.text)
            pack = reopened.json()
            self.assertEqual(pack["source"]["runId"], REFERENCE_RUN_ID)
            self.assertEqual(pack["source"]["stateDigest"], self.state_digest)
            self.assertEqual(pack["context"], original_facts)
            facts = pack["context"]
            self.assertTrue(facts["coverage"]["complete"])
            locked = next(row for row in facts["parameters"] if row["key"] == "plinth")
            self.assertEqual((locked["value"], locked["lockAuthority"]), (0.6, "client"))
            cornice = next(row for row in facts["elements"] if row["elementId"] == "portico-cornice")
            self.assertEqual(cornice["references"]["base"], {"datum": "portico-base-top"})
            self.assertIn("rel-cornice-on-base", {row["relation_id"] for row in facts["relationships"]})

            proposed = cold.post("/api/proposals", json={
                "stateDigest": pack["source"]["stateDigest"], "sourceRunId": pack["source"]["runId"],
                "targetComponentId": "portico", "elementId": "portico-cornice", "utterance": "set height to 0.5",
                "keep": ["entity:portico-base", "parameter:plinth"],
            })
            self.assertEqual(proposed.status_code, 201, proposed.text)
            proposal = proposed.json()
            self.assertEqual(proposal["sourceRunId"], REFERENCE_RUN_ID)
            self.assertEqual(proposal["baseStateDigest"], self.state_digest)
            started = cold.post(f"/api/proposals/{proposal['proposalId']}/candidate")
            self.assertEqual(started.status_code, 202, started.text)
            self.assertEqual(_finished(cold, started.json()["jobId"])["status"], "succeeded")
            result = _load_kind(self.repository, started.json()["candidateId"], "state-record")
            entities = {row["entity_id"]: row for row in result["entities"]}
            self.assertEqual(entities["portico-cornice"]["fields"]["params"]["height"], 0.5)
            self.assertEqual(entities["portico-base"], next(row for row in source_record["entities"]
                                                         if row["entity_id"] == "portico-base"))
            self.assertEqual(next(row for row in result["parameters"] if row["key"] == "plinth")["lock_authority"], "client")
        self.assertEqual(_load_kind(self.repository, REFERENCE_RUN_ID, "state-record"), source_record)
        self.assertEqual(self.repository.layout.head.read_bytes(), self.head)
        for path, data in authored.items():
            self.assertEqual(self.repository.layout.resolve_relative(path).read_bytes(), data)

    def test_the_request_is_a_template_of_the_current_values_not_of_the_asked_change(self) -> None:
        status, payload = self.pack("set portico-cornice height to 0.5")
        self.assertEqual(status, 200, payload)
        # One named control with a number: the narrow reading, and no model.
        self.assertEqual(payload["contextTier"], "scalar")
        self.assertEqual(payload["escalation"], [])
        self.assertIsNone(payload["preflight"])
        self.assertEqual(payload["request"]["path"], "/api/capabilities/candidate.modify_existing/run")
        body = payload["request"]["body"]
        self.assertEqual(body["projectId"], PROJECT_ID)
        self.assertEqual(body["sourceRunId"], REFERENCE_RUN_ID)
        self.assertEqual(body["stateDigest"], self.state_digest)
        self.assertEqual(body["targetComponentId"], "portico")
        self.assertEqual(body["elementId"], "portico-cornice")
        # 0.3 is what the record holds; 0.5 is what was asked. The template
        # holds the first, so nothing here reads as an approved change.
        self.assertEqual(body["utterance"], "set height to 0.3")
        height = next(row for row in payload["target"]["editable"] if row["field"] == "height")
        self.assertEqual(height["value"], 0.3)
        # The capability's own reading of the unit, including where it declares
        # none: a prism's height has none, and none is invented here.
        self.assertIsNone(height["unit"])
        self.assertTrue(any("values this element has now" in line for line in payload["honesty"]))

    def test_the_benchmark_words_keep_their_design_reading_and_preservation_context(self) -> None:
        prompt = benchmark_prompt("incremental-edit").replace("{state_digest}", self.state_digest)
        status, payload = self.pack(prompt)
        self.assertEqual(status, 200, payload)
        # It names several objects and "all other authored fields": the existing
        # compiler widens to the design tier, and this route does not narrow it
        # back into a scalar request nobody made.
        self.assertEqual(payload["contextTier"], "design")
        self.assertIn("architectural_or_extended_scope", payload["escalation"])
        elements = {row["elementId"] for row in payload["context"]["elements"]}
        # What must not change is still readable beside what may.
        self.assertIn("portico-base", elements)
        self.assertIn("portico-cornice", elements)
        self.assertIn(
            "rel-cornice-on-base",
            {row["relation_id"] for row in payload["context"]["relationships"]},
        )
        self.assertIn("entity:portico-base", payload["keep"]["accepted"])
        # A wider reading is not a refusal: the element's current numbers and
        # the template that holds them are still here.
        self.assertIsNone(payload["preflight"])
        self.assertEqual(payload["request"]["body"]["elementId"], "portico-cornice")

    def test_another_project_a_stale_digest_and_an_unknown_source_are_each_refused(self) -> None:
        for name, body, status, code in (
            ("another project", {"projectId": "somebody-elses-project"}, 403, "PROJECT_MISMATCH"),
            ("a stale digest", {"stateDigest": "0" * 64}, 409, "STALE_BASE"),
            ("an unknown source", {"sourceRunId": "run-that-was-never-made"}, 404, "RUN_NOT_FOUND"),
        ):
            with self.subTest(name):
                got, payload = self.pack("set portico-cornice height to 0.5", **body)
                self.assertEqual(got, status, payload)
                self.assertEqual(payload["code"], code)
        self.assertEqual(self.repository.layout.head.read_bytes(), self.head)

    def test_an_unknown_focus_is_named_and_never_replaced(self) -> None:
        for name, body, status, code in (
            ("an unknown component", {"targetComponentId": "not-a-component"}, 404, "TARGET_UNKNOWN"),
            ("an unknown element", {"elementId": "not-an-element"}, 404, "ELEMENT_UNKNOWN"),
        ):
            with self.subTest(name):
                got, payload = self.pack("set the height to 0.5", **body)
                self.assertEqual(got, status, payload)
                self.assertEqual(payload["code"], code)
                # It says what this record does declare, rather than choosing.
                self.assertIn("portico", payload["detail"])

    def test_an_obstacle_the_record_already_answers_advertises_no_runnable_request(self) -> None:
        from unittest.mock import patch

        from archflow_studio_api.routes import intents as route

        # The shape the record's own preflight answers a locked control with.
        # No fixture here binds an element field to a locked or derived
        # parameter, so the obstacle is supplied and its composition checked.
        blocked = {"status": "unsupported", "targetComponentId": "portico",
                   "elementId": "portico-cornice", "utterance": None, "semanticEdit": None,
                   "why": "The selected dimension is locked. Its lock must be released "
                          "before it can be changed.",
                   "question": None, "contextRefs": []}
        with patch.object(route, "action_preflight", return_value=blocked):
            status, payload = self.pack("set portico-cornice height to 0.5")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["preflight"], blocked)
        # The record answers this without a model, and a request that would run
        # straight past that answer is not offered at all.
        self.assertIsNone(payload["request"])
        self.assertTrue(any("No executable request is offered" in line for line in payload["honesty"]))
        # What the element actually is remains readable.
        self.assertEqual(payload["target"]["elementId"], "portico-cornice")

    def test_an_element_of_another_component_is_refused_rather_than_reassigned(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        repository, digest = make_portico_project(root)
        client = TestClient(create_app(StudioSettings(cad_export="off", project_dir=root / PROJECT_ID)))
        self.addCleanup(client.close)
        head = repository.layout.head.read_bytes()
        response = client.post("/api/intents/context", json={
            "utterance": "set the height to 0.5", "projectId": PROJECT_ID,
            "sourceRunId": REFERENCE_RUN_ID, "stateDigest": digest,
            # The abutment is under portico-roofs; naming its grandparent is two
            # selections that disagree, not a narrower one.
            "targetComponentId": "portico", "elementId": "portico-roof-abutment-west",
        })
        self.assertEqual(response.status_code, 409, response.text)
        payload = response.json()
        self.assertEqual(payload["code"], "ELEMENT_COMPONENT_MISMATCH")
        self.assertIn("portico-roofs", payload["detail"])
        self.assertEqual(repository.layout.head.read_bytes(), head)
