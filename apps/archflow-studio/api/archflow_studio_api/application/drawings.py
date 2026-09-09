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
) -> SourceDocument:
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
    source, cad_receipt = _complete_source(binding, model_source, stage_ref)
    recipe = _elevation_view(cad_receipt, view, hidden_lines=hidden_lines, scale_denominator=scale_denominator)
    drawing_id = drawing_id or recipe.name
    require_identifier(drawing_id, "drawing_id")
    selected_stage = None if stage_ref is None else stage_ref.uri
    with _document_source_lock:
        for document in list_documents(binding, model_source.run_id):
            if (document.drawing_id, document.source_stage_ref, document.model_source, document.view_recipe) == (
                drawing_id, selected_stage, model_source, recipe.to_dict(),
            ):
                document_bytes(binding, document.run_id, document.asset_sha256, document.revision_ref)
                return document
        try:
            drawing = freeze_model_axis_elevation(
                binding.repository, source=source, view=recipe, drawing_run_id=f"studio-drawing-{uuid4().hex}",
            )
        except DrawingElevationError as exc:
            raise StudioError(409, "DRAWING_GENERATION_FAILED", str(exc)) from exc
        run = binding.load_run(model_source.run_id)
        pages = _document_pages(drawing.png, "image/png")
        binding.repository.put_json(
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
        return document
