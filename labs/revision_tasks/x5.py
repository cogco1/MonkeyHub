"""X5, two short transport chains: what survives a hop through tools that know nothing of the design state, and what the
reviser does with the result.

Chains, from a 3DM state file of each arm:

| chain | hop 1 | hop 2 |
|---|---|---|
| ``3dm-glb`` | production mesh conversion (``InProcessMeshProvider``) to GLB | trimesh reads the GLB, rhino3dm writes its named meshes to 3DM |
| ``3dm-ifc`` | named meshes to IFC4 (``carrier.ifc_from_three_dm``, no property sets) | ifcopenshell's geometry reader, rhino3dm writes the meshes to 3DM |
| ``control`` | rhino3dm reads and saves the 3DM | — |

After every hop the survival of six items is recorded (object identity, parameters, dependency edges, obligations,
version binding, per-object geometry digests), and one benign revision is tried on the hop's file (with the D arm's
sidecar, which stays with the authority). The attempt is classed:

| class | when |
|---|---|
| ``correct_accept`` | accepted, and every item the arm carries survived |
| ``silent_wrong_accept`` | accepted although an item the arm carries was lost |
| ``correct_refuse`` | refused, and an item was lost |
| ``unneeded_refuse`` | refused although every item survived |
| ``crash`` | an exception |
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)
from labs.revision_tasks import carrier, reviser
from labs.revision_tasks.attacks import FAMILIES

ITEMS = ("identity", "parameters", "dependency_edges", "obligations", "binding", "geometry_digests")
CHAINS = {"3dm-glb": ("glb", "3dm"), "3dm-ifc": ("ifc", "3dm"), "control": ("3dm",)}


# ---------------------------------------------------------------- hops through tools that do not know the state

def _write_meshes(meshes: list[tuple[str, list[tuple[float, float, float]], list[tuple[int, int, int]]]], target: Path) -> Path:
    import rhino3dm

    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
    for name, vertices, faces in meshes:
        mesh = rhino3dm.Mesh()
        for x, y, z in vertices:
            mesh.Vertices.Add(float(x), float(y), float(z))
        for a, b, c in faces:
            mesh.Faces.AddFace(int(a), int(b), int(c))
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        model.Objects.AddMesh(mesh, attributes)
    if not model.Write(str(target), 8):
        raise RuntimeError(f"rhino3dm could not write {target.name}")
    return target


def hop(source: Path, fmt_in: str, fmt_out: str, target: Path) -> Path:
    if (fmt_in, fmt_out) == ("3dm", "3dm"):
        import rhino3dm

        model = rhino3dm.File3dm.Read(str(source))
        if not model.Write(str(target), 8):
            raise RuntimeError("rhino3dm could not resave")
        return target
    if (fmt_in, fmt_out) == ("3dm", "glb"):
        from monkeycad.formats.conversion import InProcessMeshProvider

        target.write_bytes(bytes(InProcessMeshProvider().convert(source.read_bytes(), "3dm", "glb").data))
        return target
    if (fmt_in, fmt_out) == ("glb", "3dm"):
        import numpy as np
        import trimesh

        scene = trimesh.load(str(source), force="scene")
        meshes = []
        for node in sorted(scene.graph.nodes_geometry):
            transform, geometry_name = scene.graph[node]
            mesh = scene.geometry[geometry_name].copy()
            mesh.apply_transform(transform)
            # glTF is y-up; the model is z-up.
            vertices = np.asarray(mesh.vertices) @ np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
            meshes.append((str(node), [tuple(v) for v in vertices.tolist()], [tuple(f) for f in mesh.faces.tolist()]))
        return _write_meshes(meshes, target)
    if (fmt_in, fmt_out) == ("3dm", "ifc"):
        carrier.ifc_from_three_dm(source).write(str(target))
        return target
    if (fmt_in, fmt_out) == ("ifc", "3dm"):
        import ifcopenshell
        import ifcopenshell.geom

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        meshes = []
        for product in sorted(ifcopenshell.open(str(source)).by_type("IfcProduct"), key=lambda p: p.Name or ""):
            if product.Representation is None or not product.Name:
                continue
            shape = ifcopenshell.geom.create_shape(settings, product)
            verts, faces = shape.geometry.verts, shape.geometry.faces
            meshes.append((product.Name, [tuple(verts[i:i + 3]) for i in range(0, len(verts), 3)],
                           [tuple(faces[i:i + 3]) for i in range(0, len(faces), 3)]))
        return _write_meshes(meshes, target)
    raise ValueError(f"no hop {fmt_in} -> {fmt_out}")


# ---------------------------------------------------------------- what survived

def _carried(path: Path, fmt: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None, dict[str, str]]:
    """(state, binding, design-state reference, object geometry digests) as the file holds them."""

    if fmt == "3dm":
        geometry, strings = carrier.three_dm_geometry(path)
        state = json.loads(strings["archflow:state"]) if "archflow:state" in strings else None
        binding = json.loads(strings["archflow:binding"]) if "archflow:binding" in strings else None
        return state, binding, strings.get("archflow:design_state_digest"), geometry
    if fmt == "glb":
        document, tail = carrier.glb_split(path.read_bytes())
        archflow = document.get("asset", {}).get("extras", {}).get("archflow") or {}
        return archflow.get("state"), archflow.get("binding"), archflow.get("design_state_digest"), carrier.glb_geometry(document, tail)
    import ifcopenshell

    model = ifcopenshell.open(str(path))
    values = carrier._ifc_psets(model, model.by_type("IfcProject")[0]).get(carrier.IFC_PSET, {})
    state = json.loads(values["state"]) if "state" in values else None
    binding = json.loads(values["binding"]) if "binding" in values else None
    return state, binding, values.get("design_state_digest"), carrier.ifc_geometry(model)


def survival(path: Path, fmt: str, arm: str, start: dict[str, Any], sidecar: Path | None) -> dict[str, Any]:
    """Each item: ``kept``, ``lost`` or ``n/a`` (the arm never carried it)."""

    state, binding, reference, geometry = _carried(path, fmt)
    if arm == "D":
        outside = json.loads(sidecar.read_text(encoding="utf-8"))
        state, binding = outside["state"], outside["binding"]
    record = (state or {}).get("record") or {}
    original = start["state"]["record"]
    names = set(start["geometry"])
    digests = (binding or {}).get("element_geometry_digests")
    row = {
        "identity": "kept" if names <= set(geometry) else "lost",
        "parameters": "kept" if record.get("parameters") == original["parameters"] else "lost",
        "dependency_edges": "kept" if (record.get("parameters"), record.get("relations")) == (original["parameters"], original["relations"]) else "lost",
        "obligations": "kept" if record.get("obligations") == original["obligations"] else "lost",
        # The binding names this file's state: in the file for C arms; for D, the file's own reference to the state it shows.
        "binding": "kept" if (binding is not None and (arm != "D" or reference == binding.get("state_digest"))) else "lost",
        "geometry_digests": "n/a" if arm == "C-unbound" else "kept" if digests is not None and digests == geometry else "lost",
    }
    return row


def classify(claim: str | None, items: dict[str, str]) -> str:
    lost = any(v == "lost" for v in items.values())
    if claim == "accept":
        return "silent_wrong_accept" if lost else "correct_accept"
    return "correct_refuse" if lost else "unneeded_refuse"


# ---------------------------------------------------------------- the run

def run_chain(family: str, arm: str, chain: str, root: Path) -> list[dict[str, Any]]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    fam = FAMILIES[family]
    start = reviser.write_state_file(fam.record(), arm=arm, fmt="3dm", out_dir=root, stem="s0", parent_sha256=None,
                                     context_root=root / "ctx", utterance="Start state.")
    state, binding, _, geometry = _carried(start.path, "3dm")
    if arm == "D":
        outside = json.loads(start.sidecar.read_text(encoding="utf-8"))
        state, binding = outside["state"], outside["binding"]
    reference = {"state": state, "geometry": geometry}
    rows, current, fmt = [], start.path, "3dm"
    for index, fmt_out in enumerate(CHAINS[chain], start=1):
        row = {"family": family, "arm": arm, "chain": chain, "hop": index, "format": fmt_out}
        try:
            current = hop(current, fmt, fmt_out, root / f"h{index}.{fmt_out}")
            fmt = fmt_out
            items = survival(current, fmt, arm, reference, start.sidecar)
            revision = reviser.revise(current, fmt=fmt, arm=arm, deltas=fam.benign, out_dir=root, stem=f"r{index}", context_root=root / "ctx",
                                      utterance="Probe revision.", sidecar=start.sidecar, scan=False)
            row.update(items)
            row.update({"claim": revision.claim, "codes": [r["code"] for r in revision.reasons], "class": classify(revision.claim, items)})
        except Exception as exc:  # noqa: BLE001 - a crash is an outcome class
            row.update({"claim": None, "codes": [f"{type(exc).__name__}: {str(exc)[:200]}"], "class": "crash"})
            rows.append(row)
            break
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the two short transport chains (and the control) into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", default="F1,F3")
    parser.add_argument("--arms", default=",".join(carrier.ARMS))
    parser.add_argument("--chains", default=",".join(CHAINS))
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    rows = []
    for family in args.families.split(","):
        for arm in args.arms.split(","):
            for chain in args.chains.split(","):
                for row in run_chain(family, arm, chain, out / "cases" / f"{family}-{arm}-{chain}"):
                    print(f"{family} {arm:9} {chain:8} hop{row['hop']} {row['format']:4} "
                          + " ".join(f"{k[:5]}={row.get(k, '-')}" for k in ITEMS) + f"  {row['claim']} {row['codes']} {row['class']}", flush=True)
                    rows.append(row)
    fields = ["family", "arm", "chain", "hop", "format", *ITEMS, "claim", "codes", "class"]
    with (out / "x5.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: json.dumps(row.get(k)) if isinstance(row.get(k), list) else row.get(k) for k in fields} for row in rows)
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = summary.setdefault(row["arm"], {})
        counts[row["class"]] = counts.get(row["class"], 0) + 1
    (out / "x5_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
