"""Perspective camera passes of one exact model: an object-ID image, a line image and a depth image (#19).

The view is a saved model view (the Render workspace's perspective camera,
``viewRecipe.kind`` "model-view") or an explicit camera; the model is the
saved view's own, or a model version or an imported asset (``sourceAsset``) as
the drawing routes take it. The three images are drawn from the model's
triangles against one depth buffer (``mesh_views.camera_passes``), so they line
up pixel for pixel, at up to 4096 pixels on the long edge, and each is
registered as a document beside the model with the camera, size and legend in
its ``viewRecipe``. A large model takes minutes, so the work is a background
job in its own P036 run ``camera-pass-<id>``, whose record goes queued,
running, then succeeded or failed. Reading, meshing and drawing the model run
in a drawing worker when the Runtime keeps a pool (``drawing_pool``), so
they hold up no other request; only retaining the images is done here.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping
import uuid

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_CAMERA_PASS
from archflow.project.repository import ProjectRepositoryError
from monkeycad.formats.three_dm_inspector import ThreeDmInspectionError
from monkeydiagram.drawing_runs import CameraTask, run_camera_task
from monkeydiagram.projection.mesh_views import (
    CAMERA_RENDERER_VERSION, DEPTH_NEAREST, MAX_CAMERA_EDGE_PX, MeshViewError, PerspectiveCamera,
)
from monkeydiagram.sources import DrawingElevationError, NativeModelSource

from ..authentication import ActorAttribution
from ..binding import ProjectBinding
from ..errors import StudioError
from . import artifacts, drawing_pool, drawings

RUN_PREFIX = "camera-pass-"
SCHEMA = "StudioCameraPass@1"
PASS_KIND = "camera-pass"
ID_BY = ("object", "layer", "material")
PASSES = ("id", "line", "depth")
STATUS_RANK = {"queued": 0, "running": 1, "failed": 2, "succeeded": 2}
#: Model units per pixel at the camera's distance to the model, as a share of a pixel, the surfaces are meshed at.
CHORD_PX = 0.75


def _invalid(detail: str) -> StudioError:
    return StudioError(422, "CAMERA_PASS_INVALID", detail)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(binding: ProjectBinding, run, payload: dict) -> None:
    try:
        binding.repository.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD,
                                                                            run_id=run.run_id),
                                    record_kind=STUDIO_CAMERA_PASS, payload=payload)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "CAMERA_PASS_WRITE_FAILED", "The camera pass record could not be retained.") from exc


def _saved_view(binding: ProjectBinding, view: Mapping[str, str]):
    """A saved model view's camera, size and model, or the refusal that says why it cannot be drawn."""

    run_id, asset = view.get("runId"), view.get("assetSha256")
    rows = [row for row in artifacts.list_documents(binding, run_id) if row.asset_sha256 == asset]
    if not rows:
        raise StudioError(404, "DOCUMENT_NOT_FOUND", f"Run {run_id} has no saved view {asset}.")
    document = rows[-1]
    recipe = document.view_recipe or {}
    camera = recipe.get("camera") or {}
    if recipe.get("kind") != "model-view" or camera.get("projection") != "perspective":
        raise _invalid("The document is not a saved perspective model view.")
    if document.model_source is None:
        raise _invalid("The saved view names no model.")
    try:
        chosen = PerspectiveCamera.from_matrices(camera.get("worldMatrix") or (), camera.get("projectionMatrix") or ())
        if camera.get("target") is not None:
            chosen = PerspectiveCamera(chosen.position, tuple(float(v) for v in camera["target"]),
                                       tuple(float(v) for v in camera.get("up") or chosen.up), chosen.fov_deg)
    except (MeshViewError, TypeError, ValueError) as exc:
        raise _invalid(f"The saved view's camera cannot be read: {exc}") from exc
    screen = recipe.get("screenSize") or (document.pages[0].width, document.pages[0].height)
    return chosen, (int(screen[0]), int(screen[1])), document


def _size(screen: tuple[int, int], long_edge: int) -> tuple[int, int]:
    width, height = screen
    if width < 1 or height < 1:
        raise _invalid("The view's size must be positive.")
    scale = long_edge / max(width, height)
    return max(16, round(width * scale)), max(16, round(height * scale))


def submit_camera_passes(
    binding: ProjectBinding, jobs, *, attribution: ActorAttribution, view: Mapping[str, str] | None = None,
    camera: Mapping[str, Any] | None = None, size: tuple[int, int] | None = None, long_edge: int = 2048,
    id_by: str = "layer", model_source=None, source_asset: Mapping[str, str] | None = None,
    source_stage_ref: str | None = None, clip: Mapping[str, Any] | None = None,
) -> dict:
    """Check the request, retain the queued run, and draw the passes as a background job."""

    if (view is None) == (camera is None):
        raise _invalid("Name one view: a saved model view, or a camera.")
    if id_by not in ID_BY:
        raise _invalid("Group the ID image by object, layer or material.")
    if isinstance(long_edge, bool) or not isinstance(long_edge, int) or not 16 <= long_edge <= MAX_CAMERA_EDGE_PX:
        raise _invalid(f"longEdge is a whole number of pixels from 16 to {MAX_CAMERA_EDGE_PX}.")
    if view is not None:
        if model_source is not None or source_asset is not None or source_stage_ref is not None:
            raise _invalid("A saved view draws its own model; name no other.")
        chosen, screen, document = _saved_view(binding, view)
        model_source, source_stage_ref = document.model_source, document.source_stage_ref
        if clip is None and isinstance((document.view_recipe or {}).get("clip"), Mapping):
            clip = document.view_recipe["clip"]
        origin = {"view": {"runId": document.run_id, "assetSha256": document.asset_sha256}}
    else:
        try:
            chosen = PerspectiveCamera(tuple(float(v) for v in camera["position"]), tuple(float(v) for v in camera["target"]),
                                       tuple(float(v) for v in camera.get("up") or (0.0, 0.0, 1.0)),
                                       float(camera.get("fovDeg", 45.0)))
            chosen.check()
        except (KeyError, TypeError, ValueError, MeshViewError) as exc:
            raise _invalid(f"A camera is {{position, target, up, fovDeg}} in the model's frame and unit: {exc}") from exc
        if size is None:
            raise _invalid("Give the size (width and height) the camera's view has.")
        screen = (int(size[0]), int(size[1]))
        origin = {}
    width, height = _size(screen, long_edge)
    if clip is not None:
        try:
            clip = {"origin": [float(v) for v in clip["origin"]], "normal": [float(v) for v in clip["normal"]]}
            if len(clip["origin"]) != 3 or len(clip["normal"]) != 3 or not any(clip["normal"]) \
                    or not all(math.isfinite(v) for v in clip["origin"] + clip["normal"]):
                raise ValueError("three finite numbers each, the normal not zero")
        except (KeyError, TypeError, ValueError) as exc:
            raise _invalid(f"A section clip is {{origin, normal}} in the model's frame: {exc}") from exc
    # Selects and checks the model now, so a refusal is the request's, not the job's.
    drawings.select_drawing_source(binding, model_source=model_source, source_stage_ref=source_stage_ref,
                                   source_asset=None if source_asset is None else dict(source_asset))
    pass_id = RUN_PREFIX + uuid.uuid4().hex[:12]
    try:
        run = binding.repository.create_run(pass_id)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "CAMERA_PASS_WRITE_FAILED", "The camera pass run could not be created.") from exc
    queued = {
        "schema": SCHEMA, "projectId": binding.project_id, "passId": pass_id, "status": "queued",
        "camera": chosen.to_dict(), "size": [width, height], "idBy": id_by, **origin,
        **({"clip": clip} if clip is not None else {}),
        "modelSource": None if model_source is None else model_source.to_dict(),
        "sourceAsset": None if source_asset is None else dict(source_asset), "sourceStageRef": source_stage_ref,
        "renderer": CAMERA_RENDERER_VERSION,
        "requestedBy": {"actorId": attribution.actor_id, "authenticated": attribution.authenticated,
                        "origin": attribution.origin},
        "submittedAt": _now(), "updatedAt": _now(), "statusPath": f"/api/render/views/passes/{pass_id}",
    }
    _save(binding, run, queued)

    def work():
        current = queued | {"status": "running", "startedAt": _now(), "updatedAt": _now()}
        _save(binding, run, current)
        try:
            result = _draw(binding, chosen, width, height, id_by, model_source, source_asset, source_stage_ref, queued)
        except StudioError as exc:
            _save(binding, run, current | {"status": "failed", "updatedAt": _now(), "failureCode": exc.code,
                                           "failureReason": exc.detail})
            raise
        except Exception as exc:
            _save(binding, run, current | {"status": "failed", "updatedAt": _now(), "failureCode": "CAMERA_PASS_FAILED",
                                           "failureReason": f"{type(exc).__name__}: {exc}"[:1500]})
            raise
        _save(binding, run, current | result | {"status": "succeeded", "updatedAt": _now()})

    try:
        jobs.submit(candidate_id=pass_id, proposal_id=pass_id, work=work, kind="camera-pass")
    except Exception as exc:
        _save(binding, run, queued | {"status": "failed", "updatedAt": _now(), "failureCode": "CAMERA_PASS_NOT_STARTED",
                                      "failureReason": f"{type(exc).__name__}: {exc}"[:1500]})
        raise
    return {"passId": pass_id, "status": "queued", "statusPath": queued["statusPath"], "size": [width, height]}


def _draw(binding, chosen: PerspectiveCamera, width, height, id_by, model_source, source_asset, source_stage_ref,
          queued: dict) -> dict:
    selected, source = drawings.camera_source(
        binding, model_source=model_source, source_asset=None if source_asset is None else dict(source_asset),
        source_stage_ref=source_stage_ref)
    groups_from = None
    if id_by != "object":
        if id_by not in ID_BY:
            raise _invalid("Group the ID image by object, layer or material.")
        groups_from = str(artifacts.artifact_bytes(binding, selected.asset_sha256, run_id=selected.run_id)[0].path)
    clip = queued.get("clip")
    # Meshed at a pixel's footprint where the model's middle stands from the eye.
    task = CameraTask(str(binding.repository.layout.root), source, chosen, width, height, id_by=id_by,
                      groups_from=groups_from, groups_sha256=selected.asset_sha256,
                      clip=None if clip is None else (tuple(clip["origin"]), tuple(clip["normal"])),
                      text=(("Software", "MonkeyHub camera passes"), ("Renderer", CAMERA_RENDERER_VERSION)),
                      chord_px=CHORD_PX)
    pool = drawing_pool.drawer()
    try:
        result = run_camera_task(task) if pool is None else pool.camera(task)
    except MeshViewError as exc:
        raise StudioError(409, "DRAWING_SOURCE_INVALID", str(exc)) from exc
    except DrawingElevationError as exc:
        if isinstance(source, NativeModelSource):
            raise StudioError(422, "DRAWING_NATIVE_GEOMETRY_UNSUPPORTED", str(exc)) from exc
        raise StudioError(409, "DRAWING_SOURCE_INVALID", str(exc)) from exc
    except ThreeDmInspectionError as exc:
        raise StudioError(422, "MODEL_INDEX_UNAVAILABLE", exc.message) from exc
    drawn, unit = result.drawn, result.unit
    depth = {"near": drawn.near, "far": drawn.far, "unit": unit, "measure": "distance along the view axis",
             "encoding": f"16-bit grey: {DEPTH_NEAREST} at near, 1 at far, linear between; 0 where no surface is"}
    legend = list(drawn.legend)
    base_recipe = {"kind": PASS_KIND, "camera": chosen.to_dict(), "size": [width, height], "idBy": id_by,
                   "renderer": CAMERA_RENDERER_VERSION, "passId": queued["passId"],
                   **({"fromView": queued["view"]} if queued.get("view") else {}),
                   **({"clip": queued["clip"]} if queued.get("clip") else {}),
                   **({"sourceAsset": dict(source_asset), "follow": "frozen"} if source_asset is not None else {})}
    identity = hashlib.sha256(json.dumps({"model": selected.to_dict(), "stage": source_stage_ref,
                                          "recipe": base_recipe | {"passId": None}},
                                         sort_keys=True).encode("utf-8")).hexdigest()
    stem = f"camera {queued['passId'][len(RUN_PREFIX):]}"
    documents = {}
    for name, png, extra in (("id", drawn.id_png, {"legend": legend}), ("line", drawn.line_png, {}),
                             ("depth", drawn.depth_png, {"depth": depth})):
        document = artifacts.save_document(
            binding, selected.run_id, f"{stem} {name}.png", "image/png", base64.b64encode(png).decode("ascii"),
            model_source if source_asset is None else None, source_stage_ref=source_stage_ref,
            view_recipe=base_recipe | {"pass": name} | extra,
            content_identity=hashlib.sha256(f"{identity}:{name}".encode("ascii")).hexdigest())
        documents[name] = {"runId": document.run_id, "assetSha256": document.asset_sha256,
                           "fileName": document.file_name}
    return {"documents": documents, "legend": legend, "depth": depth, "unit": unit,
            "objects": result.objects, "skippedObjects": result.skipped, "seenObjects": len(drawn.seen),
            "triangles": drawn.triangles, "edgesDrawn": drawn.edges_drawn, "timings": dict(result.timings)}


def pass_report(binding: ProjectBinding, pass_id: str) -> dict:
    """The pass run's latest record: queued, running, succeeded or failed."""

    if not pass_id.startswith(RUN_PREFIX) or pass_id not in binding.run_ids():
        raise StudioError(404, "CAMERA_PASS_NOT_FOUND", f"No camera pass {pass_id}.")
    rows = [binding.repository.load_json(ref) for ref in binding.record_refs(pass_id, kind=STUDIO_CAMERA_PASS)]
    rows = [row for row in rows if row.get("schema") == SCHEMA]
    if not rows:
        raise StudioError(404, "CAMERA_PASS_NOT_FOUND", f"No camera pass {pass_id}.")
    return max(rows, key=lambda row: (STATUS_RANK.get(row.get("status"), -1), str(row.get("updatedAt"))))
