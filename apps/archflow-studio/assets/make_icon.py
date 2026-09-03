#!/usr/bin/env python3
"""Draw the ArchFlow Studio icon: archflow.ico, plus a 512 px PNG preview.

    py -3.12 apps/archflow-studio/assets/make_icon.py

The mark is a semicircular arch -- two piers, impost blocks marking the springing
line, a keystone at the crown -- standing on a plinth, with one warm line flowing
through the opening: the record-driven derivation running under an architectural
order.

Three colours on a rounded-square tile: ink for the tile, limestone for the
stonework, amber for the flow. The tile's corners are transparent; the tile is
dark enough to hold its own against a light Desktop and, with its rim, light
enough to show on a dark one. There is no text at any size.

Every size is drawn from its own spec rather than downscaled from one large
image, because 16 px cannot carry what 256 px can. Below 128 px the mortar joints
beside the keystone go; below 48 px the keystone and the impost blocks go; below
32 px the plinth goes; and the strokes grow relatively thicker as the tile
shrinks, so the arch keeps its weight when there are only sixteen pixels to
spend. Each size is rasterised at 8x and box-filtered down -- exact area
averaging over the supersamples -- so two runs of this script write the same
bytes.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

INK = (18, 38, 63)  # the tile
RIM = (46, 74, 110)  # the tile's edge, so it still reads on a dark Desktop
STONE = (244, 239, 230)  # arch, imposts, keystone, plinth
FLOW = (232, 163, 61)  # the line through the opening

SS = 8  # supersampling factor; the downsample is a box filter, so this is exact

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PREVIEW_SIZE = 512

# Geometry per size, in that size's own device pixels. x0/x1 are the outer faces
# of the piers, top is the crown of the extrados, base is where the piers meet
# the plinth, thick is the depth of the arch ring. The centre and the springing
# line follow: cx = (x0 + x1) / 2, radius = (x1 - x0) / 2, springing = top +
# radius. Optional parts are None when that size is too small to carry them.
#
#   plinth   = (height, how far it oversails the piers)
#   impost   = (how far it projects sideways, its height)
#   keystone = (half-width at the extrados, half-width at the intrados,
#               rise above the extrados, drop below the intrados)
#   mortar   = width of the two joints flanking the keystone
#   flow     = (centre line, amplitude, stroke width)
SPECS = {
    16: {
        "margin": 0, "radius": 3, "rim": 1,
        "x0": 2, "x1": 14, "top": 2, "base": 14, "thick": 2,
        "plinth": None, "impost": None, "keystone": None, "mortar": None,
        "flow": (11, 1.0, 2),
    },
    24: {
        "margin": 0, "radius": 5, "rim": 1,
        "x0": 3, "x1": 21, "top": 3, "base": 21, "thick": 3,
        "plinth": None, "impost": None, "keystone": None, "mortar": None,
        "flow": (16.5, 1.5, 2.5),
    },
    32: {
        "margin": 0, "radius": 6, "rim": 1,
        "x0": 4, "x1": 28, "top": 4, "base": 26, "thick": 3.5,
        "plinth": (2, 1), "impost": None, "keystone": None, "mortar": None,
        "flow": (21, 2, 3),
    },
    48: {
        "margin": 1, "radius": 9, "rim": 1,
        "x0": 7, "x1": 41, "top": 6, "base": 39, "thick": 4.5,
        "plinth": (3, 2), "impost": (2.5, 2), "keystone": (3.5, 2.5, 1.5, 1),
        "mortar": None,
        "flow": (31, 3, 4),
    },
    64: {
        "margin": 2, "radius": 12, "rim": 1,
        "x0": 10, "x1": 54, "top": 8, "base": 52, "thick": 5.5,
        "plinth": (4, 2), "impost": (3, 2.5), "keystone": (4.5, 3, 1.5, 1),
        "mortar": None,
        "flow": (41, 4, 5),
    },
    128: {
        "margin": 4, "radius": 24, "rim": 2,
        "x0": 20, "x1": 108, "top": 15, "base": 105, "thick": 11,
        "plinth": (8, 4), "impost": (6, 5), "keystone": (8.5, 6, 3, 2),
        "mortar": 1,
        "flow": (82, 8, 9),
    },
    256: {
        "margin": 8, "radius": 48, "rim": 3,
        "x0": 40, "x1": 216, "top": 30, "base": 210, "thick": 22,
        "plinth": (16, 9), "impost": (12, 10), "keystone": (17, 11.5, 5.5, 3.5),
        "mortar": 2,
        "flow": (164, 16, 18),
    },
    512: {
        "margin": 16, "radius": 96, "rim": 6,
        "x0": 80, "x1": 432, "top": 60, "base": 420, "thick": 44,
        "plinth": (32, 18), "impost": (24, 20), "keystone": (34, 23, 11, 7),
        "mortar": 4,
        "flow": (328, 32, 36),
    },
}


def _q(value: float) -> int:
    """A device-pixel coordinate in the supersampled raster."""
    return int(round(value * SS))


def _rect(draw: ImageDraw.ImageDraw, x0, y0, x1, y1, colour) -> None:
    """Fill the half-open box [x0, x1) x [y0, y1) given in device pixels."""
    draw.rectangle([_q(x0), _q(y0), _q(x1) - 1, _q(y1) - 1], fill=colour)


def _upper_half_disc(draw: ImageDraw.ImageDraw, cx, cy, r, colour) -> None:
    draw.pieslice(
        [_q(cx - r), _q(cy - r), _q(cx + r) - 1, _q(cy + r) - 1],
        180, 360, fill=colour,
    )


def render(size: int) -> Image.Image:
    """Draw one size of the icon as an RGBA image with transparent corners."""
    spec = SPECS[size]
    side = size * SS

    x0, x1 = spec["x0"], spec["x1"]
    top, base, thick = spec["top"], spec["base"], spec["thick"]
    cx = (x0 + x1) / 2
    radius = (x1 - x0) / 2
    springing = top + radius
    inner = radius - thick

    # The whole canvas starts ink, not transparent, so that the box filter never
    # blends a colour with an unpainted pixel at the tile's rounded corners.
    art = Image.new("RGB", (side, side), INK)
    draw = ImageDraw.Draw(art)

    margin = spec["margin"]
    tile_box = [_q(margin), _q(margin), _q(size - margin) - 1, _q(size - margin) - 1]
    tile_radius = _q(spec["radius"])
    draw.rounded_rectangle(
        tile_box, radius=tile_radius, fill=INK,
        outline=RIM, width=_q(spec["rim"]),
    )

    # The arch ring: the outer portal in stone, the opening carved back to ink.
    _upper_half_disc(draw, cx, springing, radius, STONE)
    _rect(draw, x0, springing - 1, x1, base, STONE)
    _upper_half_disc(draw, cx, springing, inner, INK)
    _rect(draw, x0 + thick, springing - 1, x1 - thick, base, INK)

    if spec["plinth"] is not None:
        height, oversail = spec["plinth"]
        _rect(draw, x0 - oversail, base, x1 + oversail, base + height, STONE)

    if spec["impost"] is not None:
        project, height = spec["impost"]
        _rect(draw, x0 - project, springing - height / 2,
              x0 + thick, springing + height / 2, STONE)
        _rect(draw, x1 - thick, springing - height / 2,
              x1 + project, springing + height / 2, STONE)

    if spec["keystone"] is not None:
        wide, narrow, rise, drop = spec["keystone"]
        head = top - rise
        foot = top + thick + drop
        draw.polygon(
            [(_q(cx - wide), _q(head)), (_q(cx + wide), _q(head)),
             (_q(cx + narrow), _q(foot)), (_q(cx - narrow), _q(foot))],
            fill=STONE,
        )
        if spec["mortar"] is not None:
            joint = _q(spec["mortar"])
            draw.line([(_q(cx - wide), _q(head)), (_q(cx - narrow), _q(foot))],
                      fill=INK, width=joint)
            draw.line([(_q(cx + wide), _q(head)), (_q(cx + narrow), _q(foot))],
                      fill=INK, width=joint)

    # The flow: one period of a sine across the opening, drawn long and then cut
    # by the opening itself, so it meets the piers face-on instead of tapering.
    centre, amplitude, stroke = spec["flow"]
    opening = Image.new("L", (side, side), 0)
    cut = ImageDraw.Draw(opening)
    _upper_half_disc(cut, cx, springing, inner, 255)
    _rect(cut, x0 + thick, springing - 1, x1 - thick, base, 255)

    wave = Image.new("L", (side, side), 0)
    pen = ImageDraw.Draw(wave)
    span = (x1 - thick) - (x0 + thick)
    steps = 128
    points = []
    for step in range(steps + 1):
        x = x0 + (x1 - x0) * step / steps
        y = centre - amplitude * math.sin(2 * math.pi * (x - (x0 + thick)) / span)
        points.append((_q(x), _q(y)))
    pen.line(points, fill=255, width=_q(stroke), joint="curve")

    art.paste(FLOW, (0, 0, side, side), ImageChops.multiply(wave, opening))

    alpha = Image.new("L", (side, side), 0)
    ImageDraw.Draw(alpha).rounded_rectangle(tile_box, radius=tile_radius, fill=255)

    icon = art.resize((size, size), Image.Resampling.BOX).convert("RGBA")
    icon.putalpha(alpha.resize((size, size), Image.Resampling.BOX))
    return icon


def main() -> None:
    here = Path(__file__).resolve().parent
    ico_path = here / "archflow.ico"
    png_path = here / "archflow-icon-512.png"

    frames = [render(size) for size in ICO_SIZES]
    frames[-1].save(
        ico_path,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=frames[:-1],
    )
    render(PREVIEW_SIZE).save(png_path, format="PNG", optimize=True)

    print(f"wrote {ico_path} ({ico_path.stat().st_size} bytes)")
    with Image.open(ico_path) as written:
        print("  sizes: " + ", ".join(
            f"{w}x{h}" for w, h in sorted(written.info["sizes"])
        ))
    print(f"wrote {png_path} ({png_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
