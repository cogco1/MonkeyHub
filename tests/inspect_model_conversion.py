"""Read an existing GLB and independently inspect its production 3DM conversion.

Run from repository root with the configured Python runtime. No input/project
state is changed. The caller supplies the speculative output directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archflow.adapters.model_formats import GLB, VERSION, convert
import numpy as np
import rhino3dm

parser = argparse.ArgumentParser()
parser.add_argument("input", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
data = args.input.read_bytes()
source = GLB().read(data)
output, report = convert(data, "glb", "3dm")
native = rhino3dm.File3dm.FromByteArray(output)
rows = []
for a, obj in zip(source.meshes, native.Objects):
    b = obj.Geometry
    vertices = np.array([(v.X, v.Y, v.Z) for v in b.Vertices])
    normals = np.array([(b.Normals[i].X, b.Normals[i].Y, b.Normals[i].Z) for i in range(len(b.Normals))])
    original_normals = np.array(a.normals) if a.normals else None
    cos = None
    if original_normals is not None:
        cos = float(np.min(np.sum(normals*original_normals,axis=1)/(np.linalg.norm(normals,axis=1)*np.linalg.norm(original_normals,axis=1))))
    rows.append({"name":a.name,"vertices":len(vertices),"triangles":len(b.Faces),
                 "maxVertexErrorMeters":float(np.max(np.abs(vertices-np.array(a.vertices)))),
                 "identicalTriangleIndices":list(a.triangles)==[tuple(t[:3]) for t in b.Faces],
                 "minimumNormalCosine":cos,"materialIndex":obj.Attributes.MaterialIndex,
                 "diffuseRGBA":list(native.Materials[obj.Attributes.MaterialIndex].DiffuseColor)})
result = {"converter":VERSION,"sourceSHA256":hashlib.sha256(data).hexdigest(),
          "outputSHA256":hashlib.sha256(output).hexdigest(),"sourceMeshes":len(source.meshes),
          "outputMeshes":len(native.Objects),"units":str(native.Settings.ModelUnitSystem),
          "meshes":rows,"productionReport":report}
args.output.mkdir(parents=True,exist_ok=True)
(args.output/"review-conversion.3dm").write_bytes(output)
(args.output/"conversion-readback.json").write_text(json.dumps(result,indent=2),encoding="utf8")
assert len(source.meshes)==len(native.Objects)
assert all(r["maxVertexErrorMeters"]<1e-6 for r in rows)
assert hashlib.sha256(args.input.read_bytes()).hexdigest()==result["sourceSHA256"]
print(json.dumps({k:v for k,v in result.items() if k!='productionReport'},indent=2))
