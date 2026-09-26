"""One editable cut-plan recipe over the existing exact document and model owners."""

from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
from itertools import chain, count
from datetime import datetime, timezone
from uuid import uuid4

from archflow.adapters.cad_patch import select_patch_operations
from archflow.contracts.canonical import canonical_digest
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError
from monkeyarch.capabilities.geometry_proposal import load_compiled_geometry_program
from monkeydiagram.drawing_elevation import (
    DrawingElevationError, ElevationView, NativeModelSource, freeze_cut_plan, read_elevation_source,
    read_model_axis_elevation, plan_dressing_anchors, resolve_plan_dressing,
)

from .artifacts import (
    ModelSource, _document_pages, _document_source_lock, document_bytes, drawing_revision_replacement, list_documents,
)
from .binding import retained_sources
from .decisions import project_recipe
from .drawings import _complete_source, _elevation_view, _selected_source, _document_source, DrawingAssetSource
from .drawing_dimensions import resolve_plan_dimensions, list_plan_dimension_intents
from .intent import component_edit_proposal
from .projection import project_state, require_actionable
from .working_draft import WorkingSources
from .proposals import proposal_from
from ..transport.errors import StudioError

UNIT_METRES = {"meter": 1.0, "millimeter": .001, "inch": .0254, "foot": .3048}
# What the code draws a cut plan with when nothing else names a value: the
# last of the four layers its pens and hatch spacing are read from (03-C4).
PAPER_DEFAULTS = {"cutLineMm": .35, "visibleLineMm": .18, "hatchSpacingMm": 2}


def _plan_source(binding, source_stage_ref, model_source, source_asset=None):
    model, stage_ref = _selected_source(binding, source_stage_ref, model_source, source_asset)
    if isinstance(model, DrawingAssetSource):
        return model, None
    if stage_ref is None:
        # No inferred Stage can mean either an unaccepted candidate or several
        # accepted histories. Only the former is a genuinely stage-less source.
        matches = {ref for branch_id in binding.repository.read_design_branches()
                   for ref, stage in binding.design_history(branch_id)
                   if stage.candidate_id == model.run_id and stage.model_sha256 == model.asset_sha256}
        if len(matches) > 1:
            raise StudioError(409, "DRAWING_SOURCE_AMBIGUOUS",
                              "This model belongs to multiple accepted Stages. Select its exact source Stage.")
        if matches:
            stage_ref = next(iter(matches))
    return model, stage_ref


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
    if document.revision_ref != revision_ref or not _document_source(document) or (document.view_recipe or {}).get("kind") != "cut-plan":
        raise StudioError(422, "DRAWING_PLAN_REQUIRED", "Choose an exact retained cut-plan revision.")
    return document


def _previous_plan(binding, revision_ref):
    try:
        drawing = read_model_axis_elevation(binding.repository, record_ref_from_uri(revision_ref, binding.project_id))
    except (DrawingElevationError, ProjectRepositoryError, TypeError, ValueError) as exc:
        raise StudioError(422, "DRAWING_REVISION_INVALID", "Choose a retained cut-plan revision in this project.") from exc
    return _plan_document(binding, drawing.receipt["source"]["run_id"], drawing.png_ref.sha256, revision_ref)


def _edit_dressing(objects, operations):
    for operation in operations:
        name, op = operation["id"], operation["op"]
        item = next((item for item in objects if item["id"] == name), None)
        if op == "insert":
            if item is not None or operation["object"]["id"] != name:
                raise StudioError(422, "DRAWING_DRESSING_INVALID", "Insert needs a new matching object id.")
            objects.append(deepcopy(operation["object"]))
        elif item is None:
            raise StudioError(422, "DRAWING_DRESSING_MISSING", f"No dressing object {name} exists in this drawing revision.")
        elif op == "delete":
            objects.remove(item)
        else:
            field = {"move": "positionUv", "scale": "size", "flip": "flipped"}[op]
            item[field] = deepcopy(operation[field])
    if len(objects) > 100:
        raise StudioError(422, "DRAWING_DRESSING_LIMIT", "A drawing supports up to 100 dressing objects.")


def plan_vector(binding, *, run_id, asset_sha256, revision_ref):
    """Retained SVG, its exact-source anchor choices and cleanup report; no regeneration or write."""
    from monkeydiagram.drawing_svg import dressing_assets
    document = _plan_document(binding, run_id, asset_sha256, revision_ref)
    drawing = read_model_axis_elevation(binding.repository, record_ref_from_uri(revision_ref, binding.project_id))
    _, receipt = _complete_source(binding, _document_source(document), None if document.source_stage_ref is None else record_ref_from_uri(document.source_stage_ref, binding.project_id))
    return {"svg": drawing.svg.decode("utf-8"), "assets": dressing_assets(),
            "anchors": plan_dressing_anchors(receipt), "cleanup": drawing.receipt.get("cleanup")}


def _cleanup_report(binding, revision_ref):
    """The projection owner's cleanup report, exactly as this revision's receipt retains it.

    Only the receipt holds it, never the recipe, so it never decides which
    revision a request reuses; a revision drawn before cleanup has none.
    """
    return binding.repository.load_json(record_ref_from_uri(revision_ref, binding.project_id)).get("cleanup")


def _paper_values(binding, stage_ref, retained, requested):
    """The pens and hatch spacing in paper millimetres, by precedence (03-C4).

    Each is the request's, else the previous revision's, else the project
    recipe's, else the code default. Every revision holds all three, so a
    rebuild keeps its own values (D-05-2) and the recipe - read only while a
    value is still open - reaches a new drawing. Its value is written exactly
    as a requested one would be, so reuse compares what is drawn and a drawing
    made before the recipe keeps its revision. ``stage_ref`` is the Stage the
    source is under. A revoked recipe no longer applies; a hard one reads
    first and is not enforced (D-05-3).
    """
    values = {key: requested[key] if requested[key] is not None else retained.get(key) for key in PAPER_DEFAULTS}
    project = project_recipe(binding, stage_ref=stage_ref) if None in values.values() else {}
    return {key: value if value is not None else project[key].value if key in project else PAPER_DEFAULTS[key]
            for key, value in values.items()}


def _paper_rules(retained, hatch, beyond, spacing_mm):
    """Material hatch/poché and the fading of lines beyond the cut, in paper units (03 C3).

    Each rule is the request's, else the previous revision's. A material rule
    is stored complete - an omitted spacing is this revision's hatchSpacingMm,
    an omitted angle 45 degrees - so the recipe alone says how each material is
    drawn. No rule, an empty ``byMaterial`` or a zero fade is an absent key: a
    recipe without them is exactly the recipe it was before they existed.
    """
    rules = {}
    by_material = deepcopy(retained.get("hatch", {}).get("byMaterial", {})) if hatch is None else {
        material: {"spacingMm": float(spacing_mm if rule.get("spacingMm") is None else rule["spacingMm"]),
                   "angleDeg": float(45 if rule.get("angleDeg") is None else rule["angleDeg"]),
                   "poche": bool(rule.get("poche", False))}
        for material, rule in sorted(hatch["byMaterial"].items())}
    if by_material:
        rules["hatch"] = {"byMaterial": by_material}
    fade = retained.get("beyond", {}).get("fade", 0) if beyond is None else beyond["fade"]
    if fade:
        rules["beyond"] = {"fade": float(fade)}
    return rules


@retained_sources
def generate_plan(binding, *, attribution, reason=None, source_kind=None, source_stage_ref=None, model_source=None,
                  drawing_id=None, previous_revision_ref=None, cut_height=None, bottom=None, scale_denominator=None,
                  crop_uv=None, cut_line_mm=None, visible_line_mm=None, hatch_spacing_mm=None, hatch=None, beyond=None,
                  hidden_object_ids=None, dimensions=None, dressing=None, dressing_operations=None, follow=None, source_asset=None):
    """One cut-plan revision, or the retained one an identical request already made.

    A new drawing takes the project recipe for the pens and hatch spacing its
    request leaves open; a rebuild keeps its own (``_paper_values``).
    ``attribution`` is who asked, as the request boundary knows it, ``reason``
    why, in their words when given (05 3.2(A)), and ``source_kind`` whether
    the request says a person (``human``) or an agent (``agent``) asked, or
    None when it does not say (05 §5). All three are retained with the
    revision's receipt, never in its recipe, so they neither make nor
    distinguish revisions: a reused revision keeps its own.
    """
    previous = None if previous_revision_ref is None else _previous_plan(binding, previous_revision_ref)
    old = {} if previous is None else previous.view_recipe
    if previous is not None and drawing_id not in (None, previous.drawing_id):
        raise StudioError(409, "DRAWING_REVISION_MISMATCH", "Continue the selected drawing identity.")
    model_source, stage_ref = _plan_source(binding, source_stage_ref, model_source, source_asset)
    source, cad_receipt = _complete_source(binding, model_source, stage_ref)
    unit = cad_receipt["identity"]["length_unit"]
    drawing_id = drawing_id or (previous.drawing_id if previous else _new_plan_id(binding))
    prior_frame = old.get("frame", {})
    cut = cut_height if cut_height is not None else prior_frame.get("origin", (0, 0, 1.2 / UNIT_METRES[unit]))[2]
    low = bottom if bottom is not None else (prior_frame["origin"][2] - prior_frame["far_depth"] if prior_frame else 0)
    scale = scale_denominator or (int(prior_frame["scale"].split(":")[1]) if prior_frame else 100)
    if cut <= low:
        raise StudioError(422, "DRAWING_DEPTH_INVALID", "The cut must be above the bottom of the plan view.")
    if previous and _document_source(previous) != model_source:
        _, old_receipt = _complete_source(binding, _document_source(previous),
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
        selected_stage = None if stage_ref is None else stage_ref.uri
        values = _paper_values(binding, selected_stage, graphics, {
            "cutLineMm": cut_line_mm, "visibleLineMm": visible_line_mm, "hatchSpacingMm": hatch_spacing_mm})
        recipe = {"kind": "cut-plan", "name": drawing_id, "frame": frame.to_dict(), "graphics": {
            **values, **_paper_rules(graphics, hatch, beyond, values["hatchSpacingMm"]),
        }, "hiddenObjectIds": sorted(set(hidden_object_ids if hidden_object_ids is not None else old.get("hiddenObjectIds", []))),
            "dimensions": list(dimensions if dimensions is not None else old.get("dimensions", []))}
        # A drawing made from a chosen version stays on it until a person rebuilds
        # it to follow the Working Head again (#271); without a choice, it follows.
        if follow == "frozen" or (follow is None and old.get("follow") == "frozen"):
            recipe["follow"] = "frozen"
        if dressing is not None or "dressing" in old or dressing_operations is not None:
            recipe["dressing"] = deepcopy(dressing if dressing is not None else old.get("dressing", []))
            if dressing_operations is not None:
                if previous is None or dressing is not None:
                    raise StudioError(422, "DRAWING_DRESSING_INVALID", "Operations need an exact previous revision and cannot accompany replacement dressing.")
                _edit_dressing(recipe["dressing"], dressing_operations)
            # Missing retained anchors survive as broken intent. New anchors must be exact.
            available = {row["objectId"] for row in plan_dressing_anchors(cad_receipt)}
            retained_anchors = {row["id"]: row.get("anchorObjectId") for row in old.get("dressing", [])}
            for item in recipe["dressing"]:
                anchor = item.get("anchorObjectId")
                if anchor is not None and anchor not in available and retained_anchors.get(item["id"]) != anchor:
                    raise StudioError(422, "DRAWING_ANCHOR_UNKNOWN", "A new dressing anchor must exist in the exact source model.")
            resolve_plan_dressing(recipe, cad_receipt)
        ids = [item["id"] for item in recipe["dimensions"]]
        if isinstance(model_source, DrawingAssetSource):
            if recipe["dimensions"]:
                raise StudioError(422, "DRAWING_DIMENSION_NOT_DRIVING", "Imported geometry has no design parameters; semantic dimensions require a bound design state.")
            recipe.update(sourceAsset=model_source.to_dict(), follow="frozen")
        if len(ids) != len(set(ids)):
            raise StudioError(422, "DRAWING_DIMENSION_INVALID", "Each dimension needs its own id in this drawing.")
        unknown_hidden = set(recipe["hiddenObjectIds"]) - set(cad_receipt["physical_object_ids"])
        retained_hidden = set(old.get("hiddenObjectIds", []))
        if unknown_hidden - retained_hidden:
            raise StudioError(422, "DRAWING_OBJECT_UNKNOWN", "A newly hidden object must exist in the selected exact model.")
        with _document_source_lock:
            for document in list_documents(binding, model_source.run_id):
                if (document.drawing_id == drawing_id and _document_source(document) == model_source
                        and document.source_stage_ref == selected_stage and document.view_recipe == recipe):
                    document_bytes(binding, document.run_id, document.asset_sha256, document.revision_ref)
                    return document
            verified = read_elevation_source(binding.repository, source)
            resolved = () if isinstance(model_source, DrawingAssetSource) else resolve_plan_dimensions(
                binding, model_source, stage_ref, verified, frame, recipe["dimensions"], hidden_object_ids=recipe["hiddenObjectIds"])
            drawing = freeze_cut_plan(binding.repository, source=source, recipe=recipe,
                                      drawing_run_id=f"studio-drawing-{uuid4().hex}", dimensions=resolved,
                                      previous_revision_ref=previous_revision_ref, reason=reason, source_kind=source_kind,
                                      attribution={"actorId": attribution.actor_id, "authenticated": attribution.authenticated,
                                                   "origin": attribution.origin})
            pages = _document_pages(drawing.png, "image/png")
            # A rebuild answers for its previous revision's page wherever that
            # page is placed (#291); a fork or a changed page shape does not.
            replaces = () if previous is None else drawing_revision_replacement(
                binding, previous, pages, list_documents(binding))
            run = binding.load_run(model_source.run_id)
            binding.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                record_kind=STUDIO_SOURCE_DOCUMENT,
                payload={"schema": "StudioSourceDocument@1", "project_id": binding.project_id,
                         "run_id": run.run_id, "asset_sha256": drawing.png_ref.sha256,
                         "file_name": f"{drawing_id}.png", "mime_type": "image/png", "size_bytes": len(drawing.png),
                         "pages": [asdict(page) for page in pages],
                         "replaces_pages": [asdict(row) for row in replaces],
                         "modelSource": None if isinstance(model_source, DrawingAssetSource) else model_source.to_dict(), "sourceStageRef": selected_stage,
                         "drawingId": drawing_id, "revisionRef": drawing.receipt_ref.uri,
                         "viewRecipe": recipe, "generatedAt": datetime.now(timezone.utc).isoformat()},
            )
            return _plan_document(binding, run.run_id, drawing.png_ref.sha256, drawing.receipt_ref.uri)
    except (DrawingElevationError, ValueError) as exc:
        raise StudioError(422, "DRAWING_PLAN_INVALID", str(exc)) from exc


def _new_plan_id(binding):
    """A new cut plan is its own drawing; only its revisions share its identity."""
    taken = {document.drawing_id for document in list_documents(binding)
             if (document.view_recipe or {}).get("kind") == "cut-plan"}
    names = chain(("floor-plan",), (f"floor-plan-{n}" for n in count(2)))
    return next(name for name in names if name not in taken)


def _on_head(head, model):
    """Whether a drawing reads exactly the Working Head, the only state it may change."""
    return head is not None and model is not None and (model.run_id, model.state_digest) == (head.run_id, head.state_digest)


def _drivable(binding, head, model, stage_ref):
    """A design change may start from the Working Head, or explicitly from the
    latest accepted state of another line; never from a line's own past."""
    if _on_head(head, model):
        return True
    if stage_ref is None:
        return False
    stage = binding.design_stage(record_ref_from_uri(stage_ref, binding.project_id))
    if head is not None and stage.branch_id == head.branch_id:
        return False
    branch = binding.repository.read_design_branches().get(stage.branch_id)
    return branch is not None and ProjectRecordRef.from_dict(branch["head_stage"]).uri == stage_ref


def _live_target(resolved, document):
    """A LIVE drawing's target: the Working Head's exact drawable source, or why there is none."""
    if resolved.head is None:
        return None, None, resolved.reason
    if _on_head(resolved.head, document.model_source):
        return document.model_source, document.source_stage_ref, None
    if resolved.source is None:
        return None, None, resolved.reason
    return resolved.source, resolved.stage_ref, None


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


def plan_status(binding, *, run_id, asset_sha256, revision_ref, target_model_source=None, target_stage_ref=None,
                working: WorkingSources | None = None):
    document = _plan_document(binding, run_id, asset_sha256, revision_ref)
    result = {"status": "unknown", "detail": "The exact target could not be verified.", "dimensions": [],
              "targetModelSource": None, "targetStageRef": target_stage_ref, "lengthUnit": None, "bindingChanged": False,
              "cleanup": None}
    try:
        result["cleanup"] = _cleanup_report(binding, revision_ref)
        imported = _document_source(document)
        if isinstance(imported, DrawingAssetSource):
            _, receipt = _complete_source(binding, imported, None)
            dressing = resolve_plan_dressing(document.view_recipe, receipt)
            missing = sorted(set(document.view_recipe.get("hiddenObjectIds", [])) - set(receipt["physical_object_ids"]))
            broken = bool(missing) or any(item["status"] != "resolved" for item in dressing)
            result.update(status="partially-broken" if broken else "current", dressing=dressing,
                          unresolvedObjectIds=missing, lengthUnit=receipt["identity"]["length_unit"], targetStageRef=None,
                          detail="Some drawing anchors or visibility selections no longer resolve." if broken else
                                 "This drawing keeps the registered imported model. Re-import a revised file to draw another version.")
            return result
        # Recheck retained stage-less drawings too: their model may since have
        # been accepted into multiple histories. Never silently rebind the page.
        _, old_stage = _plan_source(binding, document.source_stage_ref, document.model_source)
        working = working or WorkingSources(binding)
        kept = False
        if target_model_source is None and target_stage_ref is None:
            if (document.view_recipe or {}).get("follow") == "frozen":
                # A chosen version is this drawing's target until a person rebuilds it.
                target_model_source, target_stage_ref, kept = document.model_source, document.source_stage_ref, True
            else:
                target_model_source, target_stage_ref, blocked = _live_target(working("drawing"), document)
                if target_model_source is None:
                    result.update(status="outdated", detail=f"The current model cannot be drawn yet: {blocked}")
                    return result
        target, stage_ref = _plan_source(binding, target_stage_ref, target_model_source)
        result.update(targetModelSource=target.to_dict(), targetStageRef=None if stage_ref is None else stage_ref.uri,
                      bindingChanged=target != document.model_source or (None if stage_ref is None else stage_ref.uri) != document.source_stage_ref)
        source, receipt = _complete_source(binding, target, stage_ref)
        unit = receipt["identity"]["length_unit"]
        result["lengthUnit"] = unit
        frame = plan_frame(document.view_recipe)
        old_source, old_receipt = _complete_source(binding, document.model_source, old_stage)
        if unit != old_receipt["identity"]["length_unit"]:
            raise ValueError("The target unit changed; explicitly revise the view instead of reinterpreting its coordinates.")
        hidden = document.view_recipe.get("hiddenObjectIds", [])
        missing = sorted(set(hidden) - set(receipt["physical_object_ids"]))
        result["unresolvedObjectIds"] = missing
        if isinstance(source, NativeModelSource) or isinstance(old_source, NativeModelSource):
            verified = read_elevation_source(binding.repository, source)
            dimensions = resolve_plan_dimensions(binding, target, stage_ref, verified, frame,
                document.view_recipe.get("dimensions", []), hidden_object_ids=hidden)
            dressing = resolve_plan_dressing(document.view_recipe, receipt)
            broken = missing or any(row["status"] != "resolved" for row in dimensions) or any(row["status"] != "resolved" for row in dressing)
            changed = target.asset_sha256 != document.model_source.asset_sha256
            result.update(status="partially-broken" if broken else "outdated" if changed else "current",
                          dimensions=list(dimensions), dressing=dressing,
                          detail="The imported/composed model changed; rebuild the drawing." if changed else "This drawing matches the retained native model.")
            return result
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
        dressing = resolve_plan_dressing(document.view_recipe, receipt)
        original_dressing = resolve_plan_dressing(document.view_recipe, old_receipt)
        result["dressing"] = dressing
        broken = missing or any(dimension["status"] != "resolved" for dimension in dimensions) or any(item["status"] != "resolved" for item in dressing)
        # Changes to unit conversion, driving availability, actual geometry or
        # anchor location matter; representation placement is the same recipe.
        changed = (old_reads != new_reads or bool(set(changes.changed_object_ids) & (old_reads | new_reads))
                   or list(dimensions) != list(original_dimensions) or dressing != original_dressing)
        result.update(status="partially-broken" if broken else "outdated" if changed else "current",
                      detail="Some drawing anchors or visibility selections no longer resolve." if broken else
                             "The target changes inputs read by this drawing. Rebuild explicitly." if changed else
                             "The source binding differs; the inputs read by this drawing are unchanged." if result["bindingChanged"] else
                             "This drawing matches the selected exact source.")
        if kept and result["status"] == "current":
            result["detail"] = "This drawing stays on the version it was drawn from until it is rebuilt."
        if result["bindingChanged"]:
            result["dimensions"] = [{**row, "canDrive": False,
                                     "driveReason": "Rebuild against the selected source before changing a design dimension."} for row in dimensions]
        elif not _drivable(binding, working.head, document.model_source, document.source_stage_ref):
            result["dimensions"] = [{**row, "canDrive": False,
                                     "driveReason": "This drawing shows an earlier model. Rebuild it on the current model before changing the design."} for row in dimensions]
    except (StudioError, ProjectRepositoryError, DrawingElevationError, KeyError, TypeError, ValueError) as exc:
        result.update(status="unknown", detail=exc.detail if isinstance(exc, StudioError) else str(exc), dimensions=[])
    return result


def plan_dimension_choices(binding, model_source=None, source_stage_ref=None, source_asset=None):
    model, stage_ref = _plan_source(binding, source_stage_ref, model_source, source_asset)
    _, receipt = _complete_source(binding, model, stage_ref)
    return {"lengthUnit": receipt["identity"]["length_unit"],
            "dimensions": [] if isinstance(model, DrawingAssetSource) else list(list_plan_dimension_intents(binding, model, stage_ref))}


def dimension_proposal(binding, *, run_id, asset_sha256, revision_ref, dimension_id, value,
                       target_model_source=None, target_stage_ref=None):
    document = _plan_document(binding, run_id, asset_sha256, revision_ref)
    if isinstance(_document_source(document), DrawingAssetSource):
        raise StudioError(409, "DRAWING_DIMENSION_NOT_DRIVING", "Imported geometry has no bound design parameters.")
    source, stage_ref = _plan_source(binding, document.source_stage_ref, document.model_source)
    projection = project_state(binding, source.run_id, source_stage_ref=stage_ref)
    # A historical view stays readable, but a change starts only from the
    # Working Head or another line's latest Stage; an old target never passes.
    working = WorkingSources(binding)
    if not _drivable(binding, working.head, source,
                     None if stage_ref is None else stage_ref.uri):
        raise StudioError(409, "STALE_BASE", "Rebuild this drawing on the current model before changing the design.")
    status = plan_status(binding, run_id=run_id, asset_sha256=asset_sha256, revision_ref=revision_ref,
                         target_model_source=target_model_source, target_stage_ref=target_stage_ref, working=working)
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
