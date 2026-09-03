from __future__ import annotations

import unittest

from archive.archflow.runtime.candidate_assembly import (
    CandidateAssembly,
    CandidateAssemblyError,
    CandidatePolicyBinding,
    CandidatePolicyKind,
    PlanValueBinding,
    assemble_candidate,
    bind_mcp_execution,
)
from archflow.state.model import ArtifactRef
from archive.tests.test_design_development import EVIDENCE, _coordinated_state


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


def _plan_bindings() -> tuple[PlanValueBinding, ...]:
    return (
        PlanValueBinding(
            json_pointer="/operations/0/component",
            design_refs=("design-component:primary-support",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/material",
            design_refs=("design-component:primary-surface",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/origin/0",
            design_refs=("design-component:building",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/origin/1",
            design_refs=("design-component:building",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/origin/2",
            design_refs=("design-component:building",),
            evidence_refs=(EVIDENCE,),
        ),
        PlanValueBinding(
            json_pointer="/operations/0/tool",
            design_refs=(),
            evidence_refs=(EVIDENCE,),
        ),
    )


def _assembly() -> CandidateAssembly:
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
        evidence_refs=(EVIDENCE,),
    )


class CandidateAssemblyTests(unittest.TestCase):
    def test_coordinated_state_becomes_traceable_pre_execution_candidate(
        self,
    ) -> None:
        assembly = _assembly()
        round_trip = CandidateAssembly.from_dict(assembly.to_dict())

        self.assertEqual(round_trip, assembly)
        self.assertEqual(round_trip.design_state, assembly.design_state)
        self.assertEqual(
            {item.key for item in assembly.submission.claims},
            {
                "component.building",
                "component.primary-support",
                "component.primary-surface",
            },
        )
        self.assertFalse(assembly.submission.delta.artifacts_add)
        self.assertIsNone(assembly.to_dict()["approval_receipt"])
        self.assertIsNone(assembly.to_dict()["hard_usability_verdict"])
        self.assertFalse(assembly.to_dict()["canonical_write_authority"])

    def test_uncoordinated_development_cannot_be_candidate(self) -> None:
        _, _, initial, _ = _coordinated_state()
        with self.assertRaisesRegex(
            CandidateAssemblyError,
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
                        design_refs=(),
                        evidence_refs=(EVIDENCE,),
                    ),
                ),
                policies=_policies(),
                evidence_refs=(EVIDENCE,),
            )

    def test_plan_policy_and_design_provenance_fail_closed(self) -> None:
        _, _, _, state = _coordinated_state()
        with self.assertRaisesRegex(CandidateAssemblyError, "leaf provenance"):
            assemble_candidate(
                state,
                workspace_id="candidate-workspace",
                plan_id="candidate-plan",
                plan_payload={"operation": "x", "coordinate": 1},
                plan_bindings=(
                    PlanValueBinding(
                        json_pointer="/operation",
                        design_refs=(),
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
                        design_refs=(),
                        evidence_refs=(EVIDENCE,),
                    ),
                ),
                policies=_policies()[:1],
                evidence_refs=(EVIDENCE,),
            )
        with self.assertRaisesRegex(
            CandidateAssemblyError,
            "unknown design identities",
        ):
            assemble_candidate(
                state,
                workspace_id="candidate-workspace",
                plan_id="candidate-plan",
                plan_payload={"operation": "x"},
                plan_bindings=(
                    PlanValueBinding(
                        json_pointer="/operation",
                        design_refs=("design-component:invented",),
                        evidence_refs=(EVIDENCE,),
                    ),
                ),
                policies=_policies(),
                evidence_refs=(EVIDENCE,),
            )

    def test_mcp_success_creates_candidate_artifact_not_acceptance(self) -> None:
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
        self.assertEqual(handoff.submission.delta.artifacts_add, (executed,))
        self.assertTrue(handoff.to_dict()["mcp_succeeded"])
        self.assertIsNone(handoff.to_dict()["hard_usability_verdict"])
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
