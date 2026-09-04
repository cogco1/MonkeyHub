"""The exchange that used to loop, and the four answers that end it.

The dialogue these tests reproduce is the real one: "raise the left colonnade a
little" against a selection sitting on the roofs, then "no — the columns", then
"supply the field", and the studio asking the same question forever because
every message arrived as a new independent request and every refusal had thrown
away the target the last one worked out.

Nothing here is a mock. The project is a real P036 project with a record shaped
like the villa's — a portico, roofs with a west abutment that carries
``height``, and a column set that carries no element at all — and the agent seam
is either absent (the deterministic pass-through) or a scripted compiler
answering exactly what a model would have answered. Every one of the four
outcomes is reached without a model, which is the point: the resolver is the
contract, not the agent.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application import clarification
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.clarification import (
    editable_descendants,
    kinds_in,
    property_in,
    resolve,
)
from archflow_studio_api.application.intent_agent import CODEX, Compilation, Selection
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, make_portico_project, write_runner_record

ROOFS = "portico-roofs"
COLUMNS = "portico-columns"
ABUTMENT = "portico-roof-abutment-west"


def scripted(**fields: object):
    """A compiler that answers one fixed compilation, whatever it is asked."""

    class Scripted:
        calls: list[dict] = []

        def compile(self, *, message, selection, projection):
            Scripted.calls.append({"message": message, "selection": selection})
            base = dict(
                status="compiled",
                provider=CODEX,
                model="scripted",
                utterance=None,
                component_id=None,
                element_id=None,
                why="",
                question=None,
                latency_ms=3,
                prompt_sha256="ab" * 32,
                raw="{}",
            )
            base.update(fields)
            return Compilation(**base)

    return Scripted()


class PorticoTestCase(unittest.TestCase):
    """One project, one client, and one helper that speaks like the client."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, self.state_digest = make_portico_project(self.root)
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def say(self, utterance: str, **body: object) -> tuple[int, dict]:
        body.setdefault("stateDigest", self.state_digest)
        body["utterance"] = utterance
        response = self.client.post("/api/intents", json=body)
        return response.status_code, response.json()

    def projection(self):
        with self.client:
            self.client.get("/api/state")
            return project_state(bound_project(self.app.state))


class TheRecordSaysWhatIsEditable(PorticoTestCase):
    """The fixture is the reproduction, so it is worth stating what it holds."""

    def test_the_column_set_is_declared_and_has_no_editable_element(self) -> None:
        projection = self.projection()
        declared = {
            entity.entity_id
            for entity in projection.record.entities_of("Component@1")
        }
        self.assertIn(COLUMNS, declared)
        self.assertEqual(editable_descendants(projection, COLUMNS), ())

    def test_the_roofs_carry_the_west_abutments_height(self) -> None:
        projection = self.projection()
        under_roofs = editable_descendants(projection, ROOFS)
        self.assertEqual([e.element_id for e in under_roofs], [ABUTMENT])
        self.assertEqual(under_roofs[0].numeric_fields["height"], 0.45)


class WordsBecomeKinds(unittest.TestCase):
    """A noun names a kind of thing, and one kind is never another."""

    def test_a_colonnade_is_not_a_column_and_a_portico_door_is_not_a_door(self) -> None:
        self.assertEqual(kinds_in("把左侧柱廊略微提高"), frozenset({"colonnade"}))
        self.assertEqual(kinds_in("不是，是柱子"), frozenset({"column"}))
        # 门 is inside 门廊: the longer word wins the characters it covers.
        self.assertEqual(kinds_in("门廊"), frozenset({"colonnade"}))
        self.assertEqual(kinds_in("门"), frozenset({"door"}))

    def test_an_identifier_carries_every_kind_its_words_name(self) -> None:
        self.assertEqual(kinds_in("portico-columns"), frozenset({"colonnade", "column"}))
        self.assertEqual(kinds_in(ABUTMENT), frozenset({"colonnade", "roof", "abutment"}))

    def test_the_quality_is_read_from_the_words_not_from_a_field_name(self) -> None:
        self.assertEqual(property_in("把左侧柱廊略微提高"), "height")
        self.assertEqual(property_in("make it a little taller"), "height")
        self.assertEqual(property_in("不是，是柱子"), None)


class TargetCorrection(PorticoTestCase):
    """1. The architect says "no, the columns", and the studio moves."""

    def test_the_first_sentence_asks_rather_than_guessing(self) -> None:
        status, payload = self.say(
            "把左侧柱廊略微提高", targetComponentId=ROOFS
        )
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertEqual(payload["outcome"], "NEEDS_CLARIFICATION")
        pending = payload["pendingIntent"]
        self.assertIsNotNone(pending["continuationToken"])
        self.assertEqual(pending["originalUtterance"], "把左侧柱廊略微提高")
        self.assertEqual(pending["requestedSemanticProperty"], "height")
        # 左侧 is relative to a viewpoint and no camera came with the request,
        # so it is a slot the architect fills and it narrows nothing.
        self.assertIn("orientation", pending["missingSlots"])

    def test_the_correction_moves_the_target_and_rejects_what_was_offered(self) -> None:
        _, first = self.say("把左侧柱廊略微提高", targetComponentId=ROOFS)
        token = first["pendingIntent"]["continuationToken"]

        status, payload = self.say(
            "不是，是柱子",
            targetComponentId=ROOFS,  # the client's selection is still stale
            continuationToken=token,
        )
        self.assertEqual(status, 422, payload)
        pending = payload["pendingIntent"]
        # The target is corrected atomically, in the same answer that rejects
        # what it replaced. The client reads targetComponentId and moves its
        # selection; nothing about the old one survives.
        self.assertEqual(pending["targetComponentId"], COLUMNS)
        self.assertIn(f"component:{ROOFS}", pending["rejectedCandidates"])
        # And the wrong answer the old resolver reached is not recommended:
        # not as the target, and not as a candidate.
        self.assertNotEqual(pending["elementId"], ABUTMENT)
        self.assertEqual(pending["candidates"], [])
        for candidate in pending["candidates"]:
            self.assertNotEqual(candidate["elementId"], ABUTMENT)
        # It exists in the model and has no control: that is the answer, not a
        # question about which field to use.
        self.assertEqual(payload["outcome"], "MISSING_EDITABLE_CONTROL")
        self.assertEqual(payload["code"], "MISSING_EDITABLE_CONTROL")
        self.assertEqual(pending["reasonCode"], "COMPONENT_HAS_NO_EDITABLE_ELEMENT")
        # The abutment's height is named once, to be refused.
        self.assertIn(f"{ABUTMENT}.height", payload["detail"])
        self.assertIn("not a substitute", payload["detail"])

    def test_the_correction_never_asks_the_architect_for_an_element_id(self) -> None:
        _, first = self.say("把左侧柱廊略微提高", targetComponentId=ROOFS)
        _, second = self.say(
            "不是，是柱子",
            targetComponentId=ROOFS,
            continuationToken=first["pendingIntent"]["continuationToken"],
        )
        self.assertNotIn("question", second)
        for leak in ("elementId", "element id", "grammar"):
            self.assertNotIn(leak, second["detail"])


class ComponentWithNoElement(PorticoTestCase):
    """2. A component that exists in the model and has no control."""

    def test_a_direct_request_is_terminal_and_makes_no_proposal(self) -> None:
        status, payload = self.say("set height to 2.0", targetComponentId=COLUMNS)
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "MISSING_EDITABLE_CONTROL")
        self.assertEqual(payload["outcome"], "MISSING_EDITABLE_CONTROL")
        self.assertEqual(
            payload["pendingIntent"]["reasonCode"],
            "COMPONENT_HAS_NO_EDITABLE_ELEMENT",
        )
        self.assertEqual(payload["pendingIntent"]["targetComponentId"], COLUMNS)
        self.assertEqual(payload["pendingIntent"]["requestedSemanticProperty"], "height")
        self.assertNotIn("proposal", payload)
        # Terminal: there is nothing left to ask, so there is no token to
        # answer with and the client shows no input box.
        self.assertIsNone(payload["pendingIntent"]["continuationToken"])

    def test_the_draft_names_where_the_suggestion_was_read_from(self) -> None:
        _, payload = self.say("set height to 2.0", targetComponentId=COLUMNS)
        draft = payload["authoredControlDraft"]
        self.assertEqual(draft["targetComponentId"], COLUMNS)
        self.assertEqual(draft["semanticProperty"], "height")
        self.assertEqual(draft["producer"], "prism")
        self.assertIn(draft["confidence"], ("high", "medium", "low"))
        self.assertTrue(draft["provenance"])
        self.assertTrue(
            any("producer prism" in line for line in draft["provenance"]),
            draft["provenance"],
        )
        self.assertTrue(draft["dependencyRequirements"])

    def test_the_resolver_answers_before_the_agent_is_asked(self) -> None:
        """A component with no control never reaches a model at all.

        An agent shown the whole record sheet would find the nearest element
        whose field happens to be called ``height`` and offer that. So it is
        not asked: the record already answers.
        """

        compiler = scripted(utterance="set height to 2.0", component_id=ABUTMENT)
        self.app.state.intent_compiler = compiler
        status, payload = self.say("raise the columns", targetComponentId="portico")
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "MISSING_EDITABLE_CONTROL")
        self.assertEqual(payload["pendingIntent"]["targetComponentId"], COLUMNS)
        self.assertEqual(compiler.calls, [])

    def test_the_agent_may_not_route_into_it_either(self) -> None:
        """A compiler that names the column set gets the same terminal answer."""

        self.app.state.intent_compiler = scripted(
            utterance="set height to 2.0", component_id=COLUMNS, element_id=None
        )
        status, payload = self.say("raise it", targetComponentId="portico")
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "MISSING_EDITABLE_CONTROL")
        self.assertEqual(payload["pendingIntent"]["targetComponentId"], COLUMNS)

    def test_nothing_is_written_to_the_project(self) -> None:
        before = sorted(
            path.name
            for path in self.repository.layout.run("run-001").records.glob("*.json")
        )
        self.say("set height to 2.0", targetComponentId=COLUMNS)
        self.say("补充 portico-columns 的字段")
        after = sorted(
            path.name
            for path in self.repository.layout.run("run-001").records.glob("*.json")
        )
        self.assertEqual(before, after)


class DeclareTheMissingControl(PorticoTestCase):
    """3. "Supply the field" is an action, and it is not a value change."""

    def test_it_is_classified_as_declare_missing_control(self) -> None:
        status, payload = self.say("补充 portico-columns 的字段")
        self.assertEqual(status, 422, payload)
        pending = payload["pendingIntent"]
        self.assertEqual(pending["actionKind"], "declare_missing_control")
        self.assertEqual(pending["targetComponentId"], COLUMNS)
        self.assertEqual(pending["reasonCode"], "CONTROL_MUST_BE_AUTHORED")
        self.assertEqual(payload["outcome"], "MISSING_EDITABLE_CONTROL")

    def test_it_never_enters_the_scalar_grammar(self) -> None:
        compiler = scripted(utterance="set height to 1.0", component_id="portico")
        self.app.state.intent_compiler = compiler
        status, payload = self.say("补充 portico-columns 的字段")
        self.assertEqual(status, 422, payload)
        self.assertEqual(compiler.calls, [])
        self.assertNotIn("acceptedForms", payload)

    def test_it_answers_with_a_draft_and_asks_nothing_again(self) -> None:
        _, payload = self.say("补充 portico-columns 的字段")
        self.assertIn("authoredControlDraft", payload)
        self.assertIsNone(payload["pendingIntent"]["continuationToken"])
        self.assertNotIn("question", payload)

    def test_english_says_it_too(self) -> None:
        _, payload = self.say(
            "add a field for the portico-columns", targetComponentId=COLUMNS
        )
        self.assertEqual(
            payload["pendingIntent"]["actionKind"], "declare_missing_control"
        )

    def test_the_original_request_carries_its_property_into_the_draft(self) -> None:
        _, first = self.say("把左侧柱廊略微提高", targetComponentId=ROOFS)
        status, payload = self.say(
            "补充字段", continuationToken=first["pendingIntent"]["continuationToken"]
        )
        self.assertEqual(status, 422, payload)
        self.assertEqual(
            payload["pendingIntent"]["actionKind"], "declare_missing_control"
        )
        self.assertEqual(payload["authoredControlDraft"]["semanticProperty"], "height")


class ClarificationAdvancesOrTerminates(PorticoTestCase):
    """4. Every reply narrows something, or the exchange stops."""

    def test_the_same_reply_twice_terminates_instead_of_asking_again(self) -> None:
        _, first = self.say("make the portico taller", targetComponentId="portico")
        self.assertEqual(first["outcome"], "NEEDS_CLARIFICATION")
        token = first["pendingIntent"]["continuationToken"]
        self.assertIsNotNone(token)

        status, second = self.say(
            "make the portico taller",
            targetComponentId="portico",
            continuationToken=token,
        )
        self.assertEqual(status, 422, second)
        self.assertEqual(second["outcome"], "UNSUPPORTED")
        self.assertEqual(second["code"], "UNSUPPORTED_REQUEST")
        self.assertEqual(
            second["pendingIntent"]["reasonCode"], "CLARIFICATION_MADE_NO_PROGRESS"
        )
        self.assertIsNone(second["pendingIntent"]["continuationToken"])
        self.assertNotIn("question", second)
        self.assertIn("missing", second["detail"])

    def test_a_reply_that_narrows_the_target_keeps_the_exchange_open(self) -> None:
        _, first = self.say("make the portico taller", targetComponentId="portico")
        status, second = self.say(
            "the cornice",
            targetComponentId="portico",
            elementId="portico-cornice",
            continuationToken=first["pendingIntent"]["continuationToken"],
        )
        # Naming the element finished the target; only the value is left, and
        # the deterministic seam still cannot read one out of "the cornice".
        self.assertEqual(status, 422, second)
        self.assertEqual(second["outcome"], "NEEDS_CLARIFICATION")
        self.assertEqual(second["pendingIntent"]["elementId"], "portico-cornice")
        self.assertEqual(second["pendingIntent"]["turn"], 2)

    def test_a_spent_token_cannot_be_replayed(self) -> None:
        _, first = self.say("make the portico taller", targetComponentId="portico")
        token = first["pendingIntent"]["continuationToken"]
        self.say("set height to 0.9", elementId="portico-base",
                 targetComponentId="portico", continuationToken=token)
        status, payload = self.say(
            "make the portico taller",
            targetComponentId="portico",
            continuationToken=token,
        )
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "STALE_CLARIFICATION")


class StaleClarification(PorticoTestCase):
    """5. A pending intent is bound to a stateDigest, and dies with it."""

    def test_a_pending_intent_is_void_when_the_record_moves(self) -> None:
        _, first = self.say("make the portico taller", targetComponentId="portico")
        token = first["pendingIntent"]["continuationToken"]

        # The record changes underneath: a new digest, a new projection.
        moved = dict(clarification_payload())
        write_runner_record(self.repository, moved)
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.text)
        digest = response.json()["stateDigest"]
        self.assertNotEqual(digest, self.state_digest)

        status, payload = self.say(
            "a little taller",
            stateDigest=digest,
            targetComponentId="portico",
            continuationToken=token,
        )
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "STALE_CLARIFICATION")
        self.assertIn("void", payload["detail"])

    def test_an_unknown_token_is_told_so_rather_than_being_answered(self) -> None:
        status, payload = self.say(
            "a little taller",
            targetComponentId="portico",
            continuationToken="pi-nothing-remembers-this",
        )
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "STALE_CLARIFICATION")


class TheGrammarStillWorks(PorticoTestCase):
    """6. Nothing above may cost a sentence that already worked."""

    def test_set_height_to_a_number(self) -> None:
        status, payload = self.say(
            "set height to 2.0", targetComponentId="portico", elementId="portico-base"
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["outcome"], "COMPILED")
        self.assertEqual(payload["proposal"]["change"]["new"], 2.0)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-base")
        self.assertIsNone(payload["pendingIntent"]["continuationToken"])

    def test_increase_by_a_percentage(self) -> None:
        status, payload = self.say(
            "increase height by 5 %",
            targetComponentId=ROOFS,
        )
        # One legal editable descendant is not a choice: it is the answer.
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], ABUTMENT)
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 0.4725)

    def test_the_proposal_is_still_proposal_only_and_kept_for_a_later_get(self) -> None:
        _, payload = self.say(
            "set height to 2.0", targetComponentId="portico", elementId="portico-base"
        )
        proposal_id = payload["proposal"]["proposalId"]
        self.assertEqual(
            self.client.get(f"/api/proposals/{proposal_id}").status_code, 200
        )
        operator = payload["proposal"]["decisionOperator"]
        self.assertIn("studio:proposal-only", str(operator))


class TheResolverAnswersWithoutAnAgent(PorticoTestCase):
    """Every one of the four outcomes, reached with no model in the process."""

    def test_all_four(self) -> None:
        projection = self.projection()
        compiled = resolve(
            projection,
            utterance="set height to 2.0",
            selection=Selection("portico", "portico-base"),
        )
        self.assertEqual(compiled.outcome, clarification.COMPILED)

        asks = resolve(
            projection,
            utterance="raise the dome",
            selection=Selection(None, None),
        )
        self.assertEqual(asks.outcome, clarification.NEEDS_CLARIFICATION)

        missing = resolve(
            projection,
            utterance="raise the columns",
            selection=Selection(None, None),
        )
        self.assertEqual(missing.outcome, clarification.MISSING_EDITABLE_CONTROL)
        self.assertEqual(missing.pending.target_component_id, COLUMNS)

        stalled = resolve(
            projection,
            utterance="raise the dome",
            selection=Selection(None, None),
            pending=asks.pending,
        )
        self.assertEqual(stalled.outcome, clarification.UNSUPPORTED)
        self.assertEqual(
            {answer.outcome for answer in (compiled, asks, missing, stalled)},
            set(clarification.OUTCOMES),
        )


def clarification_payload() -> dict[str, object]:
    """The portico record with one number moved: a different state, same shape."""

    from .support import PORTICO_RECORD_PAYLOAD

    entities = []
    for entity in PORTICO_RECORD_PAYLOAD["entities"]:  # type: ignore[index]
        if entity.get("entity_id") == "portico-base":
            fields = dict(entity["fields"])
            params = dict(fields["params"])
            params["height"] = 0.7
            fields["params"] = params
            entities.append({**entity, "fields": fields})
        else:
            entities.append(entity)
    return {**PORTICO_RECORD_PAYLOAD, "entities": entities}


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
