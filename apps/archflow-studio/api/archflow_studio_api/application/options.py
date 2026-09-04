"""Several massings on the table at once, each with its numbers, one chosen.

What an architect does at the massing stage is hold two or three shapes beside
each other and read them off: how much ground, how much floor, how many
storeys, how tall, how much of it the program actually asked for. The record
could always carry *one* massing; there was no way to hold a second one beside
it and nothing that measured either. This module is that table.

**An option is a record, not a picture.** Each one is the current record's own
``SchematicPack@1`` with one deterministic transform applied, and it is
measured by ``state.massing_metrics`` on the successor record that selecting
it would run — so the numbers on the card are the numbers of the thing that
would be built, not of an approximation of it. Nothing here computes geometry,
and nothing here writes the authored record.

**The transforms are closed, and one of them is a socket.** ``add_floor``,
``remove_floor``, ``shift_volume``, ``scale_volume`` and ``split_volume`` are
the moves a person makes with a mouse; ``pack`` takes a whole pack payload the
client sends and validates it with ``SchematicPack.from_dict``. A generative
agent plugs into ``pack`` and nowhere else, which is why no other transform
takes free-form geometry.

**What is retained, and what is not.** Each option is retained as its own run,
``option-NNN``, holding the kernel's own ``SpatialOptionProposal@2`` under the
existing ``selected-spatial-option`` kind — the same record the runner writes
for the option it executes. The metrics ride on the option in this process
only: there is no retained record kind whose payload is a set of measurements,
and inventing one here would be a vocabulary this module does not own.
Selecting an option runs it as a candidate through the studio's one candidate
path, and the run the runner makes is where the selection becomes a fact
anybody else can read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from typing import Any, Mapping, Sequence

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import SELECTED_SPATIAL_OPTION
from archflow.state.massing_metrics import (
    EnvelopeFinding,
    MassingMetrics,
    MassingMetricsError,
    envelope_check,
    massing_metrics,
)
from archflow.state.spatial import SpatialProposalError
from archflow.state.state_record import (
    SchematicPack,
    StateRecord,
    StateRecordError,
    schematic_pack_of,
    schematic_proposal,
    volume_boxes_of,
)

from ..transport.errors import StudioError
from .binding import ProjectBinding
from .candidate import massing_successor

# The six moves an option can be made by. Closed: a seventh would be a second
# way of saying one of these, and ``pack`` is already the one that takes
# anything a client can express.
ADD_FLOOR = "add_floor"
REMOVE_FLOOR = "remove_floor"
SHIFT_VOLUME = "shift_volume"
SCALE_VOLUME = "scale_volume"
SPLIT_VOLUME = "split_volume"
PACK = "pack"
TRANSFORMS = (ADD_FLOOR, REMOVE_FLOOR, SHIFT_VOLUME, SCALE_VOLUME, SPLIT_VOLUME, PACK)

# What an option's run is called. The number is this process's counter: the
# options on the table are this process's memory, and their runs are named in
# the order it made them.
RUN_PREFIX = "option"

# What the store says about itself on every option it hands out.
PERSISTENCE = (
    "the option itself is in-memory (not version history); its pack is "
    "retained in its own run"
)

_PLAN_AXES = {"x": 0, "z": 2}


@dataclass(frozen=True, slots=True)
class MassingOption:
    """One massing on the table: how it was made, what it is, what it measures."""

    option_id: str
    run_id: str
    label: str
    transform: str
    parameters: Mapping[str, Any]
    base_state_digest: str
    pack: SchematicPack
    record: StateRecord
    metrics: MassingMetrics
    envelope_findings: tuple[EnvelopeFinding, ...]
    # The retained ``selected-spatial-option`` this option's run holds.
    record_ref: str
    honesty: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OptionsTable:
    """The baseline and every option beside it, measured the same way."""

    state_digest: str
    baseline: MassingMetrics
    options: tuple[MassingOption, ...]


class OptionStore:
    """The massing options this process holds, in the order they were made.

    In memory, like the proposal store beside it, and lost on restart — the
    runs the options were retained in are not. Locked, because options are
    made on request threads and read on others.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._options: dict[str, MassingOption] = {}
        self._next = 1

    def reserve(self) -> tuple[str, str]:
        """The next option id and the run id it will be retained under."""

        with self._lock:
            number = self._next
            self._next += 1
        return f"{RUN_PREFIX}-{number:03d}", f"{RUN_PREFIX}-{number:03d}"

    def add(self, option: MassingOption) -> MassingOption:
        with self._lock:
            self._options[option.option_id] = option
        return option

    def get(self, option_id: str) -> MassingOption:
        with self._lock:
            option = self._options.get(option_id)
        if option is None:
            raise StudioError(
                404,
                "OPTION_NOT_FOUND",
                f"no massing option {option_id} in this process. Options live "
                "in memory and are lost on restart; POST /api/options makes "
                "one, GET /api/options lists the ones this process holds.",
            )
        return option

    def all(self) -> tuple[MassingOption, ...]:
        with self._lock:
            return tuple(
                self._options[key] for key in sorted(self._options)
            )


# ---------------------------------------------------------------- reading the record


@dataclass(frozen=True, slots=True)
class RecordVolume:
    """One ``Volume@1`` as the panel needs it: its box and the ground it covers."""

    volume_id: str
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]
    level_ids: tuple[str, ...]
    footprint_m2: float


@dataclass(frozen=True, slots=True)
class RecordMassing:
    """The record's massing as the options panel reads it, before any transform."""

    volumes: tuple[RecordVolume, ...]
    metrics: MassingMetrics


def record_massing(record: StateRecord) -> RecordMassing:
    """The volumes a client can name in a transform, with the current metrics.

    ``GET /api/state/frame`` answers the frame an *element* is positioned
    against — levels and axes. A massing volume is positioned against neither;
    it declares its own box, so it is a different resource rather than another
    field of the frame.
    """

    metrics = massing_metrics(record)
    boxes = volume_boxes_of(record)
    per_volume = {
        entity.entity_id: tuple(str(v) for v in entity.fields.get("level_ids", ()))
        for entity in record.entities_of("Volume@1")
    }
    volumes = tuple(
        RecordVolume(
            volume_id=volume_id,
            minimum=low,
            maximum=high,
            level_ids=per_volume.get(volume_id, ()),
            footprint_m2=_box_area(low, high),
        )
        for volume_id, (low, high) in sorted(boxes.items())
    )
    return RecordMassing(volumes=volumes, metrics=metrics)


def baseline_pack(record: StateRecord) -> SchematicPack:
    """The record's own massing as a pack, or a refusal saying it declares none."""

    try:
        pack = schematic_pack_of(record)
    except StateRecordError as exc:
        raise StudioError(422, "STATE_RECORD_INVALID", str(exc)) from exc
    if pack is None:
        raise StudioError(
            422,
            "NO_MASSING",
            "this record declares no massing: an option needs Volume@1, "
            "Space@1 and MassingLevel@1 entities together, and the record "
            "carries fewer than all three. GET /api/state/volumes shows what "
            "it does carry.",
        )
    return pack


# ---------------------------------------------------------------- making one option


def make_option(
    binding: ProjectBinding,
    store: OptionStore,
    record: StateRecord,
    *,
    state_digest: str,
    transform: str,
    parameters: Mapping[str, Any],
    label: str | None = None,
    envelope: Mapping[str, Any] | None = None,
    program_targets: Mapping[str, float] | None = None,
) -> MassingOption:
    """Apply one transform, measure the result, retain it, and put it on the table."""

    if transform not in TRANSFORMS:
        raise StudioError(
            422,
            "UNKNOWN_TRANSFORM",
            f"{transform!r} is not a massing transform. The whole vocabulary "
            "is " + ", ".join(TRANSFORMS) + ".",
        )
    base = baseline_pack(record)
    option_id, run_id = store.reserve()
    pack, honesty = _transformed(base, transform, parameters, option_id)
    successor, metrics, findings, more = _measure(
        record, pack, envelope=envelope, program_targets=program_targets
    )
    record_ref = _retain(binding, run_id, pack)
    return store.add(
        MassingOption(
            option_id=option_id,
            run_id=run_id,
            label=label or pack.label,
            transform=transform,
            parameters=dict(parameters),
            base_state_digest=state_digest,
            pack=pack,
            record=successor,
            metrics=metrics,
            envelope_findings=findings,
            record_ref=record_ref,
            honesty=tuple(honesty) + tuple(more),
        )
    )


def _measure(
    record: StateRecord,
    pack: SchematicPack,
    *,
    envelope: Mapping[str, Any] | None,
    program_targets: Mapping[str, float] | None,
) -> tuple[StateRecord, MassingMetrics, tuple[EnvelopeFinding, ...], tuple[str, ...]]:
    """Measure the option on the record selecting it would actually run.

    The successor is built here rather than at selection time for one reason:
    a transform that produces a massing the kernel refuses — a volume with no
    level, a zone with no volume, a component owning a volume that is gone —
    must be refused now, with the request that made it, and not minutes later
    as a failed run whose reason is somewhere else.
    """

    try:
        successor = massing_successor(record, pack)
        # The kernel's own validation of the option, run here so a refusal
        # arrives as a 422 about this transform. The runner would build the
        # same value from the same record.
        schematic_proposal(pack)
        metrics = massing_metrics(successor, program_targets=program_targets)
        findings = envelope_check(successor, envelope) if envelope else ()
    except (StateRecordError, SpatialProposalError, MassingMetricsError, TypeError, ValueError) as exc:
        raise StudioError(
            422,
            "MASSING_REFUSED",
            f"the kernel refuses this massing: {exc}",
        ) from exc
    return successor, metrics, findings, ()


def _retain(
    binding: ProjectBinding, run_id: str, pack: SchematicPack
) -> str:
    """Retain the option in its own run, as the kernel's own spatial option.

    ``selected-spatial-option`` is the kind the runner already writes for the
    option a run executed, and ``schematic_proposal`` is the value it writes:
    an option this process is holding is the same thing, retained before
    anybody has chosen it. Nothing new is invented for it — the studio has no
    record kind of its own for a set of measurements, so the metrics stay on
    the option in this process and are not written here.
    """

    run = binding.repository.create_run(run_id)
    return binding.repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=run_id
        ),
        record_kind=SELECTED_SPATIAL_OPTION,
        payload=schematic_proposal(pack).to_dict(),
    ).uri


# ---------------------------------------------------------------- the transforms


def _transformed(
    pack: SchematicPack,
    transform: str,
    parameters: Mapping[str, Any],
    option_id: str,
) -> tuple[SchematicPack, tuple[str, ...]]:
    """One deterministic move on the pack, and what the move could not say."""

    if transform == PACK:
        sent = _explicit_pack(parameters)
        return replace(sent, option_id=option_id), (
            "this pack was sent by the client and validated by the kernel; "
            "the studio derived none of it",
        )
    honesty: list[str] = []
    if transform == ADD_FLOOR:
        pack = _add_floor(pack)
    elif transform == REMOVE_FLOOR:
        pack = _remove_floor(pack)
    elif transform == SHIFT_VOLUME:
        pack = _shift_volume(pack, parameters)
    elif transform == SCALE_VOLUME:
        pack = _scale_volume(pack, parameters)
    else:
        pack = _split_volume(pack, parameters)
        honesty.append(
            "the declared option.footprint_cells are carried through a split "
            "unchanged; the measured footprint is footprintM2"
        )
    if transform in (SHIFT_VOLUME, SCALE_VOLUME):
        honesty.append(
            "the declared option.footprint_cells are carried unchanged; the "
            "plan the volumes actually cover is footprintM2"
        )
    return replace(pack, option_id=option_id, label=f"{pack.label} ({transform})"), tuple(honesty)


def _explicit_pack(parameters: Mapping[str, Any]) -> SchematicPack:
    """A whole pack the client sent, read by the kernel's own reader.

    This is the socket a generative agent plugs into: whatever produces a
    massing, it hands over a ``SchematicPack@1`` and the kernel decides
    whether it is one.
    """

    payload = parameters.get("pack")
    if not isinstance(payload, Mapping):
        raise StudioError(
            422,
            "PACK_REQUIRED",
            "the pack transform needs a pack: a SchematicPack@1 payload under "
            "'pack'.",
        )
    try:
        return SchematicPack.from_dict(payload)
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        raise StudioError(
            422, "PACK_INVALID", f"that pack is not a SchematicPack@1: {exc}"
        ) from exc


def _add_floor(pack: SchematicPack) -> SchematicPack:
    """One more massing level on top, the same height, carrying the top floor's volumes."""

    levels = _levels(pack)
    top = levels[-1]
    base_y = int(top["base_y"]) + int(top["height"])
    height = int(top["height"])
    level_id = _fresh(
        {str(level["level_id"]) for level in levels}
        | {str(volume["volume_id"]) for volume in pack.volumes}
        | {str(zone["zone_id"]) for zone in pack.zones},
        "massing-level",
    )
    raised = {
        str(volume["volume_id"])
        for volume in pack.volumes
        if str(top["level_id"]) in tuple(volume["level_ids"])
    }
    if not raised:
        raise StudioError(
            422,
            "NOTHING_TO_RAISE",
            f"no volume stands on {top['level_id']}, the topmost massing "
            "level, so a floor above it would carry nothing.",
        )
    volumes = tuple(
        volume
        if str(volume["volume_id"]) not in raised
        else {
            **volume,
            "max": [
                volume["max"][0],
                max(int(volume["max"][1]), base_y + height - 1),
                volume["max"][2],
            ],
            "level_ids": [*volume["level_ids"], level_id],
        }
        for volume in pack.volumes
    )
    zones = tuple(
        zone
        if not (set(zone["volume_ids"]) & raised)
        else {**zone, "level_ids": [*zone["level_ids"], level_id]}
        for zone in pack.zones
    )
    return replace(
        pack,
        levels=(*pack.levels, {"level_id": level_id, "base_y": base_y, "height": height}),
        volumes=volumes,
        zones=zones,
    )


def _remove_floor(pack: SchematicPack) -> SchematicPack:
    """Drop the topmost massing level, and whatever it alone was holding up."""

    levels = _levels(pack)
    if len(levels) < 2:
        raise StudioError(
            422,
            "LAST_FLOOR",
            "this massing has one floor: removing it would leave a building "
            "with none, which the kernel refuses rather than represents.",
        )
    top = str(levels[-1]["level_id"])
    ceiling = int(levels[-1]["base_y"]) - 1
    volumes = []
    dropped = set()
    for volume in pack.volumes:
        remaining = [level_id for level_id in volume["level_ids"] if level_id != top]
        if not remaining:
            dropped.add(str(volume["volume_id"]))
            continue
        volumes.append(
            {
                **volume,
                "max": [volume["max"][0], min(int(volume["max"][1]), ceiling), volume["max"][2]],
                "level_ids": remaining,
            }
        )
    if not volumes:
        raise StudioError(
            422,
            "NOTHING_LEFT",
            f"every volume stands only on {top}: removing it would leave no "
            "massing at all.",
        )
    below = str(levels[-2]["level_id"])
    zones = []
    for zone in pack.zones:
        volume_ids = [v for v in zone["volume_ids"] if v not in dropped]
        if not volume_ids:
            continue
        zones.append(
            {
                **zone,
                # A zone that was on the removed level alone comes down to the
                # one below it: its volumes are still there, and a zone with
                # no level is a value the kernel refuses.
                "level_ids": [l for l in zone["level_ids"] if l != top] or [below],
                "volume_ids": volume_ids,
            }
        )
    if not zones:
        raise StudioError(
            422,
            "NOTHING_LEFT",
            f"every zone is on {top} alone: removing it would leave no zone "
            "for the massing that remains.",
        )
    kept_zones = {str(zone["zone_id"]) for zone in zones}
    return replace(
        pack,
        levels=tuple(level for level in pack.levels if str(level["level_id"]) != top),
        volumes=tuple(volumes),
        zones=tuple(zones),
        connections=tuple(
            connection
            for connection in pack.connections
            if connection["source_zone_id"] in kept_zones
            and connection["target_zone_id"] in kept_zones
        ),
        components=tuple(
            component
            if not (set(component.volume_ids) & dropped)
            else replace(
                component,
                volume_ids=tuple(v for v in component.volume_ids if v not in dropped),
            )
            for component in pack.components
        ),
    )


def _shift_volume(pack: SchematicPack, parameters: Mapping[str, Any]) -> SchematicPack:
    """Move one volume in plan by whole cells; its height and levels are untouched."""

    volume = _named_volume(pack, parameters)
    dx, dz = _whole(parameters, "dx"), _whole(parameters, "dz")
    moved = {
        **volume,
        "min": [volume["min"][0] + dx, volume["min"][1], volume["min"][2] + dz],
        "max": [volume["max"][0] + dx, volume["max"][1], volume["max"][2] + dz],
    }
    return _with_volume(pack, moved)


def _scale_volume(pack: SchematicPack, parameters: Mapping[str, Any]) -> SchematicPack:
    """Scale one volume in plan about its own centre, in whole cells.

    A span is a whole number of cells and stays one: the scaled span is
    rounded to the nearest cell and never below one, so a factor small enough
    to erase a volume leaves a single cell instead of an empty box the kernel
    would refuse.
    """

    volume = _named_volume(pack, parameters)
    low, high = list(volume["min"]), list(volume["max"])
    for axis, factor_key in (("x", "sx"), ("z", "sz")):
        index = _PLAN_AXES[axis]
        factor = _positive(parameters, factor_key)
        span = int(high[index]) - int(low[index]) + 1
        scaled = max(1, int(round(span * factor)))
        centre_twice = int(low[index]) + int(high[index])
        low[index] = (centre_twice - scaled + 1) // 2
        high[index] = low[index] + scaled - 1
    return _with_volume(pack, {**volume, "min": low, "max": high})


def _split_volume(pack: SchematicPack, parameters: Mapping[str, Any]) -> SchematicPack:
    """Cut one volume in two along a plan axis; the far part becomes a new volume.

    The near part keeps the volume's id, so every zone, component and
    component ``volume_ids`` that named it still does. The far part is a new
    volume, and it is given to the same zone and the same semantic owner —
    the kernel requires exactly one owner per volume and would refuse a
    half-building whose other half belongs to nobody.
    """

    volume = _named_volume(pack, parameters)
    along = parameters.get("along")
    if along not in _PLAN_AXES:
        raise StudioError(
            422,
            "UNKNOWN_AXIS",
            f"a split runs along x or z, not {along!r}: y is up, and a floor "
            "is added with add_floor.",
        )
    index = _PLAN_AXES[along]
    at = _whole(parameters, "at")
    low, high = int(volume["min"][index]), int(volume["max"][index])
    if not low < at <= high:
        raise StudioError(
            422,
            "SPLIT_OUTSIDE_VOLUME",
            f"{volume['volume_id']} runs from {low} to {high} on {along}; a "
            f"split at {at} would leave one of the two parts empty. The cut "
            f"is the first cell of the far part, so it lies in {low + 1}..{high}.",
        )
    volume_id = str(volume["volume_id"])
    far_id = _fresh(
        {str(item["volume_id"]) for item in pack.volumes}
        | {str(item["zone_id"]) for item in pack.zones}
        | {str(item["level_id"]) for item in pack.levels},
        f"{volume_id}-far",
    )
    near_max = list(volume["max"])
    near_max[index] = at - 1
    far_min = list(volume["min"])
    far_min[index] = at
    near = {**volume, "max": near_max}
    far = {**volume, "volume_id": far_id, "min": far_min}
    return replace(
        pack,
        volumes=tuple(
            near if str(item["volume_id"]) == volume_id else item
            for item in pack.volumes
        )
        + (far,),
        zones=tuple(
            zone
            if volume_id not in zone["volume_ids"]
            else {**zone, "volume_ids": [*zone["volume_ids"], far_id]}
            for zone in pack.zones
        ),
        components=tuple(
            component
            if volume_id not in component.volume_ids
            else replace(component, volume_ids=(*component.volume_ids, far_id))
            for component in pack.components
        ),
    )


# ---------------------------------------------------------------- small readers


def _levels(pack: SchematicPack) -> list[dict]:
    levels = sorted(
        (dict(level) for level in pack.levels),
        key=lambda level: (int(level["base_y"]), str(level["level_id"])),
    )
    if not levels:
        raise StudioError(
            422,
            "NO_MASSING",
            "this massing declares no MassingLevel@1: there is no floor to "
            "add one above or to take one away from.",
        )
    return levels


def _named_volume(pack: SchematicPack, parameters: Mapping[str, Any]) -> dict:
    volume_id = parameters.get("volume_id")
    for volume in pack.volumes:
        if str(volume["volume_id"]) == volume_id:
            return dict(volume)
    known = ", ".join(sorted(str(v["volume_id"]) for v in pack.volumes)) or "none"
    raise StudioError(
        422,
        "UNKNOWN_VOLUME",
        f"this massing carries no volume {volume_id!r}. It carries: {known}.",
    )


def _with_volume(pack: SchematicPack, volume: Mapping[str, Any]) -> SchematicPack:
    return replace(
        pack,
        volumes=tuple(
            dict(volume) if str(item["volume_id"]) == str(volume["volume_id"]) else item
            for item in pack.volumes
        ),
    )


def _whole(parameters: Mapping[str, Any], key: str) -> int:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != int(value):
        raise StudioError(
            422,
            "WHOLE_CELLS",
            f"{key} is a whole number of massing cells (one cell is one "
            f"square metre in plan), not {value!r}.",
        )
    return int(value)


def _positive(parameters: Mapping[str, Any], key: str) -> float:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise StudioError(
            422,
            "POSITIVE_FACTOR",
            f"{key} is a positive scale factor, not {value!r}.",
        )
    return float(value)


def _fresh(taken: set[str], stem: str) -> str:
    if stem not in taken:
        return stem
    index = 2
    while f"{stem}-{index}" in taken:
        index += 1
    return f"{stem}-{index}"


def _box_area(low: Sequence[float], high: Sequence[float]) -> float:
    """One volume's own plan area, in the same whole cells the metrics count."""

    if any(float(v) != int(v) for v in (low[0], low[2], high[0], high[2])):
        return 0.0
    return float((int(high[0]) - int(low[0]) + 1) * (int(high[2]) - int(low[2]) + 1))
