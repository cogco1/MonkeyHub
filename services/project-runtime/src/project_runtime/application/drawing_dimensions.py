"""Resolve a straight-wall opening dimension against its exact STEP section.

The recipe carries semantic intent and paper placement only. Wall production
owns the jamb locations; the retained STEP must independently contain them.
No dimension value or parameter target supplied by a client is authoritative.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from archflow.adapters.occt_backend import cad_point, section_occt_lines
from archflow.project.record_kinds import SEAT_OCCT_EXECUTION
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError
from archflow.state.state_record import parameter_bindings_of, project_grids_of, project_levels_of
from monkeyarch.capabilities.element_producers import ProductionContext, element_rows_of, produce_rows
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeydiagram.drawing_elevation import ElevationView, VerifiedElevationSource
from monkeydiagram.drawing_svg import dimension_placement_fits

from .artifacts import ModelSource, require_complete_model, require_model_source
from .binding import ProjectBinding
from .projection import project_state
from ..transport.errors import StudioError


_METRES = {"meter": 1.0, "millimeter": 0.001, "inch": 0.0254, "foot": 0.3048}
_PARAMETER_METRES = {"m": 1.0, "meter": 1.0, "mm": 0.001, "millimeter": 0.001,
                     "ft": 0.3048, "foot": 0.3048, "in": 0.0254, "inch": 0.0254}


def _source_projection(binding, model_source, stage_ref):
    projection = project_state(binding, model_source.run_id, source_stage_ref=stage_ref)
    artifact = require_model_source(binding, model_source, projection)
    if stage_ref is not None:
        stage = binding.design_stage(stage_ref)
        if ((stage.candidate_id, stage.model_sha256) != (model_source.run_id, model_source.asset_sha256)
                or stage.model_ref.uri != artifact.receipt_ref):
            raise ValueError("The selected Stage pins a different exact model.")
    return projection, artifact


def _control(record, entity, index: int, width_m: float) -> dict[str, Any]:
    path = f"params.openings[{index}].width"
    keys = [key for field, key in parameter_bindings_of(entity, record) if field == path]
    if len(keys) != 1:
        return {"canDrive": False, "driveReason": "The opening width has no unique direct parameter binding."}
    parameter = record.parameter(keys[0])
    result = {"parameterKey": parameter.key, "parameterUnit": parameter.unit, "canDrive": False}
    unit_m = _PARAMETER_METRES.get(parameter.unit)
    if unit_m is None or not math.isclose(parameter.value * unit_m, width_m, rel_tol=1e-8, abs_tol=1e-8):
        result["driveReason"] = "The parameter's declared unit/value disagrees with the producer's measured width."
    elif parameter.expr is not None or parameter.inputs:
        result["driveReason"] = "This width is derived; change its declared inputs through the existing proposal path."
    elif any(p.lock_authority and p.ref in record.closure((parameter.ref,)) for p in record.parameters):
        result["driveReason"] = "This parameter or its dependent parameter is locked."
    else:
        result["canDrive"] = True
    return result


def _supported(opening: Mapping[str, Any]) -> bool:
    return (opening.get("kind") == "door" and opening.get("shape", "rectangular") == "rectangular"
            and opening.get("count", 1) == 1 and not opening.get("type_id"))


def list_plan_dimension_intents(
    binding: ProjectBinding, model_source: ModelSource, stage_ref: ProjectRecordRef | None,
) -> tuple[Mapping[str, Any], ...]:
    """List source-backed choices, without claiming that their geometry was measured.

    ``label`` identifies the wall/opening. A selected choice still needs exact
    STEP verification by ``resolve_plan_dimensions`` before it can be driven.
    """

    projection, _ = _source_projection(binding, model_source, stage_ref)
    entities = {entity.entity_id: entity for entity in projection.record.entities_of("Element@1")}
    choices = []
    for row in element_rows_of(projection.record):
        if row.producer != "wall":
            continue
        openings = row.params.get("openings", ())
        for index, opening in enumerate(openings):
            if not _supported(opening) or sum(o.get("opening_id") == opening["opening_id"] for o in openings) != 1:
                continue
            choices.append({"entityRef": f"entity:{row.element_id}", "openingId": opening["opening_id"],
                            "label": f"{row.element_id} / {opening['opening_id']}",
                            **_control(projection.record, entities[row.element_id], index, float(opening["width"]))})
    return tuple(choices)


def _broken(intent, status: str, detail: str) -> dict[str, Any]:
    # Preserve placement/identity for repair; never carry stale endpoints or
    # a client-supplied text/value/parameter target into an unresolved result.
    return {"id": intent["id"], "entityRef": intent["entityRef"], "openingId": intent["openingId"],
            "offsetMm": intent.get("placement", {}).get("offsetMm", 8.0),
            "status": status, "detail": detail, "canDrive": False, "driveReason": detail}


def _uv(point, frame):
    delta = tuple(a - b for a, b in zip(point, frame.origin))
    return tuple(sum(a * b for a, b in zip(delta, axis)) for axis in (frame.right, frame.up))


def _jamb(lines, expected, tolerance):
    """Match only the specified semantic segment, never an edge index or nearest line."""
    matches = []
    a, b = expected
    length = math.dist(a, b)
    for line in lines:
        points = line.points
        forward = math.dist(points[0], a) <= tolerance and math.dist(points[-1], b) <= tolerance
        reverse = math.dist(points[-1], a) <= tolerance and math.dist(points[0], b) <= tolerance
        if not (forward or reverse):
            continue
        if any(abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) > tolerance * length
               for p in points):
            continue
        matches.append(points[0] if forward else points[-1])
    return matches


def resolve_plan_dimensions(
    binding: ProjectBinding, model_source: ModelSource, stage_ref: ProjectRecordRef | None,
    verified_source: VerifiedElevationSource, frame: ElevationView,
    dimensions: Sequence[Mapping[str, Any]],
    *, hidden_object_ids: Sequence[str] = (),
) -> tuple[Mapping[str, Any], ...]:
    """Measure recipe intents in STEP units, or return an explicit broken anchor.

    This reads the source only; neither Design nor the recipe is changed. The
    caller additionally checks the explicit target source before proposing an
    edit, so a successfully measured historical dimension is not edit authority.
    """

    if not dimensions:
        return ()
    try:
        projection, artifact = _source_projection(binding, model_source, stage_ref)
        require_complete_model(artifact, projection.reference.receipt or {})
        ref = record_ref_from_uri(artifact.receipt_ref, binding.project_id)
        if ref.record_kind != SEAT_OCCT_EXECUTION or artifact.representation == "composed":
            raise ValueError("The complete model has no exact native STEP proof.")
        receipt = binding.repository.load_json(ref)
        if receipt != verified_source.receipt or verified_source.run != projection.run:
            raise ValueError("The verified STEP and exact editing source name different receipts or runs.")
        identity = receipt["identity"]
        provenance = identity["binding"]
        if (receipt["status"] != "succeeded" or receipt["readback_verified"] is not True
                or receipt["exact_artifact"]["exact_brep"] is not True
                or identity["up_axis"] != "Z-up" or identity["length_unit"] != verified_source.length_unit
                or provenance["design_state_digest"] != model_source.state_digest
                or provenance["program_digest"] != verified_source.program_digest
                or provenance["run_id"] != model_source.run_id or provenance["project_id"] != binding.project_id
                or provenance["base"] != projection.run.base.to_dict()):
            raise ValueError("The exact source lacks matching state, program, units or verified readback proof.")
        if (set(verified_source.physical_object_ids) != set(receipt["physical_object_ids"])
                or set(receipt["exact_artifact"]["deliveries"]) != set(verified_source.physical_object_ids)
                or sorted(entry.name for entry in verified_source.entries) != sorted(verified_source.physical_object_ids)):
            raise ValueError("The exact STEP objects disagree with the certified physical objects.")
        if (frame.look != (0, 0, -1) or frame.right != (1, 0, 0) or frame.up != (0, 1, 0)):
            raise ValueError("Opening dimensions require the horizontal look-down plan frame.")
        metres_per_unit = _METRES[verified_source.length_unit]
        record = projection.record
        rows = element_rows_of(record)
        by_id = {row.element_id: row for row in rows}
        entities = {entity.entity_id: entity for entity in record.entities_of("Element@1")}
    except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, AttributeError) as exc:
        return tuple(_broken(intent, "unverified", str(exc)) for intent in dimensions)

    # Produce once for all selected anchors. This reuses the existing datum /
    # host dependency order and emits domain values only, not new geometry.
    context = ProductionContext(ReferenceContext(grids=project_grids_of(record), levels=project_levels_of(record)), {})
    produced = None
    results = []
    sections = {}
    for intent in dimensions:
        entity_id = str(intent["entityRef"]).removeprefix("entity:")
        row = by_id.get(entity_id)
        if row is None or row.producer != "wall":
            results.append(_broken(intent, "missing", "The semantic wall no longer exists or is not a straight-wall producer."))
            continue
        openings = row.params.get("openings", ())
        selected = [(i, o) for i, o in enumerate(openings) if o["opening_id"] == intent["openingId"]]
        if len(selected) != 1:
            results.append(_broken(intent, "missing" if not selected else "ambiguous", "The semantic opening does not resolve uniquely."))
            continue
        index, opening = selected[0]
        if not _supported(opening):
            results.append(_broken(intent, "unverified", "Only one unfilled rectangular door opening is supported."))
            continue
        try:
            if produced is None:
                produced = {r.element_id: value for r, value in zip(rows, produce_rows(rows, context))}
            voids = [v for v in produced[entity_id].hosted_voids if v.opening_id == intent["openingId"]]
            if len(voids) != 1:
                results.append(_broken(intent, "missing" if not voids else "ambiguous", "The producer has no unique semantic opening."))
                continue
            void = voids[0]
            cut_m = frame.origin[2] * metres_per_unit
            base_m = context.datum_value(void.wall.base_level_datum_id)
            if not base_m + void.sill < cut_m < base_m + void.head:
                results.append(_broken(intent, "outside-view", "The cut plane does not pass through this opening's jambs."))
                continue
            if void.cut_object_id not in verified_source.physical_object_ids:
                raise ValueError("The producer's cut wall is not an exact physical STEP object.")
            if void.cut_object_id in hidden_object_ids:
                results.append(_broken(intent, "outside-view", "The semantic wall is hidden in this drawing."))
                continue
            tolerance = 1e-7 / metres_per_unit
            jambs = []
            for along in (void.along0, void.along1):
                points = []
                for across in (0, void.wall.thickness):
                    x, z = void.wall.plan_point(along, across)
                    point = tuple(v / metres_per_unit for v in cad_point((x, cut_m, z)))
                    points.append(_uv(point, frame))
                jambs.append(tuple(points))
            u0, v0, u1, v1 = frame.crop_uv
            if any(not (u0 <= p[0] <= u1 and v0 <= p[1] <= v1) for jamb in jambs for p in jamb):
                results.append(_broken(intent, "outside-view", "The semantic jambs are outside the drawing crop."))
                continue
            if void.cut_object_id not in sections:
                sections[void.cut_object_id] = section_occt_lines(
                    verified_source.entries, object_ids=(void.cut_object_id,), origin=frame.origin,
                    right=frame.right, up=frame.up, linear_deflection=frame.linear_deflection,
                )
            matches = [_jamb(sections[void.cut_object_id], jamb, tolerance) for jamb in jambs]
            if any(len(match) != 1 for match in matches):
                results.append(_broken(intent, "ambiguous" if any(len(m) > 1 for m in matches) else "missing",
                                       "The exact section does not contain two unique semantic jamb segments."))
                continue
            start, end = matches[0][0], matches[1][0]
            width = math.dist(start, end)
            if not math.isclose(width * metres_per_unit, void.width, rel_tol=1e-8, abs_tol=1e-7):
                raise ValueError("The measured STEP width disagrees with the wall producer.")
            control = _control(record, entities[entity_id], index, width * metres_per_unit)
            result = {"id": intent["id"], "entityRef": intent["entityRef"], "openingId": intent["openingId"],
                      "status": "resolved", "start": list(start), "end": list(end), "value": width,
                      "label": f"{width * metres_per_unit * 1000:g} mm",
                      "offsetMm": intent.get("placement", {}).get("offsetMm", 8.0), **control}
            if not dimension_placement_fits(result, frame.crop_uv, metres_per_unit * 1000 / frame.scale_denominator):
                results.append(_broken(intent, "outside-view",
                                       "The dimension strokes or label extend beyond the drawing. Adjust the paper offset or crop."))
                continue
            results.append(result)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            results.append(_broken(intent, "unverified", str(exc)))
    return tuple(results)
