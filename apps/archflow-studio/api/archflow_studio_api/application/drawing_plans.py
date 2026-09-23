"""One editable cut-plan recipe over the existing exact document and model owners."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from uuid import uuid4

from archflow.adapters.cad_patch import select_patch_operations
from archflow.contracts.canonical import canonical_digest
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError
from archflow.state.design_portfolio import DesignBranch
from monkeyarch.capabilities.geometry_proposal import load_compiled_geometry_program
from monkeydiagram.drawing_elevation import (
    DrawingElevationError, ElevationView, freeze_cut_plan, read_elevation_source,
    read_model_axis_elevation,
)

from .artifacts import (
    ModelSource, _document_pages, _document_source_lock, document_bytes, list_documents,
)
from .binding import retained_sources
from .drawings import _complete_source, _elevation_view, _selected_source
from .drawing_dimensions import resolve_plan_dimensions, list_plan_dimension_intents
from .intent import component_edit_proposal
from .projection import project_state, require_actionable
from .proposals import proposal_from
from ..transport.errors import StudioError

UNIT_METRES = {"meter": 1.0, "millimeter": .001, "inch": .0254, "foot": .3048}


def plan_frame(recipe) -> ElevationView:
    frame = recipe["frame"]
    return ElevationView(
        name=frame["name"], origin=tuple(frame["origin"]), look=tuple(frame["look"]),
        right=tuple(frame["right"]), up=tuple(frame["up"]), crop_uv=tuple(frame["crop_uv"]),
        near_depth=frame["near_depth"], far_depth=frame["far_depth"],
        hidden_lines=frame["hidden_lines"], linear_deflection=frame["linear_deflection"],
        scale_denominator=int(frame["scale"].split(":")[1]),
    )


def _plan_document(binding, run_id, asset_sha256, revision_ref):
    document, _ = document_bytes(binding, run_id, asset_sha256, revision_ref)
    if document.revision_ref != revision_ref or not document.model_source or (document.view_recipe or {}).get("kind") != "cut-plan":
        raise StudioError(422, "DRAWING_PLAN_REQUIRED", "Choose an exact retained cut-plan revision.")
    return document


def _previous_plan(binding, revision_ref):
    try:
        drawing = read_model_axis_elevation(binding.repository, record_ref_from_uri(revision_ref, binding.project_id))
    except (DrawingElevationError, ProjectRepositoryError, TypeError, ValueError) as exc:
        raise StudioError(422, "DRAWING_REVISION_INVALID", "Choose a retained cut-plan revision in this project.") from exc
    return _plan_document(binding, drawing.receipt["source"]["run_id"], drawing.png_ref.sha256, revision_ref)


@retained_sources
def generate_plan(binding, *, source_stage_ref=None, model_source=None, drawing_id=None,
                  previous_revision_ref=None, cut_height=None, bottom=None, scale_denominator=None,
                  crop_uv=None, cut_line_mm=None, visible_line_mm=None, hatch_spacing_mm=None,
                  hidden_object_ids=None, dimensions=None):
    previous = None if previous_revision_ref is None else _previous_plan(binding, previous_revision_ref)
    old = {} if previous is None else previous.view_recipe
    if previous is not None and drawing_id not in (None, previous.drawing_id):
        raise StudioError(409, "DRAWING_REVISION_MISMATCH", "Continue the selected drawing identity.")
    model_source, stage_ref = _selected_source(binding, source_stage_ref, model_source)
    if stage_ref is None:
        inferred = project_state(binding, model_source.run_id).source_stage_ref
        if inferred is not None and binding.design_stage(inferred).candidate_id == model_source.run_id:
            stage_ref = inferred
    source, cad_receipt = _complete_source(binding, model_source, stage_ref)
    unit = cad_receipt["identity"]["length_unit"]
    drawing_id = drawing_id or (previous.drawing_id if previous else "floor-plan")
    prior_frame = old.get("frame", {})
    cut = cut_height if cut_height is not None else prior_frame.get("origin", (0, 0, 1.2 / UNIT_METRES[unit]))[2]
    low = bottom if bottom is not None else (prior_frame["origin"][2] - prior_frame["far_depth"] if prior_frame else 0)
    scale = scale_denominator or (int(prior_frame["scale"].split(":")[1]) if prior_frame else 100)
    if cut <= low:
        raise StudioError(422, "DRAWING_DEPTH_INVALID", "The cut must be above the bottom of the plan view.")
    if previous and previous.model_source != model_source:
        _, old_receipt = _complete_source(binding, previous.model_source,
            None if previous.source_stage_ref is None else record_ref_from_uri(previous.source_stage_ref, binding.project_id))
        if old_receipt["identity"]["length_unit"] != unit:
            raise StudioError(409, "DRAWING_UNIT_CHANGED", "The source unit changed; the retained recipe cannot be reinterpreted in another unit.")
    try:
        frame = replace(_elevation_view(cad_receipt, "top", hidden_lines=False, scale_denominator=scale),
                        name=drawing_id, origin=(0, 0, cut), near_depth=0, far_depth=cut-low,
                        linear_deflection=.0001 / UNIT_METRES[unit])
        if crop_uv is not None or prior_frame:
            frame = replace(frame, crop_uv=tuple(crop_uv if crop_uv is not None else prior_frame["crop_uv"]))
        else:
            # Start with room for paper-space dimensions, not just the model's
            # tight bounding box. Explicit and retained crops remain the user's.
            margin = 15 * scale / (1000 * UNIT_METRES[unit])
            x0, y0, x1, y1 = frame.crop_uv
            frame = replace(frame, crop_uv=(x0-margin, y0-margin, x1+margin, y1+margin))
        graphics = old.get("graphics", {})
        recipe = {"kind": "cut-plan", "name": drawing_id, "frame": frame.to_dict(), "graphics": {
            "cutLineMm": cut_line_mm if cut_line_mm is not None else graphics.get("cutLineMm", .35),
            "visibleLineMm": visible_line_mm if visible_line_mm is not None else graphics.get("visibleLineMm", .18),
            "hatchSpacingMm": hatch_spacing_mm if hatch_spacing_mm is not None else graphics.get("hatchSpacingMm", 2),
        }, "hiddenObjectIds": sorted(set(hidden_object_ids if hidden_object_ids is not None else old.get("hiddenObjectIds", []))),
            "dimensions": list(dimensions if dimensions is not None else old.get("dimensions", []))}
        ids = [item["id"] for item in recipe["dimensions"]]
        if len(ids) != len(set(ids)):
            raise StudioError(422, "DRAWING_DIMENSION_INVALID", "Each dimension needs its own id in this drawing.")
        unknown_hidden = set(recipe["hiddenObjectIds"]) - set(cad_receipt["physical_object_ids"])
        retained_hidden = set(old.get("hiddenObjectIds", []))
        if unknown_hidden - retained_hidden:
            raise StudioError(422, "DRAWING_OBJECT_UNKNOWN", "A newly hidden object must exist in the selected exact model.")
        selected_stage = None if stage_ref is None else stage_ref.uri
        with _document_source_lock:
            for document in list_documents(binding, model_source.run_id):
                if (document.drawing_id == drawing_id and document.model_source == model_source
                        and document.source_stage_ref == selected_stage and document.view_recipe == recipe):
                    document_bytes(binding, document.run_id, document.asset_sha256, document.revision_ref)
                    return document
            verified = read_elevation_source(binding.repository, source)
            resolved = resolve_plan_dimensions(binding, model_source, stage_ref, verified, frame, recipe["dimensions"],
                                               hidden_object_ids=recipe["hiddenObjectIds"])
            drawing = freeze_cut_plan(binding.repository, source=source, recipe=recipe,
                                      drawing_run_id=f"studio-drawing-{uuid4().hex}", dimensions=resolved,
                                      previous_revision_ref=previous_revision_ref)
            run = binding.load_run(model_source.run_id)
            binding.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                record_kind=STUDIO_SOURCE_DOCUMENT,
                payload={"schema": "StudioSourceDocument@1", "project_id": binding.project_id,
                         "run_id": run.run_id, "asset_sha256": drawing.png_ref.sha256,
                         "file_name": f"{drawing_id}.png", "mime_type": "image/png", "size_bytes": len(drawing.png),
                         "pages": [asdict(page) for page in _document_pages(drawing.png, "image/png")],
                         "modelSource": model_source.to_dict(), "sourceStageRef": selected_stage,
                         "drawingId": drawing_id, "revisionRef": drawing.receipt_ref.uri,
                         "viewRecipe": recipe, "generatedAt": datetime.now(timezone.utc).isoformat()},
            )
            return _plan_document(binding, run.run_id, drawing.png_ref.sha256, drawing.receipt_ref.uri)
    except (DrawingElevationError, ValueError) as exc:
        raise StudioError(422, "DRAWING_PLAN_INVALID", str(exc)) from exc


def _branch_target(binding, document):
    if not document.source_stage_ref:
        raise StudioError(409, "DRAWING_TARGET_REQUIRED", "Choose an exact target source for this drawing; it has no accepted Stage branch.")
    return _stage_branch_head(binding, record_ref_from_uri(document.source_stage_ref, binding.project_id))


def _stage_branch_head(binding, stage_ref):
    stage = binding.design_stage(stage_ref)
    branches = binding.repository.read_design_branches()
    if stage.branch_id not in branches:
        raise StudioError(409, "DRAWING_TARGET_UNAVAILABLE", "The source Stage branch is unavailable.")
    return DesignBranch.from_dict(branches[stage.branch_id]).head_stage.uri


def _read_set(binding, receipt, frame, hidden):
    """All possible cut/occluding objects, including newly entering ones, from exact readback."""
    identity = receipt["identity"]["binding"]
    program_ref = record_ref_from_uri(identity["program_ref"]["uri"], binding.project_id)
    program = binding.repository.load_json(program_ref)
    if canonical_digest(program) != identity["program_digest"]:
        raise ValueError("The exact compiled program does not match its CAD receipt.")
    x0, y0, x1, y1 = frame.crop_uv
    cut = frame.origin[2]
    low = cut - frame.far_depth
    selected = set()
    for object_id in receipt["physical_object_ids"]:
        if object_id in hidden or receipt["expected_semantics"]["objects"][object_id].get("visible", True) is False:
            continue
        bounds = receipt["readback"][object_id]["bbox"]
        a, b = bounds["min"], bounds["max"]
        if b[0] < x0 or a[0] > x1 or b[1] < y0 or a[1] > y1 or b[2] < low or a[2] > cut:
            continue
        selected.add(object_id)
    return load_compiled_geometry_program(program), selected


def plan_status(binding, *, run_id, asset_sha256, revision_ref, target_model_source=None, target_stage_ref=None):
    document = _plan_document(binding, run_id, asset_sha256, revision_ref)
    result = {"status": "unknown", "detail": "The exact target could not be verified.", "dimensions": [],
              "targetModelSource": None, "targetStageRef": target_stage_ref, "lengthUnit": None, "bindingChanged": False}
    try:
        if target_model_source is None and target_stage_ref is None:
            target_stage_ref = _branch_target(binding, document)
        target, stage_ref = _selected_source(binding, target_stage_ref, target_model_source)
        result.update(targetModelSource=target.to_dict(), targetStageRef=None if stage_ref is None else stage_ref.uri,
                      bindingChanged=target != document.model_source or (None if stage_ref is None else stage_ref.uri) != document.source_stage_ref)
        source, receipt = _complete_source(binding, target, stage_ref)
        unit = receipt["identity"]["length_unit"]
        result["lengthUnit"] = unit
        frame = plan_frame(document.view_recipe)
        old_stage = None if not document.source_stage_ref else record_ref_from_uri(document.source_stage_ref, binding.project_id)
        _, old_receipt = _complete_source(binding, document.model_source, old_stage)
        if unit != old_receipt["identity"]["length_unit"]:
            raise ValueError("The target unit changed; explicitly revise the view instead of reinterpreting its coordinates.")
        hidden = document.view_recipe.get("hiddenObjectIds", [])
        missing = sorted(set(hidden) - set(receipt["physical_object_ids"]))
        result["unresolvedObjectIds"] = missing
        old_program, old_reads = _read_set(binding, old_receipt, frame, hidden)
        new_program, new_reads = _read_set(binding, receipt, frame, hidden)
        # Reuse the CAD owner's structural comparison. Compiler object digests
        # also include whole-state semantic evidence and are not geometry keys.
        changes = select_patch_operations(new_program, old_program)
        retained = read_model_axis_elevation(binding.repository, record_ref_from_uri(revision_ref, binding.project_id))
        original_dimensions = retained.receipt["projection"].get("dimensions", [])
        if target == document.model_source:
            dimensions = original_dimensions
        else:
            verified = read_elevation_source(binding.repository, source)
            dimensions = resolve_plan_dimensions(binding, target, stage_ref, verified, frame, document.view_recipe.get("dimensions", []),
                                                  hidden_object_ids=hidden)
        result["dimensions"] = list(dimensions)
        broken = missing or any(dimension["status"] != "resolved" for dimension in dimensions)
        # Changes to unit conversion, driving availability, actual geometry or
        # anchor location matter; representation placement is the same recipe.
        changed = (old_reads != new_reads or bool(set(changes.changed_object_ids) & (old_reads | new_reads))
                   or list(dimensions) != list(original_dimensions))
        result.update(status="partially-broken" if broken else "outdated" if changed else "current",
                      detail="Some drawing anchors or visibility selections no longer resolve." if broken else
                             "The target changes inputs read by this drawing. Rebuild explicitly." if changed else
                             "The source binding differs; the inputs read by this drawing are unchanged." if result["bindingChanged"] else
                             "This drawing matches the selected exact source.")
        if result["bindingChanged"]:
            result["dimensions"] = [{**row, "canDrive": False,
                                     "driveReason": "Rebuild against the selected source before changing a design dimension."} for row in dimensions]
        elif document.source_stage_ref and _branch_target(binding, document) != document.source_stage_ref:
            result["dimensions"] = [{**row, "canDrive": False,
                                     "driveReason": "This is a historical source. Rebuild from its branch head before changing the design."} for row in dimensions]
    except (StudioError, ProjectRepositoryError, DrawingElevationError, KeyError, TypeError, ValueError) as exc:
        result.update(status="unknown", detail=exc.detail if isinstance(exc, StudioError) else str(exc), dimensions=[])
    return result


def plan_dimension_choices(binding, model_source, source_stage_ref=None):
    model, stage_ref = _selected_source(binding, source_stage_ref, model_source)
    _, receipt = _complete_source(binding, model, stage_ref)
    return {"lengthUnit": receipt["identity"]["length_unit"],
            "dimensions": list(list_plan_dimension_intents(binding, model, stage_ref))}


def dimension_proposal(binding, *, run_id, asset_sha256, revision_ref, dimension_id, value,
                       target_model_source=None, target_stage_ref=None):
    document = _plan_document(binding, run_id, asset_sha256, revision_ref)
    source, stage_ref = _selected_source(binding, document.source_stage_ref, document.model_source)
    projection = project_state(binding, source.run_id, source_stage_ref=stage_ref)
    # A pinned historical view stays readable, but cannot pretend its old base
    # is the current accepted branch merely by supplying that same old target.
    if projection.source_stage_ref and _stage_branch_head(binding, projection.source_stage_ref) != projection.source_stage_ref.uri:
        raise StudioError(409, "STALE_BASE", "Rebuild this drawing from its branch head before changing the design.")
    status = plan_status(binding, run_id=run_id, asset_sha256=asset_sha256, revision_ref=revision_ref,
                         target_model_source=target_model_source, target_stage_ref=target_stage_ref)
    if status["status"] != "current" or status["bindingChanged"]:
        raise StudioError(409, "STALE_BASE", status["detail"])
    exact_source, _ = _complete_source(binding, source, stage_ref)
    # Re-resolve before every design request; retained canDrive is a historical
    # observation, never a grant and never trusted for execution.
    readings = resolve_plan_dimensions(binding, source, stage_ref, read_elevation_source(binding.repository, exact_source),
                                       plan_frame(document.view_recipe), document.view_recipe.get("dimensions", []),
                                       hidden_object_ids=document.view_recipe.get("hiddenObjectIds", []))
    dimension = next((row for row in readings if row["id"] == dimension_id), None)
    if dimension is None or dimension["status"] != "resolved" or not dimension.get("canDrive") or not dimension.get("parameterKey"):
        raise StudioError(409, "DRAWING_DIMENSION_NOT_DRIVING", "This dimension has no verified, unlocked direct design parameter." if dimension is None else dimension.get("driveReason", dimension.get("detail", "This dimension cannot drive the design.")))
    factors = {"m": 1, "meter": 1, "mm": .001, "millimeter": .001, "cm": .01, "inch": .0254, "in": .0254, "foot": .3048, "ft": .3048}
    parameter_unit = dimension.get("parameterUnit")
    if parameter_unit not in factors:
        raise StudioError(409, "DRAWING_DIMENSION_UNIT_UNSUPPORTED", "The parameter has no supported length unit.")
    amount = value * UNIT_METRES[status["lengthUnit"]] / factors[parameter_unit]
    require_actionable(projection)
    proposal = proposal_from(component_edit_proposal(projection, {
        "summary": f"Set opening width to {amount:g} {parameter_unit}",
        "parameters": [{"key": dimension["parameterKey"], "value": amount}],
        "entities": [], "relations": [], "removeEntityIds": [], "removeParameterKeys": [],
        "removeRelationIds": [], "protected": [], "kept": [],
    }, utterance=f"Set opening width to {amount:g} {parameter_unit}", component_id=None, keep_refs=()))
    return replace(proposal, source_run_id=source.run_id, source_stage_ref=projection.source_stage_ref)
