"""Project conventions: the human names and the compass a project declares
for itself, read from ``PROJECT.md`` at the project root.

The markdown is the one place a project may say things a typed record does
not: what people call a component ("柱子" for portico-columns), which world
direction is north, the units and frame, the open questions. It never holds
an element id's value, an editable field, an edge, a lock, a binding or an
authority - those are the record's - and the resolver reads it only for
names and directions. A convention names a component the record does not
declare, it is ignored and said so.

Format (strict, so it can be read without guessing):

    ## Names
    - 柱子, columns => portico-columns
    - 屋顶, roof => portico-roofs

    ## Compass
    - north: +y
    - east: +x

    state digest: <sha256>

Aliases are matched as whole words (Latin) or substrings (CJK) by the
resolver; the compass maps the four cardinal words to world XY directions
(+x, -x, +y, -y) so "left" and "right" can be read from a camera.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Mapping

PROJECT_MARKDOWN = "PROJECT.md"

_AXES: Mapping[str, tuple[float, float]] = {
    "+x": (1.0, 0.0),
    "-x": (-1.0, 0.0),
    "+y": (0.0, 1.0),
    "-y": (0.0, -1.0),
}
_CARDINALS = ("north", "east", "south", "west")


@dataclass(frozen=True, slots=True)
class ProjectConventions:
    aliases: Mapping[str, str] = field(default_factory=dict)
    compass: Mapping[str, tuple[float, float]] | None = None
    state_digest: str | None = None
    source: str | None = None
    honesty: tuple[str, ...] = ()


def parse_conventions(text: str, *, declared_components: set[str] | None = None) -> ProjectConventions:
    aliases: dict[str, str] = {}
    compass: dict[str, tuple[float, float]] = {}
    honesty: list[str] = []
    digest: str | None = None
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        match = re.match(r"^state digest:\s*([0-9a-f]{64})\s*$", line, re.IGNORECASE)
        if match:
            digest = match.group(1).lower()
            continue
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        if section == "names":
            head, sep, component = item.partition("=>")
            if not sep:
                head, sep, component = item.partition("→")
            if not sep:
                honesty.append(f"names line without '=>': {item!r}")
                continue
            component = component.strip()
            if declared_components is not None and component not in declared_components:
                honesty.append(f"alias names {component!r}, which the record does not declare; ignored")
                continue
            for phrase in head.split(","):
                phrase = phrase.strip()
                if phrase:
                    aliases[phrase] = component
        elif section == "compass":
            word, sep, axis = item.partition(":")
            word = word.strip().lower()
            axis = axis.strip().lower()
            if not sep or word not in _CARDINALS or axis not in _AXES:
                honesty.append(f"compass line not read: {item!r} (expected '<north|east|south|west>: <+x|-x|+y|-y>')")
                continue
            compass[word] = _AXES[axis]
    if compass:
        # The two axes given fix the other two; a compass with one word is
        # not a compass.
        if "north" in compass and "south" not in compass:
            compass["south"] = (-compass["north"][0], -compass["north"][1])
        if "east" in compass and "west" not in compass:
            compass["west"] = (-compass["east"][0], -compass["east"][1])
        if "south" in compass and "north" not in compass:
            compass["north"] = (-compass["south"][0], -compass["south"][1])
        if "west" in compass and "east" not in compass:
            compass["east"] = (-compass["west"][0], -compass["west"][1])
        if "north" in compass and "east" not in compass:
            n = compass["north"]
            compass["east"] = (n[1], -n[0])
            compass["west"] = (-n[1], n[0])
        if set(compass) != set(_CARDINALS):
            honesty.append("compass incomplete; direction words are not read")
            compass = {}
    return ProjectConventions(
        aliases=aliases,
        compass=compass or None,
        state_digest=digest,
        honesty=tuple(honesty),
    )


def project_conventions(binding) -> ProjectConventions:
    """The bound project's conventions, or none when it has no PROJECT.md."""

    path = Path(binding.project_dir) / PROJECT_MARKDOWN
    if not path.is_file():
        return ProjectConventions(source=None)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ProjectConventions(source=str(PROJECT_MARKDOWN), honesty=(f"{PROJECT_MARKDOWN} could not be read: {exc}",))
    declared = None
    try:
        from .projection import project_state

        projection = project_state(binding, require_view=False)
        declared = {entity.entity_id for entity in projection.record.entities_of("Component@1")}
    except Exception:  # noqa: BLE001 - conventions must not fail a request over the record
        declared = None
    conventions = parse_conventions(text, declared_components=declared)
    return ProjectConventions(
        aliases=conventions.aliases,
        compass=conventions.compass,
        state_digest=conventions.state_digest,
        source=PROJECT_MARKDOWN,
        honesty=conventions.honesty,
    )
