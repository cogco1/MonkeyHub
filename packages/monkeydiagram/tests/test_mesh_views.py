"""Mesh line views hide what is behind, draw the same bytes twice, and say where they came from."""

from __future__ import annotations

from io import BytesIO
from math import sqrt
import unittest
from unittest.mock import patch

from monkeycad.backends.occt.kernel import occt_available
from monkeycad.backends.occt.step import StepEntry


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class MeshLineViewTests(unittest.TestCase):
    # Looking along +Y: u = x, v = z.
    FRONT = {"right": (1, 0, 0), "up": (0, 0, 1)}

    def entries(self, *boxes):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        return tuple(StepEntry(name, (), None, BRepPrimAPI_MakeBox(gp_Pnt(*corner), *size).Shape())
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

        entries = (StepEntry("column", (), None, BRepPrimAPI_MakeCylinder(
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

    def test_a_coarse_view_after_a_fine_view_matches_a_cold_shape(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        def column():
            return (StepEntry("column", (), None, BRepPrimAPI_MakeCylinder(1.0, 6.0).Shape()),)

        entries = column()
        crop = (-1.5, -0.5, 1.5, 6.5)
        fine = self.draw(entries, crop=crop, size=1024)
        coarse = self.draw(entries, crop=crop, size=64)
        cold_coarse = self.draw(column(), crop=crop, size=64)
        self.assertGreater(fine.triangles, coarse.triangles, "the coarse view discarded the finer cached mesh")
        self.assertEqual(coarse, cold_coarse, "a prior size cannot change the thumbnail's triangles or pixels")
        self.assertEqual(self.draw(entries, crop=crop, size=1024), fine)

    def test_fills_paint_each_visible_surface_its_own_colour_and_mark_an_object_without_one(self):
        from PIL import Image

        from monkeydiagram.projection.mesh_views import (
            FILL_BACKGROUND, MeshViewError, UNKNOWN_FILL, UNKNOWN_HATCH, mesh_line_view, pixel_size, triangulate,
        )

        entries = self.entries(("wall", (0, 0, 0), (10, 1, 10)), ("front", (4, -4, 1), (2, 1, 2)),
                               ("behind", (4, 3, 4), (2, 1, 2)), ("bare", (11, 0, 0), (3, 1, 6)))
        crop, size = (-1, -1, 15, 11), 320
        meshes, _ = triangulate(entries, [entry.name for entry in entries], linear_deflection=pixel_size(crop, size) / 2)
        fills = {"wall": (200, 185, 143), "front": (160, 82, 45), "behind": (20, 90, 200), "bare": None}

        def draw():
            return mesh_line_view(meshes, **self.FRONT, crop_uv=crop, size_px=size, fills=fills)

        view = draw()
        self.assertEqual(view.png, draw().png, "the same input gives the same bytes")
        self.assertEqual(view.seen, ("bare", "front", "wall"), "the box behind the wall does not show")
        with Image.open(BytesIO(view.png)) as image:
            self.assertEqual((image.mode, image.size), ("RGB", (view.width, view.height)))
            image = image.copy()
        scale = size / max(crop[2] - crop[0], crop[3] - crop[1])

        def at(u, v):
            return image.getpixel((round((u - crop[0]) * scale), round((crop[3] - v) * scale)))

        # Inside a face a pixel is the object's own colour exactly: no light, no blend.
        self.assertEqual(at(2, 8), fills["wall"])
        self.assertEqual(at(5, 2), fills["front"], "the box in front of the wall is filled over it")
        self.assertEqual(at(4.5, 4.5), fills["wall"], "the box behind the wall is hidden")
        self.assertNotIn(fills["behind"], {colour for _, colour in image.getcolors(maxcolors=1 << 16)})
        self.assertEqual(at(-0.5, -0.5), FILL_BACKGROUND)
        # An object given no colour reads as unknown: grey under hatch lines, never a material colour.
        bare = {image.getpixel((x, y)) for x in range(round(11.5 * scale) + 20, round(11.5 * scale) + 40)
                for y in range(round(6 * scale), round(6 * scale) + 20)}
        self.assertTrue({UNKNOWN_FILL, UNKNOWN_HATCH} <= bare, bare)
        self.assertTrue(all(len(set(colour)) == 1 for colour in bare), "the unknown marking is neutral grey")
        with self.assertRaises(MeshViewError):
            mesh_line_view(meshes, **self.FRONT, crop_uv=crop, size_px=size, fills={"wall": (300, 0, 0)})

    def test_a_curve_without_faces_is_skipped_and_named(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Pnt

        from monkeydiagram.projection.mesh_views import triangulate

        curve = StepEntry("rail", (), None, BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge())
        meshes, skipped = triangulate((curve, *self.entries(("wall", (0, 0, 0), (1, 1, 1)))), ["rail", "wall"],
                                      linear_deflection=0.01)
        self.assertEqual(([mesh.object_id for mesh in meshes], skipped), (["wall"], ("rail",)))


class MeshRendererVersionTests(unittest.TestCase):
    def test_public_and_fill_helpers_remain_part_of_the_renderer_version(self):
        from monkeycad.backends.occt.preview import clear_shape_triangulation
        from monkeydiagram import sources
        from monkeydiagram.projection import mesh_views

        original_source = mesh_views.inspect.getsource
        self.assertEqual(mesh_views._renderer_version(), mesh_views.RENDERER_VERSION)
        for helper in (clear_shape_triangulation, sources.read_native_source, mesh_views._coverage, mesh_views._front_objects):
            with self.subTest(helper=helper.__name__):
                def changed_source(function):
                    source = original_source(function)
                    return source + "\n# Changed helper implementation\n" if function is helper else source

                with patch.object(mesh_views.inspect, "getsource", side_effect=changed_source):
                    self.assertNotEqual(mesh_views._renderer_version(), mesh_views.RENDERER_VERSION,
                                        "changing a helper must invalidate cached thumbnails")


class MeshFillTieTests(unittest.TestCase):
    """Needs no OCP: two objects' faces in one plane, triangulated differently."""

    def test_coplanar_faces_of_two_objects_fill_cleanly_and_a_nearer_one_still_wins(self):
        from PIL import Image

        from monkeydiagram.projection.mesh_views import ObjectMesh, mesh_line_view

        red, blue = (200, 30, 30), (30, 30, 200)

        def overlap(offset):
            # Looking along +Y: the second square is ``offset`` nearer the eye, over the first's corner.
            first = ObjectMesh("first", ((0, 0, 0), (6, 0, 0), (6, 0, 6), (0, 0, 6)), ((0, 1, 2), (0, 2, 3)))
            second = ObjectMesh("second", ((3, -offset, 3), (9, -offset, 3), (9, -offset, 9), (3, -offset, 9)),
                                ((0, 1, 3), (1, 2, 3)))
            view = mesh_line_view([first, second], right=(1, 0, 0), up=(0, 0, 1), crop_uv=(0, 0, 9, 9), size_px=180,
                                  fills={"first": red, "second": blue})
            with Image.open(BytesIO(view.png)) as image:
                # The overlap is u, v in 3..6: pixels 60..120 across and down, inside its edges.
                return {image.getpixel((x, y)) for x in range(64, 117) for y in range(64, 117)}

        self.assertEqual(overlap(0.0), {red}, "one plane: the lower label, no speckle")
        self.assertEqual(overlap(1e-9), {red}, "rounding apart is still one plane")
        self.assertEqual(overlap(0.01), {blue}, "a face a centimetre nearer is in front")


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
