"""How far a change reaches, and a number that is not the element's to move.

Two seams, one record. The record is the villa's problem with the stack put
back in: the west columns carry capitals, the capitals carry an entablature,
and every one of those is an element of its own component, exactly as the model
has it. That is what makes "把西边的柱子提高 0.1m" ambiguous in the way an
architect means it — this column, the three things stacked on the datum it
starts from, or everything on that datum — and the studio asks rather than
picking one quietly.

The second seam is the number nobody here can move. A wall whose ``top`` names
a level does not own its height: the level does. A window whose ``top`` names
the beam above it does not own its height either — the beam does, and the beam
*is* editable, so the answer names the control that actually moves it. Neither
answer compiles anything and neither reaches the agent: a change typed against
a derived control is one the kernel refuses afterwards, and the honest place to
say so is before the model is asked.

Nothing in this file is villa data. The record is synthetic and small enough to
read; the objects and the project scaffolding are the ones the catalog tests
already build.
"""

from __future__ import annotations

import copy
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, SEAT_3DM_INSPECTION

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    EVIDENCE,
    PROJECT_ID,
    RECORD_PAYLOAD,
    make_project,
    run_records,
    write_runner_record,
)
from .test_clarification_catalog import (
    ABUTMENT_OBJECTS,
    CAMERA,
    COLUMN_OBJECTS,
    PROJECT_MD,
    ROOF_OBJECTS,
    component,
)

# The west stack, as the question has to name it.
WEST_STACK = [
    "portico-columns-west",
    "portico-capitals-west",
    "portico-entablature-west",
]


def prism(entity_id, component_id, height, *, base, top=None):
    """One element, with the references the kernel reads its geometry from.

    ``base`` and ``top`` are the record's own reference shapes — ``{"level":
    ...}`` for a level and ``{"datum": "<element>-top"}`` for the top another
    element publishes. An element that declares a ``top`` does not own its
    height: the reference does, and the catalog says so.
    """

    references = {"base": base}
    if top is not None:
        references["top"] = top
    return {
        "entity_id": entity_id,
        "schema": "Element@1",
        "parent_id": component_id,
        "fields": {
            "component_id": component_id,
            "producer": "prism",
            "references": references,
            "params": {
                "profile": [[0, 0], [1, 0], [1, 1], [0, 1]],
                "height": height,
            },
        },
        "basis_refs": [EVIDENCE],
    }


def level(entity_id, role, elevation):
    return {
        "entity_id": entity_id,
        "schema": "Level@1",
        "fields": {"role": role, "elevation": elevation},
        "basis_refs": [EVIDENCE],
    }


GROUND = {"level": "level-ground"}


def stacked_record() -> dict:
    """The villa's shape with its stack, its derived wall and its derived window."""

    payload = copy.deepcopy(RECORD_PAYLOAD)
    payload["entities"].extend(
        [
            level("level-eaves", "eaves", 6.0),
            component("porticos", "building", "arrival-and-buttress"),
            component("portico-columns", "porticos", "vertical-support"),
            component("portico-capitals", "porticos", "support"),
            component("portico-entablature", "porticos", "cover"),
            component("portico-roofs", "porticos", "roof"),
            component("portico-roof-abutments", "portico-roofs", "buttress"),
            component("portico-dome", "porticos", "cover"),
            component("portico-floors", "porticos", "support"),
            component("portico-beams", "porticos", "cover"),
            component("portico-walls", "porticos", "enclosure"),
            component("portico-windows", "porticos", "opening"),
            # The two columns, on the ground datum with the abutments.
            prism("portico-columns-west", "portico-columns", 9.798, base=GROUND),
            prism("portico-columns-east", "portico-columns", 9.798, base=GROUND),
            prism("portico-roof-abutment-west", "portico-roof-abutments", 1.873, base=GROUND),
            prism("portico-roof-abutment-east", "portico-roof-abutments", 1.873, base=GROUND),
            # The stack the west columns carry: capitals on the columns, an
            # entablature on the capitals. Each seats on the top the one below
            # publishes, which is the edge the kernel reads and the scope
            # options are built from.
            prism(
                "portico-capitals-west",
                "portico-capitals",
                0.62,
                base={"datum": "portico-columns-west-top"},
            ),
            prism(
                "portico-entablature-west",
                "portico-entablature",
                1.24,
                base={"datum": "portico-capitals-west-top"},
            ),
            # A leaf: nothing seats on the dome, and it is alone on its datum.
            prism(
                "portico-dome-west",
                "portico-dome",
                2.4,
                base={"datum": "portico-roof-abutment-east-top"},
            ),
            # The east bay: a floor, a beam on it, and a window whose head
            # follows the beam. The window's height is the beam's business.
            prism("portico-floor-east", "portico-floors", 0.2, base=GROUND),
            prism(
                "portico-beam-east",
                "portico-beams",
                0.8,
                base={"datum": "portico-floor-east-top"},
            ),
            prism(
                "portico-window-east",
                "portico-windows",
                1.5,
                base={"datum": "portico-floor-east-top"},
                top={"datum": "portico-beam-east-top"},
            ),
            # A wall that runs from the ground to the eaves: its height is the
            # level's, and a level declares no element control in this record.
            prism(
                "portico-wall-west",
                "portico-walls",
                6.0,
                base=GROUND,
                top={"level": "level-eaves"},
            ),
        ]
    )
    return payload


class NoAgent:
    """A compiler that fails the test if the seam ever reaches it."""

    provider = "never"
    model = None
    calls: list[str]

    def __init__(self) -> None:
        self.calls = []

    def compile(self, *, message, selection, projection):
        self.calls.append(message)
        raise AssertionError(f"the agent was asked to compile {message!r}")


class StackedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        write_runner_record(self.repository, stacked_record())
        (self.root / PROJECT_ID / "PROJECT.md").write_text(PROJECT_MD, encoding="utf-8")
        run_id = "inspected-001"
        run = self.repository.create_run(run_id)
        objects = COLUMN_OBJECTS + ABUTMENT_OBJECTS + ROOF_OBJECTS
        ref = self.repository.put_json(
            run=run,
            destination=run_records(run_id),
            record_kind=SEAT_3DM_INSPECTION,
            payload={
                "schema": "RhinoCadInspection@2",
                "named_object_bboxes": [o["bbox"] for o in objects],
                "object_geometry_sha256": [o["sha"] for o in objects],
                "object_user_strings": [o["strings"] for o in objects],
                "object_count": len(objects),
                "document_user_strings": [
                    {"key": "archflow:length_unit", "value": "meter"}
                ],
            },
        )
        self.repository.put_json(
            run=run,
            destination=run_records(run_id),
            record_kind=RUNNER_RUN_RECEIPT,
            payload={
                "schema": "RunnerRunReceipt@3",
                "project_id": PROJECT_ID,
                "run_id": run_id,
                "seat_execution_complete": True,
                "seat_results": [
                    {
                        "seat_id": "seat-portico",
                        "status": "proposal_accepted",
                        "objects": len(objects),
                        "cad": {
                            "status": "succeeded",
                            "path": "portico.3dm",
                            "inspection_ref": ref.uri,
                        },
                    }
                ],
            },
        )
        self.app = create_app(
            StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=run_id)
        )
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]

    def ask(self, utterance: str, **body):
        body.setdefault("stateDigest", self.state_digest)
        body["utterance"] = utterance
        response = self.client.post("/api/intents", json=body)
        return response.status_code, response.json()

    def refuse_the_agent(self) -> NoAgent:
        """Put a compiler in the seam that fails if the resolver ever calls it."""

        agent = NoAgent()
        self.app.state.intent_compiler = agent
        return agent


class ScopeIsAStepTests(StackedTestCase):
    def test_a_stack_makes_how_far_a_question_with_three_readings(self) -> None:
        status, payload = self.ask("把西边的柱子提高 0.1m", camera=CAMERA)
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["outcome"], "NEEDS_CLARIFICATION")
        pending = payload["pendingIntent"]
        self.assertEqual(pending["reasonCode"], "SCOPE_UNRESOLVED")
        self.assertEqual(pending["missingSlots"], ["scope"])
        self.assertEqual(pending["elementId"], "portico-columns-west")
        self.assertEqual(
            [option["ref"] for option in pending["candidates"]],
            ["scope:element", "scope:stack", "scope:datum"],
        )
        options = {item["scope"]: item for item in pending["scopeOptions"]}
        self.assertEqual(sorted(options), ["datum", "element", "stack"])
        self.assertEqual(options["element"]["elementIds"], ["portico-columns-west"])
        self.assertEqual(options["stack"]["elementIds"], WEST_STACK)
        self.assertIn("portico-columns-east", options["datum"]["elementIds"])
        self.assertNotIn("portico-capitals-west", options["datum"]["elementIds"])
        self.assertIsNotNone(pending["continuationToken"])

    def test_the_scope_field_settles_it_and_the_change_is_the_element_alone(self) -> None:
        status, payload = self.ask("把西边的柱子提高 0.1m", camera=CAMERA, scope="element")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["scope"]["scope"], "element")
        self.assertEqual(
            payload["proposal"]["scope"]["elementIds"], ["portico-columns-west"]
        )
        self.assertEqual(payload["pendingIntent"]["knownSlots"]["scope"], "element")
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 9.898)

    def test_only_this_one_in_words_settles_it_the_same_way(self) -> None:
        _, asked = self.ask("把西边的柱子提高 0.1m", camera=CAMERA)
        status, payload = self.ask(
            "只这个",
            continuationToken=asked["pendingIntent"]["continuationToken"],
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(
            payload["proposal"]["scope"]["elementIds"], ["portico-columns-west"]
        )

    def test_the_whole_stack_widens_the_coverage_and_still_moves_one_scalar(self) -> None:
        _, asked = self.ask("把西边的柱子提高 0.1m", camera=CAMERA)
        status, payload = self.ask(
            "整个叠层",
            continuationToken=asked["pendingIntent"]["continuationToken"],
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["scope"]["scope"], "stack")
        self.assertEqual(payload["proposal"]["scope"]["elementIds"], WEST_STACK)
        # The coverage widened; the change did not. One element, one scalar.
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-columns-west")
        self.assertEqual(payload["proposal"]["change"]["old"], 9.798)
        self.assertAlmostEqual(payload["proposal"]["change"]["new"], 9.898)
        self.assertEqual(payload["proposal"]["utterance"], "set height to 9.898")

    def test_a_datum_word_is_not_read_as_the_elevation_property(self) -> None:
        _, asked = self.ask("把西边的柱子提高 0.1m", camera=CAMERA)
        status, payload = self.ask(
            "整条标高",
            continuationToken=asked["pendingIntent"]["continuationToken"],
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["scope"]["scope"], "datum")
        self.assertIn("portico-columns-east", payload["proposal"]["scope"]["elementIds"])
        # 标高 is the property table's word for an elevation; consumed as a
        # scope it must not turn the request into one about another quality.
        self.assertEqual(payload["proposal"]["target"]["key"], "height")

    def test_a_leaf_with_no_stack_and_its_own_datum_is_asked_nothing(self) -> None:
        status, payload = self.ask("把穹顶提高 0.1m", camera=CAMERA)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["proposal"]["target"]["elementId"], "portico-dome-west")
        self.assertEqual(payload["proposal"]["scope"]["scope"], "element")
        # One reading is not a choice: it is the answer, and nothing was asked.
        self.assertEqual(
            [item["scope"] for item in payload["pendingIntent"]["scopeOptions"]],
            ["element"],
        )

    def test_a_reply_that_settles_nothing_stops_instead_of_asking_again(self) -> None:
        _, asked = self.ask("把西边的柱子提高 0.1m", camera=CAMERA)
        status, payload = self.ask(
            "嗯",
            continuationToken=asked["pendingIntent"]["continuationToken"],
        )
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["outcome"], "UNSUPPORTED")
        self.assertEqual(
            payload["pendingIntent"]["reasonCode"], "CLARIFICATION_MADE_NO_PROGRESS"
        )


class DerivedControlTests(StackedTestCase):
    def test_a_height_the_level_owns_is_terminal_and_names_the_level(self) -> None:
        agent = self.refuse_the_agent()
        status, payload = self.ask("把西边的墙提高 0.1m", camera=CAMERA)
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["outcome"], "MISSING_EDITABLE_CONTROL")
        self.assertEqual(payload["pendingIntent"]["reasonCode"], "CONTROL_IS_DERIVED")
        self.assertIn("derived from entity:level-eaves", payload["detail"])
        self.assertIn("level-eaves", payload["detail"])
        self.assertIn("Level@1", payload["detail"])
        draft = payload["authoredControlDraft"]
        self.assertEqual(draft["suggestedAction"], "move level level-eaves")
        # Terminal: nothing left to ask, and the agent was never reached.
        self.assertIsNone(payload["pendingIntent"]["continuationToken"])
        self.assertEqual(agent.calls, [])

    def test_a_height_another_element_owns_offers_that_element_s_control(self) -> None:
        agent = self.refuse_the_agent()
        status, payload = self.ask("把东边的窗户提高 0.1m", camera=CAMERA)
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["outcome"], "NEEDS_CLARIFICATION")
        pending = payload["pendingIntent"]
        self.assertEqual(pending["reasonCode"], "CONTROL_IS_DERIVED")
        self.assertIn(
            "height of portico-window-east is derived from "
            "entity:portico-beam-east; change portico-beam-east instead",
            payload["detail"],
        )
        self.assertEqual(
            [option["ref"] for option in pending["candidates"]],
            ["element:portico-beam-east.height"],
        )
        self.assertEqual(pending["candidates"][0]["currentValue"], 0.8)
        self.assertIsNotNone(pending["continuationToken"])
        self.assertEqual(agent.calls, [])

    def test_the_catalog_says_derived_with_the_ref_that_pins_it(self) -> None:
        catalog = self.client.get("/api/state").json()["catalog"]
        by_element = {item["elementId"]: item for item in catalog["elements"]}
        window = by_element["portico-window-east"]["capabilities"][0]
        self.assertEqual(window["status"], "derived")
        self.assertEqual(window["source"], "derived from entity:portico-beam-east")
        beam = by_element["portico-beam-east"]["capabilities"][0]
        self.assertEqual(beam["status"], "editable")
        self.assertEqual(beam["source"], "authored")


if __name__ == "__main__":
    unittest.main()
