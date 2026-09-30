"""One window in a south wall, its lintel and an independent fixed block, on the runner's demo block.

The window width is a declared parameter the opening and the lintel read, and
``_width_edit`` is the one edit the tests make to it; ``_measured`` reads the
exact STEP a run exported. The relational window update and the non-adjacent
stage tests use it.
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
from tests.integration import runner_support


HOST = "wall-south"
LINTEL = "window-lintel"
FIXED = "fixed-building"
APERTURE = "obj-wall-south-aperture-window"
FRAME = "obj-frame-wall-south-window"
GLASS = "obj-glazing-wall-south-window"
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
    }, parent_id="exterior-walls", basis_refs=runner_support.BASIS)
    lintel = Entity(LINTEL, "Element@1", {
        "component_id": "portico-columns", "producer": "prism",
        "references": {"base": {"offset_from": {"level": "level-ground", "offset": 2.4}}},
        "params": {"profile": [["@lintel_left", -0.3], ["@lintel_right", -0.3],
                               ["@lintel_right", 0.0], ["@lintel_left", 0.0]], "height": 0.2},
    }, parent_id="portico-columns", basis_refs=runner_support.BASIS)
    fixed = Entity(FIXED, "Element@1", {
        "component_id": "portico-columns", "producer": "prism",
        "references": {"base": {"level": "level-ground"}},
        "params": {"profile": [[8.0, 3.0], [10.0, 3.0], [10.0, 5.0], [8.0, 5.0]], "height": 3.0},
    }, parent_id="portico-columns", basis_refs=runner_support.BASIS)
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
    record = runner_support._record(elements=(), extra_entities=(wall, lintel, fixed, *outside), parameters=parameters,
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
