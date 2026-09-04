"""The architect's program sheet, two-way with the record's spatial option.

A record already carries the spatial option: ``Space@1`` zones, the
``Volume@1`` boxes they occupy, the ``MassingLevel@1`` levels those boxes
stand on, and ``Connection@1`` edges naming declared relations between two
zones. What it has never carried is the architect's own document — departments
and their spaces, with a target area, a count, a clear height and a function,
plus the adjacencies the brief demands. That document is the *program sheet*,
and this module is the two directions between it and the record:

``sheet_from_record`` reads a sheet out of a record. It states only what the
record actually says: one row per zone, an area only where the zone's volume
boxes give one, a function only where the zone names a component whose
semantic kind is registered, and an adjacency row only where a connection
carries a relation of a kind the four program requirements name. Everything
the record cannot answer comes back ``None`` beside an honesty line; nothing
here fills a gap with a plausible number.

``apply_sheet`` writes a sheet into a **new** record. It only ever adds: a
``Space@1`` for a sheet space that maps to no zone yet, and — for an adjacency
that names no relation — one ``Relation`` of the matching kernel kind plus the
``Connection@1`` that carries it. It deletes nothing, and the only field it
touches on an entity that already exists is a ``program_node_refs`` entry that
entity lacks.

Two vocabularies are borrowed and neither is copied. A function is a semantic
id or alias of ``archflow.semantics``, resolved there and refused here when it
resolves to nothing. A requirement becomes a kind of
``ArchitecturalRelationKind`` and nothing else: two of the four requirements
have a kernel kind that says exactly what they mean, the other two have none,
and for those this module refuses and names the vocabulary rather than
inventing a kind for them.

Pure functions, no I/O. Where the sheet is kept on disk is
``archflow.project.layout``'s to name and ``archflow.project.inputs``'s to
read and write.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from archflow.relations.contracts import ArchitecturalRelationKind
from archflow.project.refs import require_identifier
from archflow.semantics.registry import resolve_semantic_kind, suggest_semantic
from archflow.state.state_record import (
    Entity,
    Relation,
    StateRecord,
    StateRecordEditKind,
    StateRecordOperator,
    apply_state_record_operator,
)

PROGRAM_SHEET_SCHEMA = "ProgramSheet@1"

# The prefix a zone's ``program_node_refs`` uses to say which department and
# which sheet space it stands for: ``program:<department_id>/<space_id>``.
# Both halves are portable identifiers, so the ref splits on the one slash.
PROGRAM_REF_PREFIX = "program:"

# The department a zone falls into when nothing in its ``program_node_refs``
# names one. It is not a guess about the building: it is the sheet saying the
# record has not been told which department this zone belongs to.
UNASSIGNED_DEPARTMENT = "unassigned"

# One horizontal cell of the record's massing grid, in square metres. The
# record's own spatial option declares this basis (``schematic_proposal``
# builds ``SpatialGridBasis(horizontal_area_per_cell=1.0,
# area_unit="square_metres")``), and it is the only statement of area anywhere
# in a record. Reading it is not inventing an area; assuming any other value
# would be.
HORIZONTAL_AREA_PER_CELL_M2 = 1.0

# The four requirements a program sheet may state, and the kernel relation
# kind each becomes.
#
# ``adjacent`` and ``apart`` are the two the kernel vocabulary already says:
# ``adjacent`` is the adjacency kind itself, and ``clearance`` is the kernel's
# word for free space that must remain between two zones — which is what a
# brief means when it says two spaces must be kept apart, and is the kind the
# record's zone-to-zone relations already use for exactly that ("corridor to
# hall clearance").
#
# ``near`` and ``visual`` map to ``None`` on purpose. The kernel has no kind
# for "close but not touching" and none for "visually connected"; ``clearance``
# says the opposite of the first, and ``role.view`` is a semantic role of a
# component rather than a relation between two zones. Applying a sheet that
# states one of those refuses and names the vocabulary. Inventing a kind here
# would put a second relation vocabulary beside the kernel's.
REQUIREMENT_KINDS: Mapping[str, str | None] = MappingProxyType({
    "adjacent": ArchitecturalRelationKind.ADJACENT.value,
    "near": None,
    "apart": ArchitecturalRelationKind.CLEARANCE.value,
    "visual": None,
})

REQUIREMENTS: tuple[str, ...] = tuple(REQUIREMENT_KINDS)

# The reverse reading, for the derivation: a relation of this kind between two
# zones is that requirement. Only the kinds a requirement maps to appear, so a
# connection carrying a relation of any other kind is reported in ``honesty``
# rather than being squeezed into a requirement it does not mean.
_KIND_REQUIREMENTS: Mapping[str, str] = MappingProxyType({
    kind: requirement
    for requirement, kind in REQUIREMENT_KINDS.items()
    if kind is not None
})

_RELATION_VOCABULARY = ", ".join(
    sorted(kind.value for kind in ArchitecturalRelationKind)
)


class ProgramSheetError(ValueError):
    """A program sheet this module refuses, and the reason it refuses it."""


# ---------------------------------------------------------------- record -> sheet


def sheet_from_record(
    record: StateRecord,
    *,
    state_digest: str | None = None,
) -> dict[str, Any]:
    """The program sheet the record already states, and what it cannot state.

    One space row per ``Space@1``. The department and the sheet space id come
    from the zone's ``program_node_refs`` where one is written in the
    ``program:<department>/<space>`` form; a zone that names none falls into
    ``unassigned`` under its own entity id. Area is the footprint of the
    zone's ``Volume@1`` boxes and is ``None`` when no box can be read.
    Function is the semantic kind of the component the zone names, when the
    record names one and the registry knows it.

    ``record_digest`` names the record's complete design content.
    ``state_digest`` names its run/base binding for the client that will send
    the sheet back. A caller that already holds the projection's state digest
    passes it; otherwise the record's own is taken where the record is bound to
    a run, and is ``None`` — with an honesty line — where it is not.
    """

    honesty: list[str] = []
    components = {
        entity.entity_id: entity for entity in record.entities_of("Component@1")
    }
    volumes = {
        entity.entity_id: entity for entity in record.entities_of("Volume@1")
    }
    zones = record.entities_of("Space@1")

    departments: dict[str, dict[str, Any]] = {}
    space_of_zone: dict[str, str] = {}
    areas_read = False
    without_area: list[str] = []
    for zone in zones:
        department_id, space_id = _program_ref_of(zone)
        area = _zone_area(zone, volumes, honesty)
        areas_read = areas_read or area is not None
        if area is None:
            without_area.append(zone.entity_id)
        space_of_zone[zone.entity_id] = space_id
        department = departments.setdefault(
            department_id,
            {"department_id": department_id, "name": department_id, "spaces": []},
        )
        department["spaces"].append({
            "space_id": space_id,
            # The record has nowhere to keep a display name, so the id is the
            # name until an architect types one. Inventing a prettier one here
            # would put a second name for the same space on the wire.
            "name": space_id,
            "function": _zone_function(zone, components),
            "target_area_m2": area,
            "count": 1,
            # A ``MassingLevel@1`` height is the level's full height, not the
            # clear height under whatever spans it. The record states no clear
            # height, so neither does this.
            "clear_height_m": None,
            "level_ids": [str(item) for item in zone.fields.get("level_ids", ())],
            "zone_id": zone.entity_id,
            "mapped_area_m2": area,
        })

    adjacencies = _adjacencies_of(record, space_of_zone, honesty)
    sheet = {
        "schema": PROGRAM_SHEET_SCHEMA,
        "project_id": record.project_id,
        "record_digest": record.digest,
        "state_digest": state_digest if state_digest is not None else _own_digest(record, honesty),
        "departments": [departments[key] for key in sorted(departments)],
        "adjacencies": adjacencies,
        "totals": {},
        "honesty": [],
    }
    sheet["totals"] = totals_of(sheet)
    if not zones:
        honesty.append(
            "the record declares no Space@1: this sheet has no rows, and its "
            "emptiness is the record's silence rather than a reading of the design"
        )
    else:
        honesty.insert(0, (
            f"derived from the record's {len(zones)} Space@1 zone(s): the record "
            "carries no department name, target area, count or clear height of "
            "its own, so those are the sheet's to state"
        ))
    if areas_read:
        honesty.append(
            "an area here is the footprint of the zone's Volume@1 boxes on the "
            f"record's own massing grid ({HORIZONTAL_AREA_PER_CELL_M2} m² per "
            "cell, as the record's spatial option declares); nothing else in a "
            "record states an area"
        )
    if without_area:
        honesty.append(
            "no area for zone(s) "
            + ", ".join(sorted(without_area))
            + ": the record draws no Volume@1 box on their own levels, so their "
            "area is unknown rather than zero"
        )
    if zones:
        honesty.append(
            "no clear height: a MassingLevel@1 height is the level's full "
            "height, not the clear height under what spans it"
        )
    sheet["honesty"] = honesty
    return sheet


def totals_of(sheet: Mapping[str, Any]) -> dict[str, Any]:
    """The sheet's three totals: target, mapped, and what maps to nothing.

    Both sums skip the rows that state no number rather than reading a missing
    area as zero — a sheet whose areas are unknown must not total to a
    confident figure. ``count`` multiplies a row: five offices of 12 m² are
    60 m² of target, and one zone each.
    """

    target = 0.0
    mapped = 0.0
    unmapped: list[str] = []
    for _department, space in _spaces_of(sheet):
        count = space.get("count")
        count = count if isinstance(count, int) and not isinstance(count, bool) else 1
        if isinstance(space.get("target_area_m2"), (int, float)) and not isinstance(space.get("target_area_m2"), bool):
            target += float(space["target_area_m2"]) * count
        if isinstance(space.get("mapped_area_m2"), (int, float)) and not isinstance(space.get("mapped_area_m2"), bool):
            mapped += float(space["mapped_area_m2"])
        if not space.get("zone_id"):
            unmapped.append(str(space.get("space_id")))
    return {
        "target_area_m2": target,
        "mapped_area_m2": mapped,
        "unmapped_spaces": unmapped,
    }


def _own_digest(record: StateRecord, honesty: list[str]) -> str | None:
    """The record's binding digest, or ``None`` said out loud."""

    if record.base is None:
        honesty.append(
            "this record is bound to no run, so the sheet names no state "
            "digest: a client cannot send it back against a state it was not given"
        )
        return None
    return record.state_digest


def _program_ref_of(zone: Entity) -> tuple[str, str]:
    """(department, space) as the zone's ``program_node_refs`` says them."""

    for ref in zone.fields.get("program_node_refs", ()):
        if not isinstance(ref, str) or not ref.startswith(PROGRAM_REF_PREFIX):
            continue
        body = ref[len(PROGRAM_REF_PREFIX):]
        department, separator, space = body.partition("/")
        if separator and department and space:
            return department, space
    return UNASSIGNED_DEPARTMENT, zone.entity_id


def _zone_function(
    zone: Entity,
    components: Mapping[str, Entity],
) -> str | None:
    """The semantic kind of the component this zone belongs to, or ``None``.

    A zone names its component either by being parented to it or by carrying a
    ``component_id`` — the same two ways an ``Element@1`` does. A component
    that declares ids rather than a phrase answers with its first role, which
    is a registered id like any other.
    """

    named = zone.fields.get("component_id")
    for candidate in (named if isinstance(named, str) else None, zone.parent_id):
        component = components.get(candidate) if candidate else None
        if component is None:
            continue
        kind = component.fields.get("semantic_kind")
        if isinstance(kind, str) and kind:
            return kind
        for role in component.fields.get("roles", ()):
            if isinstance(role, str) and role:
                return role
    return None


def _zone_area(
    zone: Entity,
    volumes: Mapping[str, Entity],
    honesty: list[str],
) -> float | None:
    """The footprint of the zone's volume boxes, in square metres, or ``None``.

    The union of the boxes' plan rectangles, never their sum: two volumes
    stacked on two levels of one zone are one footprint, and adding them would
    report twice the floor the record draws.
    """

    declared = tuple(zone.fields.get("volume_ids", ()))
    if not declared:
        return None
    levels = {str(item) for item in zone.fields.get("level_ids", ())}
    boxes: list[tuple[int, int, int, int]] = []
    unreadable: list[str] = []
    for volume_id in declared:
        volume = volumes.get(str(volume_id))
        if volume is None:
            unreadable.append(str(volume_id))
            continue
        if levels and not (levels & {str(item) for item in volume.fields.get("level_ids", ())}):
            # A box that stands on none of the zone's levels is not this
            # zone's floor, whatever the zone's volume_ids say.
            continue
        box = _plan_box(volume)
        if box is None:
            unreadable.append(volume.entity_id)
            continue
        boxes.append(box)
    if unreadable:
        honesty.append(
            f"zone {zone.entity_id}: volume(s) "
            + ", ".join(sorted(unreadable))
            + " could not be read as a box, so they are not in its area"
        )
    if not boxes:
        return None
    return float(_union_cells(boxes)) * HORIZONTAL_AREA_PER_CELL_M2


def _plan_box(volume: Entity) -> tuple[int, int, int, int] | None:
    """The volume's plan rectangle as half-open cell ranges, or ``None``.

    ``SiteBounds`` counts cells inclusively (``max - min + 1`` per axis), so
    the half-open upper bound is ``max + 1``. Index 1 is the vertical axis and
    plays no part in a footprint.
    """

    try:
        low = [int(value) for value in volume.fields["min"]]
        high = [int(value) for value in volume.fields["max"]]
    except (KeyError, TypeError, ValueError):
        return None
    if len(low) != 3 or len(high) != 3:
        return None
    if high[0] < low[0] or high[2] < low[2]:
        return None
    return low[0], high[0] + 1, low[2], high[2] + 1


def _union_cells(boxes: Sequence[tuple[int, int, int, int]]) -> int:
    """How many plan cells the union of these half-open rectangles covers.

    Coordinate compression: the union of axis-aligned rectangles is exact over
    the grid their own edges cut, and no cell is counted twice.
    """

    xs = sorted({edge for box in boxes for edge in (box[0], box[1])})
    zs = sorted({edge for box in boxes for edge in (box[2], box[3])})
    total = 0
    for x_low, x_high in zip(xs, xs[1:]):
        for z_low, z_high in zip(zs, zs[1:]):
            covered = any(
                box[0] <= x_low and x_high <= box[1]
                and box[2] <= z_low and z_high <= box[3]
                for box in boxes
            )
            if covered:
                total += (x_high - x_low) * (z_high - z_low)
    return total


def _adjacencies_of(
    record: StateRecord,
    space_of_zone: Mapping[str, str],
    honesty: list[str],
) -> list[dict[str, Any]]:
    """One row per connection relation whose kind a requirement names."""

    kinds = {relation.relation_id: relation.kind for relation in record.relations}
    rows: list[dict[str, Any]] = []
    for connection in record.entities_of("Connection@1"):
        source = str(connection.fields.get("source_zone_id"))
        target = str(connection.fields.get("target_zone_id"))
        for ref in connection.fields.get("relationship_refs", ()):
            relation_id = str(ref).split(":", 1)[-1]
            kind = kinds.get(relation_id)
            requirement = _KIND_REQUIREMENTS.get(str(kind))
            if requirement is None:
                honesty.append(
                    f"connection {connection.entity_id} carries relation "
                    f"{relation_id} of kind {kind}, which is not one of the "
                    f"program requirements ({', '.join(REQUIREMENTS)}); it is "
                    "declared in the record and is not a row of this sheet"
                )
                continue
            rows.append({
                "from_space_id": space_of_zone.get(source, source),
                "to_space_id": space_of_zone.get(target, target),
                "requirement": requirement,
                "relation_id": relation_id,
            })
    return rows


# ---------------------------------------------------------------- sheet -> record


def compile_sheet_operator(
    record: StateRecord, sheet: Mapping[str, Any]
) -> StateRecordOperator:
    """Compile what this sheet adds into the canonical StateRecord operator.

    A sheet space with no ``zone_id`` becomes a ``Space@1`` under its own id,
    holding the program ref that names its department and itself, the levels
    the sheet gave it, and no volume — the massing is not this document's to
    draw. A sheet space that already names a zone changes nothing about that
    zone except a ``program_node_refs`` entry it lacks.

    An adjacency with no ``relation_id`` becomes one ``Relation`` of the kernel
    kind its requirement maps to, plus the ``Connection@1`` that carries it. A
    requirement the kernel vocabulary has no kind for is refused by name; no
    kind is invented for it.

    The record it is given is never mutated, and nothing is deleted from the
    one it returns.
    """

    validate_sheet(sheet)
    declared_state = sheet.get("state_digest")
    if declared_state is None:
        raise ProgramSheetError(
            "state_digest is required to apply a program sheet; derive the sheet "
            "from a bound record before sending it back"
        )
    declared_record = sheet.get("record_digest")
    if declared_record is None:
        raise ProgramSheetError(
            "record_digest is required to apply a program sheet; derive the sheet "
            "from the record before sending it back"
        )
    if (
        str(declared_state) != record.state_digest
        or str(declared_record) != record.digest
    ):
        raise ProgramSheetError("program sheet exact base is stale")
    known = {entity.entity_id for entity in record.entities}
    zones = {
        entity.entity_id
        for entity in record.entities_of("Space@1")
    }
    entities = list(record.entities)
    relations = list(record.relations)
    relation_ids = {relation.relation_id for relation in relations}
    zone_of_space: dict[str, str] = {}

    for department, space in _spaces_of(sheet):
        department_id = str(department["department_id"])
        space_id = str(space["space_id"])
        ref = f"{PROGRAM_REF_PREFIX}{department_id}/{space_id}"
        declared_zone = space.get("zone_id")
        if declared_zone:
            zone_id = str(declared_zone)
            if zone_id not in zones:
                raise ProgramSheetError(
                    f"space {space_id}: zone_id {zone_id!r} names no Space@1 of "
                    "this record; leave it null to have the sheet add one"
                )
            zone_of_space[space_id] = zone_id
            entities = [
                _with_program_ref(entity, ref)
                if entity.entity_id == zone_id
                else entity
                for entity in entities
            ]
            continue
        if space_id in known:
            raise ProgramSheetError(
                f"space {space_id}: the record already holds an entity with "
                "that id, and a sheet never rewrites one; give the space "
                "another id, or name the zone it maps to in zone_id"
            )
        unknown = [
            level_id
            for level_id in space.get("level_ids", ())
            if str(level_id) not in known
        ]
        if unknown:
            raise ProgramSheetError(
                f"space {space_id}: level_ids name no entity of this record: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        entities.append(Entity(
            entity_id=space_id,
            schema="Space@1",
            fields={
                "program_node_refs": [ref],
                "level_ids": [str(item) for item in space.get("level_ids", ())],
                # The sheet states a program, not a massing. A zone it adds
                # occupies no volume until somebody draws one.
                "volume_ids": [],
            },
        ))
        known.add(space_id)
        zones.add(space_id)
        zone_of_space[space_id] = space_id

    for adjacency in sheet.get("adjacencies", ()):
        if adjacency.get("relation_id"):
            continue
        requirement = str(adjacency["requirement"])
        kind = REQUIREMENT_KINDS[requirement]
        if kind is None:
            raise ProgramSheetError(
                f"requirement {requirement!r} has no kind in the kernel "
                f"relation vocabulary, which is: {_RELATION_VOCABULARY}. The "
                "sheet may state it, and applying it would mean inventing a "
                "relation kind; declare the requirement the record can carry, "
                "or leave this row for a human"
            )
        subject = _zone_for(adjacency["from_space_id"], zone_of_space, zones)
        target = _zone_for(adjacency["to_space_id"], zone_of_space, zones)
        if subject == target:
            raise ProgramSheetError(
                f"adjacency {adjacency['from_space_id']} -> "
                f"{adjacency['to_space_id']}: both ends resolve to zone "
                f"{subject}; a relation joins two things"
            )
        relation_id = f"program-{requirement}-{subject}-{target}"
        connection_id = f"connection-{relation_id}"
        for identifier, what in ((relation_id, "relation"), (connection_id, "entity")):
            taken = relation_ids if what == "relation" else known
            if identifier in taken:
                raise ProgramSheetError(
                    f"adjacency {subject} -> {target}: the record already holds "
                    f"a {what} called {identifier!r}; name the relation it "
                    "already has in relation_id rather than adding a second one"
                )
        relations.append(Relation(
            relation_id=relation_id,
            kind=kind,
            subject=subject,
            object=target,
            propagation="revalidate",
            epistemic_status="declared",
        ))
        relation_ids.add(relation_id)
        entities.append(Entity(
            entity_id=connection_id,
            schema="Connection@1",
            fields={
                "source_zone_id": subject,
                "target_zone_id": target,
                "relationship_refs": [f"relation:{relation_id}"],
                "directed": False,
            },
        ))
        known.add(connection_id)
    before = {entity.entity_id: entity for entity in record.entities}
    edits = tuple(
        entity for entity in entities if before.get(entity.entity_id) != entity
    )
    additions = tuple(relations[len(record.relations) :])
    return StateRecordOperator(
        kind=StateRecordEditKind.APPLY_PROGRAM,
        base_record_digest=str(declared_record),
        base_state_digest=str(declared_state),
        entities=edits,
        relations=additions,
    )


def apply_sheet(record: StateRecord, sheet: Mapping[str, Any]) -> StateRecord:
    """Apply a program sheet through the one canonical StateRecord operator."""

    return apply_state_record_operator(record, compile_sheet_operator(record, sheet))


def _with_program_ref(entity: Entity, ref: str) -> Entity:
    """The zone with this program ref added, or the zone unchanged.

    The only field of an existing entity a sheet may touch. Existing refs keep
    their order; the new one goes on the end, so applying a sheet twice is the
    same record.
    """

    existing = [str(item) for item in entity.fields.get("program_node_refs", ())]
    if ref in existing:
        return entity
    return replace(
        entity,
        fields={**entity.fields, "program_node_refs": [*existing, ref]},
    )


def _zone_for(
    space_id: object,
    zone_of_space: Mapping[str, str],
    zones: Iterable[str],
) -> str:
    """The zone one end of an adjacency names, or a refusal naming the end."""

    key = str(space_id)
    if key in zone_of_space:
        return zone_of_space[key]
    if key in set(zones):
        # An adjacency may name a zone directly, which is what the derived
        # sheet does for a zone no department row claimed.
        return key
    raise ProgramSheetError(
        f"adjacency end {key!r} is neither a space of this sheet nor a "
        "Space@1 of this record"
    )


# ---------------------------------------------------------------- the sheet's own shape


def validate_sheet(sheet: Mapping[str, Any]) -> None:
    """Refuse a sheet this module cannot read, saying which row and why.

    Every refusal is the sheet's, never the record's: a function that resolves
    to no registered id, a requirement outside the four, a space id that is
    not a portable identifier. The record's own refusals come later and are
    about the record.
    """

    if not isinstance(sheet, Mapping):
        raise ProgramSheetError(
            f"a program sheet is a mapping, not {type(sheet).__name__}"
        )
    if sheet.get("schema") != PROGRAM_SHEET_SCHEMA:
        raise ProgramSheetError(
            f"a program sheet declares schema {PROGRAM_SHEET_SCHEMA!r}, not "
            f"{sheet.get('schema')!r}"
        )
    departments = sheet.get("departments", ())
    if not isinstance(departments, (list, tuple)):
        raise ProgramSheetError("departments must be a list")
    seen: set[str] = set()
    for department in departments:
        if not isinstance(department, Mapping):
            raise ProgramSheetError("each department is a mapping")
        _identifier(department.get("department_id"), "department_id")
        spaces = department.get("spaces", ())
        if not isinstance(spaces, (list, tuple)):
            raise ProgramSheetError(
                f"department {department['department_id']}: spaces must be a list"
            )
        for space in spaces:
            if not isinstance(space, Mapping):
                raise ProgramSheetError("each space is a mapping")
            space_id = _identifier(space.get("space_id"), "space_id")
            if space_id in seen:
                raise ProgramSheetError(
                    f"space {space_id} appears twice: one space id names one row"
                )
            seen.add(space_id)
            _function(space.get("function"), space_id)
            _optional_number(space.get("target_area_m2"), space_id, "target_area_m2")
            _optional_number(space.get("clear_height_m"), space_id, "clear_height_m")
            count = space.get("count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ProgramSheetError(
                    f"space {space_id}: count is a whole number of at least 1, "
                    f"not {count!r}"
                )
            levels = space.get("level_ids", ())
            if not isinstance(levels, (list, tuple)) or any(
                not isinstance(item, str) or not item for item in levels
            ):
                raise ProgramSheetError(
                    f"space {space_id}: level_ids is a list of entity ids"
                )
    adjacencies = sheet.get("adjacencies", ())
    if not isinstance(adjacencies, (list, tuple)):
        raise ProgramSheetError("adjacencies must be a list")
    for adjacency in adjacencies:
        if not isinstance(adjacency, Mapping):
            raise ProgramSheetError("each adjacency is a mapping")
        for end in ("from_space_id", "to_space_id"):
            if not isinstance(adjacency.get(end), str) or not adjacency[end]:
                raise ProgramSheetError(f"an adjacency needs a {end}")
        requirement = adjacency.get("requirement")
        if requirement not in REQUIREMENT_KINDS:
            raise ProgramSheetError(
                f"requirement {requirement!r} is not one of "
                f"{', '.join(REQUIREMENTS)}"
            )


def _spaces_of(
    sheet: Mapping[str, Any],
) -> Iterable[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Every (department, space) pair of the sheet, in the sheet's own order."""

    for department in sheet.get("departments", ()):
        if not isinstance(department, Mapping):
            continue
        for space in department.get("spaces", ()):
            if isinstance(space, Mapping):
                yield department, space


def _identifier(value: object, field_name: str) -> str:
    try:
        return require_identifier(value, field_name)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ProgramSheetError(str(exc)) from exc


def _function(value: object, space_id: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ProgramSheetError(
            f"space {space_id}: function is a registered role id or alias, or null"
        )
    if resolve_semantic_kind(value) is None:
        near = ", ".join(suggest_semantic(value)) or "none close"
        raise ProgramSheetError(
            f"space {space_id}: function {value!r} is not a registered role, "
            f"condition or alias; nearest: {near}"
        )


def _optional_number(value: object, space_id: str, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ProgramSheetError(
            f"space {space_id}: {field_name} is a non-negative number of "
            f"metres, or null — not {value!r}"
        )
