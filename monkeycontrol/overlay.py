"""Replaying a recording under one overlay projection, after the fact.

The raw frames carry no annotation, so the same recording can be re-drawn for
a different audience without running anything again: ``clean`` shows only
where the pointer went, ``presentation`` names the step and the element, and
``developer`` adds what a reviewer needs to argue with -- the backend, the
automation id, the bounds and what the verification actually said. Pillow is
imported inside the renderer, so a machine without it refuses by name instead
of failing to import this package.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path

from .runtime import RuntimeRefusal
from .store import ActionTraceStore

PROJECTIONS = ("clean", "presentation", "developer")
#: Events whose detail starts with a screen rectangle.
BOXED = ("target_found", "highlight")
DEFAULT_INTERVAL_MS = 250
#: The leading "x,y" or "left,top,right,bottom" a geometric detail begins with.
GEOMETRY = re.compile(r"^(-?\d+(?:,-?\d+)+)\s*(.*)$", re.DOTALL)
MARK = "#FF7A00"
CURSOR = "#FFFFFF"
INK = "#101010"
PASSED = "#1F9D55"
FAILED = "#C62828"
#: Faces tried in order, so a label keeps every glyph the desktop showed.
FACES = ("msyh.ttc", "simsun.ttc", "seguiemj.ttf", "segoeui.ttf", "DejaVuSans.ttf")
#: A frame is a whole desktop and is read scaled down, so everything drawn on
#: it is sized against this reference width rather than in absolute pixels.
REFERENCE_WIDTH = 1920
MAX_SCALE = 3.0


def _pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeRefusal(
            "BACKEND_UNAVAILABLE",
            "rendering an overlay projection needs Pillow, which is not installed",
        ) from exc
    return Image, ImageDraw, ImageFont


def _font(size: int):
    """A font that can draw a target's real name, CJK glyphs included.

    Pillow's built-in font is Latin only, and half the control names on a
    Chinese Windows would come out as empty boxes, which is exactly the label
    a viewer needs to read.
    """

    _, _, ImageFont = _pillow()
    for face in FACES:
        try:
            return ImageFont.truetype(face, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow older than 10.1
        return ImageFont.load_default()


def _geometry(detail: str) -> tuple[list[int], str]:
    """The screen numbers a detail begins with, and the words after them."""

    match = GEOMETRY.match(detail or "")
    if not match:
        return [], detail or ""
    return [int(part) for part in match.group(1).split(",")], match.group(2)


def _chip(draw, xy, text: str, font, fill: str, scale: float) -> None:
    """A filled caption box, so a label stays readable over any wallpaper."""

    left, top = xy
    pad = round(6 * scale)
    box = draw.textbbox((left, top), text, font=font)
    draw.rectangle(
        (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
        fill=fill,
        outline=INK,
    )
    draw.text((left, top), text, font=font, fill="#FFFFFF")


def _cursor(draw, point: tuple[int, int], ripple: bool, scale: float) -> None:
    x, y = point
    dot = round(7 * scale)
    draw.ellipse(
        (x - dot, y - dot, x + dot, y + dot),
        fill=CURSOR,
        outline=INK,
        width=max(2, round(2 * scale)),
    )
    if ripple:
        for radius in (round(18 * scale), round(30 * scale)):
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                outline=MARK,
                width=max(3, round(3 * scale)),
            )


def _caption(event: dict, rest: str) -> str:
    """What a boxed event says about its target, in one line."""

    if event.get("event") == "highlight":
        return rest
    # A resolution says more than a viewer needs: keep the type and the name.
    head = rest.split(" automationId=")[0]
    return f"{event.get('step_id', '')} {head}".strip()


def _paint(image, events: list[dict], projection: str, origin: tuple[int, int]) -> None:
    """Draw one frame's active events, layer by layer, in place."""

    _, ImageDraw, _ = _pillow()
    draw = ImageDraw.Draw(image)
    scale = min(MAX_SCALE, max(1.0, image.width / REFERENCE_WIDTH))
    label = _font(round(18 * scale))
    small = _font(round(14 * scale))
    named = projection in ("presentation", "developer")

    def shift(numbers: list[int]) -> list[int]:
        return [value - origin[index % 2] for index, value in enumerate(numbers)]

    # One rectangle is one box, however many events mention it: the highlight's
    # own label wins, because it is the sentence the step was announced with.
    boxes: dict[tuple, str] = {}
    for event in events:
        numbers, rest = _geometry(event.get("detail", ""))
        if len(numbers) == 4 and event.get("event") in BOXED:
            rectangle = tuple(shift(numbers))
            if event.get("event") == "highlight" or rectangle not in boxes:
                boxes[rectangle] = _caption(event, rest)
        elif len(numbers) >= 2:
            point = shift(numbers[:2])
            _cursor(draw, (point[0], point[1]), event.get("event") == "click", scale)
            if len(numbers) == 4:  # a drag ends somewhere else
                end = shift(numbers[2:])
                draw.line(
                    (point[0], point[1], end[0], end[1]),
                    fill=MARK,
                    width=max(3, round(4 * scale)),
                )
                _cursor(draw, (end[0], end[1]), False, scale)
    if named:
        for rectangle, caption in boxes.items():
            draw.rectangle(rectangle, outline=MARK, width=max(3, round(4 * scale)))
            _chip(
                draw,
                (rectangle[0], max(round(8 * scale), rectangle[1] - round(34 * scale))),
                caption[:90] or "target",
                label,
                MARK,
                scale,
            )
    if not named:
        return
    step = round(32 * scale)
    margin = round(24 * scale)
    for index, verdict in enumerate(
        event for event in events if event.get("event") == "verify"
    ):
        held = str(verdict.get("detail", "")).startswith("passed")
        _chip(
            draw,
            (margin, margin + index * step),
            f"{verdict.get('step_id', '')} VERIFY {'OK' if held else 'X'} "
            f"{verdict.get('detail', '')}"[:120],
            label,
            PASSED if held else FAILED,
            scale,
        )
    if projection != "developer":
        return
    for index, event in enumerate(events):
        _chip(
            draw,
            (margin, margin + round(120 * scale) + index * round(26 * scale)),
            f"{event.get('t')}s {event.get('event')}: {event.get('detail', '')}"[:160],
            small,
            INK,
            scale,
        )


def render_overlays(
    recording_dir: Path, projection: str, *, out_dir: Path | None = None
) -> dict:
    """Re-draw every frame of one recording under ``projection``.

    An event is drawn on the frames within one capture interval of it, which
    is the whole reason the timeline keeps seconds rather than frame numbers:
    the same events can be replayed over a recording taken at any rate.
    """

    if projection not in PROJECTIONS:
        raise ValueError(
            f"projection must be one of {', '.join(PROJECTIONS)}, not {projection!r}"
        )
    Image, _, _ = _pillow()
    directory = Path(recording_dir)
    source = ActionTraceStore(directory)
    manifest = _manifest(directory)
    span = float(manifest.get("interval_ms") or DEFAULT_INTERVAL_MS) / 1000
    timeline = source.read_lines("timeline.ndjson")
    frames = source.read_lines("frames.ndjson")
    fallback = directory / "overlays" / projection
    target = Path(out_dir) if out_dir is not None else fallback
    store = ActionTraceStore(target)
    drawn = 0
    for frame in frames:
        path = directory / str(frame.get("path") or "")
        if not path.is_file():
            continue
        moment = float(frame.get("t") or 0.0)
        active = [
            event
            for event in timeline
            if abs(float(event.get("t") or 0.0) - moment) <= span
        ]
        bounds = frame.get("bounds") or (0, 0, 0, 0)
        with Image.open(path) as opened:
            picture = opened.convert("RGB")
        _paint(picture, active, projection, (int(bounds[0]), int(bounds[1])))
        buffer = _png(picture)
        store.save_bytes(".", buffer, ".png", name=path.name)
        drawn += 1
    return {"projection": projection, "frames": drawn, "out_dir": str(target)}


def _png(picture) -> bytes:
    buffer = BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _manifest(directory: Path) -> dict:
    path = directory / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
