"""Model-axis elevations, axonometrics and section perspectives of verified shapes, drawn in memory.

Every selected shape takes part in one exact hidden-line solve for a frame
stated along the model axes (``monkeycad.backends.occt.projection.project_occt_lines``);
the visible (and, on request, hidden) polylines are cropped and serialised as
one deterministic SVG whose every polyline names its source physical object,
and a PNG is rendered from that SVG (``monkeydiagram.rendering.svg``).

What this module decides and nothing else:

- the frame is checked to be right-handed and consistent (``look`` is the
  opposite of ``right x up``), the crop window and near/far are finite and
  ordered, and the view states exactly what was applied;
- the drawn lines are cleaned at ``CLEANUP_TOLERANCE_MM`` on the sheet
  (``clean_drawing``), and the projection keeps the report of what was removed.

``project_model_axis_elevation`` and ``project_section_perspective`` return
plain values and mint nothing; ``drawing_runs`` retains them. A section
perspective has an arbitrary section plane, the kept side drawn in exact
perspective from an eye on the removed side, the cut in poché and true to
scale because the picture plane is the section plane. ``axonometric_frame``
gives a parallel view from any non-vertical direction, drawn as an elevation is.
"""

from __future__ import annotations

import asyncio
import math
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from monkeycad.backends.occt.errors import OcctBackendError
from monkeycad.backends.occt.projection import OcctDrawingPolyline, project_occt_lines
from monkeycad.backends.occt.section import OcctSectionPerspective, project_occt_section_perspective, section_occt_lines
from monkeycad.backends.occt.step import StepEntry
from monkeydiagram.rendering.svg import (
    CleanupReport,
    DrawingSvgError,
    clean_drawing,
    crop_polylines,
    drawing_svg,
    render_svg_png,
    svg_objects,
)
from monkeydiagram.sources import (
    DRAWING_LENGTH_UNITS,
    DrawingElevationError,
    VerifiedElevationSource,
    current_object_id,
    inspection_witness_ids,
)
from archflow.project.refs import require_identifier

ELEVATION_KIND = "model-axis-elevation"
SECTION_PERSPECTIVE_KIND = "section-perspective"
_TOLERANCE = 1e-9
#: Paper tolerance of the drawing cleanup: at any sheet scale, what differs by less is not seen.
CLEANUP_TOLERANCE_MM = 0.05


@contextmanager
def observed_stage(observer, phase: str, *, parent_event_id: str | None = None, details=None):
    """Report a real call boundary without changing its value or exception."""

    details = {} if details is None else details
    if observer is None:
        yield details
        return
    started_at, started = datetime.now(timezone.utc).isoformat(), perf_counter()
    event_id = str(uuid4())

    def report(status, *, ended_at=None, duration_ms=None):
        try:
            observer({"event_id": event_id, "parent_event_id": parent_event_id, "phase": phase,
                      "status": status, "started_at": started_at, "ended_at": ended_at,
                      "duration_ms": duration_ms, "details": dict(details)})
        except (Exception, asyncio.CancelledError):
            pass

    report("running")
    status = "succeeded"
    try:
        yield details
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)) else "failed"
        raise
    finally:
        report(status, ended_at=datetime.now(timezone.utc).isoformat(), duration_ms=round((perf_counter() - started) * 1000))


def _vector(value, label: str) -> tuple[float, float, float]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        raise DrawingElevationError(f"{label} must be three finite numbers")
    return tuple(float(v) for v in value)


def _unit(value, label: str) -> tuple[float, float, float]:
    vector = _vector(value, label)
    if not math.isclose(math.hypot(*vector), 1.0, rel_tol=0.0, abs_tol=_TOLERANCE):
        raise DrawingElevationError(f"{label} must be a unit direction")
    return vector


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def finite_number(value, label: str) -> float:
    """Read one finite drawing coordinate or setting, excluding booleans."""

    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DrawingElevationError(f"{label} must be a finite number")
    return float(value)


def clean_view_lines(lines, regions, *, crop_uv, hidden_lines: bool, unit: str, scale_denominator: int):
    """The lines one drawing draws, cropped to its window and cleaned at the paper tolerance, with the report.

    The tolerance is ``CLEANUP_TOLERANCE_MM`` on the sheet, in the source's
    unit at the drawing's scale.  Hidden lines take part only when drawn.
    """

    drawn = lines if hidden_lines else tuple(line for line in lines if line.kind != "hidden")
    tolerance = CLEANUP_TOLERANCE_MM * scale_denominator / (1000.0 * UNIT_METRES[unit])
    return clean_drawing(crop_polylines(drawn, crop_uv), regions, tolerance=tolerance)


@dataclass(frozen=True, slots=True)
class ElevationView:
    """One orthographic elevation frame along the model axes, in the STEP's CAD Z-up unit.

    ``origin`` is the drawing origin; ``right``/``up`` span the sheet and
    ``look`` must be ``-(right x up)`` (the sheet's normal points at the
    viewer).  ``crop_uv`` is the drawn window in sheet coordinates
    ``(u_min, v_min, u_max, v_max)`` with ``u = dot(p - origin, right)``,
    ``v = dot(p - origin, up)``; ``near_depth``/``far_depth`` bound
    ``dot(p - origin, look)``.  ``hidden_lines`` False draws visible lines
    only; the solve still computes hidden ones and the receipt counts them.
    """

    name: str
    origin: tuple[float, float, float]
    look: tuple[float, float, float]
    right: tuple[float, float, float]
    up: tuple[float, float, float]
    crop_uv: tuple[float, float, float, float]
    near_depth: float
    far_depth: float
    hidden_lines: bool = False
    linear_deflection: float = 0.0001
    scale_denominator: int = 100

    def __post_init__(self) -> None:
        try:
            require_identifier(self.name, "view name")
        except ValueError as exc:
            raise DrawingElevationError(str(exc)) from exc
        object.__setattr__(self, "origin", _vector(self.origin, "origin"))
        object.__setattr__(self, "look", _unit(self.look, "look"))
        object.__setattr__(self, "right", _unit(self.right, "right"))
        object.__setattr__(self, "up", _unit(self.up, "up"))
        if abs(sum(r * u for r, u in zip(self.right, self.up))) > _TOLERANCE:
            raise DrawingElevationError("right and up must be perpendicular")
        normal = _cross(self.right, self.up)
        if any(abs(l + n) > _TOLERANCE for l, n in zip(self.look, normal)):
            raise DrawingElevationError("look must be the opposite of right x up (the sheet faces the viewer)")
        if (not isinstance(self.crop_uv, Sequence) or isinstance(self.crop_uv, (str, bytes))
                or len(self.crop_uv) != 4):
            raise DrawingElevationError("crop_uv must be (u_min, v_min, u_max, v_max)")
        crop = tuple(finite_number(v, "crop_uv") for v in self.crop_uv)
        if not (crop[0] < crop[2] and crop[1] < crop[3]):
            raise DrawingElevationError("crop_uv must have u_min < u_max and v_min < v_max")
        object.__setattr__(self, "crop_uv", crop)
        object.__setattr__(self, "near_depth", finite_number(self.near_depth, "near_depth"))
        object.__setattr__(self, "far_depth", finite_number(self.far_depth, "far_depth"))
        if not self.near_depth < self.far_depth:
            raise DrawingElevationError("near_depth must be less than far_depth")
        if not isinstance(self.hidden_lines, bool):
            raise DrawingElevationError("hidden_lines must be a bool")
        deflection = finite_number(self.linear_deflection, "linear_deflection")
        if deflection <= 0.0:
            raise DrawingElevationError("linear_deflection must be positive")
        object.__setattr__(self, "linear_deflection", deflection)
        if (isinstance(self.scale_denominator, bool) or not isinstance(self.scale_denominator, int)
                or self.scale_denominator <= 0):
            raise DrawingElevationError("scale_denominator must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": ELEVATION_KIND,
            "projection": "orthographic",
            "origin": list(self.origin),
            "look": list(self.look),
            "right": list(self.right),
            "up": list(self.up),
            "uv_definition": "u = dot(point - origin, right); v = dot(point - origin, up)",
            "depth_definition": "dot(point - origin, look)",
            "crop_uv": list(self.crop_uv),
            "near_depth": self.near_depth,
            "far_depth": self.far_depth,
            "hidden_lines": self.hidden_lines,
            "linear_deflection": self.linear_deflection,
            "scale": f"1:{self.scale_denominator}",
        }


@dataclass(frozen=True, slots=True)
class ElevationProjection:
    """The in-memory result: every solved polyline, and the SVG and PNG of the cropped, cleaned drawing.

    ``lines`` stay as solved, so the receipt's counts keep their meaning;
    ``cleanup`` reports what ``clean_drawing`` removed before the SVG.
    """

    lines: tuple[OcctDrawingPolyline, ...]
    svg: bytes
    png: bytes
    cleanup: CleanupReport | None = None

    def counts(self) -> dict[str, Any]:
        visible = [line for line in self.lines if line.kind == "visible"]
        hidden = [line for line in self.lines if line.kind == "hidden"]
        return {
            "visible_polylines": len(visible),
            "hidden_polylines": len(hidden),
            "objects_with_visible_lines": len({line.object_id for line in visible}),
            "objects_with_hidden_lines": len({line.object_id for line in hidden}),
            "objects_drawn_in_svg": len(svg_objects(self.svg)),
        }


def project_model_axis_elevation(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str], view: ElevationView, unit: str,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None, semantics: Mapping[str, Mapping[str, str]] | None = None,
) -> ElevationProjection:
    """Solve, crop and render one elevation of the named shapes; writes nothing.

    Every object in ``object_ids`` takes part in the one visibility solve
    (restricted to the view's near/far slab); the crop then clips the
    result to the sheet window and ``clean_drawing`` cleans it.  The SVG
    holds visible polylines, plus hidden ones when the view asks for them,
    each naming its object and, from ``semantics``, its component and material.
    """

    if not isinstance(view, ElevationView):
        raise TypeError("view must be ElevationView")
    if unit not in DRAWING_LENGTH_UNITS:
        raise DrawingElevationError(f"unit {unit!r} is not a CAD length unit")
    try:
        with observed_stage(operation_observer, "drawing.hlr", parent_event_id=parent_event_id,
                             details={"scope": "global_visibility", "input_object_ids": sorted(object_ids)}) as observation:
            lines = project_occt_lines(
                entries, object_ids=tuple(object_ids), origin=view.origin, right=view.right, up=view.up,
                linear_deflection=view.linear_deflection, depth_range=(view.near_depth, view.far_depth),
            )
            observation["emitted_object_ids"] = sorted({line.object_id for line in lines})
        with observed_stage(operation_observer, "drawing.svg", parent_event_id=parent_event_id,
                             details={"input_object_ids": sorted({line.object_id for line in lines})}) as observation:
            cleaned, cleanup = clean_view_lines(lines, (), crop_uv=view.crop_uv, hidden_lines=view.hidden_lines, unit=unit,
                                        scale_denominator=view.scale_denominator)
            svg = drawing_svg(
                cleaned, crop_uv=view.crop_uv, unit=unit, scale_denominator=view.scale_denominator,
                hidden_lines=view.hidden_lines, title=view.name, semantics=semantics,
            )
            observation["emitted_object_ids"] = list(svg_objects(svg))
        with observed_stage(operation_observer, "drawing.png", parent_event_id=parent_event_id):
            png = render_svg_png(svg)
    except (OcctBackendError, DrawingSvgError) as exc:
        raise DrawingElevationError(f"elevation {view.name}: {exc}") from exc
    return ElevationProjection(lines=lines, svg=svg, png=png, cleanup=cleanup)


# ---------------------------------------------------------------- section perspective

DEFAULT_SECTION_FOV_DEG = 55.0
DEFAULT_SECTION_EYE_HEIGHT_M = 1.6
DEFAULT_SECTION_GRAPHICS = {"cutLineMm": 0.5, "visibleLineMm": 0.25, "hatchSpacingMm": 0.5}
#: Metres per CAD length unit, for defaults stated in metres.
UNIT_METRES = {"meter": 1.0, "millimeter": 0.001, "inch": 0.0254, "foot": 0.3048}
_SECTION_MARGIN = 0.05


class SectionPerspectiveError(DrawingElevationError):
    """A section refused by name - a section perspective, or a vertical cut plan's plane: ``code`` says
    which rule failed; nothing was written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def refuse_section(code: str, message: str):
    """Refuse a section request with its stable, caller-visible reason code."""

    raise SectionPerspectiveError(code, message)


def _numbers(value, count: int, label: str) -> tuple[float, ...]:
    """``count`` finite numbers, refused by name when one is not finite."""

    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != count
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
        refuse_section("SECTION_REQUEST_INVALID", f"{label} must be {count} numbers")
    if any(not math.isfinite(v) for v in value):
        refuse_section("SECTION_VALUE_NOT_FINITE", f"{label} holds a value that is not finite")
    return tuple(float(v) for v in value)


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        refuse_section("SECTION_REQUEST_INVALID", f"{label} must be a number")
    if not math.isfinite(value):
        refuse_section("SECTION_VALUE_NOT_FINITE", f"{label} is not finite")
    return float(value)


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _normalized(vector) -> tuple[float, float, float]:
    length = math.hypot(*vector)
    return tuple(v / length for v in vector)


def _section_plane(section: Mapping[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """A checked canonical section's origin and unit normal, which points to the removed side."""

    if "line" in section:
        (x1, y1), (x2, y2) = section["line"]
        length = math.dist((x1, y1), (x2, y2))
        dx, dy = (x2 - x1) / length + 0.0, (y2 - y1) / length + 0.0
        # Keeping the left of walking the line removes its right: the normal points right.
        return (x1, y1, 0.0), ((dy, -dx + 0.0, 0.0) if section["keep"] == "left" else (-dy + 0.0, dx, 0.0))
    return tuple(section["origin"]), tuple(v + 0.0 for v in _normalized(section["normal"]))


def _checked_section(section, deflection: float) -> dict[str, Any]:
    """A section request, checked and canonical: ``{line, keep}`` or ``{origin, normal}``, refused by name."""

    if not isinstance(section, Mapping) or set(section) not in ({"line", "keep"}, {"origin", "normal"}):
        refuse_section("SECTION_REQUEST_INVALID", "the section is either {line, keep} or {origin, normal}")
    if "line" in section:
        line = section["line"]
        if not isinstance(line, Sequence) or isinstance(line, (str, bytes)) or len(line) != 2:
            refuse_section("SECTION_REQUEST_INVALID", "the section line must be two plan points [[x1, y1], [x2, y2]]")
        start, end = (_numbers(point, 2, "a section line point") for point in line)
        if section["keep"] not in ("left", "right"):
            refuse_section("SECTION_REQUEST_INVALID", "keep must be left or right of walking along the section line")
        if math.dist(start, end) <= deflection:
            refuse_section("SECTION_LINE_DEGENERATE", "the section line has no length; give two distinct plan points")
        return {"line": [list(start), list(end)], "keep": section["keep"]}
    origin = _numbers(section["origin"], 3, "the section origin")
    raw = _numbers(section["normal"], 3, "the section normal")
    if math.hypot(*raw) <= _TOLERANCE:
        refuse_section("SECTION_NORMAL_DEGENERATE", "the section normal has no length")
    return {"origin": list(origin), "normal": list(raw)}


def model_axis_section(
    section: Mapping[str, Any], *, linear_deflection: float = 0.0001,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """A vertical model-axis section plane as a cut-plan frame: its origin, look and the sheet's right.

    ``section`` is stated as a section perspective states it: ``{"line": [[x1, y1],
    [x2, y2]], "keep": "left" | "right"}``, the vertical plane through a plan line
    keeping one side of walking along it, or ``{"origin": [x, y, z], "normal":
    [nx, ny, nz]}`` with the normal pointing from the kept side to the removed
    side, where the viewer stands.  The plane must contain CAD +Z and be
    perpendicular to X or Y: ``look`` is then +X, -X, +Y or -Y into the kept
    side, up is +Z and ``right = look x up``, which ``freeze_cut_plan`` composes
    as it composes a horizontal cut.  The origin is where the plane crosses its
    model axis, its other coordinates zero, so u reads as the model coordinate
    along the sheet's right and v as Z.  Any other plane is refused by name
    (``SECTION_PLANE_NOT_MODEL_AXIS``), as are the section perspective's own
    malformed requests; nothing is read or written here.
    """

    deflection = _number(linear_deflection, "linear_deflection")
    origin, normal = _section_plane(_checked_section(section, deflection))
    look = tuple(-value + 0.0 for value in normal)
    axis = next((index for index in (0, 1) if abs(abs(look[index]) - 1.0) <= _TOLERANCE), None)
    if axis is None or any(abs(look[index]) > _TOLERANCE for index in range(3) if index != axis):
        refuse_section("SECTION_PLANE_NOT_MODEL_AXIS", "a vertical section's plane contains CAD +Z and is perpendicular to X or "
                                                "Y; draw any other plane as a section perspective")
    direction = [0.0, 0.0, 0.0]
    direction[axis] = math.copysign(1.0, look[axis])
    point = [0.0, 0.0, 0.0]
    point[axis] = float(origin[axis]) + 0.0
    right = _cross(direction, (0.0, 0.0, 1.0))
    return tuple(point), tuple(direction), tuple(value + 0.0 for value in right)


def axonometric_frame(
    toward_viewer: Sequence[float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """The right, up and look of a parallel view seen from ``toward_viewer``, with CAD +Z up on the sheet.

    ``toward_viewer`` points from the model to the viewer and need not be unit
    length: ``(1, -1, 1)`` is the isometric from +X, -Y, +Z.  ``look`` is its
    opposite, ``up`` is +Z made perpendicular to it and ``right = look x up``,
    the frame ``ElevationView`` checks.  A vertical or empty direction has no
    sheet up and is refused.  Such a view is foreshortened: its scale holds
    along no model axis.
    """

    direction = _vector(toward_viewer, "the axonometric direction")
    length = math.hypot(*direction)
    if length <= _TOLERANCE:
        raise DrawingElevationError("the axonometric direction has no length")
    toward = tuple(value / length for value in direction)
    lift = tuple(z - toward[2] * t for z, t in zip((0.0, 0.0, 1.0), toward))
    if math.hypot(*lift) <= 1e-6:
        raise DrawingElevationError("the axonometric direction is vertical; a parallel view needs CAD +Z across the sheet")
    up = _normalized(lift)
    look = tuple(-value + 0.0 for value in toward)
    right = _normalized(_cross(look, up))
    return (tuple(value + 0.0 for value in right), tuple(value + 0.0 for value in up), look)


def _section_paper_rules(hatch, beyond, spacing_mm: float) -> dict[str, Any]:
    """A section perspective's material hatch/poché and beyond fade, checked and stored as a cut plan stores them.

    ``hatch`` is ``{"byMaterial": {<material>: {spacingMm?, angleDeg?, poche?}}}``
    with spacing 0.5-20 paper mm (default ``spacing_mm``, the view's
    hatchSpacingMm), angle in [0, 180) degrees (default 45) and poché false;
    each rule is stored complete.  ``beyond`` is ``{"fade": 0-1}``.  No rule, an
    empty ``byMaterial`` or a zero fade is an absent key, so a request without
    them draws exactly what it drew before they existed.
    """

    rules: dict[str, Any] = {}
    if hatch is not None:
        by_material = hatch.get("byMaterial") if isinstance(hatch, Mapping) else None
        if not isinstance(hatch, Mapping) or set(hatch) != {"byMaterial"} or not isinstance(by_material, Mapping):
            refuse_section("SECTION_REQUEST_INVALID", "graphics.hatch takes byMaterial: {material: {spacingMm, angleDeg, poche}}")
        if len(by_material) > 100:
            refuse_section("SECTION_REQUEST_INVALID", "graphics.hatch.byMaterial takes at most 100 materials")
        complete = {}
        for material, rule in sorted(by_material.items(), key=lambda item: str(item[0])):
            if (not isinstance(material, str) or not 1 <= len(material) <= 100
                    or any(ord(char) < 32 or ord(char) == 127 for char in material)):
                refuse_section("SECTION_REQUEST_INVALID", "each hatch material is a printable name of 1 to 100 characters")
            if not isinstance(rule, Mapping) or not set(rule) <= {"spacingMm", "angleDeg", "poche"}:
                refuse_section("SECTION_REQUEST_INVALID", f"the {material} hatch rule takes spacingMm, angleDeg and poche")
            spacing = spacing_mm if rule.get("spacingMm") is None else _number(rule["spacingMm"], f"the {material} hatch spacingMm")
            if rule.get("spacingMm") is not None and not 0.5 <= spacing <= 20.0:
                refuse_section("SECTION_REQUEST_INVALID", f"the {material} hatch spacingMm must be 0.5 to 20 paper millimetres")
            angle = 45.0 if rule.get("angleDeg") is None else _number(rule["angleDeg"], f"the {material} hatch angleDeg")
            if not 0.0 <= angle < 180.0:
                refuse_section("SECTION_REQUEST_INVALID", f"the {material} hatch angleDeg must be from 0 up to 180 degrees")
            poche = rule.get("poche", False)
            if not isinstance(poche, bool):
                refuse_section("SECTION_REQUEST_INVALID", f"the {material} hatch poche must be true or false")
            complete[material] = {"spacingMm": float(spacing), "angleDeg": float(angle), "poche": poche}
        if complete:
            rules["hatch"] = {"byMaterial": complete}
    if beyond is not None:
        if not isinstance(beyond, Mapping) or set(beyond) != {"fade"}:
            refuse_section("SECTION_REQUEST_INVALID", "graphics.beyond takes fade, from 0 (black) to 1 (white)")
        fade = _number(beyond["fade"], "the beyond fade")
        if not 0.0 <= fade <= 1.0:
            refuse_section("SECTION_REQUEST_INVALID", "the beyond fade must be from 0 (black) to 1 (white)")
        if fade:
            rules["beyond"] = {"fade": fade}
    return rules


@dataclass(frozen=True, slots=True)
class SectionPerspectiveView:
    """One section perspective request, checked before the model is read; the CAD Z-up frame and STEP unit.

    ``section`` is ``{"line": [[x1, y1], [x2, y2]], "keep": "left" | "right"}``, a
    vertical plane through a plan line keeping the side on the left or right
    of walking from its first point to its second, or ``{"origin": [x, y, z],
    "normal": [nx, ny, nz]}`` whose normal points from the kept side to the
    removed side.  The eye stands on the removed side; nothing there is drawn.

    ``camera`` is explicit, ``{"eye", "target", "up"?, "fovDeg"?}``, or the
    default one-point perspective, ``None`` or ``{"eyeHeight"?, "fovDeg"?,
    "up"?}``: the eye on the removed side ``eyeHeight`` (1.6 m) above the
    lowest cut point, centred on the cut, at the distance that fits the cut's
    width in the horizontal field of view (55 degrees), with the cut's centre
    as target.  ``up`` (+Z) sets the picture's up within the plane.  The
    picture plane is always the section plane, so the cut is true to scale at
    1:``scale_denominator`` and lines along the normal converge at the eye's
    foot, wherever the target is.  The target's image centres the frame,
    whose width is the field of view at the plane and whose height keeps the
    cut's proportions; the default frame is the cut with a 5% margin, and
    moving only the eye moves the vanishing point, not the frame.  ``depth``
    bounds what is kept behind the plane.  ``graphics`` takes the pens and
    hatch spacing in paper mm and, as a cut plan does, the cut's material
    ``hatch`` and the ``beyond`` fade (``_section_paper_rules``).
    """

    name: str
    section: Mapping[str, Any]
    camera: Mapping[str, Any] | None = None
    depth: float | None = None
    hidden_object_ids: tuple[str, ...] = ()
    scale_denominator: int = 100
    graphics: Mapping[str, Any] | None = None
    linear_deflection: float = 0.0001

    def __post_init__(self) -> None:
        try:
            require_identifier(self.name, "view name")
        except ValueError as exc:
            raise SectionPerspectiveError("SECTION_REQUEST_INVALID", str(exc)) from exc
        deflection = _number(self.linear_deflection, "linear_deflection")
        if deflection <= 0.0:
            refuse_section("SECTION_REQUEST_INVALID", "linear_deflection must be positive")
        object.__setattr__(self, "linear_deflection", deflection)
        canonical = _checked_section(self.section, deflection)
        object.__setattr__(self, "section", canonical)
        origin, normal = _section_plane(canonical)
        camera = {} if self.camera is None else self.camera
        if not isinstance(camera, Mapping) or not set(camera) <= {"eye", "target", "up", "fovDeg", "eyeHeight"}:
            refuse_section("SECTION_REQUEST_INVALID", "the camera takes eye, target, up, fovDeg or eyeHeight")
        explicit = "eye" in camera or "target" in camera
        if explicit and not {"eye", "target"} <= set(camera):
            refuse_section("SECTION_REQUEST_INVALID", "an explicit camera needs both eye and target")
        if explicit and "eyeHeight" in camera:
            refuse_section("SECTION_REQUEST_INVALID", "eyeHeight places the default eye; an explicit camera states its eye")
        resolved: dict[str, Any] = {}
        for key in ("eye", "target", "up"):
            if key in camera:
                resolved[key] = list(_numbers(camera[key], 3, f"the camera {key}"))
        for key in ("fovDeg", "eyeHeight"):
            if key in camera:
                resolved[key] = _number(camera[key], f"the camera {key}")
        fov = resolved.get("fovDeg", DEFAULT_SECTION_FOV_DEG)
        if not 0.0 < fov < 180.0:
            refuse_section("SECTION_CAMERA_DEGENERATE", "fovDeg must be between 0 and 180 degrees")
        up = tuple(resolved.get("up", (0.0, 0.0, 1.0)))
        in_plane = tuple(u - _dot(up, normal) * n for u, n in zip(up, normal))
        if math.hypot(*up) <= _TOLERANCE or math.hypot(*in_plane) <= 1e-6 * math.hypot(*up):
            refuse_section("SECTION_CAMERA_DEGENERATE", "the camera up must not be parallel to the section normal; "
                                                 "give an up that lies across the section plane")
        if explicit:
            eye, target = resolved["eye"], resolved["target"]
            side = _dot([e - o for e, o in zip(eye, origin)], normal)
            if side < -deflection:
                refuse_section("SECTION_EYE_ON_KEPT_SIDE", "the eye stands on the kept side of the section; "
                                                    "stand it on the removed side, where the normal points")
            if side <= deflection:
                refuse_section("SECTION_EYE_ON_PLANE", "the eye stands on the section plane; move it onto the removed side")
            if math.dist(eye, target) <= deflection:
                refuse_section("SECTION_CAMERA_DEGENERATE", "the eye and the target coincide")
            if _dot([t - e for t, e in zip(target, eye)], normal) >= 0.0:
                refuse_section("SECTION_CAMERA_DEGENERATE", "the camera must look through the cut toward the kept side")
        object.__setattr__(self, "camera", resolved or None)
        if self.depth is not None:
            depth = _number(self.depth, "depth")
            if depth <= 0.0:
                refuse_section("SECTION_DEPTH_INVALID", "depth must be a positive distance behind the section plane")
            object.__setattr__(self, "depth", depth)
        hidden = self.hidden_object_ids
        if (not isinstance(hidden, (list, tuple)) or any(not isinstance(name, str) or not name for name in hidden)
                or len(set(hidden)) != len(hidden)):
            refuse_section("SECTION_REQUEST_INVALID", "hiddenObjectIds must be distinct physical object ids")
        object.__setattr__(self, "hidden_object_ids", tuple(sorted(hidden)))
        if (isinstance(self.scale_denominator, bool) or not isinstance(self.scale_denominator, int)
                or self.scale_denominator <= 0):
            refuse_section("SECTION_REQUEST_INVALID", "scale_denominator must be a positive integer")
        graphics = dict(DEFAULT_SECTION_GRAPHICS)
        requested = {} if self.graphics is None else self.graphics
        if not isinstance(requested, Mapping) or not set(requested) <= {*graphics, "hatch", "beyond"}:
            refuse_section("SECTION_REQUEST_INVALID", "graphics takes cutLineMm, visibleLineMm, hatchSpacingMm, hatch and beyond")
        for key in DEFAULT_SECTION_GRAPHICS:
            if key in requested:
                graphics[key] = _number(requested[key], key)
                if graphics[key] <= 0.0:
                    refuse_section("SECTION_REQUEST_INVALID", f"{key} must be a positive paper millimetre value")
        graphics.update(_section_paper_rules(requested.get("hatch"), requested.get("beyond"), graphics["hatchSpacingMm"]))
        object.__setattr__(self, "graphics", graphics)

    def plane(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """The section plane's origin and unit normal (toward the removed side)."""

        return _section_plane(self.section)

    def frame(self) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
        """The drawing frame on the plane: origin, right, up and the normal ``right x up``."""

        origin, normal = self.plane()
        up = tuple((self.camera or {}).get("up", (0.0, 0.0, 1.0)))
        up = _normalized(tuple(u - _dot(up, normal) * n for u, n in zip(up, normal)))
        # Adding 0.0 turns a cross product's negative zeros into zeros for the receipt.
        return (origin, tuple(v + 0.0 for v in _normalized(_cross(up, normal))),
                tuple(v + 0.0 for v in up), tuple(v + 0.0 for v in normal))

    def request(self) -> dict[str, Any]:
        """The canonical request: with the exact source it determines the drawing."""

        return {
            "name": self.name, "section": deepcopy(self.section), "camera": deepcopy(self.camera),
            "depth": self.depth, "hiddenObjectIds": list(self.hidden_object_ids),
            "scale": f"1:{self.scale_denominator}", "graphics": deepcopy(self.graphics),
            "linear_deflection": self.linear_deflection,
        }


@dataclass(frozen=True, slots=True)
class SectionPerspectiveProjection:
    """The in-memory section perspective: the resolved view, the exact solve, and its SVG and PNG.

    ``perspective`` stays as solved; ``cleanup`` reports what ``clean_drawing``
    removed before the SVG.
    """

    view: Mapping[str, Any]
    perspective: OcctSectionPerspective
    svg: bytes
    png: bytes
    cleanup: CleanupReport | None = None

    @property
    def lines(self) -> tuple[OcctDrawingPolyline, ...]:
        return self.perspective.lines

    def counts(self) -> dict[str, Any]:
        visible = [line for line in self.lines if line.kind == "visible"]
        return {
            "visible_polylines": len(visible),
            "hidden_polylines": 0,
            "objects_with_visible_lines": len({line.object_id for line in visible}),
            "objects_with_hidden_lines": 0,
            "objects_drawn_in_svg": len(svg_objects(self.svg)),
        }

    def details(self, selected: Sequence[str]) -> dict[str, Any]:
        """What the receipt adds about the camera and the cut."""

        perspective = self.perspective
        return {
            "algorithm": ("BRepAlgoAPI_Common with an oriented box on the kept side, then one exact HLRBRep_Algo "
                          "perspective solve of the kept parts; BRepAlgoAPI_Section of the original solids for the cut"),
            "projector": ("HLRAlgo_Projector(gp_Ax2(foot, normal, right), focus) with the shapes located in the eye's "
                          "camera frame: the eye stands at foot + focus * normal, the picture plane is the section "
                          "plane (true to scale) and a point z behind it maps 1 / (1 + z / focus) toward the foot"),
            "principal_point_uv": list(perspective.principal_point),
            "focus": perspective.focus,
            "selected_object_ids": list(selected),
            "drawn_object_ids": list(perspective.drawn_object_ids),
            "cut_object_ids": list(perspective.cut_object_ids),
            "removed_object_ids": list(perspective.removed_object_ids),
            "section_polylines": sum(line.kind == "section" for line in perspective.lines),
            "section_regions": len(perspective.regions),
        }


def _perspective_image(point, frame, eye, focus) -> tuple[float, float]:
    """Where ``point`` lands in the picture, in drawing coordinates (the projector's own formula)."""

    origin, right, up, normal = frame
    foot = tuple(e - focus * n for e, n in zip(eye, normal))
    delta = [p - f for p, f in zip(point, foot)]
    shrink = 1.0 - _dot(delta, normal) / focus
    return (_dot([f - o for f, o in zip(foot, origin)], right) + _dot(delta, right) / shrink,
            _dot([f - o for f, o in zip(foot, origin)], up) + _dot(delta, up) / shrink)


def project_section_perspective(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str], view: SectionPerspectiveView, unit: str,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None, semantics: Mapping[str, Mapping[str, str]] | None = None,
) -> SectionPerspectiveProjection:
    """Cut, solve and render one section perspective of the named shapes; writes nothing.

    The plane's section of the selected objects places the default camera
    and must exist: a plane that misses them is refused by name.  The
    resolved view (plane, frame, camera and crop) is returned with the
    drawing so the receipt records exactly what was drawn.  ``semantics``
    names each object's component and material in the SVG.
    """

    if not isinstance(view, SectionPerspectiveView):
        raise TypeError("view must be SectionPerspectiveView")
    if unit not in DRAWING_LENGTH_UNITS:
        raise DrawingElevationError(f"unit {unit!r} is not a CAD length unit")
    frame = view.frame()
    origin, right, up, normal = frame
    try:
        with observed_stage(operation_observer, "drawing.hlr", parent_event_id=parent_event_id,
                             details={"scope": "global_visibility", "input_object_ids": sorted(object_ids)}) as observation:
            cut = section_occt_lines(entries, object_ids=tuple(object_ids), origin=origin, right=right, up=up,
                                     linear_deflection=view.linear_deflection)
            points = [point for line in cut for point in line.points]
            if not points:
                refuse_section("SECTION_PLANE_MISSES_MODEL", "the section plane meets none of the drawn objects; "
                                                      "move it through the model")
            u0, u1 = min(p[0] for p in points), max(p[0] for p in points)
            v0, v1 = min(p[1] for p in points), max(p[1] for p in points)
            margin = _SECTION_MARGIN * max(u1 - u0, v1 - v0)
            # The frame keeps the cut's proportions, margin included, whatever its width.
            aspect = (v1 - v0 + 2.0 * margin) / (u1 - u0 + 2.0 * margin)
            camera = dict(view.camera or {})
            fov = camera.get("fovDeg", DEFAULT_SECTION_FOV_DEG)
            half = math.tan(math.radians(fov) / 2.0)
            if "eye" in camera:
                placement, eye, target = "explicit", tuple(camera["eye"]), tuple(camera["target"])
            else:
                # One-point perspective: the eye's foot is centred on the cut at eye height, and the target is
                # the cut's centre, so the frame is the cut with its margin.
                placement = "default"
                height = camera.get("eyeHeight", DEFAULT_SECTION_EYE_HEIGHT_M / UNIT_METRES[unit])
                distance = ((u1 - u0) / 2.0 + margin) / half
                eye = tuple(o + (u0 + u1) / 2.0 * r + (v0 + height) * w + distance * n
                            for o, r, w, n in zip(origin, right, up, normal))
                target = tuple(o + (u0 + u1) / 2.0 * r + (v0 + v1) / 2.0 * w for o, r, w in zip(origin, right, up))
            perspective = project_occt_section_perspective(
                entries, object_ids=tuple(object_ids), origin=origin, right=right, up=up, eye=eye,
                linear_deflection=view.linear_deflection, depth=view.depth,
            )
            observation["emitted_object_ids"] = sorted({line.object_id for line in perspective.lines})
        width = 2.0 * perspective.focus * half
        centre = _perspective_image(target, frame, eye, perspective.focus)
        crop = (centre[0] - width / 2.0, centre[1] - width * aspect / 2.0,
                centre[0] + width / 2.0, centre[1] + width * aspect / 2.0)
        resolved = {
            "kind": SECTION_PERSPECTIVE_KIND,
            "name": view.name,
            "projection": "perspective",
            "request": view.request(),
            "section": {"origin": list(view.plane()[0]), "normal": list(normal),
                        "kept_side": "dot(point - origin, normal) <= 0", "depth": view.depth},
            "camera": {"placement": placement, "eye": list(eye), "target": list(target),
                       "up": list(camera.get("up", (0.0, 0.0, 1.0))), "fov_deg": fov},
            "origin": list(origin),
            "right": list(right),
            "up": list(up),
            "uv_definition": ("on the section plane u = dot(point - origin, right), v = dot(point - origin, up); "
                              "behind it, the point's perspective image from the eye in the same frame"),
            "picture_plane": "the section plane",
            "crop_uv": list(crop),
            "scale": f"1:{view.scale_denominator}",
            "scale_at": "the section plane",
            "hidden_lines": False,
            "linear_deflection": view.linear_deflection,
            "graphics": deepcopy(view.graphics),
            "hiddenObjectIds": list(view.hidden_object_ids),
        }
        with observed_stage(operation_observer, "drawing.svg", parent_event_id=parent_event_id,
                             details={"input_object_ids": sorted({line.object_id for line in perspective.lines})}) as observation:
            # The scale holds at the section plane, so the paper tolerance is measured there.
            cleaned, cleanup = clean_view_lines(perspective.lines, perspective.regions, crop_uv=crop, hidden_lines=False,
                                        unit=unit, scale_denominator=view.scale_denominator)
            svg = drawing_svg(
                cleaned, crop_uv=crop, unit=unit, scale_denominator=view.scale_denominator,
                hidden_lines=False, title=view.name, regions=perspective.regions, graphics=view.graphics,
                projection=SECTION_PERSPECTIVE_KIND, semantics=semantics,
            )
            observation["emitted_object_ids"] = list(svg_objects(svg))
        with observed_stage(operation_observer, "drawing.png", parent_event_id=parent_event_id):
            png = render_svg_png(svg)
    except SectionPerspectiveError:
        raise
    except (OcctBackendError, DrawingSvgError) as exc:
        raise DrawingElevationError(f"section perspective {view.name}: {exc}") from exc
    return SectionPerspectiveProjection(view=resolved, perspective=perspective, svg=svg, png=png, cleanup=cleanup)


def section_perspective_objects(verified: VerifiedElevationSource, hidden_object_ids: Sequence[str]) -> tuple[str, ...]:
    """The objects a section perspective cuts and draws: every physical object not hidden.

    Hidden inspection witnesses the source retains (aperture volumes) are
    evidence, not material, as in a cut plan.  An unknown hidden id or
    hiding everything is refused by name.
    """

    hidden_object_ids = [current_object_id(name, set(verified.physical_object_ids)) for name in hidden_object_ids]
    unknown = sorted(set(hidden_object_ids) - set(verified.physical_object_ids))
    if unknown:
        refuse_section("DRAWING_OBJECT_UNKNOWN", "These physical objects are not in the selected model: " + ", ".join(unknown))
    excluded = set(hidden_object_ids) | inspection_witness_ids(verified.receipt)
    selected = tuple(name for name in verified.physical_object_ids if name not in excluded)
    if not selected:
        refuse_section("DRAWING_EMPTY", "Keep at least one physical object in the section perspective.")
    return selected


__all__ = [
    "CLEANUP_TOLERANCE_MM",
    "DEFAULT_SECTION_EYE_HEIGHT_M",
    "DEFAULT_SECTION_FOV_DEG",
    "ELEVATION_KIND",
    "SECTION_PERSPECTIVE_KIND",
    "UNIT_METRES",
    "ElevationProjection",
    "ElevationView",
    "SectionPerspectiveError",
    "SectionPerspectiveProjection",
    "SectionPerspectiveView",
    "axonometric_frame",
    "clean_view_lines",
    "finite_number",
    "model_axis_section",
    "observed_stage",
    "project_model_axis_elevation",
    "project_section_perspective",
    "refuse_section",
    "section_perspective_objects",
]
