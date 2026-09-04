from __future__ import annotations

from dataclasses import replace
import unittest

from archive.archflow.adapters.sandbox_render import render_paper_views
from archive.archflow.realization.sandbox import VoxelizationPolicy, derive_voxel_view, realize_geometry
from archive.archflow.realization.sandbox import (
    _SUPPORTED,
    SandboxAssetPayload,
    SandboxRealizationError,
    _contains,
    _mesh_from_sections,
)
from archflow.compilers.geometry import compile_geometry_program
from archflow.state.geometry_program import (
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    LengthUnit,
)
from tests.test_geometry_compiler import COMMITMENT
from archive.tests.test_sandbox_realization import compiled_room


def _parameter(
    name: str,
    kind: GeometryParameterKind,
    value: object,
    *,
    unit: LengthUnit | None = None,
) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=kind,
        value=value,
        unit=unit,
    )


def _operation(
    op_id: str,
    kind: GeometryOperationKind,
    parameters: tuple[GeometryParameter, ...],
    *,
    inputs: tuple[str, ...] = (),
) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=kind,
        output_object_ids=(op_id,),
        input_object_ids=tuple(sorted(inputs)),
        frame_id="world",
        parameters=tuple(sorted(parameters, key=lambda item: item.name)),
        semantic_binding_ids=("room-binding",),
    )


def _compile_with(*operations: GeometryOperation):
    design_state, program, _ = compiled_room()
    binding = replace(
        program.proposal.semantic_bindings[0],
        object_ids=tuple(
            sorted(
                (
                    *program.proposal.semantic_bindings[0].object_ids,
                    *(item.op_id for item in operations),
                )
            )
        ),
    )
    proposal = replace(
        program.proposal,
        proposal_id="function-evaluator-proposal",
        semantic_bindings=(binding,),
        operations=tuple(
            sorted(
                (*program.proposal.operations, *operations),
                key=lambda item: item.op_id,
            )
        ),
    )
    result = compile_geometry_program(
        design_state,
        proposal,
        active_commitment_refs=(COMMITMENT,),
    )
    if result.program is None:
        raise AssertionError(result.receipt.issues)
    return result.program


class GeometryFunctionEvaluatorTests(unittest.TestCase):
    def test_declared_operation_vocabulary_has_a_sandbox_evaluator(self) -> None:
        self.assertEqual(_SUPPORTED, frozenset(GeometryOperationKind))

    def test_revolve_cylinder_cone_and_frustum_use_exact_containment(self) -> None:
        operations = tuple(
            _operation(
                f"revolve-{name}",
                GeometryOperationKind.REVOLVE,
                (
                    _parameter("axis_end", GeometryParameterKind.VECTOR3, [offset, 4, 0], unit=LengthUnit.METER),
                    _parameter("axis_start", GeometryParameterKind.VECTOR3, [offset, 0, 0], unit=LengthUnit.METER),
                    _parameter("end_radius", GeometryParameterKind.NUMBER, end, unit=LengthUnit.METER),
                    _parameter("start_radius", GeometryParameterKind.NUMBER, start, unit=LengthUnit.METER),
                ),
            )
            for name, offset, start, end in (
                ("cylinder", 10, 2, 2),
                ("cone", 15, 2, 0),
                ("frustum", 20, 2, 1),
            )
        )
        program = _compile_with(*operations)
        first = realize_geometry(program, workspace_id="functions")
        second = realize_geometry(program, workspace_id="functions")
        assert first.scene is not None and second.scene is not None
        objects = {item.object_id: item for item in first.scene.objects}

        for y in (0.0, 1.0, 2.0, 3.0, 4.0):
            self.assertTrue(_contains("revolve-cylinder", (11.5, y, 0), objects))
            self.assertFalse(_contains("revolve-cylinder", (12.5, y, 0), objects))
        for y in (0.5, 1.5, 2.5, 3.5):
            cone_radius = 2.0 * (1.0 - y / 4.0)
            self.assertTrue(
                _contains("revolve-cone", (15 + cone_radius - 0.1, y, 0), objects)
            )
            self.assertFalse(
                _contains("revolve-cone", (15 + cone_radius + 0.1, y, 0), objects)
            )
        self.assertTrue(_contains("revolve-frustum", (21.0, 3, 0), objects))
        self.assertEqual(
            first.scene.object("revolve-cylinder").bounds.minimum,
            (8.0, 0.0, -2.0),
        )
        self.assertEqual(
            first.scene.object("revolve-cylinder").bounds.maximum,
            (12.0, 4.0, 2.0),
        )
        self.assertEqual(first.scene.scene_digest, second.scene.scene_digest)
        first_view = derive_voxel_view(
            first.scene,
            first.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        second_view = derive_voxel_view(
            second.scene,
            second.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        self.assertEqual(first_view.view_digest, second_view.view_digest)
        self.assertEqual(
            render_paper_views(first.scene).render_digest,
            render_paper_views(second.scene).render_digest,
        )

    def test_extrusion_prism_and_bezier_are_deterministic_downstream(self) -> None:
        extrusion = _operation(
            "extrusion-prism",
            GeometryOperationKind.EXTRUSION,
            (
                _parameter(
                    "profile",
                    GeometryParameterKind.POINTS3,
                    [[25, 0, 0], [28, 0, 0], [28, 0, 2], [25, 0, 2]],
                    unit=LengthUnit.METER,
                ),
                _parameter("vector", GeometryParameterKind.VECTOR3, [0, 3, 0], unit=LengthUnit.METER),
            ),
        )
        bezier = _operation(
            "bezier-curve",
            GeometryOperationKind.CURVE,
            (
                _parameter("basis", GeometryParameterKind.TEXT, "bezier"),
                _parameter(
                    "points",
                    GeometryParameterKind.POINTS3,
                    [[0, 5, 0], [1, 7, 0], [3, 5, 0]],
                    unit=LengthUnit.METER,
                ),
            ),
        )
        program = _compile_with(bezier, extrusion)
        first = realize_geometry(program, workspace_id="functions")
        second = realize_geometry(program, workspace_id="functions")
        assert first.scene is not None and second.scene is not None
        objects = {item.object_id: item for item in first.scene.objects}

        self.assertTrue(_contains("extrusion-prism", (26, 1, 1), objects))
        self.assertFalse(_contains("extrusion-prism", (29, 1, 1), objects))
        for x in (25.5, 26.5, 27.5):
            for y in (0.5, 1.5, 2.5):
                for z in (0.5, 1.5):
                    self.assertTrue(
                        _contains("extrusion-prism", (x, y, z), objects)
                    )
        self.assertEqual(
            first.scene.object("extrusion-prism").bounds.minimum,
            (25.0, 0.0, 0.0),
        )
        self.assertEqual(
            first.scene.object("extrusion-prism").bounds.maximum,
            (28.0, 3.0, 2.0),
        )
        self.assertEqual(
            first.scene.object("bezier-curve").geometry["basis"],
            "bezier",
        )
        self.assertGreater(
            len(first.scene.object("bezier-curve").geometry["points"]),
            2,
        )
        self.assertEqual(first.scene.scene_digest, second.scene.scene_digest)
        first_view = derive_voxel_view(
            first.scene,
            first.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        second_view = derive_voxel_view(
            second.scene,
            second.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        self.assertEqual(first_view.view_digest, second_view.view_digest)
        self.assertEqual(
            render_paper_views(first.scene).render_digest,
            render_paper_views(second.scene).render_digest,
        )

    def test_loft_and_fixed_frame_sweep_are_bounded_declared_meshes(self) -> None:
        loft = _operation(
            "loft-mesh",
            GeometryOperationKind.LOFT,
            (
                _parameter("cap_ends", GeometryParameterKind.BOOLEAN, True),
                _parameter(
                    "closed_profile",
                    GeometryParameterKind.BOOLEAN,
                    True,
                ),
                _parameter(
                    "profile_size",
                    GeometryParameterKind.INTEGER,
                    4,
                ),
                _parameter(
                    "profiles",
                    GeometryParameterKind.POINTS3,
                    [
                        [25, 0, 0],
                        [25, 2, 0],
                        [25, 2, 2],
                        [25, 0, 2],
                        [28, 0, 0],
                        [28, 2, 0],
                        [28, 2, 2],
                        [28, 0, 2],
                    ],
                    unit=LengthUnit.METER,
                ),
            ),
        )
        sweep = _operation(
            "sweep-mesh",
            GeometryOperationKind.SWEEP,
            (
                _parameter("cap_ends", GeometryParameterKind.BOOLEAN, True),
                _parameter(
                    "closed_profile",
                    GeometryParameterKind.BOOLEAN,
                    True,
                ),
                _parameter("frame_mode", GeometryParameterKind.TEXT, "fixed"),
                _parameter(
                    "path",
                    GeometryParameterKind.POINTS3,
                    [[30, 0, 0], [32, 0, 0], [34, 0, 0]],
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "profile",
                    GeometryParameterKind.POINTS3,
                    [[30, 0, 0], [30, 2, 0], [30, 2, 2], [30, 0, 2]],
                    unit=LengthUnit.METER,
                ),
            ),
        )
        program = _compile_with(loft, sweep)
        first = realize_geometry(program, workspace_id="mesh-functions")
        second = realize_geometry(program, workspace_id="mesh-functions")
        assert first.scene is not None and second.scene is not None
        loft_object = first.scene.object("loft-mesh")
        sweep_object = first.scene.object("sweep-mesh")
        objects = {item.object_id: item for item in first.scene.objects}

        self.assertEqual(len(loft_object.geometry["vertices"]), 8)
        self.assertEqual(len(loft_object.geometry["faces"]), 12)
        self.assertEqual(len(sweep_object.geometry["vertices"]), 12)
        self.assertEqual(len(sweep_object.geometry["faces"]), 20)
        self.assertEqual(loft_object.bounds.minimum, (25.0, 0.0, 0.0))
        self.assertEqual(loft_object.bounds.maximum, (28.0, 2.0, 2.0))
        self.assertEqual(sweep_object.bounds.minimum, (30.0, 0.0, 0.0))
        self.assertEqual(sweep_object.bounds.maximum, (34.0, 2.0, 2.0))
        self.assertTrue(_contains("loft-mesh", (26, 1, 1), objects))
        self.assertTrue(_contains("sweep-mesh", (33, 1, 1), objects))
        self.assertEqual(first.scene.scene_digest, second.scene.scene_digest)

        first_view = derive_voxel_view(
            first.scene,
            first.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        second_view = derive_voxel_view(
            second.scene,
            second.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        self.assertIn("loft_tessellated_mesh", first_view.loss_codes)
        self.assertIn(
            "sweep_fixed_frame_tessellated_mesh",
            first_view.loss_codes,
        )
        self.assertEqual(first_view.view_digest, second_view.view_digest)
        self.assertEqual(
            first_view.to_observation().source_scan_sha256,
            first_view.view_digest,
        )
        self.assertEqual(
            render_paper_views(first.scene).render_digest,
            render_paper_views(second.scene).render_digest,
        )

    def test_tessellation_rejects_explicit_vertex_budget_overrun(self) -> None:
        profile_size = SandboxAssetPayload.MAX_VERTICES // 2 + 1
        first = tuple((0.0, float(index), 0.0) for index in range(profile_size))
        second = tuple((1.0, float(index), 0.0) for index in range(profile_size))
        with self.assertRaisesRegex(
            SandboxRealizationError,
            "explicit vertex bound",
        ):
            _mesh_from_sections(
                (first, second),
                closed_profile=False,
                cap_ends=False,
                operation_id="bounded-mesh",
                source_kind="test",
                loss_code="test_tessellation",
                tolerance=0.001,
            )

    def test_transform_and_array_preserve_source_authority(self) -> None:
        transform_source = _operation(
            "transform-source",
            GeometryOperationKind.SOLID,
            (
                _parameter(
                    "origin",
                    GeometryParameterKind.VECTOR3,
                    [38, 0, 0],
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "size",
                    GeometryParameterKind.VECTOR3,
                    [1, 1, 1],
                    unit=LengthUnit.METER,
                ),
            ),
        )
        transformed = _operation(
            "transform-result",
            GeometryOperationKind.TRANSFORM,
            (
                _parameter(
                    "matrix",
                    GeometryParameterKind.MATRIX4,
                    [
                        1, 0, 0, 0,
                        0, 1, 0, 4,
                        0, 0, 1, 0,
                        0, 0, 0, 1,
                    ],
                ),
            ),
            inputs=("transform-source",),
        )
        array_source = _operation(
            "array-source",
            GeometryOperationKind.SOLID,
            (
                _parameter(
                    "origin",
                    GeometryParameterKind.VECTOR3,
                    [42, 0, 0],
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "size",
                    GeometryParameterKind.VECTOR3,
                    [1, 1, 1],
                    unit=LengthUnit.METER,
                ),
            ),
        )
        array = _operation(
            "array-result",
            GeometryOperationKind.ARRAY,
            (
                _parameter("count", GeometryParameterKind.INTEGER, 3),
                _parameter(
                    "step",
                    GeometryParameterKind.VECTOR3,
                    [0, 3, 0],
                    unit=LengthUnit.METER,
                ),
            ),
            inputs=("array-source",),
        )
        program = _compile_with(
            array,
            array_source,
            transformed,
            transform_source,
        )
        first = realize_geometry(program, workspace_id="placement-functions")
        second = realize_geometry(program, workspace_id="placement-functions")
        assert first.scene is not None and second.scene is not None
        objects = {item.object_id: item for item in first.scene.objects}

        self.assertFalse(first.scene.object("transform-source").physical)
        self.assertFalse(first.scene.object("array-source").physical)
        self.assertTrue(first.scene.object("transform-result").physical)
        self.assertTrue(first.scene.object("array-result").physical)
        self.assertTrue(_contains("transform-result", (38.5, 4.5, 0.5), objects))
        self.assertFalse(_contains("transform-result", (38.5, 0.5, 0.5), objects))
        for y in (0.5, 3.5, 6.5):
            self.assertTrue(_contains("array-result", (42.5, y, 0.5), objects))
        self.assertFalse(_contains("array-result", (42.5, 2.0, 0.5), objects))
        self.assertEqual(first.scene.scene_digest, second.scene.scene_digest)
        self.assertEqual(
            render_paper_views(first.scene).render_digest,
            render_paper_views(second.scene).render_digest,
        )

        first_view = derive_voxel_view(
            first.scene,
            first.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        second_view = derive_voxel_view(
            second.scene,
            second.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        self.assertEqual(first_view.view_digest, second_view.view_digest)


if __name__ == "__main__":
    unittest.main()
