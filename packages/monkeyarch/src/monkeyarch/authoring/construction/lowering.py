"""A construction script lowered to design-state rows (#419, L1 -> L2).

The script's surviving shapes get their final ids here (the rules are
``identity``: ``name()``, then module variables, then ``<host>-cut-<12 hex>``
for an unnamed cutter and ``shape-<12 hex>`` for any other unnamed shape, the
digits hashed from the chain of statements that made it, so that running a
script again updates the same shapes), and each becomes one ``Component@1
<id>`` with one ``Element@1 <id>-body``.
Producers are chosen here and nowhere else: a plan extrusion is a prism, a
face a planar surface, a path a curve, a loft a loft. A ``get()`` handle
rewrites only its element's ``params`` and ``references``; ``delete`` removes
the component and its element. Cuts are the voids relation: the host element's
``references.voids`` names its cutters' elements. A new shape that redefines
existing geometry keeps what that geometry cut, unless the script uncuts it.

Before any row is written, the relations the result leaves - what cuts what,
what stands on what - are checked where the script touched them, including
against geometry of the record the script never reached, and refused at the
script line in construction words. Lowering runs under the script's deadline.
The report's bounds are what the model view will predict for the rows the
script leaves: the record's rows overlaid with the script's, produced for the
reported elements, the hosts that cut them and every element they mention
(supports, cutters, hosts) - never the whole project, which is neither applied
nor validated here. A cut whose result those predicted bounds cannot follow
would fail when the candidate is exported, so where the script made, changed,
cut or uncut its host or one of its cutters it is refused at the line of the
cut that made it.

``geometry_view`` is the other direction: what a record's geometry is, per
component, in the same words (form, bounds, cuts) and without producers.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

from archflow.state.geometry_program import DifferenceBoundsError
from archflow.state.state_record import StateRecord
from monkeyarch.capabilities.element_producers import (
    ElementProducerError,
    produce_rows,
    production_order,
    with_void_hosts,
)
from monkeyarch.construction.identity import ELEMENT_SUFFIX, Naming, identify, identity_of, made_by_construction
from monkeyarch.construction.script import (
    MAX_CUTTERS,
    MAX_ID,
    TIME_OUT,
    ConstructionError,
    Session,
    _statement_error,
    run_script,
)
from monkeyarch.construction.shapes import (
    _PRODUCTION_ERRORS,
    EDITABLE_PRODUCERS,
    Box,
    Drawn,
    ParamRef,
    RowShape,
    Shape,
    ShapeError,
    World,
    clean,
    datum_targets,
    lower_anchor,
    mentioned_elements,
    object_bounds,
    path_frame,
    short,
    union,
    within_reach,
)

_DOWNWARD = {"origin": [0.0, 0.0, 0.0], "xAxis": [1.0, 0.0, 0.0], "yAxis": [0.0, 0.0, 1.0], "normal": [0.0, -1.0, 0.0]}


@dataclass(frozen=True)
class ConstructionResult:
    """What a script proposes: rows to upsert and ids to remove, with a report the agent reads back."""

    entities: tuple[dict, ...]
    remove_entity_ids: tuple[str, ...]
    report: tuple[dict, ...]
    log: tuple[str, ...]
    summary: str
    line_of: Mapping[str, int] = field(default_factory=dict)


def _box_list(box: Box | None) -> list[list[float]] | None:
    return None if box is None else [list(box[0]), list(box[1])]


def _compact(ids: list[str]) -> str:
    """``w-1, w-2, w-3`` as ``w-1..3``; other ids as they are."""

    out, index = [], 0
    while index < len(ids):
        match = re.fullmatch(r"(.*)-(\d+)", ids[index])
        end = index
        if match:
            prefix, start = match.group(1), int(match.group(2))
            while end + 1 < len(ids) and ids[end + 1] == f"{prefix}-{start + end + 1 - index}":
                end += 1
        if end > index:
            out.append(f"{ids[index]}..{start + end - index}")
        else:
            out.append(ids[index])
        index = end + 1
    return ", ".join(out)


class _Lowering:
    def __init__(self, session: Session, lines: list[str], root: str) -> None:
        self.session = session
        self.world = session.world
        self.lines = lines
        self.root = root
        self.naming = Naming()
        self.ids = self.naming.ids
        self.survivors = [shape for shape in session.shapes if shape.deleted_line is None]
        self._voids: dict[Shape, list[str]] = {}
        self._labels: dict[str, str] = {}
        # Element id -> the shape the script made, changed, cut or uncut there (``check_relations``).
        self.touched: dict[str, Shape] = {}

    def error(self, line: int | None, message: str) -> ConstructionError:
        return _statement_error(self.lines, line, message)

    def clock(self, line: int | None) -> None:
        """Lowering stays under the script's deadline: the clock is read for every shape it lowers."""

        if time.monotonic() >= self.session.deadline:
            raise self.error(line, TIME_OUT)

    # ---- identity
    def assign_ids(self) -> None:
        """The final ids by the rules of ``identity``, refused where a shape cannot be named."""

        for shape in self.survivors:
            self.clock(shape.line)
        self.naming = identify(self.survivors)
        self.ids = self.naming.ids
        if self.naming.unspellable is not None:
            variable, line = self.naming.unspellable
            raise self.error(line, f"the variable {short(variable)} cannot name a shape; give it an id with "
                                   f"name(obj, \"...\")")
        for identifier, members in self.naming.claimed.items():
            if len(members) > 1:
                second = sorted(members, key=lambda shape: shape.seq)[1]
                raise self.error(self.ids[second][1], f"two shapes would both be called {identifier}; give one of "
                                                      f"them another id with name(obj, \"...\")")
        self.check_ids()
        self._labels = {identifier + ELEMENT_SUFFIX: identifier for identifier, _ in self.ids.values()}

    def identity(self, shape: Shape) -> str:
        return identity_of(shape, self.naming)

    def element_id_of(self, shape: Shape) -> str:
        if isinstance(shape, RowShape) and shape.existing:
            return shape.element_id  # type: ignore[return-value]
        return self.ids[shape][0] + ELEMENT_SUFFIX

    def reusable(self, identifier: str) -> bool:
        """Construction-made geometry (``made_by_construction``) that a script can edit."""

        entity = self.world.entities.get(identifier)
        if entity is None or entity.schema != "Component@1":
            return False
        if not made_by_construction(identifier, self.world.elements_of.get(identifier)):
            return False
        return self.world.entities[identifier + ELEMENT_SUFFIX].fields.get("producer") in EDITABLE_PRODUCERS

    def check_ids(self) -> None:
        reached: set[str] = set()
        for element_id in self.session.explicit:
            reached |= {element_id, self.session.rows[element_id].geometry_id}  # type: ignore[arg-type]
        for shape, (identifier, line) in self.ids.items():
            if len(identifier) > MAX_ID:
                raise self.error(line, f"the id {short(identifier)} is longer than {MAX_ID} characters; give the "
                                       "shape a shorter one with name(obj, \"...\")")
            if identifier.endswith(ELEMENT_SUFFIX):
                raise self.error(line, f"{identifier} ends with {ELEMENT_SUFFIX}, which is kept for a shape's "
                                       "geometry; name the shape differently")
            if identifier in reached or identifier + ELEMENT_SUFFIX in reached:
                raise self.error(line, f"{identifier} is also reached with get() in this script; name the new "
                                       "shape differently")
            if self.reusable(identifier):
                if self.world.entities[identifier + ELEMENT_SUFFIX].fields.get("producer") == "wall":
                    # Re-realized with support for doors and windows: a new definition would make it a plain
                    # solid again and drop its openings; get() keeps the realization.
                    raise self.error(line, f"{identifier} is realized with support for openings; change it with "
                                           "get(), or delete it and draw it again: a new definition would drop them")
                continue
            for taken in (identifier, identifier + ELEMENT_SUFFIX):
                entity = self.world.entities.get(taken)
                if entity is not None:
                    raise self.error(line, f"{taken} is already {entity.schema} in this project; name the new shape "
                                           "differently or get() it")

    # ---- cuts
    def void_key(self, cutter: Any) -> str:
        if isinstance(cutter, Shape):
            if cutter.deleted_line is not None and cutter.is_new:
                return f"\x00deleted-{id(cutter)}"
            return self.element_id_of(cutter)
        return str(cutter)

    def final_voids(self, shape: Shape) -> list[str]:
        """The element ids a shape finally cuts; a new shape replays its cuts over what its id already cut."""

        found = self._voids.get(shape)
        if found is not None:
            return found
        if isinstance(shape, RowShape) and shape.existing:
            current = dict.fromkeys(self.void_key(entry) for entry in shape.voids)
        else:
            identifier = self.ids[shape][0]
            element_id = identifier + ELEMENT_SUFFIX
            current = dict.fromkeys(self.world.voids_of.get(element_id, ()) if self.reusable(identifier) else ())
            for kind, cutter, line in shape.void_events:
                if kind == "clear":
                    current.clear()
                    continue
                key = self.void_key(cutter)
                if kind == "cut":
                    current[key] = None
                elif key in current:
                    del current[key]
                else:
                    raise self.error(line, f"{cutter.label()} does not cut {identifier}")
            if len(current) > MAX_CUTTERS:
                raise self.error(shape.line, f"a shape can be cut by at most {MAX_CUTTERS} cutters")
        found = self._voids[shape] = sorted(key for key in current if not key.startswith("\x00"))
        return found

    def label_of(self, element_id: str) -> str:
        """The id the agent knows an element by."""

        identifier = self._labels.get(element_id)
        return short(identifier if identifier is not None else self.world.geometry_id(element_id))

    # ---- the relations the result leaves
    def check_relations(self) -> None:
        """Refuse what the runtime would refuse about cuts and supports, where the script touched them."""

        world = self.world
        voids = {element_id: set(cutters) for element_id, cutters in world.voids_of.items()}
        stands = {element_id: set(targets) for element_id, targets in world.stands_on.items()}
        touched: dict[str, Shape] = {}
        gone: dict[str, int] = {}  # geometry of the record the script deleted -> the line that deleted it
        for element_id, row in self.session.rows.items():
            self.clock(row.line)
            if row.deleted_line is not None:
                voids.pop(element_id, None)
                stands.pop(element_id, None)
                gone[element_id] = row.deleted_line
                continue
            voids[element_id] = set(self.final_voids(row))
            stands[element_id] = datum_targets(row.lowered(self.element_id_of)[1])
            if row.geometry_changed or tuple(sorted(voids[element_id])) != row.original_voids:
                touched[element_id] = row
        for shape in self.survivors:
            self.clock(shape.line)
            element_id = self.element_id_of(shape)
            voids[element_id] = set(self.final_voids(shape))
            if isinstance(shape, Drawn):
                stands[element_id] = {self.element_id_of(anchor.target) for anchor in shape.anchors()
                                      if anchor.kind == "top"}
            else:
                stands[element_id] = datum_targets(shape.lowered(self.element_id_of)[1])  # type: ignore[attr-defined]
            touched[element_id] = shape
        self.touched = touched

        def refuse(element_id: str, message: str) -> ConstructionError:
            return self.error(touched[element_id].line, message)

        def first_touched(*element_ids: str) -> str | None:
            return next((element_id for element_id in element_ids if element_id in touched), None)

        name = self.label_of
        # What the script deleted is neither cut by nor carrying anything in the final model: refused at the
        # first delete that leaves it so.
        orphaned = [(gone[cutter], f"{name(cutter)} still cuts {name(host)}; uncut it first")
                    for host in sorted(voids) for cutter in sorted(voids[host] & gone.keys())]
        orphaned += [(gone[target], f"{name(user)} stands on the top of {name(target)}; delete or move it first")
                     for user in sorted(stands) for target in sorted(stands[user] & gone.keys())]
        if orphaned:
            raise self.error(*min(orphaned, key=lambda item: item[0]))
        users_of: dict[str, list[str]] = {}
        for user, targets in stands.items():
            for target in targets:
                users_of.setdefault(target, []).append(user)
        for host in sorted(voids):
            cutters = voids[host]
            if not cutters:
                continue
            if host in touched and not touched[host].is_solid:  # type: ignore[attr-defined]
                raise refuse(host, f"{name(host)} has cuts; it must stay a solid")
            for cutter in sorted(cutters):
                involved = first_touched(host, cutter)  # the cut, where the script made it; else the cutter
                if involved is not None:
                    if cutter in touched and not touched[cutter].can_cut:  # type: ignore[attr-defined]
                        raise refuse(cutter, f"{name(cutter)} cuts {name(host)}; it must stay a solid")
                    if voids.get(cutter):
                        raise refuse(involved, f"{name(cutter)} has cutters of its own and cannot cut {name(host)}")
                for user in sorted(users_of.get(cutter, ())):
                    culprit = first_touched(user, host, cutter)  # the shape that now stands on a cutter's top
                    if culprit is not None:
                        raise refuse(culprit, f"{name(user)} stands on the top of {name(cutter)}, which cuts "
                                              f"{name(host)}; a cutter carries nothing")
        for target in sorted(users_of):
            if target in touched and not touched[target].can_carry(world):  # type: ignore[attr-defined]
                raise refuse(target, f"{name(sorted(users_of[target])[0])} stands on the top of {name(target)}; "
                                     f"{name(target)} must stay a solid extruded upward")
        grey, black = 1, 2
        color: dict[str, int] = {}
        for start in sorted(touched):
            if color.get(start):
                continue
            color[start] = grey
            path, stack = [start], [iter(sorted(stands.get(start, ())))]
            while stack:
                child = next(stack[-1], None)
                if child is None:
                    color[path.pop()] = black
                    stack.pop()
                    continue
                if child not in stands:
                    continue
                if color.get(child) == grey:
                    culprit = first_touched(*path[path.index(child):])
                    if culprit is not None:
                        raise refuse(culprit, f"{name(culprit)} would stand on its own top")
                    continue
                if not color.get(child):
                    color[child] = grey
                    path.append(child)
                    stack.append(iter(sorted(stands.get(child, ()))))

    # ---- rows
    def base(self) -> tuple[dict, float]:
        default = self.world.default_level
        return {"level": default[0]}, default[1]  # type: ignore[index]

    def work_plane(self, shape: Drawn, level_elevation: float, normal) -> dict:
        drawing = shape.plane
        origin = drawing.origin  # type: ignore[union-attr]
        return {"origin": [clean(origin[0]), clean(origin[1] - level_elevation), clean(origin[2])],
                "xAxis": [clean(c) for c in drawing.x_axis], "yAxis": [clean(c) for c in drawing.y_axis],  # type: ignore[union-attr]
                "normal": [clean(c) for c in normal]}

    def drawn_row(self, shape: Drawn) -> tuple[str, dict, dict]:
        """The producer, params and references of a drawn shape: the only place a producer is chosen."""

        plan = [[clean(u), clean(v)] for u, v in shape.profile.points] if shape.profile is not None else []
        if shape.kind in ("extrude", "face"):
            params: dict[str, Any] = {"profile": plan if shape.kind == "extrude" else plan + [list(plan[0])]}
            if shape.kind == "extrude":
                height = shape.height
                params["height"] = f"@{height.key}" if isinstance(height, ParamRef) else clean(abs(height))  # type: ignore[arg-type]
            downward = shape.kind == "extrude" and not isinstance(shape.height, ParamRef) and shape.height < 0  # type: ignore[operator]
            if shape.plane is None:
                base, elevation = lower_anchor(shape.anchor, self.element_id_of)  # type: ignore[arg-type]
                if elevation is not None:
                    params["elevation"] = elevation
                if downward:
                    params["work_plane"] = {key: list(value) for key, value in _DOWNWARD.items()}
            else:
                base, level_elevation = self.base()
                normal = tuple(-c for c in shape.plane.normal) if downward else shape.plane.normal
                params["work_plane"] = self.work_plane(shape, level_elevation, normal)
            return ("prism" if shape.kind == "extrude" else "planar-surface"), params, {"base": base}
        if shape.kind == "path":
            base, level_elevation = self.base()
            frame = path_frame(shape.points)
            first = shape.points[0]
            if frame is None:
                params = {"profile": [[clean(x), clean(z)] for x, _, z in shape.points]}
                if clean(first[1] - level_elevation):
                    params["elevation"] = clean(first[1] - level_elevation)
            else:
                x_axis, y_axis, normal = frame
                relative = [tuple(p[i] - first[i] for i in range(3)) for p in shape.points]
                params = {"profile": [[clean(sum(r[i] * x_axis[i] for i in range(3))),
                                       clean(sum(r[i] * y_axis[i] for i in range(3)))] for r in relative],
                          "work_plane": {"origin": [clean(first[0]), clean(first[1] - level_elevation), clean(first[2])],
                                         "xAxis": list(x_axis), "yAxis": list(y_axis), "normal": list(normal)}}
            return "curve", params, {"base": base}
        base, _ = lower_anchor(replace(shape.sections[0].anchor, offset=0.0, param=None), self.element_id_of)
        params = {"profiles": [[[clean(x), clean(section.anchor.offset), clean(z)] for x, z in section.profile.points]
                               for section in shape.sections],
                  "profile_size": len(shape.sections[0].profile.points), "cap_ends": shape.cap}
        return "loft", params, {"base": base}

    def within_reach(self, shape: Shape) -> None:
        """A shape whose definition reaches beyond the world is refused at its line."""

        try:
            box = shape.bounds(self.world)  # type: ignore[attr-defined]
        except ShapeError:
            return
        if not within_reach(box):
            raise self.error(shape.line, f"{shape.label()} reaches beyond 100 000 m from the project origin")

    def production(self, entities: list[dict], removed: list[str]) -> tuple[tuple, Any, dict] | None:
        """The rows the script leaves, produced as the model view will predict them: (the rows in production order,
        the context they were produced in, what each produced).

        The rows are the record's overlaid with the script's (fields merged over the existing entity, as the edit
        path merges them); produced, in production order, are the script's elements, the hosts that cut them (a
        changed cutter changes its host's cut) and every element they mention, directly or not - what they stand
        on, cut and are placed on - never the whole project, which is neither applied nor validated here (the edit
        path does that once the proposal is placed). None when the rows cannot be produced together; the edit
        path then says why.
        """

        world = self.world
        gone = set(removed)
        script = {row["entity_id"]: row["fields"] for row in entities if row["schema"] == "Element@1"}
        known = (set(world.voids_of) - gone) | set(script)
        fields_by_id: dict[str, dict | None] = {}

        def fields_of(element_id: str) -> dict | None:
            if element_id not in fields_by_id:
                entity = world.entities.get(element_id)
                kept = entity is not None and entity.schema == "Element@1" and element_id not in gone
                base = dict(entity.fields) if kept else None  # type: ignore[union-attr]
                fields = {**(base or {}), **script[element_id]} if element_id in script else base
                if fields is not None and not fields.get("component_id") and entity is not None:
                    fields["component_id"] = entity.parent_id
                fields_by_id[element_id] = fields
            return fields_by_id[element_id]

        seeds = dict.fromkeys(script)
        for element_id in script:
            seeds.update(dict.fromkeys(host for host in world.void_hosts.get(element_id, ()) if host not in gone))
        needed: dict[str, None] = {}
        pending = list(seeds)
        while pending:
            element_id = pending.pop()
            if element_id in needed:
                continue
            fields = fields_of(element_id)
            if fields is None:
                continue
            needed[element_id] = None
            mentioned = mentioned_elements(fields.get("references"), known)
            openings = (fields.get("params") or {}).get("openings")
            for opening in openings if isinstance(openings, (list, tuple)) else ():
                if isinstance(opening, dict):
                    mentioned |= mentioned_elements(opening.get("at"), known)
            pending.extend(mentioned - needed.keys())
        try:
            rows = tuple(world.element_row(element_id, fields_by_id[element_id]) for element_id in needed)  # type: ignore[arg-type]
            ordered = production_order(with_void_hosts(rows))
        except (ShapeError, ElementProducerError, ValueError, KeyError, TypeError):
            return None
        context = world.base_context()
        produced = {}
        for row in ordered:
            try:
                produced[row.element_id] = produce_rows((row,), context)[0]
            except _PRODUCTION_ERRORS:
                continue
        return ordered, context, produced

    # ---- cuts the predicted bounds cannot follow
    def check_cuts(self, rows: tuple, context: Any, produced: dict, refused: set[str]) -> None:
        """Refuse a cut whose result the predicted bounds cannot follow, where the script reached it.

        ``refused`` are the hosts whose cut the prediction refused. One is the script's to fix when the script
        made, changed, cut or uncut it or one of its cutters; it is refused at the line of the cut that made the
        refused relation (else the line that changed one of them), naming the cutters the prediction refuses:
        each one it refuses alone, else those it refuses only together. A host the script never reached keeps
        its unknown bounds, as the model view has them; so does one refused for a reason that is no cut's.
        """

        row_of = {row.element_id: row for row in rows}
        found: list[tuple[int, str]] = []
        for host in sorted(refused):
            voids = tuple(sorted(row_of[host].references.get("voids") or ()))
            involved = [self.touched[element_id].line for element_id in (host, *voids) if element_id in self.touched]
            if not involved or not voids or any(void not in produced for void in voids):
                continue
            self.clock(min(involved))
            culprits = self.refused_cutters(row_of[host], voids, context, produced, min(involved))
            if culprits is not None:
                found.append(self.cut_refusal(host, voids, *culprits))
        if found:
            raise self.error(*min(found))

    def refused_cutters(self, row: Any, voids: tuple[str, ...], context: Any, produced: dict,
                        line: int) -> tuple[tuple[str, ...], bool, bool] | None:
        """(the cutters the prediction refuses to take out of ``row``'s host, whether only together, whether they
        miss it): each cutter it refuses alone, missers first, else all of them. None when it refuses the host
        without its cutters as well, or the host cannot be produced so, which is no cut's doing."""

        def refusal(kept: tuple[str, ...]) -> DifferenceBoundsError | None:
            self.clock(line)
            references = {key: value for key, value in row.references.items() if key != "voids"}
            if kept:
                references["voids"] = list(kept)
            element = produce_rows((replace(row, references=references),), context)[0]
            try:
                object_bounds((*(op for void in kept for op in produced[void].operations), *element.operations),
                              (*(item for void in kept for item in produced[void].bindings), *element.bindings),
                              context)
            except DifferenceBoundsError as exc:
                return exc
            return None

        try:
            if refusal(()) is not None:
                return None
            alone = {void: found for void in voids if (found := refusal((void,))) is not None}
        except ConstructionError:
            raise  # the deadline
        except _PRODUCTION_ERRORS:
            return None
        if not alone:
            return voids, len(voids) > 1, False
        missing = tuple(void for void, found in alone.items() if found.disjoint)
        return missing or tuple(alone), False, bool(missing)

    def cut_refusal(self, host: str, voids: tuple[str, ...], culprits: tuple[str, ...], together: bool,
                    missing: bool) -> tuple[int, str]:
        """The line and the sentence of one refused cut, in the ids the agent knows.

        The line is the ``cut()`` in this script that made a refused relation - the one that joined the last
        cutter for cutters refused only together, else the first - or, when no cut here made one, the first line
        that changed a refused cutter or the host, else another of its cutters.
        """

        shape = self.touched.get(host)
        cut_at = {} if shape is None else {self.void_key(cutter): line for cutter, line in shape.cut_lines.items()}

        def changed(*element_ids: str) -> int:
            """The first line that changed one of these, else one of the host's other cutters (one of them did)."""

            lines = [self.touched[element_id].line for element_id in element_ids if element_id in self.touched]
            return min(lines) if lines else min(self.touched[void].line for void in voids if void in self.touched)

        target = self.label_of(host)
        if together:
            made = [cut_at[cutter] for cutter in culprits if cutter in cut_at]
            line = max(made) if made else changed(*culprits, host)
            named = [self.label_of(cutter) for cutter in culprits]
            subject = (" and ".join(named) if len(named) == 2 else short(_compact(named), 120)) + " together"
            them = "them"
        else:
            line, first = min((cut_at.get(cutter) or changed(cutter, host), cutter) for cutter in culprits)
            subject = them = self.label_of(first)
            if missing:
                return line, (f"{subject} does not reach {target}, so the cut would remove nothing; move {subject} "
                              f"onto {target}, or uncut it")
        return line, (f"{subject} would cut away a whole corner or side of {target}, which a cut cannot do yet; keep "
                      f"{them} within the outer extent of {target} for now, or reshape {target} itself")

    def lower(self) -> ConstructionResult:
        self.check_relations()
        entities: list[dict] = []
        report: list[dict] = []
        removed: list[str] = []
        line_of: dict[str, int] = {}
        created, updated, deleted, cut, uncut = [], [], [], [], []
        measured: list[tuple[dict, str]] = []  # report rows whose bounds the successor states, by element id
        for shape in self.survivors:
            self.clock(shape.line)
            identifier, _ = self.ids[shape]
            element_id = identifier + ELEMENT_SUFFIX
            reuse = self.reusable(identifier)
            if isinstance(shape, Drawn):
                producer, params, references = self.drawn_row(shape)
                extra: dict = {}
            else:
                params, references = shape.lowered(self.element_id_of)  # type: ignore[attr-defined]
                producer, extra = shape.producer, dict(shape.extra)  # type: ignore[attr-defined]
            voids = self.final_voids(shape)
            if voids:
                references["voids"] = voids
            if not reuse:
                entities.append({"entity_id": identifier, "schema": "Component@1", "parent_id": self.root,
                                 "fields": {"intent": identifier}})
            entities.append({"entity_id": element_id, "schema": "Element@1", "parent_id": identifier,
                             "fields": {"component_id": identifier, "producer": producer, **extra,
                                        "references": references, "params": params}})
            self.within_reach(shape)
            report.append({"id": identifier, "form": shape.form, "status": "updated" if reuse else "created",  # type: ignore[attr-defined]
                           "bounds": None, "cuts": sorted(self.label_of(v) for v in voids), "line": shape.line})
            measured.append((report[-1], element_id))
            line_of[identifier] = line_of[element_id] = shape.line
            (updated if reuse else created).append(identifier)
            inherited = set(self.world.voids_of.get(element_id, ())) if reuse else set()
            if set(voids) - inherited:
                cut.append(identifier)
            if inherited - set(voids):
                uncut.append(identifier)
        for element_id, row in self.session.rows.items():
            self.clock(row.line)
            identifier = row.geometry_id
            if row.deleted_line is not None:
                removed.extend([element_id] + ([row.component_id] if row.owns_component else []))  # type: ignore[list-item]
                report.append({"id": identifier, "form": row.form, "status": "deleted", "bounds": None, "cuts": [],
                               "line": row.deleted_line})
                line_of[identifier] = line_of[element_id] = row.deleted_line  # type: ignore[index]
                deleted.append(identifier)
                continue
            now = tuple(self.final_voids(row))
            if not row.geometry_changed and now == row.original_voids:
                continue
            params, references = row.lowered(self.element_id_of)
            if now:
                references["voids"] = list(now)
            fields: dict[str, Any] = {}
            if row.has_params or params:
                fields["params"] = params
            if row.has_references or references:
                fields["references"] = references
            entities.append({"entity_id": element_id, "schema": "Element@1", "parent_id": row.parent_id,
                             "fields": fields})
            self.within_reach(row)
            report.append({"id": identifier, "form": row.form, "status": "updated", "bounds": None,
                           "cuts": sorted(self.label_of(v) for v in now), "line": row.line})
            measured.append((report[-1], element_id))
            line_of[identifier] = line_of[element_id] = row.line  # type: ignore[index]
            if row.geometry_changed:
                updated.append(identifier)  # type: ignore[arg-type]
            if set(now) - set(row.original_voids):
                cut.append(identifier)  # type: ignore[arg-type]
            if set(row.original_voids) - set(now):
                uncut.append(identifier)  # type: ignore[arg-type]
        if measured:
            self.clock(measured[0][0]["line"])
            production = self.production(entities, removed)
            refused: set[str] = set()
            boxes = None if production is None else _boxes_of(*production, refused=refused)
            if refused:
                self.check_cuts(*production, refused)  # type: ignore[misc]
            for row, element_id in measured:
                row["bounds"] = _box_list(boxes.get(element_id)) if boxes is not None else None
            self.clock(measured[0][0]["line"])
        parts = [f"{verb} {_compact(ids)}" for verb, ids in (("create", created), ("update", updated),
                                                             ("delete", deleted), ("cut", cut), ("uncut", uncut)) if ids]
        return ConstructionResult(
            entities=tuple(entities), remove_entity_ids=tuple(sorted(set(removed))), report=tuple(report),
            log=tuple(self.session.log), summary="construction: " + ("; ".join(parts) if parts else "no change"),
            line_of=MappingProxyType(line_of))


def compile_construction_script(script: str, record: StateRecord, *, root_component_id: str) -> ConstructionResult:
    """Run a construction script against a record and lower what it leaves to rows; refused with a line."""

    root = next((entity for entity in record.entities if entity.entity_id == root_component_id), None)
    if root is None or root.schema != "Component@1":
        raise ConstructionError(f"{short(str(root_component_id))} is not a component of this project to add shapes under")
    session, lines = run_script(script, record)
    lowering = _Lowering(session, lines, root_component_id)
    try:
        lowering.assign_ids()
        return lowering.lower()
    except RecursionError:
        raise ConstructionError("the shapes of this script depend on each other too deeply to lower") from None


# ---------------------------------------------------------------- the model, in construction terms
def _form(world: World, element_id: str) -> str:
    fields = dict(world.entities[element_id].fields)
    try:
        fields = world.resolved(element_id)
    except ShapeError:
        pass
    producer = fields.get("producer")
    if producer in ("prism", "wall"):
        return "solid"
    if producer == "loft":
        return "solid" if (fields.get("params") or {}).get("cap_ends", True) is not False else "other"
    return {"planar-surface": "face", "curve": "path"}.get(str(producer), "other")


def _element_boxes(world: World) -> dict[str, Box | None]:
    """The predicted bounds of every element the record produces, each element's cuts applied."""

    return _boxes_of(*world.production())


def _boxes_of(rows, context, produced: dict, refused: set[str] | None = None) -> dict[str, Box | None]:
    """The predicted bounds of the produced elements, each element's cuts applied; ``None`` where the prediction
    fails, and no entry for an element that did not produce. ``refused``, when given, gains every element whose
    cut the prediction refused (``DifferenceBoundsError``)."""

    voids_of = {row.element_id: tuple(row.references.get("voids") or ()) for row in rows}
    element_bounds: dict[str, Box | None] = {}
    for element_id, element in produced.items():
        operations, bindings = list(element.operations), list(element.bindings)
        for void in voids_of.get(element_id, ()):
            if void in produced:
                operations = list(produced[void].operations) + operations
                bindings = list(produced[void].bindings) + bindings
        own = {object_id for operation in element.operations for object_id in operation.output_object_ids}
        try:
            boxes = object_bounds(tuple(operations), tuple(bindings), context)
            element_bounds[element_id] = union(box for object_id, box in boxes.items() if object_id in own)
        except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
            element_bounds[element_id] = None
            if refused is not None and isinstance(exc, DifferenceBoundsError):
                refused.add(element_id)
    return element_bounds


def geometry_view(record: StateRecord) -> tuple[dict, ...]:
    """Per component with geometry: its form, bounds, cuts (what it removes), cutBy (what removes it) and whether
    it is hidden (named as a cutter). A component with several elements lists them as ``parts``. Bounds that
    cannot be predicted are ``None``; the view never raises.
    """

    world = World(record)
    element_bounds = _element_boxes(world)
    view = []
    for component in record.entities_of("Component@1"):
        parts = world.elements_of.get(component.entity_id)
        if not parts:
            continue
        forms = {_form(world, element_id) for element_id in parts}
        boxes = [element_bounds.get(element_id) for element_id in parts]
        authored_voids = [void for element_id in parts for void in world.voids_of.get(element_id, ())]
        row: dict[str, Any] = {
            "id": component.entity_id,
            "form": forms.pop() if len(forms) == 1 else "other",
            "bounds": None if any(box is None for box in boxes) else _box_list(union(boxes)),  # type: ignore[arg-type]
            "cuts": sorted({world.component_of(void) for void in authored_voids if void in world.entities}),
            "cutBy": sorted({world.component_of(host) for element_id in parts
                             for host in world.void_hosts.get(element_id, ())}),
            "hidden": any(element_id in world.void_hosts for element_id in parts),
        }
        if len(parts) > 1:
            row["parts"] = list(parts)
        view.append(row)
    return tuple(view)
