"""What a change reaches, answered by the kernel, and what it cannot see.

There is exactly one propagation rule in this system and it lives in
``StateRecord.closure``. This module calls it and arranges the answer; it adds
no heuristic, reads no geometry, and never says "probably affected". A studio
that inferred impact of its own would be a second, quieter model of the design,
disagreeing with the runner in ways nobody could audit.

The honest half matters as much as the closure. A record declares dependencies
between the things somebody wrote a relation or an expression for, and says
nothing at all about the rest — on the villa today, that is every one of its 41
components. Reporting an empty propagation without reporting that blindness
would let an empty list read as "nothing else is affected", when what it means
is "this record was never asked".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from archflow.state.state_record import StateRecord

from .projection import StateProjection

NO_EDGES = "0 dependency edges: impact closure is direct-only"


@dataclass(frozen=True, slots=True)
class ImpactLock:
    """A locked parameter the change would reach, and whose lock it is."""

    ref: str
    authority: str


@dataclass(frozen=True, slots=True)
class Impact:
    """One change's reach: the kernel's closure, split by what it means.

    ``conflicts`` is the whole closure intersected with ``protected``, target
    included. There is one definition of conflict here and everything else
    derives from it: protecting the very thing you are changing is a conflict
    in exactly the sense protecting something downstream is, and reporting the
    second while staying silent about the first would make the emptier answer
    the more alarming one.
    """

    direct: tuple[str, ...]
    propagated: tuple[str, ...]
    protected: tuple[str, ...]
    conflicts: tuple[str, ...]
    locks: tuple[ImpactLock, ...]
    unknown_coverage: tuple[str, ...]
    honesty: tuple[str, ...]


def impact(
    projection: StateProjection,
    target_ref: str | Sequence[str],
    protected: Sequence[str],
    *,
    successor: StateRecord | None = None,
) -> Impact:
    """The closure of one prefixed ref, with the protections it runs into.

    ``target_ref`` and every protected ref are already prefixed
    (``entity:<id>``, ``parameter:<key>``): the kernel's edges carry prefixed
    refs and an unprefixed one matches nothing, so a ref is never re-shaped
    here to make it match.
    """

    direct = (target_ref,) if isinstance(target_ref, str) else tuple(sorted(set(target_ref)))
    closure = set(projection.record.closure(direct))
    if successor is not None:
        closure.update(successor.closure(direct))
    propagated = tuple(sorted(closure - set(direct)))
    protected_refs = tuple(sorted(set(protected)))
    unknown = _unknown_coverage(projection)
    return Impact(
        direct=direct,
        propagated=propagated,
        protected=protected_refs,
        # The whole closure, not just what it propagated to: a change collides
        # with a protection on its own target as squarely as with one further
        # down.
        conflicts=tuple(sorted(set(closure) & set(protected_refs))),
        locks=tuple(
            ImpactLock(ref=parameter.ref, authority=parameter.lock_authority)
            for parameter in projection.parameters
            if parameter.lock_authority and parameter.ref in closure
        ),
        unknown_coverage=unknown,
        honesty=_honesty(projection, unknown=unknown),
    )


def _unknown_coverage(projection: StateProjection) -> tuple[str, ...]:
    """The components no dependency edge mentions, in id order.

    A component the record never wired to anything is not a component nothing
    can affect: it is one nobody has said anything about. The two are reported
    apart because only the second is a reason to go and author something.
    """

    touched = {
        ref
        for edge in projection.edges
        for ref in (edge.upstream_ref, edge.downstream_ref)
    }
    return tuple(
        sorted(
            entity.entity_id
            for entity in projection.record.entities_of("Component@1")
            if f"entity:{entity.entity_id}" not in touched
        )
    )


def _honesty(
    projection: StateProjection,
    *,
    unknown: tuple[str, ...],
) -> tuple[str, ...]:
    """The lines the UI shows verbatim next to the closure it just drew."""

    lines: list[str] = []
    if not projection.edges:
        lines.append(NO_EDGES)
    if unknown:
        lines.append(
            f"{len(unknown)} components appear in no dependency edge; their "
            "impact is unknown, not zero"
        )
    return tuple(lines)
