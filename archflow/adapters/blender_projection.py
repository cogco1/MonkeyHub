"""OCCT artifact -> Blender visualization; no architectural geometry builder.

The CAD execution owner supplies verified OCCT bytes and exact binding. The
runner alone retains this external-process receipt in P036.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import os
from pathlib import Path
from dataclasses import dataclass

import archflow.adapters.cad_execution as cad
import archflow.adapters.occt_backend as occt_backend
from archflow.adapters.blender_cad import _run_worker, _near, _face_loops
from archflow.adapters.blender_worker import READBACK_PREFIX, UNIT_SETTINGS
from archflow.adapters.cad_backend import CadExecutionRequest, CadExecutionResult, OcctBackend
from archflow.adapters.cad_program import _rgb
from archflow.contracts.canonical import canonical_json

def render_scene_projection(plan, images, workspace: Path, executable: str):
    """Project a retained Render Scene in a caller-owned speculative workspace.

    Unlike execute_blender_projection's OCCT receipt contract this accepts an
    already resolved imported-mesh plan; it never claims an exact STEP source.
    """
    workspace=workspace.resolve(strict=True)
    for digest,data in images.items():
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid image content identity')
        (workspace/(digest+'.image')).write_bytes(data)
    path=workspace/'scene-plan.json';path.write_text(canonical_json(plan),encoding='utf-8')
    worker=Path(__file__).with_name('blender_worker.py')
    model=workspace/'scene.blend';image=workspace/'render.png'
    environment={key:value for key,value in os.environ.items() if key not in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV')}
    for arguments in [('build',str(path),str(model)),('scene-render',str(model),str(path),str(image))]:
        # Direct output avoids a full pipe stalling native host startup and
        # preserves diagnostics even if a host times out midway through a phase.
        with (workspace/'blender.log').open('ab') as log:
            process=subprocess.run([executable,'--background','--factory-startup','--python-exit-code','1','--python',str(worker),'--',*arguments],
                cwd=workspace,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env=environment,timeout=1800,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if process.returncode:raise RuntimeError('Blender projection failed; see the retained job log.')
    report=json.loads((workspace/'readback.json').read_text(encoding='utf-8'))
    if not image.is_file() or report['sceneRevision']!=plan['render_scene']['sceneRevision']:
        raise RuntimeError('Blender did not return the requested scene revision')
    return report


@dataclass(frozen=True)
class BlenderPresentation:
    """Explicit visual settings reused across full rebuilds; never design state."""
    camera_id: str = "overview"
    render_preset: str = "preview-v1"
    resolution: int = 512
    samples: int = 16
    azimuth: float = -55.0
    elevation: float = 30.0

    def __post_init__(self):
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
        return {**self.__dict__, "engine": "CYCLES", "device": "CPU", "seed": 0,
                "denoising": False, "view_transform": "Standard", "light_preset": "two-area-v1"}


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
    workspace = request.speculative_workspace
    stem = request.artifact_stem + ".projection"
    paths = {role: workspace / (stem + suffix) for role, suffix in
             (("plan", ".json"), ("scene", ".blend"), ("render", ".png"),
              ("build_log", ".build.log"), ("inspect_log", ".inspect.log"), ("render_log", ".render.log"))}
    for path in paths.values():
        cad._strict_child(workspace, path, require_exists=False)
        if path.exists():
            raise cad.CadExecutionError(f"projection refuses to overwrite {path.name}")
    receipt = {"schema": "BlenderProjectionReceipt@1", "request_id": stem,
               "binding": request.binding.to_dict(), "source_artifact": plan["projection"]["source_artifact"],
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
            if image.format != "PNG" or image.size != (presentation.resolution, presentation.resolution):
                raise cad.CadExecutionError("render is not the requested PNG")
            image.verify()
        _source(request, source)
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
    presentation = BlenderPresentation(**{key: settings[key] for key in BlenderPresentation.__dataclass_fields__})
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
