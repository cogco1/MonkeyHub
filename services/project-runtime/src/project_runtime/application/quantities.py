"""Quantities of one exact model: each physical member's volume, area, closure, role and material (G4).

Volume, closure and solid count are what the model's CAD receipt recorded of
its cold read, the same numbers its drawings stand on; area is measured on the
same exact shapes. Role and material are the construction facets the model's
state gives each geometry id; a member with no facet material wears the model
asset's own. Reads only: nothing is retained.
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any, Mapping

from archflow.state.state_record import part_of_object

from ..binding import ProjectBinding
from ..errors import StudioError
from .artifacts import ModelSource
from .construction import construction_model
from .drawings import load_drawing_source, object_groups
from .drawing_plans import UNIT_METRES
from .projection import project_state

ROLE_FACETS = ("architectural.role", "structural.role")
CSV_COLUMNS = ("objectId", "role", "material", "materialSource", "closed", "solidCount", "volume", "volumeM3",
               "area", "areaM2", "declaredDelivery", "layer", "measuredFrom")


def _member_facets(binding: ProjectBinding, selected, source_stage_ref: str | None) -> dict[str, dict[str, str]]:
    """Each geometry id's (and each part's) facets, from the state the model was built from; none for an import."""

    if not isinstance(selected, ModelSource):
        return {}
    try:
        projection = project_state(binding, selected.run_id, require_view=False, source_stage_ref=source_stage_ref)
    except StudioError:
        return {}
    facets: dict[str, dict[str, str]] = {}
    for entity in construction_model(projection)["entities"]:
        own = dict(entity.get("facets") or {})
        facets[entity["id"]] = own
        for part in entity.get("parts") or ():
            facets[part] = {**own, **((entity.get("partFacets") or {}).get(part) or {})}
    return facets


def model_quantities(binding: ProjectBinding, *, model_source: ModelSource | None = None,
                     source_asset: Mapping[str, str] | None = None, source_stage_ref: str | None = None) -> dict[str, Any]:
    """One row per physical member of the exact model the drawing routes would select, and their sums.

    ``summary`` groups closed members by material and role: an open surface or
    a curve has no volume, so it is counted apart and adds only area.
    """
    from monkeycad.backends.occt.measure import measure_shape, surface_area

    selected, source, receipt, verified = load_drawing_source(
        binding, model_source=model_source, source_asset=None if source_asset is None else dict(source_asset),
        source_stage_ref=source_stage_ref)
    unit = verified.length_unit
    to_m = UNIT_METRES.get(unit)
    readback = receipt.get("readback") or {}
    shapes = {entry.name: entry.shape for entry in verified.entries if entry.name}
    facets = _member_facets(binding, selected, source_stage_ref)
    try:
        worn = object_groups(binding, selected, source, verified.physical_object_ids, "material")
    except StudioError:
        worn = {}
    rows = []
    for object_id in verified.physical_object_ids:
        measured = dict(readback.get(object_id) or {})
        shape = shapes.get(object_id)
        area = surface_area(shape) if shape is not None else None
        measured_from = "receipt"
        if "closed" not in measured and shape is not None:
            # An imported model's receipt keeps only bounds: closure and volume
            # are measured on the same exact shape instead.
            own_measure = measure_shape(shape)
            measured.update(closed=own_measure.closed, solid_count=own_measure.solid_count, volume=own_measure.volume)
            measured_from = "shape"
        # The one naming rule the pick and the export use: obj-<id> exactly, else the longest obj-<id>-… prefix.
        own = facets.get(part_of_object(object_id, facets) or "") or {}
        role = next((own[key] for key in ROLE_FACETS if own.get(key)), None)
        material, material_source = own.get("material.name"), "facet"
        if not material:
            material = worn.get(object_id) if worn.get(object_id) not in (None, "unknown", "no material") else None
            material_source = "model" if material else None
        # A volume is a closed member's only (as IngeTrazo reports it too); an open one has area alone.
        volume = measured.get("volume") if measured.get("closed") else None
        layers = measured.get("layers") or []
        rows.append({
            "objectId": object_id, "role": role, "material": material, "materialSource": material_source,
            "closed": measured.get("closed"), "solidCount": measured.get("solid_count"),
            "volume": volume, "volumeM3": None if volume is None or to_m is None else volume * to_m ** 3,
            "area": area, "areaM2": None if area is None or to_m is None else area * to_m ** 2,
            "declaredDelivery": measured.get("declared_delivery"), "layer": layers[-1] if layers else None,
            "bbox": measured.get("bbox"), "measuredFrom": measured_from,
        })
    groups: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        group = groups.setdefault((row["material"], row["role"]), {
            "material": row["material"], "role": row["role"], "count": 0, "closedCount": 0, "openCount": 0,
            "volume": 0.0, "volumeM3": 0.0 if to_m else None, "area": 0.0, "areaM2": 0.0 if to_m else None})
        group["count"] += 1
        if row["closed"] and row["volume"] is not None:
            group["closedCount"] += 1
            group["volume"] += row["volume"]
            if to_m:
                group["volumeM3"] += row["volumeM3"]
        else:
            group["openCount"] += 1
        if row["area"] is not None:
            group["area"] += row["area"]
            if to_m:
                group["areaM2"] += row["areaM2"]
    summary = sorted(groups.values(), key=lambda group: (group["material"] or "￿", group["role"] or "￿"))
    return {
        "projectId": binding.project_id,
        "modelSource": selected.to_dict(), "sourceStageRef": source_stage_ref,
        "lengthUnit": unit, "members": rows, "summary": summary,
        "totals": {"count": len(rows), "closedCount": sum(1 for row in rows if row["closed"]),
                   "volume": sum(row["volume"] or 0.0 for row in rows if row["closed"]),
                   "area": sum(row["area"] or 0.0 for row in rows)},
    }


def quantities_csv(result: Mapping[str, Any]) -> str:
    """The members as CSV, one row each, in the model's own unit and in metres; summary rows follow."""

    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow((*CSV_COLUMNS, "lengthUnit"))
    for row in result["members"]:
        writer.writerow((*("" if row[key] is None else row[key] for key in CSV_COLUMNS), result["lengthUnit"]))
    writer.writerow(())
    writer.writerow(("summary", "role", "material", "count", "closedCount", "openCount", "volume", "volumeM3",
                     "area", "areaM2"))
    for group in result["summary"]:
        writer.writerow(("", *("" if group[key] is None else group[key] for key in (
            "role", "material", "count", "closedCount", "openCount", "volume", "volumeM3", "area", "areaM2"))))
    return output.getvalue()
