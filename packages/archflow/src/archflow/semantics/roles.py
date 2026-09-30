"""Roles: what a component does for the building. Canonical ids are ``role.<name>``.

A role is not an entity type and not a spatial condition: a wall is an entity that
may hold ``role.weather_enclosure`` and ``role.opening_host`` at once. Aliases are
the words a person or a model may use; they resolve to the id and never enter
canonical state themselves.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SemanticTerm:
    id: str
    meaning: str
    aliases: tuple[str, ...] = ()


ROLES: tuple[SemanticTerm, ...] = (
    SemanticTerm("role.structural_support", "carries vertical load down to what is below it", ("support", "vertical-support", "site-support", "bearing", "承重", "支撑")),
    SemanticTerm("role.load_transfer", "passes load from one member or level to another", ("load-transfer", "transfer", "load-distribution", "load-spread", "load-path", "传力")),
    SemanticTerm("role.buttress", "resists lateral thrust from an arch, vault or dome", ("buttress", "扶壁")),
    SemanticTerm("role.weather_enclosure", "keeps weather out of the spaces it bounds", ("weather-enclosure", "enclosure", "weathering", "weather-shedding", "围护", "防水")),
    SemanticTerm("role.opening_host", "carries openings cut through it (windows, doors)", ("opening-host", "window-host", "door-host", "开洞载体")),
    SemanticTerm("role.stair_host", "carries a stair", ("stair-host",)),
    SemanticTerm("role.cover", "covers a space from above", ("cover", "roof", "覆盖")),
    SemanticTerm("role.level_datum", "publishes a level other elements are set to", ("level-datum", "datum", "标高基准")),
    SemanticTerm("role.course_articulation", "articulates the facade into courses or bands", ("course-articulation", "course-definition", "articulation", "分层线脚")),
    SemanticTerm("role.daylight", "admits daylight", ("daylight", "light", "光")),
    SemanticTerm("role.ventilation", "admits air", ("ventilation", "通风")),
    SemanticTerm("role.view", "gives a view out", ("view", "视野")),
    SemanticTerm("role.vertical_circulation", "moves people between levels", ("vertical-circulation", "stairs", "stair-access", "circulation", "竖向交通")),
    SemanticTerm("role.access", "gives access into or between spaces", ("access", "entry", "entrance", "arrival", "入口", "进入")),
    SemanticTerm("role.habitation", "principal occupied use", ("principal-use", "habitation", "居住", "主要使用")),
    SemanticTerm("role.service", "service use or service access", ("service-use", "service-access", "service", "服务")),
    SemanticTerm("role.storage", "storage use", ("storage", "储藏")),
    SemanticTerm("role.representation", "representational or ceremonial presence", ("representation", "ceremonial", "礼仪", "象征")),
    SemanticTerm("role.reservation", "space held for an element not yet designed", ("reservation", "reserved", "预留")),
    SemanticTerm("role.undercroft", "the space beneath a raised floor or stair", ("undercroft", "架空")),
    SemanticTerm("role.whole", "the building as a whole", ("whole-building", "building", "整体")),
)

ROLE_IDS: frozenset[str] = frozenset(term.id for term in ROLES)
