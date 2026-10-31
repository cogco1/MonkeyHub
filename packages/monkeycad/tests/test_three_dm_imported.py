"""Imported objects placed in a composed model as the record says (#300 item 22 B).

A design made from an imported model keeps each object's shape as its file
made it; the record only says which objects stand where. These build small
files with rhino3dm and place their objects: copied with their ids and
layers, named for their element and labelled with their component and file,
turned about the record's vertical and moved in record metres, and gone when
the record no longer places them.
"""

from __future__ import annotations

import base64
import hashlib
import unittest

import rhino3dm

from archflow.state.geometry_program import GeometryOperation, GeometryOperationKind, SemanticBinding
from monkeycad.formats.three_dm_compose import (
    IMPORTED_KEY,
    SOURCE_NAME_KEY,
    ComposedPatch,
    ImportedPlacement,
    adopt_named_objects,
    patch_composed_three_dm,
    place_imported_objects,
    rewrite_composed_materials,
    verify_composed_three_dm,
)
from monkeycad.patch import CadPatchError

from test_three_dm_compose import _compile, _encoded, _export, _vector


def _box(x0: float, y0: float, x1: float, y1: float, height: float) -> rhino3dm.Mesh:
    mesh = rhino3dm.Mesh()
    for x, y, z in ((x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0),
                    (x0, y0, height), (x1, y0, height), (x1, y1, height), (x0, y1, height)):
        mesh.Vertices.Add(x, y, z)
    for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        mesh.Faces.AddFace(*face)
    mesh.Normals.ComputeNormals()
    return mesh


def _file(unit=rhino3dm.UnitSystem.Meters) -> tuple[bytes, list[str]]:
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = unit
    layer = rhino3dm.Layer()
    layer.Name = "stairs"
    index = model.Layers.Add(layer)
    ids = []
    for number, (x0, x1) in enumerate(((0, 2), (4, 6), (8, 10))):
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = f"tread {number}"
        attributes.LayerIndex = index
        ids.append(str(model.Objects.AddMesh(_box(x0, 0, x1, 1, 0.2), attributes)))
    return base64.b64decode(model.Encode()), ids


def _objects(data: bytes) -> dict[str, rhino3dm.File3dmObject]:
    return {str(item.Attributes.Id): item for item in rhino3dm.File3dm.FromByteArray(data).Objects}


class PlaceImportedObjectsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data, self.ids = _file()
        self.sha = hashlib.sha256(self.data).hexdigest()

    def place(self, base: bytes, *placements: ImportedPlacement) -> bytes:
        return place_imported_objects(base, sources={self.sha: self.data}, placements=placements)

    def test_the_file_becomes_its_objects_named_for_their_elements(self) -> None:
        placed = self.place(self.data, ImportedPlacement("stair-1", "stair", self.sha, tuple(self.ids[:2])),
                            ImportedPlacement("stair-2", "stair", self.sha, (self.ids[2],)))
        objects = _objects(placed)
        self.assertEqual(set(objects), set(self.ids))
        names = {object_id: item.Attributes.Name for object_id, item in objects.items()}
        self.assertEqual(names, {self.ids[0]: "obj-stair-1-0", self.ids[1]: "obj-stair-1-1", self.ids[2]: "obj-stair-2"})
        first = objects[self.ids[0]].Attributes
        self.assertEqual(first.GetUserString("archflow:component"), "stair")
        self.assertEqual(first.GetUserString(IMPORTED_KEY), f"{self.sha}:{self.ids[0]}")
        self.assertEqual(first.GetUserString(SOURCE_NAME_KEY), "tread 0")
        source = _objects(self.data)[self.ids[0]].Geometry.GetBoundingBox()
        self.assertEqual(str(objects[self.ids[0]].Geometry.GetBoundingBox().Min), str(source.Min))

    def test_a_removed_element_is_gone_and_a_moved_one_moves_from_where_the_file_put_it(self) -> None:
        once = self.place(self.data, *(ImportedPlacement(f"t{n}", "stair", self.sha, (oid,)) for n, oid in enumerate(self.ids)))
        # Moved twice over: each placement is read from the file, never from where the last one put it.
        twice = self.place(self.place(once, ImportedPlacement("t0", "stair", self.sha, (self.ids[0],), move=(0.3, 0.0, 0.0)),
                                      ImportedPlacement("t2", "stair", self.sha, (self.ids[2],))),
                           ImportedPlacement("t0", "stair", self.sha, (self.ids[0],), move=(0.3, 0.0, 0.0)),
                           ImportedPlacement("t2", "stair", self.sha, (self.ids[2],)))
        objects = _objects(twice)
        self.assertEqual(set(objects), {self.ids[0], self.ids[2]})
        moved = objects[self.ids[0]].Geometry.GetBoundingBox()
        self.assertAlmostEqual(moved.Min.X, 0.3)
        self.assertAlmostEqual(moved.Max.X, 2.3)

    def test_turn_is_about_the_record_vertical_and_move_is_record_metres_in_the_file_unit(self) -> None:
        data, ids = _file(rhino3dm.UnitSystem.Feet)
        sha = hashlib.sha256(data).hexdigest()
        # Record (x, y-up, z) is CAD (x, z, y): a record move of 1 m along z is 3.28 ft along CAD y.
        placed = place_imported_objects(data, sources={sha: data}, placements=(
            ImportedPlacement("t", "stair", sha, (ids[0],), move=(0.0, 0.0, 1.0), turn=90.0),))
        box = _objects(placed)[ids[0]].Geometry.GetBoundingBox()
        # A turn of +90 about record +Y takes CAD (X, Y) to (Y, -X): the 0..2 ft by 0..1 ft tread lies along -Y.
        self.assertAlmostEqual(box.Min.X, 0.0, places=6)
        self.assertAlmostEqual(box.Max.X, 1.0, places=6)
        self.assertAlmostEqual(box.Min.Y, -2.0 + 1.0 / 0.3048, places=6)
        self.assertAlmostEqual(box.Max.Y, 1.0 / 0.3048, places=6)

    def test_an_object_the_file_does_not_hold_or_two_elements_placing_one_object_are_refused(self) -> None:
        with self.assertRaisesRegex(CadPatchError, "no top-level object"):
            self.place(self.data, ImportedPlacement("t", "stair", self.sha, ("00000000-0000-0000-0000-000000000001",)))
        with self.assertRaisesRegex(CadPatchError, "same object"):
            self.place(self.data, ImportedPlacement("a", "stair", self.sha, (self.ids[0],)),
                       ImportedPlacement("b", "stair", self.sha, (self.ids[0],)))


def _blocks(heights: dict[str, float] | None = None):
    """Three blocks of one component, delivered as ``obj-<element>`` as the runner names them."""

    operations = [GeometryOperation(
        op_id=name, kind=GeometryOperationKind.SOLID, output_object_ids=(f"obj-{name}",), input_object_ids=(),
        frame_id="world", parameters=(_vector("origin", [4.0 * n, 0.0, 0.0]),
                                      _vector("size", [3.0, (heights or {}).get(name, 3.0), 3.0])),
        semantic_binding_ids=("blocks-binding",)) for n, name in enumerate(("block-0", "block-1", "block-2"))]
    return _compile(operations, [SemanticBinding(binding_id="blocks-binding", component_id="blocks",
                                                 object_ids=tuple(f"obj-{op.op_id}" for op in operations),
                                                 commitment_refs=("commitment:compose",),
                                                 evidence_refs=("evidence:compose",))])


class AdoptNamedObjectsTests(unittest.TestCase):
    """housing: a complete model uploaded with the record's objects named as their elements and no identity."""

    def test_an_upload_named_as_its_elements_is_patched_in_place(self) -> None:
        prior = _blocks()
        model = _export(prior)
        for item in model.Objects:
            attributes = item.Attributes
            for key, _ in list(attributes.GetUserStrings() or ()):
                attributes.SetUserString(key, "")
            attributes.Name = attributes.Name.removeprefix("obj-")
        uploaded = _encoded(model)
        ids = {item.Attributes.Name: item.Attributes.Id for item in rhino3dm.File3dm.FromByteArray(uploaded).Objects}

        adopted = adopt_named_objects(uploaded, programs=(prior,))
        objects = {item.Attributes.Name: item for item in rhino3dm.File3dm.FromByteArray(adopted).Objects}
        self.assertEqual(sorted(objects), ["obj-block-0", "obj-block-1", "obj-block-2"])
        self.assertEqual(objects["obj-block-1"].Attributes.Id, ids["block-1"])
        self.assertEqual(objects["obj-block-1"].Attributes.GetUserString("archflow:object_ref"), "cad-object:obj-block-1")
        self.assertIs(adopt_named_objects(adopted, programs=(prior,)), adopted, "nothing left to adopt: the same bytes")

        # Block 1 grows: it alone is rebuilt from the run's export, under the id it was uploaded with.
        grown = _blocks({"block-1": 6.0})
        donor = _encoded(_export(grown))
        composed = patch_composed_three_dm(adopted, prior_program=prior, program=grown, replacement_3dm=donor)
        composed = rewrite_composed_materials(composed, programs=(grown,), material_by_component=None, material_colors=None)
        verify_composed_three_dm(composed, base_3dm=adopted,
                                 patches=(ComposedPatch(prior, grown, donor),),
                                 material_by_component=None, material_colors=None)
        result = {item.Attributes.Name: item for item in rhino3dm.File3dm.FromByteArray(composed).Objects}
        self.assertEqual({name: item.Attributes.Id for name, item in result.items()},
                         {f"obj-{name}": object_id for name, object_id in ids.items()})
        before = {name: item.Geometry.GetBoundingBox() for name, item in objects.items()}
        after = {name: item.Geometry.GetBoundingBox() for name, item in result.items()}
        self.assertNotEqual(str(after["obj-block-1"].Max), str(before["obj-block-1"].Max))
        for kept in ("obj-block-0", "obj-block-2"):
            self.assertEqual(str(after[kept].Max), str(before[kept].Max))


if __name__ == "__main__":
    unittest.main()
