from __future__ import annotations

import copy
import unittest

from archflow.project.refs import ProjectVersionRef
from archflow.validation.architectural import (
    ArchitecturalCriterion,
    ArchitecturalObservation,
    ArchitecturalUsabilityContract,
    ArchitecturalUsabilityError,
    ArchitecturalUsabilityReceipt,
    ArchitecturalUsabilityStatus,
    ArchitecturalValidationContext,
    AuthorizedCriterionSource,
    CriterionFindingStatus,
    CriterionOperator,
    CriterionSourceKind,
    canonical_value,
    evaluate_architectural_usability,
)


SHA = "1" * 64


def _context() -> ArchitecturalValidationContext:
    return ArchitecturalValidationContext(
        project_id="p060-unit",
        run_id="run-001",
        base=ProjectVersionRef("p060-unit", 3, SHA),
        design_state_digest="2" * 64,
        component_tree_digest="3" * 64,
        geometry_program_digest="4" * 64,
        realization_receipt_digest="5" * 64,
        scene_digest="6" * 64,
        artifact_ref="project://p060-unit/runs/run-001/artifacts/scene.json",
        brief_digest="7" * 64,
        component_ids=("enclosure", "root"),
        geometry_object_ids=("shell-object",),
        semantic_ownership=(("enclosure", ("shell-object",)),),
        obligation_refs=("development-obligation:verify-shell",),
    )


def _criterion(
    *,
    criterion_id: str = "minimum-shell-value",
    measurement_key: str = "shell-measurement",
    operator: CriterionOperator = CriterionOperator.MINIMUM,
    expected: object = 2.0,
    unit: str | None = "m",
    source_refs: tuple[str, ...] = ("project-record:build-policy",),
) -> ArchitecturalCriterion:
    return ArchitecturalCriterion(
        criterion_id=criterion_id,
        measurement_key=measurement_key,
        operator=operator,
        expected_json=canonical_value(expected),
        unit=unit,
        mandatory=True,
        source_refs=source_refs,
        component_ids=("enclosure",),
        geometry_object_ids=("shell-object",),
        obligation_refs=("development-obligation:verify-shell",),
    )


def _contract(
    criteria: tuple[ArchitecturalCriterion, ...] | None = None,
) -> ArchitecturalUsabilityContract:
    return ArchitecturalUsabilityContract(
        context=_context(),
        authorized_record_refs=("project-record:build-policy",),
        sources=(
            AuthorizedCriterionSource(
                source_ref="project-record:build-policy",
                kind=CriterionSourceKind.BUILD_POLICY,
                authority_ref="project-record:build-policy",
            ),
        ),
        criteria=tuple(
            sorted(criteria or (_criterion(),), key=lambda item: item.criterion_id)
        ),
    )


def _observation(
    contract: ArchitecturalUsabilityContract,
    *,
    value: object = 2.5,
    unit: str | None = "m",
    digest: str | None = None,
) -> ArchitecturalObservation:
    return ArchitecturalObservation(
        observation_id="observe-shell",
        contract_digest=digest or contract.contract_digest,
        measurement_key="shell-measurement",
        value_json=canonical_value(value),
        unit=unit,
        component_ids=("enclosure",),
        geometry_object_ids=("shell-object",),
        obligation_refs=("development-obligation:verify-shell",),
        evidence_refs=("project-evidence:sandbox-measurement",),
    )


class ArchitecturalUsabilitySchemaTests(unittest.TestCase):
    def test_contract_observation_and_receipt_round_trip_exactly(self) -> None:
        contract = _contract()
        self.assertEqual(
            ArchitecturalUsabilityContract.from_dict(contract.to_dict()),
            contract,
        )
        observation = _observation(contract)
        self.assertEqual(
            ArchitecturalObservation.from_dict(observation.to_dict()),
            observation,
        )
        receipt = evaluate_architectural_usability(contract, (observation,))
        self.assertEqual(
            ArchitecturalUsabilityReceipt.from_dict(receipt.to_dict()),
            receipt,
        )
        self.assertFalse(receipt.to_dict()["canonical_write_authority"])
        self.assertFalse(receipt.to_dict()["review_authority"])

    def test_empty_or_optional_only_contract_cannot_pass(self) -> None:
        with self.assertRaises(ArchitecturalUsabilityError):
            ArchitecturalUsabilityContract(
                context=_context(),
                authorized_record_refs=("project-record:build-policy",),
                sources=(
                    AuthorizedCriterionSource(
                        source_ref="project-record:build-policy",
                        kind=CriterionSourceKind.BUILD_POLICY,
                        authority_ref="project-record:build-policy",
                    ),
                ),
                criteria=(),
            )
        optional = _criterion()
        optional = ArchitecturalCriterion(
            criterion_id=optional.criterion_id,
            measurement_key=optional.measurement_key,
            operator=optional.operator,
            expected_json=optional.expected_json,
            unit=optional.unit,
            mandatory=False,
            source_refs=optional.source_refs,
            component_ids=optional.component_ids,
            geometry_object_ids=optional.geometry_object_ids,
            obligation_refs=optional.obligation_refs,
        )
        with self.assertRaises(ArchitecturalUsabilityError):
            _contract((optional,))

    def test_retrieval_requires_separate_authorized_adoption(self) -> None:
        with self.assertRaises(ArchitecturalUsabilityError):
            AuthorizedCriterionSource(
                source_ref="retrieval:result",
                kind=CriterionSourceKind.ADOPTED_RETRIEVAL,
                authority_ref="project-record:brief",
            )
        source = AuthorizedCriterionSource(
            source_ref="retrieval:result",
            kind=CriterionSourceKind.ADOPTED_RETRIEVAL,
            authority_ref="project-record:brief",
            adoption_ref="project-record:adoption",
        )
        with self.assertRaises(ArchitecturalUsabilityError):
            ArchitecturalUsabilityContract(
                context=_context(),
                authorized_record_refs=("project-record:brief",),
                sources=(source,),
                criteria=(
                    _criterion(source_refs=("retrieval:result",)),
                ),
            )

    def test_unknown_local_refs_and_unsupported_operator_are_rejected(self) -> None:
        bad = _criterion()
        bad = ArchitecturalCriterion(
            criterion_id=bad.criterion_id,
            measurement_key=bad.measurement_key,
            operator=bad.operator,
            expected_json=bad.expected_json,
            unit=bad.unit,
            mandatory=bad.mandatory,
            source_refs=bad.source_refs,
            component_ids=("invented-component",),
            geometry_object_ids=(),
            obligation_refs=(),
        )
        with self.assertRaises(ArchitecturalUsabilityError):
            _contract((bad,))
        payload = _criterion().to_dict()
        payload["operator"] = "architectural_magic"
        with self.assertRaises(ArchitecturalUsabilityError):
            ArchitecturalCriterion.from_dict(payload)

    def test_digest_and_authority_drift_fail_reload(self) -> None:
        payload = copy.deepcopy(_contract().to_dict())
        payload["context"]["scene_digest"] = "9" * 64
        with self.assertRaises(ArchitecturalUsabilityError):
            ArchitecturalUsabilityContract.from_dict(payload)
        payload = copy.deepcopy(_contract().to_dict())
        payload["model_self_certification_authority"] = True
        with self.assertRaises(ArchitecturalUsabilityError):
            ArchitecturalUsabilityContract.from_dict(payload)


class ArchitecturalUsabilityEvaluationTests(unittest.TestCase):
    def test_pass_requires_artifact_and_every_mandatory_measurement(self) -> None:
        contract = _contract()
        receipt = evaluate_architectural_usability(
            contract,
            (_observation(contract, value=2.5),),
        )
        self.assertEqual(receipt.status, ArchitecturalUsabilityStatus.PASSED)
        self.assertTrue(receipt.accepted)
        self.assertEqual(
            receipt.findings[0].status,
            CriterionFindingStatus.PASS,
        )
        self.assertTrue(receipt.to_dict()["artifact_presence_claimed"])
        self.assertTrue(receipt.to_dict()["architectural_usability_claimed"])

    def test_failed_measurement_is_local_and_rejects(self) -> None:
        contract = _contract()
        receipt = evaluate_architectural_usability(
            contract,
            (_observation(contract, value=1.5),),
        )
        finding = receipt.findings[0]
        self.assertEqual(receipt.status, ArchitecturalUsabilityStatus.FAILED)
        self.assertFalse(receipt.accepted)
        self.assertEqual(finding.code, "criterion_failed")
        self.assertEqual(finding.component_ids, ("enclosure",))
        self.assertEqual(finding.geometry_object_ids, ("shell-object",))
        self.assertEqual(
            finding.obligation_refs,
            ("development-obligation:verify-shell",),
        )
        self.assertEqual(finding.source_refs, ("project-record:build-policy",))

    def test_missing_stale_unit_and_locality_evidence_are_unknown(self) -> None:
        contract = _contract()
        cases = (
            ((), "observation_missing"),
            (
                (_observation(contract, digest="8" * 64),),
                "observation_stale",
            ),
            ((_observation(contract, unit="ft"),), "unit_mismatch"),
        )
        for observations, code in cases:
            with self.subTest(code=code):
                receipt = evaluate_architectural_usability(contract, observations)
                self.assertEqual(
                    receipt.status,
                    ArchitecturalUsabilityStatus.UNKNOWN,
                )
                self.assertEqual(receipt.findings[0].code, code)
        mismatched = _observation(contract)
        mismatched = ArchitecturalObservation(
            observation_id=mismatched.observation_id,
            contract_digest=mismatched.contract_digest,
            measurement_key=mismatched.measurement_key,
            value_json=mismatched.value_json,
            unit=mismatched.unit,
            component_ids=("root",),
            geometry_object_ids=mismatched.geometry_object_ids,
            obligation_refs=mismatched.obligation_refs,
            evidence_refs=mismatched.evidence_refs,
        )
        receipt = evaluate_architectural_usability(contract, (mismatched,))
        self.assertEqual(
            receipt.findings[0].code,
            "observation_locality_mismatch",
        )

    def test_non_evaluable_value_is_unknown(self) -> None:
        contract = _contract()
        receipt = evaluate_architectural_usability(
            contract,
            (_observation(contract, value="not-a-number"),),
        )
        self.assertEqual(receipt.status, ArchitecturalUsabilityStatus.UNKNOWN)
        self.assertEqual(
            receipt.findings[0].code,
            "measurement_not_evaluable",
        )

    def test_contradictory_mandatory_sources_are_typed_unknown(self) -> None:
        criteria = (
            _criterion(
                criterion_id="minimum-shell-value",
                operator=CriterionOperator.MINIMUM,
                expected=3.0,
            ),
            _criterion(
                criterion_id="maximum-shell-value",
                operator=CriterionOperator.MAXIMUM,
                expected=2.0,
            ),
        )
        contract = _contract(criteria)
        receipt = evaluate_architectural_usability(
            contract,
            (_observation(contract, value=2.5),),
        )
        self.assertEqual(receipt.status, ArchitecturalUsabilityStatus.UNKNOWN)
        self.assertEqual(
            {item.code for item in receipt.findings},
            {"contradictory_mandatory_criteria"},
        )

    def test_generic_relation_operators_do_not_embed_architectural_vocabulary(self) -> None:
        criteria = (
            _criterion(
                criterion_id="allowed-relation",
                operator=CriterionOperator.MEMBER_OF,
                expected=["connected", "adjacent"],
                unit=None,
            ),
        )
        contract = _contract(criteria)
        receipt = evaluate_architectural_usability(
            contract,
            (_observation(contract, value="connected", unit=None),),
        )
        self.assertTrue(receipt.accepted)


if __name__ == "__main__":
    unittest.main()
