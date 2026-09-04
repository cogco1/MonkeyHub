#!/usr/bin/env python3
"""Draw the MonkeyArch loading frames: frame-01.png .. frame-04.png.

    py -3.12 apps/archflow-studio/assets/loading/make_frames.py

Four frames of one loop: a monkey swinging a hammer at a wireframe box -- the
line under it says the monkey is hammering away at OCCT, so the picture had
better show a monkey hammering. These four files are the *placeholder* art:
the drawn storyboard replaces them in place, same names, same size, same
transparent ground, and neither the splash window nor the web overlay needs to
change when it does.

The palette is the icon's: limestone for the animal, amber for the geometry it
is hitting, ink only for features that sit inside a limestone shape. The
background is transparent, because the same four files are composited on the
splash's ink panel and on the web client's ground, and those are not the same
colour.

Every frame is rasterised at 4x and box-filtered down -- exact area averaging
over the supersamples -- so two runs of this script write the same bytes.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

INK = (18, 38, 63, 255)
STONE = (244, 239, 230, 255)
AMBER = (232, 163, 61, 255)
AMBER_DIM = (232, 163, 61, 120)

WIDTH = 600
HEIGHT = 360
SS = 4  # supersampling factor; the downsample is a box filter, so this is exact

# The swing, frame by frame: the angle of the forearm measured at the shoulder,
# 0 pointing right and negative pointing up. Frame 3 is the strike.
SWING = (-80.0, -46.0, -6.0, -32.0)
STRIKE = 2

SHOULDER = (256.0, 200.0)
FOREARM = 86.0
HEAD_LEN = 34.0  # the hammer head, laid across the end of the forearm

BOX_CENTRE = (410.0, 236.0)
BOX_SIDE = 104.0
BOX_DEPTH = 44.0  # the isometric offset of the back face


def _q(value: float) -> int:
    """A device-pixel coordinate in the supersampled raster."""
    return int(round(value * SS))


def _pt(point: tuple[float, float]) -> tuple[int, int]:
    return (_q(point[0]), _q(point[1]))


def _ellipse(draw: ImageDraw.ImageDraw, x0, y0, x1, y1, colour) -> None:
    draw.ellipse([_q(x0), _q(y0), _q(x1) - 1, _q(y1) - 1], fill=colour)


def _line(draw: ImageDraw.ImageDraw, points, colour, width) -> None:
    draw.line([_pt(p) for p in points], fill=colour, width=_q(width), joint="curve")


def _rotate(point, origin, degrees):
    angle = math.radians(degrees)
    dx = point[0] - origin[0]
    dy = point[1] - origin[1]
    return (
        origin[0] + dx * math.cos(angle) - dy * math.sin(angle),
        origin[1] + dx * math.sin(angle) + dy * math.cos(angle),
    )


def _box(draw: ImageDraw.ImageDraw, dent: float) -> None:
    """The wireframe box, with its top face pressed down by `dent` pixels."""
    cx, cy = BOX_CENTRE
    half = BOX_SIDE / 2
    depth = BOX_DEPTH
    stroke = 3.5

    top = cy - half + dent
    bottom = cy + half
    left, right = cx - half, cx + half
    front = [(left, top), (right, top), (right, bottom), (left, bottom)]
    back = [(x + depth, y - depth) for (x, y) in front]

    for near, far in zip(front, back):
        _line(draw, [near, far], AMBER_DIM, stroke - 1)
    _line(draw, back + [back[0]], AMBER_DIM, stroke - 1)
    _line(draw, front + [front[0]], AMBER, stroke)
    # One diagonal on the front face: enough to read as a mesh, not a crate.
    _line(draw, [front[0], front[2]], AMBER_DIM, stroke - 1.5)


def _monkey(draw: ImageDraw.ImageDraw, angle: float, lean: float):
    """The animal: tail, body, head, and the arm carrying the hammer.

    Answers where the hammer landed, so the strike frame knows where to spark.
    """
    # Tail first, so the body covers where it joins.
    _line(draw, [(172, 306), (136, 320), (110, 302), (114, 272), (138, 262)], STONE, 9)

    _ellipse(draw, 156 + lean, 196, 262 + lean, 316, STONE)
    _line(draw, [(180 + lean, 300), (176, 328), (208, 328)], STONE, 14)
    _line(draw, [(232 + lean, 300), (238, 328), (266, 328)], STONE, 14)

    # Head: skull, two ears, a muzzle, two eyes.
    _ellipse(draw, 166 + lean, 116, 258 + lean, 204, STONE)
    _ellipse(draw, 152 + lean, 140, 186 + lean, 176, STONE)
    _ellipse(draw, 238 + lean, 140, 272 + lean, 176, STONE)
    _ellipse(draw, 188 + lean, 156, 244 + lean, 202, INK)
    _ellipse(draw, 194 + lean, 162, 238 + lean, 196, AMBER)
    _ellipse(draw, 190 + lean, 138, 204 + lean, 152, INK)
    _ellipse(draw, 220 + lean, 138, 234 + lean, 152, INK)

    # The one arm on this side of the animal, and what it is holding.
    shoulder = (SHOULDER[0] + lean, SHOULDER[1])
    hand = (
        shoulder[0] + FOREARM * math.cos(math.radians(angle)),
        shoulder[1] + FOREARM * math.sin(math.radians(angle)),
    )
    _line(draw, [shoulder, hand], STONE, 13)

    # The hammer: a bar across the end of the forearm, plus the stub of the
    # haft past the hand, so the swing has a direction at every angle.
    across = angle + 90.0
    half = HEAD_LEN / 2
    end_a = (
        hand[0] + half * math.cos(math.radians(across)),
        hand[1] + half * math.sin(math.radians(across)),
    )
    end_b = (
        hand[0] - half * math.cos(math.radians(across)),
        hand[1] - half * math.sin(math.radians(across)),
    )
    stub = (
        hand[0] + 12 * math.cos(math.radians(angle)),
        hand[1] + 12 * math.sin(math.radians(angle)),
    )
    _line(draw, [hand, stub], STONE, 9)
    _line(draw, [end_a, end_b], AMBER, 17)
    return hand


def _sparks(draw: ImageDraw.ImageDraw, at: tuple[float, float]) -> None:
    for degrees in (-140, -95, -50, -10, 35):
        near = _rotate((at[0] + 14, at[1]), at, degrees)
        far = _rotate((at[0] + 34, at[1]), at, degrees)
        _line(draw, [near, far], AMBER, 4)


def render(index: int) -> Image.Image:
    """Draw one frame, 0-based, as an RGBA image over a transparent ground."""
    art = Image.new("RGBA", (WIDTH * SS, HEIGHT * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art)

    striking = index == STRIKE
    _box(draw, 5.0 if striking else 0.0)
    hand = _monkey(draw, SWING[index], 4.0 if striking else 0.0)
    if striking:
        _sparks(draw, hand)

    return art.resize((WIDTH, HEIGHT), Image.Resampling.BOX)


def main() -> None:
    here = Path(__file__).resolve().parent
    for index in range(len(SWING)):
        path = here / f"frame-{index + 1:02d}.png"
        render(index).save(path, format="PNG", optimize=True)
        print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
