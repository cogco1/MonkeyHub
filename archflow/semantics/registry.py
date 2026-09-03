"""Resolve a semantic string to registered ids, or say what it is closest to.

Accepted forms for a component's ``semantic_kind``:

- a registered id (``role.access``, ``condition.threshold``);
- several ids joined with ``+`` (``role.weather_enclosure+role.opening_host``);
- a registered alias of one term (``support``, ``入口``);
- a registered compound phrase (the phrases existing records were authored with).

Anything else resolves to ``None`` and the record refuses it, naming the nearest
registered ids. New vocabulary is added to ``roles.py`` / ``conditions.py`` with a
reason in the change that adds it; a compound phrase is added to
``COMPOUND_PHRASES`` only for records that already carry it.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from archflow.semantics.conditions import CONDITION_IDS, CONDITIONS
from archflow.semantics.roles import ROLE_IDS, ROLES

# Phrases authored before the registry existed, decomposed once into registered ids.
COMPOUND_PHRASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "whole-building": (("role.whole",), ()),
    "site-support": (("role.structural_support",), ()),
    "enclosure-and-load-distribution": (("role.weather_enclosure", "role.load_transfer"), ()),
    "weather-enclosure-and-opening-host": (("role.weather_enclosure", "role.opening_host"), ()),
    "vertical-support": (("role.structural_support",), ()),
    "horizontal-load-transfer": (("role.load_transfer",), ()),
    "column-to-entablature-transfer": (("role.load_transfer",), ("condition.interface",)),
    "hall-to-drum-load-transfer": (("role.load_transfer",), ("condition.interface",)),
    "wall-to-roof-load-transfer": (("role.load_transfer",), ("condition.interface",)),
    "horizontal-support-and-level-datum": (("role.structural_support", "role.level_datum"), ()),
    "vertical-support-and-stair-host": (("role.structural_support", "role.stair_host"), ()),
    "dome-support-and-light-transition": (("role.structural_support",), ("condition.light_transition",)),
    "drum-edge-weathering-and-load-spread": (("role.weather_enclosure", "role.load_transfer"), ("condition.edge",)),
    "facade-weathering-and-course-definition": (("role.weather_enclosure", "role.course_articulation"), ("condition.course",)),
    "weather-shedding-and-main-roof-interface": (("role.weather_enclosure",), ("condition.interface",)),
    "weathered-light-termination": (("role.weather_enclosure", "role.daylight"), ("condition.light_transition",)),
    "central-hall-cover": (("role.cover",), ()),
    "central-representation-and-vertical-light": (("role.representation", "role.daylight"), ()),
    "principal-use": (("role.habitation",), ()),
    "service-use-and-vault-support": (("role.service", "role.structural_support"), ()),
    "habitation-and-stair-access": (("role.habitation", "role.vertical_circulation"), ()),
    "storage-and-roof-support": (("role.storage", "role.structural_support"), ()),
    "arrival-and-buttress": (("role.access", "role.buttress"), ("condition.threshold",)),
    "controlled-entry": (("role.access",), ("condition.threshold",)),
    "portico-to-hall-access": (("role.access",), ("condition.threshold",)),
    "under-stair-ground-access": (("role.access",), ("condition.threshold",)),
    "vertical-circulation": (("role.vertical_circulation",), ()),
    "vertical-circulation-reservation": (("role.reservation", "role.vertical_circulation"), ()),
    "internal-vertical-circulation": (("role.vertical_circulation",), ()),
    "daylight-view-and-ventilation": (("role.daylight", "role.view", "role.ventilation"), ()),
    "attic-daylight": (("role.daylight",), ()),
    "mezzanine-daylight": (("role.daylight",), ()),
    "service-access-daylight": (("role.service", "role.daylight"), ()),
    "vertical-light-opening": (("role.daylight",), ("condition.void",)),
    "gable-enclosure": (("role.weather_enclosure",), ()),
    "weather-enclosure": (("role.weather_enclosure",), ()),
    "course-articulation": (("role.course_articulation",), ("condition.course",)),
    "floor-interface-and-transfer": (("role.load_transfer",), ("condition.interface",)),
    "stair-to-floor-interface": (("role.load_transfer",), ("condition.interface",)),
    "landing-load-path-and-undercroft": (("role.load_transfer", "role.undercroft"), ()),
    "roof-to-main-block-interface": (("role.load_transfer",), ("condition.interface",)),
}

# An alias may stand for one role and one condition at once ("entry" is the access role and
# the threshold condition); two terms of the same table may not share an alias.
_ALIASES: dict[str, tuple[str, ...]] = {}
for _table in (ROLES, CONDITIONS):
    _seen_in_table: dict[str, str] = {}
    for _term in _table:
        for _alias in _term.aliases:
            _key = _alias.lower()
            if _key in _seen_in_table:
                raise ValueError(f"semantic alias {_alias!r} is registered under both {_seen_in_table[_key]} and {_term.id}")
            _seen_in_table[_key] = _term.id
            _ALIASES[_key] = _ALIASES.get(_key, ()) + (_term.id,)
_KNOWN: frozenset[str] = frozenset(ROLE_IDS | CONDITION_IDS)


@dataclass(frozen=True, slots=True)
class SemanticResolution:
    roles: tuple[str, ...]
    conditions: tuple[str, ...]


def resolve_semantic_kind(text: str) -> SemanticResolution | None:
    """The registered ids a semantic string stands for, or None."""

    if not isinstance(text, str) or not text.strip():
        return None
    key = text.strip()
    if key in COMPOUND_PHRASES:
        roles, conditions = COMPOUND_PHRASES[key]
        return SemanticResolution(roles, conditions)
    ids = [part.strip() for part in key.split("+")]
    resolved: list[str] = []
    for part in ids:
        if part in _KNOWN:
            resolved.append(part)
        elif part.lower() in _ALIASES:
            resolved.extend(_ALIASES[part.lower()])
        else:
            return None
    return SemanticResolution(
        tuple(dict.fromkeys(i for i in resolved if i in ROLE_IDS)),
        tuple(dict.fromkeys(i for i in resolved if i in CONDITION_IDS)),
    )


def suggest_semantic(text: str, limit: int = 3) -> tuple[str, ...]:
    """The nearest registered ids, for the refusal message."""

    candidates: dict[str, str] = {i: i for i in _KNOWN}
    candidates.update({alias: "+".join(ids) for alias, ids in _ALIASES.items()})
    candidates.update({k: "+".join(v[0] + v[1]) for k, v in COMPOUND_PHRASES.items()})
    near = difflib.get_close_matches(str(text).strip().lower(), list(candidates), n=limit, cutoff=0.4)
    out: list[str] = []
    for hit in near:
        target = candidates[hit]
        if target not in out:
            out.append(target)
    return tuple(out)


def registered_ids() -> tuple[str, ...]:
    return tuple(sorted(_KNOWN))
