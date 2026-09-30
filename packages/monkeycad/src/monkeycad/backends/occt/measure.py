"""What the kernel reports about shapes: validity, closure, volume, bounds, point classification and solid-pair distance."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from monkeycad.backends.occt.errors import OcctBackendError, OcctBuildError
from monkeycad.backends.occt.kernel import _count, _explore, _free_edge_count, _occt, cad_point
from monkeycad.backends.occt.step import StepEntry


_CLASSIFIER_TOLERANCE = 1e-7


@dataclass(frozen=True, slots=True)
class ShapeMeasure:
    """What the kernel reports about one shape, in the CAD frame.

    ``volume`` is ``None`` for a shape holding no solid: an open surface
    encloses nothing, and ``VolumeProperties`` on it would report the
    signed volume its faces happen to sweep against the origin - a number,
    not a measurement.  ``free_edge_count`` is the open boundary: edges
    bounding exactly one face, zero for every closed solid.
    """

    valid: bool
    solid_count: int
    closed: bool
    face_count: int
    volume: float | None
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    free_edge_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "solid_count": self.solid_count,
            "closed": self.closed,
            "face_count": self.face_count,
            "free_edge_count": self.free_edge_count,
            "volume": self.volume,
            "bbox": {"min": list(self.bbox_min), "max": list(self.bbox_max)},
        }


def measure_shape(shape) -> ShapeMeasure:
    """Validity, solid/shell closure, open boundary, volume (solids only) and tight bounds of one shape."""

    occ = _occt()
    if shape is None or shape.IsNull():
        raise OcctBuildError("cannot measure a null shape")
    valid = bool(occ.BRepCheck.BRepCheck_Analyzer(shape).IsValid())
    solids = tuple(_explore(occ, shape, occ.TopAbs.TopAbs_SOLID))
    shells_in_solids = 0
    closed = bool(solids)
    for solid in solids:
        for shell in _explore(occ, solid, occ.TopAbs.TopAbs_SHELL):
            shells_in_solids += 1
            if not occ.BRep.BRep_Tool.IsClosed_s(shell):
                closed = False
    if _count(occ, shape, occ.TopAbs.TopAbs_SHELL) != shells_in_solids:
        closed = False  # a free shell or face outside every solid
    volume = None
    if solids:
        properties = occ.GProp.GProp_GProps()
        occ.BRepGProp.BRepGProp.VolumeProperties_s(shape, properties)
        volume = float(properties.Mass())
    box = occ.Bnd.Bnd_Box()
    occ.BRepBndLib.BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid():
        raise OcctBuildError("shape has no bounds")
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return ShapeMeasure(
        valid=valid,
        solid_count=len(solids),
        closed=closed,
        face_count=_count(occ, shape, occ.TopAbs.TopAbs_FACE),
        volume=volume,
        bbox_min=(float(xmin), float(ymin), float(zmin)),
        bbox_max=(float(xmax), float(ymax), float(zmax)),
        free_edge_count=_free_edge_count(occ, shape),
    )


def classify_point(shape, cad_xyz: Sequence[float]) -> str:
    """``inside`` | ``outside`` | ``boundary`` for one point in the CAD frame."""

    occ = _occt()
    x, y, z = (float(value) for value in cad_xyz)
    classifier = occ.BRepClass3d.BRepClass3d_SolidClassifier(
        shape, occ.gp.gp_Pnt(x, y, z), _CLASSIFIER_TOLERANCE
    )
    state = classifier.State()
    if state == occ.TopAbs.TopAbs_IN:
        return "inside"
    if state == occ.TopAbs.TopAbs_OUT:
        return "outside"
    return "boundary"


def classify_program_point(shape, program_xyz: Sequence[float]) -> str:
    """``classify_point`` for a point stated in program coordinates."""

    return classify_point(shape, cad_point(program_xyz))


def measure_occt_solid_pairs(
    entries: Sequence[StepEntry], *, object_pairs: Sequence[tuple[str, str]], length_unit: str,
) -> dict[tuple[str, str], dict[str, object]]:
    """Measure only requested pairs of final named solids, normally cold-read from STEP.

    Distance and common solid volume come from OCCT, in metres and cubic
    metres. A face/edge contact has zero common volume; a positive common
    volume is penetration. Missing, ambiguous or non-solid deliveries stay
    unchecked. In particular, a boolean's consumed operands are not
    reconstructed or compared with its final result.
    """

    to_m = {"meter": 1.0, "millimeter": 0.001, "inch": 0.0254, "foot": 0.3048}.get(length_unit)
    if to_m is None:
        raise OcctBackendError(f"unsupported solid measurement length unit {length_unit!r}")
    occ = _occt()
    by_name: dict[str, list[StepEntry]] = {}
    for entry in entries:
        by_name.setdefault(entry.name, []).append(entry)
    results: dict[tuple[str, str], dict[str, object]] = {}
    for pair in object_pairs:
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                or any(not isinstance(name, str) or not name for name in pair)):
            raise OcctBackendError("solid object pairs must each name two final objects")
        key = tuple(pair)
        row: dict[str, object] = {"status": "unchecked", "distance_m": None, "common_volume_m3": None, "detail": ""}
        results[key] = row
        if pair[0] == pair[1]:
            row["detail"] = f"{pair[0]}: a solid cannot be checked against itself"
            continue
        shapes = []
        try:
            for name in pair:
                matches = by_name.get(name, ())
                if len(matches) != 1:
                    row["detail"] = f"{name}: {'not a final delivered object' if not matches else 'ambiguous final object name'}"
                    break
                shape = matches[0].shape
                measured = measure_shape(shape)
                if not measured.valid or not measured.closed or not measured.solid_count:
                    row["detail"] = f"{name}: not a valid closed solid delivery"
                    break
                shapes.append(shape)
            if len(shapes) != 2:
                continue
            distance = occ.BRepExtrema.BRepExtrema_DistShapeShape(*shapes)
            distance.Perform()
            if not distance.IsDone():
                raise OcctBuildError("minimum solid distance failed")
            common = occ.BRepAlgoAPI.BRepAlgoAPI_Common(*shapes)
            common.Build()
            if not common.IsDone():
                raise OcctBuildError("common solid calculation failed")
            volume = 0.0
            for solid in _explore(occ, common.Shape(), occ.TopAbs.TopAbs_SOLID):
                if not occ.BRepCheck.BRepCheck_Analyzer(solid).IsValid():
                    raise OcctBuildError("common solid is invalid")
                properties = occ.GProp.GProp_GProps()
                occ.BRepGProp.BRepGProp.VolumeProperties_s(solid, properties)
                volume += abs(float(properties.Mass()))
            distance_m = float(distance.Value()) * to_m
            common_volume_m3 = volume * to_m ** 3
            if not math.isfinite(distance_m) or distance_m < 0 or not math.isfinite(common_volume_m3):
                raise OcctBuildError("solid measurement was not finite and non-negative")
            classification = "penetrating" if common_volume_m3 > 0.0 else "contact" if distance_m == 0.0 else "separated"
            row.update(status=classification, distance_m=distance_m, common_volume_m3=common_volume_m3,
                       detail=f"{pair[0]} / {pair[1]}: {classification} (OCCT solid distance and common volume)")
        except Exception as exc:
            row["detail"] = f"{pair[0]} / {pair[1]}: solid measurement unavailable: {exc}"
    return results


__all__ = [
    "ShapeMeasure",
    "classify_point",
    "classify_program_point",
    "measure_occt_solid_pairs",
    "measure_shape",
]
