"""Retained drawings and observations from exact STEP or registered native models."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import inspect
from io import BytesIO
from collections import OrderedDict
from itertools import product
import json
from math import ceil, sqrt
import os
from pathlib import Path
import threading
from time import perf_counter
from typing import Any
from uuid import uuid4

from archflow.adapters.cad_execution import project_occt_lines
from archflow.adapters.occt_backend import OcctBackendError
from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.project.index import IndexUnavailable
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, SEAT_OCCT_EXECUTION, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, record_ref_from_uri, require_identifier
from monkeydiagram.drawing_elevation import (
    SECTION_PERSPECTIVE_KIND, UNIT_METRES, DrawingElevationError, DrawnView, ElevationSource, NativeModelSource, ElevationView,
    SectionPerspectiveError, SectionPerspectiveView, freeze_model_axis_elevation, freeze_section_perspective,
    object_semantics, project_model_axis_elevation, read_elevation_source, VerifiedElevationSource,
)
from monkeydiagram.mesh_views import MeshViewError, mesh_line_view, mesh_pipeline, pixel_size, triangulate

from .artifacts import (
    FORMAT_3DM, ArtifactRecord, ModelSource, SourceDocument, _document_pages, _document_source_lock, _unavailable,
    artifact_bytes, document_bytes, list_artifacts, list_documents, require_complete_model, require_model_source, save_document,
)
from .binding import retained_sources
from .binding import ProjectBinding
from .monitoring import StudioMonitor
from .projection import StateProjection, project_state, require_readable
from .projections import DOCUMENT_PAGE, ELEVATION, SECTION_PERSPECTIVE, SHEET, ProjectionQueue, on_demand_spec
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


def _readable_model_source(
    binding: ProjectBinding, source: ModelSource, projection: StateProjection,
) -> ArtifactRecord:
    """``require_model_source`` for a read of the run's own record: an older canonical base is allowed.

    Both identities are still resolved: the run's exact retained state with a
    matching receipt digest, and model bytes registered to that state.
    """

    require_readable(projection)
    if not projection.reference_state_exact or (
            projection.run.run_id, projection.state_digest) != (source.run_id, source.state_digest):
        raise StudioError(409, "MODEL_SOURCE_MISMATCH", "The model source does not match the exact retained editing state.")
    record = next((row for row in list_artifacts(binding, run_id=source.run_id).artifacts if (
        row.run_id, row.design_state_digest, row.sha256, row.format
    ) == (source.run_id, source.state_digest, source.asset_sha256, FORMAT_3DM)), None)
    if record is None:
        raise StudioError(409, "MODEL_SOURCE_UNREGISTERED", "This model has no retained artifact binding to the requested run state.")
    if not record.available:
        raise _unavailable(binding, record)
    return record


def _complete_source(
    binding: ProjectBinding, model: ModelSource | DrawingAssetSource, stage_ref: ProjectRecordRef | None,
    *, read_only: bool = False, loaded: list | None = None,
) -> tuple[ElevationSource | NativeModelSource, dict[str, Any]]:
    """The exact complete model ``model`` names and its CAD receipt.

    ``read_only`` is for a picture of the run's own recorded state (the
    projection cache): the run may stand on an older canonical base. Anything
    that builds on the model keeps the default, which requires current HEAD.
    A native model is read to find its receipt; ``loaded`` then receives what
    was read, so a caller that draws it does not read it again.
    """

    if isinstance(model, DrawingAssetSource):
        artifact, _ = artifact_bytes(binding, model.asset_sha256, run_id=model.run_id)
        if artifact.run_id != model.run_id or artifact.representation != "external":
            raise StudioError(409, "DRAWING_SOURCE_MISMATCH", "Choose an external model asset or use its exact modelSource.")
    else:
        projection = project_state(binding, model.run_id, source_stage_ref=stage_ref)
        artifact = (_readable_model_source if read_only else require_model_source)(binding, model, projection)
    cad_ref = record_ref_from_uri(artifact.receipt_ref, binding.project_id)
    if stage_ref is not None and binding.design_stage(stage_ref).model_ref != cad_ref:
        raise StudioError(409, "DRAWING_SOURCE_MISMATCH", "The selected Stage pins a different model receipt.")
    if artifact.representation in {"composed", "external"}:
        registered = binding.repository.load_json(cad_ref)
        native = NativeModelSource(model.run_id, cad_ref, ProjectArtifactRef(**registered["artifact"]))
        try:
            verified = read_elevation_source(binding.repository, native)
        except DrawingElevationError as exc:
            raise StudioError(422, "DRAWING_NATIVE_GEOMETRY_UNSUPPORTED", str(exc)) from exc
        if loaded is not None:
            loaded.append(verified)
        return native, dict(verified.receipt)
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


# Right, up and look of each view in the Z-up frame of the verified source
# model. ``axon`` is the isometric view from the -X, -Y, +Z side, Z up on the
# sheet; only the transient model view offers it.
_VIEW_FRAMES = {
    "front": ((1, 0, 0), (0, 0, 1), (0, 1, 0)),
    "back": ((-1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "left": ((0, -1, 0), (0, 0, 1), (1, 0, 0)),
    "right": ((0, 1, 0), (0, 0, 1), (-1, 0, 0)),
    "top": ((1, 0, 0), (0, 1, 0), (0, 0, -1)),
    "axon": ((1 / sqrt(2), -1 / sqrt(2), 0), (1 / sqrt(6), 1 / sqrt(6), 2 / sqrt(6)),
             (1 / sqrt(3), 1 / sqrt(3), -1 / sqrt(3))),
}
#: The crop's margin around every physical object's bounds, as a share of the longer side.
VIEW_MARGIN = 0.05
#: The axonometric's chord error in output pixels: finer facets would not be seen.
AXON_CHORD_PX = 0.5


def model_view_pipeline(view: str) -> dict[str, Any]:
    """Every input of drawing ``view`` other than the model and the pixel size.

    The projection cache puts this into its key, so a change to the frame,
    the margin, the tessellation rule or the mesh renderer and its libraries
    gives new keys rather than stale pictures. Only the axonometric is drawn
    from the mesh.
    """

    if view != "axon":
        raise ValueError(f"only the axonometric model view has a mesh pipeline, not {view!r}")
    right, up, look = _VIEW_FRAMES[view]
    # The source of this module's framing and meshing of the view, like the mesh renderer's own version.
    code = hashlib.sha256("".join(inspect.getsource(function) for function in (
        _elevation_view, _axon_meshes, _draw_view, draw_loaded_view)).encode("utf-8")).hexdigest()[:12]
    return {"view": view, "right": list(right), "up": list(up), "look": list(look), "margin": VIEW_MARGIN,
            "chordPx": AXON_CHORD_PX, "code": code, "mesh": mesh_pipeline()}


def _library_version(name: str) -> str:
    from importlib import metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "absent"


def _source_files(*modules: str) -> dict[str, str]:
    import importlib

    return {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()[:16]
            for name in modules}


_OCCT_DRAWING = ("monkeydiagram.drawing_elevation", "monkeydiagram.drawing_svg", "archflow.adapters.cad_execution",
                 "archflow.adapters.occt_backend")


def drawing_pipeline(kind: str) -> dict[str, Any]:
    """Every input of an on-demand projection other than its content and recipe: the code and libraries drawing it.

    The projection cache digests this into the renderer version (once per
    process), so an edit to the drawing code or a library upgrade gives new
    keys rather than stale drawings. The whole source of each drawing module
    counts: an edit that changes no byte only costs one more drawing.
    """

    if kind in (ELEVATION, SECTION_PERSPECTIVE):
        from archflow.adapters.occt_backend import backend_identity

        return {"kind": kind, "code": _source_files(*_OCCT_DRAWING), "backend": backend_identity(),
                "libraries": {name: _library_version(name) for name in ("Pillow", "numpy")}}
    if kind == SHEET:
        from archflow.adapters.occt_backend import backend_identity

        return {"kind": kind, "code": {**_source_files(*_OCCT_DRAWING, "monkeydiagram.drawing_output",
                                                         "monkeydiagram.documentation.styles"),
                                       "sheet": hashlib.sha256(inspect.getsource(_drawn_sheet).encode("utf-8")).hexdigest()[:16]},
                "fonts": {role: hashlib.sha256(path.read_bytes()).hexdigest()[:16] for role, path in _sheet_fonts().items()},
                "backend": backend_identity(),
                "libraries": {name: _library_version(name) for name in ("reportlab", "pypdf", "ezdxf", "Pillow")}}
    if kind == DOCUMENT_PAGE:
        from .boards import PAGE_RASTER_EDGE

        return {"kind": kind, "edge": PAGE_RASTER_EDGE, "code": _source_files("archflow_studio_api.application.boards"),
                "libraries": {name: _library_version(name) for name in ("PyMuPDF", "Pillow")}}
    raise ValueError(f"{kind!r} is not an on-demand projection")


def _drawn_content(source, verified: VerifiedElevationSource) -> dict[str, Any]:
    """What a drawing reads from its verified source besides the view: part of its projection recipe.

    The geometry's own digest (the exact STEP or native model), its unit,
    objects and their bounds, and the semantics the SVG names; none of it
    depends on the run or base the source is bound to.
    """

    receipt = verified.receipt
    return {"geometry": _source_digest(source), "unit": verified.length_unit,
            "objects": list(verified.physical_object_ids),
            "bounds": canonical_digest(receipt.get("readback") or {}),
            "semantics": canonical_digest({"drawn": object_semantics(receipt),
                                           "objects": receipt.get("expected_semantics", {}).get("objects", {})})}


def _through_projections(projections: ProjectionQueue | None, kind: str, model_source, recipe):
    """The ``cache`` a freeze asks for its drawn view: the projection of ``recipe(verified)``, drawn at most once.

    Without a queue (a runtime that keeps no project index), or when the
    index stops answering, the view is simply drawn. A hit shows in the
    monitored stages: no ``drawing.hlr``, ``drawing.svg`` or ``drawing.png``.
    """

    if projections is None:
        return None

    def cache(verified: VerifiedElevationSource, draw) -> DrawnView:
        drawn: list[DrawnView] = []

        def files():
            drawn.append(draw())
            return {"png": drawn[0].png, "svg": drawn[0].svg}, drawn[0].facts()

        spec = on_demand_spec(kind, _source_of(model_source), recipe(verified))
        try:
            files, facts, _ = projections.on_demand(spec, files)
        except IndexUnavailable:
            return drawn[0] if drawn else draw()
        return DrawnView.from_facts(facts, svg=files["svg"], png=files["png"])

    return cache


def _source_of(model_source):
    return model_source if isinstance(model_source, ModelSource) else model_source.to_dict()


def _elevation_view(
    receipt: dict[str, Any], direction: str, *, hidden_lines: bool, scale_denominator: int,
    object_ids=None,
) -> ElevationView:
    # The crop follows the retained cold-read bounds of every physical object
    # (of ``object_ids`` alone when given).
    right, up, look = _VIEW_FRAMES[direction]
    try:
        physical = receipt["physical_object_ids"]
        measured = receipt["readback"]
        if not physical or set(measured) != set(physical):
            raise ValueError("missing physical-object bounds")
        framed = physical if object_ids is None else [object_id for object_id in physical if object_id in object_ids]
        if not framed:
            raise ValueError("no object to frame")
        corners = [point for object_id in framed for point in product(*zip(
            measured[object_id]["bbox"]["min"], measured[object_id]["bbox"]["max"],
        ))]
        us = [sum(a * b for a, b in zip(point, right)) for point in corners]
        vs = [sum(a * b for a, b in zip(point, up)) for point in corners]
        depths = [sum(a * b for a, b in zip(point, look)) for point in corners]
        margin = max(max(us) - min(us), max(vs) - min(vs), 0.001) * VIEW_MARGIN
        return ElevationView(
            name=f"elevation-{direction}", origin=(0, 0, 0), look=look, right=right, up=up,
            crop_uv=(min(us) - margin, min(vs) - margin, max(us) + margin, max(vs) + margin),
            near_depth=min(depths) - margin, far_depth=max(depths) + margin,
            hidden_lines=hidden_lines, scale_denominator=scale_denominator,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", "The exact model has no complete retained bounds for this elevation.") from exc


# Recent model views by exact verified source and view name. The projection is
# the slow part of a model view and depends on nothing else; the source is
# verified again on every read. Transient process memory, never project data.
_MODEL_VIEWS: OrderedDict[tuple[Any, str], tuple[bytes, int, int]] = OrderedDict()
_MODEL_VIEW_LIMIT = 32
_model_views_lock = threading.Lock()
#: The longest edge of a model view, in pixels.
MODEL_VIEW_MAX_EDGE = 1024


@dataclass(frozen=True, slots=True)
class ModelViewDrawing:
    """One drawn model view and where its time went: reading the source, then drawing it."""

    png: bytes
    width: int
    height: int
    load_s: float
    render_s: float


def check_model_view_source(binding: ProjectBinding, model_source: ModelSource, view: str) -> None:
    """Refuse a source that does not name exactly one retained model: its records alone, no geometry is read.

    The run's exact retained state and the model bytes registered to it, as
    ``draw_model_view`` resolves them; an older canonical base is allowed.
    Whether the model's geometry can be drawn is the drawing's own question.
    """

    if view not in _VIEW_FRAMES:
        raise StudioError(422, "MODEL_VIEW_INVALID", f"There is no {view!r} model view.")
    _readable_model_source(binding, model_source, project_state(binding, model_source.run_id))


@dataclass(frozen=True, slots=True)
class LoadedModel:
    """One exact retained model, verified and read once, to be drawn at any view and size."""

    model_source: ModelSource
    source: ElevationSource | NativeModelSource
    receipt: dict[str, Any]
    verified: VerifiedElevationSource
    load_s: float


def load_model_view(binding: ProjectBinding, model_source: ModelSource) -> LoadedModel:
    """Verify one exact retained model and read its shapes, for ``draw_loaded_view``; nothing is written.

    Reading dominates the time of a large model, so a caller drawing several
    sizes of one model reads it once.
    """

    started = perf_counter()
    kept: list = []
    source, receipt = _complete_source(binding, model_source, None, read_only=True, loaded=kept)
    try:
        verified = kept[0] if kept else read_elevation_source(binding.repository, source)
    except DrawingElevationError as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", str(exc)) from exc
    return LoadedModel(model_source, source, receipt, verified, perf_counter() - started)


def draw_loaded_view(loaded: LoadedModel, *, view: str, size_px: int = MODEL_VIEW_MAX_EDGE,
                     png_text: dict[str, str] | None = None) -> ModelViewDrawing:
    """Draw one view of a model ``load_model_view`` read; its ``load_s`` is that read's."""

    drawn = _draw_view(None, loaded.source, loaded.receipt, view, size_px=size_px, png_text=png_text,
                       started=perf_counter(), verified=loaded.verified)
    return replace(drawn, load_s=loaded.load_s)


def draw_model_view(
    binding: ProjectBinding, *, model_source: ModelSource, view: str, size_px: int = MODEL_VIEW_MAX_EDGE,
    png_text: dict[str, str] | None = None,
) -> ModelViewDrawing:
    """Verify one exact retained model and draw one view of it; nothing is cached or written.

    ``axon`` is drawn from the model's triangles against a depth buffer
    (``monkeydiagram.mesh_views``); the orthographic elevations keep the exact
    hidden-line solve. ``png_text`` becomes the axonometric PNG's text chunks.
    This is a read of the run's own recorded state, so a run on an older
    canonical base is drawn too.
    """

    return draw_loaded_view(load_model_view(binding, model_source), view=view, size_px=size_px, png_text=png_text)


def _draw_view(binding, source, receipt, view, *, size_px, png_text, started, verified=None) -> ModelViewDrawing:
    from PIL import Image

    try:
        if verified is None:
            verified = read_elevation_source(binding.repository, source)
        recipe = _elevation_view(receipt, view, hidden_lines=False, scale_denominator=1)
        loaded = perf_counter()
        if view == "axon":
            meshes, recipe = _axon_meshes(verified, receipt, recipe, size_px)
            drawn = mesh_line_view(meshes, right=recipe.right, up=recipe.up,
                                   crop_uv=recipe.crop_uv, size_px=size_px, text=png_text)
            return ModelViewDrawing(drawn.png, drawn.width, drawn.height, loaded - started, perf_counter() - loaded)
        u0, v0, u1, v1 = recipe.crop_uv
        mm_per_unit = {"meter": 1000, "millimeter": 1, "inch": 25.4, "foot": 304.8}[verified.length_unit]
        # The existing PNG renderer uses 150 dpi. Choose its paper scale before
        # rendering so even the intermediate image is bounded, not resized later.
        scale = max(1, ceil(max(u1 - u0, v1 - v0) * mm_per_unit * 150 / (25.4 * (size_px - 1))))
        recipe = replace(recipe, scale_denominator=scale, linear_deflection=0.1 / mm_per_unit)
        projected = project_model_axis_elevation(
            verified.entries, object_ids=verified.physical_object_ids, view=recipe, unit=verified.length_unit,
        )
    except (DrawingElevationError, MeshViewError) as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", str(exc)) from exc
    with Image.open(BytesIO(projected.png)) as image:
        width, height = image.size
    return ModelViewDrawing(projected.png, width, height, loaded - started, perf_counter() - loaded)


def _axon_meshes(verified: VerifiedElevationSource, receipt, recipe: ElevationView, size_px: int):
    """The surfaces to draw and the view framing them.

    A curve-only object has no surface to hide or be hidden by: it is left out
    of the drawing, and out of the frame too, so a model's loose curves do not
    leave an empty band around the building. The surfaces are then meshed
    again at the narrower frame's pixel size.
    """

    meshes, skipped = triangulate(verified.entries, verified.physical_object_ids,
                                  linear_deflection=pixel_size(recipe.crop_uv, size_px) * AXON_CHORD_PX)
    if not meshes:
        raise MeshViewError("the model has no surfaces to draw")
    if skipped:
        surfaces = {mesh.object_id for mesh in meshes}
        framed = _elevation_view(receipt, recipe.name.removeprefix("elevation-"), hidden_lines=False,
                                 scale_denominator=1, object_ids=surfaces)
        if framed.crop_uv != recipe.crop_uv:
            recipe = framed
            meshes, _ = triangulate(verified.entries, sorted(surfaces),
                                    linear_deflection=pixel_size(recipe.crop_uv, size_px) * AXON_CHORD_PX)
    return meshes, recipe


def model_view(binding: ProjectBinding, *, model_source: ModelSource, view: str) -> tuple[bytes, int, int]:
    """Read one exact retained model as a bounded line projection, without retaining a drawing.

    A repeated view of the same run, state and model asset is served from an
    in-process cache once ``_complete_source`` has verified that source again.
    The key is the exact STEP or native model and receipt those three resolve
    to, with the view name, so equal keys always draw equal lines.
    """

    started = perf_counter()
    source, receipt = _complete_source(binding, model_source, None)
    key = (source, view)
    with _model_views_lock:
        if key in _MODEL_VIEWS:
            _MODEL_VIEWS.move_to_end(key)
            return _MODEL_VIEWS[key]
    drawn = _draw_view(binding, source, receipt, view, size_px=MODEL_VIEW_MAX_EDGE, png_text=None, started=started)
    with _model_views_lock:
        _MODEL_VIEWS[key] = (drawn.png, drawn.width, drawn.height)
        _MODEL_VIEWS.move_to_end(key)
        while len(_MODEL_VIEWS) > _MODEL_VIEW_LIMIT:
            _MODEL_VIEWS.popitem(last=False)
    return drawn.png, drawn.width, drawn.height


def _registered_drawing(
    binding: ProjectBinding, monitor: StudioMonitor, operation: dict[str, Any], *, model_source: ModelSource | DrawingAssetSource,
    stage_ref: ProjectRecordRef | None, drawing_id: str, same_recipe, freeze,
) -> SourceDocument:
    """A drawing request's registered revision: the exact registered one read back, or a new one retained and registered.

    ``same_recipe(document)`` says whether a registered document answers this
    request's view; ``freeze(observe)`` retains a new drawing, reporting its
    stages to ``observe`` and raising ``StudioError`` when refused.  Every
    retained drawing view shares this cache, monitoring and documents-list
    registration.
    """

    details = operation["details"]
    selected_stage = None if stage_ref is None else stage_ref.uri
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
                "view_recipe": "same" if same_recipe(document) else "changed",
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
        drawing = freeze(observe)
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
                    "drawingId": drawing_id, "revisionRef": drawing.receipt_ref.uri, "viewRecipe": drawing.receipt["view"],
                    "generatedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
            document, _ = document_bytes(binding, run.run_id, drawing.png_ref.sha256, drawing.receipt_ref.uri)
            registration["details"]["output_refs"] = [ref.uri]
        details["output_refs"] = [drawing.receipt_ref.uri, drawing.svg_ref.uri, drawing.png_ref.uri, ref.uri]
        return document


@retained_sources
def generate_elevation(
    binding: ProjectBinding, *, source_stage_ref: str | None, model_source: ModelSource | None,
    view: str, drawing_id: str | None = None, hidden_lines: bool = False, scale_denominator: int = 100,
    monitor: StudioMonitor | None = None, source_asset=None, projections: ProjectionQueue | None = None,
) -> SourceDocument:
    """One elevation of a verified source, retained and registered in the documents list.

    An identical request on the same source reads the registered revision
    back. Otherwise the drawing's files come from ``projections`` when the
    same content was drawn at the same recipe before (by any run), and are
    drawn once and kept there when not; either way they are retained in P036
    byte for byte, with a receipt of their own.
    """

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

        cache = _through_projections(projections, ELEVATION, model_source, lambda verified: {
            "view": recipe.to_dict(), "content": _drawn_content(source, verified)})

        def freeze(observe):
            try:
                return freeze_model_axis_elevation(
                    binding.repository, source=source, view=recipe, drawing_run_id=f"studio-drawing-{uuid4().hex}",
                    operation_observer=observe, parent_event_id=operation["event_id"], cache=cache,
                )
            except DrawingElevationError as exc:
                raise StudioError(409, "DRAWING_GENERATION_FAILED", str(exc)) from exc

        return _registered_drawing(binding, monitor, operation, model_source=model_source, stage_ref=stage_ref,
                                   drawing_id=drawing_id, same_recipe=lambda document: document.view_recipe == document_recipe,
                                   freeze=freeze)


@retained_sources
def generate_section_perspective(
    binding: ProjectBinding, *, source_stage_ref: str | None, model_source: ModelSource | None,
    section: dict[str, Any], camera: dict[str, Any] | None = None, depth: float | None = None,
    hidden_object_ids: tuple[str, ...] = (), drawing_id: str | None = None, scale_denominator: int = 100,
    graphics: dict[str, float] | None = None, hatch: dict[str, Any] | None = None, beyond: dict[str, Any] | None = None,
    monitor: StudioMonitor | None = None, source_asset=None, projections: ProjectionQueue | None = None,
) -> SourceDocument:
    """One section perspective of a verified source, retained and registered like an elevation.

    The same source resolution, caches, monitoring and documents-list
    registration as ``generate_elevation``; an identical request on the same
    source reads the registered revision back.  ``hatch`` and ``beyond`` are a
    cut plan's material rules and fade, which the view checks and stores
    complete beside the pens.  The drawing owner's refusals keep their names
    on the wire.
    """

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
        except StudioError:
            details.update(cache_status="refused", cache_reason="source_unavailable")
            raise
        drawing_id = drawing_id or SECTION_PERSPECTIVE_KIND
        require_identifier(drawing_id, "drawing_id")
        rules = {key: value for key, value in (("hatch", hatch), ("beyond", beyond)) if value is not None}
        if rules:
            graphics = {**(graphics or {}), **rules}
        try:
            view = SectionPerspectiveView(
                name=drawing_id, section=section, camera=camera, depth=depth, hidden_object_ids=tuple(hidden_object_ids),
                scale_denominator=scale_denominator, graphics=graphics,
                linear_deflection=0.0001 / UNIT_METRES[cad_receipt["identity"]["length_unit"]],
            )
        except SectionPerspectiveError as exc:
            details.update(cache_status="refused", cache_reason="request_invalid")
            raise StudioError(422, exc.code, str(exc)) from exc
        request = view.request()
        operation["source_ref"] = (None if stage_ref is None else stage_ref.uri) or _source_ref(binding, source).uri
        details.update(input_identity={"model_sha256" if isinstance(source, NativeModelSource) else "step_sha256": _source_digest(source), "view_recipe": request},
                       input_object_ids=list(cad_receipt["physical_object_ids"]))

        cache = _through_projections(projections, SECTION_PERSPECTIVE, model_source, lambda verified: {
            "view": request, "content": _drawn_content(source, verified)})

        def freeze(observe):
            try:
                return freeze_section_perspective(
                    binding.repository, source=source, view=view, drawing_run_id=f"studio-drawing-{uuid4().hex}",
                    operation_observer=observe, parent_event_id=operation["event_id"], cache=cache,
                )
            except SectionPerspectiveError as exc:
                raise StudioError(422, exc.code, str(exc)) from exc
            except DrawingElevationError as exc:
                raise StudioError(409, "DRAWING_GENERATION_FAILED", str(exc)) from exc

        def same_recipe(document):
            recipe = document.view_recipe or {}
            return (recipe.get("kind") == SECTION_PERSPECTIVE_KIND and recipe.get("request") == request
                    and (not isinstance(model_source, DrawingAssetSource) or
                         (recipe.get("sourceAsset") == model_source.to_dict() and recipe.get("follow") == "frozen")))

        return _registered_drawing(binding, monitor, operation, model_source=model_source, stage_ref=stage_ref,
                                   drawing_id=drawing_id, same_recipe=same_recipe, freeze=freeze)


def _drawn_sheet(binding, monitor, run_id, verified, frames, selected, layout, recipe_json) -> tuple[bytes, bytes]:
    """A review sheet's PDF, carrying its recipe, and DXF: three exact visibility solves, composed; writes nothing."""

    from pypdf import PdfReader, PdfWriter
    from monkeydiagram.documentation.styles import compose_review_sheet
    from monkeydiagram.drawing_output import render_dxf, render_pdf

    try:
        # The same composer checks fit before any expensive visibility solve.
        compose_review_sheet(views={name: () for name in frames}, **layout)
        views = {}
        for name, frame in frames.items():
            with monitor.measure("drawing.hlr", project_id=binding.project_id, run_id=run_id,
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
        return output.getvalue(), render_dxf(canvas)
    except (ValueError, OcctBackendError) as exc:
        raise StudioError(422, "DRAWING_GENERATION_FAILED", str(exc)) from exc


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
    monitor: StudioMonitor | None = None, source_asset=None, projections: ProjectionQueue | None = None,
) -> SourceDocument:
    """Three exact visibility projections, composed and retained as one source PDF.

    An identical request on the same source reads the registered sheet back;
    otherwise its PDF and DXF come from ``projections`` when drawn before, or
    are drawn once and kept there, and are retained byte for byte. The PDF
    carries its recipe, source binding included, so only a request of the same
    source and recipe finds it there.
    """

    from monkeydiagram.documentation.styles import drawing_style

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

        def draw():
            pdf, dxf = _drawn_sheet(binding, monitor, model_source.run_id, verified, frames, selected, layout, recipe_json)
            return {"pdf": pdf, "dxf": dxf}, {}

        files = None
        if projections is not None:
            spec = on_demand_spec(SHEET, _source_of(model_source), {
                "recipe": recipe, "content": _drawn_content(source, verified)})
            drawn = []
            try:
                files, _, _ = projections.on_demand(spec, lambda: drawn.append(draw()) or drawn[0])
            except IndexUnavailable:
                files = drawn[0][0] if drawn else None
        if files is None:
            files = draw()[0]
        pdf, dxf = files["pdf"], files["dxf"]
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
