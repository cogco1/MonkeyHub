import unittest
from archflow.visualization import ProjectVisualizationState, default_visualization

class VisualizationTests(unittest.TestCase):
    def test_rejects_geometry_engine_fields_and_bad_values(self):
        for mutate in [lambda s:s.update(geometry={}), lambda s:s['camera'].update(engine='CYCLES'), lambda s:s['materials'][0].update(opacity=2), lambda s:s['lights'][0].update(intensity=float('nan'))]:
            value=default_visualization(); mutate(value)
            with self.assertRaises(ValueError): ProjectVisualizationState.from_dict(value)
    def test_detaches_mutable_inputs(self):
        value=default_visualization(); frozen=ProjectVisualizationState.from_dict(value)
        value['camera']['fov']=100
        self.assertEqual(frozen.to_dict()['camera']['fov'],38)
