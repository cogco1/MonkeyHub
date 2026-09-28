"""The construction script's agent-facing contract (#419, L1).

One machine-readable table says what a script may call: the verbs, their
signatures, what each returns and means, the language, its limits and one
example. The interpreter binds its arguments from this same table
(``script.IMPLEMENTED_VERBS`` must name exactly these verbs), so the contract an
agent reads and the one it is held to cannot drift apart.

Nothing here names how the runtime realises a shape. The layer rule
(``LAYER_RULE_TOKENS``, ``layer_rule_violations``) pins that for any L1 text:
no realisation name, classification field or backend word. ``loft`` is a
construction verb, the generic geometric operation, and is not one of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LIMITS: dict[str, Any] = {
    "characters": 20_000,  # script length
    "steps": 20_000,  # nodes evaluated; walking a sequence costs one more per 100 elements
    "seconds": 5,  # wall clock
    "loopIterations": 1_000,  # per loop and per comprehension generator
    "callDepth": 16,
    "nesting": 100,  # levels of the script's own nesting, and of nested lists and tuples
    "geometryResults": 300,  # shapes a script makes, copies included
    "rangeItems": 10_000,  # numbers one range() gives
    "listElements": 10_000,  # elements of one list or tuple, nested ones included
    "createdElements": 200_000,  # elements of all lists and tuples a script creates
    "textCharacters": 10_000,  # characters of one text value
    "printLines": 1_000,
    "roundDigits": 12,  # round(x, n) takes -12 <= n <= 12
    "profilePoints": 256,
    "profileEdgePairs": 1_000_000,  # edge pairs checked for crossings, per script
    "pathPoints": 512,
    "totalPoints": 100_000,  # every point of every profile, section and path of the shapes a script leaves
    "loftSections": 64,
    "cuttersPerShape": 300,
    "idLength": 90,
    "coordinateRange": 100_000,  # metres from the origin, every coordinate
    "minimumLength": 0.000001,  # metres, every length, width, depth, radius and height
}


class _Required:
    """The marker of a parameter without a default."""

    def __repr__(self) -> str:
        return "REQUIRED"


REQUIRED = _Required()


@dataclass(frozen=True)
class Param:
    """One verb parameter: its name, its default (``REQUIRED`` when it has none) and whether it takes the rest."""

    name: str
    default: Any = REQUIRED
    rest: bool = False


@dataclass(frozen=True)
class Verb:
    name: str
    params: tuple[Param, ...]
    returns: str
    description: str

    @property
    def signature(self) -> str:
        parts = []
        for param in self.params:
            if param.rest:
                parts.append("*" + param.name)
            elif param.default is REQUIRED:
                parts.append(param.name)
            else:
                parts.append(f"{param.name}={param.default!r}")
        return f"{self.name}({', '.join(parts)})"

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "signature": self.signature, "returns": self.returns, "description": self.description}


def _p(*names: str, **defaults: Any) -> tuple[Param, ...]:
    return tuple(Param(name) for name in names) + tuple(Param(name, value) for name, value in defaults.items())


VERBS: tuple[Verb, ...] = (
    Verb("rect", _p("x", "z", "width", "depth"), "profile",
         "Plan rectangle with its corner at (x, z), width along +x and depth along +z."),
    Verb("polygon", _p("points"), "profile",
         "Plan polygon [(x, z), ...] in order: at least three points, the first point not repeated, "
         "no point repeated and no edge crossing another."),
    Verb("circle", _p("x", "z", "radius", segments=24), "profile",
         "Regular polygon of 8 to 128 points around (x, z), starting on +x and turning toward +z. "
         "It is faceted: a polygon, not a true circle."),
    Verb("offset", _p("profile", "distance"), "profile",
         "Mitred offset of a profile: a positive distance grows it, a negative one shrinks it; "
         "an offset that collapses the profile is refused."),
    Verb("plane", _p("origin", "x_axis", "y_axis"), "plane",
         "A drawing plane through origin (x, y, z) with perpendicular axes. A profile drawn on it reads its "
         "(x, z) pairs as (u, v) along x_axis and y_axis; its normal is x_axis cross y_axis."),
    Verb("front", _p(z=0), "plane",
         "The vertical plane at depth z: draws in (x, y) and extrudes along +z."),
    Verb("side", _p(x=0), "plane",
         "The vertical plane at x: draws in (z, y) and extrudes along +x - the opposite of "
         "plane((x, 0, 0), (0, 0, 1), (0, 1, 0)), whose normal x_axis cross y_axis points along -x."),
    Verb("extrude", _p("profile", "height", at=0, plane=None), "solid",
         "Push-pull a profile into a solid. In plan it rises from at by height; a negative height goes down. "
         "On a plane it goes along the plane's normal (a negative height goes the other way); the plane's "
         "origin places it, so at is not given with plane. height may be param(key)."),
    Verb("face", _p("profile", at=0, plane=None), "face",
         "A flat face: in plan at the elevation at, or on a plane."),
    Verb("path", _p("points"), "path",
         "An open polyline [(x, y, z), ...] of 2 to 512 points lying in one plane."),
    Verb("section", _p("profile", at=0), "section",
         "One loft section: a plan profile at the elevation at."),
    Verb("loft", _p("sections", cap=True), "solid",
         "A solid through two or more sections with the same point count and corresponding point order, "
         "all measured from the same level or the same top. cap=False leaves both ends open: an open "
         "surface, not a solid."),
    Verb("move", _p("obj", dx=0, dy=0, dz=0), "obj",
         "Move a shape in place by (dx, dy, dz)."),
    Verb("rotate", _p("obj", "degrees", about=(0, 0)), "obj",
         "Rotate a shape in place about the vertical axis through about = (x, z); positive degrees turn +x "
         "toward +z."),
    Verb("scale", _p("obj", "factor", about=(0, 0)), "obj",
         "Scale a shape in place by a positive factor: plan positions about (x, z); a solid keeps its base "
         "and scales its height, a path or loft solid keeps its lowest point."),
    Verb("mirror", _p("obj", x=None, z=None), "obj",
         "Reflect a shape in place across the vertical plane x = value or z = value (give exactly one)."),
    Verb("copy", _p("obj", dx=0, dy=0, dz=0), "new shape",
         "A new shape with the same geometry (not its cuts), moved by (dx, dy, dz)."),
    Verb("array", _p("obj", "count", dx=0, dy=0, dz=0), "list of shapes",
         "[obj, copy1, ...]: count shapes, each copy moved one more step of (dx, dy, dz)."),
    Verb("pushpull", _p("obj", "distance"), "obj",
         "Lengthen a solid along its extrusion by distance (negative shortens it). A face made in this "
         "script is pulled into a solid of that height."),
    Verb("set_height", _p("obj", "height"), "obj",
         "Set a solid's height: a number (negative goes down, for a solid made in this script) or param(key)."),
    Verb("set_base", _p("obj", "at"), "obj",
         "Set where a solid, a face or a loft solid stands, like at: a number, level(id), top(obj), "
         "optionally plus a number or param(key)."),
    Verb("cut", (Param("host"), Param("cutters", rest=True)), "host",
         "Remove the cutters' solids from the host. Cutters may be given one by one or as lists. Each "
         "cutter keeps its id and stays in the model hidden. A cutter cannot have cutters of its own, a "
         "host cannot be a cutter, and nothing may stand on a cutter's top."),
    Verb("uncut", (Param("host"), Param("cutters", rest=True)), "host",
         "Stop removing the cutters from the host - all of them when none is given; they show again."),
    Verb("level", _p("id"), "anchor",
         "A project level, for at; add a number or param(key) to offset it."),
    Verb("top", _p("obj"), "anchor",
         "The top of a solid extruded upward in plan, for at: what stands on it follows it."),
    Verb("param", _p("key"), "binding",
         "A project parameter, used directly as a height or added to an anchor as an at offset. It is a "
         "binding, not a number: arithmetic on it is refused."),
    Verb("bounds", _p("obj"), "((xmin, ymin, zmin), (xmax, ymax, zmax))",
         "The shape's bounds from its definition, before cuts, where it stands now: a top it stands on is read "
         "as this script has changed or redefined it."),
    Verb("name", _p("obj", "id"), "obj",
         "Give a new shape its id."),
    Verb("get", _p("id"), "shape",
         "Existing geometry of the project, by the id the model view lists, for editing in place."),
    Verb("delete", _p("obj"), "None",
         "Remove a shape. Existing geometry is removed from the project; nothing may still stand on it or be "
         "cut by it."),
)
_VERBS_BY_NAME = {verb.name: verb for verb in VERBS}

BUILTINS: tuple[str, ...] = ("range", "len", "min", "max", "abs", "round", "sum", "enumerate", "zip", "list", "tuple",
                             "float", "int", "print")
MATH: tuple[str, ...] = ("pi", "sqrt", "sin", "cos", "tan", "atan2", "radians", "degrees", "floor", "ceil")

EXAMPLE = "\n".join([
    "mass = extrude(rect(0, 0, 12, 8), 3)",
    "block = extrude(rect(3, 2, 6, 4), 2.5, at=top(mass))",
    "for i in range(4):",
    "    cutter = extrude(rect(1 + 2.8 * i, -0.2, 1.2, 0.6), 1.5, at=0.9)",
    "    cut(mass, cutter)",
    "print(bounds(block))",
])


def verb(name: str) -> Verb:
    return _VERBS_BY_NAME[name]


def vocabulary() -> dict[str, Any]:
    """The construction contract as data: conventions, language, limits, verbs and one example (L1)."""

    return {
        "schema": "ConstructionVocabulary@1",
        "conventions": {
            "units": "metres",
            "axes": "Y is up. A plan point is (x, z); a 3D point is (x, y, z).",
            "rotation": "rotate() turns about a vertical axis; positive degrees turn +x toward +z.",
            "elevation": ("A number given as at is an elevation in metres above the project's zero. level(id) and "
                          "top(obj) measure from a project level or from the top of a solid; add a number or "
                          "param(key) to offset them."),
            "planes": ("On a plane, a profile's (x, z) pairs are read as (u, v) along the plane's x_axis and "
                       "y_axis."),
            "identity": ("Each shape that survives the script keeps one id: the one given with name(obj, id); "
                         "else the module-level variable it was last assigned to, with _ becoming - (a variable "
                         "that holds the shape itself wins over a list that contains it; several shapes under one "
                         "variable are numbered <variable>-1, <variable>-2, ... in creation order); else "
                         "<host>-cut-<12 hex digits> for an unnamed cutter; else shape-<12 hex digits>. The digits "
                         "come from the statements that made the shape - the module-level statement, then each "
                         "statement it called, down to the one that made it (their text, whitespace ignored) - and "
                         "how many shapes that chain of statements made before it, so running the same script "
                         "again updates the same shapes, named or not, and the same helper called from two "
                         "statements makes two shapes. An unnamed shape whose statement changes gets a new id and "
                         "the old shape stays: name what you will edit later, and name cutters you may change "
                         "later, since an edited unnamed cutter is a new cutter while the old one keeps cutting "
                         "its host until uncut(host) clears the host's cutters. A new shape given the id of "
                         "existing geometry redefines it and keeps what it cuts (uncut(host) with no cutters "
                         "removes them all); get(id) edits existing geometry in place, and is the only way to "
                         "change geometry realized with support for openings (doors and windows): a new definition "
                         "under its id is refused, since it would drop them. An id that already names something "
                         "else in the project is refused."),
            "reach": ("Every coordinate stays within 100 000 m of the origin and every length, width, depth, "
                      "radius and height is at least 0.000001 m. A project without a level has nothing to "
                      "measure heights from: add a level first."),
            "handles": "Every verb returns a handle or a value; handles are opaque and print as <solid id>.",
            "errors": ("A refused script saves nothing: the answer names the line, the column, the source line "
                       "and one sentence; inside a function it adds the lines it was called from. What the "
                       "result would leave wrong in the project - a cutter with cutters of its own, something "
                       "standing on a cutter's top, a shape standing on its own top, deleted geometry that "
                       "something still cuts or stands on, a cut that misses its host or would take away a whole "
                       "corner or side of it - is refused after the whole script has run, at the line that made "
                       "it so."),
        },
        "language": {
            "summary": "A small Python subset, interpreted and never executed.",
            "allowed": [
                "numbers, text, lists, tuples, True, False and None",
                "=, += and tuple unpacking; xs += [...] extends the list xs in place, the way to grow a long list",
                "for ... in over range, lists, enumerate and zip",
                "if / elif / else",
                "def with positional and keyword parameters, defaults and return",
                "break, continue and pass",
                "list comprehensions",
                "arithmetic and comparisons; text joins with +",
                "==, != and in compare numbers, text, lists and tuples, nested ones included; min, max and sum take "
                "numbers",
            ],
            "notAllowed": [
                "import", "attribute access (a.b)", "names starting with __", "while", "with", "try", "lambda",
                "global", "class", "dict and set literals", "f-strings and % formatting", "* and ** unpacking",
                "item assignment (a[i] = ...)", "a function inside a function",
            ],
            "builtins": list(BUILTINS),
            "math": list(MATH),
        },
        "limits": dict(LIMITS),
        "verbs": [item.to_dict() for item in VERBS],
        "example": EXAMPLE,
    }


# ---------------------------------------------------------------- the layer rule
LAYER_RULE_TOKENS: tuple[str, ...] = ("prism", "planar-surface", "column-array", "producer", "semantickind",
                                      "semantic_kind", "boolean", "aperture", "topology", "occt", "wall")
"""What L1 text never contains (case-insensitive substrings): realisation names, classification fields and
backend words. ``loft`` is a construction verb and ``path``/``curve`` are ordinary geometry words, so they are
not listed."""


def layer_rule_violations(text: str) -> tuple[str, ...]:
    """The ``LAYER_RULE_TOKENS`` that ``text`` contains, case-insensitively, in the order of the list."""

    lowered = text.lower()
    return tuple(token for token in LAYER_RULE_TOKENS if token in lowered)
