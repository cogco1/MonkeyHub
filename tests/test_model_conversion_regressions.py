"""Cross-format regressions kept separate from the conversion owner's active PR."""

import json
import struct

from archflow.adapters.model_formats import ThreeDM, convert


def _glb_with_black_seam() -> bytes:
    """Build an authored GLB; the production writer does not yet emit these attributes."""
    # The triangles occupy the same positions but use separate corner vertices.
    # Their opposing normals make that split observable after conversion.
    positions = [(0, 0, 0), (1, 0, 0), (0, 0, -1)] * 2
    normals = [(0, 1, 0)] * 3 + [(0, -1, 0)] * 3
    indices = [0, 1, 2, 3, 5, 4]
    binary = bytearray()
    views = []
    accessors = []

    def add(rows, fmt, kind, component):
        offset = len(binary)
        for row in rows:
            binary.extend(struct.pack("<" + fmt, *row))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(binary) - offset})
        accessors.append(
            {"bufferView": len(views) - 1, "componentType": component, "count": len(rows), "type": kind}
        )
        return len(accessors) - 1

    # The fixture is authored in glTF's Y-up coordinates. Its plane is XZ in
    # source and becomes XY in the adapter's meters/Z-up representation.
    position = add(positions, "fff", "VEC3", 5126)
    normal = add(normals, "fff", "VEC3", 5126)
    index = add([(value,) for value in indices], "I", "SCALAR", 5125)
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "black seam"}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": position, "NORMAL": normal},
            "indices": index,
            "material": 0,
        }]}],
        "materials": [{
            "name": "intentional black",
            "pbrMetallicRoughness": {"baseColorFactor": [0.0, 0.0, 0.0, 1.0]},
        }],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary))
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def test_black_material_and_split_seam_survive_glb_to_independent_3dm_readback():
    """Legal black is not the missing-material fallback; coincident corners are not welded."""
    output, report = convert(_glb_with_black_seam(), "glb", "3dm")

    # ThreeDM.read creates a fresh native model from the returned bytes rather
    # than inspecting the writer's in-memory scene.
    mesh = ThreeDM().read(output).meshes[0]
    assert len(mesh.vertices) == 6
    assert mesh.vertices[:3] == mesh.vertices[3:]
    assert mesh.triangles == [(0, 1, 2), (3, 5, 4)]
    assert mesh.normals[:3] == [(0.0, 0.0, 1.0)] * 3
    assert mesh.normals[3:] == [(0.0, 0.0, -1.0)] * 3
    assert mesh.material.name == "intentional black"
    assert mesh.material.color == (0, 0, 0, 255)
    assert report["display"]["material"] == {
        "kept": 1,
        "defaulted": 0,
        "fallback": {"name": "MonkeyHub neutral", "color": [200, 200, 200, 255]},
    }
