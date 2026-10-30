"""Perspective camera passes: an ID image, a line image and a depth image read from one buffer (#19)."""

from __future__ import annotations

from io import BytesIO
import math
import unittest

from monkeydiagram.projection.mesh_views import (
    DEPTH_NEAREST, ID_BACKGROUND, MAX_CAMERA_EDGE_PX, MeshViewError, ObjectMesh, PerspectiveCamera, camera_passes,
)

BOX_TRIANGLES = ((0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
                 (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0))


def box(name, x0, y0, z0, x1, y1, z1):
    vertices = ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
    return ObjectMesh(name, vertices, BOX_TRIANGLES)


def pixels(png):
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        image.load()
        return image.mode, image.size, image


# The eye 20 units south of the origin at height 5, looking north at the
# origin; the near box's front face is 10 units in front of it, the far
# box's 30.
CAMERA = PerspectiveCamera(position=(0.0, -20.0, 5.0), target=(0.0, 0.0, 5.0), up=(0.0, 0.0, 1.0), fov_deg=60.0)
NEAR = box("near", -1, -10, 4, 1, -8, 6)
FAR = box("far", -8, 10, 0, 8, 12, 10)


class CameraPassTests(unittest.TestCase):
    def passes(self, meshes=(NEAR, FAR), width=320, height=180, **options):
        return camera_passes(meshes, camera=CAMERA, width=width, height=height, **options)

    def test_the_three_images_line_up_on_one_buffer(self):
        drawn = self.passes(groups={"near": "Layer A", "far": "Layer B"})
        self.assertEqual((drawn.width, drawn.height), (320, 180))
        self.assertEqual(drawn.seen, ("far", "near"))
        colours = {row["key"]: row["color"] for row in drawn.legend}
        self.assertEqual(set(colours), {"Layer A", "Layer B"})
        mode, size, ids = pixels(drawn.id_png)
        self.assertEqual((mode, size), ("RGB", (320, 180)))
        centre = "#%02X%02X%02X" % ids.getpixel((160, 90))
        self.assertEqual(centre, colours["Layer A"], "the near box covers the centre")
        self.assertEqual(ids.getpixel((2, 2)), ID_BACKGROUND, "nothing in the top corner")
        far_pixel = "#%02X%02X%02X" % ids.getpixel((160 + 30, 90))
        self.assertEqual(far_pixel, colours["Layer B"], "the far box shows beside the near one")
        colours_used = {ids.getpixel((x, y)) for x in range(0, 320, 7) for y in range(0, 180, 7)}
        self.assertLessEqual(len(colours_used), 3, "no smoothing: only the two keys and the paper")

        mode, size, depth = pixels(drawn.depth_png)
        self.assertEqual(size, (320, 180))
        self.assertIn(mode, ("I;16", "I"))
        self.assertEqual(depth.getpixel((160, 90)), DEPTH_NEAREST, "the near face is the nearest surface")
        self.assertEqual(depth.getpixel((2, 2)), 0, "no surface")
        self.assertGreater(depth.getpixel((160, 90)), depth.getpixel((190, 90)))
        self.assertAlmostEqual(drawn.near, 10.0, places=3)
        self.assertAlmostEqual(drawn.far, 30.0, delta=0.5)

        mode, size, line = pixels(drawn.line_png)
        self.assertEqual((mode, size), ("L", (320, 180)))
        self.assertEqual(line.getpixel((160, 90)), 255, "no line inside a face")
        # The near box's front face spans x = -1..1 at distance 10: its left edge is at
        # 160 - focal * 1/10 output pixels.
        focal = 90 / math.tan(math.radians(30))
        edge = round(160 - focal / 10)
        self.assertLess(min(line.getpixel((x, 90)) for x in range(edge - 2, edge + 3)), 128, "the box's edge is drawn")

    def test_every_id_key_has_its_own_colour(self):
        # 4951 objects of one model once shared 120 colours: two hues rounded to one sRGB triple.
        from monkeydiagram.projection.mesh_views import _id_palette

        palette = _id_palette(6000)
        self.assertEqual(len(set(palette)), 6000)
        self.assertNotIn(ID_BACKGROUND, palette)
        self.assertEqual(palette[:3], _id_palette(3), "the order does not depend on how many keys there are")

    def test_the_same_input_draws_the_same_bytes(self):
        first, second = self.passes(), self.passes()
        self.assertEqual((first.id_png, first.line_png, first.depth_png), (second.id_png, second.line_png, second.depth_png))

    def test_a_mesh_behind_the_eye_is_not_drawn_and_one_through_it_is_cut(self):
        behind = box("behind", -1, -40, 4, 1, -30, 6)
        # Beside the eye, from behind it to five units in front: only its front part shows.
        through = box("through", 2, -25, 0, 30, -15, 10)
        drawn = self.passes(meshes=(NEAR, behind, through))
        self.assertNotIn("behind", drawn.seen)
        self.assertIn("through", drawn.seen, "the part in front of the eye shows")
        self.assertIn("near", drawn.seen)
        # Its inner face (x = 2) is nearest where it leaves the frame's right edge: 2 * focal / 160 away.
        focal = 90 / math.tan(math.radians(30))
        self.assertAlmostEqual(drawn.near, 2 * focal / 160, delta=0.05)

    def test_a_live_section_cuts_what_lies_beyond_it_and_draws_its_outline(self):
        # G5: the plane y = -5, kept toward +Y, removes the near box entirely.
        drawn = self.passes(clip=((0, -5, 0), (0, 1, 0)))
        self.assertEqual(drawn.seen, ("far",))
        # The plane y = 11 cuts into the far box: its front face goes, the nearest surface is at the cut.
        cut = self.passes(meshes=(FAR,), clip=((0, 11, 0), (0, 1, 0)))
        self.assertGreater(cut.near, 30.5, "nothing in front of the plane is seen")
        whole = self.passes(meshes=(FAR,))
        self.assertLess(whole.near, 30.5)
        _, _, lines = pixels(cut.line_png)
        self.assertEqual(lines.getpixel((160, 90)), 255, "the cut face is not filled or lined inside")
        self.assertIn(0, {lines.getpixel((x, 90)) for x in range(320)}, "the cut's outline is drawn")
        with self.assertRaises(MeshViewError):
            self.passes(clip=((0, 0, 0), (0, 0, 0)))

    def test_a_saved_view_s_matrices_give_the_same_camera(self):
        eye, right, up, forward = CAMERA.basis(__import__("numpy"))
        back = -forward
        world = [*right, 0.0, *up, 0.0, *back, 0.0, *eye, 1.0]
        f = 1 / math.tan(math.radians(60) / 2)
        projection = [f / (16 / 9), 0, 0, 0, 0, f, 0, 0, 0, 0, -1.0002, -1, 0, 0, -0.2, 0]
        saved = PerspectiveCamera.from_matrices(world, projection)
        self.assertAlmostEqual(saved.fov_deg, 60.0, places=6)
        same = camera_passes((NEAR, FAR), camera=saved, width=320, height=180)
        self.assertEqual(same.id_png, self.passes().id_png)
        with self.assertRaises(MeshViewError):
            PerspectiveCamera.from_matrices(world, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1])

    def test_sizes_are_held_to_4096_on_the_long_edge(self):
        self.assertEqual(MAX_CAMERA_EDGE_PX, 4096)
        with self.assertRaises(MeshViewError):
            self.passes(width=4097, height=100)
        large = self.passes(width=4096, height=2304)
        self.assertEqual(pixels(large.id_png)[1], (4096, 2304))
        self.assertEqual(pixels(large.depth_png)[1], (4096, 2304))

    def test_a_camera_is_refused_when_it_cannot_look(self):
        for camera in (PerspectiveCamera((0, 0, 0), (0, 0, 0), (0, 0, 1), 60),
                       PerspectiveCamera((0, 0, 0), (0, 0, 5), (0, 0, 1), 60),
                       PerspectiveCamera((0, 0, 0), (0, 5, 0), (0, 0, 1), 179)):
            with self.subTest(camera=camera), self.assertRaises(MeshViewError):
                camera_passes((NEAR,), camera=camera, width=64, height=64)


if __name__ == "__main__":
    unittest.main()
