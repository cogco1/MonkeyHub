"""Retained drawings and transient observations from the existing exact STEP owner."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
from io import BytesIO
from itertools import product
import json
from math import ceil
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from archflow.adapters.cad_execution import project_occt_lines
from archflow.adapters.occt_backend import OcctBackendError
from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, SEAT_OCCT_EXECUTION, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, record_ref_from_uri, require_identifier
from monkeydiagram.drawing_elevation import (
    DrawingElevationError, ElevationSource, NativeModelSource, ElevationView, freeze_model_axis_elevation,
    project_model_axis_elevation, read_elevation_source,
)

from .artifacts import (
    ModelSource, SourceDocument, _document_pages, _document_source_lock,
    artifact_bytes, document_bytes, list_artifacts, list_documents, require_complete_model, require_model_source, save_document,
)
from .binding import retained_sources
from .binding import ProjectBinding
from .monitoring import StudioMonitor
from .projection import project_state
from ..transport.errors import StudioError


@dataclass(frozen=True)
class DrawingAssetSource:
    run_id: str
    asset_sha256: str

    def to_dict(self):
        return {"runId": self.run_id, "assetSha256": self.asset_sha256}


def _source_ref(binding, source):
    return source.registration if isinstance(source, NativeModelSource) else ProjectRecordRef(
        binding.project_id, source.cad_receipt_relative_path, source.cad_receipt_sha256)


def _source_digest(source):
    return source.artifact.sha256 if isinstance(source, NativeModelSource) else source.step_sha256


def _document_source(document):
    asset = (document.view_recipe or {}).get("sourceAsset")
    return DrawingAssetSource(asset["runId"], asset["assetSha256"]) if asset else document.model_source


def _model_binding(source):
    return source if isinstance(source, ModelSource) else None


def _selected_source(
    binding: ProjectBinding, source_stage_ref: str | None, model_source: ModelSource | None, source_asset=None,
) -> tuple[ModelSource | DrawingAssetSource, ProjectRecordRef | None]:
    if source_asset is not None:
        if model_source is not None or source_stage_ref is not None:
            raise StudioError(422, "DRAWING_SOURCE_AMBIGUOUS", "Choose a model version or one imported asset.")
        return DrawingAssetSource(source_asset["runId"], source_asset["assetSha256"]), None
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
    return model_source, stage_ref


def _complete_source(
    binding: ProjectBinding, model: ModelSource | DrawingAssetSource, stage_ref: ProjectRecordRef | None,
) -> tuple[ElevationSource | NativeModelSource, dict[str, Any]]:
    if isinstance(model, DrawingAssetSource):
        artifact, _ = artifact_bytes(binding, model.asset_sha256, run_id=model.run_id)
        if artifact.run_id != model.run_id or artifact.representation != "external":
            raise StudioError(409, "DRAWING_SOURCE_MISMATCH", "Choose an external model asset or use its exact modelSource.")
    else:
        projection = project_state(binding, model.run_id, source_stage_ref=stage_ref)
        artifact = require_model_source(binding, model, projection)
    cad_ref = record_ref_from_uri(artifact.receipt_ref, binding.project_id)
    if stage_ref is not None and binding.design_stage(stage_ref).model_ref != cad_ref:
        raise StudioError(409, "DRAWING_SOURCE_MISMATCH", "The selected Stage pins a different model receipt.")
    if artifact.representation in {"composed", "external"}:
        registered = binding.repository.load_json(cad_ref)
        native = NativeModelSource(model.run_id, cad_ref, ProjectArtifactRef(**registered["artifact"]))
        try:
            return native, dict(read_elevation_source(binding.repository, native).receipt)
        except DrawingElevationError as exc:
            raise StudioError(422, "DRAWING_NATIVE_GEOMETRY_UNSUPPORTED", str(exc)) from exc
    if cad_ref.record_kind != SEAT_OCCT_EXECUTION:
        raise StudioError(409, "DRAWING_COMPLETE_SOURCE_UNAVAILABLE", "This complete model has no matching exact STEP. Its native components cannot stand in for a drawing of the complete building.")
    try:
        require_complete_model(artifact, projection.reference.receipt or {})
    except StudioError as exc:
        raise StudioError(409, "DRAWING_COMPLETE_SOURCE_UNAVAILABLE", exc.detail) from exc
    choices = [row for row in list_artifacts(binding, run_id=model.run_id).artifacts if row.run_id == model.run_id
               and row.receipt_ref == cad_ref.uri and row.format == "step" and row.available
               and row.design_state_digest == model.state_digest]
    if len(choices) != 1 or choices[0].relative_path is None or choices[0].sha256 is None:
        raise StudioError(409, "DRAWING_COMPLETE_SOURCE_UNAVAILABLE", "The exact STEP paired with this model is unavailable.")
    step = choices[0]
    return ElevationSource(model.run_id, step.relative_path, step.sha256, cad_ref.relative_path, cad_ref.sha256), binding.repository.load_json(cad_ref)


def _elevation_view(
    receipt: dict[str, Any], direction: str, *, hidden_lines: bool, scale_denominator: int,
) -> ElevationView:
    # Coordinates are the Z-up frame used by the verified source model. The
    # crop follows the retained cold-read bounds of every physical object.
    right, up, look = {
        "front": ((1, 0, 0), (0, 0, 1), (0, 1, 0)),
        "back": ((-1, 0, 0), (0, 0, 1), (0, -1, 0)),
        "left": ((0, -1, 0), (0, 0, 1), (1, 0, 0)),
        "right": ((0, 1, 0), (0, 0, 1), (-1, 0, 0)),
        "top": ((1, 0, 0), (0, 1, 0), (0, 0, -1)),
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
        vs = [sum(a * b for a, b in zip(point, up)) for point in corners]
        depths = [sum(a * b for a, b in zip(point, look)) for point in corners]
        margin = max(max(us) - min(us), max(vs) - min(vs), 0.001) * 0.05
        return ElevationView(
            name=f"elevation-{direction}", origin=(0, 0, 0), look=look, right=right, up=up,
            crop_uv=(min(us) - margin, min(vs) - margin, max(us) + margin, max(vs) + margin),
            near_depth=min(depths) - margin, far_depth=max(depths) + margin,
            hidden_lines=hidden_lines, scale_denominator=scale_denominator,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", "The exact model has no complete retained bounds for this elevation.") from exc


def model_view(binding: ProjectBinding, *, model_source: ModelSource, view: str) -> tuple[bytes, int, int]:
    """Read one exact retained model as a bounded line projection, without retaining a drawing."""

    from PIL import Image

    source, receipt = _complete_source(binding, model_source, None)
    try:
        verified = read_elevation_source(binding.repository, source)
        recipe = _elevation_view(receipt, view, hidden_lines=False, scale_denominator=1)
        u0, v0, u1, v1 = recipe.crop_uv
        mm_per_unit = {"meter": 1000, "millimeter": 1, "inch": 25.4, "foot": 304.8}[verified.length_unit]
        # The existing PNG renderer uses 150 dpi. Choose its paper scale before
        # rendering so even the intermediate image is bounded, not resized later.
        scale = max(1, ceil(max(u1 - u0, v1 - v0) * mm_per_unit * 150 / (25.4 * 1023)))
        recipe = replace(recipe, scale_denominator=scale, linear_deflection=0.1 / mm_per_unit)
        projected = project_model_axis_elevation(
            verified.entries, object_ids=verified.physical_object_ids, view=recipe, unit=verified.length_unit,
        )
    except DrawingElevationError as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", str(exc)) from exc
    with Image.open(BytesIO(projected.png)) as image:
        width, height = image.size
    return projected.png, width, height


@retained_sources
def generate_elevation(
    binding: ProjectBinding, *, source_stage_ref: str | None, model_source: ModelSource | None,
    view: str, drawing_id: str | None = None, hidden_lines: bool = False, scale_denominator: int = 100,
    monitor: StudioMonitor | None = None, source_asset=None,
) -> SourceDocument:
    monitor = monitor if monitor is not None else StudioMonitor(None)
    with monitor.measure(
        "drawing_generate", project_id=binding.project_id, source_ref=source_stage_ref,
        run_id=None if model_source is None else model_source.run_id,
        details={"scope": "global_visibility", "cache_status": "unknown", "executed_stages": []},
    ) as operation:
        details = operation["details"]
        try:
            model_source, stage_ref = _selected_source(binding, source_stage_ref, model_source, source_asset)
            operation["run_id"] = model_source.run_id
            source, cad_receipt = _complete_source(binding, model_source, stage_ref)
            recipe = _elevation_view(cad_receipt, view, hidden_lines=hidden_lines, scale_denominator=scale_denominator)
        except StudioError:
            details.update(cache_status="refused", cache_reason="source_unavailable")
            raise
        drawing_id = drawing_id or recipe.name
        require_identifier(drawing_id, "drawing_id")
        selected_stage = None if stage_ref is None else stage_ref.uri
        document_recipe = recipe.to_dict()
        if isinstance(model_source, DrawingAssetSource):
            document_recipe.update(sourceAsset=model_source.to_dict(), follow="frozen")
        operation["source_ref"] = selected_stage or _source_ref(binding, source).uri
        details.update(
            input_identity={"model_sha256" if isinstance(source, NativeModelSource) else "step_sha256": _source_digest(source), "view_recipe": {
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
                    "model_source": "same" if _document_source(document) == model_source else "changed",
                    "view_recipe": "same" if document.view_recipe == document_recipe else "changed",
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
                        "modelSource": None if isinstance(model_source, DrawingAssetSource) else model_source.to_dict(), "sourceStageRef": selected_stage,
                        "drawingId": drawing_id, "revisionRef": drawing.receipt_ref.uri, "viewRecipe": document_recipe,
                        "generatedAt": datetime.now(timezone.utc).isoformat(),
                    },
                )
                document, _ = document_bytes(binding, run.run_id, drawing.png_ref.sha256, drawing.receipt_ref.uri)
                registration["details"]["output_refs"] = [ref.uri]
            details["output_refs"] = [drawing.receipt_ref.uri, drawing.svg_ref.uri, drawing.png_ref.uri, ref.uri]
            return document


def _sheet_fonts() -> dict[str, Path]:
    """Installed TTFs for the paper renderer; machine paths stay out of project data."""

    import reportlab

    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    bundled = Path(reportlab.__file__).parent / "fonts"
    for normal, bold in (
        (windows / "arial.ttf", windows / "arialbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"), Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"), Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")),
        (bundled / "Vera.ttf", bundled / "VeraBd.ttf"),
    ):
        if normal.is_file() and bold.is_file():
            return {"normal": normal, "bold": bold}
    raise StudioError(503, "DRAWING_FONT_UNAVAILABLE", "Install Arial, DejaVu Sans or Liberation Sans regular and bold TTF fonts to render this sheet.")


@retained_sources
def generate_sheet(
    binding: ProjectBinding, *, source_stage_ref: str | None, model_source: ModelSource | None,
    style_id: str, scale_denominator: int = 20, hidden_object_ids: tuple[str, ...] = (),
    outline_object_ids: tuple[str, ...] = (), notes: tuple[str, ...] = (),
    monitor: StudioMonitor | None = None, source_asset=None,
) -> SourceDocument:
    """Three exact visibility projections, composed and retained as one source PDF."""

    from pypdf import PdfReader, PdfWriter
    from monkeydiagram.documentation.styles import compose_review_sheet, drawing_style
    from monkeydiagram.drawing_output import render_dxf, render_pdf

    model_source, stage_ref = _selected_source(binding, source_stage_ref, model_source, source_asset)
    source, receipt = _complete_source(binding, model_source, stage_ref)
    try:
        verified = read_elevation_source(binding.repository, source)
        style = drawing_style(style_id)
    except (DrawingElevationError, ValueError) as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", str(exc)) from exc
    physical = set(verified.physical_object_ids)
    hidden, outline = set(hidden_object_ids), set(outline_object_ids)
    unknown = (hidden | outline) - physical
    if unknown:
        raise StudioError(422, "DRAWING_OBJECT_UNKNOWN", "These physical objects are not in the selected model: " + ", ".join(sorted(unknown)))
    if hidden & outline:
        raise StudioError(422, "DRAWING_OBJECT_CONFLICT", "An object cannot be both hidden and outlined: " + ", ".join(sorted(hidden & outline)))
    selected = tuple(sorted(physical - hidden))
    if not selected:
        raise StudioError(422, "DRAWING_EMPTY", "Keep at least one physical object visible on the sheet.")
    fonts = _sheet_fonts()
    selected_receipt = {**receipt, "physical_object_ids": selected,
                        "readback": {key: receipt["readback"][key] for key in selected}}
    mm_per_unit = {"meter": 1000, "millimeter": 1, "inch": 25.4, "foot": 304.8}[verified.length_unit]
    frames = {
        view: replace(_elevation_view(selected_receipt, view, hidden_lines=False, scale_denominator=scale_denominator),
                      linear_deflection=0.1 / mm_per_unit)
        for view in ("front", "right", "top")
    }
    bounds = {key: receipt["readback"][key]["bbox"] for key in selected}
    recipe = {
        "kind": "review-sheet", "style": style, "scaleDenominator": scale_denominator,
        "hiddenObjectIds": sorted(hidden), "outlineObjectIds": sorted(outline), "notes": list(notes),
        "views": {name: frame.to_dict() for name, frame in frames.items()},
        "source": {"modelSource": None if isinstance(model_source, DrawingAssetSource) else model_source.to_dict(),
                   "sourceStageRef": None if stage_ref is None else stage_ref.uri,
                   "modelSha256" if isinstance(source, NativeModelSource) else "stepSha256": _source_digest(source),
                   "cadReceiptRef": _source_ref(binding, source).uri},
        "fonts": {name: path.name for name, path in fonts.items()}, "title": binding.project_id,
    }
    if isinstance(model_source, DrawingAssetSource):
        recipe.update(sourceAsset=model_source.to_dict(), follow="frozen")
    # Use the wire form for both cold comparison and the PDF's provenance metadata.
    recipe_json = canonical_json(recipe, ascii=False)
    recipe = json.loads(recipe_json)
    monitor = monitor if monitor is not None else StudioMonitor(None)
    with monitor.measure("drawing_generate", project_id=binding.project_id, run_id=model_source.run_id,
                         source_ref=recipe["source"]["sourceStageRef"] or recipe["source"]["cadReceiptRef"],
                         details={"scope": "global_visibility", "input_identity": {"view_recipe": {
                                      "style_id": style_id, "scale_denominator": scale_denominator}},
                                  "input_object_ids": list(selected), "cache_status": "unknown"}) as operation:
        with _document_source_lock:
            for document in list_documents(binding, model_source.run_id):
                if _document_source(document) == model_source and document.view_recipe == recipe:
                    document_bytes(binding, document.run_id, document.asset_sha256)
                    operation["details"].update(cache_status="hit", execution_path="retained_drawing")
                    return document
        operation["details"].update(cache_status="miss", execution_path="full_projection")
        layout = dict(style_id=style_id, bounds=bounds, length_unit=verified.length_unit,
                      title=binding.project_id, scale_denominator=scale_denominator,
                      notes=notes, outline_object_ids=tuple(sorted(outline)), font_mapping=fonts)
        try:
            # The same composer checks fit before any expensive visibility solve.
            compose_review_sheet(views={name: () for name in frames}, **layout)
            views = {}
            for name, frame in frames.items():
                with monitor.measure("drawing.hlr", project_id=binding.project_id, run_id=model_source.run_id,
                                     details={"input_identity": {"view_recipe": {"view": name}},
                                              "input_object_ids": list(selected)}) as projection:
                    views[name] = project_occt_lines(
                        verified.entries, object_ids=selected, origin=frame.origin,
                        right=frame.right, up=frame.up, linear_deflection=frame.linear_deflection,
                    )
                    projection["details"]["emitted_object_ids"] = sorted({line.object_id for line in views[name]})
            canvas = compose_review_sheet(views=views, **layout)
            pdf = render_pdf(canvas)
            # Source/configuration identity remains recoverable from the exported
            # PDF, including two recipes that happen to draw identical lines.
            reader = PdfReader(BytesIO(pdf))
            writer = PdfWriter(clone_from=reader)
            writer.add_metadata({"/ArchFlowViewRecipe": recipe_json})
            output = BytesIO()
            writer.write(output)
            pdf = output.getvalue()
            dxf = render_dxf(canvas)
        except (ValueError, OcctBackendError) as exc:
            raise StudioError(422, "DRAWING_GENERATION_FAILED", str(exc)) from exc
        _document_pages(pdf, "application/pdf")
        digest = hashlib.sha256(pdf).hexdigest()
        binding.repository.put_workspace_file(
            run=verified.run, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=verified.run.run_id),
            artifact_id=f"drawing-sheet-{digest}", workspace_relative_path=f"documentation/{digest}/sheet.pdf",
            media_type="application/pdf", source=BytesIO(pdf),
        )
        binding.repository.put_workspace_file(
            run=verified.run, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=verified.run.run_id),
            artifact_id=f"drawing-dxf-{digest}", workspace_relative_path=f"documentation/{digest}/sheet.dxf",
            media_type="application/dxf", source=BytesIO(dxf),
        )
        document = save_document(
            binding, model_source.run_id, f"{style_id}-1-{scale_denominator}.pdf", "application/pdf",
            base64.b64encode(pdf).decode("ascii"), _model_binding(model_source),
            drawing_id=style_id, source_stage_ref=None if stage_ref is None else stage_ref.uri,
            view_recipe=recipe, generated_at=datetime.now(timezone.utc).isoformat(),
        )
        operation["details"]["output_refs"] = [document.model_source_binding_ref]
        return document
