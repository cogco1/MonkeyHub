"""Derivation table: named quantities as expressions with a basis (P100).

A building's numbers get names and bases instead of living as literals in
authoring code. ``DerivationTable@1`` holds quantities whose ``expr`` is a
small arithmetic expression over evidence readings and other quantities;
``evaluate`` resolves them in dependency order with a safe evaluator (no
Python ``eval``: numbers, names, ``+ - * /``, unary minus, parentheses and
``min max abs round sqrt``). Unknown names, cycles, division by zero and
non-finite results fail typed. The evaluated table lists each value with
its inputs, so it can be recorded as DERIVED facts and invalidated through
its inputs.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Mapping

from archflow.contracts.canonical import canonical_json
from archflow.project.refs import require_identifier
from archflow.contracts.fields import (
    number,
)

_TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\d*\.\d+|\d+)|([A-Za-z_][A-Za-z0-9_]*)|(.))")
_FUNCTIONS = {
    "min": (2, min),
    "max": (2, max),
    "abs": (1, abs),
    "round": (2, lambda value, digits: round(value, int(digits))),
    "sqrt": (1, math.sqrt),
}
_MAX_QUANTITIES = 10_000


class DerivationError(ValueError):
    """Typed failure of the derivation table contracts."""


class _Parser:
    """Recursive-descent evaluator over one expression; names resolve through ``lookup``.

    With ``evaluate=False`` the same descent validates the grammar (tokens,
    parentheses, known functions and their arity) and collects the names the
    expression reads, without performing any arithmetic: no division, no
    function call, no finiteness check. That is what ``expression_names``
    needs, and it is why a dependency question about ``a / (a - 1)`` cannot
    fail on a division that only the real readings decide.
    """

    def __init__(self, expr: str, lookup, label: str, *, evaluate: bool = True) -> None:
        self.tokens = self._tokenize(expr, label)
        self.pos = 0
        self.lookup = lookup
        self.label = label
        self.evaluate = evaluate
        self.names: list[str] = []

    @staticmethod
    def _tokenize(expr: str, label: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        for match in _TOKEN.finditer(expr):
            number, name, other = match.groups()
            if number is not None:
                tokens.append(("num", number))
            elif name is not None:
                tokens.append(("name", name))
            elif other is not None:
                if other.strip() == "":
                    continue
                if other not in "+-*/(),":
                    raise DerivationError(f"{label}: unexpected character {other!r}")
                tokens.append(("op", other))
        if not tokens:
            raise DerivationError(f"{label}: empty expression")
        return tokens

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self, kind: str, value: str | None = None) -> str:
        token = self._peek()
        if token is None or token[0] != kind or (value is not None and token[1] != value):
            raise DerivationError(f"{self.label}: expected {value or kind} at token {self.pos}")
        self.pos += 1
        return token[1]

    def parse(self) -> float:
        value = self._sum()
        if self._peek() is not None:
            raise DerivationError(f"{self.label}: trailing tokens")
        return value

    def _sum(self) -> float:
        value = self._product()
        while (token := self._peek()) and token == ("op", "+") or token == ("op", "-"):
            self.pos += 1
            right = self._product()
            value = value + right if token[1] == "+" else value - right
        return value

    def _product(self) -> float:
        value = self._unary()
        while (token := self._peek()) and token[0] == "op" and token[1] in "*/":
            self.pos += 1
            right = self._unary()
            if not self.evaluate:
                continue                                    # grammar only: nothing is computed
            if token[1] == "/":
                if right == 0.0:
                    raise DerivationError(f"{self.label}: division by zero")
                value = value / right
            else:
                value = value * right
        return value

    def _unary(self) -> float:
        token = self._peek()
        if token == ("op", "-"):
            self.pos += 1
            return -self._unary()
        if token == ("op", "+"):
            self.pos += 1
            return self._unary()
        return self._atom()

    def _atom(self) -> float:
        token = self._peek()
        if token is None:
            raise DerivationError(f"{self.label}: unexpected end of expression")
        kind, text = token
        if kind == "num":
            self.pos += 1
            return float(text)
        if kind == "name":
            self.pos += 1
            if self._peek() == ("op", "("):
                if text not in _FUNCTIONS:
                    raise DerivationError(f"{self.label}: unknown function {text!r}")
                arity, function = _FUNCTIONS[text]
                self._take("op", "(")
                args = [self._sum()]
                while self._peek() == ("op", ","):
                    self.pos += 1
                    args.append(self._sum())
                self._take("op", ")")
                if len(args) != arity:
                    raise DerivationError(f"{self.label}: {text} takes {arity} argument(s)")
                if not self.evaluate:
                    return 0.0
                return number(function(*args), f"{self.label}: {text}")
            self.names.append(text)
            if not self.evaluate:
                return 0.0
            return self.lookup(text)
        if token == ("op", "("):
            self.pos += 1
            value = self._sum()
            self._take("op", ")")
            return value
        raise DerivationError(f"{self.label}: unexpected token {text!r}")


def expression_names(expr: str, label: str = "expression") -> tuple[str, ...]:
    """The names an expression reads, in order of first use.

    The restricted grammar is validated in full (a stray character, an unknown
    function, a wrong arity or unbalanced parentheses fail typed here), and
    nothing is computed: ``a / (a - 1)`` names ``a`` whatever ``a`` turns out
    to be, and whether it divides by zero is decided by ``evaluate`` with the
    actual readings, not by a placeholder.
    """

    parser = _Parser(expr, lambda name: 0.0, label, evaluate=False)
    parser.parse()
    return tuple(dict.fromkeys(parser.names))


@dataclass(frozen=True, slots=True)
class DerivedQuantity:
    """One named quantity: an expression with its basis and epistemic status."""

    name: str
    expr: str
    unit: str
    basis_refs: tuple[str, ...] = ()
    epistemic_status: str = "derived"
    note: str | None = None

    SCHEMA = "DerivedQuantity@1"

    def __post_init__(self) -> None:
        require_identifier(self.name, "quantity name")
        if not isinstance(self.expr, str) or not self.expr.strip():
            raise DerivationError(f"quantity {self.name}: expr must be text")
        if not isinstance(self.unit, str) or not self.unit:
            raise DerivationError(f"quantity {self.name}: unit must be text")
        if self.epistemic_status not in {"observed", "declared", "derived", "hypothesis", "disputed", "unknown"}:
            raise DerivationError(f"quantity {self.name}: invalid epistemic status")
        if tuple(sorted(set(self.basis_refs))) != tuple(self.basis_refs):
            raise DerivationError(f"quantity {self.name}: basis_refs must be sorted and unique")
        expression_names(self.expr, f"quantity {self.name}")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "name": self.name, "expr": self.expr, "unit": self.unit, "basis_refs": list(self.basis_refs),
                "epistemic_status": self.epistemic_status, "note": self.note}

    @classmethod
    def from_dict(cls, value: object) -> "DerivedQuantity":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise DerivationError("derived quantity payload malformed")
        return cls(name=value["name"], expr=value["expr"], unit=value["unit"], basis_refs=tuple(value.get("basis_refs", ())),
                   epistemic_status=value.get("epistemic_status", "derived"), note=value.get("note"))


@dataclass(frozen=True, slots=True)
class DerivationTable:
    project_id: str
    quantities: tuple[DerivedQuantity, ...]
    readings_ref: str | None = None

    SCHEMA = "DerivationTable@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        if not isinstance(self.quantities, tuple) or any(not isinstance(q, DerivedQuantity) for q in self.quantities):
            raise DerivationError("quantities must be DerivedQuantity items")
        names = [q.name for q in self.quantities]
        if len(set(names)) != len(names):
            raise DerivationError("quantity names must be unique")
        if len(names) > _MAX_QUANTITIES:
            raise DerivationError("derivation table exceeds the bounded item count")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "project_id": self.project_id, "readings_ref": self.readings_ref,
                "quantities": [q.to_dict() for q in self.quantities]}

    @classmethod
    def from_dict(cls, value: object) -> "DerivationTable":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise DerivationError("derivation table payload malformed")
        return cls(project_id=value["project_id"], quantities=tuple(DerivedQuantity.from_dict(q) for q in value["quantities"]),
                   readings_ref=value.get("readings_ref"))

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EvaluatedQuantity:
    name: str
    value: float
    unit: str
    inputs: tuple[str, ...]
    basis_refs: tuple[str, ...]
    epistemic_status: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "value": self.value, "unit": self.unit, "inputs": list(self.inputs), "basis_refs": list(self.basis_refs),
                "epistemic_status": self.epistemic_status}


@dataclass(frozen=True, slots=True)
class EvaluatedDerivations:
    """The table's values with their inputs: DERIVED facts, ready to record."""

    table_digest: str
    readings: tuple[tuple[str, float], ...]
    values: tuple[EvaluatedQuantity, ...]

    SCHEMA = "EvaluatedDerivations@1"

    def __getitem__(self, name: str) -> float:
        for item in self.values:
            if item.name == name:
                return item.value
        for key, value in self.readings:
            if key == name:
                return value
        raise KeyError(name)

    def as_mapping(self) -> dict[str, float]:
        out = {key: value for key, value in self.readings}
        out.update({item.name: item.value for item in self.values})
        return out

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "table_digest": self.table_digest, "readings": [[k, v] for k, v in self.readings],
                "values": [item.to_dict() for item in self.values]}

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


def evaluate(table: DerivationTable, readings: Mapping[str, float] | None = None) -> EvaluatedDerivations:
    """Evaluate every quantity in dependency order; readings are the leaves."""

    if not isinstance(table, DerivationTable):
        raise DerivationError("table must be a DerivationTable")
    leaves: dict[str, float] = {}
    for key, value in (readings or {}).items():
        require_identifier(key, "reading key")
        leaves[key] = number(value, f"reading {key}")
    by_name = {q.name: q for q in table.quantities}
    clash = sorted(set(by_name) & set(leaves))
    if clash:
        raise DerivationError(f"quantities shadow readings: {clash}")
    deps = {name: expression_names(q.expr, f"quantity {name}") for name, q in by_name.items()}
    for name, inputs in deps.items():
        for item in inputs:
            if item not in by_name and item not in leaves:
                raise DerivationError(f"quantity {name} reads unknown name {item!r}")
    # topological order with cycle detection
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(name: str, path: tuple[str, ...]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            raise DerivationError(f"cycle among quantities: {' -> '.join(path + (name,))}")
        state[name] = 1
        for item in deps[name]:
            if item in by_name:
                visit(item, path + (name,))
        state[name] = 2
        order.append(name)

    for name in sorted(by_name):
        visit(name, ())
    values: dict[str, float] = dict(leaves)
    evaluated: list[EvaluatedQuantity] = []
    for name in order:
        quantity = by_name[name]
        parser = _Parser(quantity.expr, lambda item: values[item], f"quantity {name}")
        value = number(parser.parse(), f"quantity {name}")
        values[name] = value
        evaluated.append(EvaluatedQuantity(name=name, value=value, unit=quantity.unit, inputs=deps[name],
                                           basis_refs=quantity.basis_refs, epistemic_status=quantity.epistemic_status))
    return EvaluatedDerivations(
        table_digest=table.digest,
        readings=tuple(sorted(leaves.items())),
        values=tuple(sorted(evaluated, key=lambda item: item.name)),
    )


def substitute(value: object, derived: EvaluatedDerivations) -> object:
    """Replace ``"@name"`` strings anywhere in a JSON-like value with the quantity."""

    if isinstance(value, str) and value.startswith("@"):
        try:
            return derived[value[1:]]
        except KeyError as exc:
            raise DerivationError(f"unknown quantity reference {value!r}") from exc
    if isinstance(value, list):
        return [substitute(item, derived) for item in value]
    if isinstance(value, tuple):
        return tuple(substitute(item, derived) for item in value)
    if isinstance(value, Mapping):
        return {key: substitute(item, derived) for key, item in value.items()}
    return value
