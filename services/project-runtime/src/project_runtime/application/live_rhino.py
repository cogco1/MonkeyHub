"""Work in the Rhino document the architect has open, through Rhino 8's own RhinoCode CLI.

The Rhino backend (``monkeycad.rhino``) starts a hidden Rhino of its own and
never sees the window the architect is working in. This module is the other
half: it finds a running Rhino whose script server is on (the architect types
``StartScriptServer`` once in that Rhino), reads the open document as it is
now - unsaved edits included - and adds or changes objects in it.

Every request runs one generated CPython script inside that Rhino. A write runs
as one Rhino command, so the architect undoes all of it with one Undo. Objects
a write creates carry ``monkeyhub.*`` user text; a later write may move, change
or delete only those, unless the request explicitly allows the architect's own
objects. Nothing here saves the document: saving stays the architect's act.

A snapshot writes a copy of the open document, as it is now, to a temporary
file and registers that copy as an external model asset, so the drawing routes
can draw what the architect is editing without the document being saved.
"""

from __future__ import annotations

import base64
import json
import ntpath
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..errors import StudioError

RHINOCODE_ENV = "MONKEYHUB_RHINOCODE"
# RhinoCode hands the script to Rhino and returns at once; the script answers
# through its result file, which is waited for this long.
SCRIPT_TIMEOUT_S = 120.0
CLI_TIMEOUT_S = 20.0
_POLL_S = 0.1

_START_HINT = ("In that Rhino, type StartScriptServer once (it lasts until Rhino closes), "
               "then ask again.")


@dataclass(frozen=True)
class LiveRhino:
    """One running Rhino that RhinoCode can reach."""

    pipe_id: str
    process_id: int
    version: str | None
    document_title: str | None
    document_path: str | None
    active_viewport: str | None

    def to_dict(self) -> dict:
        return {"rhinoId": self.pipe_id, "processId": self.process_id, "version": self.version,
                "documentTitle": self.document_title, "documentPath": self.document_path,
                "activeViewport": self.active_viewport}


Runner = Callable[..., subprocess.CompletedProcess]


def rhinocode_executable() -> Path:
    """RhinoCode.exe beside an installed Rhino 8, or the one the environment names."""

    override = os.environ.get(RHINOCODE_ENV)
    candidates: list[Path] = [Path(override)] if override else []
    try:
        from monkeycad.backends.rhino.export import discover_rhino_executables

        candidates += [Path(rhino).with_name("RhinoCode.exe") for rhino in discover_rhino_executables()]
    except Exception:  # discovery is advisory; the default install path still answers
        pass
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Rhino 8" / "System" / "RhinoCode.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise StudioError(409, "LIVE_RHINO_UNAVAILABLE",
                      "RhinoCode.exe was not found; it ships with Rhino 8 (System folder). "
                      f"Set {RHINOCODE_ENV} to its path if Rhino is installed elsewhere.")


def _cli(args: list[str], runner: Runner | None) -> subprocess.CompletedProcess:
    run = subprocess.run if runner is None else runner
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=CLI_TIMEOUT_S, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise StudioError(504, "LIVE_RHINO_TIMEOUT", "RhinoCode did not answer in time.") from exc
    except OSError as exc:
        raise StudioError(409, "LIVE_RHINO_UNAVAILABLE", f"RhinoCode could not be started: {exc}") from exc


def parse_instances(text: str) -> tuple[LiveRhino, ...]:
    """Read ``rhinocode list --json``."""

    try:
        rows = json.loads(text or "[]")
    except ValueError as exc:
        raise StudioError(502, "LIVE_RHINO_LIST_UNREADABLE", "RhinoCode's instance list was not JSON.") from exc
    instances = []
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict) or not isinstance(row.get("pipeId"), str):
            continue
        doc = row.get("activeDoc") if isinstance(row.get("activeDoc"), dict) else {}
        instances.append(LiveRhino(
            pipe_id=row["pipeId"], process_id=int(row.get("processId") or 0),
            version=row.get("processVersion"), document_title=doc.get("title") or None,
            document_path=doc.get("location") or None, active_viewport=row.get("activeViewport") or None,
        ))
    return tuple(instances)


def list_instances(*, runner: Runner | None = None) -> tuple[LiveRhino, ...]:
    result = _cli([str(rhinocode_executable()), "list", "--json"], runner)
    if result.returncode != 0:
        raise StudioError(502, "LIVE_RHINO_LIST_FAILED", (result.stderr or result.stdout or "RhinoCode list failed.").strip()[:500])
    return parse_instances(result.stdout)


def _same_path(left: str, right: str) -> bool:
    # A Rhino document path is a Windows path, compared as Windows compares it wherever this runs.
    return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(ntpath.normpath(right))


def select_instance(instances: tuple[LiveRhino, ...], *, rhino_id: str | None = None,
                    document_path: str | None = None) -> LiveRhino:
    """The one Rhino a request means: named, holding the named document, or the only one."""

    if not instances:
        raise StudioError(409, "LIVE_RHINO_NOT_RUNNING", "No running Rhino answers RhinoCode. " + _START_HINT)
    if rhino_id is not None:
        match = [row for row in instances if rhino_id in {row.pipe_id, str(row.process_id)}]
        if not match:
            raise StudioError(404, "LIVE_RHINO_NOT_FOUND", f"No running Rhino is {rhino_id}; read GET /api/live-rhino/instances.")
        instance = match[0]
        if document_path is not None and not (instance.document_path and _same_path(instance.document_path, document_path)):
            raise StudioError(409, "LIVE_RHINO_DOCUMENT_CHANGED",
                              f"Rhino {rhino_id} now has {instance.document_path or 'an unsaved document'} open, not {document_path}.")
        return instance
    if document_path is not None:
        match = [row for row in instances if row.document_path and _same_path(row.document_path, document_path)]
        if not match:
            raise StudioError(404, "LIVE_RHINO_NOT_FOUND",
                              f"No running Rhino answering RhinoCode has {document_path} open. " + _START_HINT)
        return match[0]
    if len(instances) > 1:
        names = ", ".join(f"{row.pipe_id} ({row.document_path or 'unsaved'})" for row in instances)
        raise StudioError(409, "LIVE_RHINO_AMBIGUOUS", f"Several Rhinos are running: {names}. Name rhinoId or documentPath.")
    return instances[0]


def run_in_rhino(instance: LiveRhino, request: dict, *, as_command: bool,
                 timeout_s: float = SCRIPT_TIMEOUT_S, runner: Runner | None = None,
                 work_root: Path | None = None) -> dict:
    """Run the generated script once in ``instance`` and return what it answered.

    ``as_command`` runs it through ``_-RunPythonScript``: Rhino records one undo
    step for it. A read runs as a plain script and leaves the undo list alone.
    """

    work = Path(tempfile.mkdtemp(prefix="monkeyhub-live-rhino-", dir=work_root))
    try:
        request_path, result_path, script_path = work / "request.json", work / "result.json", work / "run.py"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        script_path.write_text(RUNNER_SOURCE + f"\n_main({str(request_path)!r}, {str(result_path)!r})\n", encoding="utf-8")
        exe = str(rhinocode_executable())
        if as_command:
            args = [exe, "--rhino", instance.pipe_id, "command", f'_-RunPythonScript "{script_path}"']
        else:
            args = [exe, "--rhino", instance.pipe_id, "script", str(script_path)]
        started = _cli(args, runner)
        if started.returncode != 0:
            raise StudioError(502, "LIVE_RHINO_SCRIPT_REFUSED",
                              (started.stderr or started.stdout or "RhinoCode refused the script.").strip()[:500])
        deadline = time.monotonic() + timeout_s
        while not result_path.is_file():
            if time.monotonic() > deadline:
                raise StudioError(504, "LIVE_RHINO_TIMEOUT",
                                  "Rhino did not finish the request in time. It may be busy in a command or a dialog; "
                                  "finish or cancel it in Rhino and ask again.")
            time.sleep(_POLL_S)
        answer = json.loads(result_path.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not answer.get("ok"):
        code = str(answer.get("code") or "LIVE_RHINO_SCRIPT_FAILED")
        status = 500 if code == "LIVE_RHINO_SCRIPT_FAILED" else 409 if code in _CONFLICTS else 422
        raise StudioError(status, code, str(answer.get("detail") or "Rhino refused the request."))
    answer.pop("ok", None)
    return answer


_CONFLICTS = frozenset({"LIVE_RHINO_NO_DOCUMENT", "LIVE_RHINO_DOCUMENT_CHANGED", "LIVE_RHINO_USER_OBJECT"})


def _target(rhino_id: str | None, document_path: str | None, runner: Runner | None) -> LiveRhino:
    return select_instance(list_instances(runner=runner), rhino_id=rhino_id, document_path=document_path)


def read_document(*, rhino_id: str | None = None, document_path: str | None = None, layer_prefix: str | None = None,
                  ids: list[str] | None = None, include_geometry: bool = False, offset: int = 0, limit: int = 500,
                  runner: Runner | None = None) -> dict:
    instance = _target(rhino_id, document_path, runner)
    answer = run_in_rhino(instance, {
        "action": "read", "documentPath": instance.document_path, "layerPrefix": layer_prefix, "ids": ids,
        "includeGeometry": include_geometry, "offset": offset, "limit": limit,
    }, as_command=False, runner=runner)
    return {"rhino": instance.to_dict(), **answer}


def write_document(*, label: str, operations: list[dict], project_id: str, rhino_id: str | None = None,
                   document_path: str | None = None, allow_user_objects: bool = False,
                   runner: Runner | None = None) -> dict:
    instance = _target(rhino_id, document_path, runner)
    answer = run_in_rhino(instance, {
        "action": "write", "documentPath": instance.document_path, "label": label, "writeId": str(uuid.uuid4()),
        "projectId": project_id, "operations": operations, "allowUserObjects": allow_user_objects,
    }, as_command=True, runner=runner)
    return {"rhino": instance.to_dict(), **answer}


def snapshot_document(binding, *, rhino_id: str | None = None, document_path: str | None = None,
                      file_name: str | None = None, drawable_only: bool = False,
                      layer_prefixes: list[str] | None = None, event_sink=None, monitor=None,
                      runner: Runner | None = None) -> dict:
    """Register a copy of the open document, unsaved edits included, as a model asset to draw from."""

    from .artifacts import register_model_asset

    instance = _target(rhino_id, document_path, runner)
    with tempfile.TemporaryDirectory(prefix="monkeyhub-live-rhino-snapshot-") as folder:
        copy = Path(folder) / "snapshot.3dm"
        answer = run_in_rhino(instance, {"action": "snapshot", "documentPath": instance.document_path,
                                         "path": str(copy), "drawableOnly": drawable_only,
                                         "layerPrefixes": layer_prefixes}, as_command=False, runner=runner)
        if not copy.is_file():
            raise StudioError(500, "LIVE_RHINO_SNAPSHOT_FAILED", "Rhino reported a snapshot but wrote no file.")
        data = copy.read_bytes()
    title = (instance.document_title or "rhino").removesuffix(".3dm")
    name = file_name or f"{title}_live_snapshot.3dm"
    record = register_model_asset(binding, None, None, name, base64.b64encode(data).decode("ascii"),
                                  event_sink=event_sink, monitor=monitor)
    from monkeycad.backends.occt.native_models import drawable_report

    # Said before anything is drawn: what the drawing routes will leave out of this copy.
    return {"rhino": instance.to_dict(), **answer, "artifact": record, "undrawable": drawable_report(data)}


def capture_view(*, rhino_id: str | None = None, document_path: str | None = None, view: str | None = None,
                 width: int = 1600, height: int = 1000, runner: Runner | None = None) -> bytes:
    instance = _target(rhino_id, document_path, runner)
    with tempfile.TemporaryDirectory(prefix="monkeyhub-live-rhino-capture-") as folder:
        png = Path(folder) / "view.png"
        run_in_rhino(instance, {"action": "capture", "documentPath": instance.document_path, "view": view,
                                "width": width, "height": height, "png": str(png)}, as_command=False, runner=runner)
        if not png.is_file():
            raise StudioError(500, "LIVE_RHINO_CAPTURE_FAILED", "Rhino reported a capture but wrote no image.")
        return png.read_bytes()


# The script Rhino runs (CPython 3.9 in Rhino 8). It is sent as text and never
# imported here; ``_main(request, result)`` is appended to each copy.
RUNNER_SOURCE = r'''#! python3
import json
import os
import traceback

import System
import Rhino
from Rhino.Geometry import BoundingBox, Mesh, Point3d, PolylineCurve, Surface, TextDot, Transform, Vector3d

OWNER_KEY = "monkeyhub.owner"
OWNER = "agent"
WRITE_KEY = "monkeyhub.write"
LABEL_KEY = "monkeyhub.label"
PROJECT_KEY = "monkeyhub.project"
MAX_POINTS = 2000
# What the drawing routes can project (planar and meshed Breps, extrusions,
# meshes, curves, block instances); a snapshot may leave the rest out.
DRAWABLE = (Rhino.Geometry.Brep, Rhino.Geometry.Extrusion, Rhino.Geometry.Mesh, Rhino.Geometry.Curve,
            Rhino.Geometry.InstanceReferenceGeometry)


class Refusal(Exception):
    def __init__(self, code, detail):
        Exception.__init__(self, detail)
        self.code = code
        self.detail = detail


def _invalid(detail):
    return Refusal("LIVE_RHINO_OPERATION_INVALID", detail)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(name + " must be a number")
    return float(value)


def _point(value, name):
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        raise _invalid(name + " must be [x, y] or [x, y, z]")
    xyz = [_number(c, name) for c in value] + [0.0]
    return Point3d(xyz[0], xyz[1], xyz[2])


def _xyz(p):
    return [round(p.X, 6), round(p.Y, 6), round(p.Z, 6)]


def _layer_path(doc, index):
    if index < 0 or index >= doc.Layers.Count:
        return None
    return doc.Layers[index].FullPath


def _layer(doc, path, color=None):
    if not isinstance(path, str) or not path.strip():
        raise _invalid("layer must be a layer path such as HUB::Site")
    index = doc.Layers.FindByFullPath(path, -1)
    if index >= 0:
        return index
    parent = System.Guid.Empty
    current = ""
    for part in path.split("::"):
        current = part if not current else current + "::" + part
        index = doc.Layers.FindByFullPath(current, -1)
        if index < 0:
            layer = Rhino.DocObjects.Layer()
            layer.Name = part
            if parent != System.Guid.Empty:
                layer.ParentLayerId = parent
            if color is not None and current == path:
                layer.Color = System.Drawing.Color.FromArgb(int(color[0]), int(color[1]), int(color[2]))
            index = doc.Layers.Add(layer)
            if index < 0:
                raise _invalid("Rhino refused the layer name " + current)
        parent = doc.Layers[index].Id
    return index


def _live_objects(doc):
    """Every object in the document, hidden, locked and in blocks' reference included; not the ones Undo keeps."""

    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.HiddenObjects = True
    settings.LockedObjects = True
    settings.ReferenceObjects = True
    settings.IncludeLights = False
    return list(doc.Objects.GetObjectList(settings))


def _document(doc):
    return {"title": doc.Name, "path": doc.Path, "units": str(doc.ModelUnitSystem),
            "tolerance": doc.ModelAbsoluteTolerance, "modified": bool(doc.Modified),
            "objectCount": len(_live_objects(doc))}


def _owned(obj):
    return obj.Attributes.GetUserString(OWNER_KEY) == OWNER


def _summary(doc, obj, geometry):
    g = obj.Geometry
    a = obj.Attributes
    box = g.GetBoundingBox(True)
    keys = a.GetUserStrings()
    row = {"id": str(obj.Id), "type": str(obj.ObjectType), "layer": _layer_path(doc, a.LayerIndex),
           "name": a.Name or None, "owner": a.GetUserString(OWNER_KEY) or None,
           "bbox": [_xyz(box.Min), _xyz(box.Max)] if box.IsValid else None,
           "visible": bool(obj.Visible), "locked": bool(obj.IsLocked),
           "userText": dict((k, a.GetUserString(k)) for k in keys.AllKeys) if keys.Count else {}}
    if not geometry:
        return row
    if isinstance(g, Rhino.Geometry.Curve):
        ok, poly = g.TryGetPolyline()
        row["closed"] = bool(g.IsClosed)
        row["length"] = g.GetLength()
        if ok and poly.Count <= MAX_POINTS:
            row["points"] = [_xyz(p) for p in poly]
    elif isinstance(g, (Rhino.Geometry.Brep, Rhino.Geometry.Extrusion)):
        brep = g if isinstance(g, Rhino.Geometry.Brep) else g.ToBrep()
        row["solid"] = bool(brep.IsSolid)
        row["faces"] = brep.Faces.Count
        if brep.Vertices.Count <= MAX_POINTS:
            row["vertices"] = [_xyz(v.Location) for v in brep.Vertices]
    elif isinstance(g, Rhino.Geometry.Mesh):
        row["vertexCount"] = g.Vertices.Count
        row["faceCount"] = g.Faces.Count
        if g.Vertices.Count <= MAX_POINTS:
            row["vertices"] = [[round(v.X, 6), round(v.Y, 6), round(v.Z, 6)] for v in g.Vertices]
            row["faceIndices"] = [[f.A, f.B, f.C] + ([f.D] if f.IsQuad else []) for f in g.Faces]
    elif isinstance(g, TextDot):
        row["text"] = g.Text
        row["point"] = _xyz(g.Point)
    elif isinstance(g, Rhino.Geometry.TextEntity):
        row["text"] = g.PlainText
    elif isinstance(g, Rhino.Geometry.InstanceReferenceGeometry):
        definition = doc.InstanceDefinitions.FindId(g.ParentIdefId)
        row["block"] = definition.Name if definition else None
    return row


def _read(doc, req):
    objects = _live_objects(doc)
    prefix = req.get("layerPrefix")
    ids = set(str(i).lower() for i in (req.get("ids") or []))
    counts = {}
    picked = []
    for obj in objects:
        path = _layer_path(doc, obj.Attributes.LayerIndex) or ""
        counts[path] = counts.get(path, 0) + 1
        if prefix and not (path == prefix or path.startswith(prefix + "::")):
            continue
        if ids and str(obj.Id).lower() not in ids:
            continue
        picked.append(obj)
    offset = int(req.get("offset") or 0)
    limit = int(req.get("limit") or 500)
    page = picked[offset:offset + limit]
    layers = []
    for layer in doc.Layers:
        if layer.IsDeleted:
            continue
        c = layer.Color
        layers.append({"path": layer.FullPath, "color": "#%02X%02X%02X" % (c.R, c.G, c.B),
                       "visible": bool(layer.IsVisible), "locked": bool(layer.IsLocked),
                       "objectCount": counts.get(layer.FullPath, 0)})
    return {"document": _document(doc), "layers": layers, "total": len(picked), "offset": offset, "limit": limit,
            "objects": [_summary(doc, obj, bool(req.get("includeGeometry"))) for obj in page]}


def _closed_curve(points, z, name):
    if len(points) < 3:
        raise _invalid(name + " needs at least 3 points")
    pts = [Point3d(p.X, p.Y, z) for p in points]
    if pts[0].DistanceTo(pts[-1]) > 1e-9:
        pts.append(pts[0])
    return PolylineCurve(pts)


def _find(doc, ids, allow_user, label):
    found = []
    foreign = []
    for raw in ids or []:
        try:
            guid = System.Guid(str(raw))
        except Exception:
            raise _invalid(label + ": " + str(raw) + " is not an object id")
        obj = doc.Objects.FindId(guid)
        if obj is None or obj.IsDeleted:
            raise Refusal("LIVE_RHINO_OBJECT_NOT_FOUND", label + ": no object " + str(raw) + " in this document")
        if not _owned(obj) and not allow_user:
            foreign.append(str(raw))
        found.append(obj)
    if foreign:
        raise Refusal("LIVE_RHINO_USER_OBJECT", label + " would change the architect's own objects "
                      + ", ".join(foreign) + "; ask first and send allowUserObjects true.")
    if not found:
        raise _invalid(label + " needs ids")
    return found


def _source_layer(model, index):
    layer = model.AllLayers.FindIndex(index)
    names = []
    while layer is not None:
        names.insert(0, layer.Name)
        parent = layer.ParentLayerId
        layer = model.AllLayers.FindId(parent) if parent != System.Guid.Empty else None
    return "::".join(names)


def _transform(op):
    xform = Transform.Identity
    scale = op.get("scale")
    if scale:
        xform = Transform.Scale(_point(scale.get("center", [0, 0, 0]), "scale.center"), _number(scale.get("factor"), "scale.factor")) * xform
    rotate = op.get("rotate")
    if rotate:
        axis = rotate.get("axis", [0, 0, 1])
        xform = Transform.Rotation(System.Math.PI * _number(rotate.get("angleDeg"), "rotate.angleDeg") / 180.0,
                                   Vector3d(_point(axis, "rotate.axis")),
                                   _point(rotate.get("center", [0, 0, 0]), "rotate.center")) * xform
    translate = op.get("translate")
    if translate:
        xform = Transform.Translation(Vector3d(_point(translate, "translate"))) * xform
    return xform


def _write(doc, req):
    label = str(req.get("label") or "").strip()
    if not label:
        raise _invalid("a write needs a label")
    allow_user = bool(req.get("allowUserObjects"))
    stamp = {OWNER_KEY: OWNER, WRITE_KEY: str(req.get("writeId") or ""), LABEL_KEY: label}
    if req.get("projectId"):
        stamp[PROJECT_KEY] = str(req["projectId"])
    tol = doc.ModelAbsoluteTolerance
    planned = []  # (kind, index, payload) checked in full before the document changes
    for index, op in enumerate(req.get("operations") or []):
        kind = op.get("op")
        where = "operation " + str(index) + " (" + str(kind) + ")"
        if kind == "layer":
            planned.append(("layer", index, op))
        elif kind == "polyline":
            pts = [_point(p, where) for p in op.get("points") or []]
            if len(pts) < 2:
                raise _invalid(where + " needs at least 2 points")
            if op.get("closed") and pts[0].DistanceTo(pts[-1]) > 1e-9:
                pts.append(pts[0])
            planned.append(("add", index, (op, PolylineCurve(pts))))
        elif kind == "extrusion":
            height = _number(op.get("height"), where + " height")
            if abs(height) <= tol:
                raise _invalid(where + " height must not be zero")
            curve = _closed_curve([_point(p, where) for p in op.get("outline") or []], _number(op.get("z", 0), where + " z"), where)
            surface = Surface.CreateExtrusion(curve, Vector3d(0, 0, height))
            brep = surface.ToBrep().CapPlanarHoles(tol) if surface is not None else None
            if brep is None or not brep.IsSolid:
                raise _invalid(where + ": the outline does not make a closed solid (is it self-intersecting?)")
            planned.append(("add", index, (op, brep)))
        elif kind == "box":
            low, high = _point(op.get("min"), where + " min"), _point(op.get("max"), where + " max")
            if not (low.X < high.X and low.Y < high.Y and low.Z < high.Z):
                raise _invalid(where + ": min must be below max on every axis")
            planned.append(("add", index, (op, BoundingBox(low, high).ToBrep())))
        elif kind == "mesh":
            mesh = Mesh()
            for v in op.get("vertices") or []:
                p = _point(v, where + " vertex")
                mesh.Vertices.Add(p.X, p.Y, p.Z)
            count = mesh.Vertices.Count
            for face in op.get("faces") or []:
                if not isinstance(face, list) or len(face) not in (3, 4) or any((not isinstance(i, int)) or i < 0 or i >= count for i in face):
                    raise _invalid(where + ": a face is 3 or 4 vertex indices")
                mesh.Faces.AddFace(*face)
            mesh.Normals.ComputeNormals()
            mesh.Compact()
            if mesh.Faces.Count == 0 or not mesh.IsValid:
                raise _invalid(where + ": the mesh is not valid")
            planned.append(("add", index, (op, mesh)))
        elif kind == "dot":
            planned.append(("add", index, (op, TextDot(str(op.get("text") or ""), _point(op.get("point"), where + " point")))))
        elif kind == "import":
            path = op.get("path")
            if not isinstance(path, str) or not os.path.isabs(path) or not os.path.isfile(path) or not path.lower().endswith(".3dm"):
                raise _invalid(where + ": path must be an existing absolute .3dm file")
            model = Rhino.FileIO.File3dm.Read(path)
            if model is None:
                raise _invalid(where + ": Rhino could not read " + path)
            xform = Transform.Translation(Vector3d(_point(op.get("translate", [0, 0, 0]), where + " translate"))) * \
                Transform.Scale(Point3d.Origin, _number(op.get("scale", 1), where + " scale"))
            planned.append(("import", index, (op, model, xform)))
        elif kind == "transform":
            planned.append(("transform", index, (_find(doc, op.get("ids"), allow_user, where), _transform(op))))
        elif kind == "delete":
            planned.append(("delete", index, _find(doc, op.get("ids"), allow_user, where)))
        elif kind == "attributes":
            planned.append(("attributes", index, (_find(doc, op.get("ids"), allow_user, where), op)))
        else:
            raise _invalid(where + " is not a known operation")

    created, changed, deleted, imported = [], [], [], []

    def attributes(op, layer_path, name=None):
        a = Rhino.DocObjects.ObjectAttributes()
        a.LayerIndex = _layer(doc, layer_path)
        if op.get("name") or name:
            a.Name = op.get("name") or name
        for key, value in (op.get("userText") or {}).items():
            a.SetUserString(str(key), str(value))
        for key, value in stamp.items():
            a.SetUserString(key, value)
        return a

    for kind, index, payload in planned:
        if kind == "layer":
            _layer(doc, payload.get("path"), payload.get("color"))
        elif kind == "add":
            op, geometry = payload
            guid = doc.Objects.Add(geometry, attributes(op, op.get("layer")))
            if guid == System.Guid.Empty:
                raise _invalid("operation " + str(index) + ": Rhino refused the geometry")
            created.append({"operation": index, "id": str(guid), "op": op.get("op")})
        elif kind == "import":
            op, model, xform = payload
            target = op.get("layer")
            added, skipped = 0, {}
            for item in model.Objects:
                geometry = item.Geometry
                if geometry is None or isinstance(geometry, Rhino.Geometry.InstanceReferenceGeometry):
                    key = "block instance" if geometry is not None else "empty"
                    skipped[key] = skipped.get(key, 0) + 1
                    continue
                geometry = geometry.Duplicate()
                if not geometry.Transform(xform):
                    skipped["not transformable"] = skipped.get("not transformable", 0) + 1
                    continue
                source = _source_layer(model, item.Attributes.LayerIndex)
                layer_path = target + "::" + source if op.get("keepLayers", True) and source else target
                guid = doc.Objects.Add(geometry, attributes(op, layer_path, item.Attributes.Name))
                if guid == System.Guid.Empty:
                    skipped["refused"] = skipped.get("refused", 0) + 1
                    continue
                added += 1
            imported.append({"operation": index, "path": op.get("path"), "added": added, "skipped": skipped})
        elif kind == "transform":
            objects, xform = payload
            for obj in objects:
                guid = doc.Objects.Transform(obj.Id, xform, True)
                changed.append(str(guid))
        elif kind == "delete":
            for obj in payload:
                if doc.Objects.Delete(obj.Id, True):
                    deleted.append(str(obj.Id))
        elif kind == "attributes":
            objects, op = payload
            for obj in objects:
                a = obj.Attributes.Duplicate()
                if op.get("name") is not None:
                    a.Name = op["name"]
                if op.get("layer"):
                    a.LayerIndex = _layer(doc, op["layer"])
                for key, value in (op.get("userText") or {}).items():
                    a.SetUserString(str(key), str(value))
                doc.Objects.ModifyAttributes(obj.Id, a, True)
                changed.append(str(obj.Id))
    doc.Views.Redraw()
    return {"writeId": stamp[WRITE_KEY], "label": label, "created": created, "changed": changed,
            "deleted": deleted, "imported": imported, "document": _document(doc)}


def _capture(doc, req):
    name = req.get("view")
    view = doc.Views.Find(name, False) if name else doc.Views.ActiveView
    if view is None:
        raise Refusal("LIVE_RHINO_VIEW_NOT_FOUND", "No view " + str(name) + " in this Rhino")
    bitmap = view.CaptureToBitmap(System.Drawing.Size(int(req.get("width") or 1600), int(req.get("height") or 1000)))
    if bitmap is None:
        raise Refusal("LIVE_RHINO_CAPTURE_FAILED", "Rhino could not capture the view")
    bitmap.Save(req["png"], System.Drawing.Imaging.ImageFormat.Png)
    return {"view": view.ActiveViewport.Name}


def _snapshot(doc, req):
    # Shaded views need render meshes; a Brep never shown shaded has none, and a
    # drawing can only approximate a non-planar Brep from its saved mesh.
    meshing = doc.GetMeshingParameters(doc.MeshingParameterStyle)
    meshed = 0
    for obj in doc.Objects:
        if obj.IsDeleted or not isinstance(obj.Geometry, (Rhino.Geometry.Brep, Rhino.Geometry.Extrusion)):
            continue
        if obj.MeshCount(Rhino.Geometry.MeshType.Render, None) == 0:
            meshed += obj.CreateMeshes(Rhino.Geometry.MeshType.Render, meshing, False)
    options = Rhino.FileIO.FileWriteOptions()
    options.SuppressDialogBoxes = True
    options.IncludeRenderMeshes = True
    options.IncludePreviewImage = False
    options.IncludeHistory = False
    modified = bool(doc.Modified)
    prefixes = [p for p in (req.get("layerPrefixes") or []) if isinstance(p, str) and p]
    subset = bool(req.get("drawableOnly")) or bool(prefixes)
    written, omitted = None, {}
    selected = []
    if subset:
        # Writing a subset goes through Rhino's own "selected objects only";
        # the architect's selection is put back exactly as it was.
        selected = [obj.Id for obj in doc.Objects.GetSelectedObjects(False, False)]
        doc.Objects.UnselectAll(True)
        written = 0
        for obj in doc.Objects:
            if obj.IsDeleted:
                continue
            path = _layer_path(doc, obj.Attributes.LayerIndex) or ""
            if prefixes and not any(path == p or path.startswith(p + "::") for p in prefixes):
                continue
            if req.get("drawableOnly") and not isinstance(obj.Geometry, DRAWABLE):
                kind = str(obj.ObjectType)
                omitted[kind] = omitted.get(kind, 0) + 1
                continue
            if obj.Select(True, False, True, True, True, True):
                written += 1
        options.WriteSelectedObjectsOnly = True
    try:
        if not doc.WriteFile(req["path"], options):
            raise Refusal("LIVE_RHINO_SNAPSHOT_FAILED", "Rhino could not write a copy of the document.")
    finally:
        if subset:
            doc.Objects.UnselectAll(True)
            for guid in selected:
                doc.Objects.Select(guid, True, True)
            doc.Views.Redraw()
        doc.Modified = modified
    return {"document": _document(doc), "meshedObjects": meshed, "writtenObjects": written, "omitted": omitted}


def _main(request_path, result_path):
    try:
        with open(request_path, encoding="utf-8") as handle:
            req = json.load(handle)
        doc = Rhino.RhinoDoc.ActiveDoc
        if doc is None:
            raise Refusal("LIVE_RHINO_NO_DOCUMENT", "This Rhino has no open document.")
        expected = req.get("documentPath")
        if expected and os.path.normcase(os.path.abspath(doc.Path or "")) != os.path.normcase(os.path.abspath(expected)):
            raise Refusal("LIVE_RHINO_DOCUMENT_CHANGED", "Rhino now has " + str(doc.Path or "an unsaved document") + " open.")
        action = {"read": _read, "write": _write, "capture": _capture, "snapshot": _snapshot}.get(req.get("action"))
        if action is None:
            raise _invalid("unknown action")
        answer = action(doc, req)
        answer["ok"] = True
    except Refusal as refusal:
        answer = {"ok": False, "code": refusal.code, "detail": refusal.detail}
    except Exception:
        answer = {"ok": False, "code": "LIVE_RHINO_SCRIPT_FAILED", "detail": traceback.format_exc()[-4000:]}
    partial = result_path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(answer, handle, ensure_ascii=False)
    os.replace(partial, result_path)
'''
