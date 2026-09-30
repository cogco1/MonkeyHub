"""Drawing runs: a drawn view retained through P036 as SVG, PNG and ``DrawingProjectionReceipt@1``, read back cold.

The A0 drawing slice of ``docs/design/drawing-system.md``, as far as it is
real: a verified source (``sources``) is drawn (``projection.views``), and the
two files plus one receipt are retained in a *drawing run* whose base is the
source run's base. This is the package's only module with project write
authority, and it writes only through the repository's ports.

What this module decides and nothing else:

- the drawing run is created with ``base = source run base`` or, when it
  exists, is used only if it already carries that base;
- nothing is written until the projection and both renderings succeeded;
  the files and the receipt are read back through the repository before
  the result is reported, and the project's HEAD must be the same after as
  before;
- the receipt records the source binding, the view as applied and what the
  cleanup removed.

``freeze_model_axis_elevation`` retains a model-axis elevation or an
axonometric; ``freeze_cut_plan`` adds a real horizontal plane section and
below-cut visibility to that same retention boundary, with caller-resolved
dimensions; ``freeze_section_perspective`` retains a section perspective.
There is no second storage, design mutation or update mechanism here. The
receipt's version-ref declaration registers when this module is imported;
``archflow.project.version_ref_owners`` names it among the workflow owners.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping

from monkeycad.backends.occt.errors import OcctBackendError
from monkeycad.backends.occt.kernel import backend_identity
from monkeycad.backends.occt.projection import project_occt_lines
from monkeycad.backends.occt.section import section_occt_lines, section_occt_regions
from monkeydiagram.projection.views import (
    ELEVATION_KIND,
    SECTION_PERSPECTIVE_KIND,
    ElevationProjection,
    ElevationView,
    SectionPerspectiveView,
    _cleaned,
    _finite,
    _observed_stage,
    _refuse,
    project_model_axis_elevation,
    project_section_perspective,
    section_perspective_objects,
)
from monkeydiagram.rendering.svg import (
    PNG_MEDIA_TYPE,
    SVG_MEDIA_TYPE,
    DrawingSvgError,
    drawing_svg,
    render_svg_png,
)
from monkeydiagram.sources import (
    STEP_MEDIA_TYPE,
    DrawingElevationError,
    ElevationSource,
    NativeModelSource,
    VerifiedElevationSource,
    _artifact_bytes,
    _require,
    current_object_id,
    inspection_witness_ids,
    object_semantics,
    read_elevation_source,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DRAWING_PROJECTION_RECEIPT
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, RunRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from archflow.project.version_refs import (
    register as _register_version_refs,
    register_derived as _register_derived_fields,
)

DRAWING_PROJECTION_RECEIPT_SCHEMA = "DrawingProjectionReceipt@1"
CUT_PLAN_KIND = "cut-plan"
#: The view kinds one drawing receipt may record; each is frozen by its own function here.
DRAWING_VIEW_KINDS = (ELEVATION_KIND, CUT_PLAN_KIND, SECTION_PERSPECTIVE_KIND)
DOCUMENTATION_WORKSPACE = "documentation"


@dataclass(frozen=True, slots=True)
class ElevationDrawing:
    """One retained drawing: its run, receipt and the two files, as read back through the repository."""

    run: RunRef
    receipt_ref: ProjectRecordRef
    receipt: Mapping[str, Any]
    svg_ref: ProjectArtifactRef
    png_ref: ProjectArtifactRef
    svg: bytes
    png: bytes

    @property
    def cleanup(self) -> Mapping[str, Any] | None:
        """The receipt's ``CleanupReport`` counts; None for a drawing retained before cleanup was recorded."""

        return self.receipt.get("cleanup")

    @property
    def attribution(self) -> Mapping[str, Any] | None:
        """Who asked for this revision, as the application stated it; None when it was not recorded."""

        return self.receipt.get("attribution")

    @property
    def reason(self) -> str | None:
        """Why this revision was asked for; None when no reason was recorded."""

        return self.receipt.get("reason")

    @property
    def source_kind(self) -> str | None:
        """What kind of asker the application said asked, such as a person or an agent; None when it did not say."""

        return self.receipt.get("sourceKind")


def _drawing_run(repository: FilesystemProjectRepository, drawing_run_id: str, source_run: RunRef) -> RunRef:
    try:
        require_identifier(drawing_run_id, "drawing_run_id")
    except ValueError as exc:
        raise DrawingElevationError(str(exc)) from exc
    _require(drawing_run_id != source_run.run_id, "the drawing run must not be the source run")
    try:
        if repository.layout.run(drawing_run_id).manifest.exists():
            run = repository.load_run(drawing_run_id)
            _require(run.base == source_run.base,
                     f"drawing run {drawing_run_id} exists with another base; choose another run id")
            return run
        return repository.create_run(drawing_run_id, base=source_run.base)
    except ProjectRepositoryError as exc:
        raise DrawingElevationError(f"drawing run {drawing_run_id} cannot be used: {exc}") from exc


def _ref_dict(ref: ProjectArtifactRef) -> dict[str, str]:
    return {"artifact_id": ref.artifact_id, "relative_path": ref.relative_path,
            "sha256": ref.sha256, "media_type": ref.media_type}


def _artifact_ref(project_id: str, value: Mapping[str, Any]) -> ProjectArtifactRef:
    return ProjectArtifactRef(project_id, value["artifact_id"], value["relative_path"], value["sha256"], value["media_type"])


def _source_binding(source, verified):
    common = {"run_id": verified.run.run_id, "base": verified.run.base.to_dict(),
              "physical_object_ids": list(verified.physical_object_ids)}
    if isinstance(source, NativeModelSource):
        return {**common, "model": _ref_dict(source.artifact),
                "registration": source.registration.to_dict(),
                "object_identity": "3DM object GUID path including block instances",
                "geometry_quality": {entry.name: entry.geometry_quality for entry in verified.entries},
                **({"sourceImport": deepcopy(verified.receipt["sourceImport"])} if "sourceImport" in verified.receipt else {})}
    return {**common, "stage_id": verified.stage_id, "program_digest": verified.program_digest,
            "step": {"relative_path": source.step_relative_path, "sha256": source.step_sha256,
                     "media_type": STEP_MEDIA_TYPE},
            "cad_receipt": {"relative_path": source.cad_receipt_relative_path, "sha256": source.cad_receipt_sha256},
            "object_identity": "STEP shape name = CAD receipt physical object id"}


def _revision_provenance(attribution, reason, source_kind=None) -> dict[str, Any]:
    """Who asked for a drawing revision and why, as the application states them; checked before anything is drawn.

    ``attribution`` is a flat mapping of JSON values, such as the Studio's
    ``{"actorId", "authenticated", "origin"}``, or a dataclass of them, whose
    field names are then written in camelCase.  ``reason`` is the words the
    revision was asked with, and ``source_kind`` the kind of asker the
    request says it came from, such as the Studio's ``human`` or ``agent``.
    None of them is interpreted here, but each must be text a receipt can
    hold, so a refusal comes before any file is written.  What is not given
    is not written, so an earlier receipt and a revision without them both
    read as None.
    """

    provenance: dict[str, Any] = {}
    if attribution is not None:
        if is_dataclass(attribution) and not isinstance(attribution, type):
            attribution = {re.sub(r"_([a-z])", lambda match: match.group(1).upper(), key): value
                           for key, value in asdict(attribution).items()}
        if (not isinstance(attribution, Mapping) or not attribution
                or any(not isinstance(key, str) or not key for key in attribution)
                or any(not (value is None or isinstance(value, (str, bool))
                            or (isinstance(value, (int, float)) and math.isfinite(value)))
                       for value in attribution.values())):
            raise DrawingElevationError("attribution must be a flat mapping of names to text, true/false or numbers")
        provenance["attribution"] = dict(attribution)
    for key, text in (("reason", reason), ("sourceKind", source_kind)):
        if text is not None:
            if not isinstance(text, str):
                raise DrawingElevationError(f"{key} must be text")
            if text.strip():
                provenance[key] = text
    texts = [reason or "", source_kind or "", *(key for key in provenance.get("attribution", {})),
             *(value for value in provenance.get("attribution", {}).values() if isinstance(value, str))]
    try:
        for text in texts:
            text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DrawingElevationError("attribution, reason and sourceKind must be text a receipt can hold") from exc
    return provenance


@dataclass(frozen=True, slots=True)
class DrawnView:
    """One drawn view before it is retained: its two files and what the receipt says about the drawing.

    Everything here follows from the verified source's content and the view,
    not from the source's run or base: ``freeze_*`` retains it once drawn, and
    a cache may keep it and hand the same bytes to another retention of the
    same content, so that view is not solved again (the ``cache`` argument).
    """

    view: Mapping[str, Any]
    svg: bytes
    png: bytes
    counts: Mapping[str, Any]
    cleanup: Mapping[str, Any] | None
    details: Mapping[str, Any]
    backend: Mapping[str, Any]

    def facts(self) -> dict[str, Any]:
        """Everything but the two files, as JSON values."""

        return {"view": deepcopy(dict(self.view)), "counts": dict(self.counts),
                "cleanup": None if self.cleanup is None else dict(self.cleanup),
                "details": deepcopy(dict(self.details)), "backend": dict(self.backend)}

    @classmethod
    def from_facts(cls, facts: Mapping[str, Any], *, svg: bytes, png: bytes) -> DrawnView:
        return cls(facts["view"], svg, png, facts["counts"], facts["cleanup"], facts["details"], facts["backend"])


#: What a cache does with a drawing: given the verified source and the function drawing
#: it, the drawn view, from the cache or by calling the function once.
DrawingCache = Callable[[VerifiedElevationSource, Callable[[], DrawnView]], DrawnView]


def _drawn(view: Mapping[str, Any], projection, backend, details=None) -> DrawnView:
    return DrawnView(view, projection.svg, projection.png, projection.counts(),
                     None if projection.cleanup is None else projection.cleanup.to_dict(), details or {}, backend)


def _retain_projection(repository, *, source, verified, drawn: DrawnView, name, drawing_run_id,
                       head_before, previous_revision_ref=None, provenance=None):
    """The one receipt/artifact boundary for elevation, cut-plan and section-perspective projections."""
    view = drawn.view
    if isinstance(source, NativeModelSource) and verified.receipt.get("modelSource") is None:
        view = {**view, "sourceAsset": {"runId": source.run_id, "assetSha256": source.artifact.sha256}, "follow": "frozen"}
    run = _drawing_run(repository, drawing_run_id, verified.run)
    destination = PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run.run_id)
    try:
        svg_ref = repository.put_workspace_file(
            run=run, destination=destination, artifact_id=f"{name}-svg",
            workspace_relative_path=f"{DOCUMENTATION_WORKSPACE}/{name}.svg",
            media_type=SVG_MEDIA_TYPE, source=BytesIO(drawn.svg),
        )
        png_ref = repository.put_workspace_file(
            run=run, destination=destination, artifact_id=f"{name}-png",
            workspace_relative_path=f"{DOCUMENTATION_WORKSPACE}/{name}.png",
            media_type=PNG_MEDIA_TYPE, source=BytesIO(drawn.png),
        )
        payload = {
            "schema": DRAWING_PROJECTION_RECEIPT_SCHEMA,
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": run.base.to_dict(),
            "view": view,
            "unit": verified.length_unit,
            "source": _source_binding(source, verified),
            "projection": {
                "backend": dict(drawn.backend),
                "algorithm": "HLRBRep_Algo exact hidden-line solve over every listed object, then per-object extraction",
                "object_count": len(verified.physical_object_ids),
                **drawn.counts,
                **drawn.details,
            },
            "artifacts": {"svg": _ref_dict(svg_ref), "png": _ref_dict(png_ref)},
        }
        # What the cleanup removed describes this drawing, not its recipe: it is kept here only.
        if drawn.cleanup is not None:
            payload["cleanup"] = dict(drawn.cleanup)
        if previous_revision_ref is not None:
            payload["previousRevisionRef"] = previous_revision_ref
        payload.update(provenance or {})
        receipt_ref = repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=DRAWING_PROJECTION_RECEIPT, payload=payload,
        )
    except ProjectRepositoryError as exc:
        raise DrawingElevationError(f"the drawing could not be retained in run {run.run_id}: {exc}") from exc
    drawing = read_model_axis_elevation(repository, receipt_ref)
    _require(drawing.svg == drawn.svg and drawing.png == drawn.png,
             "the retained drawing files read back differently from what was written")
    _require(repository.read_head() == head_before, "the project's published version changed while drawing")
    return drawing


def plan_dressing_anchors(receipt: Mapping) -> list[dict]:
    """Exact physical-object centres projected in the cut plan's CAD XY frame."""
    semantics = receipt.get("expected_semantics", {}).get("objects", {})
    return [{"objectId": name, "positionUv": [(receipt["readback"][name]["bbox"]["min"][i]
                + receipt["readback"][name]["bbox"]["max"][i]) / 2 for i in (0, 1)]}
            for name in receipt["physical_object_ids"] if name in receipt.get("readback", {}) and semantics.get(name, {}).get("visible", True)]


def resolve_plan_dressing(recipe: Mapping, receipt: Mapping) -> list[dict]:
    """Keep representation intent; missing exact anchors are never rebound."""
    from monkeydiagram.rendering.svg import dressing_assets
    assets = {asset["id"] for asset in dressing_assets()}
    anchors = {row["objectId"]: row["positionUv"] for row in plan_dressing_anchors(receipt)}
    crop = recipe["frame"]["crop_uv"]
    result, seen = [], set()
    for item in recipe.get("dressing", []):
        name = item["id"]
        require_identifier(name, "dressing id")
        _require(name not in seen and item["assetId"] in assets, "dressing needs unique ids and a supported SVG asset")
        seen.add(name)
        position = item["positionUv"]
        _require(len(position) == 2, "dressing positionUv needs two view coordinates")
        u, v = (_finite(value, "dressing coordinate") for value in position)
        size = _finite(item["size"], "dressing size")
        _require(0 < size <= 100000 and isinstance(item.get("flipped", False), bool), "invalid dressing size or flip")
        anchor = item.get("anchorObjectId")
        if anchor is not None:
            anchor = current_object_id(anchor, anchors)
        if anchor is not None and anchor not in anchors:
            result.append({**item, "status": "missing", "resolvedUv": None,
                           "detail": f"Anchor {anchor} is missing or unavailable; this object has not been moved to another anchor."})
            continue
        if anchor is not None:
            u += anchors[anchor][0]
            v += anchors[anchor][1]
        fits = crop[0] <= u-size/2 and u+size/2 <= crop[2] and crop[1] <= v-size/2 and v+size/2 <= crop[3]
        result.append({**item, "status": "resolved" if fits else "outside-view", "resolvedUv": [u, v],
                       "detail": None if fits else "The symbol falls outside the drawing crop; move it or enlarge the crop."})
    return result


def freeze_cut_plan(
    repository: FilesystemProjectRepository, *, source: ElevationSource | NativeModelSource, recipe: Mapping,
    drawing_run_id: str, dimensions: tuple[Mapping, ...] = (), previous_revision_ref: str | None = None,
    attribution: Mapping[str, Any] | None = None, reason: str | None = None, source_kind: str | None = None,
) -> ElevationDrawing:
    """Retain a horizontal (or vertical model-axis) section and the exact beyond-cut visibility in the existing drawing envelope.

    ``recipe`` retains representation intent; ``dimensions`` contains the application's resolved
    source measurements or explicit unresolved statuses. Neither can change the source model.
    Coordinates are the verified STEP's CAD Z-up frame and length unit, never Program Y-up.
    ``attribution``, ``reason`` and ``source_kind`` say who asked for this revision, why and as
    what kind of asker (``_revision_provenance``).
    """
    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    provenance = _revision_provenance(attribution, reason, source_kind)
    try:
        recipe = deepcopy(dict(recipe))
        _require(recipe.get("kind") == CUT_PLAN_KIND, "the view recipe must be a cut-plan")
        require_identifier(recipe["name"], "cut-plan name")
        frame = recipe["frame"]
        scale = frame["scale"]
        _require(isinstance(scale, str) and re.fullmatch(r"1:[1-9][0-9]*", scale) is not None,
                 "the cut-plan scale must be 1:N")
        view = ElevationView(
            name=recipe["name"], origin=frame["origin"], look=frame["look"], right=frame["right"], up=frame["up"],
            crop_uv=frame["crop_uv"], near_depth=frame["near_depth"], far_depth=frame["far_depth"],
            hidden_lines=frame["hidden_lines"], linear_deflection=frame["linear_deflection"],
            scale_denominator=int(scale[2:]),
        )
        horizontal = view.right == (1, 0, 0) and view.up == (0, 1, 0) and view.look == (0, 0, -1)
        # A vertical section is the same composition on a plane containing CAD +Z, seen along a plan axis.
        vertical = view.up == (0, 0, 1) and view.look in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0))
        _require((horizontal or vertical) and view.near_depth == 0,
                 "a cut-plan must look down CAD -Z from its horizontal cut plane, "
                 "or along a plan axis from a vertical cut plane with CAD +Z up")
        graphics = recipe["graphics"]
        _require(isinstance(graphics, Mapping), "cut-plan graphics must be a mapping")
        resolved = deepcopy(list(dimensions))
        ids = [row["id"] for row in resolved]
        _require(all(isinstance(name, str) and name for name in ids) and len(ids) == len(set(ids)),
                 "cut-plan dimensions must have unique ids")
        _require(all(row["status"] in {"resolved", "missing", "ambiguous", "outside-view", "unverified"} for row in resolved),
                 "cut-plan dimensions need explicit resolution statuses")
    except (KeyError, TypeError, ValueError) as exc:
        raise DrawingElevationError(f"the cut-plan recipe is invalid: {exc}") from exc
    head_before = repository.read_head()
    verified = read_elevation_source(repository, source)
    dressing = resolve_plan_dressing(recipe, verified.receipt)
    hidden = recipe.get("hiddenObjectIds", [])
    if not isinstance(hidden, (list, tuple)) or any(not isinstance(name, str) for name in hidden):
        raise DrawingElevationError("hiddenObjectIds must be a list of physical object ids")
    hidden = [current_object_id(name, set(verified.physical_object_ids)) for name in hidden]
    # Rebuilding keeps authored visibility intent even when its source object was removed.
    # The application validates newly authored selections; this retained recipe reports missing ones.
    unresolved_objects = sorted(set(hidden) - set(verified.physical_object_ids))
    source_hidden = inspection_witness_ids(verified.receipt)
    # Native STEP also retains hidden inspection witnesses such as aperture volumes.
    # They are source evidence, not cut material or occluders in the drawing.
    excluded = set(hidden) | source_hidden
    selected = tuple(name for name in verified.physical_object_ids if name not in excluded)
    _require(bool(selected), "a cut-plan must retain at least one physical object")
    try:
        common = dict(object_ids=selected, origin=view.origin, right=view.right, up=view.up,
                      linear_deflection=view.linear_deflection)
        sections = section_occt_lines(verified.entries, **common)
        if not horizontal and not sections:
            _refuse("SECTION_PLANE_MISSES_MODEL", "the vertical section plane meets none of the drawn objects; "
                                                  "move it through the model, or draw an elevation")
        regions = section_occt_regions(verified.entries, **common)
        background = project_occt_lines(verified.entries, **common, depth_range=(0, view.far_depth))
        lines = background + sections
        # The clipped slab's top edges lie on the cut; the cleanup leaves them to the section.
        cleaned, cleanup = _cleaned(lines, regions, crop_uv=view.crop_uv, hidden_lines=view.hidden_lines,
                                    unit=verified.length_unit, scale_denominator=view.scale_denominator)
        svg = drawing_svg(cleaned, crop_uv=view.crop_uv, unit=verified.length_unit,
                          scale_denominator=view.scale_denominator, hidden_lines=view.hidden_lines,
                          title=recipe["name"], regions=regions, graphics=graphics, dimensions=resolved, dressing=dressing,
                          semantics=object_semantics(verified.receipt))
        projection = ElevationProjection(lines=lines, svg=svg, png=render_svg_png(svg), cleanup=cleanup)
    except (OcctBackendError, DrawingSvgError) as exc:
        raise DrawingElevationError(f"cut-plan {view.name}: {exc}") from exc
    drawn = _drawn(recipe, projection, backend_identity(), {
        "algorithm": ("BRepAlgoAPI_Section on the cut plane; exact below-cut slab and global HLRBRep_Algo visibility"
                      if horizontal else
                      "BRepAlgoAPI_Section on the vertical cut plane; exact beyond-cut slab and global HLRBRep_Algo visibility"),
        "selected_object_ids": list(selected), "section_polylines": len(sections),
        "section_regions": len(regions), "dimensions": resolved, "unresolvedObjectIds": unresolved_objects,
        **({"dressing": dressing} if "dressing" in recipe else {}),
    })
    return _retain_projection(
        repository, source=source, verified=verified, drawn=drawn, name=view.name, drawing_run_id=drawing_run_id,
        head_before=head_before, previous_revision_ref=previous_revision_ref, provenance=provenance,
    )


def freeze_model_axis_elevation(
    repository: FilesystemProjectRepository, *, source: ElevationSource | NativeModelSource, view: ElevationView, drawing_run_id: str,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None, attribution: Mapping[str, Any] | None = None, reason: str | None = None,
    cache: DrawingCache | None = None,
) -> ElevationDrawing:
    """Project retained STEP or native 3DM and retain SVG, PNG and receipt in the drawing run.

    Refuses before any write when the source does not verify, the frame is
    inconsistent, or the drawing run exists with another base.  A repeat
    with the same source and view writes the same bytes to the same paths
    (the repository accepts identical content) and returns the same refs.
    ``attribution`` and ``reason`` say who asked and why (``_revision_provenance``).
    ``cache``, when given, is asked for the drawn view once the source has
    verified: it may answer from what it kept instead of solving the view.
    """

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(view, ElevationView):
        raise TypeError("view must be ElevationView")
    provenance = _revision_provenance(attribution, reason)
    identity = {"view_recipe": {key: value for key, value in view.to_dict().items()
                                if key not in {"uv_definition", "depth_definition"}}}
    if isinstance(source, ElevationSource):
        identity["step_sha256"] = source.step_sha256

    def observe(event):
        operation_observer({**event, "details": {"input_identity": dict(identity), **event.get("details", {})}})

    observer = observe if operation_observer is not None else None
    with _observed_stage(observer, "drawing.load", parent_event_id=parent_event_id) as observation:
        head_before = repository.read_head()
        verified = read_elevation_source(repository, source)
        backend = backend_identity()
        identity.update(backend=backend["binding"], backend_version=backend["binding_version"])
        observation["input_object_ids"] = list(verified.physical_object_ids)
    def draw() -> DrawnView:
        return _drawn(view.to_dict(), project_model_axis_elevation(
            verified.entries, object_ids=tuple(name for name in verified.physical_object_ids
                                               if name not in inspection_witness_ids(verified.receipt)),
            view=view, unit=verified.length_unit,
            operation_observer=observer, parent_event_id=parent_event_id, semantics=object_semantics(verified.receipt),
        ), backend)

    drawn = draw() if cache is None else cache(verified, draw)
    with _observed_stage(observer, "drawing.persist", parent_event_id=parent_event_id) as observation:
        drawing = _retain_projection(
            repository, source=source, verified=verified, drawn=drawn, name=view.name,
            drawing_run_id=drawing_run_id, head_before=head_before, provenance=provenance,
        )
        observation["output_refs"] = [drawing.receipt_ref.uri, drawing.svg_ref.uri, drawing.png_ref.uri]
    return drawing


def freeze_section_perspective(
    repository: FilesystemProjectRepository, *, source: ElevationSource | NativeModelSource, view: SectionPerspectiveView,
    drawing_run_id: str, operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None, attribution: Mapping[str, Any] | None = None, reason: str | None = None,
    cache: DrawingCache | None = None,
) -> ElevationDrawing:
    """Draw a section perspective of verified STEP or native geometry and retain SVG, PNG and receipt.

    Mirrors ``freeze_model_axis_elevation``: the request was checked when the
    view was made, the source is verified, nothing is written until the
    projection and both renderings succeeded, and ``_retain_projection``
    retains the receipt with the exact plane and camera.  A repeat with the
    same source and view writes the same bytes and returns the same refs.
    ``attribution`` and ``reason`` say who asked and why (``_revision_provenance``);
    ``cache`` is asked for the drawn view as in ``freeze_model_axis_elevation``.
    """

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(view, SectionPerspectiveView):
        raise TypeError("view must be SectionPerspectiveView")
    provenance = _revision_provenance(attribution, reason)
    identity = {"view_recipe": view.request()}
    if isinstance(source, ElevationSource):
        identity["step_sha256"] = source.step_sha256
    elif isinstance(source, NativeModelSource):
        identity["model_sha256"] = source.artifact.sha256

    def observe(event):
        operation_observer({**event, "details": {"input_identity": dict(identity), **event.get("details", {})}})

    observer = observe if operation_observer is not None else None
    with _observed_stage(observer, "drawing.load", parent_event_id=parent_event_id) as observation:
        head_before = repository.read_head()
        verified = read_elevation_source(repository, source)
        backend = backend_identity()
        identity.update(backend=backend["binding"], backend_version=backend["binding_version"])
        selected = section_perspective_objects(verified, view.hidden_object_ids)
        observation["input_object_ids"] = list(selected)
    def draw() -> DrawnView:
        projection = project_section_perspective(
            verified.entries, object_ids=selected, view=view, unit=verified.length_unit,
            operation_observer=observer, parent_event_id=parent_event_id, semantics=object_semantics(verified.receipt),
        )
        return _drawn(dict(projection.view), projection, backend, projection.details(selected))

    drawn = draw() if cache is None else cache(verified, draw)
    with _observed_stage(observer, "drawing.persist", parent_event_id=parent_event_id) as observation:
        drawing = _retain_projection(
            repository, source=source, verified=verified, drawn=drawn, name=view.name,
            drawing_run_id=drawing_run_id, head_before=head_before, provenance=provenance,
        )
        observation["output_refs"] = [drawing.receipt_ref.uri, drawing.svg_ref.uri, drawing.png_ref.uri]
    return drawing


def read_model_axis_elevation(repository: FilesystemProjectRepository, receipt_ref: ProjectRecordRef) -> ElevationDrawing:
    """Cold-read one retained drawing: receipt, SVG and PNG, each verified against its sha256."""

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(receipt_ref, ProjectRecordRef):
        raise TypeError("receipt_ref must be ProjectRecordRef")
    try:
        _require(receipt_ref.record_kind == DRAWING_PROJECTION_RECEIPT, "the record is not a drawing-projection-receipt")
        receipt = repository.load_json(receipt_ref)
        _require(receipt.get("schema") == DRAWING_PROJECTION_RECEIPT_SCHEMA, "the record is not a DrawingProjectionReceipt@1")
        _require(isinstance(receipt.get("view"), Mapping) and receipt["view"].get("kind") in DRAWING_VIEW_KINDS,
                 f"the drawing receipt's view is none of {', '.join(DRAWING_VIEW_KINDS)}")
        run = repository.load_run(receipt["run_id"])
        _require(run.base.to_dict() == receipt["base"] == receipt["source"]["base"],
                 "the drawing run, its receipt and the source disagree on the base")
        svg_ref = _artifact_ref(run.project_id, receipt["artifacts"]["svg"])
        png_ref = _artifact_ref(run.project_id, receipt["artifacts"]["png"])
        for ref in (svg_ref, png_ref):
            _require(PurePosixPath(ref.relative_path).parts[:4] == ("runs", run.run_id, "workspaces", DOCUMENTATION_WORKSPACE),
                     f"{ref.relative_path} is not in the drawing run's documentation workspace")
    except (ProjectRepositoryError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DrawingElevationError):
            raise
        raise DrawingElevationError(f"the drawing receipt cannot be read: {exc!r}") from exc
    return ElevationDrawing(
        run=run, receipt_ref=receipt_ref, receipt=receipt, svg_ref=svg_ref, png_ref=png_ref,
        svg=_artifact_bytes(repository, svg_ref), png=_artifact_bytes(repository, png_ref),
    )


def list_model_axis_elevations(repository: FilesystemProjectRepository, drawing_run_id: str) -> tuple[ProjectRecordRef, ...]:
    """The drawing receipts one run retains, of every view kind, verified as the repository lists them."""

    try:
        run = repository.load_run(drawing_run_id)
        refs = repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id))
    except (ProjectRepositoryError, ValueError) as exc:
        raise DrawingElevationError(f"drawing run {drawing_run_id} cannot be listed: {exc}") from exc
    return tuple(ref for ref in refs if ref.record_kind == DRAWING_PROJECTION_RECEIPT)


__all__ = [
    "CUT_PLAN_KIND",
    "DOCUMENTATION_WORKSPACE",
    "DRAWING_PROJECTION_RECEIPT_SCHEMA",
    "DRAWING_VIEW_KINDS",
    "DrawingCache",
    "DrawnView",
    "ElevationDrawing",
    "freeze_model_axis_elevation",
    "freeze_cut_plan",
    "freeze_section_perspective",
    "plan_dressing_anchors",
    "resolve_plan_dressing",
    "list_model_axis_elevations",
    "read_model_axis_elevation",
]


# A drawing receipt names the canonical version its run was based on, and the
# base of the verified CAD run its geometry was projected from. It derives
# nothing from either.
VERSION_REF_POINTERS = {"DrawingProjectionReceipt@1": ("/base", "/source/base")}

_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("DrawingProjectionReceipt@1", ())
