"""The X3 buildings as authored records: BB, a three-storey house and studio, and BA, the Pantheon (both spec v2).

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

BA (``gold_draft/BA_万神殿_规格与请求脚本_v2.md``): see ``ba_pantheon``.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import Entity, Parameter, Relation, StateRecord, ValidatorBinding, evaluate_parameters

from labs.revision_tasks.fixtures import AT_LEAST, AT_MOST, PROJECT_ID, _bound, _component, _element, _level, _record, _x_axis

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
        # A prism, as F3's lintel: it seats on its level at the head offset, which the producer states in its own support
        # relation (a beam on a level offset states a support from the level that its offset cannot satisfy).
        half = thickness / 2
        corners = []
        for end, side in (("lo", -1), ("hi", -1), ("hi", 1), ("lo", 1)):
            point: list[Any] = [None, None]
            point[axis], point[1 - axis] = f"@lintel_{key}_{end}", round(across + side * half, 9)
            corners.append(point)
        element = f"lintel-{oid}"
        self.entities.append(_element(element, "lintels", "prism",
                                      {"base": {"offset_from": {"level": base, "offset": o["head"]}}},
                                      {"profile": corners, "height": "@lintel_height"}))
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


# ---------------------------------------------------------------- BA, the Pantheon: simplified massing at original dimensions

def _number(value: float) -> str:
    """A constant as expression text: the derivation grammar reads plain decimals, no exponent."""

    text = repr(round(value, 15))
    if "e" in text:
        raise ValueError(f"constant {value!r} needs an exponent")
    return text


def _cos_ref(radius: str, m: int, n: int = 48) -> Any:
    """The binding for ``radius * cos(360 m / n degrees)``: ``@radius``, ``@radius_c<j>``, ``@radius_n<j>`` or 0."""

    quarter = n // 4
    m %= n
    if m <= quarter:
        j, positive = m, True
    elif m <= 2 * quarter:
        j, positive = 2 * quarter - m, False
    elif m <= 3 * quarter:
        j, positive = m - 2 * quarter, False
    else:
        j, positive = n - m, True
    if j == quarter:
        return 0.0
    if positive:
        return f"@{radius}" if j == 0 else f"@{radius}_c{j}"
    return f"@{radius}_n{j}"


def _sin_ref(radius: str, m: int, n: int = 48) -> Any:
    return _cos_ref(radius, n // 4 - m, n)


def _vertex(radius: str, m: int) -> list[Any]:
    """Vertex ``m`` of the 48-gon of ``radius`` about (0, 0), as a plan point [x, y]: from +x, every 7.5 degrees anticlockwise."""

    return [_cos_ref(radius, m), _sin_ref(radius, m)]


def _edge_y(radius: str, k: int, x: str) -> str:
    """The y at ``x`` of the line through 48-gon edge k (vertices k and k + 1) of ``radius``."""

    normal = math.radians(7.5 * k + 3.75)
    return (f"({radius} * {_number(math.cos(math.radians(3.75)))} - {x} * {_number(math.cos(normal))})"
            f" / {_number(math.sin(normal))}")


def _cut_y(radius: str, x: str) -> str:
    """Where the plane at ``x`` meets the 48-gon of ``radius`` north of the centre, for x between vertices 6 and 8.

    The 48-gon is convex, so its upper boundary is the lowest of its edge lines; edges 6 and 7 (45 to 60 degrees)
    are the ones the planes x = ±17.10 cross for every radius from about 27.4 m to 34 m.
    """

    return f"min({_edge_y(radius, 6, x)}, {_edge_y(radius, 7, x)})"


class _Quantities:
    """BA's parameters: declared readings, derived expressions, and the vertex bindings of each polygon radius."""

    def __init__(self) -> None:
        self.parameters: list[Parameter] = []

    def declare(self, key: str, value: float, unit: str = "m") -> None:
        self.parameters.append(Parameter(key, value, unit, epistemic_status="declared"))

    def derive(self, key: str, expr: str, unit: str = "m") -> None:
        self.parameters.append(Parameter(key, 0.0, unit, expr=expr))

    def mirrored(self, key: str, expr: str) -> None:
        """``key`` and its west twin ``key_w`` = -key."""

        self.derive(key, expr)
        self.derive(f"{key}_w", f"0 - {key}")

    def polygon(self, radius: str, n: int = 48) -> None:
        """``radius * ±cos(360 j / n)`` for the n-gon's distinct coordinates: ``_c<j>`` positive, ``_n<j>`` negative."""

        for j in range(n // 4):
            factor = _number(math.cos(2 * math.pi * j / n))
            if j:
                self.derive(f"{radius}_c{j}", f"{radius} * {factor}")
            self.derive(f"{radius}_n{j}", f"0 - {radius}" if j == 0 else f"0 - {radius} * {factor}")

    def evaluated(self) -> tuple[Parameter, ...]:
        values = evaluate_parameters(StateRecord(PROJECT_ID, "ba-parameters", (), parameters=tuple(self.parameters)))
        return tuple(replace(p, value=values[p.key]) if p.expr is not None else p for p in self.parameters)


BA_DOME_Z = (32.5, 34.5, 36.5, 38.5, 40.5, 42.0)       # the dome's fixed sections between the attic top and the oculus edge
BA_CORNICES = (("1", "low", 12.50, 0.60, 0.45), ("2", "mid", 21.90, 0.90, 0.90), ("3", "top", None, 1.10, 1.10))


def _sphere_in(z: str) -> str:
    return f"sqrt(rotunda_inner_radius * rotunda_inner_radius - ({z} - rotunda_inner_radius) * ({z} - rotunda_inner_radius))"


def _sphere_out(z: str) -> str:
    return f"sqrt(dome_sphere_radius * dome_sphere_radius - ({z} - dome_sphere_centre) * ({z} - dome_sphere_centre))"


def _ba_quantities() -> _Quantities:
    q = _Quantities()
    # portico (spec 1.2)
    q.declare("portico_width", 34.20)
    q.mirrored("portico_half_width", "portico_width / 2")
    q.declare("lip_y", 48.35)
    q.declare("portico_depth", 15.50)
    q.derive("block_front_y", "lip_y - portico_depth")
    q.declare("step_tread", 0.40)
    for k in range(1, 5):
        q.derive(f"step_y{k}", f"lip_y + {k} * step_tread")
    q.declare("front_column_count", 8, "count")
    q.declare("corner_axis_x", 16.03)
    q.declare("front_central_bay", 4.88)
    q.derive("front_outer_bay", "(corner_axis_x - front_central_bay / 2) / (front_column_count / 2 - 1)")
    q.derive("front_middle_count", "front_column_count / 2 - 1", "count")
    q.mirrored("front_middle_centre", "(front_central_bay / 2 + corner_axis_x - front_outer_bay) / 2")
    q.derive("corner_axis_x_w", "0 - corner_axis_x")
    q.declare("inner_axis_x", 6.97)
    q.derive("inner_axis_x_w", "0 - inner_axis_x")
    q.declare("front_row_y", 47.35)
    q.declare("side_bay", 4.60)
    q.derive("middle_row_y", "front_row_y - side_bay")
    q.derive("rear_row_y", "front_row_y - 2 * side_bay")
    q.declare("column_diameter", 1.48)
    q.derive("column_radius", "column_diameter / 2")
    q.polygon("column_radius", 24)
    q.declare("column_height", 14.15)
    q.declare("entablature_height", 3.35)
    q.declare("beam_width", 2.20)
    q.derive("front_beam_back_y", "lip_y - beam_width")
    q.mirrored("side_beam_x", "portico_half_width - beam_width")
    q.mirrored("inner_beam_lo", "inner_axis_x - beam_width / 2")
    q.mirrored("inner_beam_hi", "inner_axis_x + beam_width / 2")
    q.declare("pediment_rise", 7.73)
    q.declare("pediment_thickness", 1.50)
    q.derive("pediment_back_y", "lip_y - pediment_thickness")
    q.derive("portico_roof_depth", "pediment_back_y - block_front_y")
    # K1 and P5 read the front row
    q.declare("min_central_clear", 3.0)
    q.derive("central_clear", "front_central_bay - column_diameter")
    q.declare("max_clear_spacing", 5.0)
    q.derive("front_max_clear", "max(front_outer_bay, front_central_bay) - column_diameter")
    # rotunda (D1-D6)
    q.declare("interior_diameter", 43.30)
    q.declare("wall_thickness", 6.40)
    q.declare("attic_top", 30.51)
    q.declare("crown_extra", 1.00)
    q.declare("ring_height", 0.50)
    q.declare("oculus_diameter", 8.90)
    q.declare("max_dome_top", 46.5)
    q.derive("rotunda_inner_radius", "interior_diameter / 2")
    q.derive("rotunda_outer_radius", "rotunda_inner_radius + wall_thickness")
    q.derive("dome_flat_top", "interior_diameter + crown_extra")
    q.derive("dome_top", "dome_flat_top + ring_height")
    q.derive("oculus_radius", "oculus_diameter / 2")
    q.derive("oculus_edge_z", "rotunda_inner_radius + sqrt(rotunda_inner_radius * rotunda_inner_radius - oculus_radius * oculus_radius)")
    q.derive("dome_sphere_centre", "(dome_top * dome_top - attic_top * attic_top - rotunda_outer_radius * rotunda_outer_radius)"
                                   " / (2 * (dome_top - attic_top))")
    q.derive("dome_sphere_radius", "dome_top - dome_sphere_centre")
    for k in (1, 2, 3):
        q.derive(f"attic_z{k}", f"rotunda_inner_radius + {k} * (attic_top - rotunda_inner_radius) / 4")
        q.derive(f"attic_r{k}", _sphere_in(f"attic_z{k}"))
    q.derive("dome_base_inner", _sphere_in("attic_top"))
    for i, z in enumerate(BA_DOME_Z, start=1):
        q.derive(f"dome_inner_r{i}", _sphere_in(_number(z)))
        q.derive(f"dome_outer_r{i}", _sphere_out(_number(z)))
    q.derive("dome_outer_r7", _sphere_out("oculus_edge_z"))
    q.derive("dome_top_outer_radius", _sphere_out("dome_flat_top"))
    for _, name, top, height, projection in BA_CORNICES:
        if top is None:
            q.derive(f"cornice_{name}_top", "attic_top")
        else:
            q.declare(f"cornice_{name}_top", top)
        q.declare(f"cornice_{name}_height", height)
        q.declare(f"cornice_{name}_projection", projection)
        q.derive(f"cornice_{name}_base", f"cornice_{name}_top - cornice_{name}_height")
        q.derive(f"cornice_{name}_outer", f"rotunda_outer_radius + cornice_{name}_projection")
        q.derive(f"cornice_{name}_cut_y", _cut_y(f"cornice_{name}_outer", "portico_half_width"))
    # where the link block's sides (x = ±17.10) meet the drum's outer 48-gon (spec 2.4). Vertex 7 (52.5 degrees) lies within
    # 0.03 m of that plane and crosses it when the drum grows (A1), so the block and the cornices each carry it clamped to
    # their own side: on the polygon in either case, so the shared faces stay coincident without the outline changing shape.
    q.derive("drum_cut_y", _cut_y("rotunda_outer_radius", "portico_half_width"))
    q.mirrored("drum_cut_block_x", "min(rotunda_outer_radius_c7, portico_half_width - 0.01)")
    q.derive("drum_cut_block_y", _cut_y("rotunda_outer_radius", "drum_cut_block_x"))
    q.mirrored("drum_cut_cornice_x", "max(rotunda_outer_radius_c7, portico_half_width + 0.01)")
    q.derive("drum_cut_cornice_y", _cut_y("rotunda_outer_radius", "drum_cut_cornice_x"))
    radii = ["rotunda_inner_radius", "rotunda_outer_radius", "attic_r1", "attic_r2", "attic_r3", "dome_base_inner", "oculus_radius",
             "dome_top_outer_radius", "dome_outer_r7", *(f"dome_inner_r{i}" for i in range(1, 7)), *(f"dome_outer_r{i}" for i in range(1, 7)),
             *(f"cornice_{name}_outer" for _, name, *_ in BA_CORNICES)]
    for radius in radii:
        q.polygon(radius)
    # link block
    q.declare("block_roof_thickness", 0.30)
    q.derive("block_top", "attic_top - block_roof_thickness")
    q.derive("block_cornice_front_y", "block_front_y + cornice_top_projection")
    q.declare("door_width", 4.45)
    q.declare("door_height", 7.53)
    q.declare("door_centre_x", 0.0)
    q.declare("door_back_y", 21.30)
    q.declare("door_front_y", 33.10)
    q.derive("door_x_lo", "door_centre_x - door_width / 2")
    q.derive("door_x_hi", "door_centre_x + door_width / 2")
    q.derive("second_pediment_base", "cornice_mid_top")                  # J5, by construction
    q.declare("second_pediment_rise", 7.26)
    q.derive("second_pediment_apex", "second_pediment_base + second_pediment_rise")
    q.declare("notch_apex_z", 25.73)                                       # the notch is its own geometry: J3 is not declared
    q.declare("notch_half_width", 8.473)
    q.derive("notch_half_width_w", "0 - notch_half_width")
    q.declare("relief_projection", 0.60)
    return q


BA_COLUMNS = {"F1": ("@corner_axis_x_w", "@front_row_y"), "F8": ("@corner_axis_x", "@front_row_y"),
              "M1": ("@corner_axis_x_w", "@middle_row_y"), "M2": ("@inner_axis_x_w", "@middle_row_y"),
              "M3": ("@inner_axis_x", "@middle_row_y"), "M4": ("@corner_axis_x", "@middle_row_y"),
              "R1": ("@corner_axis_x_w", "@rear_row_y"), "R2": ("@inner_axis_x_w", "@rear_row_y"),
              "R3": ("@inner_axis_x", "@rear_row_y"), "R4": ("@corner_axis_x", "@rear_row_y")}
# the beam each column carries (P2): the front row the front beam, the corner and inner lines their side and inner beams
BA_CARRIES = {"F1": "front", "F8": "front", "front-W": "front", "front-E": "front", "M1": "side-W", "R1": "side-W",
              "M2": "inner-W", "R2": "inner-W", "M3": "inner-E", "R3": "inner-E", "M4": "side-E", "R4": "side-E"}


def _rect(x0: Any, x1: Any, y0: Any, y1: Any) -> list[list[Any]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _touch(relation_id: str, a: str, b: str) -> Relation:
    return Relation(relation_id, "interface", a, b, validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 0.001)))


def _seat(relation_id: str, below: str, above: str) -> Relation:
    return Relation(relation_id, "support", below, above, validator=ValidatorBinding("support_contact", tolerance=0.001))


def _said(obligation_id: str, statement: str, source: str, *subjects: str) -> DesignObligation:
    return DesignObligation(obligation_id, statement, source, subject_refs=tuple(f"entity:{s}" for s in subjects))


def ba_pantheon() -> StateRecord:
    """BA: the Pantheon in Rome at its published dimensions, simplified to its masses (spec v2, 2026-10-14).

    Model frame: origin at the rotunda centre on the floor (z 0), +x east, +y north towards the portico, forecourt
    z -1.32; plan points are [x, y]. Rotunda circles are one 48-gon from +x every 7.5 degrees (spec 2.4), shared
    vertex for vertex by every part that touches; columns are 24-gons with a vertex at 0 degrees, so a column's x
    extent is its diameter.

    - Portico: podium, a full-width flight of five risers, 16 columns of 1.48 x 14.15, five entablature beams
      (14.15-17.50), the front pediment and the portico roof (``pediment`` triangles of rise 7.73 on the front
      beam's top).
    - Link block: body, roof slab, the second pediment (a six-point section whose V notch lies 0.50 above the
      portico roof) and the top cornice on its front; the bronze-door void cuts the block and drum pieces 1 and 2.
    - Rotunda: drum pieces ``rotunda-wall-0..7`` (piece k spans 45k to 45(k + 1) degrees), floor, attic, three
      cornices stopping at x = ±17.10, dome, and the oculus ring whose top (44.80) is the building's top.

    Parameters the requests move: ``interior_diameter`` (A1; drum, floor, attic, dome, ring, cornices and the block's
    rear edge follow, D2 lifts the springing), ``front_column_count`` with ``front_central_bay`` (A2: 8 evenly to 6
    is 6 and 6.412; the outer bays and the middle columns derive, F1 and F8 stay), ``column_diameter`` (A3),
    ``column_height`` (A4, the order: beams, front pediment and portico roof stand on it), ``entablature_height``
    (A7), ``crown_extra`` (A10, the crown thickness c of D4).

    Declared and checked: P1 (every column on the podium), P2 (each beam on the columns under it), P6 (front pediment
    and portico roof on the beams), J0 (block and block roof against drum pieces and attic pieces 1 and 2), J2
    (portico roof, side and inner beams and podium against the block front), J4 (second pediment and top cornice
    against it), D2/D3/D4 (attic piece on drum piece, dome piece on attic piece, ring piece on dome piece), D6 (each
    cornice's ends against the block's sides) and the block roof on the block. Parameter obligations: K1 (front
    central clear >= 3.0), K2 (dome top <= 46.5), P5 (front clear <= 5.0, structure OPEN). In words only: K5, P3,
    P4, P7, J1, J5, D1, D2, D4, D5, H1; J5 also holds by construction (the second pediment's base is the middle
    cornice's top), as do D1, D2, D4 and D5 (every rotunda radius and section height derives from the parameters).

    Undeclared by design (J3, spec 2.3): the portico roof against the second pediment. Their 0.50 vertical clearance
    (0.456 normal) is in no relation or obligation, and the notch keeps its own two parameters, so raising the order
    by 1.0 (A4) drives the roof 0.50 into the pediment over x ±9.579: 5.4144 m3 at the stated notch half-width 8.473,
    whose slope is 0.00002 flatter than the roof's (the spec's 5.415 takes them equal). Nothing declared notices.

    Deviations from the spec's construction table (2.1):
    - The attic, the dome and the oculus ring are each eight sector pieces like the drum (``rotunda-attic-0..7``,
      ``dome-0..7``, ``oculus-ring-0..7``): a prism or loft section is one simple polygon and cannot be an annulus,
      and a void hollowing a solid is refused by the program's analytic bounds when it can move the solid's
      extremes. So 65 visible solids (not 44) and one hidden void.
    - F2-F4 and F5-F7 are the column arrays ``portico-col-front-W`` / ``-E`` (objects ``-0..2``, west to east), so
      that the front count is a parameter; F1, F8 and the M and R rows are named prisms. The count must stay even.
    - Where the block's sides meet the drum the 48-gon vertex at 52.5 degrees (0.025 m from x = 17.10) is carried
      clamped, by ``min``/``max``, to the block's side or the cornices' side of that plane; it stays on the polygon,
      so A1, which moves it across the plane, keeps every contact face coincident.
    """

    q = _ba_quantities()
    floor, forecourt = {"level": "level-floor"}, {"level": "level-forecourt"}
    a, a_w = "@portico_half_width", "@portico_half_width_w"
    entities: list[Entity] = [
        _component("building", None, "whole-building", "the Pantheon, simplified massing at original dimensions"),
        _component("portico", "building", "arrival-and-buttress", "octastyle portico"),
        _component("portico-base", "portico", "vertical-support", "podium and steps"),
        _component("portico-columns", "portico", "vertical-support", "sixteen columns"),
        _component("portico-entablature", "portico", "load-distribution", "entablature beams"),
        _component("portico-cover", "portico", "weather-enclosure", "front pediment and pitched roof"),
        _component("link-block", "building", "enclosure-and-load-distribution", "intermediate block with the second pediment"),
        _component("rotunda", "building", "enclosure-and-load-distribution", "drum, floor, attic and cornices"),
        _component("rotunda-dome", "rotunda", "weather-enclosure", "dome and oculus ring"),
        _level("level-forecourt", "terrain-grade", -1.32),
        _level("level-floor", "ground-floor", 0.0),
        _x_axis(),
    ]
    element = entities.append

    # ---- portico
    element(_element("podium", "portico-base", "prism", {"base": forecourt, "top": floor},
                     {"profile": _rect(a_w, a, "@block_front_y", "@lip_y")}))
    risers = [round(-1.32 + 0.264 * k, 9) for k in range(5)]          # -1.32 ... -0.264: five risers of 0.264 to the floor
    steps = [["@lip_y", -1.32], ["@step_y4", -1.32]]
    for k in range(4, 0, -1):                                          # from the far tread back towards the podium
        steps += [[f"@step_y{k}", risers[5 - k]], [f"@step_y{k - 1}" if k > 1 else "@lip_y", risers[5 - k]]]
    element(_element("steps", "portico-base", "prism", {"base": floor},
                     {"profile": steps, "height": "@portico_width",
                      "work_plane": {"origin": [a, 0.0, 0.0], "xAxis": [0.0, 0.0, 1.0], "yAxis": [0.0, 1.0, 0.0], "normal": [-1.0, 0.0, 0.0]}}))
    column_profile = [[_sin_ref("column_radius", m, 24), _cos_ref("column_radius", m, 24)] for m in range(24)]
    for name, (x, y) in BA_COLUMNS.items():
        # a 24-gon drawn about the column axis: the work plane's origin is the axis, its u along +y and v along +x
        element(_element(f"portico-col-{name}", "portico-columns", "prism", {"base": floor},
                         {"profile": column_profile, "height": "@column_height",
                          "work_plane": {"origin": [x, 0.0, y], "xAxis": [0.0, 0.0, 1.0], "yAxis": [1.0, 0.0, 0.0], "normal": [0.0, 1.0, 0.0]}}))
    for side, centre in (("W", "@front_middle_centre_w"), ("E", "@front_middle_centre")):
        element(_element(f"portico-col-front-{side}", "portico-columns", "column-array",
                         {"at": {"point": [centre, "@front_row_y"]}, "direction": "X", "base": floor},
                         {"count": "@front_middle_count", "spacing": "@front_outer_bay", "radius": "@column_radius",
                          "height": "@column_height", "segments": 24}))
    beams = {"front": _rect(a_w, a, "@front_beam_back_y", "@lip_y"),
             "side-W": _rect(a_w, "@side_beam_x_w", "@block_front_y", "@front_beam_back_y"),
             "side-E": _rect("@side_beam_x", a, "@block_front_y", "@front_beam_back_y"),
             "inner-W": _rect("@inner_beam_hi_w", "@inner_beam_lo_w", "@block_front_y", "@front_beam_back_y"),
             "inner-E": _rect("@inner_beam_lo", "@inner_beam_hi", "@block_front_y", "@front_beam_back_y")}
    for name, profile in beams.items():
        element(_element(f"entablature-{name}", "portico-entablature", "prism", {"base": {"datum": "portico-col-F1-top"}},
                         {"profile": profile, "height": "@entablature_height"}))
    # two triangles of the same section on the front beam's top: the pediment's 1.50 m and the roof behind it to the block
    element(_element("pediment-front", "portico-cover", "pediment",
                     {"from": {"point": [a_w, "@lip_y"]}, "to": {"point": [a, "@lip_y"]}, "base": {"datum": "entablature-front-top"},
                      "support": "entablature-front"},
                     {"rise": "@pediment_rise", "thickness": "@pediment_thickness"}))
    element(_element("portico-roof", "portico-cover", "pediment",
                     {"from": {"point": [a_w, "@pediment_back_y"]}, "to": {"point": [a, "@pediment_back_y"]},
                      "base": {"datum": "entablature-front-top"}, "support": "entablature-front"},
                     {"rise": "@pediment_rise", "thickness": "@portico_roof_depth"}))

    # ---- link block: its rear edge runs on the drum's 48-gon from x +17.10 round to -17.10 (J0)
    block_plan = [[a_w, "@block_front_y"], [a, "@block_front_y"], [a, "@drum_cut_y"], ["@drum_cut_block_x", "@drum_cut_block_y"],
                  *(_vertex("rotunda_outer_radius", m) for m in range(8, 17)), ["@drum_cut_block_x_w", "@drum_cut_block_y"], [a_w, "@drum_cut_y"]]
    element(_element("intermediate-block", "link-block", "prism",
                     {"base": forecourt, "top": {"offset_from": {"level": "level-floor", "offset": "@block_top"}}, "voids": ["bronze-door"]},
                     {"profile": block_plan}))
    element(_element("intermediate-roof", "link-block", "prism", {"base": {"datum": "intermediate-block-top"}},
                     {"profile": block_plan, "height": "@block_roof_thickness"}))
    # the second pediment: an x-z section drawn on the block's front face and pulled 0.60 north; its foot is notched
    # under the portico roof by its own two parameters (notch apex and half-width), not by the roof
    element(_element("intermediate-pediment", "link-block", "prism", {"base": floor},
                     {"profile": [[a_w, "@second_pediment_base"], ["@notch_half_width_w", "@second_pediment_base"], [0.0, "@notch_apex_z"],
                                  ["@notch_half_width", "@second_pediment_base"], [a, "@second_pediment_base"], [0.0, "@second_pediment_apex"]],
                      "height": "@relief_projection",
                      "work_plane": {"origin": [0.0, 0.0, "@block_front_y"], "xAxis": [1.0, 0.0, 0.0], "yAxis": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0]}}))
    element(_element("intermediate-cornice-top", "link-block", "prism",
                     {"base": {"offset_from": {"level": "level-floor", "offset": "@cornice_top_base"}}},
                     {"profile": _rect(a_w, a, "@block_front_y", "@block_cornice_front_y"), "height": "@cornice_top_height"}))
    element(_element("bronze-door", "link-block", "prism", {"base": floor},
                     {"profile": _rect("@door_x_lo", "@door_x_hi", "@door_back_y", "@door_front_y"), "height": "@door_height"}))

    # ---- rotunda: drum pieces k = 0..7 span 45k to 45(k + 1) degrees
    spring = {"offset_from": {"level": "level-floor", "offset": "@rotunda_inner_radius"}}
    for k in range(8):
        piece = [*(_vertex("rotunda_outer_radius", m) for m in range(6 * k, 6 * k + 7)),
                 *(_vertex("rotunda_inner_radius", m) for m in range(6 * k + 6, 6 * k - 1, -1))]
        references: dict[str, Any] = {"base": forecourt, "top": spring}
        if k in (1, 2):
            references["voids"] = ["bronze-door"]
        element(_element(f"rotunda-wall-{k}", "rotunda", "prism", references, {"profile": piece}))
    element(_element("rotunda-floor", "rotunda", "prism", {"base": forecourt, "top": floor},
                     {"profile": [_vertex("rotunda_inner_radius", m) for m in range(48)]}))

    def sector(k: int, outer: str, inner: str, z: Any) -> list[list[Any]]:
        """One section of piece k: its outer arc anticlockwise, then its inner arc back, at height z."""

        return [*([_cos_ref(outer, m), z, _sin_ref(outer, m)] for m in range(6 * k, 6 * k + 7)),
                *([_cos_ref(inner, m), z, _sin_ref(inner, m)] for m in range(6 * k + 6, 6 * k - 1, -1))]

    attic = [("rotunda_inner_radius", "@rotunda_inner_radius"), *((f"attic_r{k}", f"@attic_z{k}") for k in (1, 2, 3)),
             ("dome_base_inner", "@attic_top")]
    dome = [("rotunda_outer_radius", "dome_base_inner", "@attic_top"),
            *((f"dome_outer_r{i}", f"dome_inner_r{i}", z) for i, z in enumerate(BA_DOME_Z, start=1)),
            ("dome_outer_r7", "oculus_radius", "@oculus_edge_z"), ("dome_top_outer_radius", "oculus_radius", "@dome_flat_top")]
    for k in range(8):
        # the attic, dome and oculus ring in the drum's eight pieces: a section is one simple polygon, never an annulus
        element(_element(f"rotunda-attic-{k}", "rotunda", "loft", {"base": floor},
                         {"profile_size": 14, "profiles": [sector(k, "rotunda_outer_radius", r, z) for r, z in attic], "loft_type": "straight"}))
        element(_element(f"dome-{k}", "rotunda-dome", "loft", {"base": floor},
                         {"profile_size": 14, "profiles": [sector(k, o, i, z) for o, i, z in dome], "loft_type": "straight"}))
        element(_element(f"oculus-ring-{k}", "rotunda-dome", "prism",
                         {"base": {"offset_from": {"level": "level-floor", "offset": "@dome_flat_top"}}},
                         {"profile": [[x, y] for x, _, y in sector(k, "dome_top_outer_radius", "oculus_radius", 0.0)], "height": "@ring_height"}))
    for number, name, *_ in BA_CORNICES:
        # from the block's east side round the south to its west side: inner edge on the drum, outer edge 0.45-1.10 out
        outer = f"cornice_{name}_outer"
        profile = [[a, "@drum_cut_y"], ["@drum_cut_cornice_x", "@drum_cut_cornice_y"],
                   *(_vertex("rotunda_outer_radius", m) for m in range(6, -31, -1)),
                   ["@drum_cut_cornice_x_w", "@drum_cut_cornice_y"], [a_w, "@drum_cut_y"], [a_w, f"@cornice_{name}_cut_y"],
                   *(_vertex(outer, m) for m in range(-31, 8)), [a, f"@cornice_{name}_cut_y"]]
        element(_element(f"rotunda-cornice-{number}", "rotunda", "prism",
                         {"base": {"offset_from": {"level": "level-floor", "offset": f"@cornice_{name}_base"}}},
                         {"profile": profile, "height": f"@cornice_{name}_height"}))

    columns = [f"portico-col-{n}" for n in (*BA_COLUMNS, "front-W", "front-E")]
    relations = [_seat(f"p1-{c.removeprefix('portico-col-')}-on-podium", "podium", c) for c in columns]
    relations += [_seat(f"p2-{beam}-on-{name}", f"portico-col-{name}", f"entablature-{beam}") for name, beam in BA_CARRIES.items()]
    relations.append(_seat("p6-pediment-on-front-beam", "entablature-front", "pediment-front"))
    relations += [_seat(f"p6-roof-on-{beam}", f"entablature-{beam}", "portico-roof") for beam in beams]
    relations += [_touch(f"j0-{part}-against-{piece}-{k}", f"intermediate-{part}", f"rotunda-{piece}-{k}")
                  for part, piece in (("block", "wall"), ("block", "attic"), ("roof", "attic")) for k in (1, 2)]
    relations += [_touch(f"j2-{e.removeprefix('entablature-')}-against-block", e, "intermediate-block")
                  for e in ("portico-roof", "entablature-side-W", "entablature-side-E", "entablature-inner-W", "entablature-inner-E", "podium")]
    relations += [_touch("j4-second-pediment-against-block", "intermediate-pediment", "intermediate-block"),
                  _touch("j4-top-cornice-against-block", "intermediate-cornice-top", "intermediate-block")]
    for k in range(8):
        relations += [_seat(f"d2-attic-on-drum-{k}", f"rotunda-wall-{k}", f"rotunda-attic-{k}"),
                      _seat(f"d3-dome-on-attic-{k}", f"rotunda-attic-{k}", f"dome-{k}"),
                      _seat(f"d4-ring-on-dome-{k}", f"dome-{k}", f"oculus-ring-{k}")]
    relations.append(_seat("block-roof-on-block", "intermediate-block", "intermediate-roof"))
    relations += [_touch(f"d6-cornice-{n}-ends-against-block", f"rotunda-cornice-{n}", "intermediate-block") for n, *_ in BA_CORNICES]

    opening = "decision:opening-request-0"
    massing = "decision:massing-stage-pantheon"
    attics, domes, rings = ([f"{part}-{k}" for k in range(8)] for part in ("rotunda-attic", "dome", "oculus-ring"))
    obligations = (
        _bound("k1-central-clear", "Keep at least 3.0 m clear between the two central columns of the front row; that is the main entrance.",
               opening, "central_clear", "min_central_clear", AT_LEAST),
        _bound("k2-dome-top", "The top of the dome must stay at or below 46.5 m above the portico floor; that is the skyline limit "
               "agreed with the city.", opening, "dome_top", "max_dome_top", AT_MOST),
        _said("k5-door-centred", "The link-block door stays centred on the portico axis.", opening, "bronze-door"),
        _bound("p5-front-clear", "Clear spacing between front columns stays at most 5.0 m; the stone entablature's span is not "
               "structurally checked (OPEN).", massing, "front_max_clear", "max_clear_spacing", AT_MOST),
        _said("p3-front-row-symmetric", "The front row is symmetric about x = 0 and its outer bays are equal.", massing,
              "portico-col-F1", "portico-col-front-W", "portico-col-front-E", "portico-col-F8"),
        _said("p4-corner-columns-fixed", "The corner columns F1 and F8 stay at x -16.03 and 16.03, y 47.35.", massing,
              "portico-col-F1", "portico-col-F8"),
        _said("p7-inner-rows-fixed", "The two inner rows stand at x ±6.97 and ±16.03, y 42.75 and 38.15.", massing,
              *(f"portico-col-{n}" for n in BA_COLUMNS if n[0] in "MR")),
        _said("j1-roof-flush-with-attic", "The link-block roof top, the attic top and the top cornice top are one level (30.51).", massing,
              "intermediate-roof", *attics, "rotunda-cornice-3", "intermediate-cornice-top"),
        _said("j5-second-pediment-on-middle-cornice", "The second pediment's base is the rotunda middle cornice's top (21.90).", massing,
              "intermediate-pediment", "rotunda-cornice-2"),
        _said("d1-concentric", "Every part of the rotunda is concentric about (0, 0).", massing,
              *(f"rotunda-wall-{k}" for k in range(8)), "rotunda-floor", *attics, *domes, *rings),
        _said("d2-inner-sphere", "The drum's top is the springing at the inner radius; the inner surface of the attic and dome is "
              "the hemisphere of that radius centred at the springing, so the inner height equals the interior diameter.", massing,
              *attics, *domes),
        _said("d4-crown", "The dome's flat top is twice the inner radius plus the crown thickness c; the 0.50 m oculus ring sits "
              "on it; the oculus is 8.90 m across.", massing, *domes, *rings),
        _said("d5-outer-cap", "The dome's outer surface is the spherical cap through the drum's outer edge at 30.51 and the "
              "oculus ring's top.", massing, *domes),
        _said("h1-door-through", "The bronze-door void cuts through the link block and drum pieces 1 and 2.", massing,
              "bronze-door", "intermediate-block", "rotunda-wall-1", "rotunda-wall-2"),
    )
    return _record("ba-pantheon", entities, q.evaluated(), tuple(relations), obligations)
