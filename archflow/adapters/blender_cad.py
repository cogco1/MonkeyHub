"""Blender mesh execution of solid and straight-extrusion compiled operations.

The caller supplies the speculative workspace. Two fresh background processes
build/save and independently read the .blend; only the runner retains a receipt.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import archflow.adapters.cad_execution as cad
from archflow.adapters.blender_worker import READBACK_PREFIX, UNIT_SETTINGS
from archflow.adapters.cad_program import (
    CadTranslationError, _params, _rgb, expected_object_bounds,
    expected_object_semantics, lift_to_base_level,
)
from archflow.contracts.canonical import canonical_json
from archflow.project.record_kinds import SEAT_BLENDER_EXECUTION
from archflow.state.geometry_program import GeometryProgramError, _require_planar_surface_profile


def _extrusion(profile, vector, op_id):
    profile = [list(point) for point in profile]
    if profile and profile[0] == profile[-1]:
        profile.pop()
    if len(profile) < 3:
        raise CadTranslationError(f"Blender extrusion {op_id} requires a polygon")
    try:
        _require_planar_surface_profile(profile + [profile[0]])
    except GeometryProgramError as exc:
        raise CadTranslationError(f"Blender extrusion {op_id}: {exc}") from exc
    origin = profile[0]
    relative = [[point[i] - origin[i] for i in range(3)] for point in profile]
    area = [0.0, 0.0, 0.0]
    for a, b in zip(relative, relative[1:] + relative[:1]):
        area[0] += a[1] * b[2] - a[2] * b[1]
        area[1] += a[2] * b[0] - a[0] * b[2]
        area[2] += a[0] * b[1] - a[1] * b[0]
    volume = abs(sum(area[i] * vector[i] for i in range(3))) / 2
    if not math.isfinite(volume) or volume <= 0:
        raise CadTranslationError(f"Blender extrusion {op_id} requires a non-coplanar non-zero vector")
    count = len(profile)
    vertices = profile + [[point[i] + vector[i] for i in range(3)] for point in profile]
    faces = [list(reversed(range(count))), list(range(count, count * 2))]
    faces.extend([i, (i + 1) % count, (i + 1) % count + count, i + count] for i in range(count))
    return vertices, faces, volume


def _scene_plan(request):
    semantics = expected_object_semantics(
        request.program, material_by_component=request.material_by_component,
        layer_by_component=request.layer_by_component,
    )
    objects = []
    for operation in request.program.proposal.operations:
        kind, op_id = operation.kind.value, operation.op_id
        if kind not in ("solid", "extrusion") or operation.input_object_ids or len(operation.output_object_ids) != 1:
            raise CadTranslationError(f"Blender does not support {kind} operation {op_id}; first slice supports solid and straight extrusion")
        params = _params(operation)
        if kind == "solid":
            origin, size = params["origin"], params["size"]
            if any(value <= 0 for value in size):
                raise CadTranslationError(f"Blender solid {op_id} requires positive sizes")
            x, y, z = origin
            dx, dy, dz = size
            vertices, faces, volume = _extrusion(
                [[x, y, z], [x + dx, y, z], [x + dx, y, z + dz], [x, y, z + dz]],
                [0, dy, 0], op_id,
            )
        else:
            profile = lift_to_base_level(params["profile"], params, op_id)
            vertices, faces, volume = _extrusion(profile, params["vector"], op_id)
        object_id = operation.output_object_ids[0]
        row = {
            "object_id": object_id,
            "object_digest": request.program.object_digest(object_id),
            "vertices": [[point[0], point[2], point[1]] for point in vertices],
            "faces": faces,
            "volume": volume,
            "semantics": semantics["objects"][object_id],
        }
        material = row["semantics"]["user_text"].get("archflow:material")
        if material:
            color = _rgb((request.material_colors or {}).get(material, (153, 153, 153)), "Blender material color")
            row["material"] = {"name": material, "color": [channel / 255 for channel in color] + [1.0]}
        objects.append(row)
    return {
        "binding_json": canonical_json(request.binding.to_dict()),
        "provenance_json": canonical_json(dict(request.provenance or {})),
        "length_unit": request.program.proposal.length_unit.value,
        "objects": sorted(objects, key=lambda row: row["object_id"]),
    }, semantics


def _near(actual, expected, tolerance):
    return len(actual) == len(expected) and all(
        type(a) in (int, float) and math.isclose(a, b, rel_tol=0, abs_tol=tolerance)
        for a, b in zip(actual, expected)
    )


def _face_loops(faces, vertex_map):
    """Compare connectivity independently of index order and face winding."""
    if not isinstance(faces, list):
        return None
    loops = []
    for face in faces:
        if (not isinstance(face, list) or len(face) < 3 or
                any(type(index) is not int or index not in vertex_map for index in face) or
                len(set(face)) != len(face)):
            return None
        loop = [vertex_map[index] for index in face]
        start = loop.index(min(loop))
        forward = loop[start:] + loop[:start]
        backward = [forward[0], *reversed(forward[1:])]
        loops.append(min(tuple(forward), tuple(backward)))
    return sorted(loops)


def _readback_failures(request, plan, readback, provenance):
    failures = []

    def fail(detail):
        failures.append({"code": "blender.readback_mismatch", "detail": detail})

    if not isinstance(readback, dict):
        fail("Blender cold read must return a scene object")
        return failures
    if not isinstance(readback.get("blender_version"), str) or not readback["blender_version"]:
        fail("Blender cold read must identify the executing version")
    if readback.get("binding_json") != plan["binding_json"]:
        fail("saved scene crossed exact program/project/run/base binding")
    if readback.get("provenance_json") != canonical_json(provenance):
        fail("saved scene lost declared provenance")
    if (readback.get("length_unit") != plan["length_unit"] or
            not math.isclose(readback.get("unit_scale", 0), UNIT_SETTINGS[plan["length_unit"]][1], rel_tol=1e-6)):
        fail("saved scene units differ from the compiled program")
    actual = readback.get("objects", [])
    if (not isinstance(actual, list) or any(not isinstance(row, dict) for row in actual) or
            sorted(str(row.get("object_id")) for row in actual) != [row["object_id"] for row in plan["objects"]]):
        fail("saved scene has missing, duplicate or unexpected object identities")
        return failures
    by_id = {row["object_id"]: row for row in actual}
    bounds = expected_object_bounds(request.program)
    for expected in plan["objects"]:
        object_id = expected["object_id"]
        row, semantic = by_id[object_id], expected["semantics"]
        if (row.get("object_digest") != expected["object_digest"] or row.get("user_text") != semantic["user_text"] or
                row.get("layer") != semantic["layer"] or row.get("visible") is not semantic.get("visible", True)):
            fail(f"{object_id}: saved object content/semantic identity differs")
        if row.get("type") != "MESH" or row.get("closed") is not True:
            fail(f"{object_id}: saved object is not a closed mesh")
        vertices = row.get("vertices", [])
        tolerance = request.readback_tolerance
        actual_order = sorted(range(len(vertices)), key=vertices.__getitem__)
        expected_order = sorted(range(len(expected["vertices"])), key=expected["vertices"].__getitem__)
        vertex_map = dict(zip(actual_order, expected_order))
        if (len(vertices) != len(expected["vertices"]) or
                any(not _near(vertices[a], expected["vertices"][b], tolerance) for a, b in vertex_map.items()) or
                _face_loops(row.get("faces"), vertex_map) !=
                _face_loops(expected["faces"], dict(enumerate(range(len(expected["vertices"])))))):
            fail(f"{object_id}: saved mesh geometry differs")
        for edge, key in (("min", "bbox_min"), ("max", "bbox_max")):
            point = bounds[object_id][key]
            if not _near((row.get("bounds") or {}).get(edge, []), [point[0], point[2], point[1]], tolerance):
                fail(f"{object_id}: saved {edge} bounds differ")
        volume = row.get("volume")
        if (type(volume) not in (int, float) or volume <= 0 or
                not math.isclose(volume, expected["volume"], rel_tol=1e-5, abs_tol=0)):
            fail(f"{object_id}: saved mesh volume differs")
        material = expected.get("material")
        native = row.get("material")
        if material is None:
            if native is not None:
                fail(f"{object_id}: saved object has an undeclared material")
        elif (not native or native.get("name") != material["name"] or
                not _near(native.get("color", []), material["color"], 1e-6)):
            fail(f"{object_id}: saved material differs")
    return failures


def _run_worker(executable, workspace, timeout, *arguments):
    return subprocess.run(
        [str(executable), "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
         "--python", str(Path(__file__).with_name("blender_worker.py")), "--", *map(str, arguments)],
        cwd=workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


class BlenderBackend:
    backend_id = "blender"
    record_kind = SEAT_BLENDER_EXECUTION
    patch_rebuild = False
    SCHEMA = "BlenderExecutionReceipt@1"

    def validate_options(self, options):
        from archflow.adapters.cad_backend import _options

        # The legacy runner CLI supplies powershell even when a different
        # backend is selected. As with OCCT, it has no effect on execution.
        _options(options, {"blender_executable", "timeout_seconds", "powershell_executable"}, self.backend_id)
        cad._positive_finite(options.get("timeout_seconds", 120), "timeout_seconds")
        executable = options.get("blender_executable")
        if executable is not None and (not isinstance(executable, (str, Path)) or not str(executable).strip()):
            raise cad.CadExecutionError("blender_executable must be a path or command name")

    def execute(self, request):
        from archflow.adapters.cad_backend import _unsupported

        self.validate_options(request.backend_options)
        request.binding.bind_program(request.program)
        try:
            plan, semantics = _scene_plan(request)
        except (CadTranslationError, GeometryProgramError) as exc:
            return _unsupported(request, self.backend_id, exc)
        workspace = cad._strict_workspace(request.speculative_workspace)
        model = workspace / f"{request.artifact_stem}.blend"
        plan_path = workspace / f"{request.artifact_stem}.blender.json"
        for path in (model, plan_path):
            cad._strict_child(workspace, path, require_exists=False)
            if path.exists():
                raise cad.CadExecutionError(f"Blender refuses to overwrite {path.name}")
        selected = request.backend_options.get("blender_executable")
        executable = shutil.which(str(selected)) if selected is not None else shutil.which("blender")
        if executable is None:
            raise cad.CadExecutionError("Blender executable is unavailable; set backend_options['blender_executable']")
        if request.source is not None:
            cad._require_file_digest(request.source.model, request.source.sha256, "source Blender artifact")
        with plan_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(plan))
        payload = {
            "schema": self.SCHEMA, "backend": self.backend_id,
            "identity": {"binding": request.binding.to_dict(), "length_unit": plan["length_unit"], "up_axis": "Z"},
            "provenance": dict(request.provenance or {}), "status": "failed", "readback_verified": False,
            "model_artifact": None, "physical_object_ids": [row["object_id"] for row in plan["objects"]],
            "expected_semantics": semantics, "readback": None, "failures": [],
        }
        timeout = request.backend_options.get("timeout_seconds", 120)
        try:
            _run_worker(executable, workspace, timeout, "build", plan_path, model)
            cad._strict_child(workspace, model, require_exists=True)
            artifact_sha = cad._sha256_bytes(model.read_bytes())
            output = _run_worker(executable, workspace, timeout, "inspect", model)
            rows = [line[len(READBACK_PREFIX):] for line in output.stdout.splitlines() if line.startswith(READBACK_PREFIX)]
            if len(rows) != 1:
                raise cad.CadExecutionError("Blender cold read did not return one scene result")
            readback = json.loads(rows[0])
            cad._require_file_digest(model, artifact_sha, "Blender artifact after cold read")
            payload["readback"] = readback
            payload["model_artifact"] = {"relative_path": model.name, "sha256": artifact_sha, "format": "blend"}
            payload["failures"] = _readback_failures(request, plan, readback, payload["provenance"])
            if not payload["failures"]:
                payload.update(status="succeeded", readback_verified=True)
        except (cad.CadExecutionError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                detail = (exc.stderr or exc.stdout or detail)[-2000:]
            payload["failures"] = [{"code": "blender.execution_failed", "detail": detail}]
        result = self.read_receipt(request, payload)
        result.validate(request, self.backend_id)
        return result

    def read_receipt(self, request, payload):
        from archflow.adapters.cad_backend import CadArtifact, CadExecutionResult, _check_receipt

        _check_receipt(request, payload, self.SCHEMA)
        if (payload.get("backend") != self.backend_id or payload["identity"].get("up_axis") != "Z" or
                payload["identity"].get("length_unit") != request.program.proposal.length_unit.value):
            raise cad.CadExecutionError("retained Blender backend differs")
        row = payload.get("model_artifact")
        if row and (row.get("format") != "blend" or Path(row["relative_path"]).suffix != ".blend"):
            raise cad.CadExecutionError("retained Blender artifact must be a .blend model")
        verified = payload.get("readback_verified") is True
        if verified:
            plan, semantics = _scene_plan(request)
            if (not payload.get("model_artifact") or not payload.get("readback") or
                    payload.get("expected_semantics") != semantics or
                    _readback_failures(request, plan, payload["readback"], payload.get("provenance", {}))):
                raise cad.CadExecutionError("retained Blender readback differs from the requested program")
        artifacts = (CadArtifact("model", row["relative_path"], row["sha256"], row["format"], verified),) if row else ()
        return CadExecutionResult(
            self.backend_id, request.binding, payload["status"], verified, artifacts,
            tuple(payload["physical_object_ids"]), payload["expected_semantics"], payload, None,
            tuple(payload["failures"]), "blender", details={"evidence_tier": "saved_blender_mesh",
                                                          "blender_version": (payload.get("readback") or {}).get("blender_version")},
        )
