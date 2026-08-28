#!/usr/bin/env python3
"""Fixed four-view render set for V4 voxel views.

A fresh V4 implementation of the V3 golden render convention
(`kevin-section-render-standard`, transcribed read-only from the frozen V3
sample-export documentation; no V3 code is imported):

- ``iso``        axonometric reference view, camera fixed, the entrance
                 rotated toward the viewer (180 degrees about the vertical);
- ``section A``  TRANSVERSE section axonometric: cut across the depth on the
                 drum mid-plane (max depth minus half the width), keeping the
                 far half so the cut face opens to the camera;
- ``section B``  AXIAL section axonometric: cut on the entrance mid-line,
                 keeping the far half;
- ``elevation``  front orthographic facade with depth dimming.

Lighting transcription: sun (-0.55, 1.0, 0.65), ambient 0.42, sky/sun
warm-cool mixing, ambient occlusion 0.07 per in-plane neighbour capped at
five, per-voxel value noise of +/-6, light vertical-gradient background,
2x supersampling with Lanczos downsampling.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SS = 2
BG_TOP = (232, 238, 247)
BG_BOTTOM = (205, 214, 228)
SKY = (150, 178, 214)
SUN_TINT = (255, 246, 228)
AMBIENT = 0.42
BASE_COLOR = (208, 200, 184)


def _normalise(cells):
    x0 = min(c[0] for c in cells)
    y0 = min(c[1] for c in cells)
    z0 = min(c[2] for c in cells)
    return {(x - x0, y - y0, z - z0) for x, y, z in cells}


def _rotate2(cells):
    """180 degrees about the vertical axis: entrance toward the camera."""

    xmax = max(c[0] for c in cells)
    zmax = max(c[2] for c in cells)
    return {(xmax - x, y, zmax - z) for x, y, z in cells}


def _noise(x, y, z):
    value = (x * 73856093) ^ (y * 19349663) ^ (z * 83492791)
    return (value % 13) - 6


def render_iso(cells, cube=5):
    cells = _normalise(cells)
    occupied = set(cells)
    cube *= SS
    hw, hh = cube, cube // 2
    sun = (-0.55, 1.0, 0.65)
    length = math.sqrt(sum(c * c for c in sun))
    sun = tuple(c / length for c in sun)

    def proj(x, y, z):
        return (x - z) * hw, (x + z) * hh - y * cube

    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    zs = [c[2] for c in cells]
    corners = [
        proj(x, y, z)
        for x in (0, max(xs) + 1)
        for y in (0, max(ys) + 1)
        for z in (0, max(zs) + 1)
    ]
    pad = cube * 3
    minx = min(p[0] for p in corners) - pad
    maxx = max(p[0] for p in corners) + pad
    miny = min(p[1] for p in corners) - pad
    maxy = max(p[1] for p in corners) + pad
    width, height = int(maxx - minx), int(maxy - miny)
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for row in range(height):
        t = row / height
        draw.line(
            [(0, row), (width, row)],
            fill=tuple(
                int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3)
            ),
        )

    surface = [
        c
        for c in cells
        if not all(
            (c[0] + dx, c[1] + dy, c[2] + dz) in occupied
            for dx, dy, dz in (
                (1, 0, 0), (-1, 0, 0), (0, 1, 0),
                (0, -1, 0), (0, 0, 1), (0, 0, -1),
            )
        )
    ]
    surface.sort(key=lambda c: ((c[0] + c[2]), c[1]))

    plane_neighbours = {
        "top": ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1),
                (1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1)),
        "pz": ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
               (1, 1, 0), (-1, -1, 0), (1, -1, 0), (-1, 1, 0)),
        "px": ((0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0),
               (0, 1, 1), (0, -1, -1), (0, 1, -1), (0, -1, 1)),
    }
    normals = {"top": (0, 1, 0), "pz": (0, 0, 1), "px": (1, 0, 0)}
    face_corners = {
        "top": ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
        "pz": ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
        "px": ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
    }
    shades = {}
    for face, normal in normals.items():
        diffuse = max(
            0.0,
            normal[0] * sun[0] + normal[1] * sun[1] + normal[2] * sun[2],
        )
        lit = AMBIENT + (1 - AMBIENT) * diffuse
        warm = diffuse
        mix = tuple(
            SUN_TINT[i] / 255 * warm + SKY[i] / 255 * (1 - warm)
            for i in range(3)
        )
        shades[face] = (lit, mix)

    for x, y, z in surface:
        noise = _noise(x, y, z)
        base = tuple(
            max(0, min(255, BASE_COLOR[i] + noise)) for i in range(3)
        )
        for face, normal in normals.items():
            neighbour = (x + normal[0], y + normal[1], z + normal[2])
            if neighbour in occupied:
                continue
            count = sum(
                1
                for dx, dy, dz in plane_neighbours[face]
                if (
                    neighbour[0] + dx,
                    neighbour[1] + dy,
                    neighbour[2] + dz,
                )
                in occupied
            )
            occlusion = 1.0 - 0.07 * min(count, 5)
            lit, mix = shades[face]
            level = lit * occlusion
            colour = tuple(
                max(0, min(255, int(base[i] * level * mix[i])))
                for i in range(3)
            )
            points = []
            for cx, cy, cz in face_corners[face]:
                u, v = proj(x + cx, y + cy, z + cz)
                points.append((u - minx, v - miny))
            draw.polygon(points, fill=colour)
    return img.resize((width // SS, height // SS), Image.LANCZOS)


def render_elevation(cells, cube=6):
    cells = _normalise(cells)
    xs = max(c[0] for c in cells) + 1
    ys = max(c[1] for c in cells) + 1
    zs = max(c[2] for c in cells) + 1
    front = {}
    for x, y, z in cells:
        key = (x, y)
        if key not in front or z < front[key]:
            front[key] = z
    img = Image.new("RGB", (xs * cube, ys * cube), BG_TOP)
    draw = ImageDraw.Draw(img)
    for (x, y), depth in front.items():
        factor = max(0.45, 1.0 - 0.45 * (depth / max(1, zs - 1)))
        colour = tuple(int(BASE_COLOR[i] * factor) for i in range(3))
        draw.rectangle(
            [
                x * cube,
                (ys - 1 - y) * cube,
                (x + 1) * cube - 1,
                (ys - y) * cube - 1,
            ],
            fill=colour,
        )
    return img


def four_views(cells):
    cells = _normalise(cells)
    xmax = max(c[0] for c in cells)
    zmax = max(c[2] for c in cells)
    transverse_cut = zmax - (xmax + 1) // 2
    axial_cut = xmax // 2
    return {
        "iso": render_iso(_rotate2(cells)),
        "section_A": render_iso(
            _rotate2({c for c in cells if c[2] >= transverse_cut})
        ),
        "section_B": render_iso(
            _rotate2({c for c in cells if c[0] >= axial_cut})
        ),
        "elevation": render_elevation(cells),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--voxel-view",
        required=True,
        help="path or glob of a DerivedVoxelView record",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    matches = sorted(glob.glob(args.voxel_view))
    if not matches:
        raise SystemExit(f"no voxel view matches {args.voxel_view}")
    view = json.load(open(matches[-1], encoding="utf-8"))
    cells = {tuple(c) for c in view["occupied_cells"]}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix, image in four_views(cells).items():
        path = args.out_dir / f"{args.name}_{suffix}.png"
        image.save(path)
        written.append(str(path))
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
