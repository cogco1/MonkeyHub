"""Declared materials travel from a facets proposal into the candidate's retained 3DM (#560).

Through the Runtime as an agent drives it: a construction script makes four
parts and runs as a candidate; a facets proposal on that candidate declares
two materials - one colour stated in lower case, kept in upper case - and
leaves one part undeclared; its candidate's retained preview then holds one
native material per declared name, bound to each of its parts, and says of
the undeclared part that it is undeclared. The first candidate's files are
read again afterwards and are byte for byte what they were. All projects are
disposable P036 fixtures; nothing is inferred from a part's id.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services/project-runtime/src"))

from monkeycad.backends.occt.kernel import occt_available
from tests.integration.runner_support import _no_rhino

routes = import_module("services.project-runtime.tests.test_construction_routes")

PARTS = "\n".join([
    "west = extrude(rect(0, 0, 3, 0.4), 3)",
    "east = extrude(rect(4, 0, 3, 0.4), 3)",
    "roof = extrude(rect(0, 0, 7, 0.4), 0.3, at=top(west))",
    "sill = extrude(rect(0, -0.6, 7, 0.5), 0.2)",
])


def _previews(project: Path, run_id: str) -> dict[str, str]:
    """Every retained preview of a run, by its project-relative path, with its sha256."""

    return {
        path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((project / "runs" / run_id).rglob("*.3dm"))
    }


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class DeclaredMaterialsTests(routes.ConstructionTestCase):
    cad_export = "occt"

    def setUp(self) -> None:
        super().setUp()
        for patcher in _no_rhino():
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_facets_proposal_gives_the_candidate_one_material_per_declared_name(self) -> None:
        import rhino3dm

        from monkeycad.program import _layer_color, _material_identity_color

        first = self.run_candidate(self.construct(PARTS)["proposalId"])
        before = _previews(self.project, first)
        self.assertTrue(before)
        for path in before:
            self.assertEqual(len(rhino3dm.File3dm.Read(str(self.project / path)).Materials), 0, path)

        declared = self.facets([
            {"id": "west", "set": {"material.name": "hemp-lime", "material.color": "#c8b98f"}},
            {"id": "east", "set": {"material.name": "hemp-lime"}},
            {"id": "roof", "set": {"material.name": "timber"}},
        ], sourceRunId=first)
        second = self.run_candidate(declared["proposalId"])
        facets = {row["id"]: row["facets"] for row in self.model(second)["entities"]}
        self.assertEqual(facets["west"], {"material.name": "hemp-lime", "material.color": "#C8B98F"})
        self.assertEqual(facets["sill"], {})

        (preview,) = [self.project / path for path in _previews(self.project, second) if path.endswith(".preview.3dm")]
        model = rhino3dm.File3dm.Read(str(preview))
        materials = {material.Name: (index, tuple(material.DiffuseColor)[:3]) for index, material in enumerate(model.Materials)}
        self.assertEqual({name: row[1] for name, row in materials.items()},
                         {"hemp-lime": (200, 185, 143), "timber": _material_identity_color("timber")})
        objects = {obj.Attributes.Name: obj.Attributes for obj in model.Objects}
        layers = {layer.FullPath: tuple(layer.Color)[:3] for layer in model.Layers}
        for part, material in (("west", "hemp-lime"), ("east", "hemp-lime"), ("roof", "timber")):
            attributes = objects[f"obj-{part}-body"]
            self.assertEqual((attributes.MaterialSource, attributes.MaterialIndex),
                             (rhino3dm.ObjectMaterialSource.MaterialFromObject, materials[material][0]), part)
            self.assertEqual(attributes.GetUserString("archflow:material"), material, part)
            self.assertEqual(layers[model.Layers[attributes.LayerIndex].FullPath], materials[material][1], part)
        sill = objects["obj-sill-body"]
        self.assertEqual((sill.MaterialSource, sill.MaterialIndex), (rhino3dm.ObjectMaterialSource.MaterialFromLayer, -1))
        self.assertEqual(sill.GetUserString("archflow:material_status"), "undeclared")
        sill_layer = model.Layers[sill.LayerIndex].FullPath
        self.assertEqual(layers[sill_layer], _layer_color(sill_layer))

        # the first candidate is retained as it was: same files, same bytes, still without materials
        self.assertEqual(_previews(self.project, first), before)

    def test_the_record_refuses_a_second_colour_for_a_material_and_a_colour_without_one(self) -> None:
        first = self.run_candidate(self.construct(PARTS)["proposalId"])
        conflict = self.facets([
            {"id": "west", "set": {"material.name": "hemp-lime", "material.color": "#C8B98F"}},
            {"id": "east", "set": {"material.name": "hemp-lime", "material.color": "#000000"}},
        ], sourceRunId=first, expect=422)
        self.assertEqual(conflict["code"], "FACETS_INVALID")
        self.assertIn("#000000 on east", conflict["detail"])
        self.assertIn("#C8B98F on west", conflict["detail"])
        nameless = self.facets([{"id": "roof", "set": {"material.color": "#8B5A2B"}}], sourceRunId=first, expect=422)
        self.assertEqual(nameless["code"], "FACETS_INVALID")
        self.assertIn("roof", nameless["detail"])
        self.assertIn("material.name", nameless["detail"])
        malformed = self.facets([{"id": "roof", "set": {"material.name": "timber", "material.color": "brown"}}],
                                sourceRunId=first, expect=422)
        self.assertIn("#RRGGBB", malformed["detail"])

    def test_each_part_of_a_geometry_id_wears_its_own_material(self) -> None:
        """#580: the portico's two parts in two materials, then one part declared and the other left undeclared."""

        import rhino3dm

        for targets, expected in (
            ([{"id": "portico", "part": "portico-base", "set": {"material.name": "travertine", "material.color": "#d8cbb0"}},
              {"id": "portico", "part": "portico-cornice", "set": {"material.name": "limestone"}}],
             {"obj-portico-base": "travertine", "obj-portico-cornice": "limestone"}),
            ([{"id": "portico", "part": "portico-cornice", "set": {"material.name": "limestone"}}],
             {"obj-portico-base": None, "obj-portico-cornice": "limestone"}),
        ):
            with self.subTest(targets=targets):
                run = self.run_candidate(self.facets(targets)["proposalId"])
                [portico] = [row for row in self.model(run)["entities"] if row["id"] == "portico"]
                self.assertEqual(portico["partFacets"], {target["part"]: {key: value.upper() if key == "material.color"
                                                                         else value for key, value in target["set"].items()}
                                                         for target in targets})
                (preview,) = [self.project / path for path in _previews(self.project, run) if path.endswith(".preview.3dm")]
                model = rhino3dm.File3dm.Read(str(preview))
                objects = {obj.Attributes.Name: obj.Attributes for obj in model.Objects}
                for name, material in expected.items():
                    attributes = objects[name]
                    if material is None:
                        self.assertEqual((attributes.MaterialSource, attributes.MaterialIndex),
                                         (rhino3dm.ObjectMaterialSource.MaterialFromLayer, -1), name)
                        self.assertEqual(attributes.GetUserString("archflow:material_status"), "undeclared", name)
                    else:
                        self.assertEqual(attributes.MaterialSource, rhino3dm.ObjectMaterialSource.MaterialFromObject, name)
                        self.assertEqual(model.Materials[attributes.MaterialIndex].Name, material, name)
                        self.assertEqual(attributes.GetUserString("archflow:material"), material, name)
                # the parts stay on their component's one layer
                self.assertEqual(objects["obj-portico-base"].LayerIndex, objects["obj-portico-cornice"].LayerIndex)


if __name__ == "__main__":
    unittest.main()
