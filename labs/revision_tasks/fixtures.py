"""Three synthetic, parametric building fixtures as in-memory State Records.

Each fixture is an authored ``StateRecord`` of Component@1 / Level@1 / Element@1
rows plus declared parameters and relations. Nothing here writes a project:
``bound`` binds a record to a synthetic run so the geometry compiler can check
exact-base identity, which is a plain value and no P036 I/O.

Coordinates follow the program frame: x along, y up, z plan depth. Every
dimension is a declared parameter or a literal in the row, so a task changes a
fixture only through a typed ``StateRecordOperator``.

F1  portico colonnade on a stylobate: column count, equal spacing, fixed end columns.
F2  portico, transition block and rotunda: one declared interface (J1, the
    transition roof stays below the rotunda cornice) and one relation that is
    deliberately left out of the record (J3, the transition roof clears the
    portico's rear entablature). J3 exists only in the tasks' oracle.
F3  wall with a door opening and a lintel bound to the door width; a straight
    stair from L1 to L2 under an L2 slab.
"""

from __future__ import annotations

from typing import Any, Callable

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import Entity, Parameter, Relation, StateRecord, ValidatorBinding

PROJECT_ID = "revision-tasks"
FIXTURE_VERSION = "1"
BASIS = ("evidence:synthetic-fixture",)
SYNTHETIC_BASE_SHA256 = "1" * 64


def _component(cid: str, parent: str | None, kind: str, intent: str) -> Entity:
    fields = {"schema": "DesignComponent@1", "semantic_kind": kind, "intent": intent, "maturity": "schematic", "revision": 1,
              "volume_ids": [], "unresolved_child_roles": [], "source_refs": list(BASIS)}
    return Entity(cid, "Component@1", fields, parent_id=parent)


def _level(level_id: str, role: str, elevation: float) -> Entity:
    return Entity(level_id, "Level@1", {"role": role, "elevation": elevation}, basis_refs=BASIS)


def _element(element_id: str, component: str, producer: str, references: dict[str, Any], params: dict[str, Any]) -> Entity:
    return Entity(element_id, "Element@1", {"component_id": component, "producer": producer, "references": references, "params": params},
                  parent_id=component, basis_refs=BASIS)


# An obligation the record declares between two parameters: the first may not exceed the second.
# The kernel stores obligations but evaluates none; the harness evaluates this one kind (pipeline.py).
AT_MOST = "validator:parameter-at-most"


def _record(run_id: str, entities: list[Entity], parameters: tuple[Parameter, ...], relations: tuple[Relation, ...],
            obligations: tuple[DesignObligation, ...] = ()) -> StateRecord:
    return StateRecord(PROJECT_ID, run_id, tuple(entities), parameters=parameters, relations=relations, obligations=obligations,
                       evidence_refs=BASIS, decision_ref=f"decision:{run_id}-fixture")


def bound(record: StateRecord) -> StateRecord:
    """The record bound to its synthetic run; the base is a fixed constant, not a project version."""

    return record.bound_to(RunRef(PROJECT_ID, record.run_id, ProjectVersionRef(PROJECT_ID, 0, SYNTHETIC_BASE_SHA256)))


# ---------------------------------------------------------------- F1 portico colonnade

def f1_portico() -> StateRecord:
    """Stylobate 12.0 m; six columns of 0.9 m diameter and 9.0 m height; end column axes 0.9 m from the stylobate ends.

    Columns are one ``column-array`` centred on the stylobate; spacing follows
    from the fixed end insets and the count, so the end columns do not move when
    the count changes. Entablature 1.8 m high on the column tops; pediment on it.
    """

    parameters = (
        Parameter("stylobate_length", 12.0, "m", epistemic_status="declared"),
        Parameter("end_inset", 0.9, "m", epistemic_status="declared"),
        Parameter("column_count", 6, "count", epistemic_status="declared"),
        Parameter("column_radius", 0.45, "m", epistemic_status="declared"),
        Parameter("column_height", 9.0, "m", epistemic_status="declared"),
        Parameter("column_spacing", 2.04, "m", expr="(stylobate_length - 2 * end_inset) / (column_count - 1)"),
        Parameter("column_clear_spacing", 1.14, "m", expr="column_spacing - 2 * column_radius"),
        Parameter("max_clear_spacing", 3.0, "m", epistemic_status="declared"),
    )
    entities = [
        _component("portico", None, "arrival-and-buttress", "a hexastyle portico"),
        _component("portico-base", "portico", "vertical-support", "stylobate"),
        _component("portico-columns", "portico", "vertical-support", "columns"),
        _component("portico-entablature", "portico", "load-distribution", "entablature"),
        _component("portico-roof", "portico", "weather-enclosure", "pediment roof"),
        _level("level-ground", "terrain-grade", 0.0),
        _element("stylobate", "portico-base", "prism", {"base": {"level": "level-ground"}},
                 {"profile": [[0.0, -1.2], [12.0, -1.2], [12.0, 1.2], [0.0, 1.2]], "height": 0.6}),
        _element("columns", "portico-columns", "column-array",
                 {"at": {"point": [6.0, 0.0]}, "direction": "X", "base": {"datum": "stylobate-top"}},
                 {"count": "@column_count", "spacing": "@column_spacing", "radius": "@column_radius", "height": "@column_height", "segments": 24}),
        _element("entablature", "portico-entablature", "beam",
                 {"from": {"point": [0.9, 0.0]}, "to": {"point": [11.1, 0.0]}, "base": {"datum": "columns-top"}, "support": "columns"},
                 {"depth": 1.2, "height": 1.8, "end_overhang": 0.6}),
        _element("pediment", "portico-roof", "pediment",
                 {"from": {"point": [0.3, 0.6]}, "to": {"point": [11.7, 0.6]}, "base": {"datum": "entablature-top"}, "support": "entablature"},
                 {"rise": 2.2, "thickness": 1.2}),
        Entity("axis-x", "GridAxis@1", {"role": "X", "origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]}, basis_refs=BASIS),
    ]
    relations = (
        Relation("p1-columns-on-stylobate", "support", "stylobate", "columns", validator=ValidatorBinding("support_contact", tolerance=0.001)),
        Relation("p2-entablature-on-columns", "support", "columns", "entablature", validator=ValidatorBinding("support_contact", tolerance=0.001)),
        Relation("pediment-on-entablature", "support", "entablature", "pediment", validator=ValidatorBinding("support_contact", tolerance=0.001)),
    )
    obligations = (
        DesignObligation("p5-clear-spacing", "Clear spacing between columns stays within the entablature's span limit; structure not yet checked.",
                         "decision:technical-stage-span-limit", subject_refs=("parameter:column_clear_spacing", "parameter:max_clear_spacing"),
                         validator_ref=AT_MOST),
    )
    return _record("f1-portico", entities, parameters, relations, obligations)


# ---------------------------------------------------------------- F2 portico, transition, rotunda

def f2_portico_rotunda() -> StateRecord:
    """Rotunda (outer radius 10.0 m, wall 1.2 m) with cornice, drum and dome; a 12 x 4 m transition block; a tetrastyle portico.

    The transition block abuts the rotunda wall (plan depth -14 .. -10); its roof
    slab overhangs 1.0 m towards the portico. The portico's rear entablature runs
    under that overhang with 0.2 m clear. J1 is declared as a clearance between
    the transition roof and the rotunda cornice. J3 (roof overhang clears the rear
    entablature) is not declared anywhere in this record.
    """

    parameters = (
        Parameter("portico_column_height", 9.0, "m", epistemic_status="declared"),
        Parameter("transition_height", 11.2, "m", epistemic_status="declared"),
    )
    entities = [
        _component("building", None, "whole-building", "rotunda with portico"),
        _component("rotunda", "building", "enclosure-and-load-distribution", "round hall"),
        _component("transition", "building", "enclosure-and-load-distribution", "transition block"),
        _component("portico", "building", "arrival-and-buttress", "front portico"),
        _level("level-ground", "terrain-grade", 0.0),
        _element("rotunda-wall", "rotunda", "ring", {"at": {"point": [0.0, 0.0]}, "base": {"level": "level-ground"}},
                 {"inner_radius": 8.8, "outer_radius": 10.0, "height": 12.0, "pieces": 8}),
        _element("rotunda-cornice", "rotunda", "ring", {"at": {"point": [0.0, 0.0]}, "base": {"offset_from": {"level": "level-ground", "offset": 12.0}}},
                 {"inner_radius": 9.4, "outer_radius": 10.3, "height": 0.6, "pieces": 8}),
        _element("rotunda-drum", "rotunda", "shell", {"at": {"point": [0.0, 0.0]}, "base": {"offset_from": {"level": "level-ground", "offset": 12.6}}},
                 {"kind": "cylinder", "outer_radius": 9.4, "thickness": 1.0, "height": 4.0, "segments": 32}),
        _element("rotunda-dome", "rotunda", "shell", {"at": {"point": [0.0, 0.0]}, "base": {"datum": "rotunda-drum-top"}},
                 {"kind": "dome", "outer_radius": 9.4, "thickness": 0.6, "height": 7.0, "segments": 32, "rings": 8}),
        _element("transition-body", "transition", "prism", {"base": {"level": "level-ground"}},
                 {"profile": [[-6.0, -14.0], [6.0, -14.0], [6.0, -10.0], [-6.0, -10.0]], "height": "@transition_height"}),
        _element("transition-roof", "transition", "prism", {"base": {"datum": "transition-body-top"}},
                 {"profile": [[-6.3, -15.0], [6.3, -15.0], [6.3, -10.0], [-6.3, -10.0]], "height": 0.3}),
        _element("portico-stylobate", "portico", "prism", {"base": {"level": "level-ground"}},
                 {"profile": [[-6.0, -19.0], [6.0, -19.0], [6.0, -14.0], [-6.0, -14.0]], "height": 1.0}),
        _element("portico-columns-front", "portico", "column-array",
                 {"at": {"point": [0.0, -18.0]}, "direction": "X", "base": {"datum": "portico-stylobate-top"}},
                 {"count": 4, "spacing": 3.0, "radius": 0.45, "height": "@portico_column_height", "segments": 24}),
        _element("portico-columns-rear", "portico", "column-array",
                 {"at": {"point": [0.0, -15.0]}, "direction": "X", "base": {"datum": "portico-stylobate-top"}},
                 {"count": 4, "spacing": 3.0, "radius": 0.45, "height": "@portico_column_height", "segments": 24}),
        _element("portico-entablature-front", "portico", "beam",
                 {"from": {"point": [-4.5, -18.0]}, "to": {"point": [4.5, -18.0]}, "base": {"datum": "portico-columns-front-top"}, "support": "portico-columns-front"},
                 {"depth": 0.9, "height": 1.0, "end_overhang": 0.6}),
        _element("portico-entablature-rear", "portico", "beam",
                 {"from": {"point": [-4.5, -15.0]}, "to": {"point": [4.5, -15.0]}, "base": {"datum": "portico-columns-rear-top"}, "support": "portico-columns-rear"},
                 {"depth": 0.9, "height": 1.0, "end_overhang": 0.6}),
        _element("portico-pediment", "portico", "pediment",
                 {"from": {"point": [-5.1, -17.55]}, "to": {"point": [5.1, -17.55]}, "base": {"datum": "portico-entablature-front-top"}, "support": "portico-entablature-front"},
                 {"rise": 1.5, "thickness": 0.9}),
        Entity("axis-x", "GridAxis@1", {"role": "X", "origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]}, basis_refs=BASIS),
    ]
    relations = (
        Relation("j1-transition-below-cornice", "interface", "transition-roof", "rotunda-cornice",
                 validator=ValidatorBinding("clearance_interval", interval_m=(0.3, 100.0))),
        Relation("j2-pediment-clear-of-drum", "interface", "portico-pediment", "rotunda-drum",
                 validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 1000.0))),
        Relation("rear-entablature-on-columns", "support", "portico-columns-rear", "portico-entablature-rear",
                 validator=ValidatorBinding("support_contact", tolerance=0.001)),
        Relation("front-entablature-on-columns", "support", "portico-columns-front", "portico-entablature-front",
                 validator=ValidatorBinding("support_contact", tolerance=0.001)),
    )
    return _record("f2-portico-rotunda", entities, parameters, relations)


# ---------------------------------------------------------------- F3 wall, door, lintel, stair

def f3_wall_stair() -> StateRecord:
    """Wall 6.0 x 3.0 x 0.2 m with a 1.2 x 2.1 m door; lintel bearing 0.15 m each side; stair L1 0.0 -> L2 3.0.

    The wall runs along x on the plan line z = 0 and takes its thickness on
    z in [-0.2, 0]. The stair (17 risers of 3/17 m, 17 goings of 0.28 m) climbs
    along +x at z = 2.0 and lands against the L2 slab, whose stairwell notch
    leaves the lower steps under the slab edge.
    """

    parameters = (
        Parameter("door_center", 2.0, "m", epistemic_status="declared"),
        Parameter("door_width", 1.2, "m", epistemic_status="declared"),
        Parameter("door_height", 2.1, "m", epistemic_status="declared"),
        Parameter("lintel_bearing", 0.15, "m", epistemic_status="declared"),
        Parameter("lintel_left", 1.25, "m", expr="door_center - door_width / 2 - lintel_bearing"),
        Parameter("lintel_right", 2.75, "m", expr="door_center + door_width / 2 + lintel_bearing"),
        Parameter("storey_height", 3.0, "m", epistemic_status="declared"),
        Parameter("stair_risers", 17, "count", epistemic_status="declared"),
        Parameter("stair_rise", 3.0 / 17, "m", expr="storey_height / stair_risers"),
    )
    door_type = {"type_id": "door-type", "frame_width": 0.05, "frame_depth": 0.2, "frame_projection": 0.0, "leaf_thickness": 0.04,
                 "leaf_offset": 0.0, "leaf_count": 1, "leaf_gap": 0.005, "clearance_bottom": 0.01, "clearance_top": 0.005}
    entities = [
        _component("building", None, "whole-building", "one storey with a stair"),
        _component("walls", "building", "weather-enclosure-and-opening-host", "wall"),
        _component("lintels", "building", "load-distribution", "lintel"),
        _component("floors", "building", "load-distribution", "upper floor"),
        _component("stairs", "building", "vertical-circulation", "stair"),
        _level("level-1", "ground-floor", 0.0),
        _level("level-2", "upper-floor", 3.0),
        _element("wall", "walls", "wall",
                 {"line": {"from": {"point": [0.0, 0.0]}, "to": {"point": [6.0, 0.0]}}, "base": {"level": "level-1"}, "top": {"level": "level-2"}},
                 {"thickness": 0.2, "types": [door_type],
                  "openings": [{"opening_id": "door", "kind": "door", "along": "@door_center", "width": "@door_width",
                                "sill": 0.0, "head": "@door_height", "type_id": "door-type"}]}),
        _element("lintel", "lintels", "prism", {"base": {"offset_from": {"level": "level-1", "offset": "@door_height"}}},
                 {"profile": [["@lintel_left", -0.2], ["@lintel_right", -0.2], ["@lintel_right", 0.0], ["@lintel_left", 0.0]], "height": 0.2}),
        _element("slab-l2", "floors", "prism", {"base": {"offset_from": {"level": "level-2", "offset": -0.2}}},
                 {"profile": [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0], [5.36, 4.0], [5.36, 1.4], [1.6, 1.4], [1.6, 4.0], [0.0, 4.0]], "height": 0.2}),
        _element("stair", "stairs", "stair",
                 {"from": {"point": [0.6, 2.0]}, "to": {"point": [5.36, 2.0]}, "base": {"level": "level-1"}, "top": {"level": "level-2"}},
                 {"count": "@stair_risers", "rise": "@stair_rise", "going": 0.28, "width": 1.0}),
    ]
    relations = (
        Relation("lintel-bearing", "dependency", "wall", "lintel",
                 validator=ValidatorBinding("lintel_minimum_bearing", tolerance=0.001),
                 parameters={"opening_object_id": "obj-wall-aperture-door", "lintel_object_id": "obj-lintel",
                             "span_axis": "x", "minimum_bearing_m": 0.15}),
        Relation("stair-lands-on-l2", "support", "stair", "slab-l2", validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 0.001))),
    )
    return _record("f3-wall-stair", entities, parameters, relations)


FIXTURES: dict[str, Callable[[], StateRecord]] = {
    "F1-portico": f1_portico,
    "F2-portico-rotunda": f2_portico_rotunda,
    "F3-wall-stair": f3_wall_stair,
}
