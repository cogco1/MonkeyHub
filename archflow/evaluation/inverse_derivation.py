"""P064 inverse-derivation contracts: schematic reading and coarse fidelity.

The V3 golden schematic is read-only external evidence. This module parses
the frozen Sponge v2 file into an occupancy grid and measures coarse
massing fidelity: bounding box, plan-footprint IoU, elevation-silhouette
IoU, and per-part presence. It deliberately measures nothing finer — a
coarse massing claim cannot support solid-cell reproduction language — and
it never writes inside the V3 tree.
"""

from __future__ import annotations

import gzip
import hashlib
import math
import struct
from dataclasses import dataclass
from pathlib import Path


class InverseDerivationError(ValueError):
    """The golden input or a fidelity computation is invalid."""


def _read_nbt(buffer: bytes):
    offset = 0

    def take(count: int) -> bytes:
        nonlocal offset
        piece = buffer[offset : offset + count]
        if len(piece) != count:
            raise InverseDerivationError("truncated NBT payload")
        offset += count
        return piece

    def read_string() -> str:
        (length,) = struct.unpack(">H", take(2))
        return take(length).decode("utf-8", "replace")

    def read_payload(tag: int):
        if tag == 1:
            return struct.unpack(">b", take(1))[0]
        if tag == 2:
            return struct.unpack(">h", take(2))[0]
        if tag == 3:
            return struct.unpack(">i", take(4))[0]
        if tag == 4:
            return struct.unpack(">q", take(8))[0]
        if tag == 5:
            return struct.unpack(">f", take(4))[0]
        if tag == 6:
            return struct.unpack(">d", take(8))[0]
        if tag == 7:
            (length,) = struct.unpack(">i", take(4))
            return take(length)
        if tag == 8:
            return read_string()
        if tag == 9:
            item_tag = take(1)[0]
            (length,) = struct.unpack(">i", take(4))
            return [read_payload(item_tag) for _ in range(length)]
        if tag == 10:
            compound: dict[str, object] = {}
            while True:
                child = take(1)[0]
                if child == 0:
                    return compound
                name = read_string()
                compound[name] = read_payload(child)
        if tag == 11:
            (length,) = struct.unpack(">i", take(4))
            return list(struct.unpack(f">{length}i", take(4 * length)))
        if tag == 12:
            (length,) = struct.unpack(">i", take(4))
            return list(struct.unpack(f">{length}q", take(8 * length)))
        raise InverseDerivationError(f"unknown NBT tag {tag}")

    root_tag = take(1)[0]
    if root_tag != 10:
        raise InverseDerivationError("root NBT tag must be a compound")
    read_string()
    return read_payload(10)


@dataclass(frozen=True, slots=True)
class OccupancyGrid:
    width: int
    height: int
    length: int
    solid: frozenset[tuple[int, int, int]]

    @property
    def solid_count(self) -> int:
        return len(self.solid)

    def footprint(self) -> frozenset[tuple[int, int]]:
        return frozenset((x, z) for x, _, z in self.solid)

    def silhouette_xy(self) -> frozenset[tuple[int, int]]:
        return frozenset((x, y) for x, y, _ in self.solid)

    def bounding_box(self) -> tuple[int, int, int]:
        if not self.solid:
            return (0, 0, 0)
        xs = [x for x, _, _ in self.solid]
        ys = [y for _, y, _ in self.solid]
        zs = [z for _, _, z in self.solid]
        return (
            max(xs) - min(xs) + 1,
            max(ys) - min(ys) + 1,
            max(zs) - min(zs) + 1,
        )


def load_sponge_schematic(
    path: Path, *, expected_sha256: str
) -> OccupancyGrid:
    """Parse one frozen Sponge v2 schematic into an occupancy grid."""

    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise InverseDerivationError(
            "schematic digest does not match the pinned freeze"
        )
    root = _read_nbt(gzip.decompress(raw))
    if root.get("Version") != 2:
        raise InverseDerivationError("only Sponge schematic v2 is supported")
    width = int(root["Width"]) & 0xFFFF
    height = int(root["Height"]) & 0xFFFF
    length = int(root["Length"]) & 0xFFFF
    palette: dict[str, int] = root["Palette"]
    air_ids = {
        index
        for name, index in palette.items()
        if name.split("[", 1)[0]
        in (
            "minecraft:air",
            "minecraft:cave_air",
            "minecraft:void_air",
        )
    }
    data = root["BlockData"]
    solid: set[tuple[int, int, int]] = set()
    index = 0
    position = 0
    while position < len(data):
        value = 0
        shift = 0
        while True:
            byte = data[position]
            position += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        if value not in air_ids:
            x = index % width
            z = (index // width) % length
            y = index // (width * length)
            solid.add((x, y, z))
        index += 1
    if index != width * height * length:
        raise InverseDerivationError("block data does not fill the region")
    return OccupancyGrid(
        width=width, height=height, length=length, solid=frozenset(solid)
    )


@dataclass(frozen=True, slots=True)
class CoarseMassing:
    """Deterministic coarse drum-and-dome massing from transcribed numbers.

    Inputs are the transcribed golden dimensions and canon ratios; nothing
    is fitted against the schematic occupancy.
    """

    width: int
    length: int
    height: int
    drum_span: float
    rise_over_span: float
    oculus_over_span: float

    def drum_center(self) -> tuple[float, float]:
        radius = self.drum_span / 2.0
        return (self.width / 2.0, self.length - radius - 2.0)

    def portico_depth(self) -> float:
        return self.length - self.drum_span - 4.0

    def footprint(self) -> frozenset[tuple[int, int]]:
        cx, cz = self.drum_center()
        radius = self.drum_span / 2.0 + 2.0
        cells: set[tuple[int, int]] = set()
        for x in range(self.width):
            for z in range(self.length):
                if math.hypot(x + 0.5 - cx, z + 0.5 - cz) <= radius:
                    cells.add((x, z))
        depth = self.portico_depth()
        p_half = self.drum_span * 0.35
        for x in range(int(cx - p_half), int(cx + p_half) + 1):
            for z in range(0, int(depth) + 1):
                if 0 <= x < self.width and 0 <= z < self.length:
                    cells.add((x, z))
        return frozenset(cells)

    def silhouette_xy(self) -> frozenset[tuple[int, int]]:
        cx, _ = self.drum_center()
        radius = self.drum_span / 2.0 + 2.0
        rise = self.drum_span * self.rise_over_span
        drum_top = self.height - rise
        cells: set[tuple[int, int]] = set()
        for x in range(self.width):
            if abs(x + 0.5 - cx) <= radius:
                for y in range(int(drum_top)):
                    cells.add((x, y))
        dome_radius = self.drum_span / 2.0
        for x in range(self.width):
            dx = abs(x + 0.5 - cx)
            if dx <= dome_radius:
                cap = drum_top + math.sqrt(
                    max(dome_radius**2 - dx**2, 0.0)
                ) * (rise / dome_radius)
                for y in range(int(drum_top), int(cap)):
                    cells.add((x, y))
        return frozenset(cells)


def iou(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 0.0
    return round(len(a & b) / len(union), 6)


def crown_oculus_open(
    grid: OccupancyGrid, massing: CoarseMassing
) -> bool:
    """No solid cell in the transcribed oculus disc at the crown layers."""

    cx, cz = massing.drum_center()
    oculus_radius = massing.drum_span * massing.oculus_over_span / 2.0
    top_layers = range(grid.height - 3, grid.height)
    for x, y, z in grid.solid:
        if y in top_layers and math.hypot(
            x + 0.5 - cx, z + 0.5 - cz
        ) <= oculus_radius * 0.8:
            return False
    return True
