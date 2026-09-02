"""P089 (first cut): the record-driven project runner.

A schematic pack becomes a real developed-design state; an element pack
is produced per seat through the real producer; handovers carry the
realized bounds of earlier seats as exclusions; receipts report wall
time; gaps fail typed.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from archflow.capabilities.declaration import DeclarationQuadrant
from archflow.capabilities.discipline_seats import SeatSpec
from archflow.capabilities.geometry_proposal import GeometryProposalProviderIdentity
from archflow.project import FilesystemProjectRepository
from archflow.runtime.project_runner import (
    ElementPack,
    ProjectRunnerError,
    RunOptions,
    SchematicPack,
    bootstrap_developed_state,
    run_project,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline
from archflow.state.geometry_program import ProjectLevel, ProjectLevels

EVIDENCE = "evidence:demo-survey"
BASIS = (EVIDENCE,)


def _component(cid, parent, kind, intent, volumes=()):
    return {"schema": "DesignComponent@1", "component_id": cid, "parent_component_id": parent, "semantic_kind": kind, "intent": intent,
            "maturity": "schematic", "revision": 1, "volume_ids": list(volumes), "unresolved_child_roles": [], "source_refs": [EVIDENCE]}


def _schematic(extra_components=()) -> SchematicPack:
    from archflow.state.spatial import DesignComponent

    probe = DesignComponent.from_dict(_component("building", None, "whole-building", "one block", ("block",)))
    fields = probe.to_dict()
    def comp(cid, parent, kind, intent, volumes=()):
        payload = dict(fields); payload.update({"component_id": cid, "parent_component_id": parent, "semantic_kind": kind, "intent": intent, "volume_ids": list(volumes)})
        return payload
    components = [
        comp("building", None, "whole-building", "one block", ("block",)),
        comp("main-block", "building", "enclosure-and-load-distribution", "the block"),
        comp("exterior-walls", "main-block", "weather-enclosure-and-opening-host", "walls"),
        comp("portico", "building", "arrival-and-buttress", "front portico"),
        comp("portico-columns", "portico", "vertical-support", "columns"),
        *extra_components,
    ]
    return SchematicPack.from_dict({
        "schema": "SchematicPack@1", "project_id": "demo", "option_id": "declared-option", "label": "demo declared schematic", "typology": "test block with a portico",
        "rationale": "declared from the survey record", "evidence_refs": [EVIDENCE],
        "levels": [{"level_id": "ground", "base_y": 0, "height": 12}], "volumes": [{"volume_id": "block", "min": [0, 0, 0], "max": [12, 12, 12], "level_ids": ["ground"]}],
        "zones": [{"zone_id": "hall", "program_node_refs": ["program-node:hall"], "level_ids": ["ground"], "volume_ids": ["block"]}],
        "connections": [], "components": components, "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]], "assumption_refs": ["assumption:declared-schematic"],
    })


def _levels() -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel("level-cornice", "main-cornice", 12.0, BASIS), ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS), ProjectLevel("level-piano-nobile", "piano-nobile", 3.5, BASIS)))


def _elements(opening_along=6.0) -> ElementPack:
    return ElementPack.from_dict({"schema": "ElementPack@1", "elements": [
        {"element_id": "portico-columns", "component_id": "portico-columns", "producer": "column-array", "base_level": "level-piano-nobile",
         "params": {"origin": [6.0, -0.2], "direction": [1, 0], "count": 4, "spacing": 2.5, "radius": 0.4, "height": 6.0, "basis_refs": [EVIDENCE]}},
        {"element_id": "wall-south", "component_id": "exterior-walls", "producer": "wall", "base_level": "level-ground",
         "params": {"origin": [12.0, 0.0], "direction": [-1, 0], "length": 12.0, "thickness": 0.6, "top_level": "level-cornice",
                    "openings": [{"opening_id": "door", "kind": "door", "along": opening_along, "width": 1.4, "sill": 3.5, "head": 8.0}]}},
    ]})


def _seats(phase: DesignPhase):
    return (
        SeatSpec(seat_id="seat-structure", disciplines=(DevelopmentDiscipline.STRUCTURE_SUPPORT,), owned_component_ids=("portico-columns",), phases=(phase,), quadrants=(DeclarationQuadrant.STRUCTURE,)),
        SeatSpec(seat_id="seat-envelope", disciplines=(DevelopmentDiscipline.ENVELOPE_OPENINGS,), owned_component_ids=("exterior-walls",), phases=(phase,), quadrants=(DeclarationQuadrant.OPENINGS,), consumes=("seat-structure",)),
        SeatSpec(seat_id="seat-review", disciplines=(DevelopmentDiscipline.USE,), owned_component_ids=(), phases=(phase,), quadrants=(), reviewer=True),
    )


def _options(**overrides) -> RunOptions:
    fields = dict(commitment_ref="commitment:demo-survey", provider_identity=GeometryProposalProviderIdentity(
        provider_id="runner-test", model_id="scripted", provider_version="1", provider_fingerprint=hashlib.sha256(b"runner-test").hexdigest()))
    fields.update(overrides)
    return RunOptions(**fields)


class BootstrapTests(unittest.TestCase):
    def test_pack_becomes_a_real_developed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = bootstrap_developed_state(_schematic(), run=run, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared")
            self.assertEqual(state.active_phase, DesignPhase.DESIGN_DEVELOPMENT)
            self.assertEqual(len(state.state_digest), 64)
            self.assertEqual([c.component_id for c in state.selected_schematic.option.proposal.components][:2], ["building", "exterior-walls"])
            other = repository.create_run("run-2")
            self.assertNotEqual(state.state_digest, bootstrap_developed_state(_schematic(), run=other, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared").selected_schematic.run_id)

    def test_malformed_packs_fail_typed(self) -> None:
        with self.assertRaises(ProjectRunnerError):
            SchematicPack.from_dict({"schema": "Other@1"})
        with self.assertRaises(ProjectRunnerError):
            ElementPack.from_dict({"schema": "ElementPack@1", "elements": [{"element_id": "a", "component_id": "c", "producer": "prism"}, {"element_id": "a", "component_id": "c", "producer": "prism"}]})
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "other", project_id="other", initial_state={"schema": "TestState@1"})
            with self.assertRaises(ProjectRunnerError):
                bootstrap_developed_state(_schematic(), run=repository.create_run("run-1"), portfolio_id="declared", branch_id="b", selection_decision_ref="decision:x")


class RunTests(unittest.TestCase):
    def _run(self, elements: ElementPack, **overrides):
        self.temporary = tempfile.TemporaryDirectory()
        repository = FilesystemProjectRepository.initialize(Path(self.temporary.name) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
        run = repository.create_run("run-1")
        pack = _schematic()
        state = bootstrap_developed_state(pack, run=run, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared")
        return repository, run_project(repository, run=run, schematic=pack, elements=elements, seats=_seats(state.active_phase), levels=_levels(), grids=None, options=_options(**overrides))

    def test_two_seats_run_through_the_producer_with_receipts(self) -> None:
        repository, receipt = self._run(_elements())
        self.assertTrue(receipt["accepted"], receipt["stages"])
        stages = {s["seat_id"]: s for s in receipt["stages"]}
        self.assertEqual(stages["seat-structure"]["status"], "accepted")
        self.assertEqual(stages["seat-structure"]["objects"], 4)
        self.assertEqual(stages["seat-envelope"]["status"], "accepted")
        self.assertGreaterEqual(stages["seat-envelope"]["objects"], 2)                      # cut wall + aperture
        self.assertEqual(stages["seat-envelope"]["round"], 1)
        self.assertTrue(all(s["wall_time_s"] >= 0.0 for s in receipt["stages"]))
        self.assertIn("receipt_ref", receipt)
        program = repository.load_json(_ref(stages["seat-envelope"]["program_ref"]))
        datum_ids = {d["datum_id"] for d in program["interface_datums"]}
        self.assertIn("portico-columns-top", datum_ids)                                        # handed over from the structure seat
        self.assertIn("level-cornice", datum_ids)

    def test_realized_bounds_of_an_earlier_seat_exclude_a_later_opening(self) -> None:
        with self.assertRaises(ProjectRunnerError) as caught:
            self._run(_elements(opening_along=4.75))       # the door would open where a column stands
        self.assertIn("intersects exclusion", str(caught.exception))

    def test_owned_leaf_without_element_fails_typed_unless_relaxed(self) -> None:
        elements = ElementPack.from_dict({"schema": "ElementPack@1", "elements": [
            {"element_id": "wall-south", "component_id": "exterior-walls", "producer": "wall", "base_level": "level-ground",
             "params": {"origin": [12.0, 0.0], "direction": [-1, 0], "length": 12.0, "thickness": 0.6, "top_level": "level-cornice"}}]})
        with self.assertRaises(ProjectRunnerError):
            self._run(elements)
        repository, receipt = self._run(elements, strict_coverage=False)
        stages = {s["seat_id"]: s for s in receipt["stages"]}
        self.assertEqual(stages["seat-structure"]["status"], "empty")
        self.assertEqual(stages["seat-structure"]["undeclared_components"], ["portico-columns"])
        self.assertEqual(stages["seat-envelope"]["status"], "accepted")


def _ref(uri: str):
    from archflow.project.refs import ProjectRecordRef

    name = uri.rsplit("/", 1)[1]
    return ProjectRecordRef(project_id="demo", relative_path=f"runs/run-1/records/{name}", sha256=name.rsplit("-", 1)[1].split(".json")[0], media_type="application/json")


if __name__ == "__main__":
    unittest.main()
