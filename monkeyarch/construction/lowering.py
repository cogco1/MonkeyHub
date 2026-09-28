"""A construction script lowered to design-state rows (#419, L1 -> L2).

The script's surviving shapes get their ids here (``name()``, then module
variables, then ``<host>-cut-<n>`` for a cutter made inside ``cut()``, then
``shape-<n>``; an automatic id never takes one the project already has), and
each becomes one ``Component@1 <id>`` with one ``Element@1 <id>-body``.
Producers are chosen here and nowhere else: a plan extrusion is a prism, a
face a planar surface, a path a curve, a loft a loft. A ``get()`` handle
rewrites only its element's ``params`` and ``references``; ``delete`` removes
the component and its element. Cuts are the voids relation: the host element's
``references.voids`` names its cutters' elements. A new shape that redefines
existing geometry keeps what that geometry cut, unless the script uncuts it.

Before any row is written, the relations the result leaves - what cuts what,
what stands on what - are checked where the script touched them, including
against geometry of the record the script never reached, and refused at the
script line in construction words.

``geometry_view`` is the other direction: what a record's geometry is, per
component, in the same words (form, bounds, cuts) and without producers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

from archflow.state.state_record import StateRecord
from monkeyarch.construction.script import (
    MAX_CUTTERS,
    MAX_ID,
    ConstructionError,
    Session,
    _statement_error,
    run_script,
)
from monkeyarch.construction.shapes import (
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
    object_bounds,
    path_frame,
    short,
    union,
    within_reach,
)

ELEMENT_SUFFIX = "-body"
_VARIABLE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
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
        self.ids: dict[Shape, tuple[str, int]] = {}
        self.survivors = [shape for shape in session.shapes if shape.deleted_line is None]
        self._voids: dict[Shape, list[str]] = {}
        self._labels: dict[str, str] = {}

    def error(self, line: int | None, message: str) -> ConstructionError:
        return _statement_error(self.lines, line, message)

    # ---- identity
    def taken(self, identifier: str, claimed: dict) -> bool:
        """An automatic id never takes one this script claimed or the project already has."""

        return (identifier in claimed or identifier in self.world.entities
                or identifier + ELEMENT_SUFFIX in self.world.entities)

    def assign_ids(self) -> None:
        claimed: dict[str, list[Shape]] = {}

        def give(shape: Shape, identifier: str, line: int) -> None:
            self.ids[shape] = (identifier, line)
            claimed.setdefault(identifier, []).append(shape)

        for shape in self.survivors:
            if shape.explicit is not None:
                give(shape, *shape.explicit)
        groups: dict[str, list[Shape]] = {}
        for shape in self.survivors:
            chosen = shape.direct or shape.listed
            if shape not in self.ids and chosen is not None:
                groups.setdefault(chosen[0], []).append(shape)
        for variable, members in groups.items():
            base = variable.replace("_", "-").lower()
            if not _VARIABLE_ID.fullmatch(base):
                line = (members[0].direct or members[0].listed)[2]  # type: ignore[index]
                raise self.error(line, f"the variable {short(variable)} cannot name a shape; give it an id with "
                                       f"name(obj, \"...\")")
            for index, member in enumerate(members, start=1):
                line = (member.direct or member.listed)[2]  # type: ignore[index]
                give(member, base if len(members) == 1 else f"{base}-{index}", line)
        cutters = [shape for shape in self.survivors if shape not in self.ids
                   and any(host.deleted_line is None for host in shape.cut_into)]
        counter = 0
        for shape in self.survivors:
            if shape in self.ids or shape in cutters:
                continue
            counter += 1
            while self.taken(f"shape-{counter}", claimed):
                counter += 1
            give(shape, f"shape-{counter}", shape.created_line)
        per_host: dict[str, int] = {}
        for shape in cutters:
            host = next(host for host in shape.cut_into if host.deleted_line is None)
            host_id = self.identity(host)
            number = per_host.get(host_id, 0) + 1
            while self.taken(f"{host_id}-cut-{number}", claimed):
                number += 1
            per_host[host_id] = number
            give(shape, f"{host_id}-cut-{number}", shape.created_line)
        for identifier, members in claimed.items():
            if len(members) > 1:
                second = sorted(members, key=lambda shape: shape.seq)[1]
                raise self.error(self.ids[second][1], f"two shapes would both be called {identifier}; give one of "
                                                      f"them another id with name(obj, \"...\")")
        self.check_ids()
        self._labels = {identifier + ELEMENT_SUFFIX: identifier for identifier, _ in self.ids.values()}

    def identity(self, shape: Shape) -> str:
        if isinstance(shape, RowShape) and shape.existing:
            return shape.geometry_id  # type: ignore[return-value]
        return self.ids[shape][0]

    def element_id_of(self, shape: Shape) -> str:
        if isinstance(shape, RowShape) and shape.existing:
            return shape.element_id  # type: ignore[return-value]
        return self.ids[shape][0] + ELEMENT_SUFFIX

    def reusable(self, identifier: str) -> bool:
        """Construction-made geometry: a component whose one element is ``<id>-body`` and editable."""

        entity = self.world.entities.get(identifier)
        if entity is None or entity.schema != "Component@1":
            return False
        element_id = identifier + ELEMENT_SUFFIX
        if self.world.elements_of.get(identifier) != [element_id]:
            return False
        return self.world.entities[element_id].fields.get("producer") in EDITABLE_PRODUCERS

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
        for element_id, row in self.session.rows.items():
            if row.deleted_line is not None:
                voids.pop(element_id, None)
                stands.pop(element_id, None)
                continue
            voids[element_id] = set(self.final_voids(row))
            stands[element_id] = datum_targets(row.lowered(self.element_id_of)[1])
            if row.geometry_changed or tuple(sorted(voids[element_id])) != row.original_voids:
                touched[element_id] = row
        for shape in self.survivors:
            element_id = self.element_id_of(shape)
            voids[element_id] = set(self.final_voids(shape))
            if isinstance(shape, Drawn):
                stands[element_id] = {self.element_id_of(anchor.target) for anchor in shape.anchors()
                                      if anchor.kind == "top"}
            else:
                stands[element_id] = datum_targets(shape.lowered(self.element_id_of)[1])  # type: ignore[attr-defined]
            touched[element_id] = shape

        def refuse(element_id: str, message: str) -> ConstructionError:
            return self.error(touched[element_id].line, message)

        def first_touched(*element_ids: str) -> str | None:
            return next((element_id for element_id in element_ids if element_id in touched), None)

        name = self.label_of
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
                if involved is None:
                    continue
                if cutter in touched and not touched[cutter].can_cut:  # type: ignore[attr-defined]
                    raise refuse(cutter, f"{name(cutter)} cuts {name(host)}; it must stay a solid")
                if voids.get(cutter):
                    raise refuse(involved, f"{name(cutter)} has cutters of its own and cannot cut {name(host)}")
                for user in sorted(users_of.get(cutter, ())):
                    raise refuse(first_touched(user, host, cutter) or involved,
                                 f"{name(user)} stands on the top of {name(cutter)}, which cuts {name(host)}; "
                                 "a cutter carries nothing")
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

    def bounds(self, shape: Shape) -> list[list[float]] | None:
        try:
            box = shape.bounds(self.world)  # type: ignore[attr-defined]
        except ShapeError:
            return None
        if not within_reach(box):
            raise self.error(shape.line, f"{shape.label()} reaches beyond 100 000 m from the project origin")
        return _box_list(box)

    def lower(self) -> ConstructionResult:
        self.check_relations()
        entities: list[dict] = []
        report: list[dict] = []
        removed: list[str] = []
        line_of: dict[str, int] = {}
        created, updated, deleted, cut, uncut = [], [], [], [], []
        for shape in self.survivors:
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
            report.append({"id": identifier, "form": shape.form, "status": "updated" if reuse else "created",  # type: ignore[attr-defined]
                           "bounds": self.bounds(shape), "cuts": sorted(self.label_of(v) for v in voids),
                           "line": shape.line})
            line_of[identifier] = line_of[element_id] = shape.line
            (updated if reuse else created).append(identifier)
            inherited = set(self.world.voids_of.get(element_id, ())) if reuse else set()
            if set(voids) - inherited:
                cut.append(identifier)
            if inherited - set(voids):
                uncut.append(identifier)
        for element_id, row in self.session.rows.items():
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
            report.append({"id": identifier, "form": row.form, "status": "updated", "bounds": self.bounds(row),
                           "cuts": sorted(self.label_of(v) for v in now), "line": row.line})
            line_of[identifier] = line_of[element_id] = row.line  # type: ignore[index]
            if row.geometry_changed:
                updated.append(identifier)  # type: ignore[arg-type]
            if set(now) - set(row.original_voids):
                cut.append(identifier)  # type: ignore[arg-type]
            if set(row.original_voids) - set(now):
                uncut.append(identifier)  # type: ignore[arg-type]
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


def geometry_view(record: StateRecord) -> tuple[dict, ...]:
    """Per component with geometry: its form, bounds, cuts (what it removes), cutBy (what removes it) and whether
    it is hidden (named as a cutter). A component with several elements lists them as ``parts``. Bounds that
    cannot be predicted are ``None``; the view never raises.
    """

    world = World(record)
    rows, context, produced = world.production()
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
        except (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
            element_bounds[element_id] = None
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
