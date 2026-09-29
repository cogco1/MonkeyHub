"""L4: a technical domain asks for the semantics it needs, never guesses them.

A domain (``structure``, ``envelope``, ...) is a reader, not an oracle: it
looks only at ``Component@1.fields.facets`` (L3, ``archflow/semantics/facets.py``,
read with ``component_facets``) and, when the facets it needs are missing,
answers an explicit enrichment request instead of inferring anything from
shape, producer or names (spec docs/2026-09-28-construction-api.md §3.6).
This is the honest entry gate every domain tool calls before it runs; there
is no structural or thermal engine here, only the question "do I know enough
about this entity to evaluate it, and if not, what is missing and why".

``DOMAINS`` names each domain's read set (the ``architectural.role`` values
it looks at) and its needs (the facet keys it requires once it does look).
``readiness`` walks every ``Component@1`` that owns at least one
``Element@1`` — a component with no element is not geometry yet, and a
domain has nothing to evaluate on it — except a construction helper: a
component whose every element another element currently names in
``references.voids`` is a hidden cutter, whose cut is part of its host's
form, and asking what it is would force meaning onto a helper (#419).
Once uncut it is ordinary geometry and is considered again. ``readiness``
sorts each component it considers into one of three
outcomes: its role is outside the domain's read set (not read, no request);
it carries no ``architectural.role`` at all (a request naming that, because
a domain reading shape alone cannot even tell whether the entity is its
business); or its role is read and some needed facet is missing (a request
naming exactly the missing keys). A component with a read role and every
needed facet is simply read. An unknown domain name is refused by
``DomainUnknown``, naming the domains that do exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from archflow.semantics.facets import allowed_facet_values
from archflow.state.state_record import StateRecord, component_facets


@dataclass(frozen=True)
class DomainNeeds:
    """One technical domain's read set and its facet needs, once it reads.

    ``reads``: the ``architectural.role`` values this domain looks at; a role
    outside this set is none of the domain's business and is never read or
    asked about. ``needs``: the facet keys the domain requires on anything it
    reads, in the order the spec table states them. ``undecided``: what the
    domain cannot tell about an entity that carries no ``architectural.role``
    at all — the phrase completing "the ``{domain}`` domain cannot tell
    whether ``{id}`` ..." in that request's reason.
    """

    reads: frozenset[str]
    needs: tuple[str, ...]
    undecided: str


DOMAINS: Mapping[str, DomainNeeds] = MappingProxyType({
    "structure": DomainNeeds(
        reads=frozenset({"wall", "column", "beam", "slab", "floor", "roof", "foundation", "stair"}),
        needs=("structural.role", "material.name"),
        undecided="carries load",
    ),
    "envelope": DomainNeeds(
        reads=frozenset({"wall", "roof", "slab", "floor", "window", "door"}),
        needs=("architectural.enclosure", "material.name"),
        undecided="belongs to the envelope",
    ),
})


class DomainUnknown(KeyError):
    """Refused: no domain by this name is registered, naming the ones that are."""

    def __init__(self, domain: str) -> None:
        self.domain = domain
        super().__init__(f"unknown domain {domain!r}; known domains: {', '.join(sorted(DOMAINS))}")

    def __str__(self) -> str:  # KeyError.__str__ would re-quote the message via repr
        return str(self.args[0])


def describe(domain: str) -> dict[str, Any]:
    """One domain's own description: what it reads, what it needs, and the facets it asks about.

    Used for ``GET /api/domains`` (every domain, described) with no record in
    hand at all — this is the registry's own answer, not a projection.
    """

    spec = _spec(domain)
    return {
        "domain": domain,
        "reads": sorted(spec.reads),
        "needs": list(spec.needs),
        "known_facets": _known_facets({"architectural.role", *spec.needs}),
    }


def readiness(record: StateRecord, domain: str) -> dict[str, Any]:
    """Whether ``domain`` can evaluate this record's geometry-bearing components now.

    Every ``Component@1`` that owns at least one ``Element@1`` is considered;
    one with no element at all is not geometry yet and is neither read nor
    asked about, and neither is one whose every element another element
    names in ``references.voids`` (a hidden cutter, considered again once
    uncut). Each considered component becomes exactly one of: read (its
    role is in the domain's set and every needed facet is present); a request
    naming ``architectural.role`` (no role at all — the domain cannot tell
    whether it is even its business); a request naming the missing facet keys
    (a read role short of what the domain needs); or nothing at all (a role
    outside the domain's read set, none of its business).

    Raises ``DomainUnknown`` for a domain name not in ``DOMAINS``.
    """

    spec = _spec(domain)
    owners = _considered_components(record)
    reads: list[str] = []
    requests: list[dict[str, Any]] = []

    for entity in record.entities_of("Component@1"):
        if entity.entity_id not in owners:
            continue
        facets = component_facets(entity)
        role = facets.get("architectural.role")
        if role is None:
            requests.append({
                "id": entity.entity_id,
                "missing": ["architectural.role"],
                "reason": (
                    f"no architectural.role: the {domain} domain cannot tell "
                    f"whether {entity.entity_id} {spec.undecided}"
                ),
            })
            continue
        if role not in spec.reads:
            continue
        missing = sorted(key for key in spec.needs if key not in facets)
        if missing:
            requests.append({
                "id": entity.entity_id,
                "missing": missing,
                "reason": f"{domain} reads {role} and needs {_and_join(missing)} to evaluate it",
            })
        else:
            reads.append(entity.entity_id)

    reads.sort()
    requests.sort(key=lambda request: request["id"])
    return {
        "domain": domain,
        "status": "enrichment_required" if requests else "ready",
        "reads": reads,
        "requests": requests,
        "known_facets": _known_facets({"architectural.role", *spec.needs}),
    }


def _spec(domain: str) -> DomainNeeds:
    try:
        return DOMAINS[domain]
    except KeyError:
        raise DomainUnknown(domain) from None


def _considered_components(record: StateRecord) -> frozenset[str]:
    """Every ``Component@1`` id that owns at least one ``Element@1`` other than a hidden cutter.

    An element's owner is its ``fields.component_id`` when it states one,
    else its ``parent_id`` — the same fallback ``element_rows_of`` and the
    Studio's own component-id resolution use, so a domain and the geometry
    compiler agree on what an element belongs to. A cutter is an element
    another element names in ``references.voids`` now; a component with
    an ordinary element beside its cutters is still considered.
    """

    cutters = _cutters(record)
    owners: set[str] = set()
    for element in record.entities_of("Element@1"):
        owner = element.fields.get("component_id") or element.parent_id
        if isinstance(owner, str) and owner and element.entity_id not in cutters:
            owners.add(owner)
    return frozenset(owners)


def _cutters(record: StateRecord) -> frozenset[str]:
    """The elements another element names in ``references.voids``: kept in the model hidden while they cut."""

    cutters: set[str] = set()
    for element in record.entities_of("Element@1"):
        references = element.fields.get("references")
        voids = references.get("voids") if isinstance(references, Mapping) else None
        if isinstance(voids, (list, tuple)):
            cutters.update(void for void in voids if isinstance(void, str) and void != element.entity_id)
    return frozenset(cutters)


def _known_facets(keys: Iterable[str]) -> dict[str, Any]:
    """``{key: allowed values or "free text"}`` for the facet keys a domain asks about."""

    out: dict[str, Any] = {}
    for key in sorted(keys):
        allowed = allowed_facet_values(key)
        out[key] = "free text" if allowed is None else list(allowed)
    return out


def _and_join(items: Sequence[str]) -> str:
    """``"a"``, ``"a and b"``, ``"a, b and c"`` — a reason sentence reads facet keys, not a repr."""

    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]
