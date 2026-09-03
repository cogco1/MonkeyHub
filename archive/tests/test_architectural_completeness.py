from __future__ import annotations

import unittest

from archive.archflow.validation.stage_completeness import (
    ArchitecturalCompletenessStatus,
    DecisionFamilyCoverage,
    ParameterBasisKind,
    ParameterEvidence,
    ParameterEvidenceIssueReason,
    ParameterGranularity,
    StageDecisionRequirements,
    compile_architectural_completeness,
)


def coverage(family_id: str) -> DecisionFamilyCoverage:
    return DecisionFamilyCoverage(
        family_id=family_id,
        decision_refs=(f"decision:{family_id}",),
        evidence_refs=(f"project:evidence/{family_id}",),
    )


class ArchitecturalCompletenessTests(unittest.TestCase):
    def test_complete_requires_every_caller_supplied_family(self) -> None:
        requirements = StageDecisionRequirements(
            typology_id="caller-owned-building-type",
            stage_id="spatial-coordination",
            required_decision_families=(
                "structure",
                "door-openings",
                "site-placement",
            ),
        )
        receipt = compile_architectural_completeness(
            requirements,
            family_coverage=(
                coverage("site-placement"),
                coverage("structure"),
                coverage("door-openings"),
            ),
            parameter_evidence=(
                ParameterEvidence(
                    parameter_id="primary-door-width",
                    decision_family="door-openings",
                    granularity=ParameterGranularity.EXACT_NUMERIC,
                    basis=ParameterBasisKind.MEASURED,
                    numeric_values=(4.2,),
                    unit="m",
                    evidence_refs=("project:measurement/door-width",),
                ),
                ParameterEvidence(
                    parameter_id="structural-axis-origin",
                    decision_family="site-placement",
                    granularity=ParameterGranularity.EXACT_NUMERIC,
                    basis=ParameterBasisKind.DERIVED,
                    numeric_values=(12, 0, 18),
                    unit="m",
                    evidence_refs=("project:derivation/grid-origin",),
                ),
                ParameterEvidence(
                    parameter_id="candidate-member-depth",
                    decision_family="structure",
                    granularity=ParameterGranularity.EXACT_NUMERIC,
                    basis=ParameterBasisKind.DECLARED_CANDIDATE,
                    numeric_values=(0.9,),
                    unit="m",
                    evidence_refs=("project:declaration/member-depth",),
                ),
            ),
        )

        self.assertIs(
            receipt.compilation_status,
            ArchitecturalCompletenessStatus.COMPLETE,
        )
        self.assertEqual((), receipt.missing_decision_families)
        self.assertEqual((), receipt.parameter_issues)
        self.assertEqual(64, len(receipt.receipt_digest))
        self.assertFalse(receipt.to_dict()["stage_acceptance_authority"])
        self.assertFalse(receipt.to_dict()["canonical_write_authority"])

    def test_missing_door_opening_family_cannot_be_complete(self) -> None:
        requirements = StageDecisionRequirements(
            typology_id="caller-owned-building-type",
            stage_id="spatial-coordination",
            required_decision_families=(
                "site-placement",
                "structure",
                "door-openings",
            ),
        )
        receipt = compile_architectural_completeness(
            requirements,
            family_coverage=(
                coverage("structure"),
                coverage("site-placement"),
            ),
        )

        self.assertIs(
            receipt.compilation_status,
            ArchitecturalCompletenessStatus.INCOMPLETE,
        )
        self.assertEqual(
            ("door-openings",),
            receipt.missing_decision_families,
        )
        self.assertEqual(
            ["door-openings"],
            receipt.to_dict()["missing_decision_families"],
        )

    def test_topology_only_basis_cannot_authorize_exact_coordinate(self) -> None:
        requirements = StageDecisionRequirements(
            typology_id="caller-owned-building-type",
            stage_id="site-and-massing",
            required_decision_families=("site-placement",),
        )
        receipt = compile_architectural_completeness(
            requirements,
            family_coverage=(coverage("site-placement"),),
            parameter_evidence=(
                ParameterEvidence(
                    parameter_id="exact-grid-coordinate",
                    decision_family="site-placement",
                    granularity=ParameterGranularity.EXACT_NUMERIC,
                    basis=ParameterBasisKind.TOPOLOGY_ONLY,
                    numeric_values=(24, 0, 16),
                    unit="m",
                    evidence_refs=("project:evidence/generic-grid-topology",),
                ),
            ),
        )

        self.assertIs(
            receipt.compilation_status,
            ArchitecturalCompletenessStatus.INCOMPLETE,
        )
        self.assertEqual((), receipt.missing_decision_families)
        self.assertEqual(1, len(receipt.parameter_issues))
        self.assertIs(
            receipt.parameter_issues[0].reason,
            ParameterEvidenceIssueReason.EXACT_NUMERIC_BASIS_REQUIRED,
        )
        self.assertEqual(
            ParameterBasisKind.TOPOLOGY_ONLY,
            receipt.parameter_issues[0].supplied_basis,
        )


if __name__ == "__main__":
    unittest.main()
