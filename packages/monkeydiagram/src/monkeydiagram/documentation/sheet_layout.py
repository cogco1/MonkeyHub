"""What a view sheet says about each drawing it places, and where a section is marked on a plan.

A view sheet places drawings its caller already retained, each at its own paper
size where the caller put it (``styles.compose_view_sheet``). Before the scene is
composed, each placed drawing gets the title, subtitle and scale label its own
frame states (``view_labels``); a vertical section's plane is read as a line in
plan (``section_line``) and marked where it crosses a placed plan, in that plan's
own paper mm (``section_mark``); and ``view_sheet_scene`` composes the paper scene
from the sheet's recipe and the views' retained marks. Nothing here reads a model
or a project, or writes: the Project Runtime draws or reads back each view,
retains the sheet and keys its projections by this module's source (#519). A
section that does not cross the plan it is marked on is a ``SheetLayoutError``
naming its refusal.

The paper scene's own module is imported where it is used: composing a sheet
loads the paper renderers, and laying one out does not.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from monkeydiagram.projection.views import UNIT_METRES

_UNIT_SYMBOLS = {"meter": "m", "millimeter": "mm", "inch": "in", "foot": "ft"}
_UNIT_DECIMALS = {"meter": 3, "millimeter": 0, "inch": 2, "foot": 3}


class SheetLayoutError(ValueError):
    """A view sheet that cannot be laid out as asked: ``code`` names the refusal, the message says which view."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _signed(value: float, unit: str) -> str:
    return f"{value:+.{_UNIT_DECIMALS[unit]}f} {_UNIT_SYMBOLS[unit]}"


def _axis(vector) -> str | None:
    """+X, -Y, ... for a model axis direction; None for any other direction."""

    for index, name in enumerate("XYZ"):
        if abs(abs(vector[index]) - 1.0) <= 1e-9:
            return ("+" if vector[index] > 0 else "-") + name
    return None


def view_labels(view: Mapping[str, Any], kind: str, recipe: Mapping[str, Any], unit: str) -> tuple[str, str, str]:
    """A placed view's title, subtitle and scale label: the caller's words, else what its own frame states."""

    label = view.get("mark_label")
    cut = f" {label}-{label}" if label else ""
    if kind == "plan":
        frame = recipe["frame"]
        title, subtitle, scale = "PLAN", f"Horizontal cut at Z {_signed(frame['origin'][2], unit)}, looking down", frame["scale"]
    elif kind == "section":
        frame = recipe["frame"]
        look = _axis(frame["look"])
        position = frame["origin"]["XYZ".index(look[1])]
        title, subtitle, scale = "SECTION" + cut, f"Vertical cut at {look[1]} {_signed(position, unit)}, looking {look}", frame["scale"]
    elif kind == "axon":
        toward = [-value for value in recipe["look"]]
        iso = max(abs(value) for value in toward) - min(abs(value) for value in toward) <= 1e-9
        sides = " / ".join(("+" if value > 0 else "-") + name for name, value in zip("XYZ", toward) if abs(value) > 1e-9)
        title = "ISOMETRIC" if iso else "AXONOMETRIC"
        subtitle, scale = f"Parallel view from {sides}, whole model, not to scale", f"display {recipe['scale']}"
    elif kind == "elevation":
        name = view["arguments"].get("view", "front")
        look = _axis(recipe["look"])
        if name == "top":
            title, subtitle = "TOP VIEW", "Orthographic, looking down; not a cut plan"
        else:
            title, subtitle = f"{name.upper()} ELEVATION", f"Orthographic, looking {look}"
        scale = recipe["scale"]
    else:
        scale = recipe["scale"]
        title, subtitle = "SECTION PERSPECTIVE" + cut, f"Cut plane at {scale}; depth in perspective, not to scale"
        scale = f"{scale} at the cut"
    return (view.get("title") or title, subtitle if view.get("subtitle") is None else view["subtitle"], scale)


def section_line(kind: str, recipe: Mapping[str, Any]):
    """A section view's plane in plan: a point on it and the horizontal direction toward its kept side."""

    if kind == "section":
        return recipe["frame"]["origin"], recipe["frame"]["look"]
    if kind == "section-perspective":
        normal = recipe["section"]["normal"]
        if abs(normal[2]) > 1e-9:
            return None
        return recipe["section"]["origin"], [-value for value in normal]
    return None


def section_mark(view_id: str, label: str, section, plan_id: str, plan: Mapping[str, Any], unit: str):
    """Where a vertical section plane crosses a placed horizontal plan, in that plan's own paper mm."""

    from .styles import SheetSectionMark

    origin, look = section
    length = math.hypot(look[0], look[1])
    lx, ly = look[0] / length, look[1] / length
    frame = plan["frame"]
    u0, v0, u1, v1 = frame["crop_uv"]
    mm_per_unit = UNIT_METRES[unit] * 1000 / int(frame["scale"].split(":")[1])
    along = (-ly, lx)
    low, high = -math.inf, math.inf
    plane = (origin[0] - frame["origin"][0], origin[1] - frame["origin"][1])
    for point, direction, (bottom, top) in zip(plane, along, ((u0, u1), (v0, v1))):
        if abs(direction) <= 1e-12:
            if not bottom <= point <= top:
                low, high = 1.0, 0.0
            continue
        first, second = (bottom - point) / direction, (top - point) / direction
        low, high = max(low, min(first, second)), min(high, max(first, second))
    if not low < high:
        raise SheetLayoutError("DRAWING_SECTION_MARK_OUTSIDE",
                               f"Section {label} ({view_id}) does not cross the plan's window; mark it on a plan it cuts.")
    # The plan's u and v are X and Y measured from its frame origin.
    ends = [(plane[0] + along[0] * t, plane[1] + along[1] * t) for t in (low, high)]
    paper = [((u - u0) * mm_per_unit, (v1 - v) * mm_per_unit) for u, v in ends]
    return SheetSectionMark(view_id=plan_id, start_mm=paper[0], end_mm=paper[1], look_mm=(lx, -ly), label=label)


def view_sheet_scene(style_id, recipe, placed, marks, fonts):
    """The view sheet's paper scene from its recipe and the retained views' marks; writes nothing."""

    from .styles import SheetView, compose_view_sheet

    views = tuple(SheetView(view_id=row["id"], size_mm=tuple(row["sizeMm"]), marks=placed[row["id"]],
                            place_mm=tuple(row["placeMm"]), title=row["title"], subtitle=row["subtitle"],
                            scale_label=row["scaleLabel"]) for row in recipe["views"])
    return compose_view_sheet(style_id=style_id, paper_size_mm=tuple(recipe["paperSizeMm"]), views=views,
                              title=recipe["title"], sheet_number=recipe["sheetNumber"], subtitle=recipe["subtitle"],
                              notes=tuple(recipe["notes"]), source_text=recipe["sourceText"], section_marks=marks,
                              font_mapping=fonts)
