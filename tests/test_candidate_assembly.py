from __future__ import annotations

import unittest

from archflow.runtime.candidate_assembly import (
    CandidateAssembly,
    CandidateAssemblyError,
    CandidatePolicyBinding,
    CandidatePolicyKind,
    PlanValueBinding,
    assemble_candidate,
    bind_mcp_execution,
)
from archflow.state import ArtifactRef
from archflow.state.candidate_program import (
    CandidateProgramError,
    CandidateProgramValue,
    CandidateValueFacet,
)
from archflow.validation.candidate_program import (
    LegacyProgramFieldMap,
    LegacyProgramProjectionError,
    project_legacy_building_program,
)
from tests.test_design_development import EVIDENCE, _coordinated_state


BUILD_POLICY = "project://portfolio-project/runs/run-001/records/build-policy.json"
APPROVAL_POLICY = (
    "project://portfolio-project/runs/run-001/records/approval-policy.json"
)


def _policies() -> tuple[CandidatePolicyBinding, ...]:
    return (
        CandidatePolicyBinding(
            kind=CandidatePolicyKind.BUILD,
            policy_ref=BUILD_POLICY,
            policy_digest="b" * 64,
            evidence_refs=(EVIDENCE,),
        ),
        CandidatePolicyBinding(
            kind=CandidatePolicyKind.APPROVAL,
            policy_ref=APPROVAL_POLICY,
            policy_digest="c" * 64,
            evidence_refs=(EVIDENCE,),
        ),
    )


def _legacy_values(state) -> tuple[CandidateProgramValue, ...]:
    source = (EVIDENCE,)
    derived = (
        state.selected_schematic.ref,
        state.selected_schematic.option.ref,
    )
    values = {
        "legacy-use": "project_test_use",
        "legacy-footprint-width": 8,
        "legacy-footprint-depth": 6,
        "legacy-footprint-tolerance": 0,
        "legacy-required-spaces": ["project-space-a", "project-space-b"],
        "legacy-minimum-clear-height": 3,
        "legacy-entrance-count": 1,
        "legacy-circulation-min-width": 1,
        "legacy-hard-requirements": ["project-hard-requirement"],
        "legacy-soft-preferences": [],
        "legacy-prohibitions": [],
    }
    return tuple(
        CandidateProgramValue.create(
            value_id=value_id,
            facet=CandidateValueFacet.VALIDATION_INPUT,
            value=value,
            source_refs=source,
            derivation_refs=derived,
        )
        for value_id, value in values.items()
    )


def _field_map() -> LegacyProgramFieldMap:
    return LegacyProgramFieldMap(
        use="legacy-use",
        footprint_width_blocks="legacy-footprint-width",
        footprint_depth_blocks="legacy-footprint-depth",
        footprint_tolerance_blocks="legacy-footprint-tolerance",
        required_spaces="legacy-required-spaces",
        minimum_clear_height="legacy-minimum-clear-height",
        entrance_count="legacy-entrance-count",
        circulation_min_width="legacy-circulation-min-width",
        hard_requirements="legacy-hard-requirements",
        soft_preferences="legacy-soft-preferences",
        prohibitions="legacy-prohibitions",
    )


def _plan_bindings() -> tuple[PlanValueBinding, ...]:
    return (
        PlanValueBinding(
            json_pointer="/operations/0/component",
            candidate_value_refs=(
                "developed-primary-support-project_resolution",
            ),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/material",
            candidate_value_refs=(
                "developed-primary-surface-project_resolution",
            ),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/origin/0",
            candidate_value_refs=("coordinate-footprint-cells",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/origin/1",
            candidate_value_refs=("dimension-level-ground",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/origin/2",
            candidate_value_refs=("coordinate-footprint-cells",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/tool",
            candidate_value_refs=(),
            evidence_refs=(EVIDENCE,),
        ),
    )


def _assembly(*, include_values: bool = True) -> CandidateAssembly:
    _, _, _, state = _coordinated_state()
    return assemble_candidate(
        state,
        workspace_id="candidate-workspace",
        plan_id="candidate-plan",
        plan_payload={
            "operations": [
                {
                    "tool": "project-authored-operation",
                    "component": "primary-support",
                    "material": "primary-surface",
                    "origin": [0, 0, 0],
                }
            ]
        },
        plan_bindings=_plan_bindings(),
        policies=_policies(),
        additional_values=(
            _legacy_values(state) if include_values else ()
        ),
        evidence_refs=(EVIDENCE,),
    )


class CandidateAssemblyTests(unittest.TestCase):
    def test_coordinated_state_becomes_traceable_pre_execution_candidate(
        self,
    ) -> None:
        assembly = _assembly()
        round_trip = CandidateAssembly.from_dict(assembly.to_dict())

        self.assertEqual(round_trip, assembly)
        self.assertEqual(
            {
                "area",
                "coordinate",
                "dimension",
                "function",
                "material",
                "topology",
            }
            - {
                item.facet.value for item in assembly.projection.values
            },
            set(),
        )
        self.assertEqual(
            assembly.projection.selected_branch_id,
            "branch-a",
        )
        self.assertEqual(
            assembly.projection.selected_option_ref,
            assembly.projection.values[0].derivation_refs[1],
        )
        self.assertFalse(assembly.submission.delta.artifacts_add)
        self.assertIsNone(assembly.to_dict()["approval_receipt"])
        self.assertIsNone(assembly.to_dict()["hard_usability_verdict"])
        self.assertFalse(assembly.to_dict()["canonical_write_authority"])

    def test_schematic_or_incomplete_development_cannot_be_candidate(
        self,
    ) -> None:
        _, _, initial, _ = _coordinated_state()
        with self.assertRaisesRegex(
            CandidateProgramError,
            "coordinated non-invalidated",
        ):
            assemble_candidate(
                initial,
                workspace_id="candidate-workspace",
                plan_id="candidate-plan",
                plan_payload={"operation": "project-authored"},
                plan_bindings=(
                    PlanValueBinding(
                        json_pointer="/operation",
                        candidate_value_refs=(),
                        evidence_refs=(EVIDENCE,),
                    ),
                ),
                policies=_policies(),
                evidence_refs=(EVIDENCE,),
            )

        _, _, _, no_material = _coordinated_state(
            include_material=False
        )
        with self.assertRaisesRegex(
            CandidateProgramError,
            "material",
        ):
            assemble_candidate(
                no_material,
                workspace_id="candidate-workspace",
                plan_id="candidate-plan",
                plan_payload={"operation": "project-authored"},
                plan_bindings=(
                    PlanValueBinding(
                        json_pointer="/operation",
                        candidate_value_refs=(),
                        evidence_refs=(EVIDENCE,),
                    ),
                ),
                policies=_policies(),
                evidence_refs=(EVIDENCE,),
            )

    def test_plan_and_policy_derivation_fail_closed(self) -> None:
        _, _, _, state = _coordinated_state()
        with self.assertRaisesRegex(
            CandidateAssemblyError,
            "leaf provenance",
        ):
            assemble_candidate(
                state,
                workspace_id="candidate-workspace",
                plan_id="candidate-plan",
                plan_payload={"operation": "x", "coordinate": 1},
                plan_bindings=(
                    PlanValueBinding(
                        json_pointer="/operation",
                        candidate_value_refs=(),
                        evidence_refs=(EVIDENCE,),
                    ),
                ),
                policies=_policies(),
                evidence_refs=(EVIDENCE,),
            )
        with self.assertRaisesRegex(
            CandidateAssemblyError,
            "build and approval",
        ):
            assemble_candidate(
                state,
                workspace_id="candidate-workspace",
                plan_id="candidate-plan",
                plan_payload={"operation": "x"},
                plan_bindings=(
                    PlanValueBinding(
                        json_pointer="/operation",
                        candidate_value_refs=(),
                        evidence_refs=(EVIDENCE,),
                    ),
                ),
                policies=_policies()[:1],
                evidence_refs=(EVIDENCE,),
            )

    def test_legacy_projection_has_no_defaults_or_reverse_authority(
        self,
    ) -> None:
        assembly = _assembly()
        legacy = project_legacy_building_program(
            assembly.projection,
            _field_map(),
        )
        self.assertEqual(legacy.program.footprint.width_blocks, 8)
        self.assertEqual(legacy.program.required_spaces[0], "project-space-a")
        self.assertTrue(legacy.to_dict()["validator_only"])
        self.assertFalse(legacy.to_dict()["generation_authority"])
        self.assertFalse(legacy.to_dict()["reverse_compiler_available"])

        missing = LegacyProgramFieldMap(
            **{
                **{
                    key: value
                    for key, value in _field_map().to_dict().items()
                    if key != "schema"
                },
                "use": "missing-explicit-use",
            }
        )
        with self.assertRaisesRegex(
            LegacyProgramProjectionError,
            "missing",
        ):
            project_legacy_building_program(
                assembly.projection,
                missing,
            )

    def test_mcp_success_creates_candidate_artifact_not_acceptance(
        self,
    ) -> None:
        assembly = _assembly()
        preview = ArtifactRef(
            artifact_id="preview-artifact",
            uri="project://portfolio-project/runs/run-001/candidates/preview",
            media_type="application/json",
            sha256="d" * 64,
        )
        executed = ArtifactRef(
            artifact_id="executed-artifact",
            uri="project://portfolio-project/runs/run-001/candidates/executed",
            media_type="application/json",
            sha256="e" * 64,
        )
        handoff = bind_mcp_execution(
            assembly,
            preview_artifact=preview,
            executed_artifact=executed,
            preview_plan_digest=assembly.plan.plan_digest,
            execute_plan_digest=assembly.plan.plan_digest,
        )
        payload = handoff.to_dict()

        self.assertEqual(
            handoff.submission.delta.artifacts_add,
            (executed,),
        )
        self.assertTrue(payload["mcp_succeeded"])
        self.assertIsNone(payload["hard_usability_verdict"])
        self.assertIsNone(payload["aesthetic_winner"])
        self.assertFalse(payload["canonical_write_authority"])
        with self.assertRaisesRegex(
            CandidateAssemblyError,
            "exact candidate plan",
        ):
            bind_mcp_execution(
                assembly,
                preview_artifact=preview,
                executed_artifact=executed,
                preview_plan_digest="f" * 64,
                execute_plan_digest=assembly.plan.plan_digest,
            )


if __name__ == "__main__":
    unittest.main()
