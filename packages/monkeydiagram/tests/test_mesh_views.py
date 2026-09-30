"""Mesh line views hide what is behind, draw the same bytes twice, and say where they came from."""

from __future__ import annotations

from io import BytesIO
from math import sqrt
import unittest

from monkeycad import occt_backend


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class MeshLineViewTests(unittest.TestCase):
    # Looking along +Y: u = x, v = z.
    FRONT = {"right": (1, 0, 0), "up": (0, 0, 1)}

    def entries(self, *boxes):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        return tuple(occt_backend.StepEntry(name, (), None, BRepPrimAPI_MakeBox(gp_Pnt(*corner), *size).Shape())
                     for name, corner, size in boxes)

    def draw(self, entries, *, crop=(-1, -1, 11, 11), size=240, **frame):
        from monkeydiagram.projection.mesh_views import mesh_line_view, pixel_size, triangulate

        meshes, skipped = triangulate(entries, [entry.name for entry in entries],
                                      linear_deflection=pixel_size(crop, size) / 2)
        self.assertEqual(skipped, ())
        return mesh_line_view(meshes, crop_uv=crop, size_px=size, **(frame or self.FRONT),
                              text={"input": "a" * 64, "renderer": "mesh-lines-1"})

    def pixels(self, view):
        from PIL import Image

        with Image.open(BytesIO(view.png)) as image:
            self.assertEqual((image.mode, image.size), ("L", (view.width, view.height)))
            return image.copy(), dict(image.text)

    def dark(self, image, u, v, crop=(-1, -1, 11, 11), size=240, reach=2):
        scale = size / max(crop[2] - crop[0], crop[3] - crop[1])
        x, y = round((u - crop[0]) * scale), round((crop[3] - v) * scale)
        return min(image.getpixel((i, j)) for i in range(x - reach, x + reach + 1)
                   for j in range(y - reach, y + reach + 1)
                   if 0 <= i < image.width and 0 <= j < image.height) < 200

    def test_an_edge_behind_a_wall_is_hidden_and_an_edge_in_front_is_drawn(self):
        # A 10 x 10 wall at y = 0..1; a small box behind it (y = 3..4) and one in front (y = -4..-3).
        wall = ("wall", (0, 0, 0), (10, 1, 10))
        behind = ("behind", (4, 3, 4), (2, 1, 2))
        front = ("front", (4, -4, 1), (2, 1, 2))
        image, text = self.pixels(self.draw(self.entries(wall, behind)))
        self.assertTrue(self.dark(image, 0, 5), "the wall's outline is drawn")
        self.assertFalse(self.dark(image, 4, 5), "the box behind the wall is hidden")
        self.assertFalse(self.dark(image, 5, 4), "the box behind the wall is hidden")
        image, _ = self.pixels(self.draw(self.entries(wall, front)))
        self.assertTrue(self.dark(image, 4, 2), "the box in front of the wall is drawn over it")
        self.assertTrue(self.dark(image, 5, 1), "the box in front of the wall is drawn over it")
        self.assertEqual(text, {"input": "a" * 64, "renderer": "mesh-lines-1"})

    def test_coplanar_triangles_draw_no_diagonal_and_the_same_input_gives_the_same_bytes(self):
        entries = self.entries(("wall", (0, 0, 0), (10, 1, 10)))
        first, second = self.draw(entries), self.draw(self.entries(("wall", (0, 0, 0), (10, 1, 10))))
        self.assertEqual(first.png, second.png)
        image, _ = self.pixels(first)
        self.assertFalse(self.dark(image, 5, 5), "the face's triangulation is not drawn")
        self.assertFalse(self.dark(image, 2.5, 7.5), "the face's triangulation is not drawn")

    def test_the_axonometric_draws_the_silhouette_of_a_cylinder_and_fits_the_longer_side(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        from monkeydiagram.projection.mesh_views import mesh_line_view, pixel_size, triangulate

        entries = (occt_backend.StepEntry("column", (), None, BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 1.0, 6.0).Shape()),)
        right = (1 / sqrt(2), -1 / sqrt(2), 0)
        up = (1 / sqrt(6), 1 / sqrt(6), 2 / sqrt(6))
        crop = (-1.5, -1.5, 1.5, 6.0)
        meshes, _ = triangulate(entries, ["column"], linear_deflection=pixel_size(crop, 300) / 2)
        view = mesh_line_view(meshes, right=right, up=up, crop_uv=crop, size_px=300)
        self.assertEqual((view.width, view.height), (120, 300))
        image, text = self.pixels(view)
        self.assertEqual(text, {})
        # Silhouette lines at u = +-1 half way up; nothing between them.
        middle = 3.0
        self.assertTrue(self.dark(image, -1.0, middle, crop, 300))
        self.assertTrue(self.dark(image, 1.0, middle, crop, 300))
        self.assertFalse(self.dark(image, 0.0, middle, crop, 300))

    def test_a_curve_without_faces_is_skipped_and_named(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Pnt

        from monkeydiagram.projection.mesh_views import triangulate

        curve = occt_backend.StepEntry("rail", (), None, BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge())
        meshes, skipped = triangulate((curve, *self.entries(("wall", (0, 0, 0), (1, 1, 1)))), ["rail", "wall"],
                                      linear_deflection=0.01)
        self.assertEqual(([mesh.object_id for mesh in meshes], skipped), (["wall"], ("rail",)))


class MeshLineViewMemoryTests(unittest.TestCase):
    """Needs no OCP: the depth buffer works on triangles the caller supplies."""

    def test_large_overlapping_faces_are_drawn_in_bounded_memory(self):
        import tracemalloc

        from monkeydiagram.projection.mesh_views import ObjectMesh, mesh_line_view

        # Twelve slabs, each filling the whole 1024 px view at its own depth: about
        # 50 million candidate pixels, which used to be expanded a few hundred
        # thousand rows at a time into gigabytes of temporaries.
        slabs = [ObjectMesh(f"slab-{index}", ((0, index, 0), (10, index, 0), (10, index, 10), (0, index, 10)),
                            ((0, 1, 2), (0, 2, 3))) for index in range(12)]
        tracemalloc.start()
        try:
            view = mesh_line_view(slabs, right=(1, 0, 0), up=(0, 0, 1), crop_uv=(0, 0, 10, 10), size_px=1024)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual((view.width, view.height), (1024, 1024))
        self.assertLess(peak, 300 * 1024 * 1024, f"peak {peak / 2**20:.0f} MB")


if __name__ == "__main__":
    unittest.main()
