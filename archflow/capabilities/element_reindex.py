"""Re-index: recover Element@1 identity from an exported model without moving it.

A project that was modelled by scripts has objects the record never
declared: the model shows 24 columns, the record has no column row. This
module reads the model back - the 3dm inspection records the spine already
keeps (named bboxes, geometry digests, ``archflow:*`` user strings) - and
drafts, for every object, the element that would have produced it:

* the component it belongs to (its ``archflow:component`` string, which
  must be a declared Component@1 - an unknown one is named, not invented);
* its family and side, read off ``archflow:producer_op`` (``column-west-3``
  is family ``column``, side ``west``, index 3);
* one element draft per (component, family, side): a producer from the
  family, parameters from the boxes, references that are symbolic where the
  geometry snaps to a declared grid axis or level and literal otherwise,
  and a confidence that says which;
* the residual between the boxes the drafted row produces and the boxes the
  model has - the proof that the draft is the object, computed by running
  the real producers on the drafted row.

Families the boxes can carry: a line of cylinders is a column-array on the
axes its centres snap to (the facade line is drafted as a GridAxis@1 when
the record lacks it); abaci over columns are capitals seated on the columns'
published top; a box spanning two axis intersections (with a symmetric
overhang) is a beam seated on the capitals' top; a box is a prism with its
literal footprint; a ring of sectors is a ring around the drafted centre
axes; wall pieces of a side, with the frame, glass and leaf objects of that
side, are one wall hosting openings whose width, sill, head and position
are read off the frame groups and whose types come from a wall row the
record already carries.

Nothing here is guessed silently. A family whose form the boxes cannot
carry (a wedge, a shell, a stair) is AMBIGUOUS: it keeps its identity in the
catalog (object -> component -> family) and gets no row. A source that
contradicts another for the same component and side is resolved by the
declared precedence or reported ALTERNATE; the base keeps the objects then.
Existing rows are never replaced: a component that already has an element
for that side keeps it, and the draft is measured against it.

The output is data: a ``ComponentCatalog@1`` payload and, optionally, a
successor StateRecord holding the drafted axes, rows and derived relations.
Writing them anywhere is the caller's (tools/reindex_project.py through the
repository); this module touches no file.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from archflow.capabilities.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    element_rows_of,
    produce_rows,
    production_order,
)
from archflow.capabilities.reference_resolver import ReferenceContext
from archflow.state.geometry_program import GeometryOperationKind
from archflow.state.state_record import Entity, Relation, StateRecord, StateRecordError, project_grids_of, project_levels_of

SCHEMA = "ComponentCatalog@1"

# ---- statuses, the same words the studio catalog speaks

DRAFT = "DRAFT"                      # a row the producers accept; residual measured
AMBIGUOUS = "AMBIGUOUS"              # identity kept, no row: the boxes cannot carry the form
ERROR = "ERROR"                      # the producers refused the drafted row; the refusal is the note
EXISTING = "EXISTING"                # the record already has this element; measured, not replaced

BOUND = "bound"
WITNESS = "witness"                  # an inspection witness: identified, never drafted
SUPERSEDED = "superseded"            # a base object a patch re-realized
ALTERNATE = "alternate"              # a patch object with no precedence over another patch
UNKNOWN_COMPONENT = "UNKNOWN_COMPONENT"

COVERED = "COVERED"
PARTIAL = "PARTIAL"
MODEL_VISIBLE_CATALOG_MISSING = "MODEL_VISIBLE_CATALOG_MISSING"
DECLARED_ONLY = "DECLARED_ONLY"
EMPTY = "EMPTY"

CARDINALS = ("north", "south", "east", "west")
SNAP_M = 0.002                       # a coordinate on an axis or level: within 2 mm
CONTACT_M = 0.025                    # stage-5 convention: an embed up to 25 mm is contact
HIGH, MEDIUM, LOW = 0.9, 0.6, 0.3
SLOPE_HINTS = ("sector", "pediment", "hip", "vault", "portico-roof")   # a 6-face solid so named may be sloped; the box hull is a reading
OPENING_PIECES = ("bottom", "left", "right", "top")


class ReindexError(ValueError):
    """A re-index that cannot proceed as asked (a source without the fields it needs)."""


# ---- the objects a source carries


@dataclass(frozen=True, slots=True)
class SourceObject:
    name: str
    component_id: str | None
    producer_op: str | None
    lo: tuple[float, float, float]      # world x, y, z (z up) as the inspection records it
    hi: tuple[float, float, float]
    geometry_sha256: str | None
    source_ref: str
    precedence: int
    faces: int | None
    object_type: str | None
    # an inspection witness (archflow:inspection_witness): a marker the readback left, not a part of the building
    witness: bool = False

    @property
    def op(self) -> str:
        return self.producer_op or self.name.removeprefix("obj-")

    @property
    def family(self) -> str:
        return family_of(self.op)

    @property
    def side(self) -> str | None:
        return side_of(self.op)

    @property
    def centre(self) -> tuple[float, float, float]:
        return tuple((a + b) / 2.0 for a, b in zip(self.lo, self.hi))  # type: ignore[return-value]

    @property
    def extent(self) -> tuple[float, float, float]:
        return tuple(b - a for a, b in zip(self.lo, self.hi))  # type: ignore[return-value]

    @property
    def ref(self) -> str:
        return f"object:{self.name}@{self.source_ref}"


def objects_of(payload: Mapping[str, Any], source_ref: str, precedence: int) -> tuple[SourceObject, ...]:
    """The objects one inspection record carries, with their strings and digests joined by name."""

    boxes = payload.get("named_object_bboxes")
    if not isinstance(boxes, list):
        raise ReindexError(f"{source_ref}: no named_object_bboxes; it is not a 3dm inspection")
    strings = {u.get("name"): {a.get("key"): a.get("value") for a in (u.get("attributes") or [])} for u in payload.get("object_user_strings") or []}
    digests = {s.get("name"): s.get("geometry_sha256") for s in payload.get("object_geometry_sha256") or []}
    out = []
    for entry in boxes:
        name = entry.get("name")
        box = entry.get("bbox") or {}
        if not name or "min" not in box or "max" not in box:
            continue
        attrs = strings.get(name, {})
        out.append(SourceObject(
            name=str(name),
            component_id=attrs.get("archflow:component"),
            producer_op=attrs.get("archflow:producer_op"),
            lo=tuple(float(v) for v in box["min"]),  # type: ignore[arg-type]
            hi=tuple(float(v) for v in box["max"]),  # type: ignore[arg-type]
            geometry_sha256=digests.get(name),
            source_ref=source_ref,
            precedence=precedence,
            faces=entry.get("mesh_face_count"),
            object_type=entry.get("type"),
            witness=bool(attrs.get("archflow:inspection_witness")),
        ))
    return tuple(out)


_INDEX = re.compile(r"-(\d+)$")
_SIDE_TOKEN = re.compile(r"-(north|south|east|west)(?=-|$)")


def family_of(op: str) -> str:
    """``column-west-3`` -> ``column``; ``entablature-return-west-left`` -> ``entablature-return-left``."""

    stem = op.removeprefix("obj-")
    stem = _INDEX.sub("", stem)
    stem = _SIDE_TOKEN.sub("", stem)
    stem = _INDEX.sub("", stem)
    return stem or op


def side_of(op: str) -> str | None:
    m = _SIDE_TOKEN.search(op)
    return m.group(1) if m else None


def index_of(op: str) -> int | None:
    m = _INDEX.search(op)
    return int(m.group(1)) if m else None


def opening_group_of(op: str) -> str | None:
    """``frame-east-window-left`` -> ``window-left``; ``door-frame-east`` -> ``door``; ``glass-east-window`` -> ``window``."""

    stem = _SIDE_TOKEN.sub("", op.removeprefix("obj-"))
    if stem.startswith("door-frame") or stem.startswith("door-leaf"):
        return "door"
    for prefix in ("frame-", "glass-"):
        if stem.startswith(prefix):
            parts = stem[len(prefix):].split("-")
            if parts and parts[-1] in OPENING_PIECES and prefix == "frame-":
                parts = parts[:-1]
            return "-".join(parts) or None
    return None


def is_opening_object(op: str) -> bool:
    stem = op.removeprefix("obj-")
    return stem.startswith(("frame-", "glass-", "door-frame", "door-leaf"))


# ---- precedence between sources


@dataclass(frozen=True, slots=True)
class Placement:
    """One object's place after precedence: bound to its element, superseded by a patch, or an alternate."""

    obj: SourceObject
    status: str
    note: str = ""


def place_objects(objects: Sequence[SourceObject]) -> tuple[Placement, ...]:
    """Per (component, side): the objects of the highest-precedence source stand; the rest are superseded.

    Two sources of the same precedence that both realize a (component, side)
    are ALTERNATE - neither stands - unless one of them is the base
    (precedence 0), which then keeps the objects: the conflict is reported,
    not resolved by guessing.
    """

    groups: dict[tuple[str | None, str | None], list[SourceObject]] = defaultdict(list)
    by_ref: dict[str, list[SourceObject]] = defaultdict(list)
    for obj in objects:
        groups[(obj.component_id, obj.side)].append(obj)
        by_ref[obj.source_ref].append(obj)
    # what each source re-realized: the extent of everything it carries (a seat's envelope
    # covers the wall band it re-modelled and the openings in it, not the floors above and below)
    extent = {ref: union_box(objs) for ref, objs in by_ref.items()}
    out: list[Placement] = []
    for (component, side), items in groups.items():
        witnesses = [obj for obj in items if obj.witness]
        out.extend(Placement(obj, WITNESS, "inspection witness: a readback marker, not building geometry") for obj in witnesses)
        items = [obj for obj in items if not obj.witness]
        if not items:
            continue
        by_source: dict[str, list[SourceObject]] = defaultdict(list)
        for obj in items:
            by_source[obj.source_ref].append(obj)
        top = max(obj.precedence for obj in items)
        leaders = [ref for ref, objs in by_source.items() if objs[0].precedence == top]
        if len(leaders) == 1:
            standing = leaders[0]
            # a patch re-realizes what it covers: a lower object whose centre lies inside the
            # patch source's extent is superseded; one outside it (a wall band the patch never touched) stands
            lo, hi = extent[standing]
            for obj in items:
                if obj.source_ref == standing:
                    out.append(Placement(obj, BOUND))
                elif _inside(obj.centre, lo, hi):
                    out.append(Placement(obj, SUPERSEDED, f"re-realized by {standing} (precedence {top})"))
                else:
                    out.append(Placement(obj, BOUND, f"outside the extent {standing} (precedence {top}) re-realized"))
        else:
            base = [ref for ref, objs in by_source.items() if objs[0].precedence == 0]
            standing = base[0] if base else None
            for obj in items:
                if standing is not None and obj.source_ref == standing:
                    out.append(Placement(obj, BOUND, f"base kept: {len(leaders)} patches of precedence {top} contend for {component}/{side}"))
                else:
                    out.append(Placement(obj, ALTERNATE, f"{len(leaders)} sources of precedence {top} realize {component}/{side}: " + ", ".join(sorted(leaders))))
    return tuple(out)


def _inside(point: tuple[float, float, float], lo: tuple[float, float, float], hi: tuple[float, float, float]) -> bool:
    return all(lo[i] - CONTACT_M <= point[i] <= hi[i] + CONTACT_M for i in range(3))


# ---- the record's frame: axes and levels to snap to


@dataclass(frozen=True, slots=True)
class AxisLine:
    axis_id: str
    role: str
    # a line in the plan: constant world x (vertical in plan) or constant world y (horizontal in plan)
    const: str            # "x" or "y"
    value: float
    drafted: bool = False
    basis: tuple[str, ...] = ()     # what a drafted axis was read from: the objects whose centres lie on it


def axis_lines_of(record: StateRecord) -> tuple[AxisLine, ...]:
    """The record's GridAxis@1 rows as plan lines. The kernel's plan is (x, z); its z is the world's y."""

    lines = []
    for e in record.entities_of("GridAxis@1"):
        origin = tuple(float(v) for v in e.fields["origin"])
        direction = tuple(float(v) for v in e.fields["direction"])
        dx, _, dz = direction
        if abs(dx) < 1e-9 and abs(dz) > 0:
            lines.append(AxisLine(e.entity_id, str(e.fields["role"]), "x", origin[0]))
        elif abs(dz) < 1e-9 and abs(dx) > 0:
            lines.append(AxisLine(e.entity_id, str(e.fields["role"]), "y", origin[2]))
    return tuple(lines)


def level_values_of(record: StateRecord) -> dict[str, float]:
    return {e.entity_id: float(e.fields["elevation"]) for e in record.entities_of("Level@1")}


class Frame:
    """Snapping against the record's axes and levels, drafting axes the model needs and the record lacks."""

    def __init__(self, record: StateRecord) -> None:
        self.lines: list[AxisLine] = list(axis_lines_of(record))
        self.levels = level_values_of(record)
        self.drafted: list[AxisLine] = []
        self._roles = {line.role for line in self.lines}

    def snap_axis(self, const: str, value: float) -> AxisLine | None:
        best = None
        for line in self.lines:
            if line.const == const and abs(line.value - value) <= SNAP_M and (best is None or abs(line.value - value) < abs(best.value - value)):
                best = line
        return best

    def axis_for(self, const: str, value: float, *, draft_id: str, basis: Sequence[str] = ()) -> AxisLine:
        found = self.snap_axis(const, value)
        if found is not None:
            return found
        if not basis:
            raise ReindexError(f"axis {draft_id}: a drafted axis needs the objects it was read from as its basis")
        role = draft_id.upper()
        n = 1
        while role in self._roles:
            n += 1
            role = f"{draft_id.upper()}-{n}"
        line = AxisLine(_ident(f"axis-{draft_id}"), role, const, round(value, 6), drafted=True, basis=tuple(basis))
        self.lines.append(line)
        self.drafted.append(line)
        self._roles.add(role)
        return line

    def snap_level(self, z: float) -> str | None:
        for level, value in self.levels.items():
            if abs(value - z) <= SNAP_M:
                return level
        return None

    def base_reference(self, z: float) -> tuple[dict[str, Any], float, str]:
        """An elevation reference for a base at world z: the level it sits on, or an offset from the nearest level."""

        if not self.levels:
            raise ReindexError("the record carries no Level@1 to seat elements on")
        level, value = min(self.levels.items(), key=lambda kv: abs(kv[1] - z))
        if abs(value - z) <= SNAP_M:
            return {"level": level}, HIGH, f"base on {level}"
        return {"offset_from": {"level": level, "offset": round(z - value, 6)}}, MEDIUM, f"base {z - value:+.3f} m from {level}"

    def axis_entities(self, basis_refs: Sequence[str]) -> tuple[Entity, ...]:
        out = []
        for line in self.drafted:
            origin = [line.value, 0.0, 0.0] if line.const == "x" else [0.0, 0.0, line.value]
            direction = [0.0, 0.0, 1.0] if line.const == "x" else [1.0, 0.0, 0.0]
            out.append(Entity(line.axis_id, "GridAxis@1", {"role": line.role, "origin": origin, "direction": direction, "epistemic_status": "derived", "note": "drafted by element_reindex from object centres"}, None, _refs(line.basis, basis_refs)))
        return tuple(out)


# ---- element drafts


@dataclass(slots=True)
class ElementDraft:
    element_id: str
    component_id: str
    side: str | None
    family: str
    producer: str | None
    references: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    objects: tuple[SourceObject, ...] = ()
    status: str = DRAFT
    confidence: float = 0.0
    residual_m: float | None = None
    notes: list[str] = field(default_factory=list)
    basis_refs: tuple[str, ...] = ()
    # objects folded in from other components (a wall's frames and glass): they bind to this element
    hosted: tuple[SourceObject, ...] = ()

    @property
    def union(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        return union_box(self.objects + self.hosted)

    @property
    def all_objects(self) -> tuple[SourceObject, ...]:
        return self.objects + self.hosted

    def to_dict(self) -> dict[str, Any]:
        lo, hi = self.union
        return {
            "element_id": self.element_id, "component_id": self.component_id, "side": self.side, "family": self.family,
            "producer": self.producer, "status": self.status, "confidence": self.confidence, "residual_m": self.residual_m,
            "objects": [o.name for o in self.objects], "hosted_objects": [o.name for o in self.hosted], "object_refs": [o.ref for o in self.all_objects],
            "references": self.references, "params": self.params, "bbox": {"min": list(lo), "max": list(hi)},
            "notes": list(self.notes), "basis_refs": list(self.basis_refs),
        }


def union_box(objects: Iterable[SourceObject]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    objs = list(objects)
    if not objs:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    lo = tuple(min(o.lo[i] for o in objs) for i in range(3))
    hi = tuple(max(o.hi[i] for o in objs) for i in range(3))
    return lo, hi  # type: ignore[return-value]


def _r(v: float) -> float:
    return round(v, 6)


def _ident(text: str) -> str:
    """A portable identifier: the text itself up to the 100-character limit, else a stable shortening."""

    if len(text) <= 100:
        return text
    return f"{text[:80].rstrip('-')}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def _refs(*parts: Iterable[str]) -> tuple[str, ...]:
    """Basis refs as the record wants them: sorted, unique."""

    return tuple(sorted({ref for part in parts for ref in part}))


def _element_id(component_id: str, family: str, side: str | None, families_in_component: int) -> str:
    """``portico-entablature`` + ``entablature-front`` + west -> ``portico-entablature-front-west``: family tokens the component already says are not repeated."""

    if families_in_component == 1:
        stem = component_id
    else:
        tokens = set(component_id.split("-"))
        rest = [t for t in family.split("-") if t not in tokens]
        stem = f"{component_id}-{'-'.join(rest)}" if rest else component_id
    return _ident(f"{stem}-{side}" if side else stem)


def _plan_box(lo, hi) -> list[list[float]]:
    """The kernel's plan profile of a world box: (x, world y) corners, counter-clockwise."""

    return [[_r(lo[0]), _r(lo[1])], [_r(hi[0]), _r(lo[1])], [_r(hi[0]), _r(hi[1])], [_r(lo[0]), _r(hi[1])]]


def _is_box(obj: SourceObject) -> bool:
    return obj.faces == 6 and (obj.object_type in (None, "Brep", "Extrusion"))


def _is_cylinder(obj: SourceObject) -> bool:
    ex, ey, _ = obj.extent
    return obj.faces is not None and obj.faces >= 10 and abs(ex - ey) <= SNAP_M * 2


def draft_prism(draft: ElementDraft, frame: Frame) -> None:
    """One box -> a prism with the literal footprint: exact for a box, references literal."""

    lo, hi = union_box(draft.objects)
    base, conf, note = frame.base_reference(lo[2])
    draft.producer = "prism"
    draft.references = {"base": base}
    draft.params = {"profile": _plan_box(lo, hi), "height": _r(hi[2] - lo[2])}
    draft.confidence = min(conf, MEDIUM)
    draft.notes.append(note)
    draft.notes.append("footprint literal (no axis names the edges)")
    if any(hint in draft.family for hint in SLOPE_HINTS):
        draft.confidence = LOW
        draft.notes.append("a 6-face solid so named may be sloped: the box hull is a reading, the readback of a recompile is the proof")


def draft_column_array(draft: ElementDraft, frame: Frame) -> None:
    """N cylinders in a line -> column-array on the axes their centres snap to, publishing <id>-top."""

    objs = sorted(draft.objects, key=lambda o: o.centre)
    if not all(_is_cylinder(o) for o in objs):
        draft.status = AMBIGUOUS
        draft.notes.append("not every object is a cylinder (face count / square extent)")
        return
    radii = [(o.extent[0] + o.extent[1]) / 4.0 for o in objs]
    bases = {round(o.lo[2], 4) for o in objs}
    tops = {round(o.hi[2], 4) for o in objs}
    if len(bases) > 1 or len(tops) > 1:
        draft.status = AMBIGUOUS
        draft.notes.append(f"columns do not share a base/top: bases {sorted(bases)}, tops {sorted(tops)}")
        return
    xs = {round(o.centre[0], 4) for o in objs}
    ys = {round(o.centre[1], 4) for o in objs}
    if len(xs) == 1:
        const, facade_value, varying, var_const = "x", objs[0].centre[0], [o.centre[1] for o in objs], "y"
    elif len(ys) == 1:
        const, facade_value, varying, var_const = "y", objs[0].centre[1], [o.centre[0] for o in objs], "x"
    else:
        draft.status = AMBIGUOUS
        draft.notes.append("column centres are not on one line")
        return
    axes = []
    for value in varying:
        line = frame.snap_axis(var_const, value)
        if line is None:
            draft.status = AMBIGUOUS
            draft.notes.append(f"column centre at {var_const}={value:.3f} is on no declared axis (2 mm)")
            return
        axes.append(line.role)
    facade = frame.axis_for(const, facade_value, draft_id=f"{draft.element_id}-line", basis=tuple(o.ref for o in objs))
    lo = objs[0].lo[2]
    base, conf, note = frame.base_reference(lo)
    draft.producer = "column-array"
    draft.references = {"axes": axes, "facade": facade.role, "base": base}
    draft.params = {"radius": _r(sum(radii) / len(radii)), "height": _r(objs[0].hi[2] - lo), "segments": max(8, (objs[0].faces or 26) - 2)}
    draft.confidence = HIGH if not facade.drafted else min(conf, MEDIUM)
    draft.notes.append(note)
    if facade.drafted:
        draft.notes.append(f"facade axis {facade.role} drafted at {const}={facade.value}")
    if max(radii) - min(radii) > SNAP_M:
        draft.notes.append(f"radii vary {min(radii):.4f}..{max(radii):.4f}; the mean is used")


def draft_capitals(draft: ElementDraft, frame: Frame, columns: ElementDraft | None) -> None:
    """Abaci over columns -> capitals on the columns' axes, seated on <columns>-top."""

    if columns is None or columns.producer != "column-array" or columns.status not in (DRAFT, EXISTING):
        draft.status = AMBIGUOUS
        draft.notes.append("no column-array element of this side to seat the capitals on")
        return
    objs = list(draft.objects)
    heights = {round(o.extent[2], 4) for o in objs}
    halves = [(o.extent[0] + o.extent[1]) / 4.0 for o in objs]
    if len(objs) != len(columns.references.get("axes", ())):
        draft.status = AMBIGUOUS
        draft.notes.append(f"{len(objs)} abaci over {len(columns.references.get('axes', ()))} columns")
        return
    lo = min(o.lo[2] for o in objs)
    column_top = columns.objects[0].hi[2] if columns.objects else None
    if column_top is None or abs(lo - column_top) > CONTACT_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"abacus base {lo:.3f} is not on the column top {column_top}")
        return
    draft.producer = "capitals"
    draft.references = {"columns": columns.element_id, "axes": list(columns.references["axes"]), "facade": columns.references["facade"], "base": {"datum": f"{columns.element_id}-top"}}
    draft.params = {"height": _r(max(heights)), "half_extent": _r(sum(halves) / len(halves))}
    draft.confidence = HIGH
    if abs(lo - column_top) > 1e-6:
        draft.params["engagement"] = {"depth": _r(column_top - lo)}
        draft.notes.append(f"embedded {column_top - lo:.4f} m into the columns")


def draft_beam(draft: ElementDraft, frame: Frame, support: ElementDraft | None) -> None:
    """One box spanning between two axis intersections -> a beam; a box that spans none is a prism."""

    if len(draft.objects) != 1 or not _is_box(draft.objects[0]):
        draft_prism(draft, frame)
        return
    obj = draft.objects[0]
    ex, ey, _ = obj.extent
    long_axis, across = ("x", ey) if ex >= ey else ("y", ex)
    centre_line = obj.centre[1] if long_axis == "x" else obj.centre[0]
    line_const = "y" if long_axis == "x" else "x"
    line = frame.snap_axis(line_const, centre_line)
    lo_end = obj.lo[0] if long_axis == "x" else obj.lo[1]
    hi_end = obj.hi[0] if long_axis == "x" else obj.hi[1]
    ends = [frame.snap_axis(long_axis, lo_end), frame.snap_axis(long_axis, hi_end)]
    if line is not None and all(e is not None for e in ends):
        _finish_beam(draft, frame, obj, line, ends, across, 0.0, support)
        return
    if line is not None:
        inner = [a for a in frame.lines if a.const == long_axis and lo_end < a.value < hi_end]
        if len(inner) >= 2:
            first, last = min(inner, key=lambda a: a.value), max(inner, key=lambda a: a.value)
            over_a, over_b = first.value - lo_end, hi_end - last.value
            if abs(over_a - over_b) <= SNAP_M:
                draft.notes.append(f"ends overhang axes {first.role}/{last.role} by {_r(over_a)} m")
                _finish_beam(draft, frame, obj, line, [first, last], across, _r(over_a), support)
                return
    draft_prism(draft, frame)
    draft.notes.append("beam ends name no axis; kept as a prism")


def _finish_beam(draft: ElementDraft, frame: Frame, obj: SourceObject, line: AxisLine, ends, across: float, overhang: float, support: ElementDraft | None) -> None:
    base, conf, note = frame.base_reference(obj.lo[2])
    if support is not None and support.status in (DRAFT, EXISTING) and support.objects and abs(support.objects[0].hi[2] - obj.lo[2]) <= CONTACT_M:
        base = {"datum": f"{support.element_id}-top"}
        conf = HIGH
        note = f"seated on {support.element_id}-top"
    draft.producer = "beam"
    draft.references = {"from": {"grid": [ends[0].role, line.role]}, "to": {"grid": [ends[1].role, line.role]}, "base": base}
    if support is not None:
        draft.references["support"] = support.element_id
    draft.params = {"depth": _r(across), "height": _r(obj.extent[2])}
    if overhang:
        draft.params["end_overhang"] = overhang
    draft.confidence = conf
    draft.notes.append(note)


def draft_ring(draft: ElementDraft, frame: Frame) -> bool:
    """Sectors around a centre -> a ring: outer radius from the union, inner from the sector on the +x axis."""

    objs = list(draft.objects)
    if len(objs) < 8 or not all(_is_box(o) for o in objs):
        return False
    lo, hi = union_box(objs)
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    half_x, half_y = (hi[0] - lo[0]) / 2.0, (hi[1] - lo[1]) / 2.0
    if abs(half_x - half_y) > SNAP_M * 5:
        return False
    on_axis = [o for o in objs if abs(o.lo[1] - cy) <= SNAP_M and o.lo[0] > cx]
    if not on_axis:
        return False
    # the sector on the +x axis spans angles 0..2pi/n: its outer arc reaches x = r_out at angle 0,
    # its inner arc falls to r_in * cos(2pi/n) at the far angle - the box's low x
    inner = (on_axis[0].lo[0] - cx) / math.cos(2 * math.pi / len(objs))
    outer = half_x
    bases = {round(o.lo[2], 4) for o in objs}
    tops = {round(o.hi[2], 4) for o in objs}
    if len(bases) > 1 or len(tops) > 1 or inner <= 0 or outer <= inner:
        return False
    basis = tuple(o.ref for o in objs)
    ax = frame.axis_for("x", cx, draft_id="centre-x", basis=basis)
    ay = frame.axis_for("y", cy, draft_id="centre-y", basis=basis)
    base, conf, note = frame.base_reference(lo[2])
    draft.producer = "ring"
    draft.references = {"at": {"grid": [ax.role, ay.role]}, "base": base}
    draft.params = {"inner_radius": _r(inner), "outer_radius": _r(outer), "pieces": len(objs), "height": _r(hi[2] - lo[2])}
    draft.confidence = min(conf, MEDIUM if (ax.drafted or ay.drafted) else HIGH)
    draft.notes.append(note)
    draft.notes.append(f"{len(objs)} sectors around ({_r(cx)}, {_r(cy)}); inner radius from the sector on the +x axis")
    return True


@dataclass(frozen=True, slots=True)
class WallTypes:
    """What an existing wall row of the record teaches: its ``types`` and which component takes which type."""

    types: tuple[Mapping[str, Any], ...]
    type_of_component: Mapping[str, str]
    taught_by: str | None


def wall_types_of(rows: Sequence[ElementRow]) -> WallTypes:
    for row in rows:
        if row.producer == "wall" and row.params.get("types"):
            mapping = {str(o.get("component_id")): str(o["type_id"]) for o in row.params.get("openings", ()) if o.get("type_id") and o.get("component_id")}
            return WallTypes(tuple(row.params["types"]), mapping, row.element_id)
    return WallTypes((), {}, None)


def draft_wall(draft: ElementDraft, frame: Frame, openings: Sequence[SourceObject], types: WallTypes, centroid: tuple[float, float]) -> None:
    """Wall pieces of a side -> one wall on the face axis between two edge axes, hosting the side's openings.

    The frame pieces of one opening (bottom/left/right/top) span exactly the
    void: the width is their extent along the wall, the sill and head their
    z range, the position their centre measured from the wall's origin. The
    origin is the end the producer keeps: the one whose right-hand normal
    points inward.
    """

    pieces = list(draft.objects)
    if not all(_is_box(o) for o in pieces):
        draft.status = AMBIGUOUS
        draft.notes.append("wall pieces are not all boxes")
        return
    lo, hi = union_box(pieces)
    ex, ey = hi[0] - lo[0], hi[1] - lo[1]
    if ex >= ey:
        # runs along x at constant y; the exterior face is the y farther from the centroid
        run, const = "x", "y"
        thickness = ey
        face_value = hi[1] if abs(hi[1] - centroid[1]) > abs(lo[1] - centroid[1]) else lo[1]
        inward = (0.0, 1.0) if face_value == lo[1] else (0.0, -1.0)
        run_lo, run_hi = lo[0], hi[0]
    else:
        run, const = "y", "x"
        thickness = ex
        face_value = hi[0] if abs(hi[0] - centroid[0]) > abs(lo[0] - centroid[0]) else lo[0]
        inward = (1.0, 0.0) if face_value == lo[0] else (-1.0, 0.0)
        run_lo, run_hi = lo[1], hi[1]
    basis = tuple(o.ref for o in pieces)
    face = frame.axis_for(const, face_value, draft_id=f"{draft.element_id}-face", basis=basis)
    end_a = frame.snap_axis(run, run_lo)
    end_b = frame.snap_axis(run, run_hi)
    if end_a is None or end_b is None:
        draft.status = AMBIGUOUS
        draft.notes.append(f"the wall's ends {run}={run_lo:.3f}/{run_hi:.3f} are on no declared axis (2 mm)")
        return
    base_level = frame.snap_level(lo[2])
    top_level = frame.snap_level(hi[2])
    if base_level is None or top_level is None:
        draft.status = AMBIGUOUS
        draft.notes.append(f"the wall's base {lo[2]:.3f} / top {hi[2]:.3f} are on no level (2 mm)")
        return

    def grid(end: AxisLine) -> dict[str, Any]:
        return {"grid": [face.role, end.role] if const == "x" else [end.role, face.role]}

    # the producer keeps the given origin when the right-hand normal of (from -> to) points inward
    chosen = None
    for a, b, sign in ((end_a, end_b, 1.0), (end_b, end_a, -1.0)):
        direction = (sign, 0.0) if run == "x" else (0.0, sign)
        normal = (direction[1], -direction[0])
        if normal[0] * inward[0] + normal[1] * inward[1] > 0:
            chosen = (a, b, sign)
            break
    if chosen is None:
        draft.status = AMBIGUOUS
        draft.notes.append("no end order gives an inward normal")
        return
    a, b, sign = chosen
    origin = a.value
    draft.producer = "wall"
    draft.references = {"line": {"from": grid(a), "to": grid(b), "face": "exterior", "inward": [int(inward[0]), int(inward[1])]}, "base": {"level": base_level}, "top": {"level": top_level}}
    draft.params = {"thickness": _r(thickness)}
    groups: dict[str, list[SourceObject]] = defaultdict(list)
    for o in openings:
        key = opening_group_of(o.op)
        if key is not None:
            groups[key].append(o)
    opening_rows = []
    untyped = []
    for key, objs in sorted(groups.items()):
        fr = [o for o in objs if o.op.removeprefix("obj-").startswith(("frame-", "door-frame"))]
        if not fr:
            continue   # glass without frames of its own name sits in a frame group by position; it is hosted all the same
        flo, fhi = union_box(fr)
        centre = (flo[0] + fhi[0]) / 2.0 if run == "x" else (flo[1] + fhi[1]) / 2.0
        width = (fhi[0] - flo[0]) if run == "x" else (fhi[1] - flo[1])
        kind = "door" if key.startswith("door") else "window"
        component = fr[0].component_id or draft.component_id
        # the opening solver names its operations from the opening id alone, so an id must be
        # unique across walls: the side goes in front ("east-window-left"), and a wall row the
        # record already carries keeps its own ids
        opening_id = f"{draft.side}-{key}" if draft.side else key
        row = {"opening_id": opening_id, "kind": kind, "at": {"host": {"element": draft.element_id, "along": _r((centre - origin) * sign)}}, "width": _r(width),
               "sill": {"offset_from": {"level": base_level, "offset": _r(flo[2] - frame.levels[base_level])}}, "head": {"offset_from": {"level": base_level, "offset": _r(fhi[2] - frame.levels[base_level])}},
               "component_id": component}
        type_id = types.type_of_component.get(component)
        if type_id:
            row["type_id"] = type_id
        else:
            untyped.append(key)
        opening_rows.append(row)
    if opening_rows:
        draft.params["openings"] = opening_rows
        if types.types:
            draft.params["types"] = [dict(t) for t in types.types]
    draft.hosted = tuple(sorted(openings, key=lambda o: o.name))
    draft.confidence = MEDIUM if face.drafted else HIGH
    draft.notes.append(f"face axis {face.role}{' drafted' if face.drafted else ''} at {const}={face.value}; from {a.role} to {b.role}; {len(opening_rows)} openings read off the frame groups")
    if types.taught_by:
        draft.notes.append(f"opening types as {types.taught_by} declares them")
    if untyped:
        draft.notes.append(f"openings without a taught type (void only): {', '.join(untyped)}")


def grid_centre(frame: Frame, objects: Sequence[SourceObject]) -> tuple[float, float]:
    """Where the building is, for telling a wall's exterior face: the declared axes' centre, else the objects' centre."""

    xs = [a.value for a in frame.lines if a.const == "x" and not a.drafted]
    ys = [a.value for a in frame.lines if a.const == "y" and not a.drafted]
    if xs and ys:
        return sum(xs) / len(xs), sum(ys) / len(ys)
    if objects:
        return sum(o.centre[0] for o in objects) / len(objects), sum(o.centre[1] for o in objects) / len(objects)
    return 0.0, 0.0


def draft_family(draft: ElementDraft, frame: Frame, siblings: Mapping[tuple[str, str | None], ElementDraft]) -> list[ElementDraft]:
    """Choose the producer a family's boxes can carry, or name the ambiguity. A multi-box family may split into one prism per box."""

    fam = draft.family
    side = draft.side
    if fam == "column":
        draft_column_array(draft, frame)
    elif fam == "abacus":
        draft_capitals(draft, frame, siblings.get(("column", side)))
    elif fam.startswith("entablature-front") or fam.startswith("entablature-return"):
        draft_beam(draft, frame, siblings.get(("abacus", side)))
    elif len(draft.objects) == 1 and _is_box(draft.objects[0]):
        draft_prism(draft, frame)
    elif draft_ring(draft, frame):
        pass
    elif all(_is_box(o) for o in draft.objects):
        # one prism per box; the family identity stays in the shared id stem
        out = []
        ordered = sorted(draft.objects, key=lambda o: (index_of(o.op) if index_of(o.op) is not None else 1_000_000, o.name))
        for k, obj in enumerate(ordered):
            # the piece keeps its own op name (minus the side), so two families' pieces never share an id
            piece = ElementDraft(_element_id(draft.component_id, _SIDE_TOKEN.sub("", obj.op.removeprefix("obj-")), side, 2), draft.component_id, side, fam, None, objects=(obj,), basis_refs=_refs([obj.ref]))
            draft_prism(piece, frame)
            piece.notes.insert(0, f"piece {k + 1} of {len(ordered)} of family {fam}")
            out.append(piece)
        return out
    else:
        draft.status = AMBIGUOUS
        kinds = sorted({f"{o.object_type or '?'}:{o.faces}" for o in draft.objects})
        draft.notes.append(f"{len(draft.objects)} object(s) of form {kinds}; no producer carries this form from boxes")
    return [draft]


# ---- proof by re-production


def _op_box(op, base_value: float) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """The world box of an extrusion or loft: kernel (x, y up, z plan) -> world (x, z plan as y, y up as z)."""

    params = {p.name: json.loads(p.value_json) for p in op.parameters}
    pts = None
    height = 0.0
    offset = 0.0
    if "base_offset" in params:
        offset = float(params["base_offset"])
    if op.kind == GeometryOperationKind.EXTRUSION:
        pts = params.get("profile")
        vector = params.get("vector") or [0.0, 0.0, 0.0]
        height = float(vector[1])
    elif op.kind == GeometryOperationKind.LOFT:
        pts = params.get("profiles")
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    zs = [float(p[2]) for p in pts]
    lo = (min(xs), min(zs), base_value + offset + min(ys))
    hi = (max(xs), max(zs), base_value + offset + max(ys) + height)
    return lo, hi


def measure(drafts: Sequence[ElementDraft], record: StateRecord, frame: Frame, existing: Sequence[ElementRow]) -> None:
    """Run the producers on the drafted rows (with the existing ones for datums) and record each draft's residual."""

    levels = project_levels_of(record)
    grids = project_grids_of(_with_axes(record, frame, ()))
    references = ReferenceContext(grids=grids, levels=levels)
    rows = list(existing)
    by_id = {d.element_id: d for d in drafts}
    for d in drafts:
        if d.status == DRAFT and d.producer:
            try:
                rows.append(ElementRow(d.element_id, d.component_id, d.producer, d.references, d.params, d.basis_refs))
            except ValueError as exc:
                d.status = ERROR
                d.notes.append(f"row refused: {exc}")
    context = ProductionContext(references=references, published={})
    for row in production_order(tuple(rows)):
        draft = by_id.get(row.element_id)
        try:
            produced = produce_rows((row,), context)[0]
        except (ElementProducerError, ValueError, KeyError, TypeError) as exc:
            if draft is not None:
                draft.status = ERROR
                draft.notes.append(f"producer refused: {exc}")
            continue
        for datum in produced.datums:
            context.published[datum.datum_id] = datum
        if draft is None:
            continue
        boxes = []
        for op, binding in zip(produced.operations, produced.bindings):
            try:
                base_value = context.datum_value(binding.datum_id)
            except (ElementProducerError, KeyError, ValueError):
                base_value = frame.levels.get(binding.datum_id)
            if base_value is None:
                continue
            box = _op_box(op, base_value)
            if box is not None:
                boxes.append(box)
        if not boxes:
            draft.notes.append("no box could be read off the produced operations")
            continue
        lo = tuple(min(b[0][i] for b in boxes) for i in range(3))
        hi = tuple(max(b[1][i] for b in boxes) for i in range(3))
        slo, shi = draft.union
        draft.residual_m = round(max(abs(a - b) for a, b in zip(lo + hi, slo + shi)), 6)
        if draft.residual_m > CONTACT_M:
            draft.confidence = min(draft.confidence, LOW)
            draft.notes.append(f"re-produced box differs from the model by {draft.residual_m} m")


def _with_axes(record: StateRecord, frame: Frame, extra: Sequence[Entity]) -> StateRecord:
    axes = frame.axis_entities(record.basis_refs[:1])
    known = {e.entity_id for e in record.entities}
    new = tuple(a for a in axes if a.entity_id not in known) + tuple(extra)
    return replace(record, entities=record.entities + new) if new else record


# ---- derived relations


def derive_support(drafts: Sequence[ElementDraft]) -> tuple[Relation, ...]:
    """A supports B when B's base meets A's top within the contact tolerance and their footprints overlap."""

    out = []
    usable = [d for d in drafts if d.status in (DRAFT, EXISTING) and d.objects]
    for a in usable:
        alo, ahi = union_box(a.objects)
        for b in usable:
            if a is b:
                continue
            blo, bhi = union_box(b.objects)
            if abs(ahi[2] - blo[2]) > CONTACT_M:
                continue
            if bhi[0] < alo[0] - SNAP_M or blo[0] > ahi[0] + SNAP_M or bhi[1] < alo[1] - SNAP_M or blo[1] > ahi[1] + SNAP_M:
                continue
            gap = round(blo[2] - ahi[2], 6)
            out.append(Relation(
                relation_id=_ident(f"{a.element_id}-supports-{b.element_id}"), kind="support", subject=a.element_id, object=b.element_id,
                datum_role="top", propagation="revalidate", parameters={"gap_m": gap} if gap else {}, epistemic_status="derived",
                basis_refs=_refs((o.ref for o in a.objects[:1]), (o.ref for o in b.objects[:1])),
            ))
    return tuple(out)


# ---- the whole


@dataclass(slots=True)
class ReindexResult:
    record: StateRecord
    placements: tuple[Placement, ...]
    drafts: tuple[ElementDraft, ...]
    frame: Frame
    relations: tuple[Relation, ...]
    sources: tuple[dict[str, Any], ...]

    def successor(self, *, run_id: str, basis_refs: Sequence[str]) -> StateRecord:
        """The record with the drafted axes, the DRAFT rows and the derived relations added; nothing replaced."""

        rows = []
        known = {e.entity_id for e in self.record.entities}
        for d in self.drafts:
            if d.status == DRAFT and d.producer and d.element_id not in known:
                rows.append(Entity(d.element_id, "Element@1", {"component_id": d.component_id, "producer": d.producer, "references": d.references, "params": d.params, "epistemic_status": "derived", "confidence": d.confidence, "residual_m": d.residual_m, "objects": [o.name for o in d.all_objects]}, d.component_id, _refs(basis_refs, d.basis_refs)))
        record = _with_axes(self.record, self.frame, tuple(rows))
        ids = {e.entity_id for e in record.entities}
        relation_ids = {r.relation_id for r in record.relations}
        relations = tuple(r for r in self.relations if r.subject in ids and r.object in ids and r.relation_id not in relation_ids)
        return replace(record, run_id=run_id, relations=record.relations + relations, basis_refs=_refs(record.basis_refs, basis_refs), predecessor_ref=f"record:{self.record.digest}")

    def catalog(self, *, run_id: str) -> dict[str, Any]:
        components = {e.entity_id: e for e in self.record.entities_of("Component@1")}
        by_component: dict[str, list[Placement]] = defaultdict(list)
        for p in self.placements:
            by_component[p.obj.component_id or "?"].append(p)
        elements_by_component: dict[str, list[ElementDraft]] = defaultdict(list)
        for d in self.drafts:
            elements_by_component[d.component_id].append(d)
        element_of_object: dict[str, str] = {}
        for d in self.drafts:
            if d.status in (DRAFT, EXISTING):
                for o in d.all_objects:
                    element_of_object[o.name] = d.element_id
        comps = []
        for cid, e in components.items():
            placed = by_component.get(cid, [])
            bound = [p for p in placed if p.status == BOUND]
            drafts = elements_by_component.get(cid, [])
            rows = [d for d in drafts if d.status in (DRAFT, EXISTING)]
            covered = sum(1 for p in bound if p.obj.name in element_of_object)
            hosts = sorted({element_of_object[p.obj.name] for p in bound if p.obj.name in element_of_object} - {d.element_id for d in rows})
            if not placed and not drafts:
                status = EMPTY
            elif not placed:
                status = DECLARED_ONLY
            elif covered == 0:
                status = MODEL_VISIBLE_CATALOG_MISSING
            elif covered < len(bound):
                status = PARTIAL
            else:
                status = COVERED
            comps.append({"component_id": cid, "parent_id": e.parent_id, "catalog_status": status, "objects_bound": len(bound), "objects_total": len(placed), "objects_with_element": covered, "elements": [d.element_id for d in rows], "hosted_by": hosts, "ambiguous": [d.element_id for d in drafts if d.status == AMBIGUOUS], "errors": [d.element_id for d in drafts if d.status == ERROR]})
        unknown = sorted({p.obj.component_id or "(none)" for p in self.placements if (p.obj.component_id or "") not in components})
        objects = [{"name": p.obj.name, "source_ref": p.obj.source_ref, "component_id": p.obj.component_id, "producer_op": p.obj.producer_op, "family": p.obj.family, "side": p.obj.side, "status": p.status if (p.obj.component_id or "") in components else UNKNOWN_COMPONENT, "element_id": element_of_object.get(p.obj.name) if p.status == BOUND else None, "geometry_sha256": p.obj.geometry_sha256, "bbox": {"min": list(p.obj.lo), "max": list(p.obj.hi)}, "note": p.note} for p in self.placements]
        drafts = [d.to_dict() for d in self.drafts]
        counts = {
            "objects": len(self.placements), "bound": sum(1 for p in self.placements if p.status == BOUND), "superseded": sum(1 for p in self.placements if p.status == SUPERSEDED), "alternate": sum(1 for p in self.placements if p.status == ALTERNATE), "witness": sum(1 for p in self.placements if p.status == WITNESS),
            "bound_with_element": sum(1 for p in self.placements if p.status == BOUND and p.obj.name in element_of_object),
            "elements_draft": sum(1 for d in self.drafts if d.status == DRAFT), "elements_existing": sum(1 for d in self.drafts if d.status == EXISTING), "elements_ambiguous": sum(1 for d in self.drafts if d.status == AMBIGUOUS), "elements_error": sum(1 for d in self.drafts if d.status == ERROR),
            "axes_drafted": len(self.frame.drafted), "relations_derived": len(self.relations),
            "components": {s: sum(1 for c in comps if c["catalog_status"] == s) for s in (COVERED, PARTIAL, MODEL_VISIBLE_CATALOG_MISSING, DECLARED_ONLY, EMPTY)},
        }
        try:
            state_digest: str | None = self.record.state_digest
        except StateRecordError:
            state_digest = None   # a record without a base names no run; its digest still identifies it
        return {
            "schema": SCHEMA, "project_id": self.record.project_id, "run_id": run_id, "base_record_digest": self.record.digest, "base_state_digest": state_digest,
            "sources": list(self.sources), "precedence_rule": "per (component, side): the highest-precedence source stands; equal precedence between patches is ALTERNATE and the base keeps the objects",
            "tolerances_m": {"snap": SNAP_M, "contact": CONTACT_M}, "summary": counts, "components": comps, "unknown_components": unknown,
            "objects": objects, "elements": drafts, "axes_drafted": [{"axis_id": a.axis_id, "role": a.role, "const": a.const, "value": a.value} for a in self.frame.drafted],
            "relations_derived": [r.to_dict() for r in self.relations],
        }


def reindex(record: StateRecord, sources: Sequence[tuple[Mapping[str, Any], str, int]]) -> ReindexResult:
    """Drafts, placements, residuals and relations for a record against its exported models.

    ``sources`` are (inspection payload, record ref, precedence); precedence 0
    is the base, higher patches win per (component, side).
    """

    objects: list[SourceObject] = []
    source_rows = []
    for payload, ref, precedence in sources:
        objs = objects_of(payload, ref, precedence)
        objects.extend(objs)
        source_rows.append({"ref": ref, "precedence": precedence, "object_count": len(objs), "file_sha256": payload.get("file_sha256"), "schema": payload.get("schema")})
    placements = place_objects(objects)
    components = {e.entity_id for e in record.entities_of("Component@1")}
    frame = Frame(record)
    existing_rows = element_rows_of(record)
    existing_by_key: dict[tuple[str, str | None], ElementRow] = {}
    for row in existing_rows:
        existing_by_key[(row.component_id, side_of(row.element_id))] = row
    types = wall_types_of(existing_rows)

    bound = [p.obj for p in placements if p.status == BOUND and (p.obj.component_id or "") in components]
    wall_sides = {o.side for o in bound if o.family == "wall"} | {side_of(r.element_id) for r in existing_rows if r.producer == "wall"}
    groups: dict[tuple[str, str, str | None], list[SourceObject]] = defaultdict(list)
    openings_by_side: dict[str | None, list[SourceObject]] = defaultdict(list)
    for obj in bound:
        if is_opening_object(obj.op) and obj.side in wall_sides:
            openings_by_side[obj.side].append(obj)
            continue
        groups[(obj.component_id, obj.family, obj.side)].append(obj)  # type: ignore[index]
    centroid = grid_centre(frame, bound)
    families_per_component: dict[str, set[str]] = defaultdict(set)
    for (cid, fam, _), _objs in groups.items():
        families_per_component[cid].add(fam)

    drafts: list[ElementDraft] = []
    siblings: dict[tuple[str, str | None], ElementDraft] = {}
    order = {"column": 0, "abacus": 1, "wall": 0}
    for (cid, fam, side), objs in sorted(groups.items(), key=lambda kv: (order.get(kv[0][1], 2), kv[0])):
        eid = _element_id(cid, fam, side, len(families_per_component[cid]))
        draft = ElementDraft(eid, cid, side, fam, None, objects=tuple(sorted(objs, key=lambda o: o.name)), basis_refs=_refs(o.ref for o in objs))
        existing = existing_by_key.get((cid, side))
        if existing is not None and (existing.element_id == eid or fam in existing.element_id or existing.producer in ("prism", "wall")):
            draft.element_id = existing.element_id
            draft.producer = existing.producer
            draft.references = dict(existing.references)
            draft.params = dict(existing.params)
            draft.status = EXISTING
            draft.confidence = HIGH
            draft.notes.append("the record already carries this element; measured against the model, not replaced")
            if existing.producer == "wall":
                draft.hosted = tuple(sorted(openings_by_side.get(side, ()), key=lambda o: o.name))
            made = [draft]
        elif fam == "wall":
            draft_wall(draft, frame, openings_by_side.get(side, ()), types, centroid)
            made = [draft]
        else:
            made = draft_family(draft, frame, siblings)
        siblings[(fam, side)] = made[0]
        drafts.extend(made)
    # openings on a side whose wall row the record carries but whose pieces no bound object realizes
    for side, objs in openings_by_side.items():
        if any(d.side == side and d.family == "wall" for d in drafts):
            continue
        existing = next((r for r in existing_rows if r.producer == "wall" and side_of(r.element_id) == side), None)
        if existing is not None:
            drafts.append(ElementDraft(existing.element_id, existing.component_id, side, "wall", existing.producer, dict(existing.references), dict(existing.params), (), EXISTING, HIGH, notes=["the record's wall row; the side's openings bind to it"], hosted=tuple(sorted(objs, key=lambda o: o.name))))
    seen: dict[str, int] = {}
    for d in drafts:
        n = seen.get(d.element_id, 0)
        seen[d.element_id] = n + 1
        if n:
            d.notes.append(f"id {d.element_id} was taken; numbered")
            d.element_id = _ident(f"{d.element_id}-{n + 1}")
    measure(drafts, record, frame, existing_rows)
    relations = derive_support(drafts)
    return ReindexResult(record, placements, tuple(drafts), frame, relations, tuple(source_rows))
