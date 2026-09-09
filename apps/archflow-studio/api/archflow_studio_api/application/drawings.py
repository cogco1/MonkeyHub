"""The Studio consumer of the existing exact STEP elevation owner."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from itertools import product
from typing import Any
from uuid import uuid4

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, SEAT_OCCT_EXECUTION, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri, require_identifier
from monkeydiagram.drawing_elevation import (
    DrawingElevationError, ElevationSource, ElevationView, freeze_model_axis_elevation,
)

from .artifacts import (
    ModelSource, SourceDocument, _document_pages, _document_source_lock,
    document_bytes, list_artifacts, list_documents, require_complete_model, require_model_source,
)
from .binding import ProjectBinding
from .monitoring import StudioMonitor
from .projection import project_state
from ..transport.errors import StudioError


def _complete_source(
    binding: ProjectBinding, model: ModelSource, stage_ref: ProjectRecordRef | None,
) -> tuple[ElevationSource, dict[str, Any]]:
    projection = project_state(binding, model.run_id, source_stage_ref=stage_ref)
    artifact = require_model_source(binding, model, projection)
    cad_ref = record_ref_from_uri(artifact.receipt_ref, binding.project_id)
    if stage_ref is not None and binding.design_stage(stage_ref).model_ref != cad_ref:
        raise StudioError(409, "DRAWING_SOURCE_MISMATCH", "The selected Stage pins a different model receipt.")
    if artifact.representation == "composed" or cad_ref.record_kind != SEAT_OCCT_EXECUTION:
        raise StudioError(409, "DRAWING_COMPLETE_SOURCE_UNAVAILABLE", "This complete model has no matching exact STEP. Its native components cannot stand in for a drawing of the complete building.")
    try:
        require_complete_model(artifact, projection.reference.receipt or {})
    except StudioError as exc:
        raise StudioError(409, "DRAWING_COMPLETE_SOURCE_UNAVAILABLE", exc.detail) from exc
    choices = [row for row in list_artifacts(binding).artifacts if row.run_id == model.run_id
               and row.receipt_ref == cad_ref.uri and row.format == "step" and row.available
               and row.design_state_digest == model.state_digest]
    if len(choices) != 1 or choices[0].relative_path is None or choices[0].sha256 is None:
        raise StudioError(409, "DRAWING_COMPLETE_SOURCE_UNAVAILABLE", "The exact STEP paired with this model is unavailable.")
    step = choices[0]
    return ElevationSource(model.run_id, step.relative_path, step.sha256, cad_ref.relative_path, cad_ref.sha256), binding.repository.load_json(cad_ref)


def _elevation_view(
    receipt: dict[str, Any], direction: str, *, hidden_lines: bool, scale_denominator: int,
) -> ElevationView:
    # Coordinates are the CAD Z-up frame used by the verified STEP. The
    # crop follows the retained cold-read bounds of every physical object.
    right, look = {
        "front": ((1, 0, 0), (0, 1, 0)),
        "back": ((-1, 0, 0), (0, -1, 0)),
        "left": ((0, -1, 0), (1, 0, 0)),
        "right": ((0, 1, 0), (-1, 0, 0)),
    }[direction]
    try:
        physical = receipt["physical_object_ids"]
        measured = receipt["readback"]
        if not physical or set(measured) != set(physical):
            raise ValueError("missing physical-object bounds")
        corners = [point for object_id in physical for point in product(*zip(
            measured[object_id]["bbox"]["min"], measured[object_id]["bbox"]["max"],
        ))]
        us = [sum(a * b for a, b in zip(point, right)) for point in corners]
        vs = [point[2] for point in corners]
        depths = [sum(a * b for a, b in zip(point, look)) for point in corners]
        margin = max(max(us) - min(us), max(vs) - min(vs), 0.001) * 0.05
        return ElevationView(
            name=f"elevation-{direction}", origin=(0, 0, 0), look=look, right=right, up=(0, 0, 1),
            crop_uv=(min(us) - margin, min(vs) - margin, max(us) + margin, max(vs) + margin),
            near_depth=min(depths) - margin, far_depth=max(depths) + margin,
            hidden_lines=hidden_lines, scale_denominator=scale_denominator,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", "The exact model has no complete retained bounds for this elevation.") from exc


def generate_elevation(
    binding: ProjectBinding, *, source_stage_ref: str | None, model_source: ModelSource | None,
    view: str, drawing_id: str | None = None, hidden_lines: bool = False, scale_denominator: int = 100,
    monitor: StudioMonitor | None = None,
) -> SourceDocument:
    monitor = monitor if monitor is not None else StudioMonitor(None)
    with monitor.measure(
        "drawing_generate", project_id=binding.project_id, source_ref=source_stage_ref,
        run_id=None if model_source is None else model_source.run_id,
        details={"scope": "global_visibility", "cache_status": "unknown", "executed_stages": []},
    ) as operation:
        details = operation["details"]
        try:
            try:
                stage_ref = None if source_stage_ref is None else record_ref_from_uri(source_stage_ref, binding.project_id)
                if stage_ref is not None and stage_ref.record_kind != DESIGN_STAGE:
                    raise ValueError("the source is not a design Stage")
            except (TypeError, ValueError) as exc:
                raise StudioError(422, "DESIGN_STAGE_REF_INVALID", "Select a retained design Stage in this project.") from exc
            if stage_ref is not None:
                stage = binding.design_stage(stage_ref)
                projection = project_state(binding, source_stage_ref=stage_ref)
                stage_model = ModelSource(stage.candidate_id, projection.state_digest, stage.model_sha256)
                if model_source is not None and model_source != stage_model:
                    raise StudioError(409, "DRAWING_SOURCE_MISMATCH", "The selected Stage and model name different contents.")
                model_source = stage_model
            if model_source is None:
                raise StudioError(422, "DRAWING_SOURCE_REQUIRED", "Select a committed Stage or an exact retained model.")
            operation["run_id"] = model_source.run_id
            source, cad_receipt = _complete_source(binding, model_source, stage_ref)
            recipe = _elevation_view(cad_receipt, view, hidden_lines=hidden_lines, scale_denominator=scale_denominator)
        except StudioError:
            details.update(cache_status="refused", cache_reason="source_unavailable")
            raise
        drawing_id = drawing_id or recipe.name
        require_identifier(drawing_id, "drawing_id")
        selected_stage = None if stage_ref is None else stage_ref.uri
        operation["source_ref"] = selected_stage or ProjectRecordRef(
            binding.project_id, source.cad_receipt_relative_path, source.cad_receipt_sha256,
        ).uri
        details.update(
            input_identity={"step_sha256": source.step_sha256, "view_recipe": {
                key: value for key, value in recipe.to_dict().items() if key not in {"uv_definition", "depth_definition"}}},
            input_object_ids=list(cad_receipt["physical_object_ids"]),
        )
        observer = monitor.observer(project_id=binding.project_id, run_id=model_source.run_id,
                                    source_ref=operation["source_ref"], parent_event_id=operation["event_id"])

        def observe(event):
            phase = event["phase"]
            if phase not in details["executed_stages"]:
                details["executed_stages"].append(phase)
            observed = event.get("details", {})
            if observed.get("input_identity"):
                details["input_identity"] = observed["input_identity"]
            if phase == "drawing.svg" and "emitted_object_ids" in observed:
                details["emitted_object_ids"] = observed["emitted_object_ids"]
            observer(event)

        with _document_source_lock:
            closest, closest_checks = None, {}
            for document in list_documents(binding, model_source.run_id):
                checks = {
                    "drawing_id": "same" if document.drawing_id == drawing_id else "changed",
                    "source_stage_ref": "same" if document.source_stage_ref == selected_stage else "changed",
                    "model_source": "same" if document.model_source == model_source else "changed",
                    "view_recipe": "same" if document.view_recipe == recipe.to_dict() else "changed",
                }
                if all(value == "same" for value in checks.values()):
                    details.update(cache_checks=checks, comparison_refs=[document.revision_ref],
                                   execution_path="retained_drawing")
                    try:
                        document_bytes(binding, document.run_id, document.asset_sha256, document.revision_ref)
                    except StudioError:
                        checks["bytes"] = "invalid"
                        details.update(cache_status="refused", cache_reason="cached_document_unavailable")
                        raise
                    checks["bytes"] = "same"
                    details.update(cache_status="hit", cache_reason="exact_registered_drawing",
                                   output_refs=[document.revision_ref], input_equivalent=True)
                    if monitor.store is not None and document.revision_ref is not None:
                        try:
                            retained = binding.repository.load_json(record_ref_from_uri(document.revision_ref, binding.project_id))
                            backend = retained["projection"]["backend"]
                            details["input_identity"].update(backend=backend["binding"], backend_version=backend["binding_version"])
                        except Exception:
                            # This optional diagnostic read cannot make a verified drawing unavailable.
                            pass
                    return document
                if document.drawing_id == drawing_id and (closest is None or
                        sum(value == "same" for value in checks.values()) > sum(value == "same" for value in closest_checks.values())):
                    closest, closest_checks = document, checks
            details.update(
                cache_status="miss", cache_reason="no_registered_drawing" if closest is None else "registered_inputs_changed",
                cache_checks={"drawing_id": "missing"} if closest is None else closest_checks,
                comparison_refs=[] if closest is None else [closest.revision_ref], execution_path="full_projection",
            )
            try:
                drawing = freeze_model_axis_elevation(
                    binding.repository, source=source, view=recipe, drawing_run_id=f"studio-drawing-{uuid4().hex}",
                    operation_observer=observe, parent_event_id=operation["event_id"],
                )
            except DrawingElevationError as exc:
                raise StudioError(409, "DRAWING_GENERATION_FAILED", str(exc)) from exc
            details["executed_stages"].append("drawing.register")
            with monitor.measure("drawing.register", project_id=binding.project_id, run_id=model_source.run_id,
                                 source_ref=operation["source_ref"], details={"input_identity": details["input_identity"]}) as registration:
                run = binding.load_run(model_source.run_id)
                pages = _document_pages(drawing.png, "image/png")
                ref = binding.repository.put_json(
                    run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                    record_kind=STUDIO_SOURCE_DOCUMENT,
                    payload={
                        "schema": "StudioSourceDocument@1", "project_id": binding.project_id, "run_id": run.run_id,
                        "asset_sha256": drawing.png_ref.sha256, "file_name": f"{drawing_id}.png", "mime_type": "image/png",
                        "size_bytes": len(drawing.png), "pages": [asdict(page) for page in pages],
                        "modelSource": model_source.to_dict(), "sourceStageRef": selected_stage,
                        "drawingId": drawing_id, "revisionRef": drawing.receipt_ref.uri, "viewRecipe": drawing.receipt["view"],
                        "generatedAt": datetime.now(timezone.utc).isoformat(),
                    },
                )
                document, _ = document_bytes(binding, run.run_id, drawing.png_ref.sha256, drawing.receipt_ref.uri)
                registration["details"]["output_refs"] = [ref.uri]
            details["output_refs"] = [drawing.receipt_ref.uri, drawing.svg_ref.uri, drawing.png_ref.uri, ref.uri]
            return document
