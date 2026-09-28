"""The construction script's parser and interpreter (#419, L1).

A script is parsed with ``ast`` and walked by an allow-list interpreter: Python
never compiles or runs it. The whole tree is checked before anything runs, so a
refused construct anywhere in the script is refused at its line even if it would
never be reached. Evaluation counts every node it evaluates (the step limit),
every loop's iterations and the call depth.

Verbs are bound from ``vocabulary.VERBS`` - the table an agent reads - and
implemented by ``Session``, which keeps the script's shapes, the existing
geometry it reached with ``get()`` and the ``print`` log. What a verb refuses it
refuses with one sentence; the interpreter adds the line, the column and the
source line (``ConstructionError``).
"""
from __future__ import annotations

import ast
import math
from typing import Any, Callable

from archflow.project.refs import require_identifier
from archflow.state.state_record import StateRecord
from monkeyarch.construction.shapes import (
    MAX_PATH_POINTS,
    Anchor,
    Drawn,
    ParamRef,
    Plane,
    PlanMap,
    Profile,
    RowShape,
    Section,
    Shape,
    ShapeError,
    World,
    as_profile,
    binding_refusal,
    circle,
    combine,
    describe,
    front,
    is_number,
    number,
    offset,
    path_frame,
    plane,
    point2,
    point3,
    polygon,
    rect,
    side,
)
from monkeyarch.construction.vocabulary import BUILTINS, LIMITS, MATH, REQUIRED, VERBS, Verb, verb

MAX_SEQUENCE = 10_000
MAX_NESTING = 100
MAX_LOG_LINES = 1_000
MAX_LOG_LINE = 2_000
MAX_ID = 90


class ConstructionError(ValueError):
    """A refused construction script: where (line, 1-based column, the source line) and one sentence why."""

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None,
                 source_line: str | None = None) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.source_line = source_line
        where = "" if line is None else (f"line {line}, column {column}: " if column is not None else f"line {line}: ")
        super().__init__(where + message)

    def to_dict(self) -> dict[str, Any]:
        return {"code": "CONSTRUCTION_INVALID", "line": self.line, "column": self.column,
                "sourceLine": self.source_line, "message": self.message}


def _source_line(lines: list[str], line: int | None) -> str | None:
    return lines[line - 1].strip() if line is not None and 1 <= line <= len(lines) else None


def _error_at(lines: list[str], node: object, message: str) -> ConstructionError:
    line = getattr(node, "lineno", None)
    column = getattr(node, "col_offset", None)
    return ConstructionError(message, line=line, column=None if column is None else column + 1,
                             source_line=_source_line(lines, line))


# ---------------------------------------------------------------- the allow-list
_REFUSED: tuple[tuple[type, str], ...] = tuple((kind, words) for kind, words in (
    (ast.Import, "import"), (ast.ImportFrom, "import"), (ast.Attribute, "attribute access (a.b)"),
    (ast.While, "while"), (ast.With, "with"), (ast.AsyncWith, "async"), (ast.Try, "try"),
    (getattr(ast, "TryStar", ast.Try), "try"), (ast.Lambda, "lambda"), (ast.Global, "global"),
    (ast.Nonlocal, "nonlocal"), (ast.ClassDef, "class"), (ast.JoinedStr, "an f-string"),
    (ast.FormattedValue, "an f-string"), (ast.Dict, "a dict"), (ast.DictComp, "a dict"), (ast.Set, "a set"),
    (ast.SetComp, "a set"), (ast.GeneratorExp, "a generator expression"), (ast.Yield, "yield"),
    (ast.YieldFrom, "yield"), (ast.Await, "await"), (ast.Starred, "* unpacking"), (ast.NamedExpr, ":="),
    (ast.Delete, "del"), (ast.Assert, "assert"), (ast.Raise, "raise"), (ast.AnnAssign, "an annotated assignment"),
    (ast.AsyncFunctionDef, "async"), (ast.AsyncFor, "async"), (getattr(ast, "Match", ast.Try), "match"),
    (ast.MatMult, "the @ operator"), (ast.BitAnd, "a bitwise operator"), (ast.BitOr, "a bitwise operator"),
    (ast.BitXor, "a bitwise operator"), (ast.LShift, "a bitwise operator"), (ast.RShift, "a bitwise operator"),
    (ast.Invert, "a bitwise operator"), (getattr(ast, "TypeAlias", ast.Try), "type"),
))
_ALLOWED = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.For, ast.If, ast.FunctionDef, ast.Return, ast.Break,
    ast.Continue, ast.Pass, ast.Constant, ast.Name, ast.Load, ast.Store, ast.BinOp, ast.UnaryOp, ast.BoolOp,
    ast.Compare, ast.IfExp, ast.Call, ast.keyword, ast.List, ast.Tuple, ast.ListComp, ast.comprehension,
    ast.Subscript, ast.Slice, ast.arguments, ast.arg, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
)
_VERB_NAMES = frozenset(item.name for item in VERBS)


def _read_only(name: str) -> str | None:
    if name in _VERB_NAMES:
        return f"{name} is a construction verb; choose another name"
    if name in BUILTINS:
        return f"{name} is a builtin; choose another name"
    if name in MATH:
        return f"{name} is a math name; choose another name"
    return None


class _Checker:
    """The allow-list pass over the whole tree, before anything runs."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def refuse(self, node: object, message: str) -> ConstructionError:
        return _error_at(self.lines, node, message)

    def name(self, node: object, name: str, *, stored: bool) -> None:
        if name.startswith("__"):
            raise self.refuse(node, "names starting with __ are not part of the construction language")
        if stored and _read_only(name):
            raise self.refuse(node, _read_only(name))  # type: ignore[arg-type]

    def target(self, node: ast.AST) -> None:
        if isinstance(node, ast.Subscript):
            raise self.refuse(node, "assignment to an item (a[i] = ...) is not part of the construction language")

    def check(self, node: ast.AST, *, position: object, in_function: bool, in_loop: bool) -> None:
        if hasattr(node, "lineno"):
            position = node
        for kind, words in _REFUSED:
            if isinstance(node, kind):
                raise self.refuse(position, f"{words} is not part of the construction language")
        if not isinstance(node, _ALLOWED):
            raise self.refuse(position, f"{type(node).__name__} is not part of the construction language")
        if isinstance(node, ast.Constant) and not (node.value is None or isinstance(node.value, (bool, int, float, str))):
            raise self.refuse(position, f"the value {node.value!r} is not part of the construction language")
        if isinstance(node, ast.Name):
            self.name(node, node.id, stored=isinstance(node.ctx, ast.Store))
        elif isinstance(node, ast.FunctionDef):
            self.function(node, in_function=in_function)
            for default in node.args.defaults:
                self.check(default, position=node, in_function=in_function, in_loop=in_loop)
            for statement in node.body:
                self.check(statement, position=node, in_function=True, in_loop=False)
            return
        elif isinstance(node, ast.Return) and not in_function:
            raise self.refuse(node, "return outside a function is not part of the construction language")
        elif isinstance(node, (ast.Break, ast.Continue)) and not in_loop:
            word = "break" if isinstance(node, ast.Break) else "continue"
            raise self.refuse(node, f"{word} outside a loop is not part of the construction language")
        elif isinstance(node, ast.For):
            if node.orelse:
                raise self.refuse(node, "for ... else is not part of the construction language")
            self.target(node.target)
            self.check(node.target, position=node, in_function=in_function, in_loop=in_loop)
            self.check(node.iter, position=node, in_function=in_function, in_loop=in_loop)
            for statement in node.body:
                self.check(statement, position=node, in_function=in_function, in_loop=True)
            return
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                self.target(target)
                if isinstance(target, (ast.Tuple, ast.List)):
                    for element in ast.walk(target):
                        if isinstance(element, ast.Subscript):
                            self.target(element)
        elif isinstance(node, ast.AugAssign):
            self.target(node.target)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise self.refuse(node, "calling the result of an expression is not part of the construction language")
            if any(keyword.arg is None for keyword in node.keywords):
                raise self.refuse(node, "** unpacking is not part of the construction language")
        elif isinstance(node, ast.comprehension) and node.is_async:
            raise self.refuse(position, "async is not part of the construction language")
        for child in ast.iter_child_nodes(node):
            self.check(child, position=position, in_function=in_function, in_loop=in_loop)

    def function(self, node: ast.FunctionDef, *, in_function: bool) -> None:
        if in_function:
            raise self.refuse(node, "a function inside a function is not part of the construction language")
        arguments = node.args
        for present, words in ((node.decorator_list, "a decorator"), (node.returns, "an annotation"),
                               (arguments.posonlyargs, "a positional-only parameter"), (arguments.vararg, "*args"),
                               (arguments.kwonlyargs, "a keyword-only parameter"), (arguments.kwarg, "**kwargs"),
                               (getattr(node, "type_params", None), "a type parameter")):
            if present:
                raise self.refuse(node, f"{words} is not part of the construction language")
        self.name(node, node.name, stored=True)
        for parameter in arguments.args:
            if parameter.annotation is not None:
                raise self.refuse(parameter, "an annotation is not part of the construction language")
            self.name(parameter, parameter.arg, stored=True)


def parse(script: object) -> tuple[ast.Module, list[str]]:
    """The checked tree of a script, or the refusal of its first disallowed construct."""

    if not isinstance(script, str):
        raise ConstructionError("the script must be text")
    if len(script) > LIMITS["characters"]:
        raise ConstructionError("the script is longer than 20 000 characters")
    lines = script.splitlines()
    try:
        tree = ast.parse(script, mode="exec")
    except SyntaxError as exc:
        raise ConstructionError(exc.msg or "invalid syntax", line=exc.lineno, column=exc.offset,
                                source_line=_source_line(lines, exc.lineno)) from None
    except (ValueError, RecursionError, MemoryError):
        raise ConstructionError("the script cannot be read: it nests too deeply or is not valid text") from None
    pending: list[tuple[ast.AST, int, object]] = [(tree, 0, None)]
    while pending:  # iteratively, so that a deep tree is refused before anything recurses over it
        node, depth, position = pending.pop()
        position = node if hasattr(node, "lineno") else position
        if depth > MAX_NESTING:
            raise _error_at(lines, position, f"the script nests more than {MAX_NESTING} levels deep")
        pending.extend((child, depth + 1, position) for child in ast.iter_child_nodes(node))
    _Checker(lines).check(tree, position=None, in_function=False, in_loop=False)
    return tree, lines


# ---------------------------------------------------------------- the session: shapes, reached geometry, log
def _flatten(value: object) -> list:
    if isinstance(value, (list, tuple)):
        return [item for element in value for item in _flatten(element)]
    return [value]


class Session:
    """What one script run holds: the shapes it made, the existing geometry it reached, the print log."""

    def __init__(self, record: StateRecord) -> None:
        self.world = World(record)
        self.shapes: list[Shape] = []  # new shapes, in creation order
        self.rows: dict[str, RowShape] = {}  # existing geometry reached with get(), by element id
        self.log: list[str] = []
        self.line = 0
        self.order = 0
        self.seq = 0
        self.results = 0

    # ---- bookkeeping
    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def new(self, shape: Shape) -> Shape:
        self.results += 1
        if self.results > LIMITS["geometryResults"]:
            raise ShapeError("the script makes more than 300 geometry results")
        self.shapes.append(shape)
        return shape

    def live(self) -> list[Shape]:
        return [shape for shape in (*self.shapes, *self.rows.values()) if shape.deleted_line is None]

    def note_assignment(self, name: str, value: object) -> None:
        """A module-level assignment: the variable a new shape was last assigned to names it."""

        self.order += 1
        if isinstance(value, Shape):
            if value.is_new:
                value.direct = (name, self.order, self.line)
            return
        if isinstance(value, (list, tuple)):
            for item in _flatten(value):
                if isinstance(item, Shape) and item.is_new:
                    item.listed = (name, self.order, self.line)

    def alive(self, value: object, what: str) -> Shape:
        if not isinstance(value, Shape):
            raise ShapeError(f"{what} needs a shape, not {describe(value)}")
        if value.deleted_line is not None:
            raise ShapeError(f"{value.label()} was deleted on line {value.deleted_line}")
        return value

    def changed(self, shape: Shape) -> Shape:
        shape.touched(self.line)
        return shape

    def world_label(self, element_id: str) -> str:
        component = self.world.component_of(element_id)
        return component if len(self.world.elements_of.get(component, [])) == 1 else element_id

    # ---- relations between shapes
    @staticmethod
    def _names(entry: object, target: Shape) -> bool:
        return entry is target or (isinstance(target, RowShape) and target.existing and entry == target.element_id)

    def hosts_of(self, target: Shape) -> list[str]:
        """What ``target`` currently cuts."""

        found = [shape.label() for shape in self.live() if any(self._names(entry, target) for entry in shape.voids)]
        if isinstance(target, RowShape) and target.existing:
            for host in self.world.void_hosts.get(target.element_id, ()):  # type: ignore[arg-type]
                if host not in self.rows:
                    found.append(self.world_label(host))
        return found

    def standing_on(self, target: Shape) -> list[str]:
        """What currently stands on ``target``'s top."""

        found = [shape.label() for shape in self.live()
                 if shape is not target and any(a.kind == "top" and a.target is target for a in shape.anchors())]  # type: ignore[attr-defined]
        if isinstance(target, RowShape) and target.existing:
            for user in self.world.top_users.get(target.element_id, ()):  # type: ignore[arg-type]
                row = self.rows.get(user)
                if row is not None and (row.deleted_line is not None or row.base_anchor is not None):
                    continue
                found.append(self.world_label(user))
        return found

    def _record_dependents(self, shape: RowShape) -> list[str]:
        ids = {shape.element_id} | ({shape.component_id} if shape.owns_component else set())
        refs = {f"entity:{identifier}" for identifier in ids}
        handled = set(self.world.void_hosts.get(shape.element_id, ())) | set(self.world.top_users.get(shape.element_id, ()))  # type: ignore[arg-type]
        found = {edge.downstream_ref.removeprefix("entity:") for edge in self.world.record.dependency_edges()
                 if edge.upstream_ref in refs and edge.downstream_ref.startswith("entity:")}
        found -= ids | handled
        found = {item for item in found if not (item in self.rows and self.rows[item].deleted_line is not None)}
        found |= {relation.relation_id for relation in self.world.record.relations if {relation.subject, relation.object} & ids}
        found |= {entity.entity_id for entity in self.world.record.entities
                  if entity.parent_id in ids and entity.entity_id not in ids}
        return sorted(found)

    def anchor(self, value: object, what: str, *, absolute_ok: bool = True) -> Anchor:
        default = self.world.default_level
        if isinstance(value, Anchor):
            anchor = value
        elif isinstance(value, ParamRef):
            if default is None:
                raise ShapeError(f"{value!r} as a base needs a project level; the project has none")
            if default[1] != 0:
                raise ShapeError(f"{value!r} alone is measured from the project's zero; write "
                                 f"level('{default[0]}') + {value!r}")
            anchor = Anchor("level", default[0], 0.0, value.key)
        elif is_number(value):
            height = number(value, what)
            anchor = Anchor("level", default[0], height - default[1]) if default else Anchor("absolute", None, height)
        else:
            raise ShapeError(f"{what} takes a number, level(id) or top(obj), optionally plus a number, "
                             f"not {describe(value)}")
        if anchor.kind == "absolute" and not absolute_ok:
            raise ShapeError(f"{what} needs a project level; the project has none")
        if anchor.kind == "top":
            self.alive(anchor.target, what)
            if self.hosts_of(anchor.target):
                raise ShapeError(f"{anchor.target.label()} cuts another shape; nothing can stand on a cutter's top")
        return anchor

    def _stands_on_itself(self, shape: Shape, anchor: Anchor) -> bool:
        seen: set[int] = set()
        pending = [anchor]
        while pending:
            current = pending.pop()
            if current.kind != "top":
                continue
            if current.target is shape:
                return True
            if id(current.target) in seen:
                continue
            seen.add(id(current.target))
            pending.extend(current.target.anchors())
        return False

    # ---- profiles and planes
    def verb_rect(self, a: dict, given: frozenset) -> Profile:
        return rect(a["x"], a["z"], a["width"], a["depth"])

    def verb_polygon(self, a: dict, given: frozenset) -> Profile:
        return polygon(a["points"])

    def verb_circle(self, a: dict, given: frozenset) -> Profile:
        return circle(a["x"], a["z"], a["radius"], a["segments"])

    def verb_offset(self, a: dict, given: frozenset) -> Profile:
        return offset(a["profile"], a["distance"])

    def verb_plane(self, a: dict, given: frozenset) -> Plane:
        return plane(a["origin"], a["x_axis"], a["y_axis"])

    def verb_front(self, a: dict, given: frozenset) -> Plane:
        return front(a["z"])

    def verb_side(self, a: dict, given: frozenset) -> Plane:
        return side(a["x"])

    # ---- geometry
    def _height(self, value: object, what: str) -> float | ParamRef:
        if isinstance(value, ParamRef):
            return value
        height = number(value, what)
        if height == 0:
            raise ShapeError("a height of zero makes no solid; use face() for a flat face")
        return height

    @staticmethod
    def _plane(value: object, what: str) -> Plane | None:
        if value is None or isinstance(value, Plane):
            return value
        raise ShapeError(f"{what} plane takes plane(), front() or side(), not {describe(value)}")

    def verb_extrude(self, a: dict, given: frozenset) -> Shape:
        profile = as_profile(a["profile"], "extrude()")
        height = self._height(a["height"], "extrude() height")
        drawing = self._plane(a["plane"], "extrude()")
        if drawing is not None:
            if "at" in given:
                raise ShapeError("at is not given with plane: the plane's origin places the profile")
            return self.new(Drawn(self._next(), self.line, "extrude", profile=profile, height=height, plane=drawing))
        anchor = self.anchor(a["at"], "extrude() at")
        return self.new(Drawn(self._next(), self.line, "extrude", profile=profile, height=height, anchor=anchor))

    def verb_face(self, a: dict, given: frozenset) -> Shape:
        profile = as_profile(a["profile"], "face()")
        drawing = self._plane(a["plane"], "face()")
        if drawing is not None:
            if "at" in given:
                raise ShapeError("at is not given with plane: the plane's origin places the profile")
            return self.new(Drawn(self._next(), self.line, "face", profile=profile, plane=drawing))
        return self.new(Drawn(self._next(), self.line, "face", profile=profile, anchor=self.anchor(a["at"], "face() at")))

    def verb_path(self, a: dict, given: frozenset) -> Shape:
        points = a["points"]
        if not isinstance(points, (list, tuple)):
            raise ShapeError(f"path() takes a list of points (x, y, z), not {describe(points)}")
        checked = [point3(point, "path()") for point in points]
        if not 2 <= len(checked) <= MAX_PATH_POINTS:
            raise ShapeError(f"path() takes 2 to {MAX_PATH_POINTS} points")
        for p, q in zip(checked, checked[1:]):
            if math.dist(p, q) <= 1e-9:
                raise ShapeError(f"path() repeats the point ({p[0]!r}, {p[1]!r}, {p[2]!r})")
        path_frame(checked)
        if self.world.default_level is None:
            raise ShapeError("a path needs a project level; the project has none")
        return self.new(Drawn(self._next(), self.line, "path", points=tuple(checked)))

    def verb_section(self, a: dict, given: frozenset) -> Section:
        return Section(as_profile(a["profile"], "section()"), self.anchor(a["at"], "section() at", absolute_ok=False))

    def verb_loft(self, a: dict, given: frozenset) -> Shape:
        sections = a["sections"]
        if not isinstance(sections, (list, tuple)) or len(sections) < 2:
            raise ShapeError("loft() needs a list of two or more sections")
        for item in sections:
            if not isinstance(item, Section):
                raise ShapeError(f"loft() takes sections made with section(profile, at), not {describe(item)}")
        if len({len(item.profile.points) for item in sections}) != 1:
            raise ShapeError("every loft section needs the same number of points")
        first = sections[0].anchor
        for item in sections:
            if item.anchor.param is not None:
                raise ShapeError(f"a loft section cannot be bound to param('{item.anchor.param}')")
            if item.anchor.kind != first.kind or item.anchor.target is not first.target:
                raise ShapeError("all loft sections are measured from the same level or the same top")
        if not isinstance(a["cap"], bool):
            raise ShapeError(f"loft() cap must be True or False, not {describe(a['cap'])}")
        return self.new(Drawn(self._next(), self.line, "loft", sections=tuple(sections), cap=a["cap"]))

    # ---- transforms
    @staticmethod
    def _edit(shape: Shape, edit: Callable[[], None]) -> None:
        """Apply an edit; a stored row the script cannot read is refused by name, never raised raw."""

        try:
            edit()
        except ShapeError:
            raise
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
            if not isinstance(shape, RowShape):
                raise
            raise ShapeError(f"{shape.label()} is stored in a form this script cannot change") from exc

    def _transform(self, value: object, what: str, change: PlanMap) -> Shape:
        shape = self.alive(value, what)
        self._edit(shape, lambda: shape.apply(change))  # type: ignore[attr-defined]
        return self.changed(shape)

    def verb_move(self, a: dict, given: frozenset) -> Shape:
        change = PlanMap("move", dx=number(a["dx"], "move() dx"), dy=number(a["dy"], "move() dy"),
                         dz=number(a["dz"], "move() dz"))
        return self._transform(a["obj"], "move()", change)

    def verb_rotate(self, a: dict, given: frozenset) -> Shape:
        change = PlanMap.rotation(number(a["degrees"], "rotate() degrees"), point2(a["about"], "rotate() about"))
        return self._transform(a["obj"], "rotate()", change)

    def verb_scale(self, a: dict, given: frozenset) -> Shape:
        factor = number(a["factor"], "scale() factor")
        if factor <= 0:
            raise ShapeError("scale() needs a positive factor; mirror() reflects a shape")
        return self._transform(a["obj"], "scale()", PlanMap("scale", factor=factor, about=point2(a["about"], "scale() about")))

    def verb_mirror(self, a: dict, given: frozenset) -> Shape:
        if (a["x"] is None) == (a["z"] is None):
            raise ShapeError("mirror() takes exactly one of x or z")
        axis = "x" if a["x"] is not None else "z"
        return self._transform(a["obj"], "mirror()", PlanMap("mirror", axis=axis, at=number(a[axis], f"mirror() {axis}")))

    def _copy(self, shape: Shape, dx: float, dy: float, dz: float) -> Shape:
        if isinstance(shape, RowShape) and shape.producer == "wall":
            raise ShapeError(f"{shape.label()} can be moved, rotated, mirrored, given a height or a base, cut or "
                             "deleted; it cannot be copied")
        twin = self.new(shape.clone(self._next(), self.line))  # type: ignore[attr-defined]
        if dx or dy or dz:
            twin.apply(PlanMap("move", dx=dx, dy=dy, dz=dz))  # type: ignore[attr-defined]
        return twin

    def verb_copy(self, a: dict, given: frozenset) -> Shape:
        shape = self.alive(a["obj"], "copy()")
        return self._copy(shape, number(a["dx"], "copy() dx"), number(a["dy"], "copy() dy"), number(a["dz"], "copy() dz"))

    def verb_array(self, a: dict, given: frozenset) -> list:
        shape = self.alive(a["obj"], "array()")
        count = a["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ShapeError(f"array() count must be a whole number of at least 1, not {count!r}")
        step = (number(a["dx"], "array() dx"), number(a["dy"], "array() dy"), number(a["dz"], "array() dz"))
        return [shape] + [self._copy(shape, *(k * c for c in step)) for k in range(1, count)]

    # ---- solid edits
    def verb_pushpull(self, a: dict, given: frozenset) -> Shape:
        shape = self.alive(a["obj"], "pushpull()")
        distance = number(a["distance"], "pushpull() distance")
        if isinstance(shape, RowShape):
            self._edit(shape, lambda: shape.pushpull(distance))
        elif shape.kind == "face":  # type: ignore[attr-defined]
            if distance == 0:
                raise ShapeError("pushpull(0) makes no solid")
            shape.kind, shape.height = "extrude", distance  # type: ignore[attr-defined]
        elif shape.kind == "extrude":  # type: ignore[attr-defined]
            if isinstance(shape.height, ParamRef):  # type: ignore[attr-defined]
                raise ShapeError(f"the height of {shape.label()} is bound to {shape.height!r}; set_height() changes it")  # type: ignore[attr-defined]
            height = float(shape.height)  # type: ignore[attr-defined]
            length = abs(height) + distance
            if length <= 0:
                raise ShapeError(f"pushpull({distance!r}) would leave {shape.label()} with no height")
            shape.height = math.copysign(length, height)  # type: ignore[attr-defined]
        else:
            raise ShapeError(f"pushpull() lengthens a solid or pulls a face; {shape.label()} is a {shape.word}")
        return self.changed(shape)

    def verb_set_height(self, a: dict, given: frozenset) -> Shape:
        shape = self.alive(a["obj"], "set_height()")
        height = self._height(a["height"], "set_height() height")
        if isinstance(shape, RowShape):
            if not isinstance(height, ParamRef) and height < 0:
                raise ShapeError(f"{shape.label()} needs a positive height")
            self._edit(shape, lambda: shape.set_height(height, self.world))
        elif shape.kind == "extrude":  # type: ignore[attr-defined]
            if not isinstance(height, ParamRef) and height < 0 and shape.plane is None and self.standing_on(shape):  # type: ignore[attr-defined]
                raise ShapeError(f"{self.standing_on(shape)[0]} stands on the top of {shape.label()}; its height must stay upward")
            shape.height = height  # type: ignore[attr-defined]
        else:
            raise ShapeError(f"set_height() changes a solid's height; {shape.label()} is a {shape.word}")
        return self.changed(shape)

    def verb_set_base(self, a: dict, given: frozenset) -> Shape:
        shape = self.alive(a["obj"], "set_base()")
        anchor = self.anchor(a["at"], "set_base() at")
        if self._stands_on_itself(shape, anchor):
            raise ShapeError(f"{shape.label()} cannot stand on its own top")
        if isinstance(shape, RowShape):
            self._edit(shape, lambda: shape.set_base(anchor))
        elif shape.kind in ("extrude", "face") and shape.plane is None:  # type: ignore[attr-defined]
            shape.anchor = anchor  # type: ignore[attr-defined]
        elif shape.kind == "loft":  # type: ignore[attr-defined]
            if anchor.kind == "absolute":
                raise ShapeError("a loft solid needs a project level; the project has none")
            if anchor.param is not None:
                raise ShapeError(f"a loft solid's base cannot be bound to param('{anchor.param}')")
            low = min(section.anchor.offset for section in shape.sections)  # type: ignore[attr-defined]
            shape.sections = tuple(Section(section.profile, Anchor(anchor.kind, anchor.target,  # type: ignore[attr-defined]
                                                                     anchor.offset + section.anchor.offset - low))
                                   for section in shape.sections)  # type: ignore[attr-defined]
        elif shape.kind == "path":  # type: ignore[attr-defined]
            raise ShapeError(f"{shape.label()} is placed by its points; move() it instead")
        else:
            raise ShapeError(f"{shape.label()} is drawn on a plane, which places it; move() it instead")
        return self.changed(shape)

    # ---- cuts
    def verb_cut(self, a: dict, given: frozenset) -> Shape:
        host = self.alive(a["host"], "cut()")
        if not host.is_solid:  # type: ignore[attr-defined]
            raise ShapeError(f"cut() needs a solid to cut; {host.label()} is a {host.word}")
        cutters = _flatten(a["cutters"])
        if not cutters:
            raise ShapeError("cut() needs at least one cutter")
        for value in cutters:
            cutter = self.alive(value, "cut()")
            if cutter is host:
                raise ShapeError("a shape cannot cut itself")
            solid = cutter.can_cut if isinstance(cutter, RowShape) else cutter.is_solid  # type: ignore[attr-defined]
            if not solid:
                raise ShapeError(f"{cutter.label()} is a {cutter.word}; only an extruded or lofted solid can cut")
            if cutter.voids:
                raise ShapeError(f"{cutter.label()} has cutters of its own and cannot cut another shape")
            hosting = self.hosts_of(host)
            if hosting:
                raise ShapeError(f"{host.label()} is itself a cutter of {', '.join(hosting)} and cannot be cut")
            standing = self.standing_on(cutter)
            if standing:
                raise ShapeError(f"{standing[0]} stands on the top of {cutter.label()}; a cutter carries nothing")
            if not any(self._names(entry, cutter) for entry in host.voids):
                host.voids.append(cutter)
            if host not in cutter.cut_into:
                cutter.cut_into.append(host)
        return self.changed(host)

    def verb_uncut(self, a: dict, given: frozenset) -> Shape:
        host = self.alive(a["host"], "uncut()")
        cutters = _flatten(a["cutters"])
        if not cutters:
            raise ShapeError("uncut() needs at least one cutter")
        for value in cutters:
            cutter = self.alive(value, "uncut()")
            kept = [entry for entry in host.voids if not self._names(entry, cutter)]
            if len(kept) == len(host.voids):
                raise ShapeError(f"{cutter.label()} does not cut {host.label()}")
            host.voids = kept
        return self.changed(host)

    # ---- anchors and bindings
    def verb_level(self, a: dict, given: frozenset) -> Anchor:
        identifier = a["id"]
        if not isinstance(identifier, str) or identifier not in self.world.levels:
            raise ShapeError(f"the project has no level {identifier}")
        return Anchor("level", identifier, 0.0)

    def verb_top(self, a: dict, given: frozenset) -> Anchor:
        shape = self.alive(a["obj"], "top()")
        carries = shape.can_carry(self.world) if isinstance(shape, RowShape) else shape.upward  # type: ignore[attr-defined]
        if not carries:
            raise ShapeError(f"top() needs a solid extruded upward from a plan profile; {shape.label()} is not one")
        if self.hosts_of(shape):
            raise ShapeError(f"{shape.label()} cuts another shape; nothing can stand on a cutter's top")
        return Anchor("top", shape, 0.0)

    def verb_param(self, a: dict, given: frozenset) -> ParamRef:
        key = a["key"]
        if not isinstance(key, str) or key not in self.world.parameter_keys:
            raise ShapeError(f"the project has no parameter {key}")
        return ParamRef(key)

    def verb_bounds(self, a: dict, given: frozenset) -> tuple:
        shape = self.alive(a["obj"], "bounds()")
        low, high = shape.bounds(self.world)  # type: ignore[attr-defined]
        return tuple(low), tuple(high)

    # ---- identity
    def verb_name(self, a: dict, given: frozenset) -> Shape:
        shape = self.alive(a["obj"], "name()")
        identifier = a["id"]
        if not shape.is_new:
            raise ShapeError(f"{shape.label()} already has its id")
        if not isinstance(identifier, str):
            raise ShapeError(f"name() takes the id as text, not {describe(identifier)}")
        try:
            require_identifier(identifier, "id")
        except ValueError:
            raise ShapeError(f"{identifier!r} is not a usable id: letters, digits, '.', '-' and '_', "
                             "starting with a letter or a digit") from None
        if identifier.endswith("-body"):
            raise ShapeError(f"{identifier} ends with -body, which is kept for a shape's geometry; choose another id")
        if len(identifier) > MAX_ID:
            raise ShapeError(f"an id has at most {MAX_ID} characters")
        shape.explicit = (identifier, self.line)
        return shape

    def verb_get(self, a: dict, given: frozenset) -> Shape:
        identifier = a["id"]
        if not isinstance(identifier, str):
            raise ShapeError(f"get() takes an id as text, not {describe(identifier)}")
        element_id, component_id, geometry_id = self.world.editable(identifier)
        return self._row(element_id, component_id, geometry_id)

    def _row(self, element_id: str, component_id: str, geometry_id: str) -> RowShape:
        found = self.rows.get(element_id)
        if found is not None:
            if found.deleted_line is not None:
                raise ShapeError(f"{found.label()} was deleted on line {found.deleted_line}")
            return found
        entity = self.world.entities[element_id]
        try:
            row = RowShape(self._next(), self.line, element_id=element_id, component_id=component_id,
                           geometry_id=geometry_id, producer=str(entity.fields["producer"]), parent_id=entity.parent_id,
                           authored=dict(entity.fields), resolved=self.world.resolved(element_id), existing=True,
                           owns_component=self.world.elements_of.get(component_id) == [element_id],
                           extra={key: entity.fields[key] for key in ("type_ref",) if key in entity.fields})
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ShapeError):
                raise
            raise ShapeError(f"{geometry_id} is stored in a form a script cannot read") from None
        self.rows[element_id] = row
        loaded = []
        for void in row.voids:
            try:
                loaded.append(self._row(*self.world.editable(void)))
            except ShapeError:
                loaded.append(void)
        row.voids = loaded
        return row

    def verb_delete(self, a: dict, given: frozenset) -> None:
        shape = self.alive(a["obj"], "delete()")
        hosting = self.hosts_of(shape)
        if hosting:
            raise ShapeError(f"{shape.label()} still cuts {', '.join(hosting)}; uncut it first")
        standing = self.standing_on(shape)
        if standing:
            raise ShapeError(f"{standing[0]} stands on the top of {shape.label()}; delete or move it first")
        if isinstance(shape, RowShape) and shape.existing:
            dependents = self._record_dependents(shape)
            if dependents:
                raise ShapeError(f"{', '.join(dependents)} still depend on {shape.label()}; change them first")
        shape.deleted_line = self.line
        return None


# ---------------------------------------------------------------- the interpreter
IMPLEMENTED_VERBS: tuple[str, ...] = tuple(sorted(name.removeprefix("verb_") for name in vars(Session)
                                                  if name.startswith("verb_")))
if set(IMPLEMENTED_VERBS) != _VERB_NAMES:  # the vocabulary and the implementation are one table
    raise RuntimeError(f"construction verbs drifted: {sorted(set(IMPLEMENTED_VERBS) ^ _VERB_NAMES)}")


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    def __init__(self, value: object) -> None:
        self.value = value


class _Function:
    """A ``def`` of the script: parameters with their defaults (evaluated at definition) and a body."""

    def __init__(self, name: str, parameters: list[tuple[str, Any]], body: list[ast.stmt]) -> None:
        self.name = name
        self.parameters = parameters
        self.body = body

    def __repr__(self) -> str:
        return f"<function {self.name}>"


class _Callable:
    """A verb or builtin, as a value a name can hold."""

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.kind = kind

    def __repr__(self) -> str:
        return f"<{self.kind} {self.name}>"


class _Frame:
    """Names of one scope; a function reads the module scope and keeps its own."""

    __slots__ = ("names", "parent", "module_level")

    def __init__(self, names: dict, parent: _Frame | None, module_level: bool) -> None:
        self.names = names
        self.parent = parent
        self.module_level = module_level


_MISSING = object()
_OPERATORS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**"}
_CONSTANTS = {"pi": math.pi}


def _bind(spec: Verb, args: list, keywords: dict) -> tuple[dict, frozenset]:
    """A verb's arguments by its vocabulary signature; defaults filled, what was given reported."""

    positional = [param for param in spec.params if not param.rest]
    rest = next((param for param in spec.params if param.rest), None)
    if len(args) > len(positional) and rest is None:
        raise ShapeError(f"{spec.name}() takes at most {len(positional)} arguments: {spec.signature}")
    values: dict[str, Any] = {}
    given: set[str] = set()
    for param, value in zip(positional, args):
        values[param.name] = value
        given.add(param.name)
    if rest is not None:
        values[rest.name] = list(args[len(positional):])
    for key, value in keywords.items():
        if key not in {param.name for param in positional}:
            raise ShapeError(f"{spec.name}() has no parameter {key}: {spec.signature}")
        if key in values:
            raise ShapeError(f"{spec.name}() is given {key} twice: {spec.signature}")
        values[key] = value
        given.add(key)
    for param in positional:
        if param.name not in values:
            if param.default is REQUIRED:
                raise ShapeError(f"{spec.name}() needs {param.name}: {spec.signature}")
            values[param.name] = param.default
    return values, frozenset(given)


def _truthy(value: object) -> bool:
    return bool(value)


class Interpreter:
    """Walks a checked tree, counting steps, loop iterations and call depth."""

    def __init__(self, lines: list[str], session: Session) -> None:
        self.lines = lines
        self.session = session
        self.steps = 0
        self.depth = 0
        self.module = _Frame({}, None, True)

    def error(self, node: object, message: str) -> ConstructionError:
        return _error_at(self.lines, node, message)

    def step(self, node: ast.AST) -> None:
        self.steps += 1
        if self.steps > LIMITS["steps"]:
            raise self.error(node, "the script takes more than 20 000 evaluation steps")

    def run(self, tree: ast.Module) -> None:
        self.block(tree.body, self.module)

    # ---- statements
    def block(self, statements: list[ast.stmt], frame: _Frame) -> None:
        for statement in statements:
            self.execute(statement, frame)

    def execute(self, node: ast.stmt, frame: _Frame) -> None:
        self.step(node)
        self.session.line = node.lineno
        if isinstance(node, ast.Expr):
            self.evaluate(node.value, frame)
        elif isinstance(node, ast.Assign):
            value = self.evaluate(node.value, frame)
            for target in node.targets:
                self.assign(target, value, frame, naming=True)
        elif isinstance(node, ast.AugAssign):
            current = self.lookup(node.target.id, frame, node.target)  # type: ignore[attr-defined]
            value = self.binary(node.op, current, self.evaluate(node.value, frame), node)
            self.assign(node.target, value, frame, naming=True)
        elif isinstance(node, ast.For):
            self.loop(node, frame)
        elif isinstance(node, ast.If):
            self.block(node.body if _truthy(self.evaluate(node.test, frame)) else node.orelse, frame)
        elif isinstance(node, ast.FunctionDef):
            defaults = [self.evaluate(default, frame) for default in node.args.defaults]
            names = [parameter.arg for parameter in node.args.args]
            padded = [_MISSING] * (len(names) - len(defaults)) + defaults
            frame.names[node.name] = _Function(node.name, list(zip(names, padded)), node.body)
        elif isinstance(node, ast.Return):
            raise _Return(None if node.value is None else self.evaluate(node.value, frame))
        elif isinstance(node, ast.Break):
            raise _Break()
        elif isinstance(node, ast.Continue):
            raise _Continue()

    def loop(self, node: ast.For, frame: _Frame) -> None:
        count = 0
        for item in self.iterate(self.evaluate(node.iter, frame), node.iter):
            count += 1
            if count > LIMITS["loopIterations"]:
                raise self.error(node, "a loop may run at most 1 000 times")
            self.assign(node.target, item, frame, naming=False)
            try:
                self.block(node.body, frame)
            except _Break:
                break
            except _Continue:
                continue

    def iterate(self, value: object, node: ast.AST):
        if isinstance(value, (list, tuple, range, str)):
            return iter(value)
        raise self.error(node, f"cannot loop over {describe(value)}")

    def assign(self, target: ast.AST, value: object, frame: _Frame, *, naming: bool) -> None:
        if isinstance(target, ast.Name):
            frame.names[target.id] = value
            if naming and frame.module_level:
                self.session.note_assignment(target.id, value)
            return
        elements = target.elts  # type: ignore[attr-defined]
        if not isinstance(value, (list, tuple, range)):
            raise self.error(target, f"cannot unpack {describe(value)} into {len(elements)} names")
        items = list(value)
        if len(items) != len(elements):
            raise self.error(target, f"cannot unpack {len(items)} values into {len(elements)} names")
        for element, item in zip(elements, items):
            self.assign(element, item, frame, naming=naming)

    # ---- expressions
    def evaluate(self, node: ast.AST, frame: _Frame) -> Any:
        self.step(node)
        if isinstance(node, ast.Constant):
            return self.checked(node.value, node) if is_number(node.value) else node.value
        if isinstance(node, ast.Name):
            return self.lookup(node.id, frame, node)
        if isinstance(node, ast.List):
            return self.sized([self.evaluate(element, frame) for element in node.elts], node)
        if isinstance(node, ast.Tuple):
            return tuple(self.evaluate(element, frame) for element in node.elts)
        if isinstance(node, ast.BinOp):
            return self.binary(node.op, self.evaluate(node.left, frame), self.evaluate(node.right, frame), node)
        if isinstance(node, ast.UnaryOp):
            return self.unary(node, self.evaluate(node.operand, frame))
        if isinstance(node, ast.BoolOp):
            result: Any = None
            for value_node in node.values:
                result = self.evaluate(value_node, frame)
                if isinstance(node.op, ast.And) and not _truthy(result):
                    return result
                if isinstance(node.op, ast.Or) and _truthy(result):
                    return result
            return result
        if isinstance(node, ast.Compare):
            left = self.evaluate(node.left, frame)
            for operator, comparator in zip(node.ops, node.comparators):
                right = self.evaluate(comparator, frame)
                if not self.compare(operator, left, right, node):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self.evaluate(node.body if _truthy(self.evaluate(node.test, frame)) else node.orelse, frame)
        if isinstance(node, ast.Call):
            return self.call(node, frame)
        if isinstance(node, ast.ListComp):
            return self.comprehension(node, frame)
        if isinstance(node, ast.Subscript):
            return self.subscript(node, frame)
        raise self.error(node, f"{type(node).__name__} is not part of the construction language")

    def lookup(self, name: str, frame: _Frame, node: ast.AST) -> Any:
        current: _Frame | None = frame
        while current is not None:
            if name in current.names:
                return current.names[name]
            current = current.parent
        if name in _VERB_NAMES:
            return _Callable(name, "verb")
        if name in BUILTINS or (name in MATH and name not in _CONSTANTS):
            return _Callable(name, "builtin")
        if name in _CONSTANTS:
            return _CONSTANTS[name]
        raise self.error(node, f"{name} is not defined")

    def sized(self, value: Any, node: ast.AST) -> Any:
        if len(value) > MAX_SEQUENCE:
            raise self.error(node, f"a list or tuple may hold at most {MAX_SEQUENCE:,} items".replace(",", " "))
        return value

    def checked(self, value: Any, node: ast.AST) -> Any:
        if isinstance(value, complex):
            raise self.error(node, "the result is not a real number")
        if isinstance(value, float) and not math.isfinite(value):
            raise self.error(node, "the result is not a finite number")
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 10 ** 18:
            raise self.error(node, "the result is too large")
        return value

    def binary(self, operator: ast.operator, left: Any, right: Any, node: ast.AST) -> Any:
        symbol = _OPERATORS[type(operator)]
        if isinstance(left, (Anchor, ParamRef)) or isinstance(right, (Anchor, ParamRef)):
            try:
                return combine(symbol, left, right)
            except ShapeError as exc:
                raise self.error(node, str(exc)) from None
        if is_number(left) and is_number(right):
            return self.arithmetic(symbol, left, right, node)
        if symbol == "+" and type(left) is type(right) and isinstance(left, (str, list, tuple)):
            return self.sized(left + right, node)
        if symbol == "*":
            sequence, count = (left, right) if isinstance(left, (list, tuple)) else (right, left)
            if isinstance(sequence, (list, tuple)) and isinstance(count, int) and not isinstance(count, bool):
                if len(sequence) * max(count, 0) > MAX_SEQUENCE:
                    raise self.error(node, f"a list or tuple may hold at most {MAX_SEQUENCE:,} items".replace(",", " "))
                return sequence * count
        if symbol == "%" and isinstance(left, str):
            raise self.error(node, "formatting text with % is not part of the construction language; join text with +")
        raise self.error(node, f"{symbol} does not apply to {describe(left)} and {describe(right)}")

    def arithmetic(self, symbol: str, a: float, b: float, node: ast.AST) -> Any:
        try:
            if symbol == "+":
                result = a + b
            elif symbol == "-":
                result = a - b
            elif symbol == "*":
                result = a * b
            elif symbol == "/":
                result = a / b
            elif symbol == "//":
                result = a // b
            elif symbol == "%":
                result = a % b
            else:
                if abs(b) > 100 and abs(a) > 1:
                    raise self.error(node, "the exponent is too large")
                result = a ** b
        except ZeroDivisionError:
            raise self.error(node, "division by zero") from None
        except OverflowError:
            raise self.error(node, "the result is too large") from None
        return self.checked(result, node)

    def unary(self, node: ast.UnaryOp, value: Any) -> Any:
        if isinstance(node.op, ast.Not):
            return not _truthy(value)
        if isinstance(value, ParamRef):
            raise self.error(node, str(binding_refusal(value)))
        if not is_number(value):
            symbol = "-" if isinstance(node.op, ast.USub) else "+"
            raise self.error(node, f"{symbol} does not apply to {describe(value)}")
        return self.checked(-value if isinstance(node.op, ast.USub) else +value, node)

    def compare(self, operator: ast.cmpop, left: Any, right: Any, node: ast.AST) -> bool:
        for value in (left, right):
            if isinstance(value, ParamRef):
                raise self.error(node, str(binding_refusal(value)))
            if isinstance(value, Anchor) and not isinstance(operator, (ast.Is, ast.IsNot)):
                raise self.error(node, "an anchor cannot be compared; bounds() gives numbers")
        if isinstance(operator, ast.Eq):
            return left == right
        if isinstance(operator, ast.NotEq):
            return left != right
        if isinstance(operator, ast.Is):
            return left is right
        if isinstance(operator, ast.IsNot):
            return left is not right
        if isinstance(operator, (ast.In, ast.NotIn)):
            if not isinstance(right, (list, tuple, range, str)) or (isinstance(right, str) and not isinstance(left, str)):
                raise self.error(node, f"cannot look for {describe(left)} in {describe(right)}")
            return (left in right) == isinstance(operator, ast.In)
        if not ((is_number(left) and is_number(right)) or (isinstance(left, str) and isinstance(right, str))):
            raise self.error(node, f"cannot compare {describe(left)} with {describe(right)}")
        if isinstance(operator, ast.Lt):
            return left < right
        if isinstance(operator, ast.LtE):
            return left <= right
        if isinstance(operator, ast.Gt):
            return left > right
        return left >= right

    def subscript(self, node: ast.Subscript, frame: _Frame) -> Any:
        value = self.evaluate(node.value, frame)
        if not isinstance(value, (list, tuple, str, range)):
            raise self.error(node, f"cannot take an item of {describe(value)}")
        if isinstance(node.slice, ast.Slice):
            parts = []
            for part in (node.slice.lower, node.slice.upper, node.slice.step):
                item = None if part is None else self.evaluate(part, frame)
                if item is not None and (isinstance(item, bool) or not isinstance(item, int)):
                    raise self.error(node, f"a slice takes whole numbers, not {describe(item)}")
                parts.append(item)
            if parts[2] == 0:
                raise self.error(node, "a slice step cannot be zero")
            return value[slice(*parts)]
        index = self.evaluate(node.slice, frame)
        if isinstance(index, bool) or not isinstance(index, int):
            raise self.error(node, f"an index must be a whole number, not {describe(index)}")
        try:
            return value[index]
        except IndexError:
            raise self.error(node, f"index {index} is out of range") from None

    def comprehension(self, node: ast.ListComp, frame: _Frame) -> list:
        scope = _Frame({}, frame, False)
        result: list = []

        def walk(index: int) -> None:
            if index == len(node.generators):
                result.append(self.evaluate(node.elt, scope))
                self.sized(result, node)
                return
            generator = node.generators[index]
            count = 0
            for item in self.iterate(self.evaluate(generator.iter, scope), generator.iter):
                count += 1
                if count > LIMITS["loopIterations"]:
                    raise self.error(node, "a loop may run at most 1 000 times")
                self.assign(generator.target, item, scope, naming=False)
                if all(_truthy(self.evaluate(condition, scope)) for condition in generator.ifs):
                    walk(index + 1)

        walk(0)
        return result

    # ---- calls
    def call(self, node: ast.Call, frame: _Frame) -> Any:
        name = node.func.id  # type: ignore[attr-defined]
        callee = self.lookup(name, frame, node.func)
        args = [self.evaluate(argument, frame) for argument in node.args]
        keywords = {keyword.arg: self.evaluate(keyword.value, frame) for keyword in node.keywords}
        if isinstance(callee, _Function):
            return self.call_function(callee, args, keywords, node)
        if not isinstance(callee, _Callable):
            raise self.error(node, f"{name} is {describe(callee)}, which cannot be called")
        try:
            if callee.kind == "verb":
                values, given = _bind(verb(callee.name), args, keywords)
                return getattr(self.session, f"verb_{callee.name}")(values, given)
            return self.builtin(callee.name, args, keywords, node)
        except ShapeError as exc:
            raise self.error(node, str(exc)) from None

    def call_function(self, function: _Function, args: list, keywords: dict, node: ast.AST) -> Any:
        names = [name for name, _ in function.parameters]
        if len(args) > len(names):
            raise self.error(node, f"{function.name}() takes {len(names)} arguments, not {len(args)}")
        bound = dict(zip(names, args))
        for key, value in keywords.items():
            if key not in names:
                raise self.error(node, f"{function.name}() has no parameter {key}")
            if key in bound:
                raise self.error(node, f"{function.name}() is given {key} twice")
            bound[key] = value
        for name, default in function.parameters:
            if name not in bound:
                if default is _MISSING:
                    raise self.error(node, f"{function.name}() needs {name}")
                bound[name] = default
        if self.depth >= LIMITS["callDepth"]:
            raise self.error(node, "calls nest deeper than 16")
        self.depth += 1
        line = self.session.line
        try:
            self.block(function.body, _Frame(bound, self.module, False))
        except _Return as result:
            return result.value
        finally:
            self.depth -= 1
            self.session.line = line
        return None

    def builtin(self, name: str, args: list, keywords: dict, node: ast.AST) -> Any:
        allowed = {"enumerate": {"start"}, "round": {"ndigits"}, "sum": {"start"}}.get(name, set())
        unknown = sorted(set(keywords) - allowed)
        if unknown:
            raise ShapeError(f"{name}() takes no keyword {unknown[0]}")
        handler: Callable[..., Any] = getattr(self, f"_builtin_{name}")
        try:
            result = handler(*args, **keywords)
        except ShapeError:
            raise
        except TypeError:
            given = ", ".join(describe(a) for a in args)
            raise ShapeError(f"{name}() cannot take {given}" if args else f"{name}() needs an argument") from None
        except (ValueError, OverflowError, ZeroDivisionError):
            raise ShapeError(f"{name}() of {', '.join(describe(a) for a in args)} is not defined") from None
        return self.checked(result, node)

    # ---- builtins: numbers only for arithmetic, sequences bounded
    @staticmethod
    def _numbers(values: object, name: str) -> list:
        if not isinstance(values, (list, tuple, range)):
            raise ShapeError(f"{name}() takes numbers or a list of numbers, not {describe(values)}")
        for value in values:
            if not is_number(value):
                raise ShapeError(f"{name}() takes numbers, not {describe(value)}")
        return list(values)

    def _sequence(self, value: object, name: str) -> list:
        if not isinstance(value, (list, tuple, range, str)):
            raise ShapeError(f"{name}() takes a list, a tuple, a range or text, not {describe(value)}")
        if len(value) > MAX_SEQUENCE:
            raise ShapeError(f"a list or tuple may hold at most {MAX_SEQUENCE} items")
        return list(value)

    def _builtin_range(self, *args: object) -> range:
        if not 1 <= len(args) <= 3 or any(isinstance(a, bool) or not isinstance(a, int) for a in args):
            raise ShapeError("range() takes one to three whole numbers")
        if len(args) == 3 and args[2] == 0:
            raise ShapeError("range() step cannot be zero")
        return range(*args)  # type: ignore[arg-type]

    def _builtin_len(self, value: object) -> int:
        return len(self._sequence(value, "len"))

    def _extreme(self, name: str, args: tuple) -> Any:
        values = self._numbers(args[0] if len(args) == 1 else args, name)
        if not values:
            raise ShapeError(f"{name}() of nothing is not defined")
        return min(values) if name == "min" else max(values)

    def _builtin_min(self, *args: object) -> Any:
        return self._extreme("min", args)

    def _builtin_max(self, *args: object) -> Any:
        return self._extreme("max", args)

    def _builtin_abs(self, value: object) -> Any:
        return abs(self._numbers((value,), "abs")[0])

    def _builtin_round(self, value: object, ndigits: object = None) -> Any:
        if ndigits is not None and (isinstance(ndigits, bool) or not isinstance(ndigits, int)):
            raise ShapeError("round() digits must be a whole number")
        return round(self._numbers((value,), "round")[0], ndigits)  # type: ignore[call-overload]

    def _builtin_sum(self, values: object, start: object = 0) -> Any:
        return sum(self._numbers(values, "sum"), self._numbers((start,), "sum")[0])

    def _builtin_enumerate(self, values: object, start: object = 0) -> list:
        if isinstance(start, bool) or not isinstance(start, int):
            raise ShapeError("enumerate() start must be a whole number")
        return [(start + index, item) for index, item in enumerate(self._sequence(values, "enumerate"))]

    def _builtin_zip(self, *values: object) -> list:
        return list(zip(*(self._sequence(value, "zip") for value in values)))

    def _builtin_list(self, *values: object) -> list:
        return [] if not values else self._sequence(values[0], "list")

    def _builtin_tuple(self, *values: object) -> tuple:
        return () if not values else tuple(self._sequence(values[0], "tuple"))

    def _builtin_float(self, value: object) -> float:
        return number(value, "float()")

    def _builtin_int(self, value: object) -> int:
        return int(number(value, "int()"))

    def _builtin_print(self, *values: object) -> None:
        if len(self.session.log) >= MAX_LOG_LINES:
            raise ShapeError(f"print() may write at most {MAX_LOG_LINES:,} lines".replace(",", " "))
        text = " ".join(value if isinstance(value, str) else str(value) for value in values)
        self.session.log.append(text if len(text) <= MAX_LOG_LINE else text[:MAX_LOG_LINE] + "...")
        return None

    def _builtin_sqrt(self, value: object) -> float:
        x = number(value, "sqrt()")
        if x < 0:
            raise ShapeError("sqrt() of a negative number is not defined")
        return math.sqrt(x)

    def _builtin_sin(self, value: object) -> float:
        return math.sin(number(value, "sin()"))

    def _builtin_cos(self, value: object) -> float:
        return math.cos(number(value, "cos()"))

    def _builtin_tan(self, value: object) -> float:
        return math.tan(number(value, "tan()"))

    def _builtin_atan2(self, y: object, x: object) -> float:
        return math.atan2(number(y, "atan2() y"), number(x, "atan2() x"))

    def _builtin_radians(self, value: object) -> float:
        return math.radians(number(value, "radians()"))

    def _builtin_degrees(self, value: object) -> float:
        return math.degrees(number(value, "degrees()"))

    def _builtin_floor(self, value: object) -> int:
        return math.floor(number(value, "floor()"))

    def _builtin_ceil(self, value: object) -> int:
        return math.ceil(number(value, "ceil()"))


def run_script(script: object, record: StateRecord) -> Session:
    """Parse, check and interpret a script against a record: the session it leaves, or a refusal."""

    tree, lines = parse(script)
    session = Session(record)
    try:
        Interpreter(lines, session).run(tree)
    except RecursionError:
        raise ConstructionError("the script nests calls and expressions too deeply") from None
    return session
