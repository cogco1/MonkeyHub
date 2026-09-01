from __future__ import annotations

import unittest

from archflow.realization import (
    VoxelizationPolicy,
    derive_voxel_view,
    realize_geometry,
)
from archflow.compilers.geometry import compile_geometry_program
from archflow.state import CanonicalState
from archflow.state.geometry_program import (
    AffineTransform,
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    CoordinateFrame,
    DetailMaturity,
    GeometryOperationKind,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    LengthUnit,
    SemanticBinding,
)
from archflow.submission import CandidateDelta, CandidateSubmission
from archflow.validation import validate_submission
from archflow.validation.use_scenarios import (
    ScenarioObservationBinding,
    UseScenarioValidator,
    VerticalCirculationEvidence,
)
from archflow.validation.usability import UseZoneEvidence
from tests.test_geometry_compiler import COMMITMENT, EVIDENCE, _state
from tests.test_sandbox_realization import _boolean, _curve, _solid
from tests.test_usability_validation import program_with


def _compiled_multilevel():
    design_state = _state()
    operations = (
        _solid("clearance", "clearance", [0, 1, 1], [2, 2, 1]),
        _curve("frame", "frame", [[0, 1, 1], [0, 3, 1]]),
        _solid("floor", "floor", [0, 0, 0], [5, 1, 3]),
        _curve("hardware", "hardware", [[0, 2, 1], [0, 2, 1.1]]),
        _curve("leaf", "leaf", [[0, 1, 1], [0, 3, 1]]),
        _solid("marker", "marker", [7, 6, 2], [1, 1, 1]),
        _boolean(
            "opening",
            "opening",
            GeometryOperationKind.BOOLEAN_INTERSECTION,
            ("opening-tool", "wall-host"),
        ),
        _solid("opening-tool", "opening-tool", [0, 1, 1], [1, 2, 1]),
        _solid("step-1", "step-1", [2, 1, 1], [1, 1, 1]),
        _solid("step-2", "step-2", [3, 2, 1], [1, 1, 1]),
        _solid("upper-floor", "upper-floor", [4, 3, 0], [3, 1, 3]),
        _boolean(
            "wall",
            "wall",
            GeometryOperationKind.BOOLEAN_DIFFERENCE,
            ("opening", "wall-host"),
            base_id="wall-host",
        ),
        _solid("wall-host", "wall-host", [0, 1, 0], [1, 2, 3]),
    )
    ordered = tuple(sorted(operations, key=lambda item: item.op_id))
    object_ids = tuple(
        sorted(
            object_id
            for operation in ordered
            for object_id in operation.output_object_ids
        )
    )
    proposal = GeometryProgramProposal(
        proposal_id="multilevel-sandbox",
        project_id=design_state.project_id,
        run_id=design_state.run_id,
        base=design_state.base,
        design_state_digest=design_state.state_digest,
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
                binding_id="room-binding",
                component_id="building",
                object_ids=object_ids,
                commitment_refs=(COMMITMENT,),
                evidence_refs=(EVIDENCE,),
            ),
        ),
        operations=ordered,
        assemblies=(
            HostedAssembly(
                assembly_id="entry",
                kind=AssemblyKind.DOOR,
                host_object_id="wall-host",
                host_socket_id="entry-axis",
                members=(
                    AssemblyMember(AssemblyRole.CLEARANCE, ("clearance",)),
                    AssemblyMember(AssemblyRole.FRAME, ("frame",)),
                    AssemblyMember(AssemblyRole.HARDWARE, ("hardware",)),
                    AssemblyMember(AssemblyRole.HOST_CUT, ("opening",)),
                    AssemblyMember(AssemblyRole.LEAF, ("leaf",)),
                ),
                interface_refs=("interface:outside-to-upper",),
                semantic_binding_ids=("room-binding",),
                maturity=DetailMaturity.FUNCTIONAL,
            ),
        ),
    )
    result = compile_geometry_program(
        design_state,
        proposal,
        active_commitment_refs=(COMMITMENT,),
    )
    if result.program is None:
        raise AssertionError(result.receipt.issues)
    return design_state, result.program


class SandboxUseScenarioIntegrationTests(unittest.TestCase):
    def test_exact_sandbox_vertical_route_passes_and_isolated_red_fails(
        self,
    ) -> None:
        design_state, geometry_program = _compiled_multilevel()
        realized = realize_geometry(
            geometry_program,
            workspace_id="sandbox-workspace",
        )
        assert realized.scene is not None
        view = derive_voxel_view(
            realized.scene,
            realized.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        observation = view.to_observation()
        target_cell = (4, 4, 1)
        target_region = next(
            item
            for item in observation.connected_regions
            if target_cell in item.cells
        )
        program = program_with(required_spaces=["upper"])
        binding = ScenarioObservationBinding.from_sandbox_realization(
            binding_id="p048-sandbox-observation",
            program=program,
            observation=observation,
            design_state_digest=design_state.state_digest,
            geometry_program_digest=geometry_program.program_digest,
            realization_receipt_digest=realized.receipt.receipt_digest,
            evidence_refs=(
                f"sandbox-scene:{realized.scene.scene_digest}",
                f"sandbox-voxel:{view.view_digest}",
            ),
        )
        zones = (
            UseZoneEvidence(
                space="upper",
                region_id=target_region.region_id,
                evidence_refs=(
                    f"sandbox-region:{target_region.region_id}",
                ),
            ),
        )
        circulation = (
            VerticalCirculationEvidence(
                circulation_id="stair-route",
                kind="stair",
                path=(
                    (1, 1, 1),
                    (2, 2, 1),
                    (3, 3, 1),
                    (4, 4, 1),
                ),
                evidence_refs=(
                    f"sandbox-scene:{realized.scene.scene_digest}",
                ),
            ),
        )
        state = CanonicalState(ref=observation.base_state)
        submission = CandidateSubmission(
            submission_id="sandbox-candidate",
            base=observation.base_state,
            workspace_id=observation.workspace_id,
            intent="Validate the exact P048 sandbox candidate.",
            delta=CandidateDelta(artifacts_add=(view.artifact,)),
            claims=(),
            evidence_refs=(view.artifact.artifact_id,),
        )

        passing = validate_submission(
            state,
            submission,
            (
                UseScenarioValidator(
                    program,
                    observation,
                    zones,
                    observation_binding=binding,
                    design_state_digest=design_state.state_digest,
                    geometry_program_digest=geometry_program.program_digest,
                    realization_receipt_digest=(
                        realized.receipt.receipt_digest
                    ),
                    vertical_circulation=circulation,
                ),
            ),
        )
        isolated = validate_submission(
            state,
            submission,
            (
                UseScenarioValidator(
                    program,
                    observation,
                    zones,
                    observation_binding=binding,
                    design_state_digest=design_state.state_digest,
                    geometry_program_digest=geometry_program.program_digest,
                    realization_receipt_digest=(
                        realized.receipt.receipt_digest
                    ),
                ),
            ),
        )

        self.assertTrue(passing.passed, passing.findings)
        self.assertFalse(isolated.passed)
        self.assertEqual(
            isolated.findings[0].code,
            "use_scenario.entrance_to_zone.failed",
        )


if __name__ == "__main__":
    unittest.main()
