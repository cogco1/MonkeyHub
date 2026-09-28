"""How a construction script's shapes get their ids (#419, L1).

Spec section 3.1: the id given with ``name(obj, id)``; else the module-level
variable the shape was last assigned to (``_`` becomes ``-``; a variable that
holds the shape itself wins over a list that contains it; several shapes under
one variable are numbered ``<v>-1``, ``<v>-2``, ... in creation order); else
``<host>-cut-<12 hex>`` for an unnamed cutter; else ``shape-<12 hex>``. The hex
digits are the sha1 of the chain of statements that made the shape - the
module-level statement, then each statement it called, down to the one that
made the shape - and of how many shapes that chain made before it, so running
a script again gives the same ids, and the same helper called from two
statements gives two shapes.

Lowering gives the final ids and refuses what cannot be named. The session
asks for the same ids while the script runs, so that a new shape given the id
of existing geometry is read as the current version of that geometry - what
stands on its top follows it before any row is written.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from monkeyarch.construction.shapes import RowShape, Shape

ELEMENT_SUFFIX = "-body"
HASH_DIGITS = 12
VARIABLE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_WIDER = (16, 24, 40)  # the digits a hashed id keeps when a shape of the same script already has the first twelve


def made_by_construction(component_id: str, element_ids: Sequence[str] | None) -> bool:
    """Whether a component is geometry a construction script made: its one element is ``<component id>-body``.

    Lowering places every shape it makes under the modelling root, so such a
    component is its own geometry wherever it is parented, never a part of
    the component above it.
    """

    return list(element_ids or ()) == [component_id + ELEMENT_SUFFIX]


def hashed(prefix: str, shape: Shape, claimed: Iterable[str]) -> str:
    """``prefix`` and twelve hex digits of the sha1 of the statements that made ``shape`` and of how many shapes
    that chain of statements made before it; should two shapes of one script share the digits, the later keeps
    more of them, deterministically."""

    key, index = shape.made_by if shape.made_by is not None else ("", shape.seq)
    digest = hashlib.sha1(f"{key}\n{index}".encode("utf-8")).hexdigest()
    identifier = prefix + digest[:HASH_DIGITS]
    for width in _WIDER:
        if identifier not in claimed:
            break
        identifier = prefix + digest[:width]
    return identifier


@dataclass
class Naming:
    """The ids of a script's surviving shapes: per shape its id and the line the id comes from; per id every shape
    given it (more than one is a refusal); the first variable that cannot spell an id, with its line."""

    ids: dict[Shape, tuple[str, int]] = field(default_factory=dict)
    claimed: dict[str, list[Shape]] = field(default_factory=dict)
    unspellable: tuple[str, int] | None = None

    def give(self, shape: Shape, identifier: str, line: int) -> None:
        self.ids[shape] = (identifier, line)
        self.claimed.setdefault(identifier, []).append(shape)

    def unique(self, identifier: str) -> Shape | None:
        """The one shape given ``identifier``, or None when no shape or several have it."""

        members = self.claimed.get(identifier)
        return members[0] if members is not None and len(members) == 1 else None


def identity_of(shape: Shape, naming: Naming) -> str:
    """The id a shape is known by: existing geometry's own, else the one the naming gives it."""

    if isinstance(shape, RowShape) and shape.existing:
        return shape.geometry_id  # type: ignore[return-value]
    return naming.ids[shape][0]


def identify(shapes: Iterable[Shape]) -> Naming:
    """The ids of the shapes that survive, by the rules above; nothing is refused here.

    A variable that cannot spell an id is reported in ``unspellable`` and its shapes fall through to hashed ids;
    two shapes resolving to one id are both given it (``claimed`` shows it). Hosts are named before their cutters,
    since a cutter's id carries its host's.
    """

    survivors = [shape for shape in shapes if shape.deleted_line is None]
    naming = Naming()
    for shape in survivors:
        if shape.explicit is not None:
            naming.give(shape, *shape.explicit)
    groups: dict[str, list[Shape]] = {}
    for shape in survivors:
        chosen = shape.direct or shape.listed
        if shape not in naming.ids and chosen is not None:
            groups.setdefault(chosen[0], []).append(shape)
    for variable, members in groups.items():
        base = variable.replace("_", "-").lower()
        if not VARIABLE_ID.fullmatch(base):
            if naming.unspellable is None:
                naming.unspellable = (variable, (members[0].direct or members[0].listed)[2])  # type: ignore[index]
            continue
        for index, member in enumerate(members, start=1):
            line = (member.direct or member.listed)[2]  # type: ignore[index]
            naming.give(member, base if len(members) == 1 else f"{base}-{index}", line)
    cutters = [shape for shape in survivors if shape not in naming.ids
               and any(host.deleted_line is None for host in shape.cut_into)]
    unnamed_cutters = set(map(id, cutters))
    for shape in survivors:  # other unnamed shapes first: a cutter is named after its host
        if shape not in naming.ids and id(shape) not in unnamed_cutters:
            naming.give(shape, hashed("shape-", shape, naming.claimed), shape.created_line)
    for shape in cutters:
        host = next(host for host in shape.cut_into if host.deleted_line is None)
        naming.give(shape, hashed(f"{identity_of(host, naming)}-cut-", shape, naming.claimed), shape.created_line)
    return naming
