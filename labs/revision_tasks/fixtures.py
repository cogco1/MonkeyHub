"""Three synthetic, parametric building fixtures as in-memory State Records (fixture version 2).

Each fixture is an authored ``StateRecord`` holding:

- Component@1, Level@1 and Element@1 rows;
- declared parameters (literal or derived by expression);
- checked relations;
- obligations, each with a validator over two parameters;
- evidence as Reading@1 rows, each marked ``current`` or ``superseded``.

Nothing here writes a project: ``bound`` binds a record to a synthetic run so
the geometry compiler can check exact-base identity, which is a plain value and
no P036 I/O.

Coordinates follow the program frame: x along, y up, z plan depth. A task
changes a fixture only through a typed ``StateRecordOperator``.

Version 2 follows the reviewed gold v2 and its history events: the commitments
K1–K4, the evidence for the stale-evidence tasks, the parameters the T2–T8
tasks move, and three relations left out of the record by design.

F1  portico on a stylobate. K1 keeps the central clear opening at least 1.0 m;
    P5 keeps the clear spacing at most 3.0 m (open, structure unchecked). The
    pediment sits at an absolute 11.4 m: the dependency "pediment on
    entablature top" is undeclared by design.
F2  portico, transition block and rotunda. J0 keeps the transition block
    against the rotunda wall (its rear face follows the radius); J1 (also an
    obligation) keeps the transition roof 0.3 m below the cornice; K2 keeps
    the dome apex at most 24.5 m. J3, the transition roof clearing the
    portico's rear entablature, is undeclared by design.
F3  wall, door, lintel and a straight stair. K3 keeps at least 0.5 m of wall
    beside the opening; K4 keeps at least 0.15 m of wall above the lintel; the
    riser limit is an obligation. "Stair clear of the wall" is undeclared by
    design.
"""

from __future__ import annotations

from typing import Any, Callable

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import Entity, Parameter, Relation, StateRecord, ValidatorBinding

PROJECT_ID = "revision-tasks"
FIXTURE_VERSION = "2"
BASIS = ("evidence:synthetic-fixture",)
SYNTHETIC_BASE_SHA256 = "1" * 64

# Obligations the record declares between two parameters. The kernel stores
# obligations but evaluates none; the harness evaluates these two kinds (pipeline.py).
AT_MOST = "validator:parameter-at-most"
AT_LEAST = "validator:parameter-at-least"


def _component(cid: str, parent: str | None, kind: str, intent: str) -> Entity:
    fields = {"schema": "DesignComponent@1", "semantic_kind": kind, "intent": intent, "maturity": "schematic", "revision": 1,
              "volume_ids": [], "unresolved_child_roles": [], "source_refs": list(BASIS)}
    return Entity(cid, "Component@1", fields, parent_id=parent)


def _level(level_id: str, role: str, elevation: float) -> Entity:
    return Entity(level_id, "Level@1", {"role": role, "elevation": elevation}, basis_refs=BASIS)


def _element(element_id: str, component: str, producer: str, references: dict[str, Any], params: dict[str, Any]) -> Entity:
    return Entity(element_id, "Element@1", {"component_id": component, "producer": producer, "references": references, "params": params},
                  parent_id=component, basis_refs=BASIS)


def _evidence(evidence_id: str, component: str, note: str, value: float, status: str, source: str,
              superseded_by: str | None = None) -> Entity:
    fields: dict[str, Any] = {"note": note, "value": value, "unit": "m", "status": status, "source_ref": source}
    if superseded_by is not None:
        fields["superseded_by"] = superseded_by
    return Entity(evidence_id, "Reading@1", fields, parent_id=component, basis_refs=(source,))


def _bound(obligation_id: str, statement: str, source: str, left: str, right: str, validator: str) -> DesignObligation:
    return DesignObligation(obligation_id, statement, source, subject_refs=(f"parameter:{left}", f"parameter:{right}"), validator_ref=validator)


def _record(run_id: str, entities: list[Entity], parameters: tuple[Parameter, ...], relations: tuple[Relation, ...],
            obligations: tuple[DesignObligation, ...] = ()) -> StateRecord:
    return StateRecord(PROJECT_ID, run_id, tuple(entities), parameters=parameters, relations=relations, obligations=obligations,
                       evidence_refs=BASIS, decision_ref=f"decision:{run_id}-fixture")


def bound(record: StateRecord) -> StateRecord:
    """The record bound to its synthetic run; the base is a fixed constant, not a project version."""

    return record.bound_to(RunRef(PROJECT_ID, record.run_id, ProjectVersionRef(PROJECT_ID, 0, SYNTHETIC_BASE_SHA256)))


def _x_axis() -> Entity:
    return Entity("axis-x", "GridAxis@1", {"role": "X", "origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]}, basis_refs=BASIS)


# ---------------------------------------------------------------- F1 portico colonnade

def f1_portico() -> StateRecord:
    """Stylobate 12.0 m; six columns of 0.9 m diameter, 9.0 m high; end column axes fixed at x 0.9 and 11.1 m.

    The columns are one ``column-array`` centred at x 6.0. Their spacing follows
    from the fixed 10.2 m span and the count, so neither a count change nor a
    longer stylobate moves the end columns. The entablature, 1.8 m high, sits on
    the column tops; the pediment is placed at an absolute 11.4 m.
    """

    parameters = (
        Parameter("stylobate_length", 12.0, "m", epistemic_status="declared"),
        Parameter("stylobate_depth", 2.4, "m", epistemic_status="declared"),
        Parameter("stylobate_front", -1.2, "m", expr="0 - stylobate_depth / 2"),
        Parameter("stylobate_back", 1.2, "m", expr="stylobate_depth / 2"),
        Parameter("column_span", 10.2, "m", epistemic_status="declared"),
        Parameter("column_count", 6, "count", epistemic_status="declared"),
        Parameter("column_radius", 0.45, "m", epistemic_status="declared"),
        Parameter("column_height", 9.0, "m", epistemic_status="declared"),
        Parameter("entablature_height", 1.8, "m", epistemic_status="declared"),
        Parameter("column_spacing", 2.04, "m", expr="column_span / (column_count - 1)"),
        Parameter("column_clear_spacing", 1.14, "m", expr="column_spacing - 2 * column_radius"),
        Parameter("max_clear_spacing", 3.0, "m", epistemic_status="declared"),
        Parameter("min_central_clear", 1.0, "m", epistemic_status="declared"),
    )
    entities = [
        _component("portico", None, "arrival-and-buttress", "a hexastyle portico"),
        _component("portico-base", "portico", "vertical-support", "stylobate"),
        _component("portico-columns", "portico", "vertical-support", "columns"),
        _component("portico-entablature", "portico", "load-distribution", "entablature"),
        _component("portico-roof", "portico", "weather-enclosure", "pediment roof"),
        _level("level-ground", "terrain-grade", 0.0),
        # The pediment's own datum: a level, not the entablature top, so the dependency stays undeclared.
        # (A level rather than an offset: produce_pediment appends base_offset out of order and the
        # geometry program refuses unsorted parameter names.)
        _level("level-pediment", "pediment-springing", 11.4),
        _element("stylobate", "portico-base", "prism", {"base": {"level": "level-ground"}},
                 {"profile": [[0.0, "@stylobate_front"], ["@stylobate_length", "@stylobate_front"], ["@stylobate_length", "@stylobate_back"],
                              [0.0, "@stylobate_back"]], "height": 0.6}),
        _element("columns", "portico-columns", "column-array",
                 {"at": {"point": [6.0, 0.0]}, "direction": "X", "base": {"datum": "stylobate-top"}},
                 {"count": "@column_count", "spacing": "@column_spacing", "radius": "@column_radius", "height": "@column_height", "segments": 24}),
        _element("entablature", "portico-entablature", "beam",
                 {"from": {"point": [0.9, 0.0]}, "to": {"point": [11.1, 0.0]}, "base": {"datum": "columns-top"}, "support": "columns"},
                 {"depth": 1.2, "height": "@entablature_height", "end_overhang": 0.6}),
        _element("pediment", "portico-roof", "pediment",
                 {"from": {"point": [0.3, 0.6]}, "to": {"point": [11.7, 0.6]}, "base": {"level": "level-pediment"}},
                 {"rise": 2.2, "thickness": 1.2}),
        _evidence("evidence-site-s1", "portico", "usable site length 12.4 (site survey 1)", 12.4, "superseded",
                  "evidence:site-survey-1", superseded_by="evidence-site-s2"),
        _evidence("evidence-site-s2", "portico", "usable site length 12.2 (site survey 2)", 12.2, "current", "evidence:site-survey-2"),
        _x_axis(),
    ]
    relations = (
        Relation("p1-columns-on-stylobate", "support", "stylobate", "columns", validator=ValidatorBinding("support_contact", tolerance=0.001)),
        Relation("p2-entablature-on-columns", "support", "columns", "entablature", validator=ValidatorBinding("support_contact", tolerance=0.001)),
    )
    obligations = (
        _bound("p5-clear-spacing", "Clear spacing between columns stays within the entablature's span limit; structure not yet checked.",
               "decision:technical-stage-span-limit", "column_clear_spacing", "max_clear_spacing", AT_MOST),
        _bound("k1-central-clear", "The clear opening between the two central columns stays at least 1.0 m (main entrance).",
               "decision:architecture-stage-entrance", "column_clear_spacing", "min_central_clear", AT_LEAST),
    )
    return _record("f1-portico", entities, parameters, relations, obligations)


# ---------------------------------------------------------------- F2 portico, transition, rotunda

def f2_portico_rotunda() -> StateRecord:
    """Rotunda (outer radius 10.0 m, wall 1.2 m, cornice base 12.0 m) with cornice, drum and dome; a 12 x 4 m transition block; a tetrastyle portico.

    The transition block's rear face sits on the rotunda's outer radius (J0); its
    roof slab overhangs 1.0 m towards the portico, 0.2 m above the rear
    entablature. J3 (that overhang clears the rear entablature) is declared
    nowhere in this record.
    """

    parameters = (
        Parameter("rotunda_radius", 10.0, "m", epistemic_status="declared"),
        Parameter("cornice_base", 12.0, "m", epistemic_status="declared"),
        Parameter("cornice_height", 0.6, "m", epistemic_status="declared"),
        Parameter("drum_height", 4.0, "m", epistemic_status="declared"),
        Parameter("dome_height", 7.0, "m", epistemic_status="declared"),
        Parameter("portico_column_height", 9.0, "m", epistemic_status="declared"),
        Parameter("transition_height", 11.2, "m", epistemic_status="declared"),
        Parameter("transition_roof_thickness", 0.3, "m", epistemic_status="declared"),
        Parameter("max_dome_apex", 24.5, "m", epistemic_status="declared"),
        Parameter("rotunda_inner", 8.8, "m", expr="rotunda_radius - 1.2"),
        Parameter("cornice_inner", 9.4, "m", expr="rotunda_radius - 0.6"),
        Parameter("cornice_outer", 10.3, "m", expr="rotunda_radius + 0.3"),
        Parameter("drum_outer", 9.4, "m", expr="rotunda_radius - 0.6"),
        Parameter("drum_base", 12.6, "m", expr="cornice_base + cornice_height"),
        Parameter("dome_apex", 23.6, "m", expr="cornice_base + cornice_height + drum_height + dome_height"),
        Parameter("transition_rear", -10.0, "m", expr="0 - rotunda_radius"),
        Parameter("transition_roof_top", 11.5, "m", expr="transition_height + transition_roof_thickness"),
        Parameter("transition_roof_limit", 11.7, "m", expr="cornice_base - 0.3"),
    )
    entities = [
        _component("building", None, "whole-building", "rotunda with portico"),
        _component("rotunda", "building", "enclosure-and-load-distribution", "round hall"),
        _component("transition", "building", "enclosure-and-load-distribution", "transition block"),
        _component("portico", "building", "arrival-and-buttress", "front portico"),
        _level("level-ground", "terrain-grade", 0.0),
        _element("rotunda-wall", "rotunda", "ring", {"at": {"point": [0.0, 0.0]}, "base": {"level": "level-ground"}},
                 {"inner_radius": "@rotunda_inner", "outer_radius": "@rotunda_radius", "height": "@cornice_base", "pieces": 8}),
        _element("rotunda-cornice", "rotunda", "ring", {"at": {"point": [0.0, 0.0]}, "base": {"offset_from": {"level": "level-ground", "offset": "@cornice_base"}}},
                 {"inner_radius": "@cornice_inner", "outer_radius": "@cornice_outer", "height": "@cornice_height", "pieces": 8}),
        _element("rotunda-drum", "rotunda", "shell", {"at": {"point": [0.0, 0.0]}, "base": {"offset_from": {"level": "level-ground", "offset": "@drum_base"}}},
                 {"kind": "cylinder", "outer_radius": "@drum_outer", "thickness": 1.0, "height": "@drum_height", "segments": 32}),
        _element("rotunda-dome", "rotunda", "shell", {"at": {"point": [0.0, 0.0]}, "base": {"datum": "rotunda-drum-top"}},
                 {"kind": "dome", "outer_radius": "@drum_outer", "thickness": 0.6, "height": "@dome_height", "segments": 32, "rings": 8}),
        _element("transition-body", "transition", "prism", {"base": {"level": "level-ground"}},
                 {"profile": [[-6.0, -14.0], [6.0, -14.0], [6.0, "@transition_rear"], [-6.0, "@transition_rear"]], "height": "@transition_height"}),
        _element("transition-roof", "transition", "prism", {"base": {"datum": "transition-body-top"}},
                 {"profile": [[-6.3, -15.0], [6.3, -15.0], [6.3, "@transition_rear"], [-6.3, "@transition_rear"]], "height": "@transition_roof_thickness"}),
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
        _evidence("evidence-cornice-old", "rotunda", "rotunda cornice base 11.8 (early estimate)", 11.8, "superseded",
                  "evidence:cornice-estimate", superseded_by="evidence-cornice"),
        _evidence("evidence-cornice", "rotunda", "rotunda cornice base 12.0 (measured)", 12.0, "current", "evidence:cornice-survey"),
        _x_axis(),
    ]
    relations = (
        Relation("j0-transition-against-rotunda", "interface", "transition-body", "rotunda-wall",
                 validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 0.25))),
        Relation("j1-transition-below-cornice", "interface", "transition-roof", "rotunda-cornice",
                 validator=ValidatorBinding("clearance_interval", interval_m=(0.3, 100.0))),
        Relation("j2-pediment-clear-of-drum", "interface", "portico-pediment", "rotunda-drum",
                 validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 1000.0))),
        Relation("rear-entablature-on-columns", "support", "portico-columns-rear", "portico-entablature-rear",
                 validator=ValidatorBinding("support_contact", tolerance=0.001)),
        Relation("front-entablature-on-columns", "support", "portico-columns-front", "portico-entablature-front",
                 validator=ValidatorBinding("support_contact", tolerance=0.001)),
    )
    obligations = (
        _bound("j1-roof-below-cornice", "The transition roof top stays at least 0.3 m below the rotunda cornice base.",
               "decision:architecture-stage-interface", "transition_roof_top", "transition_roof_limit", AT_MOST),
        _bound("k2-dome-apex", "The dome apex stays at or below 24.5 m (skyline limit agreed with the city).",
               "decision:massing-stage-skyline", "dome_apex", "max_dome_apex", AT_MOST),
    )
    return _record("f2-portico-rotunda", entities, parameters, relations, obligations)


# ---------------------------------------------------------------- F3 wall, door, lintel, stair

def f3_wall_stair() -> StateRecord:
    """Wall 6.0 x 3.0 x 0.2 m with a 1.2 x 2.1 m door centred at x 2.0; lintel bearing 0.15 m; stair L1 0.0 -> L2 3.0.

    The wall runs along x on the plan line z = 0 and takes its thickness on
    z in [-0.2, 0]. The stair (17 risers of 3/17 m, 17 goings of 0.28 m) climbs
    along +x at plan depth ``stair_y`` and lands against the L2 slab, whose
    stairwell notch leaves the lower steps under the slab edge.
    """

    parameters = (
        Parameter("wall_length", 6.0, "m", epistemic_status="declared"),
        Parameter("door_center", 2.0, "m", epistemic_status="declared"),
        Parameter("door_width", 1.2, "m", epistemic_status="declared"),
        Parameter("door_height", 2.1, "m", epistemic_status="declared"),
        Parameter("lintel_bearing", 0.15, "m", epistemic_status="declared"),
        Parameter("lintel_depth", 0.2, "m", epistemic_status="declared"),
        Parameter("lintel_left", 1.25, "m", expr="door_center - door_width / 2 - lintel_bearing"),
        Parameter("lintel_right", 2.75, "m", expr="door_center + door_width / 2 + lintel_bearing"),
        Parameter("storey_height", 3.0, "m", epistemic_status="declared"),
        Parameter("stair_risers", 17, "count", epistemic_status="declared"),
        Parameter("stair_rise", 3.0 / 17, "m", expr="storey_height / stair_risers"),
        Parameter("max_riser", 0.18, "m", epistemic_status="declared"),
        Parameter("stair_y", 2.0, "m", epistemic_status="declared"),
        Parameter("min_pier", 0.5, "m", epistemic_status="declared"),
        Parameter("left_pier", 1.4, "m", expr="door_center - door_width / 2"),
        Parameter("right_pier", 3.4, "m", expr="wall_length - door_center - door_width / 2"),
        Parameter("min_wall_above_lintel", 0.15, "m", epistemic_status="declared"),
        Parameter("wall_above_lintel", 0.7, "m", expr="storey_height - door_height - lintel_depth"),
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
                 {"line": {"from": {"point": [0.0, 0.0]}, "to": {"point": ["@wall_length", 0.0]}}, "base": {"level": "level-1"}, "top": {"level": "level-2"}},
                 {"thickness": 0.2, "types": [door_type],
                  "openings": [{"opening_id": "door", "kind": "door", "along": "@door_center", "width": "@door_width",
                                "sill": 0.0, "head": "@door_height", "type_id": "door-type"}]}),
        _element("lintel", "lintels", "prism", {"base": {"offset_from": {"level": "level-1", "offset": "@door_height"}}},
                 {"profile": [["@lintel_left", -0.2], ["@lintel_right", -0.2], ["@lintel_right", 0.0], ["@lintel_left", 0.0]], "height": "@lintel_depth"}),
        _element("slab-l2", "floors", "prism", {"base": {"offset_from": {"level": "level-2", "offset": -0.2}}},
                 {"profile": [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0], [5.36, 4.0], [5.36, 1.4], [1.6, 1.4], [1.6, 4.0], [0.0, 4.0]], "height": 0.2}),
        _element("stair", "stairs", "stair",
                 {"from": {"point": [0.6, "@stair_y"]}, "to": {"point": [5.36, "@stair_y"]}, "base": {"level": "level-1"}, "top": {"level": "level-2"}},
                 {"count": "@stair_risers", "rise": "@stair_rise", "going": 0.28, "width": 1.0}),
        _evidence("evidence-l2-old", "floors", "L2 = 3.15 (early drawing)", 3.15, "superseded", "evidence:early-drawing",
                  superseded_by="evidence-l2"),
        _evidence("evidence-l2", "floors", "L2 = 3.0 (revised section)", 3.0, "current", "evidence:revised-section"),
    ]
    relations = (
        Relation("lintel-bearing", "dependency", "wall", "lintel",
                 validator=ValidatorBinding("lintel_minimum_bearing", tolerance=0.001),
                 parameters={"opening_object_id": "obj-wall-aperture-door", "lintel_object_id": "obj-lintel",
                             "span_axis": "x", "minimum_bearing_m": 0.15}),
        Relation("stair-lands-on-l2", "support", "stair", "slab-l2", validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 0.001))),
    )
    obligations = (
        _bound("k3-left-pier", "At least 0.5 m of wall on the left of the opening.", "decision:architecture-stage-piers",
               "left_pier", "min_pier", AT_LEAST),
        _bound("k3-right-pier", "At least 0.5 m of wall on the right of the opening.", "decision:architecture-stage-piers",
               "right_pier", "min_pier", AT_LEAST),
        _bound("k4-wall-above-lintel", "At least 0.15 m of wall above every lintel.", "decision:architecture-stage-lintels",
               "wall_above_lintel", "min_wall_above_lintel", AT_LEAST),
        _bound("st2-riser-limit", "No riser above the riser limit.", "decision:architecture-stage-stair",
               "stair_rise", "max_riser", AT_MOST),
    )
    return _record("f3-wall-stair", entities, parameters, relations, obligations)


FIXTURES: dict[str, Callable[[], StateRecord]] = {
    "F1-portico": f1_portico,
    "F2-portico-rotunda": f2_portico_rotunda,
    "F3-wall-stair": f3_wall_stair,
}
