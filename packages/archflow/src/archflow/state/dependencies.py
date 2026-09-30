"""The canonical design dependency: one edge, what may propagate along it, and the one closure.

A ``DependencyEdge`` says that one ref depends on another, which relation
declares it, which ref states it and what a change may carry along it.
``downstream_closure`` is the one walk over such edges: everything a change to
some refs reaches along the edges whose effect propagates, those refs included,
in sorted order. The State Record's closures, the decision operator's
invalidation closure and the Studio frame all read it; a caller that needs
another subset of effects or a bound of its own says so at its call instead of
walking the graph itself.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.canonical import canonical_json
from archflow.contracts.fields import (
    enum_member,
    mapping as _mapping,
    require_logical_ref,
    unbounded_text,
)


class DependencyEffect(StrEnum):
    """What may propagate along one named dependency."""

    INVALIDATES = "invalidates"
    REQUIRES_REVALIDATION = "requires_revalidation"
    BLOCKS = "blocks"
    SUPPORTS_ONLY = "supports_only"


#: The effects a change travels along. ``BLOCKS`` orders obligations and
#: ``SUPPORTS_ONLY`` records support; neither carries a change downstream.
PROPAGATING_EFFECTS: tuple[DependencyEffect, ...] = (
    DependencyEffect.INVALIDATES,
    DependencyEffect.REQUIRES_REVALIDATION,
)


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    upstream_ref: str
    downstream_ref: str
    relation: str
    source_ref: str
    effect: DependencyEffect = DependencyEffect.SUPPORTS_ONLY

    def __post_init__(self) -> None:
        require_logical_ref(self.upstream_ref, "dependency upstream_ref")
        require_logical_ref(self.downstream_ref, "dependency downstream_ref")
        if self.upstream_ref == self.downstream_ref:
            raise ValueError("dependency cannot be a self edge")
        unbounded_text(self.relation, "dependency relation")
        require_logical_ref(self.source_ref, "dependency source_ref")
        if not isinstance(self.effect, DependencyEffect):
            raise TypeError(
                "dependency effect must be a DependencyEffect"
            )
        if (
            self.effect in PROPAGATING_EFFECTS
            and self.downstream_ref.startswith(
                ("obligation:", "commitment:")
            )
        ):
            raise ValueError(
                "normative refs require blocking or support-only edges"
            )
        if (
            self.effect is DependencyEffect.BLOCKS
            and (
                not self.upstream_ref.startswith("obligation:")
                or not self.downstream_ref.startswith("obligation:")
            )
        ):
            raise ValueError(
                "blocking dependency must connect obligation refs"
            )

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.upstream_ref,
            self.downstream_ref,
            self.relation,
            self.effect.value,
        )

    @property
    def ref(self) -> str:
        digest = hashlib.sha256(
            canonical_json(self.identity).encode("utf-8")
        ).hexdigest()
        return f"dependency:{digest}"

    def to_dict(self) -> dict[str, str]:
        return {
            "upstream_ref": self.upstream_ref,
            "downstream_ref": self.downstream_ref,
            "relation": self.relation,
            "source_ref": self.source_ref,
            "effect": self.effect.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> DependencyEdge:
        payload = _mapping(value, "dependency edge")
        expected = {
            "upstream_ref",
            "downstream_ref",
            "relation",
            "source_ref",
            "effect",
        }
        if set(payload) != expected:
            raise ValueError("dependency edge schema drifted")
        return cls(
            upstream_ref=payload["upstream_ref"],
            downstream_ref=payload["downstream_ref"],
            relation=payload["relation"],
            source_ref=payload["source_ref"],
            effect=enum_member(
                payload["effect"],
                DependencyEffect,
                "dependency effect",
            ),
        )


def downstream_closure(
    edges: Iterable[DependencyEdge],
    seeds: Iterable[str],
    *,
    effects: Iterable[DependencyEffect] = PROPAGATING_EFFECTS,
) -> tuple[str, ...]:
    """Everything a change to ``seeds`` reaches: the seeds and every ref
    downstream of them along ``edges`` whose effect is one of ``effects``,
    sorted."""

    return downstream_closures(edges, (seeds,), effects=effects)[0]


def downstream_closures(
    edges: Iterable[DependencyEdge],
    seed_groups: Iterable[Iterable[str]],
    *,
    effects: Iterable[DependencyEffect] = PROPAGATING_EFFECTS,
) -> tuple[tuple[str, ...], ...]:
    """``downstream_closure`` of each seed group, in group order, over one graph.

    The graph is built once for all the groups, and each closure is
    independent of the others.
    """

    carried = frozenset(effects)
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if edge.effect in carried:
            adjacency.setdefault(edge.upstream_ref, set()).add(edge.downstream_ref)
    closures: list[tuple[str, ...]] = []
    for seeds in seed_groups:
        seen = set(seeds)
        queue = deque(seen)
        while queue:
            for downstream in adjacency.get(queue.popleft(), ()):
                if downstream not in seen:
                    seen.add(downstream)
                    queue.append(downstream)
        closures.append(tuple(sorted(seen)))
    return tuple(closures)
