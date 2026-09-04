"""M065 radial-array realization tests: bounds, membership, fail-closed."""

import math
import unittest

from archive.archflow.realization.sandbox import VoxelizationPolicy, derive_voxel_view, realize_geometry
from archflow.compilers.geometry import compile_geometry_program
from archflow.state.geometry_program import (
    AffineTransform,
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
from archive.tests.test_sandbox_realization import COMMITMENT, EVIDENCE, _state


def _parameter(name, kind, value, unit=None):
    return GeometryParameter.create(
        name=name, kind=kind, value=value, unit=unit
    )


def _radial_program(
    *,
    count: int = 8,
    angle_step: float = 45.0,
    axis=(0.0, 1.0, 0.0),
    start_angle: float | None = None,
):
    state = _state()
    parameters = [
        _parameter(
            "angle_step_degrees", GeometryParameterKind.NUMBER, angle_step
        ),
        _parameter("axis", GeometryParameterKind.VECTOR3, list(axis)),
        _parameter(
            "center",
            GeometryParameterKind.VECTOR3,
            [0.0, 0.0, 0.0],
            unit=LengthUnit.METER,
        ),
        _parameter("count", GeometryParameterKind.INTEGER, count),
    ]
    if start_angle is not None:
        parameters.append(
            _parameter(
                "start_angle_degrees",
                GeometryParameterKind.NUMBER,
                start_angle,
            )
        )
    operations = (
        GeometryOperation(
            op_id="ring",
            kind=GeometryOperationKind.RADIAL_ARRAY,
            output_object_ids=("ring",),
            input_object_ids=("seed",),
            frame_id="world",
            parameters=tuple(parameters),
            semantic_binding_ids=("ring-binding",),
        ),
        GeometryOperation(
            op_id="seed",
            kind=GeometryOperationKind.SOLID,
            output_object_ids=("seed",),
            input_object_ids=(),
            frame_id="world",
            parameters=(
                _parameter(
                    "origin",
                    GeometryParameterKind.VECTOR3,
                    [4.0, 0.0, -0.5],
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "size",
                    GeometryParameterKind.VECTOR3,
                    [1.0, 1.0, 1.0],
                    unit=LengthUnit.METER,
                ),
            ),
            semantic_binding_ids=("ring-binding",),
        ),
    )
    proposal = GeometryProgramProposal(
        proposal_id="radial-ring",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=None,
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=(EVIDENCE,),
            ),
        ),
        assets=(),
        semantic_bindings=(
            SemanticBinding(
                binding_id="ring-binding",
                component_id="building",
                object_ids=("ring", "seed"),
                commitment_refs=(COMMITMENT,),
                evidence_refs=(EVIDENCE,),
            ),
        ),
        operations=operations,
        assemblies=(),
    )
    compiled = compile_geometry_program(
        state, proposal, active_commitment_refs=(COMMITMENT,)
    )
    if compiled.program is None:
        raise AssertionError(compiled.receipt.issues)
    return state, compiled.program


class RadialArrayTests(unittest.TestCase):
    def test_ring_realizes_with_symmetric_union_bounds(self):
        _, program = _radial_program()
        result = realize_geometry(program, workspace_id="radial-ring")
        ring = next(
            item for item in result.scene.objects if item.object_id == "ring"
        )
        for axis_index in (0, 2):
            self.assertAlmostEqual(
                ring.bounds.minimum[axis_index],
                -ring.bounds.maximum[axis_index],
                places=6,
            )
        self.assertGreater(ring.bounds.maximum[0], 4.9)

    def test_membership_hits_every_replica_and_misses_center(self):
        _, program = _radial_program()
        result = realize_geometry(program, workspace_id="radial-ring")
        view = derive_voxel_view(
            result.scene,
            result.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        occupied = {
            (cell[0], cell[2])
            for cell in view.occupied_cells
        }
        for index in range(8):
            theta = math.radians(45.0 * index)
            x = 4.5 * math.cos(theta)
            z = -4.5 * math.sin(theta)
            cell = (math.floor(x / 1.0), math.floor(z / 1.0))
            self.assertIn(cell, occupied, msg=f"replica {index} at {cell}")
        self.assertNotIn((0, 0), occupied)

    def test_determinism_across_realizations(self):
        _, program = _radial_program(start_angle=10.0)
        first = realize_geometry(program, workspace_id="radial-ring")
        second = realize_geometry(program, workspace_id="radial-ring")
        self.assertEqual(
            first.receipt.scene_digest, second.receipt.scene_digest
        )

    def test_zero_axis_fails_closed(self):
        _, program = _radial_program(axis=(0.0, 0.0, 0.0))
        result = realize_geometry(program, workspace_id="radial-ring")
        self.assertEqual("rejected", result.receipt.status.value)
        self.assertTrue(result.receipt.issues)

    def test_zero_angle_with_replicas_fails_closed(self):
        _, program = _radial_program(angle_step=0.0)
        result = realize_geometry(program, workspace_id="radial-ring")
        self.assertEqual("rejected", result.receipt.status.value)
        self.assertTrue(result.receipt.issues)

    def test_single_replica_zero_angle_is_allowed(self):
        _, program = _radial_program(count=1, angle_step=0.0)
        result = realize_geometry(program, workspace_id="radial-ring")
        self.assertTrue(
            any(item.object_id == "ring" for item in result.scene.objects)
        )


if __name__ == "__main__":
    unittest.main()
