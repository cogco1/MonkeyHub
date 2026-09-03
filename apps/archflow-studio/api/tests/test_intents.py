"""An architect's sentence, compiled by an agent, typed by the grammar.

The agent is a seam: ``app.state.intent_compiler``. These tests put scripted
compilers there — real objects implementing the port, answering what a model
would have answered — and everything after the seam is real: the projection,
the deterministic grammar, the kernel's closure, the proposal store. What is
proved is the contract around the agent, not the agent.
"""

from __future__ import annotations

from pathlib import Path
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
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest


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
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
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

    def test_an_agent_naming_an_element_the_record_lacks_is_the_grammars_question(self) -> None:
        self.app.state.intent_compiler = scripted(
            utterance="set height to 0.5", component_id="portico", element_id="portico-attic"
        )
        status, payload = self.ask("raise the attic")
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertIn("portico-attic", payload["question"])

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

    def test_an_agent_that_claims_to_compile_but_does_not_gets_the_forms(self) -> None:
        self.app.state.intent_compiler = scripted(
            utterance="lift it a bit", component_id="portico", element_id="portico-base"
        )
        status, payload = self.ask("lift it")
        self.assertEqual(status, 422, payload)
        self.assertIn("is not in the grammar", payload["detail"])
        self.assertEqual(len(payload["acceptedForms"]), 4)

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


class AnswerParsingTests(unittest.TestCase):
    def test_a_fenced_json_answer_is_read(self) -> None:
        raw = '```json\n{"status":"compiled","targetComponentId":"portico","elementId":"portico-base","utterance":"set height to 0.8","why":"","question":null}\n```'
        compilation = _parse_answer(raw, provider=CODEX, model=None, latency_ms=1, prompt_sha="00" * 32)
        self.assertEqual(compilation.utterance, "set height to 0.8")
        self.assertEqual(compilation.element_id, "portico-base")

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

        self.app.state.intent_compiler = CodexCompiler(executable="codex", model="gpt-5")
        response = self.client.get("/api/project")
        self.assertEqual(response.json()["intentProvider"], "codex")
        self.assertEqual(response.json()["intentModel"], "gpt-5")
