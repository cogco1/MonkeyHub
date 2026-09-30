"""Writing and cold-reading STEP: named, layered, coloured B-rep shapes in the program's unit.

Units.  OCCT's STEP statics assume millimetre internals; they are
initialised only when the first STEP controller exists.  ``_step_units``
therefore initialises the controller and then sets both ``write.step.unit``
and ``xstep.cascade.unit`` to the program's unit, so values are written
unscaled and read back unscaled.  Both statics are process-global and are
consulted at ``Transfer`` time, not when the unit is set: a second export
or readback in another unit that runs between the two leaves the first one
written in the wrong unit (a metre file marked INCH reads back scaled by
0.0254).  ``write_step`` and ``read_step`` therefore hold one shared lock,
``_STEP_LOCK``, from setting the unit through the whole STEP transfer and
write (or read transfer).  Nothing else is serialised: shape building,
measuring and tessellation do not touch the statics.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from monkeycad.backends.occt.errors import OcctBackendError, OcctBuildError
from monkeycad.backends.occt.kernel import _explore, _occt


_UNIT_TO_STEP: Mapping[str, str] = {
    "millimeter": "MM",
    "meter": "M",
    "inch": "INCH",
    "foot": "FT",
}

#: Serialises the STEP unit statics with the transfer that consumes them
#: (see the module docstring).  A redundant controller ``Init_s`` does not
#: reset the statics, so ``_occt`` itself needs no share of this lock.
_STEP_LOCK = threading.Lock()


@dataclass(frozen=True)
class StepObject:
    """One named solid or surface to write: identity, layer, display colour and visibility.

    An object that is not ``visible`` (a cutter kept for inspection, an
    aperture witness) is written with the STEP's own invisibility, so a
    reader that opens only the STEP does not show it.
    """

    object_id: str
    shape: Any
    layer: str
    color: tuple[int, int, int] | None = None
    visible: bool = True


@dataclass(frozen=True)
class StepEntry:
    """One named source shape; native mesh approximations are explicitly marked.

    ``visible`` is false only where the file marks the shape invisible.
    """

    name: str | None
    layers: tuple[str, ...]
    color: tuple[int, int, int] | None
    shape: Any
    geometry_quality: str = "exact"
    visible: bool = True


def _step_units(occ: SimpleNamespace, length_unit: str) -> None:
    unit = _UNIT_TO_STEP.get(length_unit)
    if unit is None:
        raise OcctBackendError(f"length unit {length_unit!r} has no STEP unit")
    statics = occ.Interface.Interface_Static
    if not statics.SetCVal_s("write.step.unit", unit) or not statics.SetCVal_s(
        "xstep.cascade.unit", unit
    ):
        raise OcctBuildError("OCCT refused the STEP unit statics")


def write_step(path: Path, objects: Sequence[StepObject], *, length_unit: str) -> None:
    """Write the objects as named, layered, coloured B-rep shapes in the program's unit.

    The name is the object id; the layer is the semantic layer path; the
    colour is the layer colour.  An object that is not visible is written
    with an INVISIBILITY of its styled items.  ``archflow:*`` user text has
    no STEP home and travels in the preview and the receipt instead.
    """

    occ = _occt()
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    if not objects:
        raise OcctBuildError("nothing to write")
    # The unit statics are read by Transfer; nothing may change them between
    # here and the end of the write.
    with _STEP_LOCK:
        _step_units(occ, length_unit)
        document = occ.TDocStd.TDocStd_Document(occ.TCollection.TCollection_ExtendedString("XmlXCAF"))
        shape_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        color_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ColorTool_s(document.Main())
        layer_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.LayerTool_s(document.Main())
        for item in objects:
            label = shape_tool.AddShape(item.shape, False, False)
            occ.TDataStd.TDataStd_Name.Set_s(
                label, occ.TCollection.TCollection_ExtendedString(item.object_id)
            )
            layer_tool.SetLayer(label, occ.TCollection.TCollection_ExtendedString(item.layer))
            if item.color is not None:
                red, green, blue = (channel / 255.0 for channel in item.color)
                color_tool.SetColor(
                    label,
                    occ.Quantity.Quantity_Color(red, green, blue, occ.Quantity.Quantity_TOC_RGB),
                    occ.XCAFDoc.XCAFDoc_ColorSurf,
                )
            if not item.visible:
                color_tool.SetVisibility(label, False)
        writer = occ.STEPCAFControl.STEPCAFControl_Writer()
        writer.SetNameMode(True)
        writer.SetLayerMode(True)
        writer.SetColorMode(True)
        if not writer.Transfer(document, occ.STEPControl.STEPControl_AsIs):
            raise OcctBuildError("STEP transfer failed")
        if writer.Write(str(path)) != occ.IFSelect.IFSelect_RetDone:
            raise OcctBuildError("STEP write failed")


def read_step(path: Path, *, length_unit: str) -> tuple[StepEntry, ...]:
    """Cold-read a STEP file with a fresh reader: names, layers, colours, visibility, shapes.

    Independent of any in-memory shape: the reader sees only the bytes on
    disk, interpreted in the program's unit.
    """

    occ = _occt()
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    # The file's unit is scaled to xstep.cascade.unit by Transfer, not by
    # ReadFile; the static must hold the program's unit until then.
    with _STEP_LOCK:
        _step_units(occ, length_unit)
        reader = occ.STEPCAFControl.STEPCAFControl_Reader()
        reader.SetNameMode(True)
        reader.SetLayerMode(True)
        reader.SetColorMode(True)
        if reader.ReadFile(str(path)) != occ.IFSelect.IFSelect_RetDone:
            raise OcctBuildError("STEP file could not be read")
        document = occ.TDocStd.TDocStd_Document(occ.TCollection.TCollection_ExtendedString("XmlXCAF"))
        if not reader.Transfer(document):
            raise OcctBuildError("STEP transfer into the document failed")
    shape_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    layer_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.LayerTool_s(document.Main())
    color_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    labels = occ.TDF.TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    entries: list[StepEntry] = []
    for index in range(1, labels.Length() + 1):
        label = labels.Value(index)
        shape = shape_tool.GetShape_s(label)
        layers = _layers_of(occ, layer_tool, label)
        rgb = _color_of(occ, color_tool, shape)
        visible = bool(occ.XCAFDoc.XCAFDoc_ColorTool.IsVisible_s(label))
        if shape.ShapeType() in (occ.TopAbs.TopAbs_COMPOUND, occ.TopAbs.TopAbs_SHELL):
            # A compound (an array's copies) is written as one named shape,
            # but the reader files its layer, colour and invisibility on each
            # solid, not on the compound's label.  The entry reports them only
            # when every solid agrees; a mixed compound stays unlayered (the
            # readback verification refuses it) and visible.
            # A bounded planar face is cold-read as a shell with attributes
            # on its face. Read that actual metadata, retaining the same
            # all-members-agree requirement as the solid array path.
            members = tuple(_explore(occ, shape, occ.TopAbs.TopAbs_SOLID)) or tuple(_explore(occ, shape, occ.TopAbs.TopAbs_FACE))
            if not layers and members:
                per_member = {_layers_of(occ, layer_tool, member) for member in members}
                if len(per_member) == 1:
                    layers = per_member.pop()
            if rgb is None and members:
                per_member_color = {_color_of(occ, color_tool, member) for member in members}
                if len(per_member_color) == 1:
                    rgb = per_member_color.pop()
            if visible and members:
                per_member_visible = {_visible_of(occ, shape_tool, member) for member in members}
                if len(per_member_visible) == 1:
                    visible = per_member_visible.pop()
        entries.append(
            StepEntry(name=_label_name(occ, label), layers=layers, color=rgb, shape=shape, visible=visible)
        )
    return tuple(entries)


def _layers_of(occ: SimpleNamespace, layer_tool, target) -> tuple[str, ...]:
    """The layer names filed on a label or on a shape, in the reader's order."""

    layer_labels = occ.TDF.TDF_LabelSequence()
    layer_tool.GetLayers(target, layer_labels)
    return tuple(
        _label_name(occ, layer_labels.Value(position)) or ""
        for position in range(1, layer_labels.Length() + 1)
    )


def _color_of(occ: SimpleNamespace, color_tool, shape) -> tuple[int, int, int] | None:
    color = occ.Quantity.Quantity_Color()
    if not color_tool.GetColor(shape, occ.XCAFDoc.XCAFDoc_ColorSurf, color):
        return None
    return tuple(
        int(round(channel * 255.0))
        for channel in (color.Red(), color.Green(), color.Blue())
    )


def _visible_of(occ: SimpleNamespace, shape_tool, shape) -> bool:
    """Whether the label a shape is filed under is visible, found the way the colour tool finds a shape's colour."""

    label = occ.TDF.TDF_Label()
    if not shape_tool.Search(shape, label):
        return True
    return bool(occ.XCAFDoc.XCAFDoc_ColorTool.IsVisible_s(label))


def _label_name(occ: SimpleNamespace, label) -> str | None:
    attribute = occ.TDataStd.TDataStd_Name()
    if not label.FindAttribute(occ.TDataStd.TDataStd_Name.GetID_s(), attribute):
        return None
    return str(attribute.Get().ToExtString())


__all__ = [
    "StepEntry",
    "StepObject",
    "read_step",
    "write_step",
]
