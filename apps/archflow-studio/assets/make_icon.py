#!/usr/bin/env python3
"""Draw the MonkeyArch icon: monkeyarch.ico, plus a 512 px PNG preview.

    py -3.12 apps/archflow-studio/assets/make_icon.py

MonkeyArch is the software; ArchFlow stays the name of the method it runs --
record-driven derivation of buildings. The mark has to carry both at once, so it
is one figure and one structure: a semicircular arch -- two piers, a ring, an
amber keystone at the crown -- and a monkey hanging from that keystone by one
arm. The keystone is the piece that has to be in place before either of them
holds, which is why it is the only amber in the icon and why the animal's hand
is on it.

Four colours on a rounded-square tile: ink for the tile, limestone for the
stonework and for the monkey's face patch, amber for the keystone, one warm
brown for the animal. The tile's corners are transparent; the tile is dark
enough to hold its own against a light Desktop and, with its rim, light enough
to show on a dark one. There is no text at any size.

Every size is drawn from its own spec rather than downscaled from one large
image, because 16 px cannot carry what 256 px can, and here the staging is the
animal's rather than the arch's -- the arch ring and the amber keystone are
there at every size, and it is the monkey that is spent down:

    16 px   a head, two ears and a short body, hanging straight under the
            keystone. No arm, no free hand, no tail, no face: at this scale
            each of them is a smear that costs the silhouette.
    24 px   the same figure, and a stub tail; the face arrives as one plain
            limestone oval, which is all a five-pixel head can hold.
    32 px   the figure swings off to one side, the arm reaches up to the
            keystone, the face patch takes its shape, and the tail becomes a
            hook.
    48 px   the whole animal: the eyes cut into the patch, the free hand out on
            the intrados, a leg, and a tail whose hook closes into a loop.
   128 px   and up: the second leg, the nose and mouth, and the two mortar
            joints that cut the keystone out of the ring.

Each size is rasterised at 8x and box-filtered down -- exact area averaging over
the supersamples -- so two runs of this script write the same bytes.

`draw_monkey(pen, pose)` and `Pose` keep the figure's geometry separate from the
icon's hanging pose. The application icon is the only UI surface that uses the
figure; operational progress is expressed by status and progress controls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

INK = (18, 38, 63)  # #12263F the tile, the opening, the eyes
RIM = (46, 74, 110)  # #2E4A6E the tile's edge, so it shows on a dark Desktop
STONE = (244, 239, 230)  # #F4EFE6 limestone: the arch, and the face patch
AMBER = (232, 163, 61)  # #E8A33D the keystone, and nothing else
BROWN = (168, 112, 64)  # #A87040 the monkey

SS = 8  # supersampling factor; the downsample is a box filter, so this is exact

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PREVIEW_SIZE = 512

# How much of the animal each size can carry. 0 and 1 draw the reduced figure,
# 2 and up the full one.
LEVEL = {16: 0, 24: 1, 32: 2, 48: 3, 64: 3, 128: 4, 256: 4, 512: 4}

# margin, corner radius, rim width -- in that size's own device pixels.
TILE = {16: (0, 3, 1), 24: (0, 5, 1), 32: (0, 6, 1), 48: (1, 9, 1),
        64: (2, 12, 1), 128: (4, 24, 2), 256: (8, 48, 3), 512: (16, 96, 6)}

# The arch, per size, in that size's own device pixels, so that the ring lands
# on whole pixels where there are few of them to land on. x0/x1 are the outer
# faces of the piers, top is the crown of the extrados, base is the foot of the
# piers, thick is the depth of the ring; the centre, the springing line and the
# intrados follow. key is the amber keystone as (half-width at the extrados,
# half-width at its foot, rise above the extrados, drop below the intrados) --
# it always drops past the intrados, because that overhang is what the hand
# grips. mortar is the width of the two joints flanking it, or None.
ARCH = {
    16: {"x0": 1, "x1": 15, "top": 2, "base": 15, "thick": 2,
         "key": (2.25, 1.75, 1, 1.25), "mortar": None},
    24: {"x0": 2, "x1": 22, "top": 2, "base": 22, "thick": 3,
         "key": (3.25, 2.5, 1, 1.5), "mortar": None},
    32: {"x0": 4, "x1": 28, "top": 4, "base": 26, "thick": 3.5,
         "key": (4, 3, 2, 2), "mortar": None},
    48: {"x0": 7, "x1": 41, "top": 6, "base": 39, "thick": 4.5,
         "key": (5, 3.5, 2, 2.5), "mortar": None},
    64: {"x0": 10, "x1": 54, "top": 8, "base": 52, "thick": 5.5,
         "key": (6.5, 4.5, 2.5, 3), "mortar": None},
    128: {"x0": 20, "x1": 108, "top": 15, "base": 105, "thick": 11,
          "key": (13, 9, 5, 6), "mortar": 1},
    256: {"x0": 40, "x1": 216, "top": 30, "base": 210, "thick": 22,
          "key": (26, 18, 10, 12), "mortar": 2},
    512: {"x0": 80, "x1": 432, "top": 60, "base": 420, "thick": 44,
          "key": (52, 36, 20, 24), "mortar": 4},
}

# ---------------------------------------------------------------------------
# The reduced animal, at 16 and 24 px: a head, two ears at eye level and a short
# body, hanging straight under the keystone. Fractions of the tile side;
# overlap is how far the head's crown goes up behind the keystone's foot, which
# is the whole of what says "hanging" once the arm is gone. tail, when it is
# there, is (centre dx, centre dy, start radius, end radius, start angle, end
# angle, stroke) -- dx and dy from the body's centre, angles as in PIL, 0 east
# and 90 south. face is the limestone patch as (dy, rx, ry), no eyes cut in it.
REDUCED = {
    0: {"overlap": 0.017, "head_r": 0.122,
        "ear_r": 0.070, "ear_dx": 0.134, "ear_dy": -0.020,
        "body_dy": 0.198, "body_rx": 0.134, "body_ry": 0.124,
        "tail": None, "face": None},
    1: {"overlap": 0.016, "head_r": 0.118,
        "ear_r": 0.070, "ear_dx": 0.128, "ear_dy": -0.018,
        "body_dy": 0.196, "body_rx": 0.120, "body_ry": 0.122,
        "tail": (0.128, 0.056, 0.092, 0.055, 158.0, -52.0, 0.046),
        "face": (0.020, 0.055, 0.052)},
}

# ---------------------------------------------------------------------------
# The full animal, from 32 px up. Every offset is a fraction of the tile side,
# and every one of them is measured from something else in the figure rather
# than from the tile, so that the whole animal follows the keystone's foot --
# which sits lower, relative to the tile, the smaller the tile gets.
GRIP_DY = -0.014  # the gripping hand's centre, above the keystone's foot
HAND_RX, HAND_RY = 0.044, 0.032
HEAD_DX, HEAD_DY = -0.070, 0.212  # the head, from the grip
HEAD_R = 0.098
EAR_R, EAR_DX, EAR_DY = 0.058, 0.112, -0.016  # from the head: large, and low
BODY_DX, BODY_DY = -0.002, 0.130  # from the head
BODY_RX, BODY_RY = 0.070, 0.104  # narrower than the head is wide
ARM_W = 0.052
TAIL_DX, TAIL_DY = 0.185, -0.006  # the tail's spiral centre, from the body
TAIL_R0, TAIL_A0, TAIL_W = 0.115, 158.0, 0.030

# k grows the animal as the tile shrinks, or the arch swallows it. tail_sweep
# and tail_r1 turn the tail from a hook into a hook that closes on a loop.
FULL = {
    2: {"k": 1.05, "tail_sweep": 300.0, "tail_r1": 0.048,
        "face": 1, "legs": 0, "free_arm": False},
    3: {"k": 1.00, "tail_sweep": 400.0, "tail_r1": 0.036,
        "face": 2, "legs": 1, "free_arm": True},
    4: {"k": 1.00, "tail_sweep": 430.0, "tail_r1": 0.032,
        "face": 3, "legs": 2, "free_arm": True},
}


class Pen:
    """Draws onto a supersampled raster in whatever unit the caller works in.

    `scale` is how many device pixels one of those units is worth. The icon
    works in fractions of the tile side and hands over the tile's size, so the
    same geometry can be drawn deliberately at every icon resolution.
    """

    def __init__(self, draw: ImageDraw.ImageDraw, scale: float, ss: int = SS):
        self.d = draw
        self.scale = scale
        self.ss = ss

    def q(self, u: float) -> int:
        """One of the caller's units, in supersampled device pixels."""
        return int(round(u * self.scale * self.ss))

    def w(self, u: float, floor_px: float = 1.0) -> int:
        """A stroke width: never thinner than floor_px real pixels."""
        return int(round(max(u * self.scale, floor_px) * self.ss))

    def rect(self, x0, y0, x1, y1, colour) -> None:
        self.d.rectangle([self.q(x0), self.q(y0), self.q(x1) - 1,
                          self.q(y1) - 1], fill=colour)

    def disc(self, cx, cy, r, colour) -> None:
        self.oval(cx, cy, r, r, colour)

    def oval(self, cx, cy, rx, ry, colour) -> None:
        self.d.ellipse([self.q(cx - rx), self.q(cy - ry), self.q(cx + rx) - 1,
                        self.q(cy + ry) - 1], fill=colour)

    def pie(self, cx, cy, r, a0, a1, colour) -> None:
        self.d.pieslice([self.q(cx - r), self.q(cy - r), self.q(cx + r) - 1,
                         self.q(cy + r) - 1], a0, a1, fill=colour)

    def poly(self, points, colour) -> None:
        self.d.polygon([(self.q(x), self.q(y)) for x, y in points], fill=colour)

    def stroke(self, points, colour, width, floor_px=1.0, caps=True) -> None:
        pts = [(self.q(x), self.q(y)) for x, y in points]
        wide = self.w(width, floor_px)
        self.d.line(pts, fill=colour, width=wide, joint="curve")
        if caps:  # PIL rounds joints but not ends
            for x, y in (pts[0], pts[-1]):
                self.d.ellipse([x - wide // 2, y - wide // 2,
                                x + wide // 2, y + wide // 2], fill=colour)


class Canvas(Pen):
    """A supersampled tile. All coordinates are fractions of the tile side."""

    def __init__(self, size: int):
        self.size = size
        self.side = size * SS
        # The canvas starts ink, not transparent, so the box filter never blends
        # a colour with an unpainted pixel at the tile's rounded corners.
        self.img = Image.new("RGB", (self.side, self.side), INK)
        super().__init__(ImageDraw.Draw(self.img), size, SS)

    def finish(self) -> Image.Image:
        margin, radius, _ = TILE[self.size]
        n = float(self.size)
        box = [self.q(margin / n), self.q(margin / n),
               self.q((self.size - margin) / n) - 1,
               self.q((self.size - margin) / n) - 1]
        alpha = Image.new("L", (self.side, self.side), 0)
        ImageDraw.Draw(alpha).rounded_rectangle(box, radius=self.q(radius / n),
                                                fill=255)
        icon = self.img.resize((self.size, self.size),
                               Image.Resampling.BOX).convert("RGBA")
        icon.putalpha(alpha.resize((self.size, self.size),
                                   Image.Resampling.BOX))
        return icon


def _tile(c: Canvas) -> None:
    margin, radius, rim_w = TILE[c.size]
    n = float(c.size)
    c.d.rounded_rectangle(
        [c.q(margin / n), c.q(margin / n),
         c.q((c.size - margin) / n) - 1, c.q((c.size - margin) / n) - 1],
        radius=c.q(radius / n), fill=INK, outline=RIM, width=c.q(rim_w / n),
    )


def _spiral(cx, cy, r0, r1, a0, a1, steps=112):
    """A polyline whose radius shrinks as its angle sweeps: the tail's curl."""
    points = []
    for i in range(steps + 1):
        t = i / steps
        a = math.radians(a0 + (a1 - a0) * t)
        r = r0 + (r1 - r0) * t
        points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def _tilt_about(degrees: float, ox: float, oy: float):
    """A rotation of the head's furniture about the head's centre.

    An untilted head returns the identity rather than a rotation by zero: the
    icon's poses are all untilted, and a round trip through the rotation would
    move points by a last bit or two and change the icon's bytes.
    """
    if not degrees:
        return lambda x, y: (x, y)
    ca, sa = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return lambda x, y: (ox + (x - ox) * ca - (y - oy) * sa,
                         oy + (x - ox) * sa + (y - oy) * ca)


@dataclass(frozen=True)
class Limb:
    """One arm or leg: a stroked polyline, and the hand or foot at the end.

    `hand` is (cx, cy, rx, ry) or None -- a hanging arm ends in a hand, a leg
    that is only a bent line does not.
    """

    points: tuple
    width: float
    hand: tuple | None = None
    floor_px: float = 1.5


@dataclass(frozen=True)
class Pose:
    """Where the animal's parts are, in the caller's units.

    The pose carries the head, ears, limestone face patch, body, limbs and
    spiral tail in the caller's units, so it carries the scale rather than the
    drawing routine carrying a factor.

    The order the parts are drawn in is the order they overlap in: the tail
    behind everything, then the limbs, then the body over where they join it,
    then the ears, the head, the face, and last of all `grip` -- the hand that
    has to be over whatever the animal is holding.
    """

    head: tuple
    head_r: float
    body: tuple
    body_rx: float
    body_ry: float
    ear_r: float
    ear_dx: float
    ear_dy: float
    face: int = 0
    tilt: float = 0.0
    # (root point, centre x, centre y, start radius, end radius, start angle,
    # end angle, stroke) -- the root is where the curl leaves the rump.
    tail: tuple | None = None
    limbs: tuple = ()
    grip: tuple | None = None
    fur: tuple = BROWN
    stone: tuple = STONE
    ink: tuple = INK


def draw_monkey(pen: Pen, pose: Pose) -> None:
    """Draw the animal in one pose, through one pen, in the pen's units."""
    hx, hy = pose.head
    bx, by = pose.body

    if pose.tail is not None:
        root, tcx, tcy, r0, r1, a0, a1, width = pose.tail
        pen.stroke([root] + _spiral(tcx, tcy, r0, r1, a0, a1),
                   pose.fur, width, 1.5)

    for limb in pose.limbs:
        pen.stroke(list(limb.points), pose.fur, limb.width, limb.floor_px)
        if limb.hand is not None:
            pen.oval(*limb.hand, pose.fur)

    pen.oval(bx, by, pose.body_rx, pose.body_ry, pose.fur)
    turn = _tilt_about(pose.tilt, hx, hy)
    for side in (-1, 1):
        pen.disc(*turn(hx + side * pose.ear_dx, hy + pose.ear_dy),
                 pose.ear_r, pose.fur)
    pen.disc(hx, hy, pose.head_r, pose.fur)
    _face(pen, pose.face, hx, hy, pose.head_r,
          tilt=pose.tilt, stone=pose.stone, ink=pose.ink)

    if pose.grip is not None:
        pen.oval(*pose.grip, pose.fur)


def _face(c: Pen, mode: int, hx: float, hy: float, hr: float, *,
          tilt: float = 0.0, stone=STONE, ink=INK) -> None:
    """The limestone patch: two lobes over the eyes, tapering to the muzzle.

    One shape, not a muzzle stuck under a mask -- an inverted teardrop with a
    notch cut between the lobes, which is what stops the head reading as a bear.
    It sits a little off the head's centre, so the animal looks slightly at you.

    At mode 1 -- 32 px, where the whole head is seven pixels across -- the
    patch is drawn a size smaller and no eyes are cut into it: two ink dots
    two-thirds of a pixel wide come back from the box filter as one grey smear
    across the middle of it, which costs the face more than it buys.
    """
    if mode <= 0:
        return
    scale = 0.90 if mode == 1 else 1.0
    fx = hx + 0.10 * hr
    lobe_r, lobe_dx = 0.42 * hr * scale, 0.33 * hr * scale
    lobe_cy = hy - 0.17 * hr
    muzzle_cy = lobe_cy + 0.50 * hr * scale
    mrx, mry = 0.42 * hr * scale, 0.33 * hr * scale
    turn = _tilt_about(tilt, hx, hy)

    c.poly([turn(fx - lobe_dx - lobe_r, lobe_cy),
            turn(fx + lobe_dx + lobe_r, lobe_cy),
            turn(fx + mrx, muzzle_cy), turn(fx - mrx, muzzle_cy)], stone)
    for side in (-1, 1):
        c.disc(*turn(fx + side * lobe_dx, lobe_cy), lobe_r, stone)
    c.oval(*turn(fx, muzzle_cy), mrx, mry, stone)

    if mode >= 2:
        for side in (-1, 1):
            c.disc(*turn(fx + side * lobe_dx, lobe_cy), 0.175 * hr, ink)

    if mode >= 3:
        for side in (-1, 1):
            c.disc(*turn(fx + side * 0.13 * hr, muzzle_cy - 0.06 * hr),
                   0.062 * hr, ink)
        c.stroke([turn(fx - 0.17 * hr, muzzle_cy + 0.12 * hr),
                  turn(fx, muzzle_cy + 0.19 * hr),
                  turn(fx + 0.17 * hr, muzzle_cy + 0.12 * hr)], ink, 0.060 * hr)


def _reduced_monkey(c: Canvas, level: int, cx: float, foot: float) -> None:
    """16 and 24 px: the creature under the keystone, and nothing to spare."""
    p = REDUCED[level]
    hy = foot + p["head_r"] - p["overlap"]
    by = hy + p["body_dy"]

    if p["tail"] is not None:
        dx, dy, r0, r1, a0, a1, width = p["tail"]
        root = (cx + p["body_rx"] * 0.45, by + p["body_ry"] * 0.50)
        c.stroke([root] + _spiral(cx + dx, by + dy, r0, r1, a0, a1),
                 BROWN, width, 1.5)

    c.oval(cx, by, p["body_rx"], p["body_ry"], BROWN)
    for side in (-1, 1):
        c.disc(cx + side * p["ear_dx"], hy + p["ear_dy"], p["ear_r"], BROWN)
    c.disc(cx, hy, p["head_r"], BROWN)

    if p["face"] is not None:
        dy, rx, ry = p["face"]
        c.oval(cx, hy + dy, rx, ry, STONE)


def _hanging_pose(level: int, cx: float, foot: float, pier: float) -> Pose:
    """32 px and up: hanging by one arm, the other hand out on the intrados."""
    cfg = FULL[level]
    k = cfg["k"]
    hx, hy = cx + HEAD_DX * k, foot + HEAD_DY * k
    bx, by = hx + BODY_DX * k, hy + BODY_DY * k

    limbs = []
    if cfg["legs"] >= 1:
        limbs.append(Limb(((bx - 0.026 * k, by + 0.108 * k),
                           (bx - 0.070 * k, by + 0.155 * k),
                           (bx - 0.026 * k, by + 0.175 * k)), 0.040 * k))
    if cfg["legs"] >= 2:
        limbs.append(Limb(((bx + 0.030 * k, by + 0.106 * k),
                           (bx + 0.078 * k, by + 0.143 * k),
                           (bx + 0.046 * k, by + 0.173 * k)), 0.038 * k))

    if cfg["free_arm"]:
        # The free hand, out on the inner face of the pier. It has to land on
        # the stone and not near it: an arm that stops a hair short reads as a
        # stump, and the whole point of the second hand is that the animal is
        # holding the arch it is hanging in.
        hand = (pier + 0.014 * k, by + 0.062 * k)
        limbs.append(Limb(((bx - 0.055 * k, by - 0.010 * k),
                           (bx - 0.125 * k, by + 0.040 * k), hand), 0.040 * k,
                          hand=(hand[0], hand[1], 0.034 * k, 0.030 * k)))

    # The gripping arm, straight up to the keystone. It runs behind the head and
    # the far ear, which are the same brown, so the two read as one form.
    limbs.append(Limb(((hx + 0.046 * k, hy + 0.088 * k),
                       (hx + 0.060 * k, hy - 0.020 * k),
                       (cx - 0.002, foot + 0.004)), ARM_W * k))

    return Pose(
        head=(hx, hy), head_r=HEAD_R * k,
        body=(bx, by), body_rx=BODY_RX * k, body_ry=BODY_RY * k,
        ear_r=EAR_R * k, ear_dx=EAR_DX * k, ear_dy=EAR_DY * k,
        face=cfg["face"],
        # The tail, behind everything: down off the rump, out to the right, up
        # and over, and round into a loop that keeps a hole in it at 48 px.
        tail=((bx + 0.050 * k, by + 0.029 * k),
              bx + TAIL_DX * k, by + TAIL_DY * k, TAIL_R0 * k,
              cfg["tail_r1"] * k, TAIL_A0, TAIL_A0 - cfg["tail_sweep"],
              TAIL_W * k),
        limbs=tuple(limbs),
        # The hand last of all, over the amber: the one place the two halves of
        # the mark touch, so nothing is drawn across it.
        grip=(cx, foot + GRIP_DY * k, HAND_RX * k, HAND_RY * k),
    )


def render(size: int) -> Image.Image:
    """Draw one size of the icon as an RGBA image with transparent corners."""
    level = LEVEL[size]
    spec = ARCH[size]
    n = float(size)
    c = Canvas(size)
    _tile(c)

    x0, x1 = spec["x0"] / n, spec["x1"] / n
    top, base, thick = spec["top"] / n, spec["base"] / n, spec["thick"] / n
    cx = (x0 + x1) / 2
    radius = (x1 - x0) / 2
    springing = top + radius
    inner = radius - thick

    # The ring: the outer portal in stone, the opening carved back to ink. The
    # one device pixel of overlap at the springing keeps the pie and the piers
    # from leaving a seam between them.
    c.pie(cx, springing, radius, 180, 360, STONE)
    c.rect(x0, springing - 1.0 / n, x1, base, STONE)
    c.pie(cx, springing, inner, 180, 360, INK)
    c.rect(x0 + thick, springing - 1.0 / n, x1 - thick, base, INK)

    wide, narrow, rise, drop = (v / n for v in spec["key"])
    crown, foot = top - rise, top + thick + drop
    c.poly([(cx - wide, crown), (cx + wide, crown),
            (cx + narrow, foot), (cx - narrow, foot)], AMBER)
    if spec["mortar"] is not None:
        for side in (-1, 1):
            c.stroke([(cx + side * wide, crown), (cx + side * narrow, foot)],
                     INK, spec["mortar"] / n, caps=False)

    if level <= 1:
        _reduced_monkey(c, level, cx, foot)
    else:
        draw_monkey(c, _hanging_pose(level, cx, foot, x0 + thick))

    return c.finish()


def main() -> None:
    here = Path(__file__).resolve().parent
    ico_path = here / "monkeyarch.ico"
    png_path = here / "monkeyarch-icon-512.png"

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
