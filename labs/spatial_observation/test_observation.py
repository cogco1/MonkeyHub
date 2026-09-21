import unittest

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.gp import gp_Pnt

from archflow.adapters.occt_backend import StepEntry
from labs.spatial_observation.observation import Annotation, Binding, Scene, changes


def box(name, x, y, z, dx=1, dy=1, dz=1):
    return StepEntry(name, (), None, BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape())


class ObservationTests(unittest.TestCase):
    binding = Binding("fixture", "candidate-a", "record-a")

    def test_unknown_neighbor_and_dependency_survive_selection(self):
        scene = Scene([box("cabinet", 0, 0, 0), box("unlabelled", 1.5, 0, 0),
                       box("dependent", 9, 0, 0), box("distant", 20, 0, 0)],
                      binding=self.binding, length_unit="meter",
                      annotations={"cabinet": Annotation("study cabinet", "proposed storage", ("dependent", "missing"))})
        lexical = scene.select(binding=self.binding, text="cabinet")
        local = scene.select(binding=self.binding, text="cabinet", radius_m=.6)
        self.assertNotIn("unlabelled", {o.object_id for o in lexical.objects})
        self.assertEqual({o.object_id for o in local.objects}, {"cabinet", "dependent", "unlabelled"})
        self.assertIsNone(scene.objects["unlabelled"].annotation.role)
        self.assertEqual(local.unresolved_dependencies, ("missing",))
        capped = scene.select(binding=self.binding, ids=["cabinet"], radius_m=.6, max_objects=1)
        self.assertEqual(capped.omitted_ids, ("dependent", "unlabelled"))

    def test_bounds_overlap_does_not_prove_solid_contact(self):
        # A solid ring surrounds a free-standing box. Their bounds overlap.
        outer, inner = box("o", 0, 0, 0, 5, 5, 1), box("i", 1, 1, -1, 3, 3, 3)
        ring = StepEntry("ring", (), None, BRepAlgoAPI_Cut(outer.shape, inner.shape).Shape())
        scene = Scene([ring, box("island", 2, 2, 0)], binding=self.binding, length_unit="meter")
        selected = scene.select(binding=self.binding, ids=["island"], radius_m=0)
        self.assertEqual(len(selected.objects), 2)
        measured = scene.exact_pairs(binding=self.binding, pairs=[("ring", "island")])
        self.assertEqual(measured[("ring", "island")]["status"], "separated")
        self.assertAlmostEqual(measured[("ring", "island")]["distance_m"], 1)

    def test_revision_binding_and_geometry_change(self):
        old = Scene([box("a", 0, 0, 0)], binding=self.binding, length_unit="foot")
        next_binding = Binding("fixture", "candidate-b", "record-b")
        new = Scene([box("a", 3, 0, 0)], binding=next_binding, length_unit="foot")
        with self.assertRaisesRegex(ValueError, "stale"):
            new.exact_pairs(binding=self.binding, pairs=[("a", "other")])
        delta = changes(old, new)
        self.assertEqual(delta["bounds_changed"], ("a",))
        self.assertEqual(delta["geometry_change_unknown"], ("a",))
        self.assertAlmostEqual(new.objects["a"].minimum[0], 3 * .3048, places=6)

    def test_equal_bounds_can_hide_shape_change(self):
        first = Scene([box("a", 0, 0, 0)], binding=self.binding, length_unit="meter",
                      annotations={"a": Annotation(geometry_identity="compiled-a")})
        second = Scene([box("a", 0, 0, 0)], binding=Binding("fixture", "b", "b"), length_unit="meter",
                       annotations={"a": Annotation(geometry_identity="compiled-b")})
        delta = changes(first, second)
        self.assertEqual(delta["bounds_changed"], ())
        self.assertEqual(delta["geometry_changed"], ("a",))

    def test_ambiguous_ids_are_refused(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            Scene([box("a", 0, 0, 0), box("a", 2, 0, 0)], binding=self.binding, length_unit="meter")

    def test_sight_line_passes_through_actual_opening(self):
        wall = box("wall", 0, 0, 0, 5, .5, 3)
        opening = box("cut", 1, -1, 0, 2, 3, 2.5)
        perforated = StepEntry("wall", (), None, BRepAlgoAPI_Cut(wall.shape, opening.shape).Shape())
        scene = Scene([perforated, box("glass", 1, .2, 0, 2, .02, 2.5)],
                      binding=self.binding, length_unit="foot")
        origin = [2 * .3048, -1 * .3048, 1.5 * .3048]
        target = [2 * .3048, 2 * .3048, 1.5 * .3048]
        opaque = scene.segment_hits(binding=self.binding, start_m=origin, end_m=target)
        self.assertEqual({h['object_id'] for h in opaque['hits']}, {"glass"})
        transparent = scene.segment_hits(binding=self.binding, start_m=origin, end_m=target, ignore_ids=["glass"])
        self.assertEqual(transparent['hits'], [])
        self.assertEqual(transparent['unavailable'], [])
        blocked = scene.segment_hits(binding=self.binding, start_m=[.1, -.3048, .4], end_m=[.1, .6, .4])
        self.assertEqual(blocked['hits'][0]['object_id'], 'wall')

    def test_empty_surface_hits_do_not_hide_an_occupied_viewpoint(self):
        scene = Scene([box("solid", 0, 0, 0)], binding=self.binding, length_unit="meter")
        result = scene.segment_hits(binding=self.binding, start_m=[.2, .5, .5], end_m=[.8, .5, .5])
        self.assertEqual(result['hits'], [])
        self.assertEqual(result['unavailable'], [])
        self.assertEqual({p['endpoint'] for p in result['occupied_endpoints']}, {'start', 'end'})
        self.assertEqual({p['state'] for p in result['occupied_endpoints']}, {'inside'})


if __name__ == "__main__":
    unittest.main()
