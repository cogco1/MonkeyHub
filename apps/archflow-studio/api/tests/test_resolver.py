"""The two-stage resolver against a record that looks like the villa's problem:
a porticos subtree whose columns the model shows and the catalog does not, and
two abutments the catalog holds - one west, one east.

Every case here is one of the interactions the lane must pass: the columns
never resolve to the abutment's height; "a little" is a question, not a
percentage; "not the roof, the columns" drops the roof; a component without a
catalog entry is named MODEL_VISIBLE_CATALOG_MISSING.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, SEAT_3DM_INSPECTION

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.catalog import MODEL_VISIBLE_CATALOG_MISSING, catalog_of
from archflow_studio_api.application.intent_agent import Selection
from archflow_studio_api.application.pending import (
    AMBIGUOUS_TARGET,
    MISSING_AMOUNT,
    MISSING_ELEMENT_DECLARATION,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.application.resolver import (
    CANDIDATES,
    CHANGE_EXISTING_VALUE,
    CLARIFY,
    DECLARE_MISSING_CONTROL,
    MISSING,
    RESOLVED,
    affirmed_after_negation,
    negations_in,
    resolve_action,
    resolve_target,
    validate_agent_result,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import EVIDENCE, PROJECT_ID, RECORD_PAYLOAD, make_project, run_records, write_runner_record

SHA = "b" * 64
COMPASS = {"west": (-1.0, 0.0), "east": (1.0, 0.0), "north": (0.0, 1.0), "south": (0.0, -1.0)}
# Standing south of the building, looking north: left is west.
CAMERA = {"position": [0, -50, 10], "target": [0, 0, 0], "up": [0, 0, 1], "fov": 38}
ALIASES = {"柱子": "portico-columns", "柱廊": "porticos", "屋顶": "portico-roofs", "columns": "portico-columns", "roof": "portico-roofs"}


def component(entity_id, parent_id, kind):
    return {"entity_id": entity_id, "schema": "Component@1", "parent_id": parent_id,
            "fields": {"component_id": entity_id, "semantic_kind": kind, "intent": entity_id, "source_refs": [EVIDENCE]},
            "basis_refs": [EVIDENCE]}


def prism(entity_id, component_id, height):
    return {"entity_id": entity_id, "schema": "Element@1", "parent_id": component_id,
            "fields": {"component_id": component_id, "producer": "prism",
                       "references": {"base": {"level": "level-ground"}},
                       "params": {"profile": [[0, 0], [1, 0], [1, 1], [0, 1]], "height": height}},
            "basis_refs": [EVIDENCE]}


def obj(name, component, producer):
    return {
        "bbox": {"name": name, "bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}, "layer_path": f"archflow::{component}", "type": "Brep"},
        "sha": {"name": name, "geometry_sha256": SHA, "layer_path": f"archflow::{component}", "type": "Brep"},
        "strings": {"name": name, "layer_path": f"archflow::{component}", "attributes": [
            {"key": "archflow:component", "value": component},
            {"key": "archflow:producer_op", "value": producer},
            {"key": "archflow:object_ref", "value": f"cad-object:{name}"},
        ]},
    }


COLUMN_OBJECTS = [obj(f"obj-column-{side}-{i}", "portico-columns", f"column-{side}-{i}") for side in ("west", "east") for i in range(2)]
ABUTMENT_OBJECTS = [obj(f"obj-portico-roof-abutment-{side}", "portico-roof-abutments", f"portico-roof-abutment-{side}") for side in ("west", "east")]
ROOF_OBJECTS = [obj(f"obj-portico-roof-{side}-0", "portico-roofs", f"portico-roof-{side}-0") for side in ("west", "east")]


def villa_like_record(*, columns_have_elements: bool) -> dict:
    payload = copy.deepcopy(RECORD_PAYLOAD)
    entities = payload["entities"]
    entities.extend([
        component("porticos", "building", "arrival-and-buttress"),
        component("portico-columns", "porticos", "vertical-support"),
        component("portico-roofs", "porticos", "cover"),
        component("portico-roof-abutments", "portico-roofs", "buttress"),
        prism("portico-roof-abutment-west", "portico-roof-abutments", 1.873),
        prism("portico-roof-abutment-east", "portico-roof-abutments", 1.873),
    ])
    if columns_have_elements:
        entities.extend([
            prism("portico-columns-west", "portico-columns", 9.798),
            prism("portico-columns-east", "portico-columns", 9.798),
        ])
    return payload


class ResolverTestCase(unittest.TestCase):
    columns_have_elements = False

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        write_runner_record(self.repository, villa_like_record(columns_have_elements=self.columns_have_elements))
        run_id = "inspected-001"
        run = self.repository.create_run(run_id)
        objects = COLUMN_OBJECTS + ABUTMENT_OBJECTS + ROOF_OBJECTS
        ref = self.repository.put_json(run=run, destination=run_records(run_id), record_kind=SEAT_3DM_INSPECTION, payload={
            "schema": "RhinoCadInspection@2",
            "named_object_bboxes": [o["bbox"] for o in objects],
            "object_geometry_sha256": [o["sha"] for o in objects],
            "object_user_strings": [o["strings"] for o in objects],
            "object_count": len(objects),
            "document_user_strings": [{"key": "archflow:length_unit", "value": "meter"}],
        })
        self.repository.put_json(run=run, destination=run_records(run_id), record_kind=RUNNER_RUN_RECEIPT, payload={
            "schema": "RunnerRunReceipt@3", "project_id": PROJECT_ID, "run_id": run_id, "seat_execution_complete": True,
            "seat_results": [{"seat_id": "seat-portico", "status": "proposal_accepted", "objects": len(objects),
                              "cad": {"status": "succeeded", "path": "portico.3dm", "inspection_ref": ref.uri}}],
        })
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=run_id))
        binding = bound_project(self.app.state)
        self.catalog = catalog_of(binding, project_state(binding))

    def target(self, utterance, **kwargs):
        kwargs.setdefault("selection", Selection(component_id=None, element_id=None))
        kwargs.setdefault("aliases", ALIASES)
        return resolve_target(self.catalog, utterance=utterance, **kwargs)


class WithoutColumnElements(ResolverTestCase):
    def test_a_component_the_model_shows_and_the_catalog_lacks_is_named_missing(self) -> None:
        target = self.target("把柱子提高 0.1m", selection=Selection(component_id="portico-columns", element_id=None))
        self.assertEqual(target.status, MISSING)
        self.assertEqual(target.reason_code, MODEL_VISIBLE_CATALOG_MISSING)
        self.assertEqual(target.component_id, "portico-columns")
        self.assertIsNone(target.element_id)
        result = resolve_action(self.catalog, utterance="把柱子提高 0.1m", target=target, length_unit="m")
        self.assertEqual(result.kind, DECLARE_MISSING_CONTROL)
        self.assertEqual(result.reason_code, MODEL_VISIBLE_CATALOG_MISSING)
        self.assertEqual(result.requested_property, "height")
        # Never the abutment's height in its place.
        self.assertIsNone(result.capability_id)
        self.assertNotIn("abutment", result.question)

    def test_the_agent_cannot_smuggle_the_abutment_in(self) -> None:
        target = self.target("把柱子提高 0.1m", selection=Selection(component_id="portico-columns", element_id=None))
        result = validate_agent_result(
            self.catalog, utterance="把柱子提高 0.1m", target=target,
            answer={"kind": "command", "capabilityId": "entity:portico-roof-abutment-west#params.height", "op": "increase", "value": 10, "why": "the only height"},
        )
        # The catalog holds that capability, but the sentence's subject has no
        # catalog entry: the agent's substitute is refused and the answer stays
        # the named gap on the columns.
        self.assertEqual(result.kind, DECLARE_MISSING_CONTROL)
        self.assertEqual(result.reason_code, MODEL_VISIBLE_CATALOG_MISSING)
        self.assertIsNone(result.capability_id)
        self.assertIn("refused the agent's substitute", result.why)

    def test_declared_component_without_objects_or_rows_is_a_missing_declaration(self) -> None:
        # exterior-stairs is declared nowhere here; portico-roofs has objects but no rows.
        target = self.target("raise the roof", selection=Selection(component_id="portico-roofs", element_id=None))
        # portico-roofs itself has two objects and the abutments below it have rows:
        # the editable descendants are the abutments, so this is a choice.
        self.assertEqual(target.status, CANDIDATES)
        self.assertEqual(sorted(item.element_id for item in target.candidates), ["portico-roof-abutment-east", "portico-roof-abutment-west"])
        node = self.catalog.component("portico-columns")
        assert node is not None
        self.assertEqual(node.unbound_object_count, 4)


class WithColumnElements(ResolverTestCase):
    columns_have_elements = True

    def test_left_portico_columns_go_up_by_a_tenth(self) -> None:
        utterance = "把左侧柱廊的柱子提高 0.1m"
        target = self.target(utterance, camera=CAMERA, compass=COMPASS)
        self.assertEqual(target.status, RESOLVED)
        self.assertEqual(target.element_id, "portico-columns-west")
        self.assertEqual(target.component_id, "portico-columns")
        result = resolve_action(self.catalog, utterance=utterance, target=target, length_unit="m")
        self.assertEqual(result.kind, CHANGE_EXISTING_VALUE)
        self.assertEqual(result.utterance, "set height to 9.898")
        self.assertEqual(result.capability_id, "entity:portico-columns-west#params.height")

    def test_left_needs_a_camera(self) -> None:
        target = self.target("把左侧柱廊的柱子提高 0.1m")
        self.assertEqual(target.status, CANDIDATES)
        self.assertIn("camera", target.detail)
        self.assertEqual(sorted(item.side for item in target.candidates), ["east", "west"])

    def test_a_cardinal_word_reads_the_elements_own_identity(self) -> None:
        target = self.target("把西柱廊的柱子提高 0.1m")
        self.assertEqual(target.status, RESOLVED)
        self.assertEqual(target.element_id, "portico-columns-west")

    def test_a_little_is_a_question_not_ten_percent(self) -> None:
        utterance = "把左侧柱廊略微提高"
        target = self.target(utterance, camera=CAMERA, compass=COMPASS)
        # "柱廊" names porticos; its editable descendants are four; "左" keeps the west two.
        self.assertEqual(target.status, CANDIDATES)
        self.assertEqual(sorted(item.element_id for item in target.candidates), ["portico-columns-west", "portico-roof-abutment-west"])
        result = resolve_action(self.catalog, utterance=utterance, target=target, length_unit="m")
        self.assertEqual(result.kind, CLARIFY)
        self.assertIn("target", result.missing_slots)
        self.assertNotIn("10", result.question)
        # With the target settled, the amount is still a question.
        settled = self.target(utterance, selection=Selection(component_id="portico-columns", element_id="portico-columns-west"))
        result = resolve_action(self.catalog, utterance=utterance, target=settled, length_unit="m")
        self.assertEqual(result.kind, CLARIFY)
        self.assertEqual(result.missing_slots, ("amount",))
        self.assertEqual(result.reason_code, MISSING_AMOUNT)
        self.assertIn("9.798", result.question)
        self.assertEqual(result.slots.get("direction"), "increase")

    def test_an_agents_assumed_percentage_is_downgraded_to_the_amount_question(self) -> None:
        utterance = "把柱子略微提高"
        target = self.target(utterance, selection=Selection(component_id="portico-columns", element_id="portico-columns-west"))
        result = validate_agent_result(self.catalog, utterance=utterance, target=target, answer={
            "kind": "command", "capabilityId": "entity:portico-columns-west#params.height", "op": "increase", "value": 10, "why": "a little = +10 %",
        })
        self.assertEqual(result.kind, CLARIFY)
        self.assertEqual(result.missing_slots, ("amount",))
        self.assertIn("assumed an amount", result.why)

    def test_not_the_roof_the_columns_drops_the_roof(self) -> None:
        utterance = "不是屋顶，是柱子"
        rejected = negations_in(self.catalog, utterance, ALIASES)
        self.assertIn("portico-roofs", rejected)
        self.assertIn("portico-roof-abutment-west", rejected)
        self.assertEqual(affirmed_after_negation(utterance), "柱子")
        target = self.target(affirmed_after_negation(utterance), rejected=rejected, camera=CAMERA, compass=COMPASS)
        self.assertEqual(target.status, CANDIDATES)
        self.assertEqual(sorted(item.element_id for item in target.candidates), ["portico-columns-east", "portico-columns-west"])
        self.assertTrue(all("roof" not in item.element_id for item in target.candidates))

    def test_a_component_with_one_editable_descendant_lands(self) -> None:
        target = self.target("higher", selection=Selection(component_id="portico-roof-abutments", element_id=None))
        self.assertEqual(target.status, CANDIDATES)
        target = self.target("the west one higher", selection=Selection(component_id="portico-roof-abutments", element_id=None))
        self.assertEqual(target.status, RESOLVED)
        self.assertEqual(target.element_id, "portico-roof-abutment-west")

    def test_a_typed_sentence_on_a_resolved_element_is_a_command(self) -> None:
        target = self.target("set height to 2", selection=Selection(component_id="portico-columns", element_id="portico-columns-east"))
        result = resolve_action(self.catalog, utterance="set height to 2", target=target, length_unit="m")
        self.assertEqual(result.kind, CHANGE_EXISTING_VALUE)
        self.assertEqual(result.utterance, "set height to 2")

    def test_a_unit_the_record_does_not_keep_is_a_question(self) -> None:
        target = self.target("raise it by 100 mm", selection=Selection(component_id="portico-columns", element_id="portico-columns-east"))
        result = resolve_action(self.catalog, utterance="raise it by 100 mm", target=target, length_unit="m")
        self.assertEqual(result.kind, CLARIFY)
        self.assertIn("converts nothing", result.question)

    def test_the_agents_clarify_keeps_the_catalogs_candidates(self) -> None:
        target = self.target("把柱廊提高", camera=None)
        result = validate_agent_result(self.catalog, utterance="把柱廊提高", target=target, answer={"kind": "clarify", "missingSlots": ["target"], "question": "which portico?", "why": ""})
        self.assertEqual(result.kind, CLARIFY)
        self.assertEqual(result.reason_code, AMBIGUOUS_TARGET)
        self.assertEqual(len(result.target.candidates), 4)


if __name__ == "__main__":
    unittest.main()
