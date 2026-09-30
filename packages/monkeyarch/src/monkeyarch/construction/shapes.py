"""What a construction script manipulates (#419, L1): profiles, planes, anchors and shape handles.

Profiles, planes, anchors, sections and parameter bindings are plain values. A
shape is a handle. A shape drawn by the script (``Drawn``) keeps its definition
- profile, height, anchor, plane, points or sections - and every transform is
baked into that definition. Existing geometry reached with ``get()``
(``RowShape``) keeps its element's own parameters and references and edits them
in place; nothing else of the row is touched. Bounds of a drawn shape are
analytic from its definition; bounds of existing geometry come from its
producer, read through ``World``, the record's facts. Every top a shape reads
resolves through the script's model (``World.top_of``): the current version of
what the script changed or redefined, else what the record publishes.

Every coordinate stays finite and within ``MAX_COORDINATE`` metres of the
origin, and every length is at least ``MIN_LENGTH``. Nothing here names an id:
ids are decided once the script has run (lowering). A refusal is a
``ShapeError`` with one plain sentence of bounded length; the interpreter adds
the line it happened at.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from archflow.state.derivation import DerivationError, substitute
from archflow.state.geometry_program import (
    GeometryParameter,
    GeometryParameterKind,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
    expected_object_bounds,
)
from archflow.state.state_record import (
    StateRecord,
    StateRecordError,
    evaluate_parameters,
    project_grids_of,
    project_levels_of,
    resolve_element_bindings,
)
from monkeyarch.capabilities.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    element_rows_of,
    produce_rows,
)
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeyarch.construction.vocabulary import LIMITS

EDITABLE_PRODUCERS = frozenset({"prism", "planar-surface", "curve", "loft", "wall"})
MAX_PROFILE_POINTS = LIMITS["profilePoints"]
MAX_PATH_POINTS = LIMITS["pathPoints"]
MAX_COORDINATE = float(LIMITS["coordinateRange"])
MIN_LENGTH = float(LIMITS["minimumLength"])
NO_LEVEL = "this project has no level to measure heights from; add a level first"
_EPS = 1e-12
_PRODUCTION_ERRORS = (ElementProducerError, StateRecordError, ValueError, KeyError, TypeError, IndexError,
                      ArithmeticError)
Box = tuple[tuple[float, float, float], tuple[float, float, float]]
Spend = Callable[[int], None]


class ShapeError(ValueError):
    """A refusal about a value or a shape; the interpreter adds the line it happened at."""


# ---------------------------------------------------------------- numbers, points and bounded text
def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number(value: object, what: str) -> float:
    """A finite number; True and False are not numbers for geometry."""

    if not is_number(value):
        raise ShapeError(f"{what} must be a number, not {describe(value)}")
    result = float(value)  # type: ignore[arg-type]
    if not math.isfinite(result):
        raise ShapeError(f"{what} must be a finite number")
    return result


def coordinate(value: object, what: str) -> float:
    """A position or displacement in metres, within ``MAX_COORDINATE`` of the origin."""

    result = number(value, what)
    if abs(result) > MAX_COORDINATE:
        raise ShapeError(f"{what} is {result:.6g} m; coordinates stay within 100 000 m of the origin")
    return result


def length(value: object, what: str) -> float:
    """A signed length: at least ``MIN_LENGTH`` and at most ``MAX_COORDINATE`` in size."""

    result = coordinate(value, what)
    if abs(result) < MIN_LENGTH:
        raise ShapeError(f"{what} is {result:.3g} m; a length is at least 0.000001 m")
    return result


def clean(value: float) -> float:
    """A coordinate as the record keeps it: rounded to a nanometre, never -0.0."""

    result = round(float(value), 9)
    return 0.0 if result == 0 else result


def short(text: str, limit: int = 60) -> str:
    """Text from a script, cut to a length a message can carry."""

    return text if len(text) <= limit else text[:limit - 3] + "..."


def shown(value: object) -> str:
    """A script value inside a message: short text quoted, a number as written, anything else by its kind."""

    if isinstance(value, str):
        return repr(short(value))
    if is_number(value):
        return short(repr(value), 30)
    return describe(value)


def point2(value: object, what: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ShapeError(f"{what} takes points (x, z), not {describe(value)}")
    return coordinate(value[0], f"{what} x"), coordinate(value[1], f"{what} z")


def point3(value: object, what: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ShapeError(f"{what} takes points (x, y, z), not {describe(value)}")
    return (coordinate(value[0], f"{what} x"), coordinate(value[1], f"{what} y"),
            coordinate(value[2], f"{what} z"))


def vector3(value: object, what: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ShapeError(f"{what} takes a direction (x, y, z), not {describe(value)}")
    return number(value[0], what), number(value[1], what), number(value[2], what)


def describe(value: object) -> str:
    """What a value is, in the words of the language, in a few words."""

    if value is True or value is False or value is None:
        return str(value)
    if is_number(value):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, Shape):
        return f"the {value.word} {value.label()}"
    for kind, words in ((list, "a list"), (tuple, "a tuple"), (range, "a range"), (Profile, "a profile"),
                        (Plane, "a plane"), (Anchor, "an anchor"), (Section, "a section")):
        if isinstance(value, kind):
            return words
    if isinstance(value, ParamRef):
        return repr(value)
    return "a function"


class _Full(Exception):
    pass


def render(values: Iterable[object], limit: int) -> str:
    """What ``print`` shows: values as Python writes them, stopped after ``limit`` characters."""

    parts: list[str] = []
    used = [0]

    def put(text: str) -> None:
        parts.append(text)
        used[0] += len(text)
        if used[0] > limit:
            raise _Full

    def walk(value: object, top: bool) -> None:
        if isinstance(value, str):
            put(value[:limit + 1] if top else repr(value[:limit + 1]))
        elif isinstance(value, (list, tuple)):
            put("[" if isinstance(value, list) else "(")
            for index, item in enumerate(value):
                if index:
                    put(", ")
                walk(item, False)
            if isinstance(value, tuple) and len(value) == 1:
                put(",")
            put("]" if isinstance(value, list) else ")")
        else:
            put(repr(value))

    try:
        for index, value in enumerate(values):
            if index:
                put(" ")
            walk(value, True)
    except _Full:
        return "".join(parts)[:limit] + "..."
    return "".join(parts)


def _box(points: Iterable[tuple[float, float, float]]) -> Box:
    points = list(points)
    low = tuple(clean(min(p[i] for p in points)) for i in range(3))
    high = tuple(clean(max(p[i] for p in points)) for i in range(3))
    return low, high  # type: ignore[return-value]


def union(boxes: Iterable[Box]) -> Box | None:
    boxes = list(boxes)
    if not boxes:
        return None
    return _box([corner for box in boxes for corner in box])


def within_reach(box: Box) -> bool:
    return all(abs(c) <= MAX_COORDINATE for corner in box for c in corner)


# ---------------------------------------------------------------- profiles
@dataclass(frozen=True)
class Profile:
    """A closed polygon of (x, z) points in plan, or (u, v) points on a plane; the first point is not repeated."""

    points: tuple[tuple[float, float], ...]

    def __repr__(self) -> str:
        return f"<profile {len(self.points)} points>"


def signed_area(points) -> float:
    count = len(points)
    return 0.5 * sum(points[i][0] * points[(i + 1) % count][1] - points[(i + 1) % count][0] * points[i][1]
                     for i in range(count))


def _orientation(p, q, r) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _within(p, q, r) -> bool:
    return (min(p[0], q[0]) - _EPS <= r[0] <= max(p[0], q[0]) + _EPS
            and min(p[1], q[1]) - _EPS <= r[1] <= max(p[1], q[1]) + _EPS)


def _touch(p1, p2, p3, p4) -> bool:
    d1, d2 = _orientation(p3, p4, p1), _orientation(p3, p4, p2)
    d3, d4 = _orientation(p1, p2, p3), _orientation(p1, p2, p4)
    if ((d1 > _EPS and d2 < -_EPS) or (d1 < -_EPS and d2 > _EPS)) and \
            ((d3 > _EPS and d4 < -_EPS) or (d3 < -_EPS and d4 > _EPS)):
        return True
    return any(abs(d) <= _EPS and _within(a, b, c)
               for d, a, b, c in ((d1, p3, p4, p1), (d2, p3, p4, p2), (d3, p1, p2, p3), (d4, p1, p2, p4)))


def edge_pairs(count: int) -> int:
    """How many edge pairs ``crosses_itself`` examines for a polygon of ``count`` points."""

    return max(0, count * (count - 3) // 2)


def crosses_itself(points) -> bool:
    """True when two edges of the closed polygon meet anywhere but at their shared corner."""

    count = len(points)
    edges = [(points[i], points[(i + 1) % count]) for i in range(count)]
    boxes = [(min(a[0], b[0]) - _EPS, max(a[0], b[0]) + _EPS, min(a[1], b[1]) - _EPS, max(a[1], b[1]) + _EPS)
             for a, b in edges]
    for i, (a, b) in enumerate(edges):
        c = edges[(i + 1) % count][1]
        if abs(_orientation(a, b, c)) <= _EPS and (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) < 0:
            return True  # the outline turns straight back on itself
        low_x, high_x, low_z, high_z = boxes[i]
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue
            other = boxes[j]
            if other[0] > high_x or other[1] < low_x or other[2] > high_z or other[3] < low_z:
                continue
            if _touch(a, b, *edges[j]):
                return True
    return False


def profile_points(points: object, what: str) -> list[tuple[float, float]]:
    """The checked (x, z) points of a polygon, before its shape is checked."""

    if not isinstance(points, (list, tuple)):
        raise ShapeError(f"{what} takes a list of points (x, z), not {describe(points)}")
    if len(points) > MAX_PROFILE_POINTS:
        raise ShapeError(f"{what} takes at most {MAX_PROFILE_POINTS} points")
    return [point2(point, what) for point in points]


def closed_profile(checked: list[tuple[float, float]], what: str, spend: Spend) -> Profile:
    """A profile from checked points: at least three, no repeat, edges of some length, an area, no crossing."""

    if len(checked) >= 2 and checked[0] == checked[-1]:
        raise ShapeError("a closed profile does not repeat its first point")
    if len(checked) < 3:
        raise ShapeError(f"{what} needs at least three points")
    for a, b in zip(checked, checked[1:] + checked[:1]):
        if a == b:
            raise ShapeError(f"{what} repeats the point ({a[0]!r}, {a[1]!r})")
        if math.dist(a, b) < MIN_LENGTH:
            raise ShapeError(f"{what} has an edge shorter than 0.000001 m")
    if abs(signed_area(checked)) <= _EPS:
        raise ShapeError(f"{what} makes a profile with no area")
    spend(edge_pairs(len(checked)))
    if crosses_itself(checked):
        raise ShapeError(f"{what} makes a profile that crosses itself")
    return Profile(tuple(checked))


def rect(x: object, z: object, width: object, depth: object) -> Profile:
    x0, z0 = coordinate(x, "rect() x"), coordinate(z, "rect() z")
    w, d = length(width, "rect() width"), length(depth, "rect() depth")
    for corner in (x0 + w, z0 + d):
        coordinate(corner, "rect() corner")
    return Profile(((x0, z0), (x0 + w, z0), (x0 + w, z0 + d), (x0, z0 + d)))


_QUARTERS = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))


def circle(x: object, z: object, radius: object, segments: object) -> Profile:
    cx, cz = coordinate(x, "circle() x"), coordinate(z, "circle() z")
    r = length(radius, "circle() radius")
    if r <= 0:
        raise ShapeError("circle() needs a positive radius")
    if isinstance(segments, bool) or not isinstance(segments, int) or not 8 <= segments <= 128:
        raise ShapeError(f"circle() segments must be a whole number from 8 to 128, not {shown(segments)}")
    for extreme in (cx - r, cx + r, cz - r, cz + r):
        coordinate(extreme, "circle() extent")
    points = []
    for index in range(segments):
        if (4 * index) % segments == 0:
            c, s = _QUARTERS[(4 * index) // segments]
        else:
            angle = 2.0 * math.pi * index / segments
            c, s = math.cos(angle), math.sin(angle)
        points.append((cx + r * c, cz + r * s))
    return Profile(tuple(points))


def offset(shape: Profile, distance: object, spend: Spend) -> Profile:
    """The mitred offset; refused when it collapses: an edge turns round, the area flips or does not shrink inward."""

    d = coordinate(distance, "offset() distance")
    what = f"offset({shown(distance)})"
    points = shape.points
    count, area = len(points), signed_area(shape.points)
    sense = 1.0 if area > 0 else -1.0
    normals = []
    for i in range(count):
        (x1, z1), (x2, z2) = points[i], points[(i + 1) % count]
        size = math.hypot(x2 - x1, z2 - z1)
        normals.append((sense * (z2 - z1) / size, -sense * (x2 - x1) / size))
    result = []
    for i in range(count):
        (ax, az), (bx, bz), (px, pz) = normals[i - 1], normals[i], points[i]
        det = ax * bz - az * bx
        if abs(det) <= _EPS:
            if ax * bx + az * bz < 0:
                raise ShapeError(f"{what} cannot follow a profile that turns straight back")
            result.append((px + d * ax, pz + d * az))
            continue
        c1, c2 = ax * px + az * pz + d, bx * px + bz * pz + d
        result.append(((c1 * bz - az * c2) / det, (ax * c2 - c1 * bx) / det))
    for i in range(count):
        (ox, oz), (nx, nz) = points[i], points[(i + 1) % count]
        (rx, rz), (sx, sz) = result[i], result[(i + 1) % count]
        if (nx - ox) * (sx - rx) + (nz - oz) * (sz - rz) <= 0 or math.dist(result[i], result[(i + 1) % count]) < MIN_LENGTH:
            raise ShapeError(f"{what} collapses this profile")
    new_area = signed_area(result)
    if new_area * area <= 0 or (d < 0 and abs(new_area) >= abs(area)):
        raise ShapeError(f"{what} collapses this profile")
    for x, z in result:
        coordinate(x, f"{what} x")
        coordinate(z, f"{what} z")
    spend(edge_pairs(count))
    if crosses_itself(result):
        raise ShapeError(f"{what} collapses this profile")
    return Profile(tuple(result))


# ---------------------------------------------------------------- planes
def _unit(vector: tuple[float, float, float], what: str) -> tuple[float, float, float]:
    """A unit vector along ``vector``, its length taken without overflow; a non-finite or zero one is refused."""

    largest = max(abs(c) for c in vector)
    if not math.isfinite(largest):
        raise ShapeError(f"{what} must be a finite direction")
    if largest == 0:
        raise ShapeError(f"{what} must not be zero")
    scaled = tuple(c / largest for c in vector)
    size = math.sqrt(sum(c * c for c in scaled))  # between 1 and sqrt(3)
    if size * largest <= 1e-12:
        raise ShapeError(f"{what} must not be zero")
    return tuple(c / size for c in scaled)  # type: ignore[return-value]


def _cross(a, b) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


@dataclass(frozen=True)
class Plane:
    """A drawing plane: a world origin, two perpendicular unit axes and the normal a profile extrudes along."""

    origin: tuple[float, float, float]
    x_axis: tuple[float, float, float]
    y_axis: tuple[float, float, float]
    normal: tuple[float, float, float]

    def point(self, u: float, v: float) -> tuple[float, float, float]:
        return tuple(self.origin[i] + u * self.x_axis[i] + v * self.y_axis[i] for i in range(3))  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "<plane at ({:g}, {:g}, {:g})>".format(*self.origin)


def plane(origin: object, x_axis: object, y_axis: object) -> Plane:
    o = point3(origin, "plane() origin")
    x = _unit(vector3(x_axis, "plane() x_axis"), "plane() x_axis")
    y = _unit(vector3(y_axis, "plane() y_axis"), "plane() y_axis")
    if abs(sum(a * b for a, b in zip(x, y))) > 1e-9:
        raise ShapeError("plane() needs an x_axis and a y_axis that are perpendicular")
    return Plane(o, x, y, _cross(x, y))


def front(z: object) -> Plane:
    return Plane((0.0, 0.0, coordinate(z, "front() z")), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def side(x: object) -> Plane:
    return Plane((coordinate(x, "side() x"), 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))


def _minus(p, q) -> tuple[float, float, float]:
    return (p[0] - q[0], p[1] - q[1], p[2] - q[2])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def path_frame(points) -> tuple[tuple, tuple, tuple] | None:
    """The plane a path lies in as (x axis along its first segment, y axis, normal); None for a level path.

    A path that leaves one plane is refused: a drawn path is kept in its own drawing plane.
    """

    if max(p[1] for p in points) - min(p[1] for p in points) <= 1e-9:
        return None
    first = points[0]
    x_axis = next(_unit(_minus(p, first), "a path segment") for p in points[1:] if math.dist(p, first) > 1e-9)
    # The normal from the point farthest off the first segment's line: the best-conditioned choice.
    normal = max((_cross(x_axis, _minus(p, first)) for p in points[1:]), key=lambda c: _dot(c, c))
    if math.sqrt(_dot(normal, normal)) <= 1e-9:  # one straight line: keep the plane through it that holds the vertical
        helper = (0.0, 1.0, 0.0) if abs(x_axis[1]) < 0.9 else (1.0, 0.0, 0.0)
        y_axis = _unit(tuple(h - _dot(helper, x_axis) * x for h, x in zip(helper, x_axis)), "a path frame")  # type: ignore[arg-type]
        return x_axis, y_axis, _cross(x_axis, y_axis)
    normal = _unit(normal, "a path normal")
    if any(abs(_dot(_minus(p, first), normal)) > 1e-7 for p in points):
        raise ShapeError("path() points must lie in one plane")
    return x_axis, _cross(normal, x_axis), normal


# ---------------------------------------------------------------- bindings, anchors, sections
@dataclass(frozen=True)
class ParamRef:
    """A project parameter used as a binding: it lowers to ``"@key"`` and never becomes a number in the script."""

    key: str

    def __repr__(self) -> str:
        return f"param('{self.key}')"


def binding_refusal(binding: ParamRef) -> ShapeError:
    return ShapeError(f"{binding!r} is a binding; use it directly or define the expression in the project's parameters")


@dataclass(frozen=True, eq=False)
class Anchor:
    """Where a shape stands: a project level or the top of a solid, ``offset`` metres above it.

    ``param`` is a parameter key added instead of a number.
    """

    kind: str  # "level" | "top"
    target: Any  # a level id, or a Shape
    offset: float = 0.0
    param: str | None = None

    def base_of(self, other: Anchor) -> bool:
        """Whether both measure from the same level or the same solid's top."""

        return self.kind == other.kind and (self.target == other.target if self.kind == "level"
                                            else self.target is other.target)

    def shifted(self, amount: float) -> Anchor:
        if amount == 0:
            return self
        if self.param is not None:
            raise ShapeError(f"this base is bound to param('{self.param}'); it takes a parameter or a number, not both")
        return replace(self, offset=coordinate(self.offset + amount, "the offset of a base"))

    def with_param(self, key: str) -> Anchor:
        if self.param is not None:
            raise ShapeError("a base takes one parameter")
        if self.offset != 0:
            raise ShapeError(f"a base takes a parameter or a number, not both: param('{key}') and {self.offset!r}")
        return replace(self, param=key)

    def __repr__(self) -> str:
        where = f"level {short(str(self.target))}" if self.kind == "level" else f"top of {self.target.label()}"
        extra = f" + param('{self.param}')" if self.param else (f" + {self.offset!r}" if self.offset else "")
        return f"<{where}{extra}>"


def combine(operator: str, left: object, right: object) -> Anchor:
    """``anchor + number``, ``number + anchor``, ``anchor - number`` and ``anchor + param(key)``; nothing else."""

    if isinstance(left, Anchor) and operator in ("+", "-") and is_number(right):
        return left.shifted(coordinate(right, "an anchor offset") * (1 if operator == "+" else -1))
    if isinstance(right, Anchor) and operator == "+" and is_number(left):
        return right.shifted(coordinate(left, "an anchor offset"))
    if isinstance(left, Anchor) and operator == "+" and isinstance(right, ParamRef):
        return left.with_param(right.key)
    if isinstance(right, Anchor) and operator == "+" and isinstance(left, ParamRef):
        return right.with_param(left.key)
    for value in (left, right):
        if isinstance(value, ParamRef):
            raise binding_refusal(value)
    raise ShapeError("an anchor can only be offset by adding or subtracting a number, or by adding param(key)")


@dataclass(frozen=True)
class Section:
    """One loft section: a plan profile at an anchor."""

    profile: Profile
    anchor: Anchor

    def __repr__(self) -> str:
        return f"<section {len(self.profile.points)} points>"


# ---------------------------------------------------------------- plan transforms
_EXACT = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}


@dataclass(frozen=True)
class PlanMap:
    """One transform about vertical axes: move, rotate (turning +x toward +z), mirror or scale."""

    kind: str
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    cos: float = 1.0
    sin: float = 0.0
    about: tuple[float, float] = (0.0, 0.0)
    axis: str = ""
    at: float = 0.0
    factor: float = 1.0

    @classmethod
    def rotation(cls, degrees: float, about: tuple[float, float]) -> PlanMap:
        turns = degrees % 360.0
        if turns in _EXACT:
            c, s = _EXACT[int(turns)]
        else:
            c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
        return cls("rotate", cos=c, sin=s, about=about)

    def point(self, x: float, z: float) -> tuple[float, float]:
        if self.kind == "move":
            return x + self.dx, z + self.dz
        cx, cz = self.about
        if self.kind == "rotate":
            rx, rz = x - cx, z - cz
            return cx + rx * self.cos - rz * self.sin, cz + rx * self.sin + rz * self.cos
        if self.kind == "mirror":
            return (2 * self.at - x, z) if self.axis == "x" else (x, 2 * self.at - z)
        return cx + self.factor * (x - cx), cz + self.factor * (z - cz)

    def vector(self, x: float, z: float) -> tuple[float, float]:
        """A direction: turned or reflected, never moved or scaled."""

        if self.kind == "rotate":
            return x * self.cos - z * self.sin, x * self.sin + z * self.cos
        if self.kind == "mirror":
            return (-x, z) if self.axis == "x" else (x, -z)
        return x, z

    def vector3(self, value) -> tuple[float, float, float]:
        x, z = self.vector(value[0], value[2])
        return x, value[1], z

    @property
    def lift(self) -> float:
        return self.dy if self.kind == "move" else 0.0


# ---------------------------------------------------------------- shapes
class Shape:
    """A geometry handle. Its id is decided after the script has run, from how the script named it.

    ``voids`` are its cutters as the script knows them (handles, or element ids of the record);
    ``void_events`` replay this script's cuts on a new shape over what an existing id it redefines
    already cuts (``cut``, ``uncut`` of one cutter, ``clear`` for all). ``cut_lines`` is the line of
    the ``cut()`` in this script that made each cut it has now, new shape or existing geometry.
    """

    word = "shape"

    def __init__(self, seq: int, line: int) -> None:
        self.seq = seq
        self.line = line
        self.created_line = line
        self.explicit: tuple[str, int] | None = None  # name(obj, id) and its line
        self.direct: tuple[str, int, int] | None = None  # last module variable holding it: (name, order, line)
        self.listed: tuple[str, int, int] | None = None  # last module list or tuple holding it
        self.cut_into: dict[Shape, None] = {}  # hosts it was cut into, in order (an unnamed cutter is named after the first)
        self.voids: dict[Any, None] = {}
        self.void_events: list[tuple[str, Any, int]] = []
        self.cut_lines: dict[Any, int] = {}
        self.deleted_line: int | None = None
        self.made_by: tuple[str, int] | None = None  # (the text of the statement that made it, how many it made before)

    @property
    def is_new(self) -> bool:
        return True

    def label(self) -> str:
        if self.explicit is not None:
            return short(self.explicit[0])
        chosen = self.direct or self.listed
        return short(chosen[0]) if chosen is not None else f"from line {self.created_line}"

    def touched(self, line: int) -> None:
        self.line = line

    def __repr__(self) -> str:
        return f"<{self.word} {self.label()}>"


def height_value(height: float | ParamRef, world: World) -> float:
    return world.parameter_value(height.key) if isinstance(height, ParamRef) else float(height)


class Drawn(Shape):
    """A shape the script drew: its definition, with every transform baked in.

    ``kind`` is ``extrude`` (profile, height, anchor or plane), ``face`` (profile, anchor or plane),
    ``path`` (3D points) or ``loft`` (sections, cap).
    """

    def __init__(self, seq: int, line: int, kind: str, *, profile: Profile | None = None,
                 height: float | ParamRef | None = None, anchor: Anchor | None = None, plane: Plane | None = None,
                 points: tuple[tuple[float, float, float], ...] = (), sections: tuple[Section, ...] = (),
                 cap: bool = True) -> None:
        super().__init__(seq, line)
        self.kind = kind
        self.profile = profile
        self.height = height
        self.anchor = anchor
        self.plane = plane
        self.points = points
        self.sections = sections
        self.cap = cap

    @property
    def word(self) -> str:  # type: ignore[override]
        if self.kind == "loft":
            return "solid" if self.cap else "surface"
        return {"extrude": "solid", "face": "face", "path": "path"}[self.kind]

    @property
    def form(self) -> str:
        return "other" if self.word == "surface" else self.word

    @property
    def size(self) -> int:
        """How many points its definition holds: what a transform or a measurement walks."""

        if self.kind == "loft":
            return sum(len(section.profile.points) for section in self.sections)
        return len(self.points) if self.kind == "path" else len(self.profile.points)  # type: ignore[union-attr]

    @property
    def point_count(self) -> int:
        """Its points for the script's total: every point of its profile, sections or path."""

        return self.size

    @property
    def is_solid(self) -> bool:
        return self.kind == "extrude" or (self.kind == "loft" and self.cap)

    can_cut = is_solid

    @property
    def upward(self) -> bool:
        """Extruded upward from a plan profile: the only kind of solid whose top another shape can stand on."""

        return (self.kind == "extrude" and self.plane is None
                and (isinstance(self.height, ParamRef) or float(self.height) > 0))  # type: ignore[arg-type]

    def can_carry(self, world: World) -> bool:
        return self.upward

    def anchors(self) -> list[Anchor]:
        if self.kind == "loft":
            return [section.anchor for section in self.sections]
        return [self.anchor] if self.anchor is not None else []

    def clone(self, seq: int, line: int) -> Drawn:
        return Drawn(seq, line, self.kind, profile=self.profile, height=self.height, anchor=self.anchor,
                     plane=self.plane, points=self.points, sections=self.sections, cap=self.cap)

    def apply(self, change: PlanMap) -> None:
        if self.kind in ("extrude", "face"):
            if self.plane is None:
                self.profile = Profile(tuple(change.point(x, z) for x, z in self.profile.points))  # type: ignore[union-attr]
                if change.lift:
                    self.anchor = self.anchor.shifted(change.lift)  # type: ignore[union-attr]
                if change.kind == "scale" and self.kind == "extrude":
                    self.height = _scaled_height(self.height, change.factor, self.label())
                return
            ox, oy, oz = self.plane.origin
            px, pz = change.point(ox, oz)
            profile, height = self.profile, self.height
            if change.kind == "scale":
                profile = Profile(tuple((u * change.factor, v * change.factor) for u, v in profile.points))  # type: ignore[union-attr]
                if self.kind == "extrude":
                    height = _scaled_height(height, change.factor, self.label())
            self.plane = Plane((px, oy + change.lift, pz), change.vector3(self.plane.x_axis),
                               change.vector3(self.plane.y_axis), change.vector3(self.plane.normal))
            self.profile, self.height = profile, height
            return
        if self.kind == "path":
            points = [(*change.point(x, z), y + change.lift) for x, y, z in self.points]
            moved = [(x, y, z) for x, z, y in points]
            if change.kind == "scale":
                low = min(y for _, y, _ in moved)
                moved = [(x, low + change.factor * (y - low), z) for x, y, z in moved]
            self.points = tuple(moved)
            return
        sections = [Section(Profile(tuple(change.point(x, z) for x, z in section.profile.points)),
                            section.anchor.shifted(change.lift)) for section in self.sections]
        if change.kind == "scale":
            low = min(section.anchor.offset for section in sections)
            sections = [Section(section.profile, replace(section.anchor, offset=low + change.factor * (section.anchor.offset - low)))
                        for section in sections]
        self.sections = tuple(sections)

    def check_extent(self) -> None:
        """Every coordinate within reach, every length long enough, after an edit."""

        what = f"{self.label()}"
        if self.kind in ("extrude", "face"):
            if self.plane is None:
                plan = list(self.profile.points)  # type: ignore[union-attr]
                coordinate(self.anchor.offset, f"the base offset of {what}")  # type: ignore[union-attr]
            else:
                plan = [(p[0], p[2]) for p in (self.plane.point(u, v) for u, v in self.profile.points)]  # type: ignore[union-attr]
                for p in (self.plane.point(u, v) for u, v in self.profile.points):  # type: ignore[union-attr]
                    coordinate(p[1], f"a point of {what}")
            for x, z in plan:
                coordinate(x, f"a point of {what}")
                coordinate(z, f"a point of {what}")
            points = self.profile.points  # type: ignore[union-attr]
            if any(math.dist(a, b) < MIN_LENGTH for a, b in zip(points, points[1:] + points[:1])):
                raise ShapeError(f"{what} would have an edge shorter than 0.000001 m")
            if self.kind == "extrude" and not isinstance(self.height, ParamRef):
                length(self.height, f"the height of {what}")
        elif self.kind == "path":
            for point in self.points:
                point3(point, f"a point of {what}")
            if any(math.dist(a, b) < MIN_LENGTH for a, b in zip(self.points, self.points[1:])):
                raise ShapeError(f"{what} would have a segment shorter than 0.000001 m")
        else:
            for section in self.sections:
                coordinate(section.anchor.offset, f"a section height of {what}")
                for point in section.profile.points:
                    point2(point, f"a point of {what}")
                points = section.profile.points
                if any(math.dist(a, b) < MIN_LENGTH for a, b in zip(points, points[1:] + points[:1])):
                    raise ShapeError(f"{what} would have an edge shorter than 0.000001 m")

    def bounds(self, world: World) -> Box:
        if self.kind in ("extrude", "face"):
            if self.plane is None:
                y0 = anchor_elevation(self.anchor, world)  # type: ignore[arg-type]
                heights = [y0] if self.kind == "face" else [y0, y0 + height_value(self.height, world)]  # type: ignore[arg-type]
                return _box((x, y, z) for x, z in self.profile.points for y in heights)  # type: ignore[union-attr]
            base = [self.plane.point(u, v) for u, v in self.profile.points]  # type: ignore[union-attr]
            if self.kind == "face":
                return _box(base)
            h, normal = height_value(self.height, world), self.plane.normal  # type: ignore[arg-type]
            return _box(base + [tuple(p[i] + h * normal[i] for i in range(3)) for p in base])  # type: ignore[misc]
        if self.kind == "path":
            return _box(self.points)
        elevations = [anchor_elevation(section.anchor, world) for section in self.sections]  # once per section
        return _box((x, y, z) for section, y in zip(self.sections, elevations) for x, z in section.profile.points)


def _scaled_height(height: float | ParamRef | None, factor: float, label: str) -> float | ParamRef:
    if isinstance(height, ParamRef):
        if factor != 1:
            raise ShapeError(f"the height of {label} is bound to {height!r} and cannot be scaled")
        return height
    return float(height) * factor  # type: ignore[arg-type]


def _offset_value(anchor: Anchor, world: World) -> float:
    return anchor.offset + (world.parameter_value(anchor.param) if anchor.param is not None else 0.0)


def anchor_elevation(anchor: Anchor, world: World) -> float:
    """The world elevation an anchor stands for, from the record's stated values and the script's shapes."""

    if anchor.kind == "level":
        return world.levels[anchor.target] + _offset_value(anchor, world)
    return top_value(anchor.target, world) + _offset_value(anchor, world)


def top_value(shape: Shape, world: World) -> float:
    """The elevation of a solid's top, as the runtime will publish it, worked out through the script's model
    (``World.resolve``) and kept in ``world.tops`` until the script next changes a shape."""

    return world.resolve(shape)


def _top_datum(element_id: str, value: float) -> InterfaceDatum:
    return InterfaceDatum.create(datum_id=f"{element_id}-top", kind=InterfaceDatumKind.LEVEL,
                                 published_by="construction", value=round(value, 9), unit=LengthUnit.METER)


def _bound(value: object) -> bool:
    """True when a row value carries an ``"@key"`` parameter binding anywhere."""

    if isinstance(value, str):
        return value.startswith("@")
    if isinstance(value, dict):
        return any(_bound(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_bound(item) for item in value)
    return False


def _points_in(value: object) -> int:
    """How many points a row value holds: its lists of numbers (a profile's points, a line's ends, a plane's axes)."""

    if isinstance(value, dict):
        return sum(_points_in(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        if value and all(is_number(item) or (isinstance(item, str) and item.startswith("@")) for item in value):
            return 1  # a point, its coordinates numbers or "@key" bindings
        return sum(_points_in(item) for item in value)
    return 0


def _numbers_in(value: object):
    """Every number a row value holds, for the reach check."""

    if is_number(value):
        yield float(value)  # type: ignore[arg-type]
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _numbers_in(item)


class RowShape(Shape):
    """Existing geometry reached with ``get()``, or a copy of it: the element's own parameters, edited in place.

    ``params``/``references`` are the element's authored values (what the row keeps, ``"@key"`` bindings
    included); ``resolved_*`` are the same values with bindings and type defaults resolved, used to produce
    it for bounds. Every edit changes both.
    """

    def __init__(self, seq: int, line: int, *, element_id: str | None, component_id: str | None,
                 geometry_id: str | None, producer: str, parent_id: str | None, authored: dict, resolved: dict,
                 existing: bool, owns_component: bool, extra: dict | None = None) -> None:
        super().__init__(seq, line)
        self.element_id = element_id
        self.component_id = component_id
        self.geometry_id = geometry_id
        self.producer = producer
        self.parent_id = parent_id
        self.existing = existing
        self.owns_component = owns_component
        self.has_params, self.has_references = "params" in authored, "references" in authored
        self.params: dict = copy.deepcopy(dict(authored.get("params") or {}))
        self.references: dict = copy.deepcopy(dict(authored.get("references") or {}))
        self.resolved_params: dict = copy.deepcopy(dict(resolved.get("params") or {}))
        self.resolved_references: dict = copy.deepcopy(dict(resolved.get("references") or {}))
        self.voids = dict.fromkeys(self.references.pop("voids", ()) or ())
        self.resolved_references.pop("voids", None)
        self.original_voids = tuple(sorted(self.voids))
        self.extra = dict(extra or {})
        self.base_anchor: Anchor | None = None
        self.geometry_changed = False
        self.size = sum(1 for _ in _numbers_in(self.params)) + sum(1 for _ in _numbers_in(self.references))
        self.point_count = _points_in(self.params) + _points_in(self.references)

    @property
    def is_new(self) -> bool:
        return not self.existing

    def label(self) -> str:
        return short(self.geometry_id) if self.existing else super().label()  # type: ignore[arg-type]

    @property
    def word(self) -> str:  # type: ignore[override]
        if self.producer == "loft":
            return "solid" if self.resolved_params.get("cap_ends", True) is not False else "surface"
        return {"planar-surface": "face", "curve": "path"}.get(self.producer, "solid")

    @property
    def form(self) -> str:
        return "other" if self.word == "surface" else self.word

    @property
    def is_solid(self) -> bool:
        return self.word == "solid" and "rectangular_cutouts" not in self.params

    @property
    def can_cut(self) -> bool:
        """A cutter removes one solid: an extruded solid without panel cutouts, or a capped loft solid."""

        return self.is_solid and self.producer in ("prism", "loft")

    def anchors(self) -> list[Anchor]:
        return [self.base_anchor] if self.base_anchor is not None else []

    def supports(self) -> list[Any]:
        """What its top is worked out from: the shape its base was set on, then the elements whose tops its
        references still read (their ids)."""

        references = dict(self.resolved_references)
        found: list[Any] = []
        if self.base_anchor is not None:
            references.pop("base", None)
            if self.base_anchor.kind == "top":
                found.append(self.base_anchor.target)
        found.extend(sorted(datum_targets(references)))
        return found

    def clone(self, seq: int, line: int) -> RowShape:
        twin = RowShape(seq, line, element_id=None, component_id=None, geometry_id=None, producer=self.producer,
                        parent_id=None, authored={"params": self.params, "references": self.references},
                        resolved={"params": self.resolved_params, "references": self.resolved_references},
                        existing=False, owns_component=True, extra=self.extra)
        twin.base_anchor = self.base_anchor
        twin.geometry_changed = True
        return twin

    # ---- edits
    def _refuse_reshaping(self, what: str) -> None:
        if "rectangular_cutouts" in self.params:
            raise ShapeError(f"{self.label()} carries panel cutouts; a script cannot {what} it")

    def _authored(self, params: dict, key: str) -> Any:
        if key not in params:
            raise ShapeError(f"{self.label()} takes its {key} from its type; a script cannot change it")
        if _bound(params[key]):
            raise ShapeError(f"{self.label()} has its {key} bound to project parameters; a script cannot change it")
        return params[key]

    def apply(self, change: PlanMap) -> None:
        verb = change.kind
        self._refuse_reshaping(verb)
        if self.producer == "wall":
            self._apply_to_line(change, verb)
        elif self.producer == "loft":
            for params, authored in ((self.params, True), (self.resolved_params, False)):
                profiles = self._authored(params, "profiles") if authored else params["profiles"]
                points = [[(*change.point(p[0], p[2]), p[1] + change.lift) for p in section] for section in profiles]
                rows = [[[x, y, z] for x, z, y in section] for section in points]
                if change.kind == "scale":
                    low = min(p[1] for section in rows for p in section)
                    rows = [[[x, low + change.factor * (y - low), z] for x, y, z in section] for section in rows]
                params["profiles"] = [[[clean(c) for c in p] for p in section] for section in rows]
        else:
            if (change.kind == "scale" or change.lift) and "top" in self.references:
                raise ShapeError(f"{self.label()} takes its height from a level or another solid's top; "
                                 f"{verb} can only change its plan position")
            for params, authored in ((self.params, True), (self.resolved_params, False)):
                self._apply_to_outline(params, change, authored)
        self.geometry_changed = True

    def _apply_to_outline(self, params: dict, change: PlanMap, authored: bool) -> None:
        drawing = params.get("work_plane")
        if drawing is None:
            profile = self._authored(params, "profile") if authored else params["profile"]
            params["profile"] = [[clean(c) for c in change.point(p[0], p[1])] for p in profile]
            if change.lift:
                elevation = params.get("elevation", 0.0)
                if authored and _bound(elevation):
                    raise ShapeError(f"{self.label()} has its elevation bound to project parameters; "
                                     "move it with set_base instead")
                value = clean(float(elevation) + change.lift)
                if value:
                    params["elevation"] = value
                else:
                    params.pop("elevation", None)
            if change.kind == "scale" and self.producer == "prism":
                height = self._authored(params, "height") if authored else params["height"]
                params["height"] = clean(float(height) * change.factor)
            return
        origin = drawing["origin"]
        px, pz = change.point(origin[0], origin[2])
        moved = {"origin": [clean(px), clean(origin[1] + change.lift), clean(pz)]}
        for key in ("xAxis", "yAxis", "normal"):
            moved[key] = list(change.vector3(drawing[key]))
        params["work_plane"] = moved
        if change.kind == "scale":
            profile = self._authored(params, "profile") if authored else params["profile"]
            params["profile"] = [[clean(c * change.factor) for c in p] for p in profile]
            if self.producer == "prism":
                height = self._authored(params, "height") if authored else params["height"]
                params["height"] = clean(float(height) * change.factor)

    def _apply_to_line(self, change: PlanMap, verb: str) -> None:
        if change.kind == "scale":
            raise ShapeError(f"{self.label()} can be moved, rotated or mirrored, not scaled")
        if change.lift:
            raise ShapeError(f"{self.label()} stands on a level or a solid's top without an offset; "
                             "change where it stands with set_base")
        openings = self.params.get("openings") or ()
        if change.kind == "mirror" and openings:
            raise ShapeError(f"{self.label()} has openings along it; mirror cannot keep them in place")
        for references, params in ((self.references, self.params), (self.resolved_references, self.resolved_params)):
            line = references.get("line")
            if not isinstance(line, dict):
                raise ShapeError(f"{self.label()} has no line of points to {verb}")
            moved = dict(line)
            for key in ("from", "to"):
                moved[key] = self._moved_point(line.get(key), change, verb)
            if "inward" in line:
                moved["inward"] = [clean(c) for c in change.vector(*line["inward"])]
            if change.kind == "mirror":
                moved["from"], moved["to"] = moved["to"], moved["from"]
            references["line"] = moved
            for opening in params.get("openings") or ():
                if isinstance(opening, dict) and "at" in opening:
                    opening["at"] = self._moved_point(opening["at"], change, verb)

    def _moved_point(self, reference: object, change: PlanMap, verb: str) -> dict:
        if not isinstance(reference, dict) or set(reference) != {"point"}:
            raise ShapeError(f"{self.label()} is placed on the project's grid or on another shape's line; "
                             f"a script cannot {verb} it")
        if _bound(reference):
            raise ShapeError(f"{self.label()} has its points bound to project parameters; a script cannot {verb} it")
        x, z = reference["point"]
        return {"point": [clean(c) for c in change.point(float(x), float(z))]}

    def set_height(self, height: float | ParamRef, world: World) -> None:
        if self.producer not in ("prism", "wall"):
            raise ShapeError(f"{self.label()} is a {self.word} whose height comes from its definition; "
                             "set_height changes a solid's height")
        self._refuse_reshaping("change the height of")
        if "top" in self.references:
            raise ShapeError(f"{self.label()} takes its height from a level or another solid's top; "
                             "set_height cannot keep that")
        if isinstance(height, ParamRef):
            self.params["height"], self.resolved_params["height"] = f"@{height.key}", world.parameter_value(height.key)
        else:
            if height <= 0:
                raise ShapeError(f"{self.label()} needs a positive height")
            self.params["height"] = self.resolved_params["height"] = clean(height)
        self.geometry_changed = True

    def pushpull(self, distance: float) -> None:
        if self.producer != "prism":
            raise ShapeError(f"pushpull() lengthens a solid made by extrude(); {self.label()} is not one")
        self._refuse_reshaping("push or pull")
        if "top" in self.references:
            raise ShapeError(f"{self.label()} takes its height from a level or another solid's top; "
                             "pushpull cannot keep that")
        height = float(self._authored(self.params, "height"))
        if height + distance < MIN_LENGTH:
            raise ShapeError(f"pushpull({distance!r}) would leave {self.label()} with no height")
        self.params["height"] = self.resolved_params["height"] = clean(height + distance)
        self.geometry_changed = True

    def set_base(self, anchor: Anchor) -> None:
        if "top" in self.references:
            raise ShapeError(f"{self.label()} takes its height from a level or another solid's top; "
                             "set_base cannot keep that")
        if self.producer == "wall" and (anchor.offset or anchor.param):
            raise ShapeError(f"{self.label()} stands on a level or on a solid's top without an offset")
        if self.producer == "loft":
            if anchor.param is not None:
                raise ShapeError(f"a loft solid's base cannot be bound to param('{anchor.param}')")
            self._authored(self.params, "profiles")
        self.base_anchor = anchor
        self.geometry_changed = True

    def check_extent(self) -> None:
        """Every number this row's placement states stays within reach after an edit."""

        for value in (*_numbers_in(self.params), *_numbers_in(self.references.get("line"))):
            coordinate(value, f"a value of {self.label()}")
        height = self.params.get("height")
        if is_number(height) and float(height) < MIN_LENGTH:
            raise ShapeError(f"the height of {self.label()} would be shorter than 0.000001 m")
        if self.base_anchor is not None:
            coordinate(self.base_anchor.offset, f"the base offset of {self.label()}")

    # ---- lowering and bounds
    def lowered(self, element_id_of: Callable[[Shape], str]) -> tuple[dict, dict]:
        """The authored params and references this handle now states, its base included (cuts are set by lowering)."""

        params, references = copy.deepcopy(self.params), copy.deepcopy(self.references)
        if self.base_anchor is not None:
            base, elevation = lower_anchor(self.base_anchor, element_id_of)
            rebase(self.producer, params, references, base, elevation)
        return params, references

    def bounds(self, world: World) -> Box:
        """Produced by its own producer in the record's context: the definition, before cuts."""

        context = world.context_for(self.resolved_references)
        params, references = copy.deepcopy(self.resolved_params), copy.deepcopy(self.resolved_references)
        if self.base_anchor is not None:
            anchor = self.base_anchor
            if anchor.kind == "level":
                reference: dict = {"level": anchor.target}
            else:
                reference = {"datum": "construction-anchor"}
                context.published["construction-anchor"] = InterfaceDatum.create(
                    datum_id="construction-anchor", kind=InterfaceDatumKind.LEVEL, published_by="construction",
                    value=round(top_value(anchor.target, world), 9), unit=LengthUnit.METER)
            extra = anchor.offset
            if anchor.param is not None:
                extra += world.parameter_value(anchor.param)
            rebase(self.producer, params, references, reference, extra or None)
        for target in sorted(datum_targets(references)):  # every top it reads, as the script now has it
            context.published[f"{target}-top"] = _top_datum(target, world.top_of(target))
        name = self.element_id if self.existing else "construction-copy"
        try:
            row = ElementRow(name, self.component_id or "construction-copy", self.producer, references, params)
            element = produce_rows((row,), context)[0]
            box = union(object_bounds(element.operations, element.bindings, context).values())
        except _PRODUCTION_ERRORS as exc:
            raise ShapeError(f"the bounds of {self.label()} cannot be computed from the project") from exc
        if box is None:
            raise ShapeError(f"{self.label()} has no geometry to measure")
        return box

    def top(self, world: World) -> float:
        """Its top as the runtime will publish it: produced where it now stands, on the tops as the script has them."""

        return self.bounds(world)[1][1]

    def can_carry(self, world: World) -> bool:
        """Whether its top is a level another shape can stand on: a solid extruded upward from a plan profile
        without panel cutouts - what publishes a top the runtime keeps. A block realised as a wall once it
        means one (#419 Stage C) publishes its top as the block did."""

        drawing = self.resolved_params.get("work_plane")
        horizontal = drawing is None or (list(drawing.get("normal", ())) == [0.0, 1.0, 0.0]
                                         and float(drawing["xAxis"][1]) == 0 and float(drawing["yAxis"][1]) == 0)
        return self.producer in ("prism", "wall") and horizontal and "rectangular_cutouts" not in self.params


def lower_anchor(anchor: Anchor, element_id_of: Callable[[Shape], str]) -> tuple[dict, float | str | None]:
    """The base reference and the elevation parameter an anchor lowers to."""

    base = {"level": anchor.target} if anchor.kind == "level" else {"datum": f"{element_id_of(anchor.target)}-top"}
    if anchor.param is not None:
        return base, f"@{anchor.param}"
    return base, (clean(anchor.offset) or None)


def rebase(producer: str, params: dict, references: dict, base: dict, elevation: float | str | None) -> None:
    """Make a row stand on ``base`` at ``elevation`` above it, the way its producer reads a base."""

    references["base"] = base
    if producer == "loft":
        low = min(float(p[1]) for section in params["profiles"] for p in section)
        lift = float(elevation or 0.0) - low
        params["profiles"] = [[[p[0], clean(float(p[1]) + lift), p[2]] for p in section] for section in params["profiles"]]
    elif producer != "wall":
        if elevation is None:
            params.pop("elevation", None)
        else:
            params["elevation"] = elevation


# ---------------------------------------------------------------- bounds of produced operations
def object_bounds(operations, bindings, context: ProductionContext) -> dict[str, Box]:
    """Bounds of every delivered object of these operations, their base datums resolved from ``context``.

    They are read in dependency order, whatever order they come in: a wall's come sorted by id, its cut first.
    """

    datum_of = {binding.op_id: binding.datum_id for binding in bindings if binding.parameter_name == "base_level"}
    resolved = []
    for operation in _by_inputs(tuple(operations)):
        datum = datum_of.get(operation.op_id)
        if datum is not None and all(parameter.name != "base_level" for parameter in operation.parameters):
            level = GeometryParameter.create(name="base_level", kind=GeometryParameterKind.NUMBER,
                                             value=round(context.datum_value(datum), 9), unit=LengthUnit.METER)
            operation = replace(operation, parameters=tuple(sorted((*operation.parameters, level), key=lambda p: p.name)))
        resolved.append(operation)
    program = SimpleNamespace(proposal=SimpleNamespace(operations=tuple(resolved)),
                              operation_order=tuple(operation.op_id for operation in resolved))
    return {object_id: _box((tuple(row["bbox_min"]), tuple(row["bbox_max"])))
            for object_id, row in expected_object_bounds(program).items()}


def _by_inputs(operations: tuple) -> list:
    """The operations in the order given, each moved after those whose outputs it consumes."""

    made_here = {object_id for operation in operations for object_id in operation.output_object_ids}
    ordered: list = []
    made: set[str] = set()
    remaining = list(operations)
    while remaining:
        waiting = []
        for operation in remaining:
            if all(object_id in made or object_id not in made_here for object_id in operation.input_object_ids):
                ordered.append(operation)
                made.update(operation.output_object_ids)
            else:
                waiting.append(operation)
        if len(waiting) == len(remaining):  # a cycle: left as given, for the reader to refuse
            return ordered + waiting
        remaining = waiting
    return ordered


# ---------------------------------------------------------------- the record's facts
def datum_targets(value: object) -> set[str]:
    """Element ids whose published top a reference value stands on (``{"datum": "<element>-top"}``)."""

    found: set[str] = set()
    if isinstance(value, dict):
        datum = value.get("datum")
        if isinstance(datum, str) and datum.endswith("-top"):
            found.add(datum[:-4])
        for item in value.values():
            found |= datum_targets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= datum_targets(item)
    return found


def mentioned_elements(value: object, known: Any) -> set[str]:
    """Every element a reference value names, directly or through a published ``<id>-top`` datum - what
    production orders a row after (``known`` holds the element ids there are)."""

    found: set[str] = set()
    if isinstance(value, str):
        if value in known:
            found.add(value)
        elif value.endswith("-top") and value[:-4] in known:
            found.add(value[:-4])
    elif isinstance(value, dict):
        for item in value.values():
            found |= mentioned_elements(item, known)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= mentioned_elements(item, known)
    return found


class World:
    """What one record says that a script reads: levels, parameters, editable elements, relations, production."""

    def __init__(self, record: StateRecord) -> None:
        self.record = record
        self.entities = {entity.entity_id: entity for entity in record.entities}
        levels = [entity for entity in record.entities_of("Level@1")
                  if is_number(entity.fields.get("elevation")) and math.isfinite(entity.fields["elevation"])]
        self.levels = {entity.entity_id: float(entity.fields["elevation"]) for entity in levels}
        ground = sorted((float(e.fields["elevation"]), e.entity_id) for e in levels if e.fields.get("role") == "ground")
        lowest = sorted((float(e.fields["elevation"]), e.entity_id) for e in levels)
        pick = (ground or lowest or [None])[0]
        self.default_level: tuple[str, float] | None = (pick[1], pick[0]) if pick is not None else None
        self.parameter_keys = frozenset(parameter.key for parameter in record.parameters)
        self.elements_of: dict[str, list[str]] = {}
        self.voids_of: dict[str, tuple[str, ...]] = {}  # element -> the elements it names as voids
        self.void_hosts: dict[str, list[str]] = {}  # element -> the elements that name it as a void
        self.stands_on: dict[str, set[str]] = {}  # element -> the elements whose tops it stands on
        self.top_users: dict[str, list[str]] = {}  # element -> the elements standing on its top
        for element in record.entities_of("Element@1"):
            component = element.fields.get("component_id") or element.parent_id
            self.elements_of.setdefault(str(component), []).append(element.entity_id)
            references = element.fields.get("references")
            references = references if isinstance(references, dict) else {}
            voids = references.get("voids")
            voids = tuple(str(void) for void in voids) if isinstance(voids, (list, tuple)) else ()
            self.voids_of[element.entity_id] = voids
            for void in voids:
                self.void_hosts.setdefault(void, []).append(element.entity_id)
            targets = datum_targets(references)
            self.stands_on[element.entity_id] = targets
            for target in targets:
                self.top_users.setdefault(target, []).append(element.entity_id)
        self._evaluated: Any = None
        self._values: dict[str, float] | None = None
        self._resolved: dict[str, dict] | None = None
        self._references: tuple | None = None
        self._rows: tuple | None = None
        self._rows_by_id: dict[str, ElementRow] | None = None
        self._production: tuple | None = None
        self._downstream: dict[str, set[str]] | None = None
        self.tops: dict[Any, float] = {}  # shape or element id -> the elevation of its top; cleared by every edit
        # The script's model, when a script runs: the shape that now stands for an element (the new shape that
        # redefines it, or the row the script reached; None for any other) and what walking the supports costs.
        # Without a script, the record is the model.
        self.current_shape: Callable[[str], Shape | None] | None = None
        self.spend: Callable[[int], None] | None = None

    # ---- tops, through the script's model
    def top_of(self, element_id: str) -> float:
        """The elevation of an element's top as the script now has it (see ``resolve``)."""

        return self.resolve(element_id)

    def resolve(self, root: Any) -> float:
        """The top of a shape, or of an element by id, as the script now has it.

        Worked out with an explicit stack, so a stack of supports of any depth costs steps, never recursion: each
        node after the supports it reads - a drawn shape's anchor target; a row's base shape and the elements whose
        tops its references read; for an element, the shape that now stands for it, else the elements the record
        says it stands on - and each once, kept in ``tops`` until the script next changes a shape. An element the
        script never reached is produced again, alone, on the tops so worked out: never the whole project. A
        support met again on the way down is a cycle.
        """

        tops = self.tops
        if root in tops:
            return tops[root]
        stack: list[Any] = [root]
        opened: dict[Any, None] = {}  # the nodes whose supports are above them on the stack
        walked = 0  # nodes worked out from values at hand: one step per hundred, as any walk
        while stack:
            node = stack[-1]
            if node in tops:
                stack.pop()
                continue
            if node in opened:  # every support is worked out: its own turn
                del opened[node]
                pending: list[Any] = []
            else:
                pending = [support for support in self._supports_of(node) if support not in tops]
            if pending:
                opened[node] = None
                for support in pending:
                    if support in opened:
                        raise ShapeError(f"{self._label(support)} would stand on its own top")
                    stack.append(support)
                continue
            tops[node], produced = self._top_value_of(node)
            stack.pop()
            if produced and self.spend is not None:
                self.spend(1)  # a row produced again: a step of its own
            else:
                walked += 1
        if self.spend is not None:
            self.spend(walked // 100)
        return tops[root]

    def _label(self, node: Any) -> str:
        return short(self.geometry_id(node)) if isinstance(node, str) else node.label()

    def _supports_of(self, node: Any) -> list[Any]:
        if isinstance(node, str):
            shape = self.current_shape(node) if self.current_shape is not None else None
            return [shape] if shape is not None else sorted(self.stands_on.get(node, ()))
        if isinstance(node, Drawn):
            if not node.upward:
                raise ShapeError(f"{node.label()} has no top to stand on")
            anchor = node.anchor
            return [anchor.target] if anchor.kind == "top" else []  # type: ignore[union-attr]
        return node.supports()

    def _top_value_of(self, node: Any) -> tuple[float, bool]:
        """A node's top once its supports are in ``tops``, and whether a row was produced for it."""

        if isinstance(node, str):
            shape = self.current_shape(node) if self.current_shape is not None else None
            if shape is not None:
                return self.tops[shape], False
            return self._reproduced_top(node), True
        if isinstance(node, Drawn):
            anchor = node.anchor
            below = self.levels[anchor.target] if anchor.kind == "level" else self.tops[anchor.target]  # type: ignore[union-attr]
            return below + _offset_value(anchor, self) + height_value(node.height, self), False  # type: ignore[arg-type]
        return node.top(self), True

    def _reproduced_top(self, element_id: str) -> float:
        """An element's top, its row produced alone on the tops worked out so far for what it stands on."""

        row = self.row_of(element_id)
        if row is None:
            raise ShapeError(f"the top of {short(self.geometry_id(element_id))} cannot be computed from the project")
        context = self.context_for(row.references)
        for target in self.stands_on.get(element_id, ()):
            context.published[f"{target}-top"] = _top_datum(target, self.tops[target])
        try:
            produce_rows((row,), context)
        except _PRODUCTION_ERRORS as exc:
            raise ShapeError(f"the top of {short(self.geometry_id(element_id))} cannot be computed from the project") from exc
        datum = context.published.get(f"{element_id}-top")
        if datum is None:
            raise ShapeError(f"the top of {short(self.geometry_id(element_id))} cannot be computed from the project")
        return float(json.loads(datum.value_json))

    def context_for(self, references: Mapping[str, Any]) -> ProductionContext:
        """The context one row is produced in for its top or bounds: a fresh one over the project's grids and
        levels (the tops it reads are set by the caller), or the whole record's when the row is placed on other
        elements (host lines), which only their production provides."""

        placed_on = mentioned_elements(references, self.voids_of) - datum_targets(references)
        return self.produced_context() if placed_on else self.base_context()

    def downstream(self, entity_id: str) -> set[str]:
        """The entities whose references depend on an entity: the record's dependency edges, read once."""

        if self._downstream is None:
            self._downstream = {}
            for edge in self.record.dependency_edges():
                if edge.upstream_ref.startswith("entity:") and edge.downstream_ref.startswith("entity:"):
                    self._downstream.setdefault(edge.upstream_ref.removeprefix("entity:"), set()).add(
                        edge.downstream_ref.removeprefix("entity:"))
        return self._downstream.get(entity_id, set())

    def component_of(self, element_id: str) -> str:
        element = self.entities[element_id]
        return str(element.fields.get("component_id") or element.parent_id)

    def geometry_id(self, element_id: str) -> str:
        """The id the model view lists for an element: its component's, unless the component has several parts."""

        if element_id not in self.entities:
            return element_id
        component = self.component_of(element_id)
        return component if len(self.elements_of.get(component, [])) == 1 else element_id

    def evaluated(self) -> Any:
        """The project's parameters evaluated once."""

        if self._evaluated is None:
            try:
                self._evaluated = evaluate_parameters(self.record)
            except StateRecordError as exc:
                raise ShapeError("the project's parameters cannot be evaluated") from exc
        return self._evaluated

    def parameter_value(self, key: str) -> float:
        if self._values is None:
            self._values = self.evaluated().as_mapping()
        return float(self._values[key])

    def element_row(self, element_id: str, fields: Mapping[str, Any]) -> ElementRow:
        """A producer row from an element's fields as the record keeps them: its type's defaults merged, its
        ``"@key"`` bindings resolved - for this element alone, never the whole project."""

        merged = dict(fields)
        type_ref = merged.get("type_ref")
        if type_ref is not None:
            declared = self.entities.get(str(type_ref).removeprefix("entity:"))
            if declared is None or declared.schema != "Type@1":
                raise ShapeError(f"{short(element_id)} names a type this project does not have")
            for name in ("params", "references"):
                if name in declared.fields or name in merged:
                    merged[name] = {**(declared.fields.get(name) or {}), **(merged.get(name) or {})}
        params, references = dict(merged.get("params") or {}), dict(merged.get("references") or {})
        if _bound(params) or _bound(references):
            try:
                params, references = substitute(params, self.evaluated()), substitute(references, self.evaluated())
            except DerivationError as exc:
                raise ShapeError(f"{short(element_id)} binds a parameter this project does not have") from exc
        entity = self.entities.get(element_id)
        component = merged.get("component_id") or (entity.parent_id if entity is not None else None)
        return ElementRow(element_id, str(component), str(merged.get("producer")), references, params,  # type: ignore[arg-type]
                          tuple(entity.basis_refs) if entity is not None else ())

    def resolved(self, element_id: str) -> dict:
        if self._resolved is None:
            try:
                self._resolved = resolve_element_bindings(self.record)
            except StateRecordError as exc:
                raise ShapeError("the project's parameter bindings cannot be read") from exc
        return copy.deepcopy(self._resolved[element_id])

    def editable(self, identifier: str) -> tuple[str, str, str]:
        """(element id, component id, geometry id) of the existing geometry ``identifier`` names, or a refusal."""

        entity = self.entities.get(identifier)
        if entity is None:
            raise ShapeError(f"{short(identifier)} is not in this project")
        if entity.schema == "Component@1":
            parts = self.elements_of.get(identifier, [])
            if not parts:
                raise ShapeError(f"{short(identifier)} has no geometry to edit")
            if len(parts) > 1:
                listed = short(", ".join(parts), 80)
                raise ShapeError(f"{short(identifier)} has several parts ({listed}); get() one of them by its id")
            element_id = parts[0]
        elif entity.schema == "Element@1":
            element_id = identifier
        else:
            raise ShapeError(f"{short(identifier)} is {entity.schema} in this project, not geometry")
        if self.resolved(element_id).get("producer") not in EDITABLE_PRODUCERS:
            raise ShapeError(f"{short(element_id)} is not editable by a construction script")
        component = self.component_of(element_id)
        return element_id, component, self.geometry_id(element_id)

    def base_context(self) -> ProductionContext:
        """A fresh production context over the project's grids and levels (read once), nothing published yet."""

        if self._references is None:
            grids = levels = None
            try:
                grids = project_grids_of(self.record)
            except _PRODUCTION_ERRORS:
                pass
            try:
                levels = project_levels_of(self.record) if self.levels else None
            except _PRODUCTION_ERRORS:
                pass
            self._references = (grids, levels)
        grids, levels = self._references
        return ProductionContext(references=ReferenceContext(grids=grids, levels=levels), published={})

    def rows(self) -> tuple:
        """The record's rows as the producers read them, in production order; none when they cannot be read."""

        if self._rows is None:
            try:
                self._rows = element_rows_of(self.record)
            except _PRODUCTION_ERRORS:
                self._rows = ()
        return self._rows

    def row_of(self, element_id: str) -> ElementRow | None:
        if self._rows_by_id is None:
            self._rows_by_id = {row.element_id: row for row in self.rows()}
        return self._rows_by_id.get(element_id)

    def production(self) -> tuple[tuple, ProductionContext, dict]:
        """The record's rows produced once, row by row: what fails is left out, never raised."""

        if self._production is None:
            context = self.base_context()
            rows = self.rows()
            produced = {}
            for row in rows:
                try:
                    produced[row.element_id] = produce_rows((row,), context)[0]
                except _PRODUCTION_ERRORS:
                    continue
            self._production = (rows, context, produced)
        return self._production

    def produced_context(self) -> ProductionContext:
        """A copy of the record's produced context: its published datums and host lines, to produce one row in."""

        _, base, _ = self.production()
        return ProductionContext(
            references=ReferenceContext(grids=base.references.grids, levels=base.references.levels,
                                        hosts=dict(base.references.hosts)),
            published=dict(base.published), frame_id=base.frame_id)
