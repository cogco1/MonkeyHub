"""Facets: meaning added to a component's stable identity once it is known (L3).

Canonical keys are namespaced (``architectural.role``, ``material.name``, ...).
A facet is not an entity type and not a role or a condition (ADR-006 keeps
entity, role and condition apart): ``architectural.role`` names what a
component has become for a capability to read (a wall, a slab, a stair), not
what it does for the building (``roles.py``) or what spatial condition it
forms (``conditions.py``).

Facets live only on ``Component@1.fields.facets`` (spec
docs/design/construction-api.md §3.4): the same stable identity may be a
block, later a wall, later a niche, and meaning accrues on it without ever
touching its geometry. Adding, changing or removing a facet upserts the
component alone, so every element, object, datum and dependency edge stays
byte-identical (D-419-0).

An enumerated key accepts one of its closed set of values; a free-text key
(``material.name``, ``fabrication.method``) accepts ``FREE_TEXT_MIN``-``FREE_TEXT_MAX``
characters of the architect's own words. Unknown keys and unregistered values
are refused by the State Record, naming the nearest key or the allowed
values; ``suggest_facet_key`` finds the near miss for an unknown key.
"""

from __future__ import annotations

import difflib
from types import MappingProxyType
from typing import Mapping

FREE_TEXT_MIN = 1
FREE_TEXT_MAX = 120

# A facet key maps to its closed set of accepted values, or None if it takes
# free text (FREE_TEXT_MIN-FREE_TEXT_MAX characters). Spec §3.4 table, exactly.
FACETS: Mapping[str, tuple[str, ...] | None] = MappingProxyType({
    "architectural.role": (
        "wall", "slab", "floor", "roof", "column", "beam", "stair", "ramp", "door",
        "window", "opening", "railing", "ceiling", "partition", "canopy", "screen",
        "foundation", "space", "furniture", "site",
    ),
    "architectural.enclosure": ("exterior", "interior"),
    "structural.role": ("load_bearing", "non_load_bearing", "bracing"),
    "material.name": None,
    "fabrication.method": None,
})

FACET_KEYS: frozenset[str] = frozenset(FACETS)


def allowed_facet_values(key: str) -> tuple[str, ...] | None:
    """The closed set of values a registered ``key`` accepts, or None if it takes free text."""

    return FACETS[key]


def suggest_facet_key(key: str, limit: int = 3) -> tuple[str, ...]:
    """The nearest registered facet keys, for the refusal of an unknown one."""

    return tuple(difflib.get_close_matches(str(key).strip().lower(), sorted(FACET_KEYS), n=limit, cutoff=0.4))
