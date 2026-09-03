"""P073: deterministic IFC semantic export contracts."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import ifcopenshell
    import ifcopenshell.util.element
except ImportError:  # pragma: no cover - environment without the library
    ifcopenshell = None


def op(op_id, kind, outputs, inputs=(), bindings=(), **params):
    return SimpleNamespace(
        op_id=op_id,
        kind=SimpleNamespace(value=kind),
        output_object_ids=tuple(outputs),
        input_object_ids=tuple(inputs),
        semantic_binding_ids=tuple(bindings),
        parameters=tuple(
            SimpleNamespace(name=name, value_json=json.dumps(value))
            for name, value in sorted(params.items())
        ),
    )


def binding(binding_id, component_id, object_ids, commitments=(), evidence=()):
    return SimpleNamespace(
        binding_id=binding_id,
        component_id=component_id,
        object_ids=tuple(object_ids),
        commitment_refs=tuple(commitments),
        evidence_refs=tuple(evidence),
    )


def build_program():
    operations = (
        op(
            "slab",
            "solid",
            ["slab-object"],
            bindings=["base-binding"],
            origin=[0.0, 0.0, 0.0],
            size=[20.0, 1.0, 30.0],
        ),
        op(
            "pier-seed",
            "solid",
            ["pier-seed-object"],
            bindings=["ring-binding"],
            origin=[8.0, 1.0, 14.0],
            size=[1.0, 4.0, 1.0],
        ),
        op(
            "pier-ring",
            "radial_array",
            ["pier-ring-object"],
            ["pier-seed-object"],
            bindings=["ring-binding"],
            count=6,
            center=[10.0, 0.0, 15.0],
            axis=[0.0, 1.0, 0.0],
            angle_step_degrees=60.0,
            start_angle_degrees=0.0,
        ),
        op(
            "tube-outer",
            "revolve",
            ["tube-outer-object"],
            bindings=["base-binding"],
            axis_start=[10.0, 1.0, 15.0],
            axis_end=[10.0, 6.0, 15.0],
            start_radius=5.0,
            end_radius=5.0,
        ),
        op(
            "tube-inner",
            "revolve",
            ["tube-inner-object"],
            bindings=["base-binding"],
            axis_start=[10.0, 1.0, 15.0],
            axis_end=[10.0, 7.0, 15.0],
            start_radius=4.0,
            end_radius=4.0,
        ),
        op(
            "tube-wall",
            "boolean_difference",
            ["tube-wall-object"],
            ["tube-outer-object", "tube-inner-object"],
            bindings=["base-binding"],
            base_index=1,
        ),
        op(
            "cap",
            "loft",
            ["cap-object"],
            bindings=["base-binding"],
            profile_size=4,
            profiles=[
                [8.0, 6.0, 13.0],
                [12.0, 6.0, 13.0],
                [12.0, 6.0, 17.0],
                [8.0, 6.0, 17.0],
                [9.0, 8.0, 14.0],
                [11.0, 8.0, 14.0],
                [11.0, 8.0, 16.0],
                [9.0, 8.0, 16.0],
            ],
        ),
    )
    bindings = (
        binding(
            "base-binding",
            "base",
            ["slab-object", "tube-wall-object", "cap-object"],
            commitments=["commitment:site"],
            evidence=["brief-claim:claim.occupancy"],
        ),
        binding(
            "ring-binding",
            "ring",
            ["pier-ring-object", "pier-seed-object"],
            commitments=["commitment:site", "commitment:axis"],
            evidence=["brief-claim:claim.occupancy"],
        ),
    )
    return SimpleNamespace(
        proposal=SimpleNamespace(
            operations=operations,
            semantic_bindings=bindings,
            proposal_id="test-proposal",
            project_id="test-project",
            run_id="test-run",
        ),
        operation_order=tuple(item.op_id for item in operations),
        proposal_digest="ab" * 32,
    )


@unittest.skipIf(ifcopenshell is None, "ifcopenshell not installed")
class IfcExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from archive.archflow.adapters.ifc_export import export_program_to_ifc

        cls.export = staticmethod(export_program_to_ifc)
        cls.result = export_program_to_ifc(
            build_program(),
            project_id="test-project",
            run_id="test-run",
            class_by_component={"ring": "IfcColumn"},
            provenance={"proposal_id": "test-proposal"},
        )
        handle, cls.path = tempfile.mkstemp(suffix=".ifc")
        Path(cls.path).write_text(cls.result.file_text, encoding="utf-8")
        cls.model = ifcopenshell.open(cls.path)

    def test_one_element_per_physical_object_named_by_object_id(self):
        names = {item.Name for item in self.model.by_type("IfcElement")}
        self.assertEqual(
            {
                "slab-object",
                "pier-ring-object",
                "tube-wall-object",
                "cap-object",
            },
            names,
        )

    def test_class_mapping_with_proxy_fallback(self):
        self.assertEqual(1, len(self.model.by_type("IfcColumn")))
        proxies = {
            item.Name
            for item in self.model.by_type("IfcBuildingElementProxy")
        }
        self.assertIn("slab-object", proxies)

    def test_property_sets_round_trip(self):
        element = next(
            item
            for item in self.model.by_type("IfcColumn")
            if item.Name == "pier-ring-object"
        )
        semantics = ifcopenshell.util.element.get_psets(element)[
            "Archflow_Semantics"
        ]
        self.assertEqual("ring-binding", semantics["archflow:bindings"])
        self.assertEqual("ring", semantics["archflow:component"])
        self.assertEqual(
            "commitment:axis,commitment:site",
            semantics["archflow:commitments"],
        )
        self.assertEqual(
            "test-proposal", semantics["archflow:proposal_id"]
        )

    def test_family_becomes_mapped_items(self):
        element = next(
            item
            for item in self.model.by_type("IfcColumn")
            if item.Name == "pier-ring-object"
        )
        shape = element.Representation.Representations[0]
        self.assertEqual("MappedRepresentation", shape.RepresentationType)
        self.assertEqual(6, len(shape.Items))
        sources = {item.MappingSource for item in shape.Items}
        self.assertEqual(1, len(sources))

    def test_parametric_boolean_is_exact_csg(self):
        element = next(
            item
            for item in self.model.by_type("IfcBuildingElementProxy")
            if item.Name == "tube-wall-object"
        )
        shape = element.Representation.Representations[0]
        self.assertEqual("CSG", shape.RepresentationType)
        self.assertTrue(shape.Items[0].is_a("IfcBooleanResult"))

    def test_loft_is_faceted_with_typed_note(self):
        codes = {note["code"] for note in self.result.representation_notes}
        self.assertIn("ifc.loft_faceted", codes)
        element = next(
            item
            for item in self.model.by_type("IfcBuildingElementProxy")
            if item.Name == "cap-object"
        )
        shape = element.Representation.Representations[0]
        self.assertEqual("Brep", shape.RepresentationType)

    def test_spatial_hierarchy_carries_program_identity(self):
        project = self.model.by_type("IfcProject")[0]
        self.assertEqual("test-project", project.Name)
        building = self.model.by_type("IfcBuilding")[0]
        self.assertEqual("test-run", building.Name)

    def test_export_is_deterministic(self):
        again = self.export(
            build_program(),
            project_id="test-project",
            run_id="test-run",
            class_by_component={"ring": "IfcColumn"},
            provenance={"proposal_id": "test-proposal"},
        )
        self.assertEqual(self.result.file_sha256, again.file_sha256)


if __name__ == "__main__":
    unittest.main()
