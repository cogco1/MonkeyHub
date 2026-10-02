"""A composed model wears what its run declares, whatever its geometry did (#580).

The composed model is an imported model with a run's native objects patched
in. The owner's rule: clear every material it carries and rewrite them from
the semantics. A declared object is bound to the one native material of its
declared name, in that material's colour, exactly as the run's own preview;
every other object - a component that declares nothing, an imported object
with no component - wears none and says ``archflow:material_status`` =
``undeclared``. GUIDs, logical references, every other user string and the
encoded geometry stay as the patch defines them, and the readback checks
geometry by content, never by bounds.

The models are built here with rhino3dm: an "imported" model carrying its
own materials (an object material, a layer material, a textured PBR one and a
block member's), with native objects exported under an earlier declaration.
"""

from __future__ import annotations

import base64
import hashlib
import unittest

from archflow.project.refs import ProjectVersionRef
from archflow.state.geometry_program import (
    AffineTransform,
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    CompiledGeometryObject,
    CompiledGeometryProgram,
    CoordinateFrame,
    DetailMaturity,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    LengthUnit,
    SemanticBinding,
    expected_object_bounds,
)
from monkeycad.backends.occt.export import _preview_materials
from monkeycad.formats.three_dm_compose import (
    ComposedPatch,
    patch_composed_three_dm,
    rewrite_composed_materials,
    verify_composed_three_dm,
)
from monkeycad.formats.three_dm_inspector import inspect_three_dm_contents
from monkeycad.patch import CadPatchError
from monkeycad.program import _material_identity_color, _resolved_layer_colors, expected_object_semantics

try:
    import rhino3dm
except ImportError:  # pragma: no cover - the CAD extra is not installed
    rhino3dm = None

# Five native parts: two walls of one material, a frame of another, a base
# whose component declares none, and an object no component claims.
PARTS = (
    ("wall-a", "wall-a", 0.0),
    ("wall-b", "wall-b", 3.0),
    ("frame", "frame", 6.0),
    ("base", "base", 9.0),
    ("loose", None, 12.0),
)
# What an earlier step declared: the composed model was exported with it.
EARLIER = {"wall-a": "brick", "frame": "oak", "base": "granite"}
EARLIER_COLORS = {"brick": (150, 60, 40), "oak": (120, 80, 40), "granite": (90, 90, 95)}
# What the run declares now: a material-only change from EARLIER.
DECLARED = {"wall-a": "hemp-lime", "wall-b": "hemp-lime", "frame": "timber"}
COLORS = {"hemp-lime": (200, 185, 143)}
HEMP_LIME = [200, 185, 143, 255]
TIMBER = [*_material_identity_color("timber"), 255]
FEET = 1.0 / 0.3048


def _vector(name: str, value: list[float]) -> GeometryParameter:
    return GeometryParameter.create(name=name, kind=GeometryParameterKind.VECTOR3, value=value, unit=LengthUnit.METER)


def _compile(operations, bindings, *, assemblies=()) -> CompiledGeometryProgram:
    base = ProjectVersionRef("compose-demo", 0, "1" * 64)
    operations = tuple(sorted(operations, key=lambda op: op.op_id))
    bindings = tuple(sorted(bindings, key=lambda item: item.binding_id))
    proposal = GeometryProgramProposal(
        proposal_id="compose-proposal", project_id="compose-demo", run_id="run-1", base=base,
        design_state_digest="2" * 64, predecessor_program_digest=None, length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(CoordinateFrame(frame_id="world", parent_frame_id=None, transform_from_parent=AffineTransform.identity(),
                                source_refs=("evidence:frame",)),),
        assets=(), semantic_bindings=bindings, operations=operations, assemblies=tuple(assemblies),
    )
    objects = tuple(sorted(
        (CompiledGeometryObject(object_id=out, producer_op_id=op.op_id,
                                object_digest=hashlib.sha256(f"{op.op_id}:{out}".encode()).hexdigest())
         for op in operations for out in op.output_object_ids),
        key=lambda item: item.object_id,
    ))
    components = sorted({item.component_id for item in bindings})
    return CompiledGeometryProgram(
        proposal=proposal, operation_order=tuple(op.op_id for op in operations),
        frame_digests=(("world", "5" * 64),),
        component_digests=tuple((component, "6" * 64) for component in components),
        semantic_binding_digests=tuple((item.binding_id, "7" * 64) for item in bindings),
        objects=objects, asset_substitutions=(),
    )


def _program(heights: dict[str, float] | None = None) -> CompiledGeometryProgram:
    operations, bindings = [], []
    for op_id, component, x in PARTS:
        height = (heights or {}).get(op_id, 3.0)
        operations.append(GeometryOperation(
            op_id=op_id, kind=GeometryOperationKind.SOLID, output_object_ids=(f"{op_id}-object",),
            input_object_ids=(), frame_id="world",
            parameters=(_vector("origin", [x, 0.0, 0.0]), _vector("size", [2.0, height, 0.5])),
            semantic_binding_ids=(f"{component}-binding",) if component else (),
        ))
        if component:
            bindings.append(SemanticBinding(binding_id=f"{component}-binding", component_id=component,
                                            object_ids=(f"{op_id}-object",), commitment_refs=("commitment:compose",),
                                            evidence_refs=("evidence:compose",)))
    return _compile(operations, bindings)


def _window_program() -> CompiledGeometryProgram:
    """A wall with a window whose frame and glass belong to one component."""

    boxes = {"wall": [0.0, 0.0, 0.0], "opening": [1.0, 1.0, -0.1], "window-frame": [1.0, 1.0, 0.1],
             "window-glass": [1.1, 1.1, 0.2]}
    owners = {"wall": "wall", "opening": "window", "window-frame": "window", "window-glass": "window"}
    operations = [GeometryOperation(
        op_id=name, kind=GeometryOperationKind.SOLID, output_object_ids=(f"{name}-object",), input_object_ids=(),
        frame_id="world", parameters=(_vector("origin", origin), _vector("size", [1.0, 1.0, 0.05])),
        semantic_binding_ids=(f"{owners[name]}-binding",),
    ) for name, origin in boxes.items()]
    bindings = [SemanticBinding(binding_id=f"{component}-binding", component_id=component,
                                object_ids=tuple(f"{name}-object" for name, owner in owners.items() if owner == component),
                                commitment_refs=("commitment:compose",), evidence_refs=("evidence:compose",))
                for component in ("wall", "window")]
    window = HostedAssembly(
        assembly_id="window-1", kind=AssemblyKind.WINDOW, host_object_id="wall-object", host_socket_id="socket-1",
        members=(AssemblyMember(AssemblyRole.FRAME, ("window-frame-object",)),
                 AssemblyMember(AssemblyRole.GLAZING, ("window-glass-object",)),
                 AssemblyMember(AssemblyRole.HOST_CUT, ("opening-object",))),
        interface_refs=(), semantic_binding_ids=("window-binding",), maturity=DetailMaturity.ENVELOPE,
    )
    return _compile(operations, bindings, assemblies=(window,))


def _box(bounds, scale: float = 1.0):
    (x0, y0, z0), (x1, y1, z1) = ([value * scale for value in bounds["bbox_min"]],
                                  [value * scale for value in bounds["bbox_max"]])
    mesh = rhino3dm.Mesh()
    for x, y, z in ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                    (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)):
        mesh.Vertices.Add(x, y, z)
    for a, b, c, d in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        mesh.Faces.AddFace(a, b, c, d)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _export(program, materials=None, colors=None, *, feet: bool = False, parts=None):
    """The program's native preview as the OCCT export writes it: names, layers, user text and its materials."""

    semantics = expected_object_semantics(program, material_by_component=materials, material_by_part=parts)
    objects = semantics["objects"]
    layer_colors = dict(_resolved_layer_colors({row["layer"] for row in objects.values()},
                                               material_by_component=materials, material_colors=colors))
    worn = _preview_materials(program, physical=tuple(sorted(objects)), semantics=semantics,
                              layer_colors=layer_colors, material_colors=colors)
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Feet if feet else rhino3dm.UnitSystem.Meters
    layers: dict[str, int] = {}

    def layer(path: str) -> int:
        if path not in layers:
            *parents, name = path.split("::")
            native = rhino3dm.Layer()
            native.Name = name
            if parents:
                native.ParentLayerId = model.Layers.FindIndex(layer("::".join(parents))).Id
            native.Color = (*layer_colors.get(path, (0, 0, 0)), 255)
            layers[path] = model.Layers.Add(native)
        return layers[path]

    entries: dict[object, int] = {}
    bounds = expected_object_bounds(program)
    for object_id, row in sorted(objects.items()):
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = object_id
        attributes.LayerIndex = layer(row["layer"])
        for key, value in sorted(row["user_text"].items()):
            attributes.SetUserString(key, value)
        material = worn.get(object_id)
        if material is not None:
            if material not in entries:
                native = rhino3dm.Material()
                native.Name = material.name
                native.DiffuseColor = (*material.diffuse, 255)
                native.Transparency = material.transparency
                native.SetUserString("archflow:material_id", material.name)
                entries[material] = model.Materials.Add(native)
            attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            attributes.MaterialIndex = entries[material]
        model.Objects.AddMesh(_box(bounds[object_id], FEET if feet else 1.0), attributes)
    return model


def _material(model, name: str, color: tuple[int, int, int], *, textured: bool = False) -> int:
    native = rhino3dm.Material()
    native.Name = name
    native.DiffuseColor = (*color, 255)
    if textured:
        native.ToPhysicallyBased()
        native.PhysicallyBased.Roughness = 0.4
        native.SetBitmapTexture("survey-veneer.png")
    return model.Materials.Add(native)


def _add_imported(model) -> None:
    """What the imported model brought: its own layer, materials, a block and objects with and without components."""

    layer = rhino3dm.Layer()
    layer.Name = "imported survey"
    layer.Color = (40, 70, 180, 255)
    layer.RenderMaterialIndex = _material(model, "imported veneer", (170, 120, 60), textured=True)
    layer_index = model.Layers.Add(layer)

    def attributes(name: str, **strings: str):
        native = rhino3dm.ObjectAttributes()
        native.Name = name
        native.LayerIndex = layer_index
        for key, value in strings.items():
            native.SetUserString(key.replace("__", ":"), value)
        return native

    # takes its material from the layer's veneer, and names a component that now declares timber
    cabinet = attributes("imported-cabinet", archflow__component="frame", source="survey")
    model.Objects.AddMesh(_box({"bbox_min": [20, 0, 0], "bbox_max": [21, 1, 1]}), cabinet)
    # wears its own brass and names no component
    rail = attributes("imported-rail", source="survey")
    rail.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
    rail.MaterialIndex = _material(model, "imported brass", (200, 170, 60))
    model.Objects.AddCurve(rhino3dm.PolylineCurve([rhino3dm.Point3d(20, 2, 0), rhino3dm.Point3d(24, 2, 0),
                                                   rhino3dm.Point3d(24, 5, 1)]), rail)
    # names a component that declares nothing, and still wears the stone it was imported with
    plinth = attributes("imported-plinth", archflow__component="base")
    plinth.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
    plinth.MaterialIndex = _material(model, "imported stone", (130, 130, 120))
    model.Objects.AddMesh(_box({"bbox_min": [22, 0, 0], "bbox_max": [23, 1, 1]}), plinth)
    # an unnamed point with no strings at all
    model.Objects.AddPoint(rhino3dm.Point3d(25, 0, 0), attributes(""))
    # a block whose member wears enamel, placed once with no component
    member = attributes("imported-fixture-body")
    member.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
    member.MaterialIndex = _material(model, "imported enamel", (240, 240, 230))
    definition = model.InstanceDefinitions.FindIndex(model.InstanceDefinitions.Add(
        "imported-fixture", "survey fixture", "", "", rhino3dm.Point3d(0, 0, 0),
        (_box({"bbox_min": [0, 0, 0], "bbox_max": [0.5, 0.5, 0.5]}),), (member,)))
    model.Objects.AddInstanceObject(rhino3dm.InstanceReference(definition.Id, rhino3dm.Transform.Translation(26, 0, 0)),
                                    attributes("imported-fixture-1", source="survey"))


def _encoded(model) -> bytes:
    return base64.b64decode(model.Encode())


def _composed_base(program, *, feet: bool = False) -> bytes:
    """A composed model: the native objects as an earlier step exported them, plus the imported model."""

    model = _export(program, EARLIER, EARLIER_COLORS, feet=feet)
    _add_imported(model)
    return _encoded(model)


def _by_name(rows, key: str = "name") -> dict[str, dict]:
    return {row[key]: row for row in rows}


def _strings(inspection) -> dict[str, dict[str, str]]:
    """Every object's user strings by its GUID."""

    return {row["object_id"]: {pair["key"]: pair["value"] for pair in row["attributes"]}
            for row in inspection.object_user_strings}


@unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
class ComposedMaterialTests(unittest.TestCase):
    def compose(self, base: bytes, prior, program, materials=DECLARED, colors=COLORS) -> tuple[bytes, bytes]:
        donor = _encoded(_export(program, materials, colors))
        patched = patch_composed_three_dm(base, prior_program=prior, program=program, replacement_3dm=donor)
        composed = rewrite_composed_materials(patched, programs=(program,), material_by_component=materials,
                                              material_colors=colors)
        verify_composed_three_dm(composed, base_3dm=base, patches=(ComposedPatch(prior, program, donor),),
                                 material_by_component=materials, material_colors=colors)
        return patched, composed

    def assert_declared_semantics(self, composed: bytes) -> None:
        """Each object wears exactly what the run declares, and nothing the imported model or EARLIER brought."""

        inspection = inspect_three_dm_contents(composed)
        bindings = {row["object_id"]: row for row in inspection.object_material_bindings}
        strings = _strings(inspection)
        names = {row["object_id"]: row["name"] for row in inspection.object_material_bindings}
        declared = {"wall-a-object": ("hemp-lime", HEMP_LIME), "wall-b-object": ("hemp-lime", HEMP_LIME),
                    "frame-object": ("timber", TIMBER), "imported-cabinet": ("timber", TIMBER)}
        for object_id, row in bindings.items():
            name = names[object_id]
            labels = strings.get(object_id, {})
            if row["is_instance_definition_object"]:
                self.assertEqual((row["material_source"], row["material_index"]), ("MaterialFromParent", -1), name)
                continue
            if name in declared:
                material, color = declared[name]
                self.assertEqual((row["material_source"], row["material_name"], row["archflow_material_id"],
                                  row["material_diffuse_color_rgba"]), ("MaterialFromObject", material, material, color), name)
                self.assertEqual(labels.get("archflow:material"), material, name)
                self.assertNotIn("archflow:material_status", labels, name)
            else:
                # the base, the unclaimed native object and every imported object without a declaration
                self.assertEqual((row["material_source"], row["material_index"], row["material_name"]),
                                 ("MaterialFromLayer", -1, None), name)
                self.assertEqual(labels.get("archflow:material_status"), "undeclared", name)
                self.assertNotIn("archflow:material", labels, name)
        model = rhino3dm.File3dm.FromByteArray(composed)
        self.assertEqual({layer.RenderMaterialIndex for layer in model.Layers}, {-1})
        # one table entry per declared material is worn; nothing an earlier step or the import brought is
        worn = {row["material_index"] for row in bindings.values() if row["material_index"] >= 0}
        self.assertEqual(sorted(model.Materials.FindIndex(index).Name for index in worn), ["hemp-lime", "timber"])

    def assert_same_identity(self, before: bytes, after: bytes, *, except_names=()) -> None:
        """GUIDs, logical references, other strings and geometry content are the base's for every untouched object."""

        old, new = inspect_three_dm_contents(before), inspect_three_dm_contents(after)
        geometry_before = {row["object_id"]: row for row in old.object_geometry_sha256 if row["name"] not in except_names}
        geometry_after = {row["object_id"]: row for row in new.object_geometry_sha256 if row["name"] not in except_names}
        self.assertEqual(geometry_after, geometry_before)

        def other_strings(inspection) -> dict[str, dict[str, str]]:
            # every user string but the two material labels the rewrite owns
            rows = {}
            for object_id, pairs in _strings(inspection).items():
                kept = {key: value for key, value in pairs.items()
                        if key not in ("archflow:material", "archflow:material_status")}
                if object_id in geometry_before and kept:
                    rows[object_id] = kept
            return rows

        self.assertEqual(other_strings(new), other_strings(old))

    def test_a_material_only_change_rewrites_the_composed_model(self) -> None:
        program = _program()
        base = _composed_base(program)
        patched, composed = self.compose(base, program, program)
        # the geometry patch alone has nothing to do: it is the base's own bytes ...
        self.assertEqual(patched, base)
        # ... and the composed model no longer is
        self.assertNotEqual(hashlib.sha256(composed).hexdigest(), hashlib.sha256(base).hexdigest())
        self.assert_declared_semantics(composed)
        self.assert_same_identity(base, composed)
        inspection = inspect_three_dm_contents(composed)
        refs = {row["name"]: {pair["key"]: pair["value"] for pair in row["attributes"]}.get("archflow:object_ref")
                for row in inspection.object_user_strings}
        self.assertEqual(refs["wall-a-object"], "cad-object:wall-a-object")

    def test_a_geometry_change_also_rewrites_the_objects_it_kept(self) -> None:
        prior, program = _program(), _program({"wall-b": 2.4})
        base = _composed_base(prior, feet=True)
        patched, composed = self.compose(base, prior, program)
        # wall-b was rebuilt from the run's export, so it arrives wearing hemp-lime; wall-a and frame were
        # kept and still wear the brick and oak of the earlier step until the rewrite
        before = {row["name"]: row for row in inspect_three_dm_contents(patched).object_material_bindings}
        self.assertEqual(before["wall-b-object"]["material_name"], "hemp-lime")
        self.assertEqual(before["wall-a-object"]["material_name"], "brick")
        self.assertEqual(before["frame-object"]["material_name"], "oak")
        self.assert_declared_semantics(composed)
        self.assert_same_identity(base, composed, except_names={"wall-b-object"})
        old = _by_name(inspect_three_dm_contents(base).object_geometry_sha256)
        new = _by_name(inspect_three_dm_contents(composed).object_geometry_sha256)
        self.assertEqual(new["wall-b-object"]["object_id"], old["wall-b-object"]["object_id"])
        self.assertNotEqual(new["wall-b-object"]["geometry_sha256"], old["wall-b-object"]["geometry_sha256"])
        # the rebuilt object carries the export's geometry, converted to the base's feet
        model = rhino3dm.File3dm.FromByteArray(composed)
        wall = next(item for item in model.Objects if item.Attributes.Name == "wall-b-object")
        self.assertAlmostEqual(wall.Geometry.GetBoundingBox().Max.Y, 2.4 * FEET, places=6)

    def test_an_imported_object_with_no_component_wears_nothing_and_says_so(self) -> None:
        program = _program()
        base = _composed_base(program)
        _, composed = self.compose(base, program, program)
        inspection = inspect_three_dm_contents(composed)
        bindings = _by_name(inspection.object_material_bindings)
        strings = _strings(inspection)
        for name in ("imported-rail", "imported-fixture-1", ""):
            row = bindings[name]
            self.assertEqual((row["material_source"], row["material_index"], row["material_name"]),
                             ("MaterialFromLayer", -1, None), name)
            self.assertEqual(strings[row["object_id"]]["archflow:material_status"], "undeclared", name)
            self.assertNotIn("archflow:material", strings[row["object_id"]], name)
        # their own strings are untouched
        self.assertEqual(strings[bindings["imported-rail"]["object_id"]]["source"], "survey")
        model = rhino3dm.File3dm.FromByteArray(composed)
        rail = next(item for item in model.Objects if item.Attributes.Name == "imported-rail")
        self.assertEqual(model.Layers.FindIndex(rail.Attributes.LayerIndex).RenderMaterialIndex, -1)

    def test_a_model_already_wearing_its_declarations_is_returned_unchanged(self) -> None:
        program = _program()
        _, composed = self.compose(_composed_base(program), program, program)
        again = rewrite_composed_materials(composed, programs=(program,), material_by_component=DECLARED,
                                           material_colors=COLORS)
        self.assertIs(again, composed)
        # a later colour reuses nothing stale: it is a new entry, and the previous one is left unworn
        recolored = rewrite_composed_materials(composed, programs=(program,), material_by_component=DECLARED,
                                               material_colors={"hemp-lime": (10, 20, 30)})
        rows = _by_name(inspect_three_dm_contents(recolored).object_material_bindings)
        self.assertEqual(rows["wall-a-object"]["material_diffuse_color_rgba"], [10, 20, 30, 255])
        self.assertEqual(len(rhino3dm.File3dm.FromByteArray(recolored).Materials),
                         len(rhino3dm.File3dm.FromByteArray(composed).Materials) + 1)

    def test_assembly_members_wear_what_the_runs_preview_gives_them(self) -> None:
        program = _window_program()
        materials = {"window": "timber"}
        base = _encoded(_export(program, {"window": "oak"}, None))
        _, composed = self.compose(base, program, program, materials=materials, colors=None)
        rows = _by_name(inspect_three_dm_contents(composed).object_material_bindings)
        strings = _strings(inspect_three_dm_contents(composed))
        # the glass keeps the preview's glass, the frame and opening their declared timber, the wall is undeclared
        glass = rows["window-glass-object"]
        self.assertEqual((glass["material_name"], glass["material_transparency"]), ("glazing", 0.6))
        self.assertEqual(strings[glass["object_id"]]["archflow:material"], "timber")
        self.assertEqual(rows["window-frame-object"]["material_name"], "timber")
        self.assertEqual(rows["wall-object"]["material_name"], None)
        self.assertEqual(strings[rows["wall-object"]["object_id"]]["archflow:material_status"], "undeclared")


@unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
class ComposedReadbackTests(unittest.TestCase):
    """The readback refuses a composed model whose identity, geometry or materials are not what was declared."""

    def setUp(self) -> None:
        self.prior, self.program = _program(), _program({"wall-b": 2.4})
        self.base = _composed_base(self.prior)
        self.donor = _encoded(_export(self.program, DECLARED, COLORS))
        patched = patch_composed_three_dm(self.base, prior_program=self.prior, program=self.program,
                                          replacement_3dm=self.donor)
        self.composed = rewrite_composed_materials(patched, programs=(self.program,),
                                                   material_by_component=DECLARED, material_colors=COLORS)

    def verify(self, data: bytes) -> None:
        verify_composed_three_dm(data, base_3dm=self.base,
                                 patches=(ComposedPatch(self.prior, self.program, self.donor),),
                                 material_by_component=DECLARED, material_colors=COLORS)

    def tampered(self, change) -> bytes:
        model = rhino3dm.File3dm.FromByteArray(self.composed)
        change(model)
        return _encoded(model)

    def test_the_composed_model_reads_back(self) -> None:
        self.verify(self.composed)

    def test_a_moved_vertex_in_a_kept_object_is_refused(self) -> None:
        def move(model):
            rail = next(item for item in model.Objects if item.Attributes.Name == "imported-rail")
            rail.Geometry.SetPoint(1, rhino3dm.Point3d(24, 2, 0.001))
        with self.assertRaisesRegex(CadPatchError, "imported-rail .* geometry differs"):
            self.verify(self.tampered(move))

    def test_a_rebuilt_object_with_the_base_geometry_is_refused(self) -> None:
        old = rhino3dm.File3dm.FromByteArray(self.base)
        stale = next(item for item in old.Objects if item.Attributes.Name == "wall-b-object")

        def restore(model):
            wall = next(item for item in model.Objects if item.Attributes.Name == "wall-b-object")
            model.Objects.Delete(wall.Attributes.Id)
            model.Objects.Add(stale.Geometry, wall.Attributes)
        with self.assertRaisesRegex(CadPatchError, "wall-b-object .* geometry differs"):
            self.verify(self.tampered(restore))

    def test_a_new_guid_is_refused(self) -> None:
        def reissue(model):
            rail = next(item for item in model.Objects if item.Attributes.Name == "imported-rail")
            attributes = rail.Attributes
            geometry = rail.Geometry.Duplicate()
            model.Objects.Delete(attributes.Id)
            attributes.Id = rhino3dm.ObjectAttributes().Id
            model.Objects.Add(geometry, attributes)
        with self.assertRaisesRegex(CadPatchError, "missing.*unexpected"):
            self.verify(self.tampered(reissue))

    def test_an_imported_material_left_on_an_undeclared_object_is_refused(self) -> None:
        def restore(model):
            plinth = next(item for item in model.Objects if item.Attributes.Name == "imported-plinth")
            plinth.Attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            plinth.Attributes.MaterialIndex = next(index for index, material in enumerate(model.Materials)
                                                   if material.Name == "imported stone")
        with self.assertRaisesRegex(CadPatchError, "imported-plinth .* declares no material but wears one"):
            self.verify(self.tampered(restore))

    def test_a_declared_material_in_another_colour_or_a_lost_label_is_refused(self) -> None:
        def recolor(model):
            wall = next(item for item in model.Objects if item.Attributes.Name == "wall-a-object")
            model.Materials.FindIndex(wall.Attributes.MaterialIndex).DiffuseColor = (1, 2, 3, 255)
        with self.assertRaisesRegex(CadPatchError, "wall-a-object .* does not wear material hemp-lime"):
            self.verify(self.tampered(recolor))

        def unlabel(model):
            cabinet = next(item for item in model.Objects if item.Attributes.Name == "imported-cabinet")
            cabinet.Attributes.SetUserString("archflow:material", "")
        with self.assertRaisesRegex(CadPatchError, "imported-cabinet .* is labelled"):
            self.verify(self.tampered(unlabel))

    def test_a_layer_material_or_a_block_members_own_material_is_refused(self) -> None:
        def relayer(model):
            model.Layers.FindIndex(0).RenderMaterialIndex = 0
        with self.assertRaisesRegex(CadPatchError, "still carries a render material"):
            self.verify(self.tampered(relayer))

        def member(model):
            body = next(item for item in model.Objects if item.Attributes.IsInstanceDefinitionObject)
            body.Attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            body.Attributes.MaterialIndex = 0
        with self.assertRaisesRegex(CadPatchError, "block member .* does not wear its instance's material"):
            self.verify(self.tampered(member))


# The two mixed components of #580 as the compiler names their parts' objects (obj-<part>).
PARTED = (
    ("plinth-outer-brick", "plinth", 0.0),
    ("plinth-inner-block", "plinth", 3.0),
    ("floor-porcelain-1", "floor_finish", 6.0),
    ("external-paving", "floor_finish", 9.0),
)
# What the run declares (``declared_materials``): the plinth's default brick and its block's own concrete,
# the floor's tile on its inside part only.
PART_DEFAULTS = {"plinth": "brick"}
PART_MATERIALS = {
    "plinth": {"plinth-outer-brick": "brick", "plinth-inner-block": "concrete block"},
    "floor_finish": {"floor-porcelain-1": "porcelain tile", "external-paving": None},
}
PART_COLORS = {"brick": (158, 75, 50), "concrete block": (180, 178, 170)}


def _parted_program() -> CompiledGeometryProgram:
    owned: dict[str, list[str]] = {}
    operations = []
    for part, component, x in PARTED:
        owned.setdefault(component, []).append(f"obj-{part}")
        operations.append(GeometryOperation(
            op_id=part, kind=GeometryOperationKind.SOLID, output_object_ids=(f"obj-{part}",), input_object_ids=(),
            frame_id="world", parameters=(_vector("origin", [x, 0.0, 0.0]), _vector("size", [2.0, 0.5, 0.5])),
            semantic_binding_ids=(f"{component}-binding",),
        ))
    bindings = [SemanticBinding(binding_id=f"{component}-binding", component_id=component, object_ids=tuple(sorted(objects)),
                                commitment_refs=("commitment:compose",), evidence_refs=("evidence:compose",))
                for component, objects in owned.items()]
    return _compile(operations, bindings)


def _parted_base(program) -> bytes:
    """The parted program as an earlier step exported it (one stone per component), and what was left beside it."""

    model = _export(program, {"plinth": "granite", "floor_finish": "oak"}, EARLIER_COLORS)
    survey = rhino3dm.Layer()
    survey.Name = "survey"
    layer = model.Layers.Add(survey)
    for name, component in (("imported-plinth-cap", "plinth"), ("obj-floor-porcelain-1-0", "floor_finish"),
                            ("obj-external-paving-0", "floor_finish")):
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        attributes.LayerIndex = layer
        attributes.SetUserString("archflow:component", component)
        attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
        attributes.MaterialIndex = _material(model, f"{name} stone", (130, 130, 120))
        model.Objects.AddMesh(_box({"bbox_min": [20, 0, 0], "bbox_max": [21, 1, 1]}), attributes)
    return _encoded(model)


@unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
class ComposedPartMaterialTests(unittest.TestCase):
    """#580 gap 2: parts of one component wear their own materials in the composed model, read back by the same rule."""

    def setUp(self) -> None:
        self.program = _parted_program()
        self.base = _parted_base(self.program)
        self.donor = _encoded(_export(self.program, PART_DEFAULTS, PART_COLORS, parts=PART_MATERIALS))
        patched = patch_composed_three_dm(self.base, prior_program=self.program, program=self.program,
                                          replacement_3dm=self.donor)
        self.composed = rewrite_composed_materials(patched, programs=(self.program,), material_by_component=PART_DEFAULTS,
                                                   material_by_part=PART_MATERIALS, material_colors=PART_COLORS)

    def verify(self, data: bytes) -> None:
        verify_composed_three_dm(data, base_3dm=self.base,
                                 patches=(ComposedPatch(self.program, self.program, self.donor),),
                                 material_by_component=PART_DEFAULTS, material_by_part=PART_MATERIALS,
                                 material_colors=PART_COLORS)

    def test_each_part_wears_its_own_material_and_an_undeclared_part_wears_none(self) -> None:
        self.verify(self.composed)
        inspection = inspect_three_dm_contents(self.composed)
        rows = _by_name(inspection.object_material_bindings)
        strings = _strings(inspection)
        concrete = [*PART_COLORS["concrete block"], 255]
        brick = [*PART_COLORS["brick"], 255]
        tile = [*_material_identity_color("porcelain tile"), 255]
        for name, material, color in (("obj-plinth-outer-brick", "brick", brick),
                                      ("obj-plinth-inner-block", "concrete block", concrete),
                                      # an object no part delivered takes its component's material
                                      ("imported-plinth-cap", "brick", brick),
                                      ("obj-floor-porcelain-1", "porcelain tile", tile),
                                      # one an earlier export left under a part's name is that part's
                                      ("obj-floor-porcelain-1-0", "porcelain tile", tile)):
            row = rows[name]
            self.assertEqual((row["material_source"], row["material_name"], row["material_diffuse_color_rgba"]),
                             ("MaterialFromObject", material, color), name)
            self.assertEqual(strings[row["object_id"]].get("archflow:material"), material, name)
            self.assertNotIn("archflow:material_status", strings[row["object_id"]], name)
        for name in ("obj-external-paving", "obj-external-paving-0"):
            row = rows[name]
            self.assertEqual((row["material_source"], row["material_index"], row["material_name"]),
                             ("MaterialFromLayer", -1, None), name)
            self.assertEqual(strings[row["object_id"]].get("archflow:material_status"), "undeclared", name)
            self.assertNotIn("archflow:material", strings[row["object_id"]], name)
        # nothing else moved: GUIDs, geometry and every other string are the base's
        self.assertEqual({row["object_id"]: row["geometry_sha256"] for row in inspection.object_geometry_sha256},
                         {row["object_id"]: row["geometry_sha256"]
                          for row in inspect_three_dm_contents(self.base).object_geometry_sha256})

    def test_a_part_wearing_its_components_material_or_an_undeclared_part_wearing_one_is_refused(self) -> None:
        def as_component(model):
            outer = next(item for item in model.Objects if item.Attributes.Name == "obj-plinth-outer-brick")
            inner = next(item for item in model.Objects if item.Attributes.Name == "obj-plinth-inner-block")
            inner.Attributes.MaterialIndex = outer.Attributes.MaterialIndex
        with self.assertRaisesRegex(CadPatchError, "obj-plinth-inner-block .* does not wear material concrete block"):
            self.verify(_tampered(self.composed, as_component))

        def dressed(model):
            tile = next(item for item in model.Objects if item.Attributes.Name == "obj-floor-porcelain-1")
            paving = next(item for item in model.Objects if item.Attributes.Name == "obj-external-paving")
            paving.Attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            paving.Attributes.MaterialIndex = tile.Attributes.MaterialIndex
        with self.assertRaisesRegex(CadPatchError, "obj-external-paving .* declares no material but wears one"):
            self.verify(_tampered(self.composed, dressed))
        # the same model read against the component materials alone is not what was composed
        with self.assertRaisesRegex(CadPatchError, "obj-plinth-inner-block .* does not wear material brick"):
            verify_composed_three_dm(self.composed, base_3dm=self.base,
                                     patches=(ComposedPatch(self.program, self.program, self.donor),),
                                     material_by_component=PART_DEFAULTS, material_colors=PART_COLORS)


def _tampered(data: bytes, change) -> bytes:
    model = rhino3dm.File3dm.FromByteArray(data)
    change(model)
    return _encoded(model)


if __name__ == "__main__":
    unittest.main()
