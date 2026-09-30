"""What changed between two revisions of a cut plan's view recipe, and what kind of correction that is.

A correction's evidence is one drawing's revision chain: each revision names the
one it continued, and the difference between their two view recipes
(``recipe_diff``) says what changed. Its class (``classify``) is a pure function
of that difference and of why the later page replaced the earlier. Nothing here
reads a project, a registration or a decision: the Project Runtime pairs the
retained revisions and offers the recipe corrections people repeat (#519).
"""

from __future__ import annotations

from typing import Any, Mapping

# What a correction can be, in the order ``classify`` asks. Only a recipe
# correction is ever offered as memory: a compiler defect and a semantic rule
# are the drawing owner's to fix, and a local override stays on its drawing.
CLASSES = ("compiler_defect", "semantic_rule", "recipe", "local_override")
# Recipe lists compared by the ids of the objects they hold, not by position.
_KEYED = ("dimensions", "dressing")
_MATERIAL_RULES = "graphics.hatch.byMaterial"


def recipe_diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, list[Any]]:
    """What changed from one view recipe to the next, as ``{dotted path: [old, new]}`` in path order.

    Mappings are compared key by key and every other value whole; a value
    absent on one side is None there. Three kinds of entry name what a list
    holds rather than a position in it: ``hiddenObjectIds.<id>`` is [was
    hidden, is hidden]; ``dimensions.<id>`` and ``dressing.<id>`` are the
    whole object where it was added or removed (None on the other side) and
    otherwise one entry per changed field, such as
    ``dimensions.<id>.placement.offsetMm``; and
    ``graphics.hatch.byMaterial.<material>`` is that material's whole rule.
    The number of entourage objects changed by the added ones less the
    removed.
    """

    diff: dict[str, list[Any]] = {}
    _compare("", before or {}, after or {}, diff)
    return dict(sorted(diff.items()))


def _keyed(items: Any) -> dict[str, Any] | None:
    """A recipe list by its objects' own ids; None when it cannot be read so."""

    if items is None:
        return {}
    if not isinstance(items, list) or not all(isinstance(item, Mapping) and isinstance(item.get("id"), str)
                                              for item in items):
        return None
    keyed = {item["id"]: item for item in items}
    return keyed if len(keyed) == len(items) else None


def _compare(path: str, old: Any, new: Any, diff: dict[str, list[Any]]) -> None:
    if old == new:
        return
    if path == "hiddenObjectIds" and all(value is None or (isinstance(value, list) and all(
            isinstance(name, str) for name in value)) for value in (old, new)):
        was, now = set(old or ()), set(new or ())
        for name in was ^ now:
            diff[f"{path}.{name}"] = [name in was, name in now]
        return
    was, now = (_keyed(old), _keyed(new)) if path in _KEYED else (None, None)
    if was is not None and now is not None:
        for name in was.keys() | now.keys():
            if name in was and name in now:
                _compare(f"{path}.{name}", was[name], now[name], diff)
            else:
                diff[f"{path}.{name}"] = [was.get(name), now.get(name)]
        return
    if (old is None or isinstance(old, Mapping)) and (new is None or isinstance(new, Mapping)):
        old, new = old or {}, new or {}
        for key in old.keys() | new.keys():
            name = f"{path}.{key}" if path else key
            if path == _MATERIAL_RULES:
                if old.get(key) != new.get(key):
                    diff[name] = [old.get(key), new.get(key)]
            else:
                _compare(name, old.get(key), new.get(key), diff)
        return
    diff[path] = [old, new]


def classify(diff: Mapping[str, Any], cleanup: Mapping[str, Any] | None, cause: str | None) -> str:
    """The kind of correction between two revisions: the first of these that holds (05 3.2(B)).

    ``compiler_defect``: the page followed its source (``cause`` is
    ``source``), which corrects nothing, or it only hid objects the cleanup
    flagged. ``semantic_rule``: a material's hatch or poché rule changed.
    ``recipe``: a graphics value changed - a pen, the hatch spacing, the fade
    beyond the cut - or how many entourage objects the drawing holds.
    ``local_override``: anything else - an object hidden or shown again,
    entourage moved, flipped, scaled or swapped one for one, the crop, scale
    or cut, a dimension.

    ``cleanup`` is the after revision's cleanup report as its receipt keeps
    it. That report counts lines per rule and names no object (03 C1), so V0
    cannot tell that a hidden object was one the cleanup flagged: every hide
    is a local override, and only a source rebuild is a compiler defect.
    """

    if cause == "source":
        return "compiler_defect"
    if any(key.startswith(_MATERIAL_RULES + ".") for key in diff):
        return "semantic_rule"
    if any(key.startswith("graphics.") for key in diff) or _entourage_change(diff):
        return "recipe"
    return "local_override"


def _entourage_change(diff: Mapping[str, Any]) -> int:
    """How many entourage objects a revision added, less how many it removed."""

    return sum((new is not None) - (old is not None) for key, (old, new) in diff.items()
               if key.startswith("dressing.") and (isinstance(old, Mapping) or isinstance(new, Mapping)))
