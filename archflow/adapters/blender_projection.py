"""OCCT artifact -> Blender visualization; no architectural geometry builder.

The CAD execution owner supplies verified OCCT bytes and exact binding. The
runner alone retains this external-process receipt in P036.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass

import archflow.adapters.cad_execution as cad
import archflow.adapters.occt_backend as occt_backend
from archflow.adapters.blender_cad import _run_worker, _near, _face_loops
from archflow.adapters.blender_worker import READBACK_PREFIX, UNIT_SETTINGS
from archflow.adapters.cad_backend import CadExecutionRequest, CadExecutionResult, OcctBackend
from archflow.adapters.cad_program import _rgb
from archflow.contracts.canonical import canonical_json


@dataclass(frozen=True)
class BlenderCamera:
    """Captured view in source-model units and Z-up; no design write authority."""
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    projection: str
    near: float
    far: float
    aspect: float
    vertical_fov: float | None = None
    orthographic_bounds: tuple[float, float, float, float] | None = None

    def __post_init__(self):
        def numbers(value, count):
            return isinstance(value, (tuple, list)) and len(value) == count and all(
                type(v) in (int, float) and math.isfinite(v) for v in value)
        for name in ("position", "target", "up"):
            value = getattr(self, name)
            if not numbers(value, 3):
                raise cad.CadExecutionError(f"invalid camera {name}")
            object.__setattr__(self, name, tuple(value))
        if not numbers((self.near, self.far, self.aspect), 3) or not (
                0 < self.near < self.far and 0.01 <= self.aspect <= 100):
            raise cad.CadExecutionError("invalid camera clipping or aspect")
        direction = [b - a for a, b in zip(self.position, self.target)]
        cross = [direction[1] * self.up[2] - direction[2] * self.up[1],
                 direction[2] * self.up[0] - direction[0] * self.up[2],
                 direction[0] * self.up[1] - direction[1] * self.up[0]]
        if sum(v * v for v in cross) <= 1e-20:
            raise cad.CadExecutionError("camera direction and up must span a frame")
        if self.projection == "perspective":
            if not numbers((self.vertical_fov,), 1) or not 1 <= self.vertical_fov <= 175 or self.orthographic_bounds is not None:
                raise cad.CadExecutionError("invalid perspective camera")
        elif self.projection == "orthographic":
            bounds = self.orthographic_bounds
            if not numbers(bounds, 4) or self.vertical_fov is not None:
                raise cad.CadExecutionError("invalid orthographic camera")
            left, right, top, bottom = bounds
            if not left < right or not bottom < top:
                raise cad.CadExecutionError("empty orthographic bounds")
            if not math.isclose((right - left) / (top - bottom), self.aspect, rel_tol=1e-6):
                raise cad.CadExecutionError("orthographic bounds and aspect differ")
            object.__setattr__(self, "orthographic_bounds", tuple(bounds))
        else:
            raise cad.CadExecutionError("unknown camera projection")

    def to_dict(self):
        return {**self.__dict__, "position": list(self.position), "target": list(self.target),
                "up": list(self.up), "orthographic_bounds": (
                    list(self.orthographic_bounds) if self.orthographic_bounds is not None else None)}


@dataclass(frozen=True)
class BlenderPresentation:
    """Explicit visual settings reused across full rebuilds; never design state."""
    camera_id: str = "overview"
    render_preset: str = "preview-v1"
    resolution: int = 512
    samples: int = 16
    azimuth: float = -55.0
    elevation: float = 30.0
    camera: BlenderCamera | None = None

    def __post_init__(self):
        if isinstance(self.camera, dict):
            object.__setattr__(self, "camera", BlenderCamera(**self.camera))
        if self.camera is not None and not isinstance(self.camera, BlenderCamera):
            raise cad.CadExecutionError("invalid captured camera")
        if self.camera_id != "overview" or self.render_preset != "preview-v1":
            raise cad.CadExecutionError("V1 supports overview / preview-v1 only")
        for name, maximum in (("resolution", 2048), ("samples", 256)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise cad.CadExecutionError(f"invalid Blender {name}")
        for name in ("azimuth", "elevation"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise cad.CadExecutionError(f"invalid Blender {name}")
        if not -85 <= self.elevation <= 85:
            raise cad.CadExecutionError("camera elevation must be within [-85,85]")

    def to_dict(self):
        values = {key: value for key, value in self.__dict__.items() if key != "camera"}
        if self.camera is not None:
            values["camera"] = self.camera.to_dict()
        return {**values, "engine": "CYCLES", "device": "CPU", "seed": 0,
                "denoising": False, "view_transform": "Standard", "light_preset": "two-area-v1"}

    @property
    def image_size(self):
        if self.camera is None:
            return self.resolution, self.resolution
        aspect = self.camera.aspect
        return ((self.resolution, max(1, round(self.resolution / aspect))) if aspect >= 1 else
                (max(1, round(self.resolution * aspect)), self.resolution))


def _source(request, result):
    # Reconstruct through the existing native receipt reader; do not trust a
    # caller's status flag or a digest without the originating binding.
    checked = OcctBackend().read_receipt(request, result.receipt_payload or {})
    checked.validate(request, "occt")
    result.validate(request, "occt")
    if checked.status != "succeeded" or result.artifacts != checked.artifacts:
        raise cad.CadExecutionError("projection requires successful verified OCCT source")
    artifact = next(a for a in checked.artifacts if a.name == "exact")
    if artifact.format != cad._STEP_FORMAT:
        raise cad.CadExecutionError("projection requires OCCT STEP")
    return artifact


def _plan(request, result, presentation):
    source = _source(request, result)
    path = request.speculative_workspace / source.relative_path
    entries = occt_backend.read_step(path, length_unit=request.program.proposal.length_unit.value)
    semantics = result.expected_semantics["objects"]
    if len(entries) != len(semantics) or {entry.name for entry in entries} != set(semantics):
        raise cad.CadExecutionError("STEP source has missing/duplicate/unexpected object IDs")
    objects = []
    # Deflection in source units: 1 mm. STEP reader already supplies Z-up.
    scale = UNIT_SETTINGS[request.program.proposal.length_unit.value][1]
    for entry in sorted(entries, key=lambda item: item.name):
        vertices, triangles = occt_backend.tessellate_shape(entry.shape, linear_deflection=0.001 / scale)
        semantic = semantics[entry.name]
        material = semantic["user_text"].get("archflow:material")
        color = _rgb((request.material_colors or {}).get(material, (180, 190, 200)), "projection material")
        objects.append({"object_id": entry.name, "object_digest": request.program.object_digest(entry.name),
                        "vertices": [list(v) for v in vertices], "faces": [list(f) for f in triangles], "semantics": semantic,
                        "material": {"name": material or "projection-neutral", "color": [v / 255 for v in color] + [1.0]}})
    cad._require_file_digest(path, source.sha256, "OCCT projection source after read")
    return {"binding_json": canonical_json(request.binding.to_dict()),
            "provenance_json": canonical_json(dict(request.provenance or {})),
            "length_unit": request.program.proposal.length_unit.value, "objects": objects,
            "readback_tolerance": request.readback_tolerance,
            "projection": {"source_artifact": source.to_dict(), "presentation": presentation.to_dict()}}


def _verify_scene(plan, readback):
    for key in ("binding_json", "provenance_json", "length_unit"):
        if readback.get(key) != plan[key]:
            raise cad.CadExecutionError(f"projection cold read differs: {key}")
    if not _near([readback.get("unit_scale")], [UNIT_SETTINGS[plan["length_unit"]][1]], 1e-7):
        raise cad.CadExecutionError("projection units differ")
    if readback.get("projection_json") != canonical_json(plan["projection"]):
        raise cad.CadExecutionError("projection source/presentation binding differs")
    rows = readback.get("objects", [])
    if sorted(str(row.get("object_id")) for row in rows) != [row["object_id"] for row in plan["objects"]]:
        raise cad.CadExecutionError("projection object identity coverage differs")
    for expected, actual in zip(plan["objects"], sorted(rows, key=lambda row: row["object_id"])):
        if (actual.get("object_digest") != expected["object_digest"] or
                actual.get("user_text") != expected["semantics"]["user_text"] or
                actual.get("layer") != expected["semantics"]["layer"] or
                actual.get("visible") != expected["semantics"].get("visible", True)):
            raise cad.CadExecutionError("projection saved object semantics differ")
        vertices = actual.get("vertices", [])
        expected_vertices = expected["vertices"]
        tolerance = plan["readback_tolerance"]
        if len(vertices) != len(expected_vertices) or any(not _near(a, b, tolerance) for a, b in zip(vertices, expected_vertices)):
            raise cad.CadExecutionError("projection saved geometry differs; reconcile in MonkeyHub")
        indices = {i: i for i in range(len(vertices))}
        if _face_loops(actual.get("faces"), indices) != _face_loops(expected["faces"], indices):
            raise cad.CadExecutionError("projection saved topology differs")
        native = actual.get("material") or {}
        if native.get("name") != expected["material"]["name"] or not _near(native.get("color", []), expected["material"]["color"], 1e-6):
            raise cad.CadExecutionError("projection material differs")
    if not readback.get("presentation_verified") or not readback.get("blender_version"):
        raise cad.CadExecutionError("saved camera/light/render settings differ")
    camera = plan["projection"]["presentation"].get("camera")
    if camera is not None:
        _verify_captured_camera(BlenderCamera(**camera), readback.get("visual_state") or {})


def _verify_captured_camera(camera, visual):
    """Compare host output with the requested frustum, including host clamps."""
    near, far = camera.near, camera.far
    if camera.projection == "perspective":
        y = 1 / math.tan(math.radians(camera.vertical_fov) / 2)
        expected = [[y / camera.aspect, 0, 0, 0], [0, y, 0, 0],
                    [0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)],
                    [0, 0, -1, 0]]
    else:
        left, right, top, bottom = camera.orthographic_bounds
        expected = [[2 / (right - left), 0, 0, -(right + left) / (right - left)],
                    [0, 2 / (top - bottom), 0, -(top + bottom) / (top - bottom)],
                    [0, 0, -2 / (far - near), -(far + near) / (far - near)], [0, 0, 0, 1]]
    actual = (visual.get("captured_camera") or {}).get("projection_matrix", [])
    def close(a, b):
        return len(a) == len(b) and all(math.isclose(x, y, rel_tol=2e-5, abs_tol=2e-5) for x, y in zip(a, b))
    if len(actual) != 4 or any(not close(a, b) for a, b in zip(actual, expected)):
        raise cad.CadExecutionError("saved camera projection differs from captured view")
    if not close(visual.get("camera_position", []), camera.position) or not close(visual.get("clip", []), (near, far)):
        raise cad.CadExecutionError("saved camera pose or clipping differs")
    rotation = visual.get("camera_rotation", [])
    if len(rotation) != 3:
        raise cad.CadExecutionError("saved camera orientation missing")
    x, y, z = rotation
    cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
    actual_right = [cz * cy, sz * cy, -sy]
    actual_up = [cz * sy * sx - sz * cx, sz * sy * sx + cz * cx, cy * sx]
    def normalized(v):
        length = math.sqrt(sum(n * n for n in v))
        return [n / length for n in v]
    def cross(a, b):
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    forward = normalized([b - a for a, b in zip(camera.position, camera.target)])
    right = normalized(cross(forward, camera.up))
    up = cross(right, forward)
    if not close(actual_right, right) or not close(actual_up, up):
        raise cad.CadExecutionError("saved camera orientation differs")


def _readback(output):
    rows = [line[len(READBACK_PREFIX):] for line in output.stdout.splitlines() if line.startswith(READBACK_PREFIX)]
    if len(rows) != 1:
        raise cad.CadExecutionError("Blender must return exactly one cold-read result")
    return json.loads(rows[0])


def execute_blender_projection(request: CadExecutionRequest, source: CadExecutionResult, *,
                               blender_executable: str, presentation: BlenderPresentation | None = None,
                               timeout_seconds: float = 120) -> dict:
    """Full rebuild beside OCCT output in the caller's speculative workspace.

Input refusals raise before Blender starts. External execution failures return
an explicit failed receipt, preserving logs and never claiming partial artifacts.
"""
    presentation = presentation or BlenderPresentation()
    cad._positive_finite(timeout_seconds, "timeout_seconds")
    plan = _plan(request, source, presentation)
    return _execute_projection_plan(plan, request.speculative_workspace, request.artifact_stem,
        presentation, blender_executable, timeout_seconds, lambda: _source(request, source))


def mesh_projection_plan(data: bytes, source: dict, presentation: BlenderPresentation) -> dict:
    """Project-owned 3DM mesh bytes; refuse unsupported geometry rather than omit it."""
    import hashlib
    import rhino3dm
    if hashlib.sha256(data).hexdigest() != source["sha256"]:
        raise cad.CadExecutionError("render source digest differs")
    document = rhino3dm.File3dm.FromByteArray(data)
    if document is None:
        raise cad.CadExecutionError("render source is not a readable 3DM")
    units = {rhino3dm.UnitSystem.Meters: "meter", rhino3dm.UnitSystem.Millimeters: "millimeter",
             rhino3dm.UnitSystem.Inches: "inch", rhino3dm.UnitSystem.Feet: "foot"}
    unit = units.get(document.Settings.ModelUnitSystem)
    if unit is None:
        raise cad.CadExecutionError("render source requires explicit meter/millimeter/inch/foot units")
    objects = []
    for item in document.Objects:
        geometry = item.Geometry
        if not isinstance(geometry, rhino3dm.Mesh):
            raise cad.CadExecutionError("This render path supports mesh 3DM only; convert other geometry explicitly.")
        object_id = str(item.Attributes.Id)
        vertices = [[v.X, v.Y, v.Z] for v in geometry.Vertices]
        faces = []
        for face in geometry.Faces:
            a, b, c, d = face
            faces.append([a, b, c] if c == d else [a, b, c, d])
        if not vertices or not faces or any(not math.isfinite(n) for v in vertices for n in v):
            raise cad.CadExecutionError("render source has an empty or invalid mesh")
        if any(i < 0 or i >= len(vertices) for face in faces for i in face):
            raise cad.CadExecutionError("render source has invalid mesh indices")
        layer = document.Layers.FindIndex(item.Attributes.LayerIndex)
        visible = item.Attributes.Visible and (layer is None or layer.Visible)
        objects.append({"object_id": object_id, "object_digest": cad._sha256_bytes(
            canonical_json({"vertices": vertices, "faces": faces}).encode()),
            "vertices": vertices, "faces": faces,
            "semantics": {"user_text": {"archflow:object_ref": "cad-object:" + object_id},
                          "layer": layer.Name if layer else "Imported model", "visible": visible},
            "material": {"name": "preview-neutral", "color": [.7, .73, .77, 1]}})
    if not objects or not any(row["semantics"]["visible"] for row in objects):
        raise cad.CadExecutionError("render source has no visible meshes")
    if len({row["object_id"] for row in objects}) != len(objects):
        raise cad.CadExecutionError("render source has duplicate object identities")
    return {"binding_json": canonical_json(source), "provenance_json": canonical_json({"source": "retained-mesh"}),
            "length_unit": unit, "objects": sorted(objects, key=lambda row: row["object_id"]),
            "readback_tolerance": 1e-5,
            "projection": {"source_artifact": source, "presentation": presentation.to_dict()}}


def execute_mesh_projection(data: bytes, source: dict, *, workspace, blender_executable: str,
                            presentation: BlenderPresentation, timeout_seconds: float = 180) -> dict:
    plan = mesh_projection_plan(data, source, presentation)
    return _execute_projection_plan(plan, workspace, "render", presentation,
                                    blender_executable, timeout_seconds, lambda: None)


def _execute_projection_plan(plan, workspace, artifact_stem, presentation, blender_executable,
                             timeout_seconds, verify_source):
    stem = artifact_stem + ".projection"
    paths = {role: workspace / (stem + suffix) for role, suffix in
             (("plan", ".json"), ("scene", ".blend"), ("render", ".png"),
              ("build_log", ".build.log"), ("inspect_log", ".inspect.log"), ("render_log", ".render.log"))}
    for path in paths.values():
        cad._strict_child(workspace, path, require_exists=False)
        if path.exists():
            raise cad.CadExecutionError(f"projection refuses to overwrite {path.name}")
    receipt = {"schema": "BlenderProjectionReceipt@1", "request_id": stem,
               "binding": json.loads(plan["binding_json"]), "source_artifact": plan["projection"]["source_artifact"],
               "presentation": presentation.to_dict(), "status": "failed", "artifacts": [], "logs": [],
               "blender_version": None, "readback": None, "failures": []}
    stage = "launch"
    try:
        executable = shutil.which(str(blender_executable))
        if not executable:
            raise cad.CadExecutionError("Blender executable unavailable")
        paths["plan"].write_text(canonical_json(plan), encoding="utf-8")
        for stage, args in (("build", ("build", paths["plan"], paths["scene"])),
                            ("inspect", ("inspect", paths["scene"])),
                            ("render", ("render", paths["scene"], paths["render"]))):
            log = paths[stage + "_log"]
            try:
                output = _run_worker(executable, workspace, timeout_seconds, *args)
            except (subprocess.SubprocessError, OSError) as exc:
                details = (getattr(exc, "stdout", "") or "", getattr(exc, "stderr", "") or "", str(exc))
                log.write_text("\n".join(v.decode("utf-8", "replace") if isinstance(v, bytes) else v for v in details), encoding="utf-8")
                raise
            else:
                log.write_text(output.stdout + output.stderr, encoding="utf-8")
            finally:
                if log.exists():
                    receipt["logs"].append(_artifact(log, "log"))
            if stage == "build":
                cad._strict_child(workspace, paths["scene"], require_exists=True)
                scene_sha = cad._sha256_bytes(paths["scene"].read_bytes())
            else:
                readback = _readback(output)
                _verify_scene(plan, readback)
                cad._require_file_digest(paths["scene"], scene_sha, "saved projection after cold read")
                receipt.update(readback=readback, blender_version=readback["blender_version"])
        from PIL import Image
        with Image.open(paths["render"]) as image:
            if image.format != "PNG" or image.size != presentation.image_size:
                raise cad.CadExecutionError("render is not the requested PNG")
            image.verify()
        verify_source()
        receipt.update(status="succeeded", artifacts=[_artifact(paths["scene"], "blend"), _artifact(paths["render"], "png")])
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as exc:
        receipt["failures"] = [{"stage": stage, "code": "blender.projection_failed", "detail": str(exc)}]
    return receipt


def _artifact(path, format):
    data = path.read_bytes()
    return {"relative_path": path.name, "format": format, "sha256": cad._sha256_bytes(data), "size_bytes": len(data)}


def verify_projection_artifacts(request, source, receipt):
    """Read-only retained verification; no host execution or automatic rebuild."""
    artifact = _source(request, source)
    if (receipt.get("schema") != "BlenderProjectionReceipt@1" or receipt.get("binding") != request.binding.to_dict()
            or receipt.get("source_artifact") != artifact.to_dict() or receipt.get("status") != "succeeded"
            or receipt.get("failures")):
        raise cad.CadExecutionError("projection receipt crossed source binding or failed")
    settings = receipt["presentation"]
    presentation = BlenderPresentation(**{key: settings[key] for key in BlenderPresentation.__dataclass_fields__ if key in settings})
    if settings != presentation.to_dict():
        raise cad.CadExecutionError("retained presentation differs")
    _verify_scene(_plan(request, source, presentation), receipt["readback"])
    if receipt.get("blender_version") != receipt["readback"].get("blender_version"):
        raise cad.CadExecutionError("retained Blender version differs")
    if sorted(a["format"] for a in receipt["artifacts"]) != ["blend", "png"]:
        raise cad.CadExecutionError("projection artifacts missing")
    for row in receipt["artifacts"] + receipt["logs"]:
        cad._portable_relative_path(row["relative_path"])
        path = request.speculative_workspace / row["relative_path"]
        cad._strict_child(request.speculative_workspace, path, require_exists=True)
        cad._require_file_digest(path, row["sha256"], "projection artifact")
        if path.stat().st_size != row["size_bytes"]:
            raise cad.CadExecutionError("projection artifact size differs")
