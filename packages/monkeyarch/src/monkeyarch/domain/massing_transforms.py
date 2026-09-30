"""The moves that make one massing option out of another, on its ``SchematicPack@1``.

``add_floor``, ``remove_floor``, ``shift_volume``, ``scale_volume`` and
``split_volume`` are the moves a person makes with a mouse; ``pack`` takes a
whole pack payload the client sends and validates it with
``SchematicPack.from_dict``. A generative agent plugs into ``pack`` and nowhere
else, which is why no other transform takes free-form geometry. The vocabulary
is closed: a seventh move would be a second way of saying one of these.

Each move is plain arithmetic on the pack in whole massing cells, and nothing
here measures, retains or runs it: the metrics are
``monkeyarch.domain.massing_metrics``'s, and the Project Runtime holds the
options and runs the one a person selects (#519). A move that cannot be made
is a ``MassingTransformError`` naming its refusal; nothing was changed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from archflow.state.state_record import SchematicPack, StateRecordError

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

_PLAN_AXES = {"x": 0, "z": 2}


class MassingTransformError(ValueError):
    """A move that cannot be made on this pack: ``code`` names the refusal, the message says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def require_transform(transform: str) -> None:
    """Refuse a name that is not one of the six moves, naming the whole vocabulary."""

    if transform not in TRANSFORMS:
        raise MassingTransformError(
            "UNKNOWN_TRANSFORM",
            f"{transform!r} is not a massing transform. The whole vocabulary "
            "is " + ", ".join(TRANSFORMS) + ".",
        )


# ---------------------------------------------------------------- the transforms


def transformed(
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
        raise MassingTransformError(
            "PACK_REQUIRED",
            "the pack transform needs a pack: a SchematicPack@1 payload under "
            "'pack'.",
        )
    try:
        return SchematicPack.from_dict(payload)
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        raise MassingTransformError(
            "PACK_INVALID", f"that pack is not a SchematicPack@1: {exc}"
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
        raise MassingTransformError(
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
        raise MassingTransformError(
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
        raise MassingTransformError(
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
        raise MassingTransformError(
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
        raise MassingTransformError(
            "UNKNOWN_AXIS",
            f"a split runs along x or z, not {along!r}: y is up, and a floor "
            "is added with add_floor.",
        )
    index = _PLAN_AXES[along]
    at = _whole(parameters, "at")
    low, high = int(volume["min"][index]), int(volume["max"][index])
    if not low < at <= high:
        raise MassingTransformError(
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
        raise MassingTransformError(
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
    raise MassingTransformError(
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
        raise MassingTransformError(
            "WHOLE_CELLS",
            f"{key} is a whole number of massing cells (one cell is one "
            f"square metre in plan), not {value!r}.",
        )
    return int(value)


def _positive(parameters: Mapping[str, Any], key: str) -> float:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise MassingTransformError(
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
