"""What a massing option measures: footprint, floor area, floors, height.

An architect choosing between spatial options is choosing between numbers —
how much ground each one takes, how much floor area it yields, how many floors
it stacks, how tall it stands, and how much of that area the program actually
asked for. Until now the record carried the massing and nothing measured it,
so the options an architect could hold side by side had no quantities on them
at all.

**The frame.** Everything here reads the kernel's massing frame and adds no
second one. A ``Volume@1`` box is two coordinate triples in the voxel lattice
``state.spatial.SiteBounds`` defines: **x and z are plan, y is up**, both ends
*inclusive* cells (``SiteBounds.volume`` multiplies ``max - min + 1`` per
axis). One plan cell is one square metre, because ``schematic_proposal``
declares ``SpatialGridBasis(horizontal_area_per_cell=1.0,
area_unit="square_metres")`` for every option it builds, and one cell of ``y``
is one metre by the same lattice. The boxes themselves are read by
``state.record.volume_boxes_of`` — the one reader of that field, shared with
``runtime.project_runner``'s relation check — and never re-derived here.

**A level's top face.** ``SpatialLevel.top_y`` is ``base_y + height - 1``: the
topmost cell a level *occupies*. A height in metres is the distance to the
face above that cell, so ``height_m`` measures ``max(base_y + height) -
min(base_y)`` and a single level of height 4 is 4 m tall rather than 3. The
two conventions are stated here so a reader can see they are the same lattice
counted two ways, not two frames.

Nothing here decides anything and nothing here writes. Every measurement that
could not be made is a line in ``honesty`` rather than a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from archflow.state.state_record import StateRecord, volume_boxes_of

# One plan cell of the massing lattice, in square metres. The same number
# ``schematic_proposal`` puts on every option's ``SpatialGridBasis``; named
# here so a reading of it is traceable to that declaration rather than to a
# literal 1 somewhere in an arithmetic expression.
CELL_AREA_M2 = 1.0

# The three findings ``envelope_check`` can make, and the whole vocabulary of
# them. A fourth would be a second way of saying one of these.
VOLUME_OUTSIDE_ENVELOPE = "volume_outside_envelope"
HEIGHT_EXCEEDED = "height_exceeded"
FAR_EXCEEDED = "far_exceeded"
FINDING_CODES = (VOLUME_OUTSIDE_ENVELOPE, HEIGHT_EXCEEDED, FAR_EXCEEDED)

_AXES = ((0, "x"), (1, "y"), (2, "z"))


class MassingMetricsError(ValueError):
    """A measurement the record or the envelope makes impossible to state."""


@dataclass(frozen=True, slots=True)
class LevelFootprint:
    """One massing level and the plan area the volumes on it cover."""

    level_id: str
    base_y: float
    height: float
    footprint_m2: float


@dataclass(frozen=True, slots=True)
class MassingMetrics:
    """One option's quantities, and what could not be measured.

    ``footprint_m2`` is the union of every volume's plan rectangle — the
    ground the whole massing covers, counted once where volumes overlap.
    ``gross_floor_area_m2`` is the sum of the per-level footprints, so a
    volume that spans three levels contributes its plan area three times, and
    that is the number FAR is checked against.
    """

    footprint_m2: float
    gross_floor_area_m2: float
    floor_count: int
    height_m: float
    efficiency: float | None
    per_level: tuple[LevelFootprint, ...]
    honesty: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "footprint_m2": self.footprint_m2,
            "gross_floor_area_m2": self.gross_floor_area_m2,
            "floor_count": self.floor_count,
            "height_m": self.height_m,
            "efficiency": self.efficiency,
            "per_level": [
                {
                    "level_id": level.level_id,
                    "base_y": level.base_y,
                    "height": level.height,
                    "footprint_m2": level.footprint_m2,
                }
                for level in self.per_level
            ],
            "honesty": list(self.honesty),
        }


@dataclass(frozen=True, slots=True)
class EnvelopeFinding:
    """One way this massing leaves the envelope it was given, with the numbers.

    ``subject`` is the volume a geometric finding is about and ``None`` for a
    finding about the massing as a whole. ``measured`` and ``limit`` are the
    two numbers the finding compares, always in that order, so a reader never
    has to parse ``detail`` to know by how much.
    """

    code: str
    subject: str | None
    detail: str
    measured: float
    limit: float

    def __post_init__(self) -> None:
        if self.code not in FINDING_CODES:
            raise MassingMetricsError(
                f"unknown envelope finding {self.code!r}: the vocabulary is "
                + ", ".join(FINDING_CODES)
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "subject": self.subject,
            "detail": self.detail,
            "measured": self.measured,
            "limit": self.limit,
        }


def massing_metrics(
    record: StateRecord,
    *,
    program_targets: Mapping[str, float] | None = None,
) -> MassingMetrics:
    """Measure the massing the record declares.

    ``program_targets`` is a program-sheet-shaped mapping of node id to target
    area in square metres. Given one, ``efficiency`` is the share of the gross
    floor area those targets account for; given none — or a gross floor area
    of zero — it is ``None``, because a ratio nobody can compute is not zero.
    """

    levels = _massing_levels(record)
    boxes = volume_boxes_of(record)
    honesty: list[str] = []
    rectangles: dict[str, tuple[int, int, int, int]] = {}
    for volume_id, (low, high) in sorted(boxes.items()):
        rectangle = _plan_rectangle(volume_id, low, high, honesty)
        if rectangle is not None:
            rectangles[volume_id] = rectangle
    on_level = _volumes_by_level(record, set(rectangles), {level_id for level_id, _, _ in levels}, honesty)
    per_level = tuple(
        LevelFootprint(
            level_id=level_id,
            base_y=float(base_y),
            height=float(height),
            footprint_m2=_union_area(rectangles[volume_id] for volume_id in on_level.get(level_id, ())),
        )
        for level_id, base_y, height in levels
    )
    gross = sum(level.footprint_m2 for level in per_level)
    footprint = _union_area(rectangles.values())
    if not levels:
        honesty.append(
            "no MassingLevel@1 in the record: the massing has no floors to "
            "count and no height to measure"
        )
    if not rectangles:
        honesty.append(
            "no Volume@1 with a readable plan box: nothing here covers any "
            "ground"
        )
    height = (
        max(base_y + height for _, base_y, height in levels) - min(base_y for _, base_y, _ in levels)
        if levels
        else 0.0
    )
    return MassingMetrics(
        footprint_m2=footprint,
        gross_floor_area_m2=gross,
        floor_count=len(levels),
        height_m=float(height),
        efficiency=_efficiency(program_targets, gross, honesty),
        per_level=per_level,
        honesty=tuple(honesty),
    )


def envelope_check(
    record: StateRecord,
    envelope: Mapping[str, Any],
) -> tuple[EnvelopeFinding, ...]:
    """Measure this massing against a buildable envelope and report what exceeds it.

    ``envelope`` carries what the site says, and every key is optional: ``min``
    and ``max`` are the buildable box in the same lattice as the volumes,
    ``max_height_m`` the height limit, and ``far`` with ``site_area_m2`` the
    plot ratio. A key that is absent makes no finding — an envelope that says
    nothing about height cannot be exceeded in height — and a caller who wants
    to know which checks were possible reads which keys it passed.
    """

    if not isinstance(envelope, Mapping):
        raise MassingMetricsError("envelope must be a mapping")
    findings: list[EnvelopeFinding] = []
    low_bound, high_bound = _envelope_box(envelope)
    if low_bound is not None and high_bound is not None:
        for volume_id, (low, high) in sorted(volume_boxes_of(record).items()):
            findings.extend(_outside(volume_id, low, high, low_bound, high_bound))
    metrics = massing_metrics(record)
    limit = _number(envelope.get("max_height_m"), "max_height_m")
    if limit is not None and metrics.height_m > limit:
        findings.append(
            EnvelopeFinding(
                HEIGHT_EXCEEDED,
                None,
                f"the massing stands {metrics.height_m:g} m against a limit of {limit:g} m",
                metrics.height_m,
                limit,
            )
        )
    far = _number(envelope.get("far"), "far")
    site_area = _number(envelope.get("site_area_m2"), "site_area_m2")
    if far is not None and site_area is not None:
        allowed = far * site_area
        if metrics.gross_floor_area_m2 > allowed:
            findings.append(
                EnvelopeFinding(
                    FAR_EXCEEDED,
                    None,
                    f"{metrics.gross_floor_area_m2:g} m2 of floor area against "
                    f"{allowed:g} m2 allowed ({far:g} x {site_area:g} m2 of site)",
                    metrics.gross_floor_area_m2,
                    allowed,
                )
            )
    return tuple(findings)


# ---------------------------------------------------------------- reading the record


def _massing_levels(record: StateRecord) -> tuple[tuple[str, float, float], ...]:
    """Every ``MassingLevel@1`` as ``(level_id, base_y, height)``, lowest first."""

    levels = []
    for entity in record.entities_of("MassingLevel@1"):
        try:
            levels.append((entity.entity_id, float(entity.fields["base_y"]), float(entity.fields["height"])))
        except (TypeError, ValueError) as exc:
            raise MassingMetricsError(
                f"massing level {entity.entity_id}: base_y and height must be numbers ({exc})"
            ) from exc
    return tuple(sorted(levels, key=lambda level: (level[1], level[0])))


def _volumes_by_level(
    record: StateRecord,
    measurable: set[str],
    declared_levels: set[str],
    honesty: list[str],
) -> dict[str, list[str]]:
    """Which volumes stand on which massing level, by the volumes' own ``level_ids``."""

    out: dict[str, list[str]] = {}
    for entity in record.entities_of("Volume@1"):
        if entity.entity_id not in measurable:
            continue
        named = tuple(entity.fields.get("level_ids", ()))
        if not named:
            honesty.append(
                f"volume {entity.entity_id} names no level: its area is in the "
                "footprint and in no floor"
            )
        for level_id in named:
            if level_id not in declared_levels:
                # A Level@1 is a project datum, not a massing floor; a volume
                # that names one is legal and simply contributes no floor area.
                honesty.append(
                    f"volume {entity.entity_id} names {level_id}, which is no "
                    "MassingLevel@1: it adds no floor area"
                )
                continue
            out.setdefault(level_id, []).append(entity.entity_id)
    return out


def _plan_rectangle(
    volume_id: str,
    low: Sequence[float],
    high: Sequence[float],
    honesty: list[str],
) -> tuple[int, int, int, int] | None:
    """One volume's plan rectangle ``(x0, z0, x1, z1)`` in whole inclusive cells.

    A box whose plan coordinates are not whole cells is not measured: the
    lattice counts cells, and rounding one here would put an area on the wire
    that the kernel's own ``SiteBounds`` would refuse to build.
    """

    if len(low) != 3 or len(high) != 3:
        honesty.append(f"volume {volume_id}: min and max are not coordinate triples; not measured")
        return None
    for index, axis in _AXES:
        if high[index] < low[index]:
            honesty.append(f"volume {volume_id}: max {axis} precedes min {axis}; not measured")
            return None
    for index in (0, 2):
        for value in (low[index], high[index]):
            if float(value) != int(value):
                honesty.append(
                    f"volume {volume_id}: plan bounds {low[0]},{low[2]} .. "
                    f"{high[0]},{high[2]} are not whole cells; not measured"
                )
                return None
    return (int(low[0]), int(low[2]), int(high[0]), int(high[2]))


# ---------------------------------------------------------------- the arithmetic


def _union_area(rectangles: Iterable[tuple[int, int, int, int]]) -> float:
    """The area of the union of inclusive integer plan rectangles, in square metres.

    A sweep over the compressed x coordinates, so two volumes that overlap in
    plan are counted once and a footprint is never the sum of its parts. Exact:
    every coordinate is a whole cell, so there is nothing to round.
    """

    rects = [r for r in rectangles]
    if not rects:
        return 0.0
    xs = sorted({edge for r in rects for edge in (r[0], r[2] + 1)})
    cells = 0
    for left, right in zip(xs, xs[1:]):
        width = right - left
        spans = sorted((r[1], r[3] + 1) for r in rects if r[0] <= left and r[2] + 1 >= right)
        depth = 0
        current_low = current_high = None
        for low, high in spans:
            if current_high is None or low > current_high:
                if current_high is not None:
                    depth += current_high - current_low   # type: ignore[operator]
                current_low, current_high = low, high
            elif high > current_high:
                current_high = high
        if current_high is not None:
            depth += current_high - current_low           # type: ignore[operator]
        cells += width * depth
    return float(cells) * CELL_AREA_M2


def _efficiency(
    program_targets: Mapping[str, float] | None,
    gross: float,
    honesty: list[str],
) -> float | None:
    if program_targets is None:
        return None
    if not isinstance(program_targets, Mapping):
        raise MassingMetricsError("program_targets must be a mapping of node id to target area")
    total = 0.0
    for node_id, area in program_targets.items():
        number = _number(area, f"program target {node_id}")
        if number is None:
            honesty.append(f"program target {node_id} is not a number: it is not in the efficiency")
            continue
        total += number
    if gross <= 0.0:
        honesty.append(
            "no gross floor area: the program targets cannot be expressed as a "
            "share of it"
        )
        return None
    return total / gross


def _envelope_box(
    envelope: Mapping[str, Any],
) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None]:
    low, high = envelope.get("min"), envelope.get("max")
    if low is None or high is None:
        return None, None
    return _triple(low, "envelope min"), _triple(high, "envelope max")


def _outside(
    volume_id: str,
    low: Sequence[float],
    high: Sequence[float],
    envelope_low: tuple[float, float, float],
    envelope_high: tuple[float, float, float],
) -> tuple[EnvelopeFinding, ...]:
    """One finding per axis the volume leaves the envelope on, with both numbers."""

    findings = []
    for index, axis in _AXES:
        if low[index] < envelope_low[index]:
            findings.append(
                EnvelopeFinding(
                    VOLUME_OUTSIDE_ENVELOPE,
                    volume_id,
                    f"{volume_id} reaches {low[index]:g} on {axis}, below the "
                    f"envelope's {envelope_low[index]:g}",
                    float(low[index]),
                    envelope_low[index],
                )
            )
        if high[index] > envelope_high[index]:
            findings.append(
                EnvelopeFinding(
                    VOLUME_OUTSIDE_ENVELOPE,
                    volume_id,
                    f"{volume_id} reaches {high[index]:g} on {axis}, above the "
                    f"envelope's {envelope_high[index]:g}",
                    float(high[index]),
                    envelope_high[index],
                )
            )
    return tuple(findings)


def _triple(value: object, name: str) -> tuple[float, float, float]:
    try:
        numbers = tuple(float(item) for item in value)   # type: ignore[union-attr]
    except (TypeError, ValueError) as exc:
        raise MassingMetricsError(f"{name} must be three numbers") from exc
    if len(numbers) != 3:
        raise MassingMetricsError(f"{name} must be three numbers, got {len(numbers)}")
    return numbers   # type: ignore[return-value]


def _number(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if name.startswith("program target "):
            return None
        raise MassingMetricsError(f"{name} must be a number")
    return float(value)
