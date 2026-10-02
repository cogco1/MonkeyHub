"""Declared materials in a CAD document: one colour per material, one native material each (#560).

A component's material comes only from the caller's ``material_by_component``.
Every component of one material wears one colour - the caller's
``material_colors`` entry, else an identity colour derived from the
material's name, never from a component or layer path - on its layer and on
the one native material the OCCT preview writes for it, bound to each of its
objects. A component that declares none keeps its path-derived distinction
colour, wears no material and says ``archflow:material_status`` =
``undeclared``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project.refs import BranchRef, ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.state.geometry_program import (
    AffineTransform,
    CompiledGeometryObject,
    CompiledGeometryProgram,
    CoordinateFrame,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    SemanticBinding,
)
from monkeycad.backends.occt.kernel import occt_available
from monkeycad.execution import CadProgramBinding
from monkeycad.program import (
    CadTranslationError,
    _layer_color,
    _material_color,
    _material_identity_color,
    _resolved_layer_colors,
    declared_material,
    expected_object_semantics,
)

# Four parts and an unbound plinth: two walls of one material (one of them
# stating its colour), a frame of another material with no colour, a base
# that declares none, and an object no component claims.
PARTS = (
    ("wall-a", "wall-a", 0.0),
    ("wall-b", "wall-b", 3.0),
    ("frame", "frame", 6.0),
    ("base", "base", 9.0),
    ("loose", None, 12.0),
)
MATERIALS = {"wall-a": "hemp-lime", "wall-b": "hemp-lime", "frame": "timber"}
COLORS = {"hemp-lime": (200, 185, 143)}

# The two mixed components of #580, named as the compiler names an element's
# objects (obj-<part>), and an object of the plinth no part of it delivered.
PARTED = (
    ("obj-plinth-outer-brick", "plinth", 0.0),
    ("obj-plinth-inner-block", "plinth", 3.0),
    ("damp-course", "plinth", 6.0),
    ("obj-floor-porcelain-1", "floor_finish", 9.0),
    ("obj-external-paving", "floor_finish", 12.0),
)
# What ``declared_materials`` resolves: the plinth's default brick, its inner
# block's own concrete, the floor's tile on one part and nothing on the paving.
PART_DEFAULTS = {"plinth": "brick"}
PART_MATERIALS = {
    "plinth": {"plinth-outer-brick": "brick", "plinth-inner-block": "concrete block"},
    "floor_finish": {"floor-porcelain-1": "porcelain tile", "external-paving": None},
}
PART_COLORS = {"brick": (158, 75, 50), "concrete block": (180, 178, 170)}


def _vector(name: str, value: list[float]) -> GeometryParameter:
    return GeometryParameter.create(name=name, kind=GeometryParameterKind.VECTOR3, value=value, unit=LengthUnit.METER)


def _parted_program() -> CompiledGeometryProgram:
    """One binding per component over all of its objects, one solid per object."""

    owned: dict[str, list[str]] = {}
    for object_id, component, _ in PARTED:
        owned.setdefault(component, []).append(object_id)
    operations = [GeometryOperation(
        op_id=object_id.removeprefix("obj-"), kind=GeometryOperationKind.SOLID, output_object_ids=(object_id,),
        input_object_ids=(), frame_id="world",
        parameters=(_vector("origin", [x, 0.0, 0.0]), _vector("size", [2.0, 0.5, 0.5])),
        semantic_binding_ids=(f"{component}-binding",),
    ) for object_id, component, x in PARTED]
    bindings = [SemanticBinding(binding_id=f"{component}-binding", component_id=component, object_ids=tuple(sorted(objects)),
                                commitment_refs=("commitment:materials",), evidence_refs=("evidence:materials",))
                for component, objects in sorted(owned.items())]
    proposal = GeometryProgramProposal(
        proposal_id="parted-proposal", project_id="material-demo", run_id="run-1",
        base=ProjectVersionRef("material-demo", 0, "1" * 64), design_state_digest="2" * 64,
        predecessor_program_digest=None, length_unit=LengthUnit.METER, tolerance=GeometryTolerance(0.001, 0.001),
        frames=(CoordinateFrame(frame_id="world", parent_frame_id=None, transform_from_parent=AffineTransform.identity(),
                                source_refs=("evidence:frame",)),),
        assets=(), semantic_bindings=tuple(bindings), operations=tuple(sorted(operations, key=lambda op: op.op_id)),
        assemblies=(),
    )
    return CompiledGeometryProgram(
        proposal=proposal, operation_order=tuple(op.op_id for op in operations),
        frame_digests=(("world", "5" * 64),),
        component_digests=tuple((component, "6" * 64) for component in sorted(owned)),
        semantic_binding_digests=tuple((binding.binding_id, "7" * 64) for binding in bindings),
        objects=tuple(sorted((CompiledGeometryObject(object_id=object_id, producer_op_id=object_id.removeprefix("obj-"),
                                                     object_digest=f"{index + 2}" * 64)
                              for index, (object_id, _, _) in enumerate(PARTED)), key=lambda item: item.object_id)),
        asset_substitutions=(),
    )


def _program() -> CompiledGeometryProgram:
    base = ProjectVersionRef("material-demo", 0, "1" * 64)
    operations, bindings, objects = [], [], []
    for index, (op_id, component, x) in enumerate(PARTS):
        object_id = f"{op_id}-object"
        operations.append(GeometryOperation(
            op_id=op_id, kind=GeometryOperationKind.SOLID, output_object_ids=(object_id,), input_object_ids=(),
            frame_id="world", parameters=(_vector("origin", [x, 0.0, 0.0]), _vector("size", [2.0, 3.0, 0.5])),
            semantic_binding_ids=(f"{component}-binding",) if component else (),
        ))
        if component:
            bindings.append(SemanticBinding(binding_id=f"{component}-binding", component_id=component,
                                            object_ids=(object_id,), commitment_refs=("commitment:materials",),
                                            evidence_refs=("evidence:materials",)))
        objects.append(CompiledGeometryObject(object_id=object_id, producer_op_id=op_id, object_digest=f"{index + 2}" * 64))
    proposal = GeometryProgramProposal(
        proposal_id="material-proposal", project_id="material-demo", run_id="run-1", base=base,
        design_state_digest="2" * 64, predecessor_program_digest=None, length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(CoordinateFrame(frame_id="world", parent_frame_id=None, transform_from_parent=AffineTransform.identity(),
                                source_refs=("evidence:frame",)),),
        assets=(), semantic_bindings=tuple(sorted(bindings, key=lambda b: b.binding_id)),
        operations=tuple(sorted(operations, key=lambda op: op.op_id)), assemblies=(),
    )
    return CompiledGeometryProgram(
        proposal=proposal, operation_order=tuple(op_id for op_id, _, _ in PARTS),
        frame_digests=(("world", "5" * 64),),
        component_digests=tuple(sorted((component, "6" * 64) for _, component, _ in PARTS if component)),
        semantic_binding_digests=tuple(sorted((b.binding_id, "7" * 64) for b in bindings)),
        objects=tuple(sorted(objects, key=lambda item: item.object_id)), asset_substitutions=(),
    )


def _binding(program: CompiledGeometryProgram) -> CadProgramBinding:
    proposal = program.proposal
    return CadProgramBinding(
        program_ref=ProjectRecordRef(
            project_id=proposal.project_id, media_type="application/json", sha256="8" * 64,
            relative_path=f"runs/{proposal.run_id}/branches/materials/records/stage-materials-geometry-program-{'8' * 64}.json",
        ),
        branch=BranchRef(run=RunRef(proposal.project_id, proposal.run_id, proposal.base), branch_id="materials", epoch=1),
        stage_id="stage-materials", program_digest=program.program_digest,
        design_state_digest=proposal.design_state_digest, predecessor_program_digest=None,
    )


class MaterialColorRuleTests(unittest.TestCase):
    def test_a_material_wears_its_declared_colour_else_the_identity_of_its_name(self) -> None:
        self.assertEqual(_material_color("hemp-lime", COLORS), (200, 185, 143))
        identity = _material_color("timber", COLORS)
        self.assertEqual(identity, _material_color("timber", None))
        self.assertEqual(identity, _material_identity_color("timber"))
        self.assertNotEqual(identity, _material_identity_color("oak"))
        # derived from the material, not from any component or layer that wears it
        self.assertNotIn(identity, {_layer_color("archflow::frame"), _layer_color("timber"), _layer_color("frame")})

    def test_components_of_one_material_share_its_colour_and_an_undeclared_one_keeps_its_own(self) -> None:
        layers = {"archflow::wall-a", "archflow::wall-b", "archflow::frame", "archflow::base", "20_ENVELOPE::wall-b"}
        colors = dict(_resolved_layer_colors(layers, material_by_component=MATERIALS, material_colors=COLORS))
        for layer in ("archflow::wall-a", "archflow::wall-b", "20_ENVELOPE::wall-b"):
            self.assertEqual(colors[layer], (200, 185, 143), layer)
        self.assertEqual(colors["archflow::frame"], _material_identity_color("timber"))
        self.assertEqual(colors["archflow::base"], _layer_color("archflow::base"))
        self.assertEqual(colors["archflow"], _layer_color("archflow"))
        # without any table each material still has one colour, the identity of its name
        bare = dict(_resolved_layer_colors(layers, material_by_component=MATERIALS, material_colors=None))
        self.assertEqual(bare["archflow::wall-a"], bare["archflow::wall-b"])
        self.assertEqual(bare["archflow::wall-a"], _material_identity_color("hemp-lime"))

    def test_a_layer_shared_by_components_of_different_materials_names_no_material(self) -> None:
        mixed = dict(_resolved_layer_colors({"archflow::frame+wall-a", "archflow::wall-a+wall-b"},
                                            material_by_component=MATERIALS, material_colors=COLORS))
        self.assertEqual(mixed["archflow::frame+wall-a"], _layer_color("archflow::frame+wall-a"))
        self.assertEqual(mixed["archflow::wall-a+wall-b"], (200, 185, 143))
        half = dict(_resolved_layer_colors({"archflow::base+wall-a"}, material_by_component=MATERIALS, material_colors=COLORS))
        self.assertEqual(half["archflow::base+wall-a"], _layer_color("archflow::base+wall-a"))

    def test_objects_name_their_material_or_say_it_is_undeclared(self) -> None:
        semantics = expected_object_semantics(_program(), material_by_component=MATERIALS)["objects"]
        for object_id, material in (("wall-a-object", "hemp-lime"), ("wall-b-object", "hemp-lime"), ("frame-object", "timber")):
            self.assertEqual(semantics[object_id]["user_text"]["archflow:material"], material)
            self.assertNotIn("archflow:material_status", semantics[object_id]["user_text"])
        self.assertEqual(semantics["base-object"]["user_text"]["archflow:material_status"], "undeclared")
        self.assertNotIn("archflow:material", semantics["base-object"]["user_text"])
        # an object no component claims is given neither a component nor a material statement
        self.assertEqual(semantics["loose-object"]["layer"], "archflow")
        self.assertFalse({"archflow:material", "archflow:material_status"} & set(semantics["loose-object"]["user_text"]))

    def test_each_part_names_its_own_material_and_an_undeclared_part_says_so(self) -> None:
        """#580: part override, then the component's material, then undeclared - per object, by the part it came from."""

        semantics = expected_object_semantics(_parted_program(), material_by_component=PART_DEFAULTS,
                                              material_by_part=PART_MATERIALS)["objects"]

        def labels(object_id):
            text = semantics[object_id]["user_text"]
            return text.get("archflow:material"), text.get("archflow:material_status")

        self.assertEqual(labels("obj-plinth-outer-brick"), ("brick", None))
        self.assertEqual(labels("obj-plinth-inner-block"), ("concrete block", None))
        # an object of the plinth that no part of it delivered wears the plinth's own material
        self.assertEqual(labels("damp-course"), ("brick", None))
        self.assertEqual(labels("obj-floor-porcelain-1"), ("porcelain tile", None))
        self.assertEqual(labels("obj-external-paving"), (None, "undeclared"))
        # both parts on the component's one layer: parts do not split layers or groups
        self.assertEqual({semantics[name]["layer"] for name in ("obj-plinth-outer-brick", "obj-plinth-inner-block")},
                         {"archflow::plinth"})
        # without the part map every object wears its component's material, as before
        plain = expected_object_semantics(_parted_program(), material_by_component=PART_DEFAULTS)["objects"]
        self.assertEqual(plain["obj-plinth-inner-block"]["user_text"]["archflow:material"], "brick")
        self.assertEqual(plain["obj-floor-porcelain-1"]["user_text"]["archflow:material_status"], "undeclared")

    def test_an_object_is_matched_to_its_part_among_all_of_the_components_parts(self) -> None:
        # with parts plinth-outer and plinth-outer-brick, obj-plinth-outer-brick is the second's, which wears nothing
        materials ={"plinth": {"plinth-outer": "brick", "plinth-outer-brick": None, "plinth-inner-block": "block"}}
        self.assertEqual(declared_material(("plinth",), None, object_name="obj-plinth-outer-brick",
                                           material_by_part=materials), None)
        self.assertEqual(declared_material(("plinth",), None, object_name="obj-plinth-outer-0",
                                           material_by_part=materials), "brick")
        # an object of several components names each one's material once
        self.assertEqual(declared_material(("plinth", "wall"), {"wall": "lime"}, object_name="obj-plinth-inner-block",
                                           material_by_part=materials), "block,lime")

    def test_a_statement_may_not_take_the_material_status_key(self) -> None:
        from dataclasses import replace

        program = _program()
        operations = tuple(replace(op, statements={"material_status": "declared"}) if op.op_id == "base" else op
                           for op in program.proposal.operations)
        with self.assertRaises(CadTranslationError):
            expected_object_semantics(replace(program, proposal=replace(program.proposal, operations=operations)))


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class OcctMaterialExportTests(unittest.TestCase):
    def test_the_preview_holds_one_material_per_declared_name_bound_to_its_objects(self) -> None:
        import rhino3dm

        from monkeycad.backends.occt.export import execute_occt_export
        from monkeycad.execution import CadExecutionStatus
        from monkeycad.formats.three_dm_inspector import inspect_three_dm

        program = _program()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt = execute_occt_export(program, binding=_binding(program), speculative_workspace=workspace,
                                          artifact_stem="materials@occt", provenance={"export_path": "occt-test"},
                                          material_by_component=MATERIALS, material_colors=COLORS)
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            preview = workspace / receipt.preview_artifact["relative_path"]
            model = rhino3dm.File3dm.Read(str(preview))

            materials = {material.Name: (index, tuple(material.DiffuseColor)[:3], material.GetUserString("archflow:material_id"))
                         for index, material in enumerate(model.Materials)}
            self.assertEqual(len(model.Materials), 2)
            identity = _material_identity_color("timber")
            self.assertEqual({name: row[1:] for name, row in materials.items()},
                             {"hemp-lime": ((200, 185, 143), "hemp-lime"), "timber": (identity, "timber")})

            layers = {layer.FullPath: tuple(layer.Color)[:3] for layer in model.Layers}
            objects = {obj.Attributes.Name: obj.Attributes for obj in model.Objects}
            for object_id, material in (("wall-a-object", "hemp-lime"), ("wall-b-object", "hemp-lime"), ("frame-object", "timber")):
                attributes = objects[object_id]
                self.assertEqual(attributes.MaterialSource, rhino3dm.ObjectMaterialSource.MaterialFromObject, object_id)
                self.assertEqual(attributes.MaterialIndex, materials[material][0], object_id)
                self.assertEqual(attributes.GetUserString("archflow:material"), material, object_id)
                # the layer of each part of a material wears that material's colour
                self.assertEqual(layers[f"archflow::{object_id.removesuffix('-object')}"], materials[material][1], object_id)
            undeclared = objects["base-object"]
            self.assertEqual((undeclared.MaterialSource, undeclared.MaterialIndex),
                             (rhino3dm.ObjectMaterialSource.MaterialFromLayer, -1))
            self.assertEqual(undeclared.GetUserString("archflow:material_status"), "undeclared")
            self.assertEqual(undeclared.GetUserString("archflow:material"), "")  # rhino3dm answers "" for no key
            self.assertEqual(layers["archflow::base"], _layer_color("archflow::base"))

            # the receipt says what the preview carries, and its own readback agrees
            self.assertEqual(receipt.preview_artifact["materials"]["wall-b-object"],
                             {"name": "hemp-lime", "diffuse": [200, 185, 143], "transparency": 0.0})
            self.assertNotIn("base-object", receipt.preview_artifact["materials"])
            self.assertIn("one native material per declared material, named by it and worn by each of its objects; "
                          "an object whose components declare none wears no material", receipt.preview_artifact["carries"])
            self.assertNotIn("native object materials for assembly frame and glazing members", receipt.preview_artifact["carries"])
            bindings = {row["name"]: row for row in inspect_three_dm(preview).object_material_bindings}
            self.assertEqual(bindings["wall-a-object"]["material_name"], "hemp-lime")
            self.assertEqual(bindings["wall-a-object"]["material_diffuse_color_rgba"], [200, 185, 143, 255])

    def test_the_preview_binds_each_part_to_its_own_material(self) -> None:
        """#580: one component's two parts wear two materials; a part declaring none wears none and says so."""

        import rhino3dm

        from monkeycad.backends.occt.export import execute_occt_export
        from monkeycad.execution import CadExecutionStatus

        program = _parted_program()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            receipt = execute_occt_export(program, binding=_binding(program), speculative_workspace=workspace,
                                          artifact_stem="parts@occt", provenance={"export_path": "occt-test"},
                                          material_by_component=PART_DEFAULTS, material_by_part=PART_MATERIALS,
                                          material_colors=PART_COLORS)
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            model = rhino3dm.File3dm.Read(str(workspace / receipt.preview_artifact["relative_path"]))
            table = {index: (material.Name, tuple(material.DiffuseColor)[:3]) for index, material in enumerate(model.Materials)}
            self.assertEqual(sorted(name for name, _ in table.values()), ["brick", "concrete block", "porcelain tile"])
            objects = {obj.Attributes.Name: obj.Attributes for obj in model.Objects}
            for name, (material, color) in (("obj-plinth-outer-brick", ("brick", (158, 75, 50))),
                                            ("obj-plinth-inner-block", ("concrete block", (180, 178, 170))),
                                            ("damp-course", ("brick", (158, 75, 50))),
                                            ("obj-floor-porcelain-1", ("porcelain tile",
                                                                       _material_identity_color("porcelain tile")))):
                attributes = objects[name]
                self.assertEqual(attributes.MaterialSource, rhino3dm.ObjectMaterialSource.MaterialFromObject, name)
                self.assertEqual(table[attributes.MaterialIndex], (material, color), name)
                self.assertEqual(attributes.GetUserString("archflow:material"), material, name)
            paving = objects["obj-external-paving"]
            self.assertEqual((paving.MaterialSource, paving.MaterialIndex), (rhino3dm.ObjectMaterialSource.MaterialFromLayer, -1))
            self.assertEqual(paving.GetUserString("archflow:material_status"), "undeclared")
            self.assertEqual(paving.GetUserString("archflow:material"), "")
            # one group and one layer per component, as without parts
            self.assertEqual(objects["obj-plinth-outer-brick"].LayerIndex, objects["obj-plinth-inner-block"].LayerIndex)
            self.assertEqual(receipt.preview_artifact["materials"]["obj-plinth-inner-block"],
                             {"name": "concrete block", "diffuse": [180, 178, 170], "transparency": 0.0})
            self.assertNotIn("obj-external-paving", receipt.preview_artifact["materials"])


if __name__ == "__main__":
    unittest.main()
