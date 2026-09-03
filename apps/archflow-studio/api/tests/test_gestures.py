"""Gestures on the model: circle, arrow, keep and remove marks, read into
facts through the record and carried beside the sentence.

The client sends strokes and the objects under them; nothing it sends is
believed about the record. These tests hand ``POST /api/intents`` hits in the
export's own vocabulary (``archflow:component``, ``obj-<element>`` names) and
check what the server made of them: the keep clause it added, the selection a
circle implied, the sentences it put on the agent's sheet, and what it said
about a hit it could not name.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.gestures import direction_words
from archflow_studio_api.application.intent import merge_keep
from archflow_studio_api.application.intent_agent import (
    CODEX,
    Compilation,
    Selection,
    record_sheet,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest

CAMERA = {"position": [8, -8, 6], "target": [0, 0, 0], "up": [0, 0, 1], "fov": 38}


def hit(object_name: str, component: str = "portico") -> dict:
    return {
        "objectName": object_name,
        "userStrings": {
            "archflow:component": component,
            "archflow:object_ref": f"cad-object:{object_name}",
        },
        "world": [1.0, 2.0, 0.3],
    }


def gesture(kind: str, *hits: dict, **extra: object) -> dict:
    body: dict = {"kind": kind, "screen": [[10, 10], [40, 40]], "camera": CAMERA, "hits": list(hits)}
    body.update(extra)
    return body


class Scripted:
    """A compiler that answers one sentence and remembers what it was shown."""

    def __init__(self, utterance: str, element_id: str | None = None) -> None:
        self.utterance = utterance
        self.element_id = element_id
        self.calls: list[dict] = []

    def compile(self, *, message, selection, projection):
        self.calls.append({"message": message, "selection": selection})
        return Compilation(
            status="compiled",
            provider=CODEX,
            model="scripted",
            utterance=self.utterance,
            component_id="portico",
            element_id=self.element_id,
            why="read the arrow as up",
            question=None,
            latency_ms=3,
            prompt_sha256="cd" * 32,
            raw="{}",
        )


class GestureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)

    def ask(self, utterance: str, gestures: list[dict], **body: object) -> tuple[int, dict]:
        body.setdefault("stateDigest", self.state_digest)
        body["utterance"] = utterance
        body["gestures"] = gestures
        response = self.client.post("/api/intents", json=body)
        return response.status_code, response.json()


class KeepMarkTests(GestureTestCase):
    def test_a_keep_mark_becomes_a_keep_clause_the_record_can_check(self) -> None:
        status, payload = self.ask(
            "set height to 0.8",
            [gesture("keep", hit("obj-portico-cornice"))],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(
            payload["agent"]["compiledUtterance"],
            "set height to 0.8 keep entity:portico-cornice",
        )
        self.assertEqual(payload["proposal"]["protected"], ["entity:portico-cornice"])
        self.assertEqual(
            payload["gestures"],
            ["keep mark on portico-cornice → keep entity:portico-cornice"],
        )

    def test_a_keep_mark_on_the_target_itself_is_the_grammar_s_conflict(self) -> None:
        status, payload = self.ask(
            "set height to 0.8",
            [gesture("keep", hit("obj-portico-base"))],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["status"], "conflict")

    def test_a_keep_mark_that_resolves_to_no_element_protects_nothing(self) -> None:
        status, payload = self.ask(
            "set height to 0.8",
            [gesture("keep", hit("obj-portico-frieze-0"))],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["protected"], [])
        self.assertEqual(
            payload["gestures"],
            [
                "keep mark on component portico · no element resolved, so the "
                "grammar cannot protect it"
            ],
        )


class CircleTests(GestureTestCase):
    def test_a_circle_with_no_pick_is_the_selection(self) -> None:
        status, payload = self.ask(
            "set height to 0.8",
            [gesture("circle", hit("obj-portico-base"), hit("obj-portico-base-1"))],
            targetComponentId=None,
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["componentId"], "portico")
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-base")
        self.assertEqual(
            payload["gestures"], ["circle covering portico (1 element: portico-base)"]
        )

    def test_a_circle_over_two_elements_names_the_component_only(self) -> None:
        status, payload = self.ask(
            "a little taller",
            [gesture("circle", hit("obj-portico-base"), hit("obj-portico-cornice"))],
            targetComponentId=None,
        )
        # No element was singled out, and the compiler (deterministic here)
        # cannot type "a little taller": the question is the grammar's.
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "BLOCKED_NEEDS_HUMAN")

    def test_a_pick_wins_over_a_circle(self) -> None:
        compiler = Scripted("set height to 0.8", element_id="portico-cornice")
        self.app.state.intent_compiler = compiler
        status, payload = self.ask(
            "raise the cornice",
            [gesture("circle", hit("obj-portico-base"))],
            targetComponentId="portico",
            elementId="portico-cornice",
        )
        self.assertEqual(status, 201, payload)
        selection = compiler.calls[-1]["selection"]
        self.assertEqual(selection.element_id, "portico-cornice")
        self.assertEqual(
            selection.gestures, ("circle covering portico (1 element: portico-base)",)
        )


class ArrowTests(GestureTestCase):
    def test_an_arrow_is_a_sentence_on_the_agent_s_sheet(self) -> None:
        compiler = Scripted("increase height by 10 %", element_id="portico-base")
        self.app.state.intent_compiler = compiler
        status, payload = self.ask(
            "a little more, like this",
            [
                gesture(
                    "arrow",
                    hit("obj-portico-base"),
                    worldStart=[1, 2, 0],
                    worldEnd=[1, 2, 0.4],
                    worldDirection=[0, 0, 1],
                    lengthModelUnits=0.4,
                )
            ],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        fact = (
            "arrow on portico-base (portico) · world direction +Z (up) "
            "(0.00, 0.00, 1.00) · length ≈ 0.4 model units"
        )
        self.assertEqual(payload["gestures"], [fact])
        selection = compiler.calls[-1]["selection"]
        self.assertEqual(selection.gestures, (fact,))
        binding = bound_project(self.app.state)
        sheet = record_sheet(project_state(binding), selection)
        self.assertEqual(sheet["gestures"], [fact])
        self.assertEqual(payload["proposal"]["change"]["new"], 0.66)

    def test_an_arrow_over_nothing_says_so(self) -> None:
        compiler = Scripted("increase height by 10 %", element_id="portico-base")
        self.app.state.intent_compiler = compiler
        status, payload = self.ask(
            "up a bit",
            [gesture("arrow", worldDirection=[0, 0, -1])],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(
            payload["gestures"],
            [
                "arrow over nothing the record names · world direction -Z (down) "
                "(0.00, 0.00, -1.00) · length unknown"
            ],
        )


class RemoveAndUnresolvedTests(GestureTestCase):
    def test_a_remove_mark_is_named_and_not_typed(self) -> None:
        status, payload = self.ask(
            "set height to 0.8",
            [gesture("remove", hit("obj-portico-cornice"))],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(
            payload["gestures"],
            [
                "remove mark on portico-cornice (portico) · the grammar has no "
                "form that removes; ask before proposing"
            ],
        )
        self.assertEqual(payload["proposal"]["protected"], [])

    def test_a_hit_the_record_cannot_name_is_reported_not_guessed(self) -> None:
        status, payload = self.ask(
            "set height to 0.8",
            [gesture("keep", hit("obj-tower-top", component="tower"))],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["protected"], [])
        self.assertEqual(
            payload["gestures"],
            [
                "keep mark on nothing the record names",
                "keep hit on obj-tower-top did not resolve (unknown_component)",
            ],
        )

    def test_many_unnamed_hits_are_one_sentence(self) -> None:
        hits = [hit(f"obj-tower-{i}", component="tower") for i in range(6)]
        status, payload = self.ask(
            "set height to 0.8",
            [gesture("circle", *hits)],
            targetComponentId="portico",
            elementId="portico-base",
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(
            payload["gestures"],
            [
                "circle over nothing the record names",
                "circle: 6 hits did not resolve (unknown_component) · obj-tower-0, "
                "obj-tower-1, obj-tower-2, +3 more",
            ],
        )

    def test_no_gestures_is_the_old_answer(self) -> None:
        status, payload = self.ask(
            "set height to 0.8", [], targetComponentId="portico", elementId="portico-base"
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["gestures"], [])


class MergeKeepTests(unittest.TestCase):
    def test_a_sentence_without_a_clause_gains_one(self) -> None:
        self.assertEqual(
            merge_keep("set height to 0.8", ["entity:a"]), "set height to 0.8 keep entity:a"
        )

    def test_existing_refs_stay_and_missing_ones_are_added_in_order(self) -> None:
        self.assertEqual(
            merge_keep("set height to 0.8 keep entity:a", ["entity:b", "entity:a"]),
            "set height to 0.8 keep entity:a, entity:b",
        )

    def test_a_malformed_clause_is_left_for_the_grammar_to_refuse(self) -> None:
        self.assertEqual(merge_keep("set height to 0.8 keep a,", ["entity:b"]), "set height to 0.8 keep a,")

    def test_no_refs_changes_nothing(self) -> None:
        self.assertEqual(merge_keep("set height to 0.8", []), "set height to 0.8")


class DirectionWordsTests(unittest.TestCase):
    def test_the_dominant_axis_is_named_and_z_gets_its_word(self) -> None:
        self.assertEqual(
            direction_words((0.1, 0.0, 0.9)), "world direction +Z (up) (0.10, 0.00, 0.90)"
        )
        self.assertEqual(
            direction_words((-0.8, 0.2, 0.0)), "world direction -X (-0.80, 0.20, 0.00)"
        )

    def test_nothing_is_said_about_a_zero_or_missing_direction(self) -> None:
        self.assertEqual(direction_words(None), "direction unknown")
        self.assertEqual(direction_words((0.0, 0.0, 0.0)), "direction unknown")


if __name__ == "__main__":
    unittest.main()
