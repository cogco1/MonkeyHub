from __future__ import annotations

import unittest

from archflow.capabilities import (
    architectural_completeness as legacy_architectural_completeness,
)
from archflow.validation import (
    stage_completeness as canonical_stage_completeness,
)
from archflow.validation.stage_completeness import (
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
    def test_legacy_import_is_thin_canonical_facade(self) -> None:
        public_names = (
            "ArchitecturalCompletenessError",
            "ArchitecturalCompletenessReceipt",
            "ArchitecturalCompletenessStatus",
            "DecisionFamilyCoverage",
            "ParameterBasisKind",
            "ParameterEvidence",
            "ParameterEvidenceIssue",
            "ParameterEvidenceIssueReason",
            "ParameterGranularity",
            "StageDecisionRequirements",
            "compile_architectural_completeness",
        )
        for name in public_names:
            self.assertIs(
                getattr(legacy_architectural_completeness, name),
                getattr(canonical_stage_completeness, name),
                name,
            )

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

    def test_input_order_does_not_change_payload_or_digest(self) -> None:
        first_requirements = StageDecisionRequirements(
            typology_id="caller-owned-building-type",
            stage_id="technical-detail",
            required_decision_families=("roof", "structure"),
        )
        second_requirements = StageDecisionRequirements(
            typology_id="caller-owned-building-type",
            stage_id="technical-detail",
            required_decision_families=("structure", "roof"),
        )
        roof = ParameterEvidence(
            parameter_id="roof-pitch-range",
            decision_family="roof",
            granularity=ParameterGranularity.NUMERIC_RANGE,
            basis=ParameterBasisKind.DECLARED_CANDIDATE,
            numeric_values=(12, 16),
            unit="deg",
            evidence_refs=("project:declaration/roof-pitch",),
        )
        structure = ParameterEvidence(
            parameter_id="bay-count",
            decision_family="structure",
            granularity=ParameterGranularity.EXACT_NUMERIC,
            basis=ParameterBasisKind.DERIVED,
            numeric_values=(7,),
            unit=None,
            evidence_refs=("project:derivation/bay-count",),
        )

        first = compile_architectural_completeness(
            first_requirements,
            family_coverage=(coverage("roof"), coverage("structure")),
            parameter_evidence=(roof, structure),
        )
        second = compile_architectural_completeness(
            second_requirements,
            family_coverage=(coverage("structure"), coverage("roof")),
            parameter_evidence=(structure, roof),
        )

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.receipt_digest, second.receipt_digest)
        self.assertEqual(
            "b67fe72d92b73658249d04d437ec0efe33aea7210c6e068ed54eed14e6bd36c4",
            first.requirements.requirements_digest,
        )
        self.assertEqual(
            "a365ccc74b31c8ede5aebb2eaf1e268cc33de20d07c31e9d25aa1e6b92b8be55",
            first.receipt_digest,
        )


if __name__ == "__main__":
    unittest.main()
