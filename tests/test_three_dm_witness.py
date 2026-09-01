from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import rhino3dm

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.adapters.three_dm_witness import (
    add_axis_aligned_box_brep_witnesses,
    primary_three_dm_objects,
)


class ThreeDmWitnessTests(unittest.TestCase):
    def test_box_brep_witness_survives_write_and_is_not_a_model_object(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "box.3dm"
            model = rhino3dm.File3dm()
            model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
            layer = rhino3dm.Layer()
            layer.Name = "Box"
            layer_index = model.Layers.Add(layer)
            brep = rhino3dm.Brep.CreateFromBoundingBox(
                rhino3dm.BoundingBox(
                    rhino3dm.Point3d(-1.0, 2.0, 3.0),
                    rhino3dm.Point3d(4.0, 6.0, 8.0),
                )
            )
            attributes = rhino3dm.ObjectAttributes()
            attributes.Name = "box-object"
            attributes.LayerIndex = layer_index
            source_id = model.Objects.AddBrep(brep, attributes)
            witnesses = add_axis_aligned_box_brep_witnesses(
                model,
                source_object_id=source_id,
                brep=brep,
                layer_index=layer_index,
            )

            self.assertEqual(6, len(witnesses))
            self.assertEqual(1, len(primary_three_dm_objects(model)))
            self.assertTrue(model.Write(str(path), 8))
            summary = inspect_three_dm(path)

            self.assertEqual(1, summary.object_count)
            self.assertEqual(1, summary.top_level_object_count)
            self.assertEqual(
                {"min": [-1.0, 2.0, 3.0], "max": [4.0, 6.0, 8.0]},
                summary.aggregate_bbox,
            )
            self.assertEqual(
                "explicit_trimmed_render_mesh_witnesses",
                summary.visible_bounds_witnesses[0]["source"],
            )


if __name__ == "__main__":
    unittest.main()
