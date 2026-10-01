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
characters of the architect's own words; a formatted key (``material.color``)
is text written in exactly one form (``FACET_FORMATS``), and
``canonical_facet_value`` gives the spelling the record keeps. Unknown keys,
unregistered values and text in another form are refused by the State Record,
naming the nearest key, the allowed values or the form; ``suggest_facet_key``
finds the near miss for an unknown key. A key added after the spec table
states why the registered keys could not say it (``FACET_REASONS``, ADR-006).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

FREE_TEXT_MIN = 1
FREE_TEXT_MAX = 120

# A facet key maps to its closed set of accepted values, or None if its value
# is text (FREE_TEXT_MIN-FREE_TEXT_MAX characters), free or in the one form
# FACET_FORMATS gives it. Spec §3.4 table, and the keys added since with
# their reasons in FACET_REASONS.
FACETS: Mapping[str, tuple[str, ...] | None] = MappingProxyType({
    "architectural.role": (
        "wall", "slab", "floor", "roof", "column", "beam", "stair", "ramp", "door",
        "window", "opening", "railing", "ceiling", "partition", "canopy", "screen",
        "foundation", "space", "furniture", "site",
    ),
    "architectural.enclosure": ("exterior", "interior"),
    "structural.role": ("load_bearing", "non_load_bearing", "bracing"),
    "material.name": None,
    # The material's base appearance: an sRGB colour declared beside
    # material.name. It is a colour and nothing more - no texture, finish
    # or physical parameter - and every component of one material carries
    # the same one or none (the State Record refuses anything else).
    "material.color": None,
    "fabrication.method": None,
})

FACET_KEYS: frozenset[str] = frozenset(FACETS)

# Why each key added after the spec table could not be said with the keys
# already registered (ADR-006: a new term carries its written reason).
FACET_REASONS: Mapping[str, str] = MappingProxyType({
    "material.color": (
        "material.name names what a material is, not how it looks, and no composition of the "
        "registered keys states a colour; a material's base appearance belongs to the design "
        "state (owner decision on #560, 2026-10-01)"
    ),
})


@dataclass(frozen=True, slots=True)
class FacetFormat:
    """The one written form of a formatted key: the pattern its stored text matches, and that form in words."""

    pattern: re.Pattern[str]
    words: str

    def holds(self, value: str) -> bool:
        return self.pattern.fullmatch(value) is not None


# A colour is written as it is declared, in either case, and kept in upper case.
_HEX_COLOR_ANY_CASE = re.compile(r"#[0-9A-Fa-f]{6}")

FACET_FORMATS: Mapping[str, FacetFormat] = MappingProxyType({
    "material.color": FacetFormat(re.compile(r"#[0-9A-F]{6}"), "an sRGB hex colour #RRGGBB"),
})


def allowed_facet_values(key: str) -> tuple[str, ...] | None:
    """The closed set of values a registered ``key`` accepts, or None if it takes text."""

    return FACETS[key]


def facet_format(key: str) -> FacetFormat | None:
    """The one form a formatted key's text is written in, or None for any other key."""

    return FACET_FORMATS.get(key)


def canonical_facet_value(key: str, value: str) -> str:
    """The spelling the record keeps: surrounding whitespace dropped, a hex colour in upper case.

    Only the case of an otherwise well-formed colour changes. Any other text
    comes back stripped and unchanged, and the record refuses it in the words
    of its form: nothing is guessed into a colour or out of a name.
    """

    text = value.strip()
    if key == "material.color" and _HEX_COLOR_ANY_CASE.fullmatch(text):
        return text.upper()
    return text


def material_color_rgb(value: str) -> tuple[int, int, int]:
    """A stored ``material.color`` as its three sRGB channels, 0-255."""

    if not FACET_FORMATS["material.color"].holds(value):
        raise ValueError(f"material.color {value!r} is not {FACET_FORMATS['material.color'].words}")
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def suggest_facet_key(key: str, limit: int = 3) -> tuple[str, ...]:
    """The nearest registered facet keys, for the refusal of an unknown one."""

    return tuple(difflib.get_close_matches(str(key).strip().lower(), sorted(FACET_KEYS), n=limit, cutoff=0.4))
