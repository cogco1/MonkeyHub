"""P103: incremental Rhino patch — selection, subset translation, patch plan."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from archflow.adapters.cad_execution import CadExecutionError, RhinoPatchBase, patch_composed_three_dm, prepare_rhino_three_dm_export
from archflow.adapters.cad_patch import CadPatchError, PatchSelection, build_patch_prelude, select_patch_operations
from archflow.adapters.cad_program import _physical_ids, expected_object_semantics, translate_to_rhino_python
from monkeyarch.capabilities.element_producers import ProductionContext, produce_rows
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeyarch.compilers.geometry import compile_geometry_program
from tests.test_cad_execution import _binding
from tests.test_element_producers import _grids, _levels, _rows
from tests.test_geometry_compiler import COMMITMENT, _only, _proposal, _state


def _compile(rows, *, array_seed: str | None = None):
    """The slice as a compiled program; ``array_seed`` adds a P099-style block array over that operation."""

    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
    produced = produce_rows(rows, context)
    operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for e in produced for op in e.operations)
    if array_seed is not None:
        from archflow.state.geometry_program import GeometryOperation, GeometryOperationKind, GeometryParameter, GeometryParameterKind, LengthUnit

        seed = next(op for op in operations if op.op_id == array_seed)
        operations += (GeometryOperation(
            op_id=f"{array_seed}-array", kind=GeometryOperationKind.ARRAY, output_object_ids=(f"{seed.output_object_ids[0]}-array",),
            input_object_ids=seed.output_object_ids, frame_id=seed.frame_id,
            parameters=(GeometryParameter.create(name="count", kind=GeometryParameterKind.INTEGER, value=3),
                        GeometryParameter.create(name="step", kind=GeometryParameterKind.VECTOR3, value=[0.0, 0.0, 1.0], unit=LengthUnit.METER)),
            semantic_binding_ids=seed.semantic_binding_ids),)
    bindings = tuple(b for e in produced for b in e.bindings)
    datums = tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id))
    state = _state()
    proposal = _only(_proposal(state, extra_operations=operations), operations, ())
    result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,), interface_datums=datums, datum_bindings=bindings)
    assert result.program is not None, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues]
    return result.program


def _capital_rows(**capital_params):
    rows = list(_rows())
    rows[1] = replace(rows[1], params={**rows[1].params, **capital_params})
    return tuple(rows)


class PatchSelectionTests(unittest.TestCase):
    def test_empty_patches_remap_materials_refresh_declared_colors_and_keep_texture_without_growth(self):
        try:
            import rhino3dm as r
        except ImportError:
            self.skipTest("rhino3dm is not installed")
        from tests.test_cad_program import binding, op, program

        names = tuple(f"object-{i:02}" for i in range(25))
        build = program(*(op(f"solid-{i}", "solid", [name], bindings=(f"binding-{i}",), origin=[0, 0, 0], size=[1, 1, 1]) for i, name in enumerate(names)),
                        bindings=tuple(binding(f"binding-{i}", f"component-{i}", (name,)) for i, name in enumerate(names)))
        assignments = {f"component-{i}": f"finish-{i}" for i in range(24)}
        semantics = expected_object_semantics(build, material_by_component=assignments)["objects"]
        selection = PatchSelection("1" * 64, "2" * 64, (), (), (), (), (), names, {})
        source = r.File3dm()
        source.Materials.Add(r.Material())  # unused table entry: carried indices must be remapped
        for i, name in enumerate(names):
            material = r.Material()
            material.Name, material.DiffuseColor = (f"finish-{i}" if i < 24 else "imported-texture"), (20, 30, 40, 255)
            if i < 2:
                material.Name = f"Display finish {i}"
                material.SetUserString("archflow:material" if i == 0 else "archflow:material_id", f"finish-{i}")
            if i == 0:
                material.ToPhysicallyBased()
                material.PhysicallyBased.BaseColor = (0.1, 0.2, 0.3, 0.8)
                material.PhysicallyBased.Roughness = 0.37
                material.PhysicallyBased.Opacity = 0.6
            if i == 24:
                material.SetBitmapTexture("retained-grain.png")
            material_index = source.Materials.Add(material)
            layer = r.Layer()
            layer.Name = semantics[name]["layer"]
            attributes = r.ObjectAttributes()
            attributes.Name, attributes.LayerIndex = name, source.Layers.Add(layer)
            attributes.MaterialSource, attributes.MaterialIndex = r.ObjectMaterialSource.MaterialFromObject, material_index
            attributes.PlotWeight = 0.35
            attributes.SetUserString("archflow:object_ref", f"cad-object:{name}")
            source.Objects.AddBrep(r.Brep.CreateFromBoundingBox(r.BoundingBox(r.Point3d(0, 0, 0), r.Point3d(1, 1, 1))), attributes)
        geometry = {obj.Attributes.Name: obj.Geometry.Encode() for obj in source.Objects}

        def read_source(path):
            model = r.File3dm.Read(path)
            layers = [SimpleNamespace(Index=layer.Index, Name=layer.Name, Id=layer.Id, ParentLayerId=layer.ParentLayerId,
                                      Color=SimpleNamespace(R=layer.Color[0], G=layer.Color[1], B=layer.Color[2]), RenderMaterialIndex=layer.RenderMaterialIndex) for layer in model.Layers]
            objects = []
            for obj in model.Objects:
                attributes = obj.Attributes
                objects.append(SimpleNamespace(Geometry=obj.Geometry, Attributes=SimpleNamespace(
                    Name=attributes.Name, ObjectId=attributes.Id, LayerIndex=attributes.LayerIndex, MaterialIndex=attributes.MaterialIndex,
                    Duplicate=lambda guid=attributes.Id, saved=model: r.File3dm.Decode(saved.Encode()).Objects.FindId(guid).Attributes)))
            return SimpleNamespace(Layers=layers, Objects=objects, Materials=model.Materials, InstanceDefinitions=())

        with tempfile.TemporaryDirectory() as directory:
            for revision in range(2):
                prior = Path(directory) / f"prior-{revision}.3dm"
                self.assertTrue(source.Write(str(prior), 8))
                target = r.File3dm()
                layer_ids = {}

                class NativeMaterial:
                    def __init__(self, material):
                        self.material = material
                    @property
                    def IsPhysicallyBased(self):
                        return self.material.PhysicallyBased.Supported
                    @property
                    def PhysicallyBased(self):
                        return self
                    @property
                    def BaseColor(self):
                        return SimpleNamespace(A=self.material.PhysicallyBased.BaseColor[3])
                    @BaseColor.setter
                    def BaseColor(self, rgba):
                        self.material.PhysicallyBased.BaseColor = rgba
                    def CommitChanges(self):
                        return True

                class NativeMaterials:
                    Add = target.Materials.Add
                    def __getitem__(self, index):
                        return NativeMaterial(target.Materials[index])

                def add_layer(name, color):
                    if name not in layer_ids:
                        layer = r.Layer()
                        layer.Name, layer.Color = name, (*color, 255)
                        layer_ids[name] = target.Layers.Add(layer)
                    return name

                def attributes(guid):
                    return target.Objects.FindId(guid).Attributes

                def add_material(guid):
                    index = target.Materials.Add(r.Material())
                    attributes(guid).MaterialIndex = index
                    return index

                rs = SimpleNamespace(
                    AllObjects=lambda: [], AddLayer=add_layer,
                    AddBox=lambda points: self.fail("an empty geometry patch must not rebuild objects"),
                    ObjectLayer=lambda guid, layer: setattr(attributes(guid), "LayerIndex", layer_ids[layer]),
                    GetUserText=lambda guid: [row[0] for row in attributes(guid).GetUserStrings()],
                    SetUserText=lambda guid, key, value: attributes(guid).SetUserString(key, value or ""),
                    HideObject=lambda guid: setattr(attributes(guid), "Visible", False),
                    ShowObject=lambda guid: setattr(attributes(guid), "Visible", True),
                    ObjectMaterialIndex=lambda guid, index: setattr(attributes(guid), "MaterialIndex", index),
                    ObjectMaterialSource=lambda guid, source: setattr(attributes(guid), "MaterialSource", r.ObjectMaterialSource(source)),
                    AddMaterialToObject=add_material,
                    MaterialName=lambda index, name: setattr(target.Materials[index], "Name", name),
                    MaterialColor=lambda index, rgb: setattr(target.Materials[index], "DiffuseColor", (*rgb, 255)),
                    BlockNames=lambda: [],
                )
                active = SimpleNamespace(Materials=NativeMaterials(), Layers=SimpleNamespace(FindByFullPath=lambda name, default: layer_ids.get(name, default)),
                                         Objects=SimpleNamespace(Add=target.Objects.AddBrep))
                rhino = SimpleNamespace(FileIO=SimpleNamespace(File3dm=SimpleNamespace(Read=read_source)),
                                        RhinoDoc=SimpleNamespace(ActiveDoc=active), Geometry=SimpleNamespace(InstanceReferenceGeometry=r.InstanceReference),
                                        Display=SimpleNamespace(Color4f=lambda red, green, blue, alpha: (red, green, blue, alpha)))
                colors = {name: (90 + revision * 20, 70, 50) for name in assignments.values()}
                translation = translate_to_rhino_python(build, material_by_component=assignments, material_colors=colors, operation_subset=())
                scope = {"Path": Path, "json": json, "Rhino": rhino, "rs": rs}
                with patch.dict(sys.modules, {"System": SimpleNamespace(Guid=SimpleNamespace(Empty=None)), "rhinoscriptsyntax": rs, "Rhino": rhino}), patch("builtins.print"):
                    exec(build_patch_prelude(selection, prior_model_path=prior, semantics=semantics) + "\n" + translation.script, scope)
                source = r.File3dm.Decode(target.Encode())
                self.assertEqual(len(source.Materials), 25)
                self.assertEqual({obj.Attributes.Name: obj.Geometry.Encode() for obj in source.Objects}, geometry)
                for obj in source.Objects:
                    attrs = obj.Attributes
                    self.assertEqual(attrs.PlotWeight, 0.35)
                    self.assertEqual(attrs.MaterialSource, r.ObjectMaterialSource.MaterialFromObject)
                    self.assertEqual(attrs.GetUserString("archflow:object_ref"), f"cad-object:{attrs.Name}")
                    material = source.Materials[attrs.MaterialIndex]
                    if attrs.Name != names[-1]:
                        logical = semantics[attrs.Name]["user_text"]["archflow:material"]
                        self.assertEqual(material.DiffuseColor, (*colors[logical], 255))
                        self.assertEqual(attrs.GetUserString("archflow:material"), logical)
                        if attrs.Name in names[:2]:
                            self.assertEqual(material.Name, f"Display finish {names.index(attrs.Name)}")
                        if attrs.Name == names[0]:
                            for actual, expected in zip(material.PhysicallyBased.BaseColor, (*[value / 255 for value in colors[logical]], 0.8)):
                                self.assertAlmostEqual(actual, expected, places=6)
                            self.assertAlmostEqual(material.PhysicallyBased.Roughness, 0.37, places=6)
                            self.assertAlmostEqual(material.PhysicallyBased.Opacity, 0.6, places=6)
                    else:
                        self.assertEqual(material.Name, "imported-texture")
                        self.assertEqual(material.GetBitmapTexture().FileName, "retained-grain.png")

    def test_same_program_selects_nothing(self) -> None:
        a = _compile(_rows())
        selection = select_patch_operations(a, a)
        self.assertTrue(selection.empty)
        self.assertEqual(selection.rebuilt_op_ids, ())
        self.assertEqual(len(selection.kept_object_ids), 14)

    def test_a_capital_change_rebuilds_only_the_capitals(self) -> None:
        a = _compile(_rows())
        b = _compile(_capital_rows(half_extent=0.6))
        selection = select_patch_operations(b, a)
        self.assertEqual(selection.rebuilt_op_ids, tuple(f"capitals-west-{k}" for k in range(6)))
        self.assertEqual(selection.delete_object_names, tuple(f"obj-capitals-west-{k}" for k in range(6)))
        self.assertEqual(len(selection.kept_object_ids), 8)
        self.assertEqual(set(selection.reasons.values()), {"digest"})

    def test_a_column_height_change_moves_the_chain_even_where_digests_hold(self) -> None:
        a = _compile(_rows())
        b = _compile(_rows(column_height=7.0))
        selection = select_patch_operations(b, a)
        self.assertEqual(len(selection.rebuilt_op_ids), 14)                       # columns, capitals, entablature, pediment
        self.assertEqual(selection.kept_object_ids, ())
        self.assertIn("obj-pediment-west", selection.changed_object_ids)

    def test_retired_and_added_objects(self) -> None:
        full = _compile(_rows())
        short = _compile(_rows()[:3])
        retired = select_patch_operations(short, full)
        self.assertEqual(retired.retired_object_ids, ("obj-pediment-west",))
        self.assertEqual(retired.delete_object_names, ("obj-pediment-west",))
        self.assertEqual(retired.rebuilt_op_ids, ())
        added = select_patch_operations(full, short)
        self.assertEqual(added.added_object_ids, ("obj-pediment-west",))
        self.assertEqual(added.rebuilt_op_ids, ("pediment-west",))
        self.assertEqual(added.delete_object_names, ())

    def test_identity_only_changes_select_nothing(self) -> None:
        a = _compile(_rows())
        proposal = a.proposal
        bindings = tuple(replace(b, evidence_refs=("evidence:another-record",)) for b in proposal.semantic_bindings)
        a2 = replace(a, proposal=replace(proposal, proposal_id="other-proposal", semantic_bindings=bindings))
        selection = select_patch_operations(a2, a)
        self.assertTrue(selection.empty, selection.reasons)                         # semantics are re-stamped, not rebuilt

    def test_subset_translation_names_only_the_rebuilt_objects(self) -> None:
        b = _compile(_capital_rows(half_extent=0.6))
        translation = translate_to_rhino_python(b, operation_subset=tuple(f"capitals-west-{k}" for k in range(6)))
        self.assertNotIn("_register('obj-columns-west-0'", translation.script)
        self.assertIn("_register('obj-capitals-west-0'", translation.script)
        self.assertEqual(translation.physical_object_ids, tuple(f"obj-capitals-west-{k}" for k in range(6)))
        with self.assertRaises(ValueError):
            translate_to_rhino_python(b, operation_subset=("nowhere",))

    def test_a_prior_program_with_a_block_array_is_patchable(self) -> None:
        """P099 typed instances: the array's definition and references are carried, not refused."""

        prior = _compile(_rows(), array_seed="capitals-west-0")
        self.assertIn("obj-capitals-west-0-array", {o.object_id for o in prior.objects})
        changed = _compile(_capital_rows(half_extent=0.6), array_seed="capitals-west-0")
        selection = select_patch_operations(changed, prior)                                   # no longer a blanket refusal
        self.assertIn("capitals-west-0-array", selection.rebuilt_op_ids)                      # the array consumes a rebuilt seed
        self.assertIn("obj-capitals-west-0-array", selection.delete_object_names)
        self.assertNotIn("obj-capitals-west-0", selection.kept_object_ids)                    # the seed is consumed, never physical
        self.assertEqual(set(selection.kept_object_ids), {f"obj-columns-west-{k}" for k in range(6)} | {"obj-entablature-west", "obj-pediment-west"})
        self.assertTrue(select_patch_operations(_compile(_rows(), array_seed="capitals-west-0"), prior).empty)

    def test_patch_plan_carries_the_selection_and_the_whole_denominator(self) -> None:
        a = _compile(_rows())
        b = _compile(_capital_rows(half_extent=0.6))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            prior = workspace / "prior.3dm"
            prior.write_bytes(b"not a real model")
            binding = _binding(b)
            plan = prepare_rhino_three_dm_export(b, binding=binding, speculative_workspace=workspace, artifact_name="patched.3dm", readback_tolerance=0.003,
                                                 patch=RhinoPatchBase(prior_model_path=prior, prior_program=a))
            self.assertEqual(plan.patch["rebuilt_op_ids"], [f"capitals-west-{k}" for k in range(6)])
            self.assertEqual(len(plan.expected_bounds), 14)                      # the denominator is the whole program
            script = plan.script_path.read_text(encoding="utf-8")
            self.assertIn("File3dm.Read", script)
            self.assertIn("'obj-capitals-west-0'", script)
            self.assertIn("_patch_expected_names = set(json.loads(", script)                # identity, not a bare count
            self.assertIn("AddInstanceObject", script)                                       # block families survive the carry
            self.assertIn("InstanceDefinitions.Add", script)
            self.assertIn("_patch_semantics = json.loads(", script)
            self.assertIn("obj-columns-west-0", script.split("_patch_semantics = json.loads(")[1].split("\n")[0])
            self.assertEqual(plan.to_dict()["patch"]["prior_model_sha256"], plan.patch["prior_model_sha256"])
            (workspace / "same").mkdir()
            restamp = prepare_rhino_three_dm_export(a, binding=_binding(a), speculative_workspace=workspace / "same", artifact_name="same.3dm",
                                                    readback_tolerance=0.003, patch=RhinoPatchBase(prior_model_path=prior, prior_program=a))
            self.assertEqual(restamp.patch["mode"], "restamp")                       # same geometry: carry everything, re-stamp semantics
            self.assertEqual(restamp.patch["rebuilt_op_ids"], [])
            self.assertEqual(len(restamp.patch["kept_object_ids"]), 14)
            self.assertNotIn("_register('obj-", restamp.script_path.read_text(encoding="utf-8"))
            self.assertEqual(plan.patch["mode"], "patch")


class ComposedThreeDmPatchTests(unittest.TestCase):
    """Continue a native change without flattening or rebuilding imported assets."""

    @classmethod
    def setUpClass(cls):
        import rhino3dm

        cls.rhino = rhino3dm
        cls.prior = _compile(_rows())
        cls.changed = _compile(_capital_rows(half_extent=0.6))

    def mesh(self, size=1.0):
        mesh = self.rhino.Mesh()
        for x, y, z in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)):
            mesh.Vertices.Add(x * size, y * size, z * size)
        for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                     (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            mesh.Faces.AddFace(*face)
        mesh.Normals.ComputeNormals()
        return mesh

    def native_model(self, program, *, replacement=False, feet=False):
        r = self.rhino
        model = r.File3dm()
        model.Settings.ModelUnitSystem = r.UnitSystem.Feet if feet else r.UnitSystem.Meters
        model.Settings.ModelAbsoluteTolerance = 0.004
        layer = r.Layer()
        layer.Name = "native"
        model.Layers.Add(layer)
        material = r.Material()
        material.Name = "new finish" if replacement else "original finish"
        material.DiffuseColor = (30, 180, 70, 255) if replacement else (180, 40, 30, 255)
        material_index = model.Materials.Add(material)
        group = r.Group()
        group.Name = "new native" if replacement else "original native"
        model.Groups.Add(group)
        layer_index = 0
        if replacement:
            child = r.Layer()
            child.Name = "replacement"
            child.ParentLayerId = model.Layers.FindIndex(0).Id
            child.RenderMaterialIndex = material_index
            layer_index = model.Layers.Add(child)
        for name in _physical_ids(program.proposal):
            attributes = r.ObjectAttributes()
            attributes.Name = name
            attributes.LayerIndex = layer_index
            attributes.MaterialSource = r.ObjectMaterialSource.MaterialFromObject
            attributes.MaterialIndex = material_index
            attributes.AddToGroup(0)
            attributes.SetUserString("archflow:object_ref", f"cad-object:{name}")
            model.Objects.AddMesh(self.mesh(2.0 if replacement else 1.0), attributes)
        return model

    def add_imported_equipment(self, model):
        r = self.rhino
        layer = r.Layer()
        layer.Name = "imported equipment"
        layer.Color = (40, 70, 180, 255)
        layer_index = model.Layers.Add(layer)
        material = r.Material()
        material.Name = "imported enamel"
        material_index = model.Materials.Add(material)
        group = r.Group()
        group.Name = "imported group"
        model.Groups.Add(group)
        attributes = r.ObjectAttributes()
        attributes.Name = "equipment-body"
        attributes.LayerIndex = layer_index
        attributes.MaterialIndex = material_index
        attributes.MaterialSource = r.ObjectMaterialSource.MaterialFromObject
        attributes.AddToGroup(1)
        attributes.SetUserString("source_leaf_uuid", "same-leaf-in-brep-and-display")
        attributes.Visible = False
        brep = r.Sphere(r.Point3d(0, 0, 0), 1.0).ToBrep()
        display_attributes = r.ObjectAttributes()
        display_attributes.Name = "equipment-body-display"
        display_attributes.LayerIndex = layer_index
        display_attributes.SetUserString("source_leaf_uuid", "same-leaf-in-brep-and-display")
        definition_index = model.InstanceDefinitions.Add(
            "equipment", "fixture", "", "", r.Point3d(0, 0, 0),
            (brep, self.mesh()), (attributes, display_attributes),
        )
        definition = model.InstanceDefinitions.FindIndex(definition_index)
        for x in (10.0, 20.0):
            instance_attributes = r.ObjectAttributes()
            instance_attributes.Name = f"equipment-instance-{x}"
            instance_attributes.LayerIndex = layer_index
            instance = r.InstanceReference(definition.Id, r.Transform.Translation(x, 3, 4))
            model.Objects.AddInstanceObject(instance, instance_attributes)
        attributes.Name = "invalid-imported-brep"
        model.Objects.AddBrep(r.Brep(), attributes)
        model.Strings["project-note"] = "keep source document"

    @staticmethod
    def encoded(model):
        return base64.b64decode(model.Encode())

    def patch(self, base, donor, *, program=None):
        return patch_composed_three_dm(
            self.encoded(base), prior_program=self.prior,
            program=program or self.changed, replacement_3dm=self.encoded(donor),
        )

    def test_native_change_preserves_full_document_and_scales_only_replacements(self):
        r = self.rhino
        base = self.native_model(self.prior, feet=True)
        donor = self.native_model(self.changed, replacement=True)
        self.add_imported_equipment(base)
        before = r.File3dm.FromByteArray(self.encoded(base))
        selected = select_patch_operations(self.changed, self.prior)
        original_ids = {o.Attributes.Name: o.Attributes.Id for o in before.Objects
                        if o.Attributes.Name in selected.delete_object_names}
        donor_ids = {o.Attributes.Name: o.Attributes.Id for o in donor.Objects
                     if o.Attributes.Name in selected.delete_object_names}
        self.assertTrue(all(original_ids[name] != donor_ids[name] for name in original_ids))
        survivors = {o.Attributes.Id: o for o in before.Objects
                     if o.Attributes.Name not in selected.delete_object_names}
        after = r.File3dm.FromByteArray(self.patch(base, donor))
        actual = {o.Attributes.Id: o for o in after.Objects}
        for object_id, original in survivors.items():
            self.assertEqual(actual[object_id].Geometry.Encode(), original.Geometry.Encode())
            self.assertEqual(actual[object_id].Attributes.Encode(), original.Attributes.Encode())
        self.assertEqual(after.Settings.ModelUnitSystem, r.UnitSystem.Feet)
        self.assertEqual(after.Settings.ModelAbsoluteTolerance, before.Settings.ModelAbsoluteTolerance)
        self.assertEqual(after.Strings["project-note"], "keep source document")
        self.assertEqual([d.Encode() for d in after.InstanceDefinitions], [d.Encode() for d in before.InstanceDefinitions])
        self.assertEqual([layer.Encode() for layer in after.Layers][:len(before.Layers)], [layer.Encode() for layer in before.Layers])
        self.assertEqual([m.Encode() for m in after.Materials][:len(before.Materials)], [m.Encode() for m in before.Materials])
        self.assertFalse(next(o.Geometry.IsValid for o in after.Objects if o.Attributes.Name == "invalid-imported-brep"))
        replacements = [o for o in after.Objects if o.Attributes.Name in selected.delete_object_names]
        self.assertEqual(len(replacements), 6)
        for item in replacements:
            self.assertEqual(item.Attributes.Id, original_ids[item.Attributes.Name])
            self.assertAlmostEqual(item.Geometry.GetBoundingBox().Max.X, 2.0 / 0.3048, places=5)
            self.assertEqual(after.Layers.FindIndex(item.Attributes.LayerIndex).FullPath, "native::replacement")
            self.assertEqual(after.Materials.FindIndex(item.Attributes.MaterialIndex).Name, "new finish")
            self.assertEqual([after.Groups.FindIndex(i).Name for i in item.Attributes.GetGroupList2()], ["new native"])

    def test_replaced_native_object_keeps_its_guid_after_file_save_and_cold_reopen(self):
        base = self.native_model(self.prior)
        donor = self.native_model(self.changed, replacement=True)
        selected = select_patch_operations(self.changed, self.prior)
        original_ids = {o.Attributes.Name: str(o.Attributes.Id) for o in base.Objects}
        donor_ids = {o.Attributes.Name: str(o.Attributes.Id) for o in donor.Objects}
        self.assertTrue(all(original_ids[name] != donor_ids[name]
                            for name in selected.changed_object_ids))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "revised.3dm"
            destination.write_bytes(self.patch(base, donor))
            reopened = subprocess.run(
                [sys.executable, "-c",
                 "import json,sys,rhino3dm; model=rhino3dm.File3dm.Read(sys.argv[1]); "
                 "print(json.dumps({o.Attributes.Name:str(o.Attributes.Id) for o in model.Objects}))",
                 str(destination)],
                check=True, capture_output=True, text=True,
            )
        self.assertEqual(json.loads(reopened.stdout), original_ids)

    def test_new_object_cannot_take_a_preserved_or_retired_native_guid(self):
        rows = list(_rows())
        rows[-1] = replace(rows[-1], element_id="pediment-new")
        changed = _compile(rows)
        selection = select_patch_operations(changed, self.prior)
        self.assertIn("obj-pediment-west", selection.retired_object_ids)
        self.assertIn("obj-pediment-new", selection.added_object_ids)
        for source_name in ("obj-columns-west-0", "obj-pediment-west"):
            with self.subTest(source_name=source_name):
                base = self.native_model(self.prior)
                original = self.encoded(base)
                source_id = next(o.Attributes.Id for o in base.Objects
                                 if o.Attributes.Name == source_name)
                donor = self.native_model(changed, replacement=True)
                added = next(o for o in donor.Objects if o.Attributes.Name == "obj-pediment-new")
                added.Attributes.Id = source_id
                with self.assertRaisesRegex(
                    CadPatchError, "new native object GUID collides with source identity: obj-pediment-new"
                ):
                    patch_composed_three_dm(
                        original, prior_program=self.prior, program=changed,
                        replacement_3dm=self.encoded(donor),
                    )
                retained = self.rhino.File3dm.FromByteArray(original).Objects.FindId(source_id)
                self.assertEqual(retained.Attributes.Name, source_name)

    def test_window_and_wall_opening_lower_300mm_while_roof_and_unnamed_source_stay(self):
        """Compose actual OCCT exports; this does not stand in for Stage admission."""
        try:
            import OCP  # noqa: F401 - optional native execution dependency
        except ImportError:
            self.skipTest("OCCT is not installed")
        from archflow.state.geometry_program import (
            GeometryOperation, GeometryOperationKind, GeometryParameter,
            GeometryParameterKind, LengthUnit,
        )
        from archflow.adapters.cad_execution import CadExecutionStatus, execute_occt_export
        from archflow.adapters.cad_program import expected_object_bounds

        r = self.rhino

        def build(sill):
            boxes = {
                "host-wall": ([0, 0, 0], [6, 6, 0.3]),
                "opening-tool": ([1, sill, -0.1], [1.5, 1.5, 0.5]),
                "window": ([1.05, sill + 0.05, 0.12], [1.4, 1.4, 0.06]),
                "roof": ([0, 6, 0], [6, 0.2, 4]),
            }
            operations = tuple(GeometryOperation(
                op_id=name, kind=GeometryOperationKind.SOLID,
                output_object_ids=(name,), input_object_ids=(), frame_id="world",
                parameters=tuple(GeometryParameter.create(
                    name=key, kind=GeometryParameterKind.VECTOR3,
                    value=value, unit=LengthUnit.METER,
                ) for key, value in zip(("origin", "size"), box)),
                semantic_binding_ids=("building-binding",),
            ) for name, box in boxes.items())
            operations += (GeometryOperation(
                op_id="wall-cut", kind=GeometryOperationKind.BOOLEAN_DIFFERENCE,
                output_object_ids=("wall-cut",),
                input_object_ids=("host-wall", "opening-tool"), frame_id="world",
                parameters=(),
                semantic_binding_ids=("building-binding",),
            ),)
            state = _state()
            proposal = _only(_proposal(state), operations, ())
            result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,))
            self.assertIsNotNone(result.program, result.receipt.issues)
            return result.program

        def native(program, workspace, stem):
            with patch("subprocess.Popen", side_effect=AssertionError("OCCT must not start Rhino or any process")):
                receipt = execute_occt_export(
                    program, binding=_binding(program), speculative_workspace=workspace,
                    artifact_stem=stem,
                )
            self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED, receipt.failures)
            self.assertIsNotNone(receipt.preview_artifact)
            return r.File3dm.Read(str(workspace / receipt.preview_artifact["relative_path"]))

        prior, current = build(3.5), build(3.2)
        selection = select_patch_operations(current, prior)
        self.assertEqual(selection.kept_object_ids, ("roof",))
        self.assertIn("wall-cut", selection.changed_object_ids)
        self.assertIn("window", selection.changed_object_ids)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            base = native(prior, workspace, "prior-window")
            donor = native(current, workspace, "lowered-window")
        external_id = base.Objects.AddPoint(r.Point3d(12, 2, 4))
        before = r.File3dm.FromByteArray(self.encoded(base))
        original = {item.Attributes.Name: item for item in before.Objects if item.Attributes.Name}
        after = r.File3dm.FromByteArray(patch_composed_three_dm(
            self.encoded(base), prior_program=prior, program=current,
            replacement_3dm=self.encoded(donor),
        ))
        saved = {item.Attributes.Name: item for item in after.Objects if item.Attributes.Name}
        for name in ("window", "wall-cut", "roof"):
            self.assertEqual(saved[name].Attributes.Id, original[name].Attributes.Id)
        self.assertEqual(saved["roof"].Geometry.Encode(), original["roof"].Geometry.Encode())
        self.assertEqual(saved["roof"].Attributes.Encode(), original["roof"].Attributes.Encode())
        external = after.Objects.FindId(external_id)
        self.assertEqual(external.Attributes.Name, "")
        self.assertEqual(external.Geometry.Encode(), before.Objects.FindId(external_id).Geometry.Encode())
        self.assertEqual(external.Attributes.Encode(), before.Objects.FindId(external_id).Attributes.Encode())
        self.assertAlmostEqual(
            saved["window"].Geometry.GetBoundingBox().Min.Z - original["window"].Geometry.GetBoundingBox().Min.Z,
            -0.3, places=5,
        )
        self.assertEqual({round(p.Z, 4) for p in saved["wall-cut"].Geometry.Vertices}, {0, 3.2, 4.7, 6})
        self.assertEqual({round(p.Z, 4) for p in original["wall-cut"].Geometry.Vertices}, {0, 3.5, 5, 6})
        expected = expected_object_bounds(current)
        for name, item in saved.items():
            bbox = item.Geometry.GetBoundingBox()
            for actual, planned in zip((bbox.Min.X, bbox.Min.Z, bbox.Min.Y), expected[name]["bbox_min"]):
                self.assertAlmostEqual(actual, planned, places=5)
            for actual, planned in zip((bbox.Max.X, bbox.Max.Z, bbox.Max.Y), expected[name]["bbox_max"]):
                self.assertAlmostEqual(actual, planned, places=5)

    def test_retired_and_added_native_objects_preserve_imported_assets(self):
        short = _compile(_rows()[:3])
        base = self.native_model(self.prior)
        self.add_imported_equipment(base)
        trimmed = self.rhino.File3dm.FromByteArray(self.patch(base, self.native_model(short), program=short))
        self.assertNotIn("obj-pediment-west", {o.Attributes.Name for o in trimmed.Objects})
        self.assertEqual(len(trimmed.Objects), len(base.Objects) - 1)
        restored = self.rhino.File3dm.FromByteArray(patch_composed_three_dm(
            self.encoded(trimmed), prior_program=short, program=self.prior,
            replacement_3dm=self.encoded(self.native_model(self.prior)),
        ))
        self.assertEqual({o.Attributes.Name for o in restored.Objects}, {o.Attributes.Name for o in base.Objects})
        self.assertEqual(len(restored.InstanceDefinitions), 1)

    def test_unassigned_replacements_inherit_exact_source_and_new_component_materials(self):
        r = self.rhino
        short = _compile(_rows()[:3])
        for prior, current in ((self.prior, self.changed), (short, self.prior)):
            with self.subTest(added=prior is short):
                base = self.native_model(prior)
                native = base.Materials[0]
                native.SetUserString("archflow:material", "source-glass")
                native.Transparency = 0.65
                native.ToPhysicallyBased()
                native.PhysicallyBased.Opacity = 0.35
                native.PhysicallyBased.Roughness = 0.27
                native.SetBitmapTexture("source-texture.png")
                donor = self.native_model(current, replacement=True)
                for layer in donor.Layers:
                    layer.RenderMaterialIndex = -1
                for item in (*base.Objects, *donor.Objects):
                    item.Attributes.SetUserString("archflow:component", "building")
                for item in donor.Objects:
                    item.Attributes.MaterialIndex = -1
                    item.Attributes.MaterialSource = r.ObjectMaterialSource.MaterialFromLayer
                before = r.File3dm.FromByteArray(self.encoded(base))
                output = patch_composed_three_dm(self.encoded(base), prior_program=prior, program=current, replacement_3dm=self.encoded(donor))
                after = r.File3dm.FromByteArray(output)
                self.assertEqual(len(after.Materials), len(before.Materials))
                for saved, original in zip(after.Materials, before.Materials):
                    self.assertEqual((saved.Id, saved.Name, saved.DiffuseColor, saved.Transparency, saved.GetUserStrings()),
                                     (original.Id, original.Name, original.DiffuseColor, original.Transparency, original.GetUserStrings()))
                    self.assertEqual(saved.PhysicallyBased.BaseColor, original.PhysicallyBased.BaseColor)
                    self.assertEqual(saved.PhysicallyBased.Roughness, original.PhysicallyBased.Roughness)
                replaced = set(_physical_ids(current.proposal)) - set(select_patch_operations(current, prior).kept_object_ids)
                for item in after.Objects:
                    if item.Attributes.Name in replaced:
                        self.assertEqual(item.Attributes.MaterialSource, r.ObjectMaterialSource.MaterialFromObject)
                        self.assertEqual(item.Attributes.MaterialIndex, 0)
                        self.assertEqual(item.Attributes.GetUserString("archflow:material"), "source-glass")
                self.assertAlmostEqual(after.Materials[0].Transparency, 0.65)
                self.assertAlmostEqual(after.Materials[0].PhysicallyBased.Opacity, 0.35)
                self.assertEqual(after.Materials[0].GetBitmapTexture().FileName, "source-texture.png")

    def test_new_object_does_not_guess_between_component_materials(self):
        r = self.rhino
        short = _compile(_rows()[:3])
        base = self.native_model(short)
        second = base.Materials.Add(r.Material())
        next(iter(base.Objects)).Attributes.MaterialIndex = second
        donor = self.native_model(self.prior)
        for item in (*base.Objects, *donor.Objects):
            item.Attributes.SetUserString("archflow:component", "building")
        for item in donor.Objects:
            item.Attributes.MaterialSource = r.ObjectMaterialSource.MaterialFromLayer
            item.Attributes.MaterialIndex = -1
        after = r.File3dm.FromByteArray(patch_composed_three_dm(self.encoded(base), prior_program=short, program=self.prior, replacement_3dm=self.encoded(donor)))
        added = next(item for item in after.Objects if item.Attributes.Name == "obj-pediment-west")
        self.assertEqual(added.Attributes.MaterialIndex, -1)
        self.assertEqual(len(after.Materials), 2)

    def test_unchanged_program_returns_original_bytes(self):
        base = self.native_model(self.prior, feet=True)
        self.add_imported_equipment(base)
        data = self.encoded(base)
        self.assertEqual(patch_composed_three_dm(
            data, prior_program=self.prior, program=self.prior,
            replacement_3dm=self.encoded(self.native_model(self.prior)),
        ), data)

    def test_missing_native_geometry_or_wrong_donor_unit_is_refused(self):
        base = self.native_model(self.prior)
        donor = self.native_model(self.changed)
        donor.Objects.Delete(next(o.Attributes.Id for o in donor.Objects if o.Attributes.Name == "obj-capitals-west-0"))
        with self.assertRaisesRegex(CadPatchError, "replacement is missing"):
            self.patch(base, donor)
        donor = self.native_model(self.changed, feet=True)
        with self.assertRaisesRegex(CadPatchError, "program's length unit"):
            self.patch(base, donor)
        base.Objects.Delete(next(o.Attributes.Id for o in base.Objects if o.Attributes.Name == "obj-columns-west-0"))
        with self.assertRaisesRegex(CadPatchError, "base is missing"):
            self.patch(base, self.native_model(self.changed))

    def test_custom_replacement_linetype_is_refused_without_touching_base(self):
        base = self.native_model(self.prior, feet=True)
        self.add_imported_equipment(base)
        original = self.encoded(base)
        donor = self.native_model(self.changed)
        donor.Linetypes.Add(self.rhino.Linetype.Dots)
        for item in donor.Objects:
            item.Attributes.LinetypeIndex = 0
        with self.assertRaisesRegex(CadPatchError, "custom replacement linetypes"):
            patch_composed_three_dm(
                original, prior_program=self.prior, program=self.changed,
                replacement_3dm=self.encoded(donor),
            )
        reopened = self.rhino.File3dm.FromByteArray(original)
        self.assertEqual(len(reopened.Objects), len(base.Objects))
        self.assertEqual(len(reopened.InstanceDefinitions), 1)
        self.assertEqual(reopened.Settings.ModelUnitSystem, self.rhino.UnitSystem.Feet)

    def test_external_object_sharing_a_native_name_is_refused_and_retained(self):
        base = self.native_model(self.prior)
        name = "obj-capitals-west-0"
        attributes = self.rhino.ObjectAttributes()
        attributes.Name = name
        attributes.SetUserString("source_leaf_uuid", "external-equipment")
        external_id = base.Objects.AddMesh(self.mesh(10.0), attributes)
        original = self.encoded(base)
        with self.assertRaisesRegex(CadPatchError, f"ambiguous native object {name}"):
            patch_composed_three_dm(
                original, prior_program=self.prior, program=self.changed,
                replacement_3dm=self.encoded(self.native_model(self.changed)),
            )
        reopened = self.rhino.File3dm.FromByteArray(original)
        self.assertEqual(len(reopened.Objects), 15)
        external = next(o for o in reopened.Objects if o.Attributes.Id == external_id)
        self.assertEqual(external.Attributes.GetUserString("source_leaf_uuid"), "external-equipment")
        self.assertEqual(external.Geometry.GetBoundingBox().Max.X, 10.0)

    def test_native_identity_must_be_unique_and_match_its_exported_reference(self):
        name = "obj-capitals-west-0"
        for target in ("base", "donor"):
            for fault in ("duplicate", "missing_ref", "wrong_ref"):
                with self.subTest(target=target, fault=fault):
                    base = self.native_model(self.prior)
                    donor = self.native_model(self.changed)
                    model = base if target == "base" else donor
                    item = next(o for o in model.Objects if o.Attributes.Name == name)
                    if fault == "duplicate":
                        attributes = self.rhino.ObjectAttributes()
                        attributes.Name = name
                        attributes.SetUserString("archflow:object_ref", f"cad-object:{name}")
                        model.Objects.AddMesh(self.mesh(), attributes)
                        error = f"ambiguous native object {name}"
                    else:
                        item.Attributes.SetUserString("archflow:object_ref", "" if fault == "missing_ref" else "cad-object:another-object")
                        error = f"missing or mismatched object_ref: {name}"
                    original = self.encoded(base)
                    before_ids = {o.Attributes.Id for o in base.Objects}
                    with self.assertRaisesRegex(CadPatchError, error):
                        patch_composed_three_dm(
                            original, prior_program=self.prior, program=self.changed,
                            replacement_3dm=self.encoded(donor),
                        )
                    reopened = self.rhino.File3dm.FromByteArray(original)
                    self.assertEqual({o.Attributes.Id for o in reopened.Objects}, before_ids)


if __name__ == "__main__":
    unittest.main()
