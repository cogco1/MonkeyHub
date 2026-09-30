"""Retained drawings and observations from exact STEP or registered native models.

Elevations and axonometrics, section perspectives and sheets are drawn here;
cut plans and vertical sections in ``drawing_plans``. A sheet either lays out
front, right and top at one scale (the review sheet) or places same-source
views the caller defines, each drawn through its own generator, where the
caller put them. Every sheet is one paper scene written as PDF, DXF, SVG and
PNG, retained together and registered as its PDF.
"""

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
import math
from math import ceil, sqrt
import os
from pathlib import Path
import threading
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4

from monkeycad.cad_execution import project_occt_lines
from monkeycad.occt_backend import OcctBackendError
from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, SEAT_OCCT_EXECUTION, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, record_ref_from_uri, require_identifier
from monkeydiagram.drawing_elevation import (
    CUT_PLAN_KIND, ELEVATION_KIND, SECTION_PERSPECTIVE_KIND, UNIT_METRES, DrawingElevationError, DrawnView, ElevationSource,
    NativeModelSource, ElevationView, SectionPerspectiveError, SectionPerspectiveView, axonometric_frame,
    freeze_model_axis_elevation, freeze_section_perspective, inspection_witness_ids, object_semantics,
    project_model_axis_elevation, read_elevation_source, read_model_axis_elevation, VerifiedElevationSource,
)
from monkeydiagram.drawing_svg import PNG_MEDIA_TYPE, SVG_MEDIA_TYPE, DrawingSvgError, svg_paper_marks
from monkeydiagram.mesh_views import MeshViewError, mesh_line_view, mesh_pipeline, pixel_size, triangulate

from .artifacts import (
    FORMAT_3DM, ArtifactRecord, ModelSource, SourceDocument, _document_pages, _document_source_lock, _unavailable,
    artifact_bytes, document_bytes, list_artifacts, list_documents, require_complete_model, require_model_source, save_document,
)
from ..binding import retained_sources
from ..binding import ProjectBinding
from ..monitoring import StudioMonitor
from .projection import StateProjection, project_state, require_readable
from .projections import DOCUMENT_PAGE, ELEVATION, SECTION_PERSPECTIVE, SHEET, ProjectionQueue, on_demand_spec
from ..errors import StudioError


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


def require_unit(requested: str | None, unit: str) -> None:
    """A request may state the unit its coordinates are written in, only to have it checked: nothing is converted."""

    if requested is not None and requested != unit:
        raise StudioError(422, "DRAWING_UNIT_MISMATCH", f"The request is written in {requested}, but the source model is "
                                                        f"in {unit}; state its coordinates and distances in {unit}.")


def is_vertical_section(recipe) -> bool:
    """Whether a cut-plan recipe is a vertical section: its frame has CAD +Z up; a plan's looks down -Z."""
    frame = (recipe or {}).get("frame") or {}
    return list(frame.get("up", ())) == [0, 0, 1]


#: The kinds of drawing a drawing id can name, as a refusal says them.
DRAWING_KINDS = {CUT_PLAN_KIND: "cut plan", ELEVATION_KIND: "elevation", SECTION_PERSPECTIVE_KIND: "section perspective",
                 "review-sheet": "review sheet", "view-sheet": "view sheet"}


def refuse_other_kind(binding: ProjectBinding, drawing_id: str, kind: str, what: str) -> None:
    """A drawing id names one kind of drawing for as long as the project keeps it.

    The Diagram opens the newest document of each drawing id, so another kind
    registered under it (an elevation or a sheet where a plan was) would
    silently take that drawing's place. ``what`` is the requested drawing as
    the refusal names it. Documents without a drawing kind (uploads, renders)
    never count. Call it under ``_document_source_lock`` where a registration
    follows, and before anything is drawn or written.
    """

    for document in list_documents(binding, None):
        recipe = document.view_recipe or {}
        other = recipe.get("kind")
        if document.drawing_id != drawing_id or other not in DRAWING_KINDS or other == kind:
            continue
        name = ("vertical section" if is_vertical_section(recipe) else "plan") if other == CUT_PLAN_KIND else DRAWING_KINDS[other]
        raise StudioError(409, "DRAWING_KIND_CHANGED", f"Drawing {drawing_id} is {'an' if name[0] in 'aeiou' else 'a'} "
                                                       f"{name}; draw this {what} under another drawing id.")


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


_OCCT_DRAWING = ("monkeydiagram.drawing_elevation", "monkeydiagram.drawing_svg", "monkeycad.cad_execution",
                 "monkeycad.occt_backend")


def drawing_pipeline(kind: str) -> dict[str, Any]:
    """Every input of an on-demand projection other than its content and recipe: the code and libraries drawing it.

    The projection cache digests this into the renderer version (once per
    process), so an edit to the drawing code or a library upgrade gives new
    keys rather than stale drawings. The whole source of each drawing module
    counts: an edit that changes no byte only costs one more drawing.
    """

    if kind in (ELEVATION, SECTION_PERSPECTIVE):
        from monkeycad.occt_backend import backend_identity

        return {"kind": kind, "code": _source_files(*_OCCT_DRAWING), "backend": backend_identity(),
                "libraries": {name: _library_version(name) for name in ("Pillow", "numpy", "rhino3dm")}}
    if kind == SHEET:
        from monkeycad.occt_backend import backend_identity

        return {"kind": kind, "code": {**_source_files(*_OCCT_DRAWING, "monkeydiagram.drawing_output",
                                                         "monkeydiagram.documentation.styles",
                                                         "project_runtime.application.boards"),
                                       "sheet": hashlib.sha256("".join(inspect.getsource(function) for function in (
                                           _drawn_sheet, _sheet_files, _view_sheet_scene)).encode("utf-8")).hexdigest()[:16]},
                "fonts": {role: hashlib.sha256(path.read_bytes()).hexdigest()[:16] for role, path in _sheet_fonts().items()},
                "backend": backend_identity(),
                "libraries": {name: _library_version(name)
                              for name in ("reportlab", "pypdf", "ezdxf", "Pillow", "fonttools", "rhino3dm", "PyMuPDF")}}
    if kind == DOCUMENT_PAGE:
        from .boards import PAGE_RASTER_EDGE

        return {"kind": kind, "edge": PAGE_RASTER_EDGE, "code": _source_files("project_runtime.application.boards"),
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

    Without a queue (a runtime that keeps no project index) the view is
    simply drawn; a cache that fails is skipped by ``on_demand``. A hit shows
    in the monitored stages: no ``drawing.hlr``, ``drawing.svg`` or
    ``drawing.png``. What the receipt says about a hit follows from the
    verified key and the files wherever it can: the backend (part of the
    key's renderer), an elevation's view (its recipe) and the objects the SVG
    names; the solve's own counts and details come from the key's manifest.
    """

    if projections is None:
        return None

    def cache(verified: VerifiedElevationSource, draw) -> DrawnView:
        def files():
            drawn = draw()
            return {"png": drawn.png, "svg": drawn.svg}, drawn.facts()

        recipe_of_view = recipe(verified)
        files, facts, hit = projections.on_demand(on_demand_spec(kind, _source_of(model_source), recipe_of_view), files)
        if hit:
            from monkeycad.occt_backend import backend_identity
            from monkeydiagram.drawing_svg import svg_objects

            facts = {**facts, "backend": backend_identity(),
                     "counts": {**facts["counts"], "objects_drawn_in_svg": len(svg_objects(files["svg"]))}}
            if kind == ELEVATION:
                facts["view"] = recipe_of_view["view"]
        return DrawnView.from_facts(facts, svg=files["svg"], png=files["png"])

    return cache


def _source_of(model_source):
    return model_source if isinstance(model_source, ModelSource) else model_source.to_dict()


def _elevation_view(
    receipt: dict[str, Any], direction: str, *, hidden_lines: bool, scale_denominator: int,
    object_ids=None, frame=None,
) -> ElevationView:
    # The crop follows the retained cold-read bounds of every physical object
    # (of ``object_ids`` alone when given). ``frame`` (right, up, look) replaces
    # the named direction's, as an axonometric from a stated direction does.
    right, up, look = _VIEW_FRAMES[direction] if frame is None else frame
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
    stage_ref: ProjectRecordRef | None, drawing_id: str, kind: str, what: str, same_recipe, freeze,
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
        try:
            refuse_other_kind(binding, drawing_id, kind, what)
        except StudioError:
            details.update(cache_status="refused", cache_reason="drawing_kind_changed")
            raise
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


def _parallel_frame(view: str, direction) -> tuple | None:
    """The frame of an axonometric seen from ``direction``; None keeps the named view's own frame."""

    if direction is None:
        return None
    if view != "axon":
        raise StudioError(422, "DRAWING_VIEW_INVALID", "A direction belongs to the axon view.")
    try:
        return axonometric_frame(direction)
    except DrawingElevationError as exc:
        raise StudioError(422, "DRAWING_VIEW_INVALID", str(exc)) from exc


@retained_sources
def generate_elevation(
    binding: ProjectBinding, *, source_stage_ref: str | None, model_source: ModelSource | None,
    view: str, drawing_id: str | None = None, hidden_lines: bool = False, scale_denominator: int = 100,
    monitor: StudioMonitor | None = None, source_asset=None, projections: ProjectionQueue | None = None,
    direction=None, length_unit: str | None = None,
) -> SourceDocument:
    """One elevation or axonometric of a verified source, retained and registered in the documents list.

    ``axon`` is a parallel view of the whole model from ``direction`` (toward
    the viewer), by default the model view's axon from -X, -Y, +Z. An
    identical request on the same source reads the registered revision
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
            require_unit(length_unit, cad_receipt["identity"]["length_unit"])
            recipe = _elevation_view(cad_receipt, view, hidden_lines=hidden_lines, scale_denominator=scale_denominator,
                                     frame=_parallel_frame(view, direction))
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
                                   drawing_id=drawing_id, kind=ELEVATION_KIND,
                                   what="axonometric" if view == "axon" else "elevation",
                                   same_recipe=lambda document: document.view_recipe == document_recipe, freeze=freeze)


@retained_sources
def generate_section_perspective(
    binding: ProjectBinding, *, source_stage_ref: str | None, model_source: ModelSource | None,
    section: dict[str, Any], camera: dict[str, Any] | None = None, depth: float | None = None,
    hidden_object_ids: tuple[str, ...] = (), drawing_id: str | None = None, scale_denominator: int = 100,
    graphics: dict[str, float] | None = None, hatch: dict[str, Any] | None = None, beyond: dict[str, Any] | None = None,
    monitor: StudioMonitor | None = None, source_asset=None, projections: ProjectionQueue | None = None,
    length_unit: str | None = None,
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
            require_unit(length_unit, cad_receipt["identity"]["length_unit"])
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
                                   drawing_id=drawing_id, kind=SECTION_PERSPECTIVE_KIND, what="section perspective",
                                   same_recipe=same_recipe, freeze=freeze)


def _drawn_sheet(binding, monitor, run_id, verified, frames, selected, layout, recipe_json) -> dict[str, bytes]:
    """A review sheet's four files (``_sheet_files``): three exact visibility solves, composed; writes nothing."""

    from monkeydiagram.documentation.styles import compose_review_sheet

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
        return _sheet_files(canvas, recipe_json)
    except (ValueError, OcctBackendError) as exc:
        raise StudioError(422, "DRAWING_GENERATION_FAILED", str(exc)) from exc


#: A sheet's files, in the order its projection keeps them (the first is the row's blob), with their media types.
SHEET_FILES = (("pdf", "application/pdf"), ("dxf", "application/dxf"), ("svg", SVG_MEDIA_TYPE), ("png", PNG_MEDIA_TYPE))
#: The PDF metadata naming the digests of the sheet's DXF, SVG and PNG.
SHEET_FILE_DIGESTS = "/ArchFlowSheetFiles"
SHEET_KINDS = ("review-sheet", "view-sheet")


def _sheet_files(canvas, recipe_json: str) -> dict[str, bytes]:
    """One paper scene as the sheet's four files; the PDF carries its recipe and the digests of the other three.

    PDF, DXF and SVG are serialised from the same scene; the PNG is that PDF
    page rasterised as a Board export draws it (144 dpi on white). Source and
    configuration identity stay recoverable from the PDF, including two
    recipes that happen to draw identical lines, and the PDF names the exact
    bytes of its DXF, SVG and PNG. Writes nothing.
    """

    from pypdf import PdfReader, PdfWriter
    from monkeydiagram.drawing_output import render_dxf, render_pdf, render_svg
    from .boards import page_export_png

    pdf = render_pdf(canvas)
    files = {"dxf": render_dxf(canvas), "svg": render_svg(canvas), "png": page_export_png(pdf, "application/pdf", 0)}
    writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf)))
    writer.add_metadata({"/ArchFlowViewRecipe": recipe_json, SHEET_FILE_DIGESTS: canonical_json(
        {role: hashlib.sha256(data).hexdigest() for role, data in files.items()})})
    output = BytesIO()
    writer.write(output)
    return {"pdf": output.getvalue(), **files}


def _retain_sheet(binding, model_source, stage_ref, files, *, file_name: str, drawing_id: str,
                  recipe: dict[str, Any]) -> SourceDocument:
    """Retain a sheet's four files beside each other in its source run and register its PDF in the documents list.

    A drawing id that already names another kind of drawing is refused before a file is written.
    """

    _document_pages(files["pdf"], "application/pdf")
    digest = hashlib.sha256(files["pdf"]).hexdigest()
    with _document_source_lock:
        refuse_other_kind(binding, drawing_id, recipe["kind"], DRAWING_KINDS[recipe["kind"]])
        run = binding.load_run(model_source.run_id)
        destination = PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run.run_id)
        for role, media_type in SHEET_FILES:
            binding.repository.put_workspace_file(
                run=run, destination=destination, artifact_id=f"drawing-{'sheet' if role == 'pdf' else role}-{digest}",
                workspace_relative_path=f"documentation/{digest}/sheet.{role}", media_type=media_type,
                source=BytesIO(files[role]),
            )
        return save_document(
            binding, model_source.run_id, file_name, "application/pdf", base64.b64encode(files["pdf"]).decode("ascii"),
            _model_binding(model_source), drawing_id=drawing_id, source_stage_ref=None if stage_ref is None else stage_ref.uri,
            view_recipe=recipe, generated_at=datetime.now(timezone.utc).isoformat(),
        )


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
    style_id: str, scale_denominator: int | None = None, hidden_object_ids: tuple[str, ...] = (),
    outline_object_ids: tuple[str, ...] = (), notes: tuple[str, ...] = (),
    monitor: StudioMonitor | None = None, source_asset=None, projections: ProjectionQueue | None = None,
    views: tuple[Mapping[str, Any], ...] | None = None, paper_size_mm=None, title: str | None = None,
    subtitle: str | None = None, sheet_number: str | None = None, drawing_id: str | None = None,
    length_unit: str | None = None, attribution=None,
) -> SourceDocument:
    """One sheet of one exact source, retained as PDF, DXF, SVG and PNG and registered as its PDF.

    Without ``views``: front, right and top at one scale (default 1:20), three
    exact visibility projections laid out by the style. With ``views``: each
    view drawn or read back through its own generator from this one source,
    then placed at its own scale where the caller put it (``_view_sheet``).
    An identical request on the same source reads the registered sheet back;
    otherwise its files come from ``projections`` when drawn before, or are
    drawn once and kept there, and are retained byte for byte. The PDF
    carries its recipe, source binding included, so only a request of the same
    source and recipe finds it there.
    """

    from monkeydiagram.documentation.styles import drawing_style

    if views is not None:
        return _view_sheet(
            binding, source_stage_ref=source_stage_ref, model_source=model_source, source_asset=source_asset,
            style_id=style_id, views=tuple(views), paper_size_mm=paper_size_mm, title=title, subtitle=subtitle,
            sheet_number=sheet_number, drawing_id=drawing_id, notes=tuple(notes), length_unit=length_unit,
            attribution=attribution, monitor=monitor if monitor is not None else StudioMonitor(None),
            projections=projections)
    scale_denominator = 20 if scale_denominator is None else scale_denominator
    model_source, stage_ref = _selected_source(binding, source_stage_ref, model_source, source_asset)
    source, receipt = _complete_source(binding, model_source, stage_ref)
    require_unit(length_unit, receipt["identity"]["length_unit"])
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
    selected = tuple(sorted(physical - hidden - inspection_witness_ids(receipt)))
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
            # Before anything is drawn; _retain_sheet checks again as it registers.
            refuse_other_kind(binding, style_id, "review-sheet", "review sheet")
        operation["details"].update(cache_status="miss", execution_path="full_projection")
        layout = dict(style_id=style_id, bounds=bounds, length_unit=verified.length_unit,
                      title=binding.project_id, scale_denominator=scale_denominator,
                      notes=notes, outline_object_ids=tuple(sorted(outline)), font_mapping=fonts)

        def draw():
            return _drawn_sheet(binding, monitor, model_source.run_id, verified, frames, selected, layout, recipe_json), {}

        if projections is None:
            files = draw()[0]
        else:
            files, _, _ = projections.on_demand(on_demand_spec(SHEET, _source_of(model_source), {
                "recipe": recipe, "content": _drawn_content(source, verified)}), draw)
        document = _retain_sheet(binding, model_source, stage_ref, files, file_name=f"{style_id}-1-{scale_denominator}.pdf",
                                 drawing_id=style_id, recipe=recipe)
        operation["details"]["output_refs"] = [document.model_source_binding_ref]
        return document


_UNIT_SYMBOLS = {"meter": "m", "millimeter": "mm", "inch": "in", "foot": "ft"}
_UNIT_DECIMALS = {"meter": 3, "millimeter": 0, "inch": 2, "foot": 3}
_UNIT_NAMES = {"meter": "metres", "millimeter": "millimetres", "inch": "inches", "foot": "feet"}


def _signed(value: float, unit: str) -> str:
    return f"{value:+.{_UNIT_DECIMALS[unit]}f} {_UNIT_SYMBOLS[unit]}"


def _axis(vector) -> str | None:
    """+X, -Y, ... for a model axis direction; None for any other direction."""

    for index, name in enumerate("XYZ"):
        if abs(abs(vector[index]) - 1.0) <= 1e-9:
            return ("+" if vector[index] > 0 else "-") + name
    return None


def _sheet_view_kind(view: Mapping[str, Any], recipe: Mapping[str, Any]) -> str:
    if view["kind"] == "plan":
        return "section" if is_vertical_section(recipe) else "plan"
    if view["kind"] == "elevation":
        return "axon" if view["arguments"].get("view") == "axon" else "elevation"
    return view["kind"]


def _sheet_view_labels(view: Mapping[str, Any], kind: str, recipe: Mapping[str, Any], unit: str) -> tuple[str, str, str]:
    """A placed view's title, subtitle and scale label: the caller's words, else what its own frame states."""

    label = view.get("mark_label")
    cut = f" {label}-{label}" if label else ""
    if kind == "plan":
        frame = recipe["frame"]
        title, subtitle, scale = "PLAN", f"Horizontal cut at Z {_signed(frame['origin'][2], unit)}, looking down", frame["scale"]
    elif kind == "section":
        frame = recipe["frame"]
        look = _axis(frame["look"])
        position = frame["origin"]["XYZ".index(look[1])]
        title, subtitle, scale = "SECTION" + cut, f"Vertical cut at {look[1]} {_signed(position, unit)}, looking {look}", frame["scale"]
    elif kind == "axon":
        toward = [-value for value in recipe["look"]]
        iso = max(abs(value) for value in toward) - min(abs(value) for value in toward) <= 1e-9
        sides = " / ".join(("+" if value > 0 else "-") + name for name, value in zip("XYZ", toward) if abs(value) > 1e-9)
        title = "ISOMETRIC" if iso else "AXONOMETRIC"
        subtitle, scale = f"Parallel view from {sides}, whole model, not to scale", f"display {recipe['scale']}"
    elif kind == "elevation":
        name = view["arguments"].get("view", "front")
        look = _axis(recipe["look"])
        if name == "top":
            title, subtitle = "TOP VIEW", "Orthographic, looking down; not a cut plan"
        else:
            title, subtitle = f"{name.upper()} ELEVATION", f"Orthographic, looking {look}"
        scale = recipe["scale"]
    else:
        scale = recipe["scale"]
        title, subtitle = "SECTION PERSPECTIVE" + cut, f"Cut plane at {scale}; depth in perspective, not to scale"
        scale = f"{scale} at the cut"
    return (view.get("title") or title, subtitle if view.get("subtitle") is None else view["subtitle"], scale)


def _section_line(kind: str, recipe: Mapping[str, Any]):
    """A section view's plane in plan: a point on it and the horizontal direction toward its kept side."""

    if kind == "section":
        return recipe["frame"]["origin"], recipe["frame"]["look"]
    if kind == "section-perspective":
        normal = recipe["section"]["normal"]
        if abs(normal[2]) > 1e-9:
            return None
        return recipe["section"]["origin"], [-value for value in normal]
    return None


def _section_mark(view_id: str, label: str, section, plan_id: str, plan: Mapping[str, Any], unit: str):
    """Where a vertical section plane crosses a placed horizontal plan, in that plan's own paper mm."""

    from monkeydiagram.documentation.styles import SheetSectionMark

    origin, look = section
    length = math.hypot(look[0], look[1])
    lx, ly = look[0] / length, look[1] / length
    frame = plan["frame"]
    u0, v0, u1, v1 = frame["crop_uv"]
    mm_per_unit = UNIT_METRES[unit] * 1000 / int(frame["scale"].split(":")[1])
    along = (-ly, lx)
    low, high = -math.inf, math.inf
    plane = (origin[0] - frame["origin"][0], origin[1] - frame["origin"][1])
    for point, direction, (bottom, top) in zip(plane, along, ((u0, u1), (v0, v1))):
        if abs(direction) <= 1e-12:
            if not bottom <= point <= top:
                low, high = 1.0, 0.0
            continue
        first, second = (bottom - point) / direction, (top - point) / direction
        low, high = max(low, min(first, second)), min(high, max(first, second))
    if not low < high:
        raise StudioError(422, "DRAWING_SECTION_MARK_OUTSIDE",
                          f"Section {label} ({view_id}) does not cross the plan's window; mark it on a plan it cuts.")
    # The plan's u and v are X and Y measured from its frame origin.
    ends = [(plane[0] + along[0] * t, plane[1] + along[1] * t) for t in (low, high)]
    paper = [((u - u0) * mm_per_unit, (v1 - v) * mm_per_unit) for u, v in ends]
    return SheetSectionMark(view_id=plan_id, start_mm=paper[0], end_mm=paper[1], look_mm=(lx, -ly), label=label)


def _source_text(binding, model_source, source, unit: str) -> str:
    """The sheet's statement of its one source, from the registration or receipt that names it."""

    if isinstance(source, NativeModelSource):
        registration = binding.repository.load_json(source.registration)
        name = registration.get("sourceFileName") or registration.get("fileName") or "imported model"
        provider = (registration.get("conversion") or {}).get("provider")
        via = f", read through {provider}" if provider else ""
        return f"Source: {name}{via}; model sha256 {source.artifact.sha256[:12]}; lengths in {_UNIT_NAMES[unit]}"
    return (f"Source: model {model_source.run_id}, state {model_source.state_digest[:12]}; exact STEP sha256 "
            f"{source.step_sha256[:12]}; lengths in {_UNIT_NAMES[unit]}")


def _sheet_view_document(binding, view: Mapping[str, Any], sources: Mapping[str, Any], *, attribution, monitor,
                         projections) -> SourceDocument:
    """One placed view drawn, or read back, through its own generator from the sheet's one source."""

    arguments = {**view["arguments"], "drawing_id": view["id"], **sources}
    if view["kind"] == "plan":
        from .drawing_plans import generate_plan

        if attribution is None:
            raise ValueError("A plan on a sheet is drawn for the request's actor")
        return generate_plan(binding, attribution=attribution, **arguments)
    if view["kind"] == "elevation":
        return generate_elevation(binding, monitor=monitor, projections=projections, **arguments)
    if view["kind"] == "section-perspective":
        return generate_section_perspective(binding, monitor=monitor, projections=projections, **arguments)
    raise ValueError(f"unknown sheet view kind {view['kind']!r}")


def _view_sheet_scene(style_id, recipe, placed, marks, fonts):
    """The view sheet's paper scene from its recipe and the retained views' marks; writes nothing."""

    from monkeydiagram.documentation.styles import SheetView, compose_view_sheet

    views = tuple(SheetView(view_id=row["id"], size_mm=tuple(row["sizeMm"]), marks=placed[row["id"]],
                            place_mm=tuple(row["placeMm"]), title=row["title"], subtitle=row["subtitle"],
                            scale_label=row["scaleLabel"]) for row in recipe["views"])
    return compose_view_sheet(style_id=style_id, paper_size_mm=tuple(recipe["paperSizeMm"]), views=views,
                              title=recipe["title"], sheet_number=recipe["sheetNumber"], subtitle=recipe["subtitle"],
                              notes=tuple(recipe["notes"]), source_text=recipe["sourceText"], section_marks=marks,
                              font_mapping=fonts)


def _view_sheet(binding, *, source_stage_ref, model_source, source_asset, style_id, views, paper_size_mm, title, subtitle,
                sheet_number, drawing_id, notes, length_unit, attribution, monitor, projections) -> SourceDocument:
    """Same-source views, each through its own generator, placed at its own scale where the caller put it.

    Every view is drawn from the sheet's one source (its modelSource, Stage or
    imported asset, never a borrowed one): an identical view reads its
    registered revision back, which the sheet then names exactly. Each view's
    retained SVG is placed as its paper marks, never re-projected. A view that
    fails, draws nothing or cannot be placed refuses the sheet by name; no
    sheet is registered and the model and HEAD are untouched. The views drawn
    before the refusal stay registered as their own generators registered them,
    as does a view refused here for drawing nothing; the same request again
    reads them back instead of drawing them again.
    """

    from monkeydiagram.documentation.styles import drawing_style

    selected, stage_ref = _selected_source(binding, source_stage_ref, model_source, source_asset)
    source, receipt = _complete_source(binding, selected, stage_ref)
    unit = receipt["identity"]["length_unit"]
    require_unit(length_unit, unit)
    try:
        style = drawing_style(style_id)
    except ValueError as exc:
        raise StudioError(422, "DRAWING_STYLE_UNKNOWN", str(exc)) from exc
    number = sheet_number or "01"
    drawing_id = drawing_id or f"sheet-{number}"
    require_identifier(drawing_id, "drawing_id")
    # A view's id is its own drawing's; the sheet cannot share it, nor take another kind's (checked again as it registers).
    if any(view["id"] == drawing_id for view in views):
        raise StudioError(409, "DRAWING_KIND_CHANGED", f"View {drawing_id} and its sheet cannot share one drawing id; "
                                                       "give the sheet or that view another.")
    refuse_other_kind(binding, drawing_id, "view-sheet", "view sheet")
    fonts = _sheet_fonts()
    sources = {"source_stage_ref": source_stage_ref, "model_source": model_source, "source_asset": source_asset}
    rows, placed, kinds, recipes = [], {}, {}, {}
    for view in views:
        try:
            document = _sheet_view_document(binding, view, sources, attribution=attribution, monitor=monitor,
                                            projections=projections)
        except StudioError as exc:
            raise StudioError(exc.status, exc.code, f"View {view['id']}: {exc.detail}") from exc
        if _document_source(document) != selected or document.revision_ref is None:
            raise StudioError(409, "DRAWING_SOURCE_MISMATCH", f"View {view['id']} was not drawn from the sheet's source.")
        drawing = read_model_axis_elevation(binding.repository, record_ref_from_uri(document.revision_ref, binding.project_id))
        try:
            size, marks = svg_paper_marks(drawing.svg)
        except DrawingSvgError as exc:
            raise StudioError(422, "DRAWING_SHEET_VIEW_UNSUPPORTED", f"View {view['id']}: {exc}") from exc
        if not marks:
            raise StudioError(422, "DRAWING_VIEW_EMPTY", f"View {view['id']} draws nothing in its window; move its window "
                                                         "or its cut onto the model.")
        recipe = drawing.receipt["view"]
        kind = _sheet_view_kind(view, recipe)
        view_title, view_subtitle, scale = _sheet_view_labels(view, kind, recipe, unit)
        row = {"id": view["id"], "kind": kind, "placeMm": list(view["place_mm"]), "sizeMm": list(size),
               "title": view_title, "subtitle": view_subtitle, "scaleLabel": scale, "runId": document.run_id,
               "assetSha256": document.asset_sha256, "revisionRef": document.revision_ref}
        if view.get("mark_on") is not None:
            row["mark"] = {"on": view["mark_on"], "label": view["mark_label"]}
        rows.append(row)
        placed[view["id"]], kinds[view["id"]], recipes[view["id"]] = marks, kind, recipe
    section_marks = []
    for row in rows:
        if "mark" not in row:
            continue
        target = row["mark"]["on"]
        section = _section_line(kinds[row["id"]], recipes[row["id"]])
        if section is None:
            raise StudioError(422, "DRAWING_SECTION_MARK_INVALID", f"View {row['id']} has no vertical plane to mark on a plan.")
        if kinds[target] != "plan":
            raise StudioError(422, "DRAWING_SECTION_MARK_INVALID", f"View {row['id']} is marked on {target}, which is not a "
                                                                   "horizontal cut plan.")
        mark = _section_mark(row["id"], row["mark"]["label"], section, target, recipes[target], unit)
        if mark not in section_marks:  # a section and its perspective through one plane mark it once
            section_marks.append(mark)
    recipe = {
        "kind": "view-sheet", "style": style, "paperSizeMm": list(paper_size_mm or style["paperSizeMm"]),
        "title": title or binding.project_id, "subtitle": subtitle or "", "sheetNumber": number, "notes": list(notes),
        "views": rows, "sourceText": _source_text(binding, selected, source, unit),
        "source": {"modelSource": None if isinstance(selected, DrawingAssetSource) else selected.to_dict(),
                   "sourceStageRef": None if stage_ref is None else stage_ref.uri,
                   "modelSha256" if isinstance(source, NativeModelSource) else "stepSha256": _source_digest(source),
                   "cadReceiptRef": _source_ref(binding, source).uri},
        "fonts": {name: path.name for name, path in fonts.items()},
    }
    if isinstance(selected, DrawingAssetSource):
        recipe.update(sourceAsset=selected.to_dict(), follow="frozen")
    recipe_json = canonical_json(recipe, ascii=False)
    recipe = json.loads(recipe_json)
    with monitor.measure("drawing_generate", project_id=binding.project_id, run_id=selected.run_id,
                         source_ref=recipe["source"]["sourceStageRef"] or recipe["source"]["cadReceiptRef"],
                         details={"scope": "view_sheet", "input_identity": {"view_recipe": {
                                      "style_id": style_id, "views": [row["id"] for row in rows]}},
                                  "cache_status": "unknown"}) as operation:
        with _document_source_lock:
            for document in list_documents(binding, selected.run_id):
                if _document_source(document) == selected and document.view_recipe == recipe:
                    document_bytes(binding, document.run_id, document.asset_sha256)
                    operation["details"].update(cache_status="hit", execution_path="retained_drawing")
                    return document
        operation["details"].update(cache_status="miss", execution_path="view_sheet")

        def draw():
            try:
                canvas = _view_sheet_scene(style_id, recipe, placed, tuple(section_marks), fonts)
            except ValueError as exc:
                raise StudioError(422, "DRAWING_SHEET_LAYOUT_INVALID", str(exc)) from exc
            try:
                return _sheet_files(canvas, recipe_json), {}
            except ValueError as exc:
                raise StudioError(422, "DRAWING_GENERATION_FAILED", str(exc)) from exc

        if projections is None:
            files = draw()[0]
        else:
            files, _, _ = projections.on_demand(on_demand_spec(SHEET, _source_of(selected), {"recipe": recipe}), draw)
        document = _retain_sheet(binding, selected, stage_ref, files, file_name=f"{drawing_id}.pdf",
                                 drawing_id=drawing_id, recipe=recipe)
        operation["details"]["output_refs"] = [document.revision_ref or document.asset_sha256]
        return document


def drawing_file(binding: ProjectBinding, *, run_id: str, asset_sha256: str, revision_ref: str | None,
                 file_format: str) -> tuple[bytes, str, str]:
    """One file of a registered drawing: a view's SVG or PNG, or a sheet's PDF, DXF, SVG or PNG; read, never drawn.

    A view revision's files are its receipt's, verified by their digests, and
    are read only by that exact revision (DRAWING_REVISION_REQUIRED): two
    revisions can share a PNG and differ in SVG, and the answer is immutable.
    A sheet's PDF is its registered document; its DXF, SVG and PNG are served
    only when that PDF names their digests and the retained bytes still match
    them. Returns the bytes, media type and file name.
    """

    document, data = document_bytes(binding, run_id, asset_sha256, revision_ref)
    stem = Path(document.file_name).stem or (document.drawing_id or "drawing")
    if document.revision_ref is not None and revision_ref is None:
        raise StudioError(422, "DRAWING_REVISION_REQUIRED", "A view drawing's files are read by its revisionRef, as the "
                                                            "documents list names it.")
    if document.revision_ref is not None:
        try:
            drawing = read_model_axis_elevation(binding.repository, record_ref_from_uri(document.revision_ref, binding.project_id))
        except (DrawingElevationError, TypeError, ValueError) as exc:
            raise StudioError(409, "DOCUMENT_UNAVAILABLE", "The retained drawing revision cannot be read.") from exc
        if file_format in ("svg", "png"):
            return (drawing.svg, SVG_MEDIA_TYPE, f"{stem}.svg") if file_format == "svg" else (drawing.png, PNG_MEDIA_TYPE, f"{stem}.png")
        raise StudioError(404, "DRAWING_FILE_UNAVAILABLE", "A view drawing keeps SVG and PNG; PDF and DXF belong to a sheet.")
    if (document.view_recipe or {}).get("kind") not in SHEET_KINDS or document.mime_type != "application/pdf":
        raise StudioError(404, "DRAWING_FILE_UNAVAILABLE", "This document is not a retained drawing or sheet.")
    if file_format == "pdf":
        return data, "application/pdf", document.file_name
    from pypdf import PdfReader

    try:
        named = json.loads(PdfReader(BytesIO(data)).metadata.get(SHEET_FILE_DIGESTS) or "null")
    except (AttributeError, TypeError, ValueError):
        named = None
    if not isinstance(named, dict) or not isinstance(named.get(file_format), str):
        raise StudioError(404, "DRAWING_FILE_UNAVAILABLE", "This sheet was retained before its PDF named its DXF, SVG and "
                                                           "PNG; its PDF is its registered file.")
    path = binding.repository.layout.run(document.run_id).workspaces / "documentation" / asset_sha256 / f"sheet.{file_format}"
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise StudioError(409, "DOCUMENT_UNAVAILABLE", f"The sheet's retained {file_format.upper()} cannot be read.") from exc
    if hashlib.sha256(content).hexdigest() != named[file_format]:
        raise StudioError(409, "DOCUMENT_DIGEST_MISMATCH", f"The sheet's {file_format.upper()} no longer matches the digest "
                                                           "its PDF names.")
    return content, dict(SHEET_FILES)[file_format], f"{stem}.{file_format}"
