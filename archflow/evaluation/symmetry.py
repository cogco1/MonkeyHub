"""Axial symmetry measurement over realized scene bounds.

The framework owns measurement mechanics only: the axis value, the axis
index, and the subject groups come from one exact project. Each named
group is the set of physical scene objects bound to the given semantic
binding ids; the finding reports the group's aggregate bounds center and
its signed offset from the axis plane. A criterion supplied by the
project decides pass or fail — the measurement never does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


class SymmetryMeasurementError(ValueError):
    """The measurement request or scene payload is invalid."""


@dataclass(frozen=True, slots=True)
class GroupSymmetryFinding:
    """One named group's aggregate center offset from the axis plane."""

    group_id: str
    object_ids: tuple[str, ...]
    minimum: float
    maximum: float
    center: float
    offset: float

    SCHEMA = "AxialGroupSymmetryFinding@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "group_id": self.group_id,
            "object_ids": list(self.object_ids),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "center": self.center,
            "offset": self.offset,
        }


def _bounds(item: Mapping[str, object]) -> tuple[Sequence[float], Sequence[float]]:
    bounds = item.get("bounds")
    if not isinstance(bounds, Mapping):
        raise SymmetryMeasurementError(
            f"scene object {item.get('object_id')!r} carries no bounds"
        )
    minimum = bounds.get("minimum")
    maximum = bounds.get("maximum")
    if (
        not isinstance(minimum, Sequence)
        or not isinstance(maximum, Sequence)
        or len(minimum) != 3
        or len(maximum) != 3
    ):
        raise SymmetryMeasurementError(
            f"scene object {item.get('object_id')!r} bounds are malformed"
        )
    return minimum, maximum


def axial_group_offsets(
    scene_objects: Iterable[Mapping[str, object]],
    *,
    axis_value: float,
    axis_index: int,
    groups: Mapping[str, Sequence[str]],
    physical_only: bool = True,
) -> tuple[GroupSymmetryFinding, ...]:
    """Measure each named group's center offset from the axis plane.

    ``groups`` maps a caller-chosen group id to the semantic binding ids
    whose bound physical objects constitute that group. A group that
    matches no object is a typed failure — silence would read as
    symmetric.
    """

    if axis_index not in (0, 1, 2):
        raise SymmetryMeasurementError("axis_index must be 0, 1, or 2")
    if not groups:
        raise SymmetryMeasurementError("at least one subject group required")
    objects = list(scene_objects)
    findings = []
    for group_id in sorted(groups):
        binding_ids = set(groups[group_id])
        if not binding_ids:
            raise SymmetryMeasurementError(
                f"group {group_id!r} names no binding ids"
            )
        member_ids = []
        low = None
        high = None
        for item in objects:
            if physical_only and not item.get("physical", True):
                continue
            bound = set(item.get("semantic_binding_ids", ()) or ())
            if not bound & binding_ids:
                continue
            minimum, maximum = _bounds(item)
            member_ids.append(str(item["object_id"]))
            value_low = float(minimum[axis_index])
            value_high = float(maximum[axis_index])
            low = value_low if low is None else min(low, value_low)
            high = value_high if high is None else max(high, value_high)
        if low is None or high is None:
            raise SymmetryMeasurementError(
                f"group {group_id!r} matches no physical scene object"
            )
        center = (low + high) / 2.0
        findings.append(
            GroupSymmetryFinding(
                group_id=group_id,
                object_ids=tuple(sorted(member_ids)),
                minimum=low,
                maximum=high,
                center=center,
                offset=center - float(axis_value),
            )
        )
    return tuple(findings)
