"""M069: material channel contracts."""

import unittest

from archflow.materials.ledger import (
    MaterialError,
    MaterialIntent,
    MaterialLedger,
    ledger_coverage,
    material_display_color,
)


def intent(material_id, refs=("adoption://fact",), color=None):
    return MaterialIntent(
        material_id=material_id,
        label=material_id.replace("-", " "),
        source_refs=tuple(refs),
        display_color=color,
    )


def ledger():
    return MaterialLedger(
        intents=(intent("cast-stone"), intent("timber")),
        assignments=(("hall", "cast-stone"), ("roof", "timber")),
    )


BINDINGS = [
    {"binding_id": "hall-b", "component_id": "hall", "object_ids": []},
    {"binding_id": "roof-b", "component_id": "roof", "object_ids": []},
    {"binding_id": "porch-b", "component_id": "porch", "object_ids": []},
]


class MaterialIntentTest(unittest.TestCase):
    def test_provenance_is_mandatory(self):
        with self.assertRaises(MaterialError):
            intent("cast-stone", refs=())

    def test_display_color_defaults_deterministically(self):
        self.assertEqual(
            material_display_color("cast-stone"),
            intent("cast-stone").color,
        )
        self.assertEqual(
            (10, 20, 30), intent("timber", color=(10, 20, 30)).color
        )

    def test_ledger_rejects_undeclared_material(self):
        with self.assertRaises(MaterialError):
            MaterialLedger(
                intents=(intent("cast-stone"),),
                assignments=(("hall", "granite"),),
            )


class LedgerCoverageTest(unittest.TestCase):
    def test_unassigned_components_are_typed(self):
        coverage = ledger_coverage(BINDINGS, ledger())
        self.assertEqual(["porch"], coverage["unassigned"])
        self.assertEqual(
            "cast-stone", coverage["assigned"]["hall"]["material_id"]
        )
        self.assertEqual(
            ["adoption://fact"],
            coverage["assigned"]["hall"]["source_refs"],
        )
        self.assertAlmostEqual(2 / 3, coverage["coverage_ratio"], places=5)

    def test_orphan_assignments_surface(self):
        orphan = MaterialLedger(
            intents=(intent("cast-stone"),),
            assignments=(("ghost", "cast-stone"),),
        )
        coverage = ledger_coverage(BINDINGS, orphan)
        self.assertEqual(["ghost"], coverage["orphan_assignments"])


class CadProjectionTest(unittest.TestCase):
    def test_material_layers_and_user_text(self):
        from tests.test_cad_program import semantic_build
        from archflow.adapters.cad_program import (
            expected_object_semantics,
            translate_to_rhino_python,
        )

        materials = {"colonnade": "cast-stone"}
        colors = {"cast-stone": intent("cast-stone").color}
        translation = translate_to_rhino_python(
            semantic_build(),
            material_by_component=materials,
            material_colors=colors,
        )
        color = intent("cast-stone").color
        self.assertIn(
            f"rs.AddLayer('archflow::colonnade', {color!r})",
            translation.script,
        )
        semantics = expected_object_semantics(
            semantic_build(), material_by_component=materials
        )
        self.assertEqual(
            "cast-stone",
            semantics["objects"]["ring-object"]["user_text"][
                "archflow:material"
            ],
        )
        self.assertNotIn(
            "archflow:material",
            semantics["objects"]["slab-object"]["user_text"],
        )


@unittest.skipIf(
    __import__("importlib.util", fromlist=["util"]).find_spec("ifcopenshell")
    is None,
    "ifcopenshell not installed",
)
class IfcProjectionTest(unittest.TestCase):
    def test_material_pset_and_association(self):
        import tempfile
        from pathlib import Path

        import ifcopenshell
        import ifcopenshell.util.element

        from archflow.adapters.ifc_export import export_program_to_ifc
        from tests.test_ifc_export import build_program

        result = export_program_to_ifc(
            build_program(),
            project_id="test-project",
            run_id="test-run",
            material_by_component={"ring": "cast-stone"},
        )
        handle, path = tempfile.mkstemp(suffix=".ifc")
        Path(path).write_text(result.file_text, encoding="utf-8")
        model = ifcopenshell.open(path)
        materials = model.by_type("IfcMaterial")
        self.assertEqual(1, len(materials))
        self.assertEqual("cast-stone", materials[0].Name)
        associations = model.by_type("IfcRelAssociatesMaterial")
        self.assertEqual(1, len(associations))
        related = {item.Name for item in associations[0].RelatedObjects}
        self.assertEqual({"pier-ring-object"}, related)
        element = next(
            item
            for item in model.by_type("IfcElement")
            if item.Name == "pier-ring-object"
        )
        semantics = ifcopenshell.util.element.get_psets(element)[
            "Archflow_Semantics"
        ]
        self.assertEqual("cast-stone", semantics["archflow:material"])


if __name__ == "__main__":
    unittest.main()
