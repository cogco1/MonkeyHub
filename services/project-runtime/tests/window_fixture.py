"""One window in a south wall, its lintel and an independent fixed block, on the runner's demo block.

A copy of MonkeyArch's window fixture (packages/monkeyarch/tests/test_window_relational_update.py)
and of the runner demo block it builds on (``_runner_record``, the runner fixture's ``_record``),
with the guard that fails a run reaching Rhino or starting a process. The Runtime's
non-adjacent stage test drives the same record through the API; a service's tests
cannot import a package's.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from monkeycad.backends.occt.measure import measure_shape
from monkeycad.backends.occt.step import read_step
from archflow.state.state_record import (
    Entity,
    Parameter,
    Relation,
    StateRecord,
    StateRecordEditKind,
    StateRecordOperator,
    ValidatorBinding,
)

EVIDENCE = "evidence:demo-survey"
BASIS = (EVIDENCE,)


def _component(cid, parent, kind, intent, volumes=()):
    return {"schema": "DesignComponent@1", "component_id": cid, "parent_component_id": parent, "semantic_kind": kind, "intent": intent,
            "maturity": "schematic", "revision": 1, "volume_ids": list(volumes), "unresolved_child_roles": [], "source_refs": [EVIDENCE]}


def _runner_record(opening_along: float = 6.0, extra_components=(), elements=("portico-columns", "wall-south"), extra_entities=(), relations=(), parameters=()) -> StateRecord:
    """The demo block as one State Record: components + massing, three levels, four grid lines, two element rows."""

    components = [
        _component("building", None, "whole-building", "one block", ("block",)),
        _component("main-block", "building", "enclosure-and-load-distribution", "the block"),
        _component("exterior-walls", "main-block", "weather-enclosure-and-opening-host", "walls"),
        _component("portico", "building", "arrival-and-buttress", "front portico"),
        _component("portico-columns", "portico", "vertical-support", "columns"),
        *extra_components,
    ]
    entities = [Entity(c["component_id"], "Component@1", {k: v for k, v in c.items() if k not in ("component_id", "parent_component_id")}, parent_id=c["parent_component_id"]) for c in components]
    entities += [Entity("ground", "MassingLevel@1", {"base_y": 0, "height": 12}), Entity("block", "Volume@1", {"min": [0, 0, 0], "max": [12, 12, 12], "level_ids": ["ground"]}),
                 Entity("hall", "Space@1", {"program_node_refs": ["program-node:hall"], "level_ids": ["ground"], "volume_ids": ["block"]})]
    entities += [Entity("level-cornice", "Level@1", {"role": "main-cornice", "elevation": 12.0}, basis_refs=BASIS), Entity("level-ground", "Level@1", {"role": "terrain-grade", "elevation": 0.0}, basis_refs=BASIS),
                 Entity("level-piano-nobile", "Level@1", {"role": "piano-nobile", "elevation": 3.5}, basis_refs=BASIS)]
    entities += [Entity("axis-front", "GridAxis@1", {"role": "F", "origin": [0.0, 0.0, -0.2], "direction": [1.0, 0.0, 0.0]}, basis_refs=BASIS),      # the colonnade line
                 Entity("axis-s", "GridAxis@1", {"role": "S", "origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]}, basis_refs=BASIS),           # south face
                 Entity("axis-w", "GridAxis@1", {"role": "W", "origin": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}, basis_refs=BASIS),
                 Entity("axis-e", "GridAxis@1", {"role": "E", "origin": [12.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}, basis_refs=BASIS)]
    rows = {
        "portico-columns": Entity("columns-front", "Element@1", {"component_id": "portico-columns", "producer": "column-array",
                                  "references": {"at": {"axis_point": {"axis": "F", "along": 6.0}}, "direction": "F", "base": {"level": "level-piano-nobile"}},
                                  "params": {"count": 4, "spacing": 2.5, "radius": 0.4, "height": 6.0}}, parent_id="portico-columns", basis_refs=BASIS),
        "declined-portico-columns": Entity("columns-front-declined", "Element@1", {"component_id": "portico-columns", "producer": "declined",
                                           "params": {"reason": "human decision pending"}}, parent_id="portico-columns", basis_refs=BASIS),
        "wall-south": Entity("wall-south", "Element@1", {"component_id": "exterior-walls", "producer": "wall",
                             "references": {"line": {"from": {"grid": ["E", "S"]}, "to": {"grid": ["W", "S"]}, "face": "exterior", "inward": [0, 1]}, "base": {"level": "level-ground"}, "top": {"level": "level-cornice"}},
                             "params": {"thickness": 0.6, "openings": [{"opening_id": "door", "kind": "door", "at": {"host": {"element": "wall-south", "along": opening_along}}, "width": 1.4, "sill": 3.5, "head": 8.0}]}},
                             parent_id="exterior-walls", basis_refs=BASIS),
    }
    entities += [rows[name] for name in elements]
    entities += list(extra_entities)
    return StateRecord("demo", "run-1", tuple(entities), parameters=tuple(parameters), relations=tuple(relations), evidence_refs=(EVIDENCE,), decision_ref="decision:declared-option",
                       option={"option_id": "declared-option", "label": "demo declared schematic", "typology": "test block with a portico", "rationale": "declared from the survey record",
                               "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]], "assumption_refs": ["assumption:declared-schematic"]})


def _refuse_rhino(*args, **kwargs):
    raise AssertionError(f"the OCCT export path must never reach Rhino or start a process: {args[:1]}")


def _no_rhino():
    """Fail the test if the run reaches a Rhino entry point or starts any process."""

    import subprocess
    from unittest.mock import patch

    return (patch.multiple("monkeycad.backends.rhino.export", prepare_rhino_three_dm_export=_refuse_rhino, execute_rhino_three_dm_export=_refuse_rhino),
            patch.multiple(subprocess, Popen=_refuse_rhino, run=_refuse_rhino))


HOST = "wall-south"
LINTEL = "window-lintel"
FIXED = "fixed-building"
APERTURE = "obj-wall-south-aperture-window"
LINTEL_OBJECT = "obj-window-lintel"
FIXED_OBJECT = "obj-fixed-building"
BEARING_RELATION = "window-lintel-bearing"


def _record(*, bearing: float = 0.15, checked: bool = False) -> StateRecord:
    parameters = (
        Parameter("window_left", 2.0, "m", epistemic_status="declared"),
        Parameter("window_width", 1.2, "m", epistemic_status="declared"),
        Parameter("window_center", 2.6, "m", expr="window_left + window_width / 2"),
        Parameter("bearing", bearing, "m", epistemic_status="declared"),
        Parameter("lintel_left", 2.0 - bearing, "m", expr="window_left - bearing"),
        Parameter("lintel_right", 3.2 + bearing, "m", expr="window_left + window_width + bearing"),
    )
    wall = Entity(HOST, "Element@1", {
        "component_id": "exterior-walls", "producer": "wall",
        "references": {"line": {"from": {"grid": ["W", "S"]}, "to": {"grid": ["E", "S"]}},
                       "base": {"level": "level-ground"}},
        "params": {"thickness": 0.3, "height": 3.0,
                   "types": [{"type_id": "window-type", "frame_width": 0.09, "frame_depth": 0.18,
                              "frame_projection": 0.1, "glazing_thickness": 0.025, "glazing_offset": 0.01}],
                   "openings": [{"opening_id": "window", "kind": "window", "along": "@window_center",
                                 "width": "@window_width", "sill": 0.9, "head": 2.4, "type_id": "window-type",
                                 "interface_ref": "relation:window-inside-outside"}]},
    }, parent_id="exterior-walls", basis_refs=BASIS)
    lintel = Entity(LINTEL, "Element@1", {
        "component_id": "portico-columns", "producer": "prism",
        "references": {"base": {"offset_from": {"level": "level-ground", "offset": 2.4}}},
        "params": {"profile": [["@lintel_left", -0.3], ["@lintel_right", -0.3],
                               ["@lintel_right", 0.0], ["@lintel_left", 0.0]], "height": 0.2},
    }, parent_id="portico-columns", basis_refs=BASIS)
    fixed = Entity(FIXED, "Element@1", {
        "component_id": "portico-columns", "producer": "prism",
        "references": {"base": {"level": "level-ground"}},
        "params": {"profile": [[8.0, 3.0], [10.0, 3.0], [10.0, 5.0], [8.0, 5.0]], "height": 3.0},
    }, parent_id="portico-columns", basis_refs=BASIS)
    relation = Relation(BEARING_RELATION, "dependency", HOST, LINTEL,
                        validator=ValidatorBinding("lintel_minimum_bearing", tolerance=0.001),
                        parameters={"opening_object_id": APERTURE, "lintel_object_id": LINTEL_OBJECT,
                                    "span_axis": "x", "minimum_bearing_m": 0.15}) if checked else None
    outside = (
        Entity("outside-volume", "Volume@1", {"min": [0, 0, -3], "max": [12, 3, -1], "level_ids": ["ground"]}),
        Entity("outside", "Space@1", {"program_node_refs": ["program-node:outside"], "level_ids": ["ground"], "volume_ids": ["outside-volume"]}),
        Entity("window-interface", "Connection@1", {"source_zone_id": "hall", "target_zone_id": "outside",
                                                   "relationship_refs": ["relation:window-inside-outside"]}),
    )
    interface = Relation("window-inside-outside", "interface", "hall", "outside", propagation="unchanged")
    record = _runner_record(elements=(), extra_entities=(wall, lintel, fixed, *outside), parameters=parameters,
                                    relations=(interface,) if relation is None else (interface, relation))
    return replace(record, entities=tuple(replace(entity, fields={**entity.fields, "volume_ids": ["block", "outside-volume"]})
                                         if entity.entity_id == "building" else entity for entity in record.entities))


def _width_edit(record: StateRecord, value: float) -> StateRecordOperator:
    return StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR,
                               base_record_digest=record.digest, base_state_digest=record.state_digest,
                               target_ref="parameter:window_width", key="window_width", value=value,
                               protected=(f"entity:{FIXED}", "parameter:window_left"))


def _measured(receipt):
    path = Path(receipt["seat_results"][0]["cad"]["model"])
    return {item.name: measure_shape(item.shape)
            for item in read_step(path, length_unit="meter")}
