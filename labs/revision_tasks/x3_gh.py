"""X3 GH: an expert's parametric Grasshopper definition of the building, operated through the Rhino MCP server.

The Rhino-specific half of mode GH in ``x3.py`` ("a projection with dependencies but no decisions"). Two things are
given at run time and never stored: the folder of the definitions (``<B>_v2.gh``, their baked defaults
``<B>_v2_default.3dm`` and ``bake_export.py``) and the Rhino MCP server's launcher (McNeel's ``rhino-mcp-platform``
extension, ``router-launcher.mjs``, started as ``node <launcher> --default-version 8 --startup-timeout 120``).

- ``RhinoMcp``: the harness's own client of that server. It starts the session's one Rhino 8 (``spawn_slot``, only when no
  Rhino runs), names that Rhino on each of its own calls, and ends it when the session ends: ``_-Exit`` first, and the
  process it started (its pid from ``spawn_slot``, nothing else) is ended if it is still there after ``EXIT_WAIT_S``. Rhino
  re-registers with the server when its Python runs for the first time, after which the server treats it as user-started
  and will not close it (measured), and a Rhino that ran ``_-Exit`` was left without a window and did not end (measured).
- Its steps, each a ``run_python`` script: the document in metres with 0.001 tolerance (``bake_export.py`` refuses
  anything else), Grasshopper started, a definition opened (every other Grasshopper document removed first), the
  ``PROJECT NOTES`` panel written (request 0), the definition saved, and the bake and export (``bake_export.py`` as is).
  A returned model is opened with the server's own ``open_doc`` (the document cleared, the file imported).
- ``proxy_settings``: the lab proxy (``x3_hub.serve_proxy``) in front of each turn's own server process: the server's
  tools less ``EXCLUDED_TOOLS``, the 30-call cap as in every mode and the CLI's ceiling as the result size
  (``RESULT_SIZE_CHARS``, 500,000; the other modes declare 400,000), the session's Rhino named on every call that names
  none (``rhino_pid``; the server would otherwise start another Rhino when it finds none), ``RENDER_DENY`` (no render, no
  rendered or raytraced display), and every code payload and command kept in the call log (``LOGGED_ARGUMENTS``), which
  ``code_review`` scans for file access (``FILE_ACCESS``; flags for hand review). GH's system prompt is the common one and
  ``ADDENDUM`` (``system_prompt``).
- The measurement copy (protocol owner's decision, replacing manifest 7.4's reading of the stored render meshes): after
  each bake the harness meshes every baked object from its Brep within ``DEVIATION_M`` and edges of at most ``MAX_EDGE_M``
  (``RhinoMcp.measure``; the export the agent and a consultant see keeps Rhino's own meshes), and ``measurement_copy``
  writes those meshes under the names BA's measures use for its front row (``column_names``). ``render_mesh_model`` still
  reads an export's stored meshes, for comparing exports.
- How GH's objects answer the request file's rows (``judging``, all GH only): its single dome, oculus ring and attic stand
  for the eight sectors the rows name (``adapt_measure`` says how per kind), keep rows read GH's own start
  (``KEEP_ROWS``), bounds derived from the drum's circle take the 48-gon's chord height as tolerance (``CIRCLE_ROWS``),
  and at a declared curved seam (``CURVED_SEAMS``) a contact, and the depth below which the collision check does not
  count a sample point, take the meshes' deviation bound (``SEAM_TOLERANCE_M``).
- ``method``: whether a turn changed the definition, the Rhino document's geometry other than by baking, both or neither,
  from ``SNAPSHOT``s before and after the turn and after the harness's bake.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

SERVER = "rhino"
LAB = Path(__file__).resolve().parent
# GH's sentence after the common system prompt (x3_system.md stays as frozen).
ADDENDUM = LAB / "prompts" / "x3_gh_addendum.md"
# The result size GH's tools declare: the CLI's own ceiling (the other modes keep x3_hub.RESULT_SIZE_CHARS, 400,000).
RESULT_SIZE_CHARS = 500000
# The arguments the proxy keeps in its call log: the code and commands the agent runs in Rhino.
LOGGED_ARGUMENTS = {"run_python": "script", "run_csharp": "script", "run_command": "command"}
# A scan of that code for reading or writing files (for hand review; a match is a flag, not a finding).
FILE_ACCESS = (("open(", r"\bopen\s*\("), ("System.IO", r"System\.IO"), ("File.", r"\bFile\."), ("Path", r"Path"), ("os.", r"\bos\."),
               ("pathlib", r"pathlib"), ("Read", r"Read"), ("Write", r"Write"), ("StreamReader/Writer", r"Stream(Reader|Writer)"),
               ("Directory", r"Directory"), ("shutil", r"shutil"), ("glob", r"\bglob\b"), ("subprocess/Process", r"subprocess|Process\."),
               ("network", r"urllib|requests\.|WebClient|HttpClient|socket"), ("import/export/save", r"(?i)[_\-!]+\s*(import|export|saveas|save)\b|\.(export|import|saveas)\s*\("),
               ("__file__", r"__file__"))
DEFINITIONS = {"BA": "BA_v2.gh", "BB": "BB_v2.gh"}
DEFAULT_MODELS = {"BA": "BA_v2_default.3dm", "BB": "BB_v2_default.3dm"}
BAKE_SCRIPT = "bake_export.py"
NOTES_PANEL = "PROJECT NOTES"
TOLERANCE_M = 0.001
DEVIATION_M = 0.001            # the measurement meshes' largest distance from the Brep (the export keeps Rhino's own meshes)
# Rhino's tolerance alone left trimmed ring faces up to 0.003 m off (measured); an edge no longer than this keeps a chord
# on the drum's circles (R >= 21.65 m) within L^2/(8R) < 0.001 m. Planar faces stay as few triangles (simple planes).
MAX_EDGE_M = 0.4
# The deviation the measurement meshes keep at the drum's seams: 0.001 asked; up to 0.00145 measured where a trimmed face's
# boundary edge is long (GH's BA start). A contact measured at a declared curved seam takes this as its tolerance.
SEAM_TOLERANCE_M = 0.002
# Not given to the agent: another Rhino (one instance on this machine), closing the session's Rhino, and a question to a
# person in Rhino's own panel (no one is there; the agent asks in its answer, as in every other mode).
EXCLUDED_TOOLS = ("spawn_slot", "close_slot", "ask_user")
# The machine's rule: viewports wireframe or shaded, never a render, never the GPU for rendering. (tool, argument,
# pattern, detail): the proxy refuses a call whose argument matches.
_NO_RENDER = "Rendering, and rendered or raytraced display, are not available on this machine; use Wireframe or Shaded."
RENDER_DENY = (
    ("get_viewport_image", "displayMode", r"(?i)^\s*(?!(wireframe|shaded)\s*$)\S", _NO_RENDER),
    ("run_command", "command", r"(?i)render|raytrac|cycles", _NO_RENDER),
    ("run_command", "command", r"(?i)displaymode(?!.*\b(wireframe|shaded)\b)", _NO_RENDER),
    ("run_python", "script", r"(?i)raytrac|cycles|[\"']\s*[_\-!]*\s*render|RenderPipeline", _NO_RENDER),
    ("run_csharp", "script", r"(?i)raytrac|cycles|[\"']\s*[_\-!]*\s*render|RenderPipeline", _NO_RENDER),
)
SPAWN_TIMEOUT_S = 400
CALL_TIMEOUT_S = 300
EXIT_WAIT_S = 30
RESULT_MARK = "X3JSON "


class RhinoError(RuntimeError):
    pass


# ---------------------------------------------------------------- files

def router_command(launcher: str) -> list[str]:
    return ["node", str(launcher), "--default-version", "8", "--startup-timeout", "120"]


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance(folder: Path, building: str) -> dict[str, Any]:
    """The definition, its default model and the bake script a GH session starts from, by name and SHA-256."""

    folder = Path(folder)
    names = (DEFINITIONS[building], DEFAULT_MODELS[building], BAKE_SCRIPT)
    missing = [n for n in names if not (folder / n).is_file()]
    if missing:
        raise RhinoError(f"the GH folder lacks {', '.join(missing)}")
    return {n: _sha(folder / n) for n in names}


def session_files(folder: Path, building: str, into: Path) -> dict[str, Any]:
    """Copies of the building's definition and default model under ``into`` (the session's own); the bake script is read
    where it is."""

    folder, into = Path(folder), Path(into)
    into.mkdir(parents=True, exist_ok=False)
    definition = into / DEFINITIONS[building]
    default = into / DEFAULT_MODELS[building]
    shutil.copyfile(folder / DEFINITIONS[building], definition)
    shutil.copyfile(folder / DEFAULT_MODELS[building], default)
    return {"definition": definition, "default": default, "bake": folder / BAKE_SCRIPT, "source_name": DEFINITIONS[building],
            "sha256": provenance(folder, building)}


def opening_notes(spec: dict[str, Any]) -> str:
    """Request 0 as the ``PROJECT NOTES`` panel carries it: the opening conditions and the history's rejections, in the
    architect's words (the texts M records as keep and reject decisions)."""

    opening = spec["opening"]
    lines = ["Conditions the architect set for this project:", *(f"- {c['text']}" for c in opening["conditions"]), "",
             "Proposals rejected before:", *(f"- {h['proposal']} {h['rejection']}" for h in opening["history"])]
    return "\n".join(lines)


# ---------------------------------------------------------------- the measurement copy

def joined_mesh(geometry: Any) -> tuple[Any, str]:
    """(mesh, how): a mesh as it is, or a Brep's stored face render meshes appended into one mesh; (None, why) when a face
    has no stored mesh or the geometry is neither."""

    import rhino3dm

    if isinstance(geometry, rhino3dm.Mesh):
        return geometry, "mesh"
    faces = getattr(geometry, "Faces", None)
    if faces is None:
        return None, type(geometry).__name__
    mesh = rhino3dm.Mesh()
    for i in range(len(faces)):
        part = faces[i].GetMesh(rhino3dm.MeshType.Any)
        if part is None:
            return None, "no_stored_mesh"
        mesh.Append(part)
    return mesh, "faces"


def write_measurement(objects: list[tuple[str, Any]], out: Path, unit_system: Any = None, building: str | None = None) -> dict[str, Any]:
    """The named objects' ``joined_mesh`` written to ``out`` (new) as named meshes, BA's front row under the names its
    measures use (``column_names``); what was renamed, what was left out and why."""

    import rhino3dm

    out = Path(out)
    if out.exists():
        raise RhinoError(f"{out.name} exists")
    meshes, left_out = [], []
    for name, geometry in objects:
        mesh, how = joined_mesh(geometry)
        if mesh is None:
            left_out.append([name, how])
        else:
            meshes.append((name, mesh))
    centres = {}
    for name, mesh in meshes:
        box = mesh.GetBoundingBox()
        centres[name] = (box.Min.X + box.Max.X) / 2
    renamed = column_names(building, centres) if building else {}
    written = rhino3dm.File3dm()
    if unit_system is not None:
        written.Settings.ModelUnitSystem = unit_system
    for name, mesh in meshes:
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = renamed.get(name, name or "")
        written.Objects.AddMesh(mesh, attributes)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not written.Write(str(out), 8):
        raise RhinoError(f"rhino3dm could not write {out.name}")
    return {"path": str(out), "sha256": _sha(out), "objects": len(meshes), "renamed": renamed, "left_out": left_out}


def measurement_copy(source: Path, out: Path, building: str | None = None) -> dict[str, Any]:
    """The model the oracle judges for a GH export: its named meshes (the measurement meshes ``RhinoMcp.measure`` wrote, or
    a Brep's stored face render meshes joined) with BA's front row named as its measures name it."""

    import rhino3dm

    model = rhino3dm.File3dm.Read(str(source))
    if model is None:
        raise RhinoError(f"rhino3dm cannot read {Path(source).name}")
    record = write_measurement([(o.Attributes.Name, o.Geometry) for o in model.Objects], out, model.Settings.ModelUnitSystem, building)
    return {"source": str(source), "source_sha256": _sha(Path(source)), **record}


def render_mesh_model(source: Path, out: Path, building: str | None = None) -> dict[str, Any]:
    """A GH export's stored render meshes as the measurement copy (``measurement_copy``; the first GH pilot measured so)."""

    return {**measurement_copy(source, out, building), "how": "each Brep's stored face render meshes joined; nothing meshed again"}


def mesh_boxes(path: Path) -> dict[str, dict[str, Any]]:
    """Each named mesh's box and vertex count (a measurement copy, read by the harness)."""

    import rhino3dm

    rows = {}
    for obj in rhino3dm.File3dm.Read(str(path)).Objects:
        if isinstance(obj.Geometry, rhino3dm.Mesh) and obj.Attributes.Name:
            box = obj.Geometry.GetBoundingBox()
            rows[obj.Attributes.Name] = {"min": [round(box.Min.X, 6), round(box.Min.Y, 6), round(box.Min.Z, 6)],
                                         "max": [round(box.Max.X, 6), round(box.Max.Y, 6), round(box.Max.Z, 6)],
                                         "vertices": len(obj.Geometry.Vertices)}
    return rows


def compare(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]], tolerance: float = 1e-6) -> dict[str, list[str]]:
    """Objects whose box or vertex count differ, and those added or removed (the harness's own summary, not the oracle's)."""

    def differs(a, b):
        return a["vertices"] != b["vertices"] or any(abs(p - q) > tolerance for k in ("min", "max") for p, q in zip(a[k], b[k]))

    common = sorted(set(before) & set(after))
    return {"changed": [n for n in common if differs(before[n], after[n])], "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after))}


# ---------------------------------------------------------------- how GH's objects answer BA's measures

# BA's front row: GH bakes it F1..FN, west to east; MonkeyHub names it as BA's measures do: the corners F1 and F8 and the
# middle columns as two arrays, front-W-0.. and front-E-0.. (west to east, half each; the count is even). The measurement
# copy gives GH's columns those names, ordered by their centres' x (one name for one column).
FRONT_ROW = {"BA": "obj-portico-col-F"}
# GH builds each of these as one solid where BA's measures name MonkeyHub's eight 45-degree sectors <stem>0..<stem>7.
SECTOR_WHOLES = {"BA": {"obj-dome": "obj-dome-", "obj-oculus-ring": "obj-oculus-ring-", "obj-rotunda-attic": "obj-rotunda-attic-"}}
SECTORS = 8
# Absolute bounds that keep an object where it stood: compared with GH's own start (the session's for an opening
# condition, the request's for a request row), at the row's own tolerance. Keys: condition id, or "<request>:<row>".
KEEP_ROWS = {"BA": {"K5": "session", "A10:R2-recorded": "request"}, "BB": {"B10:parapet-N-recorded": "request"}}
# Plan bounds that derive from the drum's circle, which MonkeyHub builds as a 48-gon and GH as a circle: the radius they
# derive from (after A1: inner 22.15, outer 22.15 + 6.40 = 28.55). Their tolerance is the 48-gon's chord height there.
CIRCLE_ROWS = {"BA": {"A1:drum-outer": 28.55, "A1:floor": 22.15, "A1:block-rear": 28.55}}
CIRCLE_SIDES = 48
# Faces of revolution of two members that meet (pairs of name prefixes): there two measurement meshes may overlap by
# up to their deviation: a contact measured between such members takes SEAM_TOLERANCE_M as its tolerance, and the
# collision check counts a sample point between them only when it lies deeper than that (oracle ``seams``).
# (Found on GH's BA: the pairs whose boxes overlap and whose meshes lie on one drum circle, less the sectors' radial cuts.)
CURVED_SEAMS = {"BA": (("obj-intermediate-block", "obj-rotunda-wall-"), ("obj-intermediate-block", "obj-rotunda-attic"),
                       ("obj-intermediate-block", "obj-rotunda-cornice-"), ("obj-intermediate-roof", "obj-rotunda-attic"),
                       ("obj-intermediate-roof", "obj-rotunda-cornice-"), ("obj-rotunda-attic", "obj-rotunda-cornice-"),
                       ("obj-rotunda-cornice-", "obj-rotunda-wall-"), ("obj-rotunda-floor", "obj-rotunda-wall-"))}
CONTACT_CHECKS = ("support_contact",)


def circle_tolerance(radius: float, sides: int = CIRCLE_SIDES) -> float:
    """R(1 - cos(pi/n)): how far an n-gon's sides fall inside its circle (rounded up to 0.1 mm)."""

    return math.ceil(radius * (1 - math.cos(math.pi / sides)) * 1e4) / 1e4


def column_names(building: str, centres: dict[str, float]) -> dict[str, str]:
    """{GH name: measured name} for BA's front row, by the columns' centre x (``FRONT_ROW``); only names that change."""

    stem = FRONT_ROW.get(building)
    if stem is None:
        return {}
    row = sorted((x, int(n[len(stem):]), n) for n, x in centres.items() if n.startswith(stem) and n[len(stem):].isdigit())
    if len(row) < 2:
        return {}
    names = [n for _, _, n in row]
    middle = names[1:-1]
    half = len(middle) // 2
    mapping = {names[0]: f"{stem}1", names[-1]: f"{stem}8",
               **{n: f"obj-portico-col-front-W-{i}" for i, n in enumerate(middle[:half])},
               **{n: f"obj-portico-col-front-E-{i}" for i, n in enumerate(middle[half:])}}
    return {k: v for k, v in mapping.items() if k != v}


def _sector_wholes(pattern: str, building: str) -> list[tuple[str, bool]]:
    """(whole, every sector) for each GH whole whose sectors ``pattern`` names while the whole's own name does not."""

    hits = []
    for whole, stem in SECTOR_WHOLES.get(building, {}).items():
        if fnmatch.fnmatchcase(whole, pattern):
            continue
        named = [fnmatch.fnmatchcase(f"{stem}{k}", pattern) for k in range(SECTORS)]
        if any(named):
            hits.append((whole, all(named)))
    return hits


def with_wholes(patterns: list[str], building: str) -> list[str]:
    """Name patterns that also name the GH whole of any sector they name (unchanged, unrequested, excepted objects)."""

    extra = [whole for p in patterns for whole, _ in _sector_wholes(p, building)]
    return [*patterns, *sorted(set(extra) - set(patterns))]


def adapt_measure(measure: dict[str, Any], building: str) -> tuple[dict[str, Any], list[str]]:
    """One measure as GH's objects answer it (``SECTOR_WHOLES``), and how. Per kind:

    - ``within``: the whole object; exact when the pattern names all eight sectors (the whole lies in the region iff each
      sector does); for some sectors only, the whole is held to the region (stricter in plan, said in the note).
    - ``bbox``: the whole object's height bounds, which every sector shares; a sector's plan bounds are not the whole's
      and are dropped (listed).
    - ``geometry_unchanged`` (``objects``, ``x3_except``): the whole is unchanged iff every sector is; a pattern naming a
      sector names the whole. Unrequested change is judged the same way (``with_wholes`` on the request's ``change``).
    - name lists (``lower``, ``upper``, ``a``, ``b``): a sector is replaced by its whole (the box of all sectors is the
      whole's box); ``collisions``' accepted pairs gain the whole where a prefix names only sectors.
    - any other kind naming one sector is measured on the whole (said in the note).
    """

    m, notes = json.loads(json.dumps(measure)), []
    check = m.get("check")
    if isinstance(m.get("object"), str):
        hits = _sector_wholes(m["object"], building)
        if len(hits) == 1:
            (whole, every), named = hits[0], m["object"]
            m["object"] = whole
            if check == "bbox":
                dropped = {side: (m.get(side) or [None] * 3)[:2] for side in ("min", "max") if any(v is not None for v in (m.get(side) or [None] * 3)[:2])}
                for side in ("min", "max"):
                    m[side] = [None, None, (m.get(side) or [None] * 3)[2]]
                notes.append(f"bbox {named} -> {whole}: height bounds, which every sector shares"
                             + (f"; a sector's plan bounds dropped {dropped}" if dropped else ""))
            elif check == "within":
                notes.append(f"within {named} -> {whole}: " + ("the whole lies in the region iff every sector does" if every
                                                                else "the whole held to the region (stricter in plan than the sectors named)"))
            else:
                notes.append(f"{check} {named} -> {whole}: measured on the whole object")
        elif hits:
            notes.append(f"{m['object']} names sectors of several wholes; left as written")
    for key in ("lower", "upper", "a", "b"):
        if isinstance(m.get(key), list):
            mapped = []
            for name in m[key]:
                hits = _sector_wholes(name, building)
                mapped.append(hits[0][0] if len(hits) == 1 else name)
            mapped = list(dict.fromkeys(mapped))
            if mapped != m[key]:
                notes.append(f"{key} {m[key]} -> {mapped}")
                m[key] = mapped
    for key in ("objects", "x3_except"):
        if isinstance(m.get(key), list):
            widened = with_wholes(m[key], building)
            if widened != m[key]:
                notes.append(f"{key} also names {widened[len(m[key]):]}")
                m[key] = widened
    if check == "collisions" and m.get("allowed"):
        extra = []
        for p, q in m["allowed"]:
            for prefix, other in ((p, q), (q, p)):
                for whole, stem in SECTOR_WHOLES.get(building, {}).items():
                    if stem.startswith(prefix) and not whole.startswith(prefix):
                        extra.append([whole, other])
        if extra:
            notes.append(f"accepted pairs gain {extra}")
            m["allowed"] = [*m["allowed"], *extra]
    return m, notes


def adapt_row(row: dict[str, Any], key: str, building: str, reference: dict[str, dict[str, Any]] | None) -> list[dict[str, Any]]:
    """GH's rows for one condition or request row (``key``: the condition id or ``<request>:<row>``): the sector mapping
    (``adapt_measure``), a keep row's numbers from GH's own start (``reference``: that start's mesh boxes), a circle
    row's tolerance (split into a plan row and a height row when it bounds both), and at a declared curved seam a
    contact toleranced, and a collision counted only deeper than, ``SEAM_TOLERANCE_M``. Each changed row carries
    ``x3_gh``: what was changed and the original measure."""

    measure, notes = adapt_measure(row["measure"], building)
    rows = [{**row, "measure": measure}]
    if key in KEEP_ROWS.get(building, {}) and measure.get("check") == "bbox":
        box = (reference or {}).get(measure["object"])
        if box is None:
            notes.append(f"keep row: {measure['object']} is not in GH's start; left as written")
        else:
            for side in ("min", "max"):
                measure[side] = [None if v is None else box[side][i] for i, v in enumerate(measure.get(side) or [None] * 3)]
            notes.append(f"keep row: bounds from GH's own {KEEP_ROWS[building][key]} start, tolerance {measure.get('tolerance_m', 0.002)} m")
    radius = CIRCLE_ROWS.get(building, {}).get(key)
    if radius is not None and measure.get("check") == "bbox":
        tolerance = circle_tolerance(radius)
        plan = {side: [*(measure.get(side) or [None] * 3)[:2], None] for side in ("min", "max")}
        height = {side: [None, None, (measure.get(side) or [None] * 3)[2]] for side in ("min", "max")}
        rule = f"circle R {radius} m: tolerance R(1 - cos(pi/{CIRCLE_SIDES})) = {tolerance} m on the plan bounds"
        if any(v is not None for side in height.values() for v in side):
            rows = [{**row, "id": f"{row['id']}:plan", "measure": {**measure, **plan, "tolerance_m": tolerance}},
                    {**row, "id": f"{row['id']}:height", "measure": {**measure, **height}}]
            notes.append(rule + "; the height bounds keep their own tolerance in their own row")
        else:
            measure["tolerance_m"] = tolerance
            notes.append(rule)
    if measure.get("check") == "collisions" and CURVED_SEAMS.get(building):
        for r in rows:
            r["measure"]["seams"] = [list(pair) for pair in CURVED_SEAMS[building]]
            r["measure"]["seam_depth_m"] = SEAM_TOLERANCE_M
        notes.append(f"collisions at the declared curved seams count a sample point only deeper than {SEAM_TOLERANCE_M} m "
                     "(the measurement meshes' deviation bound); the points within it are reported apart")
    if measure.get("check") in CONTACT_CHECKS:
        names = [*(measure.get("lower") or []), *(measure.get("upper") or [])]
        if any(any(n.startswith(p) for n in names) and any(n.startswith(q) for n in names) for p, q in CURVED_SEAMS.get(building, ())):
            for r in rows:
                r["measure"]["tolerance_m"] = max(float(r["measure"].get("tolerance_m", 0.001)), SEAM_TOLERANCE_M)
            notes.append(f"contact at a declared curved seam: tolerance {SEAM_TOLERANCE_M} m (the measurement meshes' deviation bound)")
    if notes:
        for r in rows:
            r["x3_gh"] = {"key": key, "notes": notes, "written": row["measure"]}
    return rows


def judging(spec: dict[str, Any], request: dict[str, Any], building: str, *, session_start: dict[str, dict[str, Any]] | None,
            request_start: dict[str, dict[str, Any]] | None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """The building's conditions and the request's rows and change list as GH's objects answer them (``adapt_row``,
    ``with_wholes``); the third value lists every row that differs from the request file."""

    conditions = [r for c in spec["conditions"] for r in adapt_row(c, c["id"], building, session_start)]
    measures = [r for m in request["measures"] for r in adapt_row(m, f"{request['id']}:{m['id']}", building, request_start)]
    change = with_wholes(list(request.get("change", [])), building)
    changed = [{"id": r["id"], **r["x3_gh"], "measure": r["measure"]} for r in (*conditions, *measures) if "x3_gh" in r]
    if change != request.get("change", []):
        changed.append({"id": "change", "key": f"{request['id']}:change", "notes": [f"also names {change[len(request['change']):]}"],
                        "written": request["change"], "measure": change})
    return {**spec, "conditions": conditions}, {**request, "measures": measures, "change": change}, changed


METHODS = ("definition", "geometry", "both", "none", "unknown")


def method(before: dict[str, Any], after: dict[str, Any], baked: dict[str, Any] | None) -> dict[str, Any]:
    """How a turn worked, from ``SNAPSHOT``s before it, after it and after the harness's bake:

    - ``definition``: the definition changed (sliders, wiring, set values, components, expressions, panels);
    - ``geometry``: the Rhino document's geometry changed other than by baking the definition: objects the bake did not
      make were added, moved or removed, or the bake's own objects stand where neither the turn's start nor the
      definition (the harness's bake after the turn) puts them. Such geometry is not carried: the export is the bake;
    - ``both``, ``none``; ``unknown`` when a snapshot failed.
    """

    if any(s is None or "error" in s for s in (before, after, baked)):
        return {"class": "unknown", "errors": [s.get("error") for s in (before, after, baked) if s and "error" in s]}
    d0, d1 = before.get("definition"), after.get("definition")
    changed = sorted(k for k in set(d0 or {}) & set(d1 or {}) if d0[k] != d1[k])
    definition = {"changed": [[k, *d1[k][:2]] for k in changed], "added": sorted(set(d1 or {}) - set(d0 or {})),
                  "removed": sorted(set(d0 or {}) - set(d1 or {})), "closed": d0 is not None and d1 is None,
                  "path_before": before.get("definition_path"), "path_after": after.get("definition_path")}
    by_definition = bool(definition["changed"] or definition["added"] or definition["removed"] or definition["closed"]
                         or definition["path_before"] != definition["path_after"])

    def part(snapshot, made_by_bake):
        rows = [json.dumps(r) for r in snapshot["document"] if bool(r[2]) == made_by_bake]
        return sorted(rows)

    other0, other1 = part(before, False), part(after, False)
    bake0, bake1, bake2 = part(before, True), part(after, True), part(baked, True)
    document = {"other_added": sorted(set(other1) - set(other0)), "other_removed": sorted(set(other0) - set(other1)),
                "baked_differs_from_start": bake1 != bake0, "baked_differs_from_definition": bake1 != bake2}
    direct = bool(document["other_added"] or document["other_removed"] or (bake1 != bake0 and bake1 != bake2))
    label = "both" if by_definition and direct else "definition" if by_definition else "geometry" if direct else "none"
    document["other_added"] = [json.loads(r) for r in document["other_added"][:50]]
    document["other_removed"] = [json.loads(r) for r in document["other_removed"][:50]]
    return {"class": label, "definition": definition, "document": document}


def largest_result(log: Path) -> dict[str, Any] | None:
    """The largest text result the proxy passed on in one turn (its ``calls.jsonl``)."""

    if not Path(log).is_file():
        return None
    rows = [json.loads(line) for line in Path(log).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if isinstance(r.get("chars"), int)]
    if not rows:
        return None
    top = max(rows, key=lambda r: r["chars"])
    return {"chars": top["chars"], "tool": top.get("name"), "call": top.get("call"), "calls_logged": len(rows)}


# ---------------------------------------------------------------- the proxy for a turn

def proxy_settings(launcher: str, *, log: Path, stderr: Path, tools: tuple[str, ...], rhino_pid: int, max_calls: int) -> dict[str, Any]:
    """``x3_hub.serve_proxy``'s configuration in front of a turn's own Rhino MCP server process: GH's result size
    (``RESULT_SIZE_CHARS``) and the code and commands kept in the call log (``LOGGED_ARGUMENTS``)."""

    return {"child": router_command(launcher), "log": str(log), "stderr": str(stderr), "max_calls": max_calls, "tools": list(tools),
            "deny": [list(rule) for rule in RENDER_DENY], "rhino_pid": int(rhino_pid), "result_size_chars": RESULT_SIZE_CHARS,
            "log_arguments": dict(LOGGED_ARGUMENTS)}


def system_prompt(common: Path, out: Path) -> dict[str, Any]:
    """GH's system prompt: the common prompt as frozen, then ``ADDENDUM`` (written to ``out``, new)."""

    text = Path(common).read_text(encoding="utf-8").rstrip("\n") + "\n\n" + ADDENDUM.read_text(encoding="utf-8").strip() + "\n"
    out = Path(out)
    if out.exists():
        raise RhinoError(f"{out.name} exists")
    out.write_text(text, encoding="utf-8", newline="\n")
    return {"path": str(out), "sha256": _sha(out), "common_sha256": _sha(Path(common)), "addendum_sha256": _sha(ADDENDUM)}


def code_review(log: Path) -> dict[str, Any]:
    """Every code payload and command the proxy logged in one turn, with the file-access patterns each matches
    (``FILE_ACCESS``, a scan for hand review: it flags more than it proves). Flagged payloads are kept whole."""

    rows = [json.loads(line) for line in Path(log).read_text(encoding="utf-8").splitlines() if line.strip()] if Path(log).is_file() else []
    payloads = []
    for row in rows:
        if "payload" not in row:
            continue
        code = row["payload"]
        flags = sorted({label for label, pattern in FILE_ACCESS if re.search(pattern, code)})
        payloads.append({"call": row.get("call"), "tool": row.get("name"), "chars": len(code), "refused": row.get("refused"),
                         "flags": flags, **({"payload": code} if flags else {})})
    flagged = [p for p in payloads if p["flags"]]
    return {"payloads": len(payloads), "flagged": len(flagged), "needs_hand_review": bool(flagged), "rows": payloads, "log": str(log)}


def agent_tools(listed: list[dict[str, Any]]) -> tuple[str, ...]:
    """The server's tools the agent is given: all it lists, less ``EXCLUDED_TOOLS``."""

    return tuple(t["name"] for t in listed if t.get("name") and t["name"] not in EXCLUDED_TOOLS)


# ---------------------------------------------------------------- Rhino processes

def running_rhinos() -> list[int]:
    """The pids of every running ``Rhino.exe`` (Windows)."""

    done = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Rhino.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                          encoding="mbcs" if os.name == "nt" else "utf-8", errors="replace", check=False)
    pids = []
    for line in done.stdout.splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if cells and cells[0].lstrip('"').lower() == "rhino.exe" and len(cells) > 1 and cells[1].isdigit():
            pids.append(int(cells[1]))
    return pids


def _text(result: dict[str, Any]) -> str:
    return "".join(c.get("text", "") for c in (result.get("content") or ()) if isinstance(c, dict) and c.get("type") == "text")


def server_error(text: str) -> dict[str, Any] | None:
    """The server answers a failure as ``{"error": {"code", "message"}}`` text, not as an MCP error (measured)."""

    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value.get("error") if isinstance(value, dict) and isinstance(value.get("error"), dict) else None


def script_result(text: str) -> dict[str, Any]:
    """A harness script's printed output and the JSON it marked with ``RESULT_MARK``."""

    try:
        value = json.loads(text)
    except ValueError:
        value = {"payload": text}
    out = value.get("payload") if isinstance(value, dict) else text
    out = out if isinstance(out, str) else json.dumps(out)
    marked = [line[len(RESULT_MARK):] for line in out.splitlines() if line.startswith(RESULT_MARK)]
    return {"out": out, "value": json.loads(marked[-1]) if marked else None}


def _literals(**values: Any) -> str:
    return "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items())


# Every script is run with ``run_python`` in the session's Rhino and prints one RESULT_MARK line.
PREPARE_DOCUMENT = """import json, Rhino
doc = __rhino_doc__
before = [str(doc.ModelUnitSystem), doc.ModelAbsoluteTolerance]
if doc.ModelUnitSystem != Rhino.UnitSystem.Meters:
    doc.AdjustModelUnitSystem(Rhino.UnitSystem.Meters, False)
doc.ModelAbsoluteTolerance = TOLERANCE
modes = sorted(set(str(v.ActiveViewport.DisplayMode.EnglishName) for v in doc.Views))
print("X3JSON " + json.dumps({"before": before, "units": str(doc.ModelUnitSystem), "tolerance": doc.ModelAbsoluteTolerance,
                              "display_modes": modes, "objects": doc.Objects.Count}))
"""

OPEN_DEFINITION = """import json, Grasshopper as gh
canvas = gh.Instances.ActiveCanvas
if canvas is None:
    raise RuntimeError("Grasshopper's canvas is not open")
server = gh.Instances.DocumentServer
removed = []
for d in list(server):
    removed.append([d.FilePath, bool(d.IsModified)])
    server.RemoveDocument(d)
io = gh.Kernel.GH_DocumentIO()
if not io.Open(PATH):
    raise RuntimeError("Grasshopper could not open " + PATH)
gdoc = io.Document
server.AddDocument(gdoc)
canvas.Document = gdoc
gdoc.Enabled = True
gdoc.NewSolution(True)
errors = []
for o in gdoc.Objects:
    o = getattr(o, "__implementation__", o)
    if hasattr(o, "RuntimeMessages"):
        errors += ["%s: %s" % (o.NickName, m) for m in o.RuntimeMessages(gh.Kernel.GH_RuntimeMessageLevel.Error)]
print("X3JSON " + json.dumps({"path": gdoc.FilePath, "objects": gdoc.ObjectCount, "removed": removed, "errors": errors}))
"""

WRITE_NOTES = """import json, Grasshopper as gh
gdoc = gh.Instances.ActiveCanvas.Document
panels = [p for p in (getattr(o, "__implementation__", o) for o in gdoc.Objects) if p.NickName == PANEL and hasattr(p, "UserText")]
if len(panels) != 1:
    raise RuntimeError("%d panels named %s" % (len(panels), PANEL))
before = panels[0].UserText
panels[0].UserText = TEXT
panels[0].ExpireSolution(True)
print("X3JSON " + json.dumps({"panel": PANEL, "before": before, "after": panels[0].UserText}))
"""

SAVE_DEFINITION = """import json, Grasshopper as gh
canvas = gh.Instances.ActiveCanvas
gdoc = canvas.Document if canvas is not None else None
if gdoc is None:
    raise RuntimeError("no Grasshopper document is open")
active = gdoc.FilePath
saved = [[p, bool(gh.Kernel.GH_DocumentIO(gdoc).SaveQuiet(p))] for p in PATHS]
print("X3JSON " + json.dumps({"active": active, "saved": saved, "path_after": gdoc.FilePath}))
"""

BAKE = """import json, Grasshopper as _gh
_canvas = _gh.Instances.ActiveCanvas
_active = _canvas.Document.FilePath if _canvas is not None and _canvas.Document is not None else None
exec(open(BAKE_SCRIPT, encoding="utf-8").read(), globals())
print("X3JSON " + json.dumps({"active": _active, "written": bool(ok), "objects": len(ids), "problems": problems}))
"""

MEASURE = """import json, Rhino, Rhino.Geometry as rg
doc = __rhino_doc__
mp = rg.MeshingParameters()
mp.Tolerance = DEVIATION
mp.MaximumEdgeLength = MAX_EDGE
mp.RelativeTolerance = 0.0
mp.MinimumTolerance = 0.0
mp.RefineGrid = True
mp.SimplePlanes = True
mp.JaggedSeams = False
model = Rhino.FileIO.File3dm()
model.Settings.ModelUnitSystem = doc.ModelUnitSystem
rows, problems = [], []
for ro in doc.Objects:
    a = ro.Attributes
    if a.GetUserString("building") != BUILDING or not (a.GetUserString("source") or "").endswith(".gh"):
        continue
    g = ro.Geometry
    brep = g if isinstance(g, rg.Brep) else (g.ToBrep() if hasattr(g, "ToBrep") else None)
    if brep is None:
        problems.append("%s: %s is not meshed" % (a.Name, type(g).__name__))
        continue
    mesh = rg.Mesh()
    for part in rg.Mesh.CreateFromBrep(brep, mp) or []:
        mesh.Append(part)
    if mesh.Faces.Count == 0:
        problems.append("%s: no mesh" % a.Name)
        continue
    attributes = Rhino.DocObjects.ObjectAttributes()
    attributes.Name = a.Name
    model.Objects.AddMesh(mesh, attributes)
    rows.append([a.Name, mesh.Vertices.Count, mesh.Faces.Count])
ok = model.Write(OUT_PATH, 8)
print("X3JSON " + json.dumps({"written": bool(ok), "objects": len(rows), "deviation_m": DEVIATION, "max_edge_m": MAX_EDGE,
                              "faces": sum(r[2] for r in rows),
                              "problems": problems}))
"""

# The definition (each object: kind, nickname, slider value and range, panel text, expression, each input's sources and
# set values) and the document (each object: name, kind, made by the bake, box), for the turn's method (``method``).
SNAPSHOT = """import json, Grasshopper as gh
doc = __rhino_doc__
canvas = gh.Instances.ActiveCanvas
gdoc = canvas.Document if canvas is not None else None
definition = None
if gdoc is not None:
    definition = {}
    for o in gdoc.Objects:
        o = getattr(o, "__implementation__", o)
        row = [o.GetType().Name, o.NickName]
        if hasattr(o, "Slider"):
            row.append(["slider", str(o.Slider.Value), str(o.Slider.Minimum), str(o.Slider.Maximum)])
        if hasattr(o, "UserText"):
            row.append(["text", o.UserText])
        if getattr(o, "Expression", None) is not None:
            row.append(["expression", str(o.Expression)])
        inputs = list(o.Params.Input) if hasattr(o, "Params") else ([o] if hasattr(o, "Sources") else [])
        for p in inputs:
            values = [str(x) for x in p.PersistentData.AllData(True)] if hasattr(p, "PersistentData") else []
            row.append([p.NickName, sorted(str(s.InstanceGuid) for s in p.Sources), values])
        definition[str(o.InstanceGuid)] = row
document = []
for ro in doc.Objects:
    a = ro.Attributes
    b = ro.Geometry.GetBoundingBox(True)
    baked = a.GetUserString("building") == BUILDING and (a.GetUserString("source") or "").endswith(".gh")
    document.append([a.Name or "", str(ro.ObjectType), baked, [round(v, 4) for v in (b.Min.X, b.Min.Y, b.Min.Z, b.Max.X, b.Max.Y, b.Max.Z)]])
print("X3JSON " + json.dumps({"definition_path": gdoc.FilePath if gdoc is not None else None, "definition": definition,
                              "document": sorted(document)}))
"""

MARK_UNMODIFIED = """import json
__rhino_doc__.Modified = False
print("X3JSON " + json.dumps({"modified": __rhino_doc__.Modified}))
"""


class RhinoMcp:
    """The harness's own stdio client of the Rhino MCP server: one server process, the session's one Rhino.

    Every call is logged to ``log`` (``calls.jsonl``: tool, seconds, result size, the server's error code) and the
    server's own log goes beside it.
    """

    def __init__(self, launcher: str, log: Path, env: Mapping[str, str] | None = None) -> None:
        self.log = Path(log)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = self.log.with_suffix(".stderr.txt").open("a", encoding="utf-8")
        environment = dict(os.environ if env is None else env)
        environment["CUDA_VISIBLE_DEVICES"] = ""            # the Rhino it starts inherits this: no GPU compute
        self.proc = subprocess.Popen(router_command(launcher), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
                                     text=True, encoding="utf-8", env=environment)
        self._answers: dict[int, dict[str, Any]] = {}
        self._cond = threading.Condition()
        self._n = 0
        self.slot: str | None = None
        self.pid: int | None = None
        self.closed: dict[str, Any] | None = None
        threading.Thread(target=self._read, daemon=True).start()
        self._request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "x3-gh-harness", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self._request("tools/list").get("result", {}).get("tools", [])

    # ---- the wire
    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and "id" in value and ("result" in value or "error" in value):
                with self._cond:
                    self._answers[value["id"]] = value
                    self._cond.notify_all()

    def _send(self, message: dict[str, Any]) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict[str, Any] | None = None, timeout: float = CALL_TIMEOUT_S) -> dict[str, Any]:
        self._n += 1
        key = self._n
        self._send({"jsonrpc": "2.0", "id": key, "method": method, "params": params or {}})
        end = time.monotonic() + timeout
        with self._cond:
            while key not in self._answers:
                left = end - time.monotonic()
                if left <= 0 or self.proc.poll() is not None:
                    raise RhinoError(f"the Rhino MCP server did not answer {method} within {timeout:.0f} s")
                self._cond.wait(min(left, 1.0))
            return self._answers.pop(key)

    def call(self, name: str, timeout: float = CALL_TIMEOUT_S, **arguments: Any) -> dict[str, Any]:
        """One tool call: its text, the server's error if it names one, and the time it took (logged)."""

        started = time.perf_counter()
        answer = self._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        result = answer.get("result") or {}
        text = _text(result)
        error = server_error(text) or (answer.get("error") if "error" in answer else None) or ({"code": "is_error", "message": text[:600]}
                                                                                              if result.get("isError") else None)
        row = {"at": datetime.now(timezone.utc).isoformat(), "tool": name, "seconds": round(time.perf_counter() - started, 2),
               "chars": len(text), "error": (error or {}).get("code")}
        with self.log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row) + "\n")
        return {"text": text, "error": error, "seconds": row["seconds"]}

    # ---- the session's Rhino
    def spawn(self) -> dict[str, Any]:
        """Start the session's Rhino; refused while any Rhino runs (one instance on this machine)."""

        others = running_rhinos()
        if others:
            raise RhinoError(f"a Rhino is already running (pid {others}); GH starts its own and keeps one Rhino on the machine")
        answer = self.call("spawn_slot", timeout=SPAWN_TIMEOUT_S, version="8")
        if answer["error"]:
            raise RhinoError(f"spawn_slot: {answer['error']}")
        payload = json.loads(answer["text"])["payload"]
        self.slot, self.pid = payload["slotId"], int(payload["pid"])
        # Rhino's Python starts on its first script and Rhino registers again: take that step now, then find it again.
        warm = self.tool("run_python", script="print(1)")
        return {"pid": self.pid, "slot": self.slot, "seconds": answer["seconds"], "first_script_error": (warm["error"] or {}).get("code"),
                "slot_after_first_script": self.find_slot()}

    def find_slot(self) -> str | None:
        """The slot this server process now gives the session's Rhino (by its pid); Rhino re-registers when its Python first
        runs, and the server then lists it again under a slot of its own."""

        answer = self.call("list_slots")
        try:
            rows = json.loads(answer["text"]).get("payload") or []
        except ValueError:
            rows = []
        self.slot = next((r.get("slotId") for r in rows if str(r.get("pid")) == str(self.pid)), None)
        return self.slot

    def tool(self, name: str, timeout: float = CALL_TIMEOUT_S, **arguments: Any) -> dict[str, Any]:
        """A tool call on the session's Rhino; once more after finding it again when the server lost it."""

        for attempt in (1, 2):
            answer = self.call(name, timeout=timeout, slot=self.slot, **arguments)
            if not answer["error"] or answer["error"].get("code") not in ("rhino_closed", "slot_not_found") or attempt == 2:
                return answer
            time.sleep(3)
            if self.find_slot() is None:
                return answer
        return answer

    def script(self, source: str, timeout: float = CALL_TIMEOUT_S, **values: Any) -> dict[str, Any]:
        """A harness script (``run_python``); raises when it fails or prints no result."""

        answer = self.tool("run_python", timeout=timeout, script=_literals(**values) + source)
        if answer["error"]:
            raise RhinoError(f"run_python: {answer['error']}")
        result = script_result(answer["text"])
        if result["value"] is None:
            raise RhinoError(f"run_python printed no result: {result['out'][-1500:]}")
        return {**result["value"], "seconds": answer["seconds"]}

    # ---- the harness's steps
    def prepare_document(self) -> dict[str, Any]:
        return self.script(PREPARE_DOCUMENT, TOLERANCE=TOLERANCE_M)

    def start_grasshopper(self) -> dict[str, Any]:
        answer = self.tool("g1_start")
        if answer["error"]:
            raise RhinoError(f"g1_start: {answer['error']}")
        return {"text": answer["text"][:300], "seconds": answer["seconds"]}

    def open_definition(self, path: Path) -> dict[str, Any]:
        try:
            return self.script(OPEN_DEFINITION, PATH=str(path))
        except RhinoError as exc:
            if "canvas is not open" not in str(exc):
                raise
            self.start_grasshopper()
            return {**self.script(OPEN_DEFINITION, PATH=str(path)), "grasshopper_restarted": True}

    def write_notes(self, text: str) -> dict[str, Any]:
        return self.script(WRITE_NOTES, PANEL=NOTES_PANEL, TEXT=text)

    def save_definition(self, paths: list[Path]) -> dict[str, Any]:
        saved = self.script(SAVE_DEFINITION, PATHS=[str(p) for p in paths])
        failed = [p for p, ok in saved["saved"] if not ok]
        if failed:
            raise RhinoError(f"Grasshopper could not save {failed}")
        return saved

    def bake(self, bake_script: Path, building: str, out: Path, source_name: str) -> dict[str, Any]:
        baked = self.script(BAKE, timeout=600, BUILDING=building, OUT_PATH=str(out), GH_SOURCE=source_name, BAKE_SCRIPT=str(bake_script))
        if not baked["written"] or not Path(out).is_file():
            raise RhinoError(f"the bake wrote no file: {baked}")
        return baked

    def measure(self, building: str, out: Path) -> dict[str, Any]:
        """The measurement meshes: every object the bake made (or a GH export brings), meshed from its Brep within
        ``DEVIATION_M`` and written to ``out`` as named meshes. The document and the export are not changed."""

        measured = self.script(MEASURE, timeout=600, BUILDING=building, OUT_PATH=str(out), DEVIATION=DEVIATION_M, MAX_EDGE=MAX_EDGE_M)
        if not measured["written"] or not Path(out).is_file():
            raise RhinoError(f"the measurement meshes were not written: {measured}")
        return measured

    def snapshot(self, building: str) -> dict[str, Any]:
        """The definition and the document as they stand (``SNAPSHOT``); ``{"error": ...}`` when Rhino cannot say."""

        try:
            return self.script(SNAPSHOT, timeout=300, BUILDING=building)
        except RhinoError as exc:
            return {"error": str(exc)[:600]}

    def import_model(self, path: Path) -> dict[str, Any]:
        """Open a returned model: the document cleared and the file imported (the server's own ``open_doc``)."""

        answer = self.tool("open_doc", path=str(path), clearFirst=True)
        if answer["error"]:
            raise RhinoError(f"open_doc: {answer['error']}")
        return {"text": answer["text"][:600], "seconds": answer["seconds"]}

    def close(self) -> dict[str, Any]:
        """End the session's Rhino (``_-Exit``; the process it started is ended if it stays) and the server process."""

        record: dict[str, Any] = {"pid": self.pid}
        if self.pid is not None and self.pid in running_rhinos():
            try:
                record["unmodified"] = self.script(MARK_UNMODIFIED, timeout=60)
                record["exit"] = (self.tool("run_command", timeout=60, command="_-Exit")["error"] or {}).get("code")
            except RhinoError as exc:
                record["exit_error"] = str(exc)[:600]
            end = time.monotonic() + EXIT_WAIT_S
            while self.pid in running_rhinos() and time.monotonic() < end:
                time.sleep(2)
            if self.pid in running_rhinos():
                done = subprocess.run(["taskkill", "/PID", str(self.pid), "/F"], capture_output=True, text=True, check=False,
                                      encoding="mbcs" if os.name == "nt" else "utf-8", errors="replace")
                record["ended_process"] = done.returncode == 0
        record["running_after"] = self.pid in running_rhinos() if self.pid is not None else False
        try:
            self.proc.stdin.close()
            self.proc.wait(60)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        self._stderr.close()
        self.closed = record
        return record


@contextmanager
def rhino_session(launcher: str, log_dir: Path, env: Mapping[str, str] | None = None) -> Iterator[RhinoMcp]:
    """The session's Rhino: started on entry, ended on exit (whatever happened in between)."""

    client = RhinoMcp(launcher, Path(log_dir) / "harness-calls.jsonl", env)
    try:
        yield client
    finally:
        client.close()
