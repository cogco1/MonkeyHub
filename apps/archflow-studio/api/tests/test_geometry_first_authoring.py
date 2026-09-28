"""Geometry first, meaning later: a fresh project models ordinary forms without GridAxis or semanticKind (#400).

The agent-facing contract this checks, through the real Studio routes:
a new component is created from geometry alone, a face, a path and a
wall-like mass stay generic, an explicitly requested wall with an opening
needs no GridAxis, and naming what a generic component is later keeps the
same component, its elements and their dependencies.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord, component_semantics
from archflow_studio_api.application.intent import _unregistered_kinds_as_intent
from archflow_studio_api.application.intent_agent import response_schema
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

PROJECT_ID = "geometry-first"
SQUARE = [[0, 0], [4, 0], [4, 3], [0, 3]]


class GeometryFirstAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="geometry-first-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.project, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
            authored_record=StateRecord(project_id=PROJECT_ID, run_id="authored", entities=()).to_dict())
        self.client = TestClient(create_app(StudioSettings(cad_export="off", project_dir=self.project)))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        response = self.client.post("/api/project/modeling", json={"projectId": PROJECT_ID})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/state/frame").json()["axes"], [])
        # Nothing here may reach a model: these are direct authoring requests.
        compiler = Mock()
        compiler.compile.side_effect = AssertionError("direct authoring cannot call a model")
        self.client.app.state.intent_compiler = compiler

    def state(self, run: str | None = None) -> dict:
        return self.client.get("/api/state" + (f"?run={run}" if run else "")).json()

    def created(self, response) -> dict:
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def candidate(self, proposal: dict) -> str:
        started = self.client.post(f"/api/proposals/{proposal['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{accepted['jobId']}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "succeeded", job)
        return accepted["candidateId"]

    def retained(self, run_id: str) -> StateRecord:
        refs = self.repository.list_json(run=self.repository.load_run(run_id), destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=run_id))
        records = [payload for ref in refs if (payload := self.repository.load_json(ref)).get("schema") == StateRecord.SCHEMA]
        self.assertEqual(len(records), 1)
        return StateRecord.from_dict(records[0])

    def sketch_generic_forms(self) -> str:
        """A mass, a face, a path and a thin wall-like slab, none of them classified."""

        base = {"parentComponentId": "model", "baseLevel": "ground"}
        proposal = self.created(self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.state()["stateDigest"],
            "sketches": [
                {"componentId": "mass", "elementId": "mass-body", "profile": SQUARE, "height": 3, **base},
                {"componentId": "mass", "elementId": "mass-roof-face", "profile": SQUARE, "height": 0,
                 "baseDatum": "mass-body-top"},
                {"componentId": "path", "elementId": "path-line", "profile": [[0, 5], [3, 6], [6, 5]], "height": 0,
                 "closed": False, **base},
                {"componentId": "screen", "elementId": "screen-body", "profile": [[6, 0], [10, 0], [10, 0.25], [6, 0.25]],
                 "height": 2.8, **base},
            ],
        }))
        entities = {entity["entity_id"]: entity for entity in proposal["change"]["edits"]["entities"]}
        for component_id in ("mass", "path", "screen"):
            self.assertEqual(entities[component_id]["schema"], "Component@1")
            self.assertNotIn("semantic_kind", entities[component_id]["fields"])
        return self.candidate(proposal)

    def test_ordinary_geometry_needs_no_grid_or_semantic_kind_and_is_not_classified(self) -> None:
        run = self.sketch_generic_forms()
        record = self.retained(run)
        self.assertFalse(any(entity.schema == "GridAxis@1" for entity in record.entities))
        producers = {entity.entity_id: entity.fields["producer"] for entity in record.entities_of("Element@1")}
        self.assertEqual(producers, {"mass-body": "prism", "mass-roof-face": "planar-surface",
                                     "path-line": "curve", "screen-body": "prism"})
        for component_id in ("mass", "path", "screen"):
            self.assertIsNone(component_semantics(record.entity(component_id)))
        tree = {node["componentId"]: node for node in self.state(run)["componentTree"]}
        # The wall-like slab stays a generic prism: nothing names it a wall.
        self.assertEqual({tree[c]["semanticKind"] for c in ("mass", "path", "screen")}, {None})
        # The seeded root is unclassified too: nothing in a fresh project is a
        # classification template for new parts (#408).
        self.assertIsNone(tree["model"]["semanticKind"])

    def test_an_explicitly_requested_wall_and_window_need_no_grid(self) -> None:
        proposal = self.created(self.client.post("/api/proposals", json={
            "stateDigest": self.state()["stateDigest"], "keep": ["entity:ground"], "semanticEdit": {
                "summary": "A wall with a window, as asked",
                "entities": [
                    {"entity_id": "north-wall", "schema": "Component@1", "parent_id": "model",
                     "fields": {"intent": "the north wall the architect asked for"}},
                    {"entity_id": "north-wall-body", "schema": "Element@1", "parent_id": "north-wall", "fields": {
                        "component_id": "north-wall", "producer": "wall",
                        "references": {"base": {"level": "ground"},
                                       "line": {"from": {"point": [0, 8]}, "to": {"point": [6, 8]}}},
                        "params": {"height": 3, "thickness": 0.3, "openings": [
                            {"opening_id": "window", "kind": "window", "along": 2, "width": 1.2, "sill": 0.9, "head": 2.1}]}}},
                ]}}))
        record = self.retained(self.candidate(proposal))
        self.assertFalse(any(entity.schema == "GridAxis@1" for entity in record.entities))
        wall = record.entity("north-wall-body")
        self.assertEqual(wall.fields["producer"], "wall")
        self.assertEqual(wall.fields["params"]["openings"][0]["opening_id"], "window")

    def test_a_fresh_frame_does_not_mention_a_grid(self) -> None:
        frame = self.client.get("/api/state/frame")
        self.assertEqual(frame.status_code, 200, frame.text)
        self.assertNotIn("gridaxis", frame.text.lower())
        self.assertFalse([line for line in frame.json()["honesty"] if "grid" in line.lower()])

    def test_this_is_a_wall_with_a_window_is_modelled_not_refused(self) -> None:
        """#408, sentence c: "a 10 m wall with a 1.2 x 1.5 m window", with the user's word "wall"."""

        proposal = self.created(self.client.post("/api/proposals", json={
            "stateDigest": self.state()["stateDigest"], "keep": ["entity:ground"], "semanticEdit": {
                "summary": "A 10 m wall with a 1.2 x 1.5 m window",
                "entities": [
                    {"entity_id": "wall-a", "schema": "Component@1", "parent_id": "model",
                     "fields": {"semantic_kind": "wall"}},
                    {"entity_id": "wall-a-body", "schema": "Element@1", "parent_id": "wall-a", "fields": {
                        "component_id": "wall-a", "producer": "wall",
                        "references": {"base": {"level": "ground"},
                                       "line": {"from": {"point": [0, 0]}, "to": {"point": [10, 0]}}},
                        "params": {"height": 3, "thickness": 0.24, "openings": [
                            {"opening_id": "window", "kind": "window", "along": 4.4, "width": 1.2, "sill": 0.9, "head": 2.4}]}}},
                ]}}))
        self.assertEqual(proposal["status"], "proposed")
        self.assertIn("'wall'", proposal["change"]["summary"])
        component = next(e for e in proposal["change"]["edits"]["entities"] if e["entity_id"] == "wall-a")
        self.assertNotIn("semantic_kind", component["fields"])
        record = self.retained(self.candidate(proposal))
        self.assertFalse(any(entity.schema == "GridAxis@1" for entity in record.entities))
        self.assertIsNone(component_semantics(record.entity("wall-a")), "no nearby id is guessed")
        self.assertEqual(record.entity("wall-a").fields["intent"], "wall", "the user's word is kept")
        self.assertEqual(record.entity("wall-a-body").fields["params"]["openings"][0]["width"], 1.2)

    def test_an_unregistered_word_does_not_refuse_a_sketch_and_a_registered_one_is_kept(self) -> None:
        base = {"parentComponentId": "model", "baseLevel": "ground", "height": 3}
        unregistered = self.created(self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.state()["stateDigest"], "componentId": "block", "elementId": "block-body",
            "profile": SQUARE, "semanticKind": "墙体", **base}))
        block = next(e for e in unregistered["change"]["edits"]["entities"] if e["entity_id"] == "block")
        self.assertEqual(block["fields"], {"intent": "墙体"})
        registered = self.created(self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.state()["stateDigest"], "componentId": "cover", "elementId": "cover-body",
            "profile": SQUARE, "semanticKind": "roof", **base}))
        cover = next(e for e in registered["change"]["edits"]["entities"] if e["entity_id"] == "cover")
        self.assertEqual(cover["fields"]["semantic_kind"], "roof")

    def test_a_form_whose_component_and_element_share_an_id_is_refused_by_name(self) -> None:
        """#413: geometry first makes a component per form; one id for both was refused without naming it."""

        response = self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.state()["stateDigest"], "componentId": "twin", "elementId": "twin",
            "parentComponentId": "model", "baseLevel": "ground", "height": 3, "profile": SQUARE})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("componentId and elementId are both 'twin'", response.text)
        self.assertIn("need different ids", response.text)
        schemas = self.client.get("/openapi.json").json()["components"]["schemas"]
        for name in ("SketchPrismRequestDto", "SketchActionDto"):
            self.assertIn("must differ from componentId", schemas[name]["properties"]["elementId"]["description"])

    def test_a_misspelt_kind_joins_the_stated_intent_and_survives_a_continued_proposal(self) -> None:
        """#413: the note said "keeps 'w' as its intent" while the word lived only in the summary."""

        digest = self.state()["stateDigest"]
        named = self.created(self.client.post("/api/proposals", json={
            "stateDigest": digest, "semanticEdit": {
                "summary": "The architect calls the block a rooof",
                "entities": [{"entity_id": "block", "schema": "Component@1", "parent_id": "model",
                              "fields": {"intent": "the north block", "semantic_kind": "rooof"}}]}}))
        block = next(e for e in named["change"]["edits"]["entities"] if e["entity_id"] == "block")
        self.assertEqual(block["fields"]["intent"], "the north block; rooof")
        self.assertNotIn("semantic_kind", block["fields"])
        summary = named["change"]["summary"]
        self.assertNotIn("keeps 'rooof' as its intent", summary)
        self.assertIn("block adds 'rooof' to its intent", summary)
        self.assertIn("registered spellings close to it: roof", summary)
        self.assertNotIn("role.", summary)
        self.assertNotIn("condition.", summary)
        continued = self.created(self.client.post("/api/proposals/sketch", json={
            "stateDigest": digest, "sourceProposalId": named["proposalId"], "componentId": "block",
            "elementId": "block-body", "profile": SQUARE, "baseLevel": "ground", "height": 3}))
        self.assertNotIn("rooof", continued["change"]["summary"])
        block = next(e for e in continued["change"]["edits"]["entities"] if e["entity_id"] == "block")
        self.assertEqual(block["fields"]["intent"], "the north block; rooof", "the word outlives the summary")
        record = self.retained(self.candidate(continued))
        self.assertEqual(record.entity("block").fields["intent"], "the north block; rooof")
        self.assertIsNone(component_semantics(record.entity("block")))

    def test_a_stated_intent_already_says_the_word_only_as_a_word(self) -> None:
        """#413: "a drywall partition" does not already say "wall"; 外墙 does say 墙."""

        rows, notes = _unregistered_kinds_as_intent([
            {"entity_id": "partition", "schema": "Component@1",
             "fields": {"intent": "a drywall partition", "semantic_kind": "wall"}},
            {"entity_id": "front", "schema": "Component@1", "fields": {"intent": "南立面外墙", "semantic_kind": "墙"}},
            {"entity_id": "side", "schema": "Component@1", "fields": {"intent": "the east wall", "semantic_kind": "Wall"}},
        ], {})
        self.assertEqual([row["fields"]["intent"] for row in rows],
                         ["a drywall partition; wall", "南立面外墙", "the east wall"])
        self.assertIn("partition adds 'wall' to its intent", notes[0])
        self.assertIn("front's intent already says '墙'", notes[1])
        self.assertIn("side's intent already says 'Wall'", notes[2])

    def test_naming_a_generic_component_later_keeps_its_identity(self) -> None:
        run = self.sketch_generic_forms()
        before = self.retained(run)
        edges = {(edge.upstream_ref, edge.downstream_ref) for edge in before.dependency_edges()}
        proposal = self.created(self.client.post("/api/proposals", json={
            "stateDigest": self.state(run)["stateDigest"], "sourceRunId": run, "semanticEdit": {
                "summary": "The architect says the slab is an enclosure",
                "entities": [{"entity_id": "screen", "schema": "Component@1", "parent_id": "model",
                              "fields": {"intent": "screen", "semantic_kind": "enclosure"}}]}}))
        changed = {entity["entity_id"] for entity in proposal["change"]["edits"]["entities"]}
        self.assertEqual(changed, {"screen"}, "enrichment upserts the component; it creates nothing new")
        after = self.retained(self.candidate(proposal))
        self.assertEqual(component_semantics(after.entity("screen")), "enclosure")
        self.assertEqual(after.entity("screen").parent_id, before.entity("screen").parent_id)
        self.assertEqual([e.entity_id for e in after.entities_of("Component@1")],
                         [e.entity_id for e in before.entities_of("Component@1")])
        body_before, body_after = before.entity("screen-body"), after.entity("screen-body")
        self.assertEqual(body_after.to_dict(), body_before.to_dict(), "its geometry and provenance are untouched")
        self.assertEqual({(edge.upstream_ref, edge.downstream_ref) for edge in after.dependency_edges()}, edges)
        # The rest stays generic: naming one part names nothing else.
        self.assertIsNone(component_semantics(after.entity("mass")))

    def test_the_agent_schema_leaves_semantic_kind_optional(self) -> None:
        edit = response_schema(strict=False)["properties"]["semanticEdit"]["anyOf"][1]
        component = next(variant for variant in edit["properties"]["entities"]["items"]["anyOf"]
                         if variant["properties"]["schema"]["enum"] == ["Component@1"])["properties"]["fields"]
        self.assertNotIn("semantic_kind", component["required"])
        self.assertNotEqual(next(iter(component["properties"])), "semantic_kind")
        strict = response_schema()["properties"]["semanticEdit"]["anyOf"][1]
        strict_component = next(variant for variant in strict["properties"]["entities"]["items"]["anyOf"]
                                if variant["properties"]["schema"]["enum"] == ["Component@1"])["properties"]["fields"]
        self.assertIn({"type": "null"}, strict_component["properties"]["semantic_kind"]["anyOf"])


if __name__ == "__main__":
    unittest.main()
