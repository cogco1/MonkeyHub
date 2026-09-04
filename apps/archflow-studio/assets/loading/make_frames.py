#!/usr/bin/env python3
"""Draw the MonkeyArch loading frames: frame-01.png .. frame-04.png.

    py -3.12 apps/archflow-studio/assets/loading/make_frames.py

Four frames of one loop, and the loop is the line underneath it: the monkey is
hammering away at OCCT, so the picture is the monkey off the icon -- the same
head, the same ears level with the eyes, the same limestone inverted-teardrop
face patch, the same 430-degree spiral tail -- standing on a limestone ground
line and swinging a hammer at a wireframe solid.

    frame-01  wind-up: the hammer is up and back, the head leans away from the
              blow, the box is clean.
    frame-02  the strike: the hammer is through the box's top front edge, the
              top face is pressed down, and the only amber in the set is the
              sparks coming off the contact.
    frame-03  the edges realign: every vertex of the solid is off its place by a
              few pixels and coming back, with amber chips still in the air.
    frame-04  recovery: the hammer is back up, the solid is whole again, and the
              next tick is the wind-up.

The animal is not drawn here. `draw_monkey` and `Pose` come out of
../make_icon.py, which is the one place the figure is described; this file says
only where the parts go for a standing figure and what it is hitting. A change
to the monkey's proportions in the icon arrives here without being copied.

The palette is the icon's: brown for the animal, limestone for its face patch,
for the ground line, for the wireframe edges and for the hammer's head, amber
for the sparks and nothing else. The ground is transparent, because the same
four files are composited on the splash window's panel and on the web client's,
and those are not the same colour -- which is also why the supersamples are
resolved through premultiplied alpha rather than averaged straight, so the
limestone edges do not come back with a dark fringe on either of them.

Every frame is rasterised at 8x and box-filtered down -- exact area averaging
over the supersamples, no randomness anywhere -- so two runs of this script
write the same bytes.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageMath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from make_icon import (  # noqa: E402  (the path above has to come first)
    AMBER, BODY_DX, BODY_DY, BODY_RX, BODY_RY, BROWN, EAR_DX, EAR_DY, EAR_R,
    HEAD_R, INK, Limb, Pen, Pose, STONE, TAIL_A0, TAIL_DX, TAIL_DY, TAIL_R0,
    TAIL_W, draw_monkey,
)

WIDTH = 600
HEIGHT = 360
SS = 8  # supersampling factor; the downsample is a box filter, so this is exact

# One tile side of the icon, in frame pixels. At 520 the head lands within a
# pixel of the size it is on the 512 px icon, so the face patch is drawn at the
# scale it was tuned at rather than at one it has to survive.
K = 520.0

FUR = BROWN
PATCH = STONE
EDGE = STONE
# The edges that are not on the near face, as a colour rather than as an alpha:
# a translucent stroke laid over an opaque one by ImageDraw replaces it instead
# of blending with it, which would punch holes in the near edges where they
# cross. Limestone carried most of the way to ink -- far enough back to sit
# behind the front face, near enough to survive the reel at a third of this
# size.
EDGE_FAR = tuple(round(0.60 * s + 0.40 * i) for s, i in zip(STONE, INK))
SPARK = AMBER

# ---------------------------------------------------------------------------
# The ground, and the animal standing on it. The head is the anchor: everything
# else in the figure is measured from it in fractions of K, exactly as in the
# icon, so the two figures cannot drift apart.
GROUND_Y = 326.0
GROUND_H = 6.0
GROUND_X0, GROUND_X1 = 26.0, 574.0

HEAD_X, HEAD_Y = 190.0, 146.4
BODY_X, BODY_Y = HEAD_X + BODY_DX * K, HEAD_Y + BODY_DY * K

# Legs. The hips ride with the body as it leans, the knees follow it part of the
# way, and the ankles and feet do not move at all: a figure whose feet slide
# under it is not standing, it is being dragged.
LEG_DROP = 0.200  # hip to ankle, so the soles land in the ground line
BACK_LEG = ((-0.020, 0.076), (-0.066, 0.146), (-0.044, LEG_DROP))
FRONT_LEG = ((0.028, 0.072), (0.074, 0.140), (0.050, LEG_DROP))
BACK_FOOT = (-0.052, 0.204, 0.036, 0.019)
FRONT_FOOT = (0.058, 0.204, 0.038, 0.019)

# There is no far arm. On the icon the animal's other hand is out on the
# intrados, where there is empty ink to show it against; here the left flank is
# where the tail's outer coil passes, and a second brown limb behind a brown
# body next to a brown tail reads as a lump on the belly rather than as an arm.
# The figure keeps two legs, a tail and the arm that is doing the work.

# ---------------------------------------------------------------------------
# The swing, as three angles per frame: the upper arm at the shoulder, the
# forearm at the elbow, and the haft at the wrist. Degrees, 0 pointing right,
# positive downward. Three and not one because the ear is as wide as the head is
# and the same brown -- an arm that lifts the hammer by folding across the face
# disappears into it, so the elbow stays outside the ear at every frame and the
# wrist is what cocks the hammer back.
SWING = (
    (-16.0, -74.0, -118.0),  # wind-up: hammer back over the shoulder
    (2.0, 8.0, 14.0),        # the strike, into the top front edge of the solid
    (-24.0, -44.0, -40.0),   # the bounce, the hammer already rising
    (-10.0, -68.0, -74.0),   # recovery, on the way to the next wind-up
)
# The whole figure's lean, and the head's turn, frame by frame: into the blow
# and out of it. The feet stay put.
LEAN = ((-3.0, 0.0), (8.0, 4.0), (3.0, 1.0), (0.0, 0.0))
TILT = (-6.0, 10.0, 4.0, -1.0)

SHOULDER = (0.068, -0.058)  # from the body's centre
UPPER, FORE = 0.088 * K, 0.118 * K
HAFT = 0.058 * K  # hand to the middle of the hammer's head
HAFT_W = 0.024 * K
ARM_W = 0.046 * K
WRIST = 0.030 * K
HEAD_BAR, HEAD_THICK = 0.112 * K, 0.040 * K

# ---------------------------------------------------------------------------
# The wireframe solid: a box on the ground line with a cylinder bored down
# through its top face.
BOX_L, BOX_R = 336.0, 470.0
BOX_TOP, BOX_BOTTOM = 210.0, GROUND_Y
BOX_DEPTH = 48.0  # the axonometric offset of the back face, up and to the right
BORE_R = 0.26  # of the top face's half-width
BORE_DROP = 30.0
EDGE_W, EDGE_FAR_W = 3.0, 2.2

DENT = 6.0  # how far the strike presses the top face down

# Frame 3 is the solid coming back to true. Each of its eight vertices is off
# its place by this much -- a fixed table, not a random one, so the file the
# launcher reads today is the file it reads tomorrow.
JITTER = ((-3.0, 2.0), (4.0, -3.0), (2.5, 3.5), (-3.5, -2.0),
          (3.0, 2.5), (-2.5, -3.5), (-4.0, 2.0), (2.0, -2.5))
NO_JITTER = ((0.0, 0.0),) * 8

# Chips still in the air on the realign frame, as (x, y, length, angle).
CHIPS = ((312.0, 196.0, 16.0, -16.0), (300.0, 232.0, 13.0, 6.0),
         (318.0, 264.0, 15.0, 22.0))

SPARK_ANGLES = (-148.0, -112.0, -74.0, -34.0, 8.0)
SPARK_NEAR, SPARK_FAR = 16.0, 44.0
SPARK_W = 4.5


def _polar(origin, length, degrees):
    a = math.radians(degrees)
    return (origin[0] + length * math.cos(a), origin[1] + length * math.sin(a))


def _ground(pen: Pen) -> None:
    pen.rect(GROUND_X0, GROUND_Y, GROUND_X1, GROUND_Y + GROUND_H, EDGE)


def _bilinear(corners, u: float, v: float):
    """A point on the top face, in the face's own (u, v) square.

    The face is read off its four drawn corners rather than off the box's
    nominal geometry, so a dented or jittered top carries its bore with it
    instead of leaving the hole hanging in the air where the face used to be.
    """
    fl, fr, br, bl = corners
    return (
        (1 - u) * (1 - v) * fl[0] + u * (1 - v) * fr[0]
        + u * v * br[0] + (1 - u) * v * bl[0],
        (1 - u) * (1 - v) * fl[1] + u * (1 - v) * fr[1]
        + u * v * br[1] + (1 - u) * v * bl[1],
    )


def _bore(pen: Pen, top_face) -> None:
    """The rim of the bore, its two silhouette lines, and the wall below them."""
    steps = 180
    rim = [_bilinear(top_face,
                     0.5 + BORE_R * math.cos(math.radians(t * 360.0 / steps)),
                     0.5 + BORE_R * math.sin(math.radians(t * 360.0 / steps)))
           for t in range(steps)]

    # Where the rim turns back on itself is where the wall of the bore starts:
    # the leftmost and rightmost samples, which is exact enough at 180 of them.
    left = min(range(steps), key=lambda i: rim[i][0])
    right = min(range(steps), key=lambda i: -rim[i][0])
    lo, hi = min(left, right), max(left, right)
    front = rim[lo:hi + 1]
    back = rim[hi:] + rim[:lo + 1]
    if sum(p[1] for p in back) / len(back) > sum(p[1] for p in front) / len(front):
        front = back

    # The rim is a cut in the near face and reads as bright as one; the wall
    # below it is inside the solid and goes back with the other far edges.
    dropped = [(x, y + BORE_DROP) for (x, y) in front]
    pen.stroke(dropped, EDGE_FAR, EDGE_FAR_W, caps=False)
    for end in (front[0], front[-1]):
        pen.stroke([end, (end[0], end[1] + BORE_DROP)], EDGE_FAR, EDGE_FAR_W)
    pen.stroke(rim + [rim[0]], EDGE, EDGE_FAR_W, caps=False)


def _box(pen: Pen, dent: float, jitter) -> None:
    """The solid: eight vertices, twelve edges, and the bore through the top."""
    front = [(BOX_L, BOX_TOP + dent), (BOX_R, BOX_TOP + dent),
             (BOX_R, BOX_BOTTOM), (BOX_L, BOX_BOTTOM)]
    back = [(x + BOX_DEPTH, y - BOX_DEPTH) for (x, y) in front]
    corners = [(x + jitter[i][0], y + jitter[i][1])
               for i, (x, y) in enumerate(front + back)]
    front, back = corners[:4], corners[4:]

    for near, far in zip(front, back):
        pen.stroke([near, far], EDGE_FAR, EDGE_FAR_W)
    pen.stroke(back + [back[0]], EDGE_FAR, EDGE_FAR_W)
    _bore(pen, (front[0], front[1], back[1], back[0]))
    pen.stroke(front + [front[0]], EDGE, EDGE_W)


def _standing_pose(index: int) -> Pose:
    """The icon's animal, on its feet, leaning into or out of the swing."""
    dx, dy = LEAN[index]
    hx, hy = HEAD_X + dx, HEAD_Y + dy
    bx, by = BODY_X + dx, BODY_Y + dy

    def hip(leg):
        return (bx + leg[0][0] * K, by + leg[0][1] * K)

    def knee(leg):
        return (BODY_X + leg[1][0] * K + dx * 0.4,
                BODY_Y + leg[1][1] * K + dy * 0.4)

    def ankle(leg):
        return (BODY_X + leg[2][0] * K, BODY_Y + leg[2][1] * K)

    def foot(spec):
        return (BODY_X + spec[0] * K, BODY_Y + spec[1] * K,
                spec[2] * K, spec[3] * K)

    limbs = (
        Limb((hip(BACK_LEG), knee(BACK_LEG), ankle(BACK_LEG)), 0.042 * K,
             hand=foot(BACK_FOOT)),
        Limb((hip(FRONT_LEG), knee(FRONT_LEG), ankle(FRONT_LEG)), 0.040 * K,
             hand=foot(FRONT_FOOT)),
    )

    return Pose(
        head=(hx, hy), head_r=HEAD_R * K,
        body=(bx, by), body_rx=BODY_RX * K, body_ry=BODY_RY * K,
        ear_r=EAR_R * K, ear_dx=EAR_DX * K, ear_dy=EAR_DY * K,
        face=3, tilt=TILT[index],
        # The icon's tail, mirrored: the animal faces the solid, so the curl
        # goes behind it. Reflecting the spiral flips its centre across the body
        # and runs its 430 degrees the other way round.
        tail=((bx - 0.050 * K, by + 0.029 * K),
              bx - TAIL_DX * K, by + TAIL_DY * K, TAIL_R0 * K, 0.032 * K,
              180.0 - TAIL_A0, 180.0 - TAIL_A0 + 430.0, TAIL_W * K),
        limbs=limbs,
    )


def _hammer(pen: Pen, index: int):
    """The near arm and what is in its hand. Answers where the head landed."""
    dx, dy = LEAN[index]
    bx, by = BODY_X + dx, BODY_Y + dy
    upper, fore, haft = SWING[index]

    shoulder = (bx + SHOULDER[0] * K, by + SHOULDER[1] * K)
    elbow = _polar(shoulder, UPPER, upper)
    hand = _polar(elbow, FORE, fore)
    centre = _polar(hand, HAFT, haft)

    pen.stroke([shoulder, elbow, hand], FUR, ARM_W, 1.5)
    pen.stroke([hand, centre], FUR, HAFT_W, 1.5)
    pen.oval(hand[0], hand[1], WRIST, WRIST * 0.92, FUR)

    # The head, square across the end of the haft: a bar, not a blob, so the
    # swing has a face to land on and a direction at every angle.
    along = (math.cos(math.radians(haft)), math.sin(math.radians(haft)))
    across = (-along[1], along[0])
    half_bar, half_thick = HEAD_BAR / 2, HEAD_THICK / 2
    pen.poly([(centre[0] + sa * across[0] * half_bar + sb * along[0] * half_thick,
               centre[1] + sa * across[1] * half_bar + sb * along[1] * half_thick)
              for sa, sb in ((-1, -1), (1, -1), (1, 1), (-1, 1))], PATCH)
    return centre


def _sparks(pen: Pen, at) -> None:
    for degrees in SPARK_ANGLES:
        pen.stroke([_polar(at, SPARK_NEAR, degrees),
                    _polar(at, SPARK_FAR, degrees)], SPARK, SPARK_W)


def _chips(pen: Pen) -> None:
    for x, y, length, degrees in CHIPS:
        pen.stroke([(x, y), _polar((x, y), length, degrees)], SPARK, SPARK_W)


def _resolve(art: Image.Image) -> Image.Image:
    """Box-filter the supersampled raster down, through premultiplied alpha.

    Averaging straight alpha would fold the transparent ground's black into the
    colour of every edge pixel, and a limestone hairline on an ink panel would
    come back with a dark rim around it. Multiplying the colour by its coverage
    first, averaging that, and dividing the coverage back out afterwards is the
    average the compositor would have arrived at.
    """
    r, g, b, a = art.split()
    alpha = a.resize((WIDTH, HEIGHT), Image.Resampling.BOX)
    bands = []
    for band in (r, g, b):
        weighted = ImageChops.multiply(band, a).resize(
            (WIDTH, HEIGHT), Image.Resampling.BOX)
        bands.append(ImageMath.lambda_eval(
            lambda args: args["convert"](
                args["min"](args["p"] * 255 / args["max"](args["a"], 1), 255),
                "L"),
            p=weighted, a=alpha))
    return Image.merge("RGBA", (*bands, alpha))


def render(index: int) -> Image.Image:
    """Draw one frame, 0-based, as an RGBA image over a transparent ground."""
    art = Image.new("RGBA", (WIDTH * SS, HEIGHT * SS), (0, 0, 0, 0))
    pen = Pen(ImageDraw.Draw(art), 1.0, SS)

    striking = index == 1
    realigning = index == 2

    _ground(pen)
    _box(pen, DENT if striking else 0.0, JITTER if realigning else NO_JITTER)
    draw_monkey(pen, _standing_pose(index))
    landed = _hammer(pen, index)
    if striking:
        _sparks(pen, landed)
    if realigning:
        _chips(pen)

    return _resolve(art)


def main() -> None:
    here = Path(__file__).resolve().parent
    for index in range(len(SWING)):
        path = here / f"frame-{index + 1:02d}.png"
        render(index).save(path, format="PNG", optimize=True)
        print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
