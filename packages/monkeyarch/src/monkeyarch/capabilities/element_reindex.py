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
axes; boxes that climb in a straight line by one rise and one going are a
stair, and a prism standing on its top binds the flight's published datum
instead of a level; wall pieces of a side, with the frame, glass and leaf
objects of that side, are one wall hosting openings whose width, sill, head
and position are read off the frame groups and whose types come from a wall
row the record already carries.

Nothing here is guessed silently. A family whose form the boxes cannot
carry is AMBIGUOUS: it keeps its identity in the catalog (object ->
component -> family) and gets no row. Two such forms are drafted only when
the export says what a bounding box cannot: a five-face solid is a wedge
only where ``archflow:wedge_low`` / ``archflow:wedge_high`` /
``archflow:wedge_axis`` name the sloped top, and a revolved solid is a
shell only where the family is a drum (a cylinder wall) and its thickness
comes from ``archflow:shell_thickness`` or from a sibling inner-surface
object. A source that contradicts another for the same component and side
is resolved by the declared precedence or reported ALTERNATE; the base
keeps the objects then. Existing rows are never replaced: a component that
already has an element for that side keeps it, and the draft is measured
against it.

The output is data: a ``ComponentCatalog@1`` payload and, optionally, the
typed StateRecord operator for the drafted axes, rows and derived relations.
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

from monkeyarch.capabilities.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    element_rows_of,
    produce_rows,
    production_order,
)
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from archflow.state.geometry_program import GeometryOperationKind
from archflow.state.state_record import (
    Entity,
    Relation,
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    project_grids_of,
    project_levels_of,
)

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
STAIR_HINTS = ("stair", "step", "flight", "spiral")     # a family so named is read as a flight before it is split into prisms
WEDGE_HINTS = ("pediment", "abutment", "sector", "wedge")
SHELL_HINTS = ("drum", "dome", "shell", "lantern-cap")
SHELL_FACES = 20                     # a revolved solid: too many faces for a box, square in plan
WEDGE_NOTE = "five-face solid: the top edge is not readable from a box; a wedge row needs low/high/axis — author it or re-export with archflow:wedge_* strings"
SHELL_THICKNESS_NOTE = "a shell needs its thickness: no inner surface object; author `thickness` or re-export with archflow:shell_thickness"


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
    # what a bounding box cannot say and the export may: the sloped top of a five-face solid
    # (archflow:wedge_low / wedge_high in metres, wedge_axis "along" | "across") and the wall
    # of a revolved shell (archflow:shell_thickness in metres, shell_kind "cylinder" | "dome").
    # Kept as written so a value that is not a number can be named rather than dropped.
    wedge_low: str | None = None
    wedge_high: str | None = None
    wedge_axis: str | None = None
    wedge_sense: str | None = None      # archflow:wedge_sense "from" | "to": which end of the run is the low edge (default from)
    shell_thickness: str | None = None
    shell_kind: str | None = None

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

    def written(attrs: Mapping[str, Any], key: str) -> str | None:
        value = attrs.get(key)
        return None if value is None else str(value)

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
            wedge_low=written(attrs, "archflow:wedge_low"),
            wedge_high=written(attrs, "archflow:wedge_high"),
            wedge_axis=written(attrs, "archflow:wedge_axis"),
            wedge_sense=written(attrs, "archflow:wedge_sense"),
            shell_thickness=written(attrs, "archflow:shell_thickness"),
            shell_kind=written(attrs, "archflow:shell_kind"),
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
    # an axis_point's ``along`` is measured from the axis origin in the axis direction, not from the
    # world origin: where the origin sits on the line, and which way the line runs
    origin_at: float = 0.0
    dir_sign: float = 1.0


def axis_lines_of(record: StateRecord) -> tuple[AxisLine, ...]:
    """The record's GridAxis@1 rows as plan lines. The kernel's plan is (x, z); its z is the world's y."""

    lines = []
    for e in record.entities_of("GridAxis@1"):
        origin = tuple(float(v) for v in e.fields["origin"])
        direction = tuple(float(v) for v in e.fields["direction"])
        dx, _, dz = direction
        if abs(dx) < 1e-9 and abs(dz) > 0:
            lines.append(AxisLine(e.entity_id, str(e.fields["role"]), "x", origin[0], origin_at=origin[2], dir_sign=1.0 if dz > 0 else -1.0))
        elif abs(dz) < 1e-9 and abs(dx) > 0:
            lines.append(AxisLine(e.entity_id, str(e.fields["role"]), "y", origin[2], origin_at=origin[0], dir_sign=1.0 if dx > 0 else -1.0))
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
    # relations the draft's rows name and the record lacks (an opening's hosts_void interface), derived,
    # and the Connection@1 entities that make them available to a spatial option
    relations: list[Relation] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)

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

    tokens = set(component_id.split("-"))
    rest = [t for t in family.split("-") if t not in tokens]
    if families_in_component == 1:
        stem = component_id
    else:
        stem = f"{component_id}-{'-'.join(rest)}" if rest else component_id
    text = f"{stem}-{side}" if side else stem
    if text == component_id:
        # entity ids are one namespace: an element may not take its component's id
        text = f"{component_id}-{'-'.join(rest)}" if rest else f"{component_id}-1"
    return _ident(text)


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


# ---- the three families a box alone cannot carry: a flight, a wedge, a shell


def _metres(text: str, key: str) -> float:
    """A user string the export wrote as a length; a value that is not one is named, never dropped."""

    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        raise ReindexError(f"{key} is {text!r}, not a number of metres") from None
    if not math.isfinite(value):
        raise ReindexError(f"{key} is {text!r}, not a finite number of metres")
    return value


def _axis_point(line: AxisLine, coordinate: float) -> dict[str, Any]:
    """A point on a declared axis: ``along`` metres from the axis origin, in the axis direction."""

    return {"axis_point": {"axis": line.role, "along": _r((coordinate - line.origin_at) * line.dir_sign)}}


def _run_references(draft: ElementDraft, frame: Frame, run: str, start: float, end: float, across: float, *, draft_id: str) -> tuple[dict[str, Any], dict[str, Any], float, float, float, float]:
    """The two ends of a run line as references, and where they actually land.

    Preferred: both ends on declared axis intersections. Failing that, points
    along the declared axis the run line lies on. Failing that, the run axis
    is drafted the way ``draft_column_array`` drafts a facade line, and the
    note says so. Returned with the resolved start, end and across values, so
    the parameters that follow are measured against the references the row
    will actually carry and not against the boxes.
    """

    across_const = "y" if run == "x" else "x"
    line = frame.snap_axis(across_const, across)
    end_a, end_b = frame.snap_axis(run, start), frame.snap_axis(run, end)
    if line is not None and end_a is not None and end_b is not None:
        draft.notes.append(f"ends on the declared axes {end_a.role} and {end_b.role}, across {line.role}")
        return {"grid": [end_a.role, line.role]}, {"grid": [end_b.role, line.role]}, end_a.value, end_b.value, line.value, HIGH
    if line is not None:
        draft.notes.append(f"the ends are on no declared axis (2 mm); they are drafted as points along {line.role}, which the run line lies on")
        return _axis_point(line, start), _axis_point(line, end), start, end, line.value, MEDIUM
    line = frame.axis_for(across_const, across, basis=tuple(o.ref for o in draft.objects), draft_id=draft_id)
    draft.notes.append(f"run axis {line.role} drafted at {across_const}={line.value}; the ends are points along it")
    return _axis_point(line, start), _axis_point(line, end), start, end, line.value, MEDIUM


def draft_stair(draft: ElementDraft, frame: Frame) -> None:
    """Boxes that climb in a straight line -> a flight of ``count`` steps of one rise and one going.

    Solid steps fill their whole rise and draft ``thickness`` 0. Steps thinner
    than their rise are read as the original thin-tread input and draft that
    height as ``thickness`` so the note and the row say what was seen; this
    is diagnostic only, because the whole-flight producer refuses a positive
    ``thickness`` (separate slab treads are not one closed solid) and does not
    carry it into geometry. The invariants a flight must hold
    are named one by one - ``collinear``, ``going``, ``rise``, ``width`` -
    so a family that is not one (a spiral, whose steps rotate) says which
    reading failed instead of being split into unrelated prisms.
    """

    objs = list(draft.objects)
    if len(objs) < 2 or not all(_is_box(o) for o in objs):
        draft.status = AMBIGUOUS
        kinds = sorted({f"{o.object_type or '?'}:{o.faces}" for o in objs})
        draft.notes.append(f"a flight is two or more 6-face boxes; this family has {len(objs)} object(s) of form {kinds}")
        return
    spread_x = max(o.centre[0] for o in objs) - min(o.centre[0] for o in objs)
    spread_y = max(o.centre[1] for o in objs) - min(o.centre[1] for o in objs)
    if min(spread_x, spread_y) > SNAP_M or max(spread_x, spread_y) <= SNAP_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"collinear: the step centres do not advance along one plan axis (they spread {_r(spread_x)} m in x and {_r(spread_y)} m in y)")
        return
    run = "x" if spread_x > spread_y else "y"
    i, j = (0, 1) if run == "x" else (1, 0)
    objs.sort(key=lambda o: (o.lo[2], o.lo[i]))
    count = len(objs)
    rises = [objs[k + 1].lo[2] - objs[k].lo[2] for k in range(count - 1)]
    if min(rises) <= 0.0 or max(rises) - min(rises) > SNAP_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"rise: the steps do not climb by one rise (base to base {_r(min(rises))}..{_r(max(rises))} m)")
        return
    rise = sum(rises) / len(rises)
    widths = [o.hi[j] - o.lo[j] for o in objs]
    if max(widths) - min(widths) > SNAP_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"width: the steps are not one width ({_r(min(widths))}..{_r(max(widths))} m across the run)")
        return
    goings = [o.hi[i] - o.lo[i] for o in objs]
    if max(goings) - min(goings) > SNAP_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"going: the steps are not one going ({_r(min(goings))}..{_r(max(goings))} m along the run)")
        return
    going = sum(goings) / len(goings)
    sign = 1.0 if objs[-1].lo[i] > objs[0].lo[i] else -1.0
    lead = [o.lo[i] if sign > 0 else o.hi[i] for o in objs]
    if max(abs(lead[k] - lead[0] - k * going * sign) for k in range(count)) > SNAP_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"going: the steps do not advance by one going along {run}; a flight has no gap between treads")
        return
    heights = [o.hi[2] - o.lo[2] for o in objs]
    if max(heights) - min(heights) > SNAP_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"rise: the steps are not one height ({_r(min(heights))}..{_r(max(heights))} m)")
        return
    height = sum(heights) / len(heights)
    if height > rise + SNAP_M:
        draft.status = AMBIGUOUS
        draft.notes.append(f"rise: a step {_r(height)} m tall over a {_r(rise)} m rise overlaps the one below; a flight's steps do not")
        return
    thickness = 0.0 if abs(height - rise) <= SNAP_M else height
    across = (objs[0].lo[j] + objs[0].hi[j]) / 2.0
    start, end = lead[0], lead[-1] + going * sign
    from_ref, to_ref, start_at, end_at, across_at, conf = _run_references(draft, frame, run, start, end, across, draft_id=f"{draft.element_id}-run")
    base, base_conf, base_note = frame.base_reference(objs[0].lo[2])
    draft.producer = "stair"
    draft.references = {"from": from_ref, "to": to_ref, "base": base}
    draft.params = {"count": count, "rise": _r(rise), "going": _r(abs(end_at - start_at) / count), "width": _r(sum(widths) / len(widths)), "thickness": _r(thickness)}
    draft.confidence = min(conf, base_conf)
    draft.notes.append(base_note)
    draft.notes.append(f"{count} steps up {run} by {_r(rise)} m, centred on {'y' if run == 'x' else 'x'}={_r(across_at)}; " + ("solid steps (the box fills the rise)" if not thickness else f"slab steps {_r(thickness)} m thick under a {_r(rise)} m rise"))


def draft_wedge(draft: ElementDraft, frame: Frame) -> None:
    """A five-face solid -> a wedge, but only where the export says which edge is low and which is high.

    A bounding box holds the same hull for a wedge rising along its length,
    one rising across it, and one rising the other way; nothing in the box
    chooses between them. So the low and high edges and the slope's axis are
    read from the object's ``archflow:wedge_*`` strings or the family stays
    AMBIGUOUS. The run is the longer plan extent and the depth the shorter -
    the reading ``draft_beam`` already makes of a box - and the low edge is
    taken at the ``from`` end, which is the one thing here that is a
    convention rather than a measurement; the note says so.
    """

    obj = draft.objects[0]
    if obj.wedge_low is None or obj.wedge_high is None or obj.wedge_axis is None:
        draft.status = AMBIGUOUS
        draft.notes.append(WEDGE_NOTE)
        return
    try:
        low = _metres(obj.wedge_low, "archflow:wedge_low")
        high = _metres(obj.wedge_high, "archflow:wedge_high")
    except ReindexError as exc:
        draft.status = AMBIGUOUS
        draft.notes.append(str(exc))
        return
    axis = obj.wedge_axis.strip().lower()
    if axis not in ("along", "across"):
        draft.status = AMBIGUOUS
        draft.notes.append(f"archflow:wedge_axis is {obj.wedge_axis!r}; a wedge slopes 'along' its run or 'across' it")
        return
    if high <= low or low < 0.0:
        draft.status = AMBIGUOUS
        draft.notes.append(f"archflow:wedge_low {low} m and wedge_high {high} m are not a slope above the base")
        return
    lo, hi = obj.lo, obj.hi
    ex, ey = hi[0] - lo[0], hi[1] - lo[1]
    run = "x" if ex >= ey else "y"
    i, j = (0, 1) if run == "x" else (1, 0)
    depth = hi[j] - lo[j]
    from_ref, to_ref, _start, _end, across_at, conf = _run_references(draft, frame, run, lo[i], hi[i], (lo[j] + hi[j]) / 2.0, draft_id=f"{draft.element_id}-line")
    base, base_conf, base_note = frame.base_reference(lo[2])
    draft.producer = "wedge"
    # The producer puts ``low`` at the ``from`` end (along) or on the -normal side (across), and
    # this drafter's from->to runs in the +x / +y world direction. archflow:wedge_sense says which
    # way the top rises, anchored to the kernel plane so it does not depend on any row's reference
    # order: "+x" | "-x" | "+z" | "-z" (kernel z is world y). The legacy "from" | "to" names the
    # low end relative to this drafter's run and is still read. A rise that contradicts the
    # drafter's orientation swaps the two references.
    sense = (obj.wedge_sense or "from").strip().lower()
    swap = False
    if sense in ("from", "to"):
        swap = sense == "to"
    elif sense in ("+x", "-x", "+z", "-z"):
        world_axis = "x" if sense[1] == "x" else "y"
        sign = 1 if sense[0] == "+" else -1
        if axis == "along":
            if world_axis != run:
                draft.status = AMBIGUOUS
                draft.notes.append(f"archflow:wedge_sense {sense!r} is not along the run ({run}); a wedge sloping along its run rises along it")
                return
            swap = sign < 0
        else:
            if world_axis == run:
                draft.status = AMBIGUOUS
                draft.notes.append(f"archflow:wedge_sense {sense!r} is along the run ({run}); a wedge sloping across it rises across it")
                return
            # for from->to along +x the +normal side is world -y; along +y it is world +x
            expected = -1 if run == "x" else 1
            swap = sign != expected
    else:
        draft.status = AMBIGUOUS
        draft.notes.append(f"archflow:wedge_sense is {obj.wedge_sense!r}; it names the rise: '+x' | '-x' | '+z' | '-z' in the kernel plane, or the legacy 'from' | 'to'")
        return
    if swap:
        from_ref, to_ref = to_ref, from_ref
        draft.notes.append(f"archflow:wedge_sense {sense!r}: the rise runs against this drafter's orientation, so the run references are swapped")
    draft.references = {"from": from_ref, "to": to_ref, "base": base}
    draft.params = {"depth": _r(depth), "low": _r(low), "high": _r(high)}
    if axis == "across":
        draft.params["slope_across"] = True
    draft.confidence = min(conf, base_conf)
    draft.notes.append(base_note)
    draft.notes.append(f"low {_r(low)} m, high {_r(high)} m and the slope {axis} the run come from the object's archflow:wedge_* strings; the run is the longer plan extent ({run}), the depth ({_r(depth)} m) the shorter, and the low edge is taken at the from end - the strings do not carry the sense")
    box_height = hi[2] - lo[2]
    if abs(high - box_height) > SNAP_M:
        draft.confidence = LOW
        draft.notes.append(f"archflow:wedge_high {_r(high)} m is not the box height {_r(box_height)} m; the string is used and the residual reports the difference")


def draft_shell(draft: ElementDraft, frame: Frame, related: Sequence[SourceObject] = ()) -> None:
    """A revolved solid -> a hollow shell, but only a drum: a cylinder wall whose thickness is stated.

    The outer radius and the height are the box's own extents. The thickness
    is not in the box at all: it comes from ``archflow:shell_thickness`` or
    from a sibling inner-surface object named ``<family>-inner*``, and
    without either the family stays AMBIGUOUS. The kind is ``cylinder``
    unless ``archflow:shell_kind`` says otherwise - a dome's rise is no more
    readable from a box than a wedge's slope.
    """

    obj = draft.objects[0]
    lo, hi = obj.lo, obj.hi
    outer = ((hi[0] - lo[0]) + (hi[1] - lo[1])) / 4.0
    kind = (obj.shell_kind or "").strip().lower() or ("cylinder" if "drum" in draft.family else "")
    if kind not in ("cylinder", "dome"):
        draft.status = AMBIGUOUS
        draft.notes.append(f"a shell's kind is not readable from a box: {'archflow:shell_kind is ' + repr(obj.shell_kind) if obj.shell_kind else 'only a drum is read as a cylinder wall'}; author the row or re-export with archflow:shell_kind")
        return
    inner = [o for o in related if o.component_id == obj.component_id and o.side == obj.side and o.op.removeprefix("obj-").startswith(f"{draft.family}-inner")]
    if obj.shell_thickness is not None:
        try:
            thickness = _metres(obj.shell_thickness, "archflow:shell_thickness")
        except ReindexError as exc:
            draft.status = AMBIGUOUS
            draft.notes.append(str(exc))
            return
        source = "archflow:shell_thickness"
    elif inner:
        ilo, ihi = union_box(inner)
        thickness = outer - ((ihi[0] - ilo[0]) + (ihi[1] - ilo[1])) / 4.0
        source = f"the inner surface object(s) {', '.join(sorted(o.name for o in inner))}"
    else:
        draft.status = AMBIGUOUS
        draft.notes.append(SHELL_THICKNESS_NOTE)
        return
    if not 0.0 < thickness < outer:
        draft.status = AMBIGUOUS
        draft.notes.append(f"a thickness of {_r(thickness)} m does not sit inside the outer radius {_r(outer)} m ({source})")
        return
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    basis = tuple(o.ref for o in draft.objects)
    ax = frame.axis_for("x", cx, draft_id="centre-x", basis=basis)
    ay = frame.axis_for("y", cy, draft_id="centre-y", basis=basis)
    base, base_conf, base_note = frame.base_reference(lo[2])
    draft.producer = "shell"
    draft.references = {"at": {"grid": [ax.role, ay.role]}, "base": base}
    draft.params = {"outer_radius": _r(outer), "thickness": _r(thickness), "height": _r(hi[2] - lo[2]), "kind": kind}
    draft.confidence = min(base_conf, MEDIUM if (ax.drafted or ay.drafted) else HIGH)
    draft.notes.append(base_note)
    draft.notes.append(f"outer radius and height are the box's extents around ({_r(cx)}, {_r(cy)}); the thickness {_r(thickness)} m comes from {source}; the segment count is the producer's default, which the box cannot say")


def seat_on_stairs(drafts: Sequence[ElementDraft]) -> None:
    """A prism standing on a drafted flight binds the flight's published top instead of a level.

    That is the whole point of ``<id>-top``: change the rise and the landing
    moves with the flight. A landing left on a level would silently stay
    behind. Only a contact within the snapping tolerance is re-seated, and a
    residual gap is declared as the base reference's offset, never absorbed.
    """

    flights = [d for d in drafts if d.producer == "stair" and d.status == DRAFT and d.objects]
    for d in drafts:
        if d.producer != "prism" or d.status != DRAFT or not d.objects:
            continue
        blo, bhi = union_box(d.objects)
        for flight in flights:
            flo, fhi = union_box(flight.objects)
            gap = _r(blo[2] - fhi[2])
            if abs(gap) > SNAP_M:
                continue
            if bhi[0] < flo[0] - SNAP_M or blo[0] > fhi[0] + SNAP_M or bhi[1] < flo[1] - SNAP_M or blo[1] > fhi[1] + SNAP_M:
                continue
            base: dict[str, Any] = {"datum": f"{flight.element_id}-top"}
            if gap:
                base["offset"] = gap
            d.references["base"] = base
            d.confidence = HIGH
            d.notes.append(f"seated on {flight.element_id}-top rather than the level above: the flight publishes what it carries" + (f" ({gap:+.4f} m off it)" if gap else ""))
            break


@dataclass(frozen=True, slots=True)
class WallTypes:
    """What an existing wall row of the record teaches: its ``types``, which component takes which
    type, and which interface relation (a ``hosts_void`` between zones) each opening component names."""

    types: tuple[Mapping[str, Any], ...]
    type_of_component: Mapping[str, str]
    taught_by: str | None
    interface_of_component: Mapping[str, str] = field(default_factory=dict)   # component -> relation id (with the teaching row's side in it)
    taught_side: str | None = None


def wall_types_of(rows: Sequence[ElementRow]) -> WallTypes:
    for row in rows:
        if row.producer == "wall" and row.params.get("types"):
            mapping = {str(o.get("component_id")): str(o["type_id"]) for o in row.params.get("openings", ()) if o.get("type_id") and o.get("component_id")}
            interfaces = {str(o.get("component_id")): str(o["interface_ref"]).removeprefix("relation:") for o in row.params.get("openings", ()) if o.get("interface_ref") and o.get("component_id")}
            return WallTypes(tuple(row.params["types"]), mapping, row.element_id, interfaces, side_of(row.element_id))
    return WallTypes((), {}, None)


@dataclass(frozen=True, slots=True)
class MirroredInterface:
    """An interface a drafted opening names: the relation id, and what the record must gain for it
    to be available - a derived hosts_void relation and the Connection@1 that carries it."""

    relation_id: str
    relation: Relation | None = None
    connection: Entity | None = None


def _swap_side(text: str, taught: str, side: str) -> str:
    return text.replace(f"-{taught}-", f"-{side}-").replace(f"-{taught}", f"-{side}") if text.endswith(f"-{taught}") else text.replace(f"-{taught}-", f"-{side}-")


def interface_for(types: WallTypes, component: str, side: str | None, record: StateRecord) -> tuple[MirroredInterface | None, str | None]:
    """The interface relation a drafted opening should name on this side: the record's own when it
    declares it, else one mirrored from the teaching side (the relation with the side substituted in
    id, subject and object, and the Connection@1 naming it, every zone existing), else a note.

    A proposal may only name interfaces the spatial option lists, and the option lists them off its
    Connection@1 entities: a mirrored relation without its connection would be refused as absent."""

    template = types.interface_of_component.get(component)
    if template is None or side is None or types.taught_side is None:
        return None, f"no interface relation is taught for {component}"
    taught = types.taught_side
    wanted = _swap_side(template, taught, side)
    if wanted == template and side != taught:
        return None, f"interface {template} carries no side to substitute"
    declared = {r.relation_id: r for r in record.relations}
    connections = [e for e in record.entities_of("Connection@1") if f"relation:{wanted}" in tuple(e.fields.get("relationship_refs", ()))]
    if wanted in declared and connections:
        return MirroredInterface(wanted), None
    source = declared.get(template)
    if source is None:
        return None, f"the taught interface {template} is not a relation of the record"
    carrier = next((e for e in record.entities_of("Connection@1") if f"relation:{template}" in tuple(e.fields.get("relationship_refs", ()))), None)
    if carrier is None:
        return None, f"no Connection@1 of the record names the taught interface {template}"
    ids = {e.entity_id for e in record.entities}
    subject = _swap_side(source.subject, taught, side)
    obj = _swap_side(source.object, taught, side)
    src_zone = _swap_side(str(carrier.fields["source_zone_id"]), taught, side)
    dst_zone = _swap_side(str(carrier.fields["target_zone_id"]), taught, side)
    for zone in (subject, obj, src_zone, dst_zone):
        if zone not in ids:
            return None, f"mirroring {template} to {side} names a zone the record lacks ({zone})"
    relation = None if wanted in declared else Relation(relation_id=wanted, kind=source.kind, subject=subject, object=obj, datum_role=source.datum_role, propagation=source.propagation, parameters=dict(source.parameters), epistemic_status="derived", basis_refs=())
    connection_id = _ident(_swap_side(carrier.entity_id, taught, side))
    connection = None if connections else Entity(connection_id, "Connection@1", {"source_zone_id": src_zone, "target_zone_id": dst_zone, "relationship_refs": [f"relation:{wanted}"], "directed": bool(carrier.fields.get("directed", False)), "epistemic_status": "derived", "note": f"mirrored from {carrier.entity_id} by element_reindex"}, carrier.parent_id, ())
    return MirroredInterface(wanted, relation, connection), None


def draft_wall(draft: ElementDraft, frame: Frame, openings: Sequence[SourceObject], types: WallTypes, centroid: tuple[float, float], record: StateRecord | None = None) -> None:
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
    uninterfaced = []
    for key, objs in sorted(groups.items()):
        fr = [o for o in objs if o.op.removeprefix("obj-").startswith(("frame-", "door-frame"))]
        if not fr:
            continue   # glass without frames of its own name sits in a frame group by position; it is hosted all the same
        flo, fhi = union_box(fr)
        centre = (flo[0] + fhi[0]) / 2.0 if run == "x" else (flo[1] + fhi[1]) / 2.0
        width = (fhi[0] - flo[0]) if run == "x" else (fhi[1] - flo[1])
        kind = "door" if key.startswith("door") else "window"
        component = fr[0].component_id or draft.component_id
        # opening ids are the model's own ("window-left"); the opening solver scopes its
        # operation ids by wall (f10c925), so two walls may name the same opening
        row = {"opening_id": key, "kind": kind, "at": {"host": {"element": draft.element_id, "along": _r((centre - origin) * sign)}}, "width": _r(width),
               "sill": {"offset_from": {"level": base_level, "offset": _r(flo[2] - frame.levels[base_level])}}, "head": {"offset_from": {"level": base_level, "offset": _r(fhi[2] - frame.levels[base_level])}},
               "component_id": component}
        type_id = types.type_of_component.get(component)
        if type_id:
            row["type_id"] = type_id
        else:
            untyped.append(key)
        if record is not None:
            mirrored, why = interface_for(types, component, draft.side, record)
            if mirrored is not None:
                row["interface_ref"] = f"relation:{mirrored.relation_id}"
                basis = _refs(o.ref for o in fr)
                if mirrored.relation is not None and all(r.relation_id != mirrored.relation.relation_id for r in draft.relations):
                    draft.relations.append(replace(mirrored.relation, basis_refs=basis))
                if mirrored.connection is not None and all(e.entity_id != mirrored.connection.entity_id for e in draft.entities):
                    draft.entities.append(replace(mirrored.connection, basis_refs=basis))
            else:
                uninterfaced.append(f"{key}: {why}")
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
    if uninterfaced:
        draft.notes.append("openings without an interface relation: " + "; ".join(uninterfaced))
    if draft.relations or draft.entities:
        draft.notes.append(f"{len(draft.relations)} hosts_void relation(s) and {len(draft.entities)} connection(s) mirrored from {types.taught_by} for this side")


def grid_centre(frame: Frame, objects: Sequence[SourceObject]) -> tuple[float, float]:
    """Where the building is, for telling a wall's exterior face: the declared axes' centre, else the objects' centre."""

    xs = [a.value for a in frame.lines if a.const == "x" and not a.drafted]
    ys = [a.value for a in frame.lines if a.const == "y" and not a.drafted]
    if xs and ys:
        return sum(xs) / len(xs), sum(ys) / len(ys)
    if objects:
        return sum(o.centre[0] for o in objects) / len(objects), sum(o.centre[1] for o in objects) / len(objects)
    return 0.0, 0.0


def is_stair_family(draft: ElementDraft) -> bool:
    """A family the model names as a flight; the invariants then say whether it is one."""

    return len(draft.objects) >= 2 and any(hint in draft.family for hint in STAIR_HINTS)


def is_wedge_family(draft: ElementDraft) -> bool:
    """One five-face solid the model names as a sloped-top piece: a pediment, an abutment, a sector."""

    if len(draft.objects) != 1:
        return False
    obj = draft.objects[0]
    described = obj.wedge_low is not None or obj.wedge_high is not None or obj.wedge_axis is not None
    # a solid the export describes as a wedge is one whatever its face count (a wedge whose low edge
    # is above the base has six faces); an undescribed six-face solid is a box and stays a prism
    return described or (obj.faces == 5 and any(hint in draft.family for hint in WEDGE_HINTS))


def is_shell_family(draft: ElementDraft) -> bool:
    """One revolved solid, square in plan, the model names as a drum, a dome, a shell or a lantern cap."""

    if len(draft.objects) != 1 or "-inner" in draft.family or not any(hint in draft.family for hint in SHELL_HINTS):
        return False
    obj = draft.objects[0]
    ex, ey, _ = obj.extent
    return (obj.faces or 0) >= SHELL_FACES and abs(ex - ey) <= SNAP_M * 2


def _existing_for(rows: Sequence[ElementRow], eid: str, component_id: str, fam: str, claimed: set[str]) -> ElementRow | None:
    """The record's own row this family is: the row whose id is the drafted id, or whose id carries
    the family's own tokens (the component's tokens do not count), or the wall row for the wall
    family. Each row is claimed by one family only, so a component with several rows never has all
    its families measured against the same one."""

    own = [t for t in fam.split("-") if t not in set(component_id.split("-"))] or fam.split("-")
    for row in rows:
        if row.element_id in claimed:
            continue
        if row.element_id == eid:
            return row
    for row in rows:
        if row.element_id in claimed:
            continue
        tokens = set(row.element_id.split("-"))
        if fam == "wall" and row.producer == "wall":
            return row
        if all(t in tokens for t in own):
            return row
    return None


def _split_into_prisms(draft: ElementDraft, frame: Frame, fam: str, side: str | None) -> list[ElementDraft]:
    """One prism per box; the family identity stays in the shared id stem."""

    out = []
    ordered = sorted(draft.objects, key=lambda o: (index_of(o.op) if index_of(o.op) is not None else 1_000_000, o.name))
    for k, obj in enumerate(ordered):
        # the piece keeps its own op name (minus the side), so two families' pieces never share an id
        piece = ElementDraft(_element_id(draft.component_id, _SIDE_TOKEN.sub("", obj.op.removeprefix("obj-")), side, 2), draft.component_id, side, fam, None, objects=(obj,), basis_refs=_refs([obj.ref]))
        draft_prism(piece, frame)
        piece.notes.insert(0, f"piece {k + 1} of {len(ordered)} of family {fam}")
        out.append(piece)
    return out


def draft_family(draft: ElementDraft, frame: Frame, siblings: Mapping[tuple[str, str | None], ElementDraft], related: Sequence[SourceObject] = ()) -> list[ElementDraft]:
    """Choose the producer a family's boxes can carry, or name the ambiguity. A multi-box family may split into one prism per box."""

    fam = draft.family
    side = draft.side
    if fam == "column":
        draft_column_array(draft, frame)
    elif fam == "abacus":
        draft_capitals(draft, frame, siblings.get(("column", side)))
    elif fam.startswith("entablature-front") or fam.startswith("entablature-return"):
        draft_beam(draft, frame, siblings.get(("abacus", side)))
    elif is_stair_family(draft):
        draft_stair(draft, frame)
        if draft.status == AMBIGUOUS and all(_is_box(o) for o in draft.objects):
            # not a flight the producer can carry (overlapping treads, a turn, uneven rises): the
            # boxes are still the model - one prism per step keeps the geometry and the identity,
            # and every piece says why the family is not a stair row
            why = "; ".join(draft.notes)
            pieces = _split_into_prisms(draft, frame, fam, side)
            for piece in pieces:
                piece.notes.append(f"family {fam} is not a flight ({why})")
            return pieces
    elif is_shell_family(draft):
        draft_shell(draft, frame, related)
    elif is_wedge_family(draft):
        draft_wedge(draft, frame)
    elif len(draft.objects) == 1 and _is_box(draft.objects[0]):
        draft_prism(draft, frame)
    elif draft_ring(draft, frame):
        pass
    elif all(_is_box(o) for o in draft.objects):
        return _split_into_prisms(draft, frame, fam, side)
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

    def operator(self, *, basis_refs: Sequence[str]) -> StateRecordOperator:
        """Compile the drafted axes, rows and relations; the state owner applies them."""

        rows = []
        known = {e.entity_id for e in self.record.entities}
        for d in self.drafts:
            if d.status == DRAFT and d.producer and d.element_id in known:
                d.status = ERROR
                d.notes.append(f"id {d.element_id} is already an entity of the record; the row was not added")
            if d.status == DRAFT and d.producer and d.element_id not in known:
                rows.append(Entity(d.element_id, "Element@1", {"component_id": d.component_id, "producer": d.producer, "references": d.references, "params": d.params, "epistemic_status": "derived", "confidence": d.confidence, "residual_m": d.residual_m, "objects": [o.name for o in d.all_objects]}, d.component_id, _refs(basis_refs, d.basis_refs)))
        extra_entities: list[Entity] = []
        seen_extra = set(known)
        for d in self.drafts:
            if d.status in (DRAFT, EXISTING):
                for e in d.entities:
                    if e.entity_id not in seen_extra:
                        extra_entities.append(e)
                        seen_extra.add(e.entity_id)
        # entities and relations go in together: a mirrored connection names a mirrored relation,
        # and the record validates both at once
        axes = tuple(a for a in self.frame.axis_entities(self.record.basis_refs[:1]) if a.entity_id not in known)
        additions = axes + tuple(extra_entities) + tuple(rows)
        ids = known | {e.entity_id for e in additions}
        relation_ids = {r.relation_id for r in self.record.relations}
        relations = tuple(r for r in self.relations if r.subject in ids and r.object in ids and r.relation_id not in relation_ids)
        return StateRecordOperator(
            kind=StateRecordEditKind.REINDEX,
            base_record_digest=self.record.digest,
            base_state_digest=self.record.state_digest,
            entities=additions,
            relations=relations,
            basis_refs=_refs(basis_refs),
        )

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
    existing_by_key: dict[tuple[str, str | None], list[ElementRow]] = defaultdict(list)
    for row in existing_rows:
        existing_by_key[(row.component_id, side_of(row.element_id))].append(row)
    claimed: set[str] = set()
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
        existing = _existing_for(existing_by_key.get((cid, side), ()), eid, cid, fam, claimed)
        if existing is not None:
            claimed.add(existing.element_id)
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
            draft_wall(draft, frame, openings_by_side.get(side, ()), types, centroid, record)
            made = [draft]
        else:
            made = draft_family(draft, frame, siblings, bound)
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
    seat_on_stairs(drafts)
    measure(drafts, record, frame, existing_rows)
    needed = tuple(r for d in drafts if d.status in (DRAFT, EXISTING) for r in d.relations)
    relations = needed + derive_support(drafts)
    return ReindexResult(record, placements, tuple(drafts), frame, relations, tuple(source_rows))
