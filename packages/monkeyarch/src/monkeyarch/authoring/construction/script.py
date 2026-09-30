"""The construction script's parser and interpreter (#419, L1).

A script is parsed with ``ast`` and walked by an allow-list interpreter: Python
never compiles or runs it. The whole tree is checked before anything runs, so a
refused construct anywhere in the script is refused at its line even if it would
never be reached.

Work is bounded, not only counted: every node evaluated is a step, every walk
over a sequence costs steps by its length, a wall-clock deadline is checked with
each step, every list or tuple the script creates is measured with its nested
elements (per value and in total), ``+=`` extends a list in place as Python does
and counts only what it adds (to that list and to every list or tuple holding
it), a comparison costs what it may walk, profile checks draw on a budget of
edge pairs, and the shapes hold 100 000 points at most. No single step can do
unbounded work, and no message repeats a script value at length.

Verbs are bound from ``vocabulary.VERBS`` - the table an agent reads - and
implemented by ``Session``, which keeps the script's shapes (each with the
chain of statements that made it - the module-level statement, the statements
it called, down to the one that made the shape - which its id is hashed from if
it has no name), the existing geometry it reached, what cuts what and what
stands on what as far as the script knows it (the record's own relations are
checked when lowering), and the ``print`` log. The session is also the script's
model for every top a shape reads: a row the script changed, or a new shape
whose id (as the script now names it) is that of existing geometry, is the
current version of that geometry. What a verb refuses it refuses with one sentence; the
interpreter adds the line, the column and the source line
(``ConstructionError``), and the lines a function was called from.
"""
from __future__ import annotations

import ast
import io
import math
import time
import tokenize
from itertools import groupby
from typing import Any, Callable

from archflow.project.refs import require_identifier
from archflow.state.state_record import StateRecord
from monkeyarch.authoring.construction.identity import ELEMENT_SUFFIX, Naming, identify
from monkeyarch.authoring.construction.shapes import (
    MAX_PATH_POINTS,
    MIN_LENGTH,
    NO_LEVEL,
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
    binding_refusal,
    circle,
    closed_profile,
    combine,
    coordinate,
    describe,
    front,
    is_number,
    length,
    number,
    offset,
    path_frame,
    plane,
    point2,
    point3,
    profile_points,
    rect,
    render,
    short,
    shown,
    side,
)
from monkeyarch.authoring.construction.vocabulary import BUILTINS, LIMITS, MATH, REQUIRED, VERBS, Verb, verb

MAX_NESTING = LIMITS["nesting"]
MAX_ELEMENTS = LIMITS["listElements"]
MAX_CREATED = LIMITS["createdElements"]
MAX_TEXT = LIMITS["textCharacters"]
MAX_LOG_LINES = LIMITS["printLines"]
MAX_LOG_LINE = 2_000
MAX_ID = LIMITS["idLength"]
MAX_PAIRS = LIMITS["profileEdgePairs"]
MAX_CUTTERS = LIMITS["cuttersPerShape"]
MAX_SECTIONS = LIMITS["loftSections"]
MAX_ROUND_DIGITS = LIMITS["roundDigits"]
MAX_POINTS = LIMITS["totalPoints"]
TIME_OUT = "the script ran longer than 5 s; split it"
_WALK = 100  # elements walked per extra step


class ConstructionError(ValueError):
    """A refused construction script: where (line, 1-based column, the source line) and one sentence why."""

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None,
                 source_line: str | None = None) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.source_line = source_line
        self.calls: list[int] = []  # the lines of the calls it happened inside, innermost first
        where = "" if line is None else (f"line {line}, column {column}: " if column is not None else f"line {line}: ")
        super().__init__(where + message)

    def to_dict(self) -> dict[str, Any]:
        return {"code": "CONSTRUCTION_INVALID", "line": self.line, "column": self.column,
                "sourceLine": self.source_line, "message": self.message}

    def with_calls(self) -> ConstructionError:
        """The same refusal, naming the calls it happened inside."""

        if not self.calls:
            return self
        chain = ", from ".join(f"line {line}" + (f" ({count} times)" if count > 1 else "")
                               for line, count in ((line, len(list(group))) for line, group in groupby(self.calls)))
        return ConstructionError(f"{self.message} (called from {chain})", line=self.line, column=self.column,
                                 source_line=self.source_line)


def _source_line(lines: list[str], line: int | None) -> str | None:
    return short(lines[line - 1].strip(), 1_000) if line is not None and 1 <= line <= len(lines) else None


def _error_at(lines: list[str], node: object, message: str) -> ConstructionError:
    line = getattr(node, "lineno", None)
    column = getattr(node, "col_offset", None)
    return ConstructionError(message, line=line, column=None if column is None else column + 1,
                             source_line=_source_line(lines, line))


def _statement_error(lines: list[str], line: int | None, message: str) -> ConstructionError:
    """A refusal at a statement's line; its column is where that statement starts."""

    text = lines[line - 1] if line is not None and 1 <= line <= len(lines) else None
    column = None if text is None else len(text) - len(text.lstrip()) + 1
    return ConstructionError(message, line=line, column=column, source_line=_source_line(lines, line))


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


def _constant_kind(value: object) -> str:
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, complex):
        return "a complex number"
    return "..." if value is Ellipsis else "this value"


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
            raise self.refuse(position, f"{_constant_kind(node.value)} is not part of the construction language")
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
        raise ConstructionError(short(exc.msg or "invalid syntax", 120), line=exc.lineno, column=exc.offset,
                                source_line=_source_line(lines, exc.lineno)) from None
    except (RecursionError, MemoryError):
        # Where the platform's parser gives up first: past any depth the nesting limit allows,
        # so it is said the same way as the limit below, whichever of the two meets it.
        raise ConstructionError(f"the script nests more than {MAX_NESTING} levels deep") from None
    except ValueError:
        raise ConstructionError("the script cannot be read: it is not valid text") from None
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
_UNKEYED = frozenset({tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT,
                      tokenize.ENDMARKER})


def _statement_key(source: str, node: ast.stmt) -> str:
    """A statement's text as the ids of the shapes it makes are hashed from: its tokens one space apart, so
    whitespace, line breaks and comments do not matter. A for or if statement is keyed by its header only."""

    def text(part: ast.AST) -> str:
        try:
            found = ast.get_source_segment(source, part)
        except (IndexError, ValueError):  # a statement from other text than ``source``
            found = None
        return found or ast.unparse(part)

    if isinstance(node, ast.For):
        written = f"for {text(node.target)} in {text(node.iter)}"
    elif isinstance(node, ast.If):
        written = f"if {text(node.test)}"
    elif isinstance(node, ast.FunctionDef):
        written = f"def {node.name}"
    else:
        written = text(node)
    try:
        return " ".join(token.string for token in tokenize.generate_tokens(io.StringIO(written).readline)
                        if token.type not in _UNKEYED)
    except (tokenize.TokenError, SyntaxError):
        return " ".join(written.split())


def _weight(value: object) -> int:
    """What comparing ``value`` with == or in can cost, in elements: one per value, nested ones included, and more
    for long text and for profiles and sections, which compare point by point. An anchor or a binding inside is
    refused as it is on its own."""

    total, pending = 0, [value]
    while pending:
        item = pending.pop()
        total += 1
        if isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, str):
            total += len(item) // _WALK
        elif isinstance(item, (Profile, Section)):
            total += 3 * len((item if isinstance(item, Profile) else item.profile).points)
        elif isinstance(item, Plane):
            total += 12
        elif isinstance(item, ParamRef):
            raise binding_refusal(item)
        elif isinstance(item, Anchor):
            raise ShapeError("an anchor cannot be compared; bounds() gives numbers")
    return total


def _flatten(value: object) -> list:
    if isinstance(value, (list, tuple)):
        return [item for element in value for item in _flatten(element)]
    return [value]


def _range_length(start: int, stop: int, step: int) -> int:
    if step > 0:
        return max(0, (stop - start + step - 1) // step)
    return max(0, (start - stop - step - 1) // (-step))


class Session:
    """What one script run holds: its shapes, the existing geometry it reached, relations, work done, the log."""

    def __init__(self, record: StateRecord, source: str = "") -> None:
        self.world = World(record)
        self.world.current_shape, self.world.spend = self.current_shape, self.tick
        self.source = source
        self.statement: ast.stmt | None = None  # the statement running now: what makes a new shape
        self.callers: list[ast.stmt | None] = []  # the statements whose calls the running statement is inside
        self._keys: dict[ast.stmt, str] = {}
        self.occurrences: dict[str, int] = {}  # statement chain key -> shapes it has made so far
        self.naming_epoch = 0  # bumped whenever what names a shape changes; the naming is worked out per epoch
        self._naming: tuple[int, Naming] | None = None
        self.points = 0  # points of the script's shapes still alive
        self.shapes: list[Shape] = []  # new shapes, in creation order
        self.rows: dict[str, RowShape] = {}  # existing geometry loaded by element id, reached or as context
        self.explicit: set[str] = set()  # element ids the script reached with get()
        self.log: list[str] = []
        self.line = 0
        self.order = 0
        self.seq = 0
        self.results = 0
        self.steps = 0
        self.deadline = time.monotonic() + LIMITS["seconds"]
        self.created_total = 0
        self.sizes: dict[int, tuple[object, int, int, bool]] = {}  # id -> (value, elements, nesting, holds shapes)
        # Which lists and tuples hold a list (or a tuple that holds one), and how many times: what += must re-measure.
        self.kids: dict[int, dict[int, int]] = {}  # a list's or tuple's id -> {id of what it holds: times}
        self.holders: dict[int, dict[int, int]] = {}  # the reverse: id -> {id of a list or tuple holding it: times}
        self.pairs = 0
        self.profiles: dict[tuple, Profile] = {}
        self.hosts_by_cutter: dict[Shape, dict[Shape, None]] = {}
        self.carried: dict[Shape, dict[Shape, None]] = {}  # a shape -> the shapes standing on its top
        self.stand_targets: dict[Shape, tuple[Shape, ...]] = {}

    # ---- work
    def tick(self, amount: int = 1) -> None:
        self.steps += amount
        if self.steps > LIMITS["steps"]:
            raise ShapeError("the script takes more than 20 000 evaluation steps")
        if time.monotonic() >= self.deadline:
            raise ShapeError(TIME_OUT)

    def charge(self, elements: int) -> None:
        """Walking a sequence costs steps by its length."""

        self.tick(elements // _WALK)

    def measure(self, value: object) -> tuple[int, int, bool]:
        """(elements with nested ones, nesting depth, whether it holds shapes) of a list or tuple; recorded the first
        time it is measured, with the lists it holds (kept current by ``grow``)."""

        if not isinstance(value, (list, tuple)):
            return 0, 0, isinstance(value, Shape)
        known = self.sizes.get(id(value))
        if known is not None and known[0] is value:
            return known[1], known[2], known[3]
        size, depth, shapes = len(value), 1, False
        kids: dict[int, int] = {}
        for item in value:
            if isinstance(item, Shape):
                shapes = True
            elif isinstance(item, (list, tuple)):
                inner, nested, held = self.measure(item)
                size, depth, shapes = size + inner, max(depth, nested + 1), shapes or held
                if self.can_grow(item):
                    kids[id(item)] = kids.get(id(item), 0) + 1
        self.remember(value, size, depth, shapes, kids)
        return size, depth, shapes

    def can_grow(self, value: object) -> bool:
        """Whether ``value``'s size can still change: a list, or a tuple that holds one."""

        return isinstance(value, list) or (isinstance(value, tuple) and id(value) in self.kids)

    def kids_of(self, value: object) -> dict[int, int]:
        return self.kids.get(id(value), {})

    def admit(self, size: int, depth: int) -> None:
        """A new list or tuple of ``size`` elements (nested ones included) and ``depth`` levels, if within bounds."""

        if size > MAX_ELEMENTS:
            raise ShapeError("a list or tuple may hold at most 10 000 elements, nested ones included")
        if depth > MAX_NESTING:
            raise ShapeError(f"lists and tuples may nest at most {MAX_NESTING} levels")
        self.count_created(size)

    def count_created(self, elements: int) -> None:
        self.created_total += elements
        if self.created_total > MAX_CREATED:
            raise ShapeError("the script creates more than 200 000 list and tuple elements; "
                             "build a long list once with a comprehension, or grow it with +=")
        self.charge(elements)

    def remember(self, value: object, size: int, depth: int, shapes: bool, kids: dict[int, int] | None = None) -> object:
        """Record a new list or tuple; ``kids`` are the lists (and list-holding tuples) it holds, by id and times."""

        known = self.sizes.get(id(value))
        if known is not None and known[0] is value:
            return value  # the same tuple back (t + (), t * 1): already recorded
        self.sizes[id(value)] = (value, size, depth, shapes)
        if kids:
            self.kids[id(value)] = kids
            for kid, times in kids.items():
                holders = self.holders.setdefault(kid, {})
                holders[id(value)] = holders.get(id(value), 0) + times
        return value

    def grow(self, target: list, addition: list | tuple) -> None:
        """``target += addition`` on a list: extended in place, as Python does, counting only what it gains.

        Every list or tuple that holds ``target`` (directly or not) gains the same elements, as many times as it
        holds ``target``, so each of them is checked and re-measured too.
        """

        added, depth, shapes = self.measure(addition)
        own = self.measure(target)[1]
        new_kids = dict(self.kids_of(addition))
        order, times, below = self._lineage(target)
        if any(kid in times for kid in new_kids):
            raise ShapeError("+= cannot put a list inside itself")
        nesting = max(own, depth)
        for key in order:
            _, size, levels, _ = self.sizes[key]
            if size + times[key] * added > MAX_ELEMENTS:
                if key == id(target):
                    raise ShapeError("a list or tuple may hold at most 10 000 elements, nested ones included")
                raise ShapeError("a list or tuple that holds this list would then hold more than 10 000 elements, "
                                 "nested ones included")
            if max(levels, below[key] + nesting) > MAX_NESTING:
                raise ShapeError(f"lists and tuples may nest at most {MAX_NESTING} levels")
        self.count_created(added)
        target.extend(addition)
        for key in order:
            value, size, levels, held = self.sizes[key]
            self.sizes[key] = (value, size + times[key] * added, max(levels, below[key] + nesting), held or shapes)
        if new_kids:
            mine = self.kids.setdefault(id(target), {})
            for kid, count in new_kids.items():
                mine[kid] = mine.get(kid, 0) + count
                holders = self.holders.setdefault(kid, {})
                holders[id(target)] = holders.get(id(target), 0) + count

    def _lineage(self, target: list) -> tuple[list[int], dict[int, int], dict[int, int]]:
        """``target`` and every list or tuple holding it, directly or not, each before what holds it; with how many
        times each holds ``target`` and how many levels below it ``target`` is at most."""

        start = id(target)
        finished: list[int] = []
        seen = {start}
        stack = [(start, iter(self.holders.get(start, ())))]
        visited = 0
        while stack:
            key, above = stack[-1]
            holder = next(above, None)
            if holder is None:
                stack.pop()
                finished.append(key)
                continue
            visited += 1
            if holder not in seen:
                seen.add(holder)
                stack.append((holder, iter(self.holders.get(holder, ()))))
        self.tick(visited // 10)
        order = finished[::-1]  # topological: each after everything it holds on the way down to target
        times, below = {start: 1}, {start: 0}
        for key in order:
            for holder, count in self.holders.get(key, {}).items():
                times[holder] = times.get(holder, 0) + count * times[key]
                below[holder] = max(below.get(holder, 0), below[key] + 1)
        return order, times, below

    def created(self, value: object) -> object:
        if isinstance(value, (list, tuple)):
            size, depth, _ = self.measure(value)
            self.admit(size, depth)
        return value

    def spend_pairs(self, pairs: int) -> None:
        self.pairs += pairs
        if self.pairs > MAX_PAIRS:
            raise ShapeError("the script checks more than 1 000 000 pairs of profile edges; "
                             "reuse a profile instead of rebuilding it")
        self.charge(pairs // 10)

    # ---- bookkeeping
    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def new(self, shape: Shape) -> Shape:
        self.results += 1
        if self.results > LIMITS["geometryResults"]:
            raise ShapeError("the script makes more than 300 geometry results")
        self.points += shape.point_count  # type: ignore[attr-defined]
        if self.points > MAX_POINTS:
            raise ShapeError("the script's shapes would have more than 100 000 points in all; use fewer or simpler "
                             "profiles, sections and paths")
        key = self.statement_key()
        shape.made_by = (key, self.occurrences.get(key, 0))
        self.occurrences[key] = shape.made_by[1] + 1
        self.shapes.append(shape)
        self.renamed()
        self._reindex(shape)
        return shape

    def renamed(self) -> None:
        """What names a shape changed: the naming is worked out again, and so is every top, since a new shape or a
        new name may now stand for existing geometry."""

        self.naming_epoch += 1
        self.world.tops.clear()

    def statement_key(self) -> str:
        """The text a new shape's id is hashed from: the chain of statements running now - the module-level one,
        then each statement it called, down to the running one - each as ``_statement_key`` reads it."""

        return "\x00".join(self._key(node) for node in (*self.callers, self.statement) if node is not None)

    def _key(self, node: ast.stmt) -> str:
        key = self._keys.get(node)
        if key is None:
            key = self._keys[node] = _statement_key(self.source, node)
        return key

    # ---- the script's model: what it names, and the current version of what it changed or redefined
    def naming(self) -> Naming:
        """The ids the shapes have as the script now names them (lowering gives the final, checked ones)."""

        if self._naming is None or self._naming[0] != self.naming_epoch:
            self._naming = (self.naming_epoch, identify(self.shapes))
        return self._naming[1]

    def redefines(self, element_id: str) -> Shape | None:
        """The new shape whose id, as the script now names it, is that of the existing element: the current
        version of that geometry."""

        if not element_id.endswith(ELEMENT_SUFFIX):
            return None
        return self.naming().unique(element_id[:-len(ELEMENT_SUFFIX)])

    def current_shape(self, element_id: str) -> Shape | None:
        """The shape that now stands for the element: the new shape that redefines it, else the row the script
        reached; None for an element the script never reached, which the record describes."""

        shape: Shape | None = self.redefines(element_id)
        if shape is not None:
            return shape
        row = self.rows.get(element_id)
        return row if row is not None and row.deleted_line is None else None

    def note_assignment(self, name: str, value: object) -> None:
        """A module-level assignment: the variable a new shape was last assigned to names it."""

        self.order += 1
        if isinstance(value, Shape):
            if value.is_new:
                value.direct = (name, self.order, self.line)
                self.renamed()
            return
        if isinstance(value, (list, tuple)):
            size, _, shapes = self.measure(value)
            if not shapes:
                return
            self.charge(size)
            self.renamed()
            for item in _flatten(value):
                if isinstance(item, Shape) and item.is_new:
                    item.listed = (name, self.order, self.line)

    def alive(self, value: object, what: str) -> Shape:
        if not isinstance(value, Shape):
            raise ShapeError(f"{what} needs a shape, not {describe(value)}")
        if value.deleted_line is not None:
            raise ShapeError(f"{value.label()} was deleted on line {value.deleted_line}")
        return value

    def _shapes_in(self, value: object, what: str) -> list[Shape]:
        if isinstance(value, (list, tuple)):
            self.charge(self.measure(value)[0])
        return [self.alive(item, what) for item in _flatten(value)]

    def changed(self, shape: Shape, *, extent: bool = True) -> Shape:
        if extent:
            shape.check_extent()  # type: ignore[attr-defined]
        shape.touched(self.line)
        self.world.tops.clear()  # a top may have moved: work the tops out again when next asked
        return shape

    def profile(self, value: object, what: str) -> Profile:
        """A profile, or a list of points read as ``polygon(points)``: checked once per distinct outline."""

        if isinstance(value, Profile):
            return value
        if not isinstance(value, (list, tuple)):
            raise ShapeError(f"{what} needs a profile from rect, polygon, circle or offset, not {describe(value)}")
        self.charge(self.measure(value)[0])
        points = profile_points(value, what)
        key = tuple(points)
        found = self.profiles.get(key)
        if found is None:
            found = self.profiles[key] = closed_profile(points, what, self.spend_pairs)
        return found

    # ---- relations: what cuts what, what stands on what
    def _link_cut(self, host: Shape, cutter: Any) -> None:
        host.voids[cutter] = None
        if isinstance(cutter, Shape):
            self.hosts_by_cutter.setdefault(cutter, {})[host] = None

    def _unlink_cut(self, host: Shape, cutter: Any) -> None:
        host.voids.pop(cutter, None)
        host.cut_lines.pop(cutter, None)
        if isinstance(cutter, Shape):
            self.hosts_by_cutter.get(cutter, {}).pop(host, None)

    def _reindex(self, shape: Shape) -> None:
        """Record what ``shape`` stands on after its anchors changed (nothing, once it is deleted)."""

        for target in self.stand_targets.get(shape, ()):
            self.carried.get(target, {}).pop(shape, None)
        targets = () if shape.deleted_line is not None else tuple(
            anchor.target for anchor in shape.anchors() if anchor.kind == "top")  # type: ignore[attr-defined]
        for target in targets:
            self.carried.setdefault(target, {})[shape] = None
        self.stand_targets[shape] = targets

    def hosts_of(self, target: Shape) -> list[str]:
        """What ``target`` cuts as this script knows it: the script's own cuts and those of the geometry it reached.

        What the record's other geometry cuts may still change - a new shape that redefines a host replays its
        cuts only when lowering - so those cuts are checked there, against the final model.
        """

        return [host.label() for host in self.hosts_by_cutter.get(target, {}) if host.deleted_line is None]

    def standing_on(self, target: Shape) -> list[str]:
        """What stands on ``target``'s top as this script knows it; the record's own supports are checked when
        lowering, against the final model."""

        return [shape.label() for shape in self.carried.get(target, {})
                if shape is not target and shape.deleted_line is None]

    def _record_dependents(self, shape: RowShape) -> list[str]:
        ids = {shape.element_id} | ({shape.component_id} if shape.owns_component else set())
        handled = set(self.world.void_hosts.get(shape.element_id, ())) | set(self.world.top_users.get(shape.element_id, ()))  # type: ignore[arg-type]
        found = set().union(*(self.world.downstream(identifier) for identifier in ids))  # type: ignore[arg-type]
        found -= ids | handled
        found = {item for item in found if not (item in self.rows and self.rows[item].deleted_line is not None)}
        found |= {relation.relation_id for relation in self.world.record.relations if {relation.subject, relation.object} & ids}
        found |= {entity.entity_id for entity in self.world.record.entities
                  if entity.parent_id in ids and entity.entity_id not in ids}
        return sorted(found)

    def anchor(self, value: object, what: str) -> Anchor:
        default = self.world.default_level
        if isinstance(value, Anchor):
            anchor = value
        elif default is None and (isinstance(value, ParamRef) or is_number(value)):
            raise ShapeError(NO_LEVEL)
        elif isinstance(value, ParamRef):
            if default[1] != 0:  # type: ignore[index]
                raise ShapeError(f"{value!r} alone is measured from the project's zero; write "
                                 f"level('{short(default[0])}') + {value!r}")  # type: ignore[index]
            anchor = Anchor("level", default[0], 0.0, value.key)  # type: ignore[index]
        elif is_number(value):
            anchor = Anchor("level", default[0], coordinate(value, what) - default[1])  # type: ignore[index]
        else:
            raise ShapeError(f"{what} takes a number, level(id) or top(obj), optionally plus a number, "
                             f"not {describe(value)}")
        if anchor.kind == "top":
            self.alive(anchor.target, what)
            if self.hosts_of(anchor.target):
                raise ShapeError(f"{anchor.target.label()} cuts another shape; nothing can stand on a cutter's top")
        return anchor

    def _stands_on_itself(self, shape: Shape, anchor: Anchor) -> bool:
        """Whether standing on ``anchor`` would lead back to ``shape``, through the script or the record."""

        if anchor.kind != "top":
            return False
        pending: list[Any] = [anchor.target]
        seen: set = set()
        while pending:
            node = pending.pop()
            if isinstance(node, str):
                if node in self.rows:
                    node = self.rows[node]
                else:
                    if isinstance(shape, RowShape) and shape.existing and shape.element_id == node:
                        return True
                    if node not in seen:
                        seen.add(node)
                        pending.extend(self.world.stands_on.get(node, ()))
                    continue
            if node is shape:
                return True
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, RowShape) and node.existing and node.base_anchor is None:
                pending.extend(self.world.stands_on.get(node.element_id, ()))  # type: ignore[arg-type]
            else:
                pending.extend(item.target for item in node.anchors() if item.kind == "top")  # type: ignore[attr-defined]
        return False

    def _default_level(self) -> tuple[str, float]:
        if self.world.default_level is None:
            raise ShapeError(NO_LEVEL)
        return self.world.default_level

    # ---- profiles and planes
    def verb_rect(self, a: dict, given: frozenset) -> Profile:
        return rect(a["x"], a["z"], a["width"], a["depth"])

    def verb_polygon(self, a: dict, given: frozenset) -> Profile:
        return self.profile(a["points"], "polygon()")

    def verb_circle(self, a: dict, given: frozenset) -> Profile:
        return circle(a["x"], a["z"], a["radius"], a["segments"])

    def verb_offset(self, a: dict, given: frozenset) -> Profile:
        return offset(self.profile(a["profile"], "offset()"), a["distance"], self.spend_pairs)

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
        if is_number(value) and number(value, what) == 0:
            raise ShapeError("a height of zero makes no solid; use face() for a flat face")
        return length(value, what)

    @staticmethod
    def _plane(value: object, what: str) -> Plane | None:
        if value is None or isinstance(value, Plane):
            return value
        raise ShapeError(f"{what} plane takes plane(), front() or side(), not {describe(value)}")

    def _drawn(self, shape: Drawn) -> Shape:
        shape.check_extent()
        return self.new(shape)

    def verb_extrude(self, a: dict, given: frozenset) -> Shape:
        profile = self.profile(a["profile"], "extrude()")
        height = self._height(a["height"], "extrude() height")
        drawing = self._plane(a["plane"], "extrude()")
        if drawing is not None:
            if "at" in given:
                raise ShapeError("at is not given with plane: the plane's origin places the profile")
            self._default_level()
            return self._drawn(Drawn(self._next(), self.line, "extrude", profile=profile, height=height, plane=drawing))
        anchor = self.anchor(a["at"], "extrude() at")
        return self._drawn(Drawn(self._next(), self.line, "extrude", profile=profile, height=height, anchor=anchor))

    def verb_face(self, a: dict, given: frozenset) -> Shape:
        profile = self.profile(a["profile"], "face()")
        drawing = self._plane(a["plane"], "face()")
        if drawing is not None:
            if "at" in given:
                raise ShapeError("at is not given with plane: the plane's origin places the profile")
            self._default_level()
            return self._drawn(Drawn(self._next(), self.line, "face", profile=profile, plane=drawing))
        return self._drawn(Drawn(self._next(), self.line, "face", profile=profile,
                                 anchor=self.anchor(a["at"], "face() at")))

    def verb_path(self, a: dict, given: frozenset) -> Shape:
        points = a["points"]
        if not isinstance(points, (list, tuple)):
            raise ShapeError(f"path() takes a list of points (x, y, z), not {describe(points)}")
        if not 2 <= len(points) <= MAX_PATH_POINTS:
            raise ShapeError(f"path() takes 2 to {MAX_PATH_POINTS} points")
        self.charge(self.measure(points)[0])
        checked = [point3(point, "path()") for point in points]
        for p, q in zip(checked, checked[1:]):
            if math.dist(p, q) < MIN_LENGTH:
                raise ShapeError(f"path() repeats the point ({p[0]!r}, {p[1]!r}, {p[2]!r})")
        path_frame(checked)
        self._default_level()
        return self._drawn(Drawn(self._next(), self.line, "path", points=tuple(checked)))

    def verb_section(self, a: dict, given: frozenset) -> Section:
        return Section(self.profile(a["profile"], "section()"), self.anchor(a["at"], "section() at"))

    def verb_loft(self, a: dict, given: frozenset) -> Shape:
        sections = a["sections"]
        if not isinstance(sections, (list, tuple)) or len(sections) < 2:
            raise ShapeError("loft() needs a list of two or more sections")
        if len(sections) > MAX_SECTIONS:
            raise ShapeError(f"loft() takes at most {MAX_SECTIONS} sections")
        for item in sections:
            if not isinstance(item, Section):
                raise ShapeError(f"loft() takes sections made with section(profile, at), not {describe(item)}")
        if len({len(item.profile.points) for item in sections}) != 1:
            raise ShapeError("every loft section needs the same number of points")
        first = sections[0].anchor
        for item in sections:
            if item.anchor.param is not None:
                raise ShapeError(f"a loft section cannot be bound to param('{item.anchor.param}')")
            if not item.anchor.base_of(first):
                raise ShapeError("all loft sections are measured from the same level or the same top")
        if not isinstance(a["cap"], bool):
            raise ShapeError(f"loft() cap must be True or False, not {describe(a['cap'])}")
        self.tick(len(sections) * len(sections[0].profile.points) // 10)  # per point, as a copy is
        return self._drawn(Drawn(self._next(), self.line, "loft", sections=tuple(sections), cap=a["cap"]))

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
        self.tick(shape.size // 10)  # type: ignore[attr-defined]
        self._edit(shape, lambda: shape.apply(change))  # type: ignore[attr-defined]
        return self.changed(shape)

    def verb_move(self, a: dict, given: frozenset) -> Shape:
        change = PlanMap("move", dx=coordinate(a["dx"], "move() dx"), dy=coordinate(a["dy"], "move() dy"),
                         dz=coordinate(a["dz"], "move() dz"))
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
        return self._transform(a["obj"], "mirror()", PlanMap("mirror", axis=axis, at=coordinate(a[axis], f"mirror() {axis}")))

    def _copy(self, shape: Shape, dx: float, dy: float, dz: float) -> Shape:
        if isinstance(shape, RowShape) and shape.producer == "wall":
            raise ShapeError(f"{shape.label()} can be moved, rotated, mirrored, given a height or a base, cut or "
                             "deleted; it cannot be copied")
        self.tick(shape.size // 10)  # type: ignore[attr-defined]
        twin = shape.clone(self._next(), self.line)  # type: ignore[attr-defined]
        if dx or dy or dz:
            twin.apply(PlanMap("move", dx=dx, dy=dy, dz=dz))
        twin.check_extent()
        return self.new(twin)

    def verb_copy(self, a: dict, given: frozenset) -> Shape:
        shape = self.alive(a["obj"], "copy()")
        return self._copy(shape, coordinate(a["dx"], "copy() dx"), coordinate(a["dy"], "copy() dy"),
                          coordinate(a["dz"], "copy() dz"))

    def verb_array(self, a: dict, given: frozenset) -> list:
        shape = self.alive(a["obj"], "array()")
        count = a["count"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= LIMITS["geometryResults"]:
            raise ShapeError(f"array() count must be a whole number from 1 to 300, not {shown(count)}")
        step = (coordinate(a["dx"], "array() dx"), coordinate(a["dy"], "array() dy"), coordinate(a["dz"], "array() dz"))
        return [shape] + [self._copy(shape, *(k * c for c in step)) for k in range(1, count)]

    # ---- solid edits
    def verb_pushpull(self, a: dict, given: frozenset) -> Shape:
        shape = self.alive(a["obj"], "pushpull()")
        distance = coordinate(a["distance"], "pushpull() distance")
        if isinstance(shape, RowShape):
            self._edit(shape, lambda: shape.pushpull(distance))
        elif shape.kind == "face":  # type: ignore[attr-defined]
            shape.kind, shape.height = "extrude", length(distance, "pushpull() distance")  # type: ignore[attr-defined]
        elif shape.kind == "extrude":  # type: ignore[attr-defined]
            if isinstance(shape.height, ParamRef):  # type: ignore[attr-defined]
                raise ShapeError(f"the height of {shape.label()} is bound to {shape.height!r}; set_height() changes it")  # type: ignore[attr-defined]
            height = float(shape.height)  # type: ignore[attr-defined]
            size = abs(height) + distance
            if size < MIN_LENGTH:
                raise ShapeError(f"pushpull({distance!r}) would leave {shape.label()} with no height")
            shape.height = math.copysign(size, height)  # type: ignore[attr-defined]
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
        self.tick(shape.size // 10)  # type: ignore[attr-defined]
        anchor = self.anchor(a["at"], "set_base() at")
        if self._stands_on_itself(shape, anchor):
            raise ShapeError(f"{shape.label()} would stand on its own top")
        if isinstance(shape, RowShape):
            self._edit(shape, lambda: shape.set_base(anchor))
        elif shape.kind in ("extrude", "face") and shape.plane is None:  # type: ignore[attr-defined]
            shape.anchor = anchor  # type: ignore[attr-defined]
        elif shape.kind == "loft":  # type: ignore[attr-defined]
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
        self._reindex(shape)
        return self.changed(shape)

    # ---- cuts
    def verb_cut(self, a: dict, given: frozenset) -> Shape:
        host = self.alive(a["host"], "cut()")
        if not host.is_solid:  # type: ignore[attr-defined]
            raise ShapeError(f"cut() needs a solid to cut; {host.label()} is a {host.word}")
        cutters = self._shapes_in(a["cutters"], "cut()")
        if not cutters:
            raise ShapeError("cut() needs at least one cutter")
        hosting = self.hosts_of(host)
        if hosting:
            raise ShapeError(f"{host.label()} is itself a cutter of {hosting[0]} and cannot be cut")
        for cutter in cutters:
            if cutter is host:
                raise ShapeError("a shape cannot cut itself")
            if not cutter.can_cut:  # type: ignore[attr-defined]
                raise ShapeError(f"{cutter.label()} is a {cutter.word}; only an extruded or lofted solid can cut")
            if cutter.voids:
                raise ShapeError(f"{cutter.label()} has cutters of its own and cannot cut another shape")
            standing = self.standing_on(cutter)
            if standing:
                raise ShapeError(f"{standing[0]} stands on the top of {cutter.label()}; a cutter carries nothing")
            if cutter not in host.voids:
                if len(host.voids) >= MAX_CUTTERS:
                    raise ShapeError(f"a shape can be cut by at most {MAX_CUTTERS} cutters")
                self._link_cut(host, cutter)
                host.cut_lines[cutter] = self.line
                if host.is_new:
                    host.void_events.append(("cut", cutter, self.line))
            if host not in cutter.cut_into:
                cutter.cut_into[host] = None
                self.renamed()  # an unnamed cutter is named after its first host
        return self.changed(host, extent=False)

    def verb_uncut(self, a: dict, given: frozenset) -> Shape:
        host = self.alive(a["host"], "uncut()")
        cutters = self._shapes_in(a["cutters"], "uncut()")
        if not cutters:
            for cutter in list(host.voids):
                self._unlink_cut(host, cutter)
            if host.is_new:
                host.void_events.append(("clear", None, self.line))
            return self.changed(host, extent=False)
        for cutter in cutters:
            if cutter in host.voids:
                self._unlink_cut(host, cutter)
                if host.is_new:
                    host.void_events.append(("uncut", cutter, self.line))
            elif host.is_new:
                # It may still cut the geometry this new shape redefines; lowering checks that.
                host.void_events.append(("uncut", cutter, self.line))
            else:
                raise ShapeError(f"{cutter.label()} does not cut {host.label()}")
        return self.changed(host, extent=False)

    # ---- anchors and bindings
    def verb_level(self, a: dict, given: frozenset) -> Anchor:
        identifier = a["id"]
        if not isinstance(identifier, str) or identifier not in self.world.levels:
            raise ShapeError(f"the project has no level {shown(identifier)}")
        return Anchor("level", identifier, 0.0)

    def verb_top(self, a: dict, given: frozenset) -> Anchor:
        shape = self.alive(a["obj"], "top()")
        if not shape.can_carry(self.world):  # type: ignore[attr-defined]
            raise ShapeError(f"top() needs a solid extruded upward from a plan profile; {shape.label()} is not one")
        if self.hosts_of(shape):
            raise ShapeError(f"{shape.label()} cuts another shape; nothing can stand on a cutter's top")
        return Anchor("top", shape, 0.0)

    def verb_param(self, a: dict, given: frozenset) -> ParamRef:
        key = a["key"]
        if not isinstance(key, str) or key not in self.world.parameter_keys:
            raise ShapeError(f"the project has no parameter {shown(key)}")
        return ParamRef(key)

    def verb_bounds(self, a: dict, given: frozenset) -> tuple:
        shape = self.alive(a["obj"], "bounds()")
        self.tick(shape.size // 10)  # type: ignore[attr-defined]
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
        if len(identifier) > MAX_ID:
            raise ShapeError(f"an id has at most {MAX_ID} characters")
        try:
            require_identifier(identifier, "id")
        except ValueError:
            raise ShapeError(f"{shown(identifier)} is not a usable id: letters, digits, '.', '-' and '_', "
                             "starting with a letter or a digit") from None
        if identifier.endswith("-body"):
            raise ShapeError(f"{identifier} ends with -body, which is kept for a shape's geometry; choose another id")
        shape.explicit = (identifier, self.line)
        self.renamed()
        return shape

    def verb_get(self, a: dict, given: frozenset) -> Shape:
        identifier = a["id"]
        if not isinstance(identifier, str):
            raise ShapeError(f"get() takes an id as text, not {describe(identifier)}")
        element_id, component_id, geometry_id = self.world.editable(identifier)
        row = self._row(element_id, component_id, geometry_id)
        self.explicit.add(element_id)
        return row

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
            raise ShapeError(f"{short(geometry_id)} is stored in a form a script cannot read") from None
        self.rows[element_id] = row
        stored, row.voids = list(row.voids), {}
        for void in stored:  # its cutters, loaded as context: the script may reach them, or not
            try:
                self._link_cut(row, self._row(*self.world.editable(void)))
            except ShapeError:
                self._link_cut(row, void)
        return row

    def verb_delete(self, a: dict, given: frozenset) -> None:
        shape = self.alive(a["obj"], "delete()")
        hosting = self.hosts_of(shape)
        if hosting:
            raise ShapeError(f"{shape.label()} still cuts {hosting[0]}; uncut it first")
        standing = self.standing_on(shape)
        if standing:
            raise ShapeError(f"{standing[0]} stands on the top of {shape.label()}; delete or move it first")
        if isinstance(shape, RowShape) and shape.existing:
            dependents = self._record_dependents(shape)
            if dependents:
                listed = short(", ".join(dependents), 80)
                raise ShapeError(f"{listed} still depend on {shape.label()}; change them first")
        shape.deleted_line = self.line
        self.renamed()  # what stood on it, if anything, is measured anew
        if shape.is_new:
            self.points -= shape.point_count  # type: ignore[attr-defined]
        for cutter in list(shape.voids):
            self._unlink_cut(shape, cutter)
        self._reindex(shape)
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
        return f"<function {short(self.name)}>"


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
            raise ShapeError(f"{spec.name}() has no parameter {short(key)}: {spec.signature}")
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
        self.depth = 0
        self.module = _Frame({}, None, True)

    def error(self, node: object, message: str) -> ConstructionError:
        return _error_at(self.lines, node, message)

    def guarded(self, node: ast.AST, work: Callable[[], Any]) -> Any:
        """Run bookkeeping that may refuse, refusing at ``node``."""

        try:
            return work()
        except ShapeError as exc:
            raise self.error(node, str(exc)) from None

    def step(self, node: ast.AST) -> None:
        try:
            self.session.tick()
        except ShapeError as exc:
            raise self.error(node, str(exc)) from None

    def run(self, tree: ast.Module) -> None:
        self.block(tree.body, self.module)

    # ---- statements
    def block(self, statements: list[ast.stmt], frame: _Frame) -> None:
        for statement in statements:
            self.execute(statement, frame)

    def execute(self, node: ast.stmt, frame: _Frame) -> None:
        self.step(node)
        self.session.line = node.lineno
        self.session.statement = node
        if isinstance(node, ast.Expr):
            self.evaluate(node.value, frame)
        elif isinstance(node, ast.Assign):
            value = self.evaluate(node.value, frame)
            for target in node.targets:
                self.assign(target, value, frame, naming=True)
        elif isinstance(node, ast.AugAssign):
            current = self.lookup(node.target.id, frame, node.target)  # type: ignore[attr-defined]
            operand = self.evaluate(node.value, frame)
            if isinstance(node.op, ast.Add) and isinstance(current, list) and isinstance(operand, (list, tuple)):
                self.guarded(node, lambda: self.session.grow(current, operand))  # in place, as Python extends a list
                value = current
            else:
                value = self.binary(node.op, current, operand, node)
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
                self.guarded(target, lambda: self.session.note_assignment(target.id, value))
            return
        elements = target.elts  # type: ignore[attr-defined]
        if not isinstance(value, (list, tuple, range)):
            raise self.error(target, f"cannot unpack {describe(value)} into {len(elements)} names")
        if len(value) != len(elements):
            raise self.error(target, f"cannot unpack {len(value)} values into {len(elements)} names")
        for element, item in zip(elements, value):
            self.assign(element, item, frame, naming=naming)

    # ---- expressions
    def evaluate(self, node: ast.AST, frame: _Frame) -> Any:
        self.step(node)
        if isinstance(node, ast.Constant):
            return self.checked(node.value, node) if is_number(node.value) else self.text(node.value, node)
        if isinstance(node, ast.Name):
            return self.lookup(node.id, frame, node)
        if isinstance(node, ast.List):
            items = [self.evaluate(element, frame) for element in node.elts]
            return self.guarded(node, lambda: self.session.created(items))
        if isinstance(node, ast.Tuple):
            items = tuple(self.evaluate(element, frame) for element in node.elts)
            return self.guarded(node, lambda: self.session.created(items))
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
        raise self.error(node, f"{short(name)} is not defined")

    def text(self, value: Any, node: ast.AST) -> Any:
        if isinstance(value, str) and len(value) > MAX_TEXT:
            raise self.error(node, "text may hold at most 10 000 characters")
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
            return self.guarded(node, lambda: combine(symbol, left, right))
        if is_number(left) and is_number(right):
            return self.arithmetic(symbol, left, right, node)
        session = self.session
        if symbol == "+" and type(left) is type(right) and isinstance(left, str):
            return self.text(left + right, node)
        if symbol == "+" and type(left) is type(right) and isinstance(left, (list, tuple)):
            (ls, ld, lh), (rs, rd, rh) = session.measure(left), session.measure(right)
            self.guarded(node, lambda: session.admit(ls + rs, max(ld, rd)))
            kids = dict(session.kids_of(left))
            for kid, times in session.kids_of(right).items():
                kids[kid] = kids.get(kid, 0) + times
            return session.remember(left + right, ls + rs, max(ld, rd), lh or rh, kids)
        if symbol == "*":
            sequence, count = (left, right) if isinstance(left, (list, tuple)) else (right, left)
            if isinstance(sequence, (list, tuple)) and isinstance(count, int) and not isinstance(count, bool):
                size, depth, shapes = session.measure(sequence)
                if count <= 0:  # an empty list or tuple: nothing nested, nothing held
                    size, depth, shapes = 0, 1, False
                total = size * max(count, 0)
                self.guarded(node, lambda: session.admit(total, depth))
                kids = {kid: times * count for kid, times in session.kids_of(sequence).items()} if count > 0 else {}
                return session.remember(sequence * count, total, depth, shapes, kids)
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
        session = self.session
        if isinstance(operator, (ast.Eq, ast.NotEq)):
            # Nested lists and tuples compare item by item, as in Python; the comparison costs what it may walk.
            self.guarded(node, lambda: session.charge(_weight(left) + _weight(right)))
            return (left == right) == isinstance(operator, ast.Eq)
        if isinstance(operator, ast.Is):
            return left is right
        if isinstance(operator, ast.IsNot):
            return left is not right
        if isinstance(operator, (ast.In, ast.NotIn)):
            if not isinstance(right, (list, tuple, range, str)) or (isinstance(right, str) and not isinstance(left, str)):
                raise self.error(node, f"cannot look for {describe(left)} in {describe(right)}")
            if isinstance(right, range):
                self.guarded(node, lambda: session.charge(_weight(left) + len(right)))
            elif isinstance(right, (list, tuple)):
                self.guarded(node, lambda: session.charge(_weight(left) * len(right) + _weight(right)))
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
            result = value[slice(*parts)]
            return self.guarded(node, lambda: self.session.created(result))
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
        counted = {"size": 0}

        def walk(index: int) -> None:
            if index == len(node.generators):
                item = self.evaluate(node.elt, scope)
                counted["size"] += 1 + self.session.measure(item)[0]
                if counted["size"] > MAX_ELEMENTS:
                    raise self.error(node, "a list or tuple may hold at most 10 000 elements, nested ones included")
                result.append(item)
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
        # Measured whole at the end: a function called for a later item may have grown (+=) an earlier one.
        return self.guarded(node, lambda: self.session.created(result))

    # ---- calls
    def call(self, node: ast.Call, frame: _Frame) -> Any:
        name = node.func.id  # type: ignore[attr-defined]
        callee = self.lookup(name, frame, node.func)
        args = [self.evaluate(argument, frame) for argument in node.args]
        keywords = {keyword.arg: self.evaluate(keyword.value, frame) for keyword in node.keywords}
        if isinstance(callee, _Function):
            return self.call_function(callee, args, keywords, node)
        if not isinstance(callee, _Callable):
            raise self.error(node, f"{short(name)} is {describe(callee)}, which cannot be called")
        try:
            if callee.kind == "verb":
                values, given = _bind(verb(callee.name), args, keywords)
                result = getattr(self.session, f"verb_{callee.name}")(values, given)
            else:
                result = self.builtin(callee.name, args, keywords, node)
            return self.session.created(result)
        except ShapeError as exc:
            raise self.error(node, str(exc)) from None

    def call_function(self, function: _Function, args: list, keywords: dict, node: ast.AST) -> Any:
        names = [name for name, _ in function.parameters]
        title = short(function.name)
        if len(args) > len(names):
            raise self.error(node, f"{title}() takes {len(names)} arguments, not {len(args)}")
        bound = dict(zip(names, args))
        for key, value in keywords.items():
            if key not in names:
                raise self.error(node, f"{title}() has no parameter {short(key)}")
            if key in bound:
                raise self.error(node, f"{title}() is given {short(key)} twice")
            bound[key] = value
        for name, default in function.parameters:
            if name not in bound:
                if default is _MISSING:
                    raise self.error(node, f"{title}() needs {short(name)}")
                bound[name] = default
        if self.depth >= LIMITS["callDepth"]:
            raise self.error(node, "calls nest deeper than 16")
        self.depth += 1
        line, statement = self.session.line, self.session.statement
        self.session.callers.append(statement)  # what the body makes is keyed by the statement that called it too
        try:
            self.block(function.body, _Frame(bound, self.module, False))
        except _Return as result:
            return result.value
        except ConstructionError as exc:
            exc.calls.append(getattr(node, "lineno", line))
            raise
        finally:
            self.depth -= 1
            self.session.callers.pop()
            self.session.line, self.session.statement = line, statement
        return None

    def builtin(self, name: str, args: list, keywords: dict, node: ast.AST) -> Any:
        allowed = {"enumerate": {"start"}, "round": {"ndigits"}, "sum": {"start"}}.get(name, set())
        unknown = sorted(set(keywords) - allowed)
        if unknown:
            raise ShapeError(f"{name}() takes no keyword {short(unknown[0])}")
        handler: Callable[..., Any] = getattr(self, f"_builtin_{name}")
        try:
            result = handler(*args, **keywords)
        except ShapeError:
            raise
        except TypeError:
            given = ", ".join(describe(a) for a in args[:4])
            raise ShapeError(f"{name}() cannot take {given}" if args else f"{name}() needs an argument") from None
        except (ValueError, OverflowError, ZeroDivisionError):
            raise ShapeError(f"{name}() of {', '.join(describe(a) for a in args[:4])} is not defined") from None
        return self.checked(result, node)

    # ---- builtins: numbers only for arithmetic, flat and bounded sequences
    def _numbers(self, values: object, name: str) -> list:
        if not isinstance(values, (list, tuple, range)):
            raise ShapeError(f"{name}() takes numbers or a flat list of numbers, not {describe(values)}")
        self.session.charge(len(values))
        for value in values:
            if not is_number(value):
                raise ShapeError(f"{name}() takes numbers, not {describe(value)}")
        return list(values)

    def _sequence(self, value: object, name: str) -> Any:
        if not isinstance(value, (list, tuple, range, str)):
            raise ShapeError(f"{name}() takes a list, a tuple, a range or text, not {describe(value)}")
        self.session.charge(len(value))
        return value

    def _builtin_range(self, *args: object) -> range:
        if not 1 <= len(args) <= 3 or any(isinstance(a, bool) or not isinstance(a, int) for a in args):
            raise ShapeError("range() takes one to three whole numbers")
        start, stop, step = (0, args[0], 1) if len(args) == 1 else (args[0], args[1], args[2] if len(args) == 3 else 1)
        if step == 0:
            raise ShapeError("range() step cannot be zero")
        if _range_length(start, stop, step) > LIMITS["rangeItems"]:  # type: ignore[arg-type]
            raise ShapeError("range() may give at most 10 000 numbers")
        return range(start, stop, step)  # type: ignore[arg-type]

    def _builtin_len(self, value: object) -> int:
        if not isinstance(value, (list, tuple, range, str)):
            raise ShapeError(f"len() takes a list, a tuple, a range or text, not {describe(value)}")
        return len(value)

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
        if ndigits is not None and (isinstance(ndigits, bool) or not isinstance(ndigits, int)
                                    or not -MAX_ROUND_DIGITS <= ndigits <= MAX_ROUND_DIGITS):
            raise ShapeError(f"round() digits must be a whole number from -{MAX_ROUND_DIGITS} to {MAX_ROUND_DIGITS}")
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
        return [] if not values else list(self._sequence(values[0], "list"))

    def _builtin_tuple(self, *values: object) -> tuple:
        return () if not values else tuple(self._sequence(values[0], "tuple"))

    def _builtin_float(self, value: object) -> float:
        return number(value, "float()")

    def _builtin_int(self, value: object) -> int:
        return int(number(value, "int()"))

    def _builtin_print(self, *values: object) -> None:
        if len(self.session.log) >= MAX_LOG_LINES:
            raise ShapeError(f"print() may write at most {MAX_LOG_LINES:,} lines".replace(",", " "))
        self.session.log.append(render(values, MAX_LOG_LINE))
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


def run_script(script: object, record: StateRecord) -> tuple[Session, list[str]]:
    """Parse, check and interpret a script against a record: the session it leaves and its lines, or a refusal."""

    tree, lines = parse(script)
    session = Session(record, source=script)  # type: ignore[arg-type]
    try:
        Interpreter(lines, session).run(tree)
    except ConstructionError as exc:
        raise exc.with_calls() from None
    except RecursionError:
        raise _statement_error(lines, session.line, "the script nests calls and expressions too deeply") from None
    return session, lines
