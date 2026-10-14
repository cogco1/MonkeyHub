"""The X3 buildings as authored records. BB, a three-storey house and studio (spec v2), is here; BA follows.

Same conventions as ``fixtures``: an in-memory ``StateRecord`` of Component@1, Level@1 and Element@1 rows built
with the production element producers (``wall`` with hosted openings and door types, ``beam`` lintels, ``prism``
slabs voided by hidden ``prism`` stair holes, ``stair`` flights), declared parameters, checked relations and
obligations. Plan points are ``[x, y]`` of the model file (east, north); the program frame carries the file's y
as its plan z.

BB (``gold_draft/BB_三层住宅_规格与请求脚本_v2.md``):

- 18 x 12 m outside, three storeys of 3.0 m, roof terrace with 1.1 m parapets and a stair-head;
- 28 walls, 46 openings (38 windows, 8 doors with frames and leaves), 46 lintels, four slabs (three with a stair
  hole voided by a hidden prism), three straight flights alternating west and east in the core;
- declared: every lintel's bearing (R2), each flight landing on the slab above (R5) and standing on the slab below
  (R6), parapets on the top-storey walls and the stair-head walls on the roof slab (R13); R8, R9 and K3, K4, K6 are
  obligations stated in words (no validator the kernel or the harness evaluates);
- undeclared by design: the flights' 0.10 m side clearance to the core walls.
"""

from __future__ import annotations

from typing import Any

from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import Entity, Parameter, Relation, StateRecord, ValidatorBinding

from labs.revision_tasks.fixtures import _component, _element, _level, _record

LEVELS = (("level-L1", "ground-floor", 0.0), ("level-L2", "second-floor", 3.0), ("level-L3", "third-floor", 6.0), ("level-R", "roof", 9.0))
ABOVE = {"level-L1": "level-L2", "level-L2": "level-L3", "level-L3": "level-R"}
EXT, INT = 0.3, 0.2
SOUTH_AXES = {1: 2.0, 2: 5.5, 3: 9.0, 4: 12.5, 5: 16.0}
SIDE_AXES = {1: 4.0, 2: 8.0}


def _door_type(type_id: str, depth: float) -> dict[str, Any]:
    return {"type_id": type_id, "frame_width": 0.05, "frame_depth": depth, "frame_projection": 0.0, "leaf_thickness": 0.04,
            "leaf_offset": 0.0, "leaf_count": 1, "leaf_gap": 0.005, "clearance_bottom": 0.01, "clearance_top": 0.005}


class _Walls:
    """Collects walls, their openings and one lintel per opening, with the parameters the lintel ends need."""

    def __init__(self) -> None:
        self.entities: list[Entity] = []
        self.parameters: list[Parameter] = []
        self.relations: list[Relation] = []

    def wall(self, wall_id: str, component: str, start: tuple[float, float], end: tuple[float, float], thickness: float,
             base: str, top: dict[str, Any] | None, height: float | None, openings: list[dict[str, Any]], types: list[dict[str, Any]]) -> None:
        references: dict[str, Any] = {"line": {"from": {"point": list(start)}, "to": {"point": list(end)}}, "base": {"level": base}}
        params: dict[str, Any] = {"thickness": thickness}
        if top is not None:
            references["top"] = top
        if height is not None:
            params["height"] = height
        if types:
            params["types"] = types
        if openings:
            params["openings"] = [{k: v for k, v in o.items() if not k.startswith("_")} for o in openings]
        self.entities.append(_element(wall_id, component, "wall", references, params))
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / length, dy / length
        nx, ny = uy, -ux                                   # the side the thickness is laid on
        for o in openings:
            self._lintel(wall_id, o, start, (ux, uy), (nx, ny), thickness, base)

    def _lintel(self, wall_id: str, o: dict[str, Any], start, u, n, thickness: float, base: str) -> None:
        oid, along, width = o["opening_id"], o["along"], o["_width_param"]
        key = oid.replace("-", "_").lower()
        axis = 0 if abs(u[0]) > 0.5 else 1                  # the plan axis the wall runs along
        centre = round(start[axis] + u[axis] * along, 9)   # the opening centre on that axis
        across = round(start[1 - axis] + n[1 - axis] * thickness / 2, 9)   # the wall's centre line
        # the lintel's ends on the wall's axis: centre -/+ (width / 2 + bearing), so they follow the opening and the bearing
        reach = o["_width_value"] / 2 + 0.15
        self.parameters.append(Parameter(f"lintel_{key}_lo", round(centre - reach, 9), "m", expr=f"{centre} - ({width} / 2 + lintel_bearing)"))
        self.parameters.append(Parameter(f"lintel_{key}_hi", round(centre + reach, 9), "m", expr=f"{centre} + ({width} / 2 + lintel_bearing)"))
        points = {}
        for end in ("lo", "hi"):
            point: list[Any] = [None, None]
            point[axis], point[1 - axis] = f"@lintel_{key}_{end}", across
            points[end] = point
        points["a"], points["b"] = points["lo"], points["hi"]
        element = f"lintel-{oid}"
        self.entities.append(_element(element, "lintels", "beam",
                                      {"from": {"point": points["a"]}, "to": {"point": points["b"]},
                                       "base": {"offset_from": {"level": base, "offset": o["head"]}}},
                                      {"depth": thickness, "height": "@lintel_height"}))
        self.relations.append(Relation(f"r2-{oid}", "dependency", wall_id, element,
                                       validator=ValidatorBinding("lintel_minimum_bearing", tolerance=0.001),
                                       parameters={"opening_object_id": f"obj-{wall_id}-aperture-{oid}", "lintel_object_id": f"obj-{element}",
                                                   "span_axis": "x" if abs(u[0]) > 0.5 else "z", "minimum_bearing_m": 0.15}))


def _window(oid: str, along: float) -> dict[str, Any]:
    return {"opening_id": oid, "kind": "window", "along": round(along, 9), "width": "@window_width", "sill": "@window_sill",
            "head": "@window_head", "_width_param": "window_width", "_width_value": 1.5}


DOOR_WIDTHS = {"front": 1.1, "flat": 1.0, "roof": 1.0}


def _door(oid: str, along: float, kind: str, type_id: str) -> dict[str, Any]:
    return {"opening_id": oid, "kind": "door", "along": round(along, 9), "width": f"@{kind}_door_width", "sill": 0.0,
            "head": f"@{kind}_door_height", "type_id": type_id, "_width_param": f"{kind}_door_width", "_width_value": DOOR_WIDTHS[kind]}


def bb_house() -> StateRecord:
    parameters = [
        Parameter("window_width", 1.5, "m", epistemic_status="declared"),
        Parameter("window_height", 1.5, "m", epistemic_status="declared"),
        Parameter("window_sill", 0.9, "m", epistemic_status="declared"),
        Parameter("window_head", 2.4, "m", expr="window_sill + window_height"),
        Parameter("front_door_width", 1.1, "m", epistemic_status="declared"),
        Parameter("front_door_height", 2.4, "m", epistemic_status="declared"),
        Parameter("flat_door_width", 1.0, "m", epistemic_status="declared"),
        Parameter("flat_door_height", 2.1, "m", epistemic_status="declared"),
        Parameter("roof_door_width", 1.0, "m", epistemic_status="declared"),
        Parameter("roof_door_height", 2.1, "m", epistemic_status="declared"),
        Parameter("lintel_bearing", 0.15, "m", epistemic_status="declared"),
        Parameter("lintel_height", 0.2, "m", epistemic_status="declared"),
        Parameter("stair_going", 0.28, "m", epistemic_status="declared"),
        Parameter("stair_width", 1.1, "m", epistemic_status="declared"),
        Parameter("hole_goings", 4, "count", epistemic_status="declared"),
    ]
    flights = {"F1": ("level-L1", "level-L2", 8.40, 1.90, 1), "F2": ("level-L2", "level-L3", 9.60, 6.66, -1), "F3": ("level-L3", "level-R", 8.40, 1.90, 1)}
    for name, (_, _, x, y0, _) in flights.items():
        f = name.lower()
        parameters += [
            Parameter(f"{f}_x", x, "m", epistemic_status="declared"),
            Parameter(f"{f}_y0", y0, "m", epistemic_status="declared"),
            Parameter(f"{f}_storey", 3.0, "m", epistemic_status="declared"),
            Parameter(f"{f}_risers", 17, "count", epistemic_status="declared"),
            Parameter(f"{f}_rise", 3.0 / 17, "m", expr=f"{f}_storey / {f}_risers"),
        ]
    parameters += [
        Parameter("f1_y1", 6.66, "m", expr="f1_y0 + f1_risers * stair_going"),
        Parameter("f2_y1", 1.90, "m", expr="f2_y0 - f2_risers * stair_going"),
        Parameter("f3_y1", 6.66, "m", expr="f3_y0 + f3_risers * stair_going"),
        # R10: a hole follows its flight in y, from the low end plus four goings to the high end; x stays on the runway
        Parameter("hole_l2_y0", 3.02, "m", expr="f1_y0 + hole_goings * stair_going"),
        Parameter("hole_l3_y1", 5.54, "m", expr="f2_y0 - hole_goings * stair_going"),
        Parameter("hole_r_y0", 3.02, "m", expr="f3_y0 + hole_goings * stair_going"),
    ]
    entities: list[Entity] = [
        _component("building", None, "whole-building", "a three-storey house and studio"),
        _component("envelope", "building", "weather-enclosure-and-opening-host", "outer walls"),
        _component("core", "building", "vertical-circulation", "stair core walls"),
        _component("partitions", "building", "weather-enclosure-and-opening-host", "party walls"),
        _component("lintels", "building", "load-distribution", "lintels"),
        _component("floors", "building", "load-distribution", "floor and roof slabs"),
        _component("stairs", "core", "vertical-circulation", "straight flights"),
        _component("roof", "building", "weather-enclosure", "parapets and stair-head"),
        *(_level(i, role, z) for i, role, z in LEVELS),
    ]
    walls = _Walls()
    front_type, flat_type, roof_type = _door_type("door-front-type", EXT), _door_type("door-flat-type", INT), _door_type("door-roof-type", INT)
    for n in (1, 2, 3):
        level = f"level-L{n}"
        ext_top = {"level": ABOVE[level]}
        int_top = {"offset_from": {"level": ABOVE[level], "offset": -0.2}}
        south = [_window(f"win-S-L{n}-{k}", x) for k, x in SOUTH_AXES.items() if not (n == 1 and k == 3)]
        if n == 1:
            south.insert(2, _door("door-front", 9.0, "front", "door-front-type"))
        # south: line along +x at y 0.3 lays the thickness on y 0-0.3; along is x
        walls.wall(f"wall-S-L{n}", "envelope", (0.0, 0.3), (18.0, 0.3), EXT, level, ext_top, None, south, [front_type] if n == 1 else [])
        # north: line along -x at y 11.7 lays the thickness on y 11.7-12.0; along is 18 - x
        walls.wall(f"wall-N-L{n}", "envelope", (18.0, 11.7), (0.0, 11.7), EXT, level, ext_top, None,
                   [_window(f"win-N-L{n}-{k}", 18.0 - x) for k, x in SOUTH_AXES.items() if k != 3], [])
        # west: line along -y at x 0.3 lays the thickness on x 0-0.3; along is 11.7 - y
        walls.wall(f"wall-W-L{n}", "envelope", (0.3, 11.7), (0.3, 0.3), EXT, level, ext_top, None,
                   [_window(f"win-W-L{n}-{k}", 11.7 - y) for k, y in SIDE_AXES.items()], [])
        # east: line along +y at x 17.7 lays the thickness on x 17.7-18.0; along is y - 0.3
        walls.wall(f"wall-E-L{n}", "envelope", (17.7, 0.3), (17.7, 11.7), EXT, level, ext_top, None,
                   [_window(f"win-E-L{n}-{k}", y - 0.3) for k, y in SIDE_AXES.items()], [])
        flat_centre = 7.21 if n == 2 else 1.35
        # core west: line along +y at x 7.55 lays the thickness on x 7.55-7.75; core east likewise at x 10.25
        walls.wall(f"wall-core-W-L{n}", "core", (7.55, 0.3), (7.55, 8.26), INT, level, int_top, None,
                   [_door(f"door-flat-W-L{n}", flat_centre - 0.3, "flat", "door-flat-type")], [flat_type])
        walls.wall(f"wall-core-E-L{n}", "core", (10.25, 0.3), (10.25, 8.26), INT, level, int_top, None,
                   [_door(f"door-flat-E-L{n}", flat_centre - 0.3, "flat", "door-flat-type")], [flat_type])
        walls.wall(f"wall-core-N-L{n}", "core", (7.55, 8.46), (10.45, 8.46), INT, level, int_top, None, [], [])
        walls.wall(f"wall-party-L{n}", "partitions", (8.9, 8.46), (8.9, 11.7), INT, level, int_top, None, [], [])
    walls.wall("wall-stairhead-S", "roof", (7.55, 3.02), (10.45, 3.02), INT, "level-R", None, 2.6, [], [])
    walls.wall("wall-stairhead-N", "roof", (7.55, 8.46), (10.45, 8.46), INT, "level-R", None, 2.6,
               [_door("door-roof", 9.0 - 7.55, "roof", "door-roof-type")], [roof_type])
    walls.wall("wall-stairhead-W", "roof", (7.55, 3.02), (7.55, 8.26), INT, "level-R", None, 2.6, [], [])
    walls.wall("wall-stairhead-E", "roof", (10.25, 3.02), (10.25, 8.26), INT, "level-R", None, 2.6, [], [])
    entities += walls.entities
    parameters += walls.parameters
    relations = list(walls.relations)

    floor = [[0.3, 0.3], [17.7, 0.3], [17.7, 11.7], [0.3, 11.7]]
    holes = {"L2": ("level-L2", [7.75, 9.00], ["@hole_l2_y0", "@f1_y1"]), "L3": ("level-L3", [9.00, 10.25], ["@f2_y1", "@hole_l3_y1"]),
             "R": ("level-R", [7.75, 9.00], ["@hole_r_y0", "@f3_y1"])}
    for name, level in (("L1", "level-L1"), ("L2", "level-L2"), ("L3", "level-L3"), ("R", "level-R")):
        references: dict[str, Any] = {"base": {"offset_from": {"level": level, "offset": -0.2}}}
        if name in holes:
            references["voids"] = [f"hole-{name}"]
        entities.append(_element(f"slab-{name}", "floors", "prism", references, {"profile": floor, "height": 0.2}))
        if name in holes:
            _, (x0, x1), (y0, y1) = holes[name]
            # the hole is taller than the slab so the cut leaves no coplanar faces; it is hidden, a void
            entities.append(_element(f"hole-{name}", "floors", "prism", {"base": {"offset_from": {"level": level, "offset": -0.25}}},
                                     {"profile": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "height": 0.3}))
    for name, (base, top, _, _, sign) in flights.items():
        f = name.lower()
        entities.append(_element(f"stair-{name}", "stairs", "stair",
                                 {"from": {"point": [f"@{f}_x", f"@{f}_y0"]}, "to": {"point": [f"@{f}_x", f"@{f}_y1"]},
                                  "base": {"level": base}, "top": {"level": top}},
                                 {"count": f"@{f}_risers", "rise": f"@{f}_rise", "going": "@stair_going", "width": "@stair_width"}))
        below, above = f"slab-{base.removeprefix('level-')}", f"slab-{top.removeprefix('level-')}"
        relations.append(Relation(f"r5-{name}-lands-on-{above}", "support", f"stair-{name}", above,
                                  validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 0.001))))
        relations.append(Relation(f"r6-{name}-on-{below}", "support", below, f"stair-{name}",
                                  validator=ValidatorBinding("support_contact", tolerance=0.001)))
    parapets = {"S": [[0.0, 0.0], [18.0, 0.0], [18.0, 0.3], [0.0, 0.3]], "N": [[0.0, 11.7], [18.0, 11.7], [18.0, 12.0], [0.0, 12.0]],
                "W": [[0.0, 0.3], [0.3, 0.3], [0.3, 11.7], [0.0, 11.7]], "E": [[17.7, 0.3], [18.0, 0.3], [18.0, 11.7], [17.7, 11.7]]}
    for side, profile in parapets.items():
        entities.append(_element(f"parapet-{side}", "roof", "prism", {"base": {"datum": f"wall-{side}-L3-top"}}, {"profile": profile, "height": 1.1}))
        relations.append(Relation(f"r13-parapet-{side}", "support", f"wall-{side}-L3", f"parapet-{side}",
                                  validator=ValidatorBinding("support_contact", tolerance=0.001)))
    entities.append(_element("roof-stairhead", "roof", "prism", {"base": {"datum": "wall-stairhead-S-top"}},
                             {"profile": [[7.55, 2.82], [10.45, 2.82], [10.45, 8.46], [7.55, 8.46]], "height": 0.2}))
    # The north stair-head wall hosts the roof door, whose leaf and head frame do not bear on the slab: its seat is not a
    # one-object contact the support check can state, so R13 is declared for the three plain walls.
    for side in ("S", "W", "E"):
        relations.append(Relation(f"r13-stairhead-{side}", "support", "slab-R", f"wall-stairhead-{side}",
                                  validator=ValidatorBinding("support_contact", tolerance=0.001)))
    obligations = (
        DesignObligation("k3-piers", "Keep at least 0.5 m of wall on each side of every opening.", "decision:opening-request-0",
                         subject_refs=tuple(f"entity:{e.entity_id}" for e in walls.entities if e.fields["producer"] == "wall")),
        DesignObligation("k4-wall-above-lintels", "Keep at least 0.15 m of wall above every lintel.", "decision:opening-request-0",
                         subject_refs=tuple(f"entity:{e.entity_id}" for e in walls.entities if e.fields["producer"] == "beam")),
        DesignObligation("k6-stair-headroom", "Headroom over the stair must stay at least 2.0 m.", "decision:opening-request-0",
                         subject_refs=tuple(f"entity:stair-{n}" for n in flights)),
        DesignObligation("r8-landing-depth", "Each flight has a landing at least 1.2 m deep at both ends, measured along the flight "
                         "to the facing wall.", "decision:design-stage-core", subject_refs=tuple(f"entity:stair-{n}" for n in flights)),
        DesignObligation("r9-flat-doors-on-landings", "A flat door in a core wall opens onto a landing: along the wall it does not "
                         "overlap the flight beside it on its floor.", "decision:design-stage-core",
                         subject_refs=tuple(f"entity:{w}" for w in ("wall-core-W-L1", "wall-core-W-L2", "wall-core-E-L2", "wall-core-E-L3", "wall-core-W-L3"))),
    )
    return _record("bb-house", entities, tuple(parameters), tuple(relations), obligations)
